from __future__ import annotations

"""
Lightweight Forecast.Solar client with per-plane caching and summaries.

Docs:
- API: https://doc.forecast.solar/api:estimate
- Content type: https://doc.forecast.solar/api#content_type
- Endpoint: https://api.forecast.solar/estimate/:lat/:lon/:dec/:az/:kwp

Public plan: 1 plane per API call, estimate data only, rate limited (typically 12/hour/zone).
"""

"""
Forecast.Solar client with:
- Per-plane caching
- time=utc query param
- Timestamp normalization to local system time ("YYYY-MM-DD HH:MM")
- Compact summaries

Docs:
- API: https://doc.forecast.solar/api:estimate
- Content type: https://doc.forecast.solar/api#content_type
- Endpoint: https://api.forecast.solar/estimate/:lat/:lon/:dec/:az/:kwp?time=utc
"""



import time
import json
import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Any, List
from datetime import datetime

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None

import requests


log = logging.getLogger("ForecastSolarClient")


@dataclass(frozen=True)
class PVPlane:
    """
    Forecast.Solar plane parameters.

    dec_deg: declination/tilt in degrees (0 = horizontal, 90 = vertical)
    az_deg: azimuth in degrees, -180..180 where 0 = South, +90 = West, -90 = East, ±180 = North.
    kwp: DC system size in kWp (e.g., 5.0)
    """
    latitude: float
    longitude: float
    dec_deg: float
    az_deg: float
    kwp: float


@dataclass
class RateLimitInfo:
    zone: Optional[str] = None
    period: Optional[int] = None
    limit: Optional[int] = None
    remaining: Optional[int] = None


class ForecastSolarRateLimitError(Exception):
    def __init__(self, message: str, period: Optional[int] = None, limit: Optional[int] = None, remaining: Optional[int] = None):
        super().__init__(message)
        self.period = period
        self.limit = limit
        self.remaining = remaining


@dataclass
class ForecastSolarEstimate:
    # All timestamped series keys are normalized to local system time in "YYYY-MM-DD HH:MM".
    watts: Dict[str, float]
    watt_hours_period: Dict[str, float]
    watt_hours: Dict[str, float]
    # Day totals are normalized to "YYYY-MM-DD" (no tz shift applied).
    watt_hours_day: Dict[str, float]
    timezone: Optional[str]
    time_local: Optional[str]
    time_utc: Optional[str]
    ratelimit: RateLimitInfo
    raw_payload: Dict[str, Any]


@dataclass
class ForecastSummaryDay:
    date: str            # "YYYY-MM-DD"
    kwh: float
    peak_kw: float
    peak_time: Optional[str]  # Local system time "YYYY-MM-DD HH:MM"


@dataclass
class ForecastSummary:
    days: List[ForecastSummaryDay]  # ordered chronologically
    provider: str = "forecast.solar"


class ForecastSolarClient:
    """
    Simple client with:
    - In-memory per-plane cache
    - Fetches UTC timestamps from Forecast.Solar (time=utc)
    - Normalizes them to local system time ("YYYY-MM-DD HH:MM")
    """

    BASE_URL = "https://api.forecast.solar"
    MIN_REFRESH_SEC = 3600  # do not refetch within an hour by default

    def __init__(self, user_agent: str = "SolarSmart/1.0 (+Indigo Plugin)"):
        self._ua = user_agent
        # cache key -> (expires_ts, ForecastSolarEstimate)
        self._cache: Dict[str, Tuple[float, ForecastSolarEstimate]] = {}

    def _plane_key(self, plane: PVPlane) -> str:
        return f"{plane.latitude:.5f},{plane.longitude:.5f},{plane.dec_deg:.1f},{plane.az_deg:.1f},{plane.kwp:.3f}"

    def get_estimate(self, plane: PVPlane, force: bool = False) -> ForecastSolarEstimate:
        key = self._plane_key(plane)
        now = time.time()
        cached = self._cache.get(key)

        if cached and not force:
            expires_ts, data = cached
            if now < expires_ts:
                return data

        url = self._build_url(plane)
        headers = {"Accept": "application/json", "User-Agent": self._ua}

        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code != 200:
            raise RuntimeError(f"Forecast.Solar HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            payload = resp.json()
        except Exception:
            payload = json.loads(resp.text or "{}")

        # Validate
        msg = ((payload or {}).get("message") or {})
        if (msg.get("type") or "").lower() != "success":
            raise RuntimeError(f"Forecast.Solar error: {msg}")

        result = payload.get("result") or {}
        info = payload.get("info") or {}
        ratelimit = payload.get("ratelimit") or {}

        # Convert timestamped series (which are in UTC due to time=utc) to local "YYYY-MM-DD HH:MM"
        watts_norm = _normalize_utc_series_to_local(result.get("watts") or {})
        whp_norm = _normalize_utc_series_to_local(result.get("watt_hours_period") or {})
        wh_norm = _normalize_utc_series_to_local(result.get("watt_hours") or {})

        # Day totals: ensure "YYYY-MM-DD" (no tz shift)
        wh_day_norm = _normalize_day_keys_no_tzshift(result.get("watt_hours_day") or {})

        est = ForecastSolarEstimate(
            watts=watts_norm,
            watt_hours_period=whp_norm,
            watt_hours=wh_norm,
            watt_hours_day={k: float(v) for k, v in wh_day_norm.items()},
            timezone=info.get("timezone"),
            time_local=info.get("time"),
            time_utc=info.get("time_utc"),
            ratelimit=RateLimitInfo(
                zone=ratelimit.get("zone"),
                period=_safe_int(ratelimit.get("period")),
                limit=_safe_int(ratelimit.get("limit")),
                remaining=_safe_int(ratelimit.get("remaining")),
            ),
            raw_payload=payload,
        )

        # Cache expiry: honor period if provided; otherwise 1 hour, min 5 minutes
        period = est.ratelimit.period or self.MIN_REFRESH_SEC
        expires_ts = now + max(300, int(period))
        self._cache[key] = (expires_ts, est)
        return est

    def summarize(self, est: ForecastSolarEstimate) -> ForecastSummary:
        days_sorted = sorted(est.watt_hours_day.items(), key=lambda kv: kv[0])
        day_kwh = [(d, round(float(wh) / 1000.0, 2)) for d, wh in days_sorted]

        watts_by_day: Dict[str, List[Tuple[str, float]]] = {}
        for ts_local, w in est.watts.items():
            d = ts_local[:10]  # "YYYY-MM-DD"
            watts_by_day.setdefault(d, []).append((ts_local, float(w)))

        out_days: List[ForecastSummaryDay] = []
        for d, kwh in day_kwh:
            lst = sorted(watts_by_day.get(d, []), key=lambda kv: kv[0])
            if lst:
                peak_ts, peak_w = max(lst, key=lambda kv: kv[1])
                out_days.append(ForecastSummaryDay(
                    date=d,
                    kwh=kwh,
                    peak_kw=round(peak_w / 1000.0, 2),
                    peak_time=peak_ts,  # already local "YYYY-MM-DD HH:MM"
                ))
            else:
                out_days.append(ForecastSummaryDay(date=d, kwh=kwh, peak_kw=0.0, peak_time=None))

        return ForecastSummary(days=out_days, provider="forecast.solar")

    def _build_url(self, plane: PVPlane) -> str:
        # Use time=utc to get canonical timestamps, then convert to system local time ourselves.
        lat = _fmt_float(plane.latitude)
        lon = _fmt_float(plane.longitude)
        dec = _fmt_float(plane.dec_deg)
        az = _fmt_float(plane.az_deg)
        kwp = _fmt_float(plane.kwp)
        return f"{self.BASE_URL}/estimate/{lat}/{lon}/{dec}/{az}/{kwp}?time=utc"


def _fmt_float(v: float) -> str:
    return f"{float(v):.6f}".rstrip("0").rstrip(".")

def _safe_int(v: Any) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except Exception:
        return None

def _get_local_tz():
    return datetime.now().astimezone().tzinfo

def _normalize_utc_series_to_local(series: Dict[str, Any]) -> Dict[str, float]:
    """
    Provider returned UTC timestamps (due to ?time=utc), possibly with 'T' and timezone suffix.
    Convert each key to local system time "YYYY-MM-DD HH:MM".
    """
    out: Dict[str, float] = {}
    if not series:
        return out
    local_tz = _get_local_tz()
    for ts, val in series.items():
        s = (ts or "").strip()
        if not s:
            continue
        s = s.replace("Z", "+00:00")
        if " " in s and "T" not in s:
            s = s.replace(" ", "T")
        try:
            dt_utc = datetime.fromisoformat(s)
        except Exception:
            # Try padding seconds if missing
            try:
                if len(s) == 16:  # YYYY-MM-DDTHH:MM
                    dt_utc = datetime.fromisoformat(s + ":00")
                else:
                    # As last resort keep the original ts (trimmed) as key
                    out[(ts[:16] if len(ts) >= 16 else ts).replace("T", " ")] = float(val)
                    continue
            except Exception:
                out[(ts[:16] if len(ts) >= 16 else ts).replace("T", " ")] = float(val)
                continue

        # Ensure timezone-aware UTC
        if dt_utc.tzinfo is None:
            # Treat naive as UTC since API guaranteed utc
            from datetime import timezone
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)

        dt_local = dt_utc.astimezone(local_tz)
        key = dt_local.strftime("%Y-%m-%d %H:%M")
        out[key] = float(val)
    return out

def _normalize_day_keys_no_tzshift(day_series: Dict[str, Any]) -> Dict[str, float]:
    """
    Ensure day keys are 'YYYY-MM-DD'. Do NOT shift by timezone; preserve provider's day grouping.
    """
    out: Dict[str, float] = {}
    for k, v in (day_series or {}).items():
        try:
            d = datetime.fromisoformat(str(k)).date()
            out[d.isoformat()] = float(v)
        except Exception:
            out[str(k)] = float(v)
    return out
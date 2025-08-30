from __future__ import annotations

"""
Forecast.Solar client with:
- Per-plane caching
- time=utc query param
- Robust UTC -> local timestamp normalization (DST safe)
- Local-day aggregation (so production totals match local calendar days)
- Compact summaries
"""

import time
import json
import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Any, List
from datetime import datetime, timezone, timedelta
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None  # Fallback handled later

import requests

log = logging.getLogger("ForecastSolarClient")


@dataclass(frozen=True)
class PVPlane:
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
    def __init__(self, message: str, period: Optional[int] = None,
                 limit: Optional[int] = None, remaining: Optional[int] = None):
        super().__init__(message)
        self.period = period
        self.limit = limit
        self.remaining = remaining


@dataclass
class ForecastSolarEstimate:
    # All timestamped series keys are LOCAL system time strings: "YYYY-MM-DD HH:MM"
    watts: Dict[str, float]
    watt_hours_period: Dict[str, float]
    watt_hours: Dict[str, float]
    # Day totals aggregated by LOCAL date: "YYYY-MM-DD"
    watt_hours_day: Dict[str, float]
    timezone: Optional[str]        # API-declared timezone (if provided)
    time_local: Optional[str]      # API "time" converted to local
    time_utc: Optional[str]        # API "time" original (UTC)
    ratelimit: RateLimitInfo
    raw_payload: Dict[str, Any]    # Original JSON (unchanged)


@dataclass
class ForecastSummaryDay:
    date: str        # Local date "YYYY-MM-DD"
    kwh: float
    peak_kw: float
    peak_time: Optional[str]  # Local "YYYY-MM-DD HH:MM"


@dataclass
class ForecastSummary:
    days: List[ForecastSummaryDay]
    provider: str = "forecast.solar"


class ForecastSolarClient:
    BASE_URL = "https://api.forecast.solar/estimate"

    def __init__(self,
                 user_agent: str = "SolarSmart/1.0",
                 session: Optional[requests.Session] = None,
                 local_timezone: Optional[str] = None,
                 cache_ttl_sec: int = 900,
                 keep_instantaneous: bool = True):
        """
        local_timezone: Optional IANA tz name. If None, system local timezone is used.
        keep_instantaneous: Keep non whole-hour timestamps (current point like 20:35:46Z). If False, filter them.
        """
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.cache_ttl_sec = cache_ttl_sec
        self.keep_instantaneous = keep_instantaneous

        # Resolve local tz
        if local_timezone and ZoneInfo:
            try:
                self.local_tz = ZoneInfo(local_timezone)
            except Exception:
                log.warning(f"Invalid timezone '{local_timezone}', falling back to system local.")
                self.local_tz = self._system_local_tz()
        else:
            self.local_tz = self._system_local_tz()

        self._cache: Dict[PVPlane, Tuple[float, ForecastSolarEstimate]] = {}

    # ------------- Public API -------------

    def get_estimate(self, plane: PVPlane, timeout: float = 10.0) -> ForecastSolarEstimate:
        """
        Return (possibly cached) forecast for given plane with timestamps normalized to local time.
        """
        now = time.time()
        cached = self._cache.get(plane)
        if cached and (now - cached[0]) < self.cache_ttl_sec:
            return cached[1]

        url = self._build_url(plane)
        resp = self.session.get(url, timeout=timeout)
        if resp.status_code == 429:
            raise self._rate_limit_error(resp)

        resp.raise_for_status()
        payload = resp.json()

        estimate = self._normalize_payload(payload)
        self._cache[plane] = (now, estimate)
        return estimate

    def summarize(self, estimate: ForecastSolarEstimate) -> ForecastSummary:
        days = []
        # Derive peak per local day from watts series
        day_peak: Dict[str, Tuple[float, str]] = {}
        for ts, w in estimate.watts.items():
            # ts = "YYYY-MM-DD HH:MM"
            day = ts[:10]
            cur_peak = day_peak.get(day)
            if (cur_peak is None) or (w > cur_peak[0]):
                day_peak[day] = (w, ts)

        for day, wh in sorted(estimate.watt_hours_day.items()):
            peak_w = 0.0
            peak_time = None
            if day in day_peak:
                peak_w, peak_ts = day_peak[day]
                peak_time = peak_ts
            days.append(
                ForecastSummaryDay(
                    date=day,
                    kwh=round(wh / 1000.0, 3),
                    peak_kw=round(peak_w / 1000.0, 3),
                    peak_time=peak_time
                )
            )
        return ForecastSummary(days=days)

    # ------------- Internal helpers -------------

    def _build_url(self, plane: PVPlane) -> str:
        # Always request UTC to ensure consistent baseline
        return f"{self.BASE_URL}/{plane.latitude}/{plane.longitude}/{plane.dec_deg}/{plane.az_deg}/{plane.kwp}?time=utc"

    def _system_local_tz(self):
        """
        Best-effort local timezone:
        - Prefer the system's current local tzinfo (DST-aware).
        - Fallback to a fixed-offset tz if zoneinfo config is unavailable.
        """
        # Prefer system-reported local tz (DST-aware)
        try:
            tz = datetime.now().astimezone().tzinfo
            if tz is not None:
                return tz
        except Exception:
            pass

        # Fixed-offset fallback (NOT DST-aware)
        offset_sec = -time.timezone
        if time.daylight and time.localtime().tm_isdst == 1:
            offset_sec = -time.altzone
        log.warning("Falling back to fixed-offset local timezone; DST changes will not be reflected.")
        return timezone(timedelta(seconds=offset_sec))

    def _rate_limit_error(self, resp: requests.Response) -> ForecastSolarRateLimitError:
        try:
            data = resp.json()
        except Exception:
            data = {}
        rl = data.get("ratelimit", {})
        return ForecastSolarRateLimitError(
            f"Rate limit exceeded ({rl})",
            period=rl.get("period"),
            limit=rl.get("limit"),
            remaining=rl.get("remaining")
        )

    def _parse_ts_utc(self, ts: str) -> datetime:
        """
        Parse an ISO8601 timestamp that SHOULD include an offset (+00:00).
        Returns an aware UTC datetime.
        """
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            # Fallback: remove trailing Z if present
            if ts.endswith("Z"):
                dt = datetime.fromisoformat(ts[:-1])
            else:
                raise
        if dt.tzinfo is None:
            # Assume UTC if missing
            dt = dt.replace(tzinfo=timezone.utc)
        # Normalize to pure UTC zone (in case offset isn't +00:00)
        return dt.astimezone(timezone.utc)

    def _ts_local_key(self, dt_utc: datetime) -> str:
        """
        Convert aware UTC datetime to local and format minute-truncated key.
        """
        dt_local = dt_utc.astimezone(self.local_tz).replace(second=0, microsecond=0)
        return dt_local.strftime("%Y-%m-%d %H:%M")

    def _normalize_series(self, series: Dict[str, Any]) -> Dict[str, float]:
        """
        Generic normalization for any time-keyed numeric series.
        - Parses keys as UTC
        - Converts to local
        - Truncates to minute
        - Optionally filters to whole hours
        - If multiple points collapse to same local minute (rare), later value overwrites.
        """
        out: Dict[str, float] = {}
        for raw_ts, val in series.items():
            try:
                dt_utc = self._parse_ts_utc(raw_ts)
            except Exception:
                continue
            if not self.keep_instantaneous:
                # Keep only whole hours (minute=0, second=0)
                if not (dt_utc.minute == 0 and dt_utc.second == 0):
                    continue
            key = self._ts_local_key(dt_utc)
            try:
                out[key] = float(val)
            except Exception:
                pass
        return dict(sorted(out.items()))

    def _aggregate_local_days(self, watts_wh: Dict[str, float]) -> Dict[str, float]:
        """
        Rebuild local-day totals from local timestamped watt_hours (cumulative or per period).
        Expects keys "YYYY-MM-DD HH:MM" already local.
        If watt_hours is cumulative (ever-increasing per API within each day), we compute per-day max.
        If it's per-interval, we just sum. Forecast.Solar's 'watt_hours' is cumulative across entire horizon;
        safer approach: sum watt_hours_period per local day if available. We'll handle that logic outside.
        """
        day_totals: Dict[str, float] = {}
        for ts, wh in watts_wh.items():
            day = ts[:10]
            day_totals[day] = max(day_totals.get(day, 0.0), wh)
        return dict(sorted(day_totals.items()))

    def _sum_period_local_days(self, wh_period: Dict[str, float]) -> Dict[str, float]:
        """
        For watt_hours_period (per-interval Wh), aggregate by local date (sum).
        """
        day_totals: Dict[str, float] = {}
        for ts, wh in wh_period.items():
            day = ts[:10]
            day_totals[day] = day_totals.get(day, 0.0) + wh
        return dict(sorted(day_totals.items()))

    def _normalize_payload(self, payload: Dict[str, Any]) -> ForecastSolarEstimate:
        """
        Transform raw Forecast.Solar payload into local-time keyed series.
        """
        result = payload.get("result", {}) or {}

        # Extract raw series
        raw_watts = result.get("watts", {}) or {}
        raw_wh_period = result.get("watt_hours_period", {}) or {}
        raw_wh_cumulative = result.get("watt_hours", {}) or {}
        raw_wh_day_api = result.get("watt_hours_day", {}) or {}

        # Normalize time series to local
        watts_local = self._normalize_series(raw_watts)
        wh_period_local = self._normalize_series(raw_wh_period)
        wh_cum_local = self._normalize_series(raw_wh_cumulative)

        # Rebuild local-day totals (prefer per-period sum if available for accuracy)
        if wh_period_local:
            wh_day_local = self._sum_period_local_days(wh_period_local)
        else:
            # Fall back to max cumulative per local day
            wh_day_local = self._aggregate_local_days(wh_cum_local)

        # Timezone info from API (might differ; we still base on UTC request)
        api_tz = result.get("timezone")
        api_time_utc = result.get("time")  # e.g., "2025-08-12T05:00:00+00:00"
        if isinstance(api_time_utc, str):
            try:
                dt_api_utc = self._parse_ts_utc(api_time_utc)
                time_local_fmt = self._ts_local_key(dt_api_utc)
            except Exception:
                dt_api_utc = None
                time_local_fmt = None
        else:
            dt_api_utc = None
            time_local_fmt = None

        # Rate limit
        rl_raw = payload.get("ratelimit", {}) or {}
        ratelimit = RateLimitInfo(
            zone=rl_raw.get("zone"),
            period=rl_raw.get("period"),
            limit=rl_raw.get("limit"),
            remaining=rl_raw.get("remaining"),
        )

        estimate = ForecastSolarEstimate(
            watts=watts_local,
            watt_hours_period=wh_period_local,
            watt_hours=wh_cum_local,
            watt_hours_day=wh_day_local,
            timezone=api_tz,
            time_local=time_local_fmt,
            time_utc=api_time_utc,
            ratelimit=ratelimit,
            raw_payload=payload
        )
        return estimate
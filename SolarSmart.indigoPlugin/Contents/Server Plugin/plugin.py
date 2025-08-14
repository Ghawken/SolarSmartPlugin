#! /usr/bin/env python2.6
# -*- coding: utf-8 -*-

"""
"""
import logging
import datetime
import time as t
import time
import sys
import os
import shutil
import subprocess
import sys
import os
from os import path
import shutil
import traceback
import asyncio
import threading
import platform
from datetime import datetime, time as dtime
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None
from datetime import datetime, timezone

import json

import math
try:
    import indigo
except:
    pass
import asyncio
import random
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import re
# Add near the top with other imports
from forecast_solar_service import ForecastSolarClient, PVPlane

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
Number = Union[int, float]


# ────────────────────────────────────────────────────────────
# Small validators
# ────────────────────────────────────────────────────────────

def _is_valid_choice(v):
    try:
        return int(v) > 0
    except Exception:
        return False

def _valid_pos_int(v):
    try:
        return int(str(v).strip()) > 0
    except Exception:
        return False

def _valid_hhmm(s: str) -> bool:
    if not s or ":" not in s:
        return False
    hh, mm = s.split(":", 1)
    try:
        h = int(hh); m = int(mm)
        return 0 <= h <= 23 and 0 <= m <= 59
    except Exception:
        return False

def _time_window_allowed(props, now_dt: datetime) -> bool:
    start = props.get("windowStart", "00:00")
    end   = props.get("windowEnd", "23:59")
    try:
        sh, sm = [int(x) for x in start.split(":", 1)]
        eh, em = [int(x) for x in end.split(":", 1)]
    except Exception:
        return True  # be permissive if misconfigured
    cur = now_dt.hour*60 + now_dt.minute
    s = sh*60 + sm
    e = eh*60 + em
    if s <= e:
        return s <= cur <= e
    else:
        # window crosses midnight
        return cur >= s or cur <= e

def _dow_allowed(props, now_dt: datetime) -> bool:
    dow_map = ["dowMon", "dowTue", "dowWed", "dowThu", "dowFri", "dowSat", "dowSun"]
    allowed = [props.get(k, False) in (True, "true", "True") for k in dow_map]
    # datetime.weekday(): Monday=0..Sunday=6
    return allowed[now_dt.weekday()]

# ---- small utils ----
def _int(v, default=0):
    try:
        return int(float(str(v).replace(",", "").strip()))
    except Exception:
        return default
################################################################################
class IndigoLogHandler(logging.Handler):
    def __init__(self, display_name, level=logging.NOTSET):
        super().__init__(level)
        self.displayName = display_name

    def emit(self, record):
        """ not used by this class; must be called independently by indigo """
        logmessage = ""
        try:
            levelno = int(record.levelno)
            is_error = False
            is_exception = False
            if self.level <= levelno:  ## should display this..
                if record.exc_info !=None:
                    is_exception = True
                if levelno == 5:	# 5
                    logmessage = '({}:{}:{}): {}'.format(path.basename(record.pathname), record.funcName, record.lineno, record.getMessage())
                elif levelno == logging.DEBUG:	# 10
                    logmessage = '({}:{}:{}): {}'.format(path.basename(record.pathname), record.funcName, record.lineno, record.getMessage())
                elif levelno == logging.INFO:		# 20
                    logmessage = record.getMessage()
                elif levelno == logging.WARNING:	# 30
                    logmessage = record.getMessage()
                elif levelno == logging.ERROR:		# 40
                    logmessage = '({}: Function: {}  line: {}):    Error :  Message : {}'.format(path.basename(record.pathname), record.funcName, record.lineno, record.getMessage())
                    is_error = True
                if is_exception:
                    logmessage = '({}: Function: {}  line: {}):    Exception :  Message : {}'.format(path.basename(record.pathname), record.funcName, record.lineno, record.getMessage())
                    indigo.server.log(message=logmessage, type=self.displayName, isError=is_error, level=levelno)
                    if record.exc_info !=None:
                        etype,value,tb = record.exc_info
                        tb_string = "".join(traceback.format_tb(tb))
                        indigo.server.log(f"Traceback:\n{tb_string}", type=self.displayName, isError=is_error, level=levelno)
                        indigo.server.log(f"Error in plugin execution:\n\n{traceback.format_exc(30)}", type=self.displayName, isError=is_error, level=levelno)
                    indigo.server.log(f"\nExc_info: {record.exc_info} \nExc_Text: {record.exc_text} \nStack_info: {record.stack_info}",type=self.displayName, isError=is_error, level=levelno)
                    return
                indigo.server.log(message=logmessage, type=self.displayName, isError=is_error, level=levelno)
        except Exception as ex:
            indigo.server.log(f"Error in Logging: {ex}",type=self.displayName, isError=is_error, level=levelno)
################################################################################
# Async Smart Solar Class
################################################################################
# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL RUNTIME / "QUOTA" / CATCH‑UP TERMINOLOGY (Documentation Only)
# -----------------------------------------------------------------------------
# NOTE: The code historically uses the word "quota". In practice this means:
#   Preferred Runtime Window Allowance:
#       A rolling allowance of minutes we *prefer* to accumulate during times
#       of surplus solar (normal scheduler operation).
#   Catch‑up:
#       A fallback period (often off‑peak / night) where we run loads to reach
#       a minimum desired runtime if solar surplus never allowed it earlier.
#
# We keep existing variable / state names for backward compatibility, but the
# wording below clarifies intent.
#
# Core rolling window (“quota”) variables
# ---------------------------------------
# st["quota_anchor_ts"] / device state "QuotaAnchorTs"
#   Epoch timestamp (float) marking the START of the current rolling preferred
#   runtime window. When the configured window length elapses we "roll over":
#   reset counters and advance this anchor to 'now' (or by whole windows).
#
# st["served_quota_mins"]  (mirrored to device state "RuntimeQuotaMins")
#   Minutes of ON runtime accumulated *within the current rolling window*.
#   This is the authoritative internal counter for "how much preferred runtime
#   has been achieved this window".
#
# device state "RemainingQuotaMins"
#   Remaining minutes still allowed / desired in the current rolling window:
#       remaining = max(0, configured_max_per_window - served_quota_mins)
#   When it reaches 0 the normal scheduler stops starting the load (unless
#   already running and protected by min runtime), and we show SKIP (quota).
#   At rollover it is refilled to the full configured max (not reset to 0).
#
# device state "RuntimeQuotaPct"
#   Percentage (0–100) of the configured preferred window allowance already
#   consumed: (served_quota_mins / configured_max) * 100 (clamped).
#
# device state "RuntimeWindowMins"
#   Cosmetic counter shown in the table as "Time Run". Represents minutes the
#   device has run since the *current quota window* started. Resets at window
#   rollover (Option 1 fix ensures this now). It is not used for decisions.
#
# start_ts (in st["start_ts"])
#   Epoch when the device was last turned ON by the scheduler. Used to enforce
#   minimum runtime (so we don't stop too early).
#
# st["cooldown_start"]
#   Epoch when the device was last turned OFF (for cooldown enforcement). Only
#   populated if cooldown logic is active. Not always present.
#
# Daily / analytic counters
# -------------------------
# st["run_today_secs"]
#   Seconds of runtime accumulated during the current *calendar day* (resets
#   at local midnight). Independent of rolling window logic; used mainly for
#   simplified analytics or potential daily policies.
#
# Catch‑up (“fallback runtime”) variables / states
# -----------------------------------------------
# Configuration props:
#   enableCatchup (bool) – allow fallback logic.
#   catchupRuntimeMins – minimum total minutes we want per window *or* per day
#       (current implementation treats it as “per rolling window” target).
#   catchupWindowStart / catchupWindowEnd – HH:MM window when fallback runs
#       (often overnight / low tariff period).
#
# st["catchup_active"]  / device state "catchupActive"
#   True only while the scheduler has explicitly started the device in the
#   catch‑up window to recover missing preferred runtime. If the load is ON
#   for normal reasons, catch‑up does not force this flag True.
#
# st["catchup_run_secs"]  (mirrored to "catchupRunTodayMins"/"catchupRunWindowAccumMins")
#   Seconds the device has actually run *while catchup_active was True*. Lets
#   you measure how much of the fallback runtime was truly “catch‑up driven”.
#
# device state "catchupDailyTargetMins"
#   Copy of catchupRuntimeMins property (exposed for UI/control pages). Treated
#   as the total desired minimum runtime for the rolling window (naming legacy).
#
# device state "catchupRemainingTodayMins"
#   Computed fallback deficit: max(0, catchupDailyTargetMins - served_quota_mins).
#   Even though the name says "Today", it currently measures the *rolling window*
#   deficit, not strictly wall‑clock day. (Rename later if you change semantics.)
#
# device state "catchupRunTodayMins"
#   Minutes accumulated under catch‑up (active) control in the current quota window
#   (and resets when window rolls over and we zero catchup_run_secs).
#
# device state "catchupRunWindowAccumMins"
#   Currently mirrors catchupRunTodayMins (placeholder if you later differentiate
#   calendar day vs rolling window accumulation).
#
# device states "catchupLastStart" / "catchupLastStop"
#   Human-friendly timestamps for when catch‑up mode last started / stopped.
#
# Interaction summary
# -------------------
# 1. Normal (preferred) operation increments served_quota_mins while solar allows.
# 2. When served_quota_mins reaches configured max, RemainingQuotaMins hits 0 and
#    scheduler will not START the load again inside that window (it may finish its
#    current minimum runtime).
# 3. At window rollover:
#       served_quota_mins -> 0
#       RemainingQuotaMins -> configured max
#       RuntimeWindowMins -> 0 (after Option 1 fix)
#       catchup_run_secs -> 0
#    (Deficit may recalc next tick; catch-up flags cleared.)
# 4. Catch‑up window: if served_quota_mins < catchupDailyTargetMins and other
#    constraints OK, scheduler starts load, sets catchup_active=True, and tracks
#    catchup_run_secs until deficit satisfied or window closes.
#
# Potential future naming cleanups (non-breaking ideas):
#   RuntimeQuotaMins        -> PreferredRuntimeUsedMins
#   RemainingQuotaMins      -> PreferredRuntimeRemainingMins
#   catchupDailyTargetMins  -> FallbackTargetMins
#   catchupRemainingTodayMins -> FallbackRemainingMins
#   RuntimeWindowMins       -> PreferredWindowRunMins
#
# Until then, this block documents the current semantics unambiguously.
# ─────────────────────────────────────────────────────────────────────────────
class SolarSmartAsyncManager:
    """
    Owns the long-running async loops for SolarSmart.
    Keeps a reference to the plugin and its asyncio loop.
    """
    def __init__(self, plugin, event_loop: asyncio.AbstractEventLoop):
        self.plugin = plugin
        self.loop = event_loop
        self._tasks = []
        # Log instantiation so it shows up in Indigo logs
        try:
            self.plugin.logger.info("SolarSmart AsyncManager initialised — async loop manager ready.")
        except Exception as e:
            # Fallback to Indigo's logger if plugin.logger isn't ready yet
            import indigo
            indigo.server.log(f"SolarSmartAsyncManager initialised (logger not ready: {e})", isError=True)

    # ----- lifecycle -----
    def _track_task(self, coro, name: str):
        async def _runner():
            try:
                await coro
            except asyncio.CancelledError:
                if getattr(self.plugin, "debug5", False):
                    self.plugin.logger.debug(f"Task '{name}' cancelled.")
            except Exception:
                self.plugin.logger.exception(f"Task '{name}' crashed")

        task = self.loop.create_task(_runner())
        self._tasks.append(task)
        return task


    async def start(self):
        """Schedule forever loops. Call once from _async_start()."""
        if getattr(self.plugin, "debug2", False):
            self.plugin.logger.debug("SolarSmartAsyncManager.start()")

        # Main 30s ticker (pv/consumption/battery -> publish states)
        #self._tasks.append(self.loop.create_task(self._ticker_main_states(period_sec=30.0)))
        asyncio.sleep(5)
        self._track_task(self._ticker_main_states(period_sec=30.0), "main_states")
        self._track_task(self._ticker_load_scheduler(period_min=self.plugin.time_for_checks_frequency),
                         "load_scheduler")

    async def stop(self):
        """Cancel all tasks gracefully."""
        if getattr(self.plugin, "debug2", False):
            self.plugin.logger.debug("SolarSmartAsyncManager.stop(): cancelling tasks")

        for t in self._tasks:
            t.cancel()
        # Give tasks a chance to finish
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    # ----- forever loops -----
    def _get_main_device(self):
        return next(
            (d for d in indigo.devices if d.deviceTypeId == "solarsmartMain" and d.enabled),
            None
        )

    def _parse_hhmm(self, s: str) -> dtime:
        s = (s or "").strip()
        try:
            hh, mm = s.split(":")
            return dtime(hour=int(hh), minute=int(mm))
        except Exception:
            # Safe default if bad input
            return dtime(0, 0)

    def _time_in_window(self, now_t: dtime, start_t: dtime, end_t: dtime) -> bool:
        # Handles normal and overnight windows (e.g., 23:00 -> 06:00)
        if start_t < end_t:
            return start_t <= now_t < end_t
        else:
            return (now_t >= start_t) or (now_t < end_t)


    def _sync_running_flags_from_external(self, loads_by_tier: dict[int, list[indigo.Device]]):
        """
        For loads controlled by Indigo devices (not action groups), mirror the *actual*
        device on/off (onOffState) into the SmartSolar load's IsRunning flag.
        No actions are sent here; we only correct flags so the scheduler can make decisions.
        """
        for tier, devs in loads_by_tier.items():
            for d in devs:
                ext_on = self.plugin._external_on_state(d)  # helper is in plugin
                if ext_on is None:
                    continue  # no onOffState, likely action group controlled

                if self._is_running(d) != bool(ext_on):
                    self._mark_running(d, bool(ext_on))
                    d.updateStateOnServer("LastReason", "Sync from external onOffState")

                    # Info log on change
                    state_str = "ON" if ext_on else "OFF"
                    self.plugin.logger.info(
                        f"External state change detected for '{d.name}': now {state_str} "
                        f"(mirrored into IsRunning)"
                    )

                    if getattr(self.plugin, "debug2", False):
                        self.plugin.logger.debug(f"Tick sync: {d.name} IsRunning <- {ext_on}")

    def _update_runtime_progress(self, dev):
        """
        Update both:
          - RuntimeQuotaPct (integer 0–100)
          - Status string embedding " (NN%)" after RUNNING/OFF

        Logic:
          used = internal served_quota_mins (authoritative)
          target =
             maxRuntimePerQuotaMins prop (if > 0)
             else used + RemainingQuotaMins (if that state exists and >= 0)
          pct = round(used / target * 100) clamped 0–100
          if target <= 0 => pct = 0

        Minimal churn: only pushes updates when value actually changes.
        Safe to call when device is disabled or OFF.
        """
        try:
            if dev.deviceTypeId != "solarsmartLoad":
                return

            props = dev.pluginProps or {}

            # Authoritative used minutes from in-memory state
            try:
                used = int(self._served_quota_mins(dev))
            except Exception:
                used = 0

            # Determine target
            try:
                target_prop = int(props.get("maxRuntimePerQuotaMins") or 0)
            except Exception:
                target_prop = 0

            remaining_state = dev.states.get("RemainingQuotaMins")
            remaining = None
            try:
                if remaining_state is not None:
                    remaining = int(remaining_state)
            except Exception:
                remaining = None

            if target_prop > 0:
                target = target_prop
            elif remaining is not None and remaining >= 0:
                target = used + remaining
            else:
                target = 0  # no meaningful target

            if target > 0:
                pct = int(round((float(used) / float(target)) * 100.0))
            else:
                pct = 0

            if pct < 0:
                pct = 0
            elif pct > 100:
                pct = 100

            # Update dedicated pct state if changed
            cur_pct = dev.states.get("RuntimeQuotaPct")
            if cur_pct != pct:
                dev.updateStateOnServer("RuntimeQuotaPct", pct)

            # Embed percent in Status
            base_status = "RUNNING" if self._is_running(dev) else "OFF"
            existing_status = dev.states.get("Status", "")
            # Strip any existing trailing " (NN%)"
            new_status = f"{base_status} ({pct}%)"
            if existing_status != new_status:
                dev.updateStateOnServer("Status", new_status)

        except Exception:
            if getattr(self.plugin, "debug3", False):
                self.plugin.logger.exception(f"_update_runtime_progress failed for {dev.name}")

    def _get_max_concurrent_loads(self) -> int:
        main = self._get_main_device()
        # default if no main or not configured
        val = 2
        if main:
            try:
                raw = main.pluginProps.get("maxConcurrentLoads", "2")
                val = int(str(raw).strip() or "2")
            except Exception:
                val = 2
        # clamp to sane range
        val = max(1, min(val, 32))
        if getattr(self.plugin, "debug2", False):
            self.plugin.logger.debug(f"maxConcurrentLoads (from Main #{main.id if main else 'n/a'}) = {val}")
        return val

    def _fmt_w(self, v) -> str:
        try:
            return f"{int(round(float(v)))} W"
        except Exception:
            return "—"

    def _get_main_device(self):
        # prefer configured main; fallback to first enabled main
        main_dev = None
        main_dev_id = self.plugin.pluginPrefs.get("main_device_id")
        if main_dev_id:
            try:
                main_dev = indigo.devices[int(main_dev_id)]
            except Exception:
                main_dev = None
        if not main_dev:
            main_dev = next((d for d in indigo.devices
                             if d.deviceTypeId == "solarsmartMain" and d.enabled), None)
        return main_dev

    def _snapshot_main_metrics(self):
        """Return (pv_w, cons_w, batt_w, headroom_w, ts_str) from the main device states."""
        main = self._get_main_device()
        if not main:
            return (None, None, None, None, None)
        try:
            pv = main.states.get("SolarProduction", None)
            con = main.states.get("SiteConsumption", None)
            bat = main.states.get("BatteryPower", None)
            hdrm = main.states.get("Headroom", None)
            ts = main.states.get("LastUpdate", None)
            # coerce to ints if possible
            pv = None if pv is None else int(pv)
            con = None if con is None else int(con)
            bat = None if bat is None else int(bat)
            hdrm = None if hdrm is None else int(hdrm)
            return (pv, con, bat, hdrm, ts)
        except Exception:
            return (None, None, None, None, None)

    async def _ticker_main_states(self, period_sec: float):
        """
        Every ~period_sec: update all SolarSmart Main devices' custom states.
        """
        # Add tiny jitter so multiple plugins don't sync-beat the server
        jitter = random.uniform(0.0, 0.7)
        await asyncio.sleep(jitter)

        if getattr(self.plugin, "debug2", False):
            self.plugin.logger.debug(f"_ticker_main_states: starting (period={period_sec}s, jitter={jitter:.2f}s)")

        while not getattr(self.plugin, "stopThread", False):
            try:
                # Iterate all enabled SolarSmart Main devices
                count = 0
                for dev in indigo.devices.iter("self"):  # all devices owned by this plugin
                    if dev.deviceTypeId != "solarsmartMain":
                        continue
                    if not dev.enabled:
                        if getattr(self.plugin, "debug2", False):
                            self.plugin.logger.debug(f"_ticker_main_states: skip {dev.name} (disabled)")
                        continue

                    # Update PV/Consumption/Battery/Headroom/LastUpdate
                    if getattr(self.plugin, "debug2", False):
                        self.plugin.logger.debug(f"_ticker_main_states: updating {dev.name} (#{dev.id})")
                    self.plugin._update_solarsmart_states(dev)  # uses helpers we wrote earlier
                    count += 1

                if getattr(self.plugin, "debug2", False):
                    self.plugin.logger.debug(f"_ticker_main_states: updated {count} main device(s) at {datetime.now()}")

            except asyncio.CancelledError:
                # Task cancelled -> exit loop cleanly
                break
            except Exception as e:
                self.plugin.logger.exception(f"_ticker_main_states: exception: {e}")

            # Sleep the base period; wake earlier if plugin is stopping
            for _ in range(int(period_sec)):
                if getattr(self.plugin, "stopThread", False):
                    break
                await asyncio.sleep(1.0)

        if getattr(self.plugin, "debug2", False):
            self.plugin.logger.debug("_ticker_main_states: exiting (stopThread set)")


    def _parse_hhmm(self, s: str) -> dtime:
        s = (s or "").strip()
        try:
            hh, mm = s.split(":")
            return dtime(hour=int(hh), minute=int(mm))
        except Exception:
            return dtime(0, 0)

    def _time_in_window(self, now_t: dtime, start_t: dtime, end_t: dtime) -> bool:
        # Handles normal (start<end) and overnight (start>end) windows
        if start_t < end_t:
            return start_t <= now_t < end_t
        else:
            return (now_t >= start_t) or (now_t < end_t)



    def _min_runtime_already_met(self, dev, st: dict) -> bool:
        """
        Return True if the device has already satisfied its configured minimum runtime
        for the active quota window (or current day if you only day-scope).

        Looks at:
          props['minRuntimeMins']
        Uses (first existing key wins):
          st['quota_window_runtime_secs']
          st['runtime_window_secs']
          st['run_today_secs']
          st['accum_runtime_secs']
        Adjust these names if your plugin tracks runtime under different keys.

        If minRuntimeMins is 0/blank => treated as satisfied (returns True).
        """
        props = dev.pluginProps or {}
        try:
            min_runtime_mins = int(props.get("minRuntimeMins") or 0)
        except Exception:
            min_runtime_mins = 0

        if min_runtime_mins <= 0:
            return True

        # Candidate keys in order of preference
        for k in ("quota_window_runtime_secs",
                  "runtime_window_secs",
                  "run_today_secs",
                  "accum_runtime_secs"):
            if k in st:
                runtime_secs = st.get(k, 0.0)
                break
        else:
            runtime_secs = 0.0

        return runtime_secs >= (min_runtime_mins * 60.0)

        # 1. In SolarSmartAsyncManager._catchup_deficit_scheduler REPLACE the entire function body with this simpler logic:

    def _catchup_deficit_scheduler(self, loads_by_tier: dict[int, list[indigo.Device]]):
        """
        Fallback Catch-up Scheduler (quota-window based) with detailed debug5 tracing.

        Semantics:
          catchupRuntimeMins (device prop) = fallback minimum runtime required per QUOTA WINDOW.
          served_quota_mins (rolling)      = total runtime already accrued this quota window.
          remaining_fallback               = max(0, catchupRuntimeMins - served_quota_mins).

          We only start a load (catchupActive=True) if:
            - enableCatchup is True
            - remaining_fallback > 0
            - inside catch-up window
            - device currently OFF
            - concurrency limit not exceeded
            - (optional) per-tick start cap not exceeded

          If catchupActive and (remaining_fallback == 0 OR window closed) we stop it.

          If the device is already ON for other reasons, we DO NOT toggle catchupActive;
          its runtime still reduces the fallback remaining naturally.

        States updated each tick:
          catchupDailyTargetMins       = configured catchupRuntimeMins (dup for Control Pages)
          catchupRemainingTodayMins    = remaining_fallback
          catchupRunTodayMins          = minutes of runtime under catchupActive
          catchupRunWindowAccumMins    = mirrors catchupRunTodayMins (placeholder)
          catchupActive                = True only if we started it for fallback
          catchupLastStart / LastStop  = timestamps of catch-up driven transitions
        """
        dbg5 = getattr(self.plugin, "debug6", False)
        now = datetime.now()
        now_t = now.time()

        # Concurrency snapshot
        max_concurrent = self._get_max_concurrent_loads()
        running_now = 0
        for _, devs in loads_by_tier.items():
            for d in devs:
                if self._is_running(d):
                    running_now += 1

        if dbg5:
            self.plugin.logger.debug(
                f"[CATCHUP][TICK] start at {now.strftime('%H:%M:%S')} "
                f"maxConc={max_concurrent} runningNow={running_now}"
            )

        catchup_starts_this_tick = 0
        total_devices = 0
        total_candidates = 0
        total_active = 0
        total_started = 0
        total_stopped = 0
        total_skipped = 0
        total_satisfied = 0

        for tier, devs in loads_by_tier.items():
            for d in devs:
                if d.deviceTypeId != "solarsmartLoad" or not d.enabled:
                    continue
                total_devices += 1
                try:
                    props = d.pluginProps or {}
                    if not bool(props.get("enableCatchup", False)):
                        # Clear stale active flag
                        st = self.plugin._load_state.setdefault(d.id, {})
                        if dbg5:
                            self.plugin.logger.debug(f"[CATCHUP][SKIP-NOFLAG] {d.name}: enableCatchup=False")
                        if st.get("catchup_active"):
                            if dbg5:
                                self.plugin.logger.debug(f"[CATCHUP] {d.name}: disabling catchupActive (prop disabled)")
                            st["catchup_active"] = False
                            d.updateStateOnServer("catchupActive", False)
                        continue

                    st = self.plugin._load_state.setdefault(d.id, {})
                    if "catchup_run_secs" not in st:
                        st["catchup_run_secs"] = 0.0

                    # Config target
                    try:
                        catchup_runtime = int(props.get("catchupRuntimeMins") or 0)
                    except Exception:
                        catchup_runtime = 0

                    d.updateStateOnServer("catchupDailyTargetMins", catchup_runtime)

                    if catchup_runtime <= 0:
                        # Target is zero; publish zeros & clear flag
                        if st.get("catchup_active"):
                            st["catchup_active"] = False
                            d.updateStateOnServer("catchupActive", False)
                        run_cu_mins = int(float(st.get("catchup_run_secs", 0.0)) // 60)
                        d.updateStateOnServer("catchupRemainingTodayMins", 0)
                        d.updateStateOnServer("catchupRunTodayMins", run_cu_mins)
                        d.updateStateOnServer("catchupRunWindowAccumMins", run_cu_mins)
                        if dbg5:
                            self.plugin.logger.debug(f"[CATCHUP] {d.name}: target=0 → nothing required")
                        continue

                    served = self._served_quota_mins(d)
                    remaining_fallback = max(0, catchup_runtime - served)

                    start_t = self._parse_hhmm(props.get("catchupWindowStart", "00:00"))
                    end_t = self._parse_hhmm(props.get("catchupWindowEnd", "06:00"))
                    in_window = self._time_in_window(now_t, start_t, end_t)
                    active = bool(st.get("catchup_active"))
                    is_running = self._is_running(d)

                    catchup_run_mins = int(float(st.get("catchup_run_secs", 0.0)) // 60)
                    d.updateStateOnServer("catchupRemainingTodayMins", remaining_fallback)
                    d.updateStateOnServer("catchupRunTodayMins", catchup_run_mins)
                    d.updateStateOnServer("catchupRunWindowAccumMins", catchup_run_mins)
                    d.updateStateOnServer("catchupActive", active)

                    total_candidates += 1
                    if active:
                        total_active += 1

                    if dbg5:
                        self.plugin.logger.debug(
                            f"[CATCHUP][EVAL] {d.name} tier={tier} served={served}m "
                            f"target={catchup_runtime}m remaining={remaining_fallback}m "
                            f"active={active} running={is_running} inWindow={in_window} "
                            f"win={start_t.strftime('%H:%M')}-{end_t.strftime('%H:%M')} "
                            f"catchupRun={catchup_run_mins}m"
                        )

                    # STOP logic (only if we started it)
                    if active and is_running:
                        if remaining_fallback == 0:
                            self._ensure_off(d, "Catch-up target satisfied")
                            st["catchup_active"] = False
                            d.updateStateOnServer("catchupActive", False)
                            d.updateStateOnServer("catchupLastStop", now.strftime("%Y-%m-%d %H:%M:%S"))
                            total_stopped += 1
                            total_satisfied += 1
                            if dbg5:
                                self.plugin.logger.debug(f"[CATCHUP][STOP] {d.name}: satisfied served={served}m")
                            continue
                        if not in_window:
                            self._ensure_off(d, "Catch-up window closed")
                            st["catchup_active"] = False
                            d.updateStateOnServer("catchupActive", False)
                            d.updateStateOnServer("catchupLastStop", now.strftime("%Y-%m-%d %H:%M:%S"))
                            total_stopped += 1
                            if dbg5:
                                self.plugin.logger.debug(
                                    f"[CATCHUP][STOP] {d.name}: window closed remaining={remaining_fallback}m"
                                )
                            continue
                        # Still running & needed
                        if dbg5:
                            self.plugin.logger.debug(
                                f"[CATCHUP][KEEP] {d.name}: remaining={remaining_fallback}m (window open)"
                            )
                        continue

                    # Natural runtime (ON but not catchupActive)
                    if is_running and not active:
                        if remaining_fallback == 0 and dbg5:
                            self.plugin.logger.debug(
                                f"[CATCHUP][PASSIVE-SAT] {d.name}: already met fallback via normal runtime"
                            )
                            total_satisfied += 1
                        elif dbg5:
                            self.plugin.logger.debug(
                                f"[CATCHUP][PASSIVE] {d.name}: running normally; remaining={remaining_fallback}m"
                            )
                        continue

                    # OFF scenarios
                    if remaining_fallback == 0:
                        total_satisfied += 1
                        if dbg5:
                            self.plugin.logger.debug(
                                f"[CATCHUP][NO-NEED] {d.name}: fallback already met; no start."
                            )
                        continue

                    if not in_window:
                        total_skipped += 1
                        if dbg5:
                            self.plugin.logger.debug(
                                f"[CATCHUP][SKIP] {d.name}: outside window remaining={remaining_fallback}m"
                            )
                        continue

                    if running_now >= max_concurrent:
                        total_skipped += 1
                        if dbg5:
                            self.plugin.logger.debug(
                                f"[CATCHUP][SKIP] {d.name}: concurrency cap ({running_now}/{max_concurrent})"
                            )
                        continue

                    if catchup_starts_this_tick >= 1:
                        total_skipped += 1
                        if dbg5:
                            self.plugin.logger.debug(
                                f"[CATCHUP][SKIP] {d.name}: per-tick catch-up start cap reached"
                            )
                        continue

                    # START for catch-up
                    self._ensure_on(d, f"Catch-up start (need {remaining_fallback}m)")
                    st["catchup_active"] = True
                    d.updateStateOnServer("catchupActive", True)
                    d.updateStateOnServer("catchupLastStart", now.strftime("%Y-%m-%d %H:%M:%S"))
                    running_now += 1
                    catchup_starts_this_tick += 1
                    total_started += 1
                    if dbg5:
                        self.plugin.logger.debug(
                            f"[CATCHUP][START] {d.name}: remaining={remaining_fallback}m "
                            f"served={served}m target={catchup_runtime}m"
                        )

                except Exception as e:
                    self.plugin.logger.exception(f"_catchup_deficit_scheduler error on {d.name}: {e}")

        if dbg5:
            self.plugin.logger.debug(
                "[CATCHUP][TICK-END] devices=%d candidates=%d active=%d started=%d stopped=%d "
                "skipped=%d satisfied=%d runningNow=%d" %
                (total_devices, total_candidates, total_active, total_started,
                 total_stopped, total_skipped, total_satisfied, running_now)
            )

    def _shed_all(self, reason: str, tiers: set[int] | None = None):
        """
        Turn OFF all running SmartSolar Load devices.
        If `tiers` is provided, only shed those tiers.
        """
        dbg = getattr(self.plugin, "debug2", False)
        if dbg:
            self.plugin.logger.debug(f"_shed_all: reason='{reason}' tiers={tiers if tiers else 'ALL'}")

        for dev in indigo.devices.iter("self"):
            if dev.deviceTypeId != "solarsmartLoad" or not dev.enabled:
                continue
            if tiers:
                try:
                    t = int(dev.pluginProps.get("tier", 0) or 0)
                    if t not in tiers:
                        continue
                except Exception:
                    pass
            if self._is_running(dev):
                self._ensure_off(dev, reason)


    async def _ticker_load_scheduler(self, period_min: float):
        """
        Every ~period_sec: decide which solarsmartLoad devices should be ON/OFF
        given current headroom, priorities, windows, quotas, and hysteresis.
        """
        if getattr(self.plugin, "debug2", False):
            self.plugin.logger.debug(f"_ticker_load_scheduler: starting (period={period_min} minutes)")
        try:
            period_sec = max(1, int(round(float(period_min) * 60.0)))
        except Exception:
            period_sec = 60  # fallback to 60s if pref is weird

        if getattr(self.plugin, "debug2", False):
            self.plugin.logger.debug(f"_ticker_load_scheduler: starting (period={period_sec}s)")

        # In-memory runtime state (persist later if you like)
        # keyed by load dev id: {"running": bool, "start_ts": float, "served_quota_mins": int, ...}
        if not hasattr(self.plugin, "_load_state"):
            self.plugin._load_state = {}
        # early in _ticker_load_scheduler
        main = next((d for d in indigo.devices if d.deviceTypeId == "solarsmartMain" and d.enabled), None)
        if not main:
            if getattr(self.plugin, "debug2", False):
                self.plugin.logger.debug("_ticker_load_scheduler: no Main device found; shedding any running loads")
            self._shed_all("No Main device available")
            return

        while not getattr(self.plugin, "stopThread", False):
            try:
                # 1) Get best-available headroom from any enabled SolarSmart Main device
                headroom_w = self._get_current_headroom_w()
                if headroom_w is None:
                    # No main yet—shed all loads just in case
                    self._shed_all("No headroom available (no main readings)")
                    await asyncio.sleep(period_sec)
                    continue

                # 2) Collect eligible loads grouped by tier
                #loads_by_tier = self._collect_eligible_loads()
                # 2) Collect all loads grouped by tier + per-load skip reasons
                loads_by_tier, skip_reasons = self._collect_loads_with_reasons()

                # 2) Sync IsRunning from real devices (no commands sent)
                self._sync_running_flags_from_external(loads_by_tier)
                # Then enforce simplified daily deficit catch-up
                self._catchup_deficit_scheduler(loads_by_tier)

                # 3) Decide ON/OFF per tier (waterfall), but keep all in the table
                self._schedule_by_tier(loads_by_tier, headroom_w, skip_reasons)

                # First accrue runtime (updates run_today_secs before deficit calc)
                self._accrue_runtime_for_running_loads(period_sec)




            except asyncio.CancelledError:
                break
            except Exception as e:
                self.plugin.logger.exception(f"_ticker_load_scheduler: exception: {e}")

            # Sleep the cadence
            for _ in range(int(period_sec)):
                if getattr(self.plugin, "stopThread", False):
                    break
                await asyncio.sleep(1.0)

        if getattr(self.plugin, "debug2", False):
            self.plugin.logger.debug("_ticker_load_scheduler: exiting (stopThread set)")

    def _accrue_runtime_for_running_loads(self, period_sec: float):
        """
        Accrue per-tick runtime:
          - Quota minutes (RuntimeQuotaMins)
          - Window runtime (RuntimeWindowMins) for UI
          - Per-day runtime seconds (run_today_secs) for simplified catch-up
          - RemainingQuotaMins
        """
        add_m = self._quota_tick_minutes(period_sec)
        add_s = add_m * 60
        now_ts = time.time()
        today_key = datetime.now().strftime("%Y-%m-%d")

        for dev in indigo.devices.iter("self"):
            if dev.deviceTypeId != "solarsmartLoad" or not dev.enabled:
                continue
            props = dev.pluginProps or {}
            self._ensure_quota_anchor(dev, props, now_ts)

            st = self.plugin._load_state.setdefault(dev.id, {})

            # Daily rollover (reset daily counters)
            if st.get("today_key") != today_key:
                st["today_key"] = today_key
                st["run_today_secs"] = 0.0
                # If a catch-up run was active, clear the flag (we'll re-evaluate deficit next scheduler pass)
                if st.get("catchup_active"):
                    st["catchup_active"] = False
                    dev.updateStateOnServer("catchupActive", False)

            if not self._is_running(dev):
                # Still refresh RemainingQuotaMins (quota may have rolled over)
                self._quota_remaining_mins(dev, props, datetime.now())
                continue

            # 1. Quota minutes
            self._add_served_minutes(dev, add_m)

            # 2. Window runtime minutes (purely cosmetic)
            try:
                cur_window = int(dev.states.get("RuntimeWindowMins", 0) or 0)
                dev.updateStateOnServer("RuntimeWindowMins", cur_window + add_m)
            except Exception:
                pass

            # 3. Daily seconds (still maintained if you want daily analytics)
            try:
                st["run_today_secs"] = float(st.get("run_today_secs", 0.0)) + add_s
            except Exception:
                st["run_today_secs"] = add_s

            # 3b. Catch-up active runtime seconds (fallback minutes actually run under catch-up)
            if st.get("catchup_active"):
                try:
                    st["catchup_run_secs"] = float(st.get("catchup_run_secs", 0.0)) + add_s
                except Exception:
                    st["catchup_run_secs"] = add_s

            # 4. Refresh RemainingQuotaMins
            self._quota_remaining_mins(dev, props, datetime.now())

## Render Table Code Base

    def _render_table_png(self, table_text: str, filename: str = "scheduler.png",
                          font_size: int = 20, padding_px=(12, 10, 12, 12)) -> str:
        """
        Render a transparent PNG for the scheduler table with:
          • Emoji title/footer (native color)
          • Monospaced grid (perfect alignment)
          • Alternating row shading
          • Status/Action colored
          • Name column green when RUN
          • Tier medals (emoji) overlaid near the Tier cell
        Saves to self.plugin.saveDirectory/filename and returns the full path.
        """
        dbg = getattr(self.plugin, "debug4", False)
        log = self.plugin.logger

        if dbg:
            log.debug(f"_render_table_png: start len={len(table_text or '')}, file={filename}, font_size={font_size}")

        # ---------- Parse lines ----------
        lines = (table_text or "").splitlines()
        if not lines:
            lines = ["(no data)"]

        has_banner = (len(lines) >= 3 and "│" in (lines[1] if len(lines) > 1 else ""))
        header = lines[1] if has_banner else lines[0]
        sep_line = lines[2] if has_banner else (lines[1] if len(lines) > 1 else "")
        row_start = 3 if has_banner else 2
        row_end = -2 if has_banner and len(lines) >= 4 else len(lines)
        rows = lines[row_start:row_end] if len(lines) > 3 else []

        # Footer headroom
        src_footer = lines[-1] if len(lines) > 0 else ""
        m = re.search(r"(-?\d+)\s*W", src_footer)
        headroom_val = m.group(1) if m else "?"
        if dbg:
            log.debug(f"_render_table_png: rows={len(rows)}, parsed headroom={headroom_val}")

        # ---------- Fonts ----------
        # Monospace for grid
        mono = None
        for fp in ("/System/Library/Fonts/Menlo.ttc",
                   "/System/Library/Fonts/SFNSMono.ttf",
                   "/Library/Fonts/Menlo.ttc"):
            try:
                mono = ImageFont.truetype(fp, font_size)
                if dbg: log.debug(f"_render_table_png: mono font={fp}")
                break
            except Exception:
                pass
        if mono is None:
            mono = ImageFont.load_default()
            if dbg: log.debug("_render_table_png: mono font=default")

        # Emoji font (20px exact; draw WITHOUT fill to keep native color)
        try:
            emoji_font = ImageFont.truetype("/System/Library/Fonts/Apple Color Emoji.ttc", font_size)
            if dbg: log.debug("_render_table_png: emoji font=Apple Color Emoji @20")
        except Exception as e:
            emoji_font = mono
            if dbg: log.debug(f"_render_table_png: emoji font fallback to mono: {e}")

        # Helper: draw text; omit fill for emoji font to preserve native color
        def _is_emoji_font(font) -> bool:
            # Detect Apple Color Emoji or Noto Color Emoji by path/name
            path = getattr(font, "path", "") or ""
            base = os.path.basename(path).lower()
            return "emoji" in base or "color" in base  # e.g., "Apple Color Emoji.ttc", "NotoColorEmoji.ttf"

        def draw_text(drw, xy, text, font, color):
            """
            Draw text; for emoji fonts, preserve native color by:
            - omitting 'fill'
            - enabling embedded_color=True (if Pillow supports it)
            """
            if _is_emoji_font(font):
                try:
                    drw.text(xy, text, font=font, embedded_color=True)  # <-- keep emoji color
                except TypeError:
                    # Older Pillow: parameter not supported; still omit fill for best chance
                    drw.text(xy, text, font=font)
            else:
                drw.text(xy, text, font=font, fill=color)

        # ---------- Measure helpers ----------
        dummy = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        d0 = ImageDraw.Draw(dummy)

        def text_wh(s: str, font) -> tuple:
            bbox = d0.textbbox((0, 0), s, font=font)
            return (bbox[2] - bbox[0], bbox[3] - bbox[1])

        line_gap = max(2, int(font_size * 0.25))

        # Title (emoji + text segments)
        title_segments = [("🌞", emoji_font), ("⚡", emoji_font),
                          (" SolarSmart Load Scheduler ", mono),
                          ("⚡", emoji_font), ("🌞", emoji_font)]
        # Footer (emoji + text)
        footer_segments = [("📊", emoji_font), (f" Final Headroom: {headroom_val} W", mono)]

        # ---------- Column parsing ----------
        parts = [p.strip() for p in header.split("│")]
        idx_tier = next((i for i, p in enumerate(parts) if p.lower().startswith("tier")), None)
        idx_name = next((i for i, p in enumerate(parts) if "name" in p.lower()), None)
        idx_status = next((i for i, p in enumerate(parts) if p.lower().startswith("status")), None)
        idx_action = next((i for i, p in enumerate(parts) if p.lower().startswith("action")), None)

        cuts = [m.end() for m in re.finditer(r"│\s*", header)]
        cell_starts = [0] + cuts
        x_positions = []
        for start in cell_starts:
            prefix = header[:start]
            x_positions.append(d0.textbbox((0, 0), prefix, font=mono)[2])
        if len(x_positions) > len(parts):
            x_positions = x_positions[:len(parts)]
        while len(x_positions) < len(parts):
            x_positions.append(x_positions[-1] if x_positions else 0)

        if dbg:
            log.debug(f"_render_table_png: header parts={parts}")
            log.debug(
                f"_render_table_png: idx_tier={idx_tier}, idx_name={idx_name}, idx_status={idx_status}, idx_action={idx_action}")
            log.debug(f"_render_table_png: x_positions={x_positions}")

        # ---------- Colors ----------
        col_text = (230, 233, 238, 255)  # body text
        col_dim = (170, 175, 185, 255)  # header/separator text
        col_status_run = (40, 205, 65, 255)  # green
        col_status_off = (155, 160, 170, 255)  # gray
        col_action_start = (255, 179, 0, 255)  # amber
        col_action_keep = (90, 200, 250, 255)  # blue-ish
        col_action_stop = (255, 69, 58, 255)  # red
        col_action_skip = (200, 200, 205, 255)  # light gray
        row_fill_a = (255, 255, 255, 18)  # subtle alt fill
        row_fill_b = (255, 255, 255, 0)  # transparent

        status_marks = {"RUN": "✓", "OFF": "✗"}
        action_marks = {"START": "⚡", "KEEP": "▶", "STOP": "■", "SKIP": "·"}

        def tier_medal(t: int) -> str:
            return "🥇" if t == 1 else "🥈" if t == 2 else "🥉" if t == 3 else "🎯"

        # ---------- Measure canvas ----------
        title_w = sum(text_wh(seg, f)[0] for seg, f in title_segments)
        title_h = max(text_wh(seg, f)[1] for seg, f in title_segments)
        header_w, header_h = text_wh(header, mono)
        sep_w, sep_h = text_wh(sep_line, mono)
        row_ws = [text_wh(r, mono)[0] for r in rows] if rows else [0]
        row_hs = [text_wh(r, mono)[1] for r in rows] if rows else [0]
        max_row_w = max(row_ws) if row_ws else 0
        row_h = max(row_hs) if row_hs else header_h
        footer_w = sum(text_wh(seg, f)[0] for seg, f in footer_segments)
        footer_h = max(text_wh(seg, f)[1] for seg, f in footer_segments)

        block_w = max(title_w, header_w, sep_w, max_row_w, footer_w)
        block_h = title_h + line_gap + header_h + line_gap + sep_h + line_gap
        block_h += (len(rows) * (row_h + line_gap))
        block_h += sep_h + line_gap + footer_h

        pad_l, pad_t, pad_r, pad_b = padding_px
        img_w = block_w + pad_l + pad_r
        img_h = block_h + pad_t + pad_b

        if dbg:
            log.debug(f"_render_table_png: canvas {img_w}x{img_h}, rows={len(rows)}")

        # ---------- Canvas ----------
        img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        drw = ImageDraw.Draw(img)

        # ---------- Draw Title (emoji+text segments) ----------
        y = pad_t
        x = pad_l
        for seg, f in title_segments:
            draw_text(drw, (x, y), seg, f, col_text)  # emojis drawn with native color
            x += text_wh(seg, f)[0]
        y += title_h + line_gap

        # ---------- Header + separator ----------
        draw_text(drw, (pad_l, y), header, mono, col_dim)
        y += header_h + line_gap
        draw_text(drw, (pad_l, y), sep_line, mono, col_dim)
        y += sep_h + line_gap

        # ---------- Body rows ----------
        for i, line in enumerate(rows):
            # stripe
            drw.rectangle([pad_l, y - 2, pad_l + block_w, y + row_h + 1],
                          fill=row_fill_a if (i % 2 == 0) else row_fill_b)

            cells = [c for c in line.split("│")]
            while len(cells) < len(parts):
                cells.append("")

            # Is running?
            is_running = False
            if idx_status is not None and idx_status < len(cells):
                s_val = cells[idx_status].strip().upper()
                if "RUN" in s_val or s_val.startswith("✓"):
                    is_running = True

            for idx in range(len(parts)):
                raw = cells[idx].strip()
                text_out = raw
                color = col_text

                # Status normalize + color
                if idx_status is not None and idx == idx_status:
                    if is_running:
                        color = col_status_run
                        text_out = f"{status_marks['RUN']} RUN"
                    else:
                        color = col_status_off
                        text_out = f"{status_marks['OFF']} OFF"

                # Action color
                elif idx_action is not None and idx == idx_action:
                    up = raw.upper()
                    if "START" in up or "⚡" in up:
                        color = col_action_start;
                        text_out = f"{action_marks['START']} START"
                    elif "KEEP" in up or "▶" in up:
                        color = col_action_keep;
                        text_out = f"{action_marks['KEEP']} KEEP"
                    elif "STOP" in up or "■" in up:
                        color = col_action_stop;
                        text_out = f"{action_marks['STOP']} STOP"
                    elif "SKIP" in up or "·" in up:
                        color = col_action_skip;
                        text_out = f"{action_marks['SKIP']} SKIP"

                # Name green when running
                elif idx_name is not None and idx == idx_name and is_running:
                    color = col_status_run

                x_cell = pad_l + (x_positions[idx] if idx < len(x_positions) else 0)
                draw_text(drw, (x_cell, y), text_out, mono, color)

                # Tier medal overlay (emoji next to the Tier value)
                if idx_tier is not None and idx == idx_tier and emoji_font is not mono:
                    try:
                        t = int(raw.split()[0])
                        medal = tier_medal(t)
                        # Draw medal at the cell start; no fill (native color)
                        draw_text(drw, (x_cell, y), medal, emoji_font, col_text)
                    except Exception:
                        pass

            y += row_h + line_gap

        # ---------- Bottom separator + Footer ----------
        draw_text(drw, (pad_l, y), sep_line if sep_line else ("─" * max(10, len(header))), mono, col_dim)
        y += sep_h + line_gap

        x = pad_l
        for seg, f in footer_segments:
            draw_text(drw, (x, y), seg, f, col_text)  # emoji drawn color; text in mono
            x += text_wh(seg, f)[0]
        y += footer_h + line_gap

        # ---------- Save ----------
        out_path = os.path.join(self.plugin.saveDirectory, filename)
        img.save(out_path, format="PNG")

        if dbg:
            log.debug(f"_render_table_png: saved {out_path} ({img_w}x{img_h}), rows={len(rows)})")

        return out_path

    # ========== Headroom ==========
# ========== Headroom ==========
    def _get_current_headroom_w(self) -> int | None:
        """
        Pick the first enabled SolarSmart Main device and read Headroom if present.
        If Headroom missing, compute PV - Consumption (best effort).
        """
        for dev in indigo.devices.iter("self"):
            if dev.deviceTypeId != "solarsmartMain":
                continue
            if not dev.enabled:
                continue

            # Prefer existing Headroom state if available
            headroom = dev.states.get("Headroom", None)
            if headroom is not None:
                try:
                    return int(headroom)
                except Exception:
                    pass

            # Fallback compute
            pv = dev.states.get("SolarProduction", None)
            cons = dev.states.get("SiteConsumption", None)
            if pv is not None and cons is not None:
                try:
                    return int(pv) - int(cons)
                except Exception:
                    continue

        return None

    def _quota_window_minutes(self, props) -> int:
        """Map the device's window selection to minutes. Defaults to 24h."""
        # Expect props like: quotaWindow = "12h"|"24h"|"48h"|"72h"
        key = (props.get("quotaWindow") or "24h").lower()
        return {
            "12h": 12 * 60,
            "24h": 24 * 60,
            "48h": 48 * 60,
            "72h": 72 * 60,
        }.get(key, 24 * 60)

        # Add near other small helpers
    def _get_quota_window_days(self, props) -> int:
        """
        Return the number of days represented by the configured quota window.
        Uses Devices.xml values (12h, 24h, 1d, 2d, 3d) via _quota_horizon_minutes.
        12h -> 0.5 day -> ceil to 1 day (we distribute catch-up over 'days' whole days).
        """
        period = (props.get("quotaWindow") or "24h").lower()
        horizon_mins = self._quota_horizon_minutes(period)  # e.g., 720, 1440, 2880, 4320
        # convert minutes to days; ceil so sub-day windows still count as 1 day for distribution
        days = max(1, int(math.ceil(float(horizon_mins) / 1440.0)))
        return days


    def _maybe_rollover_quota(self, dev: indigo.Device, props: dict, now_ts: float = None):
        """
        If the configured quota window has elapsed since QuotaAnchorTs,
        reset RuntimeQuotaMins to 0 and advance the anchor by whole windows.
        Updates RemainingQuotaMins from maxRuntimePerQuotaMins.
        """
        try:
            now_ts = now_ts or time.time()
            st = self.plugin._load_state.setdefault(dev.id, {})
            # Ensure we have an anchor (hydrate should have done this, but be safe)
            anchor = st.get("quota_anchor_ts")
            if anchor is None:
                anchor = now_ts
                st["quota_anchor_ts"] = anchor
                dev.updateStateOnServer("QuotaAnchorTs", f"{anchor:.3f}")

            window_min = self._quota_window_minutes(props)
            if window_min <= 0:
                return

            elapsed_min = int((now_ts - anchor) // 60)
            if elapsed_min < window_min:
                return  # still inside the current window

            # One or more full windows have elapsed
            n_windows = elapsed_min // window_min
            advance_sec = n_windows * window_min * 60
            new_anchor = anchor + advance_sec

            # Reset served quota to 0 for the new window
            st["served_quota_mins"] = 0
            dev.updateStateOnServer("RuntimeQuotaMins", 0)
            dev.updateStateOnServer("RuntimeWindowMins", 0)
            # Reset catch-up run accumulation for new quota window
            st["catchup_run_secs"] = 0.0
            try:
                dev.updateStateOnServer("catchupRunTodayMins", 0)
                dev.updateStateOnServer("catchupRunWindowAccumMins", 0)
                # Recompute remaining fallback (will be full target again)
                dev.updateStateOnServer("catchupRemainingTodayMins", 0)  # will be recalculated next scheduler tick
            except Exception:
                pass
            # Recompute remaining from device props
            target = int(props.get("maxRuntimePerQuotaMins") or 0)
            remaining = max(0, target)
            dev.updateStateOnServer("RemainingQuotaMins", remaining)

            # Advance anchor
            st["quota_anchor_ts"] = new_anchor
            dev.updateStateOnServer("QuotaAnchorTs", f"{new_anchor:.3f}")

            # Friendly log
            self.plugin.logger.info(
                f"Quota window reset for '{dev.name}': advanced {n_windows} window(s) of {window_min} min; "
                f"RemainingQuotaMins set to {remaining}."
            )
        except Exception:
            self.plugin.logger.exception(f"_maybe_rollover_quota: error on {dev.name}")

    # ========== Gather loads ==========
    def _collect_loads_with_reasons(self) -> tuple[dict[int, list[indigo.Device]], dict[int, str]]:
        """
        Return:
          - dict of tier -> [all load devices], regardless of eligibility
          - dict of device.id -> skip_reason (string) for ineligible loads

        Ineligible loads are included in tables with SKIP(reason), but won't be
        considered for start decisions. We still enforce OFF for those cases.
        """
        tiers: dict[int, list[indigo.Device]] = {}
        skip_reasons: dict[int, str] = {}

        now = datetime.now()

        for dev in indigo.devices.iter("self"):
            if dev.deviceTypeId != "solarsmartLoad" or not dev.enabled:
                continue

            props = dev.pluginProps or {}
            reason: str | None = None

            # Determine reason (first-match)
            if not _dow_allowed(props, now):
                reason = "window (DOW)"
            elif not _time_window_allowed(props, now):
                reason = "window (time)"
            else:
                remaining = self._quota_remaining_mins(dev, props, now)
                if remaining <= 0:
                    reason = "quota"

            # Reflect enforcement for ineligible loads
            if reason and self._is_running(dev):
                self._ensure_off(dev, f"Not eligible: {reason}")

            tier = int(props.get("tier", 2))
            tiers.setdefault(tier, []).append(dev)
            if reason:
                skip_reasons[dev.id] = reason

        # Fairness: sort each tier by least served quota minutes (keeps all loads)
        for t in tiers:
            tiers[t].sort(key=lambda d: self._served_quota_mins(d))

        return dict(sorted(tiers.items(), key=lambda kv: kv[0])), skip_reasons


    # ========== ON/OFF decisions per tier ==========
    def _should_stop(self, dev, props, headroom_w: int) -> str | None:
        """
        Decide if a RUNNING load should STOP. Return reason if yes, else None.
        Minimal criteria:
          • Quota exhausted
          • Headroom can’t sustain rated power (with small hysteresis)
        """
        # Quota
        try:
            remaining = int(self._quota_remaining_mins(dev, props, datetime.now()))
        except Exception:
            remaining = 0
        if remaining <= 0:
            return "Quota exhausted"

        # Headroom sustain check (simple + tiny hysteresis)
        try:
            rated = int(float(props.get("ratedWatts", 0)) or 0)
        except Exception:
            rated = 0
        hysteresis_w = int(props.get("shedHysteresisW", 100) or 100)  # default 100W cushion

        # If removing this load's rated draw still leaves us NEGATIVE beyond hysteresis → stop it.
        # i.e., (headroom - rated) < -hysteresis
        if (headroom_w ) < (0 - hysteresis_w):
            return f"Headroom low (need {rated}W, have {headroom_w}W)"

        return None

    def _debug7_log_device(self, dev, *, tier, status, action,
                           run_min, remaining, needed_w, catchup,
                           skip_reason, headroom, starts_this_tick,
                           running_now):
        """
        Deep per-device diagnostic dump (debug7).
        Produces a single multi-line INFO block (readable) containing:
          • Decision context
          • Preferred (window/"quota") runtime counters
          • Catch-up status
          • External / control linkage
          • Power / thresholds
          • Concurrency snapshot
          • Raw internal st[] keys (sorted) for unexpected values

        Only emitted when plugin.debug7 is True.
        """
        if not getattr(self.plugin, "debug7", False):
            return

        try:
            st = self.plugin._load_state.get(dev.id, {}) or {}
            props = dev.pluginProps or {}
            ext_on = self.plugin._external_on_state(dev)

            # Choose log level (switch to .debug if you prefer quieter)
            log_func = self.plugin.logger.info

            # Friendly helpers
            def _pint(v):
                try:
                    return int(v)
                except Exception:
                    return v

            rated = 0
            try:
                rated = int(float(props.get("ratedWatts", 0)) or 0)
            except Exception:
                pass

            max_pref = props.get("maxRuntimePerQuotaMins")
            min_run = props.get("minRuntimeMins")
            max_run = props.get("maxRuntimeMins")
            quota_window = props.get("quotaWindow")
            surge_mult = props.get("surgeMultiplier")
            start_margin_pct = props.get("startMarginPct")
            keep_margin_pct = props.get("keepMarginPct")
            cooldown_mins = props.get("cooldownMins")

            served_quota = st.get("served_quota_mins")
            anchor_ts = st.get("quota_anchor_ts")
            start_ts = st.get("start_ts")
            cooldown_start = st.get("cooldown_start")

            catch_active_state = dev.states.get("catchupActive")
            catch_active_st = st.get("catchup_active")
            catch_remaining = dev.states.get("catchupRemainingTodayMins")
            catch_target = dev.states.get("catchupDailyTargetMins")
            catch_run_today = dev.states.get("catchupRunTodayMins")
            catch_run_secs = st.get("catchup_run_secs")

            pct = dev.states.get("RuntimeQuotaPct")
            run_quota_dev = dev.states.get("RuntimeQuotaMins")
            rem_dev = dev.states.get("RemainingQuotaMins")
            run_window_dev = dev.states.get("RuntimeWindowMins")

            # Time conversions
            def _fmt_ts(ts):
                if not ts:
                    return "—"
                try:
                    return datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
                except Exception:
                    return str(ts)

            # Raw internal state key dump (sorted)
            raw_state_lines = []
            for k in sorted(st.keys()):
                raw_state_lines.append(f"    {k}: {st.get(k)!r}")

            block = []
            block.append("")
            block.append(f"════════════════════════════════════════════════════════════════════════════════")
            block.append(f"🔍 DBG7 Device: {dev.name}  (id={dev.id})  Tier {tier}")
            block.append(f"════════════════════════════════════════════════════════════════════════════════")

            # Decision
            block.append(f"🧠 Decision")
            block.append(f"    Status: {status}   Action: {action}   Skip: {skip_reason or '—'}")
            block.append(f"    Headroom Now: {headroom} W   StartsThisTick: {starts_this_tick}   RunningNow: {running_now}")

            # Preferred window (window/quota) runtime
            block.append(f"⏱️ Preferred Window Runtime")
            block.append(f"    Served (internal): {served_quota} min   Window Run (state): {run_window_dev} min")
            block.append(f"    Remaining (state): {rem_dev} min   Remaining (shown row): {remaining} min")
            block.append(f"    Used (state RuntimeQuotaMins): {run_quota_dev} min   Percent: {pct}%")
            block.append(f"    Anchor Start (ts): {anchor_ts} ({_fmt_ts(anchor_ts)})")

            # Catch-up
            block.append(f"🛟 Catch-up / Fallback")
            block.append(f"    Active: state={catch_active_state} / mem={catch_active_st}   Catch-up Str Col: {catchup}")
            block.append(f"    Target: {catch_target} min   Remaining: {catch_remaining} min")
            block.append(f"    Run Today (state): {catch_run_today} min   Run (active secs mem): {catch_run_secs} s")
            block.append(f"    Window: {props.get('catchupWindowStart','??')} - {props.get('catchupWindowEnd','??')}   Enabled: {props.get('enableCatchup')}")

            # External / control
            block.append(f"🔌 External / Control")
            block.append(f"    External Device On: {ext_on}   IsRunning(state): {dev.states.get('IsRunning')}")
            block.append(f"    Start Ts: {start_ts} ({_fmt_ts(start_ts)})   Cooldown Start: {cooldown_start} ({_fmt_ts(cooldown_start)})")
            block.append(f"    Control Mode: {props.get('controlMode')}   Cooldown Mins: {cooldown_mins}")

            # Power & thresholds
            block.append(f"⚡ Power & Thresholds")
            block.append(f"    Rated: {rated} W   Needed (start threshold est): {needed_w} W")
            block.append(f"    Surge Mult: {surge_mult}   Start Margin %: {start_margin_pct}   Keep Margin %: {keep_margin_pct}")
            block.append(f"    Min Runtime: {min_run} min   Max Runtime (per start run): {max_run} min   Max Pref Window: {max_pref} min")
            block.append(f"    Quota Window Config: {quota_window}")

            # Concurrency
            block.append(f"📦 Concurrency Snapshot")
            block.append(f"    Running Now: {running_now}   Starts This Tick: {starts_this_tick}")

            # Raw internal dict
            block.append(f"🗂️ Raw Internal st[] Keys")
            if raw_state_lines:
                block.extend(raw_state_lines)
            else:
                block.append("    (empty)")

            block.append(f"────────────────────────────────────────────────────────────────────────────────")

            log_func("\n".join(block))

        except Exception:
            self.plugin.logger.exception(f"[DBG7] multi-line dump failed for {dev.name}")


    def _schedule_by_tier(self, loads_by_tier: dict[int, list[indigo.Device]], headroom_w: int, skip_reasons: dict[int, str]):

        max_concurrent = self._get_max_concurrent_loads()
        starts_this_tick = 0
        table_rows = []  # for final summary table

        # Rollover quota windows if needed (minimal change: just call it)
        now_ts = time.time()
        for _, devs in loads_by_tier.items():
            for d in devs:
                props = d.pluginProps or {}
                self._maybe_rollover_quota(d, props, now_ts)

        pv, con, bat, hdrm, ts = self._snapshot_main_metrics()
        headroom_w = hdrm if hdrm is not None else 0

        dbg = getattr(self.plugin, "debug2", False)
        if dbg:
            self.plugin.logger.debug(f"===== LOAD SCHEDULER TICK =====")
            self.plugin.logger.debug(f"Observed headroom from Main: {headroom_w} W (PV={pv}, Load={con}, Batt={bat})")

        # Build list of currently running (tier, dev), lowest priority first for shedding
        running_pairs = []
        for tier, devs in loads_by_tier.items():
            for d in devs:
                if self._is_running(d):
                    running_pairs.append((tier, d))
        # Sort so that highest tier numbers (lowest priority) are last
        running_pairs.sort(key=lambda x: x[0], reverse=False)

        # If negative headroom, shed exactly ONE load first, then re-evaluate next tick
        if headroom_w < 0 and running_pairs:
            headroom_w = self._shed_until_positive(headroom_w, running_pairs)

        # Recompute running count after potential shed
        running_now = sum(1 for _, d in running_pairs if self._is_running(d))

        # Process tiers in priority order
        for tier, devs in sorted(loads_by_tier.items()):
            for d in devs:
                props = d.pluginProps or {}
                try:
                    rated = int(float(props.get("ratedWatts", 0)) or 0)
                except Exception:
                    rated = 0

                remaining = self._quota_remaining_mins(d, props, datetime.now())
                surge_mult = float(props.get("surgeMultiplier", "1.2") or 1.2)
                start_margin = float(props.get("startMarginPct", "20") or 20.0) / 100.0
                needed_w = int(rated * surge_mult * (1.0 + start_margin))

                # Time already run in current window (simple, user‑visible)
                try:
                    run_min = int(d.states.get("RuntimeWindowMins", 0) or 0)
                except Exception:
                    run_min = 0

                # Catch-up descriptor (clear English)
                try:
                    props_enable_cu = bool(props.get("enableCatchup", False))
                    cu_active = bool(d.states.get("catchupActive", False))
                    cu_rem = int(d.states.get("catchupRemainingTodayMins") or 0)
                    cu_target = int(d.states.get("catchupDailyTargetMins") or 0)
                except Exception:
                    props_enable_cu = False
                    cu_active = False
                    cu_rem = 0
                    cu_target = 0

                if not props_enable_cu or cu_target <= 0:
                    catchup_str = "Off"
                elif cu_rem <= 0:
                    # Target configured & satisfied
                    catchup_str = "Met"
                else:
                    if cu_active:
                        catchup_str = f"ACT {cu_rem}m"
                    else:
                        catchup_str = f"Need {cu_rem}m"
                before_headroom = headroom_w

                # 1) If there is a skip reason, show row and do not attempt start
                skip_reason = skip_reasons.get(d.id)
                if skip_reason:
                    if self._is_running(d):
                        # Device became (or stayed) ON after mirroring; enforce policy again.
                        self._ensure_off(d, f"Not eligible: {skip_reason}")
                    status = "RUN" if self._is_running(d) else "OFF"
                    action = f"SKIP ({skip_reason})"

                    table_rows.append((tier, d.name, rated, status, run_min, remaining, needed_w, catchup_str, action))
                    self._debug7_log_device(
                        d,
                        tier=tier,
                        status=status,
                        action=action,
                        run_min=run_min,
                        remaining=remaining,
                        needed_w=needed_w,
                        catchup=catchup_str,
                        skip_reason=skip_reason,
                        headroom=headroom_w,
                        starts_this_tick=starts_this_tick,
                        running_now=running_now
                    )
                    # Do not modify headroom or attempt start
                    continue

                if self._is_running(d):
                    # Decide KEEP vs STOP
                    stop_reason = self._should_stop(d, props, headroom_w)
                    if stop_reason:
                        # Stop it and reclaim headroom
                        self._ensure_off(d, stop_reason)  # <- this must call self.plugin._execute_load_action(...)
                        running_now = max(0, running_now - 1)
                        action = "STOP"
                        status = "OFF"
                        if dbg:
                            self.plugin.logger.debug(f"[STOP] {d.name}: {stop_reason} → headroom now {headroom_w}W")
                    else:
                        action = "KEEP"
                        status = "RUN"
                    table_rows.append((tier, d.name, rated, status, run_min, remaining, needed_w, catchup_str, action))
                    self._debug7_log_device(
                        d,
                        tier=tier,
                        status=status,
                        action=action,
                        run_min=run_min,
                        remaining=remaining,
                        needed_w=needed_w,
                        catchup=catchup_str,
                        skip_reason=skip_reason,
                        headroom=headroom_w,
                        starts_this_tick=starts_this_tick,
                        running_now=running_now
                    )
                    continue

            # start constraints
                if starts_this_tick >= 1:
                    action = "SKIP (cap)"
                    status = "OFF"
                elif running_now >= max_concurrent:
                    action = "SKIP (conc)"
                    status = "OFF"
                elif remaining <= 0:
                    action = "SKIP (quota)"
                    status = "OFF"
                elif not self._cooldown_met(d, int(props.get("cooldownMins") or 0)):
                    action = "SKIP (cooldownn)"
                    status = "OFF"
                elif headroom_w >= needed_w:
                    # Start exactly ONE load per tick
                    self._ensure_on(d, "Start ok (threshold met)", headroom_snapshot=headroom_w)
                    starts_this_tick += 1
                    running_now += 1
                    action = "START"
                    status = "RUN"
                    if dbg:
                        self.plugin.logger.debug(f"[START] {d.name}: rated={rated}W → headroom now {headroom_w}W")
                else:
                    action = "SKIP (headroom)"
                    status = "OFF"

            # (if you still build the table, append row here)
                table_rows.append((tier, d.name, rated, status, run_min, remaining, needed_w, catchup_str, action))
                self._debug7_log_device(
                    d,
                    tier=tier,
                    status=status,
                    action=action,
                    run_min=run_min,
                    remaining=remaining,
                    needed_w=needed_w,
                    catchup=catchup_str,
                    skip_reason=skip_reason,
                    headroom=headroom_w,
                    starts_this_tick=starts_this_tick,
                    running_now=running_now
                )

        # OPTIONAL: final safety shed — shed only ONE more if still negative.
        # Comment this block out if you want strictly one action TOTAL per tick.
        # if headroom_w < 0 and running_pairs:
         #   headroom_w = self._shed_until_positive(headroom_w, running_pairs)

        # Print summary table
        # At end of _schedule_by_tier()

        # ---------- Build & publish table (Plain English headers) ----------
        try:
            status_marks = {"RUN": "✓", "OFF": "✗"}
            action_marks = {"START": "⚡", "KEEP": "▶", "STOP": "■", "SKIP": "·"}

            # Dynamic width for load names
            name_header = "Load"
            name_width = max(len(name_header), max((len(n) for _, n, *_ in table_rows), default=0))

            # Column fixed widths (tune as needed)
            w_tier = 4  # Tier
            w_rated = 8  # Rated W
            w_status = 6  # Status
            w_run = 8  # Time Run
            w_rem = 8  # Rem Mins
            w_need = 7  # Watts Needed
            w_cu = 10  # Catch-up
            w_action = 10  # Action

            def row_line(tier, name, rated, status, run_min, rem, need, cu, action):
                s_key = "RUN" if status.upper().startswith("RUN") else "OFF"
                a_up = action.upper()
                if a_up.startswith("START"):
                    a_key = "START"
                elif a_up.startswith("KEEP"):
                    a_key = "KEEP"
                elif a_up.startswith("STOP"):
                    a_key = "STOP"
                else:
                    a_key = "SKIP"
                s_icon = status_marks.get(s_key, " ")
                a_icon = action_marks.get(a_key, " ")
                return (
                    f"{tier:<{w_tier}} │ "
                    f"{name:<{name_width}} │ "
                    f"{rated:<{w_rated}} │ "
                    f"{s_icon} {s_key:<{w_status - 2}} │ "
                    f"{run_min:>{w_run}} │ "
                    f"{rem:>{w_rem}} │ "
                    f"{need:>{w_need}} │ "
                    f"{cu:<{w_cu}} │ "
                    f"{a_icon} {a_key:<{w_action - 2}}"
                )

            header = (
                f"{'Tier':<{w_tier}} │ "
                f"{name_header:<{name_width}} │ "
                f"{'Rated W':<{w_rated}} │ "
                f"{'Status':<{w_status}} │ "
                f"{'Time Run':>{w_run}} │ "
                f"{'Rem Mins':>{w_rem}} │ "
                f"{'Watts ':>{w_need}} │ "
                f"{'Catch-up':<{w_cu}} │ "
                f"{'Action':<{w_action}}"
            )

            sep_mid = (
                f"{'─' * w_tier}─┼─"
                f"{'─' * name_width}─┼─"
                f"{'─' * w_rated}─┼─"
                f"{'─' * w_status}─┼─"
                f"{'─' * w_run}─┼─"
                f"{'─' * w_rem}─┼─"
                f"{'─' * w_need}─┼─"
                f"{'─' * w_cu}─┼─"
                f"{'─' * w_action}"
            )

            banner_top = "🌞📈  SolarSmart Scheduler  📊🔌"
            banner_bottom = f"🌤️  Final headroom: {headroom_w} W"

            rows_str = "\n".join(
                row_line(tier, name, rated, status, run_min, rem, need, cu, action)
                for (tier, name, rated, status, run_min, rem, need, cu, action) in table_rows
            )

            if dbg:
                self.plugin.logger.debug(banner_top)
                self.plugin.logger.debug(header)
                self.plugin.logger.debug(sep_mid)
                for line in rows_str.splitlines():
                    self.plugin.logger.debug(line)
                self.plugin.logger.debug(sep_mid)
                self.plugin.logger.debug(banner_bottom)

            table_text = f"{banner_top}\n{header}\n{sep_mid}\n{rows_str}\n{sep_mid}\n{banner_bottom}"

            main_dev = None
            main_dev_id = self.plugin.pluginPrefs.get("main_device_id")
            if main_dev_id:
                try:
                    main_dev = indigo.devices[int(main_dev_id)]
                except Exception:
                    main_dev = None
            if not main_dev:
                main_dev = next(
                    (d for d in indigo.devices if d.deviceTypeId == "solarsmartMain" and d.enabled),
                    None
                )
            if main_dev:
                main_dev.updateStateOnServer("schedulerTable", table_text)
            out_path = self._render_table_png(table_text, filename="scheduler.png")
            if main_dev:
                main_dev.updateStateOnServer("schedulerImagePath", out_path)

        except Exception as e:
            self.plugin.logger.exception(f"Error building schedulerTable: {e}")

    # ---------- Keep & Start logic ----------
    def _evaluate_keep(self, dev: indigo.Device, headroom_w: int) -> int:
        """
        Decide whether to keep a running device ON.
        - Respect min runtime.
        - Use keep margin.
        - If headroom too low AND min runtime met, stop it.
        """
        props = dev.pluginProps or {}
        rated = _int(props.get("ratedWatts"), 0)
        keep_margin = float(props.get("keepMarginPct", "5") or 5.0) / 100.0
        min_runtime = _int(props.get("minRuntimeMins"), 0)

        # Min runtime not met?
        if not self._min_runtime_met(dev, min_runtime):
            return headroom_w  # force keep even if headroom dips

        # Check headroom with keep margin
        needed = int(rated * (1.0 + keep_margin))
        if headroom_w >= needed:
            return headroom_w  # keep on

        # Not enough room -> stop (cooldown applies)
        self._ensure_off(dev, f"Headroom low for keep (need ≥ {needed}W, have {headroom_w}W)")
        return headroom_w + rated  # freeing headroom approx by rated

    def _try_start(self, dev: indigo.Device, headroom_w: int) -> tuple[bool, int]:
        props = dev.pluginProps or {}
        rated = _int(props.get("ratedWatts"), 0)
        start_margin = float(props.get("startMarginPct", "20") or 20.0) / 100.0
        surge_mult = float(props.get("surgeMultiplier", "1.2") or 1.2)
        remaining = self._quota_remaining_mins(dev, props, datetime.now())
        if remaining <= 0:
            return (False, headroom_w)

        # Cooldown check
        if not self._cooldown_met(dev, _int(props.get("cooldownMins"), 0)):
            return (False, headroom_w)

        # Start threshold: rated * surge * (1+margin)
        needed = int(rated * surge_mult * (1.0 + start_margin))
        if getattr(self.plugin, "debug2", False):
            self.plugin.logger.debug(
                f"[THRESH] {dev.name}: needed={needed}W (rated={rated}, surge={surge_mult}, margin={start_margin * 100:.0f}%), headroom={headroom_w}W")

        if headroom_w < needed:
            return (False, headroom_w)

        self._ensure_on(dev, "Start ok (threshold met)")
        return (True, headroom_w - rated)  # approximate impact by rated watts

    # ---------- Shedding ----------


    # ---------- Shedding (one at a time) ----------
    def _shed_until_positive(self, headroom_w: int, running_by_tier: list[tuple[int, indigo.Device]]) -> int:
        """
        Shed exactly ONE running device to improve (ideally fix) negative headroom.
        Strategy:
          1) If headroom >= 0 -> no-op.
          2) Among running loads, prefer lowest-priority (highest tier).
          3) Try to pick the smallest rated load that covers the deficit.
          4) If none can cover the deficit alone, shed the smallest load in the lowest priority.
        Returns the new headroom after shedding that one device.
        """
        dbg = getattr(self.plugin, "debug2", False)
        if headroom_w >= 0 or not running_by_tier:
            if dbg:
                self.plugin.logger.debug(f"_shed_until_positive: headroom ok ({headroom_w}W) or no running loads")
            return headroom_w

        deficit = -headroom_w
        if dbg:
            self.plugin.logger.debug(f"_shed_until_positive: starting headroom={headroom_w}W (deficit={deficit}W)")

        # Build candidate list: (tier, dev, rated)
        candidates = []
        for tier, dev in running_by_tier:
            try:
                rated = int(float((dev.pluginProps or {}).get("ratedWatts", 0)) or 0)
            except Exception:
                rated = 0
            if self._is_running(dev) and rated > 0:
                candidates.append((tier, dev, rated))

        if not candidates:
            if dbg:
                self.plugin.logger.debug("_shed_until_positive: no valid running candidates to shed")
            return headroom_w

        # Prefer lowest priority (highest tier). Within that, pick the smallest rated that fixes the deficit.
        # Split into two sets for clarity: those that can fix, those that can't.
        candidates.sort(key=lambda tdr: (tdr[0], tdr[2]))  # tier asc first; we’ll reverse for lowest priority
        # Lowest priority tier value:
        max_tier = max(t for t, _, _ in candidates)

        in_lowest_priority = [x for x in candidates if x[0] == max_tier]
        can_fix = [x for x in in_lowest_priority if x[2] >= deficit]
        if can_fix:
            # pick the smallest that fixes
            tier, dev, rated = sorted(can_fix, key=lambda tdr: tdr[2])[0]
            choice_reason = f"lowest-priority tier {tier}, minimal rated covering deficit"
        else:
            # None in lowest priority can fix; shed smallest in lowest priority to be gentle
            tier, dev, rated = sorted(in_lowest_priority, key=lambda tdr: tdr[2])[0]
            choice_reason = f"lowest-priority tier {tier}, smallest rated (partial improvement)"

        if dbg:
            self.plugin.logger.debug(
                f"_shed_until_positive: shedding ONE → {dev.name} (tier={tier}, rated={rated}W) — {choice_reason}"
            )

        self._ensure_off(dev, "Emergency shed (headroom negative)")

        if dbg:
            self.plugin.logger.debug(f"_shed_until_positive: new headroom={headroom_w}W after shedding {dev.name}")

        return headroom_w

    # ========== Runtime/Quota/Cooldown state tracking ==========
    def _is_running(self, dev: indigo.Device) -> bool:
        # Device state is the source of truth
        try:
            return bool(dev.states.get("IsRunning", False))
        except Exception:
            return bool(self.plugin._load_state.get(dev.id, {}).get("IsRunning", False))

    def _mark_running(self, dev: indigo.Device, running: bool):
        st = self.plugin._load_state.setdefault(dev.id, {})
        if running:
            now = time.time()
            st["start_ts"] = now
            st["IsRunning"] = True  # mirror
            dev.updateStateOnServer("LastStartTs", f"{now:.3f}")
            dev.updateStateOnServer("IsRunning", True)
            dev.updateStateOnServer("Status", "RUNNING")
        else:
            st["start_ts"] = None
            st["IsRunning"] = False  # mirror
            dev.updateStateOnServer("LastStartTs", "")
            dev.updateStateOnServer("IsRunning", False)
            dev.updateStateOnServer("Status", "OFF")

    def _served_quota_mins(self, dev: indigo.Device) -> int:
        st = self.plugin._load_state.setdefault(dev.id, {})
        return int(st.get("served_quota_mins", 0))

    def _set_served_quota_mins(self, dev, minutes: int):
        """Set served minutes and mirror to device state RuntimeQuotaMins."""
        st = self.plugin._load_state.setdefault(dev.id, {})
        st["served_quota_mins"] = int(minutes)
        try:
            dev.updateStateOnServer("RuntimeQuotaMins", int(minutes))
        except Exception:
            pass

    def _add_served_minutes(self, dev, minutes: int):
        """Increment served minutes and mirror to device state."""
        self._set_served_quota_mins(dev, self._served_quota_mins(dev) + int(minutes))

    def _min_runtime_met(self, dev: indigo.Device, min_runtime_mins: int) -> bool:
        if min_runtime_mins <= 0:
            return True
        st = self.plugin._load_state.get(dev.id, {})
        ts = st.get("start_ts")
        if not ts:
            return True
        return (time.time() - ts) >= (min_runtime_mins * 60)

    def _cooldown_met(self, dev: indigo.Device, cooldown_mins: int) -> bool:
        if cooldown_mins <= 0:
            return True
        st = self.plugin._load_state.get(dev.id, {})
        if self._is_running(dev):
            return True
        t0 = st.get("cooldown_start")
        if not t0:
            return True
        return (time.time() - t0) >= (cooldown_mins * 60)

    def _quota_remaining_mins(self, dev, props, now_dt: datetime) -> int:
        """
        Return remaining minutes for the quota window and update RemainingQuotaMins state.
        Ensures the anchor is valid before computing.
        """
        self._ensure_quota_anchor(dev, props, time.time())
        max_per_quota = int(props.get("maxRuntimePerQuotaMins") or 0) or 0
        used = self._served_quota_mins(dev)
        remaining = max(0, max_per_quota - used) if max_per_quota > 0 else 0
        try:
            dev.updateStateOnServer("RemainingQuotaMins", remaining)
        except Exception:
            pass
        return remaining

    def _quota_tick_minutes(self, period_sec: float) -> int:
        """How many minutes to accrue per scheduler tick (usually 1 for a 60s tick)."""
        return max(1, int(round(period_sec / 60.0)))

    def _quota_horizon_minutes(self, period: str) -> int:
        """Return the rolling quota horizon in minutes based on device props."""
        return {
            "12h": 12 * 60,
            "24h": 24 * 60,
            "1d": 24 * 60,
            "2d": 48 * 60,
            "3d": 72 * 60,
        }.get((period or "24h").lower(), 24 * 60)

    def _ensure_quota_anchor(self, dev, props, now_ts: float):
        st = self.plugin._load_state.setdefault(dev.id, {})
        period = (props.get("quotaWindow") or "24h").lower()
        horizon_mins = self._quota_horizon_minutes(period)
        anchor = st.get("quota_anchor_ts")

        # First-time: set anchor, do NOT reset minutes
        if anchor is None:
            st["quota_anchor_ts"] = now_ts
            dev.updateStateOnServer("QuotaAnchorTs", f"{now_ts:.3f}")
            return

        # Rollover: reset minutes only if horizon passed
        if (now_ts - anchor) >= (horizon_mins * 60):
            st["quota_anchor_ts"] = now_ts
            st["served_quota_mins"] = 0
            dev.updateStateOnServer("QuotaAnchorTs", f"{now_ts:.3f}")
            dev.updateStateOnServer("RuntimeQuotaMins", 0)
            target = int(props.get("maxRuntimePerQuotaMins") or 0)
            dev.updateStateOnServer("RemainingQuotaMins", target if target > 0 else 0)
            dev.updateStateOnServer("RuntimeWindowMins", 0)
            if getattr(self, "debug2", False):
                self.plugin.logger.debug(f"{dev.name}: quota window rolled over, counters reset")

    # ========== Actuation wrappers ==========
    def _ensure_on(self, dev, reason: str, headroom_snapshot: int | None = None):
        """Turn a load ON (if not already), set IsRunning immediately, and book-keep."""
        try:
            if self._is_running(dev):
                # already marked running; just update reason for visibility
                dev.updateStateOnServer("LastReason", reason)
                return

            # If not marked as runningfire the action group
            else:
                self.plugin._execute_load_action(dev, turn_on=True, reason=reason)

            # mark running NOW (don’t wait for external device feedback)
            st = self.plugin._load_state.setdefault(dev.id, {})
            st["start_ts"] = time.time()
            self._mark_running(dev, True)
            dev.updateStateOnServer("LastReason", reason)
            self._update_runtime_progress(dev)
            # Build INFO line
            props = dev.pluginProps or {}
            tier = props.get("tier", "—")
            try:
                rated = int(float(props.get("ratedWatts", 0)) or 0)
            except Exception:
                rated = 0

            pv, con, bat, hdrm_main, ts = self._snapshot_main_metrics()
            hdrm = headroom_snapshot if headroom_snapshot is not None else hdrm_main

            self.plugin.logger.info(
                f"Starting '{dev.name}' (Tier {tier}, {self._fmt_w(rated)}). "
                f"Headroom {self._fmt_w(hdrm)}; PV {self._fmt_w(pv)}, Load {self._fmt_w(con)}, "
                f"Battery {self._fmt_w(bat)}. Reason: {reason}"
            )

            if getattr(self.plugin, "debug2", False):
                self.plugin.logger.debug(f"_ensure_on: {dev.name} → ON ({reason})")
        except Exception:
            self.plugin.logger.exception(f"_ensure_on: error while starting {dev.name}")

    def _ensure_off(self, dev, reason: str):
        """Turn a running load OFF, clear flags, and always log INFO."""
        try:
            was_running = self._is_running(dev)
            if not was_running:
                # Already off – just record reason and leave quietly
                dev.updateStateOnServer("LastReason", reason)
                ext_on = self.plugin._external_on_state(dev)
                if ext_on:  # mismatch: external device is ON while we think OFF
                    if getattr(self.plugin, "debug2", False):
                        self.plugin.logger.debug(f"_ensure_off: {dev.name} external ON -> sending OFF ({reason})")
                    # Suppress state overwrites; we'll refresh percent after.
                    self.plugin._execute_load_action(dev, turn_on=False, reason=reason, update_states=False)
                    dev.updateStateOnServer("IsRunning", False)
                else:
                    # No external ON, so we are already OFF
                    if getattr(self.plugin, "debug2", False):
                        self.plugin.logger.debug(f"_ensure_off: {dev.name} already OFF ({reason})")
                self._update_runtime_progress(dev)
                return

            # Snapshot observed metrics BEFORE stopping
            pv, con, bat, hdrm, ts = self._snapshot_main_metrics()

            # Execute OFF and clear flags
            st = self.plugin._load_state.setdefault(dev.id, {})
            ##  Only do above otherwise sent everytimg self.plugin._execute_load_action(dev, turn_on=False, reason=reason)
            ran_secs = None
            if st.get("start_ts"):
                try:
                    ran_secs = max(0, int(time.time() - st["start_ts"]))
                except Exception:
                    ran_secs = None

            self._mark_running(dev, False)
            dev.updateStateOnServer("LastReason", reason)
            self._update_runtime_progress(dev)
            # Always log OFF at INFO
            ran_txt = ""
            if ran_secs is not None:
                m, s = divmod(ran_secs, 60)
                ran_txt = f" after {m}m {s}s"
            self.plugin.logger.info(
                f"Stopping '{dev.name}'{ran_txt}. Reason: {reason}. "
                f"Headroom {self._fmt_w(hdrm)}; PV {self._fmt_w(pv)}, "
                f"Load {self._fmt_w(con)}, Battery {self._fmt_w(bat)}"
            )

        except Exception:
            self.plugin.logger.exception(f"_ensure_off: error while stopping {dev.name}")


class Plugin(indigo.PluginBase):
    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)
        self._init_timezones(pluginPrefs)
        pfmt = logging.Formatter(
            '%(asctime)s.%(msecs)03d\t%(levelname)s\t%(name)s.%(funcName)s:%(filename)s:%(lineno)s:\t%(message)s',
            datefmt='%d-%m-%Y %H:%M:%S')
        self.plugin_file_handler.setFormatter(pfmt)
        ################################################################################
        # Setup Logging
        ################################################################################
        self.logger.setLevel(logging.DEBUG)
        try:
            self.logLevel = int(self.pluginPrefs["showDebugLevel"])
            self.fileloglevel = int(self.pluginPrefs["showDebugFileLevel"])
        except:
            self.logLevel = logging.INFO
            self.fileloglevel = logging.DEBUG

        raw = self.pluginPrefs.get("frequency_checks", 1)  # minutes
        self.logger.debug(f"frequency_checks: {raw}")
        try:
            self.time_for_checks_frequency = float(raw)
        except Exception:
            self.time_for_checks_frequency = 1.0  # default 1 minute
        if self.time_for_checks_frequency <= 0:
            self.time_for_checks_frequency = 1.0

        self.logger.removeHandler(self.indigo_log_handler)
        self.indigo_log_handler = IndigoLogHandler(pluginDisplayName, logging.INFO)
        ifmt = logging.Formatter("%(message)s")
        self.indigo_log_handler.setFormatter(ifmt)
        self.indigo_log_handler.setLevel(self.logLevel)
        self.logger.addHandler(self.indigo_log_handler)
        self.pluginprefDirectory = '{}/Preferences/Plugins/com.GlennNZ.indigoplugin.SmartSolar'.format(indigo.server.getInstallFolderPath())

        self.startingUp = True
        self.pluginIsInitializing = True
        self.pluginIsShuttingDown = False
        self.prefsUpdated = False
        self.logger.info(u"")
        self.logger.info("{0:=^130}".format(" Initializing New Plugin Session "))
        self.logger.info("{0:<30} {1}".format("Plugin name:", pluginDisplayName))
        self.logger.info("{0:<30} {1}".format("Plugin version:", pluginVersion))
        self.logger.info("{0:<30} {1}".format("Plugin ID:", pluginId))
        self.logger.info("{0:<30} {1}".format("Indigo version:", indigo.server.version))
        self.logger.info("{0:<30} {1}".format("Silicon version:", str(platform.machine())))
        self.logger.info("{0:<30} {1}".format("Python version:", sys.version.replace('\n', '')))
        self.logger.info("{0:<30} {1}".format("Python Directory:", sys.prefix.replace('\n', '')))

        self._log_effective_source_summary()
        #self.logger.info(u"{0:=^130}".format(""))

        self.triggers = {}
        # Internal in-memory map: { device.id: { state data } }
        self._load_state = {}

        # Change to logging
        pfmt = logging.Formatter('%(asctime)s.%(msecs)03d\t[%(levelname)8s] %(name)20s.%(funcName)-25s%(msg)s',
                                 datefmt='%Y-%m-%d %H:%M:%S')
        self.plugin_file_handler.setFormatter(pfmt)

        self.debug = self.pluginPrefs.get('showDebugInfo', False)
        self.debug1 = self.pluginPrefs.get('debug1', False)
        self.debug2 = self.pluginPrefs.get('debug2', False)
        self.debug3 = self.pluginPrefs.get('debug3', False)
        self.debug4 = self.pluginPrefs.get('debug4',False)
        self.debug5 = self.pluginPrefs.get('debug5', False)
        self.debug6 = self.pluginPrefs.get('debug6', False)
        self.debug7 = self.pluginPrefs.get('debug7', False)
        self.debug8 = self.pluginPrefs.get('debug8', False)
        self.indigo_log_handler.setLevel(self.logLevel)
        self.plugin_file_handler.setLevel(self.fileloglevel)
        self.pluginIsInitializing = False

    # ========================

    def _init_timezones(self, prefs):
        """
        Establish a default ZoneInfo for the plugin. If user prefs contain a timezone
        string (e.g., 'Australia/Sydney'), prefer it; else use system local.
        """
        tz_name = (prefs or {}).get("defaultTimezone")  # optional preference key
        self._default_tz = None
        if ZoneInfo:
            if tz_name:
                try:
                    self._default_tz = ZoneInfo(tz_name)
                except Exception:
                    self.logger.warning(f"Invalid timezone preference '{tz_name}', falling back to system local.")
            if self._default_tz is None:
                # System local: get current offset name then build ZoneInfo if possible
                try:
                    # This may not always map cleanly; if not, leave None and fall back to naive local
                    self._default_tz = ZoneInfo(datetime.now().astimezone().tzinfo.key)  # type: ignore
                except Exception:
                    try:
                        # Last resort: use the tzinfo directly (may be a fixed offset)
                        self._default_tz = datetime.now().astimezone().tzinfo  # type: ignore
                    except Exception:
                        self._default_tz = None
        if self._default_tz is None:
            # Fallback: fixed local offset (not DST-dynamic); only if zoneinfo unavailable
            local = datetime.now().astimezone().tzinfo
            self._default_tz = local
        self.logger.debug(f"Forecast default timezone set to: {getattr(self._default_tz, 'key', self._default_tz)}")

    # Core updater
    # ========================
    def _safe_int(self, v):
        try:
            if v in (None, "", "-1"):
                return None
            return int(str(v).strip())
        except Exception:
            return None


    def _update_solarsmart_states(self, dev: indigo.Device) -> None:
        """
        Reads configured PV/Consumption/Battery, normalizes to Watts (numbers only),
        computes Headroom best-effort, and publishes to the SolarSmart device states.
        """
        props = dev.pluginProps or {}
        dbg2 = getattr(self, "debug2", False)
        # --- Read numeric values ---
        pv_w = self.read_pv_watts(props)  # required
        cons_w = self.read_consumption_watts(props)  # optional
        batt_w = self.read_battery_watts(props)  # optional
        # Defaults for grid logic (may be overridden by Test device)
        use_grid_headroom = bool(props.get("useGridHeadroom", False))
        grid_w = None

        # --- Override with SolarSmart Test Source if present/enabled ---
        try:
            test_dev = next((d for d in indigo.devices
                             if d.deviceTypeId == "solarsmartTest" and d.enabled), None)
        except Exception:
            test_dev = None

        if test_dev:
            tprops = test_dev.pluginProps or {}

            def _pint(val, default=None):
                try:
                    s = (val or "").strip()
                    if s == "" and default is not None:
                        return default
                    return int(float(s))
                except Exception:
                    return default

            pv_override = _pint(tprops.get("pvTestW"), 0)  # default to 0 if blank
            cons_override = _pint(tprops.get("consTestW"), None)  # optional
            batt_override = _pint(tprops.get("battTestW"), None)  # optional (neg = discharge)
            # NEW: grid override from Test device
            test_use_grid = bool(tprops.get("useGridHeadroom", False))
            grid_test_w = _pint(tprops.get("gridTestW"), None)  # optional

            pv_w = pv_override
            cons_w = cons_override if cons_override is not None else cons_w
            batt_w = batt_override if batt_override is not None else batt_w

            # Optional: mirror props to Test device states for Control Page use
            try:
                test_dev.updateStateOnServer("SolarTestW", pv_override)
                if cons_override is not None:
                    test_dev.updateStateOnServer("ConsumptionTestW", cons_override)
                if batt_override is not None:
                    test_dev.updateStateOnServer("BatteryTestW", batt_override)
                if grid_test_w is not None:
                    test_dev.updateStateOnServer("GridTestW", grid_test_w)
                test_dev.updateStateOnServer("Status", "OVERRIDING SolarSmart Main")
                test_dev.updateStateOnServer("LastUpdate", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            except Exception:
                pass

            # Informative log (once per tick)
            # If Test device requests grid-only and provides a value, USE IT
            if test_use_grid and (grid_test_w is not None):
                use_grid_headroom = True
                grid_w = grid_test_w
                self.logger.info(
                    f"Over-riding Solar Data with SolarSmart Test device '{test_dev.name}': "
                    f"Grid={grid_w} W (grid-only mode), PV={pv_w} W, "
                    f"Cons={'None' if cons_w is None else cons_w} W, "
                    f"Batt={'None' if batt_w is None else batt_w} W"
                )
            else:
                # Regular test log (no grid-only)
                self.logger.info(
                    f"Over-riding Solar Data with SolarSmart Test device '{test_dev.name}': PV={pv_w} W, "
                    f"Cons={'None' if cons_w is None else cons_w} W, "
                    f"Batt={'None' if batt_w is None else batt_w} W"
                )

        if getattr(self, "debug2", False):
            self.logger.debug(f"_update_solarsmart_states: raw PV={pv_w}, Cons={cons_w}, Batt={batt_w}")

        if grid_w is None and use_grid_headroom:
            # you likely already have this helper; if not, add the one I sent earlier
            grid_w = self.read_grid_watts(props)

        if dbg2:
            self.logger.debug(f"_update_solarsmart_states: useGridHeadroom={use_grid_headroom}, Grid={grid_w}, "
                              f"PV={pv_w}, Cons={cons_w}, Batt={batt_w}")

        # --- Publish required + optional states ---
        # If PV is missing (misconfig), publish 0 so UI isn’t blank and log it.
        if pv_w is None:
            if getattr(self, "debug2", False):
                self.logger.debug("_update_solarsmart_states: PV missing -> publishing 0")
            pv_w = 0.0

        # Best-effort headroom (if consumption known). If batt_w is positive (charging), subtract it
        # from headroom as it already consumes PV/export capacity. If negative (discharging), it adds headroom.
        headroom = None

        if use_grid_headroom and (grid_w is not None):
            # Import positive → deficit → negative headroom; Export negative → positive headroom
            headroom = -grid_w
            # Optional info once per tick to make mode obvious
            if dbg2:
                self.logger.debug(f"_update_solarsmart_states: using GRID for headroom => {headroom} W")
        else:
            if cons_w is not None:
                headroom = pv_w - cons_w
                if batt_w is not None:
                    # Subtract only charging power (positive); negative (discharge) increases margin
                    headroom -= max(batt_w, 0.0)

        # --- Push to server (Integers for W states) ---
        try:
            dev.updateStateOnServer("SolarProduction", int(round(pv_w)))
        except Exception as e:
            if getattr(self, "debug2", False):
                self.logger.debug(f"Failed updating SolarProduction: {e}")

        if cons_w is not None:
            try:
                dev.updateStateOnServer("SiteConsumption", int(round(cons_w)))
            except Exception as e:
                if getattr(self, "debug2", False):
                    self.logger.debug(f"Failed updating SiteConsumption: {e}")

        if batt_w is not None:
            try:
                dev.updateStateOnServer("BatteryPower", int(round(batt_w)))
            except Exception as e:
                if getattr(self, "debug2", False):
                    self.logger.debug(f"Failed updating BatteryPower: {e}")
        # NEW: publish GridPower if present
        if grid_w is not None:
            try:
                dev.updateStateOnServer("GridPower", int(grid_w))
            except Exception as e:
                if dbg2:
                    self.logger.debug(f"Failed updating GridPower: {e}")

        if headroom is not None:
            try:
                dev.updateStateOnServer("Headroom", int(round(headroom)))
            except Exception as e:
                if getattr(self, "debug2", False):
                    self.logger.debug(f"Failed updating Headroom: {e}")

        # Timestamp for sanity
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            dev.updateStateOnServer("LastUpdate", ts)
        except Exception as e:
            if getattr(self, "debug2", False):
                self.logger.debug(f"Failed updating LastUpdate: {e}")

        if getattr(self, "debug2", False):
            self.logger.debug(
                f"_update_solarsmart_states: published PV={pv_w}, Cons={cons_w}, Batt={batt_w}, Headroom={headroom}"
            )

    # ========================
    # Small helper
    # ========================

    def _is_valid_choice(self, v) -> bool:
        try:
            return int(v) > 0
        except Exception:
            return False

    def _is_valid_device_choice(self, v) -> bool:
        """Valid if it's a positive int-like value (device id)."""
        try:
            return int(str(v).strip()) > 0
        except Exception:
            return False

    def _is_valid_state_choice(self, v) -> bool:
        """Valid if it's a non-empty string not equal to '-1' (state id)."""
        s = "" if v is None else str(v).strip()
        return s != "" and s != "-1"


    def __del__(self):
        if self.debugLevel >= 2:
            self.debugLog(u"__del__ method called.")
        indigo.PluginBase.__del__(self)

    def _pint(self, val, default=None):
        try:
            s = (val or "").strip()
            if s == "" and default is not None:
                return default
            return int(round(float(s)))
        except Exception:
            return default

    def _pint(self, val, default=None):
        try:
            s = (val or "").strip()
            if s == "" and default is not None:
                return default
            return int(round(float(s)))
        except Exception:
            return default

    def _log_effective_source_summary(self):
        """
        Log a plain-English summary for whichever source will be used:
        - Test device (if enabled), else
        - Main device
        Includes current readings and the computed headroom.
        """
        # Prefer enabled Test device
        test_dev = next((d for d in indigo.devices
                         if d.deviceTypeId == "solarsmartTest" and d.enabled), None)
        if test_dev:
            tprops = test_dev.pluginProps or {}
            use_grid = bool(tprops.get("useGridHeadroom", False))

            pv_w = self._pint(tprops.get("pvTestW"), 0)
            cons_w = self._pint(tprops.get("consTestW"), None)
            batt_w = self._pint(tprops.get("battTestW"), None)
            grid_w = self._pint(tprops.get("gridTestW"), None) if use_grid else None

            # Compute headroom
            headroom = None
            if use_grid and grid_w is not None:
                headroom = -grid_w
            else:
                if cons_w is not None:
                    batt_adj = max(0, batt_w) if batt_w is not None else 0
                    headroom = pv_w - cons_w - batt_adj

            # Log banner
            self.logger.info(u"{0:=^130}".format(""))
            self.logger.info("🔌🔌 SolarSmart Setup 🔌🔌 (Test Override Active.  Main Device ignored.)")
            self.logger.info("")
            if use_grid and grid_w is not None:
                flow_desc = "⚡ Importing from grid" if grid_w > 0 else (
                    "🔋 Exporting to grid" if grid_w < 0 else "➖ Balanced (no net flow)")
                self.logger.info("Mode: GRID-only — Net grid power will be the ONLY factor in headroom calculations.")
                self.logger.info("This means PV, battery, and site consumption values are ignored.")
                self.logger.info(f"Current grid reading: {grid_w} W → {flow_desc}")
            else:
                self.logger.info("Mode: PV/Consumption/Battery — Headroom is calculated from:")
                self.logger.info("PV Production − Site Consumption − Battery Charging (if charging)")
                pv_txt = f"{pv_w} W" if pv_w is not None else "unknown"
                cons_txt = f"{cons_w} W" if cons_w is not None else "unknown"
                batt_txt = f"{batt_w} W" if batt_w is not None else "none"
                self.logger.info(f"Current PV: {pv_txt}, Consumption: {cons_txt}, Battery: {batt_txt}")

            if headroom is not None:
                if headroom > 0:
                    self.logger.info(f"Positive headroom of {headroom} W — loads may be allowed to start.")
                elif headroom < 0:
                    self.logger.info(f"Negative headroom of {headroom} W — loads may need to stop.")
                else:
                    self.logger.info("Zero headroom — no spare power available for loads.")
            self.logger.info("")
            self.logger.info(u"{0:=^130}".format(""))
            return  # done

        # Otherwise, log Main device config if present
        main = next((d for d in indigo.devices
                     if d.deviceTypeId == "solarsmartMain" and d.enabled), None)
        if not main:
            return

        props = main.pluginProps or {}
        use_grid = bool(props.get("useGridHeadroom", False))

        # Pull readings using your existing readers that accept props
        grid_w = None
        if use_grid and hasattr(self, "read_grid_watts"):
            try:
                grid_w = self.read_grid_watts(props)
            except Exception:
                grid_w = None

        pv_w = self.read_pv_watts(props)
        cons_w = self.read_consumption_watts(props)
        batt_w = self.read_battery_watts(props)

        headroom = None
        if use_grid and grid_w is not None:
            headroom = -int(grid_w)
        else:
            if pv_w is None:
                pv_w = 0
            if cons_w is not None:
                batt_adj = max(0.0, float(batt_w)) if batt_w is not None else 0
                headroom = int(round(pv_w - cons_w - batt_adj))

        # Log banner
        self.logger.info(u"{0:=^130}".format(""))
        self.logger.info("🔌🔌 SolarSmart Setup 🔌🔌")
        self.logger.info("")
        if use_grid and grid_w is not None:
            flow_desc = "⚡ Importing from grid" if grid_w > 0 else (
                "🔋 Exporting to grid" if grid_w < 0 else "➖ Balanced (no net flow)")
            self.logger.info("Mode: Net Grid mode only — Net grid power will be the only factor in headroom calculations.")
            self.logger.info("(This is likely the most accurate, if your Meters support it)")
            self.logger.info("This means PV, battery, and site consumption values are reported but ignored.")
            self.logger.info(f"Current grid reading: {grid_w} W → {flow_desc}")
        else:
            self.logger.info("Mode: PV/Consumption/Battery — Headroom is calculated from:")
            self.logger.info("PV Production − Site Consumption − Battery Charging (if charging)")
            pv_txt = f"{int(pv_w)} W" if pv_w is not None else "unknown"
            cons_txt = f"{int(cons_w)} W" if cons_w is not None else "unknown"
            batt_txt = f"{int(float(batt_w))} W" if batt_w is not None else "none"
            self.logger.info(f"Current PV: {pv_txt}, Consumption: {cons_txt}, Battery: {batt_txt}")

        if headroom is not None:
            if headroom > 0:
                self.logger.info(f"Positive headroom of {headroom} W — loads may be allowed to start.")
            elif headroom < 0:
                self.logger.info(f"Negative headroom of {headroom} W — loads may need to stop.")
            else:
                self.logger.info("Zero headroom — no spare power available for loads.")
        self.logger.info("")
        self.logger.info(u"{0:=^130}".format(""))

    def closedDeviceConfigUi(self, valuesDict, userCancelled, typeId, devId):
        # Handle Test device logging too
        if typeId == "solarsmartTest":
            try:
                dev = indigo.devices[devId]
            except Exception:
                return valuesDict
            # Always show what will be used effectively
            self._log_effective_source_summary()
            return valuesDict

        if typeId != "solarsmartMain":
            return valuesDict

        try:
            dev = indigo.devices[devId]
        except Exception:
            return valuesDict

        # Always show what will be used effectively
        self._log_effective_source_summary()

        # Refresh states immediately if not cancelled
        if not userCancelled:
            if getattr(self, "debug2", False):
                self.logger.debug(f"closedDeviceConfigUi: applying immediate update for SolarSmart Main #{devId}")
            self._update_solarsmart_states(dev)

        return valuesDict

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        self.debugLog(u"closedPrefsConfigUi() method called.")
        if self.debug1:
            self.logger.debug(f"valuesDict\n {valuesDict}")
        if userCancelled:
            self.debugLog(u"User prefs dialog cancelled.")
        if not userCancelled:
            self.logLevel = int(valuesDict.get("showDebugLevel", '5'))
            self.fileloglevel = int(valuesDict.get("showDebugFileLevel", '5'))
            self.debug1 = valuesDict.get('debug1', False)
            self.debug2 = valuesDict.get('debug2', False)
            self.debug3 = valuesDict.get('debug3', False)
            self.debug4 = valuesDict.get('debug4', False)
            self.debug5 = valuesDict.get('debug5', False)
            self.debug6 = valuesDict.get('debug6', False)
            self.debug7 = valuesDict.get('debug7', False)
            self.debug8 = valuesDict.get('debug8', False)
            self.debug9 = valuesDict.get('debug9', False)

            self.indigo_log_handler.setLevel(self.logLevel)
            self.plugin_file_handler.setLevel(self.fileloglevel)

            self.logger.debug(u"logLevel = " + str(self.logLevel))
            self.logger.debug(u"User prefs saved.")
            self.logger.debug(u"Debugging on (Level: {0})".format(self.logLevel))



        return True
#
    def _external_on_state(self, load_dev: indigo.Device):
        """
        Determine the logical ON/OFF state of a SmartSolar Load that directly controls
        an Indigo device (controlMode == 'device').

        Returns:
            True  -> Logical RUNNING (load considered ON by scheduler)
            False -> Logical NOT RUNNING
            None  -> Not applicable (e.g. action group mode / misconfig / target missing)

        Logic:
          1. Read the physical device's on/off (ext_on).
             - Uses device.onState if available, else falls back to states['onOffState'].
          2. Detect "inverted" pairing:
                onCommand == 'turnOff' AND offCommand == 'turnOn'
             In that eco/energy-saver pattern, the physical device ON means
             "Eco Mode ENABLED" (so we treat scheduler logical running = False),
             and physical OFF means "Eco Mode DISABLED" (logical running = True).
          3. Return the appropriate logical boolean.

        Debug 8 logging (very verbose):
          Emits a single line per call when self.debug8 is True with:
            device id, name, controlDeviceId, physical_on, inverted flag,
            logical_on result, onCommand/offCommand choices.
        """
        try:
            props = (load_dev.pluginProps or {})
            mode = (props.get("controlMode") or "").lower()
            if mode != "device":
                if getattr(self, "debug8", False):
                    self.logger.debug(f"[DBG8][_external_on_state] {load_dev.name} (#{load_dev.id}) mode={mode} -> return None")
                return None

            # Target device id
            try:
                target_id = int(props.get("controlDeviceId", 0) or 0)
            except Exception:
                target_id = 0

            if target_id <= 0 or target_id not in indigo.devices:
                if getattr(self, "debug8", False):
                    self.logger.debug(
                        f"[DBG8][_external_on_state] {load_dev.name} (#{load_dev.id}) invalid target_id={target_id} -> None"
                    )
                return None

            target_dev = indigo.devices[target_id]

            # Physical on/off
            try:
                physical_on = bool(target_dev.onState)
            except Exception:
                physical_on = bool(target_dev.states.get("onOffState", False))

            # Commands selected
            on_cmd = (props.get("onCommand") or "").strip()
            off_cmd = (props.get("offCommand") or "").strip()

            inverted = (on_cmd == "turnOff" and off_cmd == "turnOn")

            if inverted:
                logical_on = (not physical_on)
            else:
                logical_on = physical_on

            if getattr(self, "debug8", False):
                self.logger.debug(
                    f"[DBG8][_external_on_state] Load:{load_dev.name} (#{load_dev.id}) "
                    f"Target:{target_dev.name} (#{target_id}) mode=device "
                    f"physical_on={physical_on} inverted={inverted} logical_on={logical_on} "
                    f"onCommand='{on_cmd}' offCommand='{off_cmd}'"
                )

            return logical_on

        except Exception as e:
            if getattr(self, "debug8", False):
                self.logger.debug(
                    f"[DBG8][_external_on_state] {load_dev.name} (#{getattr(load_dev, 'id', '?')}) exception: {e} -> None"
                )
            return None

    def _hydrate_load_state_from_device(self, dev: indigo.Device):
        """Rebuild in-memory _load_state for a SmartSolar Load from its persisted device states.
           If this load controls a real Indigo device (not an action group), mirror that device's
           onOffState into IsRunning (no actions sent). Otherwise, do NOT resume running on restart.
        """
        if dev.deviceTypeId != "solarsmartLoad":
            return

        st = self._load_state.setdefault(dev.id, {})

        try:
            # --- Persisted counters / anchors ---
            st["served_quota_mins"] = int(dev.states.get("RuntimeQuotaMins", 0) or 0)

            q_ts = dev.states.get("QuotaAnchorTs", "")
            st["quota_anchor_ts"] = float(q_ts) if q_ts not in ("", None, "") else None

            s_ts = dev.states.get("LastStartTs", "")
            st["start_ts"] = float(s_ts) if s_ts not in ("", None, "") else None

            props = dev.pluginProps or {}
            target = 0
            try:
                target = int(props.get("maxRuntimePerQuotaMins") or 0)
            except Exception:
                target = 0

            # --- MINIMAL NORMALISATION (overshoot & cosmetic window) ---
            # Clamp overshoot of served minutes to target so we never hydrate >100%
            if target > 0 and st["served_quota_mins"] > target:
                st["served_quota_mins"] = target
                dev.updateStateOnServer("RuntimeQuotaMins", target)

            if target > 0 and st["served_quota_mins"] >= target:
                # Refresh anchor so first tick doesn’t treat it as expired.
                now_ts = time.time()
                st["quota_anchor_ts"] = now_ts
                dev.updateStateOnServer("QuotaAnchorTs", f"{now_ts:.3f}")

            # Ensure window runtime state exists (cosmetic)
            try:
                run_window = int(dev.states.get("RuntimeWindowMins", 0) or 0)
            except Exception:
                run_window = 0
            if dev.states.get("RuntimeWindowMins", None) is None:
                dev.updateStateOnServer("RuntimeWindowMins", run_window)

            # If window runtime > served (after clamp), trim it so the table is consistent
            if run_window > st["served_quota_mins"]:
                dev.updateStateOnServer("RuntimeWindowMins", st["served_quota_mins"])

            # Recompute RemainingQuotaMins from (target - served) after clamp
            remaining = max(0, target - st["served_quota_mins"]) if target > 0 else 0
            dev.updateStateOnServer("RemainingQuotaMins", remaining)

            # If no anchor yet, create one (do NOT reset served)
            if st.get("quota_anchor_ts") is None:
                now_ts = time.time()
                st["quota_anchor_ts"] = now_ts
                dev.updateStateOnServer("QuotaAnchorTs", f"{now_ts:.3f}")

            # --- Mirror external device on/off if available ---
            ext_on = None
            try:
                ctrl_mode = (props.get("controlMode") or "").lower()
                if ctrl_mode == "device":
                    ctrl_id = int(props.get("controlDeviceId", 0) or 0)
                    if ctrl_id and ctrl_id in indigo.devices:
                        ext = indigo.devices[ctrl_id]
                        self.logger.debug(f"Startup Target Device Checking: {ext.name} {ext.onState=}")
                        try:
                            ext_on = bool(ext.onState)
                        except Exception:
                            ext_on = bool(ext.states.get("onOffState", False))
            except Exception:
                ext_on = None

            if ext_on is not None:
                st["IsRunning"] = bool(ext_on)
                if ext_on:
                    dev.updateStateOnServer("IsRunning", True)
                    dev.updateStateOnServer("Status", "RUNNING")
                    dev.updateStateOnServer("LastReason", "Hydrate from external onOffState")
                    # Seed start_ts if device ON but we had none
                    if not st.get("start_ts"):
                        now_ts = time.time()
                        st["start_ts"] = now_ts
                        dev.updateStateOnServer("LastStartTs", f"{now_ts:.3f}")
                        if getattr(self, "debug2", False):
                            self.logger.debug(
                                f"Hydrate: seeded start_ts for {dev.name} because external device is ON without persisted LastStartTs")
                else:
                    dev.updateStateOnServer("IsRunning", False)
                    dev.updateStateOnServer("Status", "OFF")
                    dev.updateStateOnServer("LastReason", "Hydrate from external onOffState")
            else:
                st["IsRunning"] = False
                dev.updateStateOnServer("IsRunning", False)
                dev.updateStateOnServer("Status", "OFF")
                if not dev.states.get("LastReason"):
                    dev.updateStateOnServer("LastReason", "Plugin restart recovery")

            # Catch-up run secs rehydrate
            try:
                cur_cu_run = int(dev.states.get("catchupRunTodayMins", 0) or 0)
                st.setdefault("catchup_run_secs", cur_cu_run * 60)
            except Exception:
                pass

            if getattr(self, "debug2", False):
                self.logger.debug(
                    f"Hydrated {dev.name}: served={st['served_quota_mins']}m (target={target}m), "
                    f"remaining={dev.states.get('RemainingQuotaMins')}m, "
                    f"windowRun={dev.states.get('RuntimeWindowMins')}m, "
                    f"anchor={st['quota_anchor_ts']}, start_ts={st.get('start_ts')}, "
                    f"IsRunning={st.get('IsRunning')} (ext_on={ext_on})"
                )

            # Update runtime percent / Status (NN%) after adjustments
            try:
                if hasattr(self, "_ss_manager"):
                    self._ss_manager._update_runtime_progress(dev)
            except Exception:
                pass

        except Exception:
            self.logger.exception(f"Hydrate failed for {dev.name}")

    # Start 'em up.
    def deviceStartComm(self, dev):
        """Indigo calls this when a device (of any type) starts communication."""
        dev.stateListOrDisplayStateIdChanged()
        if dev.deviceTypeId == "solarsmartLoad":
            self._hydrate_load_state_from_device(dev)

        if dev.deviceTypeId != "solarsmartMain":
            return
        if getattr(self, "debug2", False):
            self.logger.debug(f"deviceStartComm: SolarSmart Main #{dev.id} starting")
        # Push an immediate refresh of custom states on startup
        if dev.deviceTypeId == "solarsmartMain":
            self.pluginPrefs["main_device_id"] = str(dev.id)
        self._update_solarsmart_states(dev)

    # Shut 'em down.
    def deviceStopComm(self, dev):
        if self.debug1:
            self.debugLog(u"deviceStopComm() method called.")


    def shutdown(self):
        self.debugLog(u"shutdown() method called.")
        try:
            self._stop_forecast_thread()
        except Exception:
            pass

    def startup(self):
        self.debugLog(u"Starting Plugin. startup() method called.")
        self.logger.debug("Checking Plugin Prefs Directory")
        self._event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._event_loop)
        self._async_thread = threading.Thread(target=self._run_async_thread)
        self._async_thread.start()

        MAChome = os.path.expanduser("~") + "/"
        self.saveDirectory = MAChome + "Pictures/Indigo-smartSolar/"
        self.speakPath = os.path.join(self.pluginprefDirectory, "speak")

        for d in indigo.devices.iter("self"):
            if d.deviceTypeId == "solarsmartLoad" and d.enabled:
                self._hydrate_load_state_from_device(d)
            if d.deviceTypeId == "solarsmartMain" and d.enabled:
                self._update_solar_forecast_for_main(d, force=False)
                #self.logger.info("TODO Uncomment above line before release")
        try:

            if not os.path.exists(self.pluginprefDirectory):
                os.makedirs(self.pluginprefDirectory)
            if not os.path.exists(self.saveDirectory):
                os.makedirs(self.saveDirectory)
            speakpath = os.path.join(self.pluginprefDirectory, "speak")
            if not os.path.exists(self.speakPath):
                os.makedirs(self.speakPath)

        except:
            self.logger.error(u'Error Accessing Save and Peak Directory. ')
            pass
        # Start periodic forecast thread
        try:
            self._start_forecast_thread()
        except Exception as e:
            self.logger.error(f"Could not start forecast thread: {e}")

    def _run_async_thread(self):
        self.logger.debug("_run_async_thread starting")
        self._event_loop.create_task(self._async_start())
        self._event_loop.run_until_complete(self._async_stop())
        self._event_loop.close()

    # In your plugin class __init__ (or similar), ensure a client exists:
    # self._fs_client = ForecastSolarClient(user_agent="SolarSmart/Indigo")
 ## SOLAR FORECAST
    # Add these helpers to your plugin class
    def _wrap_azimuth_deg(self, az: float) -> float:
        """Normalize azimuth to the range [-180, 180] per forecast.solar requirement."""
        try:
            az = float(az)
        except Exception:
            return 0.0
        while az <= -180.0:
            az += 360.0
        while az > 180.0:
            az -= 360.0
        return az

    def _forecast_updates_per_day(self) -> int:
        # Fixed to 6/day for now. If you later want it configurable, read from props.
        return 6

    def _is_time_to_refresh_forecast(self) -> bool:
        """Guard to ensure we refresh at most N times per day."""
        try:
            next_ts = getattr(self, "_fs_next_refresh_ts", 0.0) or 0.0
            return time.time() >= float(next_ts)
        except Exception:
            return True

    def _schedule_next_forecast_refresh(self):
        """Schedule the next allowed refresh window based on updates per day."""
        per_day = max(1, int(self._forecast_updates_per_day()))
        interval_sec = int(86400 / per_day)
        setattr(self, "_fs_next_refresh_ts", time.time() + interval_sec)

    def _log_forecast_payload(self, main_dev, est):
        """Log and store summary/peaks using local per-device timezone formatting; raw payload at DEBUG."""
        try:
            # Summary (daily totals) — est.watt_hours_day keys are local date strings from client
            days_fmt = []
            for d in sorted(est.watt_hours_day.keys()):
                days_fmt.append(f"{self._fmt_local_date(d, main_dev)}: {round(est.watt_hours_day[d] / 1000.0, 2)} kWh")
            summary_str = ", ".join(days_fmt)
            self.logger.debug(f"Forecast.Solar summary: {summary_str}")
            main_dev.updateStateOnServer("forecastSolarSummary", summary_str)

            # Peaks
            peaks = {}
            for ts, w in est.watts.items():
                # est.watts keys are already local "YYYY-MM-DD HH:MM"
                date_key = ts[:10]
                if date_key not in peaks or w > peaks[date_key][1]:
                    peaks[date_key] = (ts, w)

            if peaks:
                peak_parts = []
                for date_key, (ts_local, watts) in sorted(peaks.items()):
                    fmt_ts = self._fmt_device_local_dt(ts_local, main_dev)
                    peak_parts.append(f"{fmt_ts} = {round(watts / 1000.0, 2)} kW")
                peak_str = "Peaks: " + ", ".join(peak_parts)
                self.logger.debug(f"Forecast.Solar peaks: {peak_str}")
                main_dev.updateStateOnServer("forecastSolarPeaks", peak_str)

            # Raw payload
            self.logger.debug("Forecast.Solar raw payload:\n" + json.dumps(est.raw_payload, indent=2)[:100000])
        except Exception:
            self.logger.exception("Error while logging Forecast.Solar payload")

    # Place this helper near the top of plugin.py (only define it once)
    def _get_device_tz(self, dev) -> object:
        """
        Return the ZoneInfo (or tzinfo) for a device.
        Device may have pluginProps['timezone'] specifying an IANA zone.
        Falls back to plugin default timezone.
        """
        props = getattr(dev, "pluginProps", {}) or {}
        tz_name = props.get("timezone")  # optional per-device override
        if tz_name and ZoneInfo:
            try:
                return ZoneInfo(tz_name)
            except Exception:
                self.logger.warning(f"{dev.name}: invalid device timezone '{tz_name}', using default.")
        return self._default_tz

    def _fmt_local_date(self, yyyy_mm_dd: str, dev=None) -> str:
        """
        Format a date string 'YYYY-MM-DD' as 'DD-MMM-YYYY' (e.g., 01-Aug-2025).
        dev parameter not used for conversion (date only) but accepted for parity.
        """
        if not yyyy_mm_dd:
            return ""
        s = yyyy_mm_dd.strip()[:10]
        try:
            d = datetime.strptime(s, "%Y-%m-%d").date()
            return d.strftime("%d-%b-%Y")
        except Exception:
            return yyyy_mm_dd

    def _fmt_device_local_dt(self, ts: str, dev=None) -> str:
        """
        Format a timestamp as 'DD-MMM-YYYY HH:MM' in the device's timezone.

        Logic:
          - If ts includes 'T' and a trailing 'Z' or an explicit offset (+/-HH:MM),
            parse as aware, then convert to device tz.
          - Else assume ts is already in plugin default local time
            (i.e., produced by ForecastSolarClient).
            If device tz differs from default, interpret naive as default tz then convert.

        Accepts forms:
          'YYYY-MM-DD HH:MM'
          'YYYY-MM-DD HH:MM:SS'
          'YYYY-MM-DDTHH:MM'
          'YYYY-MM-DDTHH:MM:SS'
          with optional 'Z' or '+HH:MM' offset.

        Returns empty string on failure (or raw ts if you prefer).
        """
        if not ts:
            return ""
        raw = ts.strip()

        # Normalize ' ' to 'T' for fromisoformat compatibility when no offset
        # (fromisoformat handles both, but we unify)
        if " " in raw and "T" not in raw:
            candidate = raw.replace(" ", "T")
        else:
            candidate = raw

        # Replace lone 'Z'
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"

        device_tz = self._get_device_tz(dev) if dev is not None else self._default_tz
        default_tz = self._default_tz

        dt_obj = None
        aware = False
        try:
            dt_obj = datetime.fromisoformat(candidate)
            aware = dt_obj.tzinfo is not None
        except Exception:
            # Fallback: trim seconds
            short = candidate[:16]
            try:
                dt_obj = datetime.strptime(short, "%Y-%m-%dT%H:%M")
                aware = False
            except Exception:
                return ts  # give up gracefully

        if aware:
            # Convert aware -> device tz
            try:
                dt_local = dt_obj.astimezone(device_tz)
            except Exception:
                dt_local = dt_obj.astimezone()  # system local fallback
        else:
            # Naive: treat as default tz (already localized by client)
            try:
                if isinstance(default_tz, timezone):
                    dt_obj = dt_obj.replace(tzinfo=default_tz)
                elif getattr(default_tz, "utcoffset", None):
                    dt_obj = dt_obj.replace(tzinfo=default_tz)
            except Exception:
                pass
            if device_tz and device_tz != default_tz:
                try:
                    dt_local = dt_obj.astimezone(device_tz)
                except Exception:
                    dt_local = dt_obj
            else:
                dt_local = dt_obj

        return dt_local.strftime("%d-%b-%Y %H:%M")

    # --- Remove or deprecate old functions to avoid accidental use ---

    def _fmt_au_date(self, yyyy_mm_dd: str) -> str:  # legacy wrapper
        return self._fmt_local_date(yyyy_mm_dd)

    def _fmt_au_dt_local(self, ts: str) -> str:  # legacy wrapper
        return self._fmt_device_local_dt(ts)

    _FORECAST_CACHE_MAX_AGE_SEC = 3600  # 1 hour

    def _forecast_cache_path(self, main_dev):
        """Return path for cached forecast JSON for this main device."""
        return os.path.join(self.pluginprefDirectory, "forecast_main_{}.json".format(main_dev.id))

    def _parse_payload_fetch_epoch(self, payload):
        """
        Derive fetch time (epoch) from payload:
        Prefer payload['message']['info']['time_utc'] (ISO8601) else file mtime handled by caller.
        Returns float epoch or None.
        """
        try:
            info = (payload.get("message") or {}).get("info") or {}
            ts_utc = info.get("time_utc") or info.get("time")
            if not ts_utc:
                return None
            # Normalize 'Z'
            if ts_utc.endswith("Z"):
                ts_utc = ts_utc[:-1] + "+00:00"
            # Python 3.11+ fromisoformat handles +HH:MM offsets
            dt = datetime.fromisoformat(ts_utc)
            if dt.tzinfo is None:
                # assume UTC
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return None

    def _load_cached_forecast(self, main_dev):
        """
        Load a cached forecast payload if present and fresh (<1h).
        Returns payload dict or None.
        """
        path = self._forecast_cache_path(main_dev)
        if not os.path.exists(path):
            if getattr(self, "debug3", False):
                self.logger.debug("Forecast cache: no file {}".format(path))
            return None
        try:
            with open(path, "r") as f:
                payload = json.load(f)
        except Exception as e:
            if getattr(self, "debug2", False):
                self.logger.debug(f"Forecast cache: corrupt ({e}) -> removing")
            try:
                os.remove(path)
            except Exception:
                pass
            return None

        # Determine age
        fetch_epoch = self._parse_payload_fetch_epoch(payload)
        if fetch_epoch is None:
            # fallback to file mtime
            try:
                fetch_epoch = os.path.getmtime(path)
            except Exception:
                fetch_epoch = time.time()

        age = time.time() - fetch_epoch
        if age > self._FORECAST_CACHE_MAX_AGE_SEC:
            if getattr(self, "debug2", False):
                self.logger.debug(f"Forecast cache stale ({int(age)}s) -> ignore")
            return None

        if getattr(self, "debug2", False):
            self.logger.debug(f"Using cached forecast for {main_dev.name} (age {int(age)}s)")
        return payload

    def _save_forecast_cache(self, main_dev, payload):
        """Persist raw API payload for reuse within 1 hour."""
        path = self._forecast_cache_path(main_dev)
        try:
            with open(path, "w") as f:
                json.dump(payload, f, indent=2)
            if getattr(self, "debug3", False):
                self.logger.debug(f"Saved forecast cache {path}")
        except Exception as e:
            if getattr(self, "debug2", False):
                self.logger.debug(f"Could not save forecast cache ({e})")

    def _process_forecast_payload(self, main_dev, payload):
        """
        Take a raw payload dict (cached or fresh), normalize via ForecastSolarClient,
        log summary/peaks, and push device states.
        """
        if not hasattr(self, "_fs_client") or self._fs_client is None:
            self._fs_client = ForecastSolarClient(user_agent="SolarSmart/Indigo")

        try:
            est = self._fs_client._normalize_payload(payload)  # use internal normalizer
        except Exception:
            self.logger.exception("Forecast.Solar: normalize failed")
            return

        # Logging + summary / peaks already handled here:
        self._log_forecast_payload(main_dev, est)

        try:
            summary = self._fs_client.summarize(est)
        except Exception:
            self.logger.exception("Forecast.Solar: summarize failed")
            return

        days = summary.days
        if len(days) >= 1:
            main_dev.updateStateOnServer("forecastTodayDate", days[0].date)
            main_dev.updateStateOnServer("forecastTodayKWh", f"{days[0].kwh:.2f}")
            main_dev.updateStateOnServer("forecastPeakKWToday", f"{days[0].peak_kw:.2f}")
            main_dev.updateStateOnServer("forecastPeakTimeToday", days[0].peak_time or "")
        if len(days) >= 2:
            main_dev.updateStateOnServer("forecastTomorrowDate", days[1].date)
            main_dev.updateStateOnServer("forecastTomorrowKWh", f"{days[1].kwh:.2f}")
            main_dev.updateStateOnServer("forecastPeakKWTomorrow", f"{days[1].peak_kw:.2f}")
            main_dev.updateStateOnServer("forecastPeakTimeTomorrow", days[1].peak_time or "")

    def _forecast_thread_loop(self):
        """
        Background thread: every 10 minutes attempt to ensure forecast is updated.
        Will only hit network if cached payload >1h old (per device).
        """
        interval = 600  # 10 minutes
        if getattr(self, "debug2", False):
            self.logger.debug("Forecast thread started (interval 600s)")
        while not getattr(self, "stopThread", False) and not getattr(self, "_forecast_thread_stop", False):
            try:
                for dev in indigo.devices.iter("self"):
                    if dev.deviceTypeId != "solarsmartMain" or not dev.enabled:
                        continue
                    props = dev.pluginProps or {}
                    if not bool(props.get("enableSolarForecast", False)):
                        continue
                    # Reuse core update method (cache-aware)
                    self._update_solar_forecast_for_main(dev, force=False)
                    time.sleep(1.0)  # slight stagger if multiple mains
            except Exception as e:
                if getattr(self, "debug3", False):
                    self.logger.debug(f"Forecast thread iteration error: {e}")

            # Responsive sleep
            slept = 0
            while slept < interval and not getattr(self, "stopThread", False) and not getattr(self, "_forecast_thread_stop", False):
                time.sleep(2)
                slept += 2

        if getattr(self, "debug2", False):
            self.logger.debug("Forecast thread exiting")

    def _start_forecast_thread(self):
        """Idempotently start forecast refresh thread."""
        if getattr(self, "_forecast_thread", None):
            return
        self._forecast_thread_stop = False
        th = threading.Thread(target=self._forecast_thread_loop, name="ForecastUpdater", daemon=True)
        self._forecast_thread = th
        th.start()

    def _stop_forecast_thread(self):
        """Signal and join forecast thread."""
        self._forecast_thread_stop = True
        th = getattr(self, "_forecast_thread", None)
        if th and th.is_alive():
            try:
                th.join(timeout=3)
            except Exception:
                pass

    # ======================================================================
    # MODIFY existing _update_solar_forecast_for_main (REPLACE its body)
    # ======================================================================
    def _update_solar_forecast_for_main(self, main_dev, force=False):
        """
        Hourly-cached Forecast.Solar fetch.
        - Tries cache first (unless force)
        - If cache stale/missing, performs live fetch (rate-limit friendly)
        - Saves raw payload to pluginprefDirectory
        - Processes payload to update forecast states
        """
        try:
            props = main_dev.pluginProps or {}
            if not bool(props.get("enableSolarForecast", False)):
                return

            # 1. Attempt cache
            if not force:
                cached = self._load_cached_forecast(main_dev)
                if cached:
                    self._process_forecast_payload(main_dev, cached)
                    return

            # 2. Live fetch (network call)
            lat, lon = indigo.server.getLatitudeAndLongitude()
            dec = float(props.get("fsTiltDeclinationDeg") or 30.0)
            az = self._wrap_azimuth_deg(props.get("fsAzimuthDeg") or 0.0)
            kwp = float(props.get("fsSystemKwp") or 5.0)
            if kwp <= 0:
                self.logger.warning("Forecast.Solar: System kWp must be > 0")
                return

            plane = PVPlane(latitude=float(lat), longitude=float(lon),
                            dec_deg=dec, az_deg=az, kwp=kwp)

            if not hasattr(self, "_fs_client") or self._fs_client is None:
                self._fs_client = ForecastSolarClient(user_agent="SolarSmart/Indigo")

            try:
                est = self._fs_client.get_estimate(plane)
            except ForecastSolarRateLimitError as e:
                # If rate limit hit, fall back to any (even stale) cache if present
                self.logger.info("Forecast.Solar rate limit reached; will retry later (using any cached data).")
                cached = self._load_cached_forecast(main_dev)
                if cached:
                    self._process_forecast_payload(main_dev, cached)
                return
            except Exception as e:
                self.logger.info(f"Forecast.Solar fetch failed: {e}")
                cached = self._load_cached_forecast(main_dev)
                if cached:
                    self._process_forecast_payload(main_dev, cached)
                return

            # 3. Save & process
            self._save_forecast_cache(main_dev, est.raw_payload)
            self._process_forecast_payload(main_dev, est.raw_payload)

        except Exception:
            self.logger.exception("Failed to update Forecast.Solar data")

## End of Solar forecast



    async def _async_start(self):
        self.logger.debug("_async_start")
        self.logger.debug("Starting event loop and setting up any connections")

        # Create & start our async manager
        self._ss_manager = SolarSmartAsyncManager(self, self._event_loop)
        await self._ss_manager.start()

    async def _async_stop(self):
        # Poll for stop; when set, cancel async tasks cleanly
        while True:
            await asyncio.sleep(5.0)
            if self.stopThread:
                try:
                    if hasattr(self, "_ss_manager") and self._ss_manager:
                        await self._ss_manager.stop()
                except Exception as e:
                    self.logger.exception(f"_async_stop: error while stopping manager: {e}")
                break
    def validatePrefsConfigUi(self, valuesDict):

        self.debugLog(u"validatePrefsConfigUi() method called.")
        error_msg_dict = indigo.Dict()
        return (True, valuesDict)

        ## Generate QR COde for Homekit and open Web-Browser to display - is a PNG

    def validateDeviceConfigUi(self, valuesDict, typeId, devId):
        """
        Extend your existing validate for solarsmartMain; validate solarsmartLoad here.
        """
        if typeId == "solarsmartMain":
            self.logger.debug(f"{valuesDict=}")
            use_grid = bool(valuesDict.get("useGridHeadroom", False))

            if use_grid:
                grid_dev_ok = self._is_valid_device_choice(valuesDict.get("gridDeviceId", ""))
                grid_st_ok = self._is_valid_state_choice(valuesDict.get("gridStateId", ""))
                if grid_dev_ok and grid_st_ok:
                    return (True, valuesDict)
                errorDict = indigo.Dict()
                if not grid_dev_ok:
                    errorDict["gridDeviceId"] = "Select the Grid device."
                if not grid_st_ok:
                    errorDict["gridStateId"] = "Select the Grid state."
                return (False, valuesDict, errorDict)

            # Not using grid-only -> PV is required
            pv_dev_ok = self._is_valid_device_choice(valuesDict.get("pvDeviceId", ""))
            pv_st_ok = self._is_valid_state_choice(valuesDict.get("pvStateId", ""))
            if pv_dev_ok and pv_st_ok:
                return (True, valuesDict)

            errorDict = indigo.Dict()
            if not pv_dev_ok:
                errorDict["pvDeviceId"] = "Select a PV device."
            if not pv_st_ok:
                errorDict["pvStateId"] = "Select the PV state."
            return (False, valuesDict, errorDict)

        if typeId != "solarsmartLoad":
            return (True, valuesDict)

        errorDict = indigo.Dict()
        ok = True

        # Tier
        try:
            tier = int(valuesDict.get("tier", "2"))
            if tier < 1 or tier > 4:
                raise ValueError()
        except Exception:
            errorDict["tier"] = "Tier must be 1, 2, 3, or 4."
            ok = False

        # Power
        try:
            watts = int(float((valuesDict.get("ratedWatts") or "0").replace(",", "")))
            if watts <= 0:
                raise ValueError()
        except Exception:
            errorDict["ratedWatts"] = "Enter rated power (Watts), e.g., 2000."
            ok = False

        # Time window HH:MM
        if not _valid_hhmm(valuesDict.get("windowStart", "")):
            errorDict["windowStart"] = "Use HH:MM (e.g., 08:00)."
            ok = False
        if not _valid_hhmm(valuesDict.get("windowEnd", "")):
            errorDict["windowEnd"] = "Use HH:MM (e.g., 20:00)."
            ok = False

        # DOW: require at least one checked
        dow_keys = ["dowMon", "dowTue", "dowWed", "dowThu", "dowFri", "dowSat", "dowSun"]
        if not any(valuesDict.get(k, False) in (True, "true", "True") for k in dow_keys):
            errorDict["dowFri"] = "Select at least one allowed day."
            ok = False

        # Runtimes
        if not _valid_pos_int(valuesDict.get("maxRuntimePerQuotaMins")):
            errorDict["maxRuntimePerQuotaMins"] = "Enter minutes (positive integer)."
            ok = False

        # Control mode requirements
        mode = valuesDict.get("controlMode", "actionGroup")
        if mode == "actionGroup":
            if not _is_valid_choice(valuesDict.get("onActionGroupId")):
                errorDict["onActionGroupId"] = "Select an Action Group to turn ON."
                ok = False
            if not _is_valid_choice(valuesDict.get("offActionGroupId")):
                errorDict["offActionGroupId"] = "Select an Action Group to turn OFF."
                ok = False
        else:
            if not _is_valid_choice(valuesDict.get("controlDeviceId")):
                errorDict["controlDeviceId"] = "Select the target device to control."
                ok = False

        if not ok:
            return (False, valuesDict, errorDict)
        return (True, valuesDict)

    # ────────────────────────────────────────────────────────────
    # Test ON/OFF callbacks (buttons in Devices.xml)
    # ────────────────────────────────────────────────────────────

    def test_on_clicked(self, valuesDict, typeId, devId):
        return self._test_button_common(valuesDict, typeId, devId, turn_on=True)

    def test_off_clicked(self, valuesDict, typeId, devId):
        return self._test_button_common(valuesDict, typeId, devId, turn_on=False)

    def _test_button_common(self, valuesDict, typeId, devId, turn_on: bool):
        try:
            dev = indigo.devices[devId]
            if getattr(self, "debug2", False):
                self.logger.debug(f"TEST {'ON' if turn_on else 'OFF'} clicked for #{devId} '{dev.name}'")

            # Merge config: prefer valuesDict (unsaved), then device.pluginProps
            props = self._merge_props(valuesDict or {}, dev.pluginProps or {})

            ok, msg = self._validate_control_config_for_test(props)
            if not ok:
                self.logger.error(f"Test {'ON' if turn_on else 'OFF'}: {msg}")
                dev.setErrorStateOnServer(msg)
                return valuesDict

            self._execute_load_action_with_props(dev, turn_on=turn_on,
                                                 reason=f"Manual TEST {'ON' if turn_on else 'OFF'}", props=props)
            dev.updateStateOnServer("Status", f"{'ON' if turn_on else 'OFF'} (test)")
            dev.updateStateOnServer("LastReason", f"Manual TEST {'ON' if turn_on else 'OFF'}")

        except Exception as e:
            self.logger.exception(f"test_button_common error: {e}")
        return valuesDict

    def _merge_props(self, vd: dict, saved: dict) -> dict:
        """Prefer valuesDict (unsaved UI) values; fall back to saved pluginProps."""
        merged = dict(saved) if saved else {}
        for k, v in (vd or {}).items():
            # Indigo can pass booleans/strings; keep raw
            merged[k] = v
        return merged

    def _validate_control_config_for_test(self, props: dict) -> tuple[bool, str]:
        mode = (props.get("controlMode") or "actionGroup").strip()
        if mode == "actionGroup":
            on_ag = self._safe_int(props.get("onActionGroupId"))
            off_ag = self._safe_int(props.get("offActionGroupId"))
            if on_ag is None or on_ag <= 0 or off_ag is None or off_ag <= 0:
                return False, "Select valid Action Groups for ON and OFF before testing."
            return True, ""
        else:
            dev_id = self._safe_int(props.get("controlDeviceId"))
            if dev_id is None or dev_id <= 0:
                return False, "Select a target device before testing."
            return True, ""

    def _execute_load_action(self, load_dev: indigo.Device, turn_on: bool, reason: str, props: dict = None,
                             update_states: bool = True):
        """
        Core method to turn a SmartSolar Load on or off.

        Args:
            load_dev      : The SmartSolar Load device object
            turn_on       : True for ON, False for OFF
            reason        : String describing why we're taking this action
            props         : Optional props dict (if None, use load_dev.pluginProps)
            update_states : Whether to mark IsRunning / LastReason in Indigo states
        """
        try:
            # Props may be passed from TEST buttons, otherwise use saved props
            props = props or (load_dev.pluginProps or {})
            mode = (props.get("controlMode") or "actionGroup").strip()

            if self.debug2:
                self.logger.debug(
                    f"_execute_load_action: {load_dev.name} => {'ON' if turn_on else 'OFF'} via {mode} | reason: {reason}")

            if mode == "actionGroup":
                ag_id = self._safe_int(props.get("onActionGroupId") if turn_on else props.get("offActionGroupId"))
                if ag_id and ag_id > 0:
                    indigo.actionGroup.execute(ag_id)
                    if self.debug2:
                        self.logger.debug(f"Executed Action Group #{ag_id} for {load_dev.name}")
                else:
                    raise ValueError(f"No valid Action Group ID for {load_dev.name}")

            else:  # Device mode
                target_id = self._safe_int(props.get("controlDeviceId"))
                if not target_id or target_id <= 0:
                    raise ValueError(f"No valid control device for {load_dev.name}")

                on_cmd = (props.get("onCommand") or "turnOn").strip()
                off_cmd = (props.get("offCommand") or "turnOff").strip()

                if turn_on:
                    if on_cmd == "toggle":
                        indigo.device.toggle(target_id)
                    else:
                        indigo.device.turnOn(target_id)
                else:
                    if off_cmd == "toggle":
                        indigo.device.toggle(target_id)
                    else:
                        indigo.device.turnOff(target_id)

                if self.debug2:
                    self.logger.debug(f"Sent {'ON' if turn_on else 'OFF'} to device #{target_id} for {load_dev.name}")

            # If called from scheduler (or if explicitly told), update the states
            if update_states:
                load_dev.updateStateOnServer("IsRunning", turn_on)
                load_dev.updateStateOnServer("LastReason", reason)
                load_dev.updateStateOnServer("Status", "RUNNING" if turn_on else "OFF")
                # Immediately replace plain RUNNING/OFF with percentage form
                try:
                    if hasattr(self, "_ss_manager"):
                        self._ss_manager._update_runtime_progress(load_dev)
                except Exception:
                    pass

        except Exception as e:
            self.logger.error(f"Error executing action for {load_dev.name}: {e}")

    def _execute_load_action_with_props(self, load_dev: indigo.Device, turn_on: bool, reason: str, props: dict):
        """Fire ON/OFF using the provided props (valuesDict-merged). No scheduler side-effects."""
        mode = (props.get("controlMode") or "actionGroup").strip()
        if getattr(self, "debug2", False):
            self.logger.debug(
                f"_execute_load_action_with_props: {load_dev.name} -> {('ON' if turn_on else 'OFF')} via {mode} | {reason}")

        if mode == "actionGroup":
            ag_id = self._safe_int(props.get("onActionGroupId") if turn_on else props.get("offActionGroupId"))
            if ag_id and ag_id > 0:
                indigo.actionGroup.execute(ag_id)
                if getattr(self, "debug2", False):
                    self.logger.debug(f"Executed Action Group #{ag_id} for {load_dev.name}")
            else:
                raise ValueError("No valid Action Group selected for test.")
        else:
            target_id = self._safe_int(props.get("controlDeviceId"))
            on_cmd = (props.get("onCommand") or "turnOn").strip()
            off_cmd = (props.get("offCommand") or "turnOff").strip()
            if not target_id or target_id <= 0:
                raise ValueError("No valid control device selected for test.")

            if turn_on:
                if on_cmd == "toggle":
                    indigo.device.toggle(target_id)
                else:
                    indigo.device.turnOn(target_id)
            else:
                if off_cmd == "toggle":
                    indigo.device.toggle(target_id)
                else:
                    indigo.device.turnOff(target_id)

            if getattr(self, "debug2", False):
                self.logger.debug(f"Sent {'ON' if turn_on else 'OFF'} to device #{target_id} for {load_dev.name}")

    def setStatestonil(self, dev):
        if self.debug1:
            self.debugLog(u'setStates to nil run')


    def refreshDataAction(self, valuesDict):
        """
        The refreshDataAction() method refreshes data for all devices based on
        a plugin menu call.
        """
        if self.debug1:
            self.debugLog(u"refreshDataAction() method called.")
        self.refreshData()
        return True

    def refreshData(self):
        """
        The refreshData() method controls the updating of all plugin
        devices.
        """
        if self.debug1:
            self.debugLog(u"refreshData() method called.")

        try:
            # Check to see if there have been any devices created.
            if indigo.devices.itervalues(filter="self"):
                if self.debugLevel >= 2:
                    self.debugLog(u"Updating data...")

                for dev in indigo.devices.itervalues(filter="self"):
                    self.refreshDataForDev(dev)

            else:
                indigo.server.log(u"No Client devices have been created.")

            return True

        except Exception as error:
            self.errorLog(u"Error refreshing devices. Please check settings.")
            self.errorLog(unicode(error.message))
            return False

    def refreshDataForDev(self, dev):

        if dev.configured:
            if self.debug1:
                self.debugLog(u"Found configured device: {0}".format(dev.name))

            if dev.enabled:
                if self.debug1:
                    self.debugLog(u"   {0} is enabled.".format(dev.name))
                timeDifference = int(t.time() - t.mktime(dev.lastChanged.timetuple()))

            else:
                if self.debug1:
                    self.debugLog(u"    Disabled: {0}".format(dev.name))


    def refreshDataForDevAction(self, valuesDict):
        """
        The refreshDataForDevAction() method refreshes data for a selected device based on
        a plugin menu call.
        """
        if self.debug1:
            self.debugLog(u"refreshDataForDevAction() method called.")

        dev = indigo.devices[valuesDict.deviceId]

        self.refreshDataForDev(dev)
        return True

    def stopSleep(self, start_sleep):
        """
        The stopSleep() method accounts for changes to the user upload interval
        preference. The plugin checks every 2 seconds to see if the sleep
        interval should be updated.
        """
        try:
            total_sleep = float(self.pluginPrefs.get('configMenuUploadInterval', 300))
        except:
            total_sleep = iTimer  # TODO: Note variable iTimer is an unresolved reference.
        if t.time() - start_sleep > total_sleep:
            return True
        return False

    def toggleDebugEnabled(self):
        """
        Toggle debug on/off.
        """
        self.debugLog(u"toggleDebugEnabled() method called.")
        if self.logLevel == logging.INFO:
            self.logLevel = logging.DEBUG
            self.indigo_log_handler.setLevel(self.logLevel)

            indigo.server.log(u'Set Logging to DEBUG')
        else:
            self.logLevel = logging.INFO
            indigo.server.log(u'Set Logging to INFO')
            self.indigo_log_handler.setLevel(self.logLevel)

        self.pluginPrefs[u"logLevel"] = self.logLevel
        return
        ## Triggers

    ## Genereate Device lists

    # ================================
    # Dynamic menu list generators
    # ================================
    # ────────────────────────────────────────────────────────────
    # Dynamic menus for solarsmartLoad
    # ────────────────────────────────────────────────────────────

    def action_group_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        """
        Build a menu of Action Groups. Action Groups don't have an 'enabled' flag,
        so we list them all.
        """
        items = [("-1", "— None —")]
        try:
            for ag in indigo.actionGroups:
                items.append((str(ag.id), f"{ag.name} (#{ag.id})"))
        except Exception as e:
            if getattr(self, "debug3", False):
                self.logger.debug(f"action_group_list: exception {e}")
        items.sort(key=lambda t: t[1].lower())
        if getattr(self, "debug3", False):
            self.logger.debug(f"action_group_list: {len(items)} entries")
        return items

    def enabled_device_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        """All enabled Indigo devices (core + from loaded plugins)."""
        return self._all_devices_menu(include_blank=True)

    # ────────────────────────────────────────────────────────────
    # ConfigUI callbacks (solarsmartLoad)
    # ────────────────────────────────────────────────────────────

    def control_mode_changed(self, valuesDict, typeId, devId):
        mode = valuesDict.get("controlMode", "actionGroup")
        if getattr(self, "debug2", False):
            self.logger.debug(f"control_mode_changed: mode={mode} for device #{devId}")
        if mode == "actionGroup":
            valuesDict["controlDeviceId"] = "-1"
            valuesDict["onCommand"] = "turnOn"
            valuesDict["offCommand"] = "turnOff"
        else:
            valuesDict["onActionGroupId"] = "-1"
            valuesDict["offActionGroupId"] = "-1"
        return valuesDict

    def control_device_changed(self, valuesDict, typeId, devId):
        if getattr(self, "debug2", False):
            self.logger.debug(
                f"control_device_changed: deviceId={valuesDict.get('controlDeviceId')} for device #{devId}")
        valuesDict["onCommand"] = "turnOn"
        valuesDict["offCommand"] = "turnOff"
        return valuesDict

    # ────────────────────────────────────────────────────────────
    # Menu generators (Devices.xml: <List class="self" method="…"/>)
    # ────────────────────────────────────────────────────────────

    # Menu generators
    def pv_device_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        return self._all_devices_menu()

    def consumption_device_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        return self._all_devices_menu(include_blank=True)

    def battery_device_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        return self._all_devices_menu(include_blank=True)

    def grid_device_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        return self._all_devices_menu(include_blank=True)

    def pv_state_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        dev_id = (valuesDict or {}).get("pvDeviceId")
        return self._state_list_for_device(dev_id)

    def consumption_state_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        dev_id = (valuesDict or {}).get("consDeviceId")
        return self._state_list_for_device(dev_id, include_blank=True)

    def battery_state_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        dev_id = (valuesDict or {}).get("battDeviceId")
        return self._state_list_for_device(dev_id, include_blank=True)

    def grid_state_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        dev_id = (valuesDict or {}).get("gridDeviceId")
        return self._state_list_for_device(dev_id, include_blank=True)

    # ────────────────────────────────────────────────────────────
    # ConfigUI callbacks (Devices.xml: <CallbackMethod>…</CallbackMethod>)
    # Reset dependent state fields when a device selection changes.
    # Return valuesDict (and optionally errorsDict) per Indigo expectations.
    # ────────────────────────────────────────────────────────────

    # Callback resets — use sentinel "-1"
    def pv_device_changed(self, valuesDict, typeId, devId):
        valuesDict["pvStateId"] = "-1"
        if getattr(self, "debug2", False):
            self.logger.debug(f"pv_device_changed: set pvStateId=-1 (devId={valuesDict.get('pvDeviceId')})")
        return valuesDict

    def consumption_device_changed(self, valuesDict, typeId, devId):
        valuesDict["consStateId"] = "-1"
        if getattr(self, "debug2", False):
            self.logger.debug(
                f"consumption_device_changed: set consStateId=-1 (devId={valuesDict.get('consDeviceId')})")
        return valuesDict

    def battery_device_changed(self, valuesDict, typeId, devId):
        valuesDict["battStateId"] = "-1"
        if getattr(self, "debug2", False):
            self.logger.debug(f"battery_device_changed: set battStateId=-1 (devId={valuesDict.get('battDeviceId')})")
        return valuesDict

    def grid_device_changed(self, valuesDict, typeId, devId):
        valuesDict["gridStateId"] = "-1"
        if getattr(self, "debug2", False):
            self.logger.debug(f"grid_device_changed: set battStateId=-1 (devId={valuesDict.get('battDeviceId')})")
        return valuesDict

    # ────────────────────────────────────────────────────────────
    # Reading & normalizing selected states (numbers only)
    # Use these in your poller/async loop to fetch Watts as numeric.
    # They strip units like "kW", "W", " A", "V", etc., and parse numbers
    # from strings like "2.34 kW" -> 2340.  Non-parsable becomes None.
    # ────────────────────────────────────────────────────────────

    def read_pv_watts(self, props: indigo.Dict) -> Optional[Number]:
        """Return PV production in Watts as a number, or None."""
        return self._read_numeric_state_watts(props.get("pvDeviceId"), props.get("pvStateId"))

    def read_consumption_watts(self, props: indigo.Dict) -> Optional[Number]:
        """Return site consumption in Watts as a number, or None if not configured."""
        dev_id, state_id = props.get("consDeviceId"), props.get("consStateId")
        if not dev_id or not state_id:
            return None
        return self._read_numeric_state_watts(dev_id, state_id)

    def read_battery_watts(self, props: indigo.Dict) -> Optional[Number]:
        """Return battery power in Watts as a number (+charge, -discharge if source uses that convention)."""
        dev_id, state_id = props.get("battDeviceId"), props.get("battStateId")
        if not dev_id or not state_id:
            return None
        return self._read_numeric_state_watts(dev_id, state_id)

    def read_grid_watts(self, props: indigo.Dict) -> Optional[Number]:
        """Return battery power in Watts as a number (+charge, -discharge if source uses that convention)."""
        dev_id, state_id = props.get("gridDeviceId"), props.get("gridStateId")
        if not dev_id or not state_id:
            return None
        return self._read_numeric_state_watts(dev_id, state_id)


    # ────────────────────────────────────────────────────────────
    # Internals
    # ────────────────────────────────────────────────────────────
    def _all_devices_menu(self, include_blank: bool = False):
        items = []
        if include_blank:
            items.append(("-1", "— None —"))

        for dev in indigo.devices:
            if not dev.enabled:
                if getattr(self, "debug3", False):
                    self.logger.debug(f"Skip device #{dev.id} '{dev.name}': disabled")
                continue

            # If owned by a plugin, skip if plugin can't be retrieved (not loaded)
            if dev.pluginId:
                try:
                    plugin_obj = indigo.server.getPlugin(dev.pluginId)  # 1 arg only
                    if plugin_obj.isEnabled() == False:
                        if getattr(self, "debug3", False):
                            self.logger.debug(f"Skip device #{dev.id} '{dev.name}': plugin '{dev.pluginId}' not enabled")
                        continue
                    if self.debug3:
                        self.logger.debug(f"Plugin for device #{dev.id} '{dev.name}': {plugin_obj}")
                except Exception as e:
                    if getattr(self, "debug3", False):
                        self.logger.debug(f"Skip device #{dev.id} '{dev.name}': plugin '{dev.pluginId}' not found ({e})")
                    continue

                if plugin_obj is None:
                    if getattr(self, "debug3", False):
                        self.logger.debug(f"Skip device #{dev.id} '{dev.name}': plugin '{dev.pluginId}' not loaded")
                    continue

            model = getattr(dev, "model", "") or ""
            label = f"{dev.name} [{model}] (#{dev.id})" if model else f"{dev.name} (#{dev.id})"
            items.append((str(dev.id), label))

        items.sort(key=lambda t: t[1].lower())
        if getattr(self, "debug3", False):
            self.logger.debug(f"_all_devices_menu built {len(items)} items (include_blank={include_blank})")
        return items


    def _state_list_for_device(self, dev_id, include_blank: bool = False):
        items = []
        if include_blank:
            items.append(("-1", "— None —"))

        # Accept None / "", "-1" as “no device selected”
        try:
            dev_id_int = int(dev_id)
        except Exception:
            dev_id_int = -1

        if dev_id_int <= 0:
            items.append(("-1", "— Select a device first —"))
            if getattr(self, "debug3", False):
                self.logger.debug("_state_list_for_device: no device selected")
            return items

        try:
            dev = indigo.devices[dev_id_int]
        except Exception as e:
            if getattr(self, "debug3", False):
                self.logger.debug(f"_state_list_for_device: device #{dev_id_int} not found ({e})")
            items.append(("-1", "— Device not found —"))
            return items

        for key in sorted(dev.states.keys(), key=str.lower):
            val = dev.states.get(key, "")
            items.append((key, f"{key} == {val}"))

        if getattr(self, "debug3", False):
            self.logger.debug(f"_state_list_for_device: device #{dev_id_int} -> {len(items)} items")
        return items

    def _read_numeric_state_watts(self, dev_id_val: Any, state_key: Any) -> Optional[Number]:
        """
        Read a device.state, parse to Watts (number only). Handles:
          - raw numeric (int/float)
          - strings with units ('123 W', '1.2 kW', '1,234W', etc.)
          - leading/trailing text ('Power: 750W', '750 watts')
        Returns None if not available or unparsable.
        """
        try:
            dev_id = int(dev_id_val)
            key = str(state_key)
        except Exception:
            return None

        try:
            dev = indigo.devices[dev_id]
            raw = dev.states.get(key, None)
        except Exception:
            return None

        if raw is None:
            return None

        # If already numeric, treat as Watts.
        if isinstance(raw, (int, float)):
            return float(raw)

        # Normalize strings
        if isinstance(raw, str):
            watts = _parse_to_watts(raw)
            return watts

        # Fallback: try str() then parse
        return _parse_to_watts(str(raw))

    # ────────────────────────────────────────────────────────────
    # Parsing helpers (pure functions)
    # ────────────────────────────────────────────────────────────

    _NUM_RE = re.compile(r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)")

    def _parse_to_watts(s: str) -> Optional[Number]:
        """
        Extract the first number and unit from a string and convert to Watts.
        Supported hints:
          - 'kW' => multiply by 1000
          - 'W' or 'watt(s)' => as-is
          - If no unit present, assume Watts
        Examples:
          '2.4 kW' -> 2400
          '950 W' -> 950
          'Power: 1,200w' -> 1200
          '1.2e3' -> 1200 (assumed Watts)
        """
        if not s:
            return None

        s_clean = s.replace(",", "").strip()
        m = _NUM_RE.search(s_clean)
        if not m:
            return None

        try:
            val = float(m.group(1))
        except Exception:
            return None

        tail = s_clean[m.end():].lower()

        # Unit inference
        if "kw" in tail:
            return val * 1000.0
        if "w" in tail or "watt" in tail:
            return val
        # If the string before/after number mentions amps/volts, we don't convert — caller must compute P.
        if "amp" in s_clean.lower() or "volt" in s_clean.lower():
            return val  # best effort; caller can decide what to do

        # Default: assume Watts
        return val


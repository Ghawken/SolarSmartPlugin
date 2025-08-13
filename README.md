# SolarSmart Indigo Plugin

Harness your surplus solar energy automatically. SolarSmart monitors your photovoltaic (PV) production, site consumption, battery power, and/or net grid flow to calculate “headroom” (excess power) and then starts or sheds discretionary loads in a priority order so you use more of your own clean energy and import less from the grid.

---

## Table of Contents

- What SolarSmart Does
- Headroom: How it’s Calculated
- Operating Modes
- Devices Provided by the Plugin
- Quick Start
- Configuration
  - Main Device (Controller)
  - Load Device(s)
  - Test Device (Simulation)
- Scheduling Logic and Tiers
- Quotas and Run Limits
- Logging and Diagnostics
- Examples
- Tips and Best Practices
- Troubleshooting
- Requirements and Compatibility
- Contributing and License
- Acknowledgements

---

## What SolarSmart Does

SolarSmart is an Indigo Domotics plugin that:

- Calculates “headroom” in watts: the spare power available to run discretionary loads.
- Starts loads (pool pump, EV charger, water heater, dehumidifier, etc.) when sufficient headroom exists.
- Sheds loads gracefully when headroom drops, prioritizing lower-impact loads first.
- Supports simple, robust control via Indigo devices or Action Groups.
- Works with either a single net grid meter or a combination of PV, site consumption, and battery power sources.
- Provides a test device to simulate conditions and validate your automations before going live.

![Main Device Setup](https://github.com/Ghawken/SolarSmartPlugin/blob/main/Images/Main_device.png?raw=true "SolarSmart Main Device (Controller)")

---

## Headroom: How it’s Calculated

Headroom is the amount of excess power you can safely use right now.

- Positive headroom → you have spare power available to start or keep running loads.
- Negative headroom → you’re short on power and importing from the grid; loads should be avoided or shed.

Depending on your metering, SolarSmart can compute headroom from:
- A single, accurate net grid meter (preferred when available).
- Separate inputs for PV output, site consumption, and battery charge/discharge.

Sign conventions (typical, but verify your meters):
- Net grid:
  - Negative watts = exporting to the grid (good; spare power).
  - Positive watts = importing from the grid (no spare power).
- Battery:
  - Negative watts = discharging (adds to available power).
  - Positive watts = charging (consumes power).

---

## Operating Modes

1) Grid-Only Mode (preferred if supported)
- Uses your net grid meter as the single source of truth.
- Example:
  - Grid = −2424 W → exporting 2424 W
  - Headroom = 2424 W

2) PV + Consumption + Battery Mode
- Used when a net grid meter state isn’t available.
- Example:
  - PV = 3800 W
  - Site Consumption = 3134 W
  - Battery = −9000 W (discharging)
  - Headroom = PV − Consumption + Battery = 3800 − 3134 + 9000 = 9666 W

3) No Spare Power example
- Grid-Only Mode
  - Grid = +1800 W → importing from grid
  - Headroom = −1800 W (loads should stop)

---

## Devices Provided by the Plugin

- SolarSmart Main Device (Controller)
  - Holds configuration, reads meters, computes headroom, runs the scheduler.
  - Exposes states such as current headroom, mode, last scheduler run, etc.

- SolarSmart Load Device
  - Represents a discretionary load you want SolarSmart to start/stop.
  - Assigned a tier (priority) and control method: direct device control or Action Groups.

- SolarSmart Test Device (optional)
  - Lets you simulate PV, consumption, battery, and grid values.
  - Ideal for validating behavior before connecting real meters and loads.

![Load Device Setup](https://github.com/Ghawken/SolarSmartPlugin/blob/main/Images/Load_Device.png?raw=true "SolarSmart Load Device")

---

## Quick Start

1) Install the plugin
- Double-click SolarSmart.indigoPlugin.
- Enable it in Indigo.

2) Create the Main Device
- Choose your operating mode.
- Select the meter/source states for PV, consumption, battery and/or net grid.
- Set the scheduler check frequency.

3) Create one or more Load Devices
- Assign each load a tier (1 = highest priority).
- Choose how the load should be controlled (device on/off or action groups).
- Optionally set runtime quotas and allowed time windows.

4) Watch it run
- As headroom rises, SolarSmart starts loads beginning with Tier 1.
- As headroom falls, SolarSmart sheds loads starting from the highest tier.

---

## Configuration

### Main Device (Controller)

Key options typically include:
- Mode:
  - Grid-Only
  - PV + Consumption + Battery
- Meters / States:
  - Net Grid state (single number), and/or
  - PV watts, Site Consumption watts, Battery watts
- Scheduler:
  - Check frequency (e.g., every 30–120 seconds)
- Logging:
  - Info (default), Debug, Very Verbose

Recommendations:
- Use Grid-Only Mode if your metering supports it (most accurate).
- Start with a moderate check frequency (e.g., 60s).
- Use Debug logging briefly while tuning.

### Load Device(s)

Each load can be configured with:
- Tier (priority):
  - Lower number = starts earlier and is shed later.
- Control method:
  - Direct device on/off, or
  - Action Groups to start/stop.
- Optional limits and constraints:
  - Quota (max runtime) per day/period.
  - Allowed time window (hh:mm → hh:mm).
  - Allowed days of week.
  - Optional minimum headroom to start, and/or sustained headroom to continue (if exposed in your version).
  - Safety off behavior when headroom drops below zero.

Note: Field names may vary slightly by version.

### Test Device (Simulation)

- Provide simulated values for PV, consumption, battery, and/or net grid.
- Use to validate tier scheduling, quotas, and shed behavior without risking real loads.

---

## Scheduling Logic and Tiers

- Start order:
  - SolarSmart evaluates headroom on each scheduler run.
  - Loads are considered in ascending tier order (Tier 1 first).
  - A load must be within its allowed time window/days (if set) and meet any minimum headroom criteria before start.
- Shed order:
  - When headroom drops, higher tier numbers are stopped first (Tier 4 before Tier 3, etc.).
- Avoiding rapid cycling:
  - Use a reasonable check frequency (e.g., 60s).
  - Consider using minimum headroom thresholds and/or your device’s own safeguards.
  - Quotas can cap total run time and provide natural breaks.

---

## Quotas and Run Limits

- Per-load quotas limit runtime per day (or configured period, if available in your version).
- When a load reaches its quota, SolarSmart will not start it again until the quota resets.
- Use quotas to prioritize limited energy budget across multiple loads (e.g., pool pump max 3h/day; EV charger off-peak window only).

---

## 1. Scheduled Catch‑Up (Fallback Runtime)

### What Problem Does Catch‑Up Solve?

Some devices (pool pumps, chlorinators, ventilation fans, etc.) must run a **minimum amount of time** each quota window (e.g. per day or rolling window) regardless of how much excess solar was actually available.  
Scheduled Catch‑Up guarantees a fallback runtime:

- If the device already ran enough minutes naturally (because there was plenty of solar), catch‑up does **nothing**.
- If it did **not** reach the fallback target, the plugin will force it ON during your defined “catch‑up window” (often overnight / off‑peak) until the shortfall is eliminated.

### Key Concepts (Plain English)

| Term | Meaning |
|------|---------|
| Catch‑up Runtime (mins) | The fallback minimum you want the load to achieve during the current quota window. |
| Served (RuntimeQuotaMins) | Total minutes the load has already run this quota window (any reason). |
| Remaining Fallback | catchupRuntimeMins − served (never below 0). |
| catchupActive | True only while the plugin forcibly runs the load to make up the deficit. |
| Concurrency | Obeys the “Max Concurrent Loads” setting on the Main device. |

### Life Cycle Example

| Scenario | Result |
|----------|--------|
| Fallback set to 120 min. Load already ran 155 min naturally. | No catch‑up needed (Remaining Fallback = 0). |
| Fallback = 180 min. Load ran only 70 min by window start. | Remaining Fallback = 110 → plugin forces ON in catch‑up window until +110 min accumulated. |
| Fallback = 60 min. Device has run 0 min. Window opens. | Plugin starts it (catchupActive = true). |
| While catch‑up running headroom goes negative. | Catch‑up continues (it ignores headroom) unless concurrency or you manually stop it. |
| Target reached (Remaining Fallback = 0) before window end. | Plugin stops the load; catchupActive = false. |
| Window closes with Remaining Fallback > 0. | Plugin stops the load; will try again next catch‑up window if still within quota window. |

### How to Enable Catch‑Up

1. Open the SmartSolar Load device (Devices.xml type: `solarsmartLoad`).
2. Check “Enable Scheduled Catch‑up”.
3. Set:
   - Catch‑up Runtime (mins) – your fallback target (e.g. 120).
   - Catch‑up Window Start / End – off‑peak hours (e.g. 00:00 → 06:00).
4. (Optional) Adjust Max Concurrent Loads on the SolarSmart Main device.
5. Save. Wait a scheduler tick (default ~60 seconds) or enable high debug to watch immediately.

### What Triggers a Catch‑Up Start?

All must be true:
- `enableCatchup` is checked.
- `catchupRuntimeMins` > 0.
- Remaining Fallback > 0.
- Device is currently OFF.
- Current time is inside the defined catch‑up window.
- Concurrency limit not exceeded.
- Only one catch‑up start per tick (internal safety throttle).

### Stopping Conditions for Catch‑Up

Catch‑up started device stops when:
- Remaining Fallback = 0 (target satisfied), **or**
- Current time exits the catch‑up window, **or**
- You manually turn it OFF, **or**
- Quota window rolls over (the runtime counters reset), **or**
- Plugin / Indigo restarts (it re‑evaluates next tick).

### Important Device States (SmartSolar Load)

| State | Description |
|-------|-------------|
| `catchupDailyTargetMins` | Exactly the configured fallback (Catch‑up Runtime). |
| `catchupRemainingTodayMins` | Minutes still needed to satisfy fallback (never negative). |
| `catchupActive` | True only while **plugin‑forced** catch‑up run is in progress. |
| `catchupRunTodayMins` | Minutes accumulated under catch‑up active time only. |
| `catchupRunWindowAccumMins` | Mirrors `catchupRunTodayMins` (placeholder). |
| `catchupLastStart` / `catchupLastStop` | Time stamps (YYYY‑MM‑DD HH:MM:SS) when catch‑up run last began/ended. |
| `RuntimeQuotaMins` | Total runtime this quota window (all causes). |
| `RemainingQuotaMins` | Remaining regular (non‑fallback) quota minutes if a max is configured. |

### Typical Control Page Elements

- Fallback Target: `catchupDailyTargetMins`
- Fallback Remaining: `catchupRemainingTodayMins`
- Active Indicator: `catchupActive`
- Last Start / Last Stop
- Total Quota Runtime: `RuntimeQuotaMins`

### Why Isn’t It Starting?

| Check | Reason |
|-------|--------|
| `enableCatchup` unchecked | Feature disabled. |
| `catchupRuntimeMins` = 0 | No fallback required. |
| Remaining Fallback = 0 | Already satisfied by normal runtime. |
| Outside catch‑up window | Wait until window start. |
| Concurrency limit reached | Another load occupies a slot. |
| Start throttle | One catch‑up start already happened this tick. |
| Not enough time passed | Wait for the next scheduler interval. |

### Logging (Deep Diagnostics)

Turn on `debug5` (in plugin preferences) to see lines like:

```
[CATCHUP][EVAL] LoadName tier=1 served=70m target=180m remaining=110m ...
[CATCHUP][START] LoadName: remaining=110m ...
[CATCHUP][KEEP] ...
[CATCHUP][STOP] ...
[CATCHUP][NO-NEED] ...
```

A summary line at tick end shows total candidates, starts, stops, and satisfied loads.

---

## 2. Solar Forecast (forecast.solar Integration)

### Purpose

Estimate today’s and tomorrow’s PV generation so you can:
- Pre‑run discretionary loads if tomorrow looks poor.
- Time energy‑intensive tasks near the forecast peak.
- Show production expectations on Indigo Control Pages.

### How It Works

1. Reads latitude & longitude from Indigo server settings.
2. Queries `forecast.solar` with:
   - Tilt (declination)
   - Azimuth (orientation)
   - System size (kWp)
3. Normalizes all timestamps to your local timezone.
4. Summarizes per LOCAL day:
   - Total kWh
   - Peak kW & peak time
5. Caches raw JSON for 1 hour (file per Main device) to respect API rate limits.
6. Background thread wakes every 10 minutes:
   - Uses cache if < 1 hour old
   - Fetches new data otherwise
7. If rate limited (HTTP 429), logs info and reuses last cached result.

### Enabling the Forecast

1. Open the SolarSmart Main device.
2. Check “Enable Solar Forecast (forecast.solar)”.
3. Set (or accept defaults):
   - Panel Tilt / Declination (deg)
   - Panel Azimuth (see below)
   - System Size (kWp DC)
4. Save. Within a minute (or sooner if cache valid) forecast states appear.

### Azimuth (forecast.solar Convention)

| Direction | Degrees |
|-----------|---------|
| South | 0 |
| West | 90 |
| North | 180 |
| East | 270 (you may also supply -90) |

### Key Main Device States

| State | Description |
|-------|-------------|
| `forecastTodayDate` / `forecastTomorrowDate` | Local date keys (YYYY‑MM‑DD). |
| `forecastTodayKWh` / `forecastTomorrowKWh` | Daily production estimate in kWh (stringified float). |
| `forecastPeakKWToday` / `forecastPeakKWTomorrow` | Peak instantaneous kW (float). |
| `forecastPeakTimeToday` / `forecastPeakTimeTomorrow` | Local timestamp of strongest production (YYYY‑MM‑DD HH:MM). |
| `forecastSolarSummary` | Comma‑separated daily totals (friendly format). |
| `forecastSolarPeaks` | “Peaks: <time> = <kW>, …” summary. |

### Cache Location

`Preferences/Plugins/com.GlennNZ.indigoplugin.SmartSolar/forecast_main_<MainDeviceID>.json`

(Contains the raw API payload including `ratelimit` info.)

### Rate Limit Strategy

| Behavior | Detail |
|----------|--------|
| Cache lifespan | 1 hour |
| Thread wake interval | 10 minutes |
| Network call frequency | At most once per hour (per Main device) |
| Rate limit exceeded | Reuse last cached payload; log INFO message. |

### Common Uses

| Goal | Automation Idea |
|------|-----------------|
| Run flexible load before poor day | If `forecastTomorrowKWh < X` then increase today’s run time. |
| Time EV / battery charge | Use `forecastPeakTimeToday` to schedule controlled ramp or preheat. |
| Control Page forecast panel | Display `forecastSolarSummary` & `forecastSolarPeaks`. |

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| No forecast states | Ensure checkbox enabled; verify server location; check log for errors. |
| Tomorrow values blank or 0 early | API sometimes populates later; will appear on next refresh. |
| Data stale > 1h | Delete cache file to force early refetch or wait for next hourly expiry. |
| Rate limit messages | Normal; plugin automatically falls back to cached data. |

### Accuracy Tips

- Set kWp to DC nameplate (sum of all panels).
- Use correct tilt / azimuth for your physical array(s).
- Forecast is weather‑model dependent: treat as planning guidance, not absolute truth.
- If you have multiple arrays with very different azimuths, consider separate Main devices (one per array) if you need per‑plane breakdown (each gets its own cache file).

### Example Control Page Layout (Simple)

| Label | Linked State |
|-------|--------------|
| Today (kWh) | `forecastTodayKWh` |
| Peak (kW @ time) | `forecastPeakKWToday` + `forecastPeakTimeToday` |
| Tomorrow (kWh) | `forecastTomorrowKWh` |
| Tomorrow Peak | `forecastPeakKWTomorrow` + `forecastPeakTimeTomorrow` |
| Summary | `forecastSolarSummary` |
| Peaks | `forecastSolarPeaks` |

### Logging Levels

| Level | Content |
|-------|---------|
| INFO | Rate limit notices, start/stop summaries. |
| debug2 / debug3 | Cache hits/misses, normalization steps. |
| debug (general) | Summaries and peak lines, truncated raw JSON. |

---

## Quick Reference Cheat Sheet

| Task | Steps |
|------|-------|
| Guarantee pump runs at least 2h overnight if solar was poor | Set Catch‑up Runtime = 120, window = 00:00–06:00, enable catch‑up. |
| Display today + tomorrow forecast on Control Page | Enable forecast, place `forecastSolarSummary` or individual states. |
| Diagnose why catch‑up not starting | Enable `debug5`, check `[CATCHUP][EVAL]` lines & remaining fallback. |
| Force fresh forecast fetch | Delete cache file or wait until >1h since last fetch. |
| Limit simultaneous forced runs | Adjust “Max Concurrent Loads” on Main device. |

---

## FAQs

**Q: Does catch‑up exceed my regular max runtime quota?**  
A: Catch‑up counts toward the same served minutes. It won’t *ignore* your quota cap; if quota is exhausted the load becomes ineligible and catch‑up won’t start.

**Q: Can a load be both normally running and catch‑up active?**  
A: If it was already ON when deficit existed, it stays a “normal” run (catchupActive stays False). Catch‑up only marks ownership when it *starts* the load.

**Q: Why is `catchupRunTodayMins` low even though fallback is satisfied?**  
A: Those minutes count only plugin‑forced (active) time. Normal passive runtime still reduces `catchupRemainingTodayMins` but does not increment the “run under catch‑up” counter.

**Q: Can I change azimuth to use traditional compass values (e.g. 180 = South)?**  
A: forecast.solar uses 0=South; the plugin applies your entry directly. Enter values per its convention (documented above).

---

## Logging and Diagnostics

The plugin logs to Indigo’s Event Log with selectable verbosity.

Typical messages:

Info
- SolarSmart: Headroom = 2424 W (Grid −2424 W)
- SolarSmart: Starting Tier 1 load “Pool Pump”
- SolarSmart: Shedding Tier 3 load “Laundry Dryer” (headroom −450 W)
- SolarSmart: Next scheduler check in 60s

Debug
- SolarSmart: Mode=GridOnly, Grid=−2424 W, Headroom=2424 W
- SolarSmart: Tier scan → T1:on, T2:eligible, T3:hold (window closed)
- SolarSmart: Scheduled ‘start EV Charger’ (min window met)
- SolarSmart: Quota remaining for “Water Heater”: 00:43:12 today

Very Verbose
- SolarSmart: Tick(60s): PV=3800, Site=3134, Battery=−9000 → Headroom=9666
- SolarSmart: DOW allowed=True; TimeWindow 08:30–16:30 → within window

Diagnostics tips:
- If event decisions look wrong, verify sign conventions for your meters.
- If loads don’t start, check time windows/days, quotas, and tier ordering.
- Use Debug temporarily to trace scheduling decisions end-to-end.

---

## Examples

1) Pool Pump (Tier 1)
- Goal: Run as much as possible on excess solar.
- Config:
  - Tier 1, no quota or high quota (e.g., 6h/day).
  - Allowed window 09:00–17:00.
- Behavior:
  - Starts early when headroom goes positive.
  - Sheds late when headroom tightens.

2) EV Charger (Tier 2 or 3)
- Goal: Charge only when exporting, avoid imports.
- Config:
  - Tier 2 or 3, set a daily quota (e.g., 2h).
  - Optional window 10:00–16:00.
- Behavior:
  - Starts after Tier 1 loads are satisfied.
  - Stops quickly if headroom dips below zero.

3) Water Heater (Tier 2)
- Goal: Heat during surplus; cap daily runtime.
- Config:
  - Tier 2, quota 1h/day, window 11:00–15:00.
- Behavior:
  - Opportunistic heating using sunshine, with strict cap.

---

## Tips and Best Practices

- Prefer Grid-Only mode when available; it captures PV, battery, and consumption in one number.
- Start conservatively:
  - Reasonable check frequency (60–120s).
  - Add one or two loads and observe behavior for a few days.
- Use quotas to prevent one load from monopolizing your headroom.
- Apply time windows and DOW filters for household routines and comfort.
- If your device supports it, use Action Groups for more robust multi-step start/stop sequences.

---

## Troubleshooting

- Event times/decisions seem wrong
  - Check meter sign conventions and the selected mode.
  - Ensure the correct Indigo device states are mapped.
- Loads never start
  - Verify quotas haven’t been reached.
  - Confirm allowed window and days of week.
  - Increase logging to Debug and review scheduler decisions.
- Rapid cycling
  - Increase scheduler check interval.
  - Add minimum headroom thresholds (if available).
  - Add time windows or quotas to reduce contention.

---

## Requirements and Compatibility

- Indigo Domotics 2024 on macOS.
- The plugin runs within Indigo’s Python environment.
- You need device states that represent either:
  - Net grid power (preferred), or
  - PV power, site consumption, and battery power.

---


## Acknowledgements

- Indigo Domotics for the automation platform.
- Thanks to solar enthusiasts for real-world scenarios and testing.


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


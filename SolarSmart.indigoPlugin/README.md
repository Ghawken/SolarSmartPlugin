# ☀️ SolarSmart — Indigo Plugin for Solar Load Prioritisation

**SolarSmart** is an Indigo Domotics plugin (Python 3, async-driven) that automatically matches your **solar generation surplus** to discretionary household loads, ensuring that excess PV power is **used locally instead of exported at a loss**.

You define your loads (e.g., pool pump, hot water heater, spa heater), give each a **priority tier**, runtime quota, and activation method, and SolarSmart dynamically schedules them according to available solar headroom.

---

## ✨ Features

- **Main SolarSmart Device**
  - Monitors *solar generation*, *household consumption*, and optionally *battery charge/discharge* from any Indigo device states you select.
  - Computes **headroom** (available surplus power) in real-time.
  - Configurable **max concurrent loads**.

- **SmartSolar Load Devices**
  - Each represents one controllable discretionary load.
  - Assign to **priority tiers** (Tier 1 runs before Tier 2, etc.).
  - Set **rated wattage**, **runtime quota**, and **quota window** (e.g., 4 hours every 24 h).
  - Supports **Allowed Days of Week** restrictions.
  - Turn on/off via Indigo Action Groups (fully configurable).
  - “Test ON” and “Test OFF” buttons for manual verification.

- **Scheduler**
  - Async loop checks solar headroom every 60 s (configurable).
  - Runs highest-priority loads that fit within current headroom.
  - Can run multiple loads if surplus allows (up to `max_concurrent_loads`).
  - Tracks remaining runtime minutes per quota window.
  - Alternates loads within the same tier if needed to share runtime.

- **Persistence**
  - Load runtime counters and quota anchors are stored in Indigo device states so they **survive plugin restarts**.
  - Optional JSON snapshot backup in `~/Pictures/Indigo-smartSolar/`.

- **Visualisation**
  - Scheduler table rendered as **emoji-enhanced PNG** for use in Indigo Control Pages.
  - Colour-coded load status (running, idle) with headroom summary.

---

## 🖥 Requirements

- Indigo Domotics **2024.2** or later (Python 3 plugin host)
- macOS 11+ (tested on Monterey/Ventura/Sonoma)
- `Pillow` Python library (for table PNG rendering)
- At least one Indigo device that reports:
  - **Solar generation** (Watts)
  - **Household consumption** (Watts)
  - *(Optional)* Battery charging/discharging (Watts)

---

## 📦 Installation

1. **Download** the latest `.indigoPlugin` bundle from [Releases](../../releases).
2. Double-click to install into Indigo.
3. Enable the plugin in **Plugins → SolarSmart → Enable**.

---

## ⚙️ Configuration

### 1. Main SolarSmart Device
Create a new **SolarSmart Main** device.

In its config UI:
- **Solar Production Source** — select an Indigo device/state providing PV generation in Watts.
- **Consumption Source** — select an Indigo device/state providing total household consumption in Watts.
- **Battery Source** *(optional)* — select an Indigo device/state for battery charge/discharge in Watts (positive = charging, negative = discharging).
- **Max Concurrent Loads** — how many loads can run simultaneously.
- The device will expose these **custom states**:
  - `SolarProduction`
  - `Consumption`
  - `Headroom`
  - `BatteryPower` *(if set)*
  - `schedulerTable` *(formatted table of load status)*

### 2. SmartSolar Load Devices
Create one **SmartSolar Load** device for each controllable load.

Config UI options:
- **Load Name** — descriptive (e.g., "Pool Pump").
- **Rated Wattage** — expected load draw when ON.
- **Priority Tier** — 1 (highest) to N.
- **Max Runtime per Quota Window** — in minutes.
- **Quota Window** — period over which runtime quota applies (e.g., 12 h, 24 h, 2 days).
- **Allowed Days of Week** — select days the load may run.
- **Action Group ON** — Indigo Action Group to start the load.
- **Action Group OFF** — Indigo Action Group to stop the load.
- **Test ON/OFF** buttons — manually test control.

The device will expose states:
- `IsRunning`
- `Status`
- `RemainingQuotaMins`
- `RuntimeQuotaMins`
- `RuntimeWindowMins`
- `QuotaAnchorTs`
- `LastStartTs`
- `LastReason`

---

## 🚦 How It Works

1. **Every 60 s**, the scheduler reads the **Main SolarSmart** device’s headroom.
2. **Tier-by-tier scheduling**:
   - For each tier, start loads that fit within headroom and quota/time constraints.
   - If surplus allows, start more than one load (up to `max_concurrent_loads`).
   - Alternate loads in the same tier if all cannot run simultaneously.
3. **Runtime accounting**:
   - Every minute a load runs, its `RuntimeQuotaMins` increases and `RemainingQuotaMins` decreases.
   - Quota resets when its **Quota Window** elapses.
4. **Visual feedback**:
   - Scheduler table with emojis and colour-coded statuses is rendered to a PNG file in:
     ```
     ~/Pictures/Indigo-smartSolar/scheduler.png
     ```
     You can use this in Indigo Control Pages.

---

## 📊 Example

**Devices:**

| Load            | Tier | Watts | Quota | Window | Days  |
|-----------------|------|-------|-------|--------|-------|
| Pool Pump       | 1    | 2000  | 240m  | 24h    | Daily |
| Water Heater    | 1    | 4800  | 360m  | 24h    | Daily |
| Spa Heater      | 2    | 3000  | 180m  | 24h    | Fri–Sun |

**Scenario:**  
- PV producing 6 kW, household consuming 2 kW → **headroom 4 kW**.
- Scheduler will start Pool Pump (2 kW) + Spa Heater (3 kW) if quota & day allow; if only 4 kW headroom, runs Pool Pump + defers Spa Heater.

---

## 🛠 Development Notes

- **Async Design**: The scheduler runs as a dedicated `asyncio` loop inside the plugin; no `runConcurrentThread` is used.
- **Persistence**: All load state is stored in Indigo device states; survives restarts. Optional JSON backup can be enabled.
- **Fonts**: Emoji rendering uses `Apple Color Emoji.ttc` at size 20 for colour output in PNG.

---


---

## 🙋 Support

For issues, please use [GitHub Issues](../../issues).  
When reporting a bug, include:
- Indigo version
- macOS version
- Plugin debug log excerpt (`debug` and `debug2` enabled)
- Steps to reproduce

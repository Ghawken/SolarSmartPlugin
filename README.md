# 🌞 SolarSmart for Indigo

**SolarSmart** is an Indigo Domotics plugin designed to intelligently schedule and control electrical loads based on **solar production**, **site consumption**, and **battery state of charge (SOC)**.  
It automates turning loads on and off to maximise self-consumption of solar energy, reduce grid imports, and prevent overload.

---

## 📦 Features

- **Tier-based load scheduling** — higher priority loads run first; lower priority loads start only if headroom is available.
- **Quota management** — limit runtime per load per day.
- **Cooldown periods** — prevent rapid on/off cycling of devices.
- **Max concurrent loads** — avoid exceeding available power or inverter limits.
- **Emergency shedding** — immediately stop loads if headroom becomes negative.
- **Per-load surge margin** — account for startup surges when deciding if a load can start.
- **Test device mode** — override real solar meter readings with manual test values for PV, consumption, and battery power.
- **Visual scheduler table** — optional PNG rendering showing current scheduling state.
- **Emoji support** (🌞🔌📈) — works with Apple Color Emoji font at supported sizes.
- **Adjustable scheduler frequency** — set the number of minutes between scheduling checks.

---

## ⚙️ How It Works

1. **Reads solar data** from your Indigo devices:
   - Solar PV production (Watts)
   - Site consumption (Watts)
   - Battery charging/discharging power (Watts, optional)
2. Calculates **headroom**:
Headroom = PV Production - Site Consumption - Battery Charging
(Battery discharge increases headroom.)
3. Runs the **scheduler** at the configured interval:
- Turns **ON** loads if headroom ≥ load's surge-adjusted requirement and other constraints pass.
- Turns **OFF** loads if headroom is negative or quotas are exceeded.
4. Logs **informational events** whenever a device is switched ON or OFF:
- ON log includes headroom, PV production, and consumption.
- OFF log includes runtime if available and reason for stopping.

---

## 🧪 Testing Mode

For safe and repeatable testing without affecting your actual solar system:

1. **Create a “SolarSmart Testing Production” device**:
- Enter PV, consumption, and battery values as positive/negative integers.
- Positive battery value = charging; negative = discharging.
- This test device **overrides** the main solar readings when enabled.
2. When in use, the plugin logs:
Using SolarSmart Test device 'SolarSmart Testing Production': PV=8000 W, Cons=2000 W, Batt=0 W

3. **Disable or delete** the test device after testing.

---

## 📋 Device Setup

### Main Solar Device
- Must have **PV Production** state (Watts).
- Optional: **Site Consumption** and **Battery Power** states.
- Configure in plugin preferences.

### Load Devices
Each load you want controlled must be set up as a **SolarSmart Load** with:
- **Tier** (priority level, 1 = highest)
- **Rated Watts**
- **Surge Multiplier** (default 1.2)
- **Start Margin (%)** (extra headroom buffer)
- **Quota (minutes/day)**
- **Cooldown (minutes)**
- **Action Group** to turn device ON/OFF

![Load Device Setup](https://github.com/Ghawken/SolarSmartPlugin/blob/main/Images/Load_Device.png?raw=true)

---

## 🔧 Plugin Preferences

| Setting | Description |
| ------- | ----------- |
| **Time (mins) for Checks** | Interval between scheduling checks. Shorter intervals respond faster to changing conditions but may increase switching frequency. |
| **Debug Logging** | Enable detailed logging for troubleshooting. |
| **Debug2 Logging** | Extra-verbose logging including table outputs and decision traces. |

---

## 📄 Logging

- **Info logs**:
- Device turning ON: `Turning ON 'Pool Pump' — PV=5000 W, Consumption=2500 W, Headroom=2000 W`
- Device turning OFF: `Turning OFF 'EV Charger' after 45 min — Reason: Lost headroom`
- **Debug logs** (when enabled):
- Scheduler decisions per tick.
- Headroom calculation details.
- Tier-by-tier table of device states.

---

## 🚀 Installation

1. Download the plugin from the [GitHub Releases](../../releases) page.
2. Install into Indigo via **Plugins → Manage Plugins**.
3. Configure in **Plugins → SolarSmart → Configure...**.
4. Add your **Main Solar Device** and **SolarSmart Load Devices**.
5. Optionally create a **SolarSmart Test Device** for simulations.

---

## 🧠 Tips & Notes

- Use **accurate rated wattages** for each load to avoid overloading.
- Set a **reasonable start margin** for surge-prone devices (e.g., pumps, compressors).
- If using a **battery system**, ensure battery charging/discharging is correctly signed.
- **One load starts per scheduler tick** — this prevents sudden large changes to load.
- Emergency shedding removes **only one device per tick** to avoid abrupt shutdowns.

---









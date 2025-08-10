# SolarSmart Indigo Plugin

SolarSmart is an Indigo Domotics plugin designed to intelligently control loads (devices) based on your solar production, consumption, battery usage, and optionally net grid usage.

![Main Device Setup](https://github.com/Ghawken/SolarSmartPlugin/blob/main/Images/Main_device.png?raw=true)

## What is "Headroom"?

Headroom is the amount of *excess power* available that can be used to start and run discretionary loads.

Think of it like this:
- If **Headroom** is *positive*, you have spare power available.
- If **Headroom** is *negative*, you’re short on power and importing from the grid.

### Example 1 — Grid-Only Mode:
```
Mode: Net Grid mode only — Net grid power will be the only factor in headroom calculations.
(This is likely the most accurate, if your meters support it)
This means PV, battery, and site consumption values are reported but ignored.
Current grid reading: -2424 W → 🔋 Exporting to grid
Positive headroom of 2424 W — loads may be allowed to start.
```
Here, a **negative grid reading** means you are exporting power to the grid. That export amount becomes your headroom.

### Example 2 — PV + Consumption + Battery Mode:
```
Mode: PV, site consumption, and battery power used to calculate headroom.
PV = 3800 W, Site Consumption = 3134 W, Battery = -9000 W (discharging)
Battery discharge adds to headroom.
Calculated headroom = 3800 - 3134 + 9000 = 9666 W
```
Here, battery discharging increases your headroom because it's adding energy to your home.

### Example 3 — No Spare Power:
```
Mode: Net Grid mode only
Current grid reading: 1800 W → ⚡ Importing from grid
Negative headroom of -1800 W — loads will be stopped.
```
Here, **positive grid reading** means you’re importing from the grid, so there’s no excess power available.

---

## How Loads Are Scheduled

SolarSmart allows you to assign loads to **tiers**. Loads in lower-numbered tiers start first when headroom is available. Higher-tier loads start only if lower tiers are already running and spare headroom remains.

![Load Device Setup](https://github.com/Ghawken/SolarSmartPlugin/blob/main/Images/Load_Device.png?raw=true)

- **Tier 1**: High-priority loads (e.g., pool pump)
- **Tier 2+**: Secondary loads (e.g., EV charger, water heater)

When headroom drops, loads are turned off starting from the highest tier first.

---

## Key Features

- Choose between **Grid-Only** mode and **PV + Consumption + Battery** mode.
- Automatic tiered scheduling of loads based on available headroom.
- Configurable check frequency (minutes between scheduler runs).
- Quota system to limit maximum runtime per day or other period.
- Test Device support to simulate solar production and consumption for testing.

---

## Installation

1. Install the plugin in Indigo.
2. Create a **SolarSmart Main Device** and configure it with your solar, consumption, battery, or grid meter sources.
3. Create **SolarSmart Load Devices**, assign them to tiers, and specify how they turn on/off (direct control or via Action Groups).
4. (Optional) Create a **Test Device** to simulate readings.

---

## Tips

- **Grid-Only mode** is most accurate if your metering supports it, as it reflects all solar, battery, and consumption in one number.
- Use **PV + Consumption + Battery mode** if you don’t have a grid meter state available.
- Keep check frequency reasonable — too short may cause rapid load cycling, too long may miss changes in solar output.

---


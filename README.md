# Tuya Garden Timers

Home Assistant custom integration for Tuya BLE water timers (ggq / sfkzq categories), providing real-time local LAN polling with cloud fallback for schedule and last-watered data.

## Features

- **Valve switches** — open/close each zone directly from HA
- **Rain delay selects** — set weather delay per zone (Off / 1–7 days)
- **Battery sensor** — per device
- **Last Watered Duration** — how long the last run was (updated from local LAN every 30 s for ggq devices)
- **Last Watered At** — timestamp of the last watering run (from cloud, updated every 5 min by default)
- **Next Scheduled** — next scheduled watering time computed from the decoded schedule payload
- **Refresh Cloud Data button** — trigger an immediate cloud data refresh on demand

## Supported devices

| Category | Description |
|---|---|
| `ggq` | Dual-zone BLE water timer (e.g. 一出二水阀V2, product `qycalacn`) |
| `sfkzq` | Single-zone BLE water timer (e.g. 一出一水阀, product `nxquc5lb`) |

## Installation via HACS

1. In HACS, go to **Integrations → Custom repositories**
2. Add `https://github.com/mdubs/garden-timer` with category **Integration**
3. Click **Download**
4. Restart Home Assistant
5. Go to **Settings → Integrations → Add Integration** and search for **Tuya Garden Timers**
6. Enter your [Tuya IoT Platform](https://iot.tuya.com) credentials (region, Access ID, Access Secret)

## Data polling

| Data | Source | Default interval |
|---|---|---|
| Battery, valve state, rain delay, watering active | Local LAN | 30 s |
| Last watered duration (ggq) | Local LAN | 30 s |
| Last watered timestamps, schedule | Tuya Cloud | 5 min (configurable) |
| Zone custom names | Tuya Cloud | Daily |

Writes (valve open/close, rain delay) use local LAN first, falling back to the Tuya Cloud API.

## Requirements

- [Tuya IoT Platform](https://iot.tuya.com) account with your devices linked
- Home Assistant 2024.1.0 or newer
- HACS 2.0.0 or newer

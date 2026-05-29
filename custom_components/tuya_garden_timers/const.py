"""Constants for Tuya Garden Timers integration."""

DOMAIN = "tuya_garden_timers"

# Default polling intervals
DEFAULT_LOCAL_SCAN_INTERVAL = 30      # seconds — local LAN poll
DEFAULT_CLOUD_FAST_INTERVAL = 300     # seconds — last-watered timestamps + schedule
CLOUD_SLOW_INTERVAL = 86400           # seconds — zone names (daily, not configurable)

CONF_REGION = "api_region"
CONF_ACCESS_ID = "access_id"
CONF_ACCESS_SECRET = "access_secret"
CONF_LOCAL_SCAN_INTERVAL = "local_scan_interval"
CONF_CLOUD_FAST_INTERVAL = "cloud_fast_interval"
# Stored internally in config entry after first cloud fetch
CONF_TOPOLOGY = "topology"

REGIONS = ["eu", "us", "in", "cn"]

WATERING_CATEGORIES = {"ggq", "sfkzq"}
GATEWAY_CATEGORIES = {"wg2"}

# ---------------------------------------------------------------------------
# Local DP number maps (confirmed by live device query 2026-05-29)
# ---------------------------------------------------------------------------
# ggq — dual-zone BLE water timer (e.g. product qycalacn)
GGQ_ZONE_DEFS = [
    {
        "zone_num": 1,
        "switch_code": "switch_1",
        "rain_delay_code": "weather_delay",
        "switch_dp": 104,        # bool
        "rain_delay_dp": 117,    # str enum e.g. "OFF"/"1"…"7"
        "use_time_dp": 111,      # int seconds, zone-1 last-watered duration
        "state_dp": 112,         # str "idle"/"watering"
        "shadow_use_time": "use_time_1",
        "shadow_rain_delay": "weather_delay",
    },
    {
        "zone_num": 2,
        "switch_code": "switch_2",
        "rain_delay_code": "weather_delay2",
        "switch_dp": 105,
        "rain_delay_dp": 114,
        "use_time_dp": 110,      # int seconds, zone-2 last-watered duration
        "state_dp": 113,
        "shadow_use_time": "use_time_2",
        "shadow_rain_delay": "weather_delay2",
    },
]

# sfkzq — single-zone BLE water timer (e.g. product nxquc5lb)
SFKZQ_ZONE_DEFS = [
    {
        "zone_num": 1,
        "switch_code": "switch",
        "rain_delay_code": "weather_delay",
        "switch_dp": 1,
        "rain_delay_dp": 10,
        "use_time_dp": None,     # sfkzq local DP 9 appears to be cumulative, use cloud
        "state_dp": 12,
        "shadow_use_time": "use_time_1",
        "shadow_rain_delay": "weather_delay",
    },
]

ZONE_DEFS_BY_CATEGORY = {"ggq": GGQ_ZONE_DEFS, "sfkzq": SFKZQ_ZONE_DEFS}

# Battery DP per category
BATTERY_DP = {"ggq": 11, "sfkzq": 7}

# ---------------------------------------------------------------------------
# Rain delay option maps  (label → Tuya DPS value)
# ---------------------------------------------------------------------------
RAIN_DELAY_OPTIONS_GGQ = {
    "Off": "OFF",
    "1 day": "1",
    "2 days": "2",
    "3 days": "3",
    "4 days": "4",
    "5 days": "5",
    "6 days": "6",
    "7 days": "7",
}

RAIN_DELAY_OPTIONS_SFKZQ = {
    "Off": "cancel",
    "24 hours": "24h",
    "48 hours": "48h",
    "72 hours": "72h",
}

RAIN_DELAY_OPTIONS_BY_CATEGORY = {
    "ggq": RAIN_DELAY_OPTIONS_GGQ,
    "sfkzq": RAIN_DELAY_OPTIONS_SFKZQ,
}

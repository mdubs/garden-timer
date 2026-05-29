"""DataUpdateCoordinator — hybrid local-LAN + Tuya Cloud data fetching.

Tier 1 — Local LAN (default every 30 s):
  battery, valve state, rain delay value, watering-active state,
  last-watered duration (ggq only via DP).

Tier 2 — Cloud fast (configurable, default every 5 min):
  last-watered timestamps (shadow property `.time` field),
  schedule payload → next-scheduled computation.

Tier 3 — Cloud slow (daily, not configurable):
  zone custom names, device names.

If local reads fail (HA on a different network, gateway offline) the
integration falls back silently to cloud-only data.
"""
from __future__ import annotations

import base64
import logging
import struct
from datetime import date, datetime, timedelta, timezone
from typing import Any

import tinytuya
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    BATTERY_DP,
    CLOUD_SLOW_INTERVAL,
    CONF_ACCESS_ID,
    CONF_ACCESS_SECRET,
    CONF_CLOUD_FAST_INTERVAL,
    CONF_GW_IP_PREFIX,
    CONF_LOCAL_SCAN_INTERVAL,
    CONF_REGION,
    CONF_SWAP_GGQ_ZONES,
    CONF_TOPOLOGY,
    DEFAULT_CLOUD_FAST_INTERVAL,
    DEFAULT_LOCAL_SCAN_INTERVAL,
    DOMAIN,
    GATEWAY_CATEGORIES,
    WATERING_CATEGORIES,
    ZONE_DEFS_BY_CATEGORY,
)

_LOGGER = logging.getLogger(__name__)

_TUYA_DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


# ---------------------------------------------------------------------------
# Topology helpers (module-level so config_flow can import them directly)
# ---------------------------------------------------------------------------

def build_topology_from_devices(devices: list) -> dict:
    """Map gateway IDs → sub-device lists (no IPs yet)."""
    gateways: dict[str, dict] = {}
    subs_by_gw: dict[str, list] = {}

    for dev in devices:
        gw_id = dev.get("gateway_id", "")
        cat = dev.get("category", "")
        dev_id = dev.get("id", "")
        if not dev_id:
            continue
        if not gw_id and cat in GATEWAY_CATEGORIES:
            gateways[dev_id] = {
                "id": dev_id,
                "key": dev.get("key", ""),
                "ip": None,
                "sub_devices": [],
            }
        elif gw_id and cat in WATERING_CATEGORIES:
            subs_by_gw.setdefault(gw_id, []).append({
                "id": dev_id,
                "node_id": dev.get("node_id", ""),
                "category": cat,
                "name": dev.get("name", dev_id),
            })

    for gw_id, subs in subs_by_gw.items():
        if gw_id in gateways:
            gateways[gw_id]["sub_devices"] = subs

    return gateways


def apply_scan_ips(topology: dict) -> dict:
    """Run deviceScan() and fill in gateway IPs."""
    try:
        scan = tinytuya.deviceScan(verbose=False, maxretry=8, color=False)
    except Exception as err:
        _LOGGER.debug("deviceScan failed: %s", err)
        return topology

    for scan_id, info in scan.items():
        # tinytuya deviceScan keyed by gwId (device ID) → {ip, ...}
        if scan_id in topology:
            topology[scan_id]["ip"] = info.get("ip")
        else:
            # Some versions key by IP; check gwId field
            gw_id = info.get("gwId") or info.get("id", "")
            if gw_id in topology:
                topology[gw_id]["ip"] = info.get("ip")

    found = sum(1 for g in topology.values() if g.get("ip"))
    _LOGGER.debug("deviceScan: found IPs for %d/%d gateways", found, len(topology))
    return topology


# ---------------------------------------------------------------------------
# Schedule binary decoding (unchanged logic from original)
# ---------------------------------------------------------------------------

def _decode_entry_ggq(data: bytes) -> dict | None:
    if len(data) < 13:
        return None
    start_mins = struct.unpack(">H", data[0:2])[0]
    duration_mins = struct.unpack(">H", data[2:4])[0]
    days_bitmask = data[4]
    volume_pct = data[5]
    flags = data[6]
    year = struct.unpack(">H", data[7:9])[0]
    month = data[9]
    day = data[10]
    interval = data[11]
    zone_index = data[12]
    return _build_entry(start_mins, duration_mins, days_bitmask, volume_pct,
                        flags, year, month, day, interval, zone_index)


def _decode_entry_sfkzq(data: bytes) -> dict | None:
    if len(data) < 12:
        return None
    start_mins = struct.unpack(">H", data[0:2])[0]
    duration_mins = struct.unpack(">H", data[2:4])[0]
    days_bitmask = data[4]
    volume_pct = data[5]
    flags = data[6]
    year = struct.unpack(">H", data[7:9])[0]
    month = data[9]
    day = data[10]
    interval = data[11]
    return _build_entry(start_mins, duration_mins, days_bitmask, volume_pct,
                        flags, year, month, day, interval, 0)


def _build_entry(start_mins, duration_mins, days_bitmask, volume_pct,
                 flags, year, month, day, interval, zone_index) -> dict:
    enabled = bool(flags & 0x01)
    is_interval = bool(flags & 0x40)
    is_even_days = bool(flags & 0x20)
    is_odd_days = bool(flags & 0x10)

    entry: dict = {
        "enabled": enabled,
        "start_minutes": start_mins,
        "duration_minutes": duration_mins,
        "volume_percent": volume_pct,
        "zone": zone_index + 1,
    }

    if is_even_days or is_odd_days:
        entry["mode"] = "calendar"
        entry["calendar_type"] = "even" if is_even_days else "odd"
    elif is_interval:
        entry["mode"] = "interval"
        entry["interval_days"] = interval
        try:
            entry["reference_date"] = date(year, month, day) if year else None
        except ValueError:
            entry["reference_date"] = None
    else:
        entry["mode"] = "weekly"
        entry["days_bitmask"] = days_bitmask

    return entry


def _decode_timer_payload(b64_value: str, category: str) -> list[dict]:
    try:
        raw = base64.b64decode(b64_value)
    except Exception:
        return []
    if len(raw) <= 2:
        return []
    entries = []
    if category == "sfkzq":
        if len(raw) >= 14:
            e = _decode_entry_sfkzq(raw[2:14])
            if e:
                entries.append(e)
    elif category == "ggq":
        pos = 2
        while pos + 13 <= len(raw):
            e = _decode_entry_ggq(raw[pos:pos + 13])
            if e:
                entries.append(e)
            pos += 13
            if pos < len(raw) and pos + 13 <= len(raw):
                pos += 1
    return entries


def _compute_next_scheduled(entry: dict) -> datetime | None:
    if not entry or not entry.get("enabled"):
        return None
    start_mins = entry.get("start_minutes", 0)
    start_h, start_m = divmod(start_mins, 60)
    mode = entry.get("mode", "weekly")
    now = datetime.now()
    today = now.date()

    if mode == "weekly":
        mask = entry.get("days_bitmask", 0)
        bit_to_py = {6: 6, 5: 0, 4: 1, 3: 2, 2: 3, 1: 4, 0: 5}
        active = {py for bit, py in bit_to_py.items() if mask & (1 << bit)}
        if not active:
            return None
        for ahead in range(8):
            check = today + timedelta(days=ahead)
            if check.weekday() in active:
                dt = datetime(check.year, check.month, check.day, start_h, start_m)
                if dt > now:
                    return dt
        return None

    elif mode == "interval":
        ref = entry.get("reference_date")
        interval_days = entry.get("interval_days") or 1
        if not ref:
            return None
        days_since = (today - ref).days
        if days_since < 0:
            next_run = ref
        else:
            if days_since % interval_days == 0:
                dt_today = datetime(today.year, today.month, today.day, start_h, start_m)
                if dt_today > now:
                    return dt_today
            periods = days_since // interval_days
            next_run = ref + timedelta(days=(periods + 1) * interval_days)
        return datetime(next_run.year, next_run.month, next_run.day, start_h, start_m)

    elif mode == "calendar":
        cal_type = entry.get("calendar_type", "")
        for ahead in range(35):
            check = today + timedelta(days=ahead)
            if (cal_type == "even" and check.day % 2 == 0) or \
               (cal_type == "odd" and check.day % 2 == 1):
                dt = datetime(check.year, check.month, check.day, start_h, start_m)
                if dt > now:
                    return dt
        return None

    return None


def _schedule_summary(entry: dict | None) -> str | None:
    if not entry:
        return None
    start_mins = entry.get("start_minutes", 0)
    start_h, start_m = divmod(start_mins, 60)
    duration = entry.get("duration_minutes", 0)
    status = "on" if entry.get("enabled") else "off"
    mode = entry.get("mode", "weekly")
    line = f"{start_h:02d}:{start_m:02d}, {duration}min [{status}]"
    if mode == "weekly":
        mask = entry.get("days_bitmask", 0)
        days = [_TUYA_DAYS[i] for i in range(7) if mask & (1 << (6 - i))]
        line += f", {', '.join(days) if days else 'no days'}"
    elif mode == "interval":
        line += f", every {entry.get('interval_days', '?')} days"
    elif mode == "calendar":
        line += f", {entry.get('calendar_type', '')} days of month"
    return line


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

class TuyaGardenCoordinator(DataUpdateCoordinator):
    """Hybrid local-LAN + cloud coordinator for garden watering timers."""

    def __init__(self, hass: HomeAssistant, config: dict, entry=None) -> None:
        self._config = config
        self._entry = entry  # ConfigEntry — used to persist discovered IPs
        self._cloud: tinytuya.Cloud | None = None

        self._local_scan_interval = int(
            config.get(CONF_LOCAL_SCAN_INTERVAL, DEFAULT_LOCAL_SCAN_INTERVAL)
        )
        self._cloud_fast_secs = int(
            config.get(CONF_CLOUD_FAST_INTERVAL, DEFAULT_CLOUD_FAST_INTERVAL)
        )

        # Gateway topology built from cloud + local scan
        # {gateway_id: {id, ip, key, sub_devices: [{id, node_id, category, name}]}}
        self._topology: dict[str, dict] = dict(config.get(CONF_TOPOLOGY) or {})

        # Apply manual IP overrides from options (gw_ip_<gateway_id> keys)
        for gw_id in self._topology:
            manual_ip = config.get(f"{CONF_GW_IP_PREFIX}{gw_id}", "").strip()
            if manual_ip:
                self._topology[gw_id]["ip"] = manual_ip
                _LOGGER.debug("[init] Using manual IP %s for gateway %s", manual_ip, gw_id[:12])

        # Cached tier data (survive across update cycles)
        self._local_dps: dict[str, dict[int, Any]] = {}     # device_id → {dp_num: val}
        self._local_dps_ts: dict[str, datetime] = {}        # device_id → last successful read time
        self._cloud_fast: dict[str, dict] = {}              # device_id → shadow props
        self._cloud_slow: dict[str, dict] = {}              # device_id → slow data

        self._last_cloud_fast: datetime | None = None
        self._last_cloud_slow: datetime | None = None

        # Local data older than this many seconds is treated as stale in _merge
        self._local_stale_secs: int = self._local_scan_interval * 3

        # Whether local reads are working (avoids log spam)
        self._local_ok: bool = bool(self._topology)

        # IP re-scan tracking: re-scan when no IPs on startup, or after repeated failures
        self._last_ip_scan: datetime | None = None
        self._local_fail_count: int = 0

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=self._local_scan_interval),
        )

    # ------------------------------------------------------------------
    # Cloud helper
    # ------------------------------------------------------------------

    def _get_cloud(self) -> tinytuya.Cloud:
        if self._cloud is None:
            self._cloud = tinytuya.Cloud(
                apiRegion=self._config[CONF_REGION],
                apiKey=self._config[CONF_ACCESS_ID],
                apiSecret=self._config[CONF_ACCESS_SECRET],
            )
        return self._cloud

    # ------------------------------------------------------------------
    # Topology: build from getdevices() + deviceScan()
    # ------------------------------------------------------------------

    def _discover_topology(self) -> dict:
        cloud = self._get_cloud()
        devices = cloud.getdevices()
        if not isinstance(devices, list):
            return {}
        topology = build_topology_from_devices(devices)
        topology = apply_scan_ips(topology)
        return topology

    # ------------------------------------------------------------------
    # Tier 1 — Local LAN reads
    # ------------------------------------------------------------------

    def _read_local_gateway(self, gw: dict) -> dict[str, dict]:
        """Read all watering sub-devices under one gateway. Returns {dev_id: dps}."""
        results: dict[str, dict] = {}
        ip = gw.get("ip")
        if not ip:
            _LOGGER.debug("[local] Gateway %s has no IP — skipping", gw.get("id"))
            return results

        _LOGGER.debug("[local] Polling gateway %s @ %s (%d sub-devices)",
                      gw.get("id"), ip, len(gw.get("sub_devices", [])))
        gw_dev = tinytuya.Device(gw["id"], ip, gw["key"], version=3.4)

        for sub in gw.get("sub_devices", []):
            try:
                sub_dev = tinytuya.Device(
                    sub["id"], ip, gw["key"],
                    node_id=sub["node_id"], parent=gw_dev, version=3.4,
                )
                status = sub_dev.status()
                if status and "dps" in status:
                    dps = {int(k): v for k, v in status["dps"].items()}
                    results[sub["id"]] = dps
                    _LOGGER.debug("[local] %s (%s) → %s", sub.get("name", sub["id"]), sub["id"], dps)
                elif status and status.get("Error"):
                    _LOGGER.debug("[local] %s (%s) error: %s", sub.get("name", sub["id"]), sub["id"], status["Error"])
                else:
                    _LOGGER.debug("[local] %s (%s) — empty/no-dps response: %s", sub.get("name", sub["id"]), sub["id"], status)
            except Exception as err:
                _LOGGER.debug("[local] %s (%s) exception: %s", sub.get("name", sub["id"]), sub["id"], err)

        return results

    def _read_all_local(self) -> dict[str, dict]:
        _LOGGER.debug("[local] Starting local poll cycle (%d gateways)", len(self._topology))
        all_dps: dict[str, dict] = {}
        for gw_id, gw in self._topology.items():
            try:
                gw_dps = self._read_local_gateway(gw)
                all_dps.update(gw_dps)
            except Exception as err:
                _LOGGER.debug("[local] Gateway %s poll failed: %s", gw_id, err)
        _LOGGER.debug("[local] Poll cycle complete — %d/%d devices responded",
                      len(all_dps),
                      sum(len(g.get("sub_devices", [])) for g in self._topology.values()))
        return all_dps

    def _is_local_fresh(self, device_id: str) -> bool:
        """Return True if the cached local DPs for this device are recent enough."""
        ts = self._local_dps_ts.get(device_id)
        if ts is None:
            _LOGGER.debug("[merge] %s — no local timestamp, using cloud data", device_id)
            return False
        age = (datetime.now() - ts).total_seconds()
        fresh = age < self._local_stale_secs
        if not fresh:
            _LOGGER.debug("[merge] %s — local data stale (%.0fs old, limit %ds), using cloud data",
                          device_id, age, self._local_stale_secs)
        return fresh

    # ------------------------------------------------------------------
    # Tier 2 — Cloud fast (last-watered timestamps + schedule)
    # ------------------------------------------------------------------

    def _fetch_shadow(self, cloud: tinytuya.Cloud, device_id: str) -> dict:
        result = cloud._tuyaplatform(
            f"cloud/thing/{device_id}/shadow/properties",
            ver="v2.0",
        )
        props_list = result.get("result", {}).get("properties", [])
        return {p["code"]: p for p in props_list}

    def _read_cloud_fast(self, device_ids: list[str]) -> dict[str, dict]:
        cloud = self._get_cloud()
        data: dict[str, dict] = {}
        for did in device_ids:
            try:
                data[did] = self._fetch_shadow(cloud, did)
            except Exception as err:
                _LOGGER.debug("Cloud fast shadow failed %s: %s", did, err)
        return data

    # ------------------------------------------------------------------
    # Tier 3 — Cloud slow (device names + zone custom names, daily)
    # ------------------------------------------------------------------

    def _read_cloud_slow(self) -> dict[str, dict]:
        cloud = self._get_cloud()

        # getdevices() must be called first to init auth state
        try:
            devices = cloud.getdevices()
        except Exception as err:
            _LOGGER.warning("Cloud slow getdevices failed: %s", err)
            return {}

        data: dict[str, dict] = {}
        watering = [d for d in devices if d.get("category") in WATERING_CATEGORIES]

        for dev in watering:
            dev_id = dev["id"]
            data[dev_id] = {
                "name": dev.get("name", dev_id),
                "category": dev.get("category", "ggq"),
                "product": dev.get("product_name", ""),
                "zone_names": {},
                "schedule_b64": None,
            }

        # Fetch shadow for zone names (custom_name) + schedule payload
        for dev_id, entry in data.items():
            category = entry["category"]
            try:
                props = self._fetch_shadow(cloud, dev_id)
            except Exception as err:
                _LOGGER.debug("Cloud slow shadow failed %s: %s", dev_id, err)
                continue

            zone_names: dict[str, str] = {}
            for code in ["switch_1", "switch_2", "switch"]:
                cname = props.get(code, {}).get("custom_name")
                if cname:
                    zone_names[code] = cname
            entry["zone_names"] = zone_names

            # Schedule payload
            sched_code = "normal_timer" if category == "ggq" else "timer"
            if sched_code in props:
                entry["schedule_b64"] = props[sched_code].get("value")

        return data

    # ------------------------------------------------------------------
    # Merge all tiers into entity-ready dict
    # ------------------------------------------------------------------

    def _merge(self) -> dict[str, dict]:
        result: dict[str, dict] = {}

        for device_id, slow in self._cloud_slow.items():
            category = slow.get("category", "ggq")
            # Only use local DP cache if data is fresh; otherwise fall through
            # to cloud shadow so external changes (app, scheduled runs) show up.
            local_dps = self._local_dps.get(device_id, {}) if self._is_local_fresh(device_id) else {}
            fast_props = self._cloud_fast.get(device_id, {})

            battery_dp = BATTERY_DP.get(category)
            battery = local_dps.get(battery_dp) if battery_dp else None
            # Fall back to cloud shadow for battery if local unavailable
            if battery is None:
                battery = fast_props.get("battery_percentage", {}).get("value")

            zones = self._build_zones(device_id, category, slow, local_dps, fast_props)

            result[device_id] = {
                "device_id": device_id,
                "name": slow.get("name", device_id),
                "category": category,
                "product": slow.get("product", ""),
                "battery": battery,
                "zones": zones,
            }

        return result

    def _build_zones(
        self,
        device_id: str,
        category: str,
        slow: dict,
        local_dps: dict,
        fast_props: dict,
    ) -> list[dict]:
        zone_defs = ZONE_DEFS_BY_CATEGORY.get(category, [])

        # If the user has enabled zone swap (valve DPs appear reversed), swap
        # switch_dp and state_dp between zone 1 and zone 2 without disturbing
        # the use_time / schedule / rain-delay mappings that were already correct.
        if category == "ggq" and self._config.get(CONF_SWAP_GGQ_ZONES) and len(zone_defs) == 2:
            z0, z1 = zone_defs[0], zone_defs[1]
            zone_defs = [
                {**z0, "switch_dp": z1["switch_dp"], "switch_code": z1["switch_code"], "state_dp": z1["state_dp"]},
                {**z1, "switch_dp": z0["switch_dp"], "switch_code": z0["switch_code"], "state_dp": z0["state_dp"]},
            ]
        zone_names = slow.get("zone_names", {})
        schedule_b64 = slow.get("schedule_b64")
        entries = _decode_timer_payload(schedule_b64, category) if schedule_b64 else []

        zones = []
        for zdef in zone_defs:
            zone_num = zdef["zone_num"]
            switch_code = zdef["switch_code"]
            name = zone_names.get(switch_code) or f"Zone {zone_num}"

            # --- Real-time data: prefer local, fall back to cloud fast ---
            valve_open = local_dps.get(zdef["switch_dp"])
            if valve_open is None:
                # Fall back to cloud shadow switch value
                valve_open = fast_props.get(switch_code, {}).get("value", False)

            rain_dp = local_dps.get(zdef["rain_delay_dp"])
            if rain_dp is None:
                rain_dp = fast_props.get(zdef["shadow_rain_delay"], {}).get("value")
            rain_delay = str(rain_dp) if rain_dp is not None else ("OFF" if category == "ggq" else "cancel")

            is_watering = False
            if zdef.get("state_dp") and local_dps:
                is_watering = local_dps.get(zdef["state_dp"]) == "watering"

            # --- Last-watered duration (local DP if available, else cloud fast) ---
            duration_s: int | None = None
            use_dp = zdef.get("use_time_dp")
            if use_dp:
                duration_s = local_dps.get(use_dp)
            if duration_s is None:
                duration_s = fast_props.get(zdef["shadow_use_time"], {}).get("value")

            # --- Last-watered timestamp (cloud fast only) ---
            ut_prop = fast_props.get(zdef["shadow_use_time"], {})
            last_watered_ms: int | None = ut_prop.get("time")

            # --- Schedule ---
            zone_entries = [e for e in entries if e.get("zone") == zone_num]
            active = zone_entries[0] if zone_entries else None

            zones.append({
                "zone_num": zone_num,
                "name": name,
                "switch_code": switch_code,
                "rain_delay_code": zdef["rain_delay_code"],
                "switch_dp": zdef["switch_dp"],
                "rain_delay_dp": zdef["rain_delay_dp"],
                "valve_open": bool(valve_open),
                "is_watering": is_watering,
                "last_watered_duration_s": duration_s,
                "last_watered_at_ms": last_watered_ms,
                "rain_delay": rain_delay,
                "schedule_entry": active,
                "next_scheduled": _compute_next_scheduled(active),
                "schedule_summary": _schedule_summary(active),
            })

        return zones

    # ------------------------------------------------------------------
    # Public write helpers
    # ------------------------------------------------------------------

    def _local_write(self, device_id: str, dp: int, value: Any) -> bool:
        """Write a single DP to a device via local LAN. Returns True on success."""
        # Find which gateway this device is under
        for gw in self._topology.values():
            for sub in gw.get("sub_devices", []):
                if sub["id"] == device_id:
                    ip = gw.get("ip")
                    if not ip:
                        return False
                    try:
                        gw_dev = tinytuya.Device(gw["id"], ip, gw["key"], version=3.4)
                        sub_dev = tinytuya.Device(
                            device_id, ip, gw["key"],
                            node_id=sub["node_id"], parent=gw_dev, version=3.4,
                        )
                        result = sub_dev.set_value(dp, value)
                        if result and result.get("Error"):
                            _LOGGER.debug(
                                "Local write DP%d=%r on %s: %s",
                                dp, value, device_id, result["Error"],
                            )
                            return False
                        return True
                    except Exception as err:
                        _LOGGER.debug("Local write failed %s DP%d: %s", device_id, dp, err)
                        return False
        return False

    def _cloud_write(self, device_id: str, commands: list[dict]) -> bool:
        """Send cloud commands. Returns True on success."""
        try:
            cloud = self._get_cloud()
            result = cloud._tuyaplatform(
                f"devices/{device_id}/commands",
                action="POST",
                post={"commands": commands},
                ver="v1.0",
            )
            success = bool(result.get("success", False))
            if not success:
                _LOGGER.warning("Cloud command to %s non-success: %s", device_id, result)
            return success
        except Exception as err:
            _LOGGER.error("Cloud write failed %s: %s", device_id, err)
            return False

    def send_command(self, device_id: str, dp: int, value: Any, code: str) -> bool:
        """Write a value — local first, then cloud fallback."""
        if self._topology and self._local_write(device_id, dp, value):
            _LOGGER.debug("Local write OK: %s DP%d=%r", device_id, dp, value)
            return True
        # Cloud fallback
        return self._cloud_write(device_id, [{"code": code, "value": value}])

    def optimistic_update_dp(self, device_id: str, dp: int, value: Any) -> None:
        """Immediately inject a known-good value into the local DP cache.

        Called right after a successful write so the entity reflects the new
        state instantly, without waiting for the next LAN poll to confirm it.
        """
        if device_id not in self._local_dps:
            self._local_dps[device_id] = {}
        self._local_dps[device_id][dp] = value
        # Keep the freshness timestamp current so _merge uses this value
        self._local_dps_ts[device_id] = datetime.now()

    # ------------------------------------------------------------------
    # Force cloud fast refresh (called by button entity)
    # ------------------------------------------------------------------

    async def async_force_cloud_refresh(self) -> None:
        """Reset the cloud-fast timer so the next update fetches fresh data."""
        self._last_cloud_fast = None
        await self.async_request_refresh()

    # ------------------------------------------------------------------
    # HA DataUpdateCoordinator hook
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict:
        now = datetime.now()

        # --- Topology discovery (once, or if empty) ---
        if not self._topology:
            try:
                self._topology = await self.hass.async_add_executor_job(
                    self._discover_topology
                )
                found = sum(1 for g in self._topology.values() if g.get("ip"))
                _LOGGER.info(
                    "Topology discovered: %d gateways, %d with local IPs",
                    len(self._topology), found,
                )
            except Exception as err:
                _LOGGER.warning("Topology discovery failed: %s", err)

        # --- IP re-scan: on startup (topology has no IPs) or after repeated local failures ---
        if self._topology:
            no_ips = not any(g.get("ip") for g in self._topology.values())
            ip_scan_stale = (
                self._last_ip_scan is None
                or (now - self._last_ip_scan).total_seconds() >= 600
            )
            if no_ips or (self._local_fail_count >= 3 and ip_scan_stale):
                try:
                    _LOGGER.debug(
                        "[update] Re-scanning gateway IPs (no_ips=%s, fail_count=%d)",
                        no_ips, self._local_fail_count,
                    )
                    prev_found = sum(1 for g in self._topology.values() if g.get("ip"))
                    await self.hass.async_add_executor_job(apply_scan_ips, self._topology)
                    found = sum(1 for g in self._topology.values() if g.get("ip"))
                    _LOGGER.debug(
                        "[update] IP re-scan complete: %d/%d gateways have IPs",
                        found, len(self._topology),
                    )
                    self._last_ip_scan = now
                    # Persist newly discovered IPs back to the config entry
                    if self._entry is not None and found > prev_found:
                        from .const import CONF_TOPOLOGY
                        updated = dict(self._entry.data)
                        updated[CONF_TOPOLOGY] = self._topology
                        self.hass.config_entries.async_update_entry(self._entry, data=updated)
                        _LOGGER.debug("[update] Persisted %d gateway IPs to config entry", found)
                except Exception as err:
                    _LOGGER.debug("[update] IP re-scan failed: %s", err)

        # --- Tier 1: Local LAN ---
        if self._topology:
            try:
                _LOGGER.debug("[update] Starting local LAN poll at %s", now.strftime("%H:%M:%S"))
                local = await self.hass.async_add_executor_job(self._read_all_local)
                if local:
                    self._local_dps.update(local)
                    # Stamp freshness timestamp for every device that responded
                    for device_id in local:
                        self._local_dps_ts[device_id] = now
                    self._local_ok = True
                    self._local_fail_count = 0
                    _LOGGER.debug("[update] Local poll OK — %d devices updated", len(local))
                else:
                    self._local_fail_count += 1
                    _LOGGER.debug(
                        "[update] Local poll returned no devices this cycle (fail_count=%d)",
                        self._local_fail_count,
                    )
            except Exception as err:
                self._local_fail_count += 1
                _LOGGER.debug("[update] Local poll exception: %s", err)
        else:
            _LOGGER.debug("[update] No topology — skipping local poll")

        # --- Tier 3: Cloud slow (daily or first run) ---
        need_slow = (
            not self._cloud_slow
            or self._last_cloud_slow is None
            or (now - self._last_cloud_slow).total_seconds() >= CLOUD_SLOW_INTERVAL
        )
        if need_slow:
            try:
                slow = await self.hass.async_add_executor_job(self._read_cloud_slow)
                if slow:
                    self._cloud_slow = slow
                    self._last_cloud_slow = now
                    _LOGGER.debug("Cloud slow refresh: %d devices", len(slow))
            except Exception as err:
                _LOGGER.warning("Cloud slow refresh failed: %s", err)
                if not self._cloud_slow:
                    raise UpdateFailed(f"Initial cloud fetch failed: {err}") from err

        # --- Tier 2: Cloud fast (configurable interval) ---
        need_fast = (
            self._last_cloud_fast is None
            or (now - self._last_cloud_fast).total_seconds() >= self._cloud_fast_secs
        )
        if need_fast and self._cloud_slow:
            try:
                device_ids = list(self._cloud_slow)
                fast = await self.hass.async_add_executor_job(
                    self._read_cloud_fast, device_ids
                )
                self._cloud_fast.update(fast)
                self._last_cloud_fast = now
                _LOGGER.debug("Cloud fast refresh: %d devices", len(fast))
            except Exception as err:
                _LOGGER.debug("Cloud fast refresh failed: %s", err)

        return self._merge()

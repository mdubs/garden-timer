"""Config flow for Tuya Garden Timers."""
from __future__ import annotations

import logging

import tinytuya
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ACCESS_ID,
    CONF_ACCESS_SECRET,
    CONF_CLOUD_FAST_INTERVAL,
    CONF_LOCAL_SCAN_INTERVAL,
    CONF_REGION,
    CONF_TOPOLOGY,
    DEFAULT_CLOUD_FAST_INTERVAL,
    DEFAULT_LOCAL_SCAN_INTERVAL,
    DOMAIN,
    REGIONS,
)
from .coordinator import TuyaGardenCoordinator, apply_scan_ips, build_topology_from_devices

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_REGION, default="eu"): vol.In(REGIONS),
        vol.Required(CONF_ACCESS_ID): str,
        vol.Required(CONF_ACCESS_SECRET): str,
        vol.Optional(
            CONF_LOCAL_SCAN_INTERVAL, default=DEFAULT_LOCAL_SCAN_INTERVAL
        ): vol.All(int, vol.Range(min=10, max=300)),
        vol.Optional(
            CONF_CLOUD_FAST_INTERVAL, default=DEFAULT_CLOUD_FAST_INTERVAL
        ): vol.All(int, vol.Range(min=60, max=3600)),
    }
)


def _test_and_discover(region: str, access_id: str, access_secret: str) -> dict:
    """Blocking: connect, validate, and build device topology.

    Returns a dict with keys 'ok' (bool), 'topology' (dict), 'error' (str|None).
    """
    cloud = tinytuya.Cloud(apiRegion=region, apiKey=access_id, apiSecret=access_secret)
    devices = cloud.getdevices()
    if not isinstance(devices, list) or not devices:
        return {"ok": False, "topology": {}, "error": "cannot_connect"}

    topology = build_topology_from_devices(devices)
    topology = apply_scan_ips(topology)

    found_ips = sum(1 for g in topology.values() if g.get("ip"))
    _LOGGER.debug(
        "Config flow discovery: %d gateways, %d with local IPs",
        len(topology), found_ips,
    )

    return {"ok": True, "topology": topology, "error": None}


class TuyaGardenTimersConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tuya Garden Timers."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                result = await self.hass.async_add_executor_job(
                    _test_and_discover,
                    user_input[CONF_REGION],
                    user_input[CONF_ACCESS_ID],
                    user_input[CONF_ACCESS_SECRET],
                )
                if not result["ok"]:
                    errors["base"] = result.get("error") or "cannot_connect"
                else:
                    await self.async_set_unique_id(user_input[CONF_ACCESS_ID])
                    self._abort_if_unique_id_configured()
                    data = dict(user_input)
                    data[CONF_TOPOLOGY] = result["topology"]
                    return self.async_create_entry(
                        title=f"Tuya Garden ({user_input[CONF_REGION].upper()})",
                        data=data,
                    )
            except Exception:
                _LOGGER.exception("Unexpected error during config flow")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )


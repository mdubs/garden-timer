"""Tuya Garden Timers — HA custom integration."""
from __future__ import annotations

from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import TuyaGardenCoordinator

PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.SELECT, Platform.BUTTON, Platform.CALENDAR]

_CARD_URL  = f"/{DOMAIN}/garden-timer-card-v5.js"
_CARD_PATH = Path(__file__).parent / "www" / "garden-timer-card-v5.js"


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    # Register the custom Lovelace card JS here rather than in
    # async_setup_entry: entry setup waits for the first (slow) Tuya fetch, and
    # dashboards loaded before then would get a 404 and a "Configuration error".
    await hass.http.async_register_static_paths(
        [StaticPathConfig(_CARD_URL, str(_CARD_PATH), cache_headers=False)]
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Merge options (polling intervals) over base config data
    config = dict(entry.data)
    config.update(entry.options)
    coordinator = TuyaGardenCoordinator(hass, config, entry=entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok

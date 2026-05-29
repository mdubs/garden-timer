"""Tuya Garden Timers — HA custom integration."""
from __future__ import annotations

from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import TuyaGardenCoordinator

PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.SELECT, Platform.BUTTON, Platform.CALENDAR]

_CARD_URL  = f"/{DOMAIN}/garden-timer-card.js"
_CARD_PATH = Path(__file__).parent / "www" / "garden-timer-card.js"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Register the custom Lovelace card JS (once per HA instance)
    if not hass.data.get(f"{DOMAIN}_card_registered"):
        hass.http.register_static_path(_CARD_URL, str(_CARD_PATH), cache_headers=False)
        hass.data[f"{DOMAIN}_card_registered"] = True

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

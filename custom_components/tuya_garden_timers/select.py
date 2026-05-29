"""Select platform (rain delay) for Tuya Garden Timers."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, RAIN_DELAY_OPTIONS_BY_CATEGORY
from .coordinator import TuyaGardenCoordinator
from .entity import TuyaGardenZoneEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TuyaGardenCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        RainDelaySelect(
            coordinator,
            device_id,
            zone["zone_num"],
            dev_data.get("category", "ggq"),
        )
        for device_id, dev_data in coordinator.data.items()
        for zone in dev_data.get("zones", [])
    ]
    async_add_entities(entities)


class RainDelaySelect(TuyaGardenZoneEntity, SelectEntity):
    """Select the rain (weather) delay for a zone."""

    def __init__(
        self,
        coordinator: TuyaGardenCoordinator,
        device_id: str,
        zone_num: int,
        category: str,
    ) -> None:
        super().__init__(coordinator, device_id, zone_num)
        self._attr_unique_id = f"{device_id}_z{zone_num}_rain_delay"
        self._options_map: dict[str, str] = RAIN_DELAY_OPTIONS_BY_CATEGORY.get(category, RAIN_DELAY_OPTIONS_BY_CATEGORY["ggq"])
        self._attr_options = list(self._options_map)
        self._attr_icon = "mdi:weather-rainy"

    @property
    def name(self) -> str:
        return f"{self._zone_name} Rain Delay"

    @property
    def current_option(self) -> str:
        raw = str(self._zone_data.get("rain_delay", ""))
        # Find matching label by DPS value (case-insensitive)
        for label, dps_val in self._options_map.items():
            if raw.upper() == dps_val.upper():
                return label
        # Default to first option ("Off") if not recognised
        return self._attr_options[0]

    async def async_select_option(self, option: str) -> None:
        dps_val = self._options_map.get(option)
        if dps_val is None:
            _LOGGER.warning("Unknown rain delay option: %s", option)
            return
        z = self._zone_data
        dp = z.get("rain_delay_dp") or 117
        code = z.get("rain_delay_code") or "weather_delay"
        await self.hass.async_add_executor_job(
            self.coordinator.send_command,
            self._device_id,
            dp,
            dps_val,
            code,
        )
        await self.coordinator.async_request_refresh()

"""Switch platform (valve open/close) for Tuya Garden Timers."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
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
        ValveSwitch(coordinator, device_id, zone["zone_num"])
        for device_id, dev_data in coordinator.data.items()
        for zone in dev_data.get("zones", [])
    ]
    async_add_entities(entities)


class ValveSwitch(TuyaGardenZoneEntity, SwitchEntity):
    """Toggle a watering valve open or closed."""

    def __init__(
        self,
        coordinator: TuyaGardenCoordinator,
        device_id: str,
        zone_num: int,
    ) -> None:
        super().__init__(coordinator, device_id, zone_num)
        self._attr_unique_id = f"{device_id}_z{zone_num}_valve"

    @property
    def name(self) -> str:
        return f"{self._zone_name} Valve"

    @property
    def is_on(self) -> bool:
        return bool(self._zone_data.get("valve_open", False))

    @property
    def extra_state_attributes(self) -> dict:
        return {"is_watering": self._zone_data.get("is_watering", False)}

    async def async_turn_on(self, **kwargs) -> None:  # noqa: ANN003
        await self._send(True)

    async def async_turn_off(self, **kwargs) -> None:  # noqa: ANN003
        await self._send(False)

    async def _send(self, value: bool) -> None:
        z = self._zone_data
        dp = z.get("switch_dp") or (104 if self._zone_num == 1 else 105)
        code = z.get("switch_code") or f"switch_{self._zone_num}"
        await self.hass.async_add_executor_job(
            self.coordinator.send_command,
            self._device_id,
            dp,
            value,
            code,
        )
        await self.coordinator.async_request_refresh()

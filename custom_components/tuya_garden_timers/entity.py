"""Shared base entity for Tuya Garden Timers."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TuyaGardenCoordinator


class TuyaGardenEntity(CoordinatorEntity[TuyaGardenCoordinator]):
    """Base class for all Tuya Garden Timers entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TuyaGardenCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id

    @property
    def _dev_data(self) -> dict:
        return self.coordinator.data.get(self._device_id, {})

    @property
    def device_info(self) -> DeviceInfo:
        dev = self._dev_data
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=dev.get("name", self._device_id),
            model=dev.get("product") or "Water Timer",
            manufacturer="Tuya",
        )


class TuyaGardenZoneEntity(TuyaGardenEntity):
    """Base class for zone-level entities."""

    def __init__(
        self,
        coordinator: TuyaGardenCoordinator,
        device_id: str,
        zone_num: int,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._zone_num = zone_num

    @property
    def _zone_data(self) -> dict:
        for z in self._dev_data.get("zones", []):
            if z["zone_num"] == self._zone_num:
                return z
        return {}

    @property
    def _zone_name(self) -> str:
        return self._zone_data.get("name") or f"Zone {self._zone_num}"

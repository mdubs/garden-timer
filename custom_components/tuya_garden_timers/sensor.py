"""Sensor platform for Tuya Garden Timers."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .coordinator import TuyaGardenCoordinator
from .entity import TuyaGardenEntity, TuyaGardenZoneEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TuyaGardenCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    for device_id, dev_data in coordinator.data.items():
        entities.append(BatterySensor(coordinator, device_id))
        for zone in dev_data.get("zones", []):
            z = zone["zone_num"]
            entities.append(LastWateredDurationSensor(coordinator, device_id, z))
            entities.append(LastWateredAtSensor(coordinator, device_id, z))
            entities.append(NextScheduledSensor(coordinator, device_id, z))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Battery
# ---------------------------------------------------------------------------

class BatterySensor(TuyaGardenEntity, SensorEntity):
    """Battery level for the device."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: TuyaGardenCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_battery"
        self._attr_name = "Battery"

    @property
    def native_value(self) -> int | None:
        return self._dev_data.get("battery")


# ---------------------------------------------------------------------------
# Zone sensors
# ---------------------------------------------------------------------------

class LastWateredDurationSensor(TuyaGardenZoneEntity, SensorEntity):
    """Duration (seconds) of the most recent watering run.

    Updated at the local-LAN poll rate (default 30 s) for ggq devices;
    falls back to cloud-fast rate (default 5 min) for sfkzq.
    """

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: TuyaGardenCoordinator,
        device_id: str,
        zone_num: int,
    ) -> None:
        super().__init__(coordinator, device_id, zone_num)
        self._attr_unique_id = f"{device_id}_z{zone_num}_last_watered_duration"

    @property
    def name(self) -> str:
        return f"{self._zone_name} Last Watered Duration"

    @property
    def native_value(self) -> int | None:
        val = self._zone_data.get("last_watered_duration_s")
        return int(val) if val is not None else None

    @property
    def extra_state_attributes(self) -> dict:
        category = self._dev_data.get("category", "ggq")
        source = "local" if category == "ggq" else "cloud"
        return {"data_source": source}


class LastWateredAtSensor(TuyaGardenZoneEntity, SensorEntity):
    """Timestamp of the most recent watering run."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: TuyaGardenCoordinator,
        device_id: str,
        zone_num: int,
    ) -> None:
        super().__init__(coordinator, device_id, zone_num)
        self._attr_unique_id = f"{device_id}_z{zone_num}_last_watered_at"

    @property
    def name(self) -> str:
        return f"{self._zone_name} Last Watered At"

    @property
    def native_value(self) -> datetime | None:
        ms = self._zone_data.get("last_watered_at_ms")
        if ms:
            return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
        return None


class NextScheduledSensor(TuyaGardenZoneEntity, SensorEntity):
    """Next scheduled watering time for this zone."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: TuyaGardenCoordinator,
        device_id: str,
        zone_num: int,
    ) -> None:
        super().__init__(coordinator, device_id, zone_num)
        self._attr_unique_id = f"{device_id}_z{zone_num}_next_scheduled"

    @property
    def name(self) -> str:
        return f"{self._zone_name} Next Scheduled"

    @property
    def native_value(self) -> datetime | None:
        dt = self._zone_data.get("next_scheduled")
        if dt is None:
            return None
        # Coordinator returns a naive local datetime; convert to aware UTC for HA
        return dt_util.as_utc(dt_util.as_local(dt))

    @property
    def extra_state_attributes(self) -> dict:
        return {"schedule": self._zone_data.get("schedule_summary")}

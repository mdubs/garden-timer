"""Button platform — manual cloud data refresh for Tuya Garden Timers."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import TuyaGardenCoordinator
from .entity import TuyaGardenEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TuyaGardenCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RefreshCloudButton(coordinator)])


class RefreshCloudButton(TuyaGardenEntity, ButtonEntity):
    """Trigger an immediate cloud data refresh (last-watered + schedules)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cloud-refresh"

    def __init__(self, coordinator: TuyaGardenCoordinator) -> None:
        # Use a stable fake device ID so it doesn't depend on any one device
        super().__init__(coordinator, "__hub__")
        self._attr_unique_id = f"{DOMAIN}_cloud_refresh"
        self._attr_name = "Refresh Cloud Data"

    @property
    def device_info(self):  # noqa: ANN201
        from homeassistant.helpers.device_registry import DeviceInfo
        return DeviceInfo(
            identifiers={(DOMAIN, "__hub__")},
            name="Tuya Garden Timers Hub",
            manufacturer="Tuya",
        )

    async def async_press(self) -> None:
        await self.coordinator.async_force_cloud_refresh()

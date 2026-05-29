"""Calendar platform — one entity per zone so each gets its own colour in the
week-view card.

The HA Calendar card (Settings → Dashboards → Calendar, or a Lovelace Calendar
card) automatically assigns a distinct colour to each calendar entity.  Add all
7 zone calendars to the card and they will render as coloured time-blocks at the
correct position on the week grid.

Disabled schedules are included on the same entity (same colour family) but
prefixed with ⏸ so they are visually distinct without being a different colour.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .coordinator import TuyaGardenCoordinator
from .entity import TuyaGardenZoneEntity

_LOGGER = logging.getLogger(__name__)

# How many days ahead to generate events
_MAX_LOOKAHEAD_DAYS = 14


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TuyaGardenCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[CalendarEntity] = []

    for device_id, dev_data in coordinator.data.items():
        for zone in dev_data.get("zones", []):
            entities.append(
                WateringZoneCalendar(coordinator, device_id, zone["zone_num"])
            )

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Per-zone calendar entity
# ---------------------------------------------------------------------------

class WateringZoneCalendar(TuyaGardenZoneEntity, CalendarEntity):
    """Calendar showing the watering schedule for one zone.

    One entity per zone means each zone gets its own colour in the HA Calendar
    card week view.  Enabled events show the plain zone name; disabled events
    are prefixed with ⏸ so they read as 'this colour but inactive'.
    """

    def __init__(
        self,
        coordinator: TuyaGardenCoordinator,
        device_id: str,
        zone_num: int,
    ) -> None:
        super().__init__(coordinator, device_id, zone_num)
        self._attr_unique_id = f"{device_id}_zone{zone_num}_calendar"

    @property
    def name(self) -> str:
        dev_name = self._dev_data.get("name", self._device_id)
        return f"{dev_name} — {self._zone_name}"

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming *enabled* event for this zone."""
        now = dt_util.now()
        horizon = now + timedelta(days=_MAX_LOOKAHEAD_DAYS)
        upcoming = self._get_events(now, horizon, enabled_only=True)
        return min(upcoming, key=lambda e: e.start) if upcoming else None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return all events (enabled + disabled) in the requested range."""
        cap = dt_util.now() + timedelta(days=_MAX_LOOKAHEAD_DAYS)
        effective_end = min(end_date, cap)
        if effective_end <= start_date:
            return []
        return self._get_events(start_date, effective_end, enabled_only=False)

    def _get_events(
        self,
        range_start: datetime,
        range_end: datetime,
        enabled_only: bool = False,
    ) -> list[CalendarEvent]:
        entry = self._zone_data.get("schedule_entry")
        if not entry:
            return []
        return _events_for_entry(
            entry,
            self._zone_name,
            range_start,
            range_end,
            enabled_only=enabled_only,
        )


# ---------------------------------------------------------------------------
# Event expansion helpers
# ---------------------------------------------------------------------------

def _events_for_entry(
    entry: dict,
    zone_name: str,
    range_start: datetime,
    range_end: datetime,
    enabled_only: bool = False,
) -> list[CalendarEvent]:
    """Expand a schedule entry into CalendarEvents within [range_start, range_end)."""
    if not entry:
        return []

    enabled: bool = bool(entry.get("enabled"))
    if enabled_only and not enabled:
        return []

    start_mins: int = entry.get("start_minutes", 0)
    duration_mins: int = entry.get("duration_minutes", 0)
    if duration_mins <= 0:
        return []

    mode: str = entry.get("mode", "weekly")

    if enabled:
        summary = zone_name
        description = f"{duration_mins} min · {_mode_label(entry)}"
    else:
        summary = f"⏸ {zone_name}"
        description = f"Schedule disabled · {duration_mins} min · {_mode_label(entry)}"

    day_start = range_start.date()
    day_end = (range_end - timedelta(seconds=1)).date()
    events: list[CalendarEvent] = []

    current = day_start
    while current <= day_end:
        if _day_matches(entry, mode, current):
            ev_start = _local_midnight(current) + timedelta(minutes=start_mins)
            ev_end = ev_start + timedelta(minutes=duration_mins)
            if ev_end > range_start and ev_start < range_end:
                events.append(
                    CalendarEvent(
                        start=ev_start,
                        end=ev_end,
                        summary=summary,
                        description=description,
                    )
                )
        current += timedelta(days=1)

    return events


def _day_matches(entry: dict, mode: str, day: date) -> bool:
    if mode == "weekly":
        mask: int = entry.get("days_bitmask", 0)
        if not mask:
            return False
        # Tuya bitmask: bit6=Sun … bit0=Sat; Python weekday: 0=Mon … 6=Sun
        py_to_bit = {0: 5, 1: 4, 2: 3, 3: 2, 4: 1, 5: 0, 6: 6}
        return bool(mask & (1 << py_to_bit[day.weekday()]))

    elif mode == "interval":
        ref: date | None = entry.get("reference_date")
        interval: int = entry.get("interval_days") or 1
        if not ref:
            return False
        return (day - ref).days % interval == 0 and day >= ref

    elif mode == "calendar":
        cal_type: str = entry.get("calendar_type", "")
        if cal_type == "even":
            return day.day % 2 == 0
        elif cal_type == "odd":
            return day.day % 2 == 1

    return False


def _local_midnight(day: date) -> datetime:
    local_tz = dt_util.DEFAULT_TIME_ZONE
    return datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=local_tz)


def _mode_label(entry: dict) -> str:
    mode = entry.get("mode", "weekly")
    if mode == "weekly":
        mask = entry.get("days_bitmask", 0)
        days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        active = [days[i] for i in range(7) if mask & (1 << (6 - i))]
        return ", ".join(active) if active else "no days"
    elif mode == "interval":
        return f"every {entry.get('interval_days', '?')} days"
    elif mode == "calendar":
        return f"{entry.get('calendar_type', '')} days of month"
    return ""

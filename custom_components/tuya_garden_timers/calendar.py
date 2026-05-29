"""Calendar platform — one shared watering schedule calendar for all zones."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .coordinator import TuyaGardenCoordinator
from .entity import TuyaGardenEntity

# How many days ahead to generate events (calendar card requests from its own
# date range, but we cap generation to avoid runaway loops on very wide ranges)
_MAX_LOOKAHEAD_DAYS = 14


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TuyaGardenCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([WateringCalendar(coordinator)])


# ---------------------------------------------------------------------------
# Event expansion helpers
# ---------------------------------------------------------------------------

def _events_for_entry(
    entry: dict,
    device_name: str,
    zone_name: str,
    range_start: datetime,
    range_end: datetime,
) -> list[CalendarEvent]:
    """Expand a single schedule entry into CalendarEvents within [range_start, range_end)."""
    if not entry or not entry.get("enabled"):
        return []

    start_mins: int = entry.get("start_minutes", 0)
    duration_mins: int = entry.get("duration_minutes", 0)
    if duration_mins <= 0:
        return []

    mode: str = entry.get("mode", "weekly")
    summary = f"{device_name} — {zone_name}"
    description = f"{duration_mins} min · {_mode_label(entry)}"

    # Iterate days in [range_start.date(), range_end.date()]
    day_start = range_start.date()
    day_end = (range_end - timedelta(seconds=1)).date()
    events: list[CalendarEvent] = []

    current = day_start
    while current <= day_end:
        if _day_matches(entry, mode, current):
            ev_start = _local_midnight(current) + timedelta(minutes=start_mins)
            ev_end = ev_start + timedelta(minutes=duration_mins)
            # Only include if the event overlaps the requested range
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
    """Return True if this schedule entry fires on the given date."""
    if mode == "weekly":
        mask: int = entry.get("days_bitmask", 0)
        if not mask:
            return False
        # Tuya bitmask: bit6=Sun, bit5=Mon, …, bit0=Sat
        # Python weekday(): 0=Mon … 6=Sun
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
    """Return a timezone-aware datetime at midnight local time for the given date."""
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


# ---------------------------------------------------------------------------
# Calendar entity
# ---------------------------------------------------------------------------

class WateringCalendar(TuyaGardenEntity, CalendarEntity):
    """A single shared calendar showing all zone watering schedules."""

    _attr_name = "Watering Schedule"

    def __init__(self, coordinator: TuyaGardenCoordinator) -> None:
        super().__init__(coordinator, "__hub__")
        self._attr_unique_id = f"{DOMAIN}_watering_calendar"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "__hub__")},
            name="Tuya Garden Timers Hub",
            manufacturer="Tuya",
        )

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming watering event."""
        now = dt_util.now()
        horizon = now + timedelta(days=_MAX_LOOKAHEAD_DAYS)
        upcoming = self._collect_events(now, horizon)
        if not upcoming:
            return None
        return min(upcoming, key=lambda e: e.start)

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return events in the requested range, capped to max lookahead."""
        cap = dt_util.now() + timedelta(days=_MAX_LOOKAHEAD_DAYS)
        effective_end = min(end_date, cap)
        if effective_end <= start_date:
            return []
        return self._collect_events(start_date, effective_end)

    def _collect_events(
        self, range_start: datetime, range_end: datetime
    ) -> list[CalendarEvent]:
        events: list[CalendarEvent] = []
        data: dict = self.coordinator.data or {}

        for dev_data in data.values():
            dev_name: str = dev_data.get("name", "Timer")
            for zone in dev_data.get("zones", []):
                entry = zone.get("schedule_entry")
                if not entry:
                    continue
                zone_name: str = zone.get("name") or f"Zone {zone.get('zone_num', '?')}"
                events.extend(
                    _events_for_entry(entry, dev_name, zone_name, range_start, range_end)
                )

        return sorted(events, key=lambda e: e.start)

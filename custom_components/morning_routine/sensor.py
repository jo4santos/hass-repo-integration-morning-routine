"""Sensor platform for Morning Routine Gamification integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CHILDREN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Morning Routine sensors from config entry."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    # Create sensor for each child
    entities = []
    for child in CHILDREN:
        entities.append(MorningRoutineChildSensor(coordinator, child))

    async_add_entities(entities)
    _LOGGER.info(f"Set up {len(entities)} Morning Routine sensors")


class MorningRoutineChildSensor(CoordinatorEntity, SensorEntity):
    """Sensor tracking individual child's morning routine progress."""

    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:clipboard-check-outline"

    def __init__(self, coordinator, child: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._child = child
        self._attr_name = f"{child.capitalize()} Morning Status"
        self._attr_unique_id = f"{DOMAIN}_{child}_morning_status"

    @property
    def native_value(self) -> int:
        """Return completion percentage."""
        if not self.coordinator.data or self._child not in self.coordinator.data:
            return 0

        activities = self.coordinator.data[self._child]["activities"]
        if not activities:
            return 0

        completed = sum(1 for activity in activities if activity["completed"])
        return int((completed / len(activities)) * 100)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return detailed activity states."""
        if not self.coordinator.data or self._child not in self.coordinator.data:
            return {}

        child_data = self.coordinator.data[self._child]

        # Create fresh copies to avoid caching issues
        import copy
        activities_copy = copy.deepcopy(child_data["activities"])

        # Log what we're returning
        completed_activities = [a["id"] for a in activities_copy if a.get("completed", False)]
        _LOGGER.debug(f"[Sensor {self._child}] Returning attributes with completed activities: {completed_activities}")

        return {
            "child": self._child,
            "activities": activities_copy,
            "last_activity_time": child_data.get("last_activity_time"),
            "photo_path": child_data.get("photo_path"),
            "audio_recording": child_data.get("audio_recording"),
            "reward_image": child_data.get("reward_image"),
            "all_complete": self.native_value == 100,
            "next_reset": child_data.get("last_reset"),
            "progress": self.native_value,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()

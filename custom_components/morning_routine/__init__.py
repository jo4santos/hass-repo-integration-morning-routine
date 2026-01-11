"""The Morning Routine Gamification integration."""
from __future__ import annotations

import copy
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, Event, ServiceCall
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.event import async_track_time_change, async_call_later
from homeassistant.util import dt as dt_util
from homeassistant.components import persistent_notification
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from .const import (
    DOMAIN,
    CHILDREN,
    ACTIVITY_TYPES,
    INSTRUMENT_ICONS,
    CONF_CALENDAR_ENTITY,
    CONF_RESET_TIME,
    CONF_BUSINESS_DAYS_ONLY,
    CONF_NFC_MAPPINGS,
    CONF_OPENAI_ENABLED,
    CONF_OPENAI_PROMPT,
    DEFAULT_OPENAI_PROMPT,
    STORAGE_VERSION,
    STORAGE_KEY,
    EVENT_ACTIVITY_COMPLETED,
    EVENT_ROUTINE_COMPLETE,
    EVENT_RESET,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)

# Service schemas
SERVICE_COMPLETE_ACTIVITY_SCHEMA = vol.Schema(
    {
        vol.Required("child"): cv.string,
        vol.Required("activity"): cv.string,
        vol.Optional("completed", default=True): cv.boolean,
    }
)

SERVICE_SAVE_PHOTO_SCHEMA = vol.Schema(
    {
        vol.Required("child"): cv.string,
        vol.Required("photo_data"): cv.string,
    }
)

SERVICE_SAVE_AUDIO_SCHEMA = vol.Schema(
    {
        vol.Required("child"): cv.string,
        vol.Required("audio_data"): cv.string,
    }
)

SERVICE_RESET_ROUTINE_SCHEMA = vol.Schema(
    {
        vol.Optional("child"): cv.string,
    }
)

SERVICE_REGENERATE_REWARD_SCHEMA = vol.Schema(
    {
        vol.Required("child"): cv.string,
    }
)

SERVICE_ADD_NFC_MAPPING_SCHEMA = vol.Schema(
    {
        vol.Required("child"): cv.string,
        vol.Required("activity"): cv.string,
        vol.Optional("timeout", default=30): cv.positive_int,
    }
)

SERVICE_REMOVE_NFC_MAPPING_SCHEMA = vol.Schema(
    {
        vol.Required("tag_id"): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Morning Routine component from configuration.yaml."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Morning Routine from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Create coordinator
    coordinator = MorningRoutineCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Forward setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services
    async def handle_complete_activity(call: ServiceCall) -> None:
        """Handle complete_activity service call."""
        child = call.data["child"]
        activity = call.data["activity"]
        completed = call.data["completed"]
        await coordinator.complete_activity(child, activity, completed=completed)

    async def handle_save_photo(call: ServiceCall) -> None:
        """Handle save_photo service call."""
        child = call.data["child"]
        photo_data = call.data["photo_data"]
        await coordinator.save_photo(child, photo_data)

    async def handle_save_audio(call: ServiceCall) -> None:
        """Handle save_audio service call."""
        child = call.data["child"]
        audio_data = call.data["audio_data"]
        await coordinator.save_audio(child, audio_data)

    async def handle_reset_routine(call: ServiceCall) -> None:
        """Handle reset_routine service call."""
        child = call.data.get("child")
        await coordinator.reset_routine(child)

    async def handle_regenerate_reward(call: ServiceCall) -> None:
        """Handle regenerate_reward service call."""
        child = call.data["child"]
        await coordinator.generate_reward(child)

    async def handle_add_nfc_mapping(call: ServiceCall) -> None:
        """Handle add_nfc_mapping service call."""
        child = call.data["child"]
        activity = call.data["activity"]
        timeout = call.data.get("timeout", 30)
        await coordinator.add_nfc_mapping(child, activity, timeout)

    async def handle_remove_nfc_mapping(call: ServiceCall) -> None:
        """Handle remove_nfc_mapping service call."""
        tag_id = call.data["tag_id"]
        await coordinator.remove_nfc_mapping(tag_id)

    async def handle_list_nfc_mappings(call: ServiceCall) -> dict:
        """Handle list_nfc_mappings service call."""
        return coordinator.list_nfc_mappings()

    hass.services.async_register(
        DOMAIN,
        "complete_activity",
        handle_complete_activity,
        schema=SERVICE_COMPLETE_ACTIVITY_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        "save_photo",
        handle_save_photo,
        schema=SERVICE_SAVE_PHOTO_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        "save_audio",
        handle_save_audio,
        schema=SERVICE_SAVE_AUDIO_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        "reset_routine",
        handle_reset_routine,
        schema=SERVICE_RESET_ROUTINE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        "regenerate_reward",
        handle_regenerate_reward,
        schema=SERVICE_REGENERATE_REWARD_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        "add_nfc_mapping",
        handle_add_nfc_mapping,
        schema=SERVICE_ADD_NFC_MAPPING_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        "remove_nfc_mapping",
        handle_remove_nfc_mapping,
        schema=SERVICE_REMOVE_NFC_MAPPING_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        "list_nfc_mappings",
        handle_list_nfc_mappings,
        supports_response=True,
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()

    return unload_ok


class MorningRoutineCoordinator(DataUpdateCoordinator):
    """Coordinate morning routine data and updates."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=5),
        )
        self.config_entry = config_entry
        self.store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}_{config_entry.entry_id}")
        self._nfc_listeners = []
        self._reset_listener = None
        self._waiting_for_tag = None  # {"child": str, "activity": str, "timeout_handle": callable}

    async def async_config_entry_first_refresh(self) -> None:
        """Handle first refresh - load data and setup listeners."""
        await self._load_data()
        await self._setup_listeners()
        await super().async_config_entry_first_refresh()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data - mainly for calendar sync checks."""
        # Check if reset time passed
        if self._should_reset():
            await self.reset_routine(None)

        return self.data

    async def _load_data(self) -> None:
        """Load persisted state."""
        stored = await self.store.async_load()
        if stored:
            self.data = stored
            # Migrate activity names to Portuguese if needed
            self._migrate_activity_names()
            _LOGGER.info("Loaded stored morning routine data")
        else:
            # Initialize default structure
            self.data = {}
            for child in CHILDREN:
                self.data[child] = {
                    "activities": self._get_default_activities(),
                    "last_activity_time": None,
                    "photo_path": None,
                    "audio_recording": None,
                    "reward_image": None,
                    "last_reset": None,
                    "last_calendar_sync": None,
                }
            await self._save_data()
            _LOGGER.info("Initialized new morning routine data")

    def _migrate_activity_names(self) -> None:
        """Update activity names to current language (Portuguese)."""
        for child in CHILDREN:
            if child not in self.data:
                continue

            for activity in self.data[child].get("activities", []):
                activity_id = activity.get("id")
                if activity_id in ACTIVITY_TYPES:
                    # Update name and icon to current config
                    activity["name"] = ACTIVITY_TYPES[activity_id]["name"]
                    activity["icon"] = ACTIVITY_TYPES[activity_id]["icon"]
                    # Update instrument-specific icons if applicable
                    if activity_id == "music_instrument" and child in INSTRUMENT_ICONS:
                        activity["icon"] = INSTRUMENT_ICONS[child]

    def _get_default_activities(self) -> list[dict[str, Any]]:
        """Get default activity list."""
        activities = []
        for activity_id, activity_config in ACTIVITY_TYPES.items():
            activities.append({
                "id": activity_id,
                "name": activity_config["name"],
                "icon": activity_config["icon"],
                "completed": False,
                "completed_at": None,
                "camera_required": activity_config.get("camera_required", False),
                "nfc_required": activity_config.get("nfc_required", False),
                "source": "default",
            })
        return activities

    async def _save_data(self) -> None:
        """Persist state to storage."""
        await self.store.async_save(self.data)
        _LOGGER.debug("Saved morning routine data to storage")

    async def _setup_listeners(self) -> None:
        """Set up NFC tag and time-based listeners."""
        # NFC tag listener
        self._nfc_listeners.append(
            self.hass.bus.async_listen("tag_scanned", self._handle_nfc_tag)
        )
        _LOGGER.info("Set up NFC tag listener")

        # Daily reset listener
        reset_time_str = self.config_entry.data.get(CONF_RESET_TIME, "06:00:00")
        hour, minute, second = reset_time_str.split(":")
        self._reset_listener = async_track_time_change(
            self.hass,
            self._scheduled_reset,
            hour=int(hour),
            minute=int(minute),
            second=int(second),
        )
        _LOGGER.info(f"Set up daily reset listener for {reset_time_str}")

    async def _handle_nfc_tag(self, event: Event) -> None:
        """Handle NFC tag scanned event."""
        tag_id = event.data.get("tag_id")
        device_id = event.data.get("device_id")

        _LOGGER.info(f"🏷️ NFC tag scanned: {tag_id} from device {device_id}")

        # Check if we're waiting to map a new tag
        if self._waiting_for_tag:
            child = self._waiting_for_tag["child"]
            activity = self._waiting_for_tag["activity"]

            _LOGGER.info(f"📌 Mapping tag {tag_id} to {child}/{activity}")

            # Cancel timeout
            if self._waiting_for_tag.get("timeout_handle"):
                self._waiting_for_tag["timeout_handle"]()

            # Add mapping to config entry
            mappings = dict(self.config_entry.data.get(CONF_NFC_MAPPINGS, {}))
            mappings[tag_id] = {"child": child, "activity": activity}

            # Update config entry
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_NFC_MAPPINGS: mappings}
            )

            _LOGGER.info(f"✅ Mapped NFC tag {tag_id} to {child}/{activity}")
            persistent_notification.async_create(self.hass,
                f"✅ Tag NFC mapeada com sucesso!\n\nTag: {tag_id}\nCriança: {child.capitalize()}\nAtividade: {ACTIVITY_TYPES.get(activity, {}).get('name', activity)}",
                title="Tag NFC Mapeada",
                notification_id=f"{DOMAIN}_nfc_mapped"
            )

            self._waiting_for_tag = None
            return

        # Look up existing mapping
        mappings = self.config_entry.data.get(CONF_NFC_MAPPINGS, {})
        mapping = mappings.get(tag_id)

        _LOGGER.info(f"📋 Current mappings: {mappings}")
        _LOGGER.info(f"🔍 Looking for tag: {tag_id}, found: {mapping}")

        if not mapping:
            _LOGGER.warning(f"❌ Unknown NFC tag scanned: {tag_id}")
            persistent_notification.async_create(self.hass,
                f"Tag NFC desconhecida: {tag_id}\n\nUsa o serviço 'add_nfc_mapping' para configurar esta tag.",
                title="Tag NFC Desconhecida",
                notification_id=f"{DOMAIN}_unknown_tag"
            )
            return

        child = mapping["child"]
        activity = mapping["activity"]

        _LOGGER.info(f"✅ NFC tag {tag_id} → {child}/{activity}, a completar atividade...")

        # Complete activity
        await self.complete_activity(child, activity, device_id=device_id)

        _LOGGER.info(f"🎉 Atividade {activity} completa para {child}!")

    async def _scheduled_reset(self, now: datetime) -> None:
        """Handle scheduled reset."""
        _LOGGER.info("Scheduled reset triggered")
        await self.reset_routine(None)

    def _should_reset(self) -> bool:
        """Check if reset should occur."""
        business_days_only = self.config_entry.data.get(CONF_BUSINESS_DAYS_ONLY, True)

        # Check if today is a business day
        if business_days_only:
            today = dt_util.now().weekday()
            if today >= 5:  # Saturday (5) or Sunday (6)
                return False

        # Check if we haven't reset today
        for child in CHILDREN:
            last_reset = self.data[child].get("last_reset")
            if last_reset:
                last_reset_date = dt_util.parse_datetime(last_reset).date()
                if last_reset_date >= dt_util.now().date():
                    return False

        return True

    def _is_child_complete(self, child: str) -> bool:
        """Check if all activities are complete for a child."""
        activities = self.data[child]["activities"]
        return all(activity["completed"] for activity in activities)

    def _calculate_progress(self, child: str) -> int:
        """Calculate completion percentage for a child."""
        activities = self.data[child]["activities"]
        if not activities:
            return 0
        completed = sum(1 for activity in activities if activity["completed"])
        return int((completed / len(activities)) * 100)

    async def complete_activity(
        self, child: str, activity_id: str, device_id: str | None = None, completed: bool = True
    ) -> None:
        """Mark an activity as complete or incomplete (toggle)."""
        if child not in CHILDREN:
            _LOGGER.error(f"Unknown child: {child}")
            return

        activities = self.data[child]["activities"]

        # Find and update activity
        activity_found = False
        for activity in activities:
            if activity["id"] == activity_id:
                activity["completed"] = completed
                if completed:
                    activity["completed_at"] = dt_util.utcnow().isoformat()
                    if device_id:
                        activity["device_id"] = device_id
                    _LOGGER.info(f"Marked activity '{activity_id}' complete for {child}")
                else:
                    activity["completed_at"] = None
                    if "device_id" in activity:
                        del activity["device_id"]
                    _LOGGER.info(f"Marked activity '{activity_id}' incomplete for {child}")
                activity_found = True
                break

        if not activity_found:
            _LOGGER.error(f"Activity '{activity_id}' not found for {child}")
            return

        self.data[child]["last_activity_time"] = dt_util.utcnow().isoformat()
        await self._save_data()

        # Fire event
        self.hass.bus.async_fire(
            EVENT_ACTIVITY_COMPLETED,
            {
                "child": child,
                "activity": activity_id,
                "completed": completed,
                "progress": self._calculate_progress(child),
            },
        )

        # Check if all complete
        if completed and self._is_child_complete(child):
            _LOGGER.info(f"All activities complete for {child}!")
            await self.generate_reward(child)

        # Create a deep copy to force coordinator update detection
        self.async_set_updated_data(copy.deepcopy(self.data))

    async def save_photo(self, child: str, photo_data: str) -> None:
        """Save photo from camera capture."""
        if child not in CHILDREN:
            _LOGGER.error(f"Unknown child: {child}")
            return

        # Import here to avoid circular dependency
        from .image_handler import ImageHandler

        handler = ImageHandler(self.hass)
        photo_path = await handler.save_photo(child, photo_data)

        self.data[child]["photo_path"] = photo_path
        await self._save_data()

        _LOGGER.info(f"Saved photo for {child}: {photo_path}")
        self.async_set_updated_data(copy.deepcopy(self.data))

    async def save_audio(self, child: str, audio_data: str) -> None:
        """Save audio recording from breakfast activity."""
        if child not in CHILDREN:
            _LOGGER.error(f"Unknown child: {child}")
            return

        # Import here to avoid circular dependency
        from .image_handler import ImageHandler

        handler = ImageHandler(self.hass)
        audio_path = await handler.save_audio(child, audio_data)

        self.data[child]["audio_recording"] = audio_path
        await self._save_data()

        _LOGGER.info(f"Saved audio for {child}: {audio_path}")
        self.async_set_updated_data(copy.deepcopy(self.data))

    async def generate_reward(self, child: str) -> None:
        """Generate AI reward when child completes all activities."""
        if child not in CHILDREN:
            _LOGGER.error(f"Unknown child: {child}")
            return

        openai_enabled = self.config_entry.data.get(CONF_OPENAI_ENABLED, False)
        if not openai_enabled:
            _LOGGER.info(f"OpenAI not enabled, skipping reward generation for {child}")
            # Fire completion event without reward
            self.hass.bus.async_fire(
                EVENT_ROUTINE_COMPLETE,
                {
                    "child": child,
                    "reward_image": None,
                },
            )
            return

        _LOGGER.info(f"Generating AI reward for {child}")

        # Get photo path for context
        photo_path = self.data[child].get("photo_path")

        # Construct prompt
        prompt_template = self.config_entry.data.get(
            CONF_OPENAI_PROMPT, DEFAULT_OPENAI_PROMPT
        )
        prompt = prompt_template.format(child=child.capitalize())

        try:
            # Call OpenAI service
            response = await self.hass.services.async_call(
                "openai_conversation",
                "generate_image",
                {"prompt": prompt, "size": "1024x1024"},
                blocking=True,
                return_response=True,
            )

            image_url = response.get("url")
            if not image_url:
                _LOGGER.error("No image URL in OpenAI response")
                return

            # Download and store image
            from .image_handler import ImageHandler

            handler = ImageHandler(self.hass)
            local_path = await handler.download_reward_image(child, image_url)

            self.data[child]["reward_image"] = local_path
            await self._save_data()

            _LOGGER.info(f"Generated reward image for {child}: {local_path}")

            # Fire completion event
            self.hass.bus.async_fire(
                EVENT_ROUTINE_COMPLETE,
                {
                    "child": child,
                    "reward_image": local_path,
                },
            )

            self.async_set_updated_data(copy.deepcopy(self.data))

        except Exception as ex:
            _LOGGER.error(f"Failed to generate reward image for {child}: {ex}")

    async def reset_routine(self, child: str | None = None) -> None:
        """Reset morning routine for a child or all children."""
        children_to_reset = [child] if child and child in CHILDREN else CHILDREN

        for child_name in children_to_reset:
            # Reset all activities
            for activity in self.data[child_name]["activities"]:
                activity["completed"] = False
                activity["completed_at"] = None
                if "device_id" in activity:
                    del activity["device_id"]

            # Clear photo, audio, and reward
            self.data[child_name]["photo_path"] = None
            self.data[child_name]["audio_recording"] = None
            self.data[child_name]["reward_image"] = None
            self.data[child_name]["last_reset"] = dt_util.utcnow().isoformat()

            _LOGGER.info(f"Reset routine for {child_name}")

        await self._save_data()

        # Sync calendar to add today's activities
        await self._sync_calendar()

        # Fire reset event
        self.hass.bus.async_fire(
            EVENT_RESET,
            {
                "children": children_to_reset,
                "reset_time": dt_util.utcnow().isoformat(),
            },
        )

        self.async_set_updated_data(copy.deepcopy(self.data))

    async def _sync_calendar(self) -> None:
        """Sync activities from Google Calendar."""
        calendar_entity = self.config_entry.data.get(CONF_CALENDAR_ENTITY)

        if not calendar_entity:
            _LOGGER.debug("No calendar entity configured, skipping sync")
            return

        # Check if calendar entity exists
        if calendar_entity not in self.hass.states.async_entity_ids("calendar"):
            _LOGGER.warning(f"Calendar entity {calendar_entity} not found")
            return

        try:
            # Get today's date range
            start = dt_util.start_of_local_day()
            end = start + timedelta(days=1)

            _LOGGER.debug(f"Fetching calendar events from {start} to {end}")

            # Call calendar service to get events
            response = await self.hass.services.async_call(
                "calendar",
                "get_events",
                {
                    "entity_id": calendar_entity,
                    "start_date_time": start.isoformat(),
                    "end_date_time": end.isoformat(),
                },
                blocking=True,
                return_response=True,
            )

            # Response format: {entity_id: {"events": [...]}}
            events = response.get(calendar_entity, {}).get("events", [])
            _LOGGER.info(f"Found {len(events)} calendar events for today")

            # Parse events for each child
            for child in CHILDREN:
                child_events = self._parse_calendar_events(child, events)

                # Add calendar-based activities
                for activity_data in child_events:
                    # Check if this activity already exists
                    existing_ids = [a["id"] for a in self.data[child]["activities"]]

                    if activity_data["id"] not in existing_ids:
                        self.data[child]["activities"].append(activity_data)
                        _LOGGER.info(f"Added calendar activity for {child}: {activity_data['name']}")

            # Update last sync time
            for child in CHILDREN:
                self.data[child]["last_calendar_sync"] = dt_util.utcnow().isoformat()

            await self._save_data()

        except Exception as ex:
            _LOGGER.error(f"Failed to sync calendar: {ex}")

    def _parse_calendar_events(self, child: str, events: list) -> list[dict]:
        """Parse calendar events to extract activities for a child."""
        activities = []

        # Determine child prefix: D- for Duarte, L- for Leonor
        child_prefix = "D-" if child == "duarte" else "L-"

        for event in events:
            summary = event.get("summary", "")
            description = event.get("description", "")

            # Check if event starts with child's prefix (D- or L-)
            if not summary.startswith(child_prefix):
                continue

            # Remove prefix to get activity name
            activity_text = summary[2:].strip()  # Remove "D-" or "L-"

            _LOGGER.debug(f"Processing event for {child}: {activity_text}")

            # Determine activity based on Portuguese keywords
            activity_id = None
            activity_name = None
            icon = None

            activity_lower = activity_text.lower()

            # Portuguese keywords mapping
            keywords = {
                # Sports
                "natação": ("sports_bag", "Saco de Natação", "mdi:swim"),
                "taekwondo": ("sports_bag", "Saco de Taekwondo", "mdi:karate"),
                "karaté": ("sports_bag", "Saco de Karaté", "mdi:karate"),
                "karate": ("sports_bag", "Saco de Karaté", "mdi:karate"),
                "futebol": ("sports_bag", "Saco de Futebol", "mdi:soccer"),
                "basquetebol": ("sports_bag", "Saco de Basquetebol", "mdi:basketball"),
                "dança": ("sports_bag", "Saco de Dança", "mdi:dance-ballroom"),
                "ginástica": ("sports_bag", "Saco de Ginástica", "mdi:gymnastics"),
                "ed. física": ("sports_bag", "Saco de Ed. Física", "mdi:run"),
                "educação física": ("sports_bag", "Saco de Ed. Física", "mdi:run"),
                # Music (will be customized per child)
                "música": ("music_instrument", None, None),  # Will be set below
            }

            for keyword, (act_id, act_name, act_icon) in keywords.items():
                if keyword in activity_lower:
                    activity_id = act_id

                    # Special handling for music - use child-specific instrument
                    if act_id == "music_instrument":
                        if child == "duarte":
                            activity_name = "Trompete"
                            icon = INSTRUMENT_ICONS["duarte"]
                        elif child == "leonor":
                            activity_name = "Flauta Transversal"
                            icon = INSTRUMENT_ICONS["leonor"]
                    else:
                        activity_name = act_name
                        icon = act_icon
                    break

            # If no match, skip this event
            if not activity_id:
                _LOGGER.debug(f"No keyword match for event: {activity_text}")
                continue

            # Create activity with calendar- prefix to distinguish from defaults
            activities.append({
                "id": f"calendar_{activity_id}_{event.get('uid', summary)[:8]}",
                "name": activity_name,
                "icon": icon,
                "completed": False,
                "completed_at": None,
                "camera_required": False,
                "nfc_required": True,  # Calendar activities typically need NFC tags
                "source": "calendar",
                "event_summary": summary,
            })

        return activities

    async def add_nfc_mapping(self, child: str, activity: str, timeout: int = 30) -> None:
        """Start listening for next NFC tag scan to map to child/activity."""
        if child not in CHILDREN:
            _LOGGER.error(f"Unknown child: {child}")
            raise ValueError(f"Unknown child: {child}")

        if activity not in ACTIVITY_TYPES:
            _LOGGER.error(f"Unknown activity: {activity}")
            raise ValueError(f"Unknown activity: {activity}")

        # Cancel any existing wait
        if self._waiting_for_tag and self._waiting_for_tag.get("timeout_handle"):
            self._waiting_for_tag["timeout_handle"]()

        # Set up timeout
        async def timeout_handler(now):
            """Handle timeout."""
            if self._waiting_for_tag:
                _LOGGER.warning(f"NFC tag mapping timeout for {child}/{activity}")
                persistent_notification.async_create(self.hass,
                    f"⏱️ NFC tag mapping timed out.\n\nNo tag was scanned within {timeout} seconds.",
                    title="NFC Mapping Timeout",
                    notification_id=f"{DOMAIN}_nfc_timeout"
                )
                self._waiting_for_tag = None

        timeout_handle = async_call_later(self.hass, timeout, timeout_handler)

        self._waiting_for_tag = {
            "child": child,
            "activity": activity,
            "timeout_handle": timeout_handle,
        }

        _LOGGER.info(f"Waiting for NFC tag scan to map to {child}/{activity} (timeout: {timeout}s)")
        persistent_notification.async_create(self.hass,
            f"📱 Scan NFC tag now!\n\nChild: {child.capitalize()}\nActivity: {ACTIVITY_TYPES[activity]['name']}\n\nTimeout: {timeout} seconds",
            title="Waiting for NFC Tag",
            notification_id=f"{DOMAIN}_waiting_tag"
        )

    async def remove_nfc_mapping(self, tag_id: str) -> None:
        """Remove an NFC tag mapping."""
        mappings = dict(self.config_entry.data.get(CONF_NFC_MAPPINGS, {}))

        if tag_id not in mappings:
            _LOGGER.warning(f"Tag {tag_id} not found in mappings")
            raise ValueError(f"Tag {tag_id} not found")

        mapping = mappings.pop(tag_id)

        # Update config entry
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_NFC_MAPPINGS: mappings}
        )

        _LOGGER.info(f"Removed NFC tag mapping: {tag_id}")
        persistent_notification.async_create(self.hass,
            f"✅ NFC tag mapping removed!\n\nTag: {tag_id}\nWas mapped to: {mapping['child']}/{mapping['activity']}",
            title="NFC Mapping Removed",
            notification_id=f"{DOMAIN}_nfc_removed"
        )

    def list_nfc_mappings(self) -> dict:
        """List all NFC tag mappings."""
        mappings = self.config_entry.data.get(CONF_NFC_MAPPINGS, {})

        # Format for display
        formatted = {}
        for tag_id, mapping in mappings.items():
            child = mapping["child"]
            activity = mapping["activity"]
            activity_name = ACTIVITY_TYPES.get(activity, {}).get("name", activity)
            formatted[tag_id] = f"{child.capitalize()} - {activity_name}"

        _LOGGER.info(f"Listed {len(formatted)} NFC mappings")
        return {"mappings": formatted, "count": len(formatted)}

    async def async_shutdown(self) -> None:
        """Shutdown coordinator and cleanup listeners."""
        _LOGGER.info("Shutting down morning routine coordinator")

        # Remove NFC listeners
        for remove_listener in self._nfc_listeners:
            remove_listener()

        # Remove reset listener
        if self._reset_listener:
            self._reset_listener()

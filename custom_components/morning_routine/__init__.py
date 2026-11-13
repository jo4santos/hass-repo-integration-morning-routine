"""The Morning Routine Gamification integration."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, Event, ServiceCall
from homeassistant.exceptions import ServiceValidationError
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
    CHILDREN_CONFIG,
    CONF_CALENDAR_ENTITY,
    CONF_RESET_TIME,
    CONF_BUSINESS_DAYS_ONLY,
    CONF_NFC_MAPPINGS,
    CONF_REWARD_TYPE,
    CONF_OPENAI_ENABLED,
    CONF_OPENAI_CONFIG_ENTRY,
    CONF_OPENAI_PROMPT,
    CONF_YOUTUBE_PLAYLIST_ID,
    CONF_ANNOUNCEMENTS_ENABLED,
    CONF_MEDIA_PLAYER_ENTITY,
    CONF_WEATHER_ENTITY,
    CONF_SCHOOL_TIME,
    CONF_DAILY_PHRASE_ENABLED,
    CONF_DAILY_PHRASE_PROMPT,
    CONF_GDRIVE_ENABLED,
    CONF_GDRIVE_CLIENT_ID,
    CONF_GDRIVE_CLIENT_SECRET,
    CONF_GDRIVE_FOLDER_ID,
    CONF_AI_AGENT,
    CONF_AI_ANNOUNCEMENTS_ENABLED,
    CONF_AI_ANNOUNCEMENT_PROMPT,
    CONF_AI_TIMEOUT,
    CONF_ANNOUNCE_SCRIPT,
    DEFAULT_REWARD_TYPE,
    DEFAULT_OPENAI_ENABLED,
    DEFAULT_OPENAI_PROMPT,
    DEFAULT_YOUTUBE_PLAYLIST_ID,
    DEFAULT_ANNOUNCEMENTS_ENABLED,
    DEFAULT_SCHOOL_TIME,
    DEFAULT_DAILY_PHRASE_ENABLED,
    DEFAULT_DAILY_PHRASE_PROMPT,
    DEFAULT_GDRIVE_ENABLED,
    DEFAULT_AI_ANNOUNCEMENTS_ENABLED,
    DEFAULT_AI_ANNOUNCEMENT_PROMPT,
    DEFAULT_AI_TIMEOUT,
    DAILY_PHRASE_TIMEOUT,
    STORAGE_VERSION,
    STORAGE_KEY,
    EVENT_ACTIVITY_COMPLETED,
    EVENT_ROUTINE_COMPLETE,
    EVENT_RESET,
    PLATFORMS,
)
from .activities import (
    activity_index,
    build_fixed_activities,
    effective_child_config,
    match_calendar_activities,
    reconcile_activities,
    special_activity_names,
)
from .announce import (
    KIND_30_MINUTES,
    KIND_10_MINUTES,
    KIND_LEAVE_NOW,
    KIND_COMPLETION,
    KIND_TIME_REMAINING,
    KIND_TIME_WITH_WEATHER,
    KIND_TIME_WITH_ACTIVITIES,
    build_child_facts,
    build_facts,
    build_prompt,
    build_weather_info,
    choose_message,
    completion_fallback,
    extract_speech,
    sanitize_answer,
    special_activities_sentence,
    temperature_description,
    translate_weather_condition,
    weather_sentence,
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

SERVICE_TEST_ANNOUNCE_COMPLETION_SCHEMA = vol.Schema(
    {
        vol.Required("child"): cv.string,
    }
)

SERVICE_GET_HISTORY_SCHEMA = vol.Schema(
    {
        vol.Required("child"): cv.string,
    }
)

SERVICE_SYNC_GDRIVE_SCHEMA = vol.Schema(
    {
        vol.Optional("child"): cv.string,
    }
)

SERVICE_GDRIVE_AUTHORIZE_SCHEMA = vol.Schema({})

SERVICE_GDRIVE_COMPLETE_AUTH_SCHEMA = vol.Schema(
    {
        vol.Required("code"): cv.string,
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

    # Apply option changes (activities, announcement times) without a restart
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

    # Register services
    async def handle_complete_activity(call: ServiceCall) -> None:
        """Handle complete_activity service call."""
        child = call.data["child"]
        activity = call.data["activity"]
        completed = call.data["completed"]
        # Activity ids are configurable: validate against the child's configuration
        coordinator.validate_activity(child, activity)
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
        coordinator.validate_activity(child, activity)
        await coordinator.add_nfc_mapping(child, activity, timeout)

    async def handle_remove_nfc_mapping(call: ServiceCall) -> None:
        """Handle remove_nfc_mapping service call."""
        tag_id = call.data["tag_id"]
        await coordinator.remove_nfc_mapping(tag_id)

    async def handle_list_nfc_mappings(call: ServiceCall) -> dict:
        """Handle list_nfc_mappings service call."""
        return coordinator.list_nfc_mappings()

    async def handle_announce_time_remaining(call: ServiceCall) -> None:
        """Handle announce_time_remaining service call."""
        _LOGGER.info("Announcing time remaining")
        await coordinator._announce_time_remaining()

    async def handle_announce_time_with_weather(call: ServiceCall) -> None:
        """Handle announce_time_with_weather service call."""
        _LOGGER.info("Announcing time with weather")
        await coordinator._announce_time_with_weather()

    async def handle_announce_time_with_activities(call: ServiceCall) -> None:
        """Handle announce_time_with_activities service call."""
        _LOGGER.info("Announcing time with activities")
        await coordinator._announce_time_with_activities()

    async def handle_test_announce_completion(call: ServiceCall) -> None:
        """Handle test_announce_completion service call."""
        child = call.data["child"]
        if child not in CHILDREN:
            raise ServiceValidationError(
                f"Criança desconhecida: '{child}'. Valores válidos: {', '.join(CHILDREN)}"
            )
        _LOGGER.info(f"Testing completion announcement for {child}")
        await coordinator._announce_completion(child, force_test=True)

    async def handle_get_history(call: ServiceCall) -> dict:
        """Handle get_history service call."""
        child = call.data["child"]
        history = await coordinator.get_history(child)
        return {"history": history}

    async def handle_sync_gdrive(call: ServiceCall) -> None:
        """Handle sync_existing_files service call."""
        child = call.data.get("child")
        await coordinator.sync_existing_files_to_gdrive(child)

    async def handle_gdrive_authorize(call: ServiceCall) -> None:
        """Handle gdrive_get_auth_url service call."""
        await coordinator.get_gdrive_auth_url()

    async def handle_gdrive_complete_auth(call: ServiceCall) -> None:
        """Handle gdrive_complete_auth service call."""
        code = call.data["code"]
        await coordinator.complete_gdrive_auth(code)

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

    hass.services.async_register(
        DOMAIN,
        "announce_time_remaining",
        handle_announce_time_remaining,
    )

    hass.services.async_register(
        DOMAIN,
        "announce_time_with_weather",
        handle_announce_time_with_weather,
    )

    hass.services.async_register(
        DOMAIN,
        "announce_time_with_activities",
        handle_announce_time_with_activities,
    )

    hass.services.async_register(
        DOMAIN,
        "test_announce_completion",
        handle_test_announce_completion,
        schema=SERVICE_TEST_ANNOUNCE_COMPLETION_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        "get_history",
        handle_get_history,
        schema=SERVICE_GET_HISTORY_SCHEMA,
        supports_response=True,
    )

    hass.services.async_register(
        DOMAIN,
        "sync_existing_files",
        handle_sync_gdrive,
        schema=SERVICE_SYNC_GDRIVE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        "gdrive_get_auth_url",
        handle_gdrive_authorize,
        schema=SERVICE_GDRIVE_AUTHORIZE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        "gdrive_complete_auth",
        handle_gdrive_complete_auth,
        schema=SERVICE_GDRIVE_COMPLETE_AUTH_SCHEMA,
    )

    return True


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle a config entry update (options saved or NFC mappings changed)."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is not None:
        await coordinator.async_options_updated()


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
        self._announcement_listeners = []  # Time-based announcement listeners
        self._waiting_for_tag = None  # {"child": str, "activity": str, "timeout_handle": callable}
        self._applied_activities_signature = None  # Activities config last applied to the state
        self.google_drive_uploader = None  # Will be initialized in async_config_entry_first_refresh

    def _get_config_value(self, key: str, default: Any = None) -> Any:
        """Get config value from options first, then data as fallback."""
        return self.config_entry.options.get(
            key,
            self.config_entry.data.get(key, default)
        )

    async def async_config_entry_first_refresh(self) -> None:
        """Handle first refresh - load data and setup listeners."""
        await self._load_data()
        await self._setup_listeners()
        await self._setup_google_drive()
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
            # Align stored activities with the current configuration (keeps progress)
            if self._reconcile_activities():
                await self._save_data()
            _LOGGER.info("Loaded stored morning routine data")
        else:
            # Initialize default structure
            self.data = {}
            for child in CHILDREN:
                self.data[child] = {
                    "activities": self._get_default_activities(child),
                    "last_activity_time": None,
                    "photo_path": None,
                    "audio_recording": None,
                    "reward_image": None,
                    "reward_video_id": None,
                    "daily_phrase": None,
                    "last_reset": None,
                    "last_calendar_sync": None,
                }
            await self._save_data()
            _LOGGER.info("Initialized new morning routine data")

        self._applied_activities_signature = self._activities_signature()

    # ------------------------------------------------------------------
    # Activities configuration (options → built-in defaults)
    # ------------------------------------------------------------------

    def get_child_activity_config(self, child: str) -> dict[str, list[dict[str, Any]]]:
        """Return the effective activity configuration of a child.

        Shape: {"fixed": [...], "calendar": [...]} - see activities.py.
        """
        return effective_child_config(self.config_entry.options, child, _LOGGER)

    def get_activity_ids(self, child: str) -> list[str]:
        """Return every activity id configured for a child (fixed + calendar)."""
        return list(activity_index(self.get_child_activity_config(child)))

    def get_activity_name(self, child: str, activity_id: str) -> str:
        """Return the display name of a child's activity (the id when unknown)."""
        return activity_index(self.get_child_activity_config(child)).get(activity_id, {}).get("name", activity_id)

    def validate_activity(self, child: str, activity_id: str) -> None:
        """Raise a clear service error for an unknown child or activity id."""
        if child not in CHILDREN:
            raise ServiceValidationError(
                f"Criança desconhecida: '{child}'. Valores válidos: {', '.join(CHILDREN)}"
            )
        valid_ids = self.get_activity_ids(child)
        if activity_id not in valid_ids:
            raise ServiceValidationError(
                f"Atividade desconhecida para {child.capitalize()}: '{activity_id}'. "
                f"Atividades configuradas: {', '.join(valid_ids)}"
            )

    def _activities_signature(self) -> str:
        """Return a fingerprint of the effective activities configuration."""
        return json.dumps(
            {child: self.get_child_activity_config(child) for child in CHILDREN},
            sort_keys=True,
        )

    def _reconcile_activities(self) -> bool:
        """Align today's stored activities with the configuration, keeping progress.

        Keeps completion state of activities that still exist, updates their
        name/icon/flags, adds new fixed activities and drops removed ones.
        """
        changed = False
        for child in CHILDREN:
            if child not in self.data:
                continue

            current = self.data[child].get("activities", [])
            updated = reconcile_activities(current, self.get_child_activity_config(child))
            if updated != current:
                self.data[child]["activities"] = updated
                changed = True
                _LOGGER.info(f"Reconciled activities for {child}: {[a['id'] for a in updated]}")
        return changed

    def _state_is_from_today(self) -> bool:
        """Check whether the stored routine was reset today (i.e. is today's routine)."""
        today = dt_util.now().date()
        for child in CHILDREN:
            last_reset = self.data.get(child, {}).get("last_reset")
            if not last_reset:
                continue
            parsed = dt_util.parse_datetime(last_reset)
            if parsed and dt_util.as_local(parsed).date() == today:
                return True
        return False

    async def async_options_updated(self) -> None:
        """Apply changed options without a restart (config entry update listener)."""
        await self._setup_announcement_listeners()

        signature = self._activities_signature()
        if signature == self._applied_activities_signature:
            return
        self._applied_activities_signature = signature

        _LOGGER.info("Activities configuration changed, reconciling today's activities")
        self._reconcile_activities()
        await self._save_data()

        # Add calendar activities whose (new) pattern matches one of today's events
        if self._state_is_from_today():
            await self._sync_calendar()

        self.async_set_updated_data(copy.deepcopy(self.data))

    def _get_default_activities(self, child: str | None = None) -> list[dict[str, Any]]:
        """Get the child's fixed activity list (always present), nothing completed."""
        return build_fixed_activities(self.get_child_activity_config(child))

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
        reset_time_str = self._get_config_value(CONF_RESET_TIME, "06:00:00")
        hour, minute, second = reset_time_str.split(":")
        self._reset_listener = async_track_time_change(
            self.hass,
            self._scheduled_reset,
            hour=int(hour),
            minute=int(minute),
            second=int(second),
        )
        _LOGGER.info(f"Set up daily reset listener for {reset_time_str}")

        # Announcement listeners
        await self._setup_announcement_listeners()

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
                f"✅ Tag NFC mapeada com sucesso!\n\nTag: {tag_id}\nCriança: {child.capitalize()}\nAtividade: {self.get_activity_name(child, activity)}",
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
            # Silently ignore unknown tags - they may be for other integrations
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

    async def _setup_announcement_listeners(self) -> None:
        """Set up time-based announcement listeners."""
        # Clear existing listeners
        for listener in self._announcement_listeners:
            listener()
        self._announcement_listeners.clear()

        # Check if announcements are enabled
        announcements_enabled = self._get_config_value(CONF_ANNOUNCEMENTS_ENABLED, DEFAULT_ANNOUNCEMENTS_ENABLED)
        if not announcements_enabled:
            _LOGGER.info("Announcements disabled, skipping setup")
            return

        # Get school time
        school_time_str = self._get_config_value(CONF_SCHOOL_TIME, DEFAULT_SCHOOL_TIME)
        try:
            school_hour, school_minute, _ = school_time_str.split(":")
            school_hour = int(school_hour)
            school_minute = int(school_minute)
        except (ValueError, AttributeError):
            _LOGGER.error(f"Invalid school time format: {school_time_str}")
            return

        # Calculate announcement times
        # 30 minutes before
        time_30 = datetime.now().replace(hour=school_hour, minute=school_minute) - timedelta(minutes=30)
        # 10 minutes before
        time_10 = datetime.now().replace(hour=school_hour, minute=school_minute) - timedelta(minutes=10)

        # Setup 30 minute announcement
        listener_30 = async_track_time_change(
            self.hass,
            self._announce_30_minutes,
            hour=time_30.hour,
            minute=time_30.minute,
            second=0,
        )
        self._announcement_listeners.append(listener_30)
        _LOGGER.info(f"Set up 30-minute announcement for {time_30.hour:02d}:{time_30.minute:02d}")

        # Setup 10 minute announcement
        listener_10 = async_track_time_change(
            self.hass,
            self._announce_10_minutes,
            hour=time_10.hour,
            minute=time_10.minute,
            second=0,
        )
        self._announcement_listeners.append(listener_10)
        _LOGGER.info(f"Set up 10-minute announcement for {time_10.hour:02d}:{time_10.minute:02d}")

        # Setup school time announcement
        listener_time = async_track_time_change(
            self.hass,
            self._announce_school_time,
            hour=school_hour,
            minute=school_minute,
            second=0,
        )
        self._announcement_listeners.append(listener_time)
        _LOGGER.info(f"Set up school time announcement for {school_hour:02d}:{school_minute:02d}")

    async def _setup_google_drive(self) -> None:
        """Set up Google Drive uploader if enabled."""
        gdrive_enabled = self._get_config_value(CONF_GDRIVE_ENABLED, DEFAULT_GDRIVE_ENABLED)

        if not gdrive_enabled:
            _LOGGER.debug("Google Drive upload disabled")
            return

        client_id = self._get_config_value(CONF_GDRIVE_CLIENT_ID)
        client_secret = self._get_config_value(CONF_GDRIVE_CLIENT_SECRET)
        folder_id = self._get_config_value(CONF_GDRIVE_FOLDER_ID)

        if not client_id or not client_secret:
            _LOGGER.warning("Google Drive enabled but credentials not configured")
            return

        try:
            # Import in executor to avoid blocking I/O operations
            def _import_google_drive():
                from .google_drive_uploader import GoogleDriveUploader
                return GoogleDriveUploader

            GoogleDriveUploader = await self.hass.async_add_executor_job(_import_google_drive)

            self.google_drive_uploader = GoogleDriveUploader(self.hass)
            await self.google_drive_uploader.setup(
                client_id=client_id,
                client_secret=client_secret,
                folder_id=folder_id,
            )
            _LOGGER.info("Google Drive uploader initialized")

            if self.google_drive_uploader.is_enabled:
                _LOGGER.info("Google Drive ready — scheduling background sync of existing files")
                self.hass.async_create_task(self.sync_existing_files_to_gdrive())
        except Exception as ex:
            _LOGGER.error(f"Failed to initialize Google Drive uploader: {ex}")
            self.google_drive_uploader = None

    # ------------------------------------------------------------------
    # Announcements
    # ------------------------------------------------------------------

    def _is_announcement_day(self, now: datetime, label: str) -> bool:
        """Return False on weekends when the routine only runs on business days."""
        business_days_only = self._get_config_value(CONF_BUSINESS_DAYS_ONLY, True)
        if business_days_only and now.weekday() >= 5:
            _LOGGER.debug(f"Skipping {label} announcement (weekend)")
            return False
        return True

    def _can_announce(self) -> bool:
        """Check that there is somewhere to announce (house script or media player)."""
        if self._get_config_value(CONF_ANNOUNCE_SCRIPT) or self._get_config_value(CONF_MEDIA_PLAYER_ENTITY):
            return True
        _LOGGER.warning("No media player or announcement script configured for announcements")
        return False

    async def _get_weather_info(self) -> dict | None:
        """Return today's weather summary for announcements (None if unavailable)."""
        weather_entity = self._get_config_value(CONF_WEATHER_ENTITY)
        if not weather_entity:
            return None

        weather_state = self.hass.states.get(weather_entity)
        if not weather_state:
            return None

        condition = self._translate_weather_condition(weather_state.state)

        # Use max temperature from today's daily forecast via service call
        forecast = await self._get_daily_forecast(weather_entity)
        temperature = (
            forecast.get('temperature') if forecast
            else weather_state.attributes.get('temperature')
        )
        precipitation = forecast.get('precipitation', 0) if forecast else None

        return build_weather_info(condition, temperature, bool(forecast), precipitation)

    def _child_grammar(self, child: str) -> dict:
        """Return name/gender/article configuration of a child."""
        return {"name": child.capitalize(), **CHILDREN_CONFIG.get(child, {})}

    def _children_facts(self) -> list[dict]:
        """Return today's routine facts of every child (for AI announcements)."""
        return [
            build_child_facts(self._child_grammar(child), self.data[child]["activities"])
            for child in CHILDREN
            if child in self.data
        ]

    async def _compose_announcement(
        self,
        kind: str,
        fallback: str,
        *,
        minutes: int | None = None,
        weather: dict | None = None,
        completion: dict | None = None,
    ) -> str:
        """Return the text to announce: written by the AI agent, or the fixed fallback.

        Any problem (disabled, no agent, timeout, error, empty or overlong answer)
        results in the fixed fallback text, so an announcement is never lost.
        """
        if not self._get_config_value(CONF_AI_ANNOUNCEMENTS_ENABLED, DEFAULT_AI_ANNOUNCEMENTS_ENABLED):
            _LOGGER.debug(f"Announcement '{kind}': AI announcements disabled, using fixed text")
            return fallback

        agent_id = self._get_config_value(CONF_AI_AGENT)
        if not agent_id:
            _LOGGER.warning(f"Announcement '{kind}': AI announcements enabled but no agent selected, using fixed text")
            return fallback

        try:
            timeout = float(self._get_config_value(CONF_AI_TIMEOUT, DEFAULT_AI_TIMEOUT))
        except (TypeError, ValueError):
            timeout = DEFAULT_AI_TIMEOUT
        timeout = min(max(timeout, 1), 60)

        try:
            facts = build_facts(
                kind,
                now=dt_util.now(),
                school_time=self._get_config_value(CONF_SCHOOL_TIME, DEFAULT_SCHOOL_TIME),
                minutes_to_school=minutes,
                children=self._children_facts(),
                weather=weather,
                completion=completion,
            )
            instructions = (
                self._get_config_value(CONF_AI_ANNOUNCEMENT_PROMPT)
                or DEFAULT_AI_ANNOUNCEMENT_PROMPT
            )
            prompt = build_prompt(instructions, facts)

            # Fresh conversation every time (no conversation_id)
            async with asyncio.timeout(timeout):
                response = await self.hass.services.async_call(
                    "conversation",
                    "process",
                    {
                        "agent_id": agent_id,
                        "text": prompt,
                    },
                    blocking=True,
                    return_response=True,
                )
        except TimeoutError:
            _LOGGER.warning(f"Announcement '{kind}': AI agent did not answer in {timeout:g}s, using fixed text")
            return fallback
        except Exception as ex:
            _LOGGER.warning(f"Announcement '{kind}': AI agent failed ({ex}), using fixed text")
            return fallback

        answer = extract_speech(response)
        if answer is None:
            _LOGGER.warning(f"Announcement '{kind}': AI agent returned an error or no text, using fixed text")
            return fallback

        message, path, reason = choose_message(answer, fallback)
        if path == "ai":
            _LOGGER.info(f"Announcement '{kind}': using AI generated text")
        else:
            _LOGGER.warning(f"Announcement '{kind}': AI answer rejected ({reason}), using fixed text")
        return message

    async def _deliver_announcement(self, message: str) -> bool:
        """Say a message: through the house announcement script, else tts.speak."""
        script_entity = self._get_config_value(CONF_ANNOUNCE_SCRIPT)
        if script_entity:
            script_state = self.hass.states.get(script_entity)
            if script_state is None or script_state.state == "unavailable":
                _LOGGER.warning(f"Announcement script {script_entity} not available, falling back to tts.speak")
            else:
                try:
                    # script.turn_on starts the script and does not wait for it to
                    # finish, so the routine is never held up by the announcement
                    await self.hass.services.async_call(
                        "script",
                        "turn_on",
                        {
                            "entity_id": script_entity,
                            "variables": {"message": message},
                        },
                        blocking=False,
                    )
                    _LOGGER.debug(f"Announcement sent to {script_entity}")
                    return True
                except Exception as ex:
                    _LOGGER.warning(f"Announcement script {script_entity} failed ({ex}), falling back to tts.speak")

        media_player = self._get_config_value(CONF_MEDIA_PLAYER_ENTITY)
        if not media_player:
            _LOGGER.warning("No media player configured for announcements")
            return False

        await self.hass.services.async_call(
            "tts",
            "speak",
            {
                "entity_id": "tts.home_assistant_cloud",
                "media_player_entity_id": media_player,
                "message": message,
                "cache": False,
            },
        )
        return True

    async def _announce(
        self,
        kind: str,
        fallback: str,
        *,
        minutes: int | None = None,
        weather: dict | None = None,
        completion: dict | None = None,
    ) -> None:
        """Compose (AI or fixed text) and deliver one announcement."""
        message = await self._compose_announcement(
            kind, fallback, minutes=minutes, weather=weather, completion=completion
        )
        if await self._deliver_announcement(message):
            _LOGGER.info(f"Announced ({kind}): {message}")

    async def _announce_30_minutes(self, now: datetime, force_test: bool = False) -> None:
        """Announce 30 minutes before school with weather forecast."""
        # Check if it's a business day (skip check if force_test)
        if not force_test and not self._is_announcement_day(now, "30-minute"):
            return

        if not self._can_announce():
            return

        weather = await self._get_weather_info()
        fallback = f"Bom dia! Faltam 30 minutos para ir para a escola.{weather_sentence(weather)}"

        await self._announce(KIND_30_MINUTES, fallback, minutes=30, weather=weather)

    async def _announce_10_minutes(self, now: datetime, force_test: bool = False) -> None:
        """Announce 10 minutes before school."""
        # Check if it's a business day (skip check if force_test)
        if not force_test and not self._is_announcement_day(now, "10-minute"):
            return

        if not self._can_announce():
            return

        fallback = "Faltam 10 minutos para ir para a escola. Vamos terminando de nos preparar!"

        await self._announce(KIND_10_MINUTES, fallback, minutes=10)

    async def _announce_school_time(self, now: datetime, force_test: bool = False) -> None:
        """Announce when it's time to go to school."""
        # Check if it's a business day (skip check if force_test)
        if not force_test and not self._is_announcement_day(now, "school time"):
            return

        if not self._can_announce():
            return

        fallback = "Está na hora de ir para a escola! Vamos lá!"

        await self._announce(KIND_LEAVE_NOW, fallback, minutes=0)

    async def _announce_completion(self, child: str, force_test: bool = False) -> None:
        """Announce when a child completes all activities (with the daily phrase, if any)."""
        # Check if announcements are enabled (skip if force_test)
        if not force_test:
            announcements_enabled = self._get_config_value(CONF_ANNOUNCEMENTS_ENABLED, DEFAULT_ANNOUNCEMENTS_ENABLED)
            daily_phrase_enabled = self._get_config_value(CONF_DAILY_PHRASE_ENABLED, DEFAULT_DAILY_PHRASE_ENABLED)
            ai_enabled = self._get_config_value(CONF_AI_ANNOUNCEMENTS_ENABLED, DEFAULT_AI_ANNOUNCEMENTS_ENABLED)

            if not announcements_enabled:
                _LOGGER.warning("Announcements are not enabled")
                return

            if not daily_phrase_enabled and not ai_enabled:
                _LOGGER.warning("Daily phrases and AI announcements are not enabled, skipping completion announcement")
                return

        if not self._can_announce():
            return

        # The daily phrase is optional: without one the completion is still announced
        daily_phrase = None
        if force_test or self._get_config_value(CONF_DAILY_PHRASE_ENABLED, DEFAULT_DAILY_PHRASE_ENABLED):
            daily_phrase = self.data[child].get("daily_phrase")
        if not daily_phrase:
            _LOGGER.info(f"No daily phrase available for {child}, announcing completion without it")

        # Build completion message with correct gender
        child_config = self._child_grammar(child)
        fallback = completion_fallback(child_config, daily_phrase)

        await self._announce(
            KIND_COMPLETION,
            fallback,
            completion={"child": child_config["name"], "daily_phrase": daily_phrase},
        )

    def _calculate_minutes_to_school(self) -> int:
        """Calculate minutes remaining until school time."""
        school_time_str = self._get_config_value(CONF_SCHOOL_TIME, DEFAULT_SCHOOL_TIME)
        try:
            school_hour, school_minute, _ = school_time_str.split(":")
            school_hour = int(school_hour)
            school_minute = int(school_minute)
        except (ValueError, AttributeError):
            _LOGGER.error(f"Invalid school time format: {school_time_str}")
            return 0

        now = dt_util.now()
        school_time = now.replace(hour=school_hour, minute=school_minute, second=0, microsecond=0)

        # If school time has passed, return 0
        if school_time <= now:
            return 0

        time_diff = school_time - now
        minutes = int(time_diff.total_seconds() / 60)
        return minutes

    def _get_special_activities(self) -> dict:
        """Get today's special activities for each child (the calendar-based ones)."""
        return {
            child: special_activity_names(self.data[child]["activities"])
            for child in CHILDREN
        }

    async def _get_daily_forecast(self, weather_entity: str) -> dict | None:
        """Get today's daily forecast using the weather.get_forecasts service.

        Returns the first daily forecast entry (today), or None if unavailable.
        Falls back to hourly if daily is not supported.
        """
        for forecast_type in ("daily", "hourly"):
            try:
                response = await self.hass.services.async_call(
                    "weather",
                    "get_forecasts",
                    {"entity_id": weather_entity, "type": forecast_type},
                    blocking=True,
                    return_response=True,
                )
                forecast_list = response.get(weather_entity, {}).get("forecast", [])
                if forecast_list:
                    return forecast_list[0]
            except Exception:
                pass
        return None

    def _translate_weather_condition(self, condition: str) -> str:
        """Translate weather condition from English to Portuguese."""
        return translate_weather_condition(condition)

    def _get_temperature_description(self, temperature: float) -> str:
        """Get temperature description for Esmoriz, Portugal climate.

        Helps children choose appropriate clothing based on temperature.
        """
        return temperature_description(temperature)

    def _get_varied_announcement(self, minutes: int, announcement_type: str = "simple") -> str:
        """Get a varied announcement message from predefined templates."""
        import random

        if minutes == 0:
            messages = [
                "Está na hora de ir para a escola! Vamos lá!",
                "Chegou a hora! Vamos para a escola!",
                "É agora! Hora de sair para a escola!",
                "Está na hora! Vamos embora!",
                "Chegou o momento! Vamos à escola!",
            ]
        elif minutes == 1:
            messages = [
                "Falta apenas 1 minuto! Já estão prontos?",
                "Falta só 1 minuto para sair!",
                "Último minuto! Já está quase na hora!",
                "1 minuto! Vamos terminando de nos preparar!",
                "Falta 1 minuto! Está quase!",
            ]
        elif minutes <= 5:
            messages = [
                f"Faltam {minutes} minutos para a escola!",
                f"Temos {minutes} minutos. Vamos continuar!",
                f"{minutes} minutos até à escola! Já está quase!",
                f"Faltam apenas {minutes} minutos!",
                f"{minutes} minutos! Vamos terminando a rotina!",
            ]
        elif minutes <= 10:
            messages = [
                f"Bom dia! Faltam {minutes} minutos para a escola.",
                f"Olá! Faltam {minutes} minutos. Vamos lá preparar-nos!",
                f"Faltam {minutes} minutos! Hora de nos organizarmos!",
                f"Bom dia! Temos {minutes} minutos até à escola!",
                f"{minutes} minutos para sair! Vamos começar!",
            ]
        else:
            messages = [
                f"Bom dia! Faltam {minutes} minutos para ir para a escola.",
                f"Olá! Temos {minutes} minutos. Vamos começar a preparar-nos!",
                f"Bom dia! Faltam {minutes} minutos. Vamos começar a rotina!",
                f"{minutes} minutos até à escola! Vamos lá tratar de tudo!",
                f"Bom dia! Faltam {minutes} minutos. Tempo suficiente para tudo!",
            ]

        return random.choice(messages)

    async def _announce_time_remaining(self) -> None:
        """Announce only how many minutes remain until school time (Type 1)."""
        if not self._can_announce():
            return

        minutes = self._calculate_minutes_to_school()
        fallback = self._get_varied_announcement(minutes, "simple")

        await self._announce(KIND_TIME_REMAINING, fallback, minutes=minutes)

    async def _announce_time_with_weather(self) -> None:
        """Announce time remaining and weather forecast (Type 2)."""
        if not self._can_announce():
            return

        minutes = self._calculate_minutes_to_school()
        base_message = self._get_varied_announcement(minutes, "simple")

        # Get weather information
        weather = await self._get_weather_info()
        fallback = base_message + weather_sentence(weather)

        await self._announce(KIND_TIME_WITH_WEATHER, fallback, minutes=minutes, weather=weather)

    async def _announce_time_with_activities(self) -> None:
        """Announce time remaining, weather, and today's special activities (Type 3)."""
        if not self._can_announce():
            return

        minutes = self._calculate_minutes_to_school()
        base_message = self._get_varied_announcement(minutes, "simple")

        # Get weather information
        weather = await self._get_weather_info()
        weather_message = weather_sentence(weather, closing=False)

        # Get special activities
        special_activities = self._get_special_activities()
        activities_message = special_activities_sentence(
            [
                {
                    "name": self._child_grammar(child)["name"],
                    "article": self._child_grammar(child).get("article", "A"),
                    "special": special_activities.get(child, []),
                }
                for child in CHILDREN
            ]
        )

        fallback = base_message + weather_message + activities_message

        await self._announce(KIND_TIME_WITH_ACTIVITIES, fallback, minutes=minutes, weather=weather)

    def _should_reset(self) -> bool:
        """Check if reset should occur."""
        business_days_only = self._get_config_value(CONF_BUSINESS_DAYS_ONLY, True)

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
                now_iso = dt_util.utcnow().isoformat()
                activity["completed"] = completed
                activity["last_modified"] = now_iso  # Add timestamp to force change detection
                if completed:
                    activity["completed_at"] = now_iso
                    if device_id:
                        activity["device_id"] = device_id
                    _LOGGER.info(f"✅ Marked activity '{activity_id}' COMPLETE for {child} at {now_iso}")
                else:
                    activity["completed_at"] = None
                    if "device_id" in activity:
                        del activity["device_id"]
                    _LOGGER.info(f"⬜ Marked activity '{activity_id}' INCOMPLETE for {child} at {now_iso}")
                activity_found = True
                break

        if not activity_found:
            _LOGGER.error(f"Activity '{activity_id}' not found for {child}")
            return

        now_iso = dt_util.utcnow().isoformat()
        self.data[child]["last_activity_time"] = now_iso

        # Log current state before save
        completed_list = [a["id"] for a in activities if a.get("completed", False)]
        _LOGGER.info(f"📊 Current state for {child}: {len(completed_list)}/{len(activities)} complete: {completed_list}")

        await self._save_data()

        # Fire event
        self.hass.bus.async_fire(
            EVENT_ACTIVITY_COMPLETED,
            {
                "child": child,
                "activity": activity_id,
                "completed": completed,
                "progress": self._calculate_progress(child),
                "timestamp": now_iso,  # Add timestamp to event
            },
        )

        # Check if all complete
        if completed and self._is_child_complete(child):
            _LOGGER.info(f"All activities complete for {child}!")
            await self.generate_reward(child)
            # Announce in the background: composing the text (AI) may take a few
            # seconds and must not delay the state update below
            self.hass.async_create_task(self._announce_completion(child))

        # Create a deep copy to force coordinator update detection
        progress = self._calculate_progress(child)
        _LOGGER.info(f"🔄 Triggering state update for {child}, progress: {progress}%, timestamp: {now_iso}")
        self.async_set_updated_data(copy.deepcopy(self.data))
        _LOGGER.info(f"✅ State update sent to Home Assistant at {dt_util.utcnow().isoformat()}")

    async def save_photo(self, child: str, photo_data: str) -> None:
        """Save photo from camera capture."""
        if child not in CHILDREN:
            _LOGGER.error(f"Unknown child: {child}")
            return

        # Import here to avoid circular dependency
        from .image_handler import ImageHandler

        handler = ImageHandler(self.hass, self.google_drive_uploader)
        photo_path = await handler.save_photo(child, photo_data)

        self.data[child]["photo_path"] = photo_path
        await self._save_data()

        _LOGGER.info(f"Saved photo for {child}: {photo_path}")

        # Automatically complete the photo activity (camera_required, "dressed" by default)
        activities = self.data[child]["activities"]
        photo_activity = next(
            (a["id"] for a in activities if a.get("camera_required")),
            next((a["id"] for a in activities if a["id"] == "dressed"), None),
        )
        if photo_activity:
            _LOGGER.info(f"Auto-completing '{photo_activity}' activity for {child} after photo save")
            await self.complete_activity(child, photo_activity, completed=True)

        self.async_set_updated_data(copy.deepcopy(self.data))

    async def save_audio(self, child: str, audio_data: str) -> None:
        """Save audio recording from breakfast activity."""
        if child not in CHILDREN:
            _LOGGER.error(f"Unknown child: {child}")
            return

        # Import here to avoid circular dependency
        from .image_handler import ImageHandler

        handler = ImageHandler(self.hass, self.google_drive_uploader)
        audio_path = await handler.save_audio(child, audio_data)

        self.data[child]["audio_recording"] = audio_path
        await self._save_data()

        _LOGGER.info(f"Saved audio for {child}: {audio_path}")

        # Transcribe audio in background
        self.hass.async_create_task(self._transcribe_audio(audio_path))

        self.async_set_updated_data(copy.deepcopy(self.data))

    async def get_history(self, child: str) -> list[dict]:
        """Get historical photos and audios for a child.

        Args:
            child: Child name (duarte or leonor)

        Returns:
            List of dictionaries with date, photo, and audio paths
        """
        if child not in CHILDREN:
            _LOGGER.error(f"Unknown child: {child}")
            return []

        from .image_handler import ImageHandler

        handler = ImageHandler(self.hass)
        return handler.list_history(child)

    async def _get_random_youtube_video(self, playlist_id: str) -> str | None:
        """Get a random video ID from a YouTube playlist."""
        import random
        import re

        try:
            # Simple approach: use YouTube's RSS feed for the playlist
            import aiohttp

            url = f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        _LOGGER.error(f"Failed to fetch YouTube playlist: {response.status}")
                        return None

                    text = await response.text()
                    # Extract video IDs from RSS feed
                    video_ids = re.findall(r'<yt:videoId>([^<]+)</yt:videoId>', text)

                    if not video_ids:
                        _LOGGER.error("No videos found in playlist")
                        return None

                    # Return random video ID
                    return random.choice(video_ids)
        except Exception as ex:
            _LOGGER.error(f"Error fetching YouTube playlist: {ex}", exc_info=True)
            return None

    async def generate_reward(self, child: str, save_only: bool = False) -> None:
        """Generate reward when child completes all activities.

        Args:
            child: Child name
            save_only: If True, only generate and save reward without firing events
        """
        if child not in CHILDREN:
            _LOGGER.error(f"Unknown child: {child}")
            return

        reward_type = self._get_config_value(CONF_REWARD_TYPE, DEFAULT_REWARD_TYPE)
        mode = "pre-generating" if save_only else "generating"
        _LOGGER.info(f"🎁 {mode.capitalize()} reward for {child}, type: {reward_type}")

        # Handle YouTube video reward
        if reward_type == "youtube_video":
            playlist_id = self._get_config_value(CONF_YOUTUBE_PLAYLIST_ID, DEFAULT_YOUTUBE_PLAYLIST_ID)
            _LOGGER.info(f"📺 Fetching random video from playlist: {playlist_id}")

            video_id = await self._get_random_youtube_video(playlist_id)
            if video_id:
                self.data[child]["reward_video_id"] = video_id
                await self._save_data()
                _LOGGER.info(f"✅ Selected random video for {child}: {video_id}")

                if not save_only:
                    # Fire completion event with video
                    self.hass.bus.async_fire(
                        EVENT_ROUTINE_COMPLETE,
                        {
                            "child": child,
                            "reward_type": "youtube_video",
                            "reward_video_id": video_id,
                        },
                    )
                    self.async_set_updated_data(copy.deepcopy(self.data))
                return
            else:
                _LOGGER.error(f"Failed to get YouTube video, falling back to quote")
                reward_type = "quote"

        # Handle AI image reward
        if reward_type == "ai_image":
            openai_enabled = self._get_config_value(CONF_OPENAI_ENABLED, False)
            if not openai_enabled:
                _LOGGER.warning(f"AI image selected but OpenAI not enabled, falling back to quote")
                reward_type = "quote"

        if reward_type == "ai_image":
            _LOGGER.info(f"🎨 Generating AI image reward for {child}...")

            # Construct prompt with correct gender
            prompt_template = self._get_config_value(
                CONF_OPENAI_PROMPT, DEFAULT_OPENAI_PROMPT
            )
            child_config = CHILDREN_CONFIG.get(child, {})
            child_name_with_article = f"{child_config.get('article', 'a').lower()} {child_config.get('name', child.capitalize())}"
            prompt = prompt_template.format(child=child_name_with_article)
            _LOGGER.info(f"🎨 Using prompt: {prompt}")

            try:
                # Get configured OpenAI config entry
                openai_entry_id = self._get_config_value(CONF_OPENAI_CONFIG_ENTRY)

                if not openai_entry_id:
                    # Fallback to first available if not configured
                    openai_entries = self.hass.config_entries.async_entries("openai_conversation")
                    if not openai_entries:
                        _LOGGER.error("❌ No OpenAI Conversation integration found. Please configure it first.")
                        reward_type = "quote"
                    else:
                        openai_entry_id = openai_entries[0].entry_id
                        _LOGGER.warning(f"⚠️ No OpenAI config selected, using first available: {openai_entry_id}")
                else:
                    _LOGGER.info(f"🎨 Using configured OpenAI config entry: {openai_entry_id}")

                if openai_entry_id:
                    # Call OpenAI service
                    _LOGGER.info(f"🎨 Calling openai_conversation.generate_image service...")
                    response = await self.hass.services.async_call(
                        "openai_conversation",
                        "generate_image",
                        {
                            "config_entry": openai_entry_id,
                            "prompt": prompt,
                            "size": "1024x1024"
                        },
                        blocking=True,
                        return_response=True,
                    )
                    _LOGGER.info(f"🎨 OpenAI response received: {response}")

                    image_url = response.get("url")
                    if not image_url:
                        _LOGGER.error(f"❌ No image URL in OpenAI response: {response}")
                        reward_type = "quote"
                    else:
                        _LOGGER.info(f"🎨 Image URL: {image_url}")

                        # Download and store image
                        from .image_handler import ImageHandler

                        handler = ImageHandler(self.hass)
                        _LOGGER.info(f"🎨 Downloading reward image...")
                        local_path = await handler.download_reward_image(child, image_url)

                        self.data[child]["reward_image"] = local_path
                        await self._save_data()

                        _LOGGER.info(f"✅ Generated reward image for {child}: {local_path}")

                        if not save_only:
                            # Fire completion event
                            self.hass.bus.async_fire(
                                EVENT_ROUTINE_COMPLETE,
                                {
                                    "child": child,
                                    "reward_type": "ai_image",
                                    "reward_image": local_path,
                                },
                            )

                            self.async_set_updated_data(copy.deepcopy(self.data))
                        return

            except Exception as ex:
                _LOGGER.error(f"❌ Failed to generate reward image for {child}: {ex}", exc_info=True)
                reward_type = "quote"

        # Fallback to quote reward
        if reward_type == "quote":
            _LOGGER.info(f"💬 Using quote reward for {child}")
            if not save_only:
                self.hass.bus.async_fire(
                    EVENT_ROUTINE_COMPLETE,
                    {
                        "child": child,
                        "reward_type": "quote",
                    },
                )
            self.async_set_updated_data(copy.deepcopy(self.data))

    async def reset_routine(self, child: str | None = None) -> None:
        """Reset morning routine for a child or all children."""
        children_to_reset = [child] if child and child in CHILDREN else CHILDREN

        for child_name in children_to_reset:
            # IMPORTANT: Start fresh with only fixed activities
            # Remove ALL activities (including calendar ones from previous day)
            self.data[child_name]["activities"] = self._get_default_activities(child_name)

            # Clear photo, audio, and reward
            self.data[child_name]["photo_path"] = None
            self.data[child_name]["audio_recording"] = None
            self.data[child_name]["reward_image"] = None
            self.data[child_name]["reward_video_id"] = None
            self.data[child_name]["last_reset"] = dt_util.utcnow().isoformat()

            _LOGGER.info(f"🔄 Reset routine for {child_name} - starting with {len(self.data[child_name]['activities'])} fixed activities")

        await self._save_data()

        # Sync calendar to add today's activities (ONLY if events exist)
        await self._sync_calendar()

        # Pre-generate daily phrases and rewards for the day
        await self._generate_daily_content()

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
        calendar_entity = self._get_config_value(CONF_CALENDAR_ENTITY)

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
        """Parse calendar events to extract activities for a child using pattern matching."""
        config = self.get_child_activity_config(child)
        if not config.get("calendar"):
            _LOGGER.debug(f"No calendar activities configured for child: {child}")
            return []

        _LOGGER.debug(f"📅 Parsing {len(events)} calendar events for {child}")

        # fnmatch wildcards; first matching pattern wins, each activity added once
        summaries = [event.get("summary", "") or "" for event in events]
        activities = match_calendar_activities(config, summaries, dt_util.utcnow().isoformat())

        for activity in activities:
            _LOGGER.info(f"✅ Matched '{activity['event_summary']}' → {activity['name']}")
        _LOGGER.info(f"📋 Found {len(activities)} unique activities for {child} from calendar: {[a['name'] for a in activities]}")

        return activities

    async def _generate_daily_content(self) -> None:
        """Pre-generate daily phrases and rewards during reset."""
        _LOGGER.info("🎨 Pre-generating daily content (phrases and rewards)")

        # Generate daily phrases
        await self._generate_daily_phrases()

        # Pre-generate rewards if AI is enabled
        reward_type = self._get_config_value(CONF_REWARD_TYPE, DEFAULT_REWARD_TYPE)
        if reward_type == "ai_image":
            openai_enabled = self._get_config_value(CONF_OPENAI_ENABLED, DEFAULT_OPENAI_ENABLED)
            if openai_enabled:
                for child in CHILDREN:
                    try:
                        _LOGGER.info(f"Pre-generating AI reward for {child}")
                        await self.generate_reward(child, save_only=True)
                    except Exception as ex:
                        _LOGGER.error(f"Failed to pre-generate reward for {child}: {ex}")
        elif reward_type == "youtube_video":
            # Pre-fetch YouTube video IDs
            playlist_id = self._get_config_value(CONF_YOUTUBE_PLAYLIST_ID, DEFAULT_YOUTUBE_PLAYLIST_ID)
            for child in CHILDREN:
                try:
                    video_id = await self._get_random_youtube_video(playlist_id)
                    if video_id:
                        self.data[child]["reward_video_id"] = video_id
                        _LOGGER.info(f"Pre-selected YouTube video for {child}: {video_id}")
                except Exception as ex:
                    _LOGGER.error(f"Failed to pre-select video for {child}: {ex}")

        await self._save_data()
        _LOGGER.info("✅ Daily content pre-generated successfully")

    async def _generate_daily_phrases(self) -> None:
        """Generate daily inspirational phrases for all children using AI."""
        daily_phrase_enabled = self._get_config_value(CONF_DAILY_PHRASE_ENABLED, DEFAULT_DAILY_PHRASE_ENABLED)
        if not daily_phrase_enabled:
            _LOGGER.debug("Daily phrases disabled, skipping generation")
            return

        # Never reuse the phrase of a previous day
        for child in CHILDREN:
            self.data[child]["daily_phrase"] = None

        # Agent: the legacy OpenAI option if set, otherwise the AI agent of the announcements
        agent_id = self._get_config_value(CONF_OPENAI_CONFIG_ENTRY) or self._get_config_value(CONF_AI_AGENT)
        if not agent_id:
            _LOGGER.warning("No AI agent configured, cannot generate daily phrases")
            return

        prompt_template = self._get_config_value(CONF_DAILY_PHRASE_PROMPT, DEFAULT_DAILY_PHRASE_PROMPT)

        for child in CHILDREN:
            try:
                child_config = CHILDREN_CONFIG.get(child, {})
                child_name_with_article = f"{child_config.get('article', 'a').lower()} {child_config.get('name', child.capitalize())}"
                prompt = prompt_template.replace("{child}", child_name_with_article)

                _LOGGER.info(f"Generating daily phrase for {child} with AI agent")

                # Call the conversation agent (fresh conversation, with a timeout)
                async with asyncio.timeout(DAILY_PHRASE_TIMEOUT):
                    response = await self.hass.services.async_call(
                        "conversation",
                        "process",
                        {
                            "agent_id": agent_id,
                            "text": prompt,
                        },
                        blocking=True,
                        return_response=True,
                    )

                # Extract the generated text (None when the agent reports an error)
                phrase = sanitize_answer(extract_speech(response))

                if phrase:
                    self.data[child]["daily_phrase"] = phrase
                    _LOGGER.info(f"Generated phrase for {child}: {phrase}")
                else:
                    _LOGGER.warning(f"No phrase generated for {child}")

            except TimeoutError:
                _LOGGER.error(f"Timed out generating daily phrase for {child}")
            except Exception as ex:
                _LOGGER.error(f"Failed to generate daily phrase for {child}: {ex}")

    async def _transcribe_audio(self, audio_path: str) -> None:
        """Transcribe audio file using OpenAI Whisper.

        Args:
            audio_path: Full filesystem path to audio file
        """
        try:
            # Check if OpenAI is configured
            openai_config_entry_id = self._get_config_value(CONF_OPENAI_CONFIG_ENTRY)
            if not openai_config_entry_id:
                _LOGGER.warning("No OpenAI configuration, skipping audio transcription")
                return

            _LOGGER.info(f"🎤 Transcribing audio: {audio_path}")

            # Get the OpenAI config entry to access API key
            import aiohttp
            import aiofiles

            # Get API key from config entry
            openai_entry = self.hass.config_entries.async_get_entry(openai_config_entry_id)
            if not openai_entry or not openai_entry.data.get("api_key"):
                _LOGGER.error("OpenAI API key not found")
                return

            api_key = openai_entry.data.get("api_key")

            # Read audio file
            async with aiofiles.open(audio_path, "rb") as audio_file:
                audio_data = await audio_file.read()

            # Call OpenAI Whisper API
            async with aiohttp.ClientSession() as session:
                form = aiohttp.FormData()
                form.add_field("file", audio_data, filename="audio.webm", content_type="audio/webm")
                form.add_field("model", "whisper-1")
                form.add_field("language", "pt")  # Portuguese

                async with session.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    data=form
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        transcription = result.get("text", "")

                        if transcription:
                            # Save transcription to text file
                            txt_path = audio_path.replace(".webm", ".txt")
                            async with aiofiles.open(txt_path, "w", encoding="utf-8") as txt_file:
                                await txt_file.write(transcription)

                            _LOGGER.info(f"✅ Transcription saved: {transcription}")
                        else:
                            _LOGGER.warning("Empty transcription received")
                    else:
                        error_text = await response.text()
                        _LOGGER.error(f"Transcription failed: HTTP {response.status} - {error_text}")

        except Exception as ex:
            _LOGGER.error(f"Failed to transcribe audio: {ex}")

    async def add_nfc_mapping(self, child: str, activity: str, timeout: int = 30) -> None:
        """Start listening for next NFC tag scan to map to child/activity."""
        # Activity ids are configurable: check against the child's fixed + calendar activities
        self.validate_activity(child, activity)

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
            f"📱 Scan NFC tag now!\n\nChild: {child.capitalize()}\nActivity: {self.get_activity_name(child, activity)}\n\nTimeout: {timeout} seconds",
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
            activity_name = self.get_activity_name(child, activity)
            formatted[tag_id] = f"{child.capitalize()} - {activity_name}"

        _LOGGER.info(f"Listed {len(formatted)} NFC mappings")
        return {"mappings": formatted, "count": len(formatted)}

    async def get_gdrive_auth_url(self) -> None:
        """Generate Google Drive authorization URL and show in notification."""
        if not self.google_drive_uploader:
            persistent_notification.async_create(
                self.hass,
                "Google Drive não está configurado. Configure primeiro o Client ID e Secret nas opções da integração.",
                title="Google Drive Não Configurado",
                notification_id=f"{DOMAIN}_gdrive_not_configured"
            )
            return

        try:
            # Use Home Assistant external URL as redirect URI
            redirect_uri = "urn:ietf:wg:oauth:2.0:oob"

            auth_url = self.google_drive_uploader.get_authorization_url(redirect_uri)

            persistent_notification.async_create(
                self.hass,
                f"**Passo 1: Autorizar Acesso**\n\n"
                f"1. Abre este URL no browser:\n\n{auth_url}\n\n"
                f"2. Faz login na tua conta Google\n"
                f"3. Autoriza o acesso\n"
                f"4. Copia o código que aparece\n"
                f"5. Usa o serviço `morning_routine.gdrive_complete_auth` com o código\n\n"
                f"⚠️ **IMPORTANTE**: Este código expira em 10 minutos!",
                title="🔐 Autorização Google Drive",
                notification_id=f"{DOMAIN}_gdrive_auth_url"
            )

            _LOGGER.info(f"Google Drive authorization URL: {auth_url}")

        except Exception as ex:
            _LOGGER.error(f"Failed to generate auth URL: {ex}")
            persistent_notification.async_create(
                self.hass,
                f"Erro ao gerar URL de autorização: {ex}",
                title="Erro de Autorização",
                notification_id=f"{DOMAIN}_gdrive_auth_error"
            )

    async def complete_gdrive_auth(self, code: str) -> None:
        """Complete Google Drive authorization with code."""
        if not self.google_drive_uploader:
            persistent_notification.async_create(
                self.hass,
                "Google Drive não está configurado.",
                title="Erro",
                notification_id=f"{DOMAIN}_gdrive_error"
            )
            return

        try:
            redirect_uri = "urn:ietf:wg:oauth:2.0:oob"

            success = await self.google_drive_uploader.handle_authorization_callback(
                code, redirect_uri
            )

            if success:
                persistent_notification.async_create(
                    self.hass,
                    "✅ Autorização concluída com sucesso!\n\n"
                    "O Google Drive está agora configurado e pronto a usar.\n\n"
                    "Podes usar o serviço `morning_routine.sync_existing_files` para sincronizar ficheiros existentes.",
                    title="✅ Google Drive Autorizado",
                    notification_id=f"{DOMAIN}_gdrive_auth_success"
                )
                _LOGGER.info("Google Drive authorization completed successfully")
            else:
                persistent_notification.async_create(
                    self.hass,
                    "❌ Falha na autorização. Verifica se o código está correto e não expirou.",
                    title="Erro de Autorização",
                    notification_id=f"{DOMAIN}_gdrive_auth_failed"
                )

        except Exception as ex:
            _LOGGER.error(f"Failed to complete authorization: {ex}")
            persistent_notification.async_create(
                self.hass,
                f"Erro ao completar autorização: {ex}",
                title="Erro",
                notification_id=f"{DOMAIN}_gdrive_auth_error"
            )

    async def sync_existing_files_to_gdrive(self, child: str | None = None) -> None:
        """Sync existing local files to Google Drive.

        Args:
            child: Optional child name. If None, syncs all children.
        """
        if not self.google_drive_uploader or not self.google_drive_uploader.is_enabled:
            _LOGGER.warning("Google Drive uploader not enabled")
            persistent_notification.async_create(
                self.hass,
                "Google Drive não está ativado ou configurado.",
                title="Sincronização Impossível",
                notification_id=f"{DOMAIN}_sync_failed"
            )
            return

        from .image_handler import ImageHandler

        handler = ImageHandler(self.hass, self.google_drive_uploader)
        storage_path = handler.storage_path

        try:
            # Get list of files
            all_files = os.listdir(storage_path)
            children_to_sync = [child] if child else CHILDREN

            total_files = 0
            uploaded_files = 0
            failed_files = 0

            for child_name in children_to_sync:
                # Filter files for this child (exclude reward images)
                child_files = [
                    f for f in all_files
                    if f.startswith(f"{child_name}_") and not f.startswith(f"{child_name}_reward_")
                ]

                _LOGGER.info(f"Found {len(child_files)} files for {child_name}")
                total_files += len(child_files)

                for filename in child_files:
                    filepath = os.path.join(storage_path, filename)

                    try:
                        # Determine MIME type
                        ext = os.path.splitext(filename)[1].lower()
                        mime_types = {
                            ".jpg": "image/jpeg",
                            ".jpeg": "image/jpeg",
                            ".png": "image/png",
                            ".webm": "audio/webm",
                            ".mp3": "audio/mpeg",
                            ".wav": "audio/wav",
                            ".txt": "text/plain",
                        }
                        mime_type = mime_types.get(ext, "application/octet-stream")

                        # Upload file (filename will be converted to new format automatically)
                        _LOGGER.info(f"Uploading {filename} to {child_name.capitalize()}/")
                        file_id = await self.google_drive_uploader.upload_file(
                            filepath=filepath,
                            mime_type=mime_type,
                            child=child_name,
                        )

                        if file_id:
                            uploaded_files += 1
                            _LOGGER.info(f"Uploaded {filename}: {file_id}")
                        else:
                            failed_files += 1
                            _LOGGER.warning(f"Failed to upload {filename}")

                    except Exception as ex:
                        failed_files += 1
                        _LOGGER.error(f"Error uploading {filename}: {ex}")

            # Send notification with results
            message = f"Sincronização concluída!\n\n"
            message += f"Total: {total_files} ficheiros\n"
            message += f"✅ Enviados: {uploaded_files}\n"
            if failed_files > 0:
                message += f"❌ Falhados: {failed_files}"

            persistent_notification.async_create(
                self.hass,
                message,
                title="Sincronização Google Drive",
                notification_id=f"{DOMAIN}_sync_complete"
            )

            _LOGGER.info(f"Sync complete: {uploaded_files}/{total_files} uploaded, {failed_files} failed")

        except Exception as ex:
            _LOGGER.error(f"Failed to sync files: {ex}")
            persistent_notification.async_create(
                self.hass,
                f"Erro ao sincronizar ficheiros: {ex}",
                title="Erro na Sincronização",
                notification_id=f"{DOMAIN}_sync_error"
            )

    async def async_shutdown(self) -> None:
        """Shutdown coordinator and cleanup listeners."""
        _LOGGER.info("Shutting down morning routine coordinator")

        # Remove NFC listeners
        for remove_listener in self._nfc_listeners:
            remove_listener()

        # Remove reset listener
        if self._reset_listener:
            self._reset_listener()

        # Remove announcement listeners
        for remove_listener in self._announcement_listeners:
            remove_listener()
        self._announcement_listeners.clear()

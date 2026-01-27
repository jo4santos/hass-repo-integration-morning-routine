"""The Morning Routine Gamification integration."""
from __future__ import annotations

import copy
import fnmatch
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
    CHILDREN_CONFIG,
    FIXED_ACTIVITIES,
    CALENDAR_ACTIVITY_MAPPING,
    ACTIVITY_TYPES,
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
    DEFAULT_REWARD_TYPE,
    DEFAULT_OPENAI_PROMPT,
    DEFAULT_YOUTUBE_PLAYLIST_ID,
    DEFAULT_ANNOUNCEMENTS_ENABLED,
    DEFAULT_SCHOOL_TIME,
    DEFAULT_DAILY_PHRASE_ENABLED,
    DEFAULT_DAILY_PHRASE_PROMPT,
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
        _LOGGER.info(f"Testing completion announcement for {child}")
        await coordinator._announce_completion(child, force_test=True)

    async def handle_get_history(call: ServiceCall) -> dict:
        """Handle get_history service call."""
        child = call.data["child"]
        history = await coordinator.get_history(child)
        return {"history": history}

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
        self._announcement_listeners = []  # Time-based announcement listeners
        self._waiting_for_tag = None  # {"child": str, "activity": str, "timeout_handle": callable}

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
                    "reward_video_id": None,
                    "daily_phrase": None,
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

    def _get_default_activities(self) -> list[dict[str, Any]]:
        """Get fixed activity list (always present)."""
        activities = []
        for activity_config in FIXED_ACTIVITIES:
            activities.append({
                "id": activity_config["id"],
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

    async def _announce_30_minutes(self, now: datetime, force_test: bool = False) -> None:
        """Announce 30 minutes before school with weather forecast."""
        # Check if it's a business day (skip check if force_test)
        if not force_test:
            business_days_only = self._get_config_value(CONF_BUSINESS_DAYS_ONLY, True)
            if business_days_only and now.weekday() >= 5:
                _LOGGER.debug("Skipping 30-minute announcement (weekend)")
                return

        media_player = self._get_config_value(CONF_MEDIA_PLAYER_ENTITY)
        weather_entity = self._get_config_value(CONF_WEATHER_ENTITY)

        if not media_player:
            _LOGGER.warning("No media player configured for announcements")
            return

        # Build weather message
        weather_message = ""
        if weather_entity and self.hass.states.get(weather_entity):
            weather_state = self.hass.states.get(weather_entity)
            weather_translations = {
                'sunny': 'céu limpo',
                'clear-night': 'noite limpa',
                'partlycloudy': 'parcialmente nublado',
                'cloudy': 'nublado',
                'rainy': 'chuvoso',
                'pouring': 'chuva forte',
                'snowy': 'neve',
                'fog': 'nevoeiro',
                'windy': 'ventoso',
                'lightning': 'trovoada'
            }
            condition = weather_translations.get(weather_state.state, weather_state.state)
            temperature = weather_state.attributes.get('temperature', 'desconhecida')

            weather_message = f" A previsão para hoje é {condition}, com uma temperatura de {temperature} graus."

            # Check for rain
            forecast = weather_state.attributes.get('forecast', [])
            if forecast and len(forecast) > 0:
                precipitation = forecast[0].get('precipitation', 0)
                if precipitation and precipitation > 0:
                    weather_message += " Há possibilidade de chuva. Não se esqueçam do guarda-chuva!"
                else:
                    weather_message += " Tenham um ótimo dia!"

        message = f"Bom dia! Faltam 30 minutos para ir para a escola.{weather_message}"

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
        _LOGGER.info(f"Announced: {message}")

    async def _announce_10_minutes(self, now: datetime, force_test: bool = False) -> None:
        """Announce 10 minutes before school."""
        # Check if it's a business day (skip check if force_test)
        if not force_test:
            business_days_only = self._get_config_value(CONF_BUSINESS_DAYS_ONLY, True)
            if business_days_only and now.weekday() >= 5:
                _LOGGER.debug("Skipping 10-minute announcement (weekend)")
                return

        media_player = self._get_config_value(CONF_MEDIA_PLAYER_ENTITY)
        if not media_player:
            _LOGGER.warning("No media player configured for announcements")
            return

        message = "Atenção! Faltam apenas 10 minutos para ir para a escola. Vamos despachar!"

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
        _LOGGER.info(f"Announced: {message}")

    async def _announce_school_time(self, now: datetime, force_test: bool = False) -> None:
        """Announce when it's time to go to school."""
        # Check if it's a business day (skip check if force_test)
        if not force_test:
            business_days_only = self._get_config_value(CONF_BUSINESS_DAYS_ONLY, True)
            if business_days_only and now.weekday() >= 5:
                _LOGGER.debug("Skipping school time announcement (weekend)")
                return

        media_player = self._get_config_value(CONF_MEDIA_PLAYER_ENTITY)
        if not media_player:
            _LOGGER.warning("No media player configured for announcements")
            return

        message = "Está na hora de ir para a escola! Vamos lá, rápido!"

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
        _LOGGER.info(f"Announced: {message}")

    async def _announce_completion(self, child: str, force_test: bool = False) -> None:
        """Announce when a child completes all activities with daily phrase."""
        # Check if announcements and daily phrases are enabled (skip if force_test)
        if not force_test:
            announcements_enabled = self._get_config_value(CONF_ANNOUNCEMENTS_ENABLED, DEFAULT_ANNOUNCEMENTS_ENABLED)
            daily_phrase_enabled = self._get_config_value(CONF_DAILY_PHRASE_ENABLED, DEFAULT_DAILY_PHRASE_ENABLED)

            if not announcements_enabled:
                _LOGGER.warning("Announcements are not enabled")
                return

            if not daily_phrase_enabled:
                _LOGGER.warning("Daily phrases are not enabled")
                return

        media_player = self._get_config_value(CONF_MEDIA_PLAYER_ENTITY)
        if not media_player:
            _LOGGER.warning("No media player configured for announcements")
            return

        # Get the daily phrase for this child
        daily_phrase = self.data[child].get("daily_phrase")
        if not daily_phrase:
            if force_test:
                # For testing, use a default phrase
                daily_phrase = "És incrível! Continua assim!"
                _LOGGER.info(f"Using test phrase for {child} (no daily phrase generated yet)")
            else:
                _LOGGER.warning(f"No daily phrase available for {child}")
                return

        # Build completion message with correct gender
        child_config = CHILDREN_CONFIG.get(child, {})
        child_name = child_config.get("name", child.capitalize())
        article = child_config.get("article", "A")
        pronoun_ready = child_config.get("pronoun_ready", "pronta")

        message = f"Parabéns! {article} {child_name} está {pronoun_ready}! A frase do dia para {article} {child_name} é: {daily_phrase}"

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
        _LOGGER.info(f"Announced completion for {child}: {message}")

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
        """Get today's special activities for each child (excluding basic activities)."""
        # Basic activities to exclude
        basic_activities = ["dressed", "breakfast", "teeth", "schoolbag", "lunchbox"]

        special_activities = {
            "duarte": [],
            "leonor": []
        }

        for child in CHILDREN:
            activities = self.data[child]["activities"]
            for activity in activities:
                if activity["id"] not in basic_activities:
                    special_activities[child].append(activity["name"])

        return special_activities

    async def _generate_ai_announcement(self, prompt: str) -> str:
        """Generate AI announcement using conversation API."""
        try:
            response = await self.hass.services.async_call(
                "conversation",
                "process",
                {
                    "text": prompt,
                    "agent_id": "conversation.home_assistant_cloud",
                },
                blocking=True,
                return_response=True,
            )

            # Extract the response text
            message = response.get("response", {}).get("speech", {}).get("plain", {}).get("speech", "").strip()

            if not message:
                _LOGGER.warning("Empty response from conversation API")
                return None

            _LOGGER.info(f"Generated AI announcement: {message}")
            return message
        except Exception as ex:
            _LOGGER.error(f"Failed to generate AI announcement: {ex}")
            return None

    async def _announce_time_remaining(self) -> None:
        """Announce only how many minutes remain until school time (Type 1)."""
        media_player = self._get_config_value(CONF_MEDIA_PLAYER_ENTITY)
        if not media_player:
            _LOGGER.warning("No media player configured for announcements")
            return

        minutes = self._calculate_minutes_to_school()

        # Generate AI message
        if minutes == 0:
            prompt = "Gera uma mensagem curta e energética em português de Portugal para crianças dizendo que está na hora de ir para a escola. Varia a forma de dizer, pode usar expressões como 'Vamos lá', 'Despachem-se', etc. Máximo 2 frases."
        elif minutes == 1:
            prompt = "Gera uma mensagem curta e energética em português de Portugal para crianças dizendo que falta apenas 1 minuto para ir para a escola. Varia a forma de dizer. Máximo 2 frases."
        else:
            prompt = f"Gera uma mensagem curta e alegre em português de Portugal para crianças dizendo 'Bom dia' e que faltam {minutes} minutos para ir para a escola. Varia a forma de dizer, pode adicionar algo motivacional. Máximo 2 frases."

        message = await self._generate_ai_announcement(prompt)

        # Fallback if AI fails
        if not message:
            if minutes == 0:
                message = "Está na hora de ir para a escola! Vamos lá, rápido!"
            elif minutes == 1:
                message = "Falta apenas 1 minuto para ir para a escola!"
            else:
                message = f"Bom dia! Faltam {minutes} minutos para ir para a escola."

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
        _LOGGER.info(f"Announced time remaining: {message}")

    async def _announce_time_with_weather(self) -> None:
        """Announce time remaining and weather forecast (Type 2)."""
        media_player = self._get_config_value(CONF_MEDIA_PLAYER_ENTITY)
        if not media_player:
            _LOGGER.warning("No media player configured for announcements")
            return

        minutes = self._calculate_minutes_to_school()

        # Get weather information
        weather_entity = self._get_config_value(CONF_WEATHER_ENTITY)
        weather_info = ""
        has_rain = False

        if weather_entity:
            weather_state = self.hass.states.get(weather_entity)
            if weather_state:
                condition = weather_state.state
                temperature = weather_state.attributes.get('temperature', 'desconhecida')
                weather_info = f"tempo: {condition}, temperatura: {temperature} graus"

                # Check for rain
                forecast = weather_state.attributes.get('forecast', [])
                if forecast and len(forecast) > 0:
                    precipitation = forecast[0].get('precipitation', 0)
                    if precipitation and precipitation > 0:
                        has_rain = True

        # Generate AI message
        if minutes == 0:
            prompt = f"Gera uma mensagem curta em português de Portugal para crianças dizendo que está na hora de ir para a escola. Inclui a previsão do tempo: {weather_info}.{' Menciona que há possibilidade de chuva e para levarem guarda-chuva.' if has_rain else ' Deseja um bom dia.'} Varia a forma de dizer. Máximo 3 frases."
        elif minutes == 1:
            prompt = f"Gera uma mensagem curta em português de Portugal para crianças dizendo 'Bom dia' e que falta apenas 1 minuto para ir para a escola. Inclui a previsão: {weather_info}.{' Menciona que há possibilidade de chuva e para levarem guarda-chuva.' if has_rain else ' Deseja um bom dia.'} Varia a forma de dizer. Máximo 3 frases."
        else:
            prompt = f"Gera uma mensagem alegre em português de Portugal para crianças dizendo 'Bom dia' e que faltam {minutes} minutos para ir para a escola. Inclui a previsão: {weather_info}.{' Menciona que há possibilidade de chuva e para levarem guarda-chuva.' if has_rain else ' Deseja um bom dia.'} Varia a forma de dizer. Máximo 3 frases."

        message = await self._generate_ai_announcement(prompt)

        # Fallback if AI fails
        if not message:
            if minutes == 0:
                message = f"Está na hora de ir para a escola! {weather_info}."
            elif minutes == 1:
                message = f"Bom dia! Falta apenas 1 minuto para ir para a escola. {weather_info}."
            else:
                message = f"Bom dia! Faltam {minutes} minutos para ir para a escola. {weather_info}."

            if has_rain:
                message += " Não se esqueçam do guarda-chuva!"

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
        _LOGGER.info(f"Announced time with weather: {message}")

    async def _announce_time_with_activities(self) -> None:
        """Announce time remaining, weather, and today's special activities (Type 3)."""
        media_player = self._get_config_value(CONF_MEDIA_PLAYER_ENTITY)
        if not media_player:
            _LOGGER.warning("No media player configured for announcements")
            return

        minutes = self._calculate_minutes_to_school()

        # Get weather information
        weather_entity = self._get_config_value(CONF_WEATHER_ENTITY)
        weather_info = ""
        has_rain = False

        if weather_entity:
            weather_state = self.hass.states.get(weather_entity)
            if weather_state:
                condition = weather_state.state
                temperature = weather_state.attributes.get('temperature', 'desconhecida')
                weather_info = f"tempo: {condition}, temperatura: {temperature} graus"

                # Check for rain
                forecast = weather_state.attributes.get('forecast', [])
                if forecast and len(forecast) > 0:
                    precipitation = forecast[0].get('precipitation', 0)
                    if precipitation and precipitation > 0:
                        has_rain = True

        # Get special activities
        special_activities = self._get_special_activities()
        activities_info = []

        for child in CHILDREN:
            child_activities = special_activities[child]
            if child_activities:
                child_config = CHILDREN_CONFIG.get(child, {})
                child_name = child_config.get("name", child.capitalize())
                article = child_config.get("article", "A")
                activities_text = ", ".join(child_activities)
                activities_info.append(f"{article} {child_name}: {activities_text}")

        activities_text = "; ".join(activities_info) if activities_info else "sem atividades extra"

        # Generate AI message
        if minutes == 0:
            prompt = f"Gera uma mensagem em português de Portugal para crianças dizendo que está na hora de ir para a escola. Inclui: {weather_info}.{' Menciona possibilidade de chuva e guarda-chuva.' if has_rain else ''} Atividades especiais de hoje: {activities_text}. Varia a forma de dizer. Máximo 4 frases."
        elif minutes == 1:
            prompt = f"Gera uma mensagem em português de Portugal para crianças dizendo 'Bom dia' e que falta 1 minuto para a escola. Inclui: {weather_info}.{' Menciona possibilidade de chuva e guarda-chuva.' if has_rain else ''} Atividades especiais: {activities_text}. Varia a forma de dizer. Máximo 4 frases."
        else:
            prompt = f"Gera uma mensagem alegre em português de Portugal para crianças dizendo 'Bom dia' e que faltam {minutes} minutos para a escola. Inclui: {weather_info}.{' Menciona possibilidade de chuva e guarda-chuva.' if has_rain else ''} Atividades especiais: {activities_text}. Varia a forma de dizer. Máximo 4 frases."

        message = await self._generate_ai_announcement(prompt)

        # Fallback if AI fails
        if not message:
            if minutes == 0:
                message = f"Está na hora de ir para a escola! {weather_info}."
            elif minutes == 1:
                message = f"Bom dia! Falta apenas 1 minuto para ir para a escola. {weather_info}."
            else:
                message = f"Bom dia! Faltam {minutes} minutos para ir para a escola. {weather_info}."

            if has_rain:
                message += " Não se esqueçam do guarda-chuva!"

            if activities_info:
                message += " " + " ".join([f"{info.split(':')[0]} tem {info.split(':')[1]} hoje." for info in activities_info])

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
        _LOGGER.info(f"Announced time with activities: {message}")

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
            await self._announce_completion(child)

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
            self.data[child_name]["activities"] = self._get_default_activities()

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
        if child not in CALENDAR_ACTIVITY_MAPPING:
            _LOGGER.warning(f"No calendar mapping defined for child: {child}")
            return []

        # Get activity mappings for this child
        child_mappings = CALENDAR_ACTIVITY_MAPPING[child]

        # Track which activities have been matched (to avoid duplicates)
        matched_activities = {}  # activity_id -> activity_dict

        _LOGGER.debug(f"📅 Parsing {len(events)} calendar events for {child}")

        for event in events:
            summary = event.get("summary", "")

            # Try to match event summary against all patterns
            for mapping in child_mappings:
                pattern = mapping["pattern"]
                activity_config = mapping["activity"]

                # Use fnmatch for wildcard matching
                if fnmatch.fnmatch(summary, pattern):
                    activity_id = activity_config["id"]

                    # Only add if not already matched (prevents duplicate activities from multiple events)
                    if activity_id not in matched_activities:
                        _LOGGER.info(f"✅ Matched '{summary}' to pattern '{pattern}' → {activity_config['name']}")

                        matched_activities[activity_id] = {
                            "id": activity_id,
                            "name": activity_config["name"],
                            "icon": activity_config["icon"],
                            "completed": False,
                            "completed_at": None,
                            "camera_required": False,
                            "nfc_required": activity_config.get("nfc_required", True),
                            "source": "calendar",
                            "event_summary": summary,
                            "last_modified": dt_util.utcnow().isoformat(),
                        }
                    else:
                        _LOGGER.debug(f"⏭️  Skipping duplicate activity '{activity_id}' from event '{summary}'")

                    # Break after first match to prevent multiple patterns matching same event
                    break

        activities = list(matched_activities.values())
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

        openai_config_entry_id = self._get_config_value(CONF_OPENAI_CONFIG_ENTRY)
        if not openai_config_entry_id:
            _LOGGER.warning("No OpenAI configuration found, cannot generate daily phrases")
            return

        prompt_template = self._get_config_value(CONF_DAILY_PHRASE_PROMPT, DEFAULT_DAILY_PHRASE_PROMPT)

        for child in CHILDREN:
            try:
                child_config = CHILDREN_CONFIG.get(child, {})
                child_name_with_article = f"{child_config.get('article', 'a').lower()} {child_config.get('name', child.capitalize())}"
                prompt = prompt_template.replace("{child}", child_name_with_article)

                _LOGGER.info(f"Generating daily phrase for {child} with OpenAI")

                # Call OpenAI conversation service
                response = await self.hass.services.async_call(
                    "conversation",
                    "process",
                    {
                        "agent_id": openai_config_entry_id,
                        "text": prompt,
                    },
                    blocking=True,
                    return_response=True,
                )

                # Extract the generated text
                phrase = response.get("response", {}).get("speech", {}).get("plain", {}).get("speech", "")

                if phrase:
                    self.data[child]["daily_phrase"] = phrase
                    _LOGGER.info(f"Generated phrase for {child}: {phrase}")
                else:
                    _LOGGER.warning(f"No phrase generated for {child}")

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

        # Remove announcement listeners
        for remove_listener in self._announcement_listeners:
            remove_listener()
        self._announcement_listeners.clear()

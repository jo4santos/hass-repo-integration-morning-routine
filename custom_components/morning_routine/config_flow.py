"""Config flow for Morning Routine Gamification integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .google_drive_uploader import GoogleDriveUploader

from .const import (
    DOMAIN,
    CONF_CALENDAR_ENTITY,
    CONF_RESET_TIME,
    CONF_BUSINESS_DAYS_ONLY,
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
    DEFAULT_RESET_TIME,
    DEFAULT_BUSINESS_DAYS_ONLY,
    DEFAULT_REWARD_TYPE,
    DEFAULT_OPENAI_ENABLED,
    DEFAULT_OPENAI_PROMPT,
    DEFAULT_YOUTUBE_PLAYLIST_ID,
    DEFAULT_ANNOUNCEMENTS_ENABLED,
    DEFAULT_SCHOOL_TIME,
    DEFAULT_DAILY_PHRASE_ENABLED,
    DEFAULT_DAILY_PHRASE_PROMPT,
    DEFAULT_GDRIVE_ENABLED,
    CHILDREN,
    CHILDREN_CONFIG,
    CONF_AI_AGENT,
    CONF_AI_ANNOUNCEMENTS_ENABLED,
    CONF_AI_ANNOUNCEMENT_PROMPT,
    CONF_AI_TIMEOUT,
    CONF_ANNOUNCE_SCRIPT,
    DEFAULT_AI_ANNOUNCEMENTS_ENABLED,
    DEFAULT_AI_ANNOUNCEMENT_PROMPT,
    DEFAULT_AI_TIMEOUT,
)
from .activities import (
    ActivityConfigError,
    conf_activities_key,
    default_child_config,
    display_config,
    effective_child_config,
    is_empty_config,
    parse_child_config,
)

_LOGGER = logging.getLogger(__name__)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Morning Routine Gamification."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        # Check if already configured
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        errors = {}

        if user_input is not None:
            # Validate calendar entity exists
            calendar_entity = user_input.get(CONF_CALENDAR_ENTITY)
            if calendar_entity and calendar_entity not in self.hass.states.async_entity_ids("calendar"):
                errors["calendar_entity"] = "no_calendar"
            else:
                # Create entry with default values for NFC and OpenAI (to be configured later via options)
                from .const import CONF_NFC_MAPPINGS
                return self.async_create_entry(
                    title="Morning Routine",
                    data={
                        CONF_CALENDAR_ENTITY: calendar_entity,
                        CONF_RESET_TIME: user_input.get(CONF_RESET_TIME, DEFAULT_RESET_TIME),
                        CONF_BUSINESS_DAYS_ONLY: user_input.get(CONF_BUSINESS_DAYS_ONLY, DEFAULT_BUSINESS_DAYS_ONLY),
                        CONF_NFC_MAPPINGS: {},
                        CONF_OPENAI_ENABLED: DEFAULT_OPENAI_ENABLED,
                        CONF_OPENAI_PROMPT: DEFAULT_OPENAI_PROMPT,
                    },
                )

        # Get all calendar entities for selector
        calendar_entities = self.hass.states.async_entity_ids("calendar")

        data_schema = vol.Schema(
            {
                vol.Optional(CONF_CALENDAR_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="calendar")
                ),
                vol.Optional(CONF_RESET_TIME, default=DEFAULT_RESET_TIME): selector.TimeSelector(),
                vol.Optional(
                    CONF_BUSINESS_DAYS_ONLY, default=DEFAULT_BUSINESS_DAYS_ONLY
                ): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "docs_url": "https://github.com/jo4santos/hass-repo-integration-morning-routine"
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return OptionsFlowHandler()


def _optional(key: str, value: Any) -> vol.Optional:
    """Optional field pre-filled with the current value (can be cleared by the user)."""
    if value in (None, ""):
        return vol.Optional(key)
    return vol.Optional(key, description={"suggested_value": value})


def _conversation_agent_selector() -> Any:
    """Selector for a conversation agent (falls back to a conversation entity selector)."""
    if hasattr(selector, "ConversationAgentSelector"):
        return selector.ConversationAgentSelector()
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="conversation"))


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Morning Routine.

    The options are split in sections (menu). Each section only writes its own
    keys: everything else already stored in the options is kept untouched.
    """

    def __init__(self) -> None:
        """Initialize options flow."""
        self._gdrive_uploader: GoogleDriveUploader | None = None
        self._pending_options: dict | None = None

    def _current(self, key: str, default: Any = None) -> Any:
        """Get current value from options first, fallback to data."""
        return self.config_entry.options.get(
            key,
            self.config_entry.data.get(key, default)
        )

    def _merged_options(self, values: dict[str, Any]) -> dict[str, Any]:
        """Return the stored options updated with the values of one section."""
        return {**self.config_entry.options, **values}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show the options menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "activities", "announcements", "rewards", "gdrive"],
        )

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """General options: calendar and daily reset."""
        errors = {}

        if user_input is not None:
            # Validate calendar entity if changed
            calendar_entity = user_input.get(CONF_CALENDAR_ENTITY)
            if calendar_entity and calendar_entity not in self.hass.states.async_entity_ids("calendar"):
                errors["calendar_entity"] = "no_calendar"
            else:
                return self.async_create_entry(
                    title="",
                    data=self._merged_options(
                        {
                            CONF_CALENDAR_ENTITY: calendar_entity,
                            CONF_RESET_TIME: user_input.get(CONF_RESET_TIME, DEFAULT_RESET_TIME),
                            CONF_BUSINESS_DAYS_ONLY: user_input.get(CONF_BUSINESS_DAYS_ONLY, DEFAULT_BUSINESS_DAYS_ONLY),
                        }
                    ),
                )

        data_schema = vol.Schema(
            {
                _optional(
                    CONF_CALENDAR_ENTITY, self._current(CONF_CALENDAR_ENTITY)
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="calendar")
                ),
                vol.Optional(
                    CONF_RESET_TIME,
                    default=self._current(CONF_RESET_TIME, DEFAULT_RESET_TIME)
                ): selector.TimeSelector(),
                vol.Optional(
                    CONF_BUSINESS_DAYS_ONLY,
                    default=self._current(CONF_BUSINESS_DAYS_ONLY, DEFAULT_BUSINESS_DAYS_ONLY)
                ): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="general",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_activities(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Activities of each child (one YAML object per child)."""
        errors = {}
        detail = ""

        if user_input is not None:
            options = dict(self.config_entry.options)

            for child in CHILDREN:
                key = conf_activities_key(child)
                value = user_input.get(key)

                # Empty field: back to the built-in defaults
                if is_empty_config(value):
                    options.pop(key, None)
                    continue

                try:
                    parsed = parse_child_config(value)
                except ActivityConfigError as err:
                    errors["base"] = err.code
                    child_name = CHILDREN_CONFIG.get(child, {}).get("name", child.capitalize())
                    detail = f"\n\n⚠️ {child_name}"
                    if err.detail:
                        detail += f" → {err.detail}"
                    break

                if parsed == default_child_config(child):
                    # Same as the defaults: nothing to store
                    options.pop(key, None)
                else:
                    options[key] = parsed

            if not errors:
                # Today's activities are reconciled by the entry update listener
                return self.async_create_entry(title="", data=options)

        schema_dict = {}
        for child in CHILDREN:
            key = conf_activities_key(child)
            if user_input is not None and not is_empty_config(user_input.get(key)):
                # Show again what the user typed, so it can be corrected
                suggested = user_input[key]
            else:
                suggested = display_config(
                    effective_child_config(self.config_entry.options, child, _LOGGER)
                )
            schema_dict[
                vol.Optional(key, description={"suggested_value": suggested})
            ] = selector.ObjectSelector()

        return self.async_show_form(
            step_id="activities",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
            description_placeholders={"detail": detail},
        )

    async def async_step_announcements(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Announcements, house announcement script, AI texts and daily phrases."""
        errors = {}

        if user_input is not None:
            ai_enabled = user_input.get(CONF_AI_ANNOUNCEMENTS_ENABLED, DEFAULT_AI_ANNOUNCEMENTS_ENABLED)
            ai_agent = user_input.get(CONF_AI_AGENT)

            if ai_enabled and not ai_agent:
                errors["base"] = "ai_agent_required"
            else:
                try:
                    ai_timeout = int(user_input.get(CONF_AI_TIMEOUT, DEFAULT_AI_TIMEOUT))
                except (TypeError, ValueError):
                    ai_timeout = DEFAULT_AI_TIMEOUT

                options = self._merged_options(
                    {
                        CONF_ANNOUNCEMENTS_ENABLED: user_input.get(CONF_ANNOUNCEMENTS_ENABLED, DEFAULT_ANNOUNCEMENTS_ENABLED),
                        CONF_SCHOOL_TIME: user_input.get(CONF_SCHOOL_TIME, DEFAULT_SCHOOL_TIME),
                        CONF_WEATHER_ENTITY: user_input.get(CONF_WEATHER_ENTITY),
                        CONF_MEDIA_PLAYER_ENTITY: user_input.get(CONF_MEDIA_PLAYER_ENTITY),
                        CONF_ANNOUNCE_SCRIPT: user_input.get(CONF_ANNOUNCE_SCRIPT),
                        CONF_AI_ANNOUNCEMENTS_ENABLED: ai_enabled,
                        CONF_AI_AGENT: ai_agent,
                        CONF_AI_TIMEOUT: ai_timeout,
                        CONF_DAILY_PHRASE_ENABLED: user_input.get(CONF_DAILY_PHRASE_ENABLED, DEFAULT_DAILY_PHRASE_ENABLED),
                        CONF_DAILY_PHRASE_PROMPT: user_input.get(CONF_DAILY_PHRASE_PROMPT, DEFAULT_DAILY_PHRASE_PROMPT),
                    }
                )

                # Only store the prompt when customised, so improved defaults keep applying
                ai_prompt = (user_input.get(CONF_AI_ANNOUNCEMENT_PROMPT) or "").strip()
                if ai_prompt and ai_prompt != DEFAULT_AI_ANNOUNCEMENT_PROMPT.strip():
                    options[CONF_AI_ANNOUNCEMENT_PROMPT] = ai_prompt
                else:
                    options.pop(CONF_AI_ANNOUNCEMENT_PROMPT, None)

                # Announcement times are re-scheduled by the entry update listener
                return self.async_create_entry(title="", data=options)

        # On error show again what the user typed
        values = user_input or {}

        def current(key: str, default: Any = None) -> Any:
            if user_input is not None:
                return values.get(key, default)
            return self._current(key, default)

        schema_dict = {
            vol.Optional(
                CONF_ANNOUNCEMENTS_ENABLED,
                default=current(CONF_ANNOUNCEMENTS_ENABLED, DEFAULT_ANNOUNCEMENTS_ENABLED)
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_SCHOOL_TIME,
                default=current(CONF_SCHOOL_TIME, DEFAULT_SCHOOL_TIME)
            ): selector.TimeSelector(),
            _optional(
                CONF_WEATHER_ENTITY, current(CONF_WEATHER_ENTITY)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="weather")
            ),
            # Voice: house announcement script (preferred) or a media player with tts.speak
            _optional(
                CONF_ANNOUNCE_SCRIPT, current(CONF_ANNOUNCE_SCRIPT)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="script")
            ),
            _optional(
                CONF_MEDIA_PLAYER_ENTITY, current(CONF_MEDIA_PLAYER_ENTITY)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="media_player")
            ),
            # AI generated announcement texts
            vol.Optional(
                CONF_AI_ANNOUNCEMENTS_ENABLED,
                default=current(CONF_AI_ANNOUNCEMENTS_ENABLED, DEFAULT_AI_ANNOUNCEMENTS_ENABLED)
            ): selector.BooleanSelector(),
            _optional(
                CONF_AI_AGENT, current(CONF_AI_AGENT)
            ): _conversation_agent_selector(),
            vol.Optional(
                CONF_AI_TIMEOUT,
                default=current(CONF_AI_TIMEOUT, DEFAULT_AI_TIMEOUT)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=2,
                    max=30,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
            vol.Optional(
                CONF_AI_ANNOUNCEMENT_PROMPT,
                default=current(CONF_AI_ANNOUNCEMENT_PROMPT, DEFAULT_AI_ANNOUNCEMENT_PROMPT)
                or DEFAULT_AI_ANNOUNCEMENT_PROMPT
            ): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
            # Daily phrase configuration
            vol.Optional(
                CONF_DAILY_PHRASE_ENABLED,
                default=current(CONF_DAILY_PHRASE_ENABLED, DEFAULT_DAILY_PHRASE_ENABLED)
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_DAILY_PHRASE_PROMPT,
                default=current(CONF_DAILY_PHRASE_PROMPT, DEFAULT_DAILY_PHRASE_PROMPT)
                or DEFAULT_DAILY_PHRASE_PROMPT
            ): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
        }

        return self.async_show_form(
            step_id="announcements",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )

    async def async_step_rewards(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Reward shown when a child completes the routine."""
        # Get available OpenAI config entries
        openai_entries = self.hass.config_entries.async_entries("openai_conversation")
        openai_options = [
            selector.SelectOptionDict(value=entry.entry_id, label=entry.title)
            for entry in openai_entries
        ]

        if user_input is not None:
            values = {
                CONF_REWARD_TYPE: user_input.get(CONF_REWARD_TYPE, DEFAULT_REWARD_TYPE),
                CONF_YOUTUBE_PLAYLIST_ID: user_input.get(CONF_YOUTUBE_PLAYLIST_ID, DEFAULT_YOUTUBE_PLAYLIST_ID),
                CONF_OPENAI_ENABLED: user_input.get(CONF_OPENAI_ENABLED, DEFAULT_OPENAI_ENABLED),
                CONF_OPENAI_PROMPT: user_input.get(CONF_OPENAI_PROMPT, DEFAULT_OPENAI_PROMPT),
            }
            # Only shown (and therefore only changed) when OpenAI integrations exist
            if openai_options:
                values[CONF_OPENAI_CONFIG_ENTRY] = user_input.get(CONF_OPENAI_CONFIG_ENTRY)

            return self.async_create_entry(title="", data=self._merged_options(values))

        schema_dict = {
            vol.Optional(
                CONF_REWARD_TYPE,
                default=self._current(CONF_REWARD_TYPE, DEFAULT_REWARD_TYPE)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value="quote", label="Citação Divertida"),
                        selector.SelectOptionDict(value="youtube_video", label="Vídeo do YouTube"),
                        selector.SelectOptionDict(value="ai_image", label="Imagem gerada por IA"),
                    ]
                )
            ),
            vol.Optional(
                CONF_YOUTUBE_PLAYLIST_ID,
                default=self._current(CONF_YOUTUBE_PLAYLIST_ID, DEFAULT_YOUTUBE_PLAYLIST_ID)
                or DEFAULT_YOUTUBE_PLAYLIST_ID
            ): selector.TextSelector(),
            vol.Optional(
                CONF_OPENAI_ENABLED,
                default=self._current(CONF_OPENAI_ENABLED, DEFAULT_OPENAI_ENABLED)
            ): selector.BooleanSelector(),
        }

        # Only add OpenAI config selector if there are OpenAI integrations
        if openai_options:
            current_entry = self._current(CONF_OPENAI_CONFIG_ENTRY)
            if current_entry not in [option["value"] for option in openai_options]:
                current_entry = None
            schema_dict[
                _optional(CONF_OPENAI_CONFIG_ENTRY, current_entry)
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=openai_options)
            )

        schema_dict[vol.Optional(
            CONF_OPENAI_PROMPT,
            default=self._current(CONF_OPENAI_PROMPT, DEFAULT_OPENAI_PROMPT)
            or DEFAULT_OPENAI_PROMPT
        )] = selector.TextSelector(
            selector.TextSelectorConfig(multiline=True)
        )

        return self.async_show_form(
            step_id="rewards",
            data_schema=vol.Schema(schema_dict),
        )

    async def async_step_gdrive(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Google Drive upload of photos and audios."""
        if user_input is not None:
            # Extract UI-only field before saving
            gdrive_authenticate = user_input.pop("gdrive_authenticate", False)

            options = self._merged_options(
                {
                    CONF_GDRIVE_ENABLED: user_input.get(CONF_GDRIVE_ENABLED, DEFAULT_GDRIVE_ENABLED),
                    CONF_GDRIVE_CLIENT_ID: user_input.get(CONF_GDRIVE_CLIENT_ID),
                    CONF_GDRIVE_CLIENT_SECRET: user_input.get(CONF_GDRIVE_CLIENT_SECRET),
                    CONF_GDRIVE_FOLDER_ID: user_input.get(CONF_GDRIVE_FOLDER_ID),
                }
            )

            # If user wants to authenticate GDrive, redirect to auth step
            if (
                gdrive_authenticate
                and options.get(CONF_GDRIVE_ENABLED)
                and options.get(CONF_GDRIVE_CLIENT_ID)
            ):
                self._pending_options = options
                return await self.async_step_gdrive_auth()

            return self.async_create_entry(title="", data=options)

        schema_dict = {
            vol.Optional(
                CONF_GDRIVE_ENABLED,
                default=self._current(CONF_GDRIVE_ENABLED, DEFAULT_GDRIVE_ENABLED)
            ): selector.BooleanSelector(),
            _optional(
                CONF_GDRIVE_CLIENT_ID, self._current(CONF_GDRIVE_CLIENT_ID)
            ): selector.TextSelector(),
            _optional(
                CONF_GDRIVE_CLIENT_SECRET, self._current(CONF_GDRIVE_CLIENT_SECRET)
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            _optional(
                CONF_GDRIVE_FOLDER_ID, self._current(CONF_GDRIVE_FOLDER_ID)
            ): selector.TextSelector(),
            vol.Optional("gdrive_authenticate", default=False): selector.BooleanSelector(),
        }

        return self.async_show_form(
            step_id="gdrive",
            data_schema=vol.Schema(schema_dict),
        )

    async def async_step_gdrive_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Google Drive OAuth authentication."""
        errors = {}

        if user_input is None:
            # First visit: generate auth URL and show form
            options = self._pending_options or {}
            uploader = GoogleDriveUploader(self.hass)
            uploader._client_id = options.get(CONF_GDRIVE_CLIENT_ID)
            uploader._client_secret = options.get(CONF_GDRIVE_CLIENT_SECRET)
            auth_url = uploader.get_authorization_url("urn:ietf:wg:oauth:2.0:oob")
            self._gdrive_uploader = uploader

            return self.async_show_form(
                step_id="gdrive_auth",
                data_schema=vol.Schema({"auth_code": selector.TextSelector()}),
                description_placeholders={"auth_url": auth_url},
                errors=errors,
            )

        # Second visit: exchange code for credentials
        code = user_input.get("auth_code", "").strip()
        success = await self._gdrive_uploader.handle_authorization_callback(
            code, "urn:ietf:wg:oauth:2.0:oob"
        )

        if success:
            return self.async_create_entry(title="", data=self._pending_options)

        errors["auth_code"] = "auth_failed"
        return self.async_show_form(
            step_id="gdrive_auth",
            data_schema=vol.Schema({"auth_code": selector.TextSelector()}),
            description_placeholders={"auth_url": ""},
            errors=errors,
        )

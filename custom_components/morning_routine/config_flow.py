"""Config flow for Morning Routine Gamification integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_CALENDAR_ENTITY,
    CONF_RESET_TIME,
    CONF_BUSINESS_DAYS_ONLY,
    CONF_OPENAI_ENABLED,
    CONF_OPENAI_CONFIG_ENTRY,
    CONF_OPENAI_PROMPT,
    DEFAULT_RESET_TIME,
    DEFAULT_BUSINESS_DAYS_ONLY,
    DEFAULT_OPENAI_ENABLED,
    DEFAULT_OPENAI_PROMPT,
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
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Morning Routine."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        errors = {}

        if user_input is not None:
            # Validate calendar entity if changed
            calendar_entity = user_input.get(CONF_CALENDAR_ENTITY)
            if calendar_entity and calendar_entity not in self.hass.states.async_entity_ids("calendar"):
                errors["calendar_entity"] = "no_calendar"
            else:
                # Save options (not data)
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_CALENDAR_ENTITY: calendar_entity,
                        CONF_RESET_TIME: user_input.get(CONF_RESET_TIME, DEFAULT_RESET_TIME),
                        CONF_BUSINESS_DAYS_ONLY: user_input.get(CONF_BUSINESS_DAYS_ONLY, DEFAULT_BUSINESS_DAYS_ONLY),
                        CONF_OPENAI_ENABLED: user_input.get(CONF_OPENAI_ENABLED, DEFAULT_OPENAI_ENABLED),
                        CONF_OPENAI_CONFIG_ENTRY: user_input.get(CONF_OPENAI_CONFIG_ENTRY),
                        CONF_OPENAI_PROMPT: user_input.get(CONF_OPENAI_PROMPT, DEFAULT_OPENAI_PROMPT),
                    },
                )

        # Get current values from options first, fallback to data
        current_values = {
            CONF_CALENDAR_ENTITY: self._config_entry.options.get(
                CONF_CALENDAR_ENTITY,
                self._config_entry.data.get(CONF_CALENDAR_ENTITY)
            ),
            CONF_RESET_TIME: self._config_entry.options.get(
                CONF_RESET_TIME,
                self._config_entry.data.get(CONF_RESET_TIME, DEFAULT_RESET_TIME)
            ),
            CONF_BUSINESS_DAYS_ONLY: self._config_entry.options.get(
                CONF_BUSINESS_DAYS_ONLY,
                self._config_entry.data.get(CONF_BUSINESS_DAYS_ONLY, DEFAULT_BUSINESS_DAYS_ONLY)
            ),
            CONF_OPENAI_ENABLED: self._config_entry.options.get(
                CONF_OPENAI_ENABLED,
                self._config_entry.data.get(CONF_OPENAI_ENABLED, DEFAULT_OPENAI_ENABLED)
            ),
            CONF_OPENAI_CONFIG_ENTRY: self._config_entry.options.get(
                CONF_OPENAI_CONFIG_ENTRY,
                self._config_entry.data.get(CONF_OPENAI_CONFIG_ENTRY)
            ),
            CONF_OPENAI_PROMPT: self._config_entry.options.get(
                CONF_OPENAI_PROMPT,
                self._config_entry.data.get(CONF_OPENAI_PROMPT, DEFAULT_OPENAI_PROMPT)
            ),
        }

        # Get available OpenAI config entries
        openai_entries = self.hass.config_entries.async_entries("openai_conversation")
        openai_options = [
            selector.SelectOptionDict(value=entry.entry_id, label=entry.title)
            for entry in openai_entries
        ]

        schema_dict = {
            vol.Optional(
                CONF_CALENDAR_ENTITY,
                default=current_values[CONF_CALENDAR_ENTITY]
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="calendar")
            ),
            vol.Optional(
                CONF_RESET_TIME,
                default=current_values[CONF_RESET_TIME]
            ): selector.TimeSelector(),
            vol.Optional(
                CONF_BUSINESS_DAYS_ONLY,
                default=current_values[CONF_BUSINESS_DAYS_ONLY]
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_OPENAI_ENABLED,
                default=current_values[CONF_OPENAI_ENABLED]
            ): selector.BooleanSelector(),
        }

        # Only add OpenAI config selector if there are OpenAI integrations
        if openai_options:
            schema_dict[vol.Optional(
                CONF_OPENAI_CONFIG_ENTRY,
                default=current_values[CONF_OPENAI_CONFIG_ENTRY]
            )] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=openai_options)
            )

        schema_dict[vol.Optional(
            CONF_OPENAI_PROMPT,
            default=current_values[CONF_OPENAI_PROMPT]
        )] = selector.TextSelector(
            selector.TextSelectorConfig(multiline=True)
        )

        data_schema = vol.Schema(schema_dict)

        return self.async_show_form(
            step_id="init",
            data_schema=data_schema,
            errors=errors,
        )

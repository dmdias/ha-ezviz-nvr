"""Config flow for EZVIZ NVR: host, username and password, checked against the NVR."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.httpx_client import get_async_client

from .api import NvrAuthError, NvrClient, NvrError
from .const import CONF_FACES, CONF_FACES_RETENTION, CONF_LIVE_STREAM, CONF_RTSP_PORT, CONF_TRANSCODE, DOMAIN

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME, default="admin"): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_PORT, default=80): int,
        vol.Optional(CONF_RTSP_PORT, default=554): int,
    }
)


class EzvizNvrConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add an NVR."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = NvrClient(
                get_async_client(self.hass, verify_ssl=False),
                user_input[CONF_HOST],
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                user_input.get(CONF_PORT, 80),
                user_input.get(CONF_RTSP_PORT, 554),
            )
            try:
                info = await client.info()
            except NvrAuthError:
                errors["base"] = "invalid_auth"
            except NvrError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(info.serial)
                self._abort_if_unique_id_configured(updates={CONF_HOST: user_input[CONF_HOST]})
                return self.async_create_entry(title=f"{info.model} ({user_input[CONF_HOST]})", data=user_input)
        return self.async_show_form(step_id="user", data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EzvizNvrOptionsFlow()


class EzvizNvrOptionsFlow(OptionsFlow):
    """Video and face recognition options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        fields: dict = {vol.Optional(CONF_TRANSCODE, default=options.get(CONF_TRANSCODE, "never")): vol.In(["never", "always"])}
        coordinator = getattr(self.config_entry, "runtime_data", None)
        channels = coordinator.data.channels.values() if coordinator and coordinator.data else []
        for channel in channels:
            key = CONF_LIVE_STREAM.format(channel=channel.id)
            fields[vol.Optional(key, default=options.get(key, "auto"))] = vol.In(["auto", "main", "sub"])
        fields[vol.Optional(CONF_FACES, default=options.get(CONF_FACES, False))] = bool
        fields[vol.Optional(CONF_FACES_RETENTION, default=options.get(CONF_FACES_RETENTION, 90))] = vol.All(vol.Coerce(int), vol.Range(min=1, max=3650))
        schema = vol.Schema(fields)
        return self.async_show_form(step_id="init", data_schema=schema)

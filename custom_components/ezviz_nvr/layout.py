"""Order of the cameras in the card, shared by every browser and phone."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_KEY = f"{DOMAIN}.layout"
STORAGE_VERSION = 1
DATA = f"{DOMAIN}_layout"


async def _store(hass: HomeAssistant) -> tuple[Store, dict[str, Any]]:
    if DATA not in hass.data:
        store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        hass.data[DATA] = (store, await store.async_load() or {})
    return hass.data[DATA]


@callback
def async_register_layout(hass: HomeAssistant) -> None:
    """Commands used by the card to read and save the camera order."""

    @websocket_api.websocket_command({vol.Required("type"): "ezviz_nvr/layout/get"})
    @websocket_api.async_response
    async def ws_get(hass, connection, msg):
        _, data = await _store(hass)
        connection.send_result(msg["id"], {"order": data.get("order", [])})

    @websocket_api.websocket_command(
        {vol.Required("type"): "ezviz_nvr/layout/set", vol.Required("order"): vol.All([str], vol.Length(max=200))}
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    async def ws_set(hass, connection, msg):
        store, data = await _store(hass)
        data["order"] = list(dict.fromkeys(msg["order"]))  # without repeats
        await store.async_save(data)
        connection.send_result(msg["id"], {"order": data["order"]})

    websocket_api.async_register_command(hass, ws_get)
    websocket_api.async_register_command(hass, ws_set)

"""EZVIZ NVR: live view and recordings of an EZVIZ / Hikvision NVR, with a dashboard card."""
from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url, remove_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.httpx_client import get_async_client

from .api import NvrClient
from .const import CONF_RTSP_PORT, DOMAIN, FRONTEND_URL, VERSION
from .coordinator import NvrCoordinator

PLATFORMS = [Platform.CAMERA, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
CARD_URL = f"{FRONTEND_URL}?v={VERSION}"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = NvrClient(
        get_async_client(hass, verify_ssl=False),
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_PORT, 80),
        entry.data.get(CONF_RTSP_PORT, 554),
    )
    coordinator = NvrCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    info = coordinator.data.info
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, info.serial)},
        name=info.model,
        manufacturer="EZVIZ",
        model=info.model,
        sw_version=info.firmware,
        serial_number=info.serial,
    )

    if not hass.data.get(f"{DOMAIN}_frontend"):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(FRONTEND_URL, str(Path(__file__).parent / "frontend" / "ezviz-nvr-card.js"), False)]
        )
        add_extra_js_url(hass, CARD_URL)
        hass.data[f"{DOMAIN}_frontend"] = True

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not hass.config_entries.async_loaded_entries(DOMAIN):
        remove_extra_js_url(hass, CARD_URL)
    return unloaded

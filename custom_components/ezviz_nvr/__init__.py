"""EZVIZ NVR: live view and recordings of an EZVIZ / Hikvision NVR, with a dashboard card."""
from __future__ import annotations

from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.httpx_client import get_async_client
import voluptuous as vol

from .api import NvrClient
from .card import async_register_card, async_unregister_card
from .const import CONF_FACES, CONF_FACES_RETENTION, CONF_RTSP_PORT, DOMAIN, FRONTEND_URL, SERVICE_GET_EVENTS, VERSION
from .coordinator import NvrCoordinator
from .events import EventImageView, async_get_events
from .faces import FaceIndex, FacePictureView, async_register_websocket

PLATFORMS = [Platform.CAMERA, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
CARD_FILE = Path(__file__).parent / "frontend" / "ezviz-nvr-card.js"
FACES = f"{DOMAIN}_faces"

GET_EVENTS_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional("date"): cv.date,
        vol.Optional("limit", default=50): vol.All(vol.Coerce(int), vol.Range(min=1, max=200)),
    }
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Events of the EZVIZ cloud cameras (e.g. a doorbell), through the EZVIZ integration."""
    hass.http.register_view(EventImageView(hass))
    hass.http.register_view(FacePictureView(hass))
    async_register_websocket(hass, lambda: hass.data.get(FACES))

    async def handle_get_events(call: ServiceCall) -> ServiceResponse:
        return await async_get_events(hass, call.data["entity_id"], call.data.get("date"), call.data["limit"])

    hass.services.async_register(DOMAIN, SERVICE_GET_EVENTS, handle_get_events, schema=GET_EVENTS_SCHEMA, supports_response=SupportsResponse.ONLY)
    return True


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

    # The static path can only be registered once per run
    if not hass.data.get(f"{DOMAIN}_frontend"):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(FRONTEND_URL, str(CARD_FILE), False)]
        )
        # The URL changes with the file, so browsers never keep an old card
        mtime = await hass.async_add_executor_job(lambda: int(CARD_FILE.stat().st_mtime))
        hass.data[f"{DOMAIN}_frontend"] = f"{FRONTEND_URL}?v={VERSION}-{mtime}"
        await async_register_card(hass, FRONTEND_URL, hass.data[f"{DOMAIN}_frontend"])

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    # People recognised in the events of the EZVIZ cloud cameras (one index for all NVRs)
    if entry.options.get(CONF_FACES) and FACES not in hass.data:
        index = FaceIndex(hass, entry.options.get(CONF_FACES_RETENTION, 90))
        hass.data[FACES] = index
        hass.data[f"{FACES}_entry"] = entry.entry_id
        await index.async_start()
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and hass.data.get(f"{FACES}_entry") == entry.entry_id:
        await hass.data.pop(FACES).async_stop()
        hass.data.pop(f"{FACES}_entry")
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The last NVR removed: stop loading the card."""
    if not [e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id]:
        await async_unregister_card(hass, FRONTEND_URL, hass.data.get(f"{DOMAIN}_frontend", FRONTEND_URL))

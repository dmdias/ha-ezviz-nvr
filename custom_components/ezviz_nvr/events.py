"""Events of the cameras in the EZVIZ cloud account (doorbells, battery cameras).

Some EZVIZ devices only talk to the EZVIZ cloud (no RTSP or HTTP on the local
network). For those, the event list and the picture of each event are read
through the client of the Home Assistant EZVIZ integration, so no second
login (and no second session) is needed.
"""
from __future__ import annotations

from datetime import date, datetime
import logging
from typing import Any
from urllib.parse import urlparse

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

EZVIZ_DOMAIN = "ezviz"
IMAGE_URL = "/api/ezviz_nvr/event_image"
# Pictures are only fetched from the EZVIZ / Hik-Connect clouds
ALLOWED_IMAGE_HOSTS = ("ezvizlife.com", "ezviz.com", "ys7.com", "hik-connect.com", "hikvision.com", "ezvizru.com")
_logged_shape = False


def ezviz_client(hass: HomeAssistant):
    """The client of the loaded EZVIZ cloud account entry, or None."""
    for entry in hass.config_entries.async_entries(EZVIZ_DOMAIN):
        if entry.data.get("type") == "EZVIZ_CLOUD_ACCOUNT" and entry.state is ConfigEntryState.LOADED:
            coordinator = getattr(entry, "runtime_data", None)
            client = getattr(coordinator, "ezviz_client", None)
            if client is not None:
                return client
    return None


def camera_serial(hass: HomeAssistant, entity_id: str) -> str:
    """Serial of an EZVIZ camera entity (its unique id)."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None or entry.platform != EZVIZ_DOMAIN:
        raise ServiceValidationError(f"{entity_id} is not a camera of the EZVIZ integration")
    return entry.unique_id


def _find_list(node: Any) -> list[dict]:
    """The longest list of dicts in a response (the message list)."""
    best: list[dict] = []
    if isinstance(node, list):
        if node and all(isinstance(i, dict) for i in node):
            best = node
        for item in node:
            found = _find_list(item)
            if len(found) > len(best):
                best = found
    elif isinstance(node, dict):
        for value in node.values():
            found = _find_list(value)
            if len(found) > len(best):
                best = found
    return best


def _flat(item: dict, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in item.items():
        if isinstance(value, dict):
            out.update(_flat(value, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = value
    return out


def _first(flat: dict[str, Any], *names: str) -> Any:
    lower = {k.lower(): v for k, v in flat.items()}
    for name in names:
        for key, value in lower.items():
            if key.split(".")[-1] == name.lower() and value not in (None, "", []):
                return value
    return None


def _event_time(value: Any) -> str | None:
    """ISO time from epoch seconds / milliseconds or an EZVIZ date string."""
    if value in (None, ""):
        return None
    try:
        number = float(value)
        if number > 1e12:
            number /= 1000
        return dt_util.utc_from_timestamp(number).astimezone(dt_util.get_default_time_zone()).isoformat()
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(str(value)[:19], fmt).replace(tzinfo=dt_util.get_default_time_zone()).isoformat()
        except ValueError:
            continue
    return str(value)


def _picture(flat: dict[str, Any]) -> str | None:
    named = _first(flat, "pic", "picUrl", "defaultPic", "picture", "pictureUrl", "img", "imgUrl", "image", "imageUrl", "alarmPicUrl", "coverUrl")
    if isinstance(named, str) and named.startswith("http"):
        return named
    for key, value in flat.items():
        if isinstance(value, str) and value.startswith("http") and any(w in key.lower() for w in ("pic", "img", "image", "cover")):
            return value
    return None


def parse_events(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Normalised events and the pagination cursor of the last one."""
    global _logged_shape
    items = _find_list(payload)
    if items and not _logged_shape:
        _logged_shape = True
        _LOGGER.debug("EZVIZ event fields (first event): %s", sorted(_flat(items[0]).keys()))
    events = []
    cursor = None
    for item in items:
        flat = _flat(item)
        msg_id = _first(flat, "msgId", "id", "messageId", "alarmId")
        events.append(
            {
                "id": str(msg_id) if msg_id is not None else None,
                # timeStr is local time; the epoch fields are local time written as if UTC
                "time": _event_time(_first(flat, "timeStr", "alarmStartTimeStr", "time", "msgTime", "alarmStartTime", "alarmTime", "createTime", "occurTime")),
                "title": _first(flat, "alarmName", "title", "subTypeName", "msgTypeName", "typeName", "detail", "content", "sampleName"),
                "video": bool(_first(flat, "isVideo")),
                "type": _first(flat, "alarmType", "subType", "msgType", "type"),
                "picture": _picture(flat),
            }
        )
        cursor = str(msg_id) if msg_id is not None else cursor
    return events, cursor


async def async_get_events(hass: HomeAssistant, entity_id: str, day: date | None, limit: int) -> dict[str, Any]:
    client = ezviz_client(hass)
    if client is None:
        raise HomeAssistantError("The EZVIZ integration (cloud account) is not set up or not loaded")
    serial = camera_serial(hass, entity_id)
    day = day or dt_util.now().date()
    events = await async_fetch_events(hass, client, serial, day, limit)
    for event in events:
        if event["picture"]:
            event["picture"] = f"{IMAGE_URL}?serial={serial}&url={_quote(event['picture'])}"
    return {"entity_id": entity_id, "serial": serial, "date": day.isoformat(), "events": events}


async def async_fetch_events(hass: HomeAssistant, client, serial: str, day: date, limit: int) -> list[dict[str, Any]]:
    """Events of a device on a day, with the cloud URLs of their pictures."""
    events: list[dict[str, Any]] = []
    cursor = ""
    while len(events) < limit:
        payload = await hass.async_add_executor_job(
            lambda c=cursor: client.get_device_messages_list(serials=serial, limit=min(50, limit - len(events)), date=day, end_time=c)
        )
        page, last = parse_events(payload)
        new = [e for e in page if e["id"] not in {x["id"] for x in events}]
        if not new:
            break
        events.extend(new)
        if len(page) < 50 or not last or last == cursor:
            break
        cursor = last
    return events


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


class EventImageView(HomeAssistantView):
    """Downloads (and decrypts) an event picture from the EZVIZ cloud."""

    url = IMAGE_URL
    name = "api:ezviz_nvr:event_image"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._cache: dict[str, bytes] = {}

    async def get(self, request: web.Request) -> web.Response:
        serial = request.query.get("serial", "")
        url = request.query.get("url", "")
        host = urlparse(url).hostname or ""
        if not url.startswith("https://") and not url.startswith("http://"):
            return web.Response(status=400)
        if not any(host == h or host.endswith("." + h) for h in ALLOWED_IMAGE_HOSTS):
            return web.Response(status=403)
        if url not in self._cache:
            client = ezviz_client(self.hass)
            if client is None:
                return web.Response(status=503)
            try:
                data = await self.hass.async_add_executor_job(lambda: client.download_alarm_image(url, serial or None))
            except Exception as err:  # noqa: BLE001 - reported as a missing image
                _LOGGER.debug("Event picture download failed: %s", err)
                return web.Response(status=404)
            if len(self._cache) > 200:
                self._cache.clear()
            self._cache[url] = data
        return web.Response(body=self._cache[url], content_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

"""Live and playback cameras for every NVR channel."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import Channel
import time

from .const import CONF_LIVE_STREAM, CONF_TRANSCODE, DOMAIN, SERVICE_GET_RECORDINGS, SERVICE_PLAY, SNAPSHOT_CACHE_SECONDS
from .coordinator import NvrCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: NvrCoordinator = entry.runtime_data
    entities: list[Camera] = []
    for channel in coordinator.data.channels.values():
        entities.append(NvrLiveCamera(coordinator, entry, channel.id))
        entities.append(NvrPlaybackCamera(coordinator, entry, channel.id))
    async_add_entities(entities)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_PLAY,
        {vol.Required("start"): cv.datetime},
        "async_play",
    )
    platform.async_register_entity_service(
        SERVICE_GET_RECORDINGS,
        {vol.Optional("date"): cv.date},
        "async_get_recordings",
        supports_response=SupportsResponse.ONLY,
    )


def _to_nvr_time(value: datetime) -> datetime:
    """NVR times are naive local times."""
    if value.tzinfo is not None:
        value = dt_util.as_local(value)
    return value.replace(tzinfo=None, microsecond=0)


def _from_nvr_time(value: datetime) -> str:
    return value.replace(tzinfo=dt_util.get_default_time_zone()).isoformat()


class NvrChannelCamera(CoordinatorEntity[NvrCoordinator], Camera):
    """Base for the cameras of one channel."""

    _attr_has_entity_name = True
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, coordinator: NvrCoordinator, entry: ConfigEntry, channel_id: int) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._entry = entry
        self.channel_id = channel_id
        info = coordinator.data.info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{info.serial}_{channel_id}")},
            name=self.channel.name,
            manufacturer="EZVIZ",
            model=f"Channel {channel_id}",
        )

    @property
    def channel(self) -> Channel:
        return self.coordinator.data.channels.get(self.channel_id) or Channel(self.channel_id, f"Camera {self.channel_id}")

    @property
    def available(self) -> bool:
        return super().available and self.channel.online

    def _transcode(self) -> bool:
        # Only on request: an ffmpeg: source plays over WebRTC (go2rtc) but not
        # over HLS, which is what remote access through a proxy falls back to
        return self._entry.options.get(CONF_TRANSCODE, "never") == "always"

    def _source(self, url: str) -> str:
        return f"ffmpeg:{url}#video=h264#audio=copy" if self._transcode() else url

    def _live_sub(self) -> bool:
        """Main or sub stream for live viewing: the channel option, else the probe."""
        choice = self._entry.options.get(CONF_LIVE_STREAM.format(channel=self.channel_id), "auto")
        if choice in ("main", "sub"):
            return choice == "sub"
        return self.channel.live_sub

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        """NVR picture when it offers one, else a frame grabbed from the stream (cached briefly)."""
        cached = getattr(self, "_snapshot", None)
        if cached and time.monotonic() - cached[0] < SNAPSHOT_CACHE_SECONDS:
            return cached[1]
        image = await self.coordinator.client.snapshot(self.channel_id)
        if image is None:
            image = await self.coordinator.client.frame(self._snapshot_url())
        if image:
            self._snapshot = (time.monotonic(), image)
        return image

    def _snapshot_url(self) -> str:
        client = self.coordinator.client
        # The sub stream is lighter; channel 1 style cameras without one use the main stream
        return client.live_url(self.channel_id, sub=self._live_sub())

    async def async_get_recordings(self, date: date | None = None) -> ServiceResponse:
        """Recorded segments of the channel on a day (today by default)."""
        day = date or dt_util.now().date()
        start = datetime(day.year, day.month, day.day)
        recordings = await self.coordinator.client.recordings(self.channel_id, start, start + timedelta(days=1) - timedelta(seconds=1))
        return {
            "channel": self.channel_id,
            "date": day.isoformat(),
            "segments": [{"start": _from_nvr_time(r.start), "end": _from_nvr_time(r.end)} for r in recordings],
        }

    async def async_play(self, start: datetime) -> None:
        raise NotImplementedError("Use the playback camera of the channel")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"channel": self.channel_id, "codec": self.channel.codec, "live_stream": "sub" if self._live_sub() else "main", "transcoded": self._transcode()}


class NvrLiveCamera(NvrChannelCamera):
    """Live view of a channel."""

    def __init__(self, coordinator: NvrCoordinator, entry: ConfigEntry, channel_id: int) -> None:
        super().__init__(coordinator, entry, channel_id)
        self._attr_unique_id = f"{coordinator.data.info.serial}_{channel_id}_live"
        self._attr_name = None  # the device name

    async def stream_source(self) -> str | None:
        return self._source(self.coordinator.client.live_url(self.channel_id, sub=self._live_sub()))


class NvrPlaybackCamera(NvrChannelCamera):
    """Recording of a channel, from a chosen time (see the play action)."""

    def __init__(self, coordinator: NvrCoordinator, entry: ConfigEntry, channel_id: int) -> None:
        super().__init__(coordinator, entry, channel_id)
        self._attr_unique_id = f"{coordinator.data.info.serial}_{channel_id}_playback"
        self._attr_translation_key = "playback"
        self._start = _to_nvr_time(dt_util.now() - timedelta(hours=1))

    async def stream_source(self) -> str | None:
        return self._source(self.coordinator.client.playback_url(self.channel_id, self._start))

    async def async_play(self, start: datetime) -> None:
        """Move the playback to a time; players reconnect to see it."""
        self._start = _to_nvr_time(start)
        if self.stream:
            self.stream.update_source(await self.stream_source())
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {**super().extra_state_attributes, "playback_start": _from_nvr_time(self._start)}

"""Polls the NVR channels, their status and the disks."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import Channel, NvrAuthError, NvrClient, NvrError, NvrInfo
from .const import DOMAIN, UPDATE_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


@dataclass
class NvrData:
    info: NvrInfo
    channels: dict[int, Channel]


class NvrCoordinator(DataUpdateCoordinator[NvrData]):
    """Keeps the channel list, online state and disk usage up to date."""

    def __init__(self, hass: HomeAssistant, client: NvrClient) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS))
        self.client = client
        self._live_sub: dict[int, bool] = {}

    async def _async_update_data(self) -> NvrData:
        try:
            info = await self.client.info()
            channels = await self.client.channels()
        except NvrAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except NvrError as err:
            raise UpdateFailed(str(err)) from err
        for channel in channels:
            if channel.id not in self._live_sub and channel.online:
                # Some IP cameras refuse live viewing of the main stream on the NVR
                main_ok = await self.client.probe(self.client.live_url(channel.id))
                sub_ok = not main_ok and await self.client.probe(self.client.live_url(channel.id, sub=True))
                self._live_sub[channel.id] = bool(sub_ok)
                _LOGGER.debug("Channel %s live stream: %s", channel.id, "sub" if sub_ok else "main")
            channel.live_sub = self._live_sub.get(channel.id, False)
        return NvrData(info=info, channels={c.id: c for c in channels})

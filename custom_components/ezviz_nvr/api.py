"""Client for EZVIZ / Hikvision NVRs over the local ISAPI (HTTP) and RTSP.

Only read requests are made: device information, channels, disks, the
stream codecs, snapshots and the recording search. Times on these NVRs are
the recorder's local time, even though they are written with a "Z" suffix,
so they are handled as naive local times.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
import re
from urllib.parse import quote
import uuid
import xml.etree.ElementTree as ET

import httpx

_LOGGER = logging.getLogger(__name__)

TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
RTSP_TIME_FORMAT = "%Y%m%dT%H%M%SZ"
SEARCH_PAGE = 40


class NvrError(Exception):
    """The NVR could not be reached or answered with an error."""


class NvrAuthError(NvrError):
    """The NVR refused the credentials."""


@dataclass
class Channel:
    """A camera input of the NVR."""

    id: int
    name: str
    ip: str | None = None
    online: bool = True
    codec: str | None = None  # main stream video codec, e.g. "H.264" or "H.265"
    live_sub: bool = False  # the main stream refused live viewing, use the sub stream

    @property
    def hevc(self) -> bool:
        return bool(self.codec) and ("265" in self.codec or "HEVC" in self.codec.upper())


@dataclass
class Recording:
    """A continuous recorded segment."""

    start: datetime
    end: datetime


@dataclass
class NvrInfo:
    """Static information about the NVR."""

    name: str
    model: str
    serial: str
    firmware: str
    disks: list[dict] = field(default_factory=list)


def _strip_ns(xml: str) -> ET.Element:
    """Parse ISAPI XML, dropping the namespaces so tags can be found by name."""
    xml = re.sub(r'\sxmlns(:\w+)?="[^"]+"', "", xml, count=0)
    return ET.fromstring(xml)


def _text(node: ET.Element | None, tag: str, default: str | None = None) -> str | None:
    if node is None:
        return default
    found = node.find(f".//{tag}")
    return found.text if found is not None and found.text is not None else default


class NvrClient:
    """Talks to one NVR."""

    def __init__(self, client: httpx.AsyncClient, host: str, username: str, password: str, http_port: int = 80, rtsp_port: int = 554) -> None:
        self._client = client
        self.host = host
        self.username = username
        self._password = password
        self.http_port = http_port
        self.rtsp_port = rtsp_port
        self._auth = httpx.DigestAuth(username, password)
        self.isapi_pictures = True  # some NVRs answer 400 for IP channel pictures

    @property
    def _base(self) -> str:
        return f"http://{self.host}:{self.http_port}" if self.http_port != 80 else f"http://{self.host}"

    async def _request(self, method: str, path: str, body: str | None = None) -> httpx.Response:
        try:
            response = await self._client.request(
                method,
                self._base + path,
                content=body.encode() if body else None,
                headers={"Content-Type": "application/xml"} if body else None,
                auth=self._auth,
                timeout=15,
            )
        except httpx.HTTPError as err:
            raise NvrError(f"Could not reach the NVR at {self.host}: {err}") from err
        if response.status_code == 401:
            raise NvrAuthError("The NVR refused the username or password")
        if response.status_code >= 400:
            raise NvrError(f"{method} {path} failed: HTTP {response.status_code}")
        return response

    async def _xml(self, path: str, body: str | None = None) -> ET.Element:
        response = await self._request("POST" if body else "GET", path, body)
        return _strip_ns(response.text)

    async def info(self) -> NvrInfo:
        root = await self._xml("/ISAPI/System/deviceInfo")
        info = NvrInfo(
            name=_text(root, "deviceName", "NVR"),
            model=_text(root, "model", "NVR"),
            serial=_text(root, "serialNumber", self.host),
            firmware=_text(root, "firmwareVersion", ""),
        )
        try:
            disks = await self._xml("/ISAPI/ContentMgmt/Storage/hdd")
            for hdd in disks.iter("hdd"):
                info.disks.append(
                    {
                        "id": _text(hdd, "id"),
                        "status": _text(hdd, "status"),
                        "capacity_mb": int(_text(hdd, "capacity", "0") or 0),
                        "free_mb": int(_text(hdd, "freeSpace", "0") or 0),
                    }
                )
        except NvrError as err:
            _LOGGER.debug("Disk information unavailable: %s", err)
        return info

    async def channels(self) -> list[Channel]:
        root = await self._xml("/ISAPI/ContentMgmt/InputProxy/channels")
        channels = [
            Channel(id=int(_text(ch, "id", "0")), name=_text(ch, "name", "") or f"Camera {_text(ch, 'id')}", ip=_text(ch, "ipAddress"))
            for ch in root.iter("InputProxyChannel")
        ]
        try:
            status = await self._xml("/ISAPI/ContentMgmt/InputProxy/channels/status")
            online = {int(_text(s, "id", "0")): _text(s, "online", "true") == "true" for s in status.iter("InputProxyChannelStatus")}
            for channel in channels:
                channel.online = online.get(channel.id, True)
        except NvrError as err:
            _LOGGER.debug("Channel status unavailable: %s", err)
        for channel in channels:
            try:
                stream = await self._xml(f"/ISAPI/Streaming/channels/{channel.id}01")
                channel.codec = _text(stream, "videoCodecType")
            except NvrError as err:
                _LOGGER.debug("Codec of channel %s unavailable: %s", channel.id, err)
        return channels

    async def snapshot(self, channel: int) -> bytes | None:
        if not self.isapi_pictures:
            return None
        try:
            response = await self._request("GET", f"/ISAPI/Streaming/channels/{channel}01/picture")
        except NvrError as err:
            _LOGGER.debug("Snapshot of channel %s failed: %s", channel, err)
            if "HTTP 400" in str(err) or "HTTP 403" in str(err):
                self.isapi_pictures = False
            return None
        return response.content if response.headers.get("content-type", "").startswith("image") else None

    async def recordings(self, channel: int, start: datetime, end: datetime) -> list[Recording]:
        """Recorded segments of a channel between two local times, merged when contiguous."""
        found: list[Recording] = []
        position = 0
        search_id = str(uuid.uuid4()).upper()
        while True:
            body = (
                '<?xml version="1.0" encoding="UTF-8"?><CMSearchDescription>'
                f"<searchID>{search_id}</searchID>"
                f"<trackList><trackID>{channel}01</trackID></trackList>"
                f"<timeSpanList><timeSpan><startTime>{start.strftime(TIME_FORMAT)}</startTime>"
                f"<endTime>{end.strftime(TIME_FORMAT)}</endTime></timeSpan></timeSpanList>"
                f"<maxResults>{SEARCH_PAGE}</maxResults><searchResultPostion>{position}</searchResultPostion>"
                "<metadataList><metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor></metadataList>"
                "</CMSearchDescription>"
            )
            root = await self._xml("/ISAPI/ContentMgmt/search", body)
            status = _text(root, "responseStatusStrg", "")
            items = list(root.iter("searchMatchItem"))
            for item in items:
                try:
                    found.append(
                        Recording(
                            start=datetime.strptime(_text(item, "startTime", ""), TIME_FORMAT),
                            end=datetime.strptime(_text(item, "endTime", ""), TIME_FORMAT),
                        )
                    )
                except ValueError:
                    continue
            if status != "MORE" or not items:
                break
            position += len(items)
        found.sort(key=lambda r: r.start)
        merged: list[Recording] = []
        for rec in found:
            if merged and rec.start <= merged[-1].end + timedelta(seconds=2):
                merged[-1].end = max(merged[-1].end, rec.end)
            else:
                merged.append(Recording(rec.start, rec.end))
        return merged

    def _rtsp(self, path: str) -> str:
        auth = f"{quote(self.username, safe='')}:{quote(self._password, safe='')}@"
        return f"rtsp://{auth}{self.host}:{self.rtsp_port}{path}"

    async def probe(self, url: str, timeout: int = 8) -> bool:
        """Whether an RTSP URL answers with video (ffprobe, which Home Assistant ships)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-timeout", str(timeout * 1_000_000),
                "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "csv=p=0", url,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout + 5)
        except (OSError, asyncio.TimeoutError):
            return False
        return proc.returncode == 0 and bool(out.strip())

    async def frame(self, url: str, timeout: int = 12) -> bytes | None:
        """A JPEG frame from an RTSP stream (over TCP, scaled down for thumbnails)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp", "-timeout", str(timeout * 1_000_000),
                "-i", url, "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "6", "-f", "image2", "-c:v", "mjpeg", "-",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout + 5)
        except (OSError, asyncio.TimeoutError):
            return None
        return out if proc.returncode == 0 and out else None

    def live_url(self, channel: int, sub: bool = False) -> str:
        return self._rtsp(f"/Streaming/Channels/{channel}0{2 if sub else 1}")

    def playback_url(self, channel: int, start: datetime) -> str:
        return self._rtsp(f"/Streaming/tracks/{channel}01/?starttime={start.strftime(RTSP_TIME_FORMAT)}")

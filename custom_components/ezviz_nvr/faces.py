"""People seen by the EZVIZ cloud cameras (e.g. a doorbell), recognised by their face.

Every few minutes the new events of the cloud cameras are read through the
EZVIZ integration. Each event picture is searched for faces; each face gets a
feature vector and joins the person whose earlier faces are most similar, or
starts a new, unnamed person. Names are set by the user; giving two people
the same name merges them.

Everything stays in Home Assistant: the index in ``.storage/ezviz_nvr.faces``
and the pictures in ``<config>/ezviz_nvr/faces``.
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta
import io
import logging
from pathlib import Path
import re
from typing import Any
import uuid

from aiohttp import web
import numpy as np
from PIL import Image

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .events import EZVIZ_DOMAIN, async_fetch_events, ezviz_client

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "ezviz_nvr.faces"
STORAGE_VERSION = 1
PICTURE_URL = "/api/ezviz_nvr/faces/picture"
EVENT_FACE = "ezviz_nvr_face_detected"

SCAN_INTERVAL = timedelta(minutes=2)
BACKFILL_DAYS = 14
MATCH_THRESHOLD = 0.42  # cosine similarity of SFace vectors for the same person
MIN_SCORE = 0.85  # detector confidence
MIN_FACE_PX = 28  # smaller faces are too blurred to tell people apart
KEEP_PER_NAMED = 10  # faces of a named person kept after the retention period
PROCESSED_PER_CAMERA = 3000
FRESH_EVENT = timedelta(minutes=15)  # older events (backfill) do not fire EVENT_FACE


def _key(*parts: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", "_".join(parts))


def _vec_encode(vec: np.ndarray) -> str:
    return base64.b64encode(vec.astype(np.float16).tobytes()).decode()


def _vec_decode(text: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(text), dtype=np.float16).astype(np.float32)


def cloud_cameras(hass: HomeAssistant) -> dict[str, str]:
    """Camera entities of the EZVIZ integration that are not an NVR, by serial."""
    devices = dr.async_get(hass)
    found = {}
    for entry in er.async_get(hass).entities.values():
        if entry.platform != EZVIZ_DOMAIN or entry.domain != "camera" or entry.disabled_by:
            continue
        device = devices.async_get(entry.device_id) if entry.device_id else None
        model, name = str(device.model or "") if device else "", str(device.name or "") if device else ""
        if re.match(r"^(UNKNOWN|HWN)", model, re.I) or re.match(r"^HWN|NVR", name, re.I):
            continue
        found[entry.unique_id] = entry.entity_id
    return found


def _analyse(engine, data: bytes) -> tuple[bytes, list[dict[str, Any]]]:
    """Faces of a picture: (picture as JPEG, [{crop, vec, box, score}]). Runs in an executor."""
    from .face_engine import jpeg

    image = Image.open(io.BytesIO(data))
    image.load()
    faces = []
    for face in engine.detect(image, min_score=MIN_SCORE):
        x, y, w, h = face.box
        if min(w, h) < MIN_FACE_PX:
            continue
        aligned = engine.align(image, face)
        # Thumbnail: the face with some margin, square
        side = max(w, h) * 1.5
        cx, cy = x + w / 2, y + h / 2
        crop = image.convert("RGB").crop((int(cx - side / 2), int(cy - side / 2), int(cx + side / 2), int(cy + side / 2))).resize((160, 160), Image.BILINEAR)
        faces.append(
            {
                "vec": engine.describe(aligned),
                "crop": jpeg(crop, 82),
                "box": [round(v) for v in face.box],
                "score": round(face.score, 3),
                "sharpness": round(engine.sharpness(aligned)),
            }
        )
    return (jpeg(image, 85) if faces else b""), faces


class FaceIndex:
    """The people seen by the cloud cameras and where they appear."""

    def __init__(self, hass: HomeAssistant, retention_days: int) -> None:
        self.hass = hass
        self.retention = timedelta(days=retention_days)
        self.folder = Path(hass.config.path("ezviz_nvr", "faces"))
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, Any] = {}
        self._engine = None
        self._lock = asyncio.Lock()
        self._unsub: CALLBACK_TYPE | None = None
        self._failures: dict[str, int] = {}
        self._matrix: np.ndarray | None = None
        self._matrix_ids: list[str] = []
        self.scanning = False
        self.last_scan: datetime | None = None
        self.last_error: str | None = None

    # ----- lifecycle -----

    async def async_start(self) -> None:
        self._data = await self._store.async_load() or {}
        for name, default in (("people", {}), ("faces", {}), ("events", {}), ("processed", {}), ("next_num", 1)):
            self._data.setdefault(name, default)
        self._unsub = async_track_time_interval(self.hass, self._async_tick, SCAN_INTERVAL)
        self.hass.async_create_background_task(self.async_scan(), "ezviz_nvr faces first scan")

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        await self._store.async_save(self._data)

    async def _async_tick(self, _now: datetime) -> None:
        await self.async_scan()

    def _save(self) -> None:
        self._store.async_delay_save(lambda: self._data, 10)

    # ----- scanning -----

    async def async_scan(self, days: int | None = None) -> int:
        """Analyse the new events of the last days; returns the number of new faces."""
        if self._lock.locked():
            return 0
        async with self._lock:
            self.scanning = True
            try:
                return await self._scan(days)
            except Exception as err:  # noqa: BLE001 - keep the timer alive, show the error in the card
                self.last_error = str(err)
                _LOGGER.warning("Face scan failed: %s", err)
                return 0
            finally:
                self.scanning = False
                self.last_scan = dt_util.now()

    async def _scan(self, days: int | None) -> int:
        client = ezviz_client(self.hass)
        if client is None:
            self.last_error = "The EZVIZ integration (cloud account) is not set up or not loaded"
            return 0
        self.last_error = None
        if days is None:
            days = BACKFILL_DAYS if not self._data["processed"] else 2
        today = dt_util.now().date()
        added = 0
        for serial, entity_id in cloud_cameras(self.hass).items():
            done = self._data["processed"].setdefault(serial, [])
            seen = set(done)
            for back in range(days):
                events = await async_fetch_events(self.hass, client, serial, today - timedelta(days=back), 200)
                for event in reversed(events):  # oldest first, so people are numbered in order
                    if not event["id"] or event["id"] in seen:
                        continue
                    ok = await self._process(client, serial, entity_id, event)
                    if ok is None:
                        continue  # retry on the next scan
                    added += ok
                    seen.add(event["id"])
                    done.append(event["id"])
            del done[:-PROCESSED_PER_CAMERA]
        self._prune()
        self._save()
        return added

    async def _process(self, client, serial: str, entity_id: str, event: dict[str, Any]) -> int | None:
        """Faces added for one event, or None when the picture could not be read (yet)."""
        if not event.get("picture"):
            return 0
        try:
            data = await self.hass.async_add_executor_job(client.download_alarm_image, event["picture"], serial)
            if self._engine is None:
                from .face_engine import FaceEngine

                self._engine = await self.hass.async_add_executor_job(FaceEngine)
            picture, faces = await self.hass.async_add_executor_job(_analyse, self._engine, data)
        except Exception as err:  # noqa: BLE001 - a broken picture must not stop the scan
            tries = self._failures[event["id"]] = self._failures.get(event["id"], 0) + 1
            _LOGGER.debug("Event %s of %s not analysed (%s): %s", event["id"], serial, tries, err)
            return None if tries < 3 else 0
        if not faces:
            return 0
        event_key = _key(serial, event["id"])
        files: dict[str, bytes] = {f"events/{event_key}.jpg": picture}
        face_ids = []
        for face in faces:
            face_id = uuid.uuid4().hex[:12]
            files[f"crops/{face_id}.jpg"] = face.pop("crop")
            person, similarity = self._match(face["vec"])
            new_person = person is None
            if new_person:
                person = self._new_person()
            self._data["faces"][face_id] = {
                "person": person,
                "event": event_key,
                "camera": entity_id,
                "time": event["time"],
                "box": face["box"],
                "score": face["score"],
                "sharpness": face["sharpness"],
                "similarity": round(similarity, 3),
                "vec": _vec_encode(face["vec"]),
            }
            self._matrix = None
            face_ids.append(face_id)
            self._announce(person, face_id, new_person)
        self._data["events"][event_key] = {"camera": entity_id, "time": event["time"], "title": event.get("title"), "faces": face_ids}
        await self.hass.async_add_executor_job(self._write, files)
        return len(face_ids)

    def _write(self, files: dict[str, bytes]) -> None:
        for name, data in files.items():
            path = self.folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def _announce(self, person: str, face_id: str, new_person: bool) -> None:
        face = self._data["faces"][face_id]
        try:
            fresh = dt_util.now() - datetime.fromisoformat(face["time"]) < FRESH_EVENT
        except (TypeError, ValueError):
            fresh = False
        if fresh:
            self.hass.bus.async_fire(
                EVENT_FACE,
                {"person_id": person, "name": self.label(person), "named": bool(self._data["people"][person].get("name")),
                 "new_person": new_person, "camera": face["camera"], "time": face["time"], "face_id": face_id},
            )

    # ----- matching -----

    def _vectors(self) -> tuple[np.ndarray, list[str]]:
        if self._matrix is None:
            ids = [fid for fid, f in self._data["faces"].items() if f.get("vec")]
            self._matrix = np.stack([_vec_decode(self._data["faces"][fid]["vec"]) for fid in ids]) if ids else np.zeros((0, 128), np.float32)
            self._matrix_ids = ids
        return self._matrix, self._matrix_ids

    def _match(self, vec: np.ndarray) -> tuple[str | None, float]:
        """The person with the most similar face, when similar enough."""
        matrix, ids = self._vectors()
        if not ids:
            return None, 0.0
        sims = matrix @ vec
        best: dict[str, float] = {}
        for fid, sim in zip(ids, sims):
            person = self._data["faces"][fid]["person"]
            best[person] = max(best.get(person, -1.0), float(sim))
        person, sim = max(best.items(), key=lambda kv: kv[1])
        return (person, sim) if sim >= MATCH_THRESHOLD else (None, sim)

    def _new_person(self) -> str:
        pid = uuid.uuid4().hex[:10]
        self._data["people"][pid] = {"num": self._data["next_num"], "name": ""}
        self._data["next_num"] += 1
        return pid

    # ----- retention -----

    def _prune(self) -> None:
        cutoff = dt_util.now() - self.retention
        by_person: dict[str, list[tuple[str, str]]] = {}
        for fid, face in self._data["faces"].items():
            by_person.setdefault(face["person"], []).append((face.get("time") or "", fid))
        doomed = []
        for pid, faces in by_person.items():
            faces.sort(reverse=True)
            keep = KEEP_PER_NAMED if self._data["people"].get(pid, {}).get("name") else 0
            for i, (when, fid) in enumerate(faces):
                try:
                    old = datetime.fromisoformat(when) < cutoff
                except ValueError:
                    old = False
                if old and i >= keep:
                    doomed.append(fid)
        if doomed:
            self._remove_faces(doomed)

    def _remove_faces(self, face_ids: list[str]) -> None:
        files = []
        for fid in face_ids:
            face = self._data["faces"].pop(fid, None)
            if not face:
                continue
            files.append(f"crops/{fid}.jpg")
            event = self._data["events"].get(face["event"])
            if event:
                event["faces"] = [f for f in event["faces"] if f != fid]
                if not event["faces"]:
                    del self._data["events"][face["event"]]
                    files.append(f"events/{face['event']}.jpg")
        used = {f["person"] for f in self._data["faces"].values()}
        for pid in [p for p, info in self._data["people"].items() if p not in used and not info.get("name")]:
            del self._data["people"][pid]
        self._matrix = None
        self.hass.async_add_executor_job(self._unlink, files)

    def _unlink(self, files: list[str]) -> None:
        for name in files:
            (self.folder / name).unlink(missing_ok=True)

    # ----- editing -----

    def label(self, pid: str) -> str:
        person = self._data["people"].get(pid, {})
        return person.get("name") or f"#{person.get('num', '?')}"

    def _by_name(self, name: str, other_than: str | None = None) -> str | None:
        wanted = name.strip().casefold()
        for pid, info in self._data["people"].items():
            if pid != other_than and info.get("name", "").strip().casefold() == wanted:
                return pid
        return None

    def rename(self, pid: str, name: str) -> str:
        """Name a person; a name already in use merges the two. Returns the resulting person."""
        if pid not in self._data["people"]:
            raise KeyError(pid)
        name = name.strip()
        target = self._by_name(name, pid) if name else None
        if target:
            for face in self._data["faces"].values():
                if face["person"] == pid:
                    face["person"] = target
            del self._data["people"][pid]
            pid = target
        else:
            self._data["people"][pid]["name"] = name
        self._save()
        return pid

    def assign(self, face_id: str, name: str) -> str:
        """Move one face to the person with this name (a new person when no one has it)."""
        face = self._data["faces"][face_id]
        old = face["person"]
        target = self._by_name(name) if name.strip() else None
        if target is None:
            target = self._new_person()
            self._data["people"][target]["name"] = name.strip()
        face["person"] = target
        if not any(f["person"] == old for f in self._data["faces"].values()) and not self._data["people"].get(old, {}).get("name"):
            self._data["people"].pop(old, None)
        self._save()
        return target

    def delete_person(self, pid: str) -> None:
        self._remove_faces([fid for fid, f in self._data["faces"].items() if f["person"] == pid])
        self._data["people"].pop(pid, None)
        self._save()

    def delete_face(self, face_id: str) -> None:
        self._remove_faces([face_id])
        self._save()

    # ----- reading -----

    def _crop_uri(self, face_id: str) -> str | None:
        path = self.folder / "crops" / f"{face_id}.jpg"
        try:
            return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode()
        except OSError:
            return None

    def _cover(self, faces: list[tuple[str, dict]]) -> str:
        """The clearest face of a person, by detector score and size."""
        return max(faces, key=lambda f: (f[1].get("score", 0) * min(f[1]["box"][2], f[1]["box"][3]), f[1].get("time") or ""))[0]

    def fill_thumbnails(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Replace the face ids in "thumbnail" by the pictures. Runs in an executor."""
        for row in rows:
            row["thumbnail"] = self._crop_uri(row["thumbnail"])
        return rows

    def people(self) -> list[dict[str, Any]]:
        """People with at least one face, last seen first (thumbnail = face id)."""
        faces_of: dict[str, list[tuple[str, dict]]] = {}
        for fid, face in self._data["faces"].items():
            faces_of.setdefault(face["person"], []).append((fid, face))
        out = []
        for pid, info in self._data["people"].items():
            faces = faces_of.get(pid, [])
            if not faces:
                continue
            times = sorted(f.get("time") or "" for _, f in faces)
            out.append(
                {
                    "id": pid,
                    "name": info.get("name", ""),
                    "label": self.label(pid),
                    "count": len({f["event"] for _, f in faces}),
                    "first": times[0],
                    "last": times[-1],
                    "cameras": sorted({f["camera"] for _, f in faces}),
                    "thumbnail": self._cover(faces),
                }
            )
        out.sort(key=lambda p: p["last"], reverse=True)
        return out

    def occurrences(self, pid: str, limit: int = 300) -> list[dict[str, Any]]:
        """Faces of a person, newest first (thumbnail = face id)."""
        rows = sorted(((fid, f) for fid, f in self._data["faces"].items() if f["person"] == pid), key=lambda x: x[1].get("time") or "", reverse=True)
        return [
            {
                "face_id": fid,
                "time": face.get("time"),
                "camera": face["camera"],
                "title": self._data["events"].get(face["event"], {}).get("title"),
                "similarity": face.get("similarity"),
                "box": face["box"],
                "thumbnail": fid,
                "picture": f"{PICTURE_URL}/{face['event']}",
                "others": [self.label(self._data["faces"][o]["person"]) for o in self._data["events"].get(face["event"], {}).get("faces", []) if o != fid and o in self._data["faces"]],
            }
            for fid, face in rows[:limit]
        ]

    def status(self) -> dict[str, Any]:
        return {
            "scanning": self.scanning,
            "last_scan": self.last_scan.isoformat() if self.last_scan else None,
            "error": self.last_error,
            "faces": len(self._data["faces"]),
            "events": len(self._data["events"]),
            "processed": sum(len(v) for v in self._data["processed"].values()),
        }

    def person_exists(self, pid: str) -> bool:
        return pid in self._data["people"]

    def face_exists(self, face_id: str) -> bool:
        return face_id in self._data["faces"]


class FacePictureView(HomeAssistantView):
    """The saved picture of an event with faces."""

    url = PICTURE_URL + "/{event_key}"
    name = "api:ezviz_nvr:faces:picture"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, event_key: str) -> web.Response:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", event_key):
            return web.Response(status=400)
        path = Path(self.hass.config.path("ezviz_nvr", "faces", "events", f"{event_key}.jpg"))
        try:
            data = await self.hass.async_add_executor_job(path.read_bytes)
        except OSError:
            return web.Response(status=404)
        return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@callback
def async_register_websocket(hass: HomeAssistant, get_index) -> None:
    """Commands used by the card: list people, occurrences, rename, move or delete."""
    import voluptuous as vol

    from homeassistant.components import websocket_api

    def _index(connection, msg):
        index = get_index()
        if index is None:
            connection.send_error(msg["id"], "not_enabled", "Face recognition is off: enable it in the EZVIZ NVR options")
        return index

    @websocket_api.websocket_command({vol.Required("type"): "ezviz_nvr/faces/people"})
    @websocket_api.async_response
    async def ws_people(hass, connection, msg):
        if (index := _index(connection, msg)) is None:
            return
        people = await hass.async_add_executor_job(index.fill_thumbnails, index.people())
        connection.send_result(msg["id"], {"people": people, "status": index.status()})

    @websocket_api.websocket_command({vol.Required("type"): "ezviz_nvr/faces/person", vol.Required("person_id"): str})
    @websocket_api.async_response
    async def ws_person(hass, connection, msg):
        if (index := _index(connection, msg)) is None:
            return
        if not index.person_exists(msg["person_id"]):
            connection.send_error(msg["id"], "not_found", "Unknown person")
            return
        rows = await hass.async_add_executor_job(index.fill_thumbnails, index.occurrences(msg["person_id"]))
        connection.send_result(msg["id"], {"occurrences": rows})

    @websocket_api.websocket_command({vol.Required("type"): "ezviz_nvr/faces/rename", vol.Required("person_id"): str, vol.Required("name"): str})
    @callback
    def ws_rename(hass, connection, msg):
        if (index := _index(connection, msg)) is None:
            return
        try:
            connection.send_result(msg["id"], {"person_id": index.rename(msg["person_id"], msg["name"])})
        except KeyError:
            connection.send_error(msg["id"], "not_found", "Unknown person")

    @websocket_api.websocket_command({vol.Required("type"): "ezviz_nvr/faces/assign", vol.Required("face_id"): str, vol.Required("name"): str})
    @callback
    def ws_assign(hass, connection, msg):
        if (index := _index(connection, msg)) is None:
            return
        if not index.face_exists(msg["face_id"]):
            connection.send_error(msg["id"], "not_found", "Unknown face")
            return
        connection.send_result(msg["id"], {"person_id": index.assign(msg["face_id"], msg["name"])})

    @websocket_api.websocket_command(
        {vol.Required("type"): "ezviz_nvr/faces/delete", vol.Exclusive("person_id", "what"): str, vol.Exclusive("face_id", "what"): str}
    )
    @websocket_api.require_admin
    @callback
    def ws_delete(hass, connection, msg):
        if (index := _index(connection, msg)) is None:
            return
        if "person_id" in msg:
            index.delete_person(msg["person_id"])
        elif "face_id" in msg:
            index.delete_face(msg["face_id"])
        connection.send_result(msg["id"], {})

    @websocket_api.websocket_command({vol.Required("type"): "ezviz_nvr/faces/scan", vol.Optional("days", default=2): vol.All(int, vol.Range(min=1, max=30))})
    @websocket_api.async_response
    async def ws_scan(hass, connection, msg):
        if (index := _index(connection, msg)) is None:
            return
        added = await index.async_scan(msg["days"])
        connection.send_result(msg["id"], {"added": added, "status": index.status()})

    for command in (ws_people, ws_person, ws_rename, ws_assign, ws_delete, ws_scan):
        websocket_api.async_register_command(hass, command)

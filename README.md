# EZVIZ NVR

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/dmdias/ha-ezviz-nvr.svg)](https://github.com/dmdias/ha-ezviz-nvr/releases)
[![Validate](https://github.com/dmdias/ha-ezviz-nvr/actions/workflows/validate.yml/badge.svg)](https://github.com/dmdias/ha-ezviz-nvr/actions/workflows/validate.yml)
[![License: GPLv3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Buy me a coffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-PayPal-ffdd00?logo=buymeacoffee&logoColor=black)](https://www.paypal.com/donate/?business=dmdias%40gmail.com&currency_code=EUR&item_name=EZVIZ+NVR)

Watch the cameras of an **EZVIZ NVR** live and **play back the recordings
on its disk** from Home Assistant, as you would in the EZVIZ app. It all
runs over your local network.

It talks to the NVR directly: ISAPI over HTTP for the channels, disks and
recording search, and RTSP for live and recorded video. It works with EZVIZ
NVRs, which are Hikvision devices inside, and should work with other
Hikvision-based NVRs.

## Contents

- [What you get](#what-you-get)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [The card](#the-card)
- [People (face recognition)](#people-face-recognition)
- [Entities](#entities)
- [Actions](#actions)
- [Video formats and remote access](#video-formats-and-remote-access)
- [Troubleshooting](#troubleshooting)
- [Support the project](#support-the-project)
- [Author](#author)
- [License](#license)

## What you get

- **Every NVR channel as cameras**: a live camera and a playback camera per
  channel, named after the channel on the NVR.
- **Recordings from the NVR disk**: list what was recorded on any day and
  play it from any moment.
- **A dashboard card** in the style of the EZVIZ app:
  - a camera grid with thumbnails;
  - a live / playback player;
  - a 24-hour timeline of the recordings: tap a time to watch from there,
    move between days, step one minute back or forward.
- **Disk sensors**: free space and usage of each disk.
- **Events of EZVIZ cloud cameras**: some devices only talk to the EZVIZ
  cloud, for example many doorbells and battery cameras. For those, the card
  lists the day's events (motion, ring, smart detection) with their
  pictures, through the Home Assistant EZVIZ integration.
- **People**, optional: faces in the events of the cloud cameras are grouped
  by person, so you can see who rang and when, and give each person a name.
  It runs inside Home Assistant.
- **Automatic stream choice**: some IP cameras refuse live viewing of their
  main stream through the NVR, so each channel uses the stream that answers.
  You can also choose per channel.
- **Local video**: the NVR video never goes through the EZVIZ cloud, and the
  NVR password stays in Home Assistant. Only the optional cloud camera events
  use the EZVIZ account, through the existing EZVIZ integration.

## Requirements

- Home Assistant 2024.12 or newer, with the built-in **go2rtc** (enabled by
  default since 2024.11) for low-latency live video.
- An EZVIZ (or Hikvision-based) NVR on the same network as Home Assistant.
- The NVR **device password**: the one set when the NVR was activated, used
  on its local screen, in SADP or in iVMS-4200. The device verification code
  printed on the label is not enough for the NVR's HTTP interface.
- Ports **80** (HTTP) and **554** (RTSP) of the NVR reachable from Home
  Assistant.
- Optional, for the events of cloud-only cameras (doorbells, ...): the
  Home Assistant [EZVIZ integration](https://www.home-assistant.io/integrations/ezviz/)
  set up with your EZVIZ account.

## Installation

1. HACS → **⋮ → Custom repositories** → add
   `https://github.com/dmdias/ha-ezviz-nvr` with type **Integration**.

   [![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=dmdias&repository=ha-ezviz-nvr&category=integration)
2. Search **EZVIZ NVR** → **Download**, and restart Home Assistant.
3. **Settings → Devices & services → Add integration → EZVIZ NVR**.

   [![Open your Home Assistant instance and start setting up EZVIZ NVR.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=ezviz_nvr)
4. Enter:
   - the NVR **IP address**;
   - username **admin**;
   - the **device password**.

   Keep the ports (80 and 554) unless you changed them on the NVR. The
   integration checks the credentials with the NVR before saving them.
5. Reload the browser. On the companion app, also reset its frontend cache:
   **Settings → Companion app → Debugging → Reset frontend cache**.

To find the NVR address, look for it in your router. You can also use the
EZVIZ integration of Home Assistant: its *Local IP* sensor shows the address
of each device.

> Hikvision devices lock the account for about 30 minutes after several wrong
> passwords. If the setup keeps saying the password is wrong, wait before
> trying again.

## Configuration

**Settings → Devices & services → EZVIZ NVR → Configure**:

| Option | Default | Description |
|---|---|---|
| Live stream of each channel | `auto` | `main`, `sub`, or `auto`. With `auto`, the integration uses the main stream if it answers, else the sub stream. Choose `sub` for a camera whose main stream only works now and then. |
| Recognise people in the events of the EZVIZ cloud cameras | off | Turns on [People](#people-face-recognition). |
| Days to keep the faces | `90` | Older faces are deleted, except the last 10 of each named person. |
| Convert H.265 to H.264 | `never` | `always` converts every channel with go2rtc so browsers without H.265 support can play it. This only works over WebRTC, not over HLS (see below). |

## The card

The integration adds the card to the dashboard resources and keeps it up to
date, so there is nothing to register. Edit a dashboard → **Add card** → **EZVIZ NVR**, or use YAML:

```yaml
type: custom:ezviz-nvr-card
columns: 3   # optional, maximum grid columns (default 2); tiles wrap on narrow screens
cloud_cameras:   # optional, EZVIZ cloud cameras to list (default: all but the NVR)
  - camera.front_doorbell
```

It looks best in a view of type **Panel**.

- **Grid**: every channel with a thumbnail, refreshed every 30 seconds, and
  its online state. The NVR disk usage is shown at the top.
- **Tap a camera**: live video.
- **Playback tab**:
  - Recorded segments of the day are shown in blue on a 24-hour timeline.
  - Tap the timeline to play from that time, or use **−1 min** / **+1 min**.
  - **◀ ▶** change the day.
  - The badge shows when the playback started and roughly where it is now.
- **EZVIZ cloud cameras** (tagged *Cloud*), such as a doorbell:
  - The grid shows the last motion picture.
  - Tapping one opens the day's events with their pictures, time and type.
  - Tap a picture to enlarge it, or use **◀ ▶** to change the day.
  - These cameras need the Home Assistant EZVIZ integration.

## People (face recognition)

Turn on **Recognise people in the events of the EZVIZ cloud cameras** in the
options. It needs the Home Assistant EZVIZ integration.

**How it works**
- Every 30 seconds the new events of the EZVIZ cloud cameras (doorbells, ...)
  are read. The first time, the last 14 days are read.
- Each event picture is searched for faces.
- Each face joins the person whose earlier faces look most alike, or starts a
  new, unnamed person.

**In the card**, the **👤 People** button lists who was seen, how many times and
when last. Open a person to:
- **Name them**: type the name and press **Save** or Enter. Giving two people
  the same name merges them. This is the usual fix when the same person was
  split in two, for example with and without sunglasses.
- **Open an occurrence**: the full picture, with the face marked. From there:
  - **Move to** puts a face that went to the wrong person under the right name.
  - **Remove face** deletes it.
- **Delete the person** with all their faces (administrators only).

**Statistics**: **📊 Statistics** in the People view shows how often each
person came:
- **Day** covers the last 30 days, **Week** the last 12 weeks and **Month**
  the last 12 months.
- Each person has a small chart, the count for the current day, week or
  month, and the total.
- A person's page has a larger chart with the current period, the total and
  the average.

Statistics count **visits**: sightings of a person less than 15 minutes apart
are one visit, so ringing and waiting at the door counts once. The sightings
themselves (time, person, camera, no picture) are kept for 3 years, so the
statistics stay after the faces are deleted.

**Automations**: when a face is found in a new event, Home Assistant fires
`ezviz_nvr_face_detected`:

```yaml
triggers:
  - trigger: event
    event_type: ezviz_nvr_face_detected
    event_data:
      name: Maria
actions:
  - action: notify.mobile_app_phone
    data:
      message: "Maria is at the door"
```

The event data has `person_id`, `name` (`#7` for an unnamed person), `named`,
`new_person`, `camera`, `time` and `face_id`.

**Privacy and resources**
- Everything stays in Home Assistant:
  - the index is in `.storage/ezviz_nvr.faces`;
  - the face thumbnails and the event pictures with faces are in
    `<config>/ezviz_nvr/faces`.
- Nothing is sent to any service. The models are
  [YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)
  (face detection, MIT) and
  [SFace](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface)
  (face features, Apache 2.0) from the OpenCV Zoo. They run on numpy, so no
  add-on is needed.
- About half a second of CPU per picture on a small server.
- Faces smaller than about 28 pixels are ignored.
- Recognition is not perfect: people at an angle, in the dark or far away may
  be missed or split into more than one person.
- Face pictures are personal data. If the camera sees a public place, check
  the rules that apply where you live.

## Entities

For an NVR with channels *Front* and *Garden*:

| Entity | Description |
|---|---|
| `camera.front` | Live view of the channel |
| `camera.front_playback` | Recording of the channel, from the time set with `ezviz_nvr.play` (one hour ago by default) |
| `sensor.<nvr>_disk_1_free` | Free space of disk 1 (GB) |
| `sensor.<nvr>_disk_1_used` | Usage of disk 1 (%) |

The camera attributes show the channel number, the codec of its main stream
and which stream the live view uses.

## Actions

### `ezviz_nvr.get_recordings`

Returns the recorded segments of a channel on a day. Contiguous files are
merged into one segment.

```yaml
action: ezviz_nvr.get_recordings
target:
  entity_id: camera.front
data:
  date: "2026-10-13"   # optional, today by default
response_variable: recordings
```

The response has the shape
`{"channel": 1, "date": "2026-10-13", "segments": [{"start": "...", "end": "..."}]}`.

### `ezviz_nvr.get_events`

Returns the events of a camera of the Home Assistant EZVIZ integration (for
example a doorbell) on a day: time, alarm type, whether a video was recorded,
and a picture URL served by Home Assistant. It uses the session of the EZVIZ
integration, so there is no second login.

```yaml
action: ezviz_nvr.get_events
data:
  entity_id: camera.front_doorbell
  date: "2026-10-13"   # optional, today by default
  limit: 50            # optional, up to 200
response_variable: events
```

Pictures come from the EZVIZ cloud only. Home Assistant decrypts them when
the camera encrypts its images.

### `ezviz_nvr.play`

Moves a playback camera to a time. Players reconnect to show the recording
from there.

```yaml
action: ezviz_nvr.play
target:
  entity_id: camera.front_playback
data:
  start: "2026-10-13 08:15:00"
```

Times are the NVR's local time. Home Assistant and the NVR should use the
same time zone.

## Video formats and remote access

Home Assistant shows camera video in one of two ways:

- **WebRTC through go2rtc**: low latency. It needs a direct connection to
  Home Assistant, which is the case on your network or over a VPN.
- **HLS**: a few seconds of delay. It works everywhere, including through a
  reverse proxy or tunnel (for example Cloudflare), where WebRTC usually
  cannot connect.

Cameras that record in **H.265 (HEVC)**:

- **Over HLS:** they play in browsers that can decode H.265. That includes
  Chrome and Edge on most current computers and phones, and Safari. Firefox
  cannot.
- **Over WebRTC:** most browsers cannot play them. The option *Convert H.265
  to H.264* fixes this, but only over WebRTC. The better fix is usually to
  set the camera to H.264 in the NVR's video settings.

## Troubleshooting

**Invalid username or password**
- Use the NVR **device password**, not the EZVIZ account password and not
  the verification code.
- After several attempts the NVR locks the account for about 30 minutes.

**A camera shows no video ("Operation not permitted")**
That camera refuses live viewing of its main stream through the NVR, often
because it only allows a few connections and the NVR is using one to
record. Set its live stream to `sub` in the integration options.

**The card says "Custom element doesn't exist: ezviz-nvr-card"**
Reload the browser. On the companion app, reset the frontend cache and
close the app completely.

**A doorbell or battery camera shows no video, only events**
- These devices only talk to the EZVIZ cloud: they have no RTSP or HTTP on
  the local network, so their live view and SD card recordings are not
  reachable locally.
- The EZVIZ Open Platform could stream them, but only for devices owned by
  the developer account. That would mean moving the device out of your app
  account, so it is not used here.

**No people appear**
- Check that the option is on and that the EZVIZ integration is loaded.
- The People view shows the time of the last scan, and the error if there
  was one.
- **Scan now** reads the last two days again.

**Thumbnails take a while to appear**
Some NVRs do not serve pictures for IP camera channels. The integration
then grabs a frame from the video, which can take a few seconds, longer for
H.265 channels. Thumbnails are cached for 25 seconds.

**The playback starts at a different time than the one I tapped**
- The NVR searches from the nearest recorded frame, so playback can start a
  few seconds earlier.
- If it is off by hours, the NVR's time zone differs from the one Home
  Assistant uses.

## Support the project

If this saves you from the EZVIZ app, you can buy me a coffee ☕. Thank you!

<a href="https://www.paypal.com/donate/?business=dmdias%40gmail.com&currency_code=EUR&item_name=EZVIZ+NVR"><img src="https://img.shields.io/badge/Buy%20me%20a%20coffee-Donate%20with%20PayPal-ffdd00?style=for-the-badge&logo=buymeacoffee&logoColor=black" alt="Buy me a coffee"></a>

Bug reports and ideas are welcome in the [issues](https://github.com/dmdias/ha-ezviz-nvr/issues).

## Author

**Daniel Dias** ([@dmdias](https://github.com/dmdias)).

## License

[GPL-3.0](LICENSE).

Not affiliated with EZVIZ or Hikvision; product names are trademarks of
their owners.

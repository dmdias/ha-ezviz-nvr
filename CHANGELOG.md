# Changelog

## 0.1.1

- Responsive camera grid: `columns` is the maximum; tiles keep a usable size and wrap
  to fewer columns (one on phones) instead of shrinking

## 0.1.0

First release.

- Config flow for an EZVIZ / Hikvision NVR (local address, username and the
  device password), checked against the NVR
- Per channel: a live camera and a playback camera; the live stream (main or
  sub) is chosen automatically, or per channel in the options
- `ezviz_nvr.get_recordings` (recorded segments of a day) and
  `ezviz_nvr.play` (move the playback to a time)
- Disk free space and usage sensors
- Thumbnails grabbed from the video when the NVR does not serve pictures for
  IP channels
- EZVIZ NVR card, loaded automatically: camera grid, live / playback player
  and a 24 h timeline of the recordings

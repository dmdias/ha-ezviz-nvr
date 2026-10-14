# Changelog

## 0.4.1

- The card is registered as a dashboard resource (kept up to date by the
  integration) instead of being written into the page when it is served. It
  no longer goes missing ("Configuration error") in the companion apps when
  the page was opened while Home Assistant was starting
- The card can be loaded twice without an error
- The person page no longer breaks the card when the person was merged or deleted

## 0.4.0

- People statistics: visits per day, week or month for each person, in a
  new Statistics view (everyone, with a small chart per person) and on each
  person's page (chart, current period, total and average)
- Visits: sightings of a person less than 15 minutes apart count as one
  visit, so several doorbell events of the same visit count once
- Sightings (time, person, camera, without pictures) are kept for 3 years,
  after the faces themselves are deleted, so the statistics stay
- The People list shows visits instead of events

## 0.3.0

- People: optional face recognition in the events of the EZVIZ cloud cameras
  (doorbells, ...). Faces are grouped by person; the card lists who was seen,
  how often and when, with every occurrence, and lets you name each person
  (the same name merges two people), move a face to another person or delete it
- Runs in Home Assistant on numpy (YuNet and SFace models from the OpenCV Zoo);
  no add-on, no cloud service
- `ezviz_nvr_face_detected` event for automations
- Fix: the card was no longer loaded after the integration options were saved
  (until Home Assistant restarted)
- The card URL changes with the card file, so browsers do not keep an old card

## 0.2.0

- Events of EZVIZ cloud cameras (doorbells, battery cameras) in the card: the
  day's events with their pictures, time and alarm type, through the session
  of the Home Assistant EZVIZ integration
- `ezviz_nvr.get_events` action
- Event pictures are served (and decrypted when needed) by Home Assistant,
  only from the EZVIZ clouds, with signed URLs

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

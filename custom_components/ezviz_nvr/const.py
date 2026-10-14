"""Constants for EZVIZ NVR."""
from typing import Final

DOMAIN: Final = "ezviz_nvr"
VERSION: Final = "0.4.1"

CONF_RTSP_PORT: Final = "rtsp_port"
CONF_TRANSCODE: Final = "transcode"  # never | always
CONF_LIVE_STREAM: Final = "live_stream_{channel}"  # auto | main | sub
SNAPSHOT_CACHE_SECONDS: Final = 25
CONF_FACES: Final = "faces"  # recognise people in the events of the EZVIZ cloud cameras
CONF_FACES_RETENTION: Final = "faces_retention_days"

FRONTEND_URL: Final = "/ezviz_nvr/ezviz-nvr-card.js"

SERVICE_PLAY: Final = "play"
SERVICE_GET_RECORDINGS: Final = "get_recordings"
SERVICE_GET_EVENTS: Final = "get_events"

UPDATE_INTERVAL_SECONDS: Final = 60

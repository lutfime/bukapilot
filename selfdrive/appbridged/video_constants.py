import re
from openpilot.system.hardware.hw import Paths

CHANNEL_VIDEO = 0x03

MEDIA_MOUNT = "/data/media"
REALDATA_ROOT = Paths.log_root()
CACHE_ROOT = "/data/media/0/kommu_video_cache"
MP4_CACHE_DIR = f"{CACHE_ROOT}/mp4"
THUMB_CACHE_DIR = f"{CACHE_ROOT}/thumbs"

MAX_DRIVES = 10
MIN_SEGMENTS_PER_DRIVE = 2

# Video BLE link frames: keep 240 B default in ble_helper (hard limit for phone compatibility).
THUMB_SEND_BURST = 16

MP4_CONVERT_TIMEOUT_SEC = 120.0
VIDEO_HTTP_PORT = 8089
TRANSPORT_EXPIRES_SEC = 600
HOTSPOT_WAIT_SEC = 5.0
WIFI_FALLBACK_GRACE_SEC = 45.0
THUMB_FFMPEG_TIMEOUT_SEC = 10.0
# App BLEService Watchcat RESET_TIMEOUT is 2000 ms; video page messageHz is 2.
VIDEO_KEEPALIVE_PERIOD_SEC = 0.5

# Raw HEVC segments from loggerd need explicit decoder + showall (see tools/lib/framereader.py).
FFMPEG_HEVC_INPUT_ARGS = ["-c:v", "hevc", "-vsync", "0", "-f", "hevc", "-flags2", "showall"]

LIST_PAYLOAD_BUDGET = 55000

SEGMENT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}--\d{2}-\d{2}-\d{2})--(\d+)$")

CAMERA_HEVC = {
  "road": "fcamera.hevc",
  "wide": "ecamera.hevc",
}

MSG = {
  "LIST_REQ": "videoListReq",
  "LIST_RESP": "videoListResp",
  "KEEPALIVE": "videoKeepalive",
  "DRIVE_OPEN": "videoDriveOpen",
  "DRIVE_CLOSE": "videoDriveClose",
  "THUMBNAIL": "videoThumbnail",
  "DOWNLOAD_REQ": "videoDownloadReq",
  "CONVERT_PROGRESS": "videoConvertProgress",
  "HOTSPOT_READY": "videoHotspotReady",
  "DOWNLOAD_TRANSPORT": "videoDownloadTransport",
  "DOWNLOAD_DONE": "videoDownloadDone",
  "DOWNLOAD_CANCEL": "videoDownloadCancel",
  "ERROR": "videoError",
}

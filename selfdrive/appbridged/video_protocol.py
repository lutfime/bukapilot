import msgpack
import os
import secrets
import threading
import time

from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.appbridged.video_constants import (
  CHANNEL_VIDEO,
  HOTSPOT_WAIT_SEC,
  LIST_PAYLOAD_BUDGET,
  MAX_DRIVES,
  MSG,
  MP4_CACHE_DIR,
  THUMB_CACHE_DIR,
  THUMB_SEND_BURST,
  TRANSPORT_EXPIRES_SEC,
  VIDEO_HTTP_PORT,
  WIFI_FALLBACK_GRACE_SEC,
)
from openpilot.selfdrive.appbridged.video_hotspot import (
  compute_hotspot_credentials,
  disable_hotspot,
  enable_hotspot,
  get_hotspot_ip,
  is_hotspot_active,
  is_hotspot_joinable,
)
from openpilot.selfdrive.appbridged.video_http_server import VideoHttpServer
from openpilot.selfdrive.appbridged.video_mp4_cache import (
  Mp4ConvertJob,
  clear_mp4_cache,
  delete_mp4_cache,
  mp4_cache_path,
)
from openpilot.selfdrive.appbridged.video_scanner import (
  resolve_hevc_path,
  scan_drives,
  validate_storage,
)
from openpilot.selfdrive.appbridged.video_thumbnail_cache import (
  ThumbGenerateJob,
  prune_orphan_thumb_cache,
  delete_thumb_cache,
  read_cached_thumb,
  thumb_cache_path,
)


HOTSPOT_WARM_SEC = 45.0


class VideoProtocolHandler:
  def __init__(self, ble, hw_helper):
    self.ble = ble
    self.hw_helper = hw_helper
    self._lock = threading.Lock()
    self._convert_job: Mp4ConvertJob | None = None
    self._next_transfer_id = 1
    self._pending_download: dict | None = None
    self._hotspot_ready_sent_for: int | None = None
    self._cached_drives: list[dict] = []
    self._open_drive_id: str | None = None
    self._thumb_queue: list[dict] = []
    self._thumb_job: ThumbGenerateJob | None = None
    self._thumb_pending: dict | None = None
    self._wifi_session: dict | None = None
    self._http_server: VideoHttpServer | None = None
    self._ensure_http_server()

  def _ensure_http_server(self) -> None:
    if self._http_server is not None:
      return
    server = VideoHttpServer(VIDEO_HTTP_PORT)
    server.on_complete = self._on_wifi_http_complete
    server.on_aborted = self._on_wifi_http_aborted
    server.on_resumed = self._on_wifi_http_resumed
    if server.start():
      self._http_server = server


  @staticmethod
  def _hotspot_base_url() -> str | None:
    try:
      return f"http://{get_hotspot_ip()}:{VIDEO_HTTP_PORT}"
    except OSError:
      return None

  @staticmethod
  def _optional_int(msg: dict, key: str) -> int | None:
    raw = msg.get(key)
    if raw is None:
      return None
    try:
      return int(raw)
    except (TypeError, ValueError):
      return None

  @staticmethod
  def _append_thumb_items(queue: list[dict], drive_id: str, segment: int, seg: dict) -> None:
    if seg.get("hasRoad") and seg.get("roadHevc"):
      queue.append({
        "driveId": drive_id,
        "segment": segment,
        "camera": "road",
        "hevc": seg["roadHevc"],
        "cache": thumb_cache_path(drive_id, segment, "road"),
      })
    if seg.get("hasWide") and seg.get("wideHevc"):
      queue.append({
        "driveId": drive_id,
        "segment": segment,
        "camera": "wide",
        "hevc": seg["wideHevc"],
        "cache": thumb_cache_path(drive_id, segment, "wide"),
      })

  def on_channel_active(self) -> None:
    with self._lock:
      ok, _reason = validate_storage(self.hw_helper)
      if not ok:
        self._cached_drives = []
        return
      if self._ensure_cache_dirs():
        prune_orphan_thumb_cache()
        self._cached_drives = scan_drives(MAX_DRIVES)

  def _reset_ble_session(self) -> None:
    with self._lock:
      self._cancel_thumbs()
      self._abort_active_unlocked()
      self._cached_drives = []

  def on_ble_connected(self) -> None:
    with self._lock:
      if self._wifi_session is not None:
        return
    self._reset_ble_session()

  def on_ble_disconnected(self) -> None:
    with self._lock:
      if self._wifi_session is not None:
        return
    self._reset_ble_session()

  def _send(self, obj: dict) -> bool:
    try:
      payload = msgpack.packb(obj, use_bin_type=True)
      self.ble.chunk_and_send(CHANNEL_VIDEO, payload)
      return True
    except ValueError as e:
      cloudlog.error(f"BLE video send failed: {e}")
      return False
    except Exception as e:
      cloudlog.error(f"video send error: {e}")
      return False

  def _send_error(self, reason: str):
    self._send({"msgType": MSG["ERROR"], "reason": reason})

  def send_list_keepalive(self) -> None:
    # Lightweight keepalive for app Watchcat (RESET_TIMEOUT = 2000 ms).
    # Do not send videoListResp here — the app treats any list response as the
    # result of videoListReq and keepalive would race with a fresh scan.
    ok, _reason = validate_storage(self.hw_helper)
    self._send({
      "msgType": MSG["KEEPALIVE"],
      "videoDlValid": ok,
    })

  def _ensure_cache_dirs(self) -> bool:
    try:
      os.makedirs(MP4_CACHE_DIR, exist_ok=True)
      os.makedirs(THUMB_CACHE_DIR, exist_ok=True)
      return True
    except OSError:
      return False

  def _is_busy(self) -> bool:
    return (
      self._convert_job is not None
      or self._pending_download is not None
      or self._wifi_session is not None
    )

  def _thumbnails_paused(self) -> bool:
    return self._is_busy()

  def is_transfer_active(self) -> bool:
    return self._is_busy()

  def should_pause_video_keepalive(self) -> bool:
    return self._wifi_session is not None


  @staticmethod
  def _matches_download(
    *,
    transfer_id: int | None,
    drive_id: str | None,
    segment: int | None,
    camera: str | None,
    item_transfer_id: int | None = None,
    item_drive_id: str | None = None,
    item_segment: int | None = None,
    item_camera: str | None = None,
  ) -> bool:
    if transfer_id is not None and item_transfer_id == transfer_id:
      return True
    return (
      drive_id is not None
      and item_drive_id == drive_id
      and item_segment == segment
      and item_camera == camera
    )

  def _cleanup_wifi_session_unlocked(self, *, delete_mp4: bool = True, disable_hotspot_on_cleanup: bool = True) -> None:
    session = self._wifi_session
    self._wifi_session = None
    if not session:
      return
    transfer_id = session.get("transferId")
    if self._http_server and transfer_id is not None:
      self._http_server.unregister(transfer_id)
    mp4 = session.get("mp4")
    if delete_mp4 and mp4 is not None:
      delete_mp4_cache(mp4)
    if disable_hotspot_on_cleanup and session.get("hotspot_started_for_transfer"):
      finished_at = session.get("http_finished_at")
      keep_warm = finished_at is not None and (time.monotonic() - finished_at) < HOTSPOT_WARM_SEC
      if not keep_warm:
        disable_hotspot()

  def _abort_active_unlocked(self):
    pending = self._pending_download
    convert_job = self._convert_job
    if self._wifi_session:
      self._cleanup_wifi_session_unlocked(delete_mp4=True)
    elif convert_job:
      convert_job.cancel()
      delete_mp4_cache(pending.get("mp4") if pending else None)
    elif pending:
      delete_mp4_cache(pending.get("mp4"))
    self._convert_job = None
    self._pending_download = None
    self._hotspot_ready_sent_for = None

  def _cancel_download(self, transfer_id: int | None = None, *, drive_id: str | None = None, segment: int | None = None, camera: str | None = None) -> bool:
    cancelled = False
    wifi = self._wifi_session
    if wifi and self._matches_download(
      transfer_id=transfer_id,
      drive_id=drive_id,
      segment=segment,
      camera=camera,
      item_transfer_id=wifi.get("transferId"),
      item_drive_id=wifi.get("driveId"),
      item_segment=wifi.get("segment"),
      item_camera=wifi.get("camera"),
    ):
      self._cleanup_wifi_session_unlocked(delete_mp4=True, disable_hotspot_on_cleanup=False)
      cancelled = True
    pending = self._pending_download
    if pending and self._matches_download(
      transfer_id=transfer_id,
      drive_id=drive_id,
      segment=segment,
      camera=camera,
      item_transfer_id=pending.get("transferId"),
      item_drive_id=pending.get("driveId"),
      item_segment=pending.get("segment"),
      item_camera=pending.get("camera"),
    ):
      if self._convert_job:
        self._convert_job.cancel()
      delete_mp4_cache(pending.get("mp4"))
      self._convert_job = None
      self._pending_download = None
      cancelled = True
    return cancelled

  def _cancel_thumbs(self):
    self._open_drive_id = None
    self._thumb_queue.clear()
    self._thumb_job = None
    self._thumb_pending = None

  def _send_thumbnail(self, drive_id: str, segment: int, camera: str, thumbnail_jpeg: bytes) -> bool:
    return self._send({
      "msgType": MSG["THUMBNAIL"],
      "driveId": drive_id,
      "segment": segment,
      "camera": camera,
      "thumbnailJpeg": thumbnail_jpeg,
    })

  def _drop_thumb_item(self, item: dict | None) -> None:
    if not item:
      return
    self._thumb_queue = [
      x for x in self._thumb_queue
      if not (x["driveId"] == item["driveId"] and x["segment"] == item["segment"] and x["camera"] == item["camera"])
    ]

  def _send_ready_cached_thumbs(self) -> bool:
    """Send cached thumbnails from anywhere in the queue (order-independent on app side)."""
    sent = 0
    i = 0
    while i < len(self._thumb_queue):
      item = self._thumb_queue[i]
      if not (jpeg := read_cached_thumb(item["hevc"], item["cache"])):
        i += 1
        continue
      if not self._send_thumbnail(item["driveId"], item["segment"], item["camera"], jpeg):
        return False
      self._thumb_queue.pop(i)
      sent += 1
      if THUMB_SEND_BURST and sent >= THUMB_SEND_BURST:
        break
    return True

  def _maybe_start_thumb_job(self) -> None:
    if self._thumb_job or not self._open_drive_id:
      return
    for item in self._thumb_queue:
      if read_cached_thumb(item["hevc"], item["cache"]):
        continue
      self._thumb_pending = item
      self._thumb_job = ThumbGenerateJob(item["hevc"], item["cache"])
      return

  def _advance_thumbnails(self) -> None:
    if not self._open_drive_id or self._thumbnails_paused():
      return

    if self._thumb_job:
      done, ok = self._thumb_job.poll()
      if done:
        item = self._thumb_pending
        self._thumb_job = None
        self._thumb_pending = None
        if item:
          if not ok:
            if not os.path.isfile(item["hevc"]):
              delete_thumb_cache(item["cache"])
            self._drop_thumb_item(item)

    if not self._send_ready_cached_thumbs():
      return
    self._maybe_start_thumb_job()

  def _build_list_response(
    self,
    drives: list[dict],
    video_dl_valid: bool = True,
  ) -> dict:
    out_drives = []
    for drive in drives:
      drive_id = drive["driveId"]
      segments_out = []
      for seg in drive["segments"]:
        segment = seg["segment"]
        item: dict = {
          "segment": segment,
          "hasRoad": seg["hasRoad"],
          "hasWide": seg["hasWide"],
        }
        segments_out.append(item)
      out_drives.append({"driveId": drive_id, "segments": segments_out})

    resp = {"msgType": MSG["LIST_RESP"], "videoDlValid": video_dl_valid, "drives": out_drives}
    while out_drives:
      if len(msgpack.packb(resp)) <= LIST_PAYLOAD_BUDGET:
        break
      out_drives.pop()
      resp["drives"] = out_drives
      if not out_drives:
        break
    return resp

  def _handle_list_req(self, msg: dict):
    ok, _reason = validate_storage(self.hw_helper)
    if not ok:
      self._send({
        "msgType": MSG["LIST_RESP"],
        "videoDlValid": False,
        "drives": [],
      })
      return
    if not self._ensure_cache_dirs():
      self._send({
        "msgType": MSG["LIST_RESP"],
        "videoDlValid": False,
        "drives": [],
      })
      return

    prune_orphan_thumb_cache()
    drives = scan_drives(MAX_DRIVES)
    self._cached_drives = drives
    if not self._is_busy():
      clear_mp4_cache()
    self._send(self._build_list_response(drives))

  def _handle_drive_open(self, msg: dict):
    ok, _reason = validate_storage(self.hw_helper)
    if not ok:
      self._send_error("sd_invalid")
      return
    if not self._ensure_cache_dirs():
      self._send_error("realdata_unavailable")
      return

    drive_id = str(msg.get("driveId") or "").strip()
    if not drive_id:
      self._send_error("drive_not_found")
      return

    self._cancel_thumbs()
    drives = self._cached_drives or scan_drives(MAX_DRIVES)
    drive = next((d for d in drives if d["driveId"] == drive_id), None)
    if not drive:
      self._send_error("drive_not_found")
      return

    self._open_drive_id = drive_id
    queue: list[dict] = []
    for seg in drive["segments"]:
      self._append_thumb_items(queue, drive_id, seg["segment"], seg)
    self._thumb_queue = queue
    self._advance_thumbnails()

  def _handle_drive_close(self):
    self._cancel_thumbs()

  def _start_download(self, drive_id: str, segment: int, camera: str):
    self._cancel_thumbs()
    ok, reason = validate_storage(self.hw_helper)
    if not ok:
      self._send_error(reason or "sd_invalid")
      return
    if self._is_busy():
      self._send_error("busy")
      return
    if camera not in ("road", "wide"):
      self._send_error("camera_not_found")
      return
    if not (hevc := resolve_hevc_path(drive_id, segment, camera)):
      self._send_error("camera_not_found")
      return
    if not self._ensure_cache_dirs():
      self._send_error("realdata_unavailable")
      return

    mp4_path = mp4_cache_path(drive_id, segment, camera)
    if not is_hotspot_active():
      enable_hotspot()
    self._hotspot_ready_sent_for = None
    transfer_id = self._next_transfer_id
    self._next_transfer_id += 1
    self._pending_download = {
      "transferId": transfer_id,
      "driveId": drive_id,
      "segment": segment,
      "camera": camera,
      "hevc": str(hevc),
      "mp4": mp4_path,
    }
    self._convert_job = Mp4ConvertJob(str(hevc), mp4_path)
    self._send_convert_progress(0)
    self._try_send_hotspot_ready(time.monotonic())


  def _try_send_hotspot_ready(self, cur_time: float) -> None:
    if not (pending := self._pending_download) or not self._convert_job:
      return
    transfer_id = pending["transferId"]
    if self._hotspot_ready_sent_for == transfer_id:
      return
    if not is_hotspot_joinable():
      return
    ssid, password = compute_hotspot_credentials()
    if not (base_url := self._hotspot_base_url()):
      return
    hotspot_ip = base_url.rsplit(":", 1)[0].removeprefix("http://")
    sent = self._send({
      "msgType": MSG["HOTSPOT_READY"],
      "transferId": transfer_id,
      "driveId": pending["driveId"],
      "segment": pending["segment"],
      "camera": pending["camera"],
      "ssid": ssid,
      "password": password,
      "hotspotIp": hotspot_ip,
      "baseUrl": base_url,
    })
    if sent:
      self._hotspot_ready_sent_for = transfer_id
      cloudlog.info(
        f"video hotspot ready sent transfer={transfer_id} ip={hotspot_ip} "
        f"during_convert={self._convert_job is not None}"
      )

  def _send_convert_progress(self, percent: int) -> None:
    if not (pending := self._pending_download):
      return
    progress = max(0, min(100, int(percent)))
    self._send({
      "msgType": MSG["CONVERT_PROGRESS"],
      "transferId": pending["transferId"],
      "driveId": pending["driveId"],
      "segment": pending["segment"],
      "camera": pending["camera"],
      "progress": progress,
      "progressPercent": progress,
    })

  def _try_finish_convert(self, cur_time: float):
    if not self._convert_job or not self._pending_download:
      return
    done, ok, err = self._convert_job.poll()
    if not done:
      return

    pending = self._pending_download
    self._convert_job = None
    if not pending:
      return

    if not ok:
      self._pending_download = None
      delete_mp4_cache(pending["mp4"])
      if err != "conversion_cancelled":
        self._send_error(err or "conversion_failed")
      return

    mp4_path = pending["mp4"]
    if not mp4_path.is_file() or mp4_path.stat().st_size <= 0:
      self._pending_download = None
      delete_mp4_cache(mp4_path)
      self._send_error("file_not_ready")
      return

    self._send_convert_progress(100)
    self._pending_download = None
    self._begin_wifi_download(pending, mp4_path, cur_time)

  def _begin_wifi_download(self, pending: dict, mp4_path, cur_time: float) -> None:
    hotspot_was_active = is_hotspot_active()
    ssid, password = compute_hotspot_credentials()
    if not hotspot_was_active:
      enable_hotspot()
    self._wifi_session = {
      "transferId": pending["transferId"],
      "driveId": pending["driveId"],
      "segment": pending["segment"],
      "camera": pending["camera"],
      "mp4": mp4_path,
      "ssid": ssid,
      "password": password,
      "totalBytes": mp4_path.stat().st_size,
      "phase": "waiting_hotspot" if not hotspot_was_active else "ready_hotspot",
      "started_at": cur_time,
      "hotspot_started_for_transfer": not hotspot_was_active,
      "transport_sent": False,
    }
    cloudlog.info(
      f"video wifi begin transfer={pending['transferId']} "
      f"phase={self._wifi_session['phase']} hotspot_was_active={hotspot_was_active} "
      f"bytes={self._wifi_session['totalBytes']}"
    )

  def _send_wifi_transport(self, session: dict, cur_time: float) -> bool:
    if session.get("transport_sent"):
      return True
    if not is_hotspot_joinable():
      return False
    if not self._http_server:
      return False
    transfer_id = session["transferId"]
    if not (base_url := self._hotspot_base_url()):
      return False
    hotspot_ip = base_url.rsplit(":", 1)[0].removeprefix("http://")
    token = secrets.token_urlsafe(24)
    expires_at = time.monotonic() + TRANSPORT_EXPIRES_SEC
    self._http_server.register(transfer_id, token, session["mp4"], expires_at)
    sent = self._send({
      "msgType": MSG["DOWNLOAD_TRANSPORT"],
      "transferId": transfer_id,
      "driveId": session["driveId"],
      "segment": session["segment"],
      "camera": session["camera"],
      "baseUrl": base_url,
      "token": token,
      "ssid": session["ssid"],
      "password": session["password"],
      "totalBytes": session["totalBytes"],
      "expiresSec": TRANSPORT_EXPIRES_SEC,
    })
    if not sent:
      self._http_server.unregister(transfer_id)
      return False
    session["token"] = token
    session["transport_sent"] = True
    session["transport_sent_at"] = cur_time
    session["download_start_deadline"] = cur_time + WIFI_FALLBACK_GRACE_SEC
    session["expires_at"] = cur_time + TRANSPORT_EXPIRES_SEC
    session["phase"] = "awaiting_http"
    cloudlog.info(
      f"video wifi transport sent transfer={transfer_id} "
      f"bytes={session.get('totalBytes')} ip={hotspot_ip} grace_sec={WIFI_FALLBACK_GRACE_SEC}"
    )
    return True


  def _fail_wifi_session(self, cur_time: float, reason: str = "wifi_download_failed") -> None:
    if not (session := self._wifi_session):
      return
    transfer_id = session["transferId"]
    mp4_path = session["mp4"]
    if self._http_server:
      self._http_server.unregister(transfer_id)
    hotspot_started = session.get("hotspot_started_for_transfer")
    self._wifi_session = None
    delete_mp4_cache(mp4_path)
    if is_hotspot_active() and hotspot_started:
      disable_hotspot()
    self._send_error(reason)

  def _finish_wifi_transfer_success(self) -> None:
    if not (session := self._wifi_session):
      return
    transfer_id = session["transferId"]
    mp4_path = session["mp4"]
    self._wifi_session = None
    if self._http_server:
      self._http_server.unregister(transfer_id)
    delete_mp4_cache(mp4_path)
    if is_hotspot_active():
      finished_at = session.get("http_finished_at")
      keep_warm = finished_at is not None and (time.monotonic() - finished_at) < HOTSPOT_WARM_SEC
      if not keep_warm and session.get("hotspot_started_for_transfer"):
        disable_hotspot()
    self._send({"msgType": MSG["DOWNLOAD_DONE"], "transferId": transfer_id})
    self.send_list_keepalive()

  def _on_wifi_http_resumed(self, transfer_id: int, start: int, total: int) -> None:
    with self._lock:
      if (session := self._wifi_session) and session.get("transferId") == transfer_id:
        session.pop("http_aborted", None)
        session.pop("http_aborted_at", None)
        session["http_abort_grace_sec"] = 25.0 if total > 0 and start >= total * 0.95 else 20.0

  def _on_wifi_http_complete(self, transfer_id: int, _total_bytes: int) -> None:
    with self._lock:
      if (session := self._wifi_session) and session.get("transferId") == transfer_id:
        session["http_finished_at"] = time.monotonic()
        session["phase"] = "complete"

  def _on_wifi_http_aborted(self, transfer_id: int) -> None:
    with self._lock:
      if (session := self._wifi_session) and session.get("transferId") == transfer_id:
        session["http_aborted"] = True
        session["http_aborted_at"] = time.monotonic()
        started = self._http_server and self._http_server.is_started(transfer_id)
        session["http_abort_grace_sec"] = 25.0 if started else 8.0

  def _tick_wifi_session(self, cur_time: float) -> None:
    if not (session := self._wifi_session):
      return
    phase = session.get("phase")
    if phase == "complete":
      return self._finish_wifi_transfer_success()
    if phase == "waiting_hotspot":
      if not is_hotspot_joinable() and (cur_time - session["started_at"]) < HOTSPOT_WAIT_SEC:
        return
      if not is_hotspot_joinable():
        return self._fail_wifi_session(cur_time)
      session["phase"] = "ready_hotspot"
      phase = "ready_hotspot"
    if phase == "ready_hotspot":
      if self._send_wifi_transport(session, cur_time):
        return
      return self._fail_wifi_session(cur_time)
    if phase != "awaiting_http":
      return
    tid = session["transferId"]
    if session.get("http_aborted"):
      join_deadline = session.get("download_start_deadline", cur_time)
      if cur_time < join_deadline:
        session.pop("http_aborted", None)
        session.pop("http_aborted_at", None)
        return
      aborted_at = session.get("http_aborted_at", cur_time)
      grace = session.get("http_abort_grace_sec", 8.0)
      if cur_time - aborted_at < grace:
        return
      return self._fail_wifi_session(cur_time)
    if self._http_server and self._http_server.is_complete(tid):
      session["phase"] = "complete"
      return self._finish_wifi_transfer_success()
    if cur_time > session.get("expires_at", cur_time):
      return self._fail_wifi_session(cur_time)
    if cur_time <= session.get("download_start_deadline", cur_time):
      return
    if self._http_server and not self._http_server.is_started(tid):
      return self._fail_wifi_session(cur_time)

  def _handle_download_req(self, msg: dict):
    drive_id = str(msg.get("driveId") or "")
    try:
      segment = int(msg.get("segment"))
    except (TypeError, ValueError):
      self._send_error("segment_not_found")
      return
    camera = str(msg.get("camera") or "")
    if not drive_id:
      self._send_error("drive_not_found")
      return
    self._start_download(drive_id, segment, camera)

  def _handle_cancel(self, msg: dict):
    transfer_id = self._optional_int(msg, "transferId")
    drive_id = str(msg.get("driveId") or "").strip() or None
    camera = str(msg.get("camera") or "").strip() or None
    segment = self._optional_int(msg, "segment")
    if transfer_id is None and (drive_id is None or camera is None or segment is None):
      return
    self._cancel_download(transfer_id, drive_id=drive_id, segment=segment, camera=camera)

  def handle_message(self, msg: dict, cur_time: float):
    if not msg or not msg.get("msgType"):
      return
    msg_type = msg.get("msgType")
    with self._lock:
      if msg_type == MSG["LIST_REQ"]:
        self._handle_list_req(msg)
      elif msg_type == MSG["DRIVE_OPEN"]:
        self._handle_drive_open(msg)
      elif msg_type == MSG["DRIVE_CLOSE"]:
        self._handle_drive_close()
      elif msg_type == MSG["DOWNLOAD_REQ"]:
        self._handle_download_req(msg)
      elif msg_type == MSG["DOWNLOAD_CANCEL"]:
        self._handle_cancel(msg)

  def tick(self, cur_time: float):
    with self._lock:
      ok, _ = validate_storage(self.hw_helper)
      if not ok and (self._convert_job or self._pending_download or self._wifi_session):
        had_active = bool(self._convert_job or self._pending_download or self._wifi_session)
        self._abort_active_unlocked()
        if had_active:
          self._send_error("transfer_timeout")
        return

      self._try_finish_convert(cur_time)
      self._try_send_hotspot_ready(cur_time)
      self._tick_wifi_session(cur_time)
      self._advance_thumbnails()

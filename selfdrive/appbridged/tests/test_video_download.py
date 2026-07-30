import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from openpilot.selfdrive.appbridged.video_constants import MSG
from openpilot.selfdrive.appbridged.video_protocol import VideoProtocolHandler
from openpilot.selfdrive.appbridged.video_mp4_cache import delete_mp4_cache
from openpilot.selfdrive.appbridged.video_scanner import get_drive, scan_drives, validate_storage
from openpilot.selfdrive.appbridged.video_thumbnail_cache import delete_thumb_cache

class TestVideoScanner(unittest.TestCase):
  def test_scan_drives_groups_and_sorts(self):
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      for name in ("boot", "badname", "2026-07-07--08-29-18--0", "2026-07-07--08-29-18--2", "2026-07-07--08-34-32--0"):
        (root / name).mkdir()
      (root / "2026-07-07--08-29-18--0" / "fcamera.hevc").write_bytes(b"x")
      (root / "2026-07-07--08-29-18--2" / "fcamera.hevc").write_bytes(b"x")
      (root / "2026-07-07--08-34-32--0" / "ecamera.hevc").write_bytes(b"x")

      import openpilot.selfdrive.appbridged.video_scanner as scanner
      old = scanner.REALDATA_ROOT
      scanner.REALDATA_ROOT = str(root)
      try:
        drives = scan_drives(10)
      finally:
        scanner.REALDATA_ROOT = old

      self.assertEqual(len(drives), 1)
      self.assertEqual(drives[0]["driveId"], "2026-07-07--08-29-18")
      self.assertEqual(len(drives[0]["segments"]), 2)
      self.assertTrue(drives[0]["segments"][0]["hasRoad"])

  def test_validate_storage_sd_invalid(self):
    hw = MagicMock()
    hw.get_sd_status.return_value = "SD card not inserted"
    ok, reason = validate_storage(hw)
    self.assertFalse(ok)
    self.assertEqual(reason, "sd_invalid")

  def test_get_drive_filters_by_id(self):
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      (root / "2026-07-07--08-29-18--0").mkdir()
      (root / "2026-07-07--08-29-18--0" / "fcamera.hevc").write_bytes(b"x")
      (root / "2026-07-07--08-29-18--1").mkdir()
      (root / "2026-07-07--08-29-18--1" / "fcamera.hevc").write_bytes(b"x")
      (root / "2026-07-07--08-34-32--0").mkdir()
      (root / "2026-07-07--08-34-32--0" / "ecamera.hevc").write_bytes(b"x")

      import openpilot.selfdrive.appbridged.video_scanner as scanner
      old = scanner.REALDATA_ROOT
      scanner.REALDATA_ROOT = str(root)
      try:
        self.assertIsNone(get_drive("2026-07-07--08-34-32"))
        drive = get_drive("2026-07-07--08-29-18")
        self.assertIsNotNone(drive)
        assert drive is not None
        self.assertEqual(drive["driveId"], "2026-07-07--08-29-18")
        self.assertIsNone(get_drive("missing-drive"))
      finally:
        scanner.REALDATA_ROOT = old


class TestVideoProtocol(unittest.TestCase):
  def test_list_response_has_no_thumbnails(self):
    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    drives = [{
      "driveId": "2026-07-07--08-29-18",
      "segments": [{"segment": 0, "hasRoad": True, "hasWide": False}],
    }]
    resp = handler._build_list_response(drives)
    self.assertEqual(resp["msgType"], MSG["LIST_RESP"])
    seg = resp["drives"][0]["segments"][0]
    self.assertNotIn("roadThumbnailBase64", seg)
    self.assertNotIn("wideThumbnailBase64", seg)

  @patch("openpilot.selfdrive.appbridged.video_protocol.read_cached_thumb")
  def test_drive_open_sends_cached_thumbnails(self, read_cached_thumb):
    read_cached_thumb.side_effect = lambda _hevc, _cache: b"thumb-data"
    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    handler._cached_drives = [{
      "driveId": "2026-07-07--08-29-18",
      "segments": [{
        "segment": 0,
        "hasRoad": True,
        "hasWide": True,
        "roadHevc": "/road.hevc",
        "wideHevc": "/wide.hevc",
      }],
    }]
    with patch.object(handler, "_send", return_value=True) as send:
      with patch.object(handler, "_ensure_cache_dirs", return_value=True):
        with patch("openpilot.selfdrive.appbridged.video_protocol.validate_storage", return_value=(True, None)):
          handler._handle_drive_open({"driveId": "2026-07-07--08-29-18"})

      thumb_msgs = [c.args[0] for c in send.call_args_list if c.args[0].get("msgType") == MSG["THUMBNAIL"]]
      self.assertEqual(len(thumb_msgs), 2)
      self.assertEqual(thumb_msgs[0]["camera"], "road")
      self.assertEqual(thumb_msgs[1]["camera"], "wide")
      self.assertEqual(thumb_msgs[0]["thumbnailJpeg"], "thumb-data")

  @patch("openpilot.selfdrive.appbridged.video_protocol.ThumbGenerateJob")
  @patch("openpilot.selfdrive.appbridged.video_protocol.read_cached_thumb")
  @patch("openpilot.selfdrive.appbridged.video_protocol.delete_thumb_cache")
  def test_drive_open_generates_thumbnails_one_by_one(self, delete_thumb_cache, read_cached_thumb, ThumbGenerateJob):
    road_ready = [False]
    wide_ready = [False]

    def read_thumb(hevc, _cache):
      if hevc == "/road0.hevc" and road_ready[0]:
        return b"thumb-road0"
      if hevc == "/wide0.hevc" and wide_ready[0]:
        return b"thumb-wide0"
      return None

    read_cached_thumb.side_effect = read_thumb
    job1 = MagicMock()
    job1.poll.side_effect = [(False, False), (True, True)]
    job2 = MagicMock()
    job2.poll.side_effect = [(False, False), (True, True)]
    ThumbGenerateJob.side_effect = [job1, job2]

    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    handler._cached_drives = [{
      "driveId": "2026-07-07--08-29-18",
      "segments": [{
        "segment": 0,
        "hasRoad": True,
        "hasWide": True,
        "roadHevc": "/road0.hevc",
        "wideHevc": "/wide0.hevc",
      }],
    }]
    with patch.object(handler, "_send", return_value=True) as send:
      with patch.object(handler, "_ensure_cache_dirs", return_value=True):
        with patch("openpilot.selfdrive.appbridged.video_protocol.validate_storage", return_value=(True, None)):
          handler._handle_drive_open({"driveId": "2026-07-07--08-29-18"})
          self.assertEqual(ThumbGenerateJob.call_count, 1)
          handler.tick(0.0)
          self.assertEqual(send.call_count, 0)
          road_ready[0] = True
          handler.tick(1.0)
          thumb_msgs = [c.args[0] for c in send.call_args_list if c.args[0].get("msgType") == MSG["THUMBNAIL"]]
          self.assertEqual(len(thumb_msgs), 1)
          self.assertEqual(thumb_msgs[0]["thumbnailJpeg"], "thumb-road0")
          self.assertEqual(ThumbGenerateJob.call_count, 2)
          self.assertIsNotNone(handler._thumb_job)

          handler.tick(2.0)
          thumb_msgs = [c.args[0] for c in send.call_args_list if c.args[0].get("msgType") == MSG["THUMBNAIL"]]
          self.assertEqual(len(thumb_msgs), 1)
          self.assertEqual(ThumbGenerateJob.call_count, 2)

          wide_ready[0] = True
          handler.tick(3.0)
          thumb_msgs = [c.args[0] for c in send.call_args_list if c.args[0].get("msgType") == MSG["THUMBNAIL"]]
          self.assertEqual(len(thumb_msgs), 2)
          self.assertEqual(thumb_msgs[1]["thumbnailJpeg"], "thumb-wide0")
          self.assertEqual(ThumbGenerateJob.call_count, 2)

          handler.tick(4.0)
          thumb_msgs = [c.args[0] for c in send.call_args_list if c.args[0].get("msgType") == MSG["THUMBNAIL"]]
          self.assertEqual(len(thumb_msgs), 2)
          delete_thumb_cache.assert_not_called()
          self.assertIsNone(handler._thumb_job)

  @patch("openpilot.selfdrive.appbridged.video_protocol.ThumbGenerateJob")
  @patch("openpilot.selfdrive.appbridged.video_protocol.read_cached_thumb")
  def test_sends_cached_thumbnails_while_generating(self, read_cached_thumb, ThumbGenerateJob):
    read_cached_thumb.side_effect = lambda hevc, _cache: b"wide-thumb" if hevc == "/wide0.hevc" else None
    job = MagicMock()
    job.poll.return_value = (False, False)
    ThumbGenerateJob.return_value = job

    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    handler._cached_drives = [{
      "driveId": "2026-07-07--08-29-18",
      "segments": [{
        "segment": 0,
        "hasRoad": True,
        "hasWide": True,
        "roadHevc": "/road0.hevc",
        "wideHevc": "/wide0.hevc",
      }],
    }]
    with patch.object(handler, "_send", return_value=True) as send:
      with patch.object(handler, "_ensure_cache_dirs", return_value=True):
        with patch("openpilot.selfdrive.appbridged.video_protocol.validate_storage", return_value=(True, None)):
          handler._handle_drive_open({"driveId": "2026-07-07--08-29-18"})

    thumb_msgs = [c.args[0] for c in send.call_args_list if c.args[0].get("msgType") == MSG["THUMBNAIL"]]
    self.assertEqual(len(thumb_msgs), 1)
    self.assertEqual(thumb_msgs[0]["camera"], "wide")
    self.assertEqual(ThumbGenerateJob.call_count, 1)
    self.assertIsNotNone(handler._thumb_job)
    self.assertEqual(len(handler._thumb_queue), 1)
    self.assertEqual(handler._thumb_queue[0]["camera"], "road")

  def test_drive_close_cancels_thumbnail_queue(self):
    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    handler._open_drive_id = "2026-07-07--08-29-18"
    handler._thumb_queue = [{"driveId": "2026-07-07--08-29-18", "segment": 0, "camera": "road"}]
    handler._handle_drive_close()
    self.assertIsNone(handler._open_drive_id)
    self.assertEqual(handler._thumb_queue, [])
    self.assertIsNone(handler._thumb_job)

  @patch("openpilot.selfdrive.appbridged.video_protocol.delete_thumb_cache")
  @patch("openpilot.selfdrive.appbridged.video_protocol.read_cached_thumb")
  def test_cached_thumbnail_is_retained_after_send(self, read_cached_thumb, delete_thumb_cache):
    read_cached_thumb.return_value = b"thumb-data"
    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    handler._cached_drives = [{
      "driveId": "2026-07-07--08-29-18",
      "segments": [{
        "segment": 0,
        "hasRoad": True,
        "hasWide": False,
        "roadHevc": "/road.hevc",
      }],
    }]
    with patch.object(handler, "_send", return_value=True):
      with patch.object(handler, "_ensure_cache_dirs", return_value=True):
        with patch("openpilot.selfdrive.appbridged.video_protocol.validate_storage", return_value=(True, None)):
          handler._handle_drive_open({"driveId": "2026-07-07--08-29-18"})
    delete_thumb_cache.assert_not_called()

  @patch("openpilot.selfdrive.appbridged.video_protocol.ThumbGenerateJob")
  @patch("openpilot.selfdrive.appbridged.video_protocol.read_cached_thumb")
  @patch("openpilot.selfdrive.appbridged.video_protocol.delete_thumb_cache")
  def test_thumbnail_send_retries_after_temporary_failure(self, delete_thumb_cache, read_cached_thumb, ThumbGenerateJob):
    road_ready = [False]

    def read_thumb(hevc, _cache):
      return b"thumb-road0" if hevc == "/road0.hevc" and road_ready[0] else None

    read_cached_thumb.side_effect = read_thumb
    job1 = MagicMock()
    job1.poll.side_effect = [(False, False), (True, True)]
    ThumbGenerateJob.side_effect = [job1]

    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    handler._cached_drives = [{
      "driveId": "2026-07-07--08-29-18",
      "segments": [{
        "segment": 0,
        "hasRoad": True,
        "hasWide": False,
        "roadHevc": "/road0.hevc",
      }],
    }]

    send_results = [False, True]
    with patch.object(handler, "_send_thumbnail", side_effect=lambda *_args: send_results.pop(0)) as send_thumb:
      with patch.object(handler, "_ensure_cache_dirs", return_value=True):
        with patch("openpilot.selfdrive.appbridged.video_protocol.validate_storage", return_value=(True, None)):
          handler._handle_drive_open({"driveId": "2026-07-07--08-29-18"})
          handler.tick(0.0)
          road_ready[0] = True
          handler.tick(1.0)
          handler.tick(2.0)
          self.assertEqual(send_thumb.call_count, 2)
          delete_thumb_cache.assert_not_called()
          self.assertEqual(len(handler._thumb_queue), 0)


  @patch("openpilot.selfdrive.appbridged.video_protocol.read_cached_thumb", return_value=b"thumb")
  def test_thumbnails_paused_during_download(self, _read_cached_thumb):
    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    handler._open_drive_id = "2026-07-07--08-29-18"
    handler._thumb_queue = [{
      "driveId": "2026-07-07--08-29-18",
      "segment": 0,
      "camera": "road",
      "hevc": "/road.hevc",
      "cache": Path("/cache.jpg"),
    }]
    handler._pending_download = {"driveId": "2026-07-07--08-29-18"}
    with patch.object(handler, "_send", return_value=True) as send:
      handler._advance_thumbnails()
      thumb_msgs = [c.args[0] for c in send.call_args_list if c.args[0].get("msgType") == MSG["THUMBNAIL"]]
      self.assertEqual(thumb_msgs, [])

  @patch("openpilot.selfdrive.appbridged.video_protocol.Mp4ConvertJob")
  @patch("openpilot.selfdrive.appbridged.video_protocol.resolve_hevc_path")
  def test_download_req_sends_convert_progress(self, resolve_hevc_path, Mp4ConvertJob):
    resolve_hevc_path.return_value = Path("/fake.hevc")
    Mp4ConvertJob.return_value = MagicMock()
    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    with patch.object(handler, "_ensure_cache_dirs", return_value=True):
      with patch.object(handler, "_send", return_value=True) as send:
        with patch("openpilot.selfdrive.appbridged.video_protocol.validate_storage", return_value=(True, None)):
          handler._handle_download_req({
            "driveId": "2026-07-07--09-10-31",
            "segment": 0,
            "camera": "road",
          })
    payload = send.call_args.args[0]
    self.assertEqual(payload["msgType"], MSG["CONVERT_PROGRESS"])
    self.assertEqual(payload["transferId"], 1)
    self.assertEqual(payload["progress"], 0)
    self.assertEqual(payload["progressPercent"], 0)
    self.assertEqual(handler._pending_download["transferId"], 1)

  @patch("openpilot.selfdrive.appbridged.video_protocol.enable_hotspot")
  @patch("openpilot.selfdrive.appbridged.video_protocol.is_hotspot_active", return_value=False)
  @patch("openpilot.selfdrive.appbridged.video_protocol.compute_hotspot_credentials", return_value=("ssid", "pass"))
  def test_convert_finish_sends_progress_100_before_wifi(self, _creds, _active, _enable):
    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    handler._pending_download = {
      "transferId": 2,
      "driveId": "2026-07-07--09-10-31",
      "segment": 0,
      "camera": "road",
      "mp4": Path("/tmp/test.mp4"),
    }
    job = MagicMock()
    job.poll.return_value = (True, True, None)
    handler._convert_job = job

    with tempfile.NamedTemporaryFile(suffix=".mp4") as tmp:
      tmp.write(b"x" * 128)
      tmp.flush()
      handler._pending_download["mp4"] = Path(tmp.name)
      with patch.object(handler, "_send", return_value=True) as send:
        handler._try_finish_convert(0.0)

    payloads = [c.args[0] for c in send.call_args_list]
    progress_msgs = [p for p in payloads if p.get("msgType") == MSG["CONVERT_PROGRESS"]]
    self.assertEqual(len(progress_msgs), 1)
    self.assertEqual(progress_msgs[0]["progress"], 100)
    self.assertEqual(progress_msgs[0]["progressPercent"], 100)
    self.assertIsNone(handler._pending_download)
    self.assertIsNotNone(handler._wifi_session)
    self.assertEqual(handler._wifi_session["transferId"], 2)

  def test_should_pause_video_keepalive_during_wifi_download(self):
    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    handler._pending_download = {"transferId": 1}
    self.assertFalse(handler.should_pause_video_keepalive())
    handler._wifi_session = {"transferId": 1}
    self.assertTrue(handler.should_pause_video_keepalive())

  @patch("openpilot.selfdrive.appbridged.video_protocol.delete_mp4_cache")
  def test_download_cancel_aborts_wifi_session(self, delete_mp4_cache):
    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    mp4 = Path("/tmp/wifi.mp4")
    handler._wifi_session = {
      "transferId": 3,
      "driveId": "drive",
      "segment": 0,
      "camera": "road",
      "mp4": mp4,
    }
    handler._handle_cancel({"transferId": 3})
    self.assertIsNone(handler._wifi_session)
    delete_mp4_cache.assert_called_once_with(mp4)

  @patch("openpilot.selfdrive.appbridged.video_protocol.delete_mp4_cache")
  def test_download_cancel_aborts_convert(self, delete_mp4_cache):
    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    mp4 = Path("/tmp/convert.mp4")
    handler._convert_job = MagicMock()
    handler._pending_download = {
      "transferId": 2,
      "mp4": mp4,
    }
    handler._handle_cancel({"transferId": 2})
    self.assertIsNone(handler._convert_job)
    self.assertIsNone(handler._pending_download)
    delete_mp4_cache.assert_called_once_with(mp4)

  def test_download_cancel_ignores_wrong_transfer_id(self):
    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    handler._wifi_session = {
      "transferId": 3,
      "driveId": "drive",
      "segment": 0,
      "camera": "road",
      "mp4": Path("/tmp/a.mp4"),
    }
    handler._pending_download = {"transferId": 2, "mp4": Path("/tmp/a.mp4")}
    handler._convert_job = MagicMock()
    handler._handle_cancel({"transferId": 9})
    self.assertIsNotNone(handler._wifi_session)
    self.assertIsNotNone(handler._pending_download)

  @patch("openpilot.selfdrive.appbridged.video_protocol.clear_mp4_cache")
  def test_list_req_clears_mp4_cache_when_idle(self, clear_mp4_cache):
    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    with patch("openpilot.selfdrive.appbridged.video_protocol.validate_storage", return_value=(True, None)):
      with patch.object(handler, "_ensure_cache_dirs", return_value=True):
        with patch("openpilot.selfdrive.appbridged.video_protocol.scan_drives", return_value=[]):
          with patch.object(handler, "_send", return_value=True):
            handler._handle_list_req({})
    clear_mp4_cache.assert_called_once()

  @patch("openpilot.selfdrive.appbridged.video_protocol.clear_mp4_cache")
  def test_list_req_keeps_mp4_cache_while_busy(self, clear_mp4_cache):
    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    handler._pending_download = {"transferId": 1}
    with patch("openpilot.selfdrive.appbridged.video_protocol.validate_storage", return_value=(True, None)):
      with patch.object(handler, "_ensure_cache_dirs", return_value=True):
        with patch("openpilot.selfdrive.appbridged.video_protocol.scan_drives", return_value=[]):
          with patch.object(handler, "_send", return_value=True):
            handler._handle_list_req({})
    clear_mp4_cache.assert_not_called()

  @patch("openpilot.selfdrive.appbridged.video_protocol.Mp4ConvertJob")
  @patch("openpilot.selfdrive.appbridged.video_protocol.resolve_hevc_path")
  def test_download_req_cancels_thumbnails(self, resolve_hevc_path, Mp4ConvertJob):
    resolve_hevc_path.return_value = Path("/fake.hevc")
    Mp4ConvertJob.return_value = MagicMock()
    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    handler._open_drive_id = "2026-07-07--08-29-18"
    handler._thumb_queue = [{"driveId": "2026-07-07--08-29-18", "segment": 0, "camera": "road"}]
    handler._thumb_job = MagicMock()
    with patch.object(handler, "_ensure_cache_dirs", return_value=True):
      with patch.object(handler, "_send", return_value=True):
        with patch("openpilot.selfdrive.appbridged.video_protocol.validate_storage", return_value=(True, None)):
          handler._handle_download_req({
          "driveId": "2026-07-07--08-29-18",
          "segment": 0,
          "camera": "road",
        })
    self.assertIsNone(handler._open_drive_id)
    self.assertEqual(handler._thumb_queue, [])
    self.assertIsNone(handler._thumb_job)


class TestVideoCacheCleanup(unittest.TestCase):
  @patch("openpilot.selfdrive.appbridged.video_thumbnail_cache.subprocess.run")
  def test_generate_thumb_recreates_cache_dir(self, run_mock):
    with tempfile.TemporaryDirectory() as tmp:
      import openpilot.selfdrive.appbridged.video_thumbnail_cache as thumb_cache
      cache_dir = Path(tmp) / "thumbs"
      cache_path = cache_dir / "file.jpg"

      def fake_run(cmd, timeout, capture_output):
        tmp_path = Path(cmd[-1])
        tmp_path.write_bytes(b"x")
        return MagicMock(returncode=0, stderr=b"")

      run_mock.side_effect = fake_run
      ok = thumb_cache._generate_thumb("/fake.hevc", cache_path)

      self.assertTrue(ok)
      self.assertTrue(cache_path.exists())

  def test_delete_thumb_cache_removes_file_only(self):
    with tempfile.TemporaryDirectory() as tmp:
      import openpilot.selfdrive.appbridged.video_thumbnail_cache as thumb_cache
      old_thumb_dir = thumb_cache.THUMB_CACHE_DIR
      old_cache_root = thumb_cache.CACHE_ROOT
      thumb_dir = Path(tmp) / "thumbs"
      thumb_dir.mkdir(parents=True)
      cache_path = thumb_dir / "file.jpg"
      cache_path.write_bytes(b"x")
      try:
        thumb_cache.THUMB_CACHE_DIR = str(thumb_dir)
        thumb_cache.CACHE_ROOT = str(Path(tmp))
        delete_thumb_cache(cache_path)
      finally:
        thumb_cache.THUMB_CACHE_DIR = old_thumb_dir
        thumb_cache.CACHE_ROOT = old_cache_root

      self.assertFalse(cache_path.exists())
      self.assertTrue(thumb_dir.exists())

  def test_clear_thumb_cache_removes_dirs(self):
    with tempfile.TemporaryDirectory() as tmp:
      import openpilot.selfdrive.appbridged.video_thumbnail_cache as thumb_cache
      old_thumb_dir = thumb_cache.THUMB_CACHE_DIR
      old_cache_root = thumb_cache.CACHE_ROOT
      thumb_dir = Path(tmp) / "thumbs"
      thumb_dir.mkdir(parents=True)
      (thumb_dir / "file.jpg").write_bytes(b"x")
      try:
        thumb_cache.THUMB_CACHE_DIR = str(thumb_dir)
        thumb_cache.CACHE_ROOT = str(Path(tmp))
        thumb_cache.clear_thumb_cache()
      finally:
        thumb_cache.THUMB_CACHE_DIR = old_thumb_dir
        thumb_cache.CACHE_ROOT = old_cache_root

      self.assertFalse(thumb_dir.exists())
      self.assertFalse(Path(tmp).exists())

  @patch("openpilot.selfdrive.appbridged.video_protocol.clear_thumb_cache")
  def test_ble_connect_does_not_clear_thumb_cache(self, clear_thumb_cache):
    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    handler._open_drive_id = "2026-07-07--08-29-18"
    handler._thumb_queue = [{"driveId": "2026-07-07--08-29-18", "segment": 0, "camera": "road"}]
    handler._cached_drives = [{"driveId": "2026-07-07--08-29-18", "segments": []}]
    handler.on_ble_connected()
    clear_thumb_cache.assert_not_called()
    self.assertIsNone(handler._open_drive_id)
    self.assertEqual(handler._thumb_queue, [])
    self.assertEqual(handler._cached_drives, [])

  @patch("openpilot.selfdrive.appbridged.video_protocol.clear_thumb_cache")
  def test_ble_disconnect_does_not_clear_thumb_cache(self, clear_thumb_cache):
    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    handler.on_ble_disconnected()
    clear_thumb_cache.assert_not_called()



  @patch("openpilot.selfdrive.appbridged.video_protocol.ThumbGenerateJob")
  @patch("openpilot.selfdrive.appbridged.video_protocol.read_cached_thumb")
  @patch("openpilot.selfdrive.appbridged.video_protocol.delete_thumb_cache")
  def test_thumb_gen_failure_keeps_cache_when_video_exists(self, delete_thumb_cache, read_cached_thumb, ThumbGenerateJob):
    read_cached_thumb.return_value = None
    job = MagicMock()
    job.poll.return_value = (True, False)
    ThumbGenerateJob.return_value = job

    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    cache_path = Path("/cache/road.jpg")
    handler._open_drive_id = "2026-07-07--08-29-18"
    handler._thumb_queue = [{
      "driveId": "2026-07-07--08-29-18",
      "segment": 0,
      "camera": "road",
      "hevc": "/road0.hevc",
      "cache": cache_path,
    }]
    handler._thumb_job = job
    handler._thumb_pending = handler._thumb_queue[0]

    with patch("os.path.isfile", return_value=True):
      handler._advance_thumbnails()

    delete_thumb_cache.assert_not_called()

  @patch("openpilot.selfdrive.appbridged.video_protocol.ThumbGenerateJob")
  @patch("openpilot.selfdrive.appbridged.video_protocol.read_cached_thumb")
  @patch("openpilot.selfdrive.appbridged.video_protocol.delete_thumb_cache")
  def test_thumb_gen_failure_deletes_cache_when_video_missing(self, delete_thumb_cache, read_cached_thumb, ThumbGenerateJob):
    read_cached_thumb.return_value = None
    job = MagicMock()
    job.poll.return_value = (True, False)
    ThumbGenerateJob.return_value = job

    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    cache_path = Path("/cache/road.jpg")
    handler._open_drive_id = "2026-07-07--08-29-18"
    handler._thumb_queue = [{
      "driveId": "2026-07-07--08-29-18",
      "segment": 0,
      "camera": "road",
      "hevc": "/road0.hevc",
      "cache": cache_path,
    }]
    handler._thumb_job = job
    handler._thumb_pending = handler._thumb_queue[0]

    with patch("os.path.isfile", return_value=False):
      handler._advance_thumbnails()

    delete_thumb_cache.assert_called_once_with(cache_path)

  def test_prune_orphan_thumb_cache_deletes_missing_video(self):
    with tempfile.TemporaryDirectory() as tmp:
      import openpilot.selfdrive.appbridged.video_thumbnail_cache as thumb_cache
      old_thumb_dir = thumb_cache.THUMB_CACHE_DIR
      thumb_dir = Path(tmp) / "thumbs"
      thumb_dir.mkdir(parents=True)
      cache_path = thumb_dir / "2026-07-07--08-29-18--0--road.jpg"
      cache_path.write_bytes(b"x")
      try:
        thumb_cache.THUMB_CACHE_DIR = str(thumb_dir)
        with patch("openpilot.selfdrive.appbridged.video_scanner.resolve_hevc_path", return_value=None):
          thumb_cache.prune_orphan_thumb_cache()
      finally:
        thumb_cache.THUMB_CACHE_DIR = old_thumb_dir
      self.assertFalse(cache_path.exists())

  def test_prune_orphan_thumb_cache_keeps_existing_video(self):
    with tempfile.TemporaryDirectory() as tmp:
      import openpilot.selfdrive.appbridged.video_thumbnail_cache as thumb_cache
      old_thumb_dir = thumb_cache.THUMB_CACHE_DIR
      thumb_dir = Path(tmp) / "thumbs"
      thumb_dir.mkdir(parents=True)
      cache_path = thumb_dir / "2026-07-07--08-29-18--0--road.jpg"
      cache_path.write_bytes(b"x")
      try:
        thumb_cache.THUMB_CACHE_DIR = str(thumb_dir)
        with patch("openpilot.selfdrive.appbridged.video_scanner.resolve_hevc_path", return_value=Path("/road.hevc")):
          thumb_cache.prune_orphan_thumb_cache()
      finally:
        thumb_cache.THUMB_CACHE_DIR = old_thumb_dir
      self.assertTrue(cache_path.exists())
  def test_delete_mp4_cache_removes_empty_dirs(self):
    with tempfile.TemporaryDirectory() as tmp:
      import openpilot.selfdrive.appbridged.video_mp4_cache as mp4_cache
      old_mp4_dir = mp4_cache.MP4_CACHE_DIR
      old_cache_root = mp4_cache.CACHE_ROOT
      mp4_dir = Path(tmp) / "mp4"
      mp4_dir.mkdir(parents=True)
      cache_path = mp4_dir / "file.mp4"
      cache_path.write_bytes(b"x")
      try:
        mp4_cache.MP4_CACHE_DIR = str(mp4_dir)
        mp4_cache.CACHE_ROOT = str(Path(tmp))
        delete_mp4_cache(cache_path)
      finally:
        mp4_cache.MP4_CACHE_DIR = old_mp4_dir
        mp4_cache.CACHE_ROOT = old_cache_root

      self.assertFalse(cache_path.exists())
      self.assertFalse(mp4_dir.exists())

  @patch("openpilot.selfdrive.appbridged.video_protocol.validate_storage")
  def test_keepalive_does_not_send_list_response(self, validate_storage):
    validate_storage.return_value = (True, None)
    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    with patch.object(handler, "_send", return_value=True) as send:
      handler.send_list_keepalive()
      payload = send.call_args.args[0]
      self.assertEqual(payload["msgType"], MSG["KEEPALIVE"])
      self.assertTrue(payload["videoDlValid"])
      self.assertNotIn("drives", payload)

  @patch("openpilot.selfdrive.appbridged.video_protocol.validate_storage")
  def test_keepalive_reports_invalid_storage(self, validate_storage):
    validate_storage.return_value = (False, "sd_invalid")
    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    with patch.object(handler, "_send", return_value=True) as send:
      handler.send_list_keepalive()
      payload = send.call_args.args[0]
      self.assertEqual(payload["msgType"], MSG["KEEPALIVE"])
      self.assertFalse(payload["videoDlValid"])


  def test_is_transfer_active_while_download_pending(self):
    handler = VideoProtocolHandler(MagicMock(), MagicMock())
    self.assertFalse(handler.is_transfer_active())
    handler._pending_download = {"driveId": "drive"}
    self.assertTrue(handler.is_transfer_active())



if __name__ == "__main__":
  unittest.main()

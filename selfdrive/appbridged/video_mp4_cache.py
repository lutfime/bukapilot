import os
import shutil
import signal
import subprocess
import threading
from pathlib import Path

from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.appbridged.video_constants import (
  CACHE_ROOT,
  FFMPEG_HEVC_INPUT_ARGS,
  MP4_CACHE_DIR,
  MP4_CONVERT_TIMEOUT_SEC,
)


def mp4_cache_path(drive_id: str, segment: int, camera: str) -> Path:
  return Path(MP4_CACHE_DIR) / f"{drive_id}--{segment}--{camera}.mp4"


def _is_mp4_valid(mp4_path: Path, hevc_path: str) -> bool:
  if not mp4_path.is_file() or mp4_path.stat().st_size <= 0:
    return False
  if not os.path.isfile(hevc_path):
    return False
  return mp4_path.stat().st_mtime >= os.path.getmtime(hevc_path)


def delete_mp4_cache(mp4_path: Path | None) -> None:
  if not mp4_path:
    return
  try:
    mp4_path.unlink(missing_ok=True)
    mp4_path.with_suffix(".tmp.mp4").unlink(missing_ok=True)
    Path(MP4_CACHE_DIR).rmdir()
    Path(CACHE_ROOT).rmdir()
  except OSError:
    pass


def clear_mp4_cache() -> None:
  shutil.rmtree(MP4_CACHE_DIR, ignore_errors=True)
  try:
    Path(CACHE_ROOT).rmdir()
  except OSError:
    pass


class Mp4ConvertJob:
  def __init__(self, hevc_path: str, mp4_path: Path):
    self.hevc_path = hevc_path
    self.mp4_path = mp4_path
    self._lock = threading.Lock()
    self._done = False
    self._ok = False
    self._error: str | None = None
    self._cancelled = False
    self._proc: subprocess.Popen | None = None
    threading.Thread(target=self._run, daemon=True).start()

  def _spawn_ffmpeg(self, cmd: list[str]) -> bool:
    proc = None
    try:
      proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
      with self._lock:
        if self._cancelled:
          try:
            os.killpg(proc.pid, signal.SIGTERM)
          except OSError:
            pass
          return False
        self._proc = proc
      _, _ = proc.communicate(timeout=MP4_CONVERT_TIMEOUT_SEC)
      return proc.returncode == 0
    except subprocess.TimeoutExpired:
      if proc is not None:
        try:
          os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
          pass
        try:
          proc.wait(timeout=5)
        except subprocess.SubprocessError:
          pass
      return False
    finally:
      with self._lock:
        self._proc = None

  def _run(self):
    ok = False
    err = None
    tmp = self.mp4_path.with_suffix(".tmp.mp4")
    try:
      if _is_mp4_valid(self.mp4_path, self.hevc_path):
        ok = True
      elif self._cancelled:
        err = "conversion_cancelled"
      else:
        tmp.unlink(missing_ok=True)
        os.makedirs(self.mp4_path.parent, exist_ok=True)
        remux_cmd = [
          "ffmpeg", "-y", "-loglevel", "error",
          *FFMPEG_HEVC_INPUT_ARGS, "-i", self.hevc_path,
          "-c:v", "copy", "-movflags", "+faststart",
          str(tmp),
        ]
        ok = self._spawn_ffmpeg(remux_cmd)
        if not ok and not self._cancelled:
          cloudlog.warning("mp4 remux failed, trying re-encode")
          tmp.unlink(missing_ok=True)
          reencode_cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            *FFMPEG_HEVC_INPUT_ARGS, "-i", self.hevc_path,
            "-c:v", "libx264", "-preset", "veryfast", "-movflags", "+faststart",
            str(tmp),
          ]
          ok = self._spawn_ffmpeg(reencode_cmd)
        if self._cancelled:
          ok = False
          err = "conversion_cancelled"
        elif ok:
          if not tmp.is_file() or tmp.stat().st_size <= 0:
            ok = False
            err = "conversion_failed"
          else:
            os.replace(tmp, self.mp4_path)
        else:
          err = "conversion_failed"
        if not ok:
          tmp.unlink(missing_ok=True)
          self.mp4_path.unlink(missing_ok=True)
    except Exception as e:
      cloudlog.error(f"mp4 convert job error: {e}")
      err = "conversion_cancelled" if self._cancelled else "conversion_failed"
      delete_mp4_cache(self.mp4_path)
    with self._lock:
      self._ok = ok
      self._error = err
      self._done = True

  def cancel(self) -> None:
    proc = None
    with self._lock:
      self._cancelled = True
      proc = self._proc
    if proc is not None:
      try:
        os.killpg(proc.pid, signal.SIGTERM)
      except OSError:
        pass
    delete_mp4_cache(self.mp4_path)

  def poll(self) -> tuple[bool, bool, str | None]:
    with self._lock:
      return self._done, self._ok, self._error

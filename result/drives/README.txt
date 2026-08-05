Kommu device drive archive
==========================

Device : kommu-e2eed9037197a64e  (dongle e2eed9037197a64e, aarch64)
Source : pulled via SSH (kommu@192.168.43.199) from /data/media/0/realdata/<route>--<seg>/qlog
Firmware on device: branch release_ka2, "bukapilot v10.0.5 release", commit 0c6977fc6 (2026-03-09)
  NOTE: device runs release_ka2 v10.0.5 — NOT the beta or staging branches we analyzed in the clone.

Each .qlog file = all segments of that route concatenated in numeric order (qlog resolution;
good for tuning analysis. Pull rlog separately if raw-CAN / full-res is needed).

Today's routes archived (2026-08-03):
  2026-08-03--06-43-17.qlog   40 MB   *** DRIVING *** ~14 min, 0-73 km/h, 12 stop-and-go episodes, brake pulsing captured
  2026-08-03--07-48-42.qlog   10 MB   parked (vEgo=0)
  2026-08-03--08-02-03.qlog    7 MB   parked (vEgo=0)
  2026-08-03--08-29-55.qlog    2 MB   parked (vEgo=0)
  2026-08-03--08-38-59.qlog   10 MB   parked (vEgo=0)
  2026-08-03--08-43-13.qlog   87 MB   parked (vEgo=0)

=> The ONLY actual driving today is 06-43-17. Everything else was parked (openpilot on, car stationary).

NOT archived:
  - March 2026 routes (2026-03-21..29) = PREVIOUS OWNER, different car (device bought 2nd-hand). Skipped per user.
  - No 2026-08-02 (yesterday) data exists on the device.

Analyze later with:
  /Users/WanLutfi/Documents/Xcode/Kommu/.venv_logs/bin/python \
    /Users/WanLutfi/Documents/Xcode/Kommu/analyze_log.py <file>.qlog --plot out.png

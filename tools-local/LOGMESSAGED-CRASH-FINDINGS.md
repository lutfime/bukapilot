# logmessaged "process error" — Root Cause & Fix

## Symptom
On a drive, openpilot showed **"process error"**, stayed blue, never engaged (`active=0,
engageable=0` for the whole route). The car was NOT controlled.

## Root cause
`logmessaged` was **dead** (`managerState`: `running=False, exitCode=-6 (SIGABRT),
shouldBeRunning=True`) — the only dead required process; all others (modeld, controlsd,
selfdrived, plannerd, pandad) were healthy. controlsd's all-alive check sees logmessaged
missing → refuses to engage → "process error".

The SIGABRT is a **msgq assertion**:
```
msgq_repo/msgq/msgq.cc:248: msgq_msg_send: Assertion `3 * total_msg_size <= q->size' failed.
```
i.e. a msgq message was too big for its queue. The offender: **`updated`** (`system.updated.updated`,
`only_offroad`) periodically logs the **entire `git diff` of the device working tree** via
`cloudlog.info(f"git diff output:\n{git_diff}")`. With accumulated local-tuning modifications the
diff was **~354 KB** → `3 × 354KB` far exceeds the msgq log queue size → assertion → logmessaged
SIGABRT. Because logmessaged *is* the logger, its own crash is invisible in swaglog — had to run
it manually to catch the assertion. It crashed while **offroad** (updated only runs offroad) and
manager didn't restart it, so it stayed dead into the next drive.

## Reproduce (device only)
```bash
ssh kommu@192.168.0.9 'cd /data/openpilot && git diff | wc -c'   # >~100KB triggers it
ssh kommu@192.168.0.9 'cd /data/openpilot && timeout 15 /usr/local/venv/bin/python -m openpilot.system.logmessaged'
# -> "msgq_msg_send: Assertion `3 * total_msg_size <= q->size' failed" / Aborted (core dumped)
```

## Fix (committed)
`system/updated/updated.py` line ~202: truncate the **cloudlog** diff to 2 KB (the full diff is
still written to the `GitDiff` **param** — a file, no msgq — so no info lost):
```python
cloudlog.info(f"git diff output ({len(git_diff)}B, truncated):\n{git_diff[:2048]}")
```
After deploying + restarting, logmessaged stays alive (`running=True`) even with updated running.
Robust to future diff growth (always truncated).

## NOT the cause
- **MADS_DEBUG logging** (selfdrived) — tiny ~200-byte message, every 50 frames. Nowhere near the
  queue limit. Not it.
- **WMI model** — modeld never logged a WMI boot that drive (the "restart" didn't reboot the
  device); the drive was on default. logmessaged died independently.
- **opm10v3 / C++ runner** — unrelated (different process).

## Lesson
When openpilot shows "process error" / won't engage, check `managerState` for the dead process
(it names exitCode/signals). If it's logmessaged, the crash won't be in swaglog — run it manually
to see the abort. A large device git diff is the trigger; keep the diff lean or keep the truncation fix.

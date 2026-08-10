# openpilot Driving Experience: 0.9 → 0.10 → 0.11 + Community Models

> Is the upgrade worth it, and is this the path to fully self-driving?
> Compiled 2026-08-04, updated 2026-08-11 with community model research.
> Sources cited inline; community quotes from r/Comma_ai (search-snippet
> verified, open threads directly for full context).
>
> **Update (2026-08-11):** Added §5 — Community Models (WMI V12, OPM10V3, DTR v6).
> These are community-trained finetunes of comma's base models that run at full speed
> on the KA2. See `tools-local/COMMUNITY-MODELS-RESEARCH.md` for technical details.

---

## The one-paragraph answer

**Yes, each jump is worth it — and yes, this is the path to full self-driving, but you're
watching the climb, not the summit.** 0.9 → 0.10 was the *planning* revolution (the model
became the planner, MPC removed). 0.10 → 0.11 is the *simulation* revolution (the model is
now trained entirely inside a learned simulator instead of a hand-coded one). Driving-quality
gains are real and users feel them — but openpilot is still a Level 2 driver-assist system.
Full autonomy (Level 4+) remains a stated future goal, not a current capability. Here's the
honest breakdown.

---

## 1. What each version actually changed (the technical delta)

| | 0.9.x | 0.10.x | 0.11.x |
|---|---|---|---|
| **Lateral planning** | Lateral MPC at inference | **Model emits curvature directly** (E2E) — MPC removed | Same E2E lateral, refined via learned-sim training |
| **Longitudinal (Experimental)** | Lead MPC | E2E `desiredAcceleration` from World Model (min-gated by MPC) | **Learned simulator trains the policy** — handles highway speeds, parked cars much better |
| **Longitudinal (Chill)** | Classical lead policy | Classical lead policy (unchanged) | Classical lead policy (still unchanged) |
| **Training method** | Hand-coded simulator + labels | World Model as supervisor, hand-coded sim for video | **Fully learned simulator** (2B-param "WMI") — no hand-coded sim left |
| **Model size** | "supercombo" | Split vision + policy; World Model 500M→1B params | Single supercombo; **880M "big model"** (0.11.2, external GPU) |
| **Headline philosophy** | "Plan with MPC, model predicts" | "Model IS the planner" | "Train the model inside a brain, not a physics engine" |

The key conceptual progression: **0.10 removed the hand-coded *controller*, 0.11 removed
the hand-coded *simulator*.** comma's framing ([0.11 blog](https://blog.comma.ai/011release/)):
the learned sim has no "artifacts" for the policy to exploit, so the model can't cheat —
fidelity just improves with more compute and data.

---

## 2. 0.9 → 0.10: The planning revolution

**What users felt (from [0.10 AMA](https://www.reddit.com/r/Comma_ai/comments/1mwpf8u/openpilot_010_is_out_ama/)
and community threads):**

- ✅ **Smoother cornering, cleaner lane-centering.** The path is a single E2E curvature
  output, not an MPC reacting tick-by-tick → fewer mid-corner corrections.
- ✅ **More decisive steering.** "Better path prediction + fewer mid-corner corrections."
- ✅ **Per-car accuracy** from live lateral-delay estimation (the `lagd` process — also
  relevant to your X70 work).
- ⚠️ **Known regression:** steering-wheel oscillation at 65+ mph on straight roads for at
  least one vehicle (2020 Hyundai Santa Fe). Validate this on the X70.
- 🔀 **Mixed reception overall.** Some users [downgraded from 0.10.0 to 0.9.9](https://www.reddit.com/r/Comma_ai/comments/1n68go9/how_do_i_downgrade_to_older_version_of_stock/)
  over lane-keeping concerns on certain cars. 0.10 was a bigger architectural leap than a
  polish release — early point releases had car-specific quirks.
- 📊 **Longitudinal (Experimental) got the World Model** — better stop-and-go and
  stopped-lead detection. Chill mode long was unchanged.

**Worth the upgrade?** Yes, but expect car-specific validation. For your X70, this is the
base you're already on (`staging`) — the gains are real, the regressions need checking.

---

## 3. 0.10 → 0.11: The simulation revolution

**What users felt (from [0.11 AMA](https://www.reddit.com/r/Comma_ai/comments/1rwhomf/openpilot_011_ships_today_ask_us_anything/)
and community threads):**

- ✅ **"OpenPilot is feeling the best it ever has"** — [r/Comma_ai thread](https://www.reddit.com/r/Comma_ai/comments/1ou8kaa/openpilot_is_feeling_the_best_it_ever_has_with/):
  *"The driving is incredibly smooth. Steering adjustments are much softer than previous
  updates"* — more human-like.
- ✅ **E2E model "can be trusted more for highway and general driving"** —
  [r/Comma_ai](https://www.reddit.com/r/Comma_ai/comments/1rwhari/new_comma_e2e_model_seems_like_people_are_finally/):
  *"Turning should be better… E2E model can be trusted more for highway and general driving."*
- ✅ **Experimental longitudinal overtakes stock ACC in user preference.** Comma's usage
  data: *"users on nightly now prefer Experimental mode (end-to-end longitudinal policy)
  over openpilot's ACC policy."* ([0.11 blog](https://blog.comma.ai/011release/))
- ✅ **Better stationary-vehicle detection, fewer false-lead brakes.**
- ⚠️ **0.10.3 specifically had a left-drift regression** noted by some users
  ([r/Comma_ai](https://www.reddit.com/r/Comma_ai/comments/1qxvx2d/openpilot_103/)) —
  resolved in 0.11.
- 🔁 **E2E long is still gated to Experimental, not Chill.** Chill mode longitudinal is
  still the classical lead policy. Graduating E2E long to Chill is the stated road to 1.0.

**The headline comma engineer quote** ([0.11 AMA](https://www.reddit.com/r/Comma_ai/comments/1rwhomf/)):
> *"I am upset now when I get in any of our cars that have experimental mode toggled off."*

That's a strong signal — internally, 0.11's Experimental longitudinal crossed a "I'd rather
have it than not" threshold for comma's own engineers.

**Worth the upgrade?** Yes — this is the version where Experimental longitudinal became
genuinely good (highway speeds, parked cars, smoother stop-and-go). If you do any
stop-and-go or dense traffic driving, 0.11 is a meaningful quality jump. The main caveat is
that the gains concentrate in **Experimental mode** — Chill users see less delta.

---

## 4. Side-by-side: what improved where

| Dimension | 0.9 → 0.10 | 0.10 → 0.11 |
|---|---|---|
| **Steering smoothness** | Improved (no MPC twitch) | Further improved (learned-sim training) |
| **Lane centering** | Improved (direct curvature) | Fixed 0.10.3 left-drift; "best it's ever been" |
| **High-speed stability** | Mixed (oscillation regression on some cars) | Better; still validate per-car |
| **Experimental longitudinal** | Got World Model; better stop-and-go | **Big jump** — highway speeds, parked cars, "prefer over ACC" |
| **Chill longitudinal** | Unchanged | Unchanged |
| **Traffic-light / stop-sign stopping** | Experimental only, inconsistent | Experimental only, more reliable |
| **Resume from stop** | Some community reports of issues | Improved but still watch |
| **Driver-monitoring / UI** | Minor | Polished (comma four focus) |

---

## 5. Is this the path to fully self-driving?

**Honest answer: yes, this is the trajectory — but you're watching the climb, not the summit.**

### What "full self-driving" means here
Two senses, only one of which exists:
1. **Planning-level E2E** — the model outputs path/curvature/accel directly, classical
   controllers still actuate. ← This exists (0.10+ lateral, 0.11 Experimental long).
2. **Full E2E** — the model directly emits steering torque / brake commands, no controller
   in the loop. ← **Does not exist anywhere.** comma's own
   [contributing/roadmap](https://github.com/commaai/openpilot/blob/master/docs/contributing/roadmap.md)
   lists *"fully end-to-end driving policy"* as the future **openpilot 1.0** goal.

So when comma markets "end-to-end," they mean sense #1 (the *planner* is a neural net), not
the model driving the car directly.

### Where 0.11 sits on the road to 1.0
From comma's own framing:
- **0.10** — World Model introduced as the training supervisor
- **0.11** — Trains fully inside the learned simulator ("first real-world robotics agent
  shipped to real users")
- **Road to 1.0** — Graduate E2E longitudinal from Experimental → Chill; reach "fully
  end-to-end driving policy"

0.11 is the **first generation** of the fully-learned-simulator approach. The longitudinal
polish (confident highway convergence, users preferring E2E over ACC) matures further
beyond 0.11. So 0.11 is a real step toward autonomy, but it's a Level 2 driver-assist
system today — the driver is still responsible and still required.

### The honest reality check
- openpilot is **SAE Level 2** (driver assistance), not Level 4+ (full autonomy). The driver
  must pay attention and intervene.
- comma's "end-to-end" marketing = the *planner* is neural, not the full driving stack.
- Full autonomy is the **stated direction**, with 0.11 as a meaningful milestone — but
  there's no announced date for Level 4, and the regulatory/verification burden for true
  self-driving is immense.
- For your use case (X70 in Malaysian traffic): each version makes the assist better, but
  none of them remove the driver from the loop.

---

## 6. Bottom line for your decision

| Question | Answer |
|---|---|
| **Is 0.10 worth it over 0.9?** | Yes — you're already there. E2E lateral + World Model long are real gains. Validate highway oscillation on the X70. |
| **Is 0.11 worth it over 0.10?** | **Yes, especially for Experimental mode.** Smoother steering, much better longitudinal, more reliable stopping. Main gains are in Experimental; Chill is largely unchanged. |
| **Is this the path to full self-driving?** | It's *a* path, and it's the most credible one in the consumer ADAS space. But you're watching Level 2 get progressively better — not Level 4 arriving. Driver still required. |
| **Should you migrate your fork to 0.11?** | For the **driving-quality gains**, yes. For the **effort**, see `0.10-vs-0.11-migration-analysis.md` — the model-only path (Option B) is now the realistic way to get 0.11's driving quality on your NPU without a full rebase. |
| **What should you do first?** | Finish X70 tuning on 0.10.3 (your `lagd` + `kf` work). The 0.11 model upgrade is orthogonal and can wait until the X70 drives well on the current base. |

---

## 7. Sources

**Official comma:**
- [0.10 release blog](https://blog.comma.ai/010release/)
- [0.11 release blog](https://blog.comma.ai/011release/) — *"users on nightly now prefer Experimental mode"*
- [openpilot RELEASES.md](https://github.com/commaai/openpilot/blob/master/RELEASES.md)
- [openpilot contributing roadmap](https://github.com/commaai/openpilot/blob/master/docs/contributing/roadmap.md) — full E2E = 1.0 goal

**Community (r/Comma_ai — open directly for full context):**
- [0.10 AMA](https://www.reddit.com/r/Comma_ai/comments/1mwpf8u/openpilot_010_is_out_ama/)
- [0.11 AMA](https://www.reddit.com/r/Comma_ai/comments/1rwhomf/openpilot_011_ships_today_ask_us_anything/)
- ["OpenPilot is feeling the best it ever has"](https://www.reddit.com/r/Comma_ai/comments/1ou8kaa/openpilot_is_feeling_the_best_it_ever_has_with/)
- ["New comma E2E model… finally able to trust"](https://www.reddit.com/r/Comma_ai/comments/1rwhari/new_comma_e2e_model_seems_like_people_are_finally/)
- [0.10.3 left-drift regression thread](https://www.reddit.com/r/Comma_ai/comments/1qxvx2d/openpilot_103/)
- [Downgrade 0.10→0.9.9 thread](https://www.reddit.com/r/Comma_ai/comments/1n68go9/how_do_i_downgrade_to_older_version_of_stock/)

> **Sourcing caveat:** Reddit blocked direct fetches (anti-bot); quotes above come from
> search-engine snippets and should be verified by opening threads directly. The comma blog
> quotes are verbatim from fetched pages.

---

## 5. Community Models (added 2026-08-11)

Beyond comma's stock models, the community trains finetunes that target different driving
styles. These run through forks like sunnypilot and FrogPilot, and are distributed as
compiled tinygrad models via sunnypilot's model manager.

### Which models work on the KA2?

**Critical finding:** not all community models are supercombo. The architecture depends on
the openpilot base version the model was trained against. Models built on the 0.10.x split
architecture (vision + policy) run at full speed on the KA2 (~30 Hz). Only the fused
supercombo models hit the 12 Hz ceiling.

| Model | Base | Architecture | Est KA2 speed | Community verdict |
|-------|------|-------------|---------------|-------------------|
| **WMI V12** | 0.10.3 | 2-file split | ~31ms / ~32 Hz | Community-recommended; solid all-around. Some left-drift on highways. |
| **OPM10V3** | post-0.11 | 3-file split | ~32ms / ~31 Hz | Strong longitudinal; lateral has right-bias + wobble. Praise for curve handling. |
| **DTR v6** | 0.10.3 | 2-file split | ~31ms (est) | "Wife-approved" comfort. Good curves. Could center better on highways. |
| **Tomb Raider 16** | 0.10.3 | 2-file split | ~31ms (est) | "Best lateral model since TR16." Strong lane-keeping. |
| **0.11 supercombo** | 0.11 | fused | 79.5ms / 12.6 Hz | ❌ Too slow on KA2 |

### bukapilot integration status

All three viable model types are integrated into a **model selector** in the KommuDrive app:
- Default (0.10.3) — production
- WMI V12 — community finetune
- OP Model 10 V3 — 3-file split

The selector writes `SelectedDrivingModel` to `/data/params/`. modeld reads it at startup.
Switching models takes effect on the next drive.

### Should you try them?

**Yes** — the simulator benchmarks confirm all three models run at ~30 Hz on the KA2 NPU,
comparable to the production 0.10.3. The real differentiator is **driving feel**, which
can only be judged on a test drive. The community reviews suggest:

- **WMI V12** if you want a proven, community-recommended model with minimal risk
- **OPM10V3** if you want better longitudinal control (acceleration/braking) and can
  tolerate some lateral tuning (the wobble may be mitigated by bukapilot's `LAT_SMOOTH_SECONDS`)
- **DTR v6** or **Tomb Raider 16** (future candidates) for comfort or lateral precision

See `tools-local/COMMUNITY-MODELS-RESEARCH.md` for the full technical analysis, conversion
pipeline, and architecture details.

# Sequential Change Log — ExoBoot Controller Troubleshooting

**Purpose**: This document captures every change made to this codebase across
debugging sessions, in chronological order, with the *reason* for each change
and the *evidence* that drove it. New AI agents (or humans returning after
time away) should read this file **before** [`CLAUDE.md`](CLAUDE.md) when the
question involves any of: gait detection, torque profile, motor faults,
position vs current control, the per-sample logger, or the analysis suite.

> **If you are a new AI agent**: Read this file end-to-end before proposing
> changes. The codebase has gone through several false starts that ended up
> being reverted; do NOT re-suggest those without checking here first.

---

## Status as of 2026-04-28 (latest)

| Area | State |
|---|---|
| Heel-strike detection | ✅ Working. Confirmed by `Final num_gait > 0` on every walk test since MAX1. |
| Collins torque profile math | ✅ Working. Cmd τ matches `peak_torque_norm × weight` to 0.01 Nm. |
| Per-sample diagnostic logger | ✅ Implemented ([`exo_logger.py`](exo_logger.py)). Writes one row per control-loop iteration. |
| FlexSEA DataLog disambiguation | ✅ Implemented. Files renamed with `_LEFT_` / `_RIGHT_` + boot ID + participant + phase on cleanup. |
| GUI log persistence | ✅ Implemented. Lives in [`data/GUIlog_*.txt`](data/). |
| Analysis suite | ✅ Implemented ([`DataLog/analysis/Analysis2.py`](DataLog/analysis/Analysis2.py)). Single-file, latest, and L/R pair modes. |
| Position-control-while-walking | ❌ **Removed** (was the source of the over-current faults). Replaced with `NO_SLACK_CURRENT` hold. |
| Walk-test reliability | ⚠️ Last failure mode (over-current via position-error spikes) **patched but not yet validated on hardware** as of this writing. Next walk test should validate. |

---

## Session 1 — 2026-04-28 morning: Per-sample logging + analysis suite

### Problem reported
- "Right boot failed almost right as I started walking and then did not turn back on" (Max, 0.8 m/s, 4/23).
- "For Sav both boots worked but I have no indication if they were actually doing what they were supposed to."
- [`data/`](data/) folder output was sometimes empty (header-only CSVs).
- [`DataLog/`](DataLog/) folder produces 2 timestamped files per walk test with **no indication of which boot is which**.
- GUI log was lost every time the GUI was closed.
- Existing [`Analysis1.py`](DataLog/analysis/Analysis1.py) could pick out strides but had no way to verify against ground truth.

### Diagnosis from existing data (before any code changes)
Looking at FlexSEA DataLog CSVs from 4/23:
- [`mot_cur`](DataLog/) was 0–7 mA for entire 3-second walking trials.
- [`mot_volt`](DataLog/) was 0 throughout.
- One spike to 256 mA on Sav_right then nothing.
- GUI log showed `HS=0` and `pg=-1.0%` throughout — meaning gait detection never completed the ARM→TRIGGER cycle, [`percent_gait`](exo_init.py) stayed at -1, controller stayed in pre-gait state forever.
- The empty [`P001_Familiarization_*.csv`](data/) files only get written when [`num_gait > prev_gait`](perception_test.py) — confirmed by the source — so empty file = no heel strike was ever detected.

### Changes made

#### A. New file — [`exo_logger.py`](exo_logger.py)
- Added `ExoLogger` class.
- Writes one CSV row **per control-loop iteration** (~100 Hz) with a fixed schema.
- Filename pattern: `{participant}_{phase}_{L|R}_{timestamp}_full.csv`.
- File is line-buffered (`buffering=1`) so a crash leaves a usable partial file.
- Initial `HEADER` includes timing, metadata, IMU, encoders, controller mode, gait state, commands, profile params.

#### B. [`exo_init.py`](exo_init.py)
- Added [`self.logger = None`](exo_init.py) attribute on the [`ExoBoot`](exo_init.py) class.
- Added [`tag_datalog(participant_id, phase)`](exo_init.py) method that renames the auto-created FlexSEA `DataLog/Data*.csv` to include side, boot ID, participant, phase. Captures the original path right after [`start_streaming`](exo_init.py) by snapshotting [`DataLog/`](DataLog/) before vs after.
- Added [`controller_mode`](exo_init.py) tagging in [`run_collins_profile`](exo_init.py) — every branch sets a string label and calls `self.logger.log()`.
- Modes used at this point: `idle_position`, `position_early_stance`, `cur_ramp_up`, `cur_ramp_down`, `position_late_stance`. **Note: `position_*` modes were later removed.**

#### C. [`perception_test.py`](perception_test.py)
- Imported `ExoLogger`.
- Added [`_attach_loggers(phase)`](perception_test.py) helper, called at start of both `_run_familiarization` and `_run_perception` after `reset_gait_state` and `sensor_check`.
- Updated [`_cleanup`](perception_test.py) to:
  - Close each boot's logger first.
  - Rename DataLog files with side/boot_id/participant/phase via `tag_datalog`.
  - Send zero current twice (defensive).

#### D. [`gui.py`](gui.py)
- Added `import os` and `from time import strftime`.
- Opens `data/GUIlog_<timestamp>.txt` in `__init__` (line-buffered).
- Every `_append_log` call also writes to the file.
- `closeEvent` closes the file handle when no experiment is running.

#### E. New file — [`DataLog/analysis/Analysis2.py`](DataLog/analysis/Analysis2.py)
- Three modes: single file, `--latest`, `--pair` (most recent matching L/R pair).
- Plots produced (initial set):
  - `torque_profile.png` — Peng-style overlay of every stride's commanded τ and current vs % gait
  - `controller_timeline.png` — mode timeline + percent_gait + commanded τ + cmd vs measured current
  - `hs_diagnostics.png` — gyroz with thresholds, armed flag, armed_time vs refractory, num_gait
  - `kinematics.png` — ankle/motor encoders + wm/wa
  - `side_by_side.png` — 5-row × 2-col L|R comparison (paired mode only)
  - `torque_LR_overlay.png` — mean τ profile of L and R on same axes (paired mode only)
- `summary.txt` — duration, mode time-share, ranges, automatic warning flags.

#### F. Procedure correction (initially incorrect, then fixed)
- **First (wrong) advice**: "Treadmill first, then press Start." This was retracted in session 2 after seeing what happened at MAX1.
- **Correct procedure** (validated in subsequent reasoning):
  1. Stand still on stopped treadmill, lace boots.
  2. Connect & Zero (still standing).
  3. Press Start Familiarization while still standing.
  4. Wait 2–3 s.
  5. Start treadmill, ramp to target speed (1.25 m/s).
- Why: Pressing Start while walking puts the controller into the position-control idle phase with a moving foot, which used to cause a position-error spike → motor over-current. The standing-still pre-tension is rejected by the [`>3000 ms` stride sanity check](exo_init.py) in `_update_expected_duration`, so it doesn't pollute the stride-time average.

---

## Session 2 — 2026-04-28 afternoon: First walk test (MAX1) → diagnosed motor over-current

### Test details
- Participant: Max, 95 kg, 1.25 m/s, Familiarization mode.
- Procedure: corrected ("standing-still start").
- Outcome: "Right as the start familiarization began I felt a force in both boots and then both boots failed."

### Diagnosis from new logger data
The new `MAX1_Familiarization_{L,R}_*_full.csv` files showed:

| Signal | LEFT | RIGHT |
|---|---|---|
| Final num_gait | **7** ✓ | **8** ✓ |
| Time in cur_ramp_up/down | 27.7% | 27.5% |
| Peak commanded τ | **21.37 Nm** | 21.38 Nm |
| Peak commanded current | **28000 mA** (saturated to firmware max) | −28000 mA |
| Peak measured current | **26499 mA** | −26224 mA |
| Fault onset | t = 2.72 s | t = 2.13 s |

**This was a SUCCESS for the diagnostic system, not a hardware failure.**

The boots delivered ~26 A of motor current (not 0!) for ~2 strides, which is what the participant felt as "the force." Then they over-currented. Math checks out:
- [`peak_torque_norm = 0.225`](config.py) × 95 kg = **21.4 Nm peak** ✓
- That τ → current conversion saturates against [`PEAK_CURRENT = 28000 mA`](config.py).

### Changes made

#### A. Added new logger columns ([`exo_logger.py`](exo_logger.py))
- `mot_volt_mV`, `mot_vel`
- `batt_volt_mV`, `batt_curr_mA`, `temp_C`
- `status_mn`, `status_ex`, `status_re` (firmware status registers)
- `mot_pos_setpoint`, `mot_pos_error` (position-control diagnostics)

#### B. [`exo_init.py`](exo_init.py) sensor cache
- Added fields: `motorVoltage`, `motorVelocity`, `motor_pos_setpoint`, `battVoltage`, `battCurrent`, `temperature`, `status_mn`, `status_ex`, `status_re`.
- [`read_data`](exo_init.py) now reads `mot_volt`, `mot_vel`, `batt_volt`, `batt_curr`, `temperature`, `status_mn`, `status_ex`, `status_re` from the FlexSEA dict.
- [`run_collins_profile`](exo_init.py) records `motor_pos_setpoint = int(motor_target)` at every `command_motor_position` call (in all three position branches).

#### C. New plots in [`Analysis2.py`](DataLog/analysis/Analysis2.py)
- `startup_zoom.png` — first 8 s only, 6 stacked rows: controller mode, percent_gait, cmd vs meas current, **position setpoint vs actual w/ error overlay**, battery V/A, status registers.
- `faults.png` — `is_fault` flag (`|cmd|>500 mA AND |meas|<100 mA`) + battery + any-status-flag timeline.
- `battery_status.png` — full-run battery and status timeline.
- New summary flags: `Max |pos error|`, `Battery sagged below…`, `Motor failed to follow current command`.

#### D. [`config.py`](config.py) — recommended values for next test
- `PEAK_CURRENT = 15000` (down from 28000 — was firmware ceiling, not a safe operating point)
- `DEFAULT_PEAK_TORQUE_NORM = 0.12` (down from 0.225 — start gentle, ramp up after success)
- *These changes were prescribed in the chat; user applied them before MAX2.*

---

## Session 3 — 2026-04-28 evening: MAX2 / MAX3 walk tests → diagnosed position-control velocity spike

### Test details
- MAX2: standard procedure (familiarization first, then treadmill).
- MAX3: "treadmill first, then familiarization after a few steps."
- Both: Max, 85 kg (weight changed in GUI), 1.25 m/s.
- Outcome: **Both boots faulted after 1 step in MAX2 and 4 strides in MAX3.**

### Diagnosis from logger data

| | MAX2 L | MAX2 R | MAX3 L | MAX3 R |
|---|---|---|---|---|
| Final num_gait | 5 | 6 | 3 | 4 |
| Cmd current peak (clamp) | 15 000 mA ✓ | −15 000 mA ✓ | 15 000 mA ✓ | −15 000 mA ✓ |
| **Meas current peak** | **+26 223 mA** | **−26 288 mA** | **+26 476 mA** | **−26 538 mA** |
| **Max position error** | **26 908 ticks** | **35 697 ticks** | **20 183 ticks** | **32 405 ticks** |
| Cmd τ peak | 10.19 Nm ✓ | 10.20 Nm ✓ | 10.20 Nm ✓ | 10.20 Nm ✓ |
| Time share `idle_position` | 76.8 % | 64.4 % | 67.3 % | 30.1 % |
| Time share `cur_ramp_up/down` | 8.1 % | 13.8 % | 10.2 % | 25.1 % |

**Key insight**: Measured current (26 A) **exceeded** commanded current (15 A). That means the motor was NOT being driven by the Collins torque profile (which was correctly clipped at 15 A). It was being driven by **position control during the non-torque phases**, where the PID asked for whatever current it took to chase the position target.

### Root cause
[`_desired_motor_position()`](exo_init.py) includes a velocity feed-forward term:
```python
motor_angle = np.floor(
    np.polyval(self.ank_mot_coeffs, self.ankleTicksRaw)
    - self.side * self.magnitude
    - (self.kinematicCoeffs[0] * self.ankleVel_filt[0]    # -400 × velocity
       + self.kinematicCoeffs[1])
)
```
At heel-strike the ankle decelerates and reverses sign rapidly. The filtered velocity can swing by thousands of ticks/s in tens of milliseconds. Multiplied by −400, the position setpoint **jumps by 20 000–35 000 motor encoder ticks** in a single sample (≈ 440–770° of motor rotation).

The position PID (`kp=100, ki=20, kd=35`) sees that error and floors current. The motor windings see ~26 A continuous. Within 1–4 strides → thermal/over-current trip → both boots dead.

This term was inherited from Peng's controller, where it was tuned for a different exo geometry, different `magnitude` constant, and likely different sample rate — and it does not survive the transition to walking on this hardware.

### Changes made

#### A. [`exo_init.py`](exo_init.py) — replaced position control with hold-current in `run_collins_profile`
- **Pre-gait idle** (`percent_gait < 0`): was [`command_motor_position(_desired_motor_position())`](exo_init.py); now [`command_motor_current(NO_SLACK_CURRENT × side)`](exo_init.py). Mode label: `idle_no_slack`.
- **Early stance** (0 % → t_onset): same change. Mode label: `early_stance_no_slack`.
- **Late stance / swing** (t_peak+t_fall → 100 %): same change. Mode label: `late_stance_no_slack`.
- **Ascending and descending Collins ramps** (`cur_ramp_up`, `cur_ramp_down`): unchanged.

Why this is safe:
- [`NO_SLACK_CURRENT = 800 mA`](config.py) is far below any thermal limit.
- It pre-tensions the cable so the Collins ramp doesn't have dead travel — the same purpose position control was meant to serve.
- It uses the same control mode (current) throughout the entire gait cycle, eliminating mode-switch transients.
- It is what Peng's original reference controller does in his published Collins-profile implementations.

Position control is **still used** in [`encoder_check`](exo_init.py) and [`zero_boot`](exo_init.py), where the participant is stationary (velocity = 0, no spike).

#### B. [`Analysis2.py`](DataLog/analysis/Analysis2.py) — fixed false-positive status flag
- Old logic flagged `status_mn != 0` as a fault. But `status_mn` is a state register (non-zero whenever the device is in any operating state), not a fault flag.
- New logic only flags `status_ex != 0` or `status_re != 0` (real fault registers), or `status_mn` *changing mid-run* (state transition, possibly into fault).

### Status going into next walk test
- All position-control-during-walking removed.
- Expected behaviour: smooth gentle pull throughout stance/swing, Collins torque pulse on top during push-off.
- Expected new controller modes in logs: `idle_no_slack`, `early_stance_no_slack`, `cur_ramp_up`, `cur_ramp_down`, `late_stance_no_slack`.
- **Not yet validated on hardware as of this writing.**

---

## Architectural Decisions (do not undo without reading why)

### 1. Position control is NOT used during walking
**Why**: The velocity feed-forward in [`_desired_motor_position()`](exo_init.py) creates 20 000–35 000-tick position-error spikes at heel-strike, which over-current the motor within 1–4 strides. Removed in Session 3.

**If you want to restore position control during walking**, you must first either:
- Remove or aggressively low-pass-filter the velocity term, AND
- Verify [`Max |pos error|`](DataLog/analysis/Analysis2.py) in the logged CSV stays below ~2000 ticks throughout, AND
- Stage a current-limited rollout (start with kp/ki/kd reduced 4× and ramp).

### 2. The diagnostic logger is line-buffered and writes every iteration
**Why**: Earlier diagnostic data was unreliable because (a) data files were only written at HS events and (b) crashes lost everything. Line-buffering means partial files survive crashes. Per-iteration logging means we can see exactly what happened at the millisecond of failure.

**Cost**: ~100 rows/s × ~50 columns × 2 boots ≈ 10 KB/s combined. Negligible for typical run lengths.

### 3. Three controller-mode label sets exist in history
For backward compatibility with old CSVs:
- **Original** (Session 1 → 2): `idle_position`, `position_early_stance`, `cur_ramp_up`, `cur_ramp_down`, `position_late_stance`.
- **Current** (Session 3 onward): `idle_no_slack`, `early_stance_no_slack`, `cur_ramp_up`, `cur_ramp_down`, `late_stance_no_slack`.

[`Analysis2.py`](DataLog/analysis/Analysis2.py) treats `controller_mode` as a free-form string (uses pandas categorical), so both work. Don't hard-code mode names in analysis without checking both sets.

### 4. FlexSEA DataLog files are renamed on cleanup
**Don't rely on the original timestamped names** for L/R disambiguation. After [`tag_datalog`](exo_init.py) runs, files are like `Data2026-04-28_..._LEFT_id50969_MAX1_Familiarization.csv`. The renaming happens in [`_cleanup`](perception_test.py) which is in the `finally` block — so it runs even on errors.

### 5. The per-stride CSVs in [`data/`](data/) are NOT the primary diagnostic source anymore
Look at `*_full.csv` files (one per boot per phase) instead. The old per-stride files (`{pid}_Familiarization_{ts}.csv`) only get rows on HS events and can be empty for failed runs.

---

## Procedure for new AI agents

When the user reports a problem:

1. **Don't guess.** Run [`Analysis2.py`](DataLog/analysis/Analysis2.py) first:
   ```bash
   python DataLog/analysis/Analysis2.py --pair --participant <ID> --phase Familiarization
   ```
2. Read `summary.txt` for both L and R. Check the FLAGS section.
3. Open `startup_zoom.png` first — most failures show up in the first 8 s.
4. Open `faults.png` to see fault windows.
5. **Cross-check Cmd current vs Meas current.** If meas > cmd, position control is fighting the user. If meas << cmd, motor is faulted.
6. **Cross-check Cmd τ vs `peak_torque_norm × user_weight`.** Mismatch = profile-init bug.
7. **Read this changelog** before suggesting changes that touch [`exo_init.py`](exo_init.py).

When the user asks for a code change:

1. If it touches gait detection, the Collins profile, or motor commanding — search this changelog for "Architectural Decisions" first.
2. Use the `multi_replace_string_in_file` tool for parallel edits across files.
3. Run `get_errors` on every file you touched after editing.
4. Don't create new markdown files unless explicitly requested.

---

## Files added/modified across all sessions

| File | Status | Purpose |
|---|---|---|
| [`exo_logger.py`](exo_logger.py) | **NEW** (Session 1) | Per-sample CSV logger |
| [`DataLog/analysis/Analysis2.py`](DataLog/analysis/Analysis2.py) | **NEW** (Session 1) | Diagnostic plots + summary |
| [`exo_init.py`](exo_init.py) | Modified (S1, S2, S3) | DataLog tag, logger hook, sensor expansion, position→current control swap |
| [`perception_test.py`](perception_test.py) | Modified (S1) | Logger attach/detach, DataLog rename in cleanup |
| [`gui.py`](gui.py) | Modified (S1) | Persistent `GUIlog_*.txt` |
| [`config.py`](config.py) | Modified (S2) | `PEAK_CURRENT`, `DEFAULT_PEAK_TORQUE_NORM` lowered |
| [`sequential_change_log.md`](sequential_change_log.md) | **NEW** (this file) | Cross-session memory |
| [`CLAUDE.md`](CLAUDE.md) | Pre-existing | General onboarding (some entries now stale; see this file for current state) |

---

## Outstanding items / known limitations

1. **Walk test post-Session-3 fix not yet validated on hardware.** The `NO_SLACK_CURRENT` swap is logically sound but needs at least one clean walk-test run for confirmation.
2. **Inter-subject robustness for very slow walkers** is not yet verified. Fixed gyroz thresholds in [`config.py`](config.py) may not fire for participants walking < 0.7 m/s. Adaptive auto-calibration was discussed but not implemented (would go in [`sensor_check`](perception_test.py) at start of each phase).
3. **Battery / status / position-error columns** are only present in CSVs from Session 2 onward. Older CSVs (MAX1 and earlier) still work with [`Analysis2.py`](DataLog/analysis/Analysis2.py) but those plots will be skipped.
4. **No automated end-to-end hardware test.** The pytest suite ([`tests/`](tests/)) covers offline math but cannot detect issues like the position-error spike — those only show up on a real boot under walking load.

---

## Update protocol for this file

When you (the AI agent) make a change that:
- Modifies the controller's behaviour during walking, or
- Changes the logger schema, or
- Changes the analysis plots, or
- Diagnoses a new failure mode from real data,

**append a new "Session N" section to this file** with:
- Date and what test triggered it
- Diagnosis evidence (specific column values, not vibes)
- Changes made (file by file)
- Reasoning
- Whether the change has been validated on hardware

Don't rewrite history — append. Future you will thank you.

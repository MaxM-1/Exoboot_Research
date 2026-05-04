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

## Session 4 — 2026-04-29: First successful walk tests (MAX4, SAV1) → tuned heel-strike sensitivity

### Test details
- **MAX4** (Max, 95 kg, 1.25 m/s, ~97 s): familiarization started before treadmill — **felt good, worked as intended.** No motor faults. Torque applied at correct times. User reported some missed timings that the controller recovered from on the next stride.
- **SAV1** (Sav, 75 kg default, 1.25 m/s, ~78 s): also worked. Sav reported 0.12 Nm/kg felt slightly high for her weight.
- **No "sudden yank" felt by either user**, despite Analysis2.py reporting one in summary.txt.

**🎉 The Session 3 NO_SLACK_CURRENT swap is validated on hardware.** Position-control-during-walking is officially gone for good.

### Diagnosis from logger data

Counting ARM events vs TRIGGER events:

| Run | ARM | TRIGGER | Lost ARMs |
|---|---|---|---|
| MAX4 L | 72 | 56 | 16 (22 %) |
| MAX4 R | 72 | **46** | **26 (36 %)** |
| SAV1 L | 69 | 65 | 4 (6 %) |
| SAV1 R | 69 | 61 | 8 (12 %) |

MAX4 had a serious right-side miss rate. Looking at every ARM in 7-25 s for MAX4 R:
- Successful triggers: `min(gyroz)` reached **−4990 to −6296** in the armed window.
- Failed (expired) ARMs: `min(gyroz)` only reached **−3776 to −4869** in the armed window.

Old [`HEELSTRIKE_THRESHOLD_BELOW = −150 / BIT_TO_GYRO_COEFF ≈ −4920`](config.py) was set right at MAX's typical heel-strike dip magnitude → ~30 % of his real heel-strikes never crossed it.

### "Sudden yank" warning was a false positive
In current-control walking, [`mot_pos_setpoint`](exo_init.py) is never updated and stays at 0, so [`mot_pos_error = setpoint − actual ≈ −mot_ang_zeroed`](exo_init.py) is always huge (~11 000 ticks). Old [`Analysis2.py`](DataLog/analysis/Analysis2.py) reported this as a yank. Neither user felt anything — it's a stale-column artifact.

### Treadmill ramp-up
User noted the treadmill takes ~2 strides per foot to reach steady-state speed after Start. With `NUM_GAIT_TIMES_TO_AVERAGE = 3`, those longer-than-steady-state ramp strides got baked into the median estimate. Bumping the buffer to 5 makes the median robust to that and to single-stride misses.

### Changes made

#### A. [`config.py`](config.py)
- `HEELSTRIKE_THRESHOLD_BELOW`: **−150 / BIT_TO_GYRO_COEFF (≈ −4920) → −100 / BIT_TO_GYRO_COEFF (≈ −3280)**. Now symmetric with the +3280 ARM threshold. Catches the real HS dips that were being missed for heavier walkers.
- `NUM_GAIT_TIMES_TO_AVERAGE`: **3 → 5**. Median of 5 strides is robust to (a) the first 1-2 strides being affected by treadmill ramp-up and (b) occasional outliers from missed HS.

#### B. [`Analysis2.py`](DataLog/analysis/Analysis2.py)
- Position-error "sudden yank" flag now only computes over rows whose `controller_mode` is in `{idle_position, position_early_stance, position_late_stance, encoder_check, zero_boot}`. Walking runs (current control only) report `n/a (no position-control phases in this run)` and never trigger the false yank warning.

### Status
- **Validated on hardware**: position→current control swap (Session 3 fix) ✅
- **Pending validation on hardware**: trigger-threshold change and 5-stride averaging (this session). Expected outcome: MAX4-class miss ratio drops from ~30 % to <10 %.
- **Watch**: with the lower trigger magnitude (−3280 instead of −4920), in principle a contralateral cross-talk dip during own swing could fire a false trigger. Mitigations already in place: `armed_time > 100 ms` AND past refractory (`max(650, 0.6 × exp_dur)` ms). If false triggers appear, push back to `−110 / BIT_TO_GYRO_COEFF (≈ −3608)` as a middle ground.

### Open question (user)
> "torque felt a little low for me" (MAX, 95 kg). After validating heel-strike fix, can consider gradually raising `DEFAULT_PEAK_TORQUE_NORM` from 0.12 toward Sav-comfortable level (~0.10) up to a published Collins reference (~0.18-0.20). Don't jump back to 0.225 — that's where MAX1 over-currented at 28 A.

---

## Session 5 — 2026-05-03: Torque→current conversion fixed for ActPack 4.1 (root cause of "phantom" over-current)

### Problem reported
> "I am trying to troubleshoot my current control as I think that there might be an issue with how the current is being calculated [...] I have just been putting bandaids on a larger fix and that fix being the current calculated."

User reviewed the [`ankle_torque_to_current`](exo_init.py) chain with their advisor and questioned the legacy `* sqrt(2) / 0.537` factor that had been inherited verbatim from Peng's controller.

### Diagnosis (from datasheet, not from logger data)

**Smoking gun — ActPack 4.1 datasheet, Table 2 footnote (provided by user):**

> "ActPack 0.2B and 4.1 are electrically the same, but 4.1 reports the **Q-axis motor current** which is **38 % of the magnitude** of the current reported by 0.2B, hence the different rating. This is also reflected by the increase in torque constant from **56 to 140 mNm/A**."

Compute the magic number from the old formula:

$$\frac{0.537}{\sqrt{2}} = 0.3797 \approx 0.38$$

So Peng's line:
```python
Dephy_current = q_axis_current * sqrt(2) / 0.537   # = q_axis_current / 0.38
```
was converting **q-axis current → 0.2B's "magnitude" current register**. On ActPack 0.2B, `mot_cur` and `command_motor_current` both used peak-phase magnitude units, and `kt ≈ 56 mNm/A` (magnitude frame).

On **ActPack 4.1** (our hardware), `mot_cur` and `command_motor_current` are **already Q-axis**, and the published `kt = 140 mNm/A` is the Q-axis constant. Applying `× sqrt(2)/0.537` on top **scales the command up by 1/0.38 ≈ 2.63×**, asking for 2.63× more current than the desired torque physically requires.

This is consistent with every "phantom" over-current we've seen since Session 1:
- MAX1 commanding 21.4 Nm (= 0.225 × 95) was *really* trying to deliver ~56 Nm-equivalent of motor torque → 28 A clamp ✓
- MAX2/MAX3 with 0.12 Nm/kg × 85 = 10.2 Nm cmd τ saw 26 A measured even with 15 A clamps in place → consistent with 2.63× over-command driving the position-control loop into the rails (Session 3 was a real bug too, but this factor was compounding it)
- Sav reporting 0.12 Nm/kg "felt slightly high" → because she was actually getting ~0.32 Nm/kg

### Changes made

#### A. [`exo_init.py`](exo_init.py) — removed the 0.2B compatibility scale
Old:
```python
def ankle_torque_to_current(self, torque_mnm):
    q_axis_current = (torque_mnm / self.wm_wa) / 1000.0 / self.kt   # A
    dephy_current = q_axis_current * sqrt(2) / 0.537
    return dephy_current  # A
```
New:
```python
def ankle_torque_to_current(self, torque_mnm):
    # ActPack 4.1, Direct Drive (1:1). mot_cur and command_motor_current
    # are already Q-axis; kt = 0.140 Nm/A is Q-axis. No extra scale.
    q_axis_current = (torque_mnm / self.wm_wa) / 1000.0 / self.kt   # A
    return q_axis_current
```
Also updated the [`self.kt`](exo_init.py) comment block to cite the Table 1 entry and explicitly forbid re-introducing the rescale, and removed the now-unused `from math import sqrt` import.

The corrected analytical relation is:

$$I_{cmd}\,[\text{mA}] = \frac{\tau_{ankle}\,[\text{Nm}]}{w_m/w_a \cdot k_t} \cdot 1000, \quad k_t = 0.140\,\text{Nm/A}$$

#### B. [`AGENTS.md`](AGENTS.md) — added a hard rule
The "do not regress" list now includes a line forbidding re-introduction of the 0.2B rescale.

#### C. [`CLAUDE.md`](CLAUDE.md) — added datasheet pointers
Hardware Constraints section now states the device reports Q-axis current and points to this session for the derivation.

### Expected effect on the next walk test

For the same `peak_torque_norm × weight`, commanded mA will drop by factor 0.38 (= ~2.63× lower). Concretely, with the current [`config.py`](config.py) values (`DEFAULT_PEAK_TORQUE_NORM = 0.12`, `PEAK_CURRENT = 15000`):

| Participant | Old peak τ → cmd I (mA, mid-stride wm_wa ≈ 50) | New peak I (mA) |
|---|---|---|
| 70 kg | 8.4 Nm → ~12 600 mA (often clipped) | ~4 800 mA |
| 85 kg (Max@85) | 10.2 Nm → ~15 000 mA (always clipped) | ~5 800 mA |
| 95 kg (Max@95) | 11.4 Nm → 15 000 mA (clipped) | ~6 500 mA |

So for the first time the controller will deliver the *true* Collins peak torque the user dialed in, instead of ~38 % of it. Participants who said "felt low" (Max in Session 4) and "felt slightly high" (Sav in Session 4) need to be **re-baselined** — their previous comfort settings reflected the over-commanded scale.

### Validation plan (do this before resuming participant runs)

1. **Bench test first** with [`PID testing/Bench_PID_Test3.py`](PID%20testing/Bench_PID_Test3.py) — boot off the participant. Manually rotate the ankle through ROM under a constant `self.tau` setpoint and verify:
   - Logged `mot_cur` ≈ `(self.tau / wm_wa / kt) × 1000` mA at quasi-static load.
   - No fault registers fire at the new (lower) commanded currents.
2. **Re-evaluate [`PEAK_CURRENT`](config.py)**. The 15000 mA clamp was set defensively to mask the 2.63× over-command. After this fix, Collins peaks up to ~0.20 Nm/kg × 95 kg ≈ 19 Nm should request only ~10 800 mA — well under 15 A. Don't reflexively raise the clamp; verify the participant feels the *correct* assistance level first.
3. **Re-baseline perception**. Sav's comfortable level is now likely 0.20–0.25 Nm/kg (was 0.10–0.12 under the old scale). Max likewise.

### Status
- **Code change**: Applied. Offline `pytest` passes (no hardware required).
- **Hardware validation**: NOT yet performed. Bench test is mandatory before next participant.
- **Open question**: After validation, consider whether [`PEAK_CURRENT`](config.py) and [`DEFAULT_PEAK_TORQUE_NORM`](config.py) should be raised back toward published Collins values (~0.18–0.20 Nm/kg). Do not change them in the same session as this fix — change one variable at a time.

---

## Session 6 — 2026-05-04: Perception-test architectural overhaul (peak-time mode + GUI/analysis upgrade)

### Problem reported (SAV6, first perception-test run)

After a successful familiarization series, the user attempted the full perception protocol for the first time and surfaced five issues:

1. **Rise/fall coupling was wrong.** The perception protocol independently varied either rise time *or* fall time while holding the other constant. With actuation start (`T_ACT_START`) and end (`T_ACT_END`) both fixed in the user's experimental design, varying only one of rise/fall produced a flat plateau at the top of the torque curve — *not* the cubic-cubic Collins shape the participant was supposed to be feeling. The varying parameter is conceptually **peak time**, with rise and fall durations coupled to it.
2. **GUI did not clearly indicate when a response was required**, what the current torque curve looked like, or where the experimenter was within the sweep schedule.
3. **No stride-within-condition counter** — experimenter could not see "stride 3 of 5" inside Timing A or Timing B.
4. **No analysis tooling** for perception-test CSV output (only the per-sample `Analysis2.py` for walk diagnostics).
5. **No "condition is being presented" announcement** — the experimenter could not narrate "Condition 7 starting now" to the participant because the staircase boundaries were invisible from the GUI.

### Diagnosis

This was an **architectural mismatch**, not a bug. Reading [`perception_test.py`](perception_test.py) `_make_profile` confirmed:

- `RISE_TIME_TEST` mode held `t_fall = DEFAULT_T_FALL` constant and slid `t_peak = DEFAULT_T_ONSET + value`. Actuation **start** stayed fixed but actuation **end** drifted.
- `FALL_TIME_TEST` mode held `t_peak = DEFAULT_T_ONSET + DEFAULT_T_RISE` constant. Actuation **start** drifted (because `t0 = t_peak - t_rise = T_ACT_START`, ✅) — wait, actually `t1 = t_peak + t_fall` drifted with `t_fall`. So in fall-mode, `t1` drifted.

Neither mode satisfied the user's actual experimental design where **both** `T_ACT_START` and `T_ACT_END` are constants. The correct single staircase variable is `t_peak`; rise and fall are coupled derivatives.

User-confirmed design constants from the figure annotations (Peng et al. 2022, peak-torque pattern):

- `T_ACT_START = 26.0 %` (actuation onset)
- `T_ACT_END   = 61.6 %` (actuation offset)
- `T_PEAK_REF  = 51.3 %` (reference peak time → reference rise = 25.3 %, fall = 10.3 %)

### Changes made

#### A. [`config.py`](config.py) — peak-time constants
- Added `T_ACT_START = 26.0`, `T_ACT_END = 61.6`, `DEFAULT_T_PEAK = 51.3`, and clamp guards `MIN_RISE = MIN_FALL = 2.0` (% gait) so the cubic does not degenerate near the endpoints.
- Renamed test-mode constant: `PEAK_TIME_TEST = "peak_time"`. `RISE_TIME_TEST` and `FALL_TIME_TEST` are kept as aliases of `PEAK_TIME_TEST` so legacy code/tests continue to import without crashing, but new logic ignores them.
- Existing `DEFAULT_T_RISE`, `DEFAULT_T_FALL`, `DEFAULT_T_ONSET` retained as derived/back-compat values.

#### B. [`perception_test.py`](perception_test.py) — single-variable staircase, richer status

- **`_make_profile`** rewritten as `_make_profile(t_peak, weight, peak_tn)` — derives `t_rise = t_peak - T_ACT_START` and `t_fall = T_ACT_END - t_peak`. No flat plateau is geometrically possible.
- New static helpers:
  - `_clamp_peak(t_peak)` — clamps to `[T_ACT_START + MIN_RISE, T_ACT_END - MIN_FALL]`.
  - `_collins_curve(t_peak, weight, peak_tn, n_pts=201)` — pure-Python Collins cubic-cubic curve sampler (mirrors [`exo_init.init_collins_profile`](exo_init.py) coefficients). Used to send live torque curves to the GUI without any flexsea dependency.
- **Familiarization mode**: Increase/Decrease now slides `t_peak` (not `t_rise` / `t_fall`). Per-stride CSV gains `t_peak` column.
- **Perception mode** (`_run_perception`):
  - `reference_value = DEFAULT_T_PEAK`. Initial comparison `= reference ± INITIAL_OFFSET` with clamping. Direction sign unchanged (Different → step toward reference; Same → step away).
  - New status messages emitted to the GUI:
    - `condition_announce {label, trial, est_total, is_practice}` — sent once at trial start so the experimenter can read aloud "Condition N".
    - `catch_flag {is_catch}` — small experimenter-only red tag.
    - `trial_phase {phase, label?, t_peak?}` — `warmup_light | warmup_collins | timing_A | timing_B | response_wait | rest`.
    - `stride_progress {k, n, phase}` — current stride within condition (1..5).
    - `profile_preview {ref, comp, ref_label, comp_label}` — two `(xs, ys)` lists for the live matplotlib preview. Sent twice per trial (warm-up + every trial start), not per loop.
  - **Trial CSV schema changed** (breaking change for any old reader):
    - `Trial #, Sweep #, Delta, Approach, Reference t_peak, Comparison t_peak, t_rise_comp, t_fall_comp, Phase Order, Is Reversal, Response, Catch Trial`.
    - Old columns `Test Mode`, `Reference Value`, `Comparison Value` are gone.
  - **Per-stride CSV schema changed**:
    - `state_time, t_peak, trial_phase, stride_in_condition, est_stride_dur, actual_stride_dur`.
    - Old `varied_value` column is gone.

#### C. [`gui.py`](gui.py) — peak-time UI, embedded torque preview

- **Setup pane**: Removed the rise/fall radio (`mode_group`). Only the from-above / from-below approach radio remains. `_collect_params` always sends `test_mode = PEAK_TIME_TEST`.
- **Status pane** completely rebuilt:
  - Big condition banner (16 pt bold, blue background; yellow background during practice trials).
  - Color-coded phase indicator (gray = warm-up / rest, blue = Timing A, orange = Timing B, **green** = "▶ RESPOND  Same / Different").
  - Stride counter "Stride k/5 (phase A|B)".
  - Trial / sweep progress "Trial: N   Sweep: x/9".
  - Reference and comparison `t_peak` lines, with the comparison showing Δ vs reference.
  - Small red **CATCH TRIAL** tag on the State row (experimenter-only).
  - **Embedded matplotlib preview** (`FigureCanvasQTAgg`) — two persistent `Line2D` artists (reference black, comparison dashed red) updated via `set_data` + `draw_idle`. Only redraws on `profile_preview` messages (twice per trial), so cost is negligible.
- **Button gating** (`_update_button_states`): tracks `_mode_active ∈ {fam, perception, None}`. Familiarization Increase/Decrease are now disabled during perception, and re-disabled when the experiment thread terminates.
- Window title is now `"Peak-Time Perception Experiment"`. Resized to 820×980 to fit the preview canvas.

#### D. [`DataLog/analysis/perception_plots.py`](DataLog/analysis/perception_plots.py) — **NEW** diagnostic suite

Per-session diagnostics for perception data. **Does not** fit psychometric functions / PSE / JND (per user request, this is graphs-only).

Outputs (all PNG @ 130 dpi, written to `<trial-csv-stem>_plots/`):

| File | Content |
|---|---|
| `staircase.png` | Comparison `t_peak` vs trial #, separate panels per approach. Markers: ○ Same, ■ Different. White-fill = catch. Blue halo = reversal. Reference dotted line. |
| `reversals.png` | Reversal-only trajectory per approach, connected. |
| `stride_dur.png` | Boxplot of `actual_stride_dur` grouped by `trial_phase` (A vs B), separate axes for L and R. |
| `profile_gallery.png` | Every comparison's Collins curve overlaid, viridis-colored by trial order, with reference in black. Vertical dotted lines at `T_ACT_START` and `T_ACT_END`. |
| `summary.txt` | Total / real / catch trial counts, reversal count, **catch false-alarm rate**, per-approach trial counts. |

CLI: `--latest`, `--participant <pid>`, positional path, `--weight` (default 75 kg).

**File-discovery gotcha (real bug, fixed during the session)**: `data/` contains both the trial CSV (`{pid}_Perception_{ts}.csv`) and per-sample `ExoLogger` files (`{pid}_Perception_{L|R}_{ts}_full.csv`) and per-stride files (`{pid}_PerceptionStride_{L|R}_{ts}.csv`). The first glob attempted on `SAV_Perception_3_Perception_*.csv` matched the `..._Perception_R_..._full.csv` ExoLogger file and crashed with `KeyError: 'Approach'`. The script now uses `_is_trial_csv` to reject any path containing `PerceptionStride`, ending in `_full.csv`, or whose post-`_Perception_` suffix starts with `L_` or `R_`.

#### E. [`tests/test_perception_helpers.py`](tests/test_perception_helpers.py) — updated to peak-time API

- `test_make_profile_for_rise_time` / `test_make_profile_for_fall_time` removed.
- New tests:
  - `test_make_profile_at_reference_peak` — `t_rise + t_fall = T_ACT_END - T_ACT_START`.
  - `test_make_profile_couples_rise_and_fall` — sliding peak later increases rise, decreases fall, and `t_peak ± rise/fall` recovers `T_ACT_START` / `T_ACT_END` to within 1e-9.
  - `test_clamp_peak_respects_min_rise_fall`.
- All 26 perception/config tests pass. (One pre-existing failure in `tests/test_exo_math.py::test_stride_duration_uses_median_and_rejects_outlier` — present on `main` before this session, **not introduced by this work**.)

### Reasoning notes for future agents

- **Why drop the rise/fall radio entirely?** Both modes now compute the same thing internally (`t_peak` staircase). Keeping the radio would only let the user pick the *initial offset sign*, which the from-above / from-below approach radio already handles. Removed to avoid a redundant control that confused the user during SAV6.
- **Why a live matplotlib preview, not a static image?** Two reasons: (a) the comparison curve changes every trial, so a static image would lie; (b) it's also useful in familiarization mode where the user is dialing `t_peak` manually — they can now see the curve update each time they press Increase/Decrease. Performance cost is bounded by `draw_idle` rate-limiting and the two-call-per-trial cadence in perception mode.
- **Why expose Δ vs reference in the GUI but hide the comparison `t_peak` numeric only behind a toggle?** This was *discussed* in the further-considerations list (Option 3 in the planning step) but the user did not opt in. The numeric `t_peak` is shown by default. If experimenter-blinding becomes a concern later, hide `lbl_comp` behind a checkbox.
- **Backward compatibility of trial CSVs**: any legacy CSV using `Test Mode` / `Reference Value` / `Comparison Value` columns will fail in `perception_plots.py` because the column names changed. There are no committed legacy perception CSVs in the repo right now, so this is a clean break — but if old files surface, write a one-shot migration helper rather than re-introducing the old columns.

### Status

- **Code change**: Applied. Offline `pytest` passes (26 perception/config tests; one unrelated `test_exo_math.py` failure pre-dates this session).
- **Hardware validation**: Partial. SAV_Perception_3 walk run completed (31 trials, 19 reversals, 25 % catch false-alarm rate per `summary.txt`). Trial CSV is well-formed and `perception_plots.py --participant SAV_Perception_3` produces all four PNGs successfully. Live GUI feedback (banner / phase / stride / preview) was not yet stress-tested across a multi-sweep session.
- **Open items**:
  1. Audio cue for response prompt was deliberately **not** added (user opted out).
  2. Catch-trial blinding policy: currently the red **CATCH TRIAL** tag is visible on the same screen the participant might glance at. If this becomes an issue, move it to a separate experimenter-only display or a small status-bar strip.
  3. The reversal-count progress (`sweep_num`) only updates on response polarity changes. For sessions where the participant gives many same-direction responses in a row, the GUI may seem stuck. Consider also surfacing the trial count toward `est_total_trials`.

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
| [`DataLog/analysis/perception_plots.py`](DataLog/analysis/perception_plots.py) | **NEW** (Session 6) | Per-session perception diagnostics (staircase, reversals, stride-dur, profile gallery) |
| [`exo_init.py`](exo_init.py) | Modified (S1, S2, S3, S5) | DataLog tag, logger hook, sensor expansion, position→current control swap, kt fix |
| [`perception_test.py`](perception_test.py) | Modified (S1, S6) | Logger attach/detach, DataLog rename in cleanup; peak-time staircase + new status messages |
| [`gui.py`](gui.py) | Modified (S1, S6) | Persistent `GUIlog_*.txt`; condition banner / phase indicator / stride counter / live torque preview |
| [`config.py`](config.py) | Modified (S2, S5, S6) | `PEAK_CURRENT`, `DEFAULT_PEAK_TORQUE_NORM`; peak-time constants `T_ACT_START`/`T_ACT_END`/`DEFAULT_T_PEAK`/`MIN_RISE`/`MIN_FALL`/`PEAK_TIME_TEST` |
| [`tests/test_perception_helpers.py`](tests/test_perception_helpers.py) | Modified (S6) | Updated to peak-time `_make_profile` API |
| [`sequential_change_log.md`](sequential_change_log.md) | **NEW** (this file) | Cross-session memory |
| [`CLAUDE.md`](CLAUDE.md) | Pre-existing | General onboarding (some entries now stale; see this file for current state) |

---

## Outstanding items / known limitations

1. **Walk test post-Session-3 fix not yet validated on hardware.** The `NO_SLACK_CURRENT` swap is logically sound but needs at least one clean walk-test run for confirmation.
2. **Inter-subject robustness for very slow walkers** is not yet verified. Fixed gyroz thresholds in [`config.py`](config.py) may not fire for participants walking < 0.7 m/s. Adaptive auto-calibration was discussed but not implemented (would go in [`sensor_check`](perception_test.py) at start of each phase).
3. **Battery / status / position-error columns** are only present in CSVs from Session 2 onward. Older CSVs (MAX1 and earlier) still work with [`Analysis2.py`](DataLog/analysis/Analysis2.py) but those plots will be skipped.
4. **No automated end-to-end hardware test.** The pytest suite ([`tests/`](tests/)) covers offline math but cannot detect issues like the position-error spike — those only show up on a real boot under walking load.
5. **Perception-test trial CSV schema changed in Session 6.** Old columns `Test Mode`, `Reference Value`, `Comparison Value` are gone; replaced with `Approach`, `Reference t_peak`, `Comparison t_peak`, `t_rise_comp`, `t_fall_comp`, `Phase Order`, `Is Reversal`. [`perception_plots.py`](DataLog/analysis/perception_plots.py) only reads the new schema. If old-schema CSVs surface, write a migration helper rather than re-introducing legacy columns.
6. **Catch-trial blinding.** The red `CATCH TRIAL` tag in the GUI is currently visible to anyone glancing at the screen. If participant blinding becomes a concern, hide the tag behind an experimenter-only toggle or move it to a separate display.

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

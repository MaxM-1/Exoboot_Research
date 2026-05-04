# CLAUDE.md — AI Agent Context

> **READ FIRST**: For active troubleshooting context, controller-design decisions, and a log of what has already been tried (and what NOT to re-suggest), read [sequential_change_log.md](sequential_change_log.md) **before** making changes to [exo_init.py](exo_init.py), [perception_test.py](perception_test.py), or anything related to motor control / gait detection. This file (CLAUDE.md) is the static project overview; `sequential_change_log.md` is the live debugging journal.

## What This Project Is

Dual-experiment perception suite for the Dephy ExoBoot ankle exoskeleton. The participant walks in two exoboots while an adaptive staircase finds a Just Noticeable Difference (JND). One of two experiment types is selected from the GUI radio (`experiment_type` in the params dict; default = MAX):

- **MAX** (`MAX_EXPERIMENT = "max"`) — varies the **peak time** of the Collins torque profile. Rise and fall durations are coupled to peak time so that **actuation start (26.0 %) and actuation end (61.6 %) stay constant** — only the peak slides. Peak torque is pinned to `MAX_FAM_PEAK_TN = 0.225 Nm/kg`. Step `MAX_DELTA = 1.0 %`, initial offset `MAX_INITIAL_OFFSET = 3.0 %`, rest `MAX_REST_STRIDES = 8`.
- **SAV** (`SAV_EXPERIMENT = "sav"`) — varies the **peak torque magnitude** (`peak_torque_norm`, Nm/kg). All timing pinned (`t_peak = DEFAULT_T_PEAK = 51.3 %`). Reference `SAV_REFERENCE_PEAK_TN = SAV_FAM_PEAK_TN = 0.18`, step `SAV_DELTA = 0.005`, initial offset `SAV_INITIAL_OFFSET = 0.05`, rest `SAV_REST_STRIDES = 15`, clamp `[0.05, 0.30]`.

Both experiments share the A/B comparison, 25 % catch trials, up–down staircase, ~9 sweeps per approach direction. Adapted from Xiangyu Peng's actuation-timing perception research. Rise-time-only and fall-time-only modes from earlier drafts have been removed (Session 6). The MAX/SAV split was added in Session 7 — see [sequential_change_log.md](sequential_change_log.md).

## Architecture

```
gui.py  (main entry, PyQt5 event loop)
  └─> perception_test.py  (PerceptionExperiment, runs in daemon thread)
        └─> exo_init.py  (ExoBoot class, one instance per boot)
              └─> flexsea.device.Device  (Dephy hardware API)

Communication: GUI ←→ Experiment thread via two queue.Queue objects
  - command_queue: GUI sends signals (start, stop, increase, decrease, same, different)
  - status_queue:  Experiment sends status updates back to GUI (polled every 50ms via QTimer)

Signal constants defined in config.py (SIG_STOP, SIG_FAM_BEGIN, etc.)
```

## File Responsibilities

- **config.py** — Single source of truth for ALL constants: hardware config, physical unit conversions, current limits, gait detection thresholds, torque profile defaults, PID gains, protocol parameters, GUI signal codes. Change experiment parameters here.
- **exo_init.py** — `ExoBoot` class wrapping one Dephy boot. Handles: device connection/streaming, encoder zeroing, heel-strike detection (gyro-based), gait cycle percentage tracking, Collins torque profile generation and execution, ankle-torque-to-current conversion, calibration loading from `bootCal.txt`. Uses 2nd-order Butterworth low-pass filter (12 Hz cutoff) for ankle velocity.
- **perception_test.py** — `PerceptionExperiment` class. Manages both boots, runs familiarization (manual reference adjustment) and the perception staircase. Dispatches MAX vs SAV via the `_StaircaseVar` helper which carries reference, delta, clamp, sweep target, rest count, label/units, and a `profile_args(value) → (t_peak, peak_torque_norm)` method. MAX varies `t_peak` (rise/fall coupled, peak torque pinned); SAV varies `peak_torque_norm` (timing pinned at `DEFAULT_T_PEAK`). Handles trial sequencing, catch trials (25 % rate), rest/warm-up strides, CSV logging (trial CSV gained `Experiment Type`, `Reference/Comparison peak_tn`, `Staircase Var`, `Reference Value`, `Comparison Value`; per-stride CSV gained `peak_tn` and `experiment_type`), and emits richer status messages to the GUI: `condition_announce`, `catch_flag`, `trial_phase`, `stride_progress`, `profile_preview`. Static helpers `_clamp_peak` and `_collins_curve` are also used by [DataLog/analysis/perception_plots.py](DataLog/analysis/perception_plots.py).
- **gui.py** — `ExperimentGUI` class (PyQt5 `QMainWindow`). Setup inputs include a **MAX / SAV experiment-type radio** (default MAX) alongside participant ID, weight, ports, and approach direction. Control buttons, real-time status display with: large condition banner, color-coded phase indicator (warm-up / Timing A / Timing B / RESPOND / Rest), stride counter, sweep/trial progress, reference and comparison values rendered with experiment-aware units (`%` for MAX, `Nm/kg` for SAV via `_refresh_reference_label`), embedded matplotlib torque-profile preview (`FigureCanvasQTAgg`), and experimenter-only `CATCH TRIAL` tag. Polls the experiment status queue via a 50 ms `QTimer`. Safe `closeEvent` stops motors before exit with a 3-second fallback timer. All experiment logic is delegated to `PerceptionExperiment`.
- **calibration/boot_calibration.py** — CLI tool. Connects to one boot, applies constant current to tighten belt, records ankle+motor ticks as user dorsiflexes.
- **calibration/calibration_analysis.py** — CLI tool. Reads calibration CSV, fits 5th-order polynomial (ankle ticks → motor ticks), writes coefficients to `bootCal.txt`.
- **data/data_analysis.py** — CLI tool. Computes JND from adaptive staircase CSV data, detects reversals, generates diagnostic plots.
- **exo_logger.py** — `ExoLogger` class. Writes one CSV row per control-loop iteration (~100 Hz), line-buffered so partial files survive crashes. Output: `data/{participant}_{phase}_{L|R}_{timestamp}_full.csv`. This is the **primary diagnostic data source** — prefer it over the per-stride CSVs.
- **DataLog/analysis/Analysis2.py** — CLI diagnostic suite. Run with a file path, `--latest`, or `--pair --participant X --phase Y` for L/R side-by-side comparison. Produces `torque_profile.png`, `controller_timeline.png`, `hs_diagnostics.png`, `kinematics.png`, `startup_zoom.png`, `faults.png`, `battery_status.png`, `side_by_side.png`, `torque_LR_overlay.png`, and `summary.txt`. **Use this first when diagnosing any walk-test problem.**
- **DataLog/analysis/perception_plots.py** — CLI diagnostic suite for **perception-test** sessions. Reads `data/{pid}_Perception_{ts}.csv` plus matching per-stride CSVs. Produces `staircase.png` (per-approach trajectories with response markers + reversal halos), `reversals.png`, `stride_dur.png`, `profile_gallery.png`, and `summary.txt` (counts + catch false-alarm rate). CLI: `--latest`, `--participant <pid>`, positional path. Does **not** fit a psychometric function — graphs only.
- **tests/** — Offline pytest suite. Covers import/syntax smoke checks, config sanity, ExoBoot math helpers without hardware, perception helper methods, calibration/data-analysis helpers with synthetic CSVs, and `ExoLogger` CSV output.

## Key Patterns

- **Gait-cycle control**: Torque is applied based on percent-gait calculated from heel-strike timing. Heel strikes detected via gyro-z threshold crossing with debounce.
- **Adaptive staircase (per experiment type)**: 25 % catch trials. 10 strides per trial (5 Timing A + 5 Timing B, A/B order randomised). Reversal counted on response polarity change. ~9 sweeps per approach direction. Up to 55 trials. JND analysis is downstream and not implemented in this codebase — only diagnostic plots.
  - **MAX**: variable = `t_peak` (% gait). Reference 51.3 %. Initial comparison ±3 % (sign from approach radio). Step 1 %. Peak torque pinned at `MAX_FAM_PEAK_TN = 0.225 Nm/kg`.
  - **SAV**: variable = `peak_torque_norm` (Nm/kg). Reference 0.18. Initial comparison ±0.05. Step 0.005. Clamp [0.05, 0.30]. Timing pinned at `DEFAULT_T_PEAK = 51.3 %`.
- **Thread safety**: All GUI↔experiment communication through queues. No shared mutable state. The `_poll_status` loop catches only `queue.Empty` (not bare `Exception`) so real errors surface.
- **Safe shutdown**: `closeEvent` calls `request_stop()` and defers window close until the experiment thread confirms, with a 3-second fallback timer to guarantee the window closes even if the thread hangs.
- **Calibration direction matters**: Must go plantarflexed → dorsiflexed (belt tightens and pulls). Reverse direction loosens belt and gives no useful mapping.

## Hardware Constraints

- Raspberry Pi 5, Ubuntu, USB 2.0 connections (long cables)
- Two Dephy ExoBoots, firmware 7.2.0, baud rate 230400
- Streaming at **100 Hz** (not 1000 — the firmware-internal control loop is faster, but the host-side stream we read is 100 Hz)
- Serial ports: `/dev/ttyACM0` and `/dev/ttyACM1` (configurable in GUI and config.py)
- Current limits are safety-critical. Current values in [config.py](config.py): `PEAK_CURRENT = 28000` mA, `NO_SLACK_CURRENT = 800` mA, `DEFAULT_PEAK_TORQUE_NORM = 0.225` Nm/kg. The 15 000 mA cap was a Session-2 bandaid before the Session-5 `kt` fix corrected the torque→current scale; with the correct conversion the working ceiling is the firmware ceiling of 28 000 mA. Do not push past the firmware ceiling. See Sessions 5 and 7 in [sequential_change_log.md](sequential_change_log.md).
- **Torque→current conversion (CRITICAL)**: This codebase runs on **ActPack 4.1 (Direct Drive 1:1)**. The device reports `mot_cur` and accepts `command_motor_current` in **Q-axis** units, and the published `kt = 140 mNm/A` is the Q-axis torque constant (datasheet Table 1, 1:1 column). [`ankle_torque_to_current`](exo_init.py) therefore computes `I_q = (tau_mNm / wm_wa) / 1000 / kt` with **no extra scale factors**. Peng's reference controller in [`RESOURCES/`](RESOURCES/) was written for ActPack 0.2B, where `mot_cur` was reported in peak-magnitude units (38 % larger numerical value) and `kt ≈ 56 mNm/A`; that controller multiplied by `sqrt(2) / 0.537` to convert Q-axis→magnitude. **Do not copy that line into the 4.1 controller** — doing so over-commands current by ~2.63×. See Session 5 in [sequential_change_log.md](sequential_change_log.md).

## Important Notes

- **Automated tests exist, but are offline only.** Run `.venv/bin/python -m pytest` from the repo root. These tests do not connect to ExoBoot hardware and do not replace live hardware validation.
- **FlexSEA version**: Uses `flexsea.device.Device` from the current Dephy Actuator-Package (not the legacy `fxs` API that Peng used).
- **bootCal.txt** must exist in `calibration/` before running experiments. Format is INI-style (ConfigParser) with polynomial coefficients per boot ID.
- **RESOURCES/ folder** contains reference materials only (Peng's old controller, Dephy API source, user guides, research papers). Not used at runtime.
- The codebase was AI-generated and has not been fully validated on hardware yet.
- **Walking control strategy (CRITICAL — do not regress)**: During walking, only `cur_ramp_up` and `cur_ramp_down` phases drive the Collins torque pulse. The pre-gait, early-stance, and late-stance phases hold a low constant current (`NO_SLACK_CURRENT * side`) — they do **not** use position control. Position control during walking caused 20 000+ tick position-error spikes at heel-strike that over-currented the motor within a few strides. See [sequential_change_log.md](sequential_change_log.md) Session 3 for the full diagnosis. Position control is still used in `encoder_check` and `zero_boot` (stationary participant only).
- **Diagnostic logger schema**: [exo_logger.py](exo_logger.py) writes ~50 columns per row including `controller_mode` (string), `current_cmd_mA`, `current_meas_mA`, `mot_pos_setpoint`, `mot_pos_error`, `batt_volt_mV`, `status_mn/ex/re`. Older CSVs (pre-Session-2) are missing the battery/status/position-error columns; [Analysis2.py](DataLog/analysis/Analysis2.py) handles missing columns gracefully.
- **Walk-test procedure** (validated): (1) stand still on stopped treadmill, (2) Connect & Zero, (3) press Start Familiarization while still standing, (4) wait 2–3 s, (5) start treadmill and ramp to 1.25 m/s. The standing-still pre-tension is rejected by the >3000 ms stride sanity check, so it does not pollute stride-time averaging.
- **FlexSEA DataLog files are auto-renamed** on cleanup with side / boot ID / participant / phase. Look for files like `Data*_LEFT_id<bootid>_<PID>_<phase>.csv` in [DataLog/](DataLog/) — the original timestamped name is replaced.

## Common Tasks

- **Change torque profile defaults**: Edit `config.py` constants (`DEFAULT_T_PEAK`, `T_ACT_START`, `T_ACT_END`, `MAX_FAM_PEAK_TN`, `SAV_FAM_PEAK_TN`, etc.)
- **Change MAX protocol parameters**: Edit `MAX_*` constants in `config.py` (`MAX_DELTA`, `MAX_INITIAL_OFFSET`, `MAX_TOTAL_SWEEPS`, `MAX_REST_STRIDES`, `MAX_FAM_DELTA`).
- **Change SAV protocol parameters**: Edit `SAV_*` constants in `config.py` (`SAV_DELTA`, `SAV_INITIAL_OFFSET`, `SAV_TOTAL_SWEEPS`, `SAV_REST_STRIDES`, `SAV_FAM_DELTA`, `SAV_REFERENCE_PEAK_TN`, `SAV_MIN_PEAK_TN`, `SAV_MAX_PEAK_TN`).
- **Add a new experiment type**: define new constants in `config.py`, extend the `_StaircaseVar` dispatch in `perception_test.py`, add a radio button in `gui.py`, branch in `DataLog/analysis/perception_plots.py`.
- **Add a new sensor reading**: Extend `ExoBoot.read_data()` in `exo_init.py`
- **Modify trial logic**: Edit `PerceptionExperiment._run_perception()` in `perception_test.py`
- **Change GUI layout**: Edit `ExperimentGUI._build_*_group()` methods in `gui.py` (PyQt5 `QGroupBox` / `QFormLayout` / `QHBoxLayout`)
- **Run offline tests**: Install `requirements-dev.txt`, then run `.venv/bin/python -m pytest`

# CLAUDE.md — AI Agent Context

> **READ FIRST**: For active troubleshooting context, controller-design decisions, and a log of what has already been tried (and what NOT to re-suggest), read [sequential_change_log.md](sequential_change_log.md) **before** making changes to [exo_init.py](exo_init.py), [perception_test.py](perception_test.py), or anything related to motor control / gait detection. This file (CLAUDE.md) is the static project overview; `sequential_change_log.md` is the live debugging journal.

## What This Project Is

Rise/fall time perception experiment for the Dephy ExoBoot ankle exoskeleton. The participant walks in two exoboots while an adaptive staircase protocol varies the rise or fall time of the torque profile to find their Just Noticeable Difference (JND). This is adapted from Xiangyu Peng's actuation-timing perception research — same protocol structure, different independent variable (rise/fall time instead of actuation onset/offset), and updated from old FlexSEA to the current Dephy Python API.

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
- **perception_test.py** — `PerceptionExperiment` class. Manages both boots, runs familiarization (manual adjustment) and perception test (adaptive staircase). Handles trial sequencing, catch trials (25% rate), rest/warmup strides, CSV logging of all trial data.
- **gui.py** — `ExperimentGUI` class (PyQt5 `QMainWindow`). Setup inputs (participant ID, weight, ports, test mode), control buttons, real-time status display, scrollable log. Polls the experiment status queue via a 50 ms `QTimer`. Safe `closeEvent` stops motors before exit with a 3-second fallback timer. All experiment logic is delegated to `PerceptionExperiment`.
- **calibration/boot_calibration.py** — CLI tool. Connects to one boot, applies constant current to tighten belt, records ankle+motor ticks as user dorsiflexes.
- **calibration/calibration_analysis.py** — CLI tool. Reads calibration CSV, fits 5th-order polynomial (ankle ticks → motor ticks), writes coefficients to `bootCal.txt`.
- **data/data_analysis.py** — CLI tool. Computes JND from adaptive staircase CSV data, detects reversals, generates diagnostic plots.
- **exo_logger.py** — `ExoLogger` class. Writes one CSV row per control-loop iteration (~100 Hz), line-buffered so partial files survive crashes. Output: `data/{participant}_{phase}_{L|R}_{timestamp}_full.csv`. This is the **primary diagnostic data source** — prefer it over the per-stride CSVs.
- **DataLog/analysis/Analysis2.py** — CLI diagnostic suite. Run with a file path, `--latest`, or `--pair --participant X --phase Y` for L/R side-by-side comparison. Produces `torque_profile.png`, `controller_timeline.png`, `hs_diagnostics.png`, `kinematics.png`, `startup_zoom.png`, `faults.png`, `battery_status.png`, `side_by_side.png`, `torque_LR_overlay.png`, and `summary.txt`. **Use this first when diagnosing any walk-test problem.**
- **tests/** — Offline pytest suite. Covers import/syntax smoke checks, config sanity, ExoBoot math helpers without hardware, perception helper methods, calibration/data-analysis helpers with synthetic CSVs, and `ExoLogger` CSV output.

## Key Patterns

- **Gait-cycle control**: Torque is applied based on percent-gait calculated from heel-strike timing. Heel strikes detected via gyro-z threshold crossing with debounce.
- **Adaptive staircase**: Starts at ±3% offset from reference. Step size 1%. 25% catch trials. 10 strides per condition (5 stimulus + 5 response). Max 55 trials. JND = mean of last N reversals.
- **Thread safety**: All GUI↔experiment communication through queues. No shared mutable state. The `_poll_status` loop catches only `queue.Empty` (not bare `Exception`) so real errors surface.
- **Safe shutdown**: `closeEvent` calls `request_stop()` and defers window close until the experiment thread confirms, with a 3-second fallback timer to guarantee the window closes even if the thread hangs.
- **Calibration direction matters**: Must go plantarflexed → dorsiflexed (belt tightens and pulls). Reverse direction loosens belt and gives no useful mapping.

## Hardware Constraints

- Raspberry Pi 5, Ubuntu, USB 2.0 connections (long cables)
- Two Dephy ExoBoots, firmware 7.2.0, baud rate 230400
- Streaming at **100 Hz** (not 1000 — the firmware-internal control loop is faster, but the host-side stream we read is 100 Hz)
- Serial ports: `/dev/ttyACM0` and `/dev/ttyACM1` (configurable in GUI and config.py)
- Current limits are safety-critical. Current values in [config.py](config.py): `PEAK_CURRENT = 15000` mA, `NO_SLACK_CURRENT = 800` mA, `DEFAULT_PEAK_TORQUE_NORM = 0.12` Nm/kg. **Do not raise `PEAK_CURRENT` above 15000 without first reviewing [sequential_change_log.md](sequential_change_log.md) — the firmware ceiling of 28000 mA over-currents the motor under sustained load.**
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

- **Change torque profile defaults**: Edit `config.py` constants (`DEFAULT_RISE_TIME`, `DEFAULT_FALL_TIME`, `PEAK_TORQUE_NM_KG`, etc.)
- **Change protocol parameters**: Edit `config.py` (`INITIAL_OFFSET_PCT`, `STEP_SIZE_PCT`, `MAX_TRIALS`, `CATCH_TRIAL_RATE`, etc.)
- **Add a new sensor reading**: Extend `ExoBoot.read_data()` in `exo_init.py`
- **Modify trial logic**: Edit `PerceptionExperiment._run_perception()` in `perception_test.py`
- **Change GUI layout**: Edit `ExperimentGUI._build_*_group()` methods in `gui.py` (PyQt5 `QGroupBox` / `QFormLayout` / `QHBoxLayout`)
- **Run offline tests**: Install `requirements-dev.txt`, then run `.venv/bin/python -m pytest`

# CLAUDE.md — AI Agent Context

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

## Key Patterns

- **Gait-cycle control**: Torque is applied based on percent-gait calculated from heel-strike timing. Heel strikes detected via gyro-z threshold crossing with debounce.
- **Adaptive staircase**: Starts at ±3% offset from reference. Step size 1%. 25% catch trials. 10 strides per condition (5 stimulus + 5 response). Max 55 trials. JND = mean of last N reversals.
- **Thread safety**: All GUI↔experiment communication through queues. No shared mutable state. The `_poll_status` loop catches only `queue.Empty` (not bare `Exception`) so real errors surface.
- **Safe shutdown**: `closeEvent` calls `request_stop()` and defers window close until the experiment thread confirms, with a 3-second fallback timer to guarantee the window closes even if the thread hangs.
- **Calibration direction matters**: Must go plantarflexed → dorsiflexed (belt tightens and pulls). Reverse direction loosens belt and gives no useful mapping.

## Hardware Constraints

- Raspberry Pi 5, Ubuntu, USB 2.0 connections (long cables)
- Two Dephy ExoBoots, firmware 7.2.0, baud rate 230400
- Streaming at 1000 Hz
- Serial ports: `/dev/ttyACM0` and `/dev/ttyACM1` (configurable in GUI and config.py)
- Current limits are safety-critical — defined in config.py (`PEAK_CURRENT_MA = 28000`)

## Important Notes

- **No automated tests.** Testing requires physical ExoBoot hardware.
- **FlexSEA version**: Uses `flexsea.device.Device` from the current Dephy Actuator-Package (not the legacy `fxs` API that Peng used).
- **bootCal.txt** must exist in `calibration/` before running experiments. Format is INI-style (ConfigParser) with polynomial coefficients per boot ID.
- **RESOURCES/ folder** contains reference materials only (Peng's old controller, Dephy API source, user guides, research papers). Not used at runtime.
- The codebase was AI-generated and has not been fully validated on hardware yet.

## Common Tasks

- **Change torque profile defaults**: Edit `config.py` constants (`DEFAULT_RISE_TIME`, `DEFAULT_FALL_TIME`, `PEAK_TORQUE_NM_KG`, etc.)
- **Change protocol parameters**: Edit `config.py` (`INITIAL_OFFSET_PCT`, `STEP_SIZE_PCT`, `MAX_TRIALS`, `CATCH_TRIAL_RATE`, etc.)
- **Add a new sensor reading**: Extend `ExoBoot.read_data()` in `exo_init.py`
- **Modify trial logic**: Edit `PerceptionExperiment._run_perception()` in `perception_test.py`
- **Change GUI layout**: Edit `ExperimentGUI._build_*_group()` methods in `gui.py` (PyQt5 `QGroupBox` / `QFormLayout` / `QHBoxLayout`)

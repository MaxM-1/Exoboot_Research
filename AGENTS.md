# AGENTS.md

Concise pointers for AI coding agents. **Read these two files before changing controller / motor / gait code:**

1. [CLAUDE.md](CLAUDE.md) — static project overview, architecture, file responsibilities, hardware constraints, common tasks.
2. [sequential_change_log.md](sequential_change_log.md) — live debugging journal. Contains what has already been tried and **what NOT to re-suggest** (e.g. position control during walking, raising `PEAK_CURRENT` above 15000 mA).

User-facing setup, calibration, and experiment workflow live in [README.md](README.md). Diagnostic plot suite is documented in [DataLog/analysis/README.md](DataLog/analysis/README.md).

## Hard rules (do not regress)

- **Walking control = current control only.** During walking, only `cur_ramp_up` / `cur_ramp_down` drive the Collins torque pulse. Pre-gait, early-stance, and late-stance hold a low constant current (`NO_SLACK_CURRENT * side`). Position control during walking caused 20 000+ tick error spikes at heel-strike — see Session 3 in [sequential_change_log.md](sequential_change_log.md). Position control is allowed only in `encoder_check` and `zero_boot` (stationary).
- **`PEAK_CURRENT = 28000 mA` is the working ceiling** after the Session 5 `kt` fix. The earlier 15000 mA bandaid is obsolete; do not re-impose it. Do not raise it past the firmware ceiling. See Session 5 (and the Session 7 hard-rule update) in [sequential_change_log.md](sequential_change_log.md).
- **Torque→current conversion is for ActPack 4.1 (Q-axis) ONLY.** [`ankle_torque_to_current`](exo_init.py) returns `(tau_mNm / wm_wa) / 1000 / kt` with `kt = 0.140 Nm/A`. Do **NOT** multiply by `sqrt(2) / 0.537` (or equivalently divide by 0.38) — that is the legacy ActPack 0.2B-firmware rescale from Peng's controller and applying it on 4.1 over-commands current by ~2.63×. See Session 5 in [sequential_change_log.md](sequential_change_log.md) for the datasheet derivation.
- **Two perception experiments share this codebase. They are dispatched by `params["experiment_type"]` (set from the GUI radio).**
  - `MAX_EXPERIMENT` ("max") — staircase varies `t_peak` (% gait). Rise/fall coupled (`t_rise = t_peak − T_ACT_START`, `t_fall = T_ACT_END − t_peak`). Peak torque pinned to `MAX_FAM_PEAK_TN = 0.225 Nm/kg`. Constants `MAX_DELTA = 1.0`, `MAX_INITIAL_OFFSET = 3.0`, `MAX_REST_STRIDES = 8`.
  - `SAV_EXPERIMENT` ("sav") — staircase varies `peak_torque_norm` (Nm/kg). All timing pinned (`t_peak = DEFAULT_T_PEAK = 51.3 %`). Constants `SAV_DELTA = 0.005`, `SAV_INITIAL_OFFSET = 0.05`, `SAV_REFERENCE_PEAK_TN = SAV_FAM_PEAK_TN = 0.18`, `SAV_REST_STRIDES = 15`, clamp `[SAV_MIN_PEAK_TN, SAV_MAX_PEAK_TN] = [0.05, 0.30]`.
  - **Do not let either experiment's staircase touch the other's pinned variable.** SAV must never modify timing; MAX must never modify torque magnitude. Use the `_StaircaseVar` helper in [perception_test.py](perception_test.py) — never hard-code knob choices in the trial loop. Legacy `RISE_TIME_TEST` / `FALL_TIME_TEST` are aliases of `PEAK_TIME_TEST` for back-compat only. See Session 6 (peak-time refactor) and Session 7 (dual-experiment refactor) in [sequential_change_log.md](sequential_change_log.md).
- **Actuation start (26.0 %) and end (61.6 %) are constants of both experimental designs** — do not let either drift, regardless of experiment type.
- **All experiment constants live in [config.py](config.py)** — do not hard-code values elsewhere. Change parameters there.
- **Thread safety**: GUI ↔ experiment thread communicate only via `command_queue` / `status_queue`. No shared mutable state.

## Build / test

Offline tests only — they do **not** touch hardware and do not replace live validation:

```powershell
.venv\Scripts\python.exe -m pytest
```

(Linux/Pi: `.venv/bin/python -m pytest`.) Dev deps: [requirements-dev.txt](requirements-dev.txt).

## Diagnostics first

When investigating any walk-test problem, run [DataLog/analysis/Analysis2.py](DataLog/analysis/Analysis2.py) before reading code. The per-iteration log written by [exo_logger.py](exo_logger.py) (~50 columns at 100 Hz) is the primary diagnostic source — prefer it over per-stride CSVs.

```powershell
python DataLog/analysis/Analysis2.py --latest
python DataLog/analysis/Analysis2.py --pair --participant P001 --phase Familiarization
```

## Conventions

- Streaming rate is **100 Hz** host-side (firmware control loop is faster — do not confuse the two).
- FlexSEA API is the current `flexsea.device.Device`, **not** the legacy `fxs` API used in `RESOURCES/Peng_controller/`.
- `bootCal.txt` (INI format, ConfigParser) must exist in `calibration/` before experiments run.
- Calibration direction is plantarflexed → dorsiflexed only. Reverse direction loosens the belt and produces an unusable mapping.
- `RESOURCES/` is reference-only and is **not** imported at runtime.

## When unsure

Ask the user before: deleting CSV/data files, modifying `bootCal.txt`, raising current limits, or any change that affects motor safety.

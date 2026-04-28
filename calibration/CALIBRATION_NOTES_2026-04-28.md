# Calibration Pipeline Notes — 2026-04-28

Reference document for future agents / collaborators working on the
ExoBoot calibration scripts in this folder. Captures what was changed,
why, and how the pipeline maps onto Xiangyu Peng's original procedure.

## Files in scope

- [boot_calibration_3.py](boot_calibration_3.py) — data collection (one boot at a time)
- [calibration_analysis_3.py](calibration_analysis_3.py) — polyfit + writes bootCal.txt
- [bootCal.txt](bootCal.txt) — INI-format coefficients consumed by the controller
- [boot_calibration.py](boot_calibration.py) / [calibration_analysis.py](calibration_analysis.py) — previous (working) versions, kept for reference
- [../RESOURCES/Peng_controller(actuation_timing_perception)/Calibration/exoTorqueCalcCal.m](../RESOURCES/Peng_controller(actuation_timing_perception)/Calibration/exoTorqueCalcCal.m) — Peng's MATLAB source of truth

## Procedure (locked in)

Per boot, with **only that boot plugged in** (so it always enumerates as
`/dev/ttyACM0`):

1. Attach exo to boot, place flat on benchtop.
2. Free-travel test: PF/DF by hand with chain slack.
3. Hold ankle at **full PLANTARFLEXION**.
4. `python calibration/boot_calibration_3.py left` (or `right`).
5. Press Enter → script holds `ZEROING_CURRENT = 1000 mA` for 2 s to tighten
   the chain (sign-flipped for the right boot).
6. Slowly **DORSIFLEX** through full ROM. The motor holds
   `NO_SLACK_CURRENT = 800 mA` to keep the chain taut while being
   back-driven by the ankle. Press Enter at full DF to stop.
7. Output CSV: `calibration/<side>_boot_calib_<timestamp>.csv` with
   columns `state_time, ank_ang, mot_ang, mot_cur`.
8. `python calibration/calibration_analysis_3.py --csv <csv> --side <left|right> --boot-id <ID> --ankle-55 <int>`
   - `--boot-id` is the boot's hex ID (e.g. `C719`, `C6D9`).
   - `--ankle-55` is the raw `ank_ang` reading at the held PF position
     (≈ first sample of the CSV).

The analyzer fits a 4th-order polynomial mapping `ank_ang → mot_ang`,
prints diagnostics, saves `fit_<side>.png`, and updates the existing
`[ids]` and `[<boot_id>]` sections of `bootCal.txt` **in place** without
disturbing the other boot's section.

## Bugs fixed during this session (2026-04-28)

The new "_3" files were AI-generated and initially broken. Issues
addressed:

### `boot_calibration_3.py`
- Could not import `config` when invoked from `calibration/` →
  `sys.path.insert(0, project_root)` at the top.
- `Device(port=port)` was missing required `firmwareVersion` and
  `logLevel` kwargs → now uses `config.FIRMWARE_VERSION` (`"7.2.0"`),
  `config.LOG_LEVEL`, and `interactive=False`.
- `start_streaming(rate)` missing the keyword → `start_streaming(frequency=rate)`.
- `set_gains(... k_val=0, b_val=0 ...)` used wrong kwarg names (this was
  the cause of "instability") → corrected to `k=0, b=0`.
- Hard-coded literals replaced by `config.STREAMING_FREQUENCY`,
  `config.CURRENT_GAINS`, `config.ZEROING_CURRENT`,
  `config.NO_SLACK_CURRENT`, `config.LEFT/RIGHT`.
- Tightening + holding currents now multiplied by `+1` for left /
  `-1` for right (matches the rest of the codebase and Peng's `dir`).
- Output column names changed to `state_time, ank_ang, mot_ang, mot_cur`
  to match the analyzer.
- `device.read()[key]` (KeyError-prone) → `s.get(key, 0)`.
- Default port fixed to `/dev/ttyACM0` since only one boot is connected
  during calibration; `--port` overrides if needed.

### `calibration_analysis_3.py`
- **Critical**: original version overwrote `bootCal.txt` as a flat
  2-line CSV. The controller expects INI format with `[ids]` and
  `[<boot_id>]` sections (`ankle_reading_55_deg`, `poly4..poly0`). Now
  uses `configparser` with `optionxform = str` (preserves uppercase boot
  IDs).
- Operates on **one boot at a time**: `--side`, `--boot-id`,
  `--ankle-55` flags. The other boot's section is preserved verbatim.
- CSV reader accepts both new (`ank_ang`, `mot_ang`) and legacy
  (`ankle_ticks`, `motor_ticks`) column names.
- `trim_startup` drops the first ~0.5 s of samples (current ramp).
  `--no-trim` disables.

## Diagnosing today's right-boot "corruption"

First two right-boot CSVs from 2026-04-28 (12h06, 12h07) were full of
parser-garbage rows: constant `state_time = 96`, `ank_ang ≈ 5e8`,
`mot_ang = 0`, `mot_cur = 0` for 1400+ rows. Diagnosis:

- Constant `state_time` → device was **not actually streaming**;
  `device.read()` returned the same uninitialized buffer every call.
- Huge `ank_ang` and exact-zero motor fields → flexsea was decoding the
  buffer with the **wrong struct layout**, i.e. the firmware on the
  board didn't match `firmwareVersion="7.2.0"`.

Fix: reflash / power-cycle the right boot before retrying. The
12h16m15s CSV that we eventually analyzed shows real, monotonic data
(state_time increments by 10 ms, ank_ang sweeps 187 → 1425, mot_ang
sweeps -30426 → -37510), confirming the issue was firmware/USB state,
not the script.

The left CSV `12h08m18s` had a session bleed (state_time jumps
8822 → 16812 mid-file) — the device retained state between two runs.
Always power-cycle / unplug between runs.

## Math validation vs. Peng's MATLAB

| Step | Peng (MATLAB)                                     | This Python pipeline                                  |
|------|---------------------------------------------------|-------------------------------------------------------|
| Trim | walk forward until `mot_acc < 200` for 1 s        | drop fixed first 50 samples (0.5 s)                   |
| Cut  | `[~, stopIdx] = max(dir * mot_ang)` (peak DF)     | **not implemented** — uses entire remainder of CSV    |
| Unique ankle | `[uA, idx] = unique(ankle); uM = motor(idx)` | `np.unique(ankle, return_index=True)`                 |
| Fit  | `polyfit(uA, uM, 5)` (6 coeffs)                   | `np.polyfit(uA, uM, 4)` (5 coeffs)                    |
| Slope | `5·p5·x⁴ + 4·p4·x³ + 3·p3·x² + 2·p2·x + p1`      | controller uses identical form on poly4..poly0        |
| Side sign | `dir = +1` (left), `-1` (right)              | `config.LEFT = +1`, `config.RIGHT = -1`               |

Order **4** (5 coeffs) is correct for this codebase because
`bootCal.txt` only stores `poly4..poly0`. Peng's MATLAB writes a 6th
coefficient that is dropped before the controller reads it. Do **not**
change to order 5 without updating the controller.

### Numerical sanity check (right boot, 2026-04-28, ID `C6D9`)

Fit:
```
poly4 = +1.202e-10
poly3 = -2.079e-06
poly2 = +1.301e-02
poly1 = -2.291e+01
poly0 = -2.570e+04
```
- At `ank ≈ 800`: predicted `mot_ang ≈ -36 730` vs measured ≈ -36 000 ✓
- Slope `dM/dA|_{800} ≈ -5.86` vs empirical ΔM/ΔA ≈ -5.72 ✓
- Negative slope is correct: with right-side sign convention, motor
  angle decreases as ankle dorsiflexes. Left boot has positive slope.

## Outstanding / nice-to-have

- Add `--auto-trim-end` flag to `calibration_analysis_3.py` that cuts at
  `argmax(dir * mot_ang)`, matching Peng's `stopIdx` behavior. Not
  required for the current dataset but improves robustness if the
  operator overshoots and starts returning toward PF.
- Add an early-abort sanity check in `boot_calibration_3.py`: read the
  first sample, raise if `state_time` is not advancing or if
  `|ank_ang| > 20000` / `|mot_ang| > 200000`. Would have caught the
  12h06/12h07 garbage runs immediately.

## Current `bootCal.txt` (post-2026-04-28 session)

```
[ids]
left  = C719
right = C6D9

[C719]   ankle_reading_55_deg = 8003   poly4..poly0 fitted from left CSV
[C6D9]   ankle_reading_55_deg = 186    poly4..poly0 fitted from right CSV
```
This file is ready for downstream use by the controller.

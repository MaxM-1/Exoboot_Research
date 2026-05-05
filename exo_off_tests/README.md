# Exo-Off Walking Tests

Barebones data recorder + analysis for troubleshooting heel-strike detection
and torque-curve latency on the treadmill **with motors commanding zero
current** (i.e. boots are powered + USB-streaming, but produce no torque).

The goal is to collect clean, no-torque gyroZ traces from different walkers
at multiple treadmill speeds (1.00 / 1.25 / 1.35 m/s) so we can re-tune
`HEELSTRIKE_THRESHOLD_ABOVE` / `HEELSTRIKE_THRESHOLD_BELOW` and the
refractory constants in [config.py](../config.py).

> "Exo off" here means **motors unpowered (zero current commanded)**.
> The boots themselves must remain powered and USB-connected because the
> IMU stream comes through the FlexSEA device — there is no other path
> to the gyro/accel signals.

## Files

- [exo_off_recorder.py](exo_off_recorder.py) — opens both boots at 100 Hz,
  never commands current, logs the standard per-iteration CSV via
  [`ExoLogger`](../exo_logger.py). Heel-strike detection still runs inside
  [`ExoBoot.read_data`](../exo_init.py) so `seg_trigger`, `gyroz_signed`,
  thresholds, etc. are populated for later analysis.
- [exo_off_analysis.py](exo_off_analysis.py) — plots gyroZ with detected
  heel-strike markers and threshold lines, ankle-angle overlay, and a
  stride-duration histogram. Prints stride stats and suggested per-speed
  threshold values (5th / 95th percentiles of gyroZ peaks).
- `data/` — output CSVs (per-side `_full.csv` plus a combined merged
  CSV per run). Auto-created on first run.

## Usage

Activate the venv first:

```bash
source .venv/bin/activate
```

### Record

```bash
# Run-until-Ctrl-C (matches treadmill pacing manually)
python exo_off_tests/exo_off_recorder.py --participant YA5 --weight 75

# Fixed-duration window
python exo_off_tests/exo_off_recorder.py --participant YA5 --weight 75 \
    --duration 60 --speed 1.25
```

Args:

- `--participant`  participant ID (required)
- `--weight`       body weight in kg (required, only used as metadata)
- `--duration`     seconds; omit for run-until-Ctrl-C
- `--speed`        treadmill speed in m/s (metadata only, e.g. `1.25`)
- `--trial-name`   suffix added to filename (default: `ExoOff`)
- `--left-port` / `--right-port`  override defaults from [config.py](../config.py)
- `--out-dir`      output directory (default: `exo_off_tests/data/`)

### Analyse

```bash
# Latest run in exo_off_tests/data/
python exo_off_tests/exo_off_analysis.py --latest

# Specific files
python exo_off_tests/exo_off_analysis.py \
    --left  exo_off_tests/data/YA5_ExoOff_L_..._full.csv \
    --right exo_off_tests/data/YA5_ExoOff_R_..._full.csv
```

The analysis prints a `thresholds_summary` block with suggested arm/trigger
values per side derived from observed gyroZ peaks; use those to update
[config.py](../config.py) for the speed you tested.

## Safety / what this script will NOT do

- Never calls `cur_ramp_up`, `cur_ramp_down`, `set_motor_current`,
  `cal_motor_offset`, or `zero_boot`. The motors stay at zero current the
  entire run. (See the hard rules in [AGENTS.md](../AGENTS.md).)
- Does not touch [config.py](../config.py) constants — analysis only
  *suggests* values; tuning is a manual edit.

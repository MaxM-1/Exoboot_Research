# DataLog / analysis

Diagnostic tooling for the raw FlexSEA CSV logs in [`../`](../).

This subfolder is **output-only for analysis** — the exoboot code (FlexSEA
`streaming_log_level`) writes new `Data*.csv` files into the parent
`DataLog/` folder by timestamped name and never lists or scans the
directory, so putting tools and generated PNGs in here is safe and will
not interfere with live logging.

## Analysis1.py

Produces 7 diagnostic figures + `summary.txt` per trial.

### Quick start

```bash
# Analyze the most recent trial automatically
python DataLog/analysis/Analysis1.py --latest

# Analyze a specific file
python DataLog/analysis/Analysis1.py DataLog/Data2026-04-19_13h15m55s_.csv

# Right boot (flips sign of gyroz to match exo_init.py convention)
python DataLog/analysis/Analysis1.py --latest --side R

# Open the resulting plots folder when finished
python DataLog/analysis/Analysis1.py --latest --show
```

### Troubleshooting cheat sheet

| Symptom                       | Look at                                   |
| ----------------------------- | ----------------------------------------- |
| Profile not firing            | `02_heelstrike.png` — tune `--arm-threshold` / `--trigger-threshold` |
| Inconsistent assistance       | `05_stride_overlay.png` — stride-to-stride variance |
| Calibration drift             | `04_ankle_vs_motor.png` — should be monotonic |
| Brownouts / motor saturation  | `06_power_thermal.png` |
| Dropped samples               | `07_sampling_health.png` — expect 10 ms @ 100 Hz |
| "Something is weird"          | `01_overview.png` + `summary.txt` |

### Detector modes

Default is the **arm / trigger** two-threshold detector that matches
`ExoBoot._heelstrike_detect` in [`exo_init.py`](../../exo_init.py):
arm on `side·gyroz > +3280`, fire on falling edge through `-4920`.
Pass `--simple --hs-threshold N` to use a single rising-edge threshold
instead (easier to tune visually for new participants).

Output of each run lives in `DataLog/analysis/<csv_stem>_plots/`.

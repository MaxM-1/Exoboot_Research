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

## perception_plots.py

Diagnostic figures for one perception-test session. Reads the trial CSV
(`data/{pid}_Perception_{ts}.csv`) plus matching per-stride CSVs. **Does
not** fit psychometric functions or compute PSE / JND — graphs only.

Outputs into `data/<trial-csv-stem>_plots/`:

- `staircase.png` — comparison `t_peak` vs trial #, separate panels per
  approach direction. Markers: ○ Same, ■ Different. White-fill = catch
  trial. Blue halo = reversal. Reference dotted line.
- `reversals.png` — reversals only, connected, per approach.
- `stride_dur.png` — boxplot of `actual_stride_dur` grouped by trial
  phase (A vs B), separately for L and R.
- `profile_gallery.png` — every comparison's Collins curve overlaid,
  viridis-colored by trial order, with reference in black. Vertical
  dotted lines mark `T_ACT_START` (26 %) and `T_ACT_END` (61.6 %).
- `summary.txt` — trial / real / catch / reversal counts and catch-trial
  false-alarm rate.

### Quick start

```bash
# Most recent perception trial CSV in data/
python DataLog/analysis/perception_plots.py --latest

# Specific participant id (uses the latest matching trial)
python DataLog/analysis/perception_plots.py --participant SAV_Perception_3

# Specific file
python DataLog/analysis/perception_plots.py data/P001_Perception_2026-05-04_10h44m56s.csv
```

The script automatically rejects per-sample ExoLogger files
(`..._full.csv`) and per-stride files (`..._PerceptionStride_...`) — only
the trial CSV is selected.

"""
Exo-Off Analysis
================

Lightweight diagnostic plots for files produced by
`exo_off_tests/exo_off_recorder.py` (or any walking trial that produces
the standard per-iter `_full.csv` schema from `ExoLogger`).

For each side:

  1. gyroZ trace (signed) with vertical markers at detected heel-strikes
     (`seg_trigger == 1`) and horizontal lines for the current
     `HEELSTRIKE_THRESHOLD_ABOVE` / `HEELSTRIKE_THRESHOLD_BELOW`.
  2. Ankle angle (zeroed) overlay with the same heel-strike markers.
  3. Stride-duration histogram derived from successive HS timestamps.

Also prints a per-side stride summary and a *suggested* threshold block
(5th / 95th percentiles of observed gyroZ stride peaks) to help re-tune
[config.py](../config.py) per treadmill speed.

Run::

    python exo_off_tests/exo_off_analysis.py --latest
    python exo_off_tests/exo_off_analysis.py \
        --left  exo_off_tests/data/YA5_ExoOff_L_..._full.csv \
        --right exo_off_tests/data/YA5_ExoOff_R_..._full.csv
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from typing import Optional

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# Allow running from anywhere
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import (
    HEELSTRIKE_THRESHOLD_ABOVE, HEELSTRIKE_THRESHOLD_BELOW,
    MIN_STRIDE_PERIOD, REFRACTORY_FRACTION, REFRACTORY_MAX,
)


DEFAULT_DATA_DIR = os.path.join(_HERE, "data")


# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Plot gyroZ + heel-strikes for an exo-off run.")
    p.add_argument("--latest", action="store_true",
                   help=f"Use the newest *_L_*_full.csv / *_R_*_full.csv pair in {DEFAULT_DATA_DIR}")
    p.add_argument("--left", help="Path to left-boot per-iter _full.csv")
    p.add_argument("--right", help="Path to right-boot per-iter _full.csv")
    p.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                   help="Directory to search when using --latest (default: exo_off_tests/data/)")
    p.add_argument("--save", default=None,
                   help="If given, save figures to this directory instead of showing.")
    return p.parse_args()


# ---------------------------------------------------------------------------
def find_latest_pair(data_dir: str):
    l_files = sorted(glob.glob(os.path.join(data_dir, "*_L_*_full.csv")))
    r_files = sorted(glob.glob(os.path.join(data_dir, "*_R_*_full.csv")))
    if not l_files or not r_files:
        raise SystemExit(f"No _L_*_full.csv / _R_*_full.csv pair found in {data_dir}")
    return l_files[-1], r_files[-1]


# ---------------------------------------------------------------------------
def _stride_durations_ms(df: pd.DataFrame) -> np.ndarray:
    """Compute stride durations from successive `seg_trigger==1` events
    using the device's `state_time_ms` column."""
    trig = df[df["seg_trigger"].astype(int) == 1]
    if len(trig) < 2:
        return np.array([])
    t = trig["state_time_ms"].to_numpy(dtype=float)
    return np.diff(t)


# ---------------------------------------------------------------------------
def _stride_peak_gyroz(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Return (positive_peaks, negative_peaks) of gyroz_signed within each
    stride window (between successive seg_triggers)."""
    trig_idx = np.where(df["seg_trigger"].astype(int).to_numpy() == 1)[0]
    pos_peaks: list[float] = []
    neg_peaks: list[float] = []
    gz = df["gyroz_signed"].to_numpy(dtype=float)
    for i in range(len(trig_idx) - 1):
        a, b = trig_idx[i], trig_idx[i + 1]
        if b - a < 5:
            continue
        seg = gz[a:b]
        pos_peaks.append(float(np.max(seg)))
        neg_peaks.append(float(np.min(seg)))
    return np.array(pos_peaks), np.array(neg_peaks)


# ---------------------------------------------------------------------------
def analyse_side(df: pd.DataFrame, side_label: str, save_dir: Optional[str]):
    t = df["state_time_ms"].to_numpy(dtype=float) / 1000.0  # s
    gz = df["gyroz_signed"].to_numpy(dtype=float)
    ank = df["ank_ang_zeroed"].to_numpy(dtype=float)
    trig_mask = df["seg_trigger"].astype(int).to_numpy() == 1
    hs_times = t[trig_mask]

    # Stride stats
    durs = _stride_durations_ms(df)
    pos_peaks, neg_peaks = _stride_peak_gyroz(df)

    # ---- Print summary -------------------------------------------------
    print(f"\n=== {side_label} ===")
    print(f"  Samples         : {len(df)}")
    print(f"  Detected strides: {trig_mask.sum()}")
    if len(durs):
        print(f"  Stride duration : mean={durs.mean():.0f} ms  "
              f"std={durs.std():.0f} ms  "
              f"min={durs.min():.0f}  max={durs.max():.0f}")
        # Outliers: > +/-30% from median
        med = float(np.median(durs))
        outliers = durs[(durs < 0.7 * med) | (durs > 1.3 * med)]
        print(f"  Outlier strides : {len(outliers)} / {len(durs)} (>±30% from median {med:.0f} ms)")
    if len(pos_peaks):
        # Suggested arm/trigger thresholds: 5th percentile of (pos,neg) peaks
        # — a threshold lower than this catches >=95% of strides.
        suggest_arm = float(np.percentile(pos_peaks, 5))
        suggest_trg = float(np.percentile(neg_peaks, 95))  # most-positive of the negatives
        print(f"  gyroZ pos peak  : median={np.median(pos_peaks):+.0f}  "
              f"5th pct={suggest_arm:+.0f}  (current ARM = {HEELSTRIKE_THRESHOLD_ABOVE:+.0f})")
        print(f"  gyroZ neg peak  : median={np.median(neg_peaks):+.0f}  "
              f"95th pct={suggest_trg:+.0f}  (current TRG = {HEELSTRIKE_THRESHOLD_BELOW:+.0f})")
        print(f"  → suggested HEELSTRIKE_THRESHOLD_ABOVE ≈ {suggest_arm * 0.85:+.0f}  "
              f"(15% margin under 5th pct)")
        print(f"  → suggested HEELSTRIKE_THRESHOLD_BELOW ≈ {suggest_trg * 0.85:+.0f}")

    # ---- Plot ----------------------------------------------------------
    fig, axes = plt.subplots(3, 1, figsize=(13, 9), sharex=False)
    fig.suptitle(f"Exo-Off Diagnostic — {side_label}", fontsize=13)

    # gyroZ + thresholds + HS markers
    ax = axes[0]
    ax.plot(t, gz, lw=0.6, color="tab:blue", label="gyroz_signed")
    ax.axhline(HEELSTRIKE_THRESHOLD_ABOVE, color="tab:green", ls="--", lw=1,
               label=f"ARM thr ({HEELSTRIKE_THRESHOLD_ABOVE:+.0f})")
    ax.axhline(HEELSTRIKE_THRESHOLD_BELOW, color="tab:red", ls="--", lw=1,
               label=f"TRG thr ({HEELSTRIKE_THRESHOLD_BELOW:+.0f})")
    for hs in hs_times:
        ax.axvline(hs, color="k", alpha=0.25, lw=0.5)
    ax.set_ylabel("gyroZ (signed, raw LSB)")
    ax.set_xlabel("time (s)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right", fontsize=8)
    ax.set_title(f"gyroZ trace — {trig_mask.sum()} heel-strikes detected")

    # Ankle angle overlay
    ax = axes[1]
    ax.plot(t, ank, lw=0.6, color="tab:purple")
    for hs in hs_times:
        ax.axvline(hs, color="k", alpha=0.25, lw=0.5)
    ax.set_ylabel("ankle angle zeroed (deg)")
    ax.set_xlabel("time (s)")
    ax.grid(True, alpha=0.3)
    ax.set_title("Ankle angle with HS markers")

    # Stride-duration histogram
    ax = axes[2]
    if len(durs):
        ax.hist(durs, bins=30, color="tab:orange", edgecolor="black", alpha=0.85)
        ax.axvline(MIN_STRIDE_PERIOD, color="red", ls="--", lw=1,
                   label=f"MIN_STRIDE_PERIOD ({MIN_STRIDE_PERIOD} ms)")
        ax.axvline(REFRACTORY_MAX, color="orange", ls="--", lw=1,
                   label=f"REFRACTORY_MAX ({REFRACTORY_MAX} ms)")
        ax.axvline(float(np.median(durs)), color="green", ls="-", lw=1,
                   label=f"median ({np.median(durs):.0f} ms)")
        ax.set_xlabel("stride duration (ms)")
        ax.set_ylabel("count")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.set_title("Stride-duration histogram")
    else:
        ax.text(0.5, 0.5, "No strides detected", ha="center", va="center",
                transform=ax.transAxes, fontsize=12)

    fig.tight_layout(rect=(0, 0, 1, 0.97))

    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        out = os.path.join(save_dir, f"exo_off_{side_label}.png")
        fig.savefig(out, dpi=130)
        print(f"  Saved figure → {out}")


# ---------------------------------------------------------------------------
def main():
    args = parse_args()

    if args.latest:
        l_path, r_path = find_latest_pair(args.data_dir)
    else:
        if not (args.left and args.right):
            raise SystemExit("Provide --latest or both --left and --right paths.")
        l_path, r_path = args.left, args.right

    print(f"LEFT : {l_path}")
    print(f"RIGHT: {r_path}")

    df_l = pd.read_csv(l_path)
    df_r = pd.read_csv(r_path)

    print(f"\nCurrent thresholds in config.py:")
    print(f"  HEELSTRIKE_THRESHOLD_ABOVE = {HEELSTRIKE_THRESHOLD_ABOVE:+.1f}")
    print(f"  HEELSTRIKE_THRESHOLD_BELOW = {HEELSTRIKE_THRESHOLD_BELOW:+.1f}")
    print(f"  MIN_STRIDE_PERIOD          = {MIN_STRIDE_PERIOD} ms")
    print(f"  REFRACTORY_FRACTION        = {REFRACTORY_FRACTION}")
    print(f"  REFRACTORY_MAX             = {REFRACTORY_MAX} ms")

    analyse_side(df_l, "LEFT", args.save)
    analyse_side(df_r, "RIGHT", args.save)

    if not args.save:
        plt.show()


if __name__ == "__main__":
    main()

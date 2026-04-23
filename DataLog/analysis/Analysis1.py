#!/usr/bin/env python3
"""
Analysis1.py — Quick-look diagnostics for raw FlexSEA DataLog CSVs
===================================================================
Loads one of the CSV files written by the Dephy FlexSEA library into
``DataLog/`` and generates a set of diagnostic plots useful for
troubleshooting the Collins torque-profile controller and the
heel-strike / gait detection logic in ``exo_init.py``.

This script lives in ``DataLog/analysis/`` so its outputs stay out of
the main ``DataLog/`` folder (which FlexSEA writes to live).
Adding this subfolder is safe — nothing in the exoboot code enumerates
the DataLog directory, it only writes new timestamped files by name.

CSV columns expected (from a real trial file):
    state_time, accelx, accely, accelz, gyrox, gyroy, gyroz,
    mot_ang, mot_vel, mot_acc, mot_cur, mot_volt,
    batt_volt, batt_curr, temperature,
    status_mn, status_ex, status_re,
    genvar_0..9, ank_ang, ank_vel, sys_time, event flags

Usage
-----
    # Analyze a specific file
    python DataLog/analysis/Analysis1.py DataLog/Data2026-04-19_13h15m55s_.csv

    # Pick the most recent DataLog CSV automatically
    python DataLog/analysis/Analysis1.py --latest

    # Right boot (flips sign of gyroz internally)
    python DataLog/analysis/Analysis1.py --latest --side R

    # Simple single-threshold heel-strike instead of arm/trigger
    python DataLog/analysis/Analysis1.py --latest --simple --hs-threshold 100

Outputs (saved to DataLog/analysis/<csv_stem>_plots/):
    01_overview.png           Multi-panel time-series overview
    02_heelstrike.png         Gyro-Z with thresholds + detected strikes
    03_motor_current.png      Motor current + voltage
    04_ankle_vs_motor.png     Phase plot (calibration sanity check)
    05_stride_overlay.png     All strides normalized to 0-100 % gait
    06_power_thermal.png      Battery power + temperature
    07_sampling_health.png    dt between samples (expect 10 ms @ 100 Hz)
    summary.txt               Numeric summary statistics
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ----------------------------------------------------------------------
#  Defaults pulled from config.py / exo_init.py
# ----------------------------------------------------------------------
STREAMING_FREQUENCY_HZ = 100        # config.STREAMING_FREQUENCY
EXPECTED_DT_MS         = 1000.0 / STREAMING_FREQUENCY_HZ   # 10 ms
# Heel-strike thresholds in raw gyro units (ExoBoot._heelstrike_detect):
#   arm when side*gyroz > +3280  (~ +100 deg/s, swing)
#   trigger when side*gyroz < -4920 (~ -150 deg/s, heel strike)
DEFAULT_ARM_THRESHOLD      =  3280
DEFAULT_TRIGGER_THRESHOLD  = -4920
DEFAULT_MIN_SPACING_MS     =  400

REQUIRED = ["state_time", "mot_ang", "mot_cur", "ank_ang", "gyroz"]


# ======================================================================
#  Loading
# ======================================================================
def find_latest_csv(datalog_dir: Path) -> Path:
    """Return the most-recently modified ``Data*.csv`` in DataLog/."""
    candidates = sorted(datalog_dir.glob("Data*.csv"),
                        key=lambda p: p.stat().st_mtime)
    if not candidates:
        sys.exit(f"ERROR: no Data*.csv files found in {datalog_dir}")
    return candidates[-1]


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        sys.exit(f"ERROR: CSV is missing required columns: {missing}")
    # state_time is in ms — convert to seconds from start
    df["t_s"] = (df["state_time"] - df["state_time"].iloc[0]) / 1000.0
    df["dt_ms"] = df["state_time"].diff().fillna(0)
    return df


# ======================================================================
#  Heel-strike detection
# ======================================================================
def detect_heelstrikes_armtrigger(df: pd.DataFrame, side_sign: int,
                                  arm: float, trigger: float,
                                  min_spacing_ms: int) -> np.ndarray:
    """Two-threshold detector matching ExoBoot._heelstrike_detect.

    A stride is detected when ``side_sign*gyroz`` first rises above
    ``arm`` (swing phase) and then falls below ``trigger`` (impact).
    Returns the sample indices of each trigger.
    """
    g = side_sign * df["gyroz"].to_numpy()
    t = df["state_time"].to_numpy()
    armed = False
    last_t = -1e18
    hits = []
    for i in range(len(g)):
        if not armed and g[i] > arm:
            armed = True
        elif armed and g[i] < trigger:
            if t[i] - last_t >= min_spacing_ms:
                hits.append(i)
                last_t = t[i]
            armed = False
    return np.asarray(hits, dtype=int)


def detect_heelstrikes_simple(df: pd.DataFrame, side_sign: int,
                              threshold: float,
                              min_spacing_ms: int) -> np.ndarray:
    """Single-threshold rising-edge detector (easier to tune visually)."""
    g = side_sign * df["gyroz"].to_numpy()
    t = df["state_time"].to_numpy()
    hits, last_t = [], -1e18
    for i in range(1, len(g)):
        if g[i - 1] < threshold <= g[i] and t[i] - last_t >= min_spacing_ms:
            hits.append(i)
            last_t = t[i]
    return np.asarray(hits, dtype=int)


# ======================================================================
#  Plots
# ======================================================================
def plot_overview(df, out):
    fig, ax = plt.subplots(6, 1, figsize=(12, 12), sharex=True)
    ax[0].plot(df.t_s, df.mot_cur, lw=0.8)
    ax[0].set_ylabel("mot_cur\n(mA)")
    ax[1].plot(df.t_s, df.mot_ang, lw=0.8)
    ax[1].set_ylabel("mot_ang\n(ticks)")
    ax[2].plot(df.t_s, df.ank_ang, lw=0.8, color="tab:orange")
    ax[2].set_ylabel("ank_ang\n(ticks)")
    ax[3].plot(df.t_s, df.gyroz, lw=0.8, color="tab:green")
    ax[3].set_ylabel("gyroz\n(raw)")
    if "batt_volt" in df:
        ax[4].plot(df.t_s, df.batt_volt / 1000.0, lw=0.8, color="tab:red")
        ax[4].set_ylabel("batt_volt\n(V)")
    if "temperature" in df:
        ax[5].plot(df.t_s, df.temperature, lw=0.8, color="tab:purple")
        ax[5].set_ylabel("temp (°C)")
    ax[-1].set_xlabel("Time (s)")
    fig.suptitle("Overview")
    fig.tight_layout()
    fig.savefig(out / "01_overview.png", dpi=120)
    plt.close(fig)


def plot_heelstrike(df, hs_idx, side_sign, params, out):
    fig, ax = plt.subplots(figsize=(12, 4))
    g = side_sign * df.gyroz
    ax.plot(df.t_s, g, lw=0.8, label=f"{side_sign:+d}·gyroz")
    if params["mode"] == "armtrigger":
        ax.axhline(params["arm"],     color="orange", ls="--", lw=1,
                   label=f"arm={params['arm']}")
        ax.axhline(params["trigger"], color="red",    ls="--", lw=1,
                   label=f"trigger={params['trigger']}")
    else:
        ax.axhline(params["threshold"], color="red", ls="--", lw=1,
                   label=f"threshold={params['threshold']}")
    ax.plot(df.t_s.iloc[hs_idx], g.iloc[hs_idx],
            "ro", ms=6, label=f"heel strikes ({len(hs_idx)})")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("side·gyroz (raw)")
    ax.set_title(f"Heel-strike detection  [mode={params['mode']}]")
    ax.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(out / "02_heelstrike.png", dpi=120)
    plt.close(fig)


def plot_motor_current(df, out):
    fig, ax = plt.subplots(2, 1, figsize=(12, 6), sharex=True)
    ax[0].plot(df.t_s, df.mot_cur, lw=0.8)
    ax[0].axhline(0, color="k", lw=0.5)
    ax[0].set_ylabel("mot_cur (mA)")
    if "mot_volt" in df:
        ax[1].plot(df.t_s, df.mot_volt, lw=0.8, color="tab:orange")
        ax[1].set_ylabel("mot_volt (mV)")
    ax[1].set_xlabel("Time (s)")
    fig.suptitle("Motor command tracking")
    fig.tight_layout()
    fig.savefig(out / "03_motor_current.png", dpi=120)
    plt.close(fig)


def plot_ankle_vs_motor(df, out):
    fig, ax = plt.subplots(figsize=(6, 6))
    sc = ax.scatter(df.ank_ang, df.mot_ang, c=df.t_s, s=3, cmap="viridis")
    ax.set_xlabel("ank_ang (ticks)")
    ax.set_ylabel("mot_ang (ticks)")
    ax.set_title("Ankle vs motor angle (color = time)")
    plt.colorbar(sc, ax=ax, label="t (s)")
    fig.tight_layout()
    fig.savefig(out / "04_ankle_vs_motor.png", dpi=120)
    plt.close(fig)


def plot_stride_overlay(df, hs_idx, out):
    if len(hs_idx) < 3:
        return
    fig, ax = plt.subplots(2, 1, figsize=(10, 7))
    for i in range(len(hs_idx) - 1):
        a, b = hs_idx[i], hs_idx[i + 1]
        if b - a < 5:
            continue
        pct = np.linspace(0, 100, b - a)
        ax[0].plot(pct, df.mot_cur.iloc[a:b].to_numpy(), lw=0.6, alpha=0.6)
        ax[1].plot(pct, df.ank_ang.iloc[a:b].to_numpy(), lw=0.6, alpha=0.6)
    ax[0].set_ylabel("mot_cur (mA)")
    ax[0].set_title(f"Stride overlay — {len(hs_idx)-1} strides")
    ax[1].set_ylabel("ank_ang (ticks)")
    ax[1].set_xlabel("Gait cycle (%)")
    fig.tight_layout()
    fig.savefig(out / "05_stride_overlay.png", dpi=120)
    plt.close(fig)


def plot_power_thermal(df, out):
    if "batt_volt" not in df or "batt_curr" not in df:
        return
    power_w = (df.batt_volt / 1000.0) * (df.batt_curr / 1000.0)
    fig, ax = plt.subplots(2, 1, figsize=(12, 5), sharex=True)
    ax[0].plot(df.t_s, power_w, lw=0.8)
    ax[0].set_ylabel("Batt power (W)")
    if "temperature" in df:
        ax[1].plot(df.t_s, df.temperature, lw=0.8, color="tab:red")
        ax[1].set_ylabel("Temp (°C)")
    ax[1].set_xlabel("Time (s)")
    fig.suptitle("Power & thermal")
    fig.tight_layout()
    fig.savefig(out / "06_power_thermal.png", dpi=120)
    plt.close(fig)


def plot_sampling_health(df, out):
    dt = df.dt_ms.iloc[1:]
    fig, ax = plt.subplots(1, 2, figsize=(12, 4))
    ax[0].plot(df.t_s.iloc[1:], dt, lw=0.5)
    ax[0].axhline(EXPECTED_DT_MS, color="red", ls="--", lw=1,
                  label=f"expected {EXPECTED_DT_MS:.0f} ms")
    ax[0].set_xlabel("Time (s)")
    ax[0].set_ylabel("dt (ms)")
    ax[0].set_title("Sample interval over time")
    ax[0].legend()
    ax[1].hist(dt, bins=50)
    ax[1].axvline(EXPECTED_DT_MS, color="red", ls="--", lw=1)
    ax[1].set_xlabel("dt (ms)")
    ax[1].set_ylabel("count")
    ax[1].set_title(f"dt histogram  (median={dt.median():.1f} ms)")
    fig.tight_layout()
    fig.savefig(out / "07_sampling_health.png", dpi=120)
    plt.close(fig)


# ======================================================================
#  Summary
# ======================================================================
def write_summary(df, hs_idx, csv_path: Path, out: Path, params: dict):
    lines = [f"Source CSV          : {csv_path}"]
    dur = df.t_s.iloc[-1]
    lines.append(f"File duration       : {dur:.2f} s ({len(df)} samples)")
    if dur > 0:
        lines.append(f"Mean sample rate    : {len(df)/dur:.1f} Hz "
                     f"(expected {STREAMING_FREQUENCY_HZ})")
    lines.append(f"dt median / max     : {df.dt_ms.iloc[1:].median():.1f} / "
                 f"{df.dt_ms.iloc[1:].max():.1f} ms")
    lines.append(f"Detection mode      : {params['mode']}  "
                 f"(side_sign={params['side_sign']:+d})")
    lines.append(f"Heel strikes found  : {len(hs_idx)}")
    if len(hs_idx) >= 2:
        strides_ms = np.diff(df.state_time.iloc[hs_idx].to_numpy())
        lines.append(f"Stride duration     : {strides_ms.mean():.0f} ± "
                     f"{strides_ms.std():.0f} ms "
                     f"(min {strides_ms.min():.0f}, max {strides_ms.max():.0f})")
    lines.append(f"mot_cur range       : {df.mot_cur.min()} .. {df.mot_cur.max()} mA")
    lines.append(f"mot_ang range       : {df.mot_ang.min()} .. {df.mot_ang.max()} ticks")
    lines.append(f"ank_ang range       : {df.ank_ang.min()} .. {df.ank_ang.max()} ticks")
    if "temperature" in df:
        lines.append(f"Temp max            : {df.temperature.max()} °C")
    if "batt_volt" in df:
        lines.append(f"Batt V min / max    : "
                     f"{df.batt_volt.min()/1000:.2f} / "
                     f"{df.batt_volt.max()/1000:.2f} V")
    text = "\n".join(lines)
    print(text)
    (out / "summary.txt").write_text(text + "\n")


# ======================================================================
#  Main
# ======================================================================
def main():
    script_dir  = Path(__file__).resolve().parent          # DataLog/analysis
    datalog_dir = script_dir.parent                         # DataLog

    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csv", nargs="?",
                   help="Path to a DataLog CSV (omit if using --latest)")
    p.add_argument("--latest", action="store_true",
                   help="Pick the most-recently-modified Data*.csv in DataLog/")
    p.add_argument("--outdir", default=None,
                   help="Output directory (default: DataLog/analysis/<csv_stem>_plots/)")
    p.add_argument("--side", choices=["L", "R"], default="L",
                   help="Boot side. L=left (sign +1), R=right (sign -1). Default L.")
    p.add_argument("--simple", action="store_true",
                   help="Use single-threshold heel-strike detection instead of "
                        "the two-threshold arm/trigger logic in exo_init.py")
    p.add_argument("--hs-threshold", type=float, default=100.0,
                   help="[--simple only] single threshold on side*gyroz (default 100)")
    p.add_argument("--arm-threshold", type=float, default=DEFAULT_ARM_THRESHOLD,
                   help=f"Arm threshold for arm/trigger detector "
                        f"(default {DEFAULT_ARM_THRESHOLD})")
    p.add_argument("--trigger-threshold", type=float, default=DEFAULT_TRIGGER_THRESHOLD,
                   help=f"Trigger threshold for arm/trigger detector "
                        f"(default {DEFAULT_TRIGGER_THRESHOLD})")
    p.add_argument("--min-spacing-ms", type=int, default=DEFAULT_MIN_SPACING_MS,
                   help=f"Debounce spacing between strikes "
                        f"(default {DEFAULT_MIN_SPACING_MS} ms)")
    p.add_argument("--show", action="store_true",
                   help="Open the output folder when done (Linux: xdg-open)")
    args = p.parse_args()

    # ---------- pick input ----------
    if args.latest:
        csv_path = find_latest_csv(datalog_dir)
        print(f"[--latest] newest CSV: {csv_path.name}")
    elif args.csv:
        csv_path = Path(args.csv).expanduser().resolve()
    else:
        p.error("must supply a CSV path or use --latest")
    if not csv_path.is_file():
        sys.exit(f"ERROR: file not found: {csv_path}")

    # ---------- output dir ----------
    if args.outdir:
        out = Path(args.outdir).expanduser().resolve()
    else:
        out = script_dir / f"{csv_path.stem}_plots"
    out.mkdir(parents=True, exist_ok=True)

    # ---------- load ----------
    print(f"Loading {csv_path.name} …")
    df = load_csv(csv_path)

    # ---------- detect ----------
    side_sign = +1 if args.side == "L" else -1
    if args.simple:
        hs_idx = detect_heelstrikes_simple(
            df, side_sign, args.hs_threshold, args.min_spacing_ms)
        params = {"mode": "simple",
                  "threshold": args.hs_threshold,
                  "side_sign": side_sign}
    else:
        hs_idx = detect_heelstrikes_armtrigger(
            df, side_sign, args.arm_threshold,
            args.trigger_threshold, args.min_spacing_ms)
        params = {"mode": "armtrigger",
                  "arm": args.arm_threshold,
                  "trigger": args.trigger_threshold,
                  "side_sign": side_sign}

    # ---------- plot ----------
    print(f"Writing plots to {out}/")
    plot_overview(df, out)
    plot_heelstrike(df, hs_idx, side_sign, params, out)
    plot_motor_current(df, out)
    plot_ankle_vs_motor(df, out)
    plot_stride_overlay(df, hs_idx, out)
    plot_power_thermal(df, out)
    plot_sampling_health(df, out)
    write_summary(df, hs_idx, csv_path, out, params)

    if args.show:
        import subprocess, platform
        if platform.system() == "Linux":
            subprocess.Popen(["xdg-open", str(out)])

    print("Done.")


if __name__ == "__main__":
    main()

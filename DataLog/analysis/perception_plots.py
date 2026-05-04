"""
Perception-Test Diagnostic Plots
=================================

Generates diagnostic figures for a single perception-test session.
Reads the trial CSV (``{pid}_Perception_*.csv``) and per-stride CSVs
(``{pid}_PerceptionStride_{L,R}_*.csv``) written by
:class:`perception_test.PerceptionExperiment`.

This is a **diagnostic** tool — it does NOT fit psychometric functions
or compute PSE / JND.  For each session it produces:

  1. ``staircase.png``    — comparison t_peak vs trial #, colored by
                            response, separate panels for from-above
                            and from-below approaches.
  2. ``reversals.png``    — connect reversals only, with the reference
                            line, to visualise convergence.
  3. ``stride_dur.png``   — boxplot of actual stride duration grouped
                            by trial phase (A / B / rest) per side.
  4. ``profile_gallery.png`` — overlay every unique comparison's
                            Collins curve, colored by trial #.
  5. ``summary.txt``      — counts (real / catch / reversals), false-
                            alarm rate from catch trials.

Usage
-----
::

    python DataLog/analysis/perception_plots.py --latest
    python DataLog/analysis/perception_plots.py --participant SAV6
    python DataLog/analysis/perception_plots.py path/to/Perception.csv

Author: Max Miller — Auburn University
"""
from __future__ import annotations

import argparse
import os
import sys
import glob
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


# ---------------------------------------------------------------------------
#  File-discovery helpers
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _is_trial_csv(p: Path) -> bool:
    """Trial CSVs are ``{pid}_Perception_{ts}.csv``.  Reject the
    per-sample ExoLogger files (``..._Perception_{L|R}_..._full.csv``)
    and the per-stride files (``..._PerceptionStride_...``)."""
    name = p.name
    if "PerceptionStride" in name:
        return False
    if name.endswith("_full.csv"):
        return False
    # After the participant id, the next token must be exactly
    # "Perception" followed by a timestamp (no _L_ / _R_ side tag).
    stem = p.stem
    if "_Perception_" not in stem:
        return False
    after = stem.split("_Perception_", 1)[1]
    # Side-tagged ExoLogger files have the form "L_<ts>" or "R_<ts>"
    if after.startswith(("L_", "R_")):
        return False
    return True


def _find_trial_csv(args) -> Path:
    if args.path:
        p = Path(args.path)
        if not p.exists():
            sys.exit(f"File not found: {p}")
        return p
    pattern = "*_Perception_*.csv"
    if args.participant:
        pattern = f"{args.participant}_Perception_*.csv"
    matches = [p for p in sorted(DATA_DIR.glob(pattern))
               if _is_trial_csv(p)]
    if not matches:
        sys.exit(f"No trial CSVs matching {pattern} in {DATA_DIR}\n"
                 f"(expected ``{{pid}}_Perception_{{timestamp}}.csv`` — "
                 "per-sample ``..._full.csv`` and per-stride "
                 "``..._PerceptionStride_...`` files are excluded.)")
    return matches[-1]


def _find_stride_csv(trial_csv: Path, side: str) -> Path | None:
    """Locate the matching ``PerceptionStride_{L,R}`` CSV.

    The trial CSV name is ``{pid}_Perception_{ts}.csv``; the stride CSV
    name is ``{pid}_PerceptionStride_{L|R}_{ts}.csv``.  Timestamps differ
    by < 1 s, so match on participant id and pick the stride file with
    the closest ``mtime``.
    """
    name = trial_csv.stem            # e.g. P001_Perception_2026-...
    pid = name.split("_Perception_")[0]
    cands = sorted(DATA_DIR.glob(f"{pid}_PerceptionStride_{side}_*.csv"))
    if not cands:
        return None
    target = trial_csv.stat().st_mtime
    return min(cands, key=lambda p: abs(p.stat().st_mtime - target))


# ---------------------------------------------------------------------------
#  Collins curve (mirror of perception_test._collins_curve)
# ---------------------------------------------------------------------------
def collins_curve(t_peak, t_act_start=26.0, t_act_end=61.6,
                  weight=75.0, peak_tn=0.225, n_pts=201):
    t_p = float(t_peak)
    t_r = t_p - t_act_start
    t_f = t_act_end - t_p
    if t_r <= 0 or t_f <= 0:
        xs = np.linspace(0, 100, n_pts)
        return xs, np.zeros_like(xs)
    peak_torque = peak_tn * weight
    onset = 0.0
    t0, t1 = t_act_start, t_act_end
    a1 = (2 * (onset - peak_torque)) / (t_r ** 3)
    b1 = (3 * (peak_torque - onset) * (t_p + t0)) / (t_r ** 3)
    c1 = (6 * (onset - peak_torque) * t_p * t0) / (t_r ** 3)
    d1 = (t_p ** 3 * onset - 3 * t0 * t_p ** 2 * onset
          + 3 * t0 ** 2 * t_p * peak_torque
          - t0 ** 3 * peak_torque) / (t_r ** 3)
    a2 = (peak_torque - onset) / (2 * t_f ** 3)
    b2 = (3 * (onset - peak_torque) * t1) / (2 * t_f ** 3)
    c2 = (3 * (peak_torque - onset)
          * (-t_p ** 2 + 2 * t1 * t_p)) / (2 * t_f ** 3)
    d2 = (2 * peak_torque * t1 ** 3
          - 6 * peak_torque * t1 ** 2 * t_p
          + 3 * peak_torque * t1 * t_p ** 2
          + 3 * onset * t1 * t_p ** 2
          - 2 * onset * t_p ** 3) / (2 * t_f ** 3)
    xs = np.linspace(0, 100, n_pts)
    ys = np.zeros_like(xs)
    asc = (xs >= t0) & (xs <= t_p)
    desc = (xs > t_p) & (xs <= t1)
    ys[asc]  = a1 * xs[asc] ** 3  + b1 * xs[asc] ** 2  + c1 * xs[asc]  + d1
    ys[desc] = a2 * xs[desc] ** 3 + b2 * xs[desc] ** 2 + c2 * xs[desc] + d2
    return xs, ys


# ---------------------------------------------------------------------------
#  Plot 1: Staircase trajectory
# ---------------------------------------------------------------------------
def plot_staircase(df: pd.DataFrame, out: Path, ref: float):
    approaches = sorted(df["Approach"].dropna().unique())
    n = max(1, len(approaches))
    fig, axes = plt.subplots(n, 1, figsize=(9, 3 * n + 0.2),
                             sharex=False, squeeze=False)
    for i, app in enumerate(approaches):
        ax = axes[i, 0]
        sub = df[df["Approach"] == app].copy()
        sub["idx"] = np.arange(1, len(sub) + 1)
        ax.axhline(ref, color="k", lw=0.8, ls=":", label=f"reference {ref:.1f}%")
        # Plot trajectory line (real trials only)
        real = sub[sub["Catch Trial"] != "Yes"]
        ax.plot(real["idx"], real["Comparison t_peak"], "-", color="#888",
                lw=1, alpha=0.6)
        # Markers by response and catch
        for resp, marker, color in [("Same", "o", "#2a8"),
                                    ("Different", "s", "#c33")]:
            for catch, mfc in [("No", color), ("Yes", "white")]:
                mask = (sub["Response"] == resp) & (sub["Catch Trial"] == catch)
                if mask.any():
                    ax.scatter(sub.loc[mask, "idx"],
                               sub.loc[mask, "Comparison t_peak"],
                               marker=marker, c=mfc, edgecolors=color,
                               s=55, linewidths=1.4,
                               label=f"{resp} {'(catch)' if catch=='Yes' else ''}")
        # Mark reversals
        rev = sub[sub["Is Reversal"] == "Yes"]
        if len(rev):
            ax.scatter(rev["idx"], rev["Comparison t_peak"],
                       s=160, facecolors="none", edgecolors="#06f",
                       lw=1.2, label="reversal")
        ax.set_title(f"Approach: {app}")
        ax.set_xlabel("Trial #")
        ax.set_ylabel("Comparison t_peak (% gait)")
        ax.grid(alpha=0.3)
        # Deduplicate legend
        h, l = ax.get_legend_handles_labels()
        seen = {}
        for hh, ll in zip(h, l):
            seen.setdefault(ll, hh)
        ax.legend(seen.values(), seen.keys(), fontsize=8, loc="best")
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------------------
#  Plot 2: Reversals only
# ---------------------------------------------------------------------------
def plot_reversals(df: pd.DataFrame, out: Path, ref: float):
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.axhline(ref, color="k", lw=0.8, ls=":", label=f"reference {ref:.1f}%")
    for app, color in [("from_above", "#c33"), ("from_below", "#2a8")]:
        sub = df[(df["Approach"] == app)
                 & (df["Is Reversal"] == "Yes")].reset_index(drop=True)
        if not len(sub):
            continue
        ax.plot(np.arange(1, len(sub) + 1), sub["Comparison t_peak"],
                "o-", color=color, lw=1.5, label=f"{app}")
    ax.set_xlabel("Reversal #")
    ax.set_ylabel("Comparison t_peak at reversal (% gait)")
    ax.set_title("Reversals only — convergence toward reference")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------------------
#  Plot 3: Stride duration boxplot
# ---------------------------------------------------------------------------
def plot_stride_dur(stride_L: pd.DataFrame | None,
                    stride_R: pd.DataFrame | None,
                    out: Path):
    sides = []
    if stride_L is not None and len(stride_L):
        sides.append(("Left", stride_L))
    if stride_R is not None and len(stride_R):
        sides.append(("Right", stride_R))
    if not sides:
        return
    fig, axes = plt.subplots(1, len(sides), figsize=(5 * len(sides), 4),
                             squeeze=False)
    for i, (label, df) in enumerate(sides):
        ax = axes[0, i]
        df = df.copy()
        df["actual_stride_dur"] = pd.to_numeric(df["actual_stride_dur"],
                                                errors="coerce")
        df = df.dropna(subset=["actual_stride_dur"])
        if "trial_phase" in df.columns:
            phases = ["A", "B"]
            data = [df.loc[df["trial_phase"] == p,
                           "actual_stride_dur"].values for p in phases]
            ax.boxplot(data, labels=phases, showfliers=False)
        else:
            ax.boxplot([df["actual_stride_dur"].values], labels=["all"])
        ax.set_title(f"{label} stride duration")
        ax.set_ylabel("ms")
        ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------------------
#  Plot 4: Torque profile gallery
# ---------------------------------------------------------------------------
def plot_profile_gallery(df: pd.DataFrame, out: Path, weight: float):
    fig, ax = plt.subplots(figsize=(8, 4))
    uniq = df.dropna(subset=["Comparison t_peak"]).copy()
    uniq["Comparison t_peak"] = pd.to_numeric(uniq["Comparison t_peak"],
                                              errors="coerce")
    uniq = uniq.dropna(subset=["Comparison t_peak"])
    if not len(uniq):
        plt.close(fig); return
    cmap = plt.get_cmap("viridis")
    n = len(uniq)
    for i, (_, row) in enumerate(uniq.iterrows()):
        xs, ys = collins_curve(row["Comparison t_peak"], weight=weight)
        ax.plot(xs, ys, color=cmap(i / max(1, n - 1)), lw=0.8, alpha=0.7)
    # Reference profile in black
    ref = uniq["Reference t_peak"].iloc[0]
    xs, ys = collins_curve(ref, weight=weight)
    ax.plot(xs, ys, "k-", lw=2.2, label=f"reference {ref:.1f}%")
    ax.set_xlabel("% gait")
    ax.set_ylabel("Torque (Nm)")
    ax.set_title(f"Torque-profile gallery — {n} comparisons "
                 "(viridis = trial order)")
    ax.axvline(26.0, color="#88a", lw=0.5, ls=":")
    ax.axvline(61.6, color="#88a", lw=0.5, ls=":")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------------------
#  Summary text
# ---------------------------------------------------------------------------
def write_summary(df: pd.DataFrame, out: Path):
    lines = []
    n = len(df)
    n_catch = int((df["Catch Trial"] == "Yes").sum())
    n_real = n - n_catch
    n_rev = int((df.get("Is Reversal", "") == "Yes").sum())
    lines.append(f"Total trials:       {n}")
    lines.append(f"  real:             {n_real}")
    lines.append(f"  catch:            {n_catch}")
    lines.append(f"Reversals:          {n_rev}")
    if n_catch:
        catch = df[df["Catch Trial"] == "Yes"]
        fa = (catch["Response"] == "Different").mean()
        lines.append(f"Catch false-alarm rate: {fa:.2%} "
                     f"({(catch['Response']=='Different').sum()}/{n_catch})")
    by_app = df.groupby("Approach").size()
    for app, k in by_app.items():
        lines.append(f"  approach {app}: {k} trials")
    out.write_text("\n".join(lines) + "\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="Path to Perception trial CSV")
    ap.add_argument("--latest", action="store_true",
                    help="Use the most recent Perception_*.csv")
    ap.add_argument("--participant", "-p",
                    help="Filter by participant id (e.g. SAV6)")
    ap.add_argument("--weight", type=float, default=75.0,
                    help="Body weight (kg) for profile gallery (default 75)")
    args = ap.parse_args()

    trial_csv = _find_trial_csv(args)
    print(f"Trial CSV: {trial_csv}")
    df = pd.read_csv(trial_csv)
    if "Reference t_peak" in df.columns:
        ref = float(pd.to_numeric(df["Reference t_peak"],
                                  errors="coerce").dropna().iloc[0])
    else:
        ref = 51.3
    out_dir = trial_csv.parent / f"{trial_csv.stem}_plots"
    out_dir.mkdir(exist_ok=True)

    plot_staircase(df, out_dir / "staircase.png", ref)
    plot_reversals(df, out_dir / "reversals.png", ref)

    stride_L_path = _find_stride_csv(trial_csv, "L")
    stride_R_path = _find_stride_csv(trial_csv, "R")
    sL = pd.read_csv(stride_L_path) if stride_L_path else None
    sR = pd.read_csv(stride_R_path) if stride_R_path else None
    plot_stride_dur(sL, sR, out_dir / "stride_dur.png")

    plot_profile_gallery(df, out_dir / "profile_gallery.png", args.weight)
    summary = write_summary(df, out_dir / "summary.txt")
    print(summary)
    print(f"\nFigures saved → {out_dir}")


if __name__ == "__main__":
    main()

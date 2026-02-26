#!/usr/bin/env python3
"""
Post‑experiment analysis for the Rise / Fall Time Perception Experiment.
========================================================================

Reads the CSV trial logs produced by ``perception_test.py`` and:

* plots the adaptive staircase (comparison value *vs.* trial number),
* marks reversal points,
* computes the **Just Noticeable Difference (JND)** from the last *N*
  reversals,
* optionally combines *from‑above* and *from‑below* runs, and
* summarises catch‑trial accuracy.

Usage::

    python data/data_analysis.py --csv path/to/trial_log.csv
    python data/data_analysis.py --csv run_above.csv run_below.csv --combine

Author:  Max Miller — Auburn University
"""

from __future__ import annotations

import argparse
import os
import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# ======================================================================
#  Utility helpers
# ======================================================================

def _detect_reversals(values: np.ndarray) -> np.ndarray:
    """Return boolean mask where a reversal (direction change) occurs.

    A reversal is defined as a change in the sign of the consecutive
    difference of the *comparison* values (ignoring catch trials where the
    value does not update).
    """
    diffs = np.diff(values)
    # Remove zeros (no change)
    nonzero = diffs != 0
    signs = np.sign(diffs)

    # Fill zeros with previous sign for comparison
    for i in range(1, len(signs)):
        if signs[i] == 0:
            signs[i] = signs[i - 1]

    reversals = np.zeros(len(values), dtype=bool)
    for i in range(1, len(signs)):
        if signs[i] != 0 and signs[i - 1] != 0 and signs[i] != signs[i - 1]:
            # The reversal corresponds to the *new* trial
            reversals[i + 1] = True  # +1 because diff shifts index
    return reversals


def compute_jnd(
    comparison_values: np.ndarray,
    discard_first: int = 4,
) -> tuple[float, float]:
    """Compute the JND from reversal values.

    Parameters
    ----------
    comparison_values : array of comparison values for non‑catch trials only.
    discard_first : how many early reversals to discard (default 4).

    Returns
    -------
    jnd_mean, jnd_std
    """
    rev_mask = _detect_reversals(comparison_values)
    rev_values = comparison_values[rev_mask]

    if len(rev_values) <= discard_first:
        print("  ⚠  Not enough reversals to discard the first "
              f"{discard_first}.  Using all reversals.")
        used = rev_values
    else:
        used = rev_values[discard_first:]

    if len(used) == 0:
        return np.nan, np.nan

    return float(np.mean(used)), float(np.std(used, ddof=1))


# ======================================================================
#  Plotting
# ======================================================================

def plot_staircase(df: pd.DataFrame, title: str = "", save_path: str | None = None):
    """Plot the adaptive staircase from a single run's trial data."""
    fig, ax = plt.subplots(figsize=(10, 5))

    # Separate catch / non‑catch
    is_catch = df["Catch Trial"].str.lower() == "yes"
    non_catch = df[~is_catch].copy()
    catch = df[is_catch].copy()

    ax.plot(non_catch["Trial #"], non_catch["Comparison Value"],
            "o-", color="tab:blue", label="Non‑catch trials")
    if len(catch):
        ax.plot(catch["Trial #"], catch["Comparison Value"],
                "x", color="tab:orange", ms=9, mew=2, label="Catch trials")

    # Reference line
    ref = df["Reference Value"].iloc[0]
    ax.axhline(ref, color="grey", ls="--", lw=1, label=f"Reference = {ref:.1f}%")

    # Reversals (non‑catch only)
    comp_arr = non_catch["Comparison Value"].values
    rev_mask = _detect_reversals(comp_arr)
    rev_trials = non_catch["Trial #"].values[rev_mask]
    rev_vals = comp_arr[rev_mask]
    ax.plot(rev_trials, rev_vals, "D", color="tab:red", ms=8,
            zorder=5, label="Reversals")

    ax.set_xlabel("Trial number")
    ax.set_ylabel("Comparison value (% stride)")
    ax.set_title(title or "Adaptive Staircase")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    if save_path:
        fig.savefig(save_path, dpi=150)
        print(f"  → Saved staircase plot: {save_path}")
    return fig, ax


def plot_combined_jnd(jnds: dict, save_path: str | None = None):
    """Bar chart comparing JNDs across conditions / approach directions."""
    fig, ax = plt.subplots(figsize=(6, 4))

    labels = list(jnds.keys())
    means = [jnds[k]["mean"] for k in labels]
    stds = [jnds[k]["std"] for k in labels]

    x = np.arange(len(labels))
    bars = ax.bar(x, means, yerr=stds, capsize=5,
                  color=["tab:blue", "tab:orange"][:len(labels)])
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("JND (% stride)")
    ax.set_title("Just Noticeable Difference — Summary")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()

    if save_path:
        fig.savefig(save_path, dpi=150)
        print(f"  → Saved JND plot: {save_path}")
    return fig, ax


# ======================================================================
#  Analysis driver
# ======================================================================

def analyse_single(csv_path: str, discard_first: int = 4,
                   show: bool = True) -> dict:
    """Analyse one run (one CSV file)."""
    df = pd.read_csv(csv_path)
    name = os.path.splitext(os.path.basename(csv_path))[0]
    print(f"\n{'=' * 60}")
    print(f"  File: {csv_path}")
    print(f"{'=' * 60}")

    # ---- basic info ------------------------------------------------
    test_mode = df["Test Mode"].iloc[0] if "Test Mode" in df.columns else "?"
    reference = df["Reference Value"].iloc[0]
    n_trials = len(df)
    n_catch = (df["Catch Trial"].str.lower() == "yes").sum()
    print(f"  Test mode     : {test_mode}")
    print(f"  Reference     : {reference:.1f}%")
    print(f"  Total trials  : {n_trials}")
    print(f"  Catch trials  : {n_catch}  "
          f"({100 * n_catch / n_trials:.0f}%)" if n_trials else "")

    # ---- catch‑trial accuracy --------------------------------------
    catch_df = df[df["Catch Trial"].str.lower() == "yes"]
    if len(catch_df):
        correct_catch = (catch_df["Response"].str.lower() == "same").sum()
        print(f"  Catch accuracy: {correct_catch}/{len(catch_df)}  "
              f"({100 * correct_catch / len(catch_df):.0f}%)")

    # ---- JND (non‑catch only) --------------------------------------
    non_catch = df[df["Catch Trial"].str.lower() != "yes"].copy()
    comp_vals = non_catch["Comparison Value"].values
    jnd_mean, jnd_std = compute_jnd(comp_vals, discard_first=discard_first)
    jnd_abs = abs(jnd_mean - reference) if not np.isnan(jnd_mean) else np.nan
    print(f"  JND mean      : {jnd_mean:.2f}% stride")
    print(f"  JND |Δ|       : {jnd_abs:.2f}% stride")
    print(f"  JND std       : {jnd_std:.2f}%")

    # ---- plot staircase -------------------------------------------
    fig_path = os.path.join(os.path.dirname(csv_path),
                            f"{name}_staircase.png")
    plot_staircase(df, title=f"Staircase — {name}", save_path=fig_path)

    if show:
        plt.show(block=False)

    return {
        "name": name,
        "test_mode": test_mode,
        "reference": reference,
        "jnd_mean": jnd_mean,
        "jnd_abs": jnd_abs,
        "jnd_std": jnd_std,
        "n_trials": n_trials,
        "n_catch": n_catch,
    }


def analyse_combined(csv_paths: list[str], discard_first: int = 4):
    """Analyse multiple runs and produce a combined summary."""
    results = []
    for p in csv_paths:
        results.append(analyse_single(p, discard_first=discard_first,
                                      show=False))

    # Combined JND bar chart
    jnds = {r["name"]: {"mean": r["jnd_abs"], "std": r["jnd_std"]}
            for r in results}
    out_dir = os.path.dirname(csv_paths[0])
    plot_combined_jnd(jnds,
                      save_path=os.path.join(out_dir, "jnd_summary.png"))

    # Summary table
    print(f"\n{'=' * 60}")
    print("  Combined Summary")
    print(f"{'=' * 60}")
    summary = pd.DataFrame(results)
    print(summary[["name", "test_mode", "reference",
                    "jnd_mean", "jnd_abs", "jnd_std",
                    "n_trials", "n_catch"]].to_string(index=False))

    summary_path = os.path.join(out_dir, "jnd_summary.csv")
    summary.to_csv(summary_path, index=False)
    print(f"\n  → Summary CSV: {summary_path}")

    plt.show()


# ======================================================================
#  CLI
# ======================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Analyse rise/fall‑time perception experiment results.")
    parser.add_argument("--csv", nargs="+", required=True,
                        help="Path(s) to trial‑log CSV(s) produced by the "
                             "perception test.")
    parser.add_argument("--discard", type=int, default=4,
                        help="Number of early reversals to discard "
                             "(default: 4).")
    parser.add_argument("--combine", action="store_true",
                        help="Combine all supplied CSVs into a single "
                             "summary (JND bar chart + table).")
    args = parser.parse_args()

    for p in args.csv:
        if not os.path.isfile(p):
            print(f"File not found: {p}", file=sys.stderr)
            sys.exit(1)

    if args.combine and len(args.csv) > 1:
        analyse_combined(args.csv, discard_first=args.discard)
    else:
        for p in args.csv:
            analyse_single(p, discard_first=args.discard)
        plt.show()


if __name__ == "__main__":
    main()

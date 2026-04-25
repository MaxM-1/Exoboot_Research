"""
Plot Position-Gain Test Results
================================

Reads the CSV produced by ``position_gain_test.py`` and produces a 4-panel
diagnostic plot per kp value.  Saves an image next to the CSV.

Usage::

    python calibration/plot_position_gain_test.py \\
        --csv calibration/position_gain_test_2026-04-25_15h30m00s.csv
"""

import argparse
import os

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    p = argparse.ArgumentParser(description="Plot position-gain test CSV.")
    p.add_argument("--csv", required=True, help="Path to gain-test CSV")
    args = p.parse_args()

    df = pd.read_csv(args.csv)
    kp_values = list(df["kp"].unique())
    n = len(kp_values)

    fig, axes = plt.subplots(n, 4, figsize=(16, 2.6 * n), sharex=False)
    if n == 1:
        axes = axes.reshape(1, -1)

    for row, kp in enumerate(kp_values):
        g = df[df["kp"] == kp].reset_index(drop=True)
        t = g["t"].values
        ank = g["ank_ang"].values
        mot = g["mot_ang"].values
        tgt = g["target"].values
        cur = g["mot_cur"].values
        err = g["pos_err"].values
        sx  = g["status_ex"].values

        # Column 0: ankle angle over time
        ax = axes[row, 0]
        ax.plot(t, ank, lw=0.8, color="C0")
        ax.set_ylabel(f"kp={kp}\nankle ticks")
        ax.set_title("ankle angle" if row == 0 else "")
        ax.grid(alpha=0.3)

        # Column 1: motor target vs actual
        ax = axes[row, 1]
        ax.plot(t, tgt, lw=0.8, color="k", label="target = polyval(ank)")
        ax.plot(t, mot, lw=0.8, color="C1", label="actual mot_ang")
        ax.set_ylabel("motor ticks")
        ax.set_title("motor: target vs actual" if row == 0 else "")
        ax.grid(alpha=0.3)
        if row == 0:
            ax.legend(fontsize=8)

        # Column 2: position error
        ax = axes[row, 2]
        ax.plot(t, err, lw=0.8, color="C3")
        ax.axhline(0, color="k", lw=0.4)
        ax.set_ylabel("position error\n(ticks)")
        ax.set_title("target − actual" if row == 0 else "")
        ax.grid(alpha=0.3)

        # Column 3: motor current with safety lines
        ax = axes[row, 3]
        ax.plot(t, cur, lw=0.8, color="C2")
        ax.axhline(0, color="k", lw=0.4)
        ax.axhline( 8000, color="orange", ls=":", lw=0.8, label="±8 A abort")
        ax.axhline(-8000, color="orange", ls=":", lw=0.8)
        ax.axhline( 28000, color="red", ls=":", lw=0.8, label="±28 A fuse")
        ax.axhline(-28000, color="red", ls=":", lw=0.8)
        # Shade fault windows
        fault = (sx == 2)
        if fault.any():
            ed = np.diff(fault.astype(int))
            s = np.where(ed == 1)[0] + 1
            e = np.where(ed == -1)[0] + 1
            if fault.iloc[0]: s = np.concatenate([[0], s])
            if fault.iloc[-1]: e = np.concatenate([e, [len(g)]])
            for ss, ee in zip(s, e):
                ax.axvspan(t[ss], t[min(ee, len(g) - 1)],
                           color="red", alpha=0.20)
        ax.set_ylabel("mot_cur (mA)")
        ax.set_title("motor current" if row == 0 else "")
        ax.set_ylim(-30000, 30000)
        ax.grid(alpha=0.3)
        if row == 0:
            ax.legend(fontsize=8, loc="upper right")

    for ax in axes[-1, :]:
        ax.set_xlabel("Time (s)")

    plt.suptitle(f"Position-gain test: {os.path.basename(args.csv)}", y=0.995)
    plt.tight_layout()
    out = args.csv.rsplit(".", 1)[0] + ".png"
    plt.savefig(out, dpi=130)
    print(f"Saved plot → {out}")


if __name__ == "__main__":
    main()

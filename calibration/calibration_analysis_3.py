"""
calibration_analysis_3.py
-------------------------
Fit a 4th-order polynomial mapping ankle ticks -> motor ticks from ONE
calibration CSV (one boot at a time) and update the existing INI-format
bootCal.txt in place.

bootCal.txt format (preserved):
    [ids]
    left  = <boot_id>
    right = <boot_id>

    [<boot_id>]
    ankle_reading_55_deg = <int>
    poly4 = ...
    poly3 = ...
    poly2 = ...
    poly1 = ...
    poly0 = ...

Usage:
  python calibration/calibration_analysis_3.py \\
      --csv calibration/left_boot_calib_<timestamp>.csv \\
      --side left  --boot-id C719 --ankle-55 5935

  python calibration/calibration_analysis_3.py \\
      --csv calibration/right_boot_calib_<timestamp>.csv \\
      --side right --boot-id C6D9 --ankle-55 2663

Diagnostics:
  - prints sample count, ankle/motor ranges, coefficients, RMS residual
  - saves fit_<side>.png next to bootCal.txt
"""

import argparse
import configparser
import os

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

POLY_ORDER = 4   # 5 coefficients (poly4..poly0), matches bootCal.txt
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))


def load_csv(path):
    df = pd.read_csv(path)
    # Accept both new and legacy column names.
    if "ank_ang" in df.columns and "mot_ang" in df.columns:
        ankle = df["ank_ang"].to_numpy(dtype=float)
        motor = df["mot_ang"].to_numpy(dtype=float)
    elif "ankle_ticks" in df.columns and "motor_ticks" in df.columns:
        ankle = df["ankle_ticks"].to_numpy(dtype=float)
        motor = df["motor_ticks"].to_numpy(dtype=float)
    else:
        raise ValueError(
            f"CSV {path} must contain (ank_ang, mot_ang) or (ankle_ticks, motor_ticks)"
        )
    return ankle, motor


def trim_startup(ankle, motor, skip_samples=50):
    """Drop the first ~0.5 s where current is ramping up."""
    if len(ankle) > skip_samples + 10:
        return ankle[skip_samples:], motor[skip_samples:]
    return ankle, motor


def fit(ankle, motor, order=POLY_ORDER):
    # Use unique ankle values to avoid singular fits when held still.
    unique_ankle, idx = np.unique(ankle, return_index=True)
    unique_motor = motor[idx]
    coeffs = np.polyfit(unique_ankle, unique_motor, order)
    return coeffs, unique_ankle, unique_motor


def report(coeffs, ankle, motor, label):
    pred = np.polyval(coeffs, ankle)
    res  = motor - pred
    rms  = float(np.sqrt(np.mean(res ** 2)))
    print(f"\n--- {label} ---")
    print(f"  samples:     {len(ankle)}")
    print(f"  ankle range: [{ankle.min():.0f}, {ankle.max():.0f}] ticks")
    print(f"  motor range: [{motor.min():.0f}, {motor.max():.0f}] ticks")
    print(f"  coefficients (highest order first):")
    for i, c in enumerate(coeffs):
        print(f"    poly{POLY_ORDER - i} = {c: .6e}")
    print(f"  RMS residual: {rms:.1f} motor ticks")
    return pred, res


def plot(ankle, motor, pred, res, label, save_path):
    order = np.argsort(ankle)
    fig, ax = plt.subplots(2, 1, figsize=(8, 6), sharex=True)
    ax[0].plot(ankle, motor, ".", ms=3, alpha=0.4, label="measured")
    ax[0].plot(ankle[order], pred[order], "r-", lw=1.5, label="polyfit")
    ax[0].set_ylabel("motor ticks")
    ax[0].set_title(f"{label} calibration  (order {POLY_ORDER})")
    ax[0].legend()
    ax[0].grid(True, alpha=0.3)
    ax[1].plot(ankle, res, ".", ms=3)
    ax[1].axhline(0, color="k", lw=0.5)
    ax[1].set_xlabel("ankle ticks")
    ax[1].set_ylabel("residual (motor ticks)")
    ax[1].grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(save_path, dpi=120)
    plt.close(fig)
    print(f"  plot saved:   {save_path}")


def update_bootcal(coeffs, side, boot_id, ankle_55, cal_path):
    """Update INI-format bootCal.txt in place, preserving the other boot."""
    cfg = configparser.ConfigParser()
    # Preserve case (boot IDs like C719 are uppercase; configparser would
    # otherwise lowercase keys).
    cfg.optionxform = str
    if os.path.exists(cal_path):
        cfg.read(cal_path)

    if not cfg.has_section("ids"):
        cfg.add_section("ids")
    cfg.set("ids", side, boot_id)

    if not cfg.has_section(boot_id):
        cfg.add_section(boot_id)
    cfg.set(boot_id, "ankle_reading_55_deg", str(int(ankle_55)))
    # poly4..poly0 = coeffs[0]..coeffs[4]
    for i, name in enumerate(["poly4", "poly3", "poly2", "poly1", "poly0"]):
        cfg.set(boot_id, name, f"{coeffs[i]:.15e}")

    with open(cal_path, "w") as fh:
        cfg.write(fh)
    print(f"\nbootCal.txt updated → [ids].{side} = {boot_id}, [{boot_id}] coefficients written")
    print(f"  file: {cal_path}")


def main():
    p = argparse.ArgumentParser(description="Analyze ONE boot calibration CSV → bootCal.txt")
    p.add_argument("--csv", required=True, help="Path to calibration CSV")
    p.add_argument("--side", required=True, choices=["left", "right"])
    p.add_argument("--boot-id", required=True, help="Boot ID, e.g. C719")
    p.add_argument("--ankle-55", required=True, type=int,
                   help="Raw ankle encoder reading at 55 deg plantarflexion")
    p.add_argument("--cal-path", default=os.path.join(_THIS_DIR, "bootCal.txt"),
                   help="Path to bootCal.txt (default: alongside this script)")
    p.add_argument("--no-trim", action="store_true",
                   help="Do not drop the first ~0.5 s of samples")
    args = p.parse_args()

    ankle_raw, motor_raw = load_csv(args.csv)
    if args.no_trim:
        ankle, motor = ankle_raw, motor_raw
    else:
        ankle, motor = trim_startup(ankle_raw, motor_raw)

    coeffs, ua, um = fit(ankle, motor)
    pred, res = report(coeffs, ua, um, args.side)
    plot(ua, um, pred, res, args.side,
         save_path=os.path.join(_THIS_DIR, f"fit_{args.side}.png"))
    update_bootcal(coeffs, args.side, args.boot_id, args.ankle_55, args.cal_path)


if __name__ == "__main__":
    main()

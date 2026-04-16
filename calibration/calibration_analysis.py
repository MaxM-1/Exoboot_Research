"""
Calibration Analysis — Python port of exoTorqueCalcCal.m
=========================================================

Reads a calibration CSV produced by ``boot_calibration.py``, fits a
5th‑order polynomial mapping **ankle ticks → motor ticks**, and writes
the coefficients to ``bootCal.txt``.

Usage::

    python calibration/calibration_analysis.py \\
        --csv calibration/left_boot_calib_2026-02-26_10h30m00s.csv \\
        --side left \\
        --boot-id AB1F \\
        --ankle-55 8170

The script also produces diagnostic plots so you can verify the fit.
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")          # headless‑safe backend (works over SSH)
import matplotlib.pyplot as plt
import configparser

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ======================================================================
#  Core calibration function
# ======================================================================
def calibrate(csv_path: str, side_sign: int):
    """Fit the ankle→motor polynomial and return coefficients + data.

    Parameters
    ----------
    csv_path : str   Path to calibration CSV.
    side_sign : int  +1 for left, −1 for right.

    Returns
    -------
    poly_coeffs : ndarray  6 coefficients of the 5th‑order polynomial
                           (highest‑degree first, same as ``numpy.polyfit``).
    unique_ankle : ndarray Unique ankle‑tick values used for fitting.
    unique_motor : ndarray Corresponding motor‑tick values.
    """
    df = pd.read_csv(csv_path)

    ankle = df["ank_ang"].values
    motor = df["mot_ang"].values

    # ---- Trim unstable start (first ~0.5 s) -----------------------
    time_ms = df["state_time"].values.astype(float)
    time_s = (time_ms - time_ms[0]) / 1000.0

    # Skip the first 0.5 s where motor current ramps up and the
    # initial position is still settling.  Use the rest of the data
    # all the way to the end of the sweep.
    start_idx = int(np.searchsorted(time_s, 0.5))
    if start_idx >= len(motor) - 10:
        start_idx = 0       # fallback: not enough data after trim
    stop_idx = len(motor) - 1

    ankle_seg = ankle[start_idx: stop_idx + 1]
    motor_seg = motor[start_idx: stop_idx + 1]

    print(f"start_idx = {start_idx}  stop_idx = {stop_idx}  "
          f"segment length = {len(ankle_seg)}")

    # ---- Unique ankle values -----------------------------------------
    unique_ankle, unique_idx = np.unique(ankle_seg, return_index=True)
    unique_motor = motor_seg[unique_idx]

    # ---- 5th(now 4th)‑order polynomial fit ------------------------------------
    poly_coeffs = np.polyfit(unique_ankle, unique_motor, 4)   #changed from 5->4 
    print("Polynomial coefficients (highest→lowest degree):")
    for i, c in enumerate(poly_coeffs):
        print(f"  c[{5-i}]  = {c: .15e}")

    return poly_coeffs, unique_ankle, unique_motor, ankle, motor, start_idx, stop_idx


# ======================================================================
#  Save coefficients to bootCal.txt
# ======================================================================
def save_to_bootcal(poly_coeffs, boot_id, ankle_55, side,
                    cal_path=None):
    """Append / update a section in ``bootCal.txt``."""
    if cal_path is None:
        cal_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "bootCal.txt")

    cfg = configparser.ConfigParser()
    cfg.read(cal_path)

    # Update IDs section
    if not cfg.has_section("ids"):
        cfg.add_section("ids")
    cfg.set("ids", side, boot_id)

    # Update boot section
    if not cfg.has_section(boot_id):
        cfg.add_section(boot_id)
    cfg.set(boot_id, "ankle_reading_55_deg", str(int(ankle_55)))
    # poly4…poly0 = coeffs[0]…coeffs[4]  (skip the constant coeffs[5])
    for i, name in enumerate(["poly4", "poly3", "poly2", "poly1", "poly0"]):
        cfg.set(boot_id, name, f"{poly_coeffs[i]:.15e}")

    with open(cal_path, "w") as fh:
        cfg.write(fh)
    print(f"bootCal.txt updated → section [{boot_id}]  ({cal_path})")


# ======================================================================
#  Diagnostic plots
# ======================================================================
def plot_calibration(poly_coeffs, unique_ankle, unique_motor,
                     ankle_raw, motor_raw, start_idx, stop_idx,
                     side, out_dir):
    """Save three diagnostic figures to *out_dir*."""
    fitted_motor = np.polyval(poly_coeffs, unique_ankle)

    # ---- 1. Raw data -------------------------------------------------
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(ankle_raw, motor_raw, ".", markersize=1, label="raw")
    ax.axvline(ankle_raw[start_idx], color="g", ls="--", label="start")
    ax.axvline(ankle_raw[stop_idx], color="r", ls="--", label="stop")
    ax.set_xlabel("Ankle ticks")
    ax.set_ylabel("Motor ticks")
    ax.set_title(f"Raw ankle vs motor ({side})")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, f"{side}_calib_raw.png"), dpi=150)
    plt.close(fig)

    # ---- 2. Fitted polynomial ----------------------------------------
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(unique_ankle, unique_motor, ".", markersize=2, label="data")
    ax.plot(unique_ankle, fitted_motor, "r-", linewidth=1.5, label="poly‑5 fit")
    ax.set_xlabel("Ankle ticks")
    ax.set_ylabel("Motor ticks")
    ax.set_title(f"Polynomial fit ({side})")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, f"{side}_calib_fit.png"), dpi=150)
    plt.close(fig)

    # ---- 3. Derivative ωm/ωa ----------------------------------------
    c = poly_coeffs
    deriv = (5 * c[0] * unique_ankle**4
             + 4 * c[1] * unique_ankle**3
             + 3 * c[2] * unique_ankle**2
             + 2 * c[3] * unique_ankle
             + c[4])
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(unique_ankle, deriv, "b-")
    ax.set_xlabel("Ankle ticks")
    ax.set_ylabel("ωm / ωa")
    ax.set_title(f"Motor‑to‑ankle velocity ratio ({side})")
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, f"{side}_calib_deriv.png"), dpi=150)
    plt.close(fig)

    print(f"Plots saved to {out_dir}")


# ======================================================================
#  CLI
# ======================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Analyse boot calibration CSV → bootCal.txt")
    parser.add_argument("--csv", required=True, help="Path to calibration CSV")
    parser.add_argument("--side", required=True, choices=["left", "right"])
    parser.add_argument("--boot-id", required=True,
                        help="4‑character hex boot ID  (e.g. AB1F)")
    parser.add_argument("--ankle-55", required=True, type=int,
                        help="Raw ankle encoder reading at 55° plantarflexion")
    args = parser.parse_args()

    side_sign = 1 if args.side == "left" else -1

    poly, ua, um, ar, mr, si, sti = calibrate(args.csv, side_sign)

    out_dir = os.path.dirname(os.path.abspath(__file__))
    plot_calibration(poly, ua, um, ar, mr, si, sti, args.side, out_dir)
    save_to_bootcal(poly, args.boot_id, args.ankle_55, args.side)


if __name__ == "__main__":
    main()

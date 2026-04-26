"""
Calibration Analysis v2 — Engagement-Filtered, Monotonicity-Checked
====================================================================

Reads a calibration CSV produced by ``boot_calibration2.py`` (or the
original ``boot_calibration.py``), fits a 4th-order polynomial mapping
**ankle ticks → motor ticks**, and writes the coefficients to
``bootCal.txt``.

Improvements over ``calibration_analysis.py``:

1.  **Engagement filtering.** The fit is restricted to the contiguous
    region where BOTH motor and ankle are actively changing.  This
    drops slack-engagement transients at the start and motor-saturation
    flats at the end, which would otherwise distort the polynomial.

2.  **Working-range bounds.** The script writes ``ank_min`` and
    ``ank_max`` into ``bootCal.txt`` so the controller can clamp ankle
    inputs to the calibrated range and avoid extrapolation during
    walking.

3.  **Monotonicity check.** Before saving, the script verifies that the
    polynomial's derivative ``dm/dank`` does not change sign across the
    working range.  A chain drive cannot reverse direction kinematically
    — if the fit's derivative flips sign, the polynomial is unphysical
    and the script refuses to save it.

Usage::

    python calibration/calibration_analysis2.py \\
        --csv calibration/left_boot_calib_v2_2026-04-26_15h17m12s.csv \\
        --side left \\
        --boot-id C719 \\
        --ankle-55 7970
"""

import argparse
import configparser
import os
import sys

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ======================================================================
#  Engagement detection
# ======================================================================
def find_engaged_sweep(df, fs=100, motor_vel_min_ticks_per_s=2000,
                       ankle_vel_min_ticks_per_s=100, win_samples=10):
    """Find the longest contiguous region where BOTH motor and ankle move.

    During slack engagement at the start, the motor sweeps fast while
    ankle is still — high motor velocity, near-zero ankle velocity.
    During saturation at the end, motor is pinned at its mechanical limit
    while ankle keeps moving — near-zero motor velocity, high ankle
    velocity.  The kinematic sweep is the region where BOTH change.

    Velocities are computed by finite difference over ``win_samples``
    samples, so the thresholds correspond to ticks/second.

    Returns
    -------
    start_idx, end_idx : int
        Indices bracketing the longest "both moving" run.
    """
    n = len(df)
    a = df["ank_ang"].values.astype(float)
    m = df["mot_ang"].values.astype(float)

    av = np.zeros(n)
    mv = np.zeros(n)
    if n > win_samples:
        av[win_samples:] = (a[win_samples:] - a[:-win_samples]) / (win_samples / fs)
        mv[win_samples:] = (m[win_samples:] - m[:-win_samples]) / (win_samples / fs)

    moving = (
        (np.abs(av) > ankle_vel_min_ticks_per_s)
        & (np.abs(mv) > motor_vel_min_ticks_per_s)
    )

    edges = np.diff(moving.astype(int))
    starts = np.where(edges == 1)[0] + 1
    ends   = np.where(edges == -1)[0] + 1
    if moving[0]:  starts = np.concatenate([[0], starts])
    if moving[-1]: ends   = np.concatenate([ends, [n]])

    if len(starts) == 0:
        return 0, n

    runs = sorted(zip(starts, ends), key=lambda r: r[1] - r[0], reverse=True)
    return int(runs[0][0]), int(runs[0][1])


# ======================================================================
#  Core calibration function
# ======================================================================
def calibrate(csv_path):
    """Fit the ankle→motor polynomial on the engaged-sweep region.

    Returns
    -------
    poly_coeffs : ndarray   5 coefficients of the 4th-order polynomial
                            (highest degree first, same as ``np.polyfit``).
    unique_ankle, unique_motor : ndarrays  Fitted (deduped) data.
    ankle_raw, motor_raw : ndarrays        All raw samples.
    start_idx, stop_idx : int              Engaged-sweep bracket.
    ank_min, ank_max : int                 Working-range bounds.
    """
    df = pd.read_csv(csv_path)

    ankle = df["ank_ang"].values
    motor = df["mot_ang"].values

    start_idx, stop_idx = find_engaged_sweep(df)
    print(f"Engaged-sweep region: idx {start_idx} – {stop_idx}  "
          f"({(stop_idx - start_idx)/100:.1f}s @ 100Hz, "
          f"{stop_idx - start_idx} samples)")

    ankle_seg = ankle[start_idx:stop_idx]
    motor_seg = motor[start_idx:stop_idx]

    if len(ankle_seg) < 50:
        raise RuntimeError(
            f"Engaged-sweep region is too short ({len(ankle_seg)} samples). "
            "Recollect calibration data with a slower, continuous sweep."
        )

    unique_ankle, unique_idx = np.unique(ankle_seg, return_index=True)
    unique_motor = motor_seg[unique_idx]

    if len(unique_ankle) < 20:
        raise RuntimeError(
            f"Only {len(unique_ankle)} unique ankle values in sweep — "
            "ankle resolution too coarse to fit a polynomial."
        )

    poly_coeffs = np.polyfit(unique_ankle, unique_motor, 4)

    ank_min = int(unique_ankle.min())
    ank_max = int(unique_ankle.max())

    print("Polynomial coefficients (highest → lowest degree):")
    for i, c in enumerate(poly_coeffs):
        print(f"  poly{4-i} = {c:+.15e}")
    print(f"Working ankle range: [{ank_min}, {ank_max}]  "
          f"(swing {ank_max - ank_min} ticks)")

    return (poly_coeffs, unique_ankle, unique_motor,
            ankle, motor, start_idx, stop_idx, ank_min, ank_max)


# ======================================================================
#  Monotonicity check
# ======================================================================
def check_monotonic(poly_coeffs, ank_min, ank_max, n_samples=200):
    """Verify ``dm/dank`` keeps a single sign across the working range.

    Raises ``ValueError`` if the polynomial's derivative changes sign in
    [ank_min, ank_max], which would be physically impossible for a
    one-way chain drive.

    Returns the (min, max) of dm/dank across the range so the caller
    can report the chain ratio bounds.
    """
    c = poly_coeffs
    deriv_coeffs = [4 * c[0], 3 * c[1], 2 * c[2], c[3]]
    xs = np.linspace(ank_min, ank_max, n_samples)
    derivs = np.polyval(deriv_coeffs, xs)
    signs = np.sign(derivs)
    if not np.all(signs == signs[0]):
        n_flips = int((np.diff(signs) != 0).sum())
        raise ValueError(
            f"Polynomial derivative changes sign {n_flips} time(s) across "
            f"the working ankle range [{ank_min}, {ank_max}].  This is "
            "physically impossible for a chain drive — refusing to save. "
            "Inspect the calibration data; the sweep may have included "
            "saturation flats or a direction reversal."
        )
    return float(derivs.min()), float(derivs.max())


# ======================================================================
#  Save coefficients to bootCal.txt
# ======================================================================
def save_to_bootcal(poly_coeffs, boot_id, ankle_55, side,
                    ank_min, ank_max, cal_path=None):
    """Append / update a section in ``bootCal.txt``.

    In addition to the polynomial, this writes ``ank_min`` and
    ``ank_max`` so the runtime controller can clamp ankle inputs to
    the calibrated range.
    """
    if cal_path is None:
        cal_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "bootCal.txt")

    cfg = configparser.ConfigParser()
    cfg.read(cal_path)

    if not cfg.has_section("ids"):
        cfg.add_section("ids")
    cfg.set("ids", side, boot_id)

    if not cfg.has_section(boot_id):
        cfg.add_section(boot_id)
    cfg.set(boot_id, "ankle_reading_55_deg", str(int(ankle_55)))
    cfg.set(boot_id, "ank_min", str(int(ank_min)))
    cfg.set(boot_id, "ank_max", str(int(ank_max)))
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
                     ank_min, ank_max, side, out_dir):
    """Save three diagnostic figures to *out_dir*."""
    fitted_motor = np.polyval(poly_coeffs, unique_ankle)

    # 1. Raw data with engagement region highlighted ---------------------
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(ankle_raw[:start_idx], motor_raw[:start_idx], ".",
            ms=1.5, color="lightgray", label="pre-engagement (dropped)")
    ax.plot(ankle_raw[start_idx:stop_idx], motor_raw[start_idx:stop_idx],
            ".", ms=1.5, color="C0", label="engaged sweep (fitted)")
    ax.plot(ankle_raw[stop_idx:], motor_raw[stop_idx:], ".",
            ms=1.5, color="lightgray", label="post-engagement (dropped)")
    ax.set_xlabel("Ankle ticks")
    ax.set_ylabel("Motor ticks")
    ax.set_title(f"Raw ankle vs motor ({side})")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, f"{side}_calib_raw.png"), dpi=150)
    plt.close(fig)

    # 2. Fitted polynomial ------------------------------------------------
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(unique_ankle, unique_motor, ".", ms=2, label="data")
    ax.plot(unique_ankle, fitted_motor, "r-", lw=1.5, label="poly-4 fit")
    ax.axvline(ank_min, color="g", ls="--", lw=1, label=f"ank_min={ank_min}")
    ax.axvline(ank_max, color="r", ls="--", lw=1, label=f"ank_max={ank_max}")
    ax.set_xlabel("Ankle ticks")
    ax.set_ylabel("Motor ticks")
    ax.set_title(f"Polynomial fit ({side})")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, f"{side}_calib_fit.png"), dpi=150)
    plt.close(fig)

    # 3. Derivative ωm/ωa -------------------------------------------------
    c = poly_coeffs
    deriv = (4 * c[0] * unique_ankle ** 3
             + 3 * c[1] * unique_ankle ** 2
             + 2 * c[2] * unique_ankle
             + c[3])
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(unique_ankle, deriv, "b-")
    ax.axhline(0, color="k", lw=0.4)
    ax.set_xlabel("Ankle ticks")
    ax.set_ylabel("ωm / ωa")
    ax.set_title(f"Motor-to-ankle velocity ratio ({side})")
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, f"{side}_calib_deriv.png"), dpi=150)
    plt.close(fig)

    print(f"Plots saved to {out_dir}")


# ======================================================================
#  CLI
# ======================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Analyse boot calibration CSV (v2) → bootCal.txt")
    parser.add_argument("--csv", required=True, help="Path to calibration CSV")
    parser.add_argument("--side", required=True, choices=["left", "right"])
    parser.add_argument("--boot-id", required=True,
                        help="4-character hex boot ID  (e.g. C719)")
    parser.add_argument("--ankle-55", required=True, type=int,
                        help="Raw ankle encoder reading at 55° plantarflexion")
    parser.add_argument("--no-monotonic-check", action="store_true",
                        help="Skip the monotonicity check (NOT recommended).")
    args = parser.parse_args()

    (poly, ua, um, ar, mr,
     si, sti, ank_min, ank_max) = calibrate(args.csv)

    out_dir = os.path.dirname(os.path.abspath(__file__))
    plot_calibration(poly, ua, um, ar, mr, si, sti,
                     ank_min, ank_max, args.side, out_dir)

    if not args.no_monotonic_check:
        try:
            d_lo, d_hi = check_monotonic(poly, ank_min, ank_max)
            print(f"Monotonicity check passed.  "
                  f"dm/dank ranges from {d_lo:.2f} to {d_hi:.2f} "
                  f"across [{ank_min}, {ank_max}].")
        except ValueError as e:
            print(f"\nMONOTONICITY CHECK FAILED:\n  {e}")
            print("\nNot saving to bootCal.txt.  Diagnostic plots have been "
                  "written so you can inspect what went wrong.")
            sys.exit(2)

    save_to_bootcal(poly, args.boot_id, args.ankle_55,
                    args.side, ank_min, ank_max)
    print("\nDone.\n")


if __name__ == "__main__":
    main()

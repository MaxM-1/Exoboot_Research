"""
Static Position-Control Gain Test
==================================

Tests the position controller's response to ankle perturbations at different
kp gain settings, **without** running the gait-detection or torque profile.
The participant stands still wearing ONE boot. For each kp value, the script
loads the calibration polynomial, anchors it via an encoder check (same as
the production controller), then commands ``command_motor_position(polyval(ank))``
at 100 Hz while the participant gently rocks their ankle. Motor current,
position error, and any firmware fault flags are logged.

This is meant to characterize how peak transient current scales with kp under
controlled conditions, isolated from gait dynamics, so a safe operating gain
can be picked before the next walking trial.

Usage::

    python calibration/z_position_gain_test_4_25.py --port /dev/ttyACM0 --side left

Options::

    --kp-list 10,20,30,50,100   comma-separated kp values to test
    --trial-sec 10              seconds per kp trial (default 10)
    --abort-ma  8000            per-sample |mot_cur| safety abort (default 8000)

Safety:
    * The script monitors |mot_cur| every sample and stops the motor immediately
      if it exceeds ``--abort-ma`` (default 8 A).  This is well below the 28 A
      I^2t fuse trip and well above what a healthy position controller should
      ever command during gentle ankle motion.
    * The script also aborts the trial if status_ex transitions to 2 (firmware
      fault), so a dropped trial does not propagate into the next kp setting.
    * Ctrl+C at any time stops the motor and closes the device cleanly.

Output:
    A timestamped CSV ``position_gain_test_<timestamp>.csv`` is written next to
    this script with per-sample data for all kp trials.  A summary table is
    printed to the console.
"""

import argparse
import configparser
import os
import signal
import sys
from time import sleep, strftime, time

import numpy as np
import pandas as pd

# Allow running from calibration/ or project root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flexsea.device import Device
from config import (
    FIRMWARE_VERSION, LOG_LEVEL,
    LEFT, RIGHT,
    CURRENT_GAINS, ZEROING_CURRENT,
)


# --------------------------------------------------------------------------
#  Defaults
# --------------------------------------------------------------------------

# Safest first.  kp=100 is the production setting; lower values are tested
# to find a gain that keeps transient current well below the fuse limit.
DEFAULT_KP_VALUES = [10, 20, 30, 50, 100]

# Held fixed across all trials.  Per Max's locked-in config:
KI_FIXED = 20
KD_FIXED = 35
FF_FIXED = 0          # ff is ignored by the position controller per Dephy docs

DEFAULT_TRIAL_SEC = 10
DEFAULT_FREQ_HZ = 100

# Per-sample current safety threshold.  8 A << 28 A fuse trip; gentle ankle
# motion with a healthy controller should stay well under 1 A.
DEFAULT_ABORT_MA = 8000


# --------------------------------------------------------------------------
#  Calibration loading  (mirrors ExoBoot._load_calibration)
# --------------------------------------------------------------------------

def load_calibration(side):
    """Load polynomial coefficients from bootCal.txt for the given side.

    Returns
    -------
    boot_id : str
    coeffs  : list[float]   poly4 .. poly0  (length 5, 4th-order polynomial)
    """
    cal_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "calibration", "bootCal.txt",
    )
    if not os.path.exists(cal_path):
        # Fallback: same directory
        cal_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "bootCal.txt")
    cfg = configparser.ConfigParser()
    cfg.read(cal_path)
    side_key = "left" if side == LEFT else "right"
    boot_id = cfg.get("ids", side_key)
    coeffs = [cfg.getfloat(boot_id, f"poly{i}") for i in range(4, -1, -1)]
    return boot_id, list(coeffs)


# --------------------------------------------------------------------------
#  Boot setup procedures  (mirror ExoBoot.zero_boot / encoder_check)
# --------------------------------------------------------------------------

def tighten_chain(device, side):
    """Apply ZEROING_CURRENT to take up chain slack."""
    print("Tightening chain ...")
    device.set_gains(**CURRENT_GAINS)
    sleep(0.5)
    device.command_motor_current(ZEROING_CURRENT * side)
    sleep(3.0)
    device.stop_motor()
    sleep(0.5)


def encoder_check(device, coeffs):
    """Anchor the polynomial constant term so polyval(current_ank) matches
    current motor position.  Modifies and returns coeffs."""
    print("Encoder check ...")
    sleep(0.5)
    for i in range(3):
        data = device.read()
        print(f"  reading {i}: mot_ang = {data.get('mot_ang', '?')}")
    data = device.read()
    a = data.get("ank_ang", 0)
    m = data.get("mot_ang", 0)
    pred = float(np.floor(np.polyval(coeffs, a)))
    offset = m - pred
    coeffs[-1] = coeffs[-1] + offset
    pred_shifted = float(np.floor(np.polyval(coeffs, a)))
    print(f"  ankle         = {a}")
    print(f"  motor actual  = {m}")
    print(f"  motor desired = {pred:.0f}")
    print(f"  offset        = {offset:+.0f}")
    print(f"  motor shifted = {pred_shifted:.0f}")
    print("  Polynomial anchored ✓\n")
    return coeffs


# --------------------------------------------------------------------------
#  One trial at a single kp
# --------------------------------------------------------------------------

def run_trial(device, coeffs, kp, ki, kd, ff,
              duration_s, freq_hz, abort_ma):
    """Run a single kp trial.  Returns (records: list[dict], aborted_at: float|None)."""
    print(f"\n>>> Trial: kp={kp}, ki={ki}, kd={kd}, ff={ff}")
    print(f"    Hold position-control for {duration_s:.1f} s.")
    print( "    Gently dorsi/plantar flex your ankle by ~5 degrees during the trial.")
    print( "    Move slowly — the goal is to characterize, not to provoke.")
    input ("    Press ENTER when ready ... ")

    # Set the requested position-control gains
    device.set_gains(kp=int(kp), ki=int(ki), kd=int(kd), k=0, b=0, ff=int(ff))
    sleep(0.2)

    period = 1.0 / freq_hz
    n_samples = int(duration_s * freq_hz)
    records = []
    aborted_at = None
    t_start = time()

    for _ in range(n_samples):
        try:
            data = device.read()
        except Exception as exc:
            print(f"  read error — stopping trial: {exc}")
            break

        a   = data.get("ank_ang",   0)
        m   = data.get("mot_ang",   0)
        cur = data.get("mot_cur",   0)
        av  = data.get("ank_vel",   0)
        mv  = data.get("mot_vel",   0)
        sx  = data.get("status_ex", 0)

        # The production controller's position target is polyval(ank).
        # We mirror that exactly — no perturbation injection, just normal
        # tracking.  The participant supplies the perturbation through
        # voluntary ankle motion.
        target = int(np.floor(np.polyval(coeffs, a)))
        device.command_motor_position(target)

        t_now = time() - t_start
        records.append({
            "kp":        kp,
            "ki":        ki,
            "kd":        kd,
            "t":         t_now,
            "ank_ang":   a,
            "ank_vel":   av,
            "mot_ang":   m,
            "mot_vel":   mv,
            "mot_cur":   cur,
            "target":    target,
            "pos_err":   target - m,
            "status_ex": sx,
        })

        # ---- Safety aborts ------------------------------------------------
        if abs(cur) > abort_ma:
            print(f"  !! ABORT |mot_cur|={abs(cur)} mA > {abort_ma} mA at t={t_now:.2f}s")
            device.stop_motor()
            aborted_at = t_now
            break
        if sx == 2:
            print(f"  !! ABORT status_ex=2 (firmware fault) at t={t_now:.2f}s")
            device.stop_motor()
            aborted_at = t_now
            break
        # ------------------------------------------------------------------

        sleep(period)

    device.stop_motor()
    msg = f"    Trial complete: {len(records)} samples"
    if aborted_at is not None:
        msg += f"   [ABORTED at t={aborted_at:.2f}s]"
    print(msg)
    sleep(1.0)  # give the firmware a moment between trials
    return records, aborted_at


# --------------------------------------------------------------------------
#  Output and summary
# --------------------------------------------------------------------------

def summarize_and_save(all_records, abort_table, output_dir):
    """Save combined CSV; print per-kp summary table."""
    if not all_records:
        print("No records collected — nothing to save.")
        return None

    df = pd.DataFrame(all_records)
    fname = f"position_gain_test_{strftime('%Y-%m-%d_%Hh%Mm%Ss')}.csv"
    out_path = os.path.join(output_dir, fname)
    df.to_csv(out_path, index=False)
    print(f"\nFull trial data → {out_path}")

    print("\n" + "=" * 88)
    print(f"{'kp':>5s} {'samples':>8s} {'|cur|max':>10s} {'|cur|p95':>10s} "
          f"{'|cur|mean':>11s} {'|err|p95':>10s} {'|err|max':>10s} {'aborted':>10s}")
    print("-" * 88)
    for kp, g in df.groupby("kp", sort=False):
        c = g["mot_cur"].abs()
        e = g["pos_err"].abs()
        ab = abort_table.get(kp)
        ab_str = f"t={ab:.2f}s" if ab is not None else "no"
        print(f"{kp:>5d} {len(g):>8d} {c.max():>10.0f} {c.quantile(0.95):>10.0f} "
              f"{c.mean():>11.0f} {e.quantile(0.95):>10.0f} {e.max():>10.0f} "
              f"{ab_str:>10s}")
    print("=" * 88)
    print("\nInterpretation:")
    print("  * A 'good' kp keeps |cur|max well below the abort threshold AND")
    print("    keeps |err|p95 small (motor faithfully tracks the polynomial).")
    print("  * If |cur|max is at the abort threshold, that kp is too aggressive")
    print("    for this hardware/sampling-rate combo.")
    print("  * If |err|p95 is large (>1000 ticks) AND |cur| is small, the gain")
    print("    is too low — the controller is sluggish.\n")

    return out_path


# --------------------------------------------------------------------------
#  Main
# --------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="Static position-control gain sweep for one ExoBoot.")
    p.add_argument("--port", default="/dev/ttyACM0",
                   help="Serial port (default: /dev/ttyACM0)")
    p.add_argument("--side", choices=["left", "right"], default="left",
                   help="Boot side (default: left)")
    p.add_argument("--fw", default=FIRMWARE_VERSION,
                   help=f"Firmware version (default: {FIRMWARE_VERSION})")
    p.add_argument("--freq", type=int, default=DEFAULT_FREQ_HZ,
                   help=f"Streaming Hz (default: {DEFAULT_FREQ_HZ})")
    p.add_argument("--trial-sec", type=float, default=DEFAULT_TRIAL_SEC,
                   help=f"Per-trial duration s (default: {DEFAULT_TRIAL_SEC})")
    p.add_argument("--abort-ma", type=int, default=DEFAULT_ABORT_MA,
                   help=f"|mot_cur| abort threshold mA (default: {DEFAULT_ABORT_MA})")
    p.add_argument("--kp-list", type=str,
                   default=",".join(str(x) for x in DEFAULT_KP_VALUES),
                   help=f"Comma-separated kp values "
                        f"(default: {','.join(str(x) for x in DEFAULT_KP_VALUES)})")
    p.add_argument("--ki", type=int, default=KI_FIXED,
                   help=f"ki held constant across trials (default: {KI_FIXED})")
    p.add_argument("--kd", type=int, default=KD_FIXED,
                   help=f"kd held constant across trials (default: {KD_FIXED})")
    p.add_argument("--stop-after-abort", action="store_true",
                   help="If a trial aborts, skip remaining higher-kp trials")
    args = p.parse_args()

    side_sign = LEFT if args.side == "left" else RIGHT
    kp_list = [int(x.strip()) for x in args.kp_list.split(",") if x.strip()]
    if not kp_list:
        print("No kp values specified.")
        sys.exit(2)

    # ---- Connect ---------------------------------------------------------
    print(f"\nConnecting to ExoBoot on {args.port} (fw {args.fw}) ...")
    device = Device(firmwareVersion=args.fw, port=args.port,
                    logLevel=LOG_LEVEL, interactive=False)
    device.open()
    sleep(1.0)
    device.start_streaming(frequency=args.freq)
    sleep(0.1)
    print(f"Connected — device ID {device.id}\n")

    # ---- Graceful shutdown handler --------------------------------------
    def _shutdown(*_):
        print("\nInterrupted — stopping motor and closing device.")
        try:
            device.stop_motor()
            sleep(0.3)
            device.close()
        except Exception:
            pass
        sys.exit(1)
    signal.signal(signal.SIGINT, _shutdown)

    # ---- Calibration -----------------------------------------------------
    boot_id, coeffs = load_calibration(side_sign)
    print(f"Loaded polynomial for boot {boot_id}: poly4..poly0 = "
          + ", ".join(f"{c:+.3e}" for c in coeffs) + "\n")

    # ---- Set up: tighten chain and anchor polynomial --------------------
    input(">>> Wear the boot, stand still on a flat surface.\n"
          "    Then press ENTER to tighten the chain ... ")
    tighten_chain(device, side_sign)
    coeffs = encoder_check(device, coeffs)

    # ---- Sweep through kp values ----------------------------------------
    all_records = []
    abort_table = {}
    for kp in kp_list:
        recs, aborted_at = run_trial(
            device, coeffs,
            kp=kp, ki=args.ki, kd=args.kd, ff=FF_FIXED,
            duration_s=args.trial_sec, freq_hz=args.freq,
            abort_ma=args.abort_ma,
        )
        all_records.extend(recs)
        if aborted_at is not None:
            abort_table[kp] = aborted_at
            if args.stop_after_abort:
                print("--stop-after-abort set; skipping remaining kp values.")
                break

    # ---- Save and summarize ---------------------------------------------
    out_dir = os.path.dirname(os.path.abspath(__file__))
    summarize_and_save(all_records, abort_table, out_dir)

    # ---- Cleanup --------------------------------------------------------
    device.stop_motor()
    sleep(0.3)
    device.close()
    print("Done.\n")


if __name__ == "__main__":
    main()

"""
Exo-Off Walking Recorder
========================

Streams sensor data from both ExoBoots at 100 Hz with motors commanding
**zero current** (no torque). Used for troubleshooting heel-strike
detection thresholds and torque-curve latency without any actuation
disturbing the gait.

Boots must be powered and USB-connected (the IMU stream comes through
the FlexSEA device). This script simply never sends a non-zero current
command — `ExoBoot.read_data` runs heel-strike detection internally,
so the produced per-iteration CSVs contain `gyroz_signed`, `seg_trigger`,
arm/trigger thresholds, etc., ready for analysis.

Run::

    python exo_off_tests/exo_off_recorder.py --participant YA5 --weight 75
    python exo_off_tests/exo_off_recorder.py --participant YA5 --weight 75 \
        --duration 60 --speed 1.25

Author: Max Miller — Auburn University
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from time import strftime

# Allow running from repo root or from this folder
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import (
    LEFT, RIGHT, LEFT_PORT, RIGHT_PORT, STREAMING_FREQUENCY,
)
from exo_init import ExoBoot
from exo_logger import ExoLogger


# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Record exo sensor data with zero motor current.")
    p.add_argument("--participant", required=True, help="Participant ID, e.g. YA5")
    p.add_argument("--weight", type=float, required=True, help="Body weight (kg) — metadata only")
    p.add_argument("--duration", type=float, default=None,
                   help="Recording duration in seconds. Omit for run-until-Ctrl-C.")
    p.add_argument("--speed", type=float, default=None,
                   help="Treadmill speed in m/s (metadata only, e.g. 1.25)")
    p.add_argument("--trial-name", default="ExoOff",
                   help="Trial name suffix used in filenames (default: ExoOff)")
    p.add_argument("--left-port", default=LEFT_PORT)
    p.add_argument("--right-port", default=RIGHT_PORT)
    p.add_argument("--out-dir", default=os.path.join(_HERE, "data"),
                   help="Output directory (default: exo_off_tests/data/)")
    return p.parse_args()


# ---------------------------------------------------------------------------
def main():
    args = parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    speed_tag = f"_v{args.speed:.2f}".replace(".", "p") if args.speed is not None else ""
    phase = f"{args.trial_name}{speed_tag}"

    print("=" * 70)
    print(f"  ExoOff Recorder | participant={args.participant}  "
          f"phase={phase}")
    print(f"  Streaming @ {STREAMING_FREQUENCY} Hz | motors at 0 mA (no torque)")
    if args.duration is not None:
        print(f"  Duration: {args.duration:.1f} s")
    else:
        print("  Duration: run-until-Ctrl-C")
    print("=" * 70)

    # ---- Open both boots (constructor opens device + starts streaming) ----
    print("Opening LEFT  boot...")
    left = ExoBoot(side=LEFT, port=args.left_port)
    print("Opening RIGHT boot...")
    right = ExoBoot(side=RIGHT, port=args.right_port)
    boots = [left, right]

    # Defensive: command zero current once before any reads.
    for b in boots:
        try:
            b.device.command_motor_current(0)
        except Exception:
            pass

    # ---- Loggers ----------------------------------------------------------
    log_params = {
        "user_weight": args.weight,
        "test_mode": "exo_off_recording",
        "approach": f"speed={args.speed}" if args.speed is not None else "",
    }
    left.logger = ExoLogger(args.out_dir, args.participant, left, phase, log_params)
    right.logger = ExoLogger(args.out_dir, args.participant, right, phase, log_params)
    for b in boots:
        b.logger.set_controller_mode("exo_off")

    print(f"  L log: {os.path.basename(left.logger.path)}")
    print(f"  R log: {os.path.basename(right.logger.path)}")
    print()
    print("Walk on the treadmill. Ctrl-C to stop." if args.duration is None
          else f"Walking for {args.duration:.1f} s...")

    # ---- Graceful Ctrl-C --------------------------------------------------
    stop_flag = {"stop": False}

    def _sig_handler(signum, frame):  # noqa: ARG001
        stop_flag["stop"] = True
        print("\n[Ctrl-C] Stopping...")

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    # ---- 100 Hz loop ------------------------------------------------------
    period = 1.0 / STREAMING_FREQUENCY
    t_start = time.perf_counter()
    next_t = t_start
    n = 0
    last_status = t_start
    try:
        while not stop_flag["stop"]:
            now = time.perf_counter()
            if args.duration is not None and (now - t_start) >= args.duration:
                break

            # Read + log both boots
            for b in boots:
                b.read_data()
                # Defensive zero-current every iter (belt-and-suspenders)
                try:
                    b.device.command_motor_current(0)
                except Exception:
                    pass
                b.logger.log(tau_Nm=0.0, current_cmd_mA=0.0)

            n += 1

            # Status line every 5 s
            if now - last_status >= 5.0:
                last_status = now
                print(f"  t={now - t_start:6.1f}s  "
                      f"L_HS={left.num_gait}  R_HS={right.num_gait}  "
                      f"L_gz={left.gyroz:+6.0f}  R_gz={right.gyroz:+6.0f}")

            # Pace to STREAMING_FREQUENCY
            next_t += period
            sleep_t = next_t - time.perf_counter()
            if sleep_t > 0:
                time.sleep(sleep_t)
            else:
                # Fell behind — resync (don't accumulate debt)
                next_t = time.perf_counter()
    finally:
        elapsed = time.perf_counter() - t_start
        print()
        print("=" * 70)
        print(f"Recording stopped after {elapsed:.1f} s ({n} iterations)")
        print(f"  Detected strides: L={left.num_gait}  R={right.num_gait}")

        # Tag FlexSEA datalog files with side / boot-id BEFORE stop_streaming
        for b in boots:
            try:
                b.tag_datalog(args.participant, phase)
            except Exception as exc:
                print(f"  tag_datalog failed: {exc}")

        # Close per-iter loggers
        for b in boots:
            try:
                b.logger.close()
            except Exception:
                pass

        # Safe shutdown (zero current x2, stop motor, stop streaming, close port)
        for b in boots:
            try:
                b.clean()
            except Exception as exc:
                print(f"  clean() failed: {exc}")

        # Combined merged CSV (per-iter rows joined by loop_iter)
        try:
            combined = _write_combined_csv(args.participant, phase,
                                           left.logger.path, right.logger.path,
                                           args.out_dir)
            if combined:
                print(f"  Combined CSV: {os.path.basename(combined)}")
        except Exception as exc:
            print(f"  Combined CSV write failed: {exc}")

        print("Done.")
        print("=" * 70)


# ---------------------------------------------------------------------------
def _write_combined_csv(participant: str, phase: str,
                        left_path: str, right_path: str,
                        out_dir: str) -> str | None:
    """Merge the two per-side `_full.csv` files into a single CSV joined
    on `loop_iter` so analysis tools that prefer one file have an option."""
    import csv

    if not (os.path.exists(left_path) and os.path.exists(right_path)):
        return None

    with open(left_path, newline="") as lf, open(right_path, newline="") as rf:
        lr = csv.reader(lf)
        rr = csv.reader(rf)
        l_header = next(lr)
        r_header = next(rr)

        ts = strftime("%Y-%m-%d_%Hh%Mm%Ss")
        out_name = f"{participant}_{phase}_combined_{ts}.csv"
        out_path = os.path.join(out_dir, out_name)
        with open(out_path, "w", newline="") as of:
            ow = csv.writer(of)
            ow.writerow([f"L_{c}" for c in l_header] + [f"R_{c}" for c in r_header])
            for l_row, r_row in zip(lr, rr):
                ow.writerow(l_row + r_row)
    return out_path


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    main()

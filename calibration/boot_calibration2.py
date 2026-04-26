"""
Boot Calibration v2 — Continuous-Sweep Procedure
=================================================

Connects to **one** ExoBoot at a time and records ankle-angle vs.
motor-angle data while the operator manually sweeps the boot from
**maximum plantarflexion** to **maximum dorsiflexion** in one slow
continuous motion.

Why a new procedure?
--------------------
The original ``boot_calibration.py`` collects for a fixed ``--collect-time``
(default 15 s).  If the operator finishes the sweep before the timer
expires, the boot ends up held at full dorsiflexion while the motor is
mechanically pinned at its limit — producing a long FLAT region in the
ankle-vs-motor data.  Those flat regions are not kinematic (the motor
can't move further; it's pile-up), and including them in the polynomial
fit produces a curve that bends incorrectly across the walking range.

This script removes that issue by:

* Letting the operator press ENTER to **start** recording (already at
  full plantarflex) and ENTER to **stop** recording (at full dorsiflex).
* Streaming a live readout of ankle/motor ticks during the sweep so the
  operator can confirm both are still changing.
* Refusing to save if the captured sweep is too short or doesn't move.

Recommended workflow (bench-only, no human in the boot)
--------------------------------------------------------
1.  Lay the boot flat on a table.  Pull the actuator lever arm fully
    forward (toward the toe) to **maximum dorsiflexion** so the chain
    has all of its slack out and is fully unspooled.  Then push the
    lever arm back so the boot reaches **maximum plantarflexion**.
2.  Run this script.  Let the script tighten the chain (zeroing the
    chain at the current — fully plantarflex — position).
3.  Press ENTER to start recording, then **slowly** sweep the lever
    arm from full plantarflex to full dorsiflex over ~10 s, in one
    continuous motion with no pauses, and **without** holding at either
    end.
4.  Press ENTER to stop recording.

Usage::

    python calibration/boot_calibration2.py --port /dev/ttyACM0 --side left
"""

import argparse
import os
import sys
import threading
from time import sleep, strftime

import pandas as pd

# Allow running from the calibration/ directory or project root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flexsea.device import Device
from config import FIRMWARE_VERSION, LOG_LEVEL


def _wait_for_enter(prompt, flag):
    """Background thread: print prompt and set flag[0] when ENTER is pressed."""
    input(prompt)
    flag[0] = True


def main():
    parser = argparse.ArgumentParser(
        description="ExoBoot calibration v2 — continuous-sweep procedure")
    parser.add_argument("--port", type=str, default="/dev/ttyACM0",
                        help="Serial port  (default: /dev/ttyACM0)")
    parser.add_argument("--side", type=str, choices=["left", "right"],
                        default="left", help="Boot side  (default: left)")
    parser.add_argument("--fw", type=str, default=FIRMWARE_VERSION,
                        help=f"Firmware version  (default: {FIRMWARE_VERSION})")
    parser.add_argument("--freq", type=int, default=100,
                        help="Streaming frequency in Hz  (default: 100)")
    parser.add_argument("--current", type=int, default=1000,
                        help="Tightening current in mA  (default: 1000)")
    parser.add_argument("--max-time", type=int, default=60,
                        help="Hard cap on recording duration in seconds "
                             "(default: 60). The operator should stop manually "
                             "before this — this is just a safety cap.")
    args = parser.parse_args()

    side_sign = 1 if args.side == "left" else -1

    # ---- Connect --------------------------------------------------------
    print(f"\nConnecting to ExoBoot on {args.port} (fw {args.fw}) …")
    device = Device(
        firmwareVersion=args.fw,
        port=args.port,
        logLevel=LOG_LEVEL,
        interactive=False,
    )
    device.open()
    sleep(1)
    device.start_streaming(frequency=args.freq)
    sleep(0.1)
    print(f"Connected — device ID {device.id}\n")

    # ---- Step 1: position the boot at full plantarflex -----------------
    input(">>> Lay boot flat on table.\n"
          "    Move the lever arm to FULL PLANTARFLEXION (chain ready to spool).\n"
          "    Then press ENTER to tighten the chain …")

    # ---- Step 2: tighten chain (zeros chain at plantarflex position) ---
    print("Applying motor current to tighten chain …")
    device.set_gains(kp=40, ki=400, kd=0, k=0, b=0, ff=128)
    sleep(0.5)
    tighten_current = args.current * side_sign
    for _ in range(30):
        device.command_motor_current(tighten_current)
        sleep(0.1)
    sleep(2)
    device.stop_motor()
    sleep(0.5)

    # Read the position right now — this is the "plantarflex anchor"
    sample = device.read()
    ank0 = sample.get("ank_ang", 0)
    mot0 = sample.get("mot_ang", 0)
    print(f"Chain tight at plantarflex: ank={ank0}, mot={mot0}\n")

    # ---- Step 3: continuous sweep with manual start / stop --------------
    print(">>> Ready to record.")
    print("    On ENTER below, recording starts.")
    print("    Slowly sweep the lever arm from PLANTARFLEX → DORSIFLEX over ~10 s.")
    print("    Move continuously — do NOT pause at either end.")
    print("    Press ENTER again when you reach full dorsiflex to STOP.\n")
    input("Press ENTER to START recording …")

    print(f"Recording … (press ENTER to stop, hard cap {args.max_time}s)")

    # Background thread waits for the second ENTER
    stop_flag = [False]
    t = threading.Thread(target=_wait_for_enter,
                         args=("", stop_flag), daemon=True)
    t.start()

    period = 1.0 / args.freq
    max_samples = args.max_time * args.freq
    records = []
    last_print = 0
    n = 0
    while n < max_samples:
        try:
            data = device.read()
        except Exception as exc:
            print(f"  read error — stopping: {exc}")
            break
        records.append({
            "state_time": data.get("state_time", 0),
            "ank_ang":    data.get("ank_ang",    0),
            "mot_ang":    data.get("mot_ang",    0),
            "mot_cur":    data.get("mot_cur",    0),
            "mot_vel":    data.get("mot_vel",    0),
            "ank_vel":    data.get("ank_vel",    0),
        })
        # Live readout every 0.5 s
        if n - last_print >= int(0.5 * args.freq):
            print(f"  t={n/args.freq:5.1f}s  ank={data.get('ank_ang','?'):>5}  "
                  f"mot={data.get('mot_ang','?'):>+7}  cur={data.get('mot_cur','?'):>+5}mA")
            last_print = n
        if stop_flag[0]:
            print("  STOP requested by operator.")
            break
        sleep(period)
        n += 1
    else:
        print(f"  Reached max-time cap ({args.max_time}s) — stopping recording.")

    # ---- Sanity checks --------------------------------------------------
    if len(records) < 100:
        print(f"\nERROR: only {len(records)} samples captured "
              "(less than 1 s).  Not saving.")
        device.stop_motor()
        sleep(0.3)
        device.close()
        sys.exit(1)

    df = pd.DataFrame(records)
    ank_swing = df["ank_ang"].max() - df["ank_ang"].min()
    mot_swing = df["mot_ang"].max() - df["mot_ang"].min()
    if ank_swing < 500:
        print(f"\nERROR: ankle swing was only {ank_swing} ticks "
              "— sweep too small.  Not saving.")
        device.stop_motor()
        sleep(0.3)
        device.close()
        sys.exit(1)
    if mot_swing < 5000:
        print(f"\nERROR: motor swing was only {mot_swing} ticks "
              "— chain may not have engaged.  Not saving.")
        device.stop_motor()
        sleep(0.3)
        device.close()
        sys.exit(1)

    # ---- Save -----------------------------------------------------------
    out_dir = os.path.dirname(os.path.abspath(__file__))
    fname = f"{args.side}_boot_calib_v2_{strftime('%Y-%m-%d_%Hh%Mm%Ss')}.csv"
    out_path = os.path.join(out_dir, fname)
    df.to_csv(out_path, index=False)
    print(f"\nSaved → {out_path}  ({len(df)} samples, {len(df)/args.freq:.1f}s)")
    print(f"  ankle range: {df['ank_ang'].min()} → {df['ank_ang'].max()}  "
          f"(swing {ank_swing})")
    print(f"  motor range: {df['mot_ang'].min()} → {df['mot_ang'].max()}  "
          f"(swing {mot_swing})")

    # ---- Cleanup --------------------------------------------------------
    device.stop_motor()
    sleep(0.3)
    device.close()
    print("\nDone.\n")


if __name__ == "__main__":
    main()

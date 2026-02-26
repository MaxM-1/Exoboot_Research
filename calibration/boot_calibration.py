"""
Boot Calibration — Data Collection
===================================

Connects to **one** ExoBoot at a time and records ankle‑angle vs.
motor‑angle data while the shoe is slowly dorsiflexed.  The resulting
CSV is then processed by ``calibration_analysis.py`` to produce the
polynomial coefficients stored in ``bootCal.txt``.

Usage::

    python calibration/boot_calibration.py --port /dev/ttyACM0 --side left

Procedure (per Xiangyu Peng's notes):
    1.  Start with the shoe fully **plantarflexed** and the belt **tight**.
    2.  A motor current is applied to keep the belt taut.
    3.  Slowly **dorsiflex** the shoe (the motor angle will change as the
        belt is pulled out — *this* direction gives the valid mapping).
    4.  Data is logged to a timestamped CSV when the collection ends.
"""

import argparse
import os
import sys
from time import sleep, strftime

import pandas as pd

# Allow running from the calibration/ directory or project root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flexsea.device import Device
from config import FIRMWARE_VERSION, LOG_LEVEL


def main():
    parser = argparse.ArgumentParser(description="ExoBoot calibration data collection")
    parser.add_argument("--port", type=str, default="/dev/ttyACM0",
                        help="Serial port  (default: /dev/ttyACM0)")
    parser.add_argument("--side", type=str, choices=["left", "right"],
                        default="left", help="Boot side  (default: left)")
    parser.add_argument("--fw", type=str, default=FIRMWARE_VERSION,
                        help=f"Firmware version  (default: {FIRMWARE_VERSION})")
    parser.add_argument("--freq", type=int, default=100,
                        help="Streaming frequency in Hz  (default: 100)")
    parser.add_argument("--current", type=int, default=1500,
                        help="Tightening current in mA  (default: 1500)")
    parser.add_argument("--collect-time", type=int, default=15,
                        help="Seconds to collect while dorsiflexing  (default: 15)")
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
    print(f"Connected — device ID {device.id}")

    # ---- Step 1: hold shoe fully plantarflexed --------------------------
    input("\n>>> Hold the shoe fully PLANTARFLEXED, then press ENTER …")

    # ---- Step 2: apply motor current to tighten strap -------------------
    print("Applying motor current to tighten strap …")
    device.set_gains(kp=100, ki=32, kd=0, k=0, b=0, ff=0)
    sleep(0.5)
    tighten_current = args.current * side_sign
    for _ in range(30):
        device.command_motor_current(tighten_current)
        sleep(0.1)
    sleep(2)
    device.stop_motor()
    sleep(0.5)
    print("Strap tight.")

    # ---- Step 3: dorsiflex slowly while recording -----------------------
    input(f"\n>>> Slowly DORSIFLEX the shoe over the next ~{args.collect_time}s.  "
          "Press ENTER to begin recording …")

    print(f"Recording for {args.collect_time} seconds …")
    records = []
    n_samples = args.collect_time * args.freq
    for _ in range(n_samples):
        data = device.read()
        records.append({
            "state_time": data.get("state_time", 0),
            "ank_ang": data.get("ank_ang", 0),
            "mot_ang": data.get("mot_ang", 0),
            "mot_cur": data.get("mot_cur", 0),
            "mot_vel": data.get("mot_vel", 0),
            "ank_vel": data.get("ank_vel", 0),
        })
        sleep(1.0 / args.freq)

    print("Recording complete.")

    # ---- Save CSV -------------------------------------------------------
    df = pd.DataFrame(records)
    out_dir = os.path.dirname(os.path.abspath(__file__))
    filename = f"{args.side}_boot_calib_{strftime('%Y-%m-%d_%Hh%Mm%Ss')}.csv"
    out_path = os.path.join(out_dir, filename)
    df.to_csv(out_path, index=False)
    print(f"Saved → {out_path}  ({len(df)} samples)")

    # ---- Clean up -------------------------------------------------------
    device.stop_motor()
    sleep(0.3)
    device.close()
    print("Done.\n")


if __name__ == "__main__":
    main()

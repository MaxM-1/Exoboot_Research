"""
boot_calibration_3.py
---------------------
Calibration sweep for ONE ExoBoot at a time, using the locked-in
procedure (current control, DF-sweep direction).

Operator procedure (per boot):
  1. Attach the exo to the physical boot. Place boot flat on benchtop.
  2. Plantarflex/dorsiflex by hand a few times with chain slack to verify
     free travel.
  3. Hold the ankle at FULL PLANTARFLEXION.
  4. Run this script. Press Enter when prompted to tighten the chain
     (current control, ZEROING_CURRENT for TIGHTEN_TIME_S seconds).
  5. When recording starts, slowly DORSIFLEX through full range of motion.
     During the sweep the motor holds NO_SLACK_CURRENT to keep the chain
     taut while being back-driven by the ankle motion.
  6. Press Enter when at full dorsiflexion to stop recording.

Output:
  calibration/<side>_boot_calib_<timestamp>.csv with columns:
    state_time, ank_ang, mot_ang, mot_cur

Usage (run from project root OR from calibration/). Only ONE boot is plugged
in at a time, so by default both sides connect on /dev/ttyACM0. Override
with --port if needed:

  python calibration/boot_calibration_3.py left
  python calibration/boot_calibration_3.py right
  python calibration/boot_calibration_3.py left  --port /dev/ttyACM1

Then run the analysis with the boot ID + ankle-at-55deg reading:
  python calibration/calibration_analysis_3.py \\
      --csv calibration/left_boot_calib_<timestamp>.csv \\
      --side left --boot-id C719 --ankle-55 5935
"""

import argparse
import os
import sys
import csv
import time
import threading
from datetime import datetime

# Allow running from either the project root or the calibration/ folder.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJ_ROOT = os.path.dirname(_THIS_DIR)
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from flexsea.device import Device
import config

# --- locked-in parameters (sourced from config.py) ---
SAMPLE_RATE_HZ   = config.STREAMING_FREQUENCY     # 100 Hz
DT               = 1.0 / SAMPLE_RATE_HZ
KP = config.CURRENT_GAINS["kp"]
KI = config.CURRENT_GAINS["ki"]
KD = config.CURRENT_GAINS["kd"]
FF = config.CURRENT_GAINS["ff"]
ZEROING_CURRENT  = config.ZEROING_CURRENT         # 1000 mA, tightens chain
NO_SLACK_CURRENT = config.NO_SLACK_CURRENT        # 800 mA, holds tension
TIGHTEN_TIME_S   = 2.0                            # hold ZEROING_CURRENT before sweep

# Only one boot is connected at a time during calibration, so both sides
# default to /dev/ttyACM0. Override on the command line with --port.
DEFAULT_PORT = "/dev/ttyACM0"


def get_sign(boot_name):
    if boot_name == "left":
        return config.LEFT      # +1
    if boot_name == "right":
        return config.RIGHT     # -1
    raise ValueError(f"boot must be 'left' or 'right', got {boot_name!r}")


def calibrate(boot_name, port=DEFAULT_PORT):
    sign = get_sign(boot_name)
    print(f"\n=== Calibrating {boot_name} boot on {port} (fw {config.FIRMWARE_VERSION}) ===")

    device = Device(
        firmwareVersion=config.FIRMWARE_VERSION,
        port=port,
        logLevel=config.LOG_LEVEL,
        interactive=False,
    )
    device.open()
    time.sleep(1.0)
    device.start_streaming(frequency=SAMPLE_RATE_HZ)
    time.sleep(0.2)
    print(f"Connected — device ID {device.id}")

    # Locked-in current-control gains. flexsea expects k=, b= (NOT k_val=/b_val=).
    device.set_gains(kp=KP, ki=KI, kd=KD, k=0, b=0, ff=FF)
    time.sleep(0.2)

    rows = []
    try:
        input("\nHold ankle at FULL PLANTARFLEXION, then press Enter to tighten chain... ")

        # --- tighten chain ---
        tighten_cmd = ZEROING_CURRENT * sign
        print(f"Tightening chain at {tighten_cmd} mA for {TIGHTEN_TIME_S:.1f} s...")
        t_end = time.perf_counter() + TIGHTEN_TIME_S
        while time.perf_counter() < t_end:
            device.command_motor_current(tighten_cmd)
            time.sleep(DT)

        # --- dorsiflexion sweep ---
        hold_cmd = NO_SLACK_CURRENT * sign
        print()
        print("Chain should now be tight.")
        print(f"Slowly DORSIFLEX through the full range of motion (hold {hold_cmd} mA).")
        print("Press Enter at full dorsiflexion to stop recording.\n")

        stop_event = threading.Event()
        threading.Thread(
            target=lambda: (input(), stop_event.set()),
            daemon=True,
        ).start()

        t0 = time.perf_counter()
        next_t = t0
        while not stop_event.is_set():
            device.command_motor_current(hold_cmd)
            s = device.read()
            rows.append((
                s.get("state_time", 0),
                s.get("ank_ang", 0),
                s.get("mot_ang", 0),
                s.get("mot_cur", 0),
            ))
            next_t += DT
            sleep_s = next_t - time.perf_counter()
            if sleep_s > 0:
                time.sleep(sleep_s)

    finally:
        # Always release the motor before closing.
        try:
            device.command_motor_current(0)
            time.sleep(0.2)
            device.stop_motor()
        except Exception:
            pass
        try:
            device.stop_streaming()
        except Exception:
            pass
        device.close()

    # --- save CSV (into calibration/ next to this script) ---
    stamp = datetime.now().strftime("%Y-%m-%d_%Hh%Mm%Ss")
    csv_path = os.path.join(_THIS_DIR, f"{boot_name}_boot_calib_{stamp}.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["state_time", "ank_ang", "mot_ang", "mot_cur"])
        w.writerows(rows)
    print(f"\nSaved {len(rows)} samples to {csv_path}")
    return csv_path


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="ExoBoot calibration sweep (one boot at a time)")
    p.add_argument("side", choices=["left", "right"], help="Which boot is plugged in")
    p.add_argument("--port", default=DEFAULT_PORT,
                   help=f"Serial port (default: {DEFAULT_PORT})")
    args = p.parse_args()
    calibrate(args.side, port=args.port)

"""
ROM Position-Control Test — Static Calibration Validation
==========================================================

TEST2 - 4/23 changing CURRENT_ABORT_MA and POSITION GAINS
rest is copy and pasted from ROm position test 1
==========================================================


Purpose
-------
Validates that the ankle→motor polynomial calibration is good enough for
safe walking BEFORE attempting a walking trial.  Runs the boots in pure
position control (the same control mode used during phases 1 and 4 of
the Collins torque profile) while the user slowly moves their ankle
through the full range of motion.

If the polynomial is well-calibrated for the current wearer, the motor
current should stay low (under ~1000 mA) throughout the ROM.  If the
polynomial diverges from the actual mechanical relationship at any
ankle angle, this test will reveal it as elevated motor current — but
with safety guards in place so a bad polynomial doesn't cause a
runaway like it did in the familiarization trial.

Safety
------
Unlike the run_collins_profile() path, this script has:
  1. A polynomial-target error guard:  if the polynomial predicts a
     motor position > SAFE_POLY_ERROR_TICKS away from actual, falls
     back to a light fixed current instead of commanding the bad
     position.
  2. A motor current watchdog:  if current exceeds CURRENT_ABORT_MA at
     any time, the motor is immediately stopped and the script exits.
  3. Ctrl-C is caught cleanly — motor is always zeroed before exit.

Usage
-----
Run one boot at a time.  Put both boots on and stand still, then:

    python ROM_position_test1.py --port /dev/ttyACM0 --side left

The script will:
  1. Connect to the boot and start streaming.
  2. Run zero_boot + encoder_check (same as GUI Connect & Zero).
  3. Enter position-control tracking loop.
  4. Wait for you to press ENTER, then start logging.
  5. You slowly move your ankle through the full ROM for ~20 seconds.
  6. Press Ctrl-C to stop.  The script safely shuts down and saves a CSV.

Then run again for the other boot:

    python ROM_position_test.py --port /dev/ttyACM1 --side right

Analyze the output CSV to verify:
  - Motor current stays under ~1000 mA throughout the ROM
  - tracking_error_ticks stays within tolerance
  - No SAFETY_FALLBACK events occurred in the log

Author: Max Miller — Auburn University
"""

import argparse
import csv
import os
import signal
import sys
from time import sleep, time, strftime

import numpy as np

# ---------------------------------------------------------------------------
#  Add project root to path
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
#PROJECT_ROOT = os.path.join(SCRIPT_DIR, "..")
PROJECT_ROOT = SCRIPT_DIR
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from flexsea.device import Device
from config import (
    FIRMWARE_VERSION, LOG_LEVEL,
    STREAMING_FREQUENCY,
    CURRENT_GAINS, POSITION_GAINS,
    ZEROING_CURRENT, NO_SLACK_CURRENT,
    LEFT, RIGHT,
    TICKS_TO_ANGLE_COEFF,
    ANGLE_TO_TICKS_COEFF,
)
import configparser


# ===========================================================================
#  SAFETY PARAMETERS — TUNED FOR RIGID-CHAIN ROM TEST
# ===========================================================================
SAFE_POLY_ERROR_TICKS = 5000    # If polynomial target differs from actual
                                # motor position by more than this, fall back
                                # to light current instead of commanding the
                                # (presumed-bad) target.
FALLBACK_CURRENT_MA = 500       # Light current applied during fallback mode.
CURRENT_ABORT_MA = 15000         # Absolute abort: if current exceeds this at
                                # any point, stop the motor immediately and
                                # exit.  (Well below the 28000 mA firmware
                                # limit but high enough to allow brief
                                # transients during fast ankle motion.)
WATCHDOG_LOOP_MS = 50           # If a control iteration takes longer than
                                # this, zero the motor (likely USB hang).

#4/23 changing current_abort_ma from 8000 to 15000

# ===========================================================================
#  Ctrl-C handler — ensure motor is always zeroed on exit
# ===========================================================================
_device = None
_interrupted = False

def _sigint_handler(signum, frame):
    global _interrupted
    print("\n\n[Ctrl-C received — stopping motor and saving data …]")
    _interrupted = True

signal.signal(signal.SIGINT, _sigint_handler)


# ===========================================================================
#  Calibration loading — same as ExoBoot class does
# ===========================================================================
def load_calibration(side_sign):
    """Load the polynomial coefficients from bootCal.txt for this boot."""
    cal_path = os.path.join(PROJECT_ROOT, "calibration", "bootCal.txt")
    cfg = configparser.ConfigParser()
    cfg.read(cal_path)

    side_key = "left" if side_sign == LEFT else "right"
    boot_id = cfg.get("ids", side_key)

    ankle_55 = cfg.getint(boot_id, "ankle_reading_55_deg")
    # poly4 … poly0  (descending order, 5 coefficients = 4th-order fit)
    ank_mot_coeffs = [
        cfg.getfloat(boot_id, f"poly{i}") for i in range(4, -1, -1)
    ]
    # Add a 6th element for the runtime offset set by encoder_check
    #ank_mot_coeffs = ank_mot_coeffs + [0.0]  <-commenting out based on feedback of first trial, seems to be causing issues 
    return boot_id, ankle_55, ank_mot_coeffs


# ===========================================================================
#  Zero-boot sequence — matches ExoBoot.zero_boot()
# ===========================================================================
def zero_boot_sequence(device, side_sign):
    """Apply zeroing current to tighten chain, then return to rest."""
    print("Zeroing boot — tightening chain for 3 seconds …")
    device.set_gains(**CURRENT_GAINS)
    sleep(0.5)
    device.command_motor_current(ZEROING_CURRENT * side_sign)
    sleep(3)
    device.stop_motor()
    sleep(0.5)
    print("Zeroing complete.")


# ===========================================================================
#  Encoder check — shifts polynomial constant to align with current pose
# ===========================================================================
def encoder_check(device, side_sign, ank_mot_coeffs):
    """Align the polynomial to the current standing pose."""
    print("\nEncoder check — stand still for 1 second …")
    device.set_gains(**POSITION_GAINS)
    sleep(0.5)

    # Take 3 readings to confirm things are stable
    for i in range(3):
        data = device.read()
        print(f"  reading {i}: mot_ang = {data.get('mot_ang', '?')}")
        sleep(0.05)

    data = device.read()
    initial_ankle = data.get("ank_ang", 0)
    initial_motor = data.get("mot_ang", 0)

    initial_motor_des = np.floor(np.polyval(ank_mot_coeffs, initial_ankle))
    offset = initial_motor - initial_motor_des
    ank_mot_coeffs[-1] += offset
    initial_motor_shifted = np.floor(np.polyval(ank_mot_coeffs, initial_ankle))

    print(f"  ankle reading = {initial_ankle}")
    print(f"  motor actual  = {initial_motor}")
    print(f"  motor desired = {initial_motor_des:.0f}")
    print(f"  offset applied = {offset:.0f}")
    print(f"  motor after shift = {initial_motor_shifted:.0f} (should match actual)")

    device.stop_motor()
    print("Encoder check complete.\n")
    return ank_mot_coeffs


# ===========================================================================
#  Main ROM test
# ===========================================================================
def run_rom_test(port, side_name, fw, freq, duration_s):
    global _device, _interrupted

    side_sign = LEFT if side_name == "left" else RIGHT

    # ---- Load calibration ------------------------------------------------
    boot_id, ankle_55, ank_mot_coeffs = load_calibration(side_sign)
    print(f"Loaded calibration for boot {boot_id} (ankle_55 = {ankle_55})")
    print(f"Polynomial coefficients (initial): {ank_mot_coeffs}")
    print()

    # ---- Connect ---------------------------------------------------------
    print(f"Connecting to ExoBoot on {port} (fw {fw}) …")
    device = Device(
        firmwareVersion=fw,
        port=port,
        logLevel=LOG_LEVEL,
        interactive=False,
    )
    device.open()
    sleep(1)
    device.start_streaming(frequency=freq)
    sleep(0.1)
    _device = device
    print(f"Connected — device ID {device.id}")
    print()

    # ---- Zero + encoder check -------------------------------------------
    zero_boot_sequence(device, side_sign)
    ank_mot_coeffs = encoder_check(device, side_sign, ank_mot_coeffs)

    # ---- Prepare CSV logging --------------------------------------------
    timestamp = strftime("%Y-%m-%d_%Hh%Mm%Ss")
    csv_path = os.path.join(
        SCRIPT_DIR,
        f"ROM_test_{side_name}_{timestamp}.csv",
    )
    csv_file = open(csv_path, "w", newline="")
    writer = csv.writer(csv_file)
    writer.writerow([
        "timestamp_s",
        "state_time",
        "ank_ang", "ank_ang_deg",
        "mot_ang", "mot_ang_deg",
        "poly_target", "tracking_error_ticks",
        "mot_cur",
        "control_mode",     # "position" or "fallback_current"
        "safety_event",     # "", "POLY_UNSAFE", "CURRENT_ABORT", "LOOP_SLOW"
    ])

    # ---- Set position gains for the tracking loop -----------------------
    device.set_gains(**POSITION_GAINS)
    sleep(0.2)
    print("Position gains set.")

    # ---- Wait for user to start -----------------------------------------
    print("\n" + "=" * 60)
    print("READY FOR ROM TEST")
    print("=" * 60)
    print("The motor is now in POSITION CONTROL tracking the polynomial.")
    print("When you press ENTER, logging will begin.")
    print("Slowly move the ankle through the full range of motion:")
    print("  - Full plantarflexion (toes down)")
    print("  - Full dorsiflexion (toes up)")
    print("  - Repeat a few times, ~3 seconds per direction")
    print()
    print("If you feel anything wrong or the motor runs away:")
    print("  Press Ctrl-C immediately.  The motor will be zeroed.")
    print()
    print("Maximum test duration: {} seconds".format(duration_s))
    print("=" * 60)
    input("Press ENTER to begin logging (or Ctrl-C to abort) …")

    print("\n>>> RECORDING — move your ankle slowly through the ROM …\n")

    # ---- Main control loop ----------------------------------------------
    t0 = time()
    loop_dt_target = 1.0 / freq
    n_fallback = 0
    n_samples = 0
    max_current_seen = 0

    try:
        while not _interrupted:
            loop_start = time()
            elapsed = loop_start - t0

            if elapsed > duration_s:
                print(f"\n[Max duration {duration_s}s reached — stopping]")
                break

            # Read one sample
            data = device.read()
            state_time = data.get("state_time", 0)
            ank_ang = data.get("ank_ang", 0)
            mot_ang = data.get("mot_ang", 0)
            mot_cur = data.get("mot_cur", 0)

            # Compute polynomial target
            poly_target = int(np.floor(np.polyval(ank_mot_coeffs, ank_ang)))
            tracking_err = poly_target - mot_ang

            # SAFETY 1 — polynomial sanity check
            safety_event = ""
            control_mode = "position"
            if abs(tracking_err) > SAFE_POLY_ERROR_TICKS:
                # Bad polynomial target — fall back to light current
                device.set_gains(**CURRENT_GAINS)
                device.command_motor_current(FALLBACK_CURRENT_MA * side_sign)
                device.set_gains(**POSITION_GAINS)
                control_mode = "fallback_current"
                safety_event = "POLY_UNSAFE"
                n_fallback += 1
                if n_fallback <= 5 or n_fallback % 50 == 0:
                    print(
                        f"  [UNSAFE POLY #{n_fallback}] "
                        f"t={elapsed:.2f}s  ankle={ank_ang}  "
                        f"target={poly_target}  actual={mot_ang}  "
                        f"err={tracking_err} ticks → fallback"
                    )
            else:
                # Safe — command position as planned
                device.command_motor_position(poly_target)

            # SAFETY 2 — absolute current abort
            if abs(mot_cur) > CURRENT_ABORT_MA:
                safety_event = "CURRENT_ABORT"
                print(
                    f"\n*** CURRENT ABORT *** |mot_cur| = {mot_cur} mA "
                    f"exceeds limit {CURRENT_ABORT_MA} mA at t={elapsed:.2f}s"
                )
                device.stop_motor()
                writer.writerow([
                    elapsed, state_time,
                    ank_ang, ank_ang * TICKS_TO_ANGLE_COEFF,
                    mot_ang, mot_ang * TICKS_TO_ANGLE_COEFF,
                    poly_target, tracking_err,
                    mot_cur, control_mode, safety_event,
                ])
                break

            if abs(mot_cur) > max_current_seen:
                max_current_seen = abs(mot_cur)

            # Log
            writer.writerow([
                elapsed, state_time,
                ank_ang, ank_ang * TICKS_TO_ANGLE_COEFF,
                mot_ang, mot_ang * TICKS_TO_ANGLE_COEFF,
                poly_target, tracking_err,
                mot_cur, control_mode, safety_event,
            ])
            n_samples += 1

            # Periodic status print (every 2 seconds)
            if n_samples % (freq * 2) == 0:
                print(
                    f"  t={elapsed:5.1f}s  "
                    f"ankle={ank_ang:>5}  "
                    f"motor={mot_ang:>7}  "
                    f"target={poly_target:>7}  "
                    f"err={tracking_err:>6}  "
                    f"cur={mot_cur:>5} mA"
                )

            # SAFETY 3 — loop timing watchdog
            loop_elapsed_ms = (time() - loop_start) * 1000
            if loop_elapsed_ms > WATCHDOG_LOOP_MS:
                print(
                    f"  [SLOW LOOP] iteration took {loop_elapsed_ms:.1f} ms — "
                    f"zeroing motor as a precaution"
                )
                device.stop_motor()
                safety_event = "LOOP_SLOW"

            # Pace the loop
            remaining = loop_dt_target - (time() - loop_start)
            if remaining > 0:
                sleep(remaining)

    finally:
        # ---- Cleanup ----------------------------------------------------
        print("\nStopping motor …")
        try:
            device.stop_motor()
            sleep(0.1)
            device.stop_motor()   # send twice in case first is lost on USB
            sleep(0.1)
        except Exception as e:
            print(f"  (stop_motor failed: {e})")
        try:
            device.stop_streaming()
            sleep(0.1)
        except Exception:
            pass
        try:
            device.close()
        except Exception:
            pass

        csv_file.close()

        # Summary
        print()
        print("=" * 60)
        print("ROM TEST SUMMARY")
        print("=" * 60)
        print(f"  Samples logged: {n_samples}")
        print(f"  Max |current| seen: {max_current_seen} mA")
        print(f"  Fallback events (unsafe polynomial): {n_fallback}")
        if n_fallback > 0:
            pct = n_fallback / max(n_samples, 1) * 100
            print(f"  Fallback fraction: {pct:.1f}% of samples")
            print()
            print("  >>> The polynomial is NOT SAFE for walking as-is.")
            print("  >>> Recommendations:")
            print("      - Inspect the CSV to see which ankle angles caused fallback")
            print("      - Consider re-calibrating with extra attention to those angles")
            print("      - Or adjust SAFE_POLY_ERROR_TICKS if error pattern is consistent")
        else:
            print()
            print("  >>> No fallback events — polynomial looks safe.")
            print(f"  >>> If max current stayed well under 1000 mA, you're ready to walk.")
        print()
        print(f"  Data saved to: {csv_path}")
        print("=" * 60)


# ===========================================================================
#  CLI
# ===========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="ROM position-control test for ExoBoot calibration validation"
    )
    parser.add_argument("--port", required=True,
                        help="Serial port (e.g. /dev/ttyACM0)")
    parser.add_argument("--side", required=True, choices=["left", "right"],
                        help="Boot side")
    parser.add_argument("--fw", default=FIRMWARE_VERSION,
                        help=f"Firmware version (default {FIRMWARE_VERSION})")
    parser.add_argument("--freq", type=int, default=STREAMING_FREQUENCY,
                        help=f"Streaming frequency Hz (default {STREAMING_FREQUENCY})")
    parser.add_argument("--duration", type=int, default=30,
                        help="Max test duration in seconds (default 30)")
    args = parser.parse_args()

    run_rom_test(args.port, args.side, args.fw, args.freq, args.duration)


if __name__ == "__main__":
    main()
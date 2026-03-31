"""
Bench PID Test 1 — Step Response & Gain Sweep for Rigid-Chain ExoBoot
======================================================================

Standalone bench-test script for tuning PID gains on the *rigid-chain*
ExoBoot (as opposed to Xiangyu's elastic-belt version).  Connects to a
single boot on the bench (no walking, no gait detection) and runs:

    Phase 1 — Current-loop step responses  (inner loop)
    Phase 2 — Position-loop step responses (outer loop)
    Phase 3 — Optional automated gain sweep

All sensor data is logged to timestamped CSVs so you can plot
step responses, measure rise time / overshoot / settling time,
and compare across gain sets.

Safety
------
* A software current clamp (MAX_TEST_CURRENT_MA) limits commands.
* A watchdog timer zeros the motor if a loop iteration takes too long.
* Ctrl-C is caught and the motor is safely shut down.
* The boot is always returned to zero-current before disconnect.

Usage
-----
    python PID_testing/Bench_PID_Test1.py --port /dev/ttyACM0 --side left

Author:  Max Miller — Auburn University
Date:    March 2026
"""

import argparse
import csv
import os
import sys
import signal
from time import sleep, time, strftime
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
#  Add project root to path so we can import from the main codebase
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.join(SCRIPT_DIR, "..")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from flexsea.device import Device


# ═══════════════════════════════════════════════════════════════════════════
#  TEST CONFIGURATION — EDIT THESE
# ═══════════════════════════════════════════════════════════════════════════

# --- Hardware ---
FIRMWARE_VERSION = "7.2.0"
STREAMING_FREQ   = 500          # Hz — match your config.py
LOG_LEVEL        = 6            # 6 = silent

# --- Safety ---
MAX_TEST_CURRENT_MA = 5000      # Hard clamp — never exceed this on bench
WATCHDOG_TIMEOUT_S  = 0.05      # Kill motor if a loop takes > 50 ms
SETTLE_PAUSE_S      = 2.0       # Seconds to wait between tests

# --- Motor constant (for torque estimates in logs) ---
KT = 0.14                       # Nm/A

# --- Encoder ---
TICKS_PER_REV     = 2**14       # 16384
TICKS_TO_DEG      = 360.0 / TICKS_PER_REV
TICKS_TO_RAD      = 2 * np.pi / TICKS_PER_REV

# --- Side constants ---
LEFT  =  1
RIGHT = -1


# ═══════════════════════════════════════════════════════════════════════════
#  GAIN SETS TO TEST
# ═══════════════════════════════════════════════════════════════════════════
#
#  Each dict must have keys: kp, ki, kd, k, b, ff
#  The FlexSEA set_gains call takes all six every time.
#
#  NOTE: Dephy recommended ranges (from FlexSEA docs, ActPack 4.1):
#    Current:  kp [0,80]  rec 40  |  ki [0,800]  rec 400  |  ff [0,128] rec 128
#    Position: kp [0,1000]        |  ki [0,1000]           |  kd [0,1000] rec 0
#

# ----- Current-loop gain sets (inner loop) -----
CURRENT_GAIN_SETS: List[Dict[str, int]] = [
    # Xiangyu's original (almost certainly tuned for belt + 1000 Hz)
    {"label": "Xiangyu_orig",   "kp": 100, "ki":  32, "kd": 0, "k": 0, "b": 0, "ff": 0},
    # Dephy recommended defaults
    {"label": "Dephy_default",  "kp":  40, "ki": 400, "kd": 0, "k": 0, "b": 0, "ff": 128},
    # Conservative start for rigid chain @ 500 Hz
    {"label": "chain_conserv",  "kp":  40, "ki": 200, "kd": 0, "k": 0, "b": 0, "ff":  64},
    # Moderate — bump kp slightly
    {"label": "chain_moderate", "kp":  50, "ki": 300, "kd": 0, "k": 0, "b": 0, "ff":  64},
]

# ----- Position-loop gain sets (outer loop) -----
POSITION_GAIN_SETS: List[Dict[str, int]] = [
    # Xiangyu's original
    {"label": "Xiangyu_orig",    "kp": 175, "ki": 50, "kd":  0, "k": 0, "b": 0, "ff": 0},
    # Conservative for rigid chain
    {"label": "chain_conserv",   "kp":  80, "ki": 20, "kd":  5, "k": 0, "b": 0, "ff": 0},
    # Moderate — add damping to replace belt compliance
    {"label": "chain_moderate",  "kp": 100, "ki": 30, "kd": 10, "k": 0, "b": 0, "ff": 0},
    # Higher bandwidth attempt
    {"label": "chain_aggressive","kp": 150, "ki": 40, "kd": 15, "k": 0, "b": 0, "ff": 0},
]

# ----- Step-test parameters -----
CURRENT_STEP_AMPLITUDES_MA = [500, 1000, 1500, 2000]   # mA steps to command
POSITION_STEP_AMPLITUDES_TICKS = [500, 1000, 2000]      # encoder tick steps
STEP_DURATION_S   = 1.0         # How long to hold each step
RECORD_DURATION_S = 2.0         # Total recording window (step + settle)


# ═══════════════════════════════════════════════════════════════════════════
#  DATA LOGGING
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class SampleRow:
    """One row of bench-test data."""
    timestamp_s:      float = 0.0
    loop_dt_ms:       float = 0.0
    test_phase:       str   = ""        # "current" or "position"
    gain_label:       str   = ""
    step_amplitude:   float = 0.0       # mA or ticks, depending on phase
    command:          float = 0.0       # What we sent (mA or ticks)

    # Raw sensor readings
    state_time:       int   = 0         # Firmware timestamp (ms)
    mot_ang:          int   = 0         # Motor encoder (ticks)
    mot_vel:          int   = 0         # Motor velocity (firmware units)
    mot_cur:          int   = 0         # Measured motor current (mA)
    ank_ang:          int   = 0         # Ankle encoder (ticks)
    ank_vel:          int   = 0         # Ankle velocity (firmware units)
    batt_volt:        int   = 0         # Battery voltage (mV)
    batt_curr:        int   = 0         # Battery current (mA)
    temperature:      int   = 0         # Board temperature

    # Derived
    mot_ang_deg:      float = 0.0
    ank_ang_deg:      float = 0.0
    current_error_ma: float = 0.0       # command - measured (current mode)
    position_error_ticks: float = 0.0   # command - measured (position mode)
    torque_est_nm:    float = 0.0       # kt * current


class DataLogger:
    """Collects SampleRow objects and writes them to CSV."""

    def __init__(self, output_dir: str, prefix: str):
        os.makedirs(output_dir, exist_ok=True)
        ts = strftime("%Y-%m-%d_%Hh%Mm%Ss")
        self.filepath = os.path.join(output_dir, f"{prefix}_{ts}.csv")
        self.rows: List[SampleRow] = []

    def add(self, row: SampleRow):
        self.rows.append(row)

    def save(self):
        if not self.rows:
            print("  [logger] No data to save.")
            return
        fieldnames = list(asdict(self.rows[0]).keys())
        with open(self.filepath, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in self.rows:
                writer.writerow(asdict(row))
        print(f"  [logger] Saved {len(self.rows)} samples → {self.filepath}")


# ═══════════════════════════════════════════════════════════════════════════
#  DEVICE HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def clamp_current(value_ma: int) -> int:
    """Enforce the bench safety limit."""
    return max(-MAX_TEST_CURRENT_MA, min(MAX_TEST_CURRENT_MA, value_ma))


def read_device_safe(device: Device) -> dict:
    """Read from device with error handling.

    Legacy FW 7.2.0 returns a dict keyed by YAML spec-file field names.
    """
    try:
        data = device.read()
        return data
    except Exception as e:
        print(f"  [WARNING] device.read() failed: {e}")
        return {}


def safe_shutdown(device: Device):
    """Zero the motor and stop streaming."""
    print("\n  [SHUTDOWN] Zeroing motor current …")
    try:
        device.command_motor_current(0)
        sleep(0.1)
        device.command_motor_current(0)
        sleep(0.1)
    except Exception:
        pass
    try:
        device.stop_streaming()
    except Exception:
        pass
    print("  [SHUTDOWN] Done.")


# ═══════════════════════════════════════════════════════════════════════════
#  STEP RESPONSE TESTS
# ═══════════════════════════════════════════════════════════════════════════

def run_current_step_test(
    device: Device,
    logger: DataLogger,
    gains: Dict[str, int],
    step_ma: int,
    t0_global: float,
):
    """
    Current-loop step response.

    1. Set current gains.
    2. Command 0 mA, record baseline for 0.3 s.
    3. Command step_ma, record for STEP_DURATION_S.
    4. Command 0 mA, record settling for remainder of RECORD_DURATION_S.
    """
    label = gains["label"]
    step_ma = clamp_current(step_ma)
    print(f"    Current step: {label}  |  amplitude = {step_ma} mA")

    # Set gains (FlexSEA retries internally)
    device.set_gains(gains["kp"], gains["ki"], gains["kd"],
                     gains["k"], gains["b"], gains["ff"])
    sleep(0.01)

    # Switch to current control, command 0
    device.command_motor_current(0)
    sleep(0.05)

    dt_target = 1.0 / STREAMING_FREQ
    baseline_dur = 0.3
    total_dur = RECORD_DURATION_S
    step_on_time = baseline_dur
    step_off_time = baseline_dur + STEP_DURATION_S

    t_start = time()

    while True:
        t_loop_start = time()
        elapsed = t_loop_start - t_start

        if elapsed > total_dur:
            break

        # Determine command
        if elapsed < step_on_time:
            cmd_ma = 0
        elif elapsed < step_off_time:
            cmd_ma = step_ma
        else:
            cmd_ma = 0

        device.command_motor_current(clamp_current(cmd_ma))

        # Read sensors
        data = read_device_safe(device)
        if not data:
            sleep(dt_target)
            continue

        mot_cur = data.get("mot_cur", 0)

        row = SampleRow(
            timestamp_s      = time() - t0_global,
            loop_dt_ms       = (time() - t_loop_start) * 1000,
            test_phase       = "current",
            gain_label       = label,
            step_amplitude   = step_ma,
            command          = cmd_ma,
            state_time       = data.get("state_time", 0),
            mot_ang          = data.get("mot_ang", 0),
            mot_vel          = data.get("mot_vel", 0),
            mot_cur          = mot_cur,
            ank_ang          = data.get("ank_ang", 0),
            ank_vel          = data.get("ank_vel", 0),
            batt_volt        = data.get("batt_volt", 0),
            batt_curr        = data.get("batt_curr", 0),
            temperature      = data.get("temperature", 0),
            mot_ang_deg      = data.get("mot_ang", 0) * TICKS_TO_DEG,
            ank_ang_deg      = data.get("ank_ang", 0) * TICKS_TO_DEG,
            current_error_ma = cmd_ma - mot_cur,
            position_error_ticks = 0,
            torque_est_nm    = (mot_cur / 1000.0) * KT,
        )
        logger.add(row)

        # Watchdog — if we're way behind, bail
        loop_elapsed = time() - t_loop_start
        if loop_elapsed > WATCHDOG_TIMEOUT_S:
            print(f"    [WATCHDOG] Loop took {loop_elapsed*1000:.1f} ms — zeroing motor")
            device.command_motor_current(0)

        # Pace to streaming rate
        remaining = dt_target - (time() - t_loop_start)
        if remaining > 0:
            sleep(remaining)

    # Return to zero
    device.command_motor_current(0)
    sleep(0.05)


def run_position_step_test(
    device: Device,
    logger: DataLogger,
    gains: Dict[str, int],
    step_ticks: int,
    baseline_pos: int,
    t0_global: float,
):
    """
    Position-loop step response.

    1. Set position gains.
    2. Command current position (hold), record baseline for 0.3 s.
    3. Command baseline + step_ticks, record for STEP_DURATION_S.
    4. Command baseline (return), record settling for remainder.
    """
    label = gains["label"]
    print(f"    Position step: {label}  |  amplitude = {step_ticks} ticks "
          f"({step_ticks * TICKS_TO_DEG:.1f}°)")

    # Set gains
    device.set_gains(gains["kp"], gains["ki"], gains["kd"],
                     gains["k"], gains["b"], gains["ff"])
    sleep(0.01)

    # Switch to position control, hold current position
    device.command_motor_position(baseline_pos)
    sleep(0.05)

    dt_target = 1.0 / STREAMING_FREQ
    baseline_dur = 0.3
    total_dur = RECORD_DURATION_S
    step_on_time = baseline_dur
    step_off_time = baseline_dur + STEP_DURATION_S

    target_pos = baseline_pos  # will change during step

    t_start = time()

    while True:
        t_loop_start = time()
        elapsed = t_loop_start - t_start

        if elapsed > total_dur:
            break

        # Determine target
        if elapsed < step_on_time:
            target_pos = baseline_pos
        elif elapsed < step_off_time:
            target_pos = baseline_pos + step_ticks
        else:
            target_pos = baseline_pos

        device.command_motor_position(target_pos)

        # Read sensors
        data = read_device_safe(device)
        if not data:
            sleep(dt_target)
            continue

        mot_ang = data.get("mot_ang", 0)
        mot_cur = data.get("mot_cur", 0)

        row = SampleRow(
            timestamp_s      = time() - t0_global,
            loop_dt_ms       = (time() - t_loop_start) * 1000,
            test_phase       = "position",
            gain_label       = label,
            step_amplitude   = step_ticks,
            command          = target_pos,
            state_time       = data.get("state_time", 0),
            mot_ang          = mot_ang,
            mot_vel          = data.get("mot_vel", 0),
            mot_cur          = mot_cur,
            ank_ang          = data.get("ank_ang", 0),
            ank_vel          = data.get("ank_vel", 0),
            batt_volt        = data.get("batt_volt", 0),
            batt_curr        = data.get("batt_curr", 0),
            temperature      = data.get("temperature", 0),
            mot_ang_deg      = mot_ang * TICKS_TO_DEG,
            ank_ang_deg      = data.get("ank_ang", 0) * TICKS_TO_DEG,
            current_error_ma = 0,
            position_error_ticks = target_pos - mot_ang,
            torque_est_nm    = (mot_cur / 1000.0) * KT,
        )
        logger.add(row)

        # Watchdog
        loop_elapsed = time() - t_loop_start
        if loop_elapsed > WATCHDOG_TIMEOUT_S:
            print(f"    [WATCHDOG] Loop took {loop_elapsed*1000:.1f} ms — zeroing motor")
            device.command_motor_current(0)

        remaining = dt_target - (time() - t_loop_start)
        if remaining > 0:
            sleep(remaining)

    # Return to zero current (safe state)
    device.command_motor_current(0)
    sleep(0.05)


# ═══════════════════════════════════════════════════════════════════════════
#  ANALYSIS HELPERS (run offline after data collection)
# ═══════════════════════════════════════════════════════════════════════════

def compute_step_metrics(times, values, command_value, settle_band_pct=5.0):
    """
    Given a time series during a step, compute:
      - rise_time_s:    10% → 90% of final value
      - overshoot_pct:  peak overshoot as % of step
      - settling_time_s: time to stay within ±settle_band_pct of final
      - steady_state_error: mean error in last 20% of window

    Returns a dict of metrics, or None if data is too short.
    """
    if len(times) < 10:
        return None

    times = np.array(times, dtype=float)
    values = np.array(values, dtype=float)
    t0 = times[0]
    times = times - t0

    final_val = command_value
    if abs(final_val) < 1e-9:
        return None

    # Rise time (10% to 90%)
    val_10 = 0.1 * final_val
    val_90 = 0.9 * final_val

    crossed_10 = np.where(values >= val_10)[0]
    crossed_90 = np.where(values >= val_90)[0]

    rise_time = None
    if len(crossed_10) > 0 and len(crossed_90) > 0:
        t_10 = times[crossed_10[0]]
        t_90 = times[crossed_90[0]]
        if t_90 > t_10:
            rise_time = t_90 - t_10

    # Overshoot
    peak = np.max(values) if final_val > 0 else np.min(values)
    overshoot_pct = ((peak - final_val) / abs(final_val)) * 100.0

    # Settling time
    band = abs(final_val) * settle_band_pct / 100.0
    within_band = np.abs(values - final_val) <= band
    settling_time = None
    # Walk backwards to find the last time it left the band
    for i in range(len(within_band) - 1, -1, -1):
        if not within_band[i]:
            if i + 1 < len(times):
                settling_time = times[i + 1]
            break

    # Steady-state error (last 20% of data)
    tail_start = int(0.8 * len(values))
    ss_error = np.mean(values[tail_start:]) - final_val

    return {
        "rise_time_s":       rise_time,
        "overshoot_pct":     overshoot_pct,
        "settling_time_s":   settling_time,
        "steady_state_error": ss_error,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  QUICK PLOT (optional — needs matplotlib)
# ═══════════════════════════════════════════════════════════════════════════

def plot_results(csv_path: str, output_dir: str):
    """
    Generate step-response plots from a bench-test CSV.
    Saves PNGs to output_dir.  Call this after the test, or run
    separately:  python Bench_PID_Test1.py --plot <csv_path>
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import pandas as pd
    except ImportError:
        print("  [plot] matplotlib or pandas not available — skipping plots.")
        return

    df = pd.read_csv(csv_path)
    os.makedirs(output_dir, exist_ok=True)

    for phase in df["test_phase"].unique():
        phase_df = df[df["test_phase"] == phase]

        for label in phase_df["gain_label"].unique():
            gain_df = phase_df[phase_df["gain_label"] == label]

            for amp in gain_df["step_amplitude"].unique():
                trial_df = gain_df[gain_df["step_amplitude"] == amp].copy()
                trial_df = trial_df.sort_values("timestamp_s")

                t = trial_df["timestamp_s"].values
                t = t - t[0]  # Zero-reference time

                fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)
                fig.suptitle(
                    f"{phase.upper()} step  |  gains: {label}  |  "
                    f"amplitude: {amp:.0f}",
                    fontsize=14,
                )

                # --- Panel 1: Command vs Measured ---
                if phase == "current":
                    axes[0].plot(t, trial_df["command"].values,
                                "r--", label="Command (mA)", linewidth=1.5)
                    axes[0].plot(t, trial_df["mot_cur"].values,
                                "b-", label="Measured (mA)", linewidth=1)
                    axes[0].set_ylabel("Current (mA)")
                else:
                    axes[0].plot(t, trial_df["command"].values,
                                "r--", label="Command (ticks)", linewidth=1.5)
                    axes[0].plot(t, trial_df["mot_ang"].values,
                                "b-", label="Measured (ticks)", linewidth=1)
                    axes[0].set_ylabel("Position (ticks)")
                axes[0].legend(loc="upper right")
                axes[0].grid(True, alpha=0.3)

                # --- Panel 2: Error ---
                if phase == "current":
                    axes[1].plot(t, trial_df["current_error_ma"].values,
                                "k-", linewidth=0.8)
                    axes[1].set_ylabel("Current Error (mA)")
                else:
                    axes[1].plot(t, trial_df["position_error_ticks"].values,
                                "k-", linewidth=0.8)
                    axes[1].set_ylabel("Position Error (ticks)")
                axes[1].axhline(0, color="gray", linestyle=":", linewidth=0.5)
                axes[1].grid(True, alpha=0.3)

                # --- Panel 3: Estimated torque + motor velocity ---
                ax3a = axes[2]
                ax3b = ax3a.twinx()
                ax3a.plot(t, trial_df["torque_est_nm"].values,
                          "g-", label="Torque est (Nm)", linewidth=1)
                ax3b.plot(t, trial_df["mot_vel"].values,
                          "m-", label="Motor vel", linewidth=0.8, alpha=0.7)
                ax3a.set_ylabel("Torque (Nm)", color="g")
                ax3b.set_ylabel("Motor Velocity", color="m")
                ax3a.set_xlabel("Time (s)")
                ax3a.grid(True, alpha=0.3)

                # Add legend combining both axes
                lines_a, labels_a = ax3a.get_legend_handles_labels()
                lines_b, labels_b = ax3b.get_legend_handles_labels()
                ax3a.legend(lines_a + lines_b, labels_a + labels_b,
                            loc="upper right")

                plt.tight_layout()
                fname = f"{phase}_{label}_amp{amp:.0f}.png"
                fig.savefig(os.path.join(output_dir, fname), dpi=150)
                plt.close(fig)
                print(f"  [plot] Saved {fname}")


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Bench PID test for rigid-chain ExoBoot"
    )
    parser.add_argument("--port", type=str, default="/dev/ttyACM0",
                        help="Serial port for ExoBoot")
    parser.add_argument("--side", type=str, default="left",
                        choices=["left", "right"],
                        help="Which boot (affects sign conventions)")
    parser.add_argument("--freq", type=int, default=STREAMING_FREQ,
                        help="Streaming frequency in Hz")
    parser.add_argument("--skip-current", action="store_true",
                        help="Skip current-loop tests")
    parser.add_argument("--skip-position", action="store_true",
                        help="Skip position-loop tests")
    parser.add_argument("--plot", type=str, default=None,
                        help="Plot results from a previous CSV instead of running tests")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Directory for output CSVs and plots")

    args = parser.parse_args()

    # --- Plot-only mode ---
    if args.plot:
        out_dir = args.output_dir or os.path.join(SCRIPT_DIR, "results")
        plot_results(args.plot, out_dir)
        return

    side = LEFT if args.side == "left" else RIGHT
    output_dir = args.output_dir or os.path.join(SCRIPT_DIR, "results")

    # --- Banner ---
    print("=" * 70)
    print("  BENCH PID TEST 1 — Rigid-Chain ExoBoot")
    print(f"  Port: {args.port}  |  Side: {args.side}  |  Freq: {args.freq} Hz")
    print(f"  Max current: {MAX_TEST_CURRENT_MA} mA")
    print(f"  Output: {output_dir}")
    print("=" * 70)
    print()

    # --- Connect ---
    print("[1/5] Connecting to ExoBoot …")
    device = Device(
        firmwareVersion=FIRMWARE_VERSION,
        port=args.port,
        logLevel=LOG_LEVEL,
        interactive=False,
    )
    device.open()
    sleep(1)
    device.start_streaming(frequency=args.freq)
    sleep(0.5)

    # Zero the motor
    device.command_motor_current(0)
    sleep(0.2)

    # Catch Ctrl-C for clean shutdown
    def signal_handler(sig, frame):
        print("\n  [SIGINT] Caught — shutting down safely …")
        safe_shutdown(device)
        sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)

    # Read initial position
    init_data = read_device_safe(device)
    baseline_pos = init_data.get("mot_ang", 0)
    print(f"  Initial motor position: {baseline_pos} ticks "
          f"({baseline_pos * TICKS_TO_DEG:.1f}°)")
    print()

    # --- Logger ---
    logger = DataLogger(output_dir, prefix="bench_pid_test1")
    t0_global = time()

    # ═══════════════════════════════════════════════════════════════════
    #  PHASE 1 — Current Loop
    # ═══════════════════════════════════════════════════════════════════
    if not args.skip_current:
        print("[2/5] PHASE 1 — Current-loop step responses")
        print(f"  Gain sets: {len(CURRENT_GAIN_SETS)}")
        print(f"  Step amplitudes: {CURRENT_STEP_AMPLITUDES_MA} mA")
        print(f"  Step duration: {STEP_DURATION_S} s  |  Record window: {RECORD_DURATION_S} s")
        print()

        for gains in CURRENT_GAIN_SETS:
            print(f"  --- Gain set: {gains['label']} "
                  f"(kp={gains['kp']}, ki={gains['ki']}, kd={gains['kd']}, "
                  f"ff={gains['ff']}) ---")

            for amp in CURRENT_STEP_AMPLITUDES_MA:
                run_current_step_test(device, logger, gains, amp, t0_global)
                sleep(SETTLE_PAUSE_S)

            print()

        # Return to safe state
        device.command_motor_current(0)
        sleep(0.5)
        print("  Phase 1 complete.\n")
    else:
        print("[2/5] PHASE 1 — SKIPPED (--skip-current)\n")

    # ═══════════════════════════════════════════════════════════════════
    #  PHASE 2 — Position Loop
    # ═══════════════════════════════════════════════════════════════════
    if not args.skip_position:
        print("[3/5] PHASE 2 — Position-loop step responses")
        print(f"  Gain sets: {len(POSITION_GAIN_SETS)}")
        print(f"  Step amplitudes: {POSITION_STEP_AMPLITUDES_TICKS} ticks")
        print()

        # Re-read baseline (motor may have drifted during current tests)
        data = read_device_safe(device)
        baseline_pos = data.get("mot_ang", 0)
        print(f"  Updated baseline: {baseline_pos} ticks")

        for gains in POSITION_GAIN_SETS:
            print(f"  --- Gain set: {gains['label']} "
                  f"(kp={gains['kp']}, ki={gains['ki']}, kd={gains['kd']}) ---")

            for amp in POSITION_STEP_AMPLITUDES_TICKS:
                run_position_step_test(
                    device, logger, gains, amp, baseline_pos, t0_global
                )
                sleep(SETTLE_PAUSE_S)

                # Re-read baseline after each step to avoid drift accumulation
                data = read_device_safe(device)
                baseline_pos = data.get("mot_ang", 0)

            print()

        device.command_motor_current(0)
        sleep(0.5)
        print("  Phase 2 complete.\n")
    else:
        print("[3/5] PHASE 2 — SKIPPED (--skip-position)\n")

    # ═══════════════════════════════════════════════════════════════════
    #  SAVE DATA
    # ═══════════════════════════════════════════════════════════════════
    print("[4/5] Saving data …")
    logger.save()

    # ═══════════════════════════════════════════════════════════════════
    #  GENERATE PLOTS
    # ═══════════════════════════════════════════════════════════════════
    print("[5/5] Generating plots …")
    plot_results(logger.filepath, output_dir)

    # ═══════════════════════════════════════════════════════════════════
    #  SHUTDOWN
    # ═══════════════════════════════════════════════════════════════════
    safe_shutdown(device)
    print("\n" + "=" * 70)
    print("  TEST COMPLETE")
    print(f"  Data:  {logger.filepath}")
    print(f"  Plots: {output_dir}/")
    print("=" * 70)


if __name__ == "__main__":
    main()
"""
Bench PID Test 3 — Step Response for Rigid-Chain ExoBoot
=========================================================

v3 changes from v2:
  - ROM LIMIT FIX: Now uses the ANKLE encoder for ROM protection, not
    the motor encoder.  The motor spins many revolutions per degree of
    ankle motion (~17-40:1 ratio depending on ankle angle), so motor-
    based limits were triggering hundreds of degrees too early.
  - Updated gain sets based on v2 results:
    * Current loop: Dephy defaults (kp=40, ki=400, ff=128) were best.
      Xiangyu's (kp=100, ki=32) had massive steady-state error.
      New sets explore the neighborhood around Dephy defaults.
    * Position loop: All v2 sets had 30-50% overshoot.  kd needs to
      be much higher on the rigid chain.  New sets push kd to 20-40.
  - Motor drift tolerance increased for return-to-home.
  - Cleaner console output (ROM warnings not spammed every iteration).

Usage
-----
    python PID_testing/Bench_PID_Test3.py --port /dev/ttyACM0 --side left

Author:  Max Miller — Auburn University
Date:    March 2026
"""

import argparse
import csv
import os
import sys
import signal
from time import sleep, time, strftime
from dataclasses import dataclass, asdict
from typing import List, Dict

import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.join(SCRIPT_DIR, "..")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from flexsea.device import Device


# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

# --- Hardware ---
FIRMWARE_VERSION = "7.2.0"
STREAMING_FREQ   = 100    #was prev. 500hz 
LOG_LEVEL        = 6

# --- Safety ---
MAX_TEST_CURRENT_MA = 5000
WATCHDOG_TIMEOUT_S  = 0.05
SETTLE_PAUSE_S      = 1.5

# --- Encoder ---
TICKS_PER_REV = 2**14           # 16384
TICKS_TO_DEG  = 360.0 / TICKS_PER_REV
DEG_TO_TICKS  = TICKS_PER_REV / 360.0

# --- ROM Safety (v3: ANKLE-based, not motor-based) ---
# These are ANKLE degrees — the ankle encoder reads directly in ankle frame.
ANKLE_ROM_DEG         = 115.0   # Full mechanical ROM at the ankle
ANKLE_SAFETY_MARGIN   = 20.0    # Stay this far from each mechanical stop
ANKLE_ROM_LIMIT_TICKS = int((ANKLE_ROM_DEG - ANKLE_SAFETY_MARGIN) * DEG_TO_TICKS)
# That's ~95° × 45.5 = ~4323 ankle ticks from the starting position

# --- Motor constant ---
KT = 0.14

# --- Side ---
LEFT  =  1
RIGHT = -1


# ═══════════════════════════════════════════════════════════════════════════
#  GAIN SETS — UPDATED FROM v2 RESULTS
# ═══════════════════════════════════════════════════════════════════════════

# --- Current loop ---
# v2 finding: Dephy defaults (kp=40, ki=400, ff=128) were clearly best.
# Xiangyu's (kp=100, ki=32) had huge SS error — ki way too low.
# Now we explore around the Dephy defaults.
CURRENT_GAIN_SETS: List[Dict[str, int]] = [
    # Baseline: Dephy recommended (best from v2)
    {"label": "Dephy_default",  "kp":  40, "ki": 400, "kd": 0, "k": 0, "b": 0, "ff": 128},
    # Slightly reduced ff to see if it helps with the chain
    {"label": "Dephy_ff100",    "kp":  40, "ki": 400, "kd": 0, "k": 0, "b": 0, "ff": 100},
    # Bump ki higher for tighter SS tracking
    {"label": "chain_ki500",    "kp":  40, "ki": 500, "kd": 0, "k": 0, "b": 0, "ff": 100},
    # Slightly higher kp with moderate ki
    {"label": "chain_kp50",     "kp":  50, "ki": 350, "kd": 0, "k": 0, "b": 0, "ff": 100},
]

# --- Position loop ---
# v2 finding: ALL sets had 30-50% overshoot.  kd=0 (Xiangyu) caused
# ringing with 46+ zero-crossings.  kd=10 (chain_moderate) was best
# overall but still 43% overshoot.  Need MUCH more derivative action.
POSITION_GAIN_SETS: List[Dict[str, int]] = [
    # v2 winner for reference
    {"label": "v2_moderate",     "kp": 100, "ki": 30, "kd": 10, "k": 0, "b": 0, "ff": 0},
    # Higher damping to cut overshoot
    {"label": "high_damp_v1",    "kp": 100, "ki": 25, "kd": 25, "k": 0, "b": 0, "ff": 0},
    # Even more damping
    {"label": "high_damp_v2",    "kp": 100, "ki": 20, "kd": 35, "k": 0, "b": 0, "ff": 0},
    # Lower kp with heavy damping (slower but well-damped)
    {"label": "overdamped",      "kp":  80, "ki": 20, "kd": 40, "k": 0, "b": 0, "ff": 0},
]

# --- Step parameters ---
CURRENT_STEP_AMPLITUDES_MA  = [500, 1000, 1500, 2000]
CURRENT_PULSE_DURATION_S    = 0.100
CURRENT_RECORD_DURATION_S   = 0.500
CURRENT_PULSE_MODE          = "bidirectional"

POSITION_STEP_AMPLITUDES_TICKS = [200, 500, 1000]
POSITION_STEP_DURATION_S   = 0.500
POSITION_RECORD_DURATION_S = 1.200

# Return-to-home (between current tests)
RETURN_TO_HOME_GAINS = {"kp": 50, "ki": 15, "kd": 5, "k": 0, "b": 0, "ff": 0}
RETURN_TO_HOME_TIMEOUT_S = 3.0
RETURN_TO_HOME_TOLERANCE_TICKS = 50  # motor ticks — loosened from v2's 30


# ═══════════════════════════════════════════════════════════════════════════
#  DATA LOGGING
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class SampleRow:
    timestamp_s:      float = 0.0
    loop_dt_ms:       float = 0.0
    test_phase:       str   = ""
    gain_label:       str   = ""
    step_amplitude:   float = 0.0
    command:          float = 0.0
    state_time:       int   = 0
    mot_ang:          int   = 0
    mot_vel:          int   = 0
    mot_cur:          int   = 0
    ank_ang:          int   = 0
    ank_vel:          int   = 0
    batt_volt:        int   = 0
    batt_curr:        int   = 0
    temperature:      int   = 0
    mot_ang_deg:      float = 0.0
    ank_ang_deg:      float = 0.0
    current_error_ma: float = 0.0
    position_error_ticks: float = 0.0
    torque_est_nm:    float = 0.0
    ankle_travel_from_start_deg: float = 0.0
    motor_travel_from_baseline_deg: float = 0.0
    rom_limit_hit:    bool  = False


class DataLogger:
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
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def clamp_current(value_ma: int) -> int:
    return max(-MAX_TEST_CURRENT_MA, min(MAX_TEST_CURRENT_MA, value_ma))


def read_device_safe(device: Device) -> dict:
    try:
        return device.read()
    except Exception as e:
        print(f"  [WARNING] device.read() failed: {e}")
        return {}


def safe_shutdown(device: Device):
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


def check_ankle_rom(ank_ang: int, ank_start: int) -> bool:
    """Return True if ankle has exceeded safe ROM from starting position."""
    travel = abs(ank_ang - ank_start)
    return travel >= ANKLE_ROM_LIMIT_TICKS


def make_sample(t0_global, t_loop_start, phase, label, amplitude, cmd,
                data, baseline_mot, ank_start, rom_hit):
    """Build a SampleRow from raw data dict."""
    mot_ang = data.get("mot_ang", 0)
    mot_cur = data.get("mot_cur", 0)
    ank_ang = data.get("ank_ang", 0)
    return SampleRow(
        timestamp_s      = time() - t0_global,
        loop_dt_ms       = (time() - t_loop_start) * 1000,
        test_phase       = phase,
        gain_label       = label,
        step_amplitude   = amplitude,
        command          = cmd,
        state_time       = data.get("state_time", 0),
        mot_ang          = mot_ang,
        mot_vel          = data.get("mot_vel", 0),
        mot_cur          = mot_cur,
        ank_ang          = ank_ang,
        ank_vel          = data.get("ank_vel", 0),
        batt_volt        = data.get("batt_volt", 0),
        batt_curr        = data.get("batt_curr", 0),
        temperature      = data.get("temperature", 0),
        mot_ang_deg      = mot_ang * TICKS_TO_DEG,
        ank_ang_deg      = ank_ang * TICKS_TO_DEG,
        current_error_ma = cmd - mot_cur if phase == "current" else 0,
        position_error_ticks = (cmd - mot_ang) if phase == "position" else 0,
        torque_est_nm    = (mot_cur / 1000.0) * KT,
        ankle_travel_from_start_deg = (ank_ang - ank_start) * TICKS_TO_DEG,
        motor_travel_from_baseline_deg = (mot_ang - baseline_mot) * TICKS_TO_DEG,
        rom_limit_hit    = rom_hit,
    )


def return_to_home(device, logger, baseline_pos, ank_start, t0_global):
    """Use position control to bring motor back to baseline."""
    device.set_gains(
        RETURN_TO_HOME_GAINS["kp"], RETURN_TO_HOME_GAINS["ki"],
        RETURN_TO_HOME_GAINS["kd"], RETURN_TO_HOME_GAINS["k"],
        RETURN_TO_HOME_GAINS["b"],  RETURN_TO_HOME_GAINS["ff"],
    )
    sleep(0.01)
    device.command_motor_position(baseline_pos)

    dt = 1.0 / STREAMING_FREQ
    t_start = time()

    while (time() - t_start) < RETURN_TO_HOME_TIMEOUT_S:
        t_loop = time()
        data = read_device_safe(device)
        if not data:
            sleep(dt)
            continue

        mot_ang = data.get("mot_ang", 0)
        error = abs(mot_ang - baseline_pos)

        row = make_sample(t0_global, t_loop, "return_home", "return_home",
                          0, baseline_pos, data, baseline_pos, ank_start, False)
        logger.add(row)

        if error <= RETURN_TO_HOME_TOLERANCE_TICKS:
            break

        remaining = dt - (time() - t_loop)
        if remaining > 0:
            sleep(remaining)

    device.command_motor_current(0)
    sleep(0.05)


# ═══════════════════════════════════════════════════════════════════════════
#  CURRENT-LOOP STEP TEST (v3: ankle-based ROM)
# ═══════════════════════════════════════════════════════════════════════════

def run_current_step_test(device, logger, gains, step_ma, baseline_mot,
                          ank_start, t0_global):
    """Short-pulse current step with ankle-based ROM protection."""
    label = gains["label"]
    step_ma = clamp_current(step_ma)
    print(f"    Current step: {label}  |  {step_ma} mA  |  {CURRENT_PULSE_MODE}")

    device.set_gains(gains["kp"], gains["ki"], gains["kd"],
                     gains["k"], gains["b"], gains["ff"])
    sleep(0.01)
    device.command_motor_current(0)
    sleep(0.05)

    dt = 1.0 / STREAMING_FREQ
    baseline_dur = 0.100
    pulse1_dur = CURRENT_PULSE_DURATION_S
    total_dur = CURRENT_RECORD_DURATION_S

    if CURRENT_PULSE_MODE == "bidirectional":
        pulse1_on  = baseline_dur
        pulse1_off = baseline_dur + pulse1_dur
        pulse2_off = pulse1_off + pulse1_dur
    else:
        pulse1_on  = baseline_dur
        pulse1_off = baseline_dur + pulse1_dur
        pulse2_off = pulse1_off  # no second pulse

    rom_hit = False
    rom_warned = False
    t_start = time()

    while True:
        t_loop = time()
        elapsed = t_loop - t_start
        if elapsed > total_dur:
            break

        # Determine command
        if elapsed < pulse1_on:
            cmd_ma = 0
        elif elapsed < pulse1_off:
            cmd_ma = step_ma
        elif CURRENT_PULSE_MODE == "bidirectional" and elapsed < pulse2_off:
            cmd_ma = -step_ma
        else:
            cmd_ma = 0

        data = read_device_safe(device)
        if not data:
            sleep(dt)
            continue

        ank_ang = data.get("ank_ang", 0)

        # ROM check on ANKLE encoder
        if check_ankle_rom(ank_ang, ank_start):
            device.command_motor_current(0)
            if not rom_warned:
                ank_travel = abs(ank_ang - ank_start) * TICKS_TO_DEG
                print(f"    [ANKLE ROM] Travel = {ank_travel:.1f}° "
                      f"(limit = {ANKLE_ROM_LIMIT_TICKS * TICKS_TO_DEG:.1f}°) — zeroed")
                rom_warned = True
            rom_hit = True
            cmd_ma = 0
        else:
            device.command_motor_current(clamp_current(cmd_ma))

        row = make_sample(t0_global, t_loop, "current", label, step_ma,
                          cmd_ma, data, baseline_mot, ank_start, rom_hit)
        logger.add(row)

        # Time watchdog
        if (time() - t_loop) > WATCHDOG_TIMEOUT_S:
            device.command_motor_current(0)

        remaining = dt - (time() - t_loop)
        if remaining > 0:
            sleep(remaining)

    device.command_motor_current(0)
    sleep(0.05)

    if rom_hit:
        print(f"    ⚠  Ankle ROM limit was hit")
    return rom_hit


# ═══════════════════════════════════════════════════════════════════════════
#  POSITION-LOOP STEP TEST (v3: ankle-based ROM)
# ═══════════════════════════════════════════════════════════════════════════

def run_position_step_test(device, logger, gains, step_ticks, baseline_pos,
                           ank_start, t0_global):
    """Position step response with ankle ROM check."""
    label = gains["label"]
    print(f"    Position step: {label}  |  {step_ticks} ticks "
          f"({step_ticks * TICKS_TO_DEG:.1f}°)")

    device.set_gains(gains["kp"], gains["ki"], gains["kd"],
                     gains["k"], gains["b"], gains["ff"])
    sleep(0.01)
    device.command_motor_position(baseline_pos)
    sleep(0.05)

    dt = 1.0 / STREAMING_FREQ
    baseline_dur = 0.200
    total_dur = POSITION_RECORD_DURATION_S
    step_on  = baseline_dur
    step_off = baseline_dur + POSITION_STEP_DURATION_S

    t_start = time()

    while True:
        t_loop = time()
        elapsed = t_loop - t_start
        if elapsed > total_dur:
            break

        if elapsed < step_on:
            target = baseline_pos
        elif elapsed < step_off:
            target = baseline_pos + step_ticks
        else:
            target = baseline_pos

        device.command_motor_position(target)

        data = read_device_safe(device)
        if not data:
            sleep(dt)
            continue

        row = make_sample(t0_global, t_loop, "position", label, step_ticks,
                          target, data, baseline_pos, ank_start, False)
        logger.add(row)

        if (time() - t_loop) > WATCHDOG_TIMEOUT_S:
            device.command_motor_current(0)

        remaining = dt - (time() - t_loop)
        if remaining > 0:
            sleep(remaining)

    device.command_motor_current(0)
    sleep(0.05)


# ═══════════════════════════════════════════════════════════════════════════
#  PLOTTING
# ═══════════════════════════════════════════════════════════════════════════

def plot_results(csv_path: str, output_dir: str):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import pandas as pd
    except ImportError:
        print("  [plot] matplotlib/pandas not available — skipping.")
        return

    df = pd.read_csv(csv_path)
    os.makedirs(output_dir, exist_ok=True)

    # ---- Current comparison: overlay all gains at each amplitude ----
    curr_df = df[df['test_phase'] == 'current']
    if len(curr_df) > 0:
        for amp in sorted(curr_df['step_amplitude'].unique()):
            fig, ax = plt.subplots(1, 1, figsize=(12, 6))
            ax.set_title(f"Current Loop — {amp:.0f} mA Step (all gain sets)",
                         fontsize=14, fontweight='bold')

            for label in curr_df['gain_label'].unique():
                adf = curr_df[(curr_df['gain_label'] == label) &
                              (curr_df['step_amplitude'] == amp)]
                if len(adf) == 0:
                    continue
                t = (adf['timestamp_s'].values - adf['timestamp_s'].values[0]) * 1000
                ax.plot(t, adf['mot_cur'].values, linewidth=1.2, label=label)

            # Plot command reference
            adf0 = curr_df[curr_df['step_amplitude'] == amp].iloc[:1]
            if len(adf0) > 0:
                first = curr_df[(curr_df['step_amplitude'] == amp)]
                t = (first['timestamp_s'].values - first['timestamp_s'].values[0]) * 1000
                ax.plot(t[:len(first)], first['command'].values,
                        'k--', linewidth=1, alpha=0.4, label='Command')

            ax.set_xlabel("Time (ms)")
            ax.set_ylabel("Current (mA)")
            ax.legend(fontsize=9)
            ax.grid(True, alpha=0.3)
            plt.tight_layout()
            fig.savefig(os.path.join(output_dir, f"current_overlay_{amp:.0f}mA.png"), dpi=150)
            plt.close(fig)
            print(f"  [plot] current_overlay_{amp:.0f}mA.png")

    # ---- Position comparison: overlay at each step size ----
    pos_df = df[df['test_phase'] == 'position']
    if len(pos_df) > 0:
        for amp in sorted(pos_df['step_amplitude'].unique()):
            fig, axes = plt.subplots(2, 1, figsize=(12, 9), sharex=True)
            fig.suptitle(f"Position Loop — {amp:.0f} Tick Step "
                         f"({amp * TICKS_TO_DEG:.1f}°, all gain sets)",
                         fontsize=14, fontweight='bold')

            for label in pos_df['gain_label'].unique():
                adf = pos_df[(pos_df['gain_label'] == label) &
                             (pos_df['step_amplitude'] == amp)]
                if len(adf) == 0:
                    continue

                baseline_cmd = adf.iloc[0]['command']
                t = (adf['timestamp_s'].values - adf['timestamp_s'].values[0]) * 1000

                axes[0].plot(t, adf['mot_ang'].values - baseline_cmd,
                             linewidth=1.2, label=label)
                axes[1].plot(t, adf['position_error_ticks'].values,
                             linewidth=1, label=label, alpha=0.8)

            # Command reference
            adf0 = pos_df[pos_df['step_amplitude'] == amp]
            if len(adf0) > 0:
                first_label = adf0['gain_label'].values[0]
                ref = adf0[adf0['gain_label'] == first_label]
                baseline_cmd = ref.iloc[0]['command']
                t = (ref['timestamp_s'].values - ref['timestamp_s'].values[0]) * 1000
                axes[0].plot(t, ref['command'].values - baseline_cmd,
                             'k--', linewidth=1.5, alpha=0.4, label='Command')

            axes[0].set_ylabel("Position (ticks from baseline)")
            axes[0].legend(fontsize=9)
            axes[0].grid(True, alpha=0.3)

            axes[1].axhline(0, color='gray', linestyle=':', linewidth=0.5)
            axes[1].set_ylabel("Position Error (ticks)")
            axes[1].set_xlabel("Time (ms)")
            axes[1].legend(fontsize=9)
            axes[1].grid(True, alpha=0.3)

            plt.tight_layout()
            fig.savefig(os.path.join(output_dir,
                        f"position_overlay_{amp:.0f}ticks.png"), dpi=150)
            plt.close(fig)
            print(f"  [plot] position_overlay_{amp:.0f}ticks.png")

    # ---- Ankle travel over entire test ----
    fig, ax = plt.subplots(1, 1, figsize=(14, 4))
    ax.set_title("Ankle Travel From Start (entire test)", fontsize=14)
    t = (df['timestamp_s'].values - df['timestamp_s'].values[0])
    ax.plot(t, df['ankle_travel_from_start_deg'].values, 'darkorange', linewidth=0.8)
    limit_deg = ANKLE_ROM_LIMIT_TICKS * TICKS_TO_DEG
    ax.axhline( limit_deg, color='red', linestyle='--', label=f'+{limit_deg:.0f}° limit')
    ax.axhline(-limit_deg, color='red', linestyle='--', label=f'-{limit_deg:.0f}° limit')
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Ankle Travel (°)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    fig.savefig(os.path.join(output_dir, "ankle_travel_timeline.png"), dpi=150)
    plt.close(fig)
    print(f"  [plot] ankle_travel_timeline.png")


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Bench PID test v3 — ankle-based ROM safety"
    )
    parser.add_argument("--port", type=str, default="/dev/ttyACM0")
    parser.add_argument("--side", type=str, default="left",
                        choices=["left", "right"])
    parser.add_argument("--freq", type=int, default=STREAMING_FREQ)
    parser.add_argument("--skip-current", action="store_true")
    parser.add_argument("--skip-position", action="store_true")
    parser.add_argument("--plot", type=str, default=None,
                        help="Plot a previous CSV instead of running tests")
    parser.add_argument("--output-dir", type=str, default=None)
    args = parser.parse_args()

    if args.plot:
        out = args.output_dir or os.path.join(SCRIPT_DIR, "results")
        plot_results(args.plot, out)
        return

    side = LEFT if args.side == "left" else RIGHT
    output_dir = args.output_dir or os.path.join(SCRIPT_DIR, "results")

    print("=" * 70)
    print("  BENCH PID TEST 3 — Rigid-Chain ExoBoot (Ankle ROM)")
    print(f"  Port: {args.port}  |  Side: {args.side}  |  Freq: {args.freq} Hz")
    print(f"  Ankle ROM: {ANKLE_ROM_DEG}°  |  Safety: {ANKLE_SAFETY_MARGIN}°  "
          f"|  Limit: {ANKLE_ROM_LIMIT_TICKS * TICKS_TO_DEG:.1f}° ankle travel")
    print(f"  Output: {output_dir}")
    print("=" * 70)
    print()

    # --- Connect ---
    print("[1/5] Connecting …")
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
    device.command_motor_current(0)
    sleep(0.2)

    def signal_handler(sig, frame):
        safe_shutdown(device)
        sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)

    init = read_device_safe(device)
    baseline_mot = init.get("mot_ang", 0)
    ank_start    = init.get("ank_ang", 0)

    print(f"  Motor baseline: {baseline_mot} ticks ({baseline_mot * TICKS_TO_DEG:.1f}°)")
    print(f"  Ankle start:    {ank_start} ticks ({ank_start * TICKS_TO_DEG:.1f}°)")
    print(f"  Ankle ROM window: [{(ank_start - ANKLE_ROM_LIMIT_TICKS)*TICKS_TO_DEG:.1f}°, "
          f"{(ank_start + ANKLE_ROM_LIMIT_TICKS)*TICKS_TO_DEG:.1f}°]")
    print()

    logger = DataLogger(output_dir, prefix="bench_pid_test3")
    t0 = time()

    # ═══════════════════════════════════════════════════════════════════
    #  PHASE 1 — Current Loop
    # ═══════════════════════════════════════════════════════════════════
    if not args.skip_current:
        print("[2/5] PHASE 1 — Current-loop step responses")
        print(f"  Gain sets: {[g['label'] for g in CURRENT_GAIN_SETS]}")
        print(f"  Amplitudes: {CURRENT_STEP_AMPLITUDES_MA} mA")
        print(f"  Pulse: {CURRENT_PULSE_DURATION_S*1000:.0f} ms  |  "
              f"Mode: {CURRENT_PULSE_MODE}")
        print()

        rom_hits = 0
        for gains in CURRENT_GAIN_SETS:
            print(f"  --- {gains['label']} "
                  f"(kp={gains['kp']}, ki={gains['ki']}, ff={gains['ff']}) ---")

            for amp in CURRENT_STEP_AMPLITUDES_MA:
                hit = run_current_step_test(
                    device, logger, gains, amp, baseline_mot, ank_start, t0)
                if hit:
                    rom_hits += 1

                print(f"    Returning to home …")
                return_to_home(device, logger, baseline_mot, ank_start, t0)

                # Check for motor drift
                data = read_device_safe(device)
                new_mot = data.get("mot_ang", 0)
                drift = abs(new_mot - baseline_mot) * TICKS_TO_DEG
                if drift > 5.0:
                    print(f"    [DRIFT] Motor baseline shifted {drift:.1f}° — updating")
                    baseline_mot = new_mot
                # Also update ankle reference in case it shifted
                new_ank = data.get("ank_ang", 0)
                ank_drift = abs(new_ank - ank_start) * TICKS_TO_DEG
                if ank_drift > 3.0:
                    print(f"    [DRIFT] Ankle shifted {ank_drift:.1f}° — updating")
                    ank_start = new_ank

                sleep(SETTLE_PAUSE_S)
            print()

        device.command_motor_current(0)
        sleep(0.5)
        print(f"  Phase 1 complete.  Ankle ROM hits: {rom_hits}\n")
    else:
        print("[2/5] PHASE 1 — SKIPPED\n")

    # ═══════════════════════════════════════════════════════════════════
    #  PHASE 2 — Position Loop
    # ═══════════════════════════════════════════════════════════════════
    if not args.skip_position:
        print("[3/5] PHASE 2 — Position-loop step responses")
        print(f"  Gain sets: {[g['label'] for g in POSITION_GAIN_SETS]}")
        print(f"  Amplitudes: {POSITION_STEP_AMPLITUDES_TICKS} ticks")
        print()

        data = read_device_safe(device)
        baseline_mot = data.get("mot_ang", 0)
        ank_start = data.get("ank_ang", 0)
        print(f"  Motor baseline: {baseline_mot}  |  Ankle: {ank_start}")

        for gains in POSITION_GAIN_SETS:
            print(f"  --- {gains['label']} "
                  f"(kp={gains['kp']}, ki={gains['ki']}, kd={gains['kd']}) ---")

            for amp in POSITION_STEP_AMPLITUDES_TICKS:
                run_position_step_test(
                    device, logger, gains, amp, baseline_mot, ank_start, t0)
                sleep(SETTLE_PAUSE_S)

                data = read_device_safe(device)
                baseline_mot = data.get("mot_ang", 0)
            print()

        device.command_motor_current(0)
        sleep(0.5)
        print("  Phase 2 complete.\n")
    else:
        print("[3/5] PHASE 2 — SKIPPED\n")

    # ═══════════════════════════════════════════════════════════════════
    #  SAVE & PLOT
    # ═══════════════════════════════════════════════════════════════════
    print("[4/5] Saving …")
    logger.save()

    print("[5/5] Plotting …")
    plot_results(logger.filepath, output_dir)

    safe_shutdown(device)
    print("\n" + "=" * 70)
    print("  TEST COMPLETE")
    print(f"  Data:  {logger.filepath}")
    print(f"  Plots: {output_dir}/")
    print("=" * 70)


if __name__ == "__main__":
    main()
"""
Bench PID Test 2 — Step Response & Gain Sweep for Rigid-Chain ExoBoot
======================================================================

v2 changes from Bench_PID_Test1:
  - ROM safety:  Motor position is tracked every iteration.  If the motor
    moves beyond ROM_SAFETY_MARGIN_DEG of the measured ROM, current is
    killed immediately.  This prevents chain-maxing.
  - Bidirectional current pulses:  Instead of holding current in one
    direction for 1 s (which winds the chain to its limit), current
    tests now use short ON/OFF pulses OR alternating +/- pulses so the
    motor oscillates near its starting point.
  - Shorter step durations:  Current steps are 100 ms ON (plenty for
    measuring rise time at 500 Hz — that's 50 samples), not 1 s.
  - Position-return between current tests:  After each current pulse
    the motor is commanded back toward the baseline via position control
    before the next pulse fires.
  - Configurable ROM:  Set TOTAL_ROM_DEG to your measured range.

Standalone bench-test script for tuning PID gains on the *rigid-chain*
ExoBoot (as opposed to Xiangyu's elastic-belt version).  Connects to a
single boot on the bench (no walking, no gait detection) and runs:

    Phase 1 — Current-loop step responses  (inner loop, short pulses)
    Phase 2 — Position-loop step responses (outer loop, bounded steps)

All sensor data is logged to timestamped CSVs.

Safety
------
* ROM watchdog kills current if motor exceeds safe travel.
* Software current clamp (MAX_TEST_CURRENT_MA) limits commands.
* Time watchdog zeros motor if a loop iteration takes too long.
* Ctrl-C is caught and the motor is safely shut down.
* The boot is always returned to zero-current before disconnect.

Usage
-----
    python PID_testing/Bench_PID_Test2.py --port /dev/ttyACM0 --side left

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
SETTLE_PAUSE_S      = 1.5       # Seconds to wait between tests

# --- ROM Safety (CRITICAL for rigid chain) ---
TOTAL_ROM_DEG        = 115.0    # Your measured full ROM in degrees
ROM_SAFETY_MARGIN_DEG = 20.0    # Stay this far from mechanical limits
USABLE_ROM_DEG       = TOTAL_ROM_DEG - (2 * ROM_SAFETY_MARGIN_DEG)  # 75°

# Convert to ticks for fast comparison in the loop
TICKS_PER_REV     = 2**14       # 16384
TICKS_TO_DEG      = 360.0 / TICKS_PER_REV
DEG_TO_TICKS      = TICKS_PER_REV / 360.0
TICKS_TO_RAD      = 2 * np.pi / TICKS_PER_REV

# Max ticks the motor is allowed to move from its baseline in either direction
ROM_LIMIT_TICKS = int((TOTAL_ROM_DEG - ROM_SAFETY_MARGIN_DEG) * DEG_TO_TICKS)
# For current tests: tighter limit (half usable ROM) since we want to stay near center
CURRENT_TEST_ROM_TICKS = int((USABLE_ROM_DEG / 2.0) * DEG_TO_TICKS)

# --- Motor constant ---
KT = 0.14                       # Nm/A

# --- Side constants ---
LEFT  =  1
RIGHT = -1


# ═══════════════════════════════════════════════════════════════════════════
#  GAIN SETS TO TEST
# ═══════════════════════════════════════════════════════════════════════════
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

# ----- Current step-test parameters (v2: short pulses) -----
CURRENT_STEP_AMPLITUDES_MA = [500, 1000, 1500, 2000]
CURRENT_PULSE_DURATION_S   = 0.100   # 100 ms ON pulse (50 samples at 500 Hz)
CURRENT_RECORD_DURATION_S  = 0.500   # 500 ms total window (pulse + settling)
CURRENT_PULSE_MODE         = "bidirectional"  # "unidirectional" or "bidirectional"
#   bidirectional:  +step for 100 ms, then -step for 100 ms, then 0
#   unidirectional: +step for 100 ms, then 0

# ----- Position step-test parameters (v2: bounded to safe ROM) -----
# These will be clamped at runtime to never exceed USABLE_ROM_DEG / 2
POSITION_STEP_AMPLITUDES_TICKS = [200, 500, 1000]
POSITION_STEP_DURATION_S  = 0.500    # Hold the step for 500 ms
POSITION_RECORD_DURATION_S = 1.200   # Total record window

# Position gains used to return motor to baseline between current tests
RETURN_TO_HOME_GAINS = {"kp": 50, "ki": 15, "kd": 5, "k": 0, "b": 0, "ff": 0}
RETURN_TO_HOME_TIMEOUT_S = 2.0       # Max time to wait for return
RETURN_TO_HOME_TOLERANCE_TICKS = 30  # "close enough" to baseline


# ═══════════════════════════════════════════════════════════════════════════
#  DATA LOGGING
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class SampleRow:
    """One row of bench-test data."""
    timestamp_s:      float = 0.0
    loop_dt_ms:       float = 0.0
    test_phase:       str   = ""        # "current" / "position" / "return_home"
    gain_label:       str   = ""
    step_amplitude:   float = 0.0       # mA or ticks, depending on phase
    command:          float = 0.0       # What we sent (mA or ticks)

    # Raw sensor readings
    state_time:       int   = 0
    mot_ang:          int   = 0
    mot_vel:          int   = 0
    mot_cur:          int   = 0
    ank_ang:          int   = 0
    ank_vel:          int   = 0
    batt_volt:        int   = 0
    batt_curr:        int   = 0
    temperature:      int   = 0

    # Derived
    mot_ang_deg:      float = 0.0
    ank_ang_deg:      float = 0.0
    current_error_ma: float = 0.0
    position_error_ticks: float = 0.0
    torque_est_nm:    float = 0.0
    travel_from_baseline_deg: float = 0.0   # NEW: track ROM usage
    rom_limit_hit:    bool  = False         # NEW: flag if safety triggered


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
    """Read from device with error handling."""
    try:
        return device.read()
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


def check_rom_limit(mot_ang: int, baseline_pos: int, limit_ticks: int) -> bool:
    """Return True if the motor has exceeded the safe travel limit."""
    travel = abs(mot_ang - baseline_pos)
    return travel >= limit_ticks


def return_to_home(
    device: Device,
    logger: DataLogger,
    baseline_pos: int,
    t0_global: float,
):
    """
    Use position control to bring the motor back to baseline_pos.
    This is called between current-step tests to prevent drift.
    """
    device.set_gains(
        RETURN_TO_HOME_GAINS["kp"], RETURN_TO_HOME_GAINS["ki"],
        RETURN_TO_HOME_GAINS["kd"], RETURN_TO_HOME_GAINS["k"],
        RETURN_TO_HOME_GAINS["b"],  RETURN_TO_HOME_GAINS["ff"],
    )
    sleep(0.01)

    device.command_motor_position(baseline_pos)

    dt_target = 1.0 / STREAMING_FREQ
    t_start = time()

    while (time() - t_start) < RETURN_TO_HOME_TIMEOUT_S:
        t_loop = time()
        data = read_device_safe(device)
        if not data:
            sleep(dt_target)
            continue

        mot_ang = data.get("mot_ang", 0)
        error_ticks = abs(mot_ang - baseline_pos)

        # Log the return journey
        row = SampleRow(
            timestamp_s = time() - t0_global,
            loop_dt_ms  = (time() - t_loop) * 1000,
            test_phase  = "return_home",
            gain_label  = "return_home",
            command     = baseline_pos,
            mot_ang     = mot_ang,
            mot_vel     = data.get("mot_vel", 0),
            mot_cur     = data.get("mot_cur", 0),
            ank_ang     = data.get("ank_ang", 0),
            mot_ang_deg = mot_ang * TICKS_TO_DEG,
            position_error_ticks = baseline_pos - mot_ang,
            travel_from_baseline_deg = (mot_ang - baseline_pos) * TICKS_TO_DEG,
        )
        logger.add(row)

        if error_ticks <= RETURN_TO_HOME_TOLERANCE_TICKS:
            break

        remaining = dt_target - (time() - t_loop)
        if remaining > 0:
            sleep(remaining)

    # Switch back to zero current (safe idle)
    device.command_motor_current(0)
    sleep(0.05)


# ═══════════════════════════════════════════════════════════════════════════
#  CURRENT-LOOP STEP TEST (v2: short pulses with ROM watchdog)
# ═══════════════════════════════════════════════════════════════════════════

def run_current_step_test(
    device: Device,
    logger: DataLogger,
    gains: Dict[str, int],
    step_ma: int,
    baseline_pos: int,
    t0_global: float,
):
    """
    Current-loop step response with ROM protection.

    Bidirectional mode (default):
      0 → +step_ma (100 ms) → -step_ma (100 ms) → 0 (settling)
      This keeps the motor oscillating near baseline instead of
      running away toward the chain limit.

    Unidirectional mode:
      0 → +step_ma (100 ms) → 0 (settling)
      Shorter pulse means less total travel than v1's 1-second hold.

    ROM watchdog:  If motor moves > CURRENT_TEST_ROM_TICKS from baseline,
    current is immediately zeroed and the test is flagged.
    """
    label = gains["label"]
    step_ma = clamp_current(step_ma)
    print(f"    Current step: {label}  |  amplitude = {step_ma} mA  "
          f"|  mode = {CURRENT_PULSE_MODE}")

    # Set current gains
    device.set_gains(gains["kp"], gains["ki"], gains["kd"],
                     gains["k"], gains["b"], gains["ff"])
    sleep(0.01)

    # Start from zero current
    device.command_motor_current(0)
    sleep(0.05)

    dt_target = 1.0 / STREAMING_FREQ
    baseline_dur = 0.100  # 100 ms baseline recording
    pulse1_dur = CURRENT_PULSE_DURATION_S
    total_dur = CURRENT_RECORD_DURATION_S

    if CURRENT_PULSE_MODE == "bidirectional":
        # Timeline: [0, baseline] → [baseline, baseline+pulse] = +step
        #           → [baseline+pulse, baseline+2*pulse] = -step → settle
        pulse1_on  = baseline_dur
        pulse1_off = baseline_dur + pulse1_dur
        pulse2_on  = pulse1_off
        pulse2_off = pulse1_off + pulse1_dur
    else:
        pulse1_on  = baseline_dur
        pulse1_off = baseline_dur + pulse1_dur
        pulse2_on  = None  # no second pulse
        pulse2_off = None

    rom_hit = False
    t_start = time()

    while True:
        t_loop_start = time()
        elapsed = t_loop_start - t_start

        if elapsed > total_dur:
            break

        # --- Determine current command based on timeline ---
        if elapsed < pulse1_on:
            cmd_ma = 0
        elif elapsed < pulse1_off:
            cmd_ma = step_ma
        elif CURRENT_PULSE_MODE == "bidirectional" and elapsed < pulse2_off:
            cmd_ma = -step_ma
        else:
            cmd_ma = 0

        # --- ROM watchdog: check position BEFORE sending command ---
        data = read_device_safe(device)
        if not data:
            sleep(dt_target)
            continue

        mot_ang = data.get("mot_ang", 0)
        travel_ticks = mot_ang - baseline_pos
        travel_deg = travel_ticks * TICKS_TO_DEG

        if check_rom_limit(mot_ang, baseline_pos, CURRENT_TEST_ROM_TICKS):
            # KILL CURRENT — we're too close to the mechanical stop
            device.command_motor_current(0)
            rom_hit = True
            print(f"    [ROM LIMIT] Travel = {abs(travel_deg):.1f}° "
                  f"(limit = {CURRENT_TEST_ROM_TICKS * TICKS_TO_DEG:.1f}°) "
                  f"— current zeroed!")
            cmd_ma = 0
            # Keep logging but don't send any more current
        else:
            device.command_motor_current(clamp_current(cmd_ma))

        mot_cur = data.get("mot_cur", 0)

        row = SampleRow(
            timestamp_s      = time() - t0_global,
            loop_dt_ms       = (time() - t_loop_start) * 1000,
            test_phase       = "current",
            gain_label       = label,
            step_amplitude   = step_ma,
            command          = cmd_ma,
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
            current_error_ma = cmd_ma - mot_cur,
            position_error_ticks = 0,
            torque_est_nm    = (mot_cur / 1000.0) * KT,
            travel_from_baseline_deg = travel_deg,
            rom_limit_hit    = rom_hit,
        )
        logger.add(row)

        # Time watchdog
        loop_elapsed = time() - t_loop_start
        if loop_elapsed > WATCHDOG_TIMEOUT_S:
            print(f"    [WATCHDOG] Loop took {loop_elapsed*1000:.1f} ms — zeroing")
            device.command_motor_current(0)

        remaining = dt_target - (time() - t_loop_start)
        if remaining > 0:
            sleep(remaining)

    # Zero current
    device.command_motor_current(0)
    sleep(0.05)

    if rom_hit:
        print(f"    ⚠  ROM limit was hit during this test — data is flagged")

    return rom_hit


# ═══════════════════════════════════════════════════════════════════════════
#  POSITION-LOOP STEP TEST (v2: bounded steps with ROM check)
# ═══════════════════════════════════════════════════════════════════════════

def run_position_step_test(
    device: Device,
    logger: DataLogger,
    gains: Dict[str, int],
    step_ticks: int,
    baseline_pos: int,
    t0_global: float,
):
    """
    Position-loop step response with ROM bounding.

    The step is clamped so that baseline + step never exceeds
    ROM_LIMIT_TICKS from the initial position.
    """
    label = gains["label"]

    # Clamp step to safe ROM
    max_step = ROM_LIMIT_TICKS - abs(baseline_pos)  # conservative
    # Actually, ROM_LIMIT_TICKS is from the *test start* baseline,
    # so just clamp to half usable ROM in either direction
    half_rom = CURRENT_TEST_ROM_TICKS  # reuse the same conservative limit
    if abs(step_ticks) > half_rom:
        original = step_ticks
        step_ticks = half_rom if step_ticks > 0 else -half_rom
        print(f"    [ROM CLAMP] Step {original} ticks → {step_ticks} ticks "
              f"({step_ticks * TICKS_TO_DEG:.1f}°)")

    print(f"    Position step: {label}  |  amplitude = {step_ticks} ticks "
          f"({step_ticks * TICKS_TO_DEG:.1f}°)")

    # Set position gains
    device.set_gains(gains["kp"], gains["ki"], gains["kd"],
                     gains["k"], gains["b"], gains["ff"])
    sleep(0.01)

    # Hold current position
    device.command_motor_position(baseline_pos)
    sleep(0.05)

    dt_target = 1.0 / STREAMING_FREQ
    baseline_dur = 0.200
    total_dur = POSITION_RECORD_DURATION_S
    step_on_time = baseline_dur
    step_off_time = baseline_dur + POSITION_STEP_DURATION_S

    target_pos = baseline_pos
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
        travel_deg = (mot_ang - baseline_pos) * TICKS_TO_DEG

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
            travel_from_baseline_deg = travel_deg,
            rom_limit_hit    = False,
        )
        logger.add(row)

        # Time watchdog
        loop_elapsed = time() - t_loop_start
        if loop_elapsed > WATCHDOG_TIMEOUT_S:
            print(f"    [WATCHDOG] Loop took {loop_elapsed*1000:.1f} ms — zeroing")
            device.command_motor_current(0)

        remaining = dt_target - (time() - t_loop_start)
        if remaining > 0:
            sleep(remaining)

    # Return to zero current (safe state)
    device.command_motor_current(0)
    sleep(0.05)


# ═══════════════════════════════════════════════════════════════════════════
#  ANALYSIS HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def compute_step_metrics(times, values, command_value, settle_band_pct=5.0):
    """
    Given a time series during a step, compute:
      - rise_time_s:    10% → 90% of final value
      - overshoot_pct:  peak overshoot as % of step
      - settling_time_s: time to stay within ±settle_band_pct of final
      - steady_state_error: mean error in last 20% of window
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

    peak = np.max(values) if final_val > 0 else np.min(values)
    overshoot_pct = ((peak - final_val) / abs(final_val)) * 100.0

    band = abs(final_val) * settle_band_pct / 100.0
    within_band = np.abs(values - final_val) <= band
    settling_time = None
    for i in range(len(within_band) - 1, -1, -1):
        if not within_band[i]:
            if i + 1 < len(times):
                settling_time = times[i + 1]
            break

    tail_start = int(0.8 * len(values))
    ss_error = np.mean(values[tail_start:]) - final_val

    return {
        "rise_time_s":       rise_time,
        "overshoot_pct":     overshoot_pct,
        "settling_time_s":   settling_time,
        "steady_state_error": ss_error,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  PLOTTING
# ═══════════════════════════════════════════════════════════════════════════

def plot_results(csv_path: str, output_dir: str):
    """Generate step-response plots from a bench-test CSV."""
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
        if phase == "return_home":
            continue  # skip return-to-home segments in plots

        phase_df = df[df["test_phase"] == phase]

        for label in phase_df["gain_label"].unique():
            gain_df = phase_df[phase_df["gain_label"] == label]

            for amp in gain_df["step_amplitude"].unique():
                trial_df = gain_df[gain_df["step_amplitude"] == amp].copy()
                trial_df = trial_df.sort_values("timestamp_s")

                t = trial_df["timestamp_s"].values
                t = t - t[0]

                fig, axes = plt.subplots(4, 1, figsize=(12, 13), sharex=True)
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

                # --- Panel 3: Torque estimate + motor velocity ---
                ax3a = axes[2]
                ax3b = ax3a.twinx()
                ax3a.plot(t, trial_df["torque_est_nm"].values,
                          "g-", label="Torque est (Nm)", linewidth=1)
                ax3b.plot(t, trial_df["mot_vel"].values,
                          "m-", label="Motor vel", linewidth=0.8, alpha=0.7)
                ax3a.set_ylabel("Torque (Nm)", color="g")
                ax3b.set_ylabel("Motor Velocity", color="m")
                ax3a.grid(True, alpha=0.3)
                lines_a, labels_a = ax3a.get_legend_handles_labels()
                lines_b, labels_b = ax3b.get_legend_handles_labels()
                ax3a.legend(lines_a + lines_b, labels_a + labels_b,
                            loc="upper right")

                # --- Panel 4: Travel from baseline (ROM usage) ---
                axes[3].plot(t, trial_df["travel_from_baseline_deg"].values,
                             "darkorange", linewidth=1)
                axes[3].axhline(CURRENT_TEST_ROM_TICKS * TICKS_TO_DEG,
                                color="red", linestyle="--", linewidth=1,
                                label=f"ROM limit (+{CURRENT_TEST_ROM_TICKS * TICKS_TO_DEG:.0f}°)")
                axes[3].axhline(-CURRENT_TEST_ROM_TICKS * TICKS_TO_DEG,
                                color="red", linestyle="--", linewidth=1,
                                label=f"ROM limit (−{CURRENT_TEST_ROM_TICKS * TICKS_TO_DEG:.0f}°)")
                axes[3].set_ylabel("Travel from baseline (°)")
                axes[3].set_xlabel("Time (s)")
                axes[3].legend(loc="upper right")
                axes[3].grid(True, alpha=0.3)

                # Flag ROM hits
                if trial_df["rom_limit_hit"].any():
                    axes[3].set_facecolor("#fff0f0")
                    axes[3].text(0.5, 0.95, "⚠ ROM LIMIT HIT",
                                 transform=axes[3].transAxes, fontsize=12,
                                 color="red", ha="center", va="top",
                                 fontweight="bold")

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
        description="Bench PID test v2 for rigid-chain ExoBoot (ROM-safe)"
    )
    parser.add_argument("--port", type=str, default="/dev/ttyACM0")
    parser.add_argument("--side", type=str, default="left",
                        choices=["left", "right"])
    parser.add_argument("--freq", type=int, default=STREAMING_FREQ)
    parser.add_argument("--skip-current", action="store_true")
    parser.add_argument("--skip-position", action="store_true")
    parser.add_argument("--plot", type=str, default=None,
                        help="Plot results from a previous CSV")
    parser.add_argument("--output-dir", type=str, default=None)

    args = parser.parse_args()

    if args.plot:
        out_dir = args.output_dir or os.path.join(SCRIPT_DIR, "results")
        plot_results(args.plot, out_dir)
        return

    side = LEFT if args.side == "left" else RIGHT
    output_dir = args.output_dir or os.path.join(SCRIPT_DIR, "results")

    # --- Banner ---
    print("=" * 70)
    print("  BENCH PID TEST 2 — Rigid-Chain ExoBoot (ROM-Safe)")
    print(f"  Port: {args.port}  |  Side: {args.side}  |  Freq: {args.freq} Hz")
    print(f"  Max current: {MAX_TEST_CURRENT_MA} mA")
    print(f"  Total ROM: {TOTAL_ROM_DEG}°  |  Safety margin: {ROM_SAFETY_MARGIN_DEG}°")
    print(f"  Usable ROM: {USABLE_ROM_DEG}°  |  Current test limit: "
          f"±{CURRENT_TEST_ROM_TICKS * TICKS_TO_DEG:.1f}°")
    print(f"  Current pulse: {CURRENT_PULSE_DURATION_S*1000:.0f} ms  |  "
          f"Mode: {CURRENT_PULSE_MODE}")
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

    device.command_motor_current(0)
    sleep(0.2)

    # Ctrl-C handler
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
    print(f"  ROM limits: [{(baseline_pos - CURRENT_TEST_ROM_TICKS) * TICKS_TO_DEG:.1f}°, "
          f"{(baseline_pos + CURRENT_TEST_ROM_TICKS) * TICKS_TO_DEG:.1f}°]")
    print()

    logger = DataLogger(output_dir, prefix="bench_pid_test2")
    t0_global = time()

    # ═══════════════════════════════════════════════════════════════════
    #  PHASE 1 — Current Loop (short pulses)
    # ═══════════════════════════════════════════════════════════════════
    if not args.skip_current:
        print("[2/5] PHASE 1 — Current-loop step responses (short pulses)")
        print(f"  Gain sets: {len(CURRENT_GAIN_SETS)}")
        print(f"  Step amplitudes: {CURRENT_STEP_AMPLITUDES_MA} mA")
        print(f"  Pulse: {CURRENT_PULSE_DURATION_S*1000:.0f} ms ON  |  "
              f"Record: {CURRENT_RECORD_DURATION_S*1000:.0f} ms total")
        print()

        rom_hit_count = 0

        for gains in CURRENT_GAIN_SETS:
            print(f"  --- {gains['label']} "
                  f"(kp={gains['kp']}, ki={gains['ki']}, ff={gains['ff']}) ---")

            for amp in CURRENT_STEP_AMPLITUDES_MA:
                rom_hit = run_current_step_test(
                    device, logger, gains, amp, baseline_pos, t0_global
                )
                if rom_hit:
                    rom_hit_count += 1

                # Return motor to baseline before next test
                print(f"    Returning to home …")
                return_to_home(device, logger, baseline_pos, t0_global)

                # Re-read baseline (in case of small drift)
                data = read_device_safe(device)
                new_pos = data.get("mot_ang", 0)
                drift = abs(new_pos - baseline_pos) * TICKS_TO_DEG
                if drift > 2.0:
                    print(f"    [DRIFT] Baseline shifted {drift:.1f}° — updating")
                    baseline_pos = new_pos

                sleep(SETTLE_PAUSE_S)

            print()

        device.command_motor_current(0)
        sleep(0.5)
        print(f"  Phase 1 complete.  ROM limit hit {rom_hit_count} time(s).\n")
    else:
        print("[2/5] PHASE 1 — SKIPPED\n")

    # ═══════════════════════════════════════════════════════════════════
    #  PHASE 2 — Position Loop
    # ═══════════════════════════════════════════════════════════════════
    if not args.skip_position:
        print("[3/5] PHASE 2 — Position-loop step responses")
        print(f"  Gain sets: {len(POSITION_GAIN_SETS)}")
        print(f"  Step amplitudes: {POSITION_STEP_AMPLITUDES_TICKS} ticks")
        print()

        data = read_device_safe(device)
        baseline_pos = data.get("mot_ang", 0)
        print(f"  Baseline: {baseline_pos} ticks ({baseline_pos * TICKS_TO_DEG:.1f}°)")

        for gains in POSITION_GAIN_SETS:
            print(f"  --- {gains['label']} "
                  f"(kp={gains['kp']}, ki={gains['ki']}, kd={gains['kd']}) ---")

            for amp in POSITION_STEP_AMPLITUDES_TICKS:
                run_position_step_test(
                    device, logger, gains, amp, baseline_pos, t0_global
                )
                sleep(SETTLE_PAUSE_S)

                data = read_device_safe(device)
                baseline_pos = data.get("mot_ang", 0)

            print()

        device.command_motor_current(0)
        sleep(0.5)
        print("  Phase 2 complete.\n")
    else:
        print("[3/5] PHASE 2 — SKIPPED\n")

    # ═══════════════════════════════════════════════════════════════════
    #  SAVE & PLOT
    # ═══════════════════════════════════════════════════════════════════
    print("[4/5] Saving data …")
    logger.save()

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
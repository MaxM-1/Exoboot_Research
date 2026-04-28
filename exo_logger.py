"""
ExoLogger — Unified per-sample CSV logger for the ExoBoot controller.
====================================================================

Writes one row per control-loop iteration with EVERYTHING needed to
diagnose why a boot did/did not produce torque, including:

  * Raw FlexSEA sensor values (gyro, accel, encoders, mot_cur, …)
  * Controller state (mode: idle_position / position_track /
        cur_ramp_up / cur_ramp_down)
  * Gait state (num_gait, percent_gait, expected_duration,
        current_duration, heelstrike_armed, segmentation_trigger,
        armed_time_ms, refractory_ms, arm/trig thresholds)
  * Commanded torque (Nm) and motor current (mA)
  * Static metadata stamped on every row (participant_id, weight_kg,
        side, boot_id, phase, gains, profile parameters)

One file per boot per phase, written **line-buffered** so a crash still
leaves a usable partial file.

Author:  Max Miller — Auburn University
"""
from __future__ import annotations

import csv
import os
import time
from time import strftime
from typing import Optional

from config import (
    LEFT, MIN_STRIDE_PERIOD, REFRACTORY_FRACTION, REFRACTORY_MAX,
    CURRENT_GAINS, POSITION_GAINS,
)


HEADER = [
    # --- timing ---
    "wall_time_s", "state_time_ms", "loop_iter",
    # --- metadata (constant per file) ---
    "participant_id", "side", "boot_id", "phase", "weight_kg",
    "test_mode", "approach",
    "t_rise", "t_fall", "t_peak", "peak_torque_norm",
    "kp_cur", "ki_cur", "kd_cur", "ff_cur",
    "kp_pos", "ki_pos", "kd_pos",
    # --- raw IMU ---
    "accelx", "accely", "accelz",
    "gyrox", "gyroy", "gyroz_signed",
    # --- raw encoders ---
    "mot_ang_raw", "mot_ang_zeroed", "mot_vel", "mot_cur_meas_mA",
    "mot_volt_mV",
    "ank_ang_raw", "ank_ang_zeroed",
    "ank_vel", "ank_vel_filt",
    # --- battery / thermal ---
    "batt_volt_mV", "batt_curr_mA", "temp_C",
    # --- firmware status registers (non-zero = fault) ---
    "status_mn", "status_ex", "status_re",
    # --- controller / command state ---
    "controller_mode",         # idle_position / position_track / cur_ramp_up / cur_ramp_down
    "gains_mode",              # current / position / none
    "tau_Nm", "current_cmd_mA", "wm_wa",
    # --- position-control diagnostics ---
    "mot_pos_setpoint",        # last commanded motor position (ticks)
    "mot_pos_error",           # setpoint - actual (ticks)
    # --- gait state ---
    "num_gait", "percent_gait", "expected_dur_ms", "current_dur_ms",
    "hs_armed", "armed_time_ms", "refractory_ms",
    "seg_trigger", "arm_thr", "trg_thr",
    # --- derived ---
    "stride_idx_in_phase",
]


class ExoLogger:
    """Per-sample CSV logger attached to a single ExoBoot instance.

    Usage::

        boot.logger = ExoLogger(out_dir, "P001", boot, "Familiarization",
                                params)
        # ... in run_collins_profile after each command_motor_*:
        boot.logger.set_controller_mode("cur_ramp_up")
        boot.logger.log(tau_Nm=tau, current_cmd_mA=cur)
        # ... at experiment end:
        boot.logger.close()
    """

    def __init__(self, out_dir: str, participant_id: str, boot,
                 phase: str, params: dict):
        os.makedirs(out_dir, exist_ok=True)
        side_str = "L" if boot.side == LEFT else "R"
        ts = strftime("%Y-%m-%d_%Hh%Mm%Ss")
        fname = f"{participant_id}_{phase}_{side_str}_{ts}_full.csv"
        self.path = os.path.join(out_dir, fname)
        # buffering=1  ⇒ line-buffered, so a crash still leaves a CSV
        self.fh = open(self.path, "w", newline="", buffering=1)
        self.w = csv.writer(self.fh)
        self.w.writerow(HEADER)

        self.boot = boot
        self._participant = participant_id
        self._side_str = side_str
        self._phase = phase
        self._iter = 0
        self._t0: Optional[float] = None
        self._stride_idx = 0
        self._last_num_gait = 0
        self._controller_mode = "idle"

        # Static metadata cached once
        self._meta_static = {
            "participant_id": participant_id,
            "side": side_str,
            "boot_id": getattr(boot, "boot_id",
                               str(getattr(boot.device, "id", ""))),
            "phase": phase,
            "weight_kg": params.get("user_weight", ""),
            "test_mode": params.get("test_mode", ""),
            "approach": params.get("approach", ""),
            "kp_cur": CURRENT_GAINS["kp"], "ki_cur": CURRENT_GAINS["ki"],
            "kd_cur": CURRENT_GAINS["kd"], "ff_cur": CURRENT_GAINS["ff"],
            "kp_pos": POSITION_GAINS["kp"], "ki_pos": POSITION_GAINS["ki"],
            "kd_pos": POSITION_GAINS["kd"],
        }

    # ------------------------------------------------------------------
    def set_controller_mode(self, mode: str):
        """Set the current controller mode (called from run_collins_profile)."""
        self._controller_mode = mode

    # ------------------------------------------------------------------
    def log(self, tau_Nm: float = 0.0, current_cmd_mA: float = 0.0):
        """Write one row reflecting the boot's current state and the
        most recent commanded torque/current."""
        b = self.boot
        if self._t0 is None:
            self._t0 = time.time()

        # Track stride index for easy per-stride grouping in analysis
        if b.num_gait != self._last_num_gait:
            self._stride_idx += 1
            self._last_num_gait = b.num_gait

        armed_time = (b.current_time - b.armed_timestamp
                      if b.armed_timestamp != -1 else -1)
        if b.expected_duration > 0:
            refr = max(MIN_STRIDE_PERIOD,
                       REFRACTORY_FRACTION * b.expected_duration)
            refr = min(refr, REFRACTORY_MAX)
        else:
            refr = MIN_STRIDE_PERIOD

        m = self._meta_static
        row = [
            f"{time.time() - self._t0:.4f}",
            b.current_time,
            self._iter,
            m["participant_id"], m["side"], m["boot_id"], m["phase"],
            m["weight_kg"], m["test_mode"], m["approach"],
            b.t_rise, b.t_fall, b.t_peak, b.peak_torque_norm,
            m["kp_cur"], m["ki_cur"], m["kd_cur"], m["ff_cur"],
            m["kp_pos"], m["ki_pos"], m["kd_pos"],
            b.accelx, b.accely, b.accelz,
            b.gyrox, b.gyroy, b.gyroz,
            b.motorTicksRaw, b.motorTicksZeroed,
            getattr(b, "motorVelocity", 0), b.motorCurrent,
            getattr(b, "motorVoltage", 0),
            b.ankleTicksRaw, b.ankleTicksZeroed,
            b.ankleVelocity,
            b.ankleVel_filt[0] if b.ankleVel_filt else 0,
            getattr(b, "battVoltage", 0),
            getattr(b, "battCurrent", 0),
            getattr(b, "temperature", 0),
            getattr(b, "status_mn", 0),
            getattr(b, "status_ex", 0),
            getattr(b, "status_re", 0),
            self._controller_mode,
            b._gains_mode if b._gains_mode else "none",
            f"{tau_Nm:.4f}", f"{current_cmd_mA:.1f}",
            f"{b.wm_wa:.3f}",
            getattr(b, "motor_pos_setpoint", 0),
            (getattr(b, "motor_pos_setpoint", 0) - b.motorTicksRaw)
            if getattr(b, "motor_pos_setpoint", None) is not None else 0,
            b.num_gait, f"{b.percent_gait:.2f}",
            f"{b.expected_duration:.0f}", f"{b.current_duration:.0f}",
            int(b.heelstrike_armed),
            armed_time, f"{refr:.0f}",
            int(b.segmentation_trigger),
            f"{b.segmentation_arm_threshold:.0f}",
            f"{b.segmentation_trigger_threshold:.0f}",
            self._stride_idx,
        ]
        self.w.writerow(row)
        self._iter += 1

    # ------------------------------------------------------------------
    def close(self):
        try:
            self.fh.flush()
            self.fh.close()
        except Exception:
            pass

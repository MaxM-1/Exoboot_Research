"""
ExoBoot Initialisation & Real‑Time Control
===========================================

Wraps the Dephy FlexSEA ``Device`` class (Actuator‑Package ≥ 12.1.0) for
the EB‑60 ExoBoot running **firmware 7.2.0** (legacy mode).

Ported from Xiangyu Peng's ``Exo_Init.py`` with the following changes:

* Old procedural API (``fxOpen``, ``fxReadDevice``, …) replaced by the
  object‑oriented ``Device`` class.
* ``read_data()`` unpacks the dictionary returned by ``Device.read()``
  (legacy devices return a dict keyed by the YAML spec‑file field names).
* ``set_gains`` is called only when the control mode actually *changes*
  (position ↔ current) to avoid the 5 ms retry overhead on every
  iteration.
* Constructor does **not** block on ``input()`` — encoder check is
  automatic and results are reported via a ``status_callback``.

Author:  Max Miller — Auburn University
"""

import os
import numpy as np
from math import sqrt
from time import sleep
import configparser
from scipy.signal import butter

from flexsea.device import Device

from config import (
    FIRMWARE_VERSION, STREAMING_FREQUENCY, LOG_LEVEL,
    LEFT, RIGHT,
    ZEROING_CURRENT, NO_SLACK_CURRENT, PEAK_CURRENT,
    TICKS_TO_ANGLE_COEFF, ANGLE_TO_TICKS_COEFF, BIT_TO_GYRO_COEFF,
    NUM_GAIT_TIMES_TO_AVERAGE, ARMED_DURATION_PERCENT,
    HEELSTRIKE_THRESHOLD_ABOVE, HEELSTRIKE_THRESHOLD_BELOW,
    MIN_STRIDE_PERIOD, REFRACTORY_FRACTION, REFRACTORY_MAX,
    STRIDE_OUTLIER_FACTOR, MIN_ARMED_DURATION,
    MAX_ARMED_FRACTION, MAX_ARMED_MS,
    CURRENT_GAINS, POSITION_GAINS,
)


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------
def tick_to_angle(ticks):
    return ticks * TICKS_TO_ANGLE_COEFF

def nm_to_mnm(torque):
    """Nm  → mNm"""
    return torque * 1000.0

def a_to_ma(current):
    """A   → mA"""
    return current * 1000.0


# ===========================================================================
#                              ExoBoot Class
# ===========================================================================
class ExoBoot:
    """High‑level wrapper around a single Dephy ExoBoot."""

    # -----------------------------------------------------------------
    #  Construction & Connection
    # -----------------------------------------------------------------
    def __init__(
        self,
        side: int,
        port: str,
        firmware_version: str = FIRMWARE_VERSION,
        frequency: int = STREAMING_FREQUENCY,
        log_level: int = LOG_LEVEL,
        status_callback=None,
    ):
        """
        Create an ``ExoBoot`` object, connect to the device, and start
        streaming sensor data.

        Parameters
        ----------
        side : int
            ``LEFT`` (1) or ``RIGHT`` (−1).
        port : str
            Serial port, e.g. ``'/dev/ttyACM0'``.
        firmware_version : str
            Firmware version string (default ``'7.2.0'``).
        frequency : int
            Streaming frequency in Hz.
        log_level : int
            C‑library log verbosity (0 = most verbose, 6 = off).
        status_callback : callable, optional
            ``callback(message: str)`` used to emit progress messages
            (forwarded to the GUI log).  Falls back to ``print``.
        """
        self.side = side
        self.port = port
        self.firmware_version = firmware_version
        self.frequency = frequency
        self.log_level = log_level
        self.log = status_callback if status_callback else print

        # ---- Connect -------------------------------------------------
        self.log(f"Connecting to ExoBoot on {port} …")
        self.device = Device(
            firmwareVersion=self.firmware_version,
            port=self.port,
            logLevel=self.log_level,
            interactive=False,
        )
        self.device.open()
        sleep(1)
        self.device.start_streaming(frequency=self.frequency)
        sleep(0.1)
        # Force motor to a known-zero state so no stale command persists
        # from a previous session or power-cycle.
        try:
            self.device.command_motor_current(0)
            sleep(0.05)
        except Exception:
            pass

        # Flush any stale data sitting in the USB/serial buffer from a
        # prior session so the first real read_data() is fresh.
        for _ in range(10):
            try:
                self.device.read()
            except Exception:
                pass
            sleep(0.005)
        self.log(f"ExoBoot connected — ID {self.device.id}")

        # ---- Gait state ---------------------------------------------
        self.num_gait: int = 0
        self.num_gait_in_block: int = 0
        self.percent_gait: float = -1

        self.past_stride_times = [-1] * NUM_GAIT_TIMES_TO_AVERAGE
        self.expected_duration: float = -1

        self.segmentation_trigger: bool = False
        self.heelstrike_armed: bool = False
        self.segmentation_arm_threshold = HEELSTRIKE_THRESHOLD_ABOVE
        self.segmentation_trigger_threshold = HEELSTRIKE_THRESHOLD_BELOW

        self.current_duration: float = -1
        self.heelstrike_timestamp_current: float = -1
        self.heelstrike_timestamp_previous: float = -1
        self.armed_timestamp: float = -1

        # ---- Debug counters ------------------------------------------
        self._dbg_count: int = 0
        self._dbg_gz_min: float = 0.0
        self._dbg_gz_max: float = 0.0
        self._dbg_armed_events: int = 0
        self._dbg_trigger_events: int = 0

        # ---- Sensor cache --------------------------------------------
        self.current_time: int = -1
        self.accelx: int = 0
        self.accely: int = 0
        self.accelz: int = 0
        self.gyrox: int = 0
        self.gyroy: int = 0
        self.gyroz: int = 0

        self.motorTicksRaw: int = 0
        self.motorTicksZeroed: int = 0
        self.motorTicksOffset: int = 0
        self.motorCurrent: int = 0

        self.ankleTicksRaw: int = 0
        self.ankleTicksZeroed: int = 0
        self.ankle_ticks_offset: int = 0
        self.ankleTicksAbsZeroed: int = 0
        self.ankleVelocity: int = 0

        self.tau: float = 0
        self.current: float = 0

        # ---- Collins torque‑profile ----------------------------------
        self.t_rise: float = -1
        self.t_fall: float = -1
        self.t_peak: float = -1
        self.weight: float = -1
        self.peak_torque_norm: float = -1
        self.peak_torque: float = -1

        self.a1: float = 0
        self.b1: float = 0
        self.c1: float = 0
        self.d1: float = 0
        self.a2: float = 0
        self.b2: float = 0
        self.c2: float = 0
        self.d2: float = 0

        # ---- Low‑pass filter (2nd‑order Butterworth, 12 Hz) ----------
        self.sampling_freq = 100    #changing to 100
        self.b_filt, self.a_filt = butter(
            2, 12 / (self.sampling_freq / 2), "low"
        )
        self.ankleVel = [0.0] * 3
        self.ankleVel_filt = [0.0] * 3

        # ---- Position‑control kinematic coeffs -----------------------
        self.kinematicCoeffs = np.array([-400, 0])
        self.magnitude = 40 * 45.5111

        # ---- Motor constant ------------------------------------------
        self.kt = 0.14  # Nm / A (q‑axis)

        # ---- Gains‑mode tracker (avoid re‑sending same gains) --------
        self._gains_mode: str | None = None   # 'current' | 'position'

        # ---- Calibration data ----------------------------------------
        self._load_calibration()

        # ---- First data read -----------------------------------------
        self.read_data()
        self.heelstrike_timestamp_current = self.current_time

    # -----------------------------------------------------------------
    #  Load calibration polynomial from bootCal.txt
    # -----------------------------------------------------------------
    def _load_calibration(self):
        cal_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "calibration", "bootCal.txt",
        )
        cfg = configparser.ConfigParser()
        cfg.read(cal_path)

        side_key = "left" if self.side == LEFT else "right"
        self.boot_id = cfg.get("ids", side_key)

        self.ankle_ticks_abs_offset_plantar = cfg.getint(
            self.boot_id, "ankle_reading_55_deg"
        )
        self.ankle_ticks_abs_offset = (
            self.ankle_ticks_abs_offset_plantar
            - self.side * 55 * ANGLE_TO_TICKS_COEFF
        )

        # Polynomial coefficients  (4th‑degree: poly4·x⁴ … poly0·x)
        self.wm_wa_coeffs = [
            cfg.getfloat(self.boot_id, f"poly{i}") for i in range(4, -1, -1)
        ]
        self.wm_wa: float = 0.0

        self.ank_mot_coeffs = list(self.wm_wa_coeffs) #+ [0.0]
        # The 6th element is the constant offset, set by encoder_check.

        # ---- Working ankle range (written by calibration_analysis2.py) ----
        # The polynomial is only valid between ank_min and ank_max — the
        # bounds of the engaged-sweep region during bench calibration.
        # Outside this range the 4th-order fit extrapolates badly, so we
        # clamp the ankle input before evaluating polyval and its derivative.
        # If ank_min/ank_max are missing from the cal file (legacy entries)
        # we fall back to (-inf, +inf) and skip clamping for backward
        # compatibility.
        try:
            self.ank_min = cfg.getint(self.boot_id, "ank_min")
            self.ank_max = cfg.getint(self.boot_id, "ank_max")
            self.log(
                f"Calibrated ankle range: [{self.ank_min}, {self.ank_max}]"
            )
        except (configparser.NoOptionError, ValueError):
            self.ank_min = float("-inf")
            self.ank_max = float("inf")
            self.log(
                "WARNING: ank_min/ank_max not found in bootCal.txt — "
                "polynomial may extrapolate during walking."
            )

    # -----------------------------------------------------------------
    #  Initialise (zero + encoder‑check) — call after construction
    # -----------------------------------------------------------------
    def initialize(self):
        """Zero the boot and align the encoder polynomial.

        Call this **after** construction and before running any torque
        profile.  The participant should be standing still.
        """
        self.log("Stand still — zeroing boot …")
        self.zero_boot()
        self.encoder_check()
        self.log("Boot initialised.\n")

    # -----------------------------------------------------------------
    #  Read sensor data
    # -----------------------------------------------------------------
    def read_data(self):
        """Read one sample from the device and update all cached fields."""
        data = self.device.read()

        self.current_time = data.get("state_time", 0)
        self.accelx = data.get("accelx", 0)
        self.accely = data.get("accely", 0)
        self.accelz = data.get("accelz", 0)
        self.gyrox = data.get("gyrox", 0)
        self.gyroy = data.get("gyroy", 0)
        self.gyroz = data.get("gyroz", 0) * self.side

        self.motorTicksRaw = data.get("mot_ang", 0)
        self.motorTicksZeroed = self.side * (
            self.motorTicksRaw - self.motorTicksOffset
        )
        self.motorCurrent = data.get("mot_cur", 0)

        self.ankleTicksRaw = data.get("ank_ang", 0)
        self.ankleTicksZeroed = self.side * (
            self.ankleTicksRaw - self.ankle_ticks_offset
        )
        self.ankleTicksAbsZeroed = self.side * (
            self.ankleTicksRaw - self.ankle_ticks_abs_offset
        )
        self.ankleVelocity = data.get("ank_vel", 0)

        # ---- Velocity low‑pass filter --------------------------------
        self.ankleVel.pop()
        self.ankleVel.insert(0, self.ankleVelocity)
        y_new = self._lpfilter(
            self.ankleVel, self.ankleVel_filt, self.a_filt, self.b_filt
        )
        self.ankleVel_filt.pop()
        self.ankleVel_filt.insert(0, y_new)

        # ---- Gait segmentation ---------------------------------------
        self._heelstrike_detect()
        if self.segmentation_trigger:
            self.heelstrike_timestamp_previous = self.heelstrike_timestamp_current
            self.heelstrike_timestamp_current = self.current_time
            self._update_expected_duration()

        self._percent_gait_calc()
        self._calc_wm_wa()

    # -----------------------------------------------------------------
    #  Heel‑strike detection
    # -----------------------------------------------------------------
    def _heelstrike_detect(self):
        triggered = False
        armed_time = 0

        # ---- Debug tracking ------------------------------------------
        self._dbg_count += 1
        if self.gyroz < self._dbg_gz_min:
            self._dbg_gz_min = self.gyroz
        if self.gyroz > self._dbg_gz_max:
            self._dbg_gz_max = self.gyroz

        # Periodic sensor report (every ~1 s for the first 15 s)
        if self._dbg_count % 1000 == 0 and self._dbg_count <= 15000:
            side_str = "L" if self.side == LEFT else "R"
            self.log(
                f"  [{side_str} DBG @{self._dbg_count}] "
                f"gz={self.gyroz:.0f}  "
                f"min/max={self._dbg_gz_min:.0f}/{self._dbg_gz_max:.0f}  "
                f"arm_thr={self.segmentation_arm_threshold:.0f}  "
                f"trg_thr={self.segmentation_trigger_threshold:.0f}  "
                f"armed={self.heelstrike_armed}  "
                f"HS={self.num_gait}  "
                f"exp_dur={self.expected_duration}"
            )
        # ---- end debug -----------------------------------------------

        # ---- ARM check (runs OUTSIDE the refractory gate) ---------
        #      The boot must be allowed to arm during swing even while
        #      the refractory window blocks triggers — otherwise a
        #      growing refractory can swallow the ARM event and cause
        #      missed heel‑strikes → runaway expected_duration.
        if (self.gyroz >= self.segmentation_arm_threshold
                and not self.heelstrike_armed):
            self.heelstrike_armed = True
            self.armed_timestamp = self.current_time
            self._dbg_armed_events += 1
            if self._dbg_armed_events <= 20:
                side_str = "L" if self.side == LEFT else "R"
                self.log(
                    f"  [{side_str} ARM] gz={self.gyroz:.0f}  "
                    f"t={self.current_time}"
                )

        if self.armed_timestamp != -1:
            armed_time = self.current_time - self.armed_timestamp

        # ---- Max‑armed expiry: if armed longer than 55 % of stride,
        #      this ARM event came from contralateral vibration (e.g.
        #      opposite foot's toe‑off).  Disarm so we can re‑arm on
        #      the correct swing‑phase event. ----------------------
        if self.heelstrike_armed:
            max_armed = (MAX_ARMED_FRACTION * self.expected_duration
                         if self.expected_duration > 0
                         else MAX_ARMED_MS)
            if armed_time > max_armed:
                self.heelstrike_armed = False
                self.armed_timestamp = -1

        # ---- Refractory period: block contralateral cross‑talk ----
        #      Only the TRIGGER is gated — ARM was handled above.
        if self.expected_duration > 0:
            refractory = max(MIN_STRIDE_PERIOD,
                             REFRACTORY_FRACTION * self.expected_duration)
            refractory = min(refractory, REFRACTORY_MAX)   # hard cap
        else:
            refractory = MIN_STRIDE_PERIOD
        time_since_last_hs = self.current_time - self.heelstrike_timestamp_current
        if time_since_last_hs < refractory:
            self.segmentation_trigger = False
            return

        if (self.heelstrike_armed
                and self.gyroz <= self.segmentation_trigger_threshold):
            # Use the larger of the percentage‑based threshold and the
            # absolute floor so the very first strides aren't noise.
            threshold = max(
                ARMED_DURATION_PERCENT / 100.0 * self.expected_duration,
                MIN_ARMED_DURATION,
            )
            self._dbg_trigger_events += 1
            if armed_time > threshold:
                # ---- VALID ipsilateral heel‑strike ----
                triggered = True
                self.heelstrike_armed = False
                self.armed_timestamp = -1
                self.num_gait += 1
                self.num_gait_in_block += 1
                side_str = "Left" if self.side == LEFT else "Right"
                self.log(
                    f"  {side_str} heel‑strike #{self.num_gait}"
                    f"  (armed_time={armed_time:.0f}  "
                    f"thresh={threshold:.1f}  "
                    f"exp_dur={self.expected_duration:.1f})"
                )
            else:
                # ---- Too short: cross‑talk spike.  Stay armed so
                #      the real ipsilateral HS a few hundred ms later
                #      still finds the boot in the armed state. ----
                if self._dbg_trigger_events <= 20:
                    side_str = "L" if self.side == LEFT else "R"
                    self.log(
                        f"  [{side_str} TRG HOLD] "
                        f"armed_time={armed_time:.0f} "
                        f"<= thresh={threshold:.1f}  "
                        f"exp_dur={self.expected_duration}"
                    )

        self.segmentation_trigger = triggered

    # -----------------------------------------------------------------
    #  Gait‑percentage calculation
    # -----------------------------------------------------------------
    def _percent_gait_calc(self):
        if self.expected_duration != -1:
            elapsed = self.current_time - self.heelstrike_timestamp_current
            self.percent_gait = 100.0 * elapsed / self.expected_duration
        if self.percent_gait > 100:
            self.percent_gait = 100

    # -----------------------------------------------------------------
    #  Stride‑duration estimator
    # -----------------------------------------------------------------
    def _update_expected_duration(self):
        self.current_duration = (
            self.heelstrike_timestamp_current
            - self.heelstrike_timestamp_previous
        )

        # Reject obviously invalid durations (< 400 ms ≈ 150 steps/min
        # or > 3000 ms ≈ 20 steps/min) — this also discards the bogus
        # first "stride" from reset‑time to the first real heel‑strike
        # when the gap is too long.
        if self.current_duration < 400 or self.current_duration > 3000:
            return

        if self.heelstrike_timestamp_previous == -1:
            self.heelstrike_timestamp_previous = self.heelstrike_timestamp_current
            return

        if -1 in self.past_stride_times:
            # Buffer still filling — accept any reasonable duration.
            self.past_stride_times.insert(0, self.current_duration)
            self.past_stride_times.pop()
        elif (self.expected_duration > 0
              and (1.0 / STRIDE_OUTLIER_FACTOR) * self.expected_duration
                  <= self.current_duration
                  <= STRIDE_OUTLIER_FACTOR * self.expected_duration):
            # Within ±30 % of current estimate — accept.
            self.past_stride_times.insert(0, self.current_duration)
            self.past_stride_times.pop()
        # else: outlier — silently rejected, buffer unchanged.

        # Use *median* for robustness: a single outlier that slips in
        # during initial fill cannot corrupt the estimate.
        valid = sorted(t for t in self.past_stride_times if t != -1)
        if valid:
            self.expected_duration = valid[len(valid) // 2]

    # -----------------------------------------------------------------
    #  Motor‑to‑ankle velocity ratio  (derivative of 4th‑order poly)
    # used to be 5th order poly but changing to 4th order poly on 4/21 following ROM position testing (first test trial)
    # -----------------------------------------------------------------
    def _calc_wm_wa(self):
        """ Derivative of the ankle→motor polynomial (4th-order).
        Given the polynomial P(x) = c[0]·x^4 + c[1]·x^3 + c[2]·x^2 + c[3]·x + c[4],
        the derivative (motor-to-ankle velocity ratio) is:
        P'(x) = 4·c[0]·x^3 + 3·c[1]·x^2 + 2·c[2]·x + c[3]

        Clamps ankle to the calibrated range [ank_min, ank_max] before
        evaluating, so that walking-time excursions outside the cal
        sweep don't drive wm_wa to unphysical values.
        """
        x = max(self.ank_min, min(self.ank_max, self.ankleTicksRaw))
        c = self.wm_wa_coeffs
        self.wm_wa = (
            4 * c[0] * x**3
            + 3 * c[1] * x**2
            + 2 * c[2] * x
            + c[3]
        )
        if self.wm_wa <= 0.5:          # safety clamp
            self.wm_wa = 1.0

    # -----------------------------------------------------------------
    #  Torque → motor current
    # -----------------------------------------------------------------
    def ankle_torque_to_current(self, torque_mnm):
        """Convert ankle torque (mNm) to Dephy motor current (A)."""
        q_axis_current = (torque_mnm / self.wm_wa) / 1000.0 / self.kt   # A
        dephy_current = q_axis_current * sqrt(2) / 0.537
        return dephy_current  # A

    # -----------------------------------------------------------------
    #  Collins torque‑profile coefficients
    # -----------------------------------------------------------------
    def init_collins_profile(
        self,
        t_rise=None, t_fall=None, t_peak=None,
        weight=None, peak_torque_norm=None,
    ):
        """(Re)compute the cubic‑spline coefficients for the ascending
        and descending segments of the Collins torque profile."""
        if t_rise is not None:
            self.t_rise = t_rise
        if t_fall is not None:
            self.t_fall = t_fall
        if t_peak is not None:
            self.t_peak = t_peak
        if weight is not None:
            self.weight = weight
        if peak_torque_norm is not None:
            self.peak_torque_norm = peak_torque_norm

        # Check all parameters are set
        if -1 in (self.t_rise, self.t_fall, self.t_peak,
                   self.weight, self.peak_torque_norm):
            self.log(
                "WARNING — Collins parameter missing: "
                f"t_rise={self.t_rise}  t_fall={self.t_fall}  "
                f"t_peak={self.t_peak}  weight={self.weight}  "
                f"peak_torque_norm={self.peak_torque_norm}"
            )
            return

        self.peak_torque = self.peak_torque_norm * self.weight
        onset_torque = 0.0
        t0 = self.t_peak - self.t_rise      # actuation start (%)
        t1 = self.t_peak + self.t_fall       # actuation end   (%)

        # Ascending cubic (t0 → t_peak)
        self.a1 = (2 * (onset_torque - self.peak_torque)) / (self.t_rise ** 3)
        self.b1 = (3 * (self.peak_torque - onset_torque) * (self.t_peak + t0)) / (self.t_rise ** 3)
        self.c1 = (6 * (onset_torque - self.peak_torque) * self.t_peak * t0) / (self.t_rise ** 3)
        self.d1 = (
            self.t_peak ** 3 * onset_torque
            - 3 * t0 * self.t_peak ** 2 * onset_torque
            + 3 * t0 ** 2 * self.t_peak * self.peak_torque
            - t0 ** 3 * self.peak_torque
        ) / (self.t_rise ** 3)

        # Descending cubic (t_peak → t1)
        self.a2 = (self.peak_torque - onset_torque) / (2 * self.t_fall ** 3)
        self.b2 = (3 * (onset_torque - self.peak_torque) * t1) / (2 * self.t_fall ** 3)
        self.c2 = (
            3 * (self.peak_torque - onset_torque)
            * (-self.t_peak ** 2 + 2 * t1 * self.t_peak)
        ) / (2 * self.t_fall ** 3)
        self.d2 = (
            2 * self.peak_torque * t1 ** 3
            - 6 * self.peak_torque * t1 ** 2 * self.t_peak
            + 3 * self.peak_torque * t1 * self.t_peak ** 2
            + 3 * onset_torque * t1 * self.t_peak ** 2
            - 2 * onset_torque * self.t_peak ** 3
        ) / (2 * self.t_fall ** 3)

    # -----------------------------------------------------------------
    #  Execute one iteration of the Collins torque profile
    # -----------------------------------------------------------------
    def run_collins_profile(self):
        """Read data and send the appropriate motor command for the
        current gait phase.  Call this once per control‑loop iteration."""
        self.read_data()
        # ---- Phase‑level debug (every ~1 s) --------------------------
        if self._dbg_count % 1000 == 500 and self._dbg_count <= 15000:
            side_str = "L" if self.side == LEFT else "R"
            self.log(
                f"  [{side_str} CMD @{self._dbg_count}] "
                f"pg={self.percent_gait:.1f}%  "
                f"tau={self.tau:.2f}Nm  "
                f"cur={self.current:.0f}mA  "
                f"exp_dur={self.expected_duration:.0f}  "
                f"HS={self.num_gait}  "
                f"wm_wa={self.wm_wa:.2f}"
            )
        # ---- end debug -----------------------------------------------

        # Before gait cadence is established, keep chain loaded with
        # position control tracking the ankle polynomial.
        if self.percent_gait < 0:
            self._set_position_gains()
            motor_target = self._desired_motor_position()
            self.device.command_motor_position(int(motor_target))
            return

        t_onset = self.t_peak - self.t_rise   # actuation start (%)

        # Phase 1 — Early stance  (0 % → t_onset):  position control
        # Track the ankle-to-motor polynomial to keep chain loaded
        # without applying torque.
        if 0 <= self.percent_gait <= t_onset:
            self._set_position_gains()
            motor_target = self._desired_motor_position()
            self.device.command_motor_position(int(motor_target))

        # Phase 2 — Ascending curve  (t_onset → t_peak):  torque ramp up
        elif t_onset < self.percent_gait <= self.t_peak:
            self._set_current_gains()
            pg = self.percent_gait
            self.tau = (self.a1 * pg**3 + self.b1 * pg**2
                        + self.c1 * pg + self.d1)
            self.current = a_to_ma(
                self.ankle_torque_to_current(nm_to_mnm(self.tau))
            )
            self.current = max(min(self.current, PEAK_CURRENT), NO_SLACK_CURRENT)
            self.device.command_motor_current(int(self.current * self.side))

        # Phase 3 — Descending curve  (t_peak → t_peak+t_fall): ramp down
        elif self.t_peak < self.percent_gait <= self.t_peak + self.t_fall:
            self._set_current_gains()
            pg = self.percent_gait
            self.tau = (self.a2 * pg**3 + self.b2 * pg**2
                        + self.c2 * pg + self.d2)
            self.current = a_to_ma(
                self.ankle_torque_to_current(nm_to_mnm(self.tau))
            )
            self.current = max(min(self.current, PEAK_CURRENT), NO_SLACK_CURRENT)
            self.device.command_motor_current(int(self.current * self.side))

        # Phase 4 — Late stance / swing  (t_peak+t_fall → 100 %):  position control
        elif self.percent_gait > self.t_peak + self.t_fall:
            self._set_position_gains()
            motor_target = self._desired_motor_position()
            self.device.command_motor_position(int(motor_target))

    # -----------------------------------------------------------------
    #  Gain‑mode helpers  (avoid redundant set_gains calls)
    # -----------------------------------------------------------------
    def _set_current_gains(self):
        if self._gains_mode != "current":
            self.device.set_gains(**CURRENT_GAINS)
            self._gains_mode = "current"

    def _set_position_gains(self):
        # Use soft gains when ankle is outside the calibrated range
        if (self.ankleTicksRaw < self.ank_min or 
            self.ankleTicksRaw > self.ank_max):
            if self._gains_mode != "position_soft":
                self.device.set_gains(kp=10, ki=0, kd=0, k=0, b=0, ff=0)
                self._gains_mode = "position_soft"
        else:
                if self._gains_mode != "position":
                    self.device.set_gains(**POSITION_GAINS)
                    self._gains_mode = "position"
                    
    # -----------------------------------------------------------------
    #  Desired motor position from ankle angle
    # -----------------------------------------------------------------
    def _desired_motor_position(self):
        ank_clamped = max(self.ank_min, min(self.ank_max, self.ankleTicksRaw))
        motor_angle = np.floor(
            np.polyval(self.ank_mot_coeffs, ank_clamped)
            - self.side * self.magnitude
            - (self.kinematicCoeffs[0] * self.ankleVel_filt[0]
                + self.kinematicCoeffs[1])
        )
        return motor_angle

    # -----------------------------------------------------------------
    #  Encoder check  (automatic — no user input)
    # -----------------------------------------------------------------
    def encoder_check(self):
        """Align the ankle→motor polynomial constant to the current
        position.  Should be called while the participant stands still
        with the belt tight."""
        self.log(f"Encoder check (boot {self.device.id}) …")
        self.device.set_gains(**POSITION_GAINS)
        self._gains_mode = "position"
        sleep(0.5)

        for i in range(3):
            data = self.device.read()
            self.log(f"  reading {i}: mot_ang = {data.get('mot_ang', '?')}")

        data = self.device.read()
        initial_ankle = data.get("ank_ang", 0)
        initial_motor = data.get("mot_ang", 0)

        initial_motor_des = np.floor(
            np.polyval(self.ank_mot_coeffs, initial_ankle)
        )
        zeroing = initial_motor - initial_motor_des
        self.ank_mot_coeffs[-1] += zeroing
        initial_motor_shift = np.floor(
            np.polyval(self.ank_mot_coeffs, initial_ankle)
        )

        self.log(f"  ankle  = {initial_ankle}")
        self.log(f"  motor actual  = {initial_motor}")
        self.log(f"  motor desired = {initial_motor_des}")
        self.log(f"  offset        = {zeroing}")
        self.log(f"  motor shifted = {initial_motor_shift}")
        self.log("  Encoder check OK ✓")

        self.device.stop_motor()
        self._gains_mode = None

    # -----------------------------------------------------------------
    #  Reset gait‑detection state
    # -----------------------------------------------------------------
    def reset_gait_state(self):
        """Clear all gait‑segmentation state so the next heel‑strike
        detection starts fresh.  Call this right before entering a
        control loop if there was a long idle period (e.g. between
        Connect & Zero and Start Familiarization).

        Also resets the low-pass filter history and gains-mode tracker
        so no stale values bleed across experiment phases.
        """
        self.read_data()                    # get a fresh timestamp
        self.num_gait = 0
        self.num_gait_in_block = 0
        self.percent_gait = -1
        self.past_stride_times = [-1] * NUM_GAIT_TIMES_TO_AVERAGE
        self.expected_duration = -1
        self.current_duration = -1
        self.segmentation_trigger = False
        self.heelstrike_armed = False
        self.armed_timestamp = -1
        # Use -1 so the first "stride" (reset-time → first real HS)
        # produces a huge duration that the >3000 ms sanity check
        # rejects.  Real stride timing starts from the second HS.
        self.heelstrike_timestamp_current = -1
        self.heelstrike_timestamp_previous = -1

        # Clear low-pass filter history so old velocity samples don't
        # leak into the next phase.
        self.ankleVel = [0.0] * 3
        self.ankleVel_filt = [0.0] * 3

        # Clear torque / current accumulators
        self.tau = 0.0
        self.current = 0.0

        # Force gains to be re-sent on the next control iteration
        self._gains_mode = None

        # ---- Debug counters (reset each phase) -----------------------
        self._dbg_count: int = 0
        self._dbg_gz_min: float = 0.0
        self._dbg_gz_max: float = 0.0
        self._dbg_armed_events: int = 0
        self._dbg_trigger_events: int = 0

    # -----------------------------------------------------------------
    #  Zero boot (tighten belt & record encoder offsets)
    # -----------------------------------------------------------------
    def zero_boot(self):
        """Tighten the belt and zero the encoder offsets."""
        self.log("Zeroing boot — tightening belt …")
        self.device.set_gains(**CURRENT_GAINS)
        self._gains_mode = "current"
        sleep(0.5)
        self.device.command_motor_current(ZEROING_CURRENT * self.side)
        sleep(3)
        self._zero_encoders()
        self.device.stop_motor()
        self._gains_mode = None

    def _zero_encoders(self):
        self.read_data()
        self.motorTicksOffset = self.motorTicksRaw
        self.ankle_ticks_offset = self.ankleTicksRaw

    # -----------------------------------------------------------------
    #  Low‑pass filter  (2nd‑order IIR, direct‑form)
    # -----------------------------------------------------------------
    @staticmethod
    def _lpfilter(x, ypast, a, b):
        return (-(a[1] * ypast[0] + a[2] * ypast[1])
                + b[0] * x[0] + b[1] * x[1] + b[2] * x[2])

    # -----------------------------------------------------------------
    #  Simple current ramp (utility)
    # -----------------------------------------------------------------
    def current_control(self, current_ma, duration_s):
        """Apply *current_ma* for *duration_s* seconds, then stop."""
        n_iter = int(duration_s * 10)
        self.device.set_gains(**CURRENT_GAINS)
        self._gains_mode = "current"
        sleep(0.5)
        for _ in range(n_iter):
            self.device.command_motor_current(current_ma * self.side)
            sleep(0.1)
        self.device.stop_motor()
        self._gains_mode = None
        sleep(0.5)

    # -----------------------------------------------------------------
    #  Print status (debugging)
    # -----------------------------------------------------------------
    def print_status(self):
        print(f"  time            = {self.current_time}")
        print(f"  expected_dur    = {self.expected_duration}")
        print(f"  stride_times    = {self.past_stride_times}")
        print(f"  current sent    = {self.current}")
        print(f"  motor current   = {self.motorCurrent}")
        print(f"  gait #          = {self.num_gait}")
        print(f"  percent_gait    = {self.percent_gait:.1f}")

    # -----------------------------------------------------------------
    #  Clean‑up
    # -----------------------------------------------------------------
    def clean(self):
        """Safely shut down: zero motor, stop streaming, close port.

        Sends zero-current twice with a short delay to make sure the
        firmware registers the command even if the first write is lost
        on the USB bus.  Then stops streaming and closes the port so
        no stale state persists for the next session.
        """
        try:
            self.device.command_motor_current(0)
            sleep(0.05)
            self.device.command_motor_current(0)
            sleep(0.05)
        except Exception:
            pass
        try:
            self.device.stop_motor()
            sleep(0.1)
        except Exception:
            pass
        try:
            self.device.stop_streaming()
            sleep(0.05)
        except Exception:
            pass
        try:
            self.device.close()
        except Exception:
            pass
        self._gains_mode = None

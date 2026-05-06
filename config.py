"""
Configuration for the Rise/Fall Time Perception Experiment.
Dephy ExoBoot controller — Max Miller, Auburn University.

All tuneable constants live here so the rest of the codebase can
simply ``from config import *``.
"""

# ==============================================================================
# FlexSEA / Hardware
# ==============================================================================
FIRMWARE_VERSION = "7.2.0"          # Legacy ExoBoot firmware
BAUD_RATE = 230400                  # Default Dephy baud rate
LEFT_PORT = "/dev/ttyACM0"
RIGHT_PORT = "/dev/ttyACM1"
STREAMING_FREQUENCY = 100          # Hz - Used to be 1000 then changed to 100  
LOG_LEVEL = 6                       # 0 = most verbose, 6 = off

# ==============================================================================
# Boot Side Constants
# ==============================================================================
LEFT = 1
RIGHT = -1

# ==============================================================================
# Current Limits (mA)
# ==============================================================================
ZEROING_CURRENT = 1000              # 1800 -> 1000
NO_SLACK_CURRENT = 800              # 1200 -> 800
PEAK_CURRENT = 28000                #4_28 changed from 28000 to 15000 | 5_2 changing from 15000 to 26000 then back to 15000
WARMUP_CURRENT = 600                # Light current during warm‑up strides

# ==============================================================================
# Unit Conversions
# ==============================================================================
TICKS_TO_ANGLE_COEFF = 0.02197      # degrees per tick  (360 / 2^14)
ANGLE_TO_TICKS_COEFF = 1.0 / TICKS_TO_ANGLE_COEFF
BIT_TO_GYRO_COEFF = 1.0 / 32.8

# ==============================================================================
# Gait‑Segmentation (Heel‑Strike Detection)
# ==============================================================================
NUM_GAIT_TIMES_TO_AVERAGE = 5      # 4_29 changed 3 -> 5 (treadmill ramp-up makes
                                    # first ~2 strides/foot longer than steady state;
                                    # 5-sample median is robust to that and to
                                    # occasional missed HS outliers)
ARMED_DURATION_PERCENT = 10
HEELSTRIKE_THRESHOLD_ABOVE = 100 / BIT_TO_GYRO_COEFF    # ≈ 3280  (was 150 → 4920)
HEELSTRIKE_THRESHOLD_BELOW = -100 / BIT_TO_GYRO_COEFF   # ≈ −3280 (4_29 was -150 → -4920;
                                    # MAX4 logs showed real HS dips reaching only
                                    # -3776 to -4869, so old -4920 missed ~30 %
                                    # of strides for heavier walkers)
MIN_STRIDE_PERIOD = 650            # ms — absolute‑minimum refractory period
REFRACTORY_FRACTION = 0.60         # dynamic refractory = 60 % of expected stride
REFRACTORY_MAX = 850               # ms — hard cap so refractory can never grow
                                    #      large enough to block ipsilateral ARM
STRIDE_OUTLIER_FACTOR = 1.3        # accept strides within ±30 % of expected
MIN_ARMED_DURATION = 100           # ms — floor for armed‑time check
MAX_ARMED_FRACTION = 0.65          # disarm if armed > 65 % of expected stride | 0.55->0.65
MAX_ARMED_MS = 900                 # ms — absolute cap when expected_duration unknown | 600->900

# ==============================================================================
# Collins Torque‑Profile Defaults  (% of gait cycle)
# ==============================================================================
# Actuation start and end times are HELD CONSTANT throughout the perception
# experiment.  Only the peak time slides between them; rise and fall
# durations are derived from t_peak so that there is never a flat region
# at the top of the torque curve.
T_ACT_START = 26.0                  # Actuation‑start timing (% gait) — CONSTANT
T_ACT_END   = 61.6                  # Actuation‑end   timing (% gait) — CONSTANT
DEFAULT_T_RISE = 25.3               # Reference rise time  (= T_PEAK_REF - T_ACT_START)
DEFAULT_T_FALL = 10.3               # Reference fall time  (= T_ACT_END   - T_PEAK_REF)
DEFAULT_T_PEAK = T_ACT_START + DEFAULT_T_RISE   # 51.3 % — reference peak time
DEFAULT_T_ONSET = T_ACT_START       # Backwards-compat alias
MIN_RISE = 2.0                      # Minimum rise duration (% gait) — clamp guard
MIN_FALL = 2.0                      # Minimum fall duration (% gait) — clamp guard
DEFAULT_PEAK_TORQUE_NORM = 0.225    # Normalised peak torque  (Nm / kg)
        #4_28 changed from 0.225 to 0.12
        #5_2 changed from 0.12 to 0.20 then back to 0.12

# ------------------------------------------------------------------
# Per-experiment familiarization peak torque (Nm/kg)
# ------------------------------------------------------------------
# MAX experiment varies t_peak; the torque magnitude during MAX
# familiarization & MAX perception is held at 0.225 Nm/kg (legacy).
# SAV experiment varies peak torque; familiarization & the SAV
# reference both sit at the published Collins value of 0.18 Nm/kg.
MAX_FAM_PEAK_TN = 0.225
SAV_FAM_PEAK_TN = 0.18
# ==============================================================================
# PID Gains
# ==============================================================================
#CURRENT_GAINS = {"kp": 100, "ki": 32, "kd": 0, "k": 0, "b": 0, "ff": 0} old values
#CURRENT_GAINS = {"kp": 40, "ki": 400, "kd": 0, "k": 0, "b": 0, "ff": 128} pre-5_2
CURRENT_GAINS = {"kp": 40, "ki": 250, "kd": 0, "k": 0, "b": 0, "ff": 128} #post5_2 changes


#POSITION_GAINS = {"kp": 175, "ki": 50, "kd": 0, "k": 0, "b": 0, "ff": 0} old values 

POSITION_GAINS = {"kp": 100, "ki": 20, "kd": 35, "k": 0, "b": 0, "ff": 0}
#above are prev gains used in walking trial - commenting out for now for ROM_position test2 import

#POSITION_GAINS = {"kp": 100, "ki": 0, "kd": 35, "k": 0, "b": 0, "ff": 0} #gain adjustment for ROM position test 2


# ==============================================================================
# Perception‑Test Protocol
# ==============================================================================
# MAX experiment (peak‑time staircase) — values in % stride period.
MAX_DELTA = 1.0                     # Adaptive step‑size (% stride period)
MAX_INITIAL_OFFSET = 3.0            # Starting offset from reference (% stride)
MAX_TOTAL_SWEEPS = 9                # Sweeps per approach direction
MAX_REST_STRIDES = 8                # Rest strides between trials (≈ 8 s)
MAX_FAM_DELTA = 1.0                 # Manual fam. adjustment step (% stride)

# SAV experiment (peak‑torque staircase) — values in Nm/kg.
SAV_DELTA = 0.01                   # Adaptive step‑size (Nm/kg) [0.005->0.01 on 5_6]
SAV_INITIAL_OFFSET = 0.05           # Starting offset from reference (Nm/kg)
SAV_TOTAL_SWEEPS = 9                # Sweeps per approach direction
SAV_REST_STRIDES = 8               # Rest strides between trials (≈ 15 s) [15->8 on 5_6]
SAV_FAM_DELTA = 0.005               # Manual fam. adjustment step (Nm/kg)
SAV_REFERENCE_PEAK_TN = SAV_FAM_PEAK_TN  # Reference torque for SAV staircase
SAV_MIN_PEAK_TN = 0.05              # Lower clamp on staircase value (Nm/kg)
SAV_MAX_PEAK_TN = 0.30              # Upper clamp on staircase value (Nm/kg)

# Legacy aliases (back-compat — old callers / tests).  These mirror MAX.
DELTA = MAX_DELTA
INITIAL_OFFSET = MAX_INITIAL_OFFSET
TOTAL_SWEEPS = MAX_TOTAL_SWEEPS
REST_STRIDES = MAX_REST_STRIDES
FAMILIARIZATION_DELTA = MAX_FAM_DELTA

# Shared protocol constants (both experiments).
STRIDES_PER_CONDITION = 5           # Strides per condition inside a trial
TOTAL_STRIDES_PER_TRIAL = 10        # 2 × STRIDES_PER_CONDITION
TOTAL_TRIALS_MAX = 55               # Hard upper limit on trial count
CATCH_TRIAL_DENOMINATOR = 4         # 1 / 4 → 25 % catch‑trial rate
WARMUP_STRIDES = 10                 # Light‑current warm‑up strides
WARMUP_AUGMENTED_STRIDES = 10       # Collins‑profile warm‑up strides
NUM_PRACTICE_TRIALS = 2             # Practice trials before real recording

# ==============================================================================
# Experiment Modes
# ==============================================================================
# The perception test now varies a single quantity — the peak time t_peak
# — with rise/fall durations derived to keep actuation start and end
# constant.  RISE_TIME_TEST / FALL_TIME_TEST are kept as legacy aliases
# (treated as PEAK_TIME_TEST) for backwards compatibility with old data
# files; new code should use PEAK_TIME_TEST.
PEAK_TIME_TEST = "peak_time"
RISE_TIME_TEST = PEAK_TIME_TEST     # legacy alias
FALL_TIME_TEST = PEAK_TIME_TEST     # legacy alias
APPROACH_FROM_ABOVE = "from_above"
APPROACH_FROM_BELOW = "from_below"

# ------------------------------------------------------------------
# Experiment type — selects which Collins parameter the perception
# staircase varies.
#   MAX  → peak time   (t_peak, % gait)        — varies timing
#   SAV  → peak torque (peak_torque_norm, Nm/kg) — varies magnitude
# ------------------------------------------------------------------
MAX_EXPERIMENT = "max"
SAV_EXPERIMENT = "sav"
DEFAULT_EXPERIMENT = MAX_EXPERIMENT

# ==============================================================================
# GUI ↔ Controller Signal Constants
# ==============================================================================
STOP_SIGNAL = 0
FAMILIARIZATION_BEGIN_SIGNAL = 1
PERCEPTION_TEST_BEGIN_SIGNAL = 2
INCREASE_SIGNAL = 4
DECREASE_SIGNAL = 5
DIFFERENCE_RESPONSE = 6
SAME_RESPONSE = 7

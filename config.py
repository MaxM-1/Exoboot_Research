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
DEFAULT_T_RISE = 25.3               # Rise time  (reference value)
DEFAULT_T_FALL = 10.3               # Fall time  (reference value)
DEFAULT_T_ONSET = 26.0              # Actuation‑start timing
DEFAULT_PEAK_TORQUE_NORM = 0.225    # Normalised peak torque  (Nm / kg)
        #4_28 changed from 0.225 to 0.12
        #5_2 changed from 0.12 to 0.20 then back to 0.12
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
DELTA = 1.0                         # Adaptive step‑size (% stride period)
INITIAL_OFFSET = 3.0                # Starting offset from reference
STRIDES_PER_CONDITION = 5           # Strides per condition inside a trial
TOTAL_STRIDES_PER_TRIAL = 10        # 2 × STRIDES_PER_CONDITION
TOTAL_SWEEPS = 9                    # Sweeps per approach direction
TOTAL_TRIALS_MAX = 55               # Hard upper limit on trial count
CATCH_TRIAL_DENOMINATOR = 4         # 1 / 4 → 25 % catch‑trial rate
REST_STRIDES = 8                    # Rest strides between trials
WARMUP_STRIDES = 10                 # Light‑current warm‑up strides
WARMUP_AUGMENTED_STRIDES = 10       # Collins‑profile warm‑up strides
NUM_PRACTICE_TRIALS = 2             # Practice trials before real recording
FAMILIARIZATION_DELTA = 1.0         # Step for manual fam. adjustment

# ==============================================================================
# Experiment Modes
# ==============================================================================
RISE_TIME_TEST = "rise_time"
FALL_TIME_TEST = "fall_time"
APPROACH_FROM_ABOVE = "from_above"
APPROACH_FROM_BELOW = "from_below"

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

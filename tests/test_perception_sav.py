"""Tests for SAV-mode (peak-torque staircase) support in
``perception_test._StaircaseVar``.

These exercise only the pure-Python staircase abstraction — no
hardware, no FlexSEA.  They lock in:

* the experiment-type dispatch (MAX vs SAV) reads the right config
  constants;
* clamping respects per-mode bounds;
* ``profile_args`` produces the right ``(t_peak, peak_tn)`` pair
  (SAV holds t_peak constant; MAX holds peak_tn constant);
* ``_make_profile`` accepts a per-trial ``peak_tn`` while keeping
  T_ACT_START / T_ACT_END constant.
"""
from config import (
    DEFAULT_T_PEAK,
    MAX_DELTA, MAX_EXPERIMENT, MAX_FAM_PEAK_TN, MAX_INITIAL_OFFSET,
    MAX_REST_STRIDES, MAX_TOTAL_SWEEPS,
    SAV_DELTA, SAV_EXPERIMENT, SAV_FAM_PEAK_TN, SAV_INITIAL_OFFSET,
    SAV_MAX_PEAK_TN, SAV_MIN_PEAK_TN, SAV_REFERENCE_PEAK_TN,
    SAV_REST_STRIDES, SAV_TOTAL_SWEEPS,
    T_ACT_END, T_ACT_START,
)
from perception_test import PerceptionExperiment, _StaircaseVar


# ---------------------------------------------------------------------------
#  MAX dispatch
# ---------------------------------------------------------------------------
def test_max_var_uses_max_constants():
    v = _StaircaseVar(MAX_EXPERIMENT)
    assert v.experiment_type == MAX_EXPERIMENT
    assert v.reference == DEFAULT_T_PEAK
    assert v.delta == MAX_DELTA
    assert v.initial_offset == MAX_INITIAL_OFFSET
    assert v.total_sweeps == MAX_TOTAL_SWEEPS
    assert v.rest_strides == MAX_REST_STRIDES
    assert v.fixed_peak_tn == MAX_FAM_PEAK_TN
    assert v.label == "t_peak"
    assert v.units == "% gait"


def test_max_profile_args_holds_peak_tn_fixed():
    v = _StaircaseVar(MAX_EXPERIMENT)
    t_peak, peak_tn = v.profile_args(50.0)
    assert t_peak == 50.0
    assert peak_tn == MAX_FAM_PEAK_TN


def test_max_clamp_uses_perception_clamp_peak():
    v = _StaircaseVar(MAX_EXPERIMENT)
    # Out-of-range values clamp to the rise/fall guards.
    assert v.clamp(0.0) == PerceptionExperiment._clamp_peak(0.0)
    assert v.clamp(1000.0) == PerceptionExperiment._clamp_peak(1000.0)


# ---------------------------------------------------------------------------
#  SAV dispatch
# ---------------------------------------------------------------------------
def test_sav_var_uses_sav_constants():
    v = _StaircaseVar(SAV_EXPERIMENT)
    assert v.experiment_type == SAV_EXPERIMENT
    assert v.reference == SAV_REFERENCE_PEAK_TN
    assert v.delta == SAV_DELTA
    assert v.initial_offset == SAV_INITIAL_OFFSET
    assert v.total_sweeps == SAV_TOTAL_SWEEPS
    assert v.rest_strides == SAV_REST_STRIDES
    assert v.fam_value == SAV_FAM_PEAK_TN
    assert v.label == "peak_tn"
    assert v.units == "Nm/kg"


def test_sav_profile_args_holds_t_peak_constant():
    v = _StaircaseVar(SAV_EXPERIMENT)
    t_peak, peak_tn = v.profile_args(0.20)
    # SAV must hold the timing constant — only the magnitude varies.
    assert t_peak == DEFAULT_T_PEAK
    assert peak_tn == 0.20


def test_sav_clamp_respects_min_max_peak_tn():
    v = _StaircaseVar(SAV_EXPERIMENT)
    assert v.clamp(0.0) == SAV_MIN_PEAK_TN
    assert v.clamp(10.0) == SAV_MAX_PEAK_TN
    assert v.clamp(SAV_REFERENCE_PEAK_TN) == SAV_REFERENCE_PEAK_TN


def test_sav_initial_offset_step_is_ten_deltas():
    """The protocol starts the SAV staircase at ±0.05 Nm/kg from the
    reference and steps by 0.005 Nm/kg — initial offset should equal
    exactly 10 ``DELTA`` increments."""
    v = _StaircaseVar(SAV_EXPERIMENT)
    assert v.initial_offset == 10 * v.delta


# ---------------------------------------------------------------------------
#  _make_profile with per-trial peak_tn
# ---------------------------------------------------------------------------
def test_make_profile_accepts_per_trial_peak_tn():
    """SAV varies peak_torque_norm per trial while holding t_peak
    constant — _make_profile must round-trip whatever peak_tn it
    receives without altering timing."""
    exp = PerceptionExperiment()

    p_lo = exp._make_profile(t_peak=DEFAULT_T_PEAK, weight=80.0,
                             peak_tn=0.13)
    p_hi = exp._make_profile(t_peak=DEFAULT_T_PEAK, weight=80.0,
                             peak_tn=0.23)

    # Timing identical
    assert p_lo["t_peak"] == p_hi["t_peak"] == DEFAULT_T_PEAK
    assert p_lo["t_rise"] == p_hi["t_rise"] == DEFAULT_T_PEAK - T_ACT_START
    assert p_lo["t_fall"] == p_hi["t_fall"] == T_ACT_END - DEFAULT_T_PEAK
    # Magnitude differs
    assert p_lo["peak_torque_norm"] == 0.13
    assert p_hi["peak_torque_norm"] == 0.23


# ---------------------------------------------------------------------------
#  Experiment-type fallback
# ---------------------------------------------------------------------------
def test_unknown_experiment_type_falls_back_to_max():
    v = _StaircaseVar("nonsense")
    assert v.experiment_type == MAX_EXPERIMENT

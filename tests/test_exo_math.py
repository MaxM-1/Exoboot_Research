import pytest

from config import (
    DEFAULT_PEAK_TORQUE_NORM,
    DEFAULT_T_FALL,
    DEFAULT_T_ONSET,
    DEFAULT_T_RISE,
    NUM_GAIT_TIMES_TO_AVERAGE,
    TICKS_TO_ANGLE_COEFF,
)
from exo_init import ExoBoot, a_to_ma, nm_to_mnm, tick_to_angle


def bare_boot():
    boot = ExoBoot.__new__(ExoBoot)
    boot.log = lambda *args, **kwargs: None
    return boot


def test_unit_conversions():
    assert tick_to_angle(100) == pytest.approx(100 * TICKS_TO_ANGLE_COEFF)
    assert nm_to_mnm(2.5) == pytest.approx(2500.0)
    assert a_to_ma(1.25) == pytest.approx(1250.0)


def test_lpfilter_direct_form_calculation():
    x = [10.0, 5.0, 1.0]
    ypast = [2.0, -4.0, 0.0]
    a = [1.0, -0.5, 0.25]
    b = [0.2, 0.3, 0.4]

    expected = -((a[1] * ypast[0]) + (a[2] * ypast[1]))
    expected += b[0] * x[0] + b[1] * x[1] + b[2] * x[2]

    assert ExoBoot._lpfilter(x, ypast, a, b) == pytest.approx(expected)


def test_calc_wm_wa_uses_polynomial_derivative():
    boot = bare_boot()
    boot.ankleTicksRaw = 2.0
    boot.wm_wa_coeffs = [1.0, 2.0, 3.0, 4.0, 5.0]

    boot._calc_wm_wa()

    assert boot.wm_wa == pytest.approx(72.0)


def test_calc_wm_wa_clamps_small_or_negative_values():
    boot = bare_boot()
    boot.ankleTicksRaw = 2.0
    boot.wm_wa_coeffs = [0.0, 0.0, 0.0, 0.0, 5.0]

    boot._calc_wm_wa()

    assert boot.wm_wa == pytest.approx(1.0)


def test_stride_duration_uses_median_and_rejects_outlier():
    boot = bare_boot()
    boot.past_stride_times = [-1] * NUM_GAIT_TIMES_TO_AVERAGE
    boot.expected_duration = -1

    for duration_ms in (1000, 1100, 900):
        boot.heelstrike_timestamp_previous = 0
        boot.heelstrike_timestamp_current = duration_ms
        boot._update_expected_duration()

    assert boot.expected_duration == 1000
    before = list(boot.past_stride_times)

    boot.heelstrike_timestamp_previous = 0
    boot.heelstrike_timestamp_current = 2000
    boot._update_expected_duration()

    assert boot.past_stride_times == before
    assert boot.expected_duration == 1000


def test_collins_profile_hits_expected_boundaries():
    boot = bare_boot()
    weight = 80.0
    t_peak = DEFAULT_T_ONSET + DEFAULT_T_RISE

    boot.init_collins_profile(
        t_rise=DEFAULT_T_RISE,
        t_fall=DEFAULT_T_FALL,
        t_peak=t_peak,
        weight=weight,
        peak_torque_norm=DEFAULT_PEAK_TORQUE_NORM,
    )

    t_onset = t_peak - DEFAULT_T_RISE
    t_end = t_peak + DEFAULT_T_FALL
    peak_torque = weight * DEFAULT_PEAK_TORQUE_NORM

    rise_start = boot.a1 * t_onset**3 + boot.b1 * t_onset**2 + boot.c1 * t_onset + boot.d1
    rise_peak = boot.a1 * t_peak**3 + boot.b1 * t_peak**2 + boot.c1 * t_peak + boot.d1
    fall_peak = boot.a2 * t_peak**3 + boot.b2 * t_peak**2 + boot.c2 * t_peak + boot.d2
    fall_end = boot.a2 * t_end**3 + boot.b2 * t_end**2 + boot.c2 * t_end + boot.d2

    assert boot.peak_torque == pytest.approx(peak_torque)
    assert rise_start == pytest.approx(0.0, abs=1e-9)
    assert rise_peak == pytest.approx(peak_torque)
    assert fall_peak == pytest.approx(peak_torque)
    assert fall_end == pytest.approx(0.0, abs=1e-9)

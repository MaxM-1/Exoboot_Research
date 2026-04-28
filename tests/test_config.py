import config


def test_current_limits_are_ordered():
    assert 0 < config.WARMUP_CURRENT <= config.NO_SLACK_CURRENT
    assert config.NO_SLACK_CURRENT <= config.ZEROING_CURRENT
    assert config.ZEROING_CURRENT < config.PEAK_CURRENT


def test_profile_timing_fits_inside_stride():
    t_peak = config.DEFAULT_T_ONSET + config.DEFAULT_T_RISE
    t_end = t_peak + config.DEFAULT_T_FALL

    assert 0 < config.DEFAULT_T_ONSET < t_peak < t_end < 100
    assert config.DEFAULT_PEAK_TORQUE_NORM > 0


def test_gait_detection_thresholds_are_sane():
    assert config.LEFT == 1
    assert config.RIGHT == -1
    assert config.HEELSTRIKE_THRESHOLD_ABOVE > 0
    assert config.HEELSTRIKE_THRESHOLD_BELOW < 0
    assert 0 < config.REFRACTORY_FRACTION < 1
    assert config.MIN_STRIDE_PERIOD < config.REFRACTORY_MAX


def test_gui_signal_values_are_unique():
    signals = [
        config.STOP_SIGNAL,
        config.FAMILIARIZATION_BEGIN_SIGNAL,
        config.PERCEPTION_TEST_BEGIN_SIGNAL,
        config.INCREASE_SIGNAL,
        config.DECREASE_SIGNAL,
        config.DIFFERENCE_RESPONSE,
        config.SAME_RESPONSE,
    ]

    assert len(signals) == len(set(signals))

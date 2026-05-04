import queue

from config import (
    DEFAULT_T_PEAK,
    MIN_FALL,
    MIN_RISE,
    T_ACT_END,
    T_ACT_START,
)
from perception_test import PerceptionExperiment, _pad


def test_make_profile_at_reference_peak():
    exp = PerceptionExperiment()

    profile = exp._make_profile(
        t_peak=DEFAULT_T_PEAK,
        weight=75.0,
        peak_tn=0.2,
    )

    assert profile["t_peak"] == DEFAULT_T_PEAK
    assert profile["t_rise"] == DEFAULT_T_PEAK - T_ACT_START
    assert profile["t_fall"] == T_ACT_END - DEFAULT_T_PEAK
    assert profile["weight"] == 75.0
    assert profile["peak_torque_norm"] == 0.2


def test_make_profile_couples_rise_and_fall():
    """Sliding t_peak should keep actuation start/end constant; only
    rise and fall durations change in opposite directions."""
    exp = PerceptionExperiment()

    p_low = exp._make_profile(t_peak=DEFAULT_T_PEAK - 3.0,
                              weight=70.0, peak_tn=0.2)
    p_hi = exp._make_profile(t_peak=DEFAULT_T_PEAK + 3.0,
                             weight=70.0, peak_tn=0.2)

    # Actuation start & end recovered from t_peak ± rise/fall
    assert abs((p_low["t_peak"] - p_low["t_rise"]) - T_ACT_START) < 1e-9
    assert abs((p_low["t_peak"] + p_low["t_fall"]) - T_ACT_END) < 1e-9
    assert abs((p_hi["t_peak"] - p_hi["t_rise"]) - T_ACT_START) < 1e-9
    assert abs((p_hi["t_peak"] + p_hi["t_fall"]) - T_ACT_END) < 1e-9
    # Sliding peak later → longer rise, shorter fall
    assert p_hi["t_rise"] > p_low["t_rise"]
    assert p_hi["t_fall"] < p_low["t_fall"]


def test_clamp_peak_respects_min_rise_fall():
    assert PerceptionExperiment._clamp_peak(0.0) == T_ACT_START + MIN_RISE
    assert PerceptionExperiment._clamp_peak(1000.0) == T_ACT_END - MIN_FALL
    assert PerceptionExperiment._clamp_peak(DEFAULT_T_PEAK) == DEFAULT_T_PEAK


def test_pad_extends_all_lists_to_same_length():
    data = {"a": [1], "b": [2, 3], "c": []}

    _pad(data)

    assert data == {"a": [1, ""], "b": [2, 3], "c": ["", ""]}


def test_pad_handles_empty_dict():
    data = {}

    _pad(data)

    assert data == {}


def test_flush_cmd_discards_pending_commands():
    exp = PerceptionExperiment()
    exp.command_queue.put("old")
    exp.command_queue.put("also-old")

    exp._flush_cmd()

    try:
        exp.command_queue.get_nowait()
    except queue.Empty:
        pass
    else:
        raise AssertionError("command queue should have been empty")


def test_status_helpers_enqueue_messages():
    exp = PerceptionExperiment()

    exp._send("state", value="Ready")
    exp._log("hello")

    assert exp.status_queue.get_nowait() == {"type": "state", "value": "Ready"}
    assert exp.status_queue.get_nowait() == {"type": "log", "message": "hello"}

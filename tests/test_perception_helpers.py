import queue

from config import (
    DEFAULT_T_FALL,
    DEFAULT_T_ONSET,
    DEFAULT_T_RISE,
    FALL_TIME_TEST,
    RISE_TIME_TEST,
)
from perception_test import PerceptionExperiment, _pad


def test_make_profile_for_rise_time():
    exp = PerceptionExperiment()

    profile = exp._make_profile(
        value=30.0,
        test_mode=RISE_TIME_TEST,
        weight=75.0,
        peak_tn=0.2,
    )

    assert profile == {
        "t_rise": 30.0,
        "t_fall": DEFAULT_T_FALL,
        "t_peak": DEFAULT_T_ONSET + 30.0,
        "weight": 75.0,
        "peak_torque_norm": 0.2,
    }


def test_make_profile_for_fall_time():
    exp = PerceptionExperiment()

    profile = exp._make_profile(
        value=12.0,
        test_mode=FALL_TIME_TEST,
        weight=70.0,
        peak_tn=0.25,
    )

    assert profile == {
        "t_rise": DEFAULT_T_RISE,
        "t_fall": 12.0,
        "t_peak": DEFAULT_T_ONSET + DEFAULT_T_RISE,
        "weight": 70.0,
        "peak_torque_norm": 0.25,
    }


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

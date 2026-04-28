import configparser

import numpy as np
import pandas as pd
import pytest

from calibration import calibration_analysis_3
from data import data_analysis


def test_detect_reversals_marks_direction_changes():
    values = np.array([13.0, 12.0, 11.0, 12.0, 13.0, 12.0])

    mask = data_analysis._detect_reversals(values)

    assert mask.tolist() == [False, False, False, True, False, True]


def test_compute_jnd_uses_reversal_values():
    values = np.array([13.0, 12.0, 11.0, 12.0, 13.0, 12.0, 11.0, 12.0, 13.0])

    mean, std = data_analysis.compute_jnd(values, discard_first=0)

    assert mean == pytest.approx(12.0)
    assert std == pytest.approx(0.0)


def test_load_csv_accepts_new_and_legacy_column_names(tmp_path):
    new_csv = tmp_path / "new.csv"
    legacy_csv = tmp_path / "legacy.csv"
    pd.DataFrame({"ank_ang": [1, 2], "mot_ang": [3, 4]}).to_csv(new_csv, index=False)
    pd.DataFrame({"ankle_ticks": [5, 6], "motor_ticks": [7, 8]}).to_csv(
        legacy_csv, index=False
    )

    ankle, motor = calibration_analysis_3.load_csv(new_csv)
    assert ankle.tolist() == [1.0, 2.0]
    assert motor.tolist() == [3.0, 4.0]

    ankle, motor = calibration_analysis_3.load_csv(legacy_csv)
    assert ankle.tolist() == [5.0, 6.0]
    assert motor.tolist() == [7.0, 8.0]


def test_trim_startup_drops_first_samples_when_possible():
    ankle = np.arange(70)
    motor = np.arange(70) * 2

    trimmed_ankle, trimmed_motor = calibration_analysis_3.trim_startup(
        ankle, motor, skip_samples=50
    )

    assert trimmed_ankle.tolist() == list(range(50, 70))
    assert trimmed_motor.tolist() == [x * 2 for x in range(50, 70)]


def test_trim_startup_keeps_short_arrays():
    ankle = np.arange(55)
    motor = np.arange(55) * 2

    trimmed_ankle, trimmed_motor = calibration_analysis_3.trim_startup(
        ankle, motor, skip_samples=50
    )

    assert trimmed_ankle.tolist() == ankle.tolist()
    assert trimmed_motor.tolist() == motor.tolist()


def test_fit_recovers_fourth_order_polynomial():
    true_coeffs = np.array([2.0, -3.0, 4.0, 0.5, 7.0])
    ankle = np.linspace(-2, 2, 9)
    motor = np.polyval(true_coeffs, ankle)

    coeffs, unique_ankle, unique_motor = calibration_analysis_3.fit(ankle, motor)

    assert np.allclose(coeffs, true_coeffs)
    assert unique_ankle.tolist() == ankle.tolist()
    assert unique_motor.tolist() == motor.tolist()


def test_update_bootcal_preserves_other_side_and_writes_coefficients(tmp_path):
    cal_path = tmp_path / "bootCal.txt"
    cal_path.write_text(
        "[ids]\n"
        "left = OLDL\n"
        "right = OLDR\n\n"
        "[OLDR]\n"
        "ankle_reading_55_deg = 123\n"
        "poly4 = 1\n"
        "poly3 = 2\n"
        "poly2 = 3\n"
        "poly1 = 4\n"
        "poly0 = 5\n"
    )
    coeffs = np.array([10.0, 20.0, 30.0, 40.0, 50.0])

    calibration_analysis_3.update_bootcal(
        coeffs=coeffs,
        side="left",
        boot_id="C719",
        ankle_55=8003,
        cal_path=cal_path,
    )

    cfg = configparser.ConfigParser()
    cfg.read(cal_path)

    assert cfg.get("ids", "left") == "C719"
    assert cfg.get("ids", "right") == "OLDR"
    assert cfg.getint("C719", "ankle_reading_55_deg") == 8003
    assert cfg.getfloat("C719", "poly4") == pytest.approx(10.0)
    assert cfg.getfloat("C719", "poly0") == pytest.approx(50.0)
    assert cfg.has_section("OLDR")

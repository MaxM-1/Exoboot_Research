import csv
from types import SimpleNamespace

from config import LEFT
from exo_logger import HEADER, ExoLogger


def fake_boot():
    return SimpleNamespace(
        side=LEFT,
        boot_id="C719",
        device=SimpleNamespace(id="C719"),
        t_rise=25.3,
        t_fall=10.3,
        t_peak=51.3,
        peak_torque_norm=0.225,
        accelx=1,
        accely=2,
        accelz=3,
        gyrox=4,
        gyroy=5,
        gyroz=6,
        motorTicksRaw=100,
        motorTicksZeroed=10,
        motorCurrent=200,
        ankleTicksRaw=300,
        ankleTicksZeroed=30,
        ankleVelocity=40,
        ankleVel_filt=[41.0, 0.0, 0.0],
        _gains_mode="current",
        wm_wa=2.5,
        num_gait=0,
        percent_gait=12.5,
        expected_duration=1000,
        current_duration=980,
        heelstrike_armed=False,
        armed_timestamp=-1,
        current_time=12345,
        segmentation_trigger=False,
        segmentation_arm_threshold=3280,
        segmentation_trigger_threshold=-4920,
    )


def test_exo_logger_writes_header_and_row(tmp_path):
    boot = fake_boot()
    logger = ExoLogger(
        out_dir=str(tmp_path),
        participant_id="PTEST",
        boot=boot,
        phase="Unit",
        params={
            "user_weight": "75",
            "test_mode": "rise_time",
            "approach": "from_above",
        },
    )

    logger.set_controller_mode("cur_ramp_up")
    logger.log(tau_Nm=1.2345, current_cmd_mA=678.9)
    logger.close()

    with open(logger.path, newline="") as fh:
        rows = list(csv.reader(fh))

    assert rows[0] == HEADER
    assert len(rows) == 2

    row = dict(zip(HEADER, rows[1]))
    assert row["participant_id"] == "PTEST"
    assert row["side"] == "L"
    assert row["boot_id"] == "C719"
    assert row["phase"] == "Unit"
    assert row["controller_mode"] == "cur_ramp_up"
    assert row["tau_Nm"] == "1.2345"
    assert row["current_cmd_mA"] == "678.9"

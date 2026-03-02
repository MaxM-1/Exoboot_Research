# Exoboot Rise/Fall Time Perception Experiment

Controller and experiment platform for studying human perception of rise and fall time parameters in the Dephy ExoBoot ankle exoskeleton torque profile. Adapted from Xiangyu Peng's actuation-timing perception work, using the current FlexSEA Python API and a PyQt5 GUI (replacing Peng's Android app).

## Hardware Setup

- Raspberry Pi 5 (16 GB RAM, Ubuntu)
- Two Dephy ExoBoots (left + right) connected via long USB 2.0 cables
- Serial ports: typically `/dev/ttyACM0` (left) and `/dev/ttyACM1` (right)
- Firmware: 7.2.0, baud rate: 230400

## Project Structure

| File | Purpose |
|------|---------|
| `config.py` | All constants: hardware settings, torque profile defaults, PID gains, protocol parameters |
| `exo_init.py` | `ExoBoot` class — device wrapper for FlexSEA, gait detection, torque profile execution |
| `perception_test.py` | `PerceptionExperiment` class — adaptive staircase protocol, familiarization, trial logic |
| `gui.py` | PyQt5 GUI — experiment control, participant responses, status display, safe motor shutdown on close |
| `calibration/boot_calibration.py` | Collect ankle-angle vs motor-angle calibration data |
| `calibration/calibration_analysis.py` | Fit 5th-order polynomial from calibration data, write `bootCal.txt` |
| `calibration/bootCal.txt` | Calibration coefficients loaded by `ExoBoot` at startup |
| `data/data_analysis.py` | Post-experiment analysis: JND computation, staircase plots |
| `requirements.txt` | Python dependencies |
| `RESOURCES/` | Reference materials: Dephy API source, Peng's original controller, user guides, papers |

## Setup

```bash
pip install -r requirements.txt
```

On Raspberry Pi 5, install PyQt5 via apt instead of pip for reliable ARM64 support:

```bash
sudo apt install python3-pyqt5
```

FlexSEA must be installed separately from the Dephy Actuator-Package (see `RESOURCES/Actuator-Package-develop/` or the [Dephy GitHub](https://github.com/DephyInc/Actuator-Package)).

## Workflow

### 1. Calibrate Boots

Calibration maps ankle angle to motor angle with the belt tightened. Start with the shoe fully **plantarflexed** and belt tight, then slowly **dorsiflex** (this direction only — reversing loosens the belt and breaks the mapping).

```bash
# Collect data (one boot at a time)
python calibration/boot_calibration.py --port /dev/ttyACM0 --side left --collect-time 15

# Fit polynomial and write bootCal.txt
python calibration/calibration_analysis.py --csv calibration/left_boot_calib_<timestamp>.csv --side left --boot-id C719 --ankle-55 6856
```

Repeat for the right boot. The analysis script appends to `calibration/bootCal.txt`.

### 2. Run Experiment

```bash
python gui.py
```

1. Enter participant ID, weight, firmware version, serial ports, and test mode (rise-time or fall-time, approach from above or below)
2. Click **Connect & Zero** — connects to both boots and zeros encoders
3. Click **Start Familiarization** — participant walks while adjusting rise/fall time to find a comfortable reference
4. Click **Start Perception Test** — adaptive staircase runs automatically; participant responds "Same" or "Different" each trial

### 3. Analyze Results

```bash
# Single run
python data/data_analysis.py --csv data/P001_perception_rise_above.csv

# Combine above + below for full JND
python data/data_analysis.py --csv data/P001_rise_above.csv data/P001_rise_below.csv --combine
```

## Configuration

All tunable parameters live in `config.py`:

- **Serial ports and baud rate** — `LEFT_PORT`, `RIGHT_PORT`, `BAUD_RATE`
- **Torque profile** — `DEFAULT_RISE_TIME`, `DEFAULT_FALL_TIME`, `PEAK_TORQUE_NM_KG`
- **PID gains** — `CURRENT_KP/KI`, `POSITION_KP/KI`
- **Protocol** — `INITIAL_OFFSET_PCT`, `STEP_SIZE_PCT`, `STRIDES_PER_CONDITION`, `CATCH_TRIAL_RATE`, `MAX_TRIALS`
- **Current limits** — `ZEROING_CURRENT_MA`, `PEAK_CURRENT_MA`

## Data Output

| Location | Contents |
|----------|----------|
| `DataLog/` | High-frequency sensor logs (CSV) from each session |
| `data/` | Trial-level perception results and familiarization logs |
| `calibration/` | Calibration CSVs, diagnostic plots, `bootCal.txt` |

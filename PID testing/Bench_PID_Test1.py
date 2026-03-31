"""Bench-test PID gains on a single boot (no walking required)."""
from time import sleep, time
from flexsea.device import Device
from config import FIRMWARE_VERSION, LOG_LEVEL, CURRENT_GAINS

PORT = "/dev/ttyACM0"  # adjust as needed
STREAM_FREQ = 100
TEST_CURRENT_MA = 500   # small safe current for testing
TEST_DURATION_S = 3

dev = Device(firmwareVersion=FIRMWARE_VERSION, port=PORT,
             logLevel=LOG_LEVEL, interactive=False)
dev.open()
sleep(1)
dev.start_streaming(frequency=STREAM_FREQ)
sleep(0.5)

# Try different gains here without editing config.py
test_gains = {"kp": 40, "ki": 400, "kd": 0, "k": 0, "b": 0, "ff": 0}
dev.set_gains(**test_gains)
dev.command_motor_current(TEST_CURRENT_MA)

t0 = time()
while time() - t0 < TEST_DURATION_S:
    data = dev.read()
    actual = data.get("mot_cur", 0)
    print(f"Commanded: {TEST_CURRENT_MA}  Actual: {actual}")
    sleep(1.0 / STREAM_FREQ)

dev.command_motor_current(0)
sleep(0.1)
dev.stop_streaming()
dev.close()
print("Done.")

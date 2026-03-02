"""
Perception Test — Adaptive Staircase Controller
=================================================

Runs the rise‑ or fall‑time perception experiment on two Dephy
ExoBoots.  Designed to be launched in a **background thread** by the
GUI (``gui.py``); all communication with the GUI happens through two
``queue.Queue`` objects.

Adapted from Xiangyu Peng's ``Timing_Perception_Adaptive_Always.py``
with the following major changes:

* The parameter under test is **rise time** or **fall time** (not
  actuation‑onset timing).
* Socket communication replaced by thread‑safe queues.
* The Android‑app state machine replaced by signal constants shared
  with the tkinter GUI.
* The ``ExoBoot`` class uses the new FlexSEA ``Device`` API (fw 7.2.0).

Author:  Max Miller — Auburn University
"""

import os
import queue
import random
import threading
from time import sleep, strftime

import pandas as pd

from config import *
from exo_init import ExoBoot


class PerceptionExperiment:
    """Manages boot connections and runs experiment protocols."""

    def __init__(self):
        # Queues shared with the GUI
        self.command_queue: queue.Queue = queue.Queue()
        self.status_queue: queue.Queue = queue.Queue()

        # Threading
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        # Boot handles (created on start)
        self.left_boot: ExoBoot | None = None
        self.right_boot: ExoBoot | None = None

        # Experiment parameters (set by GUI before start)
        self.params: dict = {}

    # ==================================================================
    #  Status helpers
    # ==================================================================
    def _send(self, msg_type: str, **kwargs):
        self.status_queue.put({"type": msg_type, **kwargs})

    def _log(self, message: str):
        self._send("log", message=message)

    # ==================================================================
    #  Command helpers
    # ==================================================================
    def _check_cmd(self):
        """Non‑blocking check for a single GUI command."""
        try:
            return self.command_queue.get_nowait()
        except queue.Empty:
            return None

    def _flush_cmd(self):
        """Discard stale commands in the queue."""
        while not self.command_queue.empty():
            try:
                self.command_queue.get_nowait()
            except queue.Empty:
                break

    # ==================================================================
    #  Public API  (called from the GUI thread)
    # ==================================================================
    def start(self, params: dict):
        """Launch the experiment in a daemon thread.

        ``params`` must contain at least::

            participant_id : str
            user_weight    : float   (kg)
            test_mode      : str     RISE_TIME_TEST | FALL_TIME_TEST
            approach        : str     APPROACH_FROM_ABOVE | APPROACH_FROM_BELOW
            mode           : str     'familiarization' | 'perception'
            left_port      : str
            right_port     : str
            firmware       : str
        """
        self.stop_event.clear()
        self.params = params
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def request_stop(self):
        self.stop_event.set()
        self.command_queue.put(STOP_SIGNAL)

    # ==================================================================
    #  Background‑thread entry point
    # ==================================================================
    def _run(self):
        try:
            self._connect_and_init()

            if self.stop_event.is_set():
                self._cleanup()
                return

            # Signal the GUI that boots are connected & zeroed
            self._send("connected")

            mode = self.params.get("mode", "connect_only")
            if mode == "connect_only":
                # Wait for the user to start the treadmill, then press
                # Start Familiarization or Start Perception Test
                self._wait_for_start_command()
            elif mode == "familiarization":
                self._run_familiarization()
            else:
                self._run_perception()
        except Exception as exc:
            import traceback
            self._send("error", message=str(exc))
            traceback.print_exc()
        finally:
            self._cleanup()

    # ==================================================================
    #  Wait for GUI start command  (connect_only mode)
    # ==================================================================
    def _wait_for_start_command(self):
        """After connect+zero, block until the GUI sends a start signal.

        The user starts the treadmill during this window.
        """
        self._log("Waiting for start command — start the treadmill now …")
        while not self.stop_event.is_set():
            try:
                cmd = self.command_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if cmd == STOP_SIGNAL:
                self._send("state", value="Stopped")
                return
            elif cmd == FAMILIARIZATION_BEGIN_SIGNAL:
                self._run_familiarization()
                return
            elif cmd == PERCEPTION_TEST_BEGIN_SIGNAL:
                self._run_perception()
                return

        # stop_event was set while waiting
        self._send("state", value="Stopped")

    # ==================================================================
    #  Connect & initialise both boots
    # ==================================================================
    def _connect_and_init(self):
        p = self.params
        self._send("state", value="Connecting …")

        self.left_boot = ExoBoot(
            side=LEFT,
            port=p.get("left_port", LEFT_PORT),
            firmware_version=p.get("firmware", FIRMWARE_VERSION),
            status_callback=self._log,
        )
        self.right_boot = ExoBoot(
            side=RIGHT,
            port=p.get("right_port", RIGHT_PORT),
            firmware_version=p.get("firmware", FIRMWARE_VERSION),
            status_callback=self._log,
        )

        self._send("state", value="Initialising boots …")
        self.left_boot.initialize()
        self.right_boot.initialize()
        self._send("state", value="Ready")

    # ==================================================================
    #  Familiarization mode
    # ==================================================================
    def _run_familiarization(self):
        self._log("=== Familiarization ===")
        self._send("state", value="Familiarizing")

        # Reset gait detection so stale timestamps from the connect
        # phase don't corrupt stride‑duration estimates.
        self.left_boot.reset_gait_state()
        self.right_boot.reset_gait_state()
        self._log("Gait detection reset — ready for heel‑strikes.")

        # ---- Sensor pre‑flight check (2 s) ---------------------------
        self._log("Sensor check — reading 2 s of IMU data …")
        l_gz_min = l_gz_max = self.left_boot.gyroz
        r_gz_min = r_gz_max = self.right_boot.gyroz
        l_st_first = self.left_boot.current_time
        r_st_first = self.right_boot.current_time
        for _ in range(2000):
            self.left_boot.read_data()
            self.right_boot.read_data()
            l_gz_min = min(l_gz_min, self.left_boot.gyroz)
            l_gz_max = max(l_gz_max, self.left_boot.gyroz)
            r_gz_min = min(r_gz_min, self.right_boot.gyroz)
            r_gz_max = max(r_gz_max, self.right_boot.gyroz)
            sleep(0.001)
        l_st_last = self.left_boot.current_time
        r_st_last = self.right_boot.current_time
        self._log(
            f"  Left  gyroz  min={l_gz_min:.0f}  max={l_gz_max:.0f}  "
            f"state_time {l_st_first}→{l_st_last} "
            f"(Δ={l_st_last - l_st_first})"
        )
        self._log(
            f"  Right gyroz  min={r_gz_min:.0f}  max={r_gz_max:.0f}  "
            f"state_time {r_st_first}→{r_st_last} "
            f"(Δ={r_st_last - r_st_first})"
        )
        self._log(
            f"  Thresholds:  ARM >= {self.left_boot.segmentation_arm_threshold:.0f}   "
            f"TRIGGER <= {self.left_boot.segmentation_trigger_threshold:.0f}"
        )
        if l_gz_min == l_gz_max == 0:
            self._log("  ⚠ WARNING: Left gyroz stuck at 0 — IMU may not be streaming!")
        if r_gz_min == r_gz_max == 0:
            self._log("  ⚠ WARNING: Right gyroz stuck at 0 — IMU may not be streaming!")
        if l_st_first == l_st_last:
            self._log("  ⚠ WARNING: Left state_time not changing — data may be stale!")
        if r_st_first == r_st_last:
            self._log("  ⚠ WARNING: Right state_time not changing — data may be stale!")

        # Re-reset so the sensor-check reads don't pollute gait state
        self.left_boot.reset_gait_state()
        self.right_boot.reset_gait_state()
        self._log("Sensor check complete — starting control loop.")

        p = self.params
        user_weight = float(p["user_weight"])
        test_mode = p["test_mode"]

        t_rise = DEFAULT_T_RISE
        t_fall = DEFAULT_T_FALL
        t_onset = DEFAULT_T_ONSET
        t_peak = t_onset + t_rise
        peak_tn = DEFAULT_PEAK_TORQUE_NORM

        self.left_boot.init_collins_profile(
            t_rise=t_rise, t_fall=t_fall, t_peak=t_peak,
            weight=user_weight, peak_torque_norm=peak_tn,
        )
        self.right_boot.init_collins_profile(
            t_rise=t_rise, t_fall=t_fall, t_peak=t_peak,
            weight=user_weight, peak_torque_norm=peak_tn,
        )

        self._log(f"t_rise={t_rise:.1f}%  t_fall={t_fall:.1f}%  "
                  f"t_peak={t_peak:.1f}%")

        # Data log
        fam_data = {
            "state_time": [], "t_rise": [], "t_fall": [],
            "t_peak": [], "est_stride_dur": [], "actual_stride_dur": [],
        }
        left_prev_gait = self.left_boot.num_gait
        update_left = False
        update_right = False
        current_left_num = 0
        current_right_num = 0

        while not self.stop_event.is_set():
            self.left_boot.run_collins_profile()
            self.right_boot.run_collins_profile()

            # ---- GUI commands ----------------------------------------
            cmd = self._check_cmd()
            if cmd == STOP_SIGNAL:
                break
            if cmd == INCREASE_SIGNAL:
                if test_mode == RISE_TIME_TEST:
                    t_rise += FAMILIARIZATION_DELTA
                    t_peak = t_onset + t_rise
                else:
                    t_fall += FAMILIARIZATION_DELTA
                current_left_num = self.left_boot.num_gait
                current_right_num = self.right_boot.num_gait
                update_left = update_right = True
            if cmd == DECREASE_SIGNAL:
                if test_mode == RISE_TIME_TEST:
                    t_rise -= FAMILIARIZATION_DELTA
                    t_peak = t_onset + t_rise
                else:
                    t_fall -= FAMILIARIZATION_DELTA
                current_left_num = self.left_boot.num_gait
                current_right_num = self.right_boot.num_gait
                update_left = update_right = True

            # Apply at next heel‑strike
            if update_left and self.left_boot.num_gait > current_left_num:
                self.left_boot.init_collins_profile(
                    t_rise=t_rise, t_fall=t_fall, t_peak=t_peak,
                    weight=user_weight, peak_torque_norm=peak_tn,
                )
                self._log(f"Left  → t_rise={t_rise:.1f}  t_fall={t_fall:.1f}  "
                          f"t_peak={t_peak:.1f}")
                update_left = False
            if update_right and self.right_boot.num_gait > current_right_num:
                self.right_boot.init_collins_profile(
                    t_rise=t_rise, t_fall=t_fall, t_peak=t_peak,
                    weight=user_weight, peak_torque_norm=peak_tn,
                )
                self._log(f"Right → t_rise={t_rise:.1f}  t_fall={t_fall:.1f}  "
                          f"t_peak={t_peak:.1f}")
                update_right = False

            # ---- Per‑stride logging ----------------------------------
            if self.left_boot.num_gait > left_prev_gait:
                fam_data["actual_stride_dur"].append(self.left_boot.current_duration)
                fam_data["state_time"].append(self.left_boot.current_time)
                fam_data["t_rise"].append(self.left_boot.t_rise)
                fam_data["t_fall"].append(self.left_boot.t_fall)
                fam_data["t_peak"].append(self.left_boot.t_peak)
                fam_data["est_stride_dur"].append(self.left_boot.expected_duration)
                left_prev_gait = self.left_boot.num_gait

            sleep(1 / self.left_boot.frequency)

        # ---- Save ----------------------------------------------------
        self._save_fam_data(fam_data)
        self._log("Familiarization ended.")
        self._send("state", value="Stopped")

    # ==================================================================
    #  Perception test  (adaptive staircase)
    # ==================================================================
    def _run_perception(self):
        self._log("=== Perception Test ===")
        self._send("state", value="Perception — warming up")

        # Reset gait detection so stale timestamps from the connect
        # phase don't corrupt stride‑duration estimates.
        self.left_boot.reset_gait_state()
        self.right_boot.reset_gait_state()
        self._log("Gait detection reset — ready for heel‑strikes.")

        # ---- Sensor pre‑flight check (2 s) ---------------------------
        self._log("Sensor check — reading 2 s of IMU data …")
        l_gz_min = l_gz_max = self.left_boot.gyroz
        r_gz_min = r_gz_max = self.right_boot.gyroz
        l_st_first = self.left_boot.current_time
        r_st_first = self.right_boot.current_time
        for _ in range(2000):
            self.left_boot.read_data()
            self.right_boot.read_data()
            l_gz_min = min(l_gz_min, self.left_boot.gyroz)
            l_gz_max = max(l_gz_max, self.left_boot.gyroz)
            r_gz_min = min(r_gz_min, self.right_boot.gyroz)
            r_gz_max = max(r_gz_max, self.right_boot.gyroz)
            sleep(0.001)
        l_st_last = self.left_boot.current_time
        r_st_last = self.right_boot.current_time
        self._log(
            f"  Left  gyroz  min={l_gz_min:.0f}  max={l_gz_max:.0f}  "
            f"state_time {l_st_first}→{l_st_last} "
            f"(Δ={l_st_last - l_st_first})"
        )
        self._log(
            f"  Right gyroz  min={r_gz_min:.0f}  max={r_gz_max:.0f}  "
            f"state_time {r_st_first}→{r_st_last} "
            f"(Δ={r_st_last - r_st_first})"
        )
        self._log(
            f"  Thresholds:  ARM >= {self.left_boot.segmentation_arm_threshold:.0f}   "
            f"TRIGGER <= {self.left_boot.segmentation_trigger_threshold:.0f}"
        )
        if l_gz_min == l_gz_max == 0:
            self._log("  ⚠ WARNING: Left gyroz stuck at 0 — IMU may not be streaming!")
        if r_gz_min == r_gz_max == 0:
            self._log("  ⚠ WARNING: Right gyroz stuck at 0 — IMU may not be streaming!")
        if l_st_first == l_st_last:
            self._log("  ⚠ WARNING: Left state_time not changing — data may be stale!")
        if r_st_first == r_st_last:
            self._log("  ⚠ WARNING: Right state_time not changing — data may be stale!")

        # Re-reset so the sensor-check reads don't pollute gait state
        self.left_boot.reset_gait_state()
        self.right_boot.reset_gait_state()
        self._log("Sensor check complete — starting control loop.")

        p = self.params
        user_weight = float(p["user_weight"])
        test_mode = p["test_mode"]
        approach = p["approach"]

        t_rise = DEFAULT_T_RISE
        t_fall = DEFAULT_T_FALL
        t_onset = DEFAULT_T_ONSET
        t_peak = t_onset + t_rise
        peak_tn = DEFAULT_PEAK_TORQUE_NORM

        # ---- Reference / comparison setup ----------------------------
        if test_mode == RISE_TIME_TEST:
            reference_value = DEFAULT_T_RISE
        else:
            reference_value = DEFAULT_T_FALL

        if approach == APPROACH_FROM_ABOVE:
            init_comparison = reference_value + INITIAL_OFFSET
            direction = -1     # must decrease to approach reference
        else:
            init_comparison = reference_value - INITIAL_OFFSET
            direction = 1      # must increase to approach reference

        adaptive_comp = init_comparison
        comparison_value = init_comparison

        # Reference Collins profile
        ref_profile = self._make_profile(reference_value, test_mode,
                                         user_weight, peak_tn)
        self.left_boot.init_collins_profile(**ref_profile)
        self.right_boot.init_collins_profile(**ref_profile)

        # ---- Warm‑up: light current ----------------------------------
        self._log(f"Warm‑up: {WARMUP_STRIDES} strides (light current) …")
        while (self.left_boot.num_gait < WARMUP_STRIDES
               and not self.stop_event.is_set()):
            self.left_boot.read_data()
            self.right_boot.read_data()
            self.left_boot.device.command_motor_current(WARMUP_CURRENT)
            self.right_boot.device.command_motor_current(-WARMUP_CURRENT)
            sleep(1 / self.left_boot.frequency)

        # ---- Warm‑up: augmented Collins profile ----------------------
        self._log(f"Warm‑up: {WARMUP_AUGMENTED_STRIDES} strides (Collins) …")
        target = self.left_boot.num_gait + WARMUP_AUGMENTED_STRIDES
        while self.left_boot.num_gait < target and not self.stop_event.is_set():
            self.left_boot.run_collins_profile()
            self.right_boot.run_collins_profile()
            sleep(1 / self.left_boot.frequency)

        self._log("Perception test begins!")
        self._send("state", value="Perception — running")

        # ---- Data containers -----------------------------------------
        trial_data = {
            "Trial #": [], "Sweep #": [], "Delta": [],
            "Test Mode": [], "Reference Value": [],
            "Comparison Value": [], "Response": [], "Catch Trial": [],
        }
        stride_data_L = {
            "state_time": [], "varied_value": [],
            "est_stride_dur": [], "actual_stride_dur": [],
        }
        stride_data_R = dict(stride_data_L)   # independent copy
        stride_data_R = {k: [] for k in stride_data_L}

        # ---- Staircase state -----------------------------------------
        trial_num = 0
        sweep_num = 0.0
        prev_response = None
        catch_flag = 1              # 0 → catch trial

        left_prev_gait = self.left_boot.num_gait
        right_prev_gait = self.right_boot.num_gait

        # ==============================================================
        #  Trial loop
        # ==============================================================
        while (trial_num < TOTAL_TRIALS_MAX
               and sweep_num < TOTAL_SWEEPS
               and not self.stop_event.is_set()):

            # ---- Determine catch trial --------------------------------
            if trial_num >= NUM_PRACTICE_TRIALS:
                catch_flag = random.randint(0, CATCH_TRIAL_DENOMINATOR - 1)
            else:
                catch_flag = 1       # practice trials are never catch

            is_catch = (catch_flag == 0)
            if is_catch:
                trial_comp = reference_value
            else:
                trial_comp = adaptive_comp

            # ---- Build profiles for Timing A & B ---------------------
            ref_prof = self._make_profile(reference_value, test_mode,
                                          user_weight, peak_tn)
            comp_prof = self._make_profile(trial_comp, test_mode,
                                           user_weight, peak_tn)
            timing_list = [
                ("ref", reference_value, ref_prof),
                ("comp", trial_comp, comp_prof),
            ]
            random.shuffle(timing_list)
            label_A, val_A, prof_A = timing_list[0]
            label_B, val_B, prof_B = timing_list[1]

            catch_str = "CATCH " if is_catch else ""
            self._log(f"\n--- {catch_str}Trial {trial_num+1}  "
                      f"sweep={int(sweep_num)}  ---")
            self._log(f"  Timing A ({label_A}) = {val_A:.1f}%")
            self._send("trial_info", trial=trial_num + 1,
                       sweep=int(sweep_num), catch=is_catch,
                       comparison=trial_comp)

            # ---- Run Timing A ----------------------------------------
            cur_L = self.left_boot.num_gait
            cur_R = self.right_boot.num_gait

            self.left_boot.init_collins_profile(**prof_A)

            right_A = False
            left_B = False
            right_B = False
            resp_phase = False
            left_in_trial = True

            response = None

            while not self.stop_event.is_set():
                self.left_boot.run_collins_profile()
                self.right_boot.run_collins_profile()

                # Right boot → Timing A at next HS
                if self.right_boot.num_gait > cur_R and not right_A:
                    self.right_boot.init_collins_profile(**prof_A)
                    right_A = True

                # After STRIDES_PER_CONDITION → switch to Timing B
                if (self.left_boot.num_gait - cur_L >= STRIDES_PER_CONDITION
                        and not left_B):
                    self.left_boot.init_collins_profile(**prof_B)
                    left_B = True
                    cur_R = self.right_boot.num_gait
                    self._log(f"  Timing B ({label_B}) = {val_B:.1f}%")

                if (self.right_boot.num_gait > cur_R
                        and not right_B and left_B):
                    self.right_boot.init_collins_profile(**prof_B)
                    right_B = True

                # After TOTAL_STRIDES_PER_TRIAL → response phase
                if (self.left_boot.num_gait - cur_L >= TOTAL_STRIDES_PER_TRIAL
                        and not resp_phase):
                    # Revert to reference while waiting
                    self.left_boot.init_collins_profile(**ref_prof)
                    self.right_boot.init_collins_profile(**ref_prof)
                    resp_phase = True
                    left_in_trial = False
                    self._send("awaiting_response", prompt="Same or Different?")
                    self._log("  Awaiting response …")
                    self._flush_cmd()

                # Poll for response
                if resp_phase:
                    cmd = self._check_cmd()
                    if cmd in (DIFFERENCE_RESPONSE, SAME_RESPONSE):
                        response = cmd
                        break
                    if cmd == STOP_SIGNAL:
                        return

                # ---- Per‑stride logging ------------------------------
                if self.left_boot.num_gait > left_prev_gait and left_in_trial:
                    stride_data_L["actual_stride_dur"].append(
                        self.left_boot.current_duration)
                    stride_data_L["state_time"].append(
                        self.left_boot.current_time)
                    stride_data_L["varied_value"].append(
                        self.left_boot.t_rise if test_mode == RISE_TIME_TEST
                        else self.left_boot.t_fall)
                    stride_data_L["est_stride_dur"].append(
                        self.left_boot.expected_duration)
                    left_prev_gait = self.left_boot.num_gait

                if self.right_boot.num_gait > right_prev_gait:
                    stride_data_R["actual_stride_dur"].append(
                        self.right_boot.current_duration)
                    stride_data_R["state_time"].append(
                        self.right_boot.current_time)
                    stride_data_R["varied_value"].append(
                        self.right_boot.t_rise if test_mode == RISE_TIME_TEST
                        else self.right_boot.t_fall)
                    stride_data_R["est_stride_dur"].append(
                        self.right_boot.expected_duration)
                    right_prev_gait = self.right_boot.num_gait

                sleep(1 / self.left_boot.frequency)

            if response is None:
                break      # stop_event was set

            # ---- Process response ------------------------------------
            resp_str = ("Different" if response == DIFFERENCE_RESPONSE
                        else "Same")
            self._log(f"  Response: {resp_str}")

            if not is_catch:
                if response == DIFFERENCE_RESPONSE:
                    new_val = adaptive_comp + direction * DELTA
                    # Don't cross reference
                    if direction == 1:
                        adaptive_comp = min(new_val, reference_value)
                    else:
                        adaptive_comp = max(new_val, reference_value)
                elif response == SAME_RESPONSE:
                    adaptive_comp -= direction * DELTA

                if prev_response is not None and response != prev_response:
                    sweep_num += 0.5
                prev_response = response

            comparison_value = adaptive_comp

            # ---- Log trial -------------------------------------------
            trial_data["Trial #"].append(trial_num + 1)
            trial_data["Sweep #"].append(int(sweep_num))
            trial_data["Delta"].append(DELTA)
            trial_data["Test Mode"].append(test_mode)
            trial_data["Reference Value"].append(reference_value)
            trial_data["Comparison Value"].append(trial_comp)
            trial_data["Response"].append(resp_str)
            trial_data["Catch Trial"].append("Yes" if is_catch else "No")

            self._log(f"  Next comparison = {adaptive_comp:.1f}%  "
                      f"sweep = {int(sweep_num)}")
            self._send("trial_info", trial=trial_num + 1,
                       sweep=int(sweep_num), catch=is_catch,
                       comparison=adaptive_comp)

            trial_num += 1

            # ---- Practice‑trial reset --------------------------------
            if trial_num == NUM_PRACTICE_TRIALS:
                adaptive_comp = init_comparison
                comparison_value = init_comparison
                sweep_num = 0
                prev_response = None
                self._log("Practice complete — real recording starts.")

            # ---- Rest period -----------------------------------------
            self._log(f"  Rest ({REST_STRIDES} strides) …")
            self._send("state", value="Resting …")
            rest_start = self.left_boot.num_gait
            while (self.left_boot.num_gait - rest_start < REST_STRIDES
                   and not self.stop_event.is_set()):
                self.left_boot.run_collins_profile()
                self.right_boot.run_collins_profile()
                sleep(1 / self.left_boot.frequency)

        # ---- Save data -----------------------------------------------
        self._save_perception_data(trial_data, stride_data_L, stride_data_R)
        self._log("=== Perception test complete ===")
        self._send("state", value="Complete")

    # ==================================================================
    #  Profile‑parameter builder
    # ==================================================================
    def _make_profile(self, value, test_mode, weight, peak_tn):
        """Return a dict suitable for ``init_collins_profile(**d)``."""
        if test_mode == RISE_TIME_TEST:
            t_r = value
            t_f = DEFAULT_T_FALL
            t_p = DEFAULT_T_ONSET + value
        else:
            t_r = DEFAULT_T_RISE
            t_f = value
            t_p = DEFAULT_T_ONSET + DEFAULT_T_RISE
        return dict(t_rise=t_r, t_fall=t_f, t_peak=t_p,
                    weight=weight, peak_torque_norm=peak_tn)

    # ==================================================================
    #  Data‑saving helpers
    # ==================================================================
    def _data_dir(self):
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(d, exist_ok=True)
        return d

    def _save_perception_data(self, trial_data, stride_L, stride_R):
        pid = self.params.get("participant_id", "unknown")
        ts = strftime("%Y-%m-%d_%Hh%Mm%Ss")
        d = self._data_dir()

        _pad(trial_data)
        pd.DataFrame(trial_data).to_csv(
            os.path.join(d, f"{pid}_Perception_{ts}.csv"), index=False)
        _pad(stride_L)
        pd.DataFrame(stride_L).to_csv(
            os.path.join(d, f"{pid}_PerceptionStride_L_{ts}.csv"), index=False)
        _pad(stride_R)
        pd.DataFrame(stride_R).to_csv(
            os.path.join(d, f"{pid}_PerceptionStride_R_{ts}.csv"), index=False)
        self._log(f"Data saved → {d}")

    def _save_fam_data(self, fam_data):
        pid = self.params.get("participant_id", "unknown")
        ts = strftime("%Y-%m-%d_%Hh%Mm%Ss")
        d = self._data_dir()
        _pad(fam_data)
        pd.DataFrame(fam_data).to_csv(
            os.path.join(d, f"{pid}_Familiarization_{ts}.csv"), index=False)
        self._log(f"Familiarization data saved → {d}")

    # ==================================================================
    #  Clean‑up
    # ==================================================================
    def _cleanup(self):
        self._log("Shutting down boots …")
        for boot in (self.left_boot, self.right_boot):
            if boot is not None:
                try:
                    boot.device.command_motor_current(0)
                    sleep(0.05)
                    boot.device.command_motor_current(0)
                except Exception:
                    pass
                boot.clean()
        self.left_boot = None
        self.right_boot = None
        self._flush_cmd()           # discard stale commands
        self._send("state", value="Idle")


# ======================================================================
#  Utility
# ======================================================================
def _pad(data: dict):
    """Pad all lists in *data* to equal length (to appease ``DataFrame``)."""
    if not data:
        return
    max_len = max(len(v) for v in data.values())
    for k in data:
        while len(data[k]) < max_len:
            data[k].append("")

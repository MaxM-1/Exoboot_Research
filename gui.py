#!/usr/bin/env python3
"""
Experiment GUI — Rise / Fall Time Perception
=============================================

Simple **tkinter** interface that replaces Xiangyu Peng's Android app.
Run with::

    python gui.py

The GUI runs on the **main thread** (required by tkinter).  The
experiment controller (``PerceptionExperiment``) runs in a daemon
thread; communication happens via two ``queue.Queue`` objects.

Author:  Max Miller — Auburn University
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

from config import *
from perception_test import PerceptionExperiment


# ======================================================================
#  Main application
# ======================================================================
class ExperimentGUI:
    """tkinter front‑end for the perception experiment."""

    POLL_MS = 100          # status‑queue polling interval

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Rise / Fall Time Perception Experiment")
        self.root.resizable(True, True)

        # ---- Experiment backend -----------------------------------
        self.experiment = PerceptionExperiment()

        # ---- State ------------------------------------------------
        self._running = False
        self._awaiting_response = False
        self._connected = False       # True after Connect & Zero

        # ---- Build UI ---------------------------------------------
        self._build_setup_frame()
        self._build_control_frame()
        self._build_status_frame()
        self._build_log_frame()
        self._update_button_states()

        # ---- Start polling ----------------------------------------
        self.root.after(self.POLL_MS, self._poll_status)

    # ==================================================================
    #  Setup frame
    # ==================================================================
    def _build_setup_frame(self):
        frame = ttk.LabelFrame(self.root, text="Setup", padding=8)
        frame.pack(fill="x", padx=8, pady=(8, 2))

        # Row helpers
        def _entry_row(parent, label, default, row):
            ttk.Label(parent, text=label).grid(
                row=row, column=0, sticky="e", padx=(0, 4), pady=2)
            var = tk.StringVar(value=default)
            ttk.Entry(parent, textvariable=var, width=24).grid(
                row=row, column=1, sticky="w", pady=2)
            return var

        self.var_pid = _entry_row(frame, "Participant ID:", "P001", 0)
        self.var_weight = _entry_row(frame, "Weight (kg):", "75", 1)
        self.var_fw = _entry_row(frame, "Firmware:", FIRMWARE_VERSION, 2)
        self.var_lport = _entry_row(frame, "Left port:", LEFT_PORT, 3)
        self.var_rport = _entry_row(frame, "Right port:", RIGHT_PORT, 4)

        # Radio — test mode
        ttk.Label(frame, text="Test mode:").grid(
            row=5, column=0, sticky="e", padx=(0, 4), pady=2)
        mode_frame = ttk.Frame(frame)
        mode_frame.grid(row=5, column=1, sticky="w")
        self.var_mode = tk.StringVar(value=RISE_TIME_TEST)
        ttk.Radiobutton(mode_frame, text="Rise time",
                        variable=self.var_mode,
                        value=RISE_TIME_TEST).pack(side="left")
        ttk.Radiobutton(mode_frame, text="Fall time",
                        variable=self.var_mode,
                        value=FALL_TIME_TEST).pack(side="left", padx=8)

        # Radio — approach direction
        ttk.Label(frame, text="Direction:").grid(
            row=6, column=0, sticky="e", padx=(0, 4), pady=2)
        dir_frame = ttk.Frame(frame)
        dir_frame.grid(row=6, column=1, sticky="w")
        self.var_approach = tk.StringVar(value=APPROACH_FROM_ABOVE)
        ttk.Radiobutton(dir_frame, text="From above",
                        variable=self.var_approach,
                        value=APPROACH_FROM_ABOVE).pack(side="left")
        ttk.Radiobutton(dir_frame, text="From below",
                        variable=self.var_approach,
                        value=APPROACH_FROM_BELOW).pack(side="left", padx=8)

    # ==================================================================
    #  Control buttons
    # ==================================================================
    def _build_control_frame(self):
        frame = ttk.LabelFrame(self.root, text="Controls", padding=8)
        frame.pack(fill="x", padx=8, pady=2)

        row0 = ttk.Frame(frame)
        row0.pack(fill="x", pady=2)
        self.btn_connect = ttk.Button(
            row0, text="Connect & Zero", command=self._on_connect_zero)
        self.btn_connect.pack(side="left", padx=2)
        self.btn_fam = ttk.Button(
            row0, text="Start Familiarization", command=self._on_start_fam)
        self.btn_fam.pack(side="left", padx=2)
        self.btn_test = ttk.Button(
            row0, text="Start Perception Test", command=self._on_start_test)
        self.btn_test.pack(side="left", padx=2)
        self.btn_stop = ttk.Button(
            row0, text="Stop", command=self._on_stop)
        self.btn_stop.pack(side="left", padx=2)

        # Familiarization adjust
        row1 = ttk.Frame(frame)
        row1.pack(fill="x", pady=2)
        ttk.Label(row1, text="Familiarization:").pack(side="left")
        self.btn_inc = ttk.Button(
            row1, text="▲ Increase", width=12, command=self._on_increase)
        self.btn_inc.pack(side="left", padx=2)
        self.btn_dec = ttk.Button(
            row1, text="▼ Decrease", width=12, command=self._on_decrease)
        self.btn_dec.pack(side="left", padx=2)

        # Response buttons
        row2 = ttk.Frame(frame)
        row2.pack(fill="x", pady=2)
        ttk.Label(row2, text="Participant response:").pack(side="left")
        self.btn_diff = ttk.Button(
            row2, text="Different", width=12,
            command=self._on_different)
        self.btn_diff.pack(side="left", padx=2)
        self.btn_same = ttk.Button(
            row2, text="Same", width=12,
            command=self._on_same)
        self.btn_same.pack(side="left", padx=2)

    # ==================================================================
    #  Status display
    # ==================================================================
    def _build_status_frame(self):
        frame = ttk.LabelFrame(self.root, text="Status", padding=8)
        frame.pack(fill="x", padx=8, pady=2)

        self.lbl_state = ttk.Label(frame, text="State:  Idle",
                                   font=("", 11, "bold"))
        self.lbl_state.pack(anchor="w")
        self.lbl_trial = ttk.Label(frame, text="Trial: --   Sweep: --   "
                                                "Catch: --")
        self.lbl_trial.pack(anchor="w")
        self.lbl_ref = ttk.Label(
            frame,
            text=f"Reference: t_rise={DEFAULT_T_RISE}%  "
                 f"t_fall={DEFAULT_T_FALL}%",
        )
        self.lbl_ref.pack(anchor="w")
        self.lbl_comp = ttk.Label(frame, text="Comparison: --")
        self.lbl_comp.pack(anchor="w")

    # ==================================================================
    #  Scrolling log
    # ==================================================================
    def _build_log_frame(self):
        frame = ttk.LabelFrame(self.root, text="Log", padding=4)
        frame.pack(fill="both", expand=True, padx=8, pady=(2, 8))

        self.log_text = scrolledtext.ScrolledText(
            frame, height=14, state="disabled", wrap="word",
            font=("Courier", 9),
        )
        self.log_text.pack(fill="both", expand=True)

    # ==================================================================
    #  Button callbacks
    # ==================================================================
    def _collect_params(self, mode: str) -> dict:
        return {
            "participant_id": self.var_pid.get(),
            "user_weight": self.var_weight.get(),
            "test_mode": self.var_mode.get(),
            "approach": self.var_approach.get(),
            "left_port": self.var_lport.get(),
            "right_port": self.var_rport.get(),
            "firmware": self.var_fw.get(),
            "mode": mode,
        }

    def _on_connect_zero(self):
        """Connect to boots and zero them, then wait for user to start
        treadmill before pressing Start Familiarization / Test."""
        if self._connected or self._running:
            return
        self._append_log(">>> Connecting and zeroing boots …")
        self.lbl_state.config(text="State:  Connecting …")
        params = self._collect_params("connect_only")
        self.experiment = PerceptionExperiment()
        self.experiment.start(params)

    def _on_start_fam(self):
        if self._running:
            return
        if not self._connected:
            messagebox.showwarning(
                "Not connected",
                "Press 'Connect & Zero' first, then start the treadmill "
                "before pressing Start Familiarization.")
            return
        self._running = True
        self._update_button_states()
        self._append_log(">>> Starting familiarization …")
        self.experiment.command_queue.put(FAMILIARIZATION_BEGIN_SIGNAL)

    def _on_start_test(self):
        if self._running:
            return
        if not self._connected:
            messagebox.showwarning(
                "Not connected",
                "Press 'Connect & Zero' first, then start the treadmill "
                "before pressing Start Perception Test.")
            return
        self._running = True
        self._update_button_states()
        self._append_log(">>> Starting perception test …")
        self.experiment.command_queue.put(PERCEPTION_TEST_BEGIN_SIGNAL)

    def _on_stop(self):
        if not self._running and not self._connected:
            return
        self._append_log(">>> STOP requested")
        self.experiment.request_stop()

    def _on_increase(self):
        self.experiment.command_queue.put(INCREASE_SIGNAL)

    def _on_decrease(self):
        self.experiment.command_queue.put(DECREASE_SIGNAL)

    def _on_different(self):
        if self._awaiting_response:
            self.experiment.command_queue.put(DIFFERENCE_RESPONSE)
            self._awaiting_response = False
            self._update_button_states()

    def _on_same(self):
        if self._awaiting_response:
            self.experiment.command_queue.put(SAME_RESPONSE)
            self._awaiting_response = False
            self._update_button_states()

    # ==================================================================
    #  Button‑state management
    # ==================================================================
    def _update_button_states(self):
        running = self._running
        awaiting = self._awaiting_response
        connected = self._connected

        # Connect & Zero: only when not connected and not running
        self.btn_connect.config(
            state="normal" if (not connected and not running) else "disabled")

        # Start buttons: only when connected but not yet running
        can_start = connected and not running
        self.btn_fam.config(state="normal" if can_start else "disabled")
        self.btn_test.config(state="normal" if can_start else "disabled")

        # Stop: when connected or running
        self.btn_stop.config(
            state="normal" if (running or connected) else "disabled")

        # Familiarization adjust: only while running
        self.btn_inc.config(state="normal" if running else "disabled")
        self.btn_dec.config(state="normal" if running else "disabled")

        # Response buttons: only while awaiting response
        self.btn_diff.config(state="normal" if awaiting else "disabled")
        self.btn_same.config(state="normal" if awaiting else "disabled")

    # ==================================================================
    #  Status‑queue polling  (runs on main thread via root.after)
    # ==================================================================
    def _poll_status(self):
        try:
            while True:
                msg = self.experiment.status_queue.get_nowait()
                self._handle_status(msg)
        except Exception:
            pass
        self.root.after(self.POLL_MS, self._poll_status)

    def _handle_status(self, msg: dict):
        t = msg.get("type", "")

        if t == "log":
            self._append_log(msg.get("message", ""))

        elif t == "connected":
            # Boots are connected and zeroed — waiting for user
            self._connected = True
            self._running = False
            self.lbl_state.config(text="State:  Connected & Zeroed — "
                                       "start treadmill then press Start")
            self._append_log("Boots connected and zeroed.  "
                             "Start the treadmill, then press "
                             "'Start Familiarization' or 'Start "
                             "Perception Test'.")
            self._update_button_states()

        elif t == "state":
            val = msg.get("value", "")
            self.lbl_state.config(text=f"State:  {val}")
            if val in ("Idle", "Complete", "Stopped"):
                self._running = False
                self._connected = False
                self._awaiting_response = False
                self._update_button_states()

        elif t == "trial_info":
            trial = msg.get("trial", "--")
            sweep = msg.get("sweep", "--")
            catch = msg.get("catch", False)
            comp = msg.get("comparison", "--")
            catch_str = "Yes" if catch else "No"
            self.lbl_trial.config(
                text=f"Trial: {trial}   Sweep: {sweep}   "
                     f"Catch: {catch_str}")
            mode = self.var_mode.get()
            if mode == RISE_TIME_TEST:
                self.lbl_comp.config(
                    text=f"Comparison: t_rise={comp}%  "
                         f"t_fall={DEFAULT_T_FALL}%")
            else:
                self.lbl_comp.config(
                    text=f"Comparison: t_rise={DEFAULT_T_RISE}%  "
                         f"t_fall={comp}%")

        elif t == "awaiting_response":
            self._awaiting_response = True
            self._update_button_states()
            self._append_log(f"   {msg.get('prompt', 'Respond')}")

        elif t == "error":
            self._append_log(f"   ERROR: {msg.get('message', '')}")
            messagebox.showerror("Error", msg.get("message", "Unknown error"))
            self._running = False
            self._connected = False
            self._awaiting_response = False
            self._update_button_states()

    # ==================================================================
    #  Log helper
    # ==================================================================
    def _append_log(self, text: str):
        self.log_text.config(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")


# ======================================================================
#  Entry point
# ======================================================================
def main():
    root = tk.Tk()
    ExperimentGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

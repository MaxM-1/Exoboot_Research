#!/usr/bin/env python3
"""
Experiment GUI — Rise / Fall Time Perception
=============================================

**PyQt5** interface that replaces Xiangyu Peng's Android app.
Run with::

    python gui.py

The GUI runs on the **main thread** (required by Qt).  The experiment
controller (``PerceptionExperiment``) runs in a daemon thread;
communication happens via two ``queue.Queue`` objects, polled by a
``QTimer``.

Author:  Max Miller — Auburn University
"""

import sys
import os
import queue
from time import strftime

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QFormLayout,
    QGroupBox, QLabel, QLineEdit, QRadioButton, QButtonGroup,
    QPushButton, QTextEdit, QMessageBox,
)

from config import *
from perception_test import PerceptionExperiment


# ======================================================================
#  Main application
# ======================================================================
class ExperimentGUI(QMainWindow):
    """PyQt5 front-end for the perception experiment."""

    POLL_MS = 50           # status-queue polling interval (ms)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rise / Fall Time Perception Experiment")

        # ---- Persistent GUI log file (data/GUIlog_*.txt) ----------
        log_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(log_dir, exist_ok=True)
        self._gui_log_path = os.path.join(
            log_dir, f"GUIlog_{strftime('%Y-%m-%d_%Hh%Mm%Ss')}.txt")
        try:
            self._gui_log_fh = open(self._gui_log_path, "a", buffering=1)
        except Exception:
            self._gui_log_fh = None

        # ---- Experiment backend -----------------------------------
        self.experiment = PerceptionExperiment()

        # ---- State ------------------------------------------------
        self._running = False
        self._awaiting_response = False
        self._connected = False       # True after Connect & Zero
        self._closing = False         # True while safe-shutdown in progress

        # ---- Build UI ---------------------------------------------
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(4)

        main_layout.addWidget(self._build_setup_group())
        main_layout.addWidget(self._build_control_group())
        main_layout.addWidget(self._build_status_group())
        main_layout.addWidget(self._build_log_group(), stretch=1)

        self._update_button_states()

        # ---- Start polling ----------------------------------------
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_status)
        self._poll_timer.start(self.POLL_MS)

    # ==================================================================
    #  Setup group
    # ==================================================================
    def _build_setup_group(self) -> QGroupBox:
        group = QGroupBox("Setup")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.edit_pid = QLineEdit("P001")
        self.edit_weight = QLineEdit("75")
        self.edit_fw = QLineEdit(FIRMWARE_VERSION)
        self.edit_lport = QLineEdit(LEFT_PORT)
        self.edit_rport = QLineEdit(RIGHT_PORT)

        for widget in (self.edit_pid, self.edit_weight, self.edit_fw,
                       self.edit_lport, self.edit_rport):
            widget.setFixedWidth(180)

        form.addRow("Participant ID:", self.edit_pid)
        form.addRow("Weight (kg):", self.edit_weight)
        form.addRow("Firmware:", self.edit_fw)
        form.addRow("Left port:", self.edit_lport)
        form.addRow("Right port:", self.edit_rport)

        # Radio — test mode
        mode_layout = QHBoxLayout()
        self.radio_rise = QRadioButton("Rise time")
        self.radio_fall = QRadioButton("Fall time")
        self.radio_rise.setChecked(True)
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.radio_rise)
        self.mode_group.addButton(self.radio_fall)
        mode_layout.addWidget(self.radio_rise)
        mode_layout.addWidget(self.radio_fall)
        mode_layout.addStretch()
        mode_widget = QWidget()
        mode_widget.setLayout(mode_layout)
        form.addRow("Test mode:", mode_widget)

        # Radio — approach direction
        dir_layout = QHBoxLayout()
        self.radio_above = QRadioButton("From above")
        self.radio_below = QRadioButton("From below")
        self.radio_above.setChecked(True)
        self.approach_group = QButtonGroup(self)
        self.approach_group.addButton(self.radio_above)
        self.approach_group.addButton(self.radio_below)
        dir_layout.addWidget(self.radio_above)
        dir_layout.addWidget(self.radio_below)
        dir_layout.addStretch()
        dir_widget = QWidget()
        dir_widget.setLayout(dir_layout)
        form.addRow("Direction:", dir_widget)

        group.setLayout(form)
        return group

    # ==================================================================
    #  Control buttons
    # ==================================================================
    def _build_control_group(self) -> QGroupBox:
        group = QGroupBox("Controls")
        layout = QVBoxLayout()

        # Row 0 — main controls
        row0 = QHBoxLayout()
        self.btn_connect = QPushButton("Connect && Zero")
        self.btn_fam = QPushButton("Start Familiarization")
        self.btn_test = QPushButton("Start Perception Test")
        self.btn_stop = QPushButton("Stop")

        self.btn_connect.clicked.connect(self._on_connect_zero)
        self.btn_fam.clicked.connect(self._on_start_fam)
        self.btn_test.clicked.connect(self._on_start_test)
        self.btn_stop.clicked.connect(self._on_stop)

        for btn in (self.btn_connect, self.btn_fam, self.btn_test,
                    self.btn_stop):
            row0.addWidget(btn)
        row0.addStretch()
        layout.addLayout(row0)

        # Row 1 — familiarization adjust
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Familiarization:"))
        self.btn_inc = QPushButton("\u25B2 Increase")
        self.btn_dec = QPushButton("\u25BC Decrease")
        self.btn_inc.setFixedWidth(110)
        self.btn_dec.setFixedWidth(110)
        self.btn_inc.clicked.connect(self._on_increase)
        self.btn_dec.clicked.connect(self._on_decrease)
        row1.addWidget(self.btn_inc)
        row1.addWidget(self.btn_dec)
        row1.addStretch()
        layout.addLayout(row1)

        # Row 2 — participant response
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Participant response:"))
        self.btn_diff = QPushButton("Different")
        self.btn_same = QPushButton("Same")
        self.btn_diff.setFixedWidth(110)
        self.btn_same.setFixedWidth(110)
        self.btn_diff.clicked.connect(self._on_different)
        self.btn_same.clicked.connect(self._on_same)
        row2.addWidget(self.btn_diff)
        row2.addWidget(self.btn_same)
        row2.addStretch()
        layout.addLayout(row2)

        group.setLayout(layout)
        return group

    # ==================================================================
    #  Status display
    # ==================================================================
    def _build_status_group(self) -> QGroupBox:
        group = QGroupBox("Status")
        layout = QVBoxLayout()

        self.lbl_state = QLabel("State:  Idle")
        self.lbl_state.setFont(QFont("", 11, QFont.Bold))
        self.lbl_trial = QLabel("Trial: --   Sweep: --   Catch: --")
        self.lbl_ref = QLabel(
            f"Reference: t_rise={DEFAULT_T_RISE}%  "
            f"t_fall={DEFAULT_T_FALL}%"
        )
        self.lbl_comp = QLabel("Comparison: --")

        layout.addWidget(self.lbl_state)
        layout.addWidget(self.lbl_trial)
        layout.addWidget(self.lbl_ref)
        layout.addWidget(self.lbl_comp)

        group.setLayout(layout)
        return group

    # ==================================================================
    #  Scrolling log
    # ==================================================================
    def _build_log_group(self) -> QGroupBox:
        group = QGroupBox("Log")
        layout = QVBoxLayout()

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Courier", 9))
        layout.addWidget(self.log_text)

        group.setLayout(layout)
        return group

    # ==================================================================
    #  Button callbacks
    # ==================================================================
    def _collect_params(self, mode: str) -> dict:
        test_mode = (RISE_TIME_TEST if self.radio_rise.isChecked()
                     else FALL_TIME_TEST)
        approach = (APPROACH_FROM_ABOVE if self.radio_above.isChecked()
                    else APPROACH_FROM_BELOW)
        return {
            "participant_id": self.edit_pid.text(),
            "user_weight": self.edit_weight.text(),
            "test_mode": test_mode,
            "approach": approach,
            "left_port": self.edit_lport.text(),
            "right_port": self.edit_rport.text(),
            "firmware": self.edit_fw.text(),
            "mode": mode,
        }

    def _on_connect_zero(self):
        """Connect to boots and zero them, then wait for user to start
        treadmill before pressing Start Familiarization / Test."""
        if self._connected or self._running:
            return

        # --- Clean up any previous experiment instance -----------------
        # Stop a lingering thread (e.g. if a prior run errored out)
        # and drain the old status queue so stale messages don't
        # bleed into the new session.
        if self.experiment is not None:
            try:
                self.experiment.request_stop()
            except Exception:
                pass
            while True:
                try:
                    self.experiment.status_queue.get_nowait()
                except queue.Empty:
                    break

        self._append_log(">>> Connecting and zeroing boots \u2026")
        self.lbl_state.setText("State:  Connecting \u2026")
        params = self._collect_params("connect_only")
        self.experiment = PerceptionExperiment()   # fresh instance
        self.experiment.start(params)

    def _on_start_fam(self):
        if self._running:
            return
        if not self._connected:
            QMessageBox.warning(
                self, "Not connected",
                "Press 'Connect & Zero' first, then start the treadmill "
                "before pressing Start Familiarization.")
            return
        self._running = True
        self._update_button_states()
        self._append_log(">>> Starting familiarization \u2026")
        self.experiment.command_queue.put(FAMILIARIZATION_BEGIN_SIGNAL)

    def _on_start_test(self):
        if self._running:
            return
        if not self._connected:
            QMessageBox.warning(
                self, "Not connected",
                "Press 'Connect & Zero' first, then start the treadmill "
                "before pressing Start Perception Test.")
            return
        self._running = True
        self._update_button_states()
        self._append_log(">>> Starting perception test \u2026")
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
    #  Button-state management
    # ==================================================================
    def _update_button_states(self):
        running = self._running
        awaiting = self._awaiting_response
        connected = self._connected

        # Connect & Zero: only when not connected and not running
        self.btn_connect.setEnabled(not connected and not running)

        # Start buttons: only when connected but not yet running
        can_start = connected and not running
        self.btn_fam.setEnabled(can_start)
        self.btn_test.setEnabled(can_start)

        # Stop: when connected or running
        self.btn_stop.setEnabled(running or connected)

        # Familiarization adjust: only while running
        self.btn_inc.setEnabled(running)
        self.btn_dec.setEnabled(running)

        # Response buttons: only while awaiting response
        self.btn_diff.setEnabled(awaiting)
        self.btn_same.setEnabled(awaiting)

    # ==================================================================
    #  Status-queue polling  (runs on main thread via QTimer)
    # ==================================================================
    def _poll_status(self):
        while True:
            try:
                msg = self.experiment.status_queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle_status(msg)
            except Exception as exc:
                self._append_log(f"GUI error handling status: {exc}")

    def _handle_status(self, msg: dict):
        t = msg.get("type", "")

        if t == "log":
            self._append_log(msg.get("message", ""))

        elif t == "connected":
            # Boots are connected and zeroed — waiting for user
            self._connected = True
            self._running = False
            self.lbl_state.setText(
                "State:  Connected & Zeroed \u2014 "
                "start treadmill then press Start")
            self._append_log(
                "Boots connected and zeroed.  "
                "Start the treadmill, then press "
                "'Start Familiarization' or 'Start "
                "Perception Test'.")
            self._update_button_states()

        elif t == "state":
            val = msg.get("value", "")
            self.lbl_state.setText(f"State:  {val}")
            if val in ("Idle", "Complete", "Stopped"):
                self._running = False
                self._connected = False
                self._awaiting_response = False
                self._update_button_states()
                # If we were waiting on shutdown to close, do it now
                if self._closing:
                    self.close()

        elif t == "trial_info":
            trial = msg.get("trial", "--")
            sweep = msg.get("sweep", "--")
            catch = msg.get("catch", False)
            comp = msg.get("comparison", "--")
            catch_str = "Yes" if catch else "No"
            self.lbl_trial.setText(
                f"Trial: {trial}   Sweep: {sweep}   "
                f"Catch: {catch_str}")
            test_mode = (RISE_TIME_TEST if self.radio_rise.isChecked()
                         else FALL_TIME_TEST)
            if test_mode == RISE_TIME_TEST:
                self.lbl_comp.setText(
                    f"Comparison: t_rise={comp}%  "
                    f"t_fall={DEFAULT_T_FALL}%")
            else:
                self.lbl_comp.setText(
                    f"Comparison: t_rise={DEFAULT_T_RISE}%  "
                    f"t_fall={comp}%")

        elif t == "awaiting_response":
            self._awaiting_response = True
            self._update_button_states()
            self._append_log(f"   {msg.get('prompt', 'Respond')}")

        elif t == "error":
            self._append_log(f"   ERROR: {msg.get('message', '')}")
            QMessageBox.critical(
                self, "Error", msg.get("message", "Unknown error"))
            self._running = False
            self._connected = False
            self._awaiting_response = False
            self._update_button_states()

    # ==================================================================
    #  Safe window close  (stop motors before exit)
    # ==================================================================
    def closeEvent(self, event):
        """Ensure exoboots are safely stopped before the window closes.

        If an experiment is active, request a stop and defer the close
        until the experiment thread confirms shutdown via the status
        queue.  A 3-second fallback timer guarantees the window closes
        even if the thread hangs.
        """
        if self._closing:
            # Already waiting — accept unconditionally (fallback timer)
            event.accept()
            return

        if self._running or self._connected:
            self._closing = True
            self._append_log("Shutting down safely \u2014 stopping motors \u2026")
            self.experiment.request_stop()
            # Fallback: force-close after 3 seconds even if thread hangs
            QTimer.singleShot(3000, self.close)
            event.ignore()
        else:
            try:
                if getattr(self, "_gui_log_fh", None):
                    self._gui_log_fh.close()
                    self._gui_log_fh = None
            except Exception:
                pass
            event.accept()

    # ==================================================================
    #  Log helper
    # ==================================================================
    def _append_log(self, text: str):
        self.log_text.append(text)
        self.log_text.ensureCursorVisible()
        if getattr(self, "_gui_log_fh", None):
            try:
                self._gui_log_fh.write(text + "\n")
            except Exception:
                pass


# ======================================================================
#  Entry point
# ======================================================================
def main():
    app = QApplication(sys.argv)
    window = ExperimentGUI()
    window.resize(700, 650)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

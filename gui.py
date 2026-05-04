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

# matplotlib (Qt5 backend) for live torque-profile preview
import matplotlib
matplotlib.use("Qt5Agg")
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

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
        self.setWindowTitle("Peak-Time Perception Experiment")

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
        self._mode_active = None      # 'fam' | 'perception' | None

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
        form.addRow("Approach:", dir_widget)

        # Radio — experiment type (MAX = peak time, SAV = peak torque)
        exp_layout = QHBoxLayout()
        self.radio_max = QRadioButton("MAX (peak time)")
        self.radio_sav = QRadioButton("SAV (peak torque)")
        self.radio_max.setChecked(DEFAULT_EXPERIMENT == MAX_EXPERIMENT)
        self.radio_sav.setChecked(DEFAULT_EXPERIMENT == SAV_EXPERIMENT)
        self.experiment_group = QButtonGroup(self)
        self.experiment_group.addButton(self.radio_max)
        self.experiment_group.addButton(self.radio_sav)
        self.experiment_group.buttonClicked.connect(
            self._on_experiment_type_changed)
        exp_layout.addWidget(self.radio_max)
        exp_layout.addWidget(self.radio_sav)
        exp_layout.addStretch()
        exp_widget = QWidget()
        exp_widget.setLayout(exp_layout)
        form.addRow("Experiment:", exp_widget)

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

        # ---- State line + small CATCH tag (experimenter-only) ----
        state_row = QHBoxLayout()
        self.lbl_state = QLabel("State:  Idle")
        self.lbl_state.setFont(QFont("", 11, QFont.Bold))
        self.lbl_catch = QLabel("")        # "CATCH" in red when active
        self.lbl_catch.setStyleSheet("color: #b00; font-weight: bold;")
        state_row.addWidget(self.lbl_state)
        state_row.addStretch()
        state_row.addWidget(self.lbl_catch)
        layout.addLayout(state_row)

        # ---- Big condition-announcement banner -------------------
        self.lbl_condition = QLabel("Condition: \u2014")
        self.lbl_condition.setFont(QFont("", 16, QFont.Bold))
        self.lbl_condition.setStyleSheet(
            "background:#e8f0ff; padding:6px; border:1px solid #88a;")
        self.lbl_condition.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_condition)

        # ---- Trial-phase indicator (color-coded) -----------------
        self.lbl_phase = QLabel("Phase: \u2014")
        self.lbl_phase.setFont(QFont("", 12, QFont.Bold))
        self.lbl_phase.setStyleSheet(
            "background:#ddd; padding:4px;")
        self.lbl_phase.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_phase)

        # ---- Stride counter / trial-progress ---------------------
        progress_row = QHBoxLayout()
        self.lbl_stride = QLabel("Stride: --/--")
        self.lbl_progress = QLabel("Trial: --   Sweep: --/{}".format(
            TOTAL_SWEEPS))
        progress_row.addWidget(self.lbl_stride)
        progress_row.addStretch()
        progress_row.addWidget(self.lbl_progress)
        layout.addLayout(progress_row)

        # ---- Reference / comparison numeric --------------------------
        self.lbl_ref = QLabel(
            f"Reference t_peak: {DEFAULT_T_PEAK:.1f}%   "
            f"(start={T_ACT_START:.1f}%  end={T_ACT_END:.1f}%)"
        )
        self.lbl_comp = QLabel("Comparison t_peak: --")
        layout.addWidget(self.lbl_ref)
        layout.addWidget(self.lbl_comp)

        # ---- Live torque-profile preview (matplotlib) ----------------
        self._fig = Figure(figsize=(5, 2.4), tight_layout=True)
        self._canvas = FigureCanvas(self._fig)
        self._canvas.setMinimumHeight(220)
        self._ax = self._fig.add_subplot(111)
        self._ax.set_xlabel("% gait")
        self._ax.set_ylabel("Torque (Nm)")
        self._ax.set_xlim(0, 100)
        self._ax.grid(True, alpha=0.3)
        # Persistent line artists — updated via set_data on each preview msg
        (self._ln_ref,) = self._ax.plot([], [], "k-", lw=2,
                                        label="reference")
        (self._ln_comp,) = self._ax.plot([], [], color="#c33",
                                         lw=2, ls="--",
                                         label="comparison")
        self._ax.axvline(T_ACT_START, color="#88a", lw=0.7, ls=":")
        self._ax.axvline(T_ACT_END, color="#88a", lw=0.7, ls=":")
        self._ax.legend(loc="upper right", fontsize=8)
        layout.addWidget(self._canvas)

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
        approach = (APPROACH_FROM_ABOVE if self.radio_above.isChecked()
                    else APPROACH_FROM_BELOW)
        experiment_type = (SAV_EXPERIMENT if self.radio_sav.isChecked()
                           else MAX_EXPERIMENT)
        return {
            "participant_id": self.edit_pid.text(),
            "user_weight": self.edit_weight.text(),
            "test_mode": PEAK_TIME_TEST,
            "experiment_type": experiment_type,
            "approach": approach,
            "left_port": self.edit_lport.text(),
            "right_port": self.edit_rport.text(),
            "firmware": self.edit_fw.text(),
            "mode": mode,
        }

    def _on_experiment_type_changed(self, *_):
        """Refresh reference label when the experiment radio toggles."""
        self._refresh_reference_label()

    def _current_experiment_type(self) -> str:
        return (SAV_EXPERIMENT if self.radio_sav.isChecked()
                else MAX_EXPERIMENT)

    def _current_total_sweeps(self) -> int:
        return (SAV_TOTAL_SWEEPS
                if self._current_experiment_type() == SAV_EXPERIMENT
                else MAX_TOTAL_SWEEPS)

    def _refresh_reference_label(self):
        if not hasattr(self, "lbl_ref"):
            return
        if self._current_experiment_type() == SAV_EXPERIMENT:
            self.lbl_ref.setText(
                f"Reference peak_tn: {SAV_REFERENCE_PEAK_TN:.3f}Nm/kg   "
                f"(t_peak={DEFAULT_T_PEAK:.1f}% held constant)")
            self.lbl_comp.setText("Comparison peak_tn: --")
        else:
            self.lbl_ref.setText(
                f"Reference t_peak: {DEFAULT_T_PEAK:.1f}%   "
                f"(start={T_ACT_START:.1f}%  end={T_ACT_END:.1f}%)")
            self.lbl_comp.setText("Comparison t_peak: --")
        # Update sweep total in the progress label
        if hasattr(self, "lbl_progress"):
            self.lbl_progress.setText(
                f"Trial: --   Sweep: --/{self._current_total_sweeps()}")

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
        self._mode_active = "fam"
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
        self._mode_active = "perception"
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

        # Familiarization adjust: only while running familiarization
        fam_running = running and self._mode_active == "fam"
        self.btn_inc.setEnabled(fam_running)
        self.btn_dec.setEnabled(fam_running)

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
                self._mode_active = None
                self.lbl_phase.setText("Phase: \u2014")
                self.lbl_phase.setStyleSheet("background:#ddd; padding:4px;")
                self.lbl_catch.setText("")
                self._update_button_states()
                # If we were waiting on shutdown to close, do it now
                if self._closing:
                    self.close()

        elif t == "trial_info":
            trial = msg.get("trial", "--")
            sweep = msg.get("sweep", "--")
            ref = msg.get("reference", DEFAULT_T_PEAK)
            comp = msg.get("comparison", "--")
            var_label = msg.get("var_label", "t_peak")
            var_units = msg.get("var_units", "% gait")
            total_sweeps = msg.get("total_sweeps",
                                   self._current_total_sweeps())
            fmt = ".3f" if var_units == "Nm/kg" else ".1f"
            self.lbl_progress.setText(
                f"Trial: {trial}   Sweep: {sweep}/{total_sweeps}")
            try:
                self.lbl_comp.setText(
                    f"Comparison {var_label}: {comp:{fmt}}{var_units}   "
                    f"(\u0394 vs ref = {comp - ref:+{fmt}}{var_units})")
            except (TypeError, ValueError):
                self.lbl_comp.setText(
                    f"Comparison {var_label}: {comp}")

        elif t == "condition_announce":
            label = msg.get("label", "Condition")
            self.lbl_condition.setText(label)
            if msg.get("is_practice"):
                self.lbl_condition.setStyleSheet(
                    "background:#fff5cc; padding:6px; "
                    "border:1px solid #cc8;")
            else:
                self.lbl_condition.setStyleSheet(
                    "background:#e8f0ff; padding:6px; "
                    "border:1px solid #88a;")

        elif t == "catch_flag":
            if msg.get("is_catch"):
                self.lbl_catch.setText("CATCH TRIAL")
            else:
                self.lbl_catch.setText("")

        elif t == "trial_phase":
            phase = msg.get("phase", "")
            label = msg.get("label", "")
            value = msg.get("value", msg.get("t_peak", None))
            var_label = msg.get("var_label", "t_peak")
            var_units = msg.get("var_units", "% gait")
            extra = ""
            if value is not None:
                fmt = ".3f" if var_units == "Nm/kg" else ".1f"
                try:
                    extra = f"  ({var_label}={value:{fmt}}{var_units})"
                except (TypeError, ValueError):
                    extra = f"  ({var_label}={value})"
            colors = {
                "warmup_light":   ("Warm-up (light)",      "#ddd"),
                "warmup_collins": ("Warm-up (Collins)",    "#ddd"),
                "timing_A":       (f"Timing A {label}{extra}", "#cce0ff"),
                "timing_B":       (f"Timing B {label}{extra}", "#ffd9b3"),
                "response_wait":  ("Awaiting response",    "#c8eccc"),
                "rest":           ("Rest",                 "#eee"),
            }
            text, bg = colors.get(phase, (phase, "#ddd"))
            self.lbl_phase.setText(f"Phase: {text}")
            self.lbl_phase.setStyleSheet(
                f"background:{bg}; padding:4px;")

        elif t == "stride_progress":
            k = msg.get("k", 0); n = msg.get("n", 0)
            ph = msg.get("phase", "")
            self.lbl_stride.setText(f"Stride {k}/{n}  (phase {ph})")

        elif t == "profile_preview":
            ref = msg.get("ref")
            comp = msg.get("comp")
            ref_lbl = msg.get("ref_label", "reference")
            comp_lbl = msg.get("comp_label", "comparison")
            try:
                if ref is not None:
                    self._ln_ref.set_data(ref[0], ref[1])
                    self._ln_ref.set_label(ref_lbl)
                if comp is not None:
                    self._ln_comp.set_data(comp[0], comp[1])
                    self._ln_comp.set_label(comp_lbl)
                self._ax.relim(); self._ax.autoscale_view(scaley=True)
                self._ax.set_xlim(0, 100)
                self._ax.legend(loc="upper right", fontsize=8)
                self._canvas.draw_idle()
            except Exception as exc:
                self._append_log(f"preview draw failed: {exc}")

        elif t == "awaiting_response":
            self._awaiting_response = True
            self._update_button_states()
            self.lbl_phase.setText("Phase: \u25B6 RESPOND  Same / Different")
            self.lbl_phase.setStyleSheet(
                "background:#9bd99e; padding:6px; "
                "font-weight:bold;")
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
    window.resize(820, 980)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

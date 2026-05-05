#!/usr/bin/env python3
"""
Analysis2.py — Comprehensive ExoBoot diagnostics
=================================================

Reads the per-sample CSV files written by ``exo_logger.ExoLogger``
(filenames like ``P001_Familiarization_L_2026-04-28_*_full.csv``) and
produces every plot needed to figure out WHY a run did or did not work.

Three modes
-----------
1. **Single file**::

       python DataLog/analysis/Analysis2.py path/to/P001_Familiarization_L_..._full.csv

2. **Latest single file** (most recent ``*_full.csv`` in ``data/``)::

       python DataLog/analysis/Analysis2.py --latest

3. **Side-by-side L vs R comparison** (most recent matching pair)::

       python DataLog/analysis/Analysis2.py --pair
       python DataLog/analysis/Analysis2.py --pair --participant P001 --phase Familiarization

Outputs go in ``<csv_stem>_plots/`` next to the CSV (or
``data/<participant>_<phase>_<ts>_LRplots/`` for paired runs).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


# ---------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------
DATA_DIR = (Path(__file__).resolve().parent.parent.parent / "data")

FNAME_RE = re.compile(
    r"^(?P<pid>[^_]+)_(?P<phase>[^_]+)_(?P<side>[LR])_"
    r"(?P<ts>\d{4}-\d{2}-\d{2}_\d{2}h\d{2}m\d{2}s)_full\.csv$"
)


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["t_s"] = (df.state_time_ms - df.state_time_ms.iloc[0]) / 1000.0
    return df


def find_latest(data_dir: Path = DATA_DIR) -> Path:
    cands = sorted(data_dir.glob("*_full.csv"),
                   key=lambda p: p.stat().st_mtime)
    if not cands:
        sys.exit(f"No *_full.csv found in {data_dir}")
    return cands[-1]


def find_pair(participant: Optional[str] = None,
              phase: Optional[str] = None,
              ts: Optional[str] = None,
              data_dir: Path = DATA_DIR) -> Tuple[Path, Path]:
    """Find the most recent L/R pair matching the filters."""
    files = []
    for p in data_dir.glob("*_full.csv"):
        m = FNAME_RE.match(p.name)
        if not m:
            continue
        if participant and m.group("pid") != participant:
            continue
        if phase and m.group("phase") != phase:
            continue
        if ts and m.group("ts") != ts:
            continue
        files.append((p, m.groupdict()))
    if not files:
        sys.exit("No matching files.")
    # group by (pid, phase, ts)
    groups: dict = {}
    for p, g in files:
        key = (g["pid"], g["phase"], g["ts"])
        groups.setdefault(key, {})[g["side"]] = p
    # pick the newest fully-paired group
    paired = [(k, v) for k, v in groups.items()
              if "L" in v and "R" in v]
    if not paired:
        sys.exit("No L/R pair found. Available groups:\n  " +
                 "\n  ".join(f"{k} → sides {list(v)}" for k, v in groups.items()))
    paired.sort(key=lambda kv: max(kv[1]["L"].stat().st_mtime,
                                   kv[1]["R"].stat().st_mtime))
    _, sides = paired[-1]
    return sides["L"], sides["R"]


# ---------------------------------------------------------------------
#  Single-file plotting
# ---------------------------------------------------------------------
def plot_torque_profile(df: pd.DataFrame, out: Path, title_suffix=""):
    """Peng-style torque-vs-gait-cycle overlay."""
    fig, ax = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
    strides = sorted(df.stride_idx_in_phase.unique())
    n_overlaid = 0
    for s in strides:
        sub = df[(df.stride_idx_in_phase == s) & (df.percent_gait >= 0)]
        if len(sub) < 10:
            continue
        ax[0].plot(sub.percent_gait, sub.tau_Nm, lw=0.5, alpha=0.35)
        ax[1].plot(sub.percent_gait, sub.current_cmd_mA, lw=0.5, alpha=0.35)
        n_overlaid += 1
    # mean curve binned by % gait
    mask = df.percent_gait >= 0
    if mask.sum() > 0:
        bins = np.arange(0, 101, 1)
        cats = pd.cut(df.loc[mask, "percent_gait"], bins)
        x = bins[:-1] + 0.5
        ax[0].plot(x, df.loc[mask].groupby(cats, observed=False)["tau_Nm"].mean().values,
                   "k-", lw=2, label="mean cmd τ")
        ax[1].plot(x, df.loc[mask].groupby(cats, observed=False)["current_cmd_mA"].mean().values,
                   "k-", lw=2, label="mean cmd current")
        ax[1].plot(x, df.loc[mask].groupby(cats, observed=False)["mot_cur_meas_mA"].mean().values,
                   "r-", lw=1.5, alpha=0.8, label="mean MEASURED current")
    # Profile reference verticals
    if not df.t_peak.isna().all() and df.t_peak.iloc[-1] > 0:
        tp = df.t_peak.iloc[-1]; tr = df.t_rise.iloc[-1]; tf = df.t_fall.iloc[-1]
        for a in ax:
            a.axvline(tp - tr, color="g", ls="--", alpha=0.5, label="t_onset")
            a.axvline(tp,      color="orange", ls="--", alpha=0.5, label="t_peak")
            a.axvline(tp + tf, color="b", ls="--", alpha=0.5, label="t_end")
    ax[0].set_ylabel("Commanded τ (Nm)"); ax[0].legend(loc="upper right", fontsize=8)
    ax[1].set_ylabel("Current (mA)");    ax[1].legend(loc="upper right", fontsize=8)
    ax[1].set_xlabel("Gait cycle (%)")
    ax[0].set_title(f"Torque profile — {n_overlaid} strides overlaid {title_suffix}")
    fig.tight_layout()
    fig.savefig(out / "torque_profile.png", dpi=120)
    plt.close(fig)


def plot_controller_timeline(df: pd.DataFrame, out: Path, title_suffix=""):
    fig, ax = plt.subplots(4, 1, figsize=(12, 10), sharex=True)
    modes = df.controller_mode.astype("category")
    ax[0].plot(df.t_s, modes.cat.codes, drawstyle="steps-post", lw=0.6)
    ax[0].set_yticks(range(len(modes.cat.categories)))
    ax[0].set_yticklabels(modes.cat.categories, fontsize=8)
    ax[0].set_ylabel("Controller mode"); ax[0].grid(alpha=0.3)
    ax[1].plot(df.t_s, df.percent_gait, lw=0.6)
    ax[1].set_ylabel("% gait"); ax[1].set_ylim(-5, 105); ax[1].grid(alpha=0.3)
    ax[2].plot(df.t_s, df.tau_Nm, lw=0.6, color="C0")
    ax[2].set_ylabel("Commanded τ (Nm)", color="C0"); ax[2].grid(alpha=0.3)
    ax[3].plot(df.t_s, df.current_cmd_mA, lw=0.6, label="commanded")
    ax[3].plot(df.t_s, df.mot_cur_meas_mA, "r-", lw=0.4, alpha=0.7, label="measured")
    ax[3].set_ylabel("Current (mA)"); ax[3].legend(loc="upper right", fontsize=8)
    ax[3].set_xlabel("Time (s)"); ax[3].grid(alpha=0.3)
    fig.suptitle(f"Controller timeline {title_suffix}")
    fig.tight_layout()
    fig.savefig(out / "controller_timeline.png", dpi=120)
    plt.close(fig)


def plot_hs_diagnostics(df: pd.DataFrame, out: Path, title_suffix=""):
    """The plot that answers 'why isn't HS firing?'"""
    fig, ax = plt.subplots(4, 1, figsize=(14, 10), sharex=True)
    ax[0].plot(df.t_s, df.gyroz_signed, lw=0.5, label="signed gyroz")
    if len(df):
        ax[0].axhline(df.arm_thr.iloc[0], color="orange", ls="--", label="ARM thr")
        ax[0].axhline(df.trg_thr.iloc[0], color="red",    ls="--", label="TRG thr")
    ax[0].legend(loc="upper right", fontsize=8); ax[0].set_ylabel("gyroz (raw bits)")
    ax[0].grid(alpha=0.3)
    ax[1].plot(df.t_s, df.hs_armed, drawstyle="steps-post", lw=0.7)
    ax[1].set_ylabel("armed (0/1)"); ax[1].set_ylim(-0.1, 1.1); ax[1].grid(alpha=0.3)
    ax[2].plot(df.t_s, df.armed_time_ms, lw=0.5, label="armed time")
    ax[2].plot(df.t_s, df.refractory_ms, lw=0.8, color="purple", label="refractory")
    ax[2].set_ylabel("ms"); ax[2].legend(loc="upper right", fontsize=8); ax[2].grid(alpha=0.3)
    ax[3].plot(df.t_s, df.num_gait, drawstyle="steps-post", lw=0.8)
    ax[3].set_ylabel("num_gait"); ax[3].set_xlabel("Time (s)"); ax[3].grid(alpha=0.3)
    triggers = df[df.seg_trigger == 1]
    for a in ax:
        for t in triggers.t_s:
            a.axvline(t, color="g", alpha=0.25)
    fig.suptitle(f"HS diagnostics — {len(triggers)} TRIGGERs, "
                 f"final num_gait={int(df.num_gait.iloc[-1] if len(df) else 0)} "
                 f"{title_suffix}")
    fig.tight_layout()
    fig.savefig(out / "hs_diagnostics.png", dpi=120)
    plt.close(fig)


def plot_kinematics(df: pd.DataFrame, out: Path, title_suffix=""):
    fig, ax = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    ax[0].plot(df.t_s, df.ank_ang_zeroed, lw=0.6)
    ax[0].set_ylabel("ank_ang (zeroed)"); ax[0].grid(alpha=0.3)
    ax[1].plot(df.t_s, df.mot_ang_zeroed, lw=0.6)
    ax[1].set_ylabel("mot_ang (zeroed)"); ax[1].grid(alpha=0.3)
    ax[2].plot(df.t_s, df.wm_wa, lw=0.6)
    ax[2].set_ylabel("wm/wa"); ax[2].set_xlabel("Time (s)"); ax[2].grid(alpha=0.3)
    fig.suptitle(f"Kinematics {title_suffix}")
    fig.tight_layout()
    fig.savefig(out / "kinematics.png", dpi=120)
    plt.close(fig)


# ---------------------------------------------------------------------
#  NEW: startup_zoom — first N seconds of every key signal
# ---------------------------------------------------------------------
def plot_startup_zoom(df: pd.DataFrame, out: Path,
                      title_suffix: str = "",
                      window_s: float = 8.0):
    """Zoom on the first ``window_s`` of the run.  Critical for
    diagnosing 'felt a force right when I pressed Start' events.
    Six rows: controller mode, % gait, cmd-vs-meas current,
    pos setpoint vs actual w/ error overlay, battery V/A, status regs.
    """
    sub = df[df.t_s <= window_s].copy()
    if len(sub) == 0:
        return
    fig, ax = plt.subplots(6, 1, figsize=(13, 13), sharex=True)

    modes = sub.controller_mode.astype("category")
    ax[0].plot(sub.t_s, modes.cat.codes, drawstyle="steps-post", lw=0.8)
    ax[0].set_yticks(range(len(modes.cat.categories)))
    ax[0].set_yticklabels(modes.cat.categories, fontsize=8)
    ax[0].set_ylabel("mode"); ax[0].grid(alpha=0.3)

    ax[1].plot(sub.t_s, sub.percent_gait, lw=0.6)
    ax[1].set_ylabel("% gait"); ax[1].set_ylim(-5, 105); ax[1].grid(alpha=0.3)

    ax[2].plot(sub.t_s, sub.current_cmd_mA, lw=0.7, label="cmd")
    ax[2].plot(sub.t_s, sub.mot_cur_meas_mA, "r-", lw=0.5, alpha=0.8, label="meas")
    ax[2].set_ylabel("current (mA)"); ax[2].legend(loc="upper right", fontsize=8)
    ax[2].grid(alpha=0.3)

    if "mot_pos_setpoint" in sub.columns:
        ax[3].plot(sub.t_s, sub.mot_pos_setpoint, lw=0.7, label="setpoint")
        ax[3].plot(sub.t_s, sub.mot_ang_raw, "r-", lw=0.5, alpha=0.8, label="actual")
        ax3b = ax[3].twinx()
        ax3b.plot(sub.t_s, sub.mot_pos_error, color="green", lw=0.5,
                  alpha=0.6, label="error")
        ax3b.set_ylabel("pos err (ticks)", color="green")
        ax[3].set_ylabel("motor pos (ticks)")
        ax[3].legend(loc="upper left", fontsize=8)
    ax[3].grid(alpha=0.3)

    if "batt_volt_mV" in sub.columns:
        ax[4].plot(sub.t_s, sub.batt_volt_mV, lw=0.6, label="batt V (mV)")
        ax4b = ax[4].twinx()
        ax4b.plot(sub.t_s, sub.batt_curr_mA, color="red", lw=0.5,
                  alpha=0.7, label="batt I (mA)")
        ax4b.set_ylabel("batt I (mA)", color="red")
        ax[4].set_ylabel("batt V (mV)")
    ax[4].grid(alpha=0.3)

    if "status_mn" in sub.columns:
        ax[5].plot(sub.t_s, sub.status_mn, lw=0.7, label="status_mn")
        ax[5].plot(sub.t_s, sub.status_ex, lw=0.7, label="status_ex")
        ax[5].plot(sub.t_s, sub.status_re, lw=0.7, label="status_re")
        ax[5].legend(loc="upper right", fontsize=8)
    ax[5].set_ylabel("status regs")
    ax[5].set_xlabel("Time (s)"); ax[5].grid(alpha=0.3)

    fig.suptitle(f"Startup zoom (first {window_s:.0f} s) {title_suffix}")
    fig.tight_layout()
    fig.savefig(out / "startup_zoom.png", dpi=120)
    plt.close(fig)


# ---------------------------------------------------------------------
#  NEW: fault detector — "big cmd, no meas" + status flags
# ---------------------------------------------------------------------
def plot_faults(df: pd.DataFrame, out: Path, title_suffix: str = ""):
    """Detect intervals where a large current was commanded but the
    motor failed to deliver it (motor fault / disconnect / brownout).
    """
    fig, ax = plt.subplots(4, 1, figsize=(13, 9), sharex=True)

    cmd_abs = df.current_cmd_mA.abs()
    meas_abs = df.mot_cur_meas_mA.abs()
    is_fault = (cmd_abs > 500) & (meas_abs < 100)

    ax[0].plot(df.t_s, cmd_abs, lw=0.5, label="|cmd|")
    ax[0].plot(df.t_s, meas_abs, "r-", lw=0.4, alpha=0.7, label="|meas|")
    ax[0].set_ylabel("current (mA)"); ax[0].legend(fontsize=8)
    ax[0].grid(alpha=0.3)

    ax[1].plot(df.t_s, is_fault.astype(int), drawstyle="steps-post", lw=0.7,
               color="red")
    ax[1].set_ylabel("fault flag"); ax[1].set_ylim(-0.1, 1.1)
    ax[1].grid(alpha=0.3)

    if "batt_volt_mV" in df.columns:
        ax[2].plot(df.t_s, df.batt_volt_mV, lw=0.5)
        ax[2].set_ylabel("batt V (mV)")
    ax[2].grid(alpha=0.3)

    if "status_mn" in df.columns:
        any_status = ((df.status_mn != 0) | (df.status_ex != 0) |
                      (df.status_re != 0))
        ax[3].plot(df.t_s, any_status.astype(int), drawstyle="steps-post",
                   lw=0.7, color="purple")
        ax[3].set_ylabel("any status flag")
        ax[3].set_ylim(-0.1, 1.1)
    ax[3].set_xlabel("Time (s)"); ax[3].grid(alpha=0.3)

    n_fault = int(is_fault.sum())
    fig.suptitle(f"Fault detector — {n_fault} fault samples "
                 f"({n_fault/max(len(df),1)*100:.1f}% of run)  {title_suffix}")
    fig.tight_layout()
    fig.savefig(out / "faults.png", dpi=120)
    plt.close(fig)


# ---------------------------------------------------------------------
#  NEW: battery / status timeline (full run)
# ---------------------------------------------------------------------
def plot_battery_status(df: pd.DataFrame, out: Path, title_suffix: str = ""):
    if "batt_volt_mV" not in df.columns:
        return
    fig, ax = plt.subplots(3, 1, figsize=(13, 8), sharex=True)
    ax[0].plot(df.t_s, df.batt_volt_mV, lw=0.6)
    ax[0].set_ylabel("batt V (mV)"); ax[0].grid(alpha=0.3)
    ax[1].plot(df.t_s, df.batt_curr_mA, lw=0.6, color="red")
    ax[1].set_ylabel("batt I (mA)"); ax[1].grid(alpha=0.3)
    if "temp_C" in df.columns:
        ax2b = ax[1].twinx()
        ax2b.plot(df.t_s, df.temp_C, lw=0.5, color="orange", alpha=0.7)
        ax2b.set_ylabel("temp (C)", color="orange")
    ax[2].plot(df.t_s, df.status_mn, lw=0.6, label="mn")
    ax[2].plot(df.t_s, df.status_ex, lw=0.6, label="ex")
    ax[2].plot(df.t_s, df.status_re, lw=0.6, label="re")
    ax[2].legend(fontsize=8); ax[2].set_ylabel("status regs")
    ax[2].set_xlabel("Time (s)"); ax[2].grid(alpha=0.3)
    fig.suptitle(f"Battery / status timeline {title_suffix}")
    fig.tight_layout()
    fig.savefig(out / "battery_status.png", dpi=120)
    plt.close(fig)


# ---------------------------------------------------------------------
#  NEW: latency diagnostics — localise where lag lives
# ---------------------------------------------------------------------
def latency_diagnostics(df: pd.DataFrame, out: Path,
                        title_suffix: str = "") -> dict:
    """Compute and plot four latency metrics and return a stats dict.

    1. ARM → TRIGGER latency   (= ``armed_time_ms`` sampled at trigger rows)
    2. ``current_dur − expected_dur`` drift per stride (estimator bias)
    3. Cross-correlation lag of ``current_cmd_mA`` × ``mot_cur_meas_mA``
       inside the actuation window (motor / current-loop lag)
    4. Δ % gait between mean-cmd peak and mean-meas peak
       (combined effect of estimator + motor lag on the delivered curve)

    Metrics 3 & 4 require torque to have been commanded; on exo-off
    runs only metrics 1 & 2 are populated. Returns a dict that
    :func:`write_summary` can append to ``summary.txt``.
    """
    stats: dict = {}
    if len(df) == 0:
        return stats

    triggers = df[df.seg_trigger == 1]
    n_trig = len(triggers)
    stats["n_triggers"] = n_trig

    # Sample armed_time / current_dur from the row IMMEDIATELY before the
    # trigger row, because the trigger handler resets armed_time to -1
    # and updates expected_dur on the trigger sample itself.
    trig_idx = triggers.index.values
    pre_idx = np.array([i - 1 for i in trig_idx if i > 0], dtype=int)

    # ---- Metric 1 ----------------------------------------------------
    if len(pre_idx) > 0:
        at = df.armed_time_ms.iloc[pre_idx].values.astype(float)
        at = at[at > 0]   # drop -1 sentinels (rare, e.g. very first event)
        if len(at) > 0:
            stats["arm2trig_ms_median"] = float(np.median(at))
            stats["arm2trig_ms_mean"]   = float(np.mean(at))
            stats["arm2trig_ms_p5"]     = float(np.percentile(at, 5))
            stats["arm2trig_ms_p95"]    = float(np.percentile(at, 95))
            stats["arm2trig_ms_std"]    = float(np.std(at))

    # ---- Metric 2 ----------------------------------------------------
    # At trigger row i, the prediction USED during the stride that just
    # ended is expected_dur_ms one sample earlier (the trigger row
    # itself updates expected_dur from the new current_dur).
    drift_ms: list[float] = []
    if len(pre_idx) > 0:
        for i in pre_idx:
            cd = df.current_dur_ms.iloc[i + 1]   # trigger row has the new dur
            ed_prev = df.expected_dur_ms.iloc[i]
            if cd > 0 and ed_prev > 0:
                drift_ms.append(float(cd - ed_prev))
    drift = np.asarray(drift_ms, dtype=float)
    if len(drift) > 0:
        stats["dur_drift_ms_median"] = float(np.median(drift))
        stats["dur_drift_ms_mean"]   = float(np.mean(drift))
        stats["dur_drift_ms_std"]    = float(np.std(drift))

    # ---- Metric 3 ----------------------------------------------------
    has_torque = df.current_cmd_mA.abs().max() > 100
    motor_lag_list: list[float] = []
    if has_torque and "stride_idx_in_phase" in df.columns:
        for s in df.stride_idx_in_phase.unique():
            sub = df[(df.stride_idx_in_phase == s) &
                     (df.percent_gait >= 20) &
                     (df.percent_gait <= 70)]
            if len(sub) < 20:
                continue
            x = sub.current_cmd_mA.values.astype(float)
            y = sub.mot_cur_meas_mA.values.astype(float)
            if x.std() < 50 or y.std() < 50:
                continue
            x = x - x.mean(); y = y - y.mean()
            corr = np.correlate(y, x, mode="full")
            lags = np.arange(-len(x) + 1, len(x))
            mask = (lags >= -20) & (lags <= 60)   # ±200 / +600 ms @100 Hz
            if mask.sum() == 0:
                continue
            best = lags[mask][int(np.argmax(corr[mask]))]
            motor_lag_list.append(best * 10.0)    # 10 ms / sample
    motor_lag = np.asarray(motor_lag_list, dtype=float)
    if len(motor_lag) > 0:
        stats["motor_lag_ms_median"] = float(np.median(motor_lag))
        stats["motor_lag_ms_p5"]     = float(np.percentile(motor_lag, 5))
        stats["motor_lag_ms_p95"]    = float(np.percentile(motor_lag, 95))

    # ---- Metric 4 ----------------------------------------------------
    cmd_mean = meas_mean = None; xx = None
    peak_offset_pct = None
    if has_torque:
        m = df.percent_gait >= 0
        if m.sum() > 0:
            bins = np.arange(0, 101, 1); xx = bins[:-1] + 0.5
            cats = pd.cut(df.loc[m, "percent_gait"], bins)
            cmd_mean = df.loc[m].groupby(cats, observed=False)["current_cmd_mA"].mean().values
            meas_mean = df.loc[m].groupby(cats, observed=False)["mot_cur_meas_mA"].mean().values
            sgn = 1 if abs(np.nanmin(cmd_mean)) <= abs(np.nanmax(cmd_mean)) else -1
            try:
                cmd_pg  = float(xx[int(np.nanargmax(sgn * cmd_mean))])
                meas_pg = float(xx[int(np.nanargmax(sgn * meas_mean))])
                peak_offset_pct = meas_pg - cmd_pg
                stats["cmd_peak_pct_gait"]   = cmd_pg
                stats["meas_peak_pct_gait"]  = meas_pg
                stats["peak_offset_pct_gait"] = peak_offset_pct
            except (ValueError, IndexError):
                pass

    # ---- Plot --------------------------------------------------------
    fig, ax = plt.subplots(2, 2, figsize=(13, 9))

    if len(pre_idx) > 0:
        at = df.armed_time_ms.iloc[pre_idx].values.astype(float)
        at = at[at > 0]
        if len(at) > 0:
            ax[0, 0].hist(at, bins=30, color="C0", edgecolor="black", alpha=0.8)
            ax[0, 0].axvline(np.median(at), color="red", ls="--",
                             label=f"median={np.median(at):.0f} ms")
            ax[0, 0].set_xlabel("ARM → TRIGGER latency (ms)")
            ax[0, 0].set_ylabel("count")
            ax[0, 0].legend(fontsize=8)
            ax[0, 0].set_title(f"Metric 1: detector latency  (n={len(at)})")
        else:
            ax[0, 0].set_title("Metric 1: armed_time unavailable")
    else:
        ax[0, 0].set_title("Metric 1: no triggers")
    ax[0, 0].grid(alpha=0.3)

    if len(drift) > 0:
        ax[0, 1].plot(np.arange(len(drift)), drift, "o-", lw=0.5, ms=3)
        ax[0, 1].axhline(0, color="k", lw=0.5)
        ax[0, 1].axhline(np.median(drift), color="red", ls="--",
                         label=f"median={np.median(drift):.0f} ms")
        ax[0, 1].set_xlabel("stride #")
        ax[0, 1].set_ylabel("current_dur − expected_dur (ms)")
        ax[0, 1].set_title("Metric 2: stride-duration estimator drift")
        ax[0, 1].legend(fontsize=8)
    else:
        ax[0, 1].set_title("Metric 2: insufficient strides")
    ax[0, 1].grid(alpha=0.3)

    if has_torque and cmd_mean is not None and xx is not None:
        ax[1, 0].plot(xx, cmd_mean, "C0", lw=2, label="cmd mean")
        ax[1, 0].plot(xx, meas_mean, "r-", lw=1.5, alpha=0.85, label="meas mean")
        if peak_offset_pct is not None:
            ax[1, 0].axvline(stats["cmd_peak_pct_gait"], color="C0", ls=":")
            ax[1, 0].axvline(stats["meas_peak_pct_gait"], color="r", ls=":")
            ax[1, 0].set_title(
                f"Metric 4: cmd vs meas peak — Δ={peak_offset_pct:+.1f}% gait")
        else:
            ax[1, 0].set_title("Metric 4: peaks not resolvable")
        ax[1, 0].set_xlabel("% gait")
        ax[1, 0].set_ylabel("current (mA)")
        ax[1, 0].legend(fontsize=8)
    else:
        ax[1, 0].set_title("Metric 4: no torque commanded")
    ax[1, 0].grid(alpha=0.3)

    if len(motor_lag) > 0:
        ax[1, 1].hist(motor_lag, bins=20, color="C2",
                      edgecolor="black", alpha=0.8)
        ax[1, 1].axvline(np.median(motor_lag), color="red", ls="--",
                         label=f"median={np.median(motor_lag):.0f} ms")
        ax[1, 1].set_xlabel("cmd→meas xcorr lag (ms)")
        ax[1, 1].set_ylabel("count")
        ax[1, 1].legend(fontsize=8)
        ax[1, 1].set_title(f"Metric 3: motor / current-loop lag  (n={len(motor_lag)})")
    else:
        ax[1, 1].set_title("Metric 3: no torque commanded")
    ax[1, 1].grid(alpha=0.3)

    fig.suptitle(f"Latency diagnostics {title_suffix}")
    fig.tight_layout()
    fig.savefig(out / "latency.png", dpi=120)
    plt.close(fig)
    return stats


def write_summary(df: pd.DataFrame, out: Path, label: str = "",
                  latency: dict | None = None) -> str:
    lines = [f"=== Diagnostic summary {label} ==="]
    if len(df) == 0:
        lines.append("EMPTY FILE")
        text = "\n".join(lines)
        (out / "summary.txt").write_text(text + "\n"); return text
    dur = df.t_s.iloc[-1]
    lines.append(f"File rows           : {len(df)}")
    lines.append(f"Duration            : {dur:.1f} s  (≈ {len(df)/max(dur,1e-6):.1f} Hz)")
    lines.append(f"Side                : {df.side.iloc[0]}   "
                 f"Boot ID: {df.boot_id.iloc[0]}   "
                 f"Phase: {df.phase.iloc[0]}")
    lines.append(f"Participant         : {df.participant_id.iloc[0]}   "
                 f"Weight: {df.weight_kg.iloc[0]} kg")
    lines.append(f"Profile             : t_rise={df.t_rise.iloc[-1]}  "
                 f"t_fall={df.t_fall.iloc[-1]}  t_peak={df.t_peak.iloc[-1]}  "
                 f"peak_τ_norm={df.peak_torque_norm.iloc[-1]}")
    lines.append(f"Final num_gait      : {int(df.num_gait.iloc[-1])}")
    lines.append(f"TRIGGER events      : {int(df.seg_trigger.sum())}")
    lines.append(f"% time armed        : {100*df.hs_armed.mean():.1f}%")
    lines.append(f"gyroz range         : {df.gyroz_signed.min():.0f} .. "
                 f"{df.gyroz_signed.max():.0f}")
    lines.append(f"ARM thr / TRG thr   : {df.arm_thr.iloc[0]} / {df.trg_thr.iloc[0]}")
    lines.append("Controller-mode time-share:")
    for m, frac in (df.controller_mode.value_counts(normalize=True) * 100).items():
        lines.append(f"   {m:25s} {frac:5.1f}%")
    lines.append(f"Cmd τ range         : {df.tau_Nm.min():.2f} .. {df.tau_Nm.max():.2f} Nm")
    lines.append(f"Cmd current range   : {df.current_cmd_mA.min():.0f} .. "
                 f"{df.current_cmd_mA.max():.0f} mA")
    lines.append(f"Meas current range  : {df.mot_cur_meas_mA.min():.0f} .. "
                 f"{df.mot_cur_meas_mA.max():.0f} mA")

    # ---- Diagnostic flags --------------------------------------------
    flags = []
    if df.percent_gait.max() < 0:
        flags.append("⚠ percent_gait never advanced — HS detection FAILED.")
    if abs(df.tau_Nm).max() < 0.01:
        flags.append("⚠ Commanded τ ≈ 0 — Collins profile never ran.")
    if abs(df.mot_cur_meas_mA).max() < 50:
        flags.append("⚠ Measured current ≈ 0 — motor never delivered torque.")
    if df.num_gait.iloc[-1] == 0 and df.hs_armed.sum() > 0:
        flags.append("⚠ Armed but never TRIGGERED — lower |trg_thr| or lengthen MIN_ARMED_DURATION.")
    if df.controller_mode.eq("idle_position").mean() > 0.95:
        flags.append("⚠ >95 % of time in idle_position — boot never left pre-gait state.")
    if "mot_pos_error" in df.columns:
        # Only meaningful in modes that actually use position control.
        # In current-control walking, mot_pos_setpoint stays at 0 so
        # mot_pos_error == -mot_ang_zeroed and is huge but irrelevant.
        pos_modes = ("idle_position", "position_early_stance",
                     "position_late_stance", "encoder_check", "zero_boot")
        pos_mask = df.controller_mode.isin(pos_modes) if "controller_mode" in df.columns else None
        if pos_mask is not None and pos_mask.any():
            max_err = df.loc[pos_mask, "mot_pos_error"].abs().max()
            lines.append(f"Max |pos error|     : {max_err:.0f} ticks  (position-control phases only)")
            if max_err > 1000:
                flags.append(
                    f"⚠ Position error spiked to {max_err:.0f} ticks "
                    "— position controller asked for a step the cable couldn't follow. "
                    "This is the classic 'sudden yank' signature.")
        else:
            lines.append("Max |pos error|     : n/a (no position-control phases in this run)")
    if "batt_volt_mV" in df.columns and df.batt_volt_mV.max() > 0:
        bmin = df.batt_volt_mV[df.batt_volt_mV > 0].min()
        bmax = df.batt_volt_mV.max()
        lines.append(f"Batt V range        : {bmin:.0f} .. {bmax:.0f} mV")
        if bmin < 30000 and bmin > 0:   # nominal pack ~36-42 V
            flags.append(
                f"⚠ Battery sagged to {bmin:.0f} mV — likely brownout under load.")
    if "status_mn" in df.columns:
        # status_mn is normally non-zero (it's a state register, not a
        # fault flag).  Only flag if status_ex / status_re report
        # something, OR if mn changes mid-run (rare) which can indicate
        # a state transition into fault.
        n_fault_ex = int((df.status_ex != 0).sum())
        n_fault_re = int((df.status_re != 0).sum())
        mn_unique = df.status_mn.nunique()
        if n_fault_ex > 0 or n_fault_re > 0:
            flags.append(
                f"⚠ Firmware fault status: ex={n_fault_ex} re={n_fault_re} "
                "samples — motor / regulator fault reported.")
        if mn_unique > 1:
            transitions = (df.status_mn.diff().abs() > 0).sum()
            flags.append(
                f"⚠ status_mn changed {transitions} times during run "
                "— device entered/left a fault or special state.")
    if "current_cmd_mA" in df.columns and "mot_cur_meas_mA" in df.columns:
        is_fault = ((df.current_cmd_mA.abs() > 500) &
                    (df.mot_cur_meas_mA.abs() < 100))
        if is_fault.sum() > 50:
            t_first = df.t_s[is_fault].iloc[0]
            flags.append(
                f"⚠ Motor failed to follow current command (cmd>500, meas<100) "
                f"in {int(is_fault.sum())} samples; first at t={t_first:.2f}s.")
    if flags:
        lines.append("\nFLAGS:")
        lines.extend("  " + f for f in flags)
    else:
        lines.append("\nNo critical flags raised.")

    # ---- Latency block ----------------------------------------------
    if latency:
        lines.append("\nLATENCY:")
        if "arm2trig_ms_median" in latency:
            lines.append(
                f"  ARM→TRIG armed_time      : "
                f"median={latency['arm2trig_ms_median']:.0f}  "
                f"mean={latency['arm2trig_ms_mean']:.0f}  "
                f"std={latency['arm2trig_ms_std']:.0f}  "
                f"5/95 pct={latency['arm2trig_ms_p5']:.0f}/"
                f"{latency['arm2trig_ms_p95']:.0f} ms")
        if "dur_drift_ms_median" in latency:
            lines.append(
                f"  current_dur − expected   : "
                f"median={latency['dur_drift_ms_median']:+.0f}  "
                f"mean={latency['dur_drift_ms_mean']:+.0f}  "
                f"std={latency['dur_drift_ms_std']:.0f} ms  "
                f"(positive = estimator under-predicts)")
        if "motor_lag_ms_median" in latency:
            lines.append(
                f"  cmd→meas xcorr lag       : "
                f"median={latency['motor_lag_ms_median']:+.0f}  "
                f"5/95 pct={latency['motor_lag_ms_p5']:+.0f}/"
                f"{latency['motor_lag_ms_p95']:+.0f} ms")
        if "peak_offset_pct_gait" in latency:
            lines.append(
                f"  cmd vs meas peak %gait   : "
                f"cmd={latency['cmd_peak_pct_gait']:.1f}%  "
                f"meas={latency['meas_peak_pct_gait']:.1f}%  "
                f"Δ={latency['peak_offset_pct_gait']:+.1f}%")

    text = "\n".join(lines)
    (out / "summary.txt").write_text(text + "\n")
    return text


def analyze_single(path: Path) -> pd.DataFrame:
    df = load(path)
    out = path.parent / f"{path.stem}_plots"
    out.mkdir(exist_ok=True)
    print(f"[{path.name}]  → plots in {out}")
    plot_torque_profile(df, out, title_suffix=f"({path.stem})")
    plot_controller_timeline(df, out, title_suffix=f"({path.stem})")
    plot_hs_diagnostics(df, out, title_suffix=f"({path.stem})")
    plot_kinematics(df, out, title_suffix=f"({path.stem})")
    plot_startup_zoom(df, out, title_suffix=f"({path.stem})")
    plot_faults(df, out, title_suffix=f"({path.stem})")
    plot_battery_status(df, out, title_suffix=f"({path.stem})")
    lat = latency_diagnostics(df, out, title_suffix=f"({path.stem})")
    summary = write_summary(df, out, label=f"({path.stem})", latency=lat)
    print(summary)
    return df


# ---------------------------------------------------------------------
#  Side-by-side L/R comparison
# ---------------------------------------------------------------------
def plot_side_by_side(dL: pd.DataFrame, dR: pd.DataFrame,
                      out: Path, label: str = ""):
    """Two columns (L | R), key signals on shared y-axes."""
    fig, ax = plt.subplots(5, 2, figsize=(15, 12), sharex="col")
    for col, (df, name) in enumerate([(dL, "LEFT"), (dR, "RIGHT")]):
        if len(df) == 0:
            ax[0, col].set_title(f"{name}: NO DATA"); continue
        ax[0, col].plot(df.t_s, df.gyroz_signed, lw=0.4)
        ax[0, col].axhline(df.arm_thr.iloc[0], color="orange", ls="--", lw=0.7)
        ax[0, col].axhline(df.trg_thr.iloc[0], color="red", ls="--", lw=0.7)
        ax[0, col].set_title(f"{name}  (boot {df.boot_id.iloc[0]})")
        ax[0, col].set_ylabel("gyroz" if col == 0 else "")
        ax[1, col].plot(df.t_s, df.percent_gait, lw=0.4)
        ax[1, col].set_ylabel("% gait" if col == 0 else "")
        ax[1, col].set_ylim(-5, 105)
        ax[2, col].plot(df.t_s, df.num_gait, drawstyle="steps-post", lw=0.7)
        ax[2, col].set_ylabel("num_gait" if col == 0 else "")
        ax[3, col].plot(df.t_s, df.tau_Nm, lw=0.5)
        ax[3, col].set_ylabel("τ cmd (Nm)" if col == 0 else "")
        ax[4, col].plot(df.t_s, df.current_cmd_mA, lw=0.5, label="cmd")
        ax[4, col].plot(df.t_s, df.mot_cur_meas_mA, "r-", lw=0.4, alpha=0.7, label="meas")
        ax[4, col].set_ylabel("current (mA)" if col == 0 else "")
        ax[4, col].set_xlabel("t (s)")
        ax[4, col].legend(loc="upper right", fontsize=7)
        for r in range(5):
            ax[r, col].grid(alpha=0.3)
    fig.suptitle(f"Side-by-side L vs R   {label}")
    fig.tight_layout()
    fig.savefig(out / "side_by_side.png", dpi=120)
    plt.close(fig)


def plot_torque_overlay_LR(dL: pd.DataFrame, dR: pd.DataFrame,
                            out: Path, label: str = ""):
    """Mean torque profile of L and R on the same axes."""
    fig, ax = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
    bins = np.arange(0, 101, 1); x = bins[:-1] + 0.5
    for df, name, color in [(dL, "L", "C0"), (dR, "R", "C3")]:
        m = df.percent_gait >= 0
        if m.sum() == 0:
            continue
        cats = pd.cut(df.loc[m, "percent_gait"], bins)
        mt = df.loc[m].groupby(cats, observed=False)["tau_Nm"].mean()
        mc_cmd = df.loc[m].groupby(cats, observed=False)["current_cmd_mA"].mean()
        mc_meas = df.loc[m].groupby(cats, observed=False)["mot_cur_meas_mA"].mean()
        ax[0].plot(x, mt.values, color=color, lw=2,
                   label=f"{name} mean cmd τ")
        ax[1].plot(x, mc_cmd.values, color=color, lw=2,
                   label=f"{name} mean cmd current")
        ax[1].plot(x, mc_meas.values, color=color, lw=1, ls="--", alpha=0.7,
                   label=f"{name} mean MEAS current")
    ax[0].set_ylabel("Commanded τ (Nm)")
    ax[1].set_ylabel("Current (mA)")
    ax[1].set_xlabel("Gait cycle (%)")
    ax[0].legend(); ax[1].legend(fontsize=8)
    ax[0].grid(alpha=0.3); ax[1].grid(alpha=0.3)
    ax[0].set_title(f"Torque-profile mean overlay (L vs R) {label}")
    fig.tight_layout()
    fig.savefig(out / "torque_LR_overlay.png", dpi=120)
    plt.close(fig)


def analyze_pair(left: Path, right: Path):
    dL = load(left); dR = load(right)
    m = FNAME_RE.match(left.name)
    pid = m.group("pid") if m else "unk"
    phase = m.group("phase") if m else "unk"
    ts = m.group("ts") if m else ""
    out = left.parent / f"{pid}_{phase}_{ts}_LRplots"
    out.mkdir(exist_ok=True)
    print(f"L: {left.name}\nR: {right.name}\n→ {out}")
    plot_side_by_side(dL, dR, out, label=f"{pid}/{phase}/{ts}")
    plot_torque_overlay_LR(dL, dR, out, label=f"{pid}/{phase}/{ts}")
    # Also produce per-side individual plots inside the same folder
    for df, name in [(dL, "L"), (dR, "R")]:
        sub = out / f"{name}_plots"; sub.mkdir(exist_ok=True)
        plot_torque_profile(df, sub, f"({name})")
        plot_controller_timeline(df, sub, f"({name})")
        plot_hs_diagnostics(df, sub, f"({name})")
        plot_kinematics(df, sub, f"({name})")
        plot_startup_zoom(df, sub, f"({name})")
        plot_faults(df, sub, f"({name})")
        plot_battery_status(df, sub, f"({name})")
        lat = latency_diagnostics(df, sub, f"({name})")
        write_summary(df, sub, f"({name})", latency=lat)
    # Combined summary
    summary_L = (out / "L_plots" / "summary.txt").read_text()
    summary_R = (out / "R_plots" / "summary.txt").read_text()
    (out / "summary_LR.txt").write_text(summary_L + "\n\n" + summary_R)
    print("=" * 60); print(summary_L)
    print("=" * 60); print(summary_R)


# ---------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?", help="Path to *_full.csv")
    ap.add_argument("--latest", action="store_true",
                    help="Analyze most recent *_full.csv")
    ap.add_argument("--pair", action="store_true",
                    help="Analyze most recent matching L/R pair")
    ap.add_argument("--participant", default=None)
    ap.add_argument("--phase", default=None)
    ap.add_argument("--ts", default=None,
                    help="Timestamp filter for --pair")
    ap.add_argument("--data-dir", default=None,
                    help="Override default data folder")
    args = ap.parse_args()

    data_dir = Path(args.data_dir).resolve() if args.data_dir else DATA_DIR
    if not data_dir.exists():
        sys.exit(f"data dir not found: {data_dir}")

    if args.pair:
        L, R = find_pair(args.participant, args.phase, args.ts, data_dir)
        analyze_pair(L, R)
    elif args.latest:
        analyze_single(find_latest(data_dir))
    elif args.csv:
        analyze_single(Path(args.csv).resolve())
    else:
        ap.print_help(); sys.exit(1)


if __name__ == "__main__":
    main()

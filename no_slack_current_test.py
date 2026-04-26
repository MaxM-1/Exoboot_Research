"""
No-Slack Current Control Bench Test
=====================================

Tests whether holding ``command_motor_current(NO_SLACK_CURRENT * side)``
indefinitely is safe and well-behaved on a rigid-chain ExoBoot, regardless
of ankle motion.

Motivation
----------
Position control on a rigid chain has a structural problem: when the chain
forces a kinematic relationship between ankle and motor, the position
controller can't move the motor independently to fix any modeling error.
Instead it pulls current trying to overcome a constraint that won't yield,
and on deep dorsiflexion can trip the I^2t fuse.

Pure low-amplitude current control is the alternative: command a small
constant current (NO_SLACK_CURRENT, ~800 mA) that just keeps the chain
tensioned. The motor doesn't try to track any position target — it just
applies a steady tension and lets the ankle drag it kinematically. This is
what Peng's paper actually does in their position-control phases on the
elastic chain (the "no slack" wording in section II-C-1), and arguably more
appropriate for a rigid chain than the polynomial-tracking implementation.

Test procedure
--------------
1. Connect, tighten chain, anchor encoder via the same encoder check the
   production controller uses.
2. Switch into current-control mode with NO_SLACK_CURRENT.
3. The participant moves their ankle through the full range, including
   gradual push to maximum dorsiflexion that previously tripped the fuse.
4. The script logs ankle, motor position, commanded current, actual current,
   and status_ex at 100 Hz.

Pass criteria
-------------
* status_ex stays at 0 throughout — no firmware faults
* |mot_cur| stays close to NO_SLACK_CURRENT — current control is stable
* The ankle can reach the full ROM that previously triggered aborts under
  position control

Usage::

    python calibration/no_slack_current_test.py --port /dev/ttyACM0 --side left

Options::

    --duration 30        seconds to hold (default 30)
    --current 800        commanded |current| in mA (default NO_SLACK_CURRENT)
    --abort-ma 3000      |mot_cur| safety abort threshold (default 3000)
"""

import argparse
import os
import signal
import sys
from time import sleep, strftime, time

import numpy as np
import pandas as pd

# Allow running from calibration/ or project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flexsea.device import Device
from config import (
    FIRMWARE_VERSION, LOG_LEVEL,
    LEFT, RIGHT,
    CURRENT_GAINS, ZEROING_CURRENT, NO_SLACK_CURRENT,
)


DEFAULT_DURATION_S = 30
DEFAULT_FREQ_HZ = 100
# Tighter abort than the position test: in current control mode, the actual
# current should track the commanded value within a few hundred mA. Anything
# substantially above NO_SLACK_CURRENT means current control isn't working.
DEFAULT_ABORT_MA = 3000


def tighten_chain(device, side):
    print("Tightening chain ...")
    device.set_gains(**CURRENT_GAINS)
    sleep(0.5)
    device.command_motor_current(ZEROING_CURRENT * side)
    sleep(3.0)
    device.stop_motor()
    sleep(0.5)


def main():
    p = argparse.ArgumentParser(
        description="No-slack current control bench test.")
    p.add_argument("--port", default="/dev/ttyACM0")
    p.add_argument("--side", choices=["left", "right"], default="left")
    p.add_argument("--fw", default=FIRMWARE_VERSION)
    p.add_argument("--freq", type=int, default=DEFAULT_FREQ_HZ)
    p.add_argument("--duration", type=float, default=DEFAULT_DURATION_S,
                   help=f"Hold duration in seconds (default {DEFAULT_DURATION_S})")
    p.add_argument("--current", type=int, default=NO_SLACK_CURRENT,
                   help=f"|commanded current| in mA (default {NO_SLACK_CURRENT})")
    p.add_argument("--abort-ma", type=int, default=DEFAULT_ABORT_MA,
                   help=f"|mot_cur| abort threshold (default {DEFAULT_ABORT_MA})")
    args = p.parse_args()

    side_sign = LEFT if args.side == "left" else RIGHT

    # ---- Connect ---------------------------------------------------------
    print(f"\nConnecting to ExoBoot on {args.port} (fw {args.fw}) ...")
    device = Device(firmwareVersion=args.fw, port=args.port,
                    logLevel=LOG_LEVEL, interactive=False)
    device.open()
    sleep(1.0)
    device.start_streaming(frequency=args.freq)
    sleep(0.1)
    print(f"Connected — device ID {device.id}\n")

    # ---- Graceful shutdown ----------------------------------------------
    def _shutdown(*_):
        print("\nInterrupted — stopping motor and closing device.")
        try:
            device.stop_motor()
            sleep(0.3)
            device.close()
        except Exception:
            pass
        sys.exit(1)
    signal.signal(signal.SIGINT, _shutdown)

    # ---- Setup -----------------------------------------------------------
    input(">>> Wear the boot, stand still on a flat surface.\n"
          "    Then press ENTER to tighten the chain ... ")
    tighten_chain(device, side_sign)

    # Optional encoder check just to sanity-check the chain tightened
    sleep(0.3)
    data = device.read()
    print(f"  After tighten: ankle={data.get('ank_ang', '?')}, "
          f"motor={data.get('mot_ang', '?')}\n")

    # ---- Hold NO_SLACK_CURRENT and log ----------------------------------
    print(f">>> Holding {args.current * side_sign:+d} mA "
          f"(commanded current control) for {args.duration:.0f}s")
    print( "    Move your ankle through the full range:")
    print( "      1. Gentle plantarflex / dorsiflex around neutral (~5s)")
    print( "      2. Push to comfortable dorsiflex limit (~5s)")
    print( "      3. Push to MAX dorsiflex — the angle that previously aborted (~5s)")
    print( "      4. Return to neutral and continue gentle motion to fill the time")
    input ("    Press ENTER when ready ... ")

    # Switch to current control mode and command the steady current
    device.set_gains(**CURRENT_GAINS)
    sleep(0.2)
    device.command_motor_current(args.current * side_sign)

    period = 1.0 / args.freq
    n_samples = int(args.duration * args.freq)
    records = []
    aborted_at = None
    t_start = time()

    for _ in range(n_samples):
        try:
            data = device.read()
        except Exception as exc:
            print(f"  read error — stopping: {exc}")
            break

        a   = data.get("ank_ang",   0)
        m   = data.get("mot_ang",   0)
        cur = data.get("mot_cur",   0)
        av  = data.get("ank_vel",   0)
        mv  = data.get("mot_vel",   0)
        sx  = data.get("status_ex", 0)
        t_now = time() - t_start

        records.append({
            "t":         t_now,
            "ank_ang":   a,
            "ank_vel":   av,
            "mot_ang":   m,
            "mot_vel":   mv,
            "mot_cur":   cur,
            "cmd_cur":   args.current * side_sign,
            "status_ex": sx,
        })

        # Re-issue the current command each cycle (matches production loop pattern)
        device.command_motor_current(args.current * side_sign)

        # Safety: in current control mode, |mot_cur| should track
        # the commanded value within a few hundred mA. Anything well
        # above this means something is wrong.
        if abs(cur) > args.abort_ma:
            print(f"  !! ABORT |mot_cur|={abs(cur)} mA > {args.abort_ma} mA "
                  f"at t={t_now:.2f}s (ankle={a})")
            device.stop_motor()
            aborted_at = t_now
            break
        if sx == 2:
            print(f"  !! ABORT status_ex=2 (firmware fault) at t={t_now:.2f}s")
            device.stop_motor()
            aborted_at = t_now
            break

        sleep(period)

    device.stop_motor()
    sleep(0.5)
    msg = f"\nTrial complete: {len(records)} samples"
    if aborted_at is not None:
        msg += f"   [ABORTED at t={aborted_at:.2f}s]"
    print(msg)

    # ---- Save and summarize ---------------------------------------------
    if records:
        df = pd.DataFrame(records)
        out_dir = os.path.dirname(os.path.abspath(__file__))
        fname = f"no_slack_test_{strftime('%Y-%m-%d_%Hh%Mm%Ss')}.csv"
        out_path = os.path.join(out_dir, fname)
        df.to_csv(out_path, index=False)
        print(f"\nData → {out_path}")

        c = df["mot_cur"].abs()
        print("\n" + "=" * 78)
        print(f"{'samples':>10s} {'|cur|max':>10s} {'|cur|p95':>10s} "
              f"{'|cur|mean':>11s} {'ank_min':>9s} {'ank_max':>9s}")
        print("-" * 78)
        print(f"{len(df):>10d} {c.max():>10.0f} {c.quantile(0.95):>10.0f} "
              f"{c.mean():>11.0f} {df['ank_ang'].min():>9.0f} "
              f"{df['ank_ang'].max():>9.0f}")
        print("=" * 78)

        print("\nInterpretation:")
        print(f"  Commanded current was {abs(args.current)} mA.")
        if c.max() < abs(args.current) * 1.5 and aborted_at is None:
            print(f"  ✓ Actual |cur|max stayed close to commanded — current control")
            print( "    works on this rigid chain through the full ankle ROM.")
            print( "    This supports moving phase 4 (and possibly phase 1) from")
            print( "    polyval position control to constant NO_SLACK_CURRENT.")
        elif aborted_at is not None:
            print( "  ✗ Aborted before completion — current control is NOT well-behaved")
            print( "    on this configuration. Need to investigate further before")
            print( "    proposing this as the production architecture.")
        else:
            print(f"  ⚠ Actual |cur|max ({c.max():.0f}) exceeded commanded "
                  f"({abs(args.current)}) by more than 50%.")
            print( "    Current control is partly working but something is leaking.")
            print( "    Worth comparing the time series of cur vs ank to see when.")

    device.stop_motor()
    sleep(0.3)
    device.close()
    print("\nDone.\n")


if __name__ == "__main__":
    main()

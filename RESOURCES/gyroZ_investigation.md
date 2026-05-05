# GyroZ Heel-Strike Detection & Torque-Timing Investigation

**Date:** May 5, 2026
**Author:** Max Miller (with AI agent analysis)
**Trigger:** Professor "YA" walking-trial feedback on 5/4/2026 — *"had to walk
in a very specific pattern to get good heel-strike timing on both; felt like I
needed to pick my heel up more than normal; GyroZ thresholds may need to be
lowered."*

This document captures (a) the hypothesis we tested, (b) the data and analysis
that ruled it out, (c) the latency-localisation work that replaced it, and
(d) the conclusion: **the controller is not lagging in any tunable layer, and
the residual ~2 % gait phase offset is consistent with the published behaviour
of this exact heel-strike detection schema.**

---

## 1. Initial hypothesis (what YA reported)

YA's subjective feeling was that strides were being missed unless she walked
with an exaggerated heel pickup, and she attributed it to the gyroZ thresholds
in [config.py](config.py):

```python
HEELSTRIKE_THRESHOLD_ABOVE = +100 / BIT_TO_GYRO_COEFF   # ≈ +3280
HEELSTRIKE_THRESHOLD_BELOW = -100 / BIT_TO_GYRO_COEFF   # ≈ -3280
```

The natural interpretation: lower the magnitude of the trigger threshold so
weaker heel-strike dips are still caught. This was the starting plan.

---

## 2. Step-1 evidence: Analysis2 summaries across participants

Ran [DataLog/analysis/Analysis2.py](DataLog/analysis/Analysis2.py) `--pair` on
every YA trial and on contemporaneous reference participants (B1, BOB1,
LUCY2, MAX12, MAX4). Stats from each `summary.txt`:

| Trial | Side | n_strides = TRIGGERs | mean stride (ms) | gyroz min | gyroz max |
|---|---|---|---|---|---|
| YA1 5/3 10:29 | L/R | 67/67 | 1233/1233 | −10446 / −10357 | 14101 / 13278 |
| YA2 5/3 10:36 | L/R | 115/115 | 1176/1176 | −11573 / −10606 | 15006 / 13671 |
| YA2 5/4 11:25 | L/R | 41/39 | 1268/1333 | −8935 / −9116 | 13604 / 13903 |
| YA3 5/4 11:31 | L/R | 93/93 | 1180/1180 | −10303 / −8872 | 15393 / 14427 |
| YA4 5/4 11:48 | L/R | 48/48 | 1175/1175 | −11063 / −8967 | 15616 / 14386 |
| BOB1 5/5 | L/R | 126/127 | 1232/1222 | −9185 / −8533 | 12764 / 10724 |
| LUCY2 5/5 | L/R | 118/118 | 1153/1153 | −10561 / −10446 | 14713 / 13914 |
| B1 5/3 | L/R | 77/78 | 1222/1207 | −10803 / −9369 | 13101 / 12774 |
| MAX12 5/3 | L/R | 19/19 | 1358/1358 | −8558 / −7725 | 13176 / 12143 |

### What this proved

1. **No "Armed but never TRIGGERED" flag** appeared in any YA summary. That
   flag (`write_summary` in [Analysis2.py](DataLog/analysis/Analysis2.py))
   only fires when strides arm without firing — it is the canonical
   signature of a threshold-bound run. It did not fire.
2. **YA gyroZ minima (−8.9k to −11.6k)** clear the −3280 trigger threshold
   by **2.7×–3.5×**. The signal was nowhere near the threshold.
3. **`% time armed` clusters at 26–30 %** across all participants. YA's
   distribution is identical to BOB1 / LUCY2 / B1 / MAX12.

### Conclusion of Step 1

The hypothesis "lower the gyroZ threshold magnitude" was **rejected**. The
detector was firing one trigger per stride on every YA trial and there was
no margin problem. Lowering thresholds would only invite false triggers from
contralateral cross-talk without addressing the symptom.

---

## 3. Step-2 evidence: Exo-off baseline

User then collected exo-off (motors at zero current, IMU streaming) walking
data — [exo_off_tests/data/](exo_off_tests/data) — for SAV and MAX
participants at 1.25 m/s, analysed with
[exo_off_tests/exo_off_analysis.py](exo_off_tests/exo_off_analysis.py).

| Trial | Side | Strides | Stride dur (ms) | Pos peak 5th-pct | Neg peak 95th-pct |
|---|---|---|---|---|---|
| SAV_OFF1 | L | 87 | 1002 ± 42 | +11,822 | −6,703 |
| SAV_OFF1 | R | 87 | 1003 ± 42 | +12,358 | −7,144 |
| SAV_OFF2 | L | 47 | 977 ± 24 | +11,662 | −6,948 |
| SAV_OFF2 | R | 46 | 975 ± 13 | +12,298 | −7,217 |

Even the **weakest** stride (5th percentile) clears the +3280 ARM by 3.6×
and the −3280 TRIGGER by 2×. The detector found 100 % of strides cleanly.
This was a second, independent confirmation that thresholds are not the
bottleneck.

> **Note:** The auto-suggested thresholds in
> [exo_off_tests/5_5 Exo_OFF notes](exo_off_tests/5_5%20Exo_OFF%20notes)
> (≈ ±10,500 / ±6,000) are a 5th-pct + 15-% recipe, which would actually
> *tighten* the negative trigger from −3280 to −5,900 — closer to the
> −4,920 value that already missed ~30 % of MAX4's strides
> ([sequential_change_log.md](sequential_change_log.md), Session 4).
> That recipe should not be applied globally.

---

## 4. Step-3: Latency localisation

Since detection isn't the problem, the next question is: **where does the
"torque a little later than commanded" feeling actually come from?** Four
candidate latencies, each measurable from the existing per-iteration logs:

1. **ARM → TRIGGER** (detector fire delay relative to gyroZ zero-cross)
2. **`current_dur − expected_dur`** (stride-duration estimator drift)
3. **Cmd-current → measured-current** cross-correlation lag (motor /
   current-loop)
4. **Δ %-gait** between mean cmd-current peak and mean meas-current peak
   (combined effect of #2 + #3 on the delivered curve)

Implemented as `latency_diagnostics()` in
[DataLog/analysis/Analysis2.py](DataLog/analysis/Analysis2.py); produces
`latency.png` and a `LATENCY:` block appended to each `summary.txt`.

### Cross-participant results

| Trial | ARM→TRIG (ms) L/R | Stride drift (ms) | Motor xcorr lag | Δpeak %gait L/R |
|---|---|---|---|---|
| B1 | 330 / 300 | 0 / 0 | +20 ms | +2 / +3 |
| BOB1 | 310 / 300 | 0 / 0 | +10 ms | +2 / +2 |
| LUCY2 | 290 / 280 | 0 / 0 | +10 ms | +2 / +2 |
| **YA1** | **300 / 310** | **0 / 0** | **+20 ms** | **+8 / +8** ⚠ |
| YA2 | 300 / 310 | −10 / −10 | +10 ms | +2 / +2 |
| YA3 | 290 / 290 | −10 / 0 | +10 ms | +2 / +2 |
| YA4 | 290 / 310 | +5 / 0 | +10 ms | +2 / +2 |
| MAX12 | 300 / 360 | 0 / +10 | +10 ms | +1 / +1 |

### Interpretation of each metric

1. **Detector latency (M1) is normal for YA.** YA's 290–310 ms sits in the
   middle of every other participant's distribution. The detector layer is
   exonerated.
2. **Stride estimator (M2) is normal.** Median drift is ±10 ms — pure noise.
   The median-of-5 estimator tracks YA's ~1175 ms cadence cleanly.
3. **Motor / current-loop lag (M3) is +10 to +20 ms.** That is 1–2 samples
   at the 100 Hz host log rate; the ActPack 4.1 firmware loop is much
   faster. Not actionable from the host.
4. **Δpeak %gait (M4) is +1 to +3 % across every healthy participant.** YA2,
   YA3, YA4 show +2 % — identical to BOB1, LUCY2, B1, MAX12. **YA1 is the
   only outlier at +8 %**, which did not repeat in any later YA trial. Most
   likely a one-off (cold motor, treadmill ramp-up dominating the short
   record, or an early experiment-side state issue).

---

## 5. Comparison to Peng's published controller

> **In Xiangyu Peng's published paper, the gyroZ heel-strike detection
> schema we use was characterised as producing a net ≈ 2 % later torque
> application in measured vs commanded torque traces.**

This is exactly the behaviour we are observing. The +2 % gait offset visible
in *every* participant's `latency.png` and `LATENCY:` block — including
healthy reference walkers (BOB1, LUCY2, B1, MAX12) and YA2/3/4 — is the
**expected residual phase shift baked into this detection algorithm**, not
a bug introduced in our port.

The implication is important:

- The +2 % offset is a **known, published characteristic** of the algorithm.
- It is **not tunable from `HEELSTRIKE_THRESHOLD_ABOVE/BELOW`,
  `REFRACTORY_FRACTION`, `MAX_ARMED_FRACTION`,** or any other
  [config.py](config.py) constant — those govern *whether* a stride is
  detected, not the systemic phase offset of the detection event itself.
- Eliminating it would require a different detection scheme (e.g.
  acceleration-derived heel-strike, force-sensitive resistors, or a
  predictive model that issues triggers before the gyroZ zero-cross).

---

## 6. Conclusions

1. **The detector is not the cause of YA's subjective feedback.** Every YA
   stride was detected, with normal latency, normal armed-time, and normal
   stride-estimator behaviour.
2. **The motor / current-loop is not lagging meaningfully** (≤ 20 ms at the
   host sample rate).
3. **The +2 % gait residual is a published characteristic of the Peng
   gyroZ-based detection schema** — it is not a bug, it is the algorithm.
4. **No `config.py` change is justified by this evidence.** Lowering
   `HEELSTRIKE_THRESHOLD_BELOW` magnitude would only raise false-trigger
   risk without addressing any measurable lag.
5. **The remaining candidates** for YA's subjective feeling are
   non-software:
   - Mechanical strap / heel coupling slip at 1.35 m/s
   - Profile *intent* — `T_ACT_START = 26.0` and `T_ACT_END = 61.6` may
     simply feel too early/late for individual gait styles. That is a
     design question, not a latency bug.
6. **YA1's +8 %-gait Δpeak outlier** is worth a re-test if YA returns; it
   did not repeat in YA2/3/4 so it is most likely a one-off, but is the
   only data point that does not match the cohort.

---

## 7. Reproducibility / pointers to evidence

- Threshold rejection summaries:
  [data/YA1…YA4_*_LRplots/L_plots/summary.txt](data) and the matching
  `R_plots/summary.txt` files.
- Exo-off baseline numbers:
  [exo_off_tests/5_5 Exo_OFF notes](exo_off_tests/5_5%20Exo_OFF%20notes)
  and the raw CSVs in [exo_off_tests/data/](exo_off_tests/data).
- Latency diagnostics implementation:
  `latency_diagnostics()` in
  [DataLog/analysis/Analysis2.py](DataLog/analysis/Analysis2.py).
- Per-trial latency results:
  `latency.png` and the `LATENCY:` block in each
  `data/<participant>_*_LRplots/{L,R}_plots/summary.txt`.
- Algorithm hard rules and historical changes:
  [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md),
  [sequential_change_log.md](sequential_change_log.md) (Sessions 4 & 7
  for prior threshold tuning).

To regenerate every result:

```bash
source .venv/bin/activate
for P in YA1 YA2 YA3 YA4 B1 BOB1 LUCY2 MAX12; do
    python DataLog/analysis/Analysis2.py --pair --participant "$P" \
        --phase Familiarization
done
```

---

## 8. Recommendation to the lab

- **Do not modify** `HEELSTRIKE_THRESHOLD_ABOVE/BELOW`,
  `REFRACTORY_FRACTION`, or `MAX_ARMED_FRACTION` based on YA's feedback —
  the evidence does not support a change.
- **Communicate to YA** that her detector is firing on every stride and the
  controller is not actually missing or delaying her heel-strikes; the
  ~2 % offset in delivered vs commanded torque is the algorithm's published
  behaviour and matches every other participant.
- **If the subjective feeling persists**, the productive levers are
  mechanical (strap fit, heel coupling) and design-level (`T_ACT_START`
  / `T_ACT_END` choice), not detection thresholds.
- **Continue collecting exo-off data** at 1.00, 1.25, and 1.35 m/s for
  future participants — it is the cheapest way to verify the detector
  remains margin-safe across walker variability.

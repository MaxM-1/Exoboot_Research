# GyroZ Heel-Strike Detection & Torque-Timing Investigation

---

## Initial hypothesis (YA reported)

YA feeling that strides were being missed unless walked
with an exaggerated heel pickup, attributed to the gyroZ thresholds
in config.py:

```python
HEELSTRIKE_THRESHOLD_ABOVE = +100 / BIT_TO_GYRO_COEFF   # ~ +3280
HEELSTRIKE_THRESHOLD_BELOW = -100 / BIT_TO_GYRO_COEFF   # ~ -3280
```

starting plan: lower the magnitude of the trigger threshold so
weaker heel-strike dips are still caught??

---

## Analysis2 summaries across different tests


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

### proved

1. **No "Armed but never TRIGGERED" flag** appeared in any YA GUI log summary. That
   flag
   only fires when strides arm without firing
2. **YA gyroZ min (−8.9k to −11.6k)** clear the −3280 trigger threshold
   by 2.7×–3.5×. The signal was nowhere near the threshold.
3. **`% time armed` clusters at 26–30 %** across all participants. YA's
   distribution is identical to BOB1 / LUCY2 / B1 / MAX12.

### Conclusion 1

hypothesis "lower the gyroZ threshold magnitude" was **rejected**. The
detector was firing one trigger per stride on every YA trial and there was
no margin problem. Lowering thresholds would only invite false triggers 

---

## Exo-off baseline

collected exo-off (motors at zero current, IMU streaming) walking
data for SAV and MAX
at 1.25 m/s, analyzed 

| Trial | Side | Strides | Stride dur (ms) | Pos peak 5th-pct | Neg peak 95th-pct |
|---|---|---|---|---|---|
| SAV_OFF1 | L | 87 | 1002 ± 42 | +11,822 | −6,703 |
| SAV_OFF1 | R | 87 | 1003 ± 42 | +12,358 | −7,144 |
| SAV_OFF2 | L | 47 | 977 ± 24 | +11,662 | −6,948 |
| SAV_OFF2 | R | 46 | 975 ± 13 | +12,298 | −7,217 |

Even the weakest stride clears the +3280 ARM by 3.6×
and the −3280 TRIGGER by 2×. detector found 100 % of strides cleanly.

 second, independent confirmation that thresholds are not
bottleneck.

same deal for MAX

---

## Latency investigation

Since detection isn't a problem, investigating question of: where does the
"torque a little later than commanded" feeling actually come from? Four
candidate latencies, each measurable from the existing logs:

1. ARM -> TRIGGER (detector fire delay relative to gyroZ zero-cross)
2. `current_dur − expected_dur` (stride-duration estimator drift)
3. Cmd-current → measured-current cross-correlation lag (motor /
   current-loop)
4. delta %-gait between mean cmd-current peak and mean meas-current peak
   (combined effect of #2 + #3 on the delivered/measured curve)


### Cross-participant results

| Trial | ARM→TRIG (ms) L/R | Stride drift (ms) | Motor xcorr lag | delta peak %gait L/R |
|---|---|---|---|---|
| B1 | 330 / 300 | 0 / 0 | +20 ms | +2 / +3 |
| BOB1 | 310 / 300 | 0 / 0 | +10 ms | +2 / +2 |
| LUCY2 | 290 / 280 | 0 / 0 | +10 ms | +2 / +2 |
| **YA1** | **300 / 310** | **0 / 0** | **+20 ms** | **+8 / +8** !!!! |
| YA2 | 300 / 310 | −10 / −10 | +10 ms | +2 / +2 |
| YA3 | 290 / 290 | −10 / 0 | +10 ms | +2 / +2 |
| YA4 | 290 / 310 | +5 / 0 | +10 ms | +2 / +2 |
| MAX12 | 300 / 360 | 0 / +10 | +10 ms | +1 / +1 |

---

## Comparison to Xiangyu
In Xiangyu paper, the gyroZ heel-strike detection
schema we use was characterised as producing a net ~ 2 % later torque
application in measured vs commanded torque traces.

This is exactly the behaviour we are observing. The +2 % gait offset visible
in *every* participant's 

- The +2 % offset is a **known, characteristic** of the algorithm.
- It is **not tunable from `HEELSTRIKE_THRESHOLD_ABOVE/BELOW`,
  `REFRACTORY_FRACTION`, `MAX_ARMED_FRACTION`,** or any other
  constant
- Eliminating it would require a different detection scheme

---
tried compensating with feedforward gain and shit got all fucked up 
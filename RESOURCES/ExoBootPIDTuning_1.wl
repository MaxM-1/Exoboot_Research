(* ::Package:: *)

(* ========================================================================== *)
(*  ExoBoot PID Gain Tuning \[LongDash] Wolfram Mathematica Notebook                    *)
(*  Dephy EB-60 ExoBoot, Firmware 7.2.0, FlexSEA Controller                  *)
(*  Author: Max Miller \[LongDash] Auburn University                                    *)
(*  Date: March 2026                                                          *)
(*                                                                            *)
(*  Purpose: Model-based PID tuning for the cascaded position/current         *)
(*  control loops in the ExoBoot ankle exoskeleton.                           *)
(*                                                                            *)
(*  Your current gains from Xiangyu:                                          *)
(*    Current:  kp=100, ki=32,  kd=0                                          *)
(*    Position: kp=175, ki=50,  kd=0                                          *)
(*                                                                            *)
(*  This notebook:                                                            *)
(*    1. Builds a transfer-function model of the DC motor + transmission      *)
(*    2. Designs the INNER current loop (fast) via root locus / Bode          *)
(*    3. Designs the OUTER position loop (slow) with the closed inner loop    *)
(*    4. Provides interactive Manipulate[] widgets to sweep gains              *)
(*    5. Simulates the full Collins torque-profile tracking                    *)
(* ========================================================================== *)


(* ========================================================================== *)
(*  SECTION 0 \[LongDash] System Parameters                                            *)
(* ========================================================================== *)

(* --- Dephy EB-60 Motor Parameters (approximate from datasheet / Dephy docs) --- *)
(* You should update these with your actual measured or datasheet values *)

(* Motor electrical parameters *)
Ra = 1.25;          (* Armature resistance [Ohm] \[LongDash] typical for EB-60 *)
La = 0.35 * 10^-3;  (* Armature inductance [H] \[LongDash] typical brushless *)
kt = 0.14;          (* Torque constant [Nm/A] \[LongDash] from your exo_init.py *)
ke = 0.14;          (* Back-EMF constant [V/(rad/s)] \[LongDash] = kt for SI *)

(* Motor mechanical parameters *)
Jm = 0.33 * 10^-5;  (* Motor rotor inertia [kg\[CenterDot]m\.b2] *)
Bm = 0.001;          (* Motor viscous friction [Nm/(rad/s)] *)

(* Transmission \[LongDash] Dephy uses a Bowden cable + pulley system *)
(* The wm/wa ratio from your calibration is position-dependent, *)
(* but we linearize around a typical operating point *)
nGear = 50.0;        (* Effective gear/transmission ratio \[LongDash] motor:ankle *)
                      (* This is approximate from your wm_wa values *)

(* Reflected load inertia (ankle + foot + shoe + exo structure) *)
Jload = 0.05;        (* Total ankle inertia [kg\[CenterDot]m\.b2] \[LongDash] approximate *)
Jref = Jm + Jload / nGear^2;  (* Reflected to motor shaft *)

(* Controller timing *)
Ts = 1.0/100;       (* Control loop period [s] \[LongDash] your STREAMING_FREQUENCY *)
                     (* NOTE: config.py says 100 Hz now, was 1000 Hz *)

(* Dephy FlexSEA internal current-sense gain *)
(* The firmware digitises current; these gains are in firmware units. *)
(* We work in physical units (A, rad, rad/s) and convert at the end. *)

(* Encoder resolution *)
ticksPerRev = 2^14;            (* 16384 ticks/rev \[LongDash] 14-bit encoder *)
tickToRad = 2 Pi / ticksPerRev;  (* rad per tick *)

Print["=== System Parameters ==="];
Print["  Ra = ", Ra, " \[CapitalOmega]"];
Print["  La = ", La*1000, " mH"];
Print["  kt = ", kt, " Nm/A"];
Print["  Jref = ", ScientificForm[Jref], " kg\[CenterDot]m\.b2"];
Print["  Ts = ", Ts*1000, " ms  (", 1/Ts, " Hz)"];
Print["  Ticks/rev = ", ticksPerRev];


(* ========================================================================== *)
(*  SECTION 1 \[LongDash] Continuous-Time Plant Models                                  *)
(* ========================================================================== *)

Print["\n=== Building Plant Transfer Functions ==="];

(* --- Electrical subsystem: V(s) \[RightArrow] I(s) ---
   The armature is an RL circuit with back-EMF.
   At the current-loop level, we can often neglect the back-EMF
   (it's a disturbance rejected by the current PI controller).
   
   G_elec(s) = 1 / (La*s + Ra)
*)

Gelec = TransferFunctionModel[{{1/(La s + Ra)}}, s];
Print["  Electrical plant (V\[RightArrow]I): 1/(", La, "\[CenterDot]s + ", Ra, ")"];

(* --- Mechanical subsystem: T(s) \[RightArrow] \[Theta](s)  [torque to motor angle] ---
   G_mech(s) = 1 / (Jref*s^2 + Bm*s)
*)

Gmech = TransferFunctionModel[{{1/(Jref s^2 + Bm s)}}, s];
Print["  Mechanical plant (T\[RightArrow]\[Theta]): 1/(", Jref, "\[CenterDot]s\.b2 + ", Bm, "\[CenterDot]s)"];

(* --- Full motor: V(s) \[RightArrow] \[Theta](s) ---
   Including back-EMF coupling.
   
   G_motor(s) = kt / [s(La*Jref*s\.b2 + (Ra*Jref + La*Bm)*s + (Ra*Bm + kt*ke))]
   
   For the current loop we use G_elec.
   For the position loop we use G_motor with the inner current loop closed.
*)

GmotorNum = kt;
GmotorDen = s*(La*Jref*s^2 + (Ra*Jref + La*Bm)*s + (Ra*Bm + kt*ke));
Gmotor = TransferFunctionModel[{{GmotorNum/GmotorDen}}, s];

Print["  Full motor plant (V\[RightArrow]\[Theta]) constructed."];

(* --- Current plant for inner loop ---
   With back-EMF neglected (valid when current loop is much faster
   than mechanical dynamics), this is simply:
   
   G_current(s) = 1 / (La*s + Ra)
*)

Gcurrent = Gelec;


(* ========================================================================== *)
(*  SECTION 2 \[LongDash] Inner Current Loop Design                                     *)
(* ========================================================================== *)

Print["\n=== Inner Current Loop ==="];
Print["  Xiangyu's gains: kp=100, ki=32, kd=0"];

(* 
  The FlexSEA controller runs a discrete PID.  We first design in
  continuous time, then discretise.
  
  PI controller: C(s) = kp + ki/s = (kp*s + ki) / s
  
  Design goal for the current loop:
    - Bandwidth \[GreaterEqual] 5\[Times] the position-loop bandwidth
    - Phase margin > 45\[Degree]
    - Minimal overshoot (critically damped or slightly underdamped)
*)

(* PI controller in s-domain, parameterised *)
CcurrentPI[kp_, ki_] := TransferFunctionModel[{{(kp*s + ki)/s}}, s];

(* Open-loop for current: L(s) = C(s) * G_current(s) *)

(* --- Evaluate Xiangyu's current gains --- *)
(* Note: FlexSEA gains are in firmware integer units. The scaling
   between firmware units and physical SI units depends on the
   particular FlexSEA board.  For the EB-60 with firmware 7.2.0:
   
   Physical kp \[TildeTilde] firmware_kp * V_scale / I_scale
   
   We'll work with a normalised model first, then map back.
   For now, treat the firmware gains as-is and see what the
   loop shape looks like. *)

(* Physical-ish gains \[LongDash] rough mapping for EB-60 *)
(* The FlexSEA current controller operates on mA error, outputs mV *)
(* Approximate scaling: kp_phys \[TildeTilde] kp_fw * 0.001 [V/A], ki_phys \[TildeTilde] ki_fw * 0.001 * Ts *)

kpCurrXiangyu = 100 * 0.001;   (* \[TildeTilde] 0.1 V/A *)
kiCurrXiangyu = 32 * 0.001 / Ts; (* \[TildeTilde] 0.32 V/(A\[CenterDot]s) \[RightArrow] need to scale by loop rate *)

Print["  Xiangyu physical current gains (approx): kp=", kpCurrXiangyu, " ki=", kiCurrXiangyu];

(* Open-loop transfer function with Xiangyu's gains *)
LcurrXiangyu = SystemsModelSeriesConnect[
  CcurrentPI[kpCurrXiangyu, kiCurrXiangyu],
  Gcurrent
];

(* Bode plot of the current open-loop *)
Print["\n--- Current Loop: Bode Plot (Xiangyu's Gains) ---"];

bodeCurrentXiangyu = BodePlot[
  LcurrXiangyu,
  {0.1, 10000},
  PlotLabel -> "Current Loop Open-Loop Bode (Xiangyu: kp=100, ki=32)",
  ImageSize -> 600,
  PlotStyle -> {Directive[Thick, Blue]},
  GridLines -> Automatic
];

Print[bodeCurrentXiangyu];

(* Phase and gain margins *)
currentMargins = TransferFunctionModel[LcurrXiangyu, s];
Print["  Gain margin: ", GainMargins[LcurrXiangyu]];
Print["  Phase margin: ", PhaseMargins[LcurrXiangyu]];

(* Closed-loop step response *)
TcurrXiangyu = SystemsModelFeedbackConnect[LcurrXiangyu];

stepCurrXiangyu = Plot[
  OutputResponse[TcurrXiangyu, UnitStep[t], {t, 0, 0.05}][[1]],
  {t, 0, 0.05},
  PlotLabel -> "Current Loop Step Response (Xiangyu: kp=100, ki=32)",
  AxesLabel -> {"Time [s]", "Current [A]"},
  PlotStyle -> {Directive[Thick, Blue]},
  GridLines -> Automatic,
  Epilog -> {Dashed, Red, Line[{{0, 1}, {0.05, 1}}]},
  ImageSize -> 600
];

Print[stepCurrXiangyu];


(* --- Interactive Current Gain Tuning --- *)
Print["\n--- Interactive Current Gain Tuner ---"];

Manipulate[
  Module[{Lloop, Tcl, resp, gm, pm},
    Lloop = SystemsModelSeriesConnect[
      CcurrentPI[kpC * 0.001, kiC * 0.001 / Ts],
      Gcurrent
    ];
    Tcl = SystemsModelFeedbackConnect[Lloop];
    resp = OutputResponse[Tcl, UnitStep[t], {t, 0, 0.05}];
    gm = Quiet@GainMargins[Lloop];
    pm = Quiet@PhaseMargins[Lloop];
    
    Column[{
      (* Step response *)
      Plot[resp[[1]], {t, 0, 0.05},
        PlotLabel -> StringForm["Current Step Response  (kp=``, ki=``)", kpC, kiC],
        AxesLabel -> {"Time [s]", "Current [A]"},
        PlotRange -> {-0.1, 2.0},
        PlotStyle -> {Directive[Thick, Blue]},
        Epilog -> {Dashed, Red, Line[{{0, 1}, {0.05, 1}}]},
        GridLines -> Automatic,
        ImageSize -> 500
      ],
      (* Bode plot *)
      BodePlot[Lloop, {1, 50000},
        PlotLabel -> "Open-Loop Bode",
        ImageSize -> 500,
        PlotStyle -> {Directive[Thick, Blue]}
      ],
      (* Margins *)
      Style[StringForm["Gain Margin: ``", gm], 14],
      Style[StringForm["Phase Margin: ``", pm], 14],
      Style[StringForm["Firmware gains \[RightArrow] CURRENT_GAINS = {\"kp\": ``, \"ki\": ``, \"kd\": 0, \"k\": 0, \"b\": 0, \"ff\": 0}", kpC, kiC], Bold, 14, Red]
    }]
  ],
  {{kpC, 100, "kp (firmware units)"}, 10, 1000, 10, Appearance -> "Labeled"},
  {{kiC, 32, "ki (firmware units)"}, 1, 500, 1, Appearance -> "Labeled"},
  TrackedSymbols :> {kpC, kiC},
  SaveDefinitions -> True
]


(* ========================================================================== *)
(*  SECTION 3 \[LongDash] Outer Position Loop Design                                    *)
(* ========================================================================== *)

Print["\n=== Outer Position Loop ==="];
Print["  Xiangyu's gains: kp=175, ki=50, kd=0"];

(*
  The position loop commands current, and the (closed) current loop
  delivers that current to the motor.  The position-loop plant is:
  
  G_pos(s) = T_current_CL(s) * kt / (Jref*s^2 + Bm*s)
  
  Where T_current_CL is the closed-loop current transfer function.
  Since the current loop is much faster, we can approximate
  T_current_CL \[TildeTilde] 1 for the position loop bandwidth.
  
  Simplified position plant:
  G_pos(s) = kt / (Jref*s^2 + Bm*s) = kt / [s(Jref*s + Bm)]
*)

Gpos = TransferFunctionModel[{{kt / (Jref*s^2 + Bm*s)}}, s];

(* Position PID controller:  C(s) = kp + ki/s + kd*s *)
CposPID[kp_, ki_, kd_] := TransferFunctionModel[
  {{(kd*s^2 + kp*s + ki)/s}}, s
];

(* Xiangyu's position gains \[LongDash] same firmware scaling issue *)
(* For position control, the FlexSEA takes encoder-tick error *)
(* and outputs current in mA.  Approximate scaling: *)
(* kp_phys \[TildeTilde] kp_fw * (mA per tick) \[RightArrow] need motor constant scaling *)

(* Rough physical mapping for position gains *)
(* Input: ticks error, Output: mA command *)
(* Physical: kp [A/rad] \[TildeTilde] kp_fw * tickToRad^-1 * 0.001 *)

kpPosXiangyu = 175;  (* firmware units \[LongDash] maps tick error \[RightArrow] mA *)
kiPosXiangyu = 50;
kdPosXiangyu = 0;

(* For analysis, convert to a normalised "per-radian" basis *)
kpPosPhys = kpPosXiangyu * 0.001 / tickToRad;  (* A/rad *)
kiPosPhys = kiPosXiangyu * 0.001 / (tickToRad * Ts); (* A/(rad\[CenterDot]s) *)
kdPosPhys = kdPosXiangyu * 0.001 * Ts / tickToRad; (* A\[CenterDot]s/rad *)

Print["  Physical position gains (approx): kp=", kpPosPhys, " A/rad"];
Print["                                    ki=", kiPosPhys, " A/(rad\[CenterDot]s)"];

(* Open-loop *)
LposXiangyu = SystemsModelSeriesConnect[
  CposPID[kpPosPhys, kiPosPhys, kdPosPhys],
  Gpos
];

(* Bode plot *)
Print["\n--- Position Loop: Bode Plot (Xiangyu's Gains) ---"];

bodePosXiangyu = BodePlot[
  LposXiangyu,
  {0.01, 1000},
  PlotLabel -> "Position Loop Open-Loop Bode (Xiangyu: kp=175, ki=50, kd=0)",
  ImageSize -> 600,
  PlotStyle -> {Directive[Thick, RGBColor[0.8, 0.2, 0.2]]},
  GridLines -> Automatic
];

Print[bodePosXiangyu];

Print["  Gain margin: ", GainMargins[LposXiangyu]];
Print["  Phase margin: ", PhaseMargins[LposXiangyu]];

(* Closed-loop step response *)
TposXiangyu = SystemsModelFeedbackConnect[LposXiangyu];

stepPosXiangyu = Plot[
  OutputResponse[TposXiangyu, UnitStep[t], {t, 0, 0.5}][[1]],
  {t, 0, 0.5},
  PlotLabel -> "Position Loop Step Response (Xiangyu: kp=175, ki=50)",
  AxesLabel -> {"Time [s]", "Position [rad]"},
  PlotRange -> {-0.1, 2.0},
  PlotStyle -> {Directive[Thick, RGBColor[0.8, 0.2, 0.2]]},
  GridLines -> Automatic,
  Epilog -> {Dashed, Red, Line[{{0, 1}, {0.5, 1}}]},
  ImageSize -> 600
];

Print[stepPosXiangyu];


(* --- Interactive Position Gain Tuner --- *)
Print["\n--- Interactive Position Gain Tuner ---"];

Manipulate[
  Module[{kpP, kiP, kdP, Lloop, Tcl, resp, gm, pm},
    kpP = kpPos * 0.001 / tickToRad;
    kiP = kiPos * 0.001 / (tickToRad * Ts);
    kdP = kdPos * 0.001 * Ts / tickToRad;
    
    Lloop = SystemsModelSeriesConnect[
      CposPID[kpP, kiP, kdP],
      Gpos
    ];
    Tcl = SystemsModelFeedbackConnect[Lloop];
    resp = OutputResponse[Tcl, UnitStep[t], {t, 0, tFinal}];
    gm = Quiet@GainMargins[Lloop];
    pm = Quiet@PhaseMargins[Lloop];
    
    Column[{
      (* Step response *)
      Plot[resp[[1]], {t, 0, tFinal},
        PlotLabel -> StringForm["Position Step (kp=``, ki=``, kd=``)", kpPos, kiPos, kdPos],
        AxesLabel -> {"Time [s]", "Position [rad]"},
        PlotRange -> {-0.1, 2.5},
        PlotStyle -> {Directive[Thick, RGBColor[0.8, 0.2, 0.2]]},
        Epilog -> {Dashed, Red, Line[{{0, 1}, {tFinal, 1}}]},
        GridLines -> Automatic,
        ImageSize -> 500
      ],
      (* Bode *)
      BodePlot[Lloop, {0.01, 5000},
        PlotLabel -> "Open-Loop Bode",
        ImageSize -> 500,
        PlotStyle -> {Directive[Thick, RGBColor[0.8, 0.2, 0.2]]}
      ],
      (* Root locus *)
      RootLocusPlot[Lloop,
        PlotLabel -> "Root Locus",
        ImageSize -> 400
      ],
      (* Info *)
      Style[StringForm["Gain Margin: ``", gm], 14],
      Style[StringForm["Phase Margin: ``", pm], 14],
      Style[StringForm[
        "Firmware gains \[RightArrow] POSITION_GAINS = {\"kp\": ``, \"ki\": ``, \"kd\": ``, \"k\": 0, \"b\": 0, \"ff\": 0}",
        kpPos, kiPos, kdPos
      ], Bold, 14, RGBColor[0.8, 0.2, 0.2]]
    }]
  ],
  {{kpPos, 175, "kp (firmware units)"}, 10, 1000, 5, Appearance -> "Labeled"},
  {{kiPos, 50, "ki (firmware units)"}, 0, 500, 1, Appearance -> "Labeled"},
  {{kdPos, 0, "kd (firmware units)"}, 0, 200, 1, Appearance -> "Labeled"},
  {{tFinal, 0.5, "Sim time [s]"}, 0.05, 2.0, 0.05, Appearance -> "Labeled"},
  TrackedSymbols :> {kpPos, kiPos, kdPos, tFinal},
  SaveDefinitions -> True
]


(* ========================================================================== *)
(*  SECTION 4 \[LongDash] Discrete-Time Analysis (Z-domain)                            *)
(* ========================================================================== *)

Print["\n=== Discrete-Time Analysis (Ts = ", Ts*1000, " ms) ==="];

(*
  The FlexSEA runs a discrete PID at your streaming rate.
  Let's verify stability in the z-domain.
*)

(* Discretize the current plant *)
GcurrentZ = ToDiscreteTimeModel[Gcurrent, Ts, z, Method -> "ZeroOrderHold"];
Print["  Discrete current plant: ", GcurrentZ];

(* Discretize the position plant *)
GposZ = ToDiscreteTimeModel[Gpos, Ts, z, Method -> "ZeroOrderHold"];
Print["  Discrete position plant: ", GposZ];

(* Discrete PI controller for current loop *)
CcurrentZ[kp_, ki_] := TransferFunctionModel[
  {{kp + ki * Ts / (1 - z^-1)}}, z, SamplingPeriod -> Ts
];

(* Discrete PID controller for position loop *)
CposZ[kp_, ki_, kd_] := TransferFunctionModel[
  {{kp + ki * Ts / (1 - z^-1) + kd / (Ts * (1 - z^-1))}}, z, 
  SamplingPeriod -> Ts
];

(* Check discrete stability with Xiangyu's gains *)
Print["\n--- Discrete Poles Check ---"];

LcurrZXiangyu = SystemsModelSeriesConnect[
  CcurrentZ[kpCurrXiangyu, kiCurrXiangyu],
  GcurrentZ
];
TcurrZXiangyu = SystemsModelFeedbackConnect[LcurrZXiangyu];
currPoles = TransferFunctionPoles[TcurrZXiangyu];
Print["  Current CL poles (z-domain): ", currPoles];
Print["  All inside unit circle? ", And @@ (Abs[#] < 1 & /@ Flatten[currPoles])];

(* Discrete step response comparison *)
Print["\n--- Discrete vs Continuous Step Response ---"];

stepDiscrete = DiscretePlot[
  Flatten[OutputResponse[TcurrZXiangyu, Table[1, {k, 0, 50}]]][[;; 50]],
  {n, 1, 50},
  PlotLabel -> "Current Loop \[LongDash] Discrete Step Response",
  AxesLabel -> {"Sample #", "Current [normalised]"},
  PlotStyle -> Blue,
  ImageSize -> 600
];

Print[stepDiscrete];


(* ========================================================================== *)
(*  SECTION 5 \[LongDash] Collins Torque Profile Tracking Simulation                    *)
(* ========================================================================== *)

Print["\n=== Collins Torque Profile Tracking ==="];

(*
  Simulate how well the position controller tracks the desired motor
  angle during the Collins torque profile.  The profile has 4 phases:
  
  Phase 1: Position control (early stance \[RightArrow] t_onset)
  Phase 2: Current control \[LongDash] ascending torque (t_onset \[RightArrow] t_peak)
  Phase 3: Current control \[LongDash] descending torque (t_peak \[RightArrow] t_peak+t_fall)
  Phase 4: Position control (late stance \[RightArrow] next heel strike)
  
  We focus on Phases 1 & 4 (position control).
*)

(* Collins profile parameters \[LongDash] from your config.py defaults *)
tRise = 25.3;    (* % of gait *)
tFall = 10.3;    (* % of gait *)
tPeakGait = 51.3; (* DEFAULT_T_ONSET + tRise = 26.0 + 25.3 *)
tOnset = tPeakGait - tRise;  (* 26.0% *)
tEnd = tPeakGait + tFall;    (* 61.6% *)

(* Typical stride duration *)
strideDuration = 1.1; (* seconds \[LongDash] ~1.1 s for normal walking *)

(* Convert gait percentage to time *)
gaitToTime[pctGait_] := pctGait/100.0 * strideDuration;

(* Phase 1: 0% \[RightArrow] tOnset (position control) *)
(* Phase 4: tEnd \[RightArrow] 100% (position control) *)

Print["  Stride duration: ", strideDuration, " s"];
Print["  Position control active: 0\[Dash]", gaitToTime[tOnset]*1000, " ms  and  ", 
      gaitToTime[tEnd]*1000, " ms\[Dash]", strideDuration*1000, " ms"];
Print["  Current control active: ", gaitToTime[tOnset]*1000, " ms\[Dash]", 
      gaitToTime[tEnd]*1000, " ms"];

(*
  During position control, the desired motor position changes with
  ankle angle.  Simulate a typical ankle trajectory and see how well
  the position loop tracks it.
*)

(* Simplified ankle trajectory during stance (sinusoidal approximation) *)
(* Ankle goes from ~5\[Degree] plantarflexion at heel strike to ~15\[Degree] dorsiflexion *)
(* at mid-stance, back to plantarflexion for toe-off *)

ankleTrajectory[t_] := -5 + 20 * Sin[Pi * t / (0.6 * strideDuration)]^2;

(* Motor position = f(ankle) via the calibration polynomial *)
(* Simplified linear approximation: motor_ticks \[TildeTilde] nGear * ankle_ticks *)
(* Convert to radians *)
desiredMotorPos[t_] := nGear * ankleTrajectory[t] * Pi/180;

(* Simulate tracking *)
Manipulate[
  Module[{kpP, kiP, kdP, Lloop, Tcl, ref, resp},
    kpP = kpSim * 0.001 / tickToRad;
    kiP = kiSim * 0.001 / (tickToRad * Ts);
    kdP = kdSim * 0.001 * Ts / tickToRad;
    
    Lloop = SystemsModelSeriesConnect[
      CposPID[kpP, kiP, kdP],
      Gpos
    ];
    Tcl = SystemsModelFeedbackConnect[Lloop];
    
    (* Reference: ramp up to a target position (simplified tracking test) *)
    Column[{
      (* Step response for a large step \[LongDash] simulates position demand change *)
      Plot[{
        OutputResponse[Tcl, UnitStep[t] * desiredMotorPos[0.3], {t, 0, 0.4}][[1]],
        UnitStep[t] * desiredMotorPos[0.3]
      }, {t, 0, 0.4},
        PlotLabel -> StringForm["Position Tracking (kp=``, ki=``, kd=``)", kpSim, kiSim, kdSim],
        AxesLabel -> {"Time [s]", "Motor Pos [rad]"},
        PlotStyle -> {Directive[Thick, Blue], Directive[Dashed, Red]},
        PlotLegends -> {"Actual", "Desired"},
        GridLines -> Automatic,
        ImageSize -> 550
      ],
      
      (* Tracking error *)
      Plot[
        UnitStep[t] * desiredMotorPos[0.3] - 
          OutputResponse[Tcl, UnitStep[t] * desiredMotorPos[0.3], {t, 0, 0.4}][[1]],
        {t, 0, 0.4},
        PlotLabel -> "Tracking Error",
        AxesLabel -> {"Time [s]", "Error [rad]"},
        PlotStyle -> {Directive[Thick, Orange]},
        Filling -> Axis,
        GridLines -> Automatic,
        ImageSize -> 550
      ],
      
      Style[StringForm[
        "\nFor config.py:\n  POSITION_GAINS = {\"kp\": ``, \"ki\": ``, \"kd\": ``, \"k\": 0, \"b\": 0, \"ff\": 0}",
        kpSim, kiSim, kdSim
      ], Bold, 16, Darker[Blue]]
    }]
  ],
  {{kpSim, 175, "kp (firmware)"}, 10, 1000, 5, Appearance -> "Labeled"},
  {{kiSim, 50, "ki (firmware)"}, 0, 500, 1, Appearance -> "Labeled"},
  {{kdSim, 0, "kd (firmware)"}, 0, 200, 1, Appearance -> "Labeled"},
  TrackedSymbols :> {kpSim, kiSim, kdSim},
  SaveDefinitions -> True
]


(* ========================================================================== *)
(*  SECTION 6 \[LongDash] Automated Gain Suggestions                                    *)
(* ========================================================================== *)

Print["\n=== Automated Gain Computation ==="];

(*
  Method: Place closed-loop poles at desired locations.
  
  For the current loop (PI):
    - Desired bandwidth: ~500 Hz (well above the 100 Hz control rate...
      but at 100 Hz sampling, the max achievable BW is ~30-40 Hz Nyquist limit)
    - Actually, at Ts = 10 ms, aim for current BW \[TildeTilde] 20-30 Hz
    - Phase margin \[GreaterEqual] 60\[Degree]
  
  For the position loop (PID):
    - Desired bandwidth: ~5-10 Hz (fast enough for gait tracking)
    - Phase margin \[GreaterEqual] 50\[Degree]
    - Zero steady-state error for step inputs
    
  IMPORTANT: At 100 Hz sampling, your achievable bandwidths are
  quite limited compared to 1000 Hz. This is likely a major source
  of your issues if you dropped from 1000 Hz to 100 Hz without
  re-tuning the gains!
*)

Print[""];
Print["\:2554\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2557"];
Print["\:2551  CRITICAL NOTE                                              \:2551"];
Print["\:2551                                                              \:2551"];
Print["\:2551  Your config.py shows STREAMING_FREQUENCY = 100 Hz          \:2551"];
Print["\:2551  (comment says 'Used to be 1000 then changed to 100')       \:2551"];
Print["\:2551                                                              \:2551"];
Print["\:2551  Xiangyu's gains were VERY LIKELY tuned at 1000 Hz!         \:2551"];
Print["\:2551  Dropping to 100 Hz without re-tuning is almost certainly   \:2551"];
Print["\:2551  why you're having control issues.                           \:2551"];
Print["\:2551                                                              \:2551"];
Print["\:2551  At 100 Hz, the Nyquist frequency is 50 Hz.                \:2551"];
Print["\:2551  Current-loop BW should be \[LessEqual] ~25 Hz.                        \:2551"];
Print["\:2551  Position-loop BW should be \[LessEqual] ~8 Hz.                        \:2551"];
Print["\:255a\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:255d"];
Print[""];

(* --- Ziegler-Nichols style auto-tune --- *)
(* Find the ultimate gain and period for each loop *)

(* Current loop: find crossover *)
Print["  Attempting automatic gain computation..."];

(* For a 1st-order plant 1/(La*s + Ra), a PI controller with *)
(* zero placed at the plant pole gives nice cancellation *)

(* Current PI \[LongDash] zero-cancellation design *)
(* Plant pole at s = -Ra/La *)
currentPlantPole = Ra/La;
Print["  Current plant pole: ", currentPlantPole, " rad/s (", currentPlantPole/(2 Pi), " Hz)"];

(* Desired closed-loop bandwidth for current loop *)
wcDesiredCurrent = 2 Pi * 20;  (* 20 Hz \[LongDash] conservative for 100 Hz sampling *)

(* PI zero cancels the plant pole: ki/kp = Ra/La *)
(* Then the open-loop becomes kp/(La*s), and the CL BW = kp/La *)
(* So kp = wc * La *)
kpCurrentSuggested = wcDesiredCurrent * La;
kiCurrentSuggested = kpCurrentSuggested * (Ra/La);

Print["  Suggested current gains (physical): kp=", kpCurrentSuggested, "  ki=", kiCurrentSuggested];

(* Convert back to firmware units *)
kpCurrentFW = Round[kpCurrentSuggested / 0.001];
kiCurrentFW = Round[kiCurrentSuggested * Ts / 0.001];

Print["  Suggested CURRENT_GAINS (firmware): kp=", kpCurrentFW, "  ki=", kiCurrentFW];

(* Position loop \[LongDash] for a double-integrator-like plant *)
(* At the position loop level with fast current loop: *)
(* G \[TildeTilde] kt / (Jref*s^2 + Bm*s) *)
(* PD + I design: place closed-loop poles for ~5 Hz BW *)

wcDesiredPos = 2 Pi * 5;  (* 5 Hz \[LongDash] position bandwidth *)
zetaDesired = 0.85;       (* Slightly underdamped *)

(* Desired characteristic: s^2 + 2*zeta*wn*s + wn^2 *)
(* With PID: the controller adds a zero and adjusts pole locations *)

(* Simple approach: kp for bandwidth, kd for damping, ki for steady-state *)
kpPosSuggested = wcDesiredPos^2 * Jref / kt;
kdPosSuggested = 2 * zetaDesired * wcDesiredPos * Jref / kt - Bm / kt;
kiPosSuggested = wcDesiredPos^3 * Jref / (5 * kt);  (* ki set for slow integral *)

Print["  Suggested position gains (physical): kp=", kpPosSuggested, 
      "  ki=", kiPosSuggested, "  kd=", kdPosSuggested];

(* Convert to firmware units *)
kpPosFW = Round[kpPosSuggested * tickToRad / 0.001];
kiPosFW = Round[kiPosSuggested * tickToRad * Ts / 0.001];
kdPosFW = Round[kdPosSuggested * tickToRad / (Ts * 0.001)];

Print["  Suggested POSITION_GAINS (firmware): kp=", kpPosFW, "  ki=", kiPosFW, "  kd=", kdPosFW];

Print[""];
Print["\:2554\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2557"];
Print["\:2551  SUGGESTED config.py CHANGES                                \:2551"];
Print["\:2551                                                              \:2551"];
Print["  CURRENT_GAINS = {\"kp\": ", kpCurrentFW, ", \"ki\": ", kiCurrentFW, 
      ", \"kd\": 0, \"k\": 0, \"b\": 0, \"ff\": 0}"];
Print["  POSITION_GAINS = {\"kp\": ", kpPosFW, ", \"ki\": ", kiPosFW, 
      ", \"kd\": ", kdPosFW, ", \"k\": 0, \"b\": 0, \"ff\": 0}"];
Print["\:2551                                                              \:2551"];
Print["\:2551  \:26a0 VALIDATE ON HARDWARE WITH LIGHT LOADS FIRST!             \:2551"];
Print["\:2551  Start with the current loop, verify step response,         \:2551"];
Print["\:2551  THEN tune the position loop.                               \:2551"];
Print["\:255a\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:255d"];


(* ========================================================================== *)
(*  SECTION 7 \[LongDash] Sensitivity Analysis                                          *)
(* ========================================================================== *)

Print["\n=== Parameter Sensitivity Analysis ==="];

(* 
  The motor parameters (Ra, La, Jm, etc.) are approximate.
  Let's see how sensitive the stability margins are to parameter
  uncertainty.
*)

Manipulate[
  Module[{RaSens, LaSens, JrefSens, GcurrSens, GposSens, 
          LcurrSens, LposSens, gmCurr, pmCurr, gmPos, pmPos},
    
    RaSens = Ra * (1 + deltaRa/100);
    LaSens = La * (1 + deltaLa/100);
    JrefSens = Jref * (1 + deltaJ/100);
    
    GcurrSens = TransferFunctionModel[{{1/(LaSens s + RaSens)}}, s];
    GposSens = TransferFunctionModel[{{kt/(JrefSens s^2 + Bm s)}}, s];
    
    LcurrSens = SystemsModelSeriesConnect[
      CcurrentPI[kpCurrXiangyu, kiCurrXiangyu], GcurrSens
    ];
    LposSens = SystemsModelSeriesConnect[
      CposPID[kpPosPhys, kiPosPhys, kdPosPhys], GposSens
    ];
    
    gmCurr = Quiet@GainMargins[LcurrSens];
    pmCurr = Quiet@PhaseMargins[LcurrSens];
    gmPos = Quiet@GainMargins[LposSens];
    pmPos = Quiet@PhaseMargins[LposSens];
    
    Column[{
      Style["Parameter Sensitivity", Bold, 16],
      Style[StringForm["Ra = `` \[CapitalOmega] (``%)", RaSens, deltaRa], 13],
      Style[StringForm["La = `` mH (``%)", LaSens*1000, deltaLa], 13],
      Style[StringForm["Jref = `` kg\[CenterDot]m\.b2 (``%)", ScientificForm[JrefSens], deltaJ], 13],
      "",
      Style["Current Loop:", Bold, 14],
      Style[StringForm["  Gain Margin: ``", gmCurr], 13],
      Style[StringForm["  Phase Margin: ``", pmCurr], 13],
      "",
      Style["Position Loop:", Bold, 14],
      Style[StringForm["  Gain Margin: ``", gmPos], 13],
      Style[StringForm["  Phase Margin: ``", pmPos], 13],
      "",
      (* Combined Bode *)
      BodePlot[{LcurrSens, LposSens}, {0.1, 10000},
        PlotLabel -> "Open-Loop Bode (Blue=Current, Red=Position)",
        PlotStyle -> {Directive[Thick, Blue], Directive[Thick, Red]},
        ImageSize -> 550
      ]
    }]
  ],
  {{deltaRa, 0, "\[CapitalDelta]Ra [%]"}, -50, 50, 5, Appearance -> "Labeled"},
  {{deltaLa, 0, "\[CapitalDelta]La [%]"}, -50, 50, 5, Appearance -> "Labeled"},
  {{deltaJ, 0, "\[CapitalDelta]J [%]"}, -50, 100, 5, Appearance -> "Labeled"},
  TrackedSymbols :> {deltaRa, deltaLa, deltaJ},
  SaveDefinitions -> True
]


(* ========================================================================== *)
(*  SECTION 8 \[LongDash] Frequency Response at Different Sampling Rates                *)
(* ========================================================================== *)

Print["\n=== 100 Hz vs 1000 Hz Comparison ==="];

(*
  This section directly compares the discrete-time behaviour at
  100 Hz (your current rate) vs 1000 Hz (Xiangyu's original rate)
  with the SAME firmware gains.  This shows why the gains broke.
*)

Ts100 = 1.0/100;
Ts1000 = 1.0/1000;

(* Discrete current plants *)
Gcurr100 = ToDiscreteTimeModel[Gcurrent, Ts100, z, Method -> "ZeroOrderHold"];
Gcurr1000 = ToDiscreteTimeModel[Gcurrent, Ts1000, z, Method -> "ZeroOrderHold"];

(* With Xiangyu's gains at both rates *)
(* The firmware PI accumulator behaviour changes with Ts *)
Lcurr100 = SystemsModelSeriesConnect[CcurrentZ[kpCurrXiangyu, kiCurrXiangyu], Gcurr100];
Lcurr1000 = SystemsModelSeriesConnect[
  TransferFunctionModel[
    {{kpCurrXiangyu + kiCurrXiangyu * Ts1000 / (1 - z^-1)}}, z, SamplingPeriod -> Ts1000
  ],
  Gcurr1000
];

Tcurr100 = SystemsModelFeedbackConnect[Lcurr100];
Tcurr1000 = SystemsModelFeedbackConnect[Lcurr1000];

(* Check poles *)
poles100 = Flatten[TransferFunctionPoles[Tcurr100]];
poles1000 = Flatten[TransferFunctionPoles[Tcurr1000]];

Print["  Current CL poles at 100 Hz:  ", poles100, "  |z| = ", Abs /@ poles100];
Print["  Current CL poles at 1000 Hz: ", poles1000, "  |z| = ", Abs /@ poles1000];

stable100 = And @@ (Abs[#] < 1 & /@ poles100);
stable1000 = And @@ (Abs[#] < 1 & /@ poles1000);

Print["  Stable at 100 Hz?  ", stable100];
Print["  Stable at 1000 Hz? ", stable1000];

If[!stable100 && stable1000,
  Print["\n  \[FivePointedStar] CONFIRMED: Xiangyu's gains are UNSTABLE at 100 Hz but stable at 1000 Hz."];
  Print["    This is almost certainly your problem!"];
];

(* Discrete step response comparison *)
nSamp100 = 100;
nSamp1000 = 1000;

resp100 = Flatten[OutputResponse[Tcurr100, Table[1, nSamp100]]];
resp1000 = Flatten[OutputResponse[Tcurr1000, Table[1, nSamp1000]]];

compPlot = Show[
  ListPlot[
    Transpose[{Range[0, (nSamp100-1)] * Ts100 * 1000, resp100[[;; nSamp100]]}],
    PlotStyle -> {Red, PointSize[0.01]},
    Joined -> True,
    PlotLegends -> {"100 Hz (your rate)"}
  ],
  ListPlot[
    Transpose[{Range[0, (nSamp1000-1)] * Ts1000 * 1000, resp1000[[;; nSamp1000]]}],
    PlotStyle -> {Blue, PointSize[0.003]},
    Joined -> True,
    PlotLegends -> {"1000 Hz (Xiangyu's rate)"}
  ],
  PlotLabel -> "Current Step Response: 100 Hz vs 1000 Hz (Same Gains)",
  AxesLabel -> {"Time [ms]", "Current [normalised]"},
  PlotRange -> All,
  GridLines -> Automatic,
  ImageSize -> 650
];

Print[compPlot];


(* ========================================================================== *)
(*  SECTION 9 \[LongDash] Quick-Start Gain Recommendations                             *)
(* ========================================================================== *)

Print["\n"];
Print["\:2554\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2557"];
Print["\:2551                    TUNING WORKFLOW                              \:2551"];
Print["\:2560\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2563"];
Print["\:2551                                                                  \:2551"];
Print["\:2551  1. EITHER go back to 1000 Hz streaming                        \:2551"];
Print["\:2551     (change STREAMING_FREQUENCY = 1000 in config.py)            \:2551"];
Print["\:2551     and use Xiangyu's original gains,                           \:2551"];
Print["\:2551                                                                  \:2551"];
Print["\:2551  2. OR stay at 100 Hz and use the re-tuned gains from          \:2551"];
Print["\:2551     Section 6, validated with the interactive widgets.          \:2551"];
Print["\:2551                                                                  \:2551"];
Print["\:2551  Testing procedure (with the boot on a bench, NOT on a person):\:2551"];
Print["\:2551                                                                  \:2551"];
Print["\:2551  a) Set CURRENT_GAINS to new values                            \:2551"];
Print["\:2551  b) Run current_control(500, 2.0) \[LongDash] watch for oscillation      \:2551"];
Print["\:2551  c) If stable, increase to current_control(2000, 2.0)          \:2551"];
Print["\:2551  d) Set POSITION_GAINS to new values                           \:2551"];
Print["\:2551  e) Run encoder_check() \[LongDash] motor should hold position quietly   \:2551"];
Print["\:2551  f) Command small position steps \[LongDash] watch for overshoot/ringing \:2551"];
Print["\:2551  g) Only then test on a person with the Collins profile        \:2551"];
Print["\:2551                                                                  \:2551"];
Print["\:255a\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:2550\:255d"];




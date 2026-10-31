# Simulator predictions behind the 072-083 plans (SIMULATION_ONLY)

These files are the simulator's predictions for the testbed experiments, kept with the release so that
`evaluation/predict_check.py` (release 073) can compare them with the testbed runs. The simulator is
deterministic (seeded); every file is reproduced exactly by the command given.

| File | Produced by | Quoted in |
|---|---|---|
| `sweep_072_summary.csv`, `sweep_072_power.csv`, `SWEEP_072.md` | `python3 evaluation/sim_sweep.py --des bin/evicon-des --out <dir>` (release 072 simulator; 200 episodes per point; release 073 does not change the simulator) | sections 1 and 5 of `PREREG_expW_072.md`, `PREREG_expS_072.md`, `PLAN_expL_072.md`, frozen on 2026-10-21 before any W, S or L cell ran |
| `expC_073_summary.csv`, `expC_073_power.csv`, `SIM_EXPC_073.md` | `python3 evaluation/sim_expC.py --des bin/evicon-des --out <dir>` (release 073; 200 episodes) | sections 1 and 5 of `PREREG_expC_073.md`, frozen before any C cell ran |
| `expC2_074_summary.csv`, `expC2_074_power.csv`, `SIM_EXPC2_074.md` | `python3 evaluation/sim_expC2.py --des bin/evicon-des --out <dir>` (release 074; 200 episodes; the predicted contracts `experiments/owners/zephyr/pred-*.json`) | sections 1 and 5 of `PREREG_expC2_074.md`, frozen before any C2 cell ran |
| `expC3_075_summary.csv`, `expC3_075_power.csv`, `SIM_EXPC3_075.md` | `python3 evaluation/sim_expC3.py --des bin/evicon-des --out <dir>` (release 075 simulator, unchanged since 072; 200 episodes per scenario; scenarios owner_up (the prediction) and owner_crash (the contrast); the predicted contracts `experiments/owners/zephyr/pred-*.json`) | sections 1 and 5 of `PREREG_expC3_075.md`, frozen before any C3 cell ran |
| `extra083_summary.csv`, `extra083_effects.csv`, `extra083_power.csv`, `SIM_EXTRA083.md` | `python3 evaluation/sim_extra083.py --des bin/evicon-des --out <dir>` (release 083 simulator: the QueryOnly policies QUERY_ONLY, QUERY_ONLY_W and `authority.certificate greedy` added, every earlier policy and default unchanged; 200 episodes per scenario; ThingsBoard's contract `experiments/sim/contracts083/sim-tb-083.json`; `extra083_power.csv` lists the plan's paired-difference hypotheses after its 0.80 rule, the estimands' means are in `extra083_effects.csv`) | sections 1 and 5 of `PREREG_extra_083.md`, frozen before any of its cells ran |
| `extra084_summary.csv`, `extra084_effects.csv`, `extra084_power.csv`, `extra084_calibration.csv`, `SIM_EXTRA084.md` | `python3 evaluation/sim_extra084.py --des bin/evicon-des --out <dir> --calibrate` (release 084 simulator: `authority.fence_uncertain per_identity` (rule R5') and the policy PERIODIC_FULL added, every earlier policy and default unchanged; 200 episodes per scenario; the (a) scenarios with the twin's crash probability fitted to the testbed's K0F -> K0S gap at W = 6, 0.028, the base twin's next to them; `extra084_power.csv` lists the plan's paired-difference hypotheses after its 0.80 rule) | sections 1(c) and 5 of `PREREG_extra_084.md`, frozen before any of its cells ran |

The prediction is the number frozen in a plan; these CSVs carry the same numbers with more digits and
for every contract, policy and point of the sweep. Do not edit them: regenerate with the commands above.

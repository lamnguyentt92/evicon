# Preregistration: the review experiments of release 084 (per-identity fence intake on the orphan schedules, and full periodic reconciliation on the firmware and on MinIO)

Release 084. Frozen before any of its cells runs on the testbed. The six campaign configs below carry the SHA-256 of
this file, and `evicon_review.py campaign` refuses to run them if this file, or any file listed under *Frozen files*,
has changed. Changes after freezing are recorded under *Deviations*; nothing above that section is edited.

## 1. Background (exploratory evidence, not part of the test)

The external review of 31 October 2026 asked for two controls that need testbed cells (its items M3 and M5) and for
analyses of the existing traces that need none (section 1(e)). This plan covers the cells.

**(a) Rule R5 and the stop experiment.** On the emulated owner with crash orphans (`expS-w6`, W = 6, queue channel), a
per-command stop beat the owner fence by 20.7 pp of clean fulfilment (episodes 0-7: K0F 58.7 %, K0S 79.4 %; all 12
episodes in the 081 final report). The runs of experiment O (`expO-noorphan`: the same schedules, crash after the
receipt, so no orphans) located the cause: without orphans the fence and the stop are equivalent (O-1 MET, mean -0.04
pp), and K0F gains +20.7 pp from S to O on the same schedules. The fence audit of the 081 report shows the mechanism
on S's K0F cells: 2,775 fence calls, 112 OK, 2,191 DEVICE_OFFLINE (a fence sent while the device was offline), 388
BARRIER_UNCERTAIN_RECEIPTS and 84 BARRIER_QUERY_FAILED; in O, 0 BARRIER_UNCERTAIN_RECEIPTS. A crash orphan is an
identity the owner executed but whose receipt it lost: the owner can answer only UNCERTAIN for it, and under rule R5
(all-or-nothing intake, the runtime's rule since release 066) one UNCERTAIN identity in a fence's answer aborts the
whole fence, so the terminal answers the owner gave for every other identity at or below the cutoff are discarded. The
window then stays full of commands the owner had answered. At W unbounded the window never binds and the gap is small
(Si, episodes 0-3: +2.1 pp).

**(b) Rule R5' (per-identity intake).** Release 084 adds the alternative the review proposed, behind the new
default-off flag `-fence-intake per-identity` (policy EVICON_FI = EVICON with that flag): a fence whose answer lists
identities UNCERTAIN takes every terminal answer as evidence, seals every unlisted non-terminal identity at or below
the cutoff STOPPED (the owner's cutoff is durable: they never ran and never will), keeps each UNCERTAIN identity
pending (owner-uncertain; never fenced again, since a complete answer at a durable cutoff is final for it), writes no
anchor and leaves the cutoff where it is (barrier status PARTIAL). Conflicts and invalid receipts still abort, as
under R5. Checks before freezing (section 9): the runtime tests F52-F54, a property test over random executions (lost,
held, late and reply-lost messages, crash orphans) in which R5' and R5 produce no false STOPPED and no duplicate while
a negative control that treats an incomplete answer as complete is caught, and a TLA+ model (`formal/EviconFenceFI.tla`)
whose safety invariants hold under R5' and fail for the negative control (TLC results in `formal/TLC_RESULTS_FI.txt`).

**(c) Simulator prediction** (`evaluation/sim_extra084.py`, 200 episodes per scenario, `SIMULATION_ONLY`; tables in
`sim_predictions/SIM_EXTRA084.md`). The base twin has about 0.5 owner crashes per episode where the testbed's schedules
have about 8 crash windows, and predicts a K0F -> K0S gap of only +4.4 pp at W = 6. For (a) the twin's crash
probability was therefore fitted to the testbed's gap before any cell (`calibrate()`: 0.020 -> +15.6, 0.025 -> +18.1,
0.030 -> +22.8, 0.035 -> +25.6 pp at 100 episodes; `CAL_CRASH` = 0.028, `extra084_calibration.csv`); the base twin's
prediction is reported next to it.

| Scenario | Cell | Policy | Clean % | Fences / ep. | Fence aborts / ep. | Stops / ep. | Queries / ep. | Owner calls / ep. |
|---|---|---|---|---|---|---|---|---|
| a_s6 (W = 6, calibrated) | K0F | EVICON (R5) | 54.12 | 344.0 | 342.6 | 0 | 0 | 344.0 |
| | K0F | EVICON_FI (R5') | 76.13 | 7.5 | 0 | 0 | 0 | 7.5 |
| | K0S | EVICON | 75.98 | 0 | 0 | 41.3 | 0 | 41.2 |
| a_sinf (W unbounded, calibrated) | K0F | EVICON / EVICON_FI | 75.68 / 76.96 | 575.3 / 7.4 | 573.6 / 0 | 0 | 0 | 575.3 / 7.4 |
| | K0S | EVICON | 76.60 | 0 | 0 | 103.9 | 0 | 103.9 |
| a_s6_base (base twin) | K0F | EVICON / EVICON_FI | 72.66 / 76.98 | 169.6 / 5.7 | 165.5 / 0 | 0 | 0 | 169.6 / 5.7 |
| | K0S | EVICON | 76.86 | 0 | 0 | 43.9 | 0 | 43.9 |
| c_fw_w6 / c_fw_winf | ZEPHYR_CUTOFF | EVICON | 76.98 / 76.98 | 3.7 | 0 | 0 | 25.0 | 28.6 / 28.7 |
| | ZEPHYR_CUTOFF | PERIODIC_FULL | 76.97 / 76.97 | 3.7 | 0 | 0 | 26.5 / 26.6 | 30.2 / 30.3 |
| c_minio_w6 / c_minio_winf | MINIO_COND | EVICON | 77.18 / 77.21 | 0 | 0 | 51.5 / 113.9 | 54.6 / 119.0 | 106.2 / 232.9 |
| | MINIO_COND | PERIODIC_FULL | 77.18 / 77.22 | 0 | 0 | 52.0 / 115.2 | 53.4 / 116.3 | 105.4 / 231.5 |

Paired effects (pp of offered, mean (SD) over 200 episodes): W = 6, R5 -> R5' +22.22 (19.08), stop -> R5' +0.16 (1.05),
R5 -> stop +22.06 (19.02); W unbounded, R5 -> R5' +1.29 (1.61), stop -> R5' +0.37 (1.94); base twin W = 6, R5 -> R5'
+4.47 (11.78); PERIODIC_FULL against EVICON: firmware -0.01 (0.09) at both W, MinIO -0.00 (0.36) at W = 6 and +0.02
(0.29) at W unbounded. No scenario has a safety violation. The twin's fence counts are not the testbed's: it retries an
aborted fence every sweep and has no DEVICE_OFFLINE outcome, so it reproduces the size of the gap, not the mix of
fence outcomes; the prediction for (a) rests on the fitted crash rate, and the base twin's +4.5 pp is the low end.

**(d) Full periodic reconciliation (PERIODIC_FULL).** The review (M5) asked whether EviCon's fulfilment comes from its
evidence rules or from its trigger (resolution when the window is full or the probe budget is spent, and the search for
an admission certificate). PERIODIC_FULL is EVICON with `-resolve-trigger PERIODIC_FULL -resolve-sweep 1s`: every Δ =
1 s, for every ambiguous command older than the ambiguity threshold, it queries first if the contract has Q_A, then
stops it if the contract has Q_S, else fences the full frontier if it has F; at most the probe budget of owner calls
per sweep, one of them kept for a backward action when one exists; no certificate search, no trigger by a blocked
request or a full window. Its backward actions follow EVICON's threshold rule (resolve.Decide, θ = cost / (hold x
magnitude), the same costs: hold 1, stop 1, fence 5) with the query already taken, so PERIODIC_FULL differs from EVICON
in the trigger only. θ = 0 (act at once on every aged command) was rejected: the 071 matched-cadence comparison
(RES_PERIODIC IMMEDIATE) already covers it, and a fence every second pauses the domain and seals commands that a
query would have resolved. The guard is unchanged: PERIODIC_FULL releases nothing without evidence. Runtime test F54;
simulator policy PERIODIC_FULL (section 1(c)).

**(e) No cells.** The delivery binding rule (`conformance/reference/delivery_binding.py`): QoS 1 only to an owner
whose suppression is structural (the cutoff firmware under CONFIG_EVC_GUARD_EVICTED=y and CONFIG_EVC_DEDUP_HORIZON_MS=-1)
or with T and e + ε <= h; a finite measured D_h alone is not enough. For every config so far it gives what the 077
gateway's binding "auto" gives; the driver refuses a firmware campaign where they differ. The firmware host test
`test_evc_late_copy.c` feeds late copies (after ring eviction, after a reboot, beyond a positive horizon). The Level-2
analyses (`evaluation/review_l2_084.py`) run in the report over every run it reads.

## 2. Design

Common regimes: as the reference runs each new run replays (4 devices; 300 s per cell + 60 s tail; 0.5 commands/s per
device; values 1-5; bound 345 per device; deadline 60 s; validity 90 s; fault windows 2/min; offline 10-40 s; mix offline
0.3, drop_up 0.2, drop_down 0.2, delay_up 0.1 (6 s), crash 0.2; authority: no assumed bound on the network delay,
SKI_RENTAL resolution, query first, costs hold 1, stop 1, fence 5, probe budget 4, the T4 retry guard on). Each config
differs from its reference config only in the name, the episodes, the contracts, the policies, `exclude_cells`, the
activation gate, the plan and (firmware) the channel and binding keys, which are the values the reference run used
(test H5 of `test_release084`).

| Run | Config | Runner | Owner, channel | Contracts | Policies | W | Seed (replays) | Episodes | Cells | Hours |
|---|---|---|---|---|---|---|---|---|---|---|
| **FIs6** | `expFI_s6.json` | `campaign_ext084.py` | evicon-device, queue, crash orphans | K0F, K0S | EVICON, EVICON_FI (not on K0S) | 6 | 20261017 (S, episodes 0-7) | 8 | 24 | 2.9 |
| **FIsi** | `expFI_sinf.json` | `campaign_ext084.py` | as FIs6 | K0F | EVICON, EVICON_FI | 1000 | 20261017 (Si, episodes 0-3) | 4 | 8 | 1.0 |
| **PFz6** | `expPF_zephyr_w6.json` | `campaign_zephyr_ext084.py` | the cutoff firmware behind Mosquitto, queue, binding auto (QoS 1) | ZEPHYR_CUTOFF (measured in this review run) | PERIODIC_FULL | 6 | 20261017 (C2) | 8 | 8 | 1.0 |
| **PFzi** | `expPF_zephyr_winf.json` | `campaign_zephyr_ext084.py` | as PFz6 | ZEPHYR_CUTOFF | PERIODIC_FULL | 1000 | 20261017 (Rz) | 8 | 8 | 1.0 |
| **PFm6** | `expPF_minio_w6.json` | `campaign_real_ext084.py` | MinIO behind the S3 gateway, direct | MINIO_COND (the 070 measured contract) | PERIODIC_FULL | 6 | 20261025 (C) | 8 | 8 | 1.0 |
| **PFmi** | `expPF_minio_winf.json` | `campaign_real_ext084.py` | as PFm6 | MINIO_COND | PERIODIC_FULL | 1000 | 20261025 (Rm) | 8 | 8 | 1.0 |

Total: 6 campaigns, 64 cells, about 7.9 hours of cells (section 6: about 10.9 testbed hours with preflight, build,
verify, distribute, the firmware measurement and the final report).

Reference runs (no new cells; read from the archive and the 078 run): S `expS-w6/20261022T161003`, Si
`expS-winf/20261023T010003`, C2 `expC2-zephyr/20261024T044342`, Rz `expR-zephyr-winf/20261028T084744`, C
`expC-minio/20261023T083658`, Rm `expR-minio-winf/20261028T145228` (the earliest complete run of each config, as the
report selects them).

- **Within run and across runs.** (a) is within run: R5, R5' and the stop on the same schedules in the same run. (c) is
  across runs: PERIODIC_FULL runs alone and is paired with the EVICON cells of the reference run on the same schedule
  files (common random numbers). A within-run PERIODIC_FULL/EVICON comparison would double the 32 cells of (c) and does
  not fit the budget of about 11 testbed hours; the testbed power of the cross-run rules (section 5) includes the
  run-to-run spread of the same policy (C2 -> Rz, C -> Rm). The release, the day and, on the firmware, the measured
  contract differ between the arms; the estimands report the EVICON levels of both reference runs next to them.
- **Runners.** `campaign_ext084.py` (on the frozen `campaign_ext077.py` and `campaign.py`), `campaign_real_ext084.py`
  (on the frozen `campaign_real.py`) and `campaign_zephyr_ext084.py` (on the frozen `campaign_zephyr_ext077.py`) import
  the frozen runners unchanged and add only the 084 policies (`ext084.py`): EVICON_FI = EVICON's arguments and
  `-fence-intake per-identity`; PERIODIC_FULL = EVICON's arguments with `-resolve-trigger PERIODIC_FULL` (the sweep of 1 s
  is EVICON's); `exclude_cells` for the emulated runner (`ext084.install_exclusion`: the frozen `run_campaign` with one
  skip inserted); the firmware runner checks the delivery binding rule before the first cell (the driver checks it too,
  with the configuration of the build that runs). Every policy of earlier releases keeps its arguments (test H3). The
  driver picks the runner from the policies (`evicon_review.py`: `_uses_084_policies`).
- **Contracts measured in the same review run**: the firmware's (`measure zephyr-plain zephyr`, as for C2, on the binary
  the runs use: MEASURED, {D_h, Q_A, F}, "owner up"); otherwise no firmware cell runs. The MinIO runs use MinIO's 070
  contract, as C and Rm.
- **Truth**: `evicon-plant` on VM-E (on MinIO the object versions, as in C and Rm).
- **Pairing**: contracts and policies of the same episode within a run; across runs, episodes whose schedule files
  `schedule_ep<e>.json` are byte-identical (test H5 replays them through `campaign.schedule`).
- **Activation gates**: at least one acting decision of the gated policies in every VALID gated cell: FIs6, EVICON and
  EVICON_FI on K0F and K0S (K0S/EVICON_FI is not run); FIsi, EVICON and EVICON_FI on K0F; PFz6 and PFzi, PERIODIC_FULL on
  ZEPHYR_CUTOFF; none on PFm6 and PFmi (as C, Rm and 083's Gm: the MinIO runs carry no gate).
- **Analysis**: `evaluation/pairs_ext083.py --plan evaluation/prereg/PREREG_extra_084.md --run <key>=<run> ... --out
  <dir>` (frozen by PREREG_extra_083.md): the frozen `pairs_report.py` on the ```pairs-spec``` block and the
  ```pairs-ext``` block with pairs_report's own functions (contrasts between policies within a run, contrasts across runs
  paired by schedule, safety over groups of runs). Holm within each family; the two blocks' families are disjoint.
  `evaluation/policy_compare.py` (frozen) runs the ```policy-compare``` block, `evaluation/fi084.py` the trace counts of
  E-FI and E-PF, `evaluation/esim084.py` E-SIM. `evicon_review.py report` runs them with the earliest complete run of
  each campaign among its sources.

## 3. Hypotheses and decision rules

Tests on per-episode paired differences (to minus from), clean fulfilment in pp of offered, Student t, alpha = 0.05;
Holm within each family. Detection, absence and safety rules have no p-value and are not part of a family.

**The 0.80 rule** (as in PREREG_extra_083.md). A paired-difference rule is a confirmatory hypothesis only if its power
under the testbed's own spread (section 5: `power_extra084_testbed.py`), at the predicted (simulator) mean and with Holm
in its family, is at least 0.80. Otherwise it is an estimand (section 4), reported with its interval and the prediction
and no verdict. Applied before freezing: FI-2 (`equivalent 5`, power 0.05) and FI-3 (`at_least 0`, 0.75) became
estimands; FI-1 and PF-1 to PF-4 have power 1.00.

The five safety counts are bound violations, duplicate effects, logical duplicates, false STOPPED and false APPLIED, as
in every plan. Block: S = ```pairs-spec``` (pairs_report.py), X = ```pairs-ext``` (pairs_ext083.py).

| ID | Block, family | Hypothesis | Decision rule |
|---|---|---|---|
| **FI-1** | X, fi-w6 | On the orphan schedules at W = 6, per-identity intake (R5') raises clean fulfilment over all-or-nothing intake (R5) by more than 10 pp | `at_least 10`, run FIs6, K0F, EVICON -> EVICON_FI, `clean_pp` |
| **FI-A1** | S, - | R5' is safe at W = 6 | `absent`, run FIs6, K0F/EVICON_FI, the five safety counts |
| **FI-A2** | S, - | ... and at W unbounded | `absent`, run FIsi, K0F/EVICON_FI, the five safety counts |
| **X-S1** | S, - | Safety in every cell of FIs6 and FIsi (R5, R5', the stop) | `safety` (pairs_report): the five counts 0 in every VALID cell; late effects reported separately |
| **PF-1** | X, pf-fw | On the cutoff firmware at W = 6, full periodic reconciliation is equivalent to EviCon (within 3 pp) | `equivalent 3`, PFz6 PERIODIC_FULL against C2 EVICON, ZEPHYR_CUTOFF, `clean_pp` |
| **PF-2** | X, pf-fw | ... at W unbounded | `equivalent 3`, PFzi PERIODIC_FULL against Rz EVICON, ZEPHYR_CUTOFF, `clean_pp` |
| **PF-3** | X, pf-minio | On MinIO's conditional PUT at W = 6 | `equivalent 3`, PFm6 PERIODIC_FULL against C EVICON, MINIO_COND, `clean_pp` |
| **PF-4** | X, pf-minio | ... at W unbounded | `equivalent 3`, PFmi PERIODIC_FULL against Rm EVICON, MINIO_COND, `clean_pp` |
| **PF-A1 to PF-A4** | S, - | PERIODIC_FULL is safe on the firmware and on MinIO at both W | `absent`, runs PFz6, PFzi (ZEPHYR_CUTOFF), PFm6, PFmi (MINIO_COND), PERIODIC_FULL, the five safety counts |
| **X-S2** | X, - | Safety in every cell of the four PERIODIC_FULL runs | `safety` over the runs that were run |

In all, 13 rules: 5 paired-difference hypotheses (in three families), 6 absence rules and 2 safety rules (before the
0.80 rule: 7 paired-difference hypotheses). The safety rule X-S2 is decided on the runs of its group that were run; a
run not run is listed (section 6), and the rule is NOT RUN only when none of its runs was run. X-S1 is NOT RUN unless
both FIs6 and FIsi completed (both run in session 1).

Interpretation fixed in advance:
- **FI-1 MET**: R5 is what costs the fence its fulfilment on the orphan schedules, and per-identity intake recovers it;
  the paper describes R5' as the fence intake for an owner whose answer is per identity at a durable cutoff, with E-FI
  (aborts under R5, per-identity commits under R5', terminal answers per fence, orphans listed) and FI-2 (how close R5'
  comes to the stop). The runtime's default stays R5 (an owner that cannot answer per identity keeps all-or-nothing).
- **FI-1 NOT MET with a positive estimate**: R5' recovers less than 10 pp; the part of the gap that is not the abort
  (e.g. fences sent while the device is offline, which fail under either rule) is reported from E-FI and the M3
  counterfactual of the Level-2 analyses. **NOT MET with an estimate at or below 0**: R5' does not help on the testbed;
  the paper keeps R5 and reports the estimate and E-FI.
- **FI-A1, FI-A2 PRESENT or X-S1 NOT MET**: a safety failure of R5' (or of R5, or the stop); reported with the cells and
  traces, the plan of record stops until the cause is known, and R5' stays off.
- **PF-1 to PF-4 MET**: on these owners a guarded full periodic reconciliation at Δ = 1 s reaches EviCon's fulfilment:
  what EviCon contributes there is its evidence rules (the guard), not its trigger; with E-PF the paper reports what the
  trigger saves (owner calls per episode, time to a terminal outcome). **NOT MET with PERIODIC_FULL below EviCon**: the
  trigger and the certificate add fulfilment on that owner; **above**: periodic reconciliation is better there. Either
  NOT MET is read with the cross-run caveat (section 2): the estimate includes the difference between the runs, which
  the reference runs' EVICON levels (E-PF) bound.
- **PF-A1 to PF-A4 PRESENT or X-S2 NOT MET**: a safety failure of PERIODIC_FULL; reported, and the plan stops until the
  cause is known.

## 4. Estimands (reported with intervals, no claims)

| ID | Estimand |
|---|---|
| FI-2 | Within FIs6: the stop (K0S/EVICON) -> R5' (K0F/EVICON_FI), clean (was the hypothesis FI-2, `equivalent 5`, prediction +0.16 pp), confirmed, admitted, window-bound defers, mean time to a terminal outcome |
| FI-3 | Within FIsi: R5 -> R5' on K0F (was FI-3, `at_least 0`, prediction +1.29 pp), clean, confirmed, admitted, fences, unresolved at the end; Si K0S/EVICON -> FIsi K0F/EVICON_FI (the stop at W unbounded, across runs) |
| E-FI | Per run, contract and policy (fi084.py, from the traces): fences sent and completed, fences aborted with OWNER_UNCERTAIN (R5), fences completed with per-identity intake (R5'), terminal answers per completed fence, distinct identities the owner listed UNCERTAIN, crashes, unresolved at the end; within FIs6, R5 -> R5' confirmed, admitted, window-bound defers, fences, stranded capacity-time; S -> FIs6 for EVICON on K0F and K0S and Si -> FIsi on K0F (replications) |
| E-PF | Per PERIODIC_FULL run against its reference run's EVICON cells: confirmed and admitted fulfilment, queries, stops, fences (owner calls per episode), mean time to a terminal outcome, unresolved at the end (pairs_ext083.py and fi084.py) |
| E-P | Policy comparisons (`policy_compare.py`, section 8): per-episode differences of clean and admitted fulfilment and the safety counts, FI-E1 to FI-E4 and PF-E1 to PF-E4 |
| E-SIM | Levels per run and the tested effects against the simulator's prediction of section 1(c) (`evaluation/esim084.py`, from the decided results; (a) against the calibrated twin) |

## 5. Sample size

**Under the simulator's paired differences** (resampling the 200 episodes, Holm within each family, 2,000 resamples;
`SIMULATION_ONLY`; `sim_predictions/extra084_power.csv`; the estimands' simulator means are in `extra084_effects.csv`):

| ID | Simulator mean (SD), pp | n | Power |
|---|---|---|---|
| FI-1 | +22.22 (19.08) | 8 | 0.46 |
| PF-1, PF-2 | -0.01 (0.09), -0.01 (0.09) | 8 | 1.00 |
| PF-3, PF-4 | -0.00 (0.36), +0.02 (0.29) | 8 | 1.00 |
| estimands (no verdict) | FI-2 +0.16 (1.05); FI-3 +1.29 (1.61) | 8, 4 | - |

The simulator's per-episode differences are bimodal and more spread than the testbed's (S K0F -> K0S: SD 8.1 pp against
the twin's 19.0), so these are lower bounds. **Under the testbed's own spread** (`evaluation/power_extra084_testbed.py`:
per-episode differences of the closest earlier testbed runs, from the 081 final report's cells, centred and shifted to
a true mean, the other members of the family at the simulator's means, Holm, 4,000 resamples, seed 84; templates
s6-gap = S K0F -> K0S, episodes 0-7, SD 8.1; si-gap = Si K0F -> K0S, episodes 0-3, SD 0.9; fw-cross = C2 -> Rz
ZEPHYR_CUTOFF/EVICON plus Wqi K0F RES_PERIODIC -> EVICON, SD 1.1; minio-cross = C -> Rm MINIO_COND/EVICON plus the same
policy spread, SD 1.3). The 0.80 rule of section 3, applied to the plan as drafted:

| Rule as drafted | Predicted mean | Power | Decision |
|---|---|---|---|
| FI-1 (`at_least 10`) | +22.22 | 1.00 | hypothesis |
| FI-2 (`equivalent 5`, stop -> R5' at W = 6) | +0.16 | 0.05 | estimand |
| FI-3 (`at_least 0`, R5 -> R5' at W unbounded, n 4) | +1.29 | 0.75 | estimand |
| PF-1, PF-2 (`equivalent 3`) | -0.01, -0.01 | 1.00, 1.00 | hypotheses |
| PF-3, PF-4 (`equivalent 3`) | +0.00, +0.02 | 1.00, 1.00 | hypotheses |

The hypotheses kept, at the simulator's mean and nearby means (Holm within the kept families):

| Rule | Template (SD, pp) | True mean -> power |
|---|---|---|
| FI-1 (`at_least 10`, n 8) | s6-gap (8.1) | +22.22 (simulator): 1.00, +15: 0.46, +10: 0.06, +5: 0.00; twice the spread: 0.63 at +22.22 |
| PF-1 (`equivalent 3`, n 8) | fw-cross (1.1) | -0.01 (simulator): 1.00, +1: 1.00, +2: 0.74; three times the spread: 0.70 at -0.01 |
| PF-2 (`equivalent 3`, n 8) | fw-cross (1.1) | -0.01 (simulator): 1.00, +1: 0.99, +2: 0.74; x3: 0.71 |
| PF-3 (`equivalent 3`, n 8) | minio-cross (1.3) | +0.00 (simulator): 1.00, +1: 0.97, +2: 0.66; x3: 0.54 |
| PF-4 (`equivalent 3`, n 8) | minio-cross (1.3) | +0.02 (simulator): 1.00, +1: 0.96, +2: 0.67; x3: 0.57 |

FI-1's power rests on the calibrated prediction: if R5' recovers only the base twin's +4.5 pp, FI-1 is NOT MET (power
0.00 at +5 pp); the experiment O result (the whole gap is the orphans) is the testbed evidence for the calibrated
size. FI-2's power depends on its template (s6-gap 0.05; with si-gap, a near-null fence/stop pair, 1.00): it is an
estimand either way. Eight episodes, as the runs FIs6, PFz6, PFzi, PFm6, PFmi replay (C2's, C's, Rz's, Rm's and the
first eight of S's); four for FIsi (the first four of Si's), which keeps (a) at W unbounded within the budget.

## 6. Exclusions, stopping and order

- INVALID cells are excluded and listed (a fault could not be applied, a process died, a firmware process exited, the
  truth could not be read). An INVALID cell is re-run once by resuming the campaign; if it is INVALID again it stays
  excluded. A run with more than 10 % excluded cells is not interpretable and its hypotheses are not decided.
- No interim analysis: `pairs_ext083.py`, `policy_compare.py` and `fi084.py` run only when every cell of a run is done.
  A campaign may be split over several sessions (resume).
- **Order.** Release 083's runs come first: the 084 sessions start only when release 083's plan has finished on the
  testbed (every one of its 30 campaigns finished in `/root/EVICON_TOIT_083/results/LATEST`). Then two sessions of one
  review run of release 084, (a) before (c):
  - Session 1 (about 7.3 h): `preflight build verify distribute --new --quick` (0.4 h), `campaign expFI_s6` (2.9 h),
    `expFI_sinf` (1.0 h), `measure zephyr-plain zephyr` (0.9 h), `campaign expPF_zephyr_w6` (1.0 h),
    `expPF_zephyr_winf` (1.0 h), `bundle --incremental` after each block.
  - Session 2 (about 3.6 h): `preflight build verify distribute --quick` and `distribute --force` (0.2 h), `campaign
    expPF_minio_w6` (1.0 h), `expPF_minio_winf` (1.0 h), `report --archive /root/evicon-archive --from
    /root/EVICON_TOIT_078/results/review-20261028T080322 --from <the 083 review run>` (about 1.3 h; the data freeze),
    `bundle --incremental`.
  A measurement precedes the runs on its contract; no firmware build after session 1's measurement (a new build makes
  the firmware runs refuse to start: measured binary != run binary).
- **Not run.** The data freeze is the final `report` of this plan (session 2, or the last session the testbed allows). A
  campaign that has not completed by then is reported as NOT RUN: its hypotheses are reported NOT RUN, its estimands are
  left out, and no cell of it enters any analysis. No partial results.
- **Safety stop.** A safety violation in any cell of this plan stops the session (the operator scripts stop and bundle);
  it is reported with the cells and traces.
- The earliest complete run of each config is the preregistered one.

## 7. Frozen files

SHA-256 at freezing time (`scripts/freeze_prereg.py`); the driver checks them before running. Every file frozen by
`PREREG_extra_083.md` is frozen here again with the same SHA-256 (none changed in release 084), with that plan itself,
through the ```frozen-extra``` block; added: the 084 runners and their policy module (`ext084.py`, `campaign_ext084.py`,
`campaign_real_ext084.py`, `campaign_zephyr_ext084.py`), the delivery binding rule and its declarations, the simulator
prediction of section 1(c) with its script and its calibration, and the testbed power script of section 5. The
analysis of this plan is `pairs_ext083.py` (frozen since 083), `policy_compare.py` (frozen) and the estimand scripts
`fi084.py` and `esim084.py`, which decide nothing.

The Go code is built from release 084 (the run's `source.json` records its source). Since release 083 the Go code
changed in these places, each off unless set: the authority flag `-fence-intake` (default `all-or-nothing`, rule R5;
`per-identity`, rule R5', with the journal's barrier status PARTIAL, written only on that path; tests F52, F53, F53b and
the property test of `internal/runtime`); the resolve trigger PERIODIC_FULL (a branch of the sweep that only
`-resolve-trigger PERIODIC_FULL` takes; test F54); the simulator's `fence_uncertain: per_identity` and its PERIODIC_FULL
policy (the output of every earlier scenario is unchanged, byte for byte); the compiler's rule for expiry-revealed
evidence, ER = T and (X or (Q_A and r = infinity)), which now needs the owner's declared record retention
(`record_retention_ms` -1; the emulated device, whose queries answer from its journal, and the simulator's K3T contract
declare it; a measured contract never can) instead of an unbounded suppression horizon (no contract of this plan has T;
every earlier configuration keeps its level); and `buildinfo.Release`. The firmware's source is unchanged (only a host
test was added).

```frozen-extra
evaluation/pairs_report.py
evaluation/cadence_report.py
evaluation/policy_compare.py
evaluation/latency_control.py
evaluation/campaign_ext077.py
evaluation/campaign_real.py
evaluation/campaign_zephyr.py
evaluation/campaign_zephyr_ext.py
conformance/reference/mqtt_owner_gateway_ext.py
conformance/reference/mqtt_owner_gateway.py
conformance/reference/s3_owner_gateway.py
conformance/reference/s3_client.py
conformance/reference/_health.py
conformance/evicon_probe/faults.py
conformance/evicon_probe/adapters/mqtt5.py
conformance/evicon_probe/probes.py
conformance/evicon_probe/rubric.py
conformance/configs/zephyr-mqtt.json
conformance/configs/zephyr-mqtt-plain.json
conformance/configs/zephyr-mqtt-stop.json
firmware/zephyr-cutoff/src/main.c
firmware/zephyr-cutoff/src/evc_core.c
firmware/zephyr-cutoff/src/evc_core.h
firmware/zephyr-cutoff/src/evc_proto.c
firmware/zephyr-cutoff/src/evc_proto.h
firmware/zephyr-cutoff/Kconfig
firmware/zephyr-cutoff/prj.conf
firmware/zephyr-cutoff/CMakeLists.txt
firmware/zephyr-cutoff/boards/native_sim.conf
firmware/zephyr-cutoff/boards/native_sim_native_64.conf
experiments/owners/minio/meas-minio-plain-070.json
experiments/owners/minio/meas-minio-cond-070.json
experiments/owners/zephyr/pred-zephyr-plain-074.json
experiments/owners/zephyr/pred-zephyr-cutoff-074.json
experiments/owners/zephyr/pred-zephyr-f-075.json
experiments/owners/zephyr/pred-zephyr-stop-074.json
experiments/owners/zephyr/pred-zephyr-qa-075.json
experiments/owners/zephyr/pred-zephyr-qs-075.json
evaluation/sim_expC3.py
evaluation/sim_predictions/expC3_075_summary.csv
evaluation/sim_predictions/expC3_075_power.csv
evaluation/power_expC3_testbed.py
evaluation/sim_review077.py
evaluation/sim_predictions/review077_summary.csv
evaluation/sim_predictions/review077_power.csv
evaluation/sim_sensitivity077.py
evaluation/sim_predictions/sensitivity077_summary.csv
evaluation/power_review077_testbed.py
evaluation/late_attribution.py
evaluation/campaign_zephyr_ext077.py
evaluation/campaign_tb.py
conformance/reference/mqtt_owner_gateway_ext077.py
conformance/reference/tb_owner_gateway.py
conformance/reference/mqtt_device.py
conformance/reference/qos_dup_check.py
conformance/reference/zephyr_auth_check.py
conformance/evicon_probe/adapters/thingsboard.py
conformance/configs/thingsboard-rpc-r0.json
conformance/configs/zephyr-mqtt-mcu.json
firmware/zephyr-mcu/CMakeLists.txt
firmware/zephyr-mcu/Kconfig
firmware/zephyr-mcu/prj.conf
firmware/zephyr-mcu/boards/mps2_an385.conf
firmware/zephyr-mcu/boards/mps2_an385.overlay
firmware/zephyr-mcu/boards/nrf52840dk_nrf52840.conf
firmware/zephyr-auth/CMakeLists.txt
firmware/zephyr-auth/Kconfig
firmware/zephyr-auth/prj.conf
firmware/zephyr-auth/src/main_auth.c
firmware/zephyr-auth/src/evc_auth.c
firmware/zephyr-auth/src/evc_auth.h
evaluation/sim_theta077.py
evaluation/sim_predictions/theta077_summary.csv
evaluation/pairs_ext083.py
evaluation/ext083.py
evaluation/campaign_ext083.py
evaluation/campaign_real_ext083.py
evaluation/campaign_tb_ext083.py
evaluation/sim_extra083.py
evaluation/power_extra083_testbed.py
evaluation/sim_predictions/extra083_summary.csv
evaluation/sim_predictions/extra083_effects.csv
evaluation/sim_predictions/extra083_power.csv
experiments/sim/contracts083/sim-tb-083.json
evaluation/prereg/PREREG_extra_083.md
evaluation/ext084.py
evaluation/campaign_ext084.py
evaluation/campaign_real_ext084.py
evaluation/campaign_zephyr_ext084.py
conformance/reference/delivery_binding.py
experiments/owners/bindings/delivery-binding-084.json
evaluation/sim_extra084.py
evaluation/power_extra084_testbed.py
evaluation/sim_predictions/extra084_summary.csv
evaluation/sim_predictions/extra084_effects.csv
evaluation/sim_predictions/extra084_power.csv
evaluation/sim_predictions/extra084_calibration.csv
```

```frozen-files
1e506bf933854ea67a90ef508fa383bc5dedbb70b51a4896eb6c21efa0e7c125  evaluation/analyze.py
05f26bb9e57df331acbf8d44294324222a5d1cd5ff978b4336caa69212f7cb06  evaluation/power.py
88b7aa80a31fb16de96df62844d889dea4cd343c61162c8fbcd74ad39f76a821  evaluation/sim_to_real.py
7d928b8062787b6ba5985003f54fd1e1ae2398afb29af986c1205a8dffed0962  evaluation/campaign.py
e234ea14c1f7e3b3e012f2501cfd82262597409c2bd82d2002350592c527ec28  experiments/sim/iot_dosing_testbed.json
9af132fbb904bbe940dab6a56491479bd46e8f1ec62f85d04516fec74474ed02  evaluation/campaigns/expA_dosing.json
dc3e1caf193427cd965ad3ab6864fe9627a24974040498b53329536b0b9f8726  evaluation/pairs_report.py
014632c0597ff87b516be01f8629444046ccbca8b74cc78522beb47754dab6ba  evaluation/cadence_report.py
d518a157cba3e013b66e0e10d7442a559381e5bdadfdd0fa537f1dcfca36de64  evaluation/policy_compare.py
e0c27967a9403464c871f6b4cdd7bc53dbc85627ad37eaaf13ded0316c0e620e  evaluation/latency_control.py
39a410da83d6dd03e33c6bceffa42cb4d5ad27a1fe65a7a7c7853f237ea6ae05  evaluation/campaign_ext077.py
65eb699cf290296595eb084c031570a9edcd5004a2869de15b35c1f1ef958557  evaluation/campaign_real.py
15c46ffd982baacecbec4fce2ca39e7c6ba16fc3148bfa8fb0032bc4126947db  evaluation/campaign_zephyr.py
82a9d4f92cdc48940e262504a770a70e004d3f2f39975a98f65dde4de1b33b8c  evaluation/campaign_zephyr_ext.py
a134ecd20a151bfffa4f44867674937150e7490595bca3a28ac2f77c6783cd23  conformance/reference/mqtt_owner_gateway_ext.py
211dc8580291a6ce2b6cbf1e9033ecbf6db81a23f4ea5cc36310a8aa07153b9e  conformance/reference/mqtt_owner_gateway.py
81506987d1b347fdb46a0cbbe0e7e457309414329a055cd6dc92a04afbc4c226  conformance/reference/s3_owner_gateway.py
a2314c70898999dc16892ab9815c002d972a54826004d75d7e1ba7748a9e6d4c  conformance/reference/s3_client.py
ac7f3cad94d465453b266f9eadd423275c75451714ef064d5b3adf52b3ef655d  conformance/reference/_health.py
16571e8004a5fc394dfb4a21c25bcc952e6736176f4b1ff62d7a43c4e3e903a8  conformance/evicon_probe/faults.py
4a2c0b57cce7789658804819a3c2c387ed6f5b3deb7a978eb6fb29d505a501a1  conformance/evicon_probe/adapters/mqtt5.py
35f2552a7f6ba243e74a4f41d9c3233a8a56fcc3491a70aeeee1b444783dae75  conformance/evicon_probe/probes.py
ab366e75137b452c9c6a457c4a2399b015c8079f5ca5ea2d75db4c89ee71d72f  conformance/evicon_probe/rubric.py
9fce6819d5f860c4f343dee5354736ee24ede3f9a9208e3fd2df69105be594b1  conformance/configs/zephyr-mqtt.json
fa90d3478844503b8742291204212beb0d3fe7e4e3db4563080f5ef22062be4c  conformance/configs/zephyr-mqtt-plain.json
15735bd1bd31040f3904a96d09efaf83f59653a608f12cf76ca786e50c9d8fc5  conformance/configs/zephyr-mqtt-stop.json
6dcb257e6bdd4bbb24db92666a850acff76120f97418d4f39b4f4b9bcb8dc08a  firmware/zephyr-cutoff/src/main.c
b0b0a23b8a75699c8508f79c965ae30c4fcaeffcd6e0e72e0038c3af3908d371  firmware/zephyr-cutoff/src/evc_core.c
d4cab58a0c5c88c4fae4a63837add862cd383947d67b05aa2ba6d17e0bd4f647  firmware/zephyr-cutoff/src/evc_core.h
db075ae7ca471cd754e5bc653c9ae7f4d8219288ac301d40939cd6d8ff4e79d1  firmware/zephyr-cutoff/src/evc_proto.c
ff525dc620a20b48e97d40a90ba79db8c9088accbe49b936e14cbc881d7d1982  firmware/zephyr-cutoff/src/evc_proto.h
473360c3058bd8341b2bc00d6fd3d6f57f2520da643268499019169a20a3347f  firmware/zephyr-cutoff/Kconfig
4999fdc6ecd47ed875847ef7f8b8649c87bf1c3a72e650549fd546f0cfec99aa  firmware/zephyr-cutoff/prj.conf
224b9a65e66babd878e1124f06018d8b4b99dc75ec0f46718bee213749a91ec7  firmware/zephyr-cutoff/CMakeLists.txt
6d5a779db9b05857f8832d53791f2b801fe9a7188929dc633d2fd8dd53220784  firmware/zephyr-cutoff/boards/native_sim.conf
1cb500fef6054174ffb46e2acd2f09af0e4c166addd7bd76a4f291bcd1409ee9  firmware/zephyr-cutoff/boards/native_sim_native_64.conf
5215bebd11b54436b37063c8bcfd925327da389f9c75daf541bb68b0587d3d59  experiments/owners/minio/meas-minio-plain-070.json
dbe4797d7c73cf99049931ec1c5b97b1a657bd7d407b34e5f02b9049986017eb  experiments/owners/minio/meas-minio-cond-070.json
4c7f4f704e78d5c4887dd0c9dc0853d135cd3a5c5fdb6c592697712a534416a5  experiments/owners/zephyr/pred-zephyr-plain-074.json
69b280454fe66f8f2def5acdb95b2c6e47378a3059a9f3eb670b2ac8bd589423  experiments/owners/zephyr/pred-zephyr-cutoff-074.json
827ad1ae3501b73be812e0294363e1e8fe67c49ed9ea953973f180762aa87690  experiments/owners/zephyr/pred-zephyr-f-075.json
424f40ed02618c8da661d6000f3f66f5dd505e8f3dd461c0df8d3805320ef5fe  experiments/owners/zephyr/pred-zephyr-stop-074.json
417c1d3fcce8a724f77114a16d81f7c021acc39df35faf6427ca6e657f076993  experiments/owners/zephyr/pred-zephyr-qa-075.json
6f1cba944074e50decbe95f898dca7411f57a9d36d517663695e83abbae7ab89  experiments/owners/zephyr/pred-zephyr-qs-075.json
b7d060157317ad35b798123490530ccce78c92e66bc1f583518ca18f3e43368e  evaluation/sim_expC3.py
351dc86ccde34eee15a51a75df83d92a4fbba0ab10138a4cda5a238257a5e7f7  evaluation/sim_predictions/expC3_075_summary.csv
bd1a6b2cd51dc48306b047e91ec573514b8a8b808edb5084a34d7b7a75155fbf  evaluation/sim_predictions/expC3_075_power.csv
3dc2c2f7bfa818c2146a82708725d4db0a3b03d665a68a49542ef506808baee4  evaluation/power_expC3_testbed.py
e3374cae6c6f3f1dee7642cfb595b8dd2f23623b8b570957183d5b77dec194c4  evaluation/sim_review077.py
fd82e23455a282f75ba6118b4a16fbda8f6cd0920acca847b88181d5edb8f738  evaluation/sim_predictions/review077_summary.csv
d77b9617e20dbe4d7c91f981ba3004a0e96e36a9999614373e6d3f65e386d0e8  evaluation/sim_predictions/review077_power.csv
d41be299f833b056b39346fb06c032e8f0e25158aae5016220640cdf56bc67dd  evaluation/sim_sensitivity077.py
9e5191abe6cc26987a74bd0463230a93fd1aaeebc3d71953ab16cadb266b083b  evaluation/sim_predictions/sensitivity077_summary.csv
425e37a34890777a4609263ade04f87dcf7104c87b2276ef4e99082425439d0d  evaluation/power_review077_testbed.py
f8077caa11e3d582ed2ff846a9482023b1b328e63a6d2302c5e4aaf86d38a408  evaluation/late_attribution.py
c28e85309cabea1df4fce7a9382e5c86b0682d60b944d3e8395d7267b90644d5  evaluation/campaign_zephyr_ext077.py
917feaa3d964587af256cd12abbaefa609e836abc4e854b460e4c8a54dc1d074  evaluation/campaign_tb.py
768ac1cc78b3c2a669ff64a2ba9df2f497bcfbfbc4881a4dd14fa212dc76e1ce  conformance/reference/mqtt_owner_gateway_ext077.py
aabf42a23350634563fdb01eeca3ee9742db9a20d311e3cda2ffd04d6ad79ee6  conformance/reference/tb_owner_gateway.py
53957cf6f6aaaaa72f607fa4af74c1b373c8e39304db4820c031056cb4ee9813  conformance/reference/mqtt_device.py
5a45d5a98208081115c100981d8254707a9d1cf36d0522954045b427f5fab653  conformance/reference/qos_dup_check.py
f00efde4083569081f5f7c6979f2720adfb1f708b0eecaba048c60d887690e44  conformance/reference/zephyr_auth_check.py
24821287a662cfa8ca35403883d342adeeea99c8b4000c9fef2c42af725a717b  conformance/evicon_probe/adapters/thingsboard.py
1cb10d8e5cb762fcd21bc198eafc97dc8fb8dbf1d6ac0d752bea1e49267b34f6  conformance/configs/thingsboard-rpc-r0.json
5a4e715730ad05a187a63830fba249fd5fc84a7ad4f7435bf89b6a35944d51b6  conformance/configs/zephyr-mqtt-mcu.json
530590774973bca3634f97a363f6c67cd4910c8e8de39c071821d6ff0a54ae21  firmware/zephyr-mcu/CMakeLists.txt
73f6a1cb8607aa1731a9d99d55d649ecd8cc02a3bb116aafe500e0967693227e  firmware/zephyr-mcu/Kconfig
fbdb3dc4647a8648e94dd7ade7da4d5c69897b5d43d3cbb2d6bd7e3c9a004714  firmware/zephyr-mcu/prj.conf
e9ec5c7a03564e018dd5fb602b76dde394728816f4900977b774f4478e131e90  firmware/zephyr-mcu/boards/mps2_an385.conf
fe7f30175c18cbdb25a4186bd0e2e0565562bf9ea154865dd61df8c30adc1d55  firmware/zephyr-mcu/boards/mps2_an385.overlay
5cdab9552fe8d677d8944beffbf24dc461d61d3e2f9d6d8274f2099ee7726fd5  firmware/zephyr-mcu/boards/nrf52840dk_nrf52840.conf
c40236ccc4995f1543cb213da551517c6d88b5c49958f431fb8654d4f15a0d7b  firmware/zephyr-auth/CMakeLists.txt
99f00ecdd3196adf78376f52f539c6aa5bbf1b3fa5832c1ce557476ed01cf2cc  firmware/zephyr-auth/Kconfig
4999fdc6ecd47ed875847ef7f8b8649c87bf1c3a72e650549fd546f0cfec99aa  firmware/zephyr-auth/prj.conf
88045177009497ea74a121760160b7745cf6e8873d1f2a9d7750c79e1f66b579  firmware/zephyr-auth/src/main_auth.c
0a539591ba86f008a58411293a2bb7e9822be2f6f3b522fa99575e5793430c4d  firmware/zephyr-auth/src/evc_auth.c
20218119e87bacfd5fb0e200c6e49a924cb095caa649f5352b764ee84da0c7a5  firmware/zephyr-auth/src/evc_auth.h
e2ae26348ffbb11a8b9ebea824d4d489f10c931ccc66fad46f02d41cced5bd87  evaluation/sim_theta077.py
6b5f1ba7ccb4510708c3cf84185d903922b32c4e2d7a55edeebba2838573c113  evaluation/sim_predictions/theta077_summary.csv
392e2164e58418e934a4ed74f06e26f918e289f3ff8372b453dce68aa705d87a  evaluation/pairs_ext083.py
97e44f2f5efd8a92586abd2e7c4dac54ed3a8ee2705b416d466b5e7ab99403a8  evaluation/ext083.py
d44aaccfebcc4e84429867a00ca6b9b9343205f77f1124956ce28be3bf64ce3a  evaluation/campaign_ext083.py
5b0a73ffa072194e429ab3366c3cc90de1236f4459ed2ce0b469212493476a9b  evaluation/campaign_real_ext083.py
8b21e5b6f2a1b7a307231c6251228c2aad65f20a94cb0dc3f343f704e86ba446  evaluation/campaign_tb_ext083.py
531bf189aaf8766eaecc477935071da682c622fedce3eb81b29e92909d7dc368  evaluation/sim_extra083.py
21bb6fde62dea576e359625b193c3ed1f8679498fbf868268d30e92721171c0e  evaluation/power_extra083_testbed.py
78af26fbc0b8527a98aa77f268faff856ef02fd70dc9ce6590139a6b43fb0047  evaluation/sim_predictions/extra083_summary.csv
bcc677dbf1d11ce757594e9e3c06af08ea1866d6ed659babad58382ca743fc4a  evaluation/sim_predictions/extra083_effects.csv
713fd57dbf5ee1b77fd0f46baefa9ef95e39be40a43ad0d0978d13340826c489  evaluation/sim_predictions/extra083_power.csv
ab5a95b4cfa46e914237441ec12abf8f87f8dbefcfcf03e96acc8a260fc463b4  experiments/sim/contracts083/sim-tb-083.json
6e3f24a918de659c1feb8aaa886d9e9681195353528b5df10e615e8897ff3a06  evaluation/prereg/PREREG_extra_083.md
978b9a87c610e61c666b4b5ef508c1f2ed601898282d87c15844942c16947981  evaluation/ext084.py
37aa3dba4b539c0fe5d805c294c71b32e3e87374470540046706da764a70e50a  evaluation/campaign_ext084.py
377867c4ea88ead592c86ef11408e13fc54f9e07da3efdd8debf091d5ac1005e  evaluation/campaign_real_ext084.py
7fa7b4578beaf7d0eca26fd7380646396e1552140a950206db3773c45d9d7a33  evaluation/campaign_zephyr_ext084.py
fd4c103efdbe7d1309374af8922447bc1bdfe62b53a25616bd4b0b2b04638e10  conformance/reference/delivery_binding.py
5448902de4a52e80e850541ceac35397a623382ce1ea1521919c7fa2b98ffca2  experiments/owners/bindings/delivery-binding-084.json
7edb29309405c0b59947a08afb734cd88b99c108b64bac9a1f53e6f2405cb4c2  evaluation/sim_extra084.py
66aaf008c4a0827e2ebcd66af573ff1a01967712980df55d0f99f66d3488e369  evaluation/power_extra084_testbed.py
7e71e974a5e0513128596e9dfab28b86f907b17004c59776f6648e4dd8824a9f  evaluation/sim_predictions/extra084_summary.csv
cfa9bde3a40802c4907809917ebc3703ba836e53244156294f3404024e74adb2  evaluation/sim_predictions/extra084_effects.csv
8fc3d9dd083832bab155fa0af06aa04cca189bf784756275c300d96549ff3b31  evaluation/sim_predictions/extra084_power.csv
3a49d43232def54eee9469bf1efdb368ad9d2ea99acc7cf56e0a5b70e5a91daa  evaluation/sim_predictions/extra084_calibration.csv
```

## 8. Analysis spec (read by pairs_report.py, pairs_ext083.py and policy_compare.py)

```pairs-spec
{
  "name": "The review experiments of release 084: per-identity fence intake on the orphan schedules, and full periodic reconciliation on the firmware and on MinIO",
  "runs": {
    "FIs6": {"campaign": "expFI-s6"},
    "FIsi": {"campaign": "expFI-sinf"},
    "PFz6": {"campaign": "expPF-zephyr-w6"},
    "PFzi": {"campaign": "expPF-zephyr-winf"},
    "PFm6": {"campaign": "expPF-minio-w6"},
    "PFmi": {"campaign": "expPF-minio-winf"},
    "S": {"campaign": "expS-w6", "reference": true},
    "Si": {"campaign": "expS-winf", "reference": true},
    "C2": {"campaign": "expC2-zephyr", "reference": true},
    "Rz": {"campaign": "expR-zephyr-winf", "reference": true},
    "C": {"campaign": "expC-minio", "reference": true},
    "Rm": {"campaign": "expR-minio-winf", "reference": true}
  },
  "alpha": 0.05,
  "max_invalid_fraction": 0.1,
  "hypotheses": [
    {"id": "FI-A1", "run": "FIs6", "test": "absent", "contract": "K0F", "policy": "EVICON_FI", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "FI-A2", "run": "FIsi", "test": "absent", "contract": "K0F", "policy": "EVICON_FI", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "PF-A1", "run": "PFz6", "test": "absent", "contract": "ZEPHYR_CUTOFF", "policy": "PERIODIC_FULL", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "PF-A2", "run": "PFzi", "test": "absent", "contract": "ZEPHYR_CUTOFF", "policy": "PERIODIC_FULL", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "PF-A3", "run": "PFm6", "test": "absent", "contract": "MINIO_COND", "policy": "PERIODIC_FULL", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "PF-A4", "run": "PFmi", "test": "absent", "contract": "MINIO_COND", "policy": "PERIODIC_FULL", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]}
  ],
  "safety": {"id": "X-S1", "runs": ["FIs6", "FIsi"], "exempt_contracts": []},
  "estimands": [
    {"run": "FIs6", "policy": "EVICON", "from": "K0F", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers", "fences_reached_ok"]},
    {"run": "FIs6", "from_run": "S", "policy": "EVICON", "from": "K0F", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "FIs6", "from_run": "S", "policy": "EVICON", "from": "K0S", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "FIsi", "from_run": "Si", "policy": "EVICON", "from": "K0F", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp"]}
  ]
}
```

```pairs-ext
{
  "hypotheses": [
    {"id": "FI-1", "family": "fi-w6", "run": "FIs6", "contract": "K0F", "from_policy": "EVICON", "to_policy": "EVICON_FI", "metric": "clean_pp", "test": "at_least", "margin": 10.0},
    {"id": "PF-1", "family": "pf-fw", "run": "PFz6", "from_run": "C2", "contract": "ZEPHYR_CUTOFF", "from_policy": "EVICON", "to_policy": "PERIODIC_FULL", "metric": "clean_pp", "test": "equivalent", "margin": 3.0},
    {"id": "PF-2", "family": "pf-fw", "run": "PFzi", "from_run": "Rz", "contract": "ZEPHYR_CUTOFF", "from_policy": "EVICON", "to_policy": "PERIODIC_FULL", "metric": "clean_pp", "test": "equivalent", "margin": 3.0},
    {"id": "PF-3", "family": "pf-minio", "run": "PFm6", "from_run": "C", "contract": "MINIO_COND", "from_policy": "EVICON", "to_policy": "PERIODIC_FULL", "metric": "clean_pp", "test": "equivalent", "margin": 3.0},
    {"id": "PF-4", "family": "pf-minio", "run": "PFmi", "from_run": "Rm", "contract": "MINIO_COND", "from_policy": "EVICON", "to_policy": "PERIODIC_FULL", "metric": "clean_pp", "test": "equivalent", "margin": 3.0},
    {"id": "X-S2", "test": "safety", "runs": ["PFz6", "PFzi", "PFm6", "PFmi"]}
  ],
  "estimands": [
    {"label": "FI-2 W = 6: the stop (K0S/EVICON) -> R5' (K0F/EVICON_FI)", "run": "FIs6", "from": "K0S", "to": "K0F", "from_policy": "EVICON", "to_policy": "EVICON_FI", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers", "mean_time_to_terminal_s"]},
    {"label": "E-FI W = 6: R5 -> R5' on K0F", "run": "FIs6", "contract": "K0F", "from_policy": "EVICON", "to_policy": "EVICON_FI", "metrics": ["confirmed_pp", "admitted_pp", "window_defers", "fences", "unresolved_at_end", "stranded_resolved_unit_sec"]},
    {"label": "FI-3 W unbounded: R5 -> R5' on K0F", "run": "FIsi", "contract": "K0F", "from_policy": "EVICON", "to_policy": "EVICON_FI", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "fences", "unresolved_at_end"]},
    {"label": "FI-3 W unbounded: the stop (Si K0S/EVICON) -> R5' (FIsi K0F/EVICON_FI)", "run": "FIsi", "from_run": "Si", "from": "K0S", "to": "K0F", "from_policy": "EVICON", "to_policy": "EVICON_FI", "metrics": ["clean_pp", "confirmed_pp"]},
    {"label": "E-PF firmware W = 6", "run": "PFz6", "from_run": "C2", "contract": "ZEPHYR_CUTOFF", "from_policy": "EVICON", "to_policy": "PERIODIC_FULL", "metrics": ["confirmed_pp", "admitted_pp", "queries", "fences", "mean_time_to_terminal_s", "unresolved_at_end"]},
    {"label": "E-PF firmware W unbounded", "run": "PFzi", "from_run": "Rz", "contract": "ZEPHYR_CUTOFF", "from_policy": "EVICON", "to_policy": "PERIODIC_FULL", "metrics": ["confirmed_pp", "admitted_pp", "queries", "fences", "mean_time_to_terminal_s", "unresolved_at_end"]},
    {"label": "E-PF MinIO W = 6", "run": "PFm6", "from_run": "C", "contract": "MINIO_COND", "from_policy": "EVICON", "to_policy": "PERIODIC_FULL", "metrics": ["confirmed_pp", "admitted_pp", "queries", "stops", "mean_time_to_terminal_s", "unresolved_at_end"]},
    {"label": "E-PF MinIO W unbounded", "run": "PFmi", "from_run": "Rm", "contract": "MINIO_COND", "from_policy": "EVICON", "to_policy": "PERIODIC_FULL", "metrics": ["confirmed_pp", "admitted_pp", "queries", "stops", "mean_time_to_terminal_s", "unresolved_at_end"]},
    {"label": "E-PF the reference runs: C2 -> Rz (EVICON, the run-to-run spread on the firmware)", "run": "Rz", "from_run": "C2", "contract": "ZEPHYR_CUTOFF", "from_policy": "EVICON", "to_policy": "EVICON", "metrics": ["clean_pp"]},
    {"label": "E-PF the reference runs: C -> Rm (EVICON, the run-to-run spread on MinIO)", "run": "Rm", "from_run": "C", "contract": "MINIO_COND", "from_policy": "EVICON", "to_policy": "EVICON", "metrics": ["clean_pp"]}
  ]
}
```

```policy-compare
{
  "name": "Release 084: rule R5' against R5 and the stop, and PERIODIC_FULL against EviCon on the same schedules (PREREG_extra_084.md, E-P)",
  "runs": {
    "FIs6": {"campaign": "expFI-s6"},
    "FIsi": {"campaign": "expFI-sinf"},
    "PFz6": {"campaign": "expPF-zephyr-w6"},
    "PFzi": {"campaign": "expPF-zephyr-winf"},
    "PFm6": {"campaign": "expPF-minio-w6"},
    "PFmi": {"campaign": "expPF-minio-winf"},
    "Si": {"campaign": "expS-winf"},
    "C2": {"campaign": "expC2-zephyr"},
    "Rz": {"campaign": "expR-zephyr-winf"},
    "C": {"campaign": "expC-minio"},
    "Rm": {"campaign": "expR-minio-winf"}
  },
  "comparisons": [
    {"id": "FI-E1", "a": {"run": "FIs6", "contract": "K0F", "policy": "EVICON"}, "b": {"run": "FIs6", "contract": "K0F", "policy": "EVICON_FI"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "FI-E2", "a": {"run": "FIs6", "contract": "K0S", "policy": "EVICON"}, "b": {"run": "FIs6", "contract": "K0F", "policy": "EVICON_FI"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "FI-E3", "a": {"run": "FIsi", "contract": "K0F", "policy": "EVICON"}, "b": {"run": "FIsi", "contract": "K0F", "policy": "EVICON_FI"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "FI-E4", "a": {"run": "Si", "contract": "K0S", "policy": "EVICON"}, "b": {"run": "FIsi", "contract": "K0F", "policy": "EVICON_FI"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "PF-E1", "a": {"run": "C2", "contract": "ZEPHYR_CUTOFF", "policy": "EVICON"}, "b": {"run": "PFz6", "contract": "ZEPHYR_CUTOFF", "policy": "PERIODIC_FULL"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "PF-E2", "a": {"run": "Rz", "contract": "ZEPHYR_CUTOFF", "policy": "EVICON"}, "b": {"run": "PFzi", "contract": "ZEPHYR_CUTOFF", "policy": "PERIODIC_FULL"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "PF-E3", "a": {"run": "C", "contract": "MINIO_COND", "policy": "EVICON"}, "b": {"run": "PFm6", "contract": "MINIO_COND", "policy": "PERIODIC_FULL"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "PF-E4", "a": {"run": "Rm", "contract": "MINIO_COND", "policy": "EVICON"}, "b": {"run": "PFmi", "contract": "MINIO_COND", "policy": "PERIODIC_FULL"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]}
  ]
}
```

## 9. Pre-release checks (before freezing; not results)

Checks of the code path before freezing, on one machine and on a simulated testbed. They are not results of this plan:
the regime, the scale and the timing differ from the testbed runs.

**Unit tests.** Go: every package passes (`go test ./...`, `go vet`, `gofmt`), among them the runtime tests F52 (a
complete fence answer that lists an orphan UNCERTAIN: R5' takes the terminal answers, seals the unlisted identities at or
below the cutoff STOPPED, keeps the orphan pending and owner-uncertain, barrier PARTIAL), F53 (no fence loop and no block
by the owner-uncertain identity; a fence that would cover only owner-uncertain identities is not sent), F53b (conflicts
and invalid receipts still abort), F54 (PERIODIC_FULL: the query, then the stop or the fence under EVICON's threshold
rule, within the probe budget), the validation of `-fence-intake`, the property test over 60 random executions with
crash orphans and lost, held, late and reply-lost messages (R5': no violation in 55 per-identity fences; R5: none in 33
aborts; the negative control that treats an incomplete answer as complete: 53 false STOPPED found), the retention tests
of the compiler and the emulated device, and the simulator's `TestFenceIntakeAndPeriodicFull084`. The simulator's output
of every scenario of earlier releases is byte-identical to release 083's. Python: 188 analysis tests (among them
`test_release084` J1-J14 and `test_prereg` on every plan, `PREREG_review_077.md` still `db67640d...`,
`PREREG_extra_083.md` still `6e3f24a9...`), 80 conformance tests (0 skipped, Mosquitto present), and the firmware host
tests, among them the late copies of section 1 in the cutoff build and in the negative-control build.

**TLA+.** `formal/EviconFenceFI.tla` with TLC 1.7.4: the per-identity configurations hold every invariant; the negative
control without the completeness check violates NoFalseStopped (`formal/TLC_RESULTS_FI.txt`).

**Simulator.** `sim_extra084.py` (section 1; 200 episodes per scenario; `crash_mid_prob` fitted to the testbed's
K0F -> K0S gap before any cell) and `power_extra084_testbed.py` (section 5) on the release-084 simulator and the 081 final
report's cells.

**Analysis spec.** Synthetic runs made of renamed cells of the reference runs (FIs6: S's K0F/EVICON cells as R5, its
K0S/EVICON cells as R5' and as the stop, episodes 0-7; FIsi: Si's, episodes 0-3; PFz6, PFzi, PFm6, PFmi: the EVICON cells
of C2, Rz, C and Rm as PERIODIC_FULL), with the reference runs' schedule files: `pairs_ext083.py` with this plan decided
all 13 rules (FI-1 MET, n = 8; PF-1 to PF-4 MET with difference 0, n = 8; FI-A1, FI-A2, PF-A1 to PF-A4 ABSENT; X-S1, X-S2
MET) and paired the estimands FI-2 (n = 8) and FI-3 (n = 4); `policy_compare.py` paired its 8 comparisons; `fi084.py`
and `esim084.py` ran on the same runs.

**Simulated testbed** (the driver end to end on one machine: sshd on five loopback addresses as VM-A to VM-E, Docker as
VM-B with Mosquitto and the MinIO stand-in image; the firmware build placed as VM-D's build output; a copy of this tree
with this plan frozen in the copy; each 084 config as a copy of 1 episode of 180 s + 30 s tail; the two operator scripts
with the copy's paths; for the 083 guard a copy of release 083's 30 configs with finished stand-in runs; an empty
directory as the 078 run; an archive imported from the session-1 bundles of the same run): every stage PASS, every cell
VALID.

| Stage | Result |
|---|---|
| guard: one 083 campaign left unfinished | both scripts refuse (`REFUSED: 083 not finished ... 1 of 30 campaigns left`, exit 1) and run nothing |
| session 1: `preflight build verify distribute --new --quick` | PASS (verify: `go test`, 80 conformance tests with 0 skipped, the firmware host tests, 188 analysis tests, 3 of them skipped on the shortened copies) |
| `campaign expFI_s6`, `expFI_sinf` | PASS: 3 + 2 cells, all VALID, gate PASS, safety violations 0 |
| `measure zephyr-plain zephyr` | K0; {D_h, Q_A, F} (K3) |
| `campaign expPF_zephyr_w6`, `_winf` | PASS: 1 + 1 cells, VALID, gate PASS; the driver's binding check: every contract with binding auto gets the QoS the rule allows |
| `bundle --incremental`; the script started again | PASS; the second call skips every stage, measurement and campaign and writes no bundle |
| session 2: the stages, `distribute --force` | PASS |
| `campaign expPF_minio_w6`, `_winf` | PASS: 1 + 1 cells, VALID |
| `report --archive <archive> --from <078 run> --from <083 run>` (the planned form, strict) | PASS, every section (see below) |
| `bundle --incremental` | PASS; `ALL DONE` |

| Run | Cell | Clean | Safety counts | Notes |
|---|---|---|---|---|
| FIs6 | K0F: EVICON (R5) / EVICON_FI (R5'); K0S: EVICON | 94.0 / 100 / 100 % | 0 | fences 212 / 210 / -; aborted with OWNER_UNCERTAIN 9 / 0; per-identity intakes 0 / 4; stops 868 (K0S) |
| FIsi | K0F: EVICON / EVICON_FI | 99.7 / 100 % | 0 | fences 210 / 209; aborted 9 / 0; unresolved at the end 13 / 3 |
| PFz6, PFzi | ZEPHYR_CUTOFF / PERIODIC_FULL | 100 / 100 % | 0 | fences 116 / 115, queries 33 / 33 |
| PFm6, PFmi | MINIO_COND / PERIODIC_FULL | 99.2 / 99.2 % | 0 | stops 51 / 49, queries 58 / 59 |

`report` read the archive, the stand-ins of the 078 and 083 runs and this run, and gave `pairs_ext083.py` the six runs of
this plan: FI-A1, FI-A2, PF-A1 to PF-A4 ABSENT; X-S1, X-S2 MET; FI-1 NOT COMPUTABLE (one episode); PF-1 to PF-4 NOT RUN
(the reference runs are not in the simulated archive); FI-2 and FI-3 paired with n = 1. `policy_compare.py`, E-FI/E-PF,
E-SIM, the Level-2 analyses (9 cells; the R5 counterfactual: 18 aborted fences with 15 identities R5' would have resolved;
the rubric re-classification: one capability label changes, no measured contract), the safety total (9 guarded VALID
cells in 6 campaigns, no violation), the paper tables and the claims check (the 13 rules of this plan) ran.

**Changes made before freezing** (from these checks): the report reads a review run once when the archive holds it and it
is also read live (with `--from`, or as the run that writes the report): on the simulated testbed the archive held this
run's own session-1 bundles and the report counted those campaigns twice (16 guarded cells in 10 campaigns instead of 9
in 6; `test_release084` J14); section 2 no longer lists activation gates for PFm6 and PFmi, whose configs carry none (as
C, Rm and 083's Gm); `tb_settings()` read the reference device's response QoS as None (now 1).

## Deviations

None so far.

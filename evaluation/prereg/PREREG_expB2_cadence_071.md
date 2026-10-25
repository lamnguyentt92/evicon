# Preregistration: experiment B2 (resolution cadence) on the 5-VM testbed

Release 071. Frozen on 2026-10-21, before any B2 cell runs. The campaign config below carries the
SHA-256 of this file, and `evicon_review.py campaign` refuses to run it if this file, or any file
listed under *Frozen files*, has changed. Changes after freezing are recorded under *Deviations*;
nothing above that section is edited.

## 1. Background (exploratory evidence, not part of the test)

The confirmatory experiment A (`PREREG_expA_confirm_067.md`, run 067) and its replication on the
fixed device emulator (release 069, `REPLICATION_expA_k0_069.md`) both left the secondary
hypothesis H-B undecided. On K0F, EVICON and RES_PERIODIC reach the same clean fulfilment:
+1.74 pp and +0.85 pp, with 95 % intervals of about ±5 and ±3 pp. Yet EVICON calls the owner
fence about four times as often: 258 against 52, and 204 against 50 fences per episode.

The traces of the 069 run, analysed after it, show the mechanism:

- A fence that reaches an offline owner leaves a pending barrier. The background sweep retries it
  every `resolve_sweep`, which is **1 s for EVICON** and **10 s (`periodic`) for RES_PERIODIC**.
- 2,594 of EVICON's 3,256 fence calls failed with DEVICE_OFFLINE. The median gap between two
  consecutive DEVICE_OFFLINE results is 1.0 s for EVICON and 10.0 s for RES_PERIODIC.
- In both runs, EVICON also had lower stranded capacity-time:
  - censored: about −1,120 unit·s per episode, about −23 %, unadjusted 95 % interval excluding 0,
    not significant after Holm;
  - mean time to a terminal outcome: −0.69 s (069 run).

Experiment B2 separates the two ingredients of EVICON: its decision logic (ski-rental threshold,
certificates, candidate trigger) and its resolution cadence. It runs EVICON with the cadence of
RES_PERIODIC.

**Disclosure.** The equivalence margin (±5 pp) was chosen after seeing the H-B results above,
which already lie within ±5 pp for EVICON at a 1 s cadence in the 069 run. It is justified by the
size of the contract effect: one sixth of the +30 pp that the fence adds on K0. It is not
justified by those H-B results. B2 tests a configuration, EVICON at 10 s, that has never been run.

## 2. Design

| Item | Value |
|---|---|
| Config | `evaluation/campaigns/expB2_cadence.json` |
| Regime | identical to `expA_confirm_k0.json` (4 devices; 300 s per cell + 60 s tail; 0.5 commands/s per device; values 1-5; bound 345; deadline 60 s; validity 90 s; fault windows 2/min, offline 10-40 s, mix offline 0.3, drop_up 0.2, drop_down 0.2, delay_up 0.1, crash between effect and receipt 0.2) and the **same seed, 20261017** |
| Contract | K0F only (no forward path, owner fence) |
| Policies | EVICON with `resolve_sweep` = **10 s**; RES_PERIODIC with `periodic` = 10 s (unchanged). Every other authority parameter as in `expA_confirm_k0.json` |
| Episodes | **32** (0-31); 64 cells |
| Pairing | within B2: the two policies of the same episode (common random numbers). Across runs: episodes 0-15 replay the arrival and fault schedules of the reference run `expA-confirm-k0/20261020T073331` (release 069, EVICON at 1 s, same seed). `cadence_report.py` uses an episode across runs only when the two schedule files are byte-identical |
| Activation gate | at least one acting EVICON decision in every VALID cell |
| Unit of analysis | one episode: EVICON minus RES_PERIODIC |
| Primary metric | `clean_fulfilled` in percentage points of offered requests |
| Analysis | `evaluation/cadence_report.py --b2 <B2 run> --ref <reference run>`. `evicon_review.py report` runs it when both runs are among its sources. The driver also writes the usual `analysis/` files (`analyze.py`, frozen since 067) |

## 3. Hypotheses and decision rules

| ID | Hypothesis | Decision rule |
|---|---|---|
| **H-C** (primary) | At the same resolution cadence, EVICON's decision logic and periodic reconciliation reach the same clean fulfilment on K0F | `cadence_report.py`, `H-C.verdict`. **Equivalent** when the 90 % Student-t interval of the mean per-episode difference (EVICON − RES_PERIODIC, pp) lies inside (−5, +5): TOST, α = 0.05 per side. **Different** when instead the 95 % t-interval excludes 0. **Inconclusive** otherwise |
| **H-S** | Safety holds in every cell | `analysis/report.json`: `groups_with_safety_violations` empty |

## 4. Estimands (reported with intervals, no claims beyond them)

| ID | Estimand | Expectation (not tested) |
|---|---|---|
| E1 | Fences per episode, ratio EVICON / RES_PERIODIC, in B2 and in the reference run | Clearly below the reference ratio of about 4; we expect at most 2 |
| E2 | EVICON − RES_PERIODIC in B2: stranded capacity-time (resolved, censored), mean time to a terminal outcome, fences | Smaller stranded-time and time-to-terminal advantages than at 1 s |
| E3 | Cadence effect, difference in differences over episodes 0-15 with identical schedules: (EVICON − RES_PERIODIC) at 1 s minus at 10 s, for the metrics of E2 and clean fulfilment | Positive for fences; negative for stranded capacity-time; about 0 for clean fulfilment |

Interpretation fixed in advance:
- If H-C is equivalent and E1 drops, the extra fences of EVICON come from its cadence, not from its
  decision logic.
- E3 then prices the cadence: fence calls against stranded capacity-time.
- If H-C is "different", the decision logic itself matters at equal cadence, and the paper reports
  in which direction.

## 5. Sample size

The paired difference in clean fulfilment of EVICON and RES_PERIODIC on K0F has SD 6.5 pp in the
069 run (fixed emulator) and about 9.7 pp in the 067 run (emulator defect). Assumptions: margin
±5 pp, α = 0.05 per side, true difference 0.85 pp (069). Under them, 32 episodes give a TOST power
of about 0.97 with SD 6.5 and about 0.73 with SD 9.7. Twenty-four episodes would give 0.92 and
0.54.

## 6. Exclusions, stopping and order

- INVALID cells are excluded and listed. An INVALID cell is re-run once by resuming the campaign;
  if it is INVALID again it stays excluded.
- If more than 10 % of the cells are excluded, the result is reported as not interpretable.
- No interim analysis: the driver analyses only when every cell is done. The campaign may be
  split over several sessions with `--max-cells` (resume).
- B2 runs as one campaign, after `preflight build verify distribute` of release 071 in the same run.

## 7. Frozen files

SHA-256 at freezing time (`scripts/freeze_prereg.py`); the driver checks them before running.

The six files frozen for experiment A are frozen again here, unchanged. `cadence_report.py` is
added through the `frozen-extra` block.

The Go runtime is built from the release that runs the campaign; the run's `source.json` records
its source. Release 071 changes no Go code; only `buildinfo.Release` differs from 069 and 070.

```frozen-extra
evaluation/cadence_report.py
```

```frozen-files
1e506bf933854ea67a90ef508fa383bc5dedbb70b51a4896eb6c21efa0e7c125  evaluation/analyze.py
05f26bb9e57df331acbf8d44294324222a5d1cd5ff978b4336caa69212f7cb06  evaluation/power.py
88b7aa80a31fb16de96df62844d889dea4cd343c61162c8fbcd74ad39f76a821  evaluation/sim_to_real.py
7d928b8062787b6ba5985003f54fd1e1ae2398afb29af986c1205a8dffed0962  evaluation/campaign.py
e234ea14c1f7e3b3e012f2501cfd82262597409c2bd82d2002350592c527ec28  experiments/sim/iot_dosing_testbed.json
9af132fbb904bbe940dab6a56491479bd46e8f1ec62f85d04516fec74474ed02  evaluation/campaigns/expA_dosing.json
014632c0597ff87b516be01f8629444046ccbca8b74cc78522beb47754dab6ba  evaluation/cadence_report.py
```

## 8. Deviations

(none)

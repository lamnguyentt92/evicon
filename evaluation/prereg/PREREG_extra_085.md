# Preregistration: the experiments of release 085 (EVICON and full periodic reconciliation in the same campaigns, and the ThingsBoard RPC checks)

Release 085. Frozen before any of its cells runs on the testbed. The four campaign configs below carry the SHA-256 of
this file, and `evicon_review.py campaign` refuses to run them if this file, or any file listed under *Frozen files*,
has changed. Changes after freezing are recorded under *Deviations*; nothing above that section is edited.

## 1. Background (exploratory evidence, not part of the test)

The simulated JNCA reviewer of 4 November 2026 asked for three things that need the testbed (items M5a, M3a, M3b) and
for boundary cases of the temporal results that need none (M1, N5). The author approved the experiments on 4 November.
This plan covers the cells and the checks.

**(a) The matched comparison (M5a).** Plan 084 compared full periodic reconciliation (PERIODIC_FULL) with EviCon across
runs: PERIODIC_FULL ran alone (PFz6, PFzi, PFm6, PFmi) and was paired with the EVICON cells of earlier runs (C2, Rz, C,
Rm) on byte-identical schedules. All four equivalence hypotheses were met (PF-1 to PF-4, within 3 pp), but the arms
differed in release, day and, on the firmware, in the contract measurement (the same capability set {D_h, Q_A, F},
measured in two review runs; both arms published at QoS 1). The reviewer accepts the result as "equivalence observed
across the reported runs" and asks, for a causal reading, for a small matched comparison on the same build, owner
configuration, delivery binding and contract, preferably interleaving the policy order. Here EVICON and PERIODIC_FULL
run in one campaign per owner and window, on the schedules of the 084 PF runs; the order of the two policies alternates
by episode (even episodes EVICON first, odd episodes PERIODIC_FULL first: the new default-off runner option
`policy_order: alternate_by_episode`, `evaluation/ext085.py`).

The 084 testbed data (final report of release 084, run review-20261102T174310) give the expected size: PERIODIC_FULL
minus EVICON, clean fulfilment, +0.05 (firmware, W = 6), +0.09 (firmware, W unbounded), +0.10 (MinIO, W = 6), -0.04 pp
(MinIO, W unbounded), per-episode SD 0.16-0.26 pp; owner calls per episode, EVICON against PERIODIC_FULL: firmware at W = 6
244.2 queries and 15.8 fences against 42.0 and 127.5; at W unbounded 208.5 / 15.2 against 46.8 / 125.1; MinIO at W = 6
198.8 queries and 45.9 stops against 113.8 and 93.2; at W unbounded 254.2 / 45.1 against 113.1 / 91.8. Under a linear
cost model with logical owner calls as the unit, EviCon has the lower modelled cost when the fence (or stop) to query
cost ratio exceeds 1.81, 1.47, 1.79 and 3.03 (the reviewer's 1.81, 1.47, 1.80 and 3.02, from rounded means;
`evaluation/pm085.py` on the 084 cells, with paired bootstrap intervals 1.54-2.30, 1.37-1.59, 1.50-2.33, 1.55-5.13).

**(b) Simulator prediction.** The twin of release 084 (`evaluation/sim_extra084.py`, `sim_predictions/extra084_*.csv`,
frozen by plan 084) runs PERIODIC_FULL and EVICON on the same simulated schedules, which is the matched design of (a): it
predicts PERIODIC_FULL minus EVICON = -0.01 pp (SD 0.09) on the cutoff firmware at both windows and -0.00 (0.36) and +0.02
(0.29) pp on MinIO at W = 6 and unbounded, with no safety violation. The release-085 simulator is the 084 one (no change),
so these are the plan's predictions; the twin's owner-call counts (firmware 28.6 against 30.2 per episode) are not the
testbed's (it has no DEVICE_OFFLINE outcome and resolves far fewer commands) and are not used.

**(c) ThingsBoard (M3a, M3b).** The probe adapter maps only the RPC status `SUCCESSFUL` to APPLIED
(`conformance/evicon_probe/adapters/thingsboard.py`, TB_STATUS); release 083 measured Q_A for the persistent one-way and
two-way RPCs of ThingsBoard CE 4.3.1.6. The reviewer asks which platform event sets that status for one-way RPCs (the
current documentation names DELIVERED as the one-way terminal state), with a fault cut between the platform's hand-over
and the device's effect, and how the absolute RPC expiry behaves when the hand-off to the gateway is delayed. The checks
TB-1 to TB-4 (section 3) answer this on the testbed's ThingsBoard with the reference device extended by two test-only
switches (`conformance/reference/mqtt_device_tbcheck.py`: hold the next handler between the receipt and the plant write;
send the MQTT acknowledgement before the plant write, a negative control) and the check module
`conformance/tb_rpc_checks.py`, run by the new driver stage `tbcheck`. Expectations (not the rules): one-way SUCCESSFUL
follows the device's acknowledgement, two-way SUCCESSFUL follows its reply; a held handler shows neither; a past-expiry
request is expired by the platform.

**(d) No cells (section 9).** The fence pause under rule R5' (N5): `evaluation/overhead.py` now ends a pause at
BARRIER_PER_IDENTITY_COMMITTED too (on the 084 traces: W = 6, 187 pauses, median 1,137.0 ms, paused fraction 0.1248; W
unbounded, 99, 1,105.5 ms, 0.1264; the R5 rows unchanged); the authority's `-expires-after` is transport metadata (M3b:
two tests); TLC boundary cases of the temporal results (M1, `formal/EviconTiming.tla`); three report analyses (the
safety result split by transport premise, an interval for the effect of removing the window on the firmware at QoS 0,
an offline upper bound on clean fulfilment).

## 2. Design

Common regime: that of the runs each new run replays (4 devices; 300 s per cell + 60 s tail; 0.5 commands/s per device;
values 1-5; bound 345 per device; deadline 60 s; validity 90 s; fault windows 2/min; offline 10-40 s; mix offline 0.3,
drop_up 0.2, drop_down 0.2, delay_up 0.1 (6 s), crash 0.2; authority: no assumed bound on the network delay, SKI_RENTAL
resolution, query first, costs hold 1, stop 1, fence 5, probe budget 4, the T4 retry guard on). Each config is a copy of
the 084 PF config of the same owner and window and differs from it only in the name, the policies (EVICON,
PERIODIC_FULL), `policy_order`, the activation gate (firmware: both policies), the plan and the notes (test K5 of
`test_release085`).

| Run | Config | Runner | Owner, channel | Contract | Policies (order) | W | Seed (replays) | Episodes | Cells | Hours |
|---|---|---|---|---|---|---|---|---|---|---|
| **PMz6** | `expPM_zephyr_w6.json` | `campaign_zephyr_ext085.py` | the cutoff firmware behind Mosquitto, queue, binding auto (QoS 1) | ZEPHYR_CUTOFF (measured in this review run) | EVICON, PERIODIC_FULL (alternating) | 6 | 20261017 (C2, PFz6) | 8 | 16 | 1.95 |
| **PMzi** | `expPM_zephyr_winf.json` | `campaign_zephyr_ext085.py` | as PMz6 | ZEPHYR_CUTOFF | as PMz6 | 1000 | 20261017 (Rz, PFzi) | 8 | 16 | 1.95 |
| **PMm6** | `expPM_minio_w6.json` | `campaign_real_ext085.py` | MinIO behind the S3 gateway, direct | MINIO_COND (the 070 measured contract) | as PMz6 | 6 | 20261025 (C, PFm6) | 8 | 16 | 1.95 |
| **PMmi** | `expPM_minio_winf.json` | `campaign_real_ext085.py` | as PMm6 | MINIO_COND | as PMz6 | 1000 | 20261025 (Rm, PFmi) | 8 | 16 | 1.95 |
| **TBC** | stage `tbcheck` | `conformance/tb_rpc_checks.py` | ThingsBoard CE (VM-B), the check device (VM-E), one ThingsBoard owner gateway (VM-C) | - | - | - | - | 140 trials | - | 0.75 |

Total: 4 campaigns, 64 cells (7.3 min per cell: the 084 PF runs took 57.6-58.4 min for 8 cells), about 7.8 hours of
cells, and the checks (section 6: about 10.3 testbed hours with preflight, build, verify, distribute, the firmware
measurement, the checks and the final report).

Reference runs (no new cells; read from the archive, the 078 run and the 084 run): C2 `expC2-zephyr/20261024T044342`, Rz
`expR-zephyr-winf/20261028T084744`, C `expC-minio/20261023T083658`, Rm `expR-minio-winf/20261028T145228` (EVICON, the earliest
complete run of each config, as the report selects them), and the 084 PF runs PFz6 `expPF-zephyr-w6/20261102T221954`, PFzi
`expPF-zephyr-winf/20261102T231817`, PFm6 `expPF-minio-w6/20261103T001652`, PFmi `expPF-minio-winf/20261103T011429`.

- **Within run.** PM-1 to PM-4 are within-run contrasts: both policies on the same build (one review run of release 085,
  one source fingerprint), the same day, the same measured contract file and delivery binding (firmware: the contract of
  `measure zephyr-plain zephyr` in this review run, binding auto = QoS 1 by the 084 delivery binding rule; MinIO: the 070
  contract, direct channel), the same owner configuration and the same schedule per episode. `evaluation/pm085.py`
  checks from the cells' gateway logs that every cell of both policies of a firmware run started its gateways with the
  same contract SHA-256, binding and command QoS, and reports the measurement it used.
- **Policy order.** `policy_order: alternate_by_episode` (`evaluation/ext085.py`): the frozen runners' `run_campaign`
  runs unchanged except for the policy loop, which follows `ext085.policy_order(cfg, ep)` (the config's order in even
  episodes, the reverse in odd ones); the run's manifest records the order of every episode, and `pm085.py` the order
  the cells actually ran (done.jsonl). Without the key every runner behaves as before (test K4).
- **Runners.** `campaign_zephyr_ext085.py` (on the frozen `campaign_zephyr_ext084.py`, which checks the delivery binding
  rule before the first cell) and `campaign_real_ext085.py` (on the frozen `campaign_real_ext084.py`); the policies'
  arguments are those of `ext084.py` (EVICON; PERIODIC_FULL = EVICON's with `-resolve-trigger PERIODIC_FULL`). The driver
  picks them by the config key (`evicon_review.py`: `_uses_085_options`).
- **Truth**: `evicon-plant` on VM-E (MinIO: the object versions, as in C, Rm and the PF runs).
- **Pairing**: within a run, the two policies of an episode; across runs (the estimands of section 4), episodes whose
  schedule files are byte-identical (test K5 replays them through `campaign.schedule`).
- **Activation gates**: at least one acting decision of EVICON and of PERIODIC_FULL in every VALID cell of PMz6 and PMzi;
  none on PMm6 and PMmi (as C, Rm and the PF runs on MinIO).
- **Analysis**: `evaluation/pairs_ext083.py --plan evaluation/prereg/PREREG_extra_085.md --run <key>=<run> ...` (frozen by
  plan 083): the ```pairs-spec``` block through the frozen `pairs_report.py` and the ```pairs-ext``` block with its
  own functions; Holm within each family; the two blocks' families are disjoint. `evaluation/policy_compare.py` runs the
  ```policy-compare``` block, `evaluation/pm085.py` the estimands E-PM. The checks TB-1 to TB-4 are decided by
  `conformance/tb_rpc_checks.py` (`rules`, frozen here) from its trials. `evicon_review.py report` runs all of them with
  the earliest complete run of each campaign among its sources and the newest `tbcheck` among them.
- **The checks (TBC).** ThingsBoard CE as the release deploys it (`thingsboard/tb-node:4.3.1.6`, EviCon's compose project
  on VM-B, installed and provisioned as for `measure thingsboard`; the exact version is recorded from `/api/system/info`),
  the check device on VM-E in ThingsBoard mode at QoS 1, the plant on VM-E, and one ThingsBoard owner gateway
  (`conformance/reference/tb_owner_gateway.py`, frozen, unchanged) on VM-C. Credentials only from the environment
  (TB_USERNAME, TB_PASSWORD, TB_DEVICE_TOKEN). Call shapes: the measured ones (`thingsboard-rpc-oneway`: persistent,
  retries 2; `thingsboard-rpc-r0`: persistent, retries 0; timeout 5000 ms, expirationTime = send + 60 s); in CUT and ACK
  the RPC timeout is 30000 ms, longer than the hold. Times are VM-C wall-clock times after the trial's send; the plant's
  effect time (VM-E clock) is converted with the clock offset VM-E - VM-C that the check measures over HTTP to the check
  device on VM-E, the plant's host (the shortest of 20 round trips; uncertainty u = half that round trip; again after the
  trials), not with the driver's ssh-based offset, which is biased by about half the ssh round trip (section 9).
  SEQ (20 per direction), CUT (10 per direction), ACK (10 per direction) and TTL (5 per kind: expires_at_ms already past,
  created 70 s ago; within, created 50 s ago; without), as `tb_rpc_checks.py` describes. Every RPC the checks create is
  deleted at the end and the device is left in its default modes. Only a run on the testbed's ThingsBoard counts; a run
  against the simulated testbed's stand-in is marked so and is not a result.

## 3. Hypotheses, checks and decision rules

Tests on per-episode paired differences (to minus from), clean fulfilment in pp of offered, Student t, alpha = 0.05;
TOST for equivalence; Holm within each family. Absence, safety and check rules have no p-value and are in no family.

**The 0.80 rule** (as in plans 083 and 084). A paired-difference rule is a confirmatory hypothesis only if its power under
the testbed's own spread (section 5), at the predicted (simulator) mean and with Holm in its family, is at least 0.80;
otherwise an estimand. Applied before freezing: PM-1 to PM-4 have power 1.00 under both templates; all four are
hypotheses.

The five safety counts are bound violations, duplicate effects, logical duplicates, false STOPPED and false APPLIED, as
in every plan. Block: S = ```pairs-spec``` (pairs_report.py), X = ```pairs-ext``` (pairs_ext083.py), K =
`tb_rpc_checks.rules` (kind "check").

| ID | Block, family | Hypothesis or check | Decision rule |
|---|---|---|---|
| **PM-1** | X, pm-fw | On the cutoff firmware at W = 6, in one campaign, full periodic reconciliation is equivalent to EviCon (within 3 pp) | `equivalent 3`, run PMz6, ZEPHYR_CUTOFF, EVICON -> PERIODIC_FULL, `clean_pp` |
| **PM-2** | X, pm-fw | ... at W unbounded | `equivalent 3`, run PMzi, ZEPHYR_CUTOFF, EVICON -> PERIODIC_FULL, `clean_pp` |
| **PM-3** | X, pm-minio | On MinIO's conditional PUT at W = 6 | `equivalent 3`, run PMm6, MINIO_COND, EVICON -> PERIODIC_FULL, `clean_pp` |
| **PM-4** | X, pm-minio | ... at W unbounded | `equivalent 3`, run PMmi, MINIO_COND, EVICON -> PERIODIC_FULL, `clean_pp` |
| **PM-A1 to PM-A8** | S, - | Both policies are safe in every run | `absent`, runs PMz6, PMzi (ZEPHYR_CUTOFF), PMm6, PMmi (MINIO_COND), EVICON and PERIODIC_FULL, the five safety counts |
| **PM-S** | S, - | Safety in every cell of the four runs (both policies) | `safety` (pairs_report): the five counts 0 in every VALID cell of PMz6, PMzi, PMm6, PMmi; late effects reported separately |
| **TB-1** | K | One-way persistent RPC (SEQ, n = 20): in every trial the status the adapter maps to APPLIED (SUCCESSFUL) is first seen after the plant's effect | MET: every valid trial reaches SUCCESSFUL and has exactly one effect, whose plant time (converted to VM-C's clock) plus the clock uncertainty u is earlier than the end of the first poll that returned SUCCESSFUL; NOT REACHED: no trial reaches SUCCESSFUL; NOT MET otherwise |
| **TB-2** | K | CUT, both directions (n = 10 each): no APPLIED and no effect while the device's handler is held after the platform's hand-over | ABSENT: in no exercised trial (handed over, i.e. SENT or a later status, and the device held the handler through the 10 s window) SUCCESSFUL was seen, the applied query answered APPLIED or the plant had an effect while held; DETECTED otherwise |
| **TB-3a** | K | ACK negative control, one-way (n = 10): the device acknowledges before its effect and holds the handler | DETECTED: in at least one exercised trial SUCCESSFUL (or an APPLIED answer) while held, i.e. before the effect; ABSENT otherwise |
| **TB-3b** | K | ACK negative control, two-way (n = 10) | as TB-3a, two-way |
| **TB-4** | K | TTL through the ThingsBoard owner gateway (n = 5 per kind) | MET: every past-expiry request (expires_at_ms = created 70 s ago + 60 s) has no effect and the RPC's expirationTime equals expires_at_ms; every within-expiry request (created 50 s ago) has exactly one effect and expirationTime = expires_at_ms; every request without expires_at_ms has exactly one effect and expirationTime = the gateway's receipt time + 60 s (within the request's wall-clock interval on VM-C, +-1 ms); NOT MET otherwise |

In all, 18 rules: 4 paired-difference hypotheses (two families), 8 absence rules, 1 safety rule and 5 checks. A check
with more than 10 % invalid trials (a REST or admin call failed), or, for TB-2 and TB-3, fewer than 90 % exercised
trials, is NOT INTERPRETABLE; `tbcheck` is then run once more (`--force`) and the second run decides.

Interpretation fixed in advance:
- **PM-1 to PM-4 MET**: on the same build, day, measured contract, delivery binding and schedules, a guarded full
  periodic reconciliation at Δ = 1 s reaches EviCon's clean fulfilment within 3 pp on these owners: the equivalence of
  plan 084 holds in a matched design, and the differences of E-PM (owner calls, time to a terminal outcome, fence pause)
  are differences between the two triggers under identical conditions (for these owners, schedules and regime, not in
  general). **NOT MET with the TOST interval below -3 pp**: EviCon's trigger and certificate add clean fulfilment on that
  owner (the 084 cross-run equivalence did not survive the matched design). **NOT MET above +3 pp**: periodic
  reconciliation is better there. **NOT MET with the interval across a margin**: equivalence is not established; the
  estimate and interval are reported. In every case the paper reports the 084 cross-run results next to these.
- **PM-A1 to PM-A8 PRESENT or PM-S NOT MET**: a safety failure; reported with the cells and traces, the plan of record
  stops until the cause is known.
- **TB-1 MET**: for this device on this platform version the one-way status mapped to APPLIED follows the device's effect
  in every trial; with TB-3a it says which event sets it. **TB-1 NOT MET**: SUCCESSFUL was seen before the effect (or
  without exactly one effect): the paper reclassifies the one-way row (Q_A not established for one-way RPCs). **TB-1 NOT
  REACHED**: one-way RPCs never reach SUCCESSFUL on this version: the paper reclassifies the one-way row and states how
  the 083 measurement observed Q_A (or that it cannot have from SUCCESSFUL).
- **TB-2 ABSENT**: a fault cut between the platform's hand-over and the device's effect produces no APPLIED and no
  effect on either direction: the status mapped to APPLIED waits for a device event after the cut. **TB-2 DETECTED on
  one-way**: one-way APPLIED does not wait for the device: the one-way row is reclassified. **TB-2 DETECTED on two-way**:
  the two-way Q_A that the ThingsBoard runtime runs rely on is not established on this path; the paper's ThingsBoard
  results are restated as observations without that premise.
- **TB-3a DETECTED**: one-way SUCCESSFUL follows the device's MQTT acknowledgement (not its effect): one-way Q_A rests on
  the device acknowledging after its effect, which the reference device does (TB-1); the paper states that device premise
  for the one-way row. **TB-3a ABSENT**: one-way SUCCESSFUL does not follow the acknowledgement alone (TB-1 and TB-2 then
  say what it follows). **TB-3b ABSENT**: two-way SUCCESSFUL waits for the device's reply, which the device sends after
  its effect, whatever the acknowledgement order. **TB-3b DETECTED**: two-way SUCCESSFUL is set on the acknowledgement:
  two-way Q_A also rests on acknowledgement after the effect, a premise the paper must state for the ThingsBoard runs.
- **TB-4 MET**: the gateway forwards the absolute expiry it is given; the platform never delivers a past-expiry RPC and
  delivers a within-expiry one; without expires_at_ms the expiry is the gateway's receipt time + 60 s, so a strict
  contract {Q_A} without an absolute expiry would expire later after a delayed hand-off (review M3b), and the 084
  configuration's absolute expiry is kept by `-expires-after 60s` (section 9: byte-identical dispatch requests, the same
  decisions). **TB-4 NOT MET**: the part that differs is reported and the paper describes the gateway's actual behaviour.

## 4. Estimands (reported with intervals, no claims)

| ID | Estimand |
|---|---|
| E-PM | Per matched run and policy (`pm085.py`, `pairs_ext083.py`): logical owner calls per episode (queries, stops, fences and their sum; one call of the owner's evidence API, a fence answer composed by the gateway being one call), clean and confirmed fulfilment, admitted, mean time to a terminal outcome, unresolved at the end; the fence pause (number of pauses, p50 / p95 / max, paused fraction; the end events of release 085, rounded-index percentiles); the break-even cost ratio r* = -mean(Δqueries) / mean(Δfences) (firmware) or -mean(Δqueries) / mean(Δstops) (MinIO), Δ = EVICON minus PERIODIC_FULL per episode, with a paired bootstrap 95 % interval (10,000 resamples, seed 85): EviCon has the lower modelled cost under a linear model with logical owner calls as the unit iff the fence (stop) to query cost ratio exceeds r* (arithmetic on call counts, not a measured energy, latency or wear result); the same-contract check and the order of the cells; within-run differences EVICON -> PERIODIC_FULL of confirmed fulfilment, queries, fences or stops, time to terminal and unresolved at the end (pairs-ext estimands) |
| E-PM-R | Run to run: the 085 EVICON arm against the earlier EVICON cells on the same schedules (C2 -> PMz6, Rz -> PMzi, C -> PMm6, Rm -> PMmi), and the 085 PERIODIC_FULL arm against the 084 PF runs (PFz6 -> PMz6, ...), clean and confirmed fulfilment, queries and fences or stops; the 084 cross-run break-even of section 1(a) next to the matched one |
| E-P | Policy comparisons (`policy_compare.py`, section 8): per-episode differences of clean and admitted fulfilment and the safety counts, PM-E1 to PM-E4 |
| E-TB | Per check and direction (`tb_rpc_checks.py`): the observed status sequences with their times, the margin between the plant's effect and the first SUCCESSFUL, the statuses seen while held (DELIVERED with the early acknowledgement), the final statuses after the release, the expirationTime the gateway sent, the platform version |

## 5. Sample size

**Prediction.** The simulator of plan 084 runs the two policies on the same schedules (section 1(b)): PERIODIC_FULL minus
EVICON -0.01 (firmware, both windows), -0.00 and +0.02 pp (MinIO, W = 6 and unbounded). **Testbed spread**
(`evaluation/power_extra085_testbed.py`; the per-episode differences of the 084 final report's cells, copied into
`sim_predictions/extra085_templates.csv` with the SHA-256 of their source): the primary templates are the 084 PF pairs
(PERIODIC_FULL of the 084 run minus EVICON of the reference run on the same schedules: one policy per run, a release and
a day apart, so they include the run-to-run spread and are conservative for a within-run pair; SD 0.26, 0.16, 0.18,
0.24 pp); the within-run templates are two guarded arms of one run of the same owner (C2: ZEPHYR_STOP minus
ZEPHYR_CUTOFF, EVICON, SD 0.29; C: MINIO_COND NAIVE minus EVICON, SD 0.17). Each rule as `pairs_ext083.py` tests it (TOST
with margin 3 pp, Holm within its family with the other member at its predicted mean), n = 8, 4,000 resamples, seed 85
(`sim_predictions/extra085_power.csv`):

| Rule | Family | Predicted mean (simulator) | 084 cross-run mean | Template SD, primary / within | Power (primary) | Power (within) | Decision |
|---|---|---|---|---|---|---|---|
| PM-1 | pm-fw | -0.01 | +0.05 | 0.26 / 0.29 | 1.00 | 1.00 | hypothesis |
| PM-2 | pm-fw | -0.01 | +0.09 | 0.16 / 0.29 | 1.00 | 1.00 | hypothesis |
| PM-3 | pm-minio | -0.00 | +0.11 | 0.18 / 0.17 | 1.00 | 1.00 | hypothesis |
| PM-4 | pm-minio | +0.02 | -0.04 | 0.24 / 0.17 | 1.00 | 1.00 | hypothesis |

| Rule | True mean -> power (primary template) | Spread x3 / x10 at the prediction |
|---|---|---|
| PM-1 | -0.01: 1.00, +1.00: 1.00, +2.00: 1.00, +2.50: 1.00, -2.00: 1.00 | 1.00 / 0.90 |
| PM-2 | -0.01: 1.00, +1.00: 1.00, +2.00: 1.00, +2.50: 1.00, -2.00: 1.00 | 1.00 / 1.00 |
| PM-3 | -0.00: 1.00, +1.00: 1.00, +2.00: 1.00, +2.50: 1.00, -2.00: 1.00 | 1.00 / 0.97 |
| PM-4 | +0.02: 1.00, +1.00: 1.00, +2.00: 1.00, +2.50: 1.00, -2.00: 1.00 | 1.00 / 0.94 |

Eight episodes, as the PF runs replayed (C2's, Rz's, C's and Rm's). The per-episode clean fulfilment of the two arms is
almost perfectly correlated on the same schedule (correlation 1.00 in the 084 pairs; arm SDs 2.1-3.5 pp), so the pairing
by episode carries the power; an unpaired comparison of the arms would not be powered (no unpaired rule is planned). The
checks have fixed counts (SEQ 20, CUT 10, ACK 10 per direction, TTL 5 per kind): they are detection, absence and
consistency rules without a power requirement; ACK's 10 trials per direction suffice to see a status the platform sets
on every acknowledgement.

## 6. Exclusions, stopping and order

- INVALID cells are excluded and listed (a fault could not be applied, a process died, a firmware process exited, the
  truth could not be read). An INVALID cell is re-run once by resuming the campaign; if it is INVALID again it stays
  excluded. A run with more than 10 % excluded cells is not interpretable and its hypotheses are not decided.
- A check with more than 10 % invalid trials, or (TB-2, TB-3) fewer than 90 % exercised trials, is not interpretable;
  `tbcheck` is run once more with `--force` and the second run decides; if it is not interpretable again, the check is
  reported NOT INTERPRETABLE.
- No interim analysis: `pairs_ext083.py`, `policy_compare.py` and `pm085.py` run only when every cell of a run is done. A
  campaign may be split over several sessions (resume).
- **Order** (one review run of release 085; the operator script `run085.sh`): `preflight build verify distribute --new
  --quick` (0.15 h); `measure zephyr-plain zephyr` (0.65 h; the firmware contract in this review run, needed by the
  firmware runs); `tbcheck --from <the 083 review run>` (0.75 h; the 083 run holds the measured thingsboard-rpc-r0
  contract the frozen gateway starts with); `campaign expPM_zephyr_w6`, `expPM_zephyr_winf`, `expPM_minio_w6`,
  `expPM_minio_winf` (1.95 h each); `report --archive /root/evicon-archive --from
  /root/EVICON_TOIT_078/results/review-20261028T080322 --from <the 083 review run> --from <the 084 review run>` (0.8 h;
  the data freeze); `bundle --incremental` after new work. About 10.3 h in all; the script can stop before a campaign
  that would not end by a given time (`--until HH:MM`, Vietnam time) and resumes in a second session, e.g. session 1
  (about 5.4 h): the stages, the measurement, the checks, PMz6, PMzi; session 2 (about 4.9 h): the stages again
  (`distribute --force`), PMm6, PMmi, the report. No firmware build after the measurement (a new build makes the
  firmware runs refuse to start).
- **Not run.** The data freeze is this plan's final `report`. A campaign not completed by then is NOT RUN: its hypotheses
  are reported NOT RUN, its estimands are left out, no cell of it enters any analysis. A check not run is NOT RUN.
- **Safety stop.** A safety violation in any cell of this plan stops the session (the operator script stops and bundles);
  it is reported with the cells and traces.
- **Earlier plans.** The final report also decides every earlier plan; their verdicts must equal the 084 final report's
  (`evaluation/verdicts085.py` lists every difference; a difference is logged as a FAIL and reported).
- The earliest complete run of each config is the preregistered one.

## 7. Frozen files

SHA-256 at freezing time (`scripts/freeze_prereg.py`); the driver checks them before running. Every file frozen by
`PREREG_extra_084.md` is frozen here again with the same SHA-256 (none changed in release 085), with that plan itself,
through the ```frozen-extra``` block; added: the 085 runners and their option module (`ext085.py`,
`campaign_zephyr_ext085.py`, `campaign_real_ext085.py`), the check module and the check device (`conformance/tb_rpc_checks.py`,
which decides TB-1 to TB-4, and `conformance/reference/mqtt_device_tbcheck.py`), and the testbed power script with its
templates and output. The analysis of this plan is `pairs_ext083.py` (frozen since 083), `policy_compare.py` (frozen),
`tb_rpc_checks.rules` (frozen here) and the estimand script `pm085.py`, which decides nothing.

The Go code is built from release 085 (the run's `source.json` records its source). Since release 084 the Go code
changed in these places only: `buildinfo.Release`; the rule that gives the dispatcher's expiry from `-expires-after`
and the contract moved from `cmd/evicon-authority/main.go` into `internal/outbox.DispatchExpiry`, unchanged (tests
`TestDispatchExpiry085`, `TestExpiresAfterDispatchIdentical085`, `TestTTLChangesNoDecision085`; the flag's help text
names it transport metadata). The authority's admission, resolution and release are unchanged; the simulator is
unchanged. The firmware is unchanged.

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
evaluation/prereg/PREREG_extra_084.md
evaluation/ext085.py
evaluation/campaign_zephyr_ext085.py
evaluation/campaign_real_ext085.py
conformance/tb_rpc_checks.py
conformance/reference/mqtt_device_tbcheck.py
evaluation/power_extra085_testbed.py
evaluation/sim_predictions/extra085_templates.csv
evaluation/sim_predictions/extra085_power.csv
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
b11351645c1e0f05f4a270412539a072f17148ea8607223dcb6182a171505345  evaluation/prereg/PREREG_extra_084.md
7bda8a41bed46610890e66c10c3789e228421b9c2fe33c3a11495409984720b4  evaluation/ext085.py
94afd4d630bf56f850aa89fa668ecc583548ff84a1bbbb13666bfccb17753bf4  evaluation/campaign_zephyr_ext085.py
029aa988ee7bbf2a12ef445e7417194937bac7dea85880a44c9350cb2c6274ca  evaluation/campaign_real_ext085.py
ac2e0ffc87abcdabd6ed0e86245acb3fa4b6049d75bfc96e9dc52ba9d41117de  conformance/tb_rpc_checks.py
bfa513619f58c3da77df9bb172ebcb6eb15213598d407049abb4d467f87689bf  conformance/reference/mqtt_device_tbcheck.py
2b56c5b87c2d718b1f589233ae16e7aa4fd79e72669da7fffd9261cc5a4683ff  evaluation/power_extra085_testbed.py
ce0de1a0f4716a0fe72d8ace491f7f99410aeb8c889bbbd805c1267cc00f02f7  evaluation/sim_predictions/extra085_templates.csv
c6fc4be8418e265169b6b90b8c21cc98715a84cafafdd069ab925dbe34315fd7  evaluation/sim_predictions/extra085_power.csv
```

## 8. Analysis spec (read by pairs_report.py, pairs_ext083.py and policy_compare.py)

```pairs-spec
{
  "name": "The experiments of release 085: EVICON and full periodic reconciliation in the same campaigns (the matched comparison)",
  "runs": {
    "PMz6": {"campaign": "expPM-zephyr-w6"},
    "PMzi": {"campaign": "expPM-zephyr-winf"},
    "PMm6": {"campaign": "expPM-minio-w6"},
    "PMmi": {"campaign": "expPM-minio-winf"},
    "C2": {"campaign": "expC2-zephyr", "reference": true},
    "Rz": {"campaign": "expR-zephyr-winf", "reference": true},
    "C": {"campaign": "expC-minio", "reference": true},
    "Rm": {"campaign": "expR-minio-winf", "reference": true},
    "PFz6": {"campaign": "expPF-zephyr-w6", "reference": true},
    "PFzi": {"campaign": "expPF-zephyr-winf", "reference": true},
    "PFm6": {"campaign": "expPF-minio-w6", "reference": true},
    "PFmi": {"campaign": "expPF-minio-winf", "reference": true}
  },
  "alpha": 0.05,
  "max_invalid_fraction": 0.1,
  "hypotheses": [
    {"id": "PM-A1", "run": "PMz6", "test": "absent", "contract": "ZEPHYR_CUTOFF", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "PM-A2", "run": "PMz6", "test": "absent", "contract": "ZEPHYR_CUTOFF", "policy": "PERIODIC_FULL", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "PM-A3", "run": "PMzi", "test": "absent", "contract": "ZEPHYR_CUTOFF", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "PM-A4", "run": "PMzi", "test": "absent", "contract": "ZEPHYR_CUTOFF", "policy": "PERIODIC_FULL", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "PM-A5", "run": "PMm6", "test": "absent", "contract": "MINIO_COND", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "PM-A6", "run": "PMm6", "test": "absent", "contract": "MINIO_COND", "policy": "PERIODIC_FULL", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "PM-A7", "run": "PMmi", "test": "absent", "contract": "MINIO_COND", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "PM-A8", "run": "PMmi", "test": "absent", "contract": "MINIO_COND", "policy": "PERIODIC_FULL", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]}
  ],
  "safety": {"id": "PM-S", "runs": ["PMz6", "PMzi", "PMm6", "PMmi"], "exempt_contracts": []},
  "estimands": [
    {"run": "PMz6", "from_run": "C2", "policy": "EVICON", "from": "ZEPHYR_CUTOFF", "to": "ZEPHYR_CUTOFF", "metrics": ["clean_pp", "confirmed_pp", "queries", "fences"]},
    {"run": "PMz6", "from_run": "PFz6", "policy": "PERIODIC_FULL", "from": "ZEPHYR_CUTOFF", "to": "ZEPHYR_CUTOFF", "metrics": ["clean_pp", "confirmed_pp", "queries", "fences"]},
    {"run": "PMzi", "from_run": "Rz", "policy": "EVICON", "from": "ZEPHYR_CUTOFF", "to": "ZEPHYR_CUTOFF", "metrics": ["clean_pp", "confirmed_pp", "queries", "fences"]},
    {"run": "PMzi", "from_run": "PFzi", "policy": "PERIODIC_FULL", "from": "ZEPHYR_CUTOFF", "to": "ZEPHYR_CUTOFF", "metrics": ["clean_pp", "confirmed_pp", "queries", "fences"]},
    {"run": "PMm6", "from_run": "C", "policy": "EVICON", "from": "MINIO_COND", "to": "MINIO_COND", "metrics": ["clean_pp", "confirmed_pp", "queries", "stops"]},
    {"run": "PMm6", "from_run": "PFm6", "policy": "PERIODIC_FULL", "from": "MINIO_COND", "to": "MINIO_COND", "metrics": ["clean_pp", "confirmed_pp", "queries", "stops"]},
    {"run": "PMmi", "from_run": "Rm", "policy": "EVICON", "from": "MINIO_COND", "to": "MINIO_COND", "metrics": ["clean_pp", "confirmed_pp", "queries", "stops"]},
    {"run": "PMmi", "from_run": "PFmi", "policy": "PERIODIC_FULL", "from": "MINIO_COND", "to": "MINIO_COND", "metrics": ["clean_pp", "confirmed_pp", "queries", "stops"]}
  ]
}
```

```pairs-ext
{
  "hypotheses": [
    {"id": "PM-1", "family": "pm-fw", "run": "PMz6", "contract": "ZEPHYR_CUTOFF", "from_policy": "EVICON", "to_policy": "PERIODIC_FULL", "metric": "clean_pp", "test": "equivalent", "margin": 3.0},
    {"id": "PM-2", "family": "pm-fw", "run": "PMzi", "contract": "ZEPHYR_CUTOFF", "from_policy": "EVICON", "to_policy": "PERIODIC_FULL", "metric": "clean_pp", "test": "equivalent", "margin": 3.0},
    {"id": "PM-3", "family": "pm-minio", "run": "PMm6", "contract": "MINIO_COND", "from_policy": "EVICON", "to_policy": "PERIODIC_FULL", "metric": "clean_pp", "test": "equivalent", "margin": 3.0},
    {"id": "PM-4", "family": "pm-minio", "run": "PMmi", "contract": "MINIO_COND", "from_policy": "EVICON", "to_policy": "PERIODIC_FULL", "metric": "clean_pp", "test": "equivalent", "margin": 3.0}
  ],
  "estimands": [
    {"label": "E-PM firmware W = 6: EVICON -> PERIODIC_FULL (within run)", "run": "PMz6", "contract": "ZEPHYR_CUTOFF", "from_policy": "EVICON", "to_policy": "PERIODIC_FULL", "metrics": ["confirmed_pp", "admitted_pp", "queries", "fences", "mean_time_to_terminal_s", "unresolved_at_end"]},
    {"label": "E-PM firmware W unbounded: EVICON -> PERIODIC_FULL (within run)", "run": "PMzi", "contract": "ZEPHYR_CUTOFF", "from_policy": "EVICON", "to_policy": "PERIODIC_FULL", "metrics": ["confirmed_pp", "admitted_pp", "queries", "fences", "mean_time_to_terminal_s", "unresolved_at_end"]},
    {"label": "E-PM MinIO W = 6: EVICON -> PERIODIC_FULL (within run)", "run": "PMm6", "contract": "MINIO_COND", "from_policy": "EVICON", "to_policy": "PERIODIC_FULL", "metrics": ["confirmed_pp", "admitted_pp", "queries", "stops", "mean_time_to_terminal_s", "unresolved_at_end"]},
    {"label": "E-PM MinIO W unbounded: EVICON -> PERIODIC_FULL (within run)", "run": "PMmi", "contract": "MINIO_COND", "from_policy": "EVICON", "to_policy": "PERIODIC_FULL", "metrics": ["confirmed_pp", "admitted_pp", "queries", "stops", "mean_time_to_terminal_s", "unresolved_at_end"]},
    {"label": "E-PM-R firmware W = 6: the 084 cross-run comparison (C2 EVICON -> PFz6 PERIODIC_FULL)", "run": "PFz6", "from_run": "C2", "contract": "ZEPHYR_CUTOFF", "from_policy": "EVICON", "to_policy": "PERIODIC_FULL", "metrics": ["clean_pp", "confirmed_pp", "queries", "fences"]},
    {"label": "E-PM-R firmware W unbounded: the 084 cross-run comparison (Rz EVICON -> PFzi PERIODIC_FULL)", "run": "PFzi", "from_run": "Rz", "contract": "ZEPHYR_CUTOFF", "from_policy": "EVICON", "to_policy": "PERIODIC_FULL", "metrics": ["clean_pp", "confirmed_pp", "queries", "fences"]},
    {"label": "E-PM-R MinIO W = 6: the 084 cross-run comparison (C EVICON -> PFm6 PERIODIC_FULL)", "run": "PFm6", "from_run": "C", "contract": "MINIO_COND", "from_policy": "EVICON", "to_policy": "PERIODIC_FULL", "metrics": ["clean_pp", "confirmed_pp", "queries", "stops"]},
    {"label": "E-PM-R MinIO W unbounded: the 084 cross-run comparison (Rm EVICON -> PFmi PERIODIC_FULL)", "run": "PFmi", "from_run": "Rm", "contract": "MINIO_COND", "from_policy": "EVICON", "to_policy": "PERIODIC_FULL", "metrics": ["clean_pp", "confirmed_pp", "queries", "stops"]}
  ]
}
```

```policy-compare
{
  "name": "Release 085: PERIODIC_FULL against EviCon in the same campaigns (PREREG_extra_085.md, E-P)",
  "runs": {
    "PMz6": {"campaign": "expPM-zephyr-w6"},
    "PMzi": {"campaign": "expPM-zephyr-winf"},
    "PMm6": {"campaign": "expPM-minio-w6"},
    "PMmi": {"campaign": "expPM-minio-winf"}
  },
  "comparisons": [
    {"id": "PM-E1", "a": {"run": "PMz6", "contract": "ZEPHYR_CUTOFF", "policy": "EVICON"}, "b": {"run": "PMz6", "contract": "ZEPHYR_CUTOFF", "policy": "PERIODIC_FULL"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "PM-E2", "a": {"run": "PMzi", "contract": "ZEPHYR_CUTOFF", "policy": "EVICON"}, "b": {"run": "PMzi", "contract": "ZEPHYR_CUTOFF", "policy": "PERIODIC_FULL"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "PM-E3", "a": {"run": "PMm6", "contract": "MINIO_COND", "policy": "EVICON"}, "b": {"run": "PMm6", "contract": "MINIO_COND", "policy": "PERIODIC_FULL"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "PM-E4", "a": {"run": "PMmi", "contract": "MINIO_COND", "policy": "EVICON"}, "b": {"run": "PMmi", "contract": "MINIO_COND", "policy": "PERIODIC_FULL"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]}
  ]
}
```

## 9. Pre-release checks (before freezing; not results)

Checks of the code path before freezing, on one machine and on a simulated testbed. They are not results of this plan:
the regime, the scale, the timing and (for the checks) the platform differ from the testbed runs.

**Unit tests.** Go: every package passes (`go test ./...`, `go vet`, `gofmt`), among them the tests of the transport
expiry (M3b): `TestDispatchExpiry085` (the dispatcher's expiry: `-expires-after` when given, else the contract's T, else
none), `TestExpiresAfterDispatchIdentical085` (the 084 ThingsBoard configuration {Q_A, T(60 s)} with r = 0 and the
strict contract {Q_A} with `-expires-after 60s`: the same journal gives byte-identical dispatch requests,
`expires_at_ms` = creation + 60 s) and `TestTTLChangesNoDecision085` (their compiled allowed actions are equal for
20,000 random command timings, seed 85: T with r = 0 changes no decision). Python: 209 analysis tests (among them
`test_release085` K1-K18 and `test_prereg` on every plan, `PREREG_review_077.md` still `db67640d...`,
`PREREG_extra_083.md` still `6e3f24a9...`, `PREREG_extra_084.md` still `b1135164...`), 86 conformance tests (0 skipped,
Mosquitto present; among them the six tests of the check device and the rules: the default modes as the frozen device, a
held handler with the acknowledgement after the plant write, the acknowledgement before it, the hold timeout, the admin
endpoints, and `tb_rpc_checks.rules` on synthetic trials of every outcome: MET, NOT MET, NOT REACHED, ABSENT, DETECTED,
NOT INTERPRETABLE), and the firmware host tests. The fence pause (section 1(d)): a synthetic trace with an anchor, an
abort, a per-identity intake and an open pause gives the expected pauses (K3); on the 084 traces the corrected values
are those of section 1(d).

**TLA+** (review M1). `formal/EviconTiming.tla` with TLC 1.7.4, the expected result of every configuration written in
`formal/run_tlc_timing.sh` before the first run (`formal/TLC_RESULTS_TIMING.txt`, logs in `formal/tlc_logs_timing/`):

| Configuration | Expected | Result |
|---|---|---|
| `firstsend_after_expiry`: first send after t0 + e + eps | `NoEffect` holds | no error (35,355 states generated, 13,638 distinct, depth 17) |
| `firstsend_before_expiry_witness`: first send by t0 + e, every copy may be lost | `NoHiddenEffect` violated: an effect the client gets no answer for, the indistinguishable execution of Theorem 2(b) | violated (3-state trace) |
| `firstsend_boundary_witness`: first send at exactly t0 + e + eps | violated (still a witness) | violated (7-state trace) |
| `original_after_validity`: the dispatcher's rule (attempt 0 always allowed, the validity cut for resends only) | `NoLateEffect` violated: the boundary of Proposition 1(ii) | violated (8-state trace) |
| `reissue_after_validity`: a re-issue created in time, queued, dispatched after V | `NoLateEffect` violated | violated (12-state trace) |
| `validity_every_attempt`: V checked for every attempt, the original and the queued re-issue included | `NoLateEffect` and `SuppressedNoEffect` hold | no error (10,865 / 7,804, depth 18) |

Bounded model checking of an abstraction (E = 3, eps = 1, D = 2; V = 4, D = 1), not a proof about the Go code; release
085 does not change the dispatcher.

**Analysis spec.** Synthetic runs made of renamed cells of the 084 final report (PMz6: C2's ZEPHYR_CUTOFF/EVICON cells
as EVICON and PFz6's cells as PERIODIC_FULL, episodes 0-7, the configs' schedule files and the alternating order; PMzi,
PMm6, PMmi likewise), with the reference runs: `pairs_ext083.py` with this plan decided all 13 rules of its blocks (PM-1
to PM-4 MET: +0.04, +0.09, +0.10, -0.04 pp, n = 8; PM-A1 to PM-A8 ABSENT; PM-S MET) and paired every estimand;
`policy_compare.py` paired PM-E1 to PM-E4; `pm085.py` reproduced the 084 break-even ratios of section 1(a) (1.81, 1.47,
1.79, 3.03) and the order of the cells. The earlier plans' verdicts: the release-085 claims check run on a copy of the
084 final report gives the 084 verdicts of all 150 earlier hypotheses and the same six cross-cutting claims
(`verdicts085.py`: identical).

**Simulated testbed** (the driver end to end on one machine: sshd on five loopback addresses as VM-A to VM-E, Docker as
VM-B with Mosquitto, the MinIO stand-in image and ThingsBoard CE 4.3.1.6 as a labelled stand-in image; the firmware
build placed as VM-D's build output; a copy of this tree with this plan frozen in the copy; expPM_zephyr_w6 as a copy of 2
episodes, the other three configs of 1 episode, 180 s + 30 s tail; the operator script with the copy's paths and
`--archive-missing-ok`; an empty directory as the 078 run; the 083 run's measured thingsboard-rpc-r0 contract file as the
083 run; the 084 final report's REPORT.md and claims.json as the 084 run and as its archive copy):

| Stage | Result |
|---|---|
| refusal: the script started while another `evicon_review.py` ran | `REFUSED: another evicon_review.py is running (pid ...)`, exit 1, nothing run |
| piece 1: `run085.sh --until <25 min later>`: `preflight build verify distribute --new --quick` | PASS (verify: `go test`, 86 conformance tests with 0 skipped, the firmware host tests, 207 analysis tests, 2 skipped on the shortened copy); then `ENDED ...: the next step, measure zephyr-plain zephyr (about 50 min), would not end by ...`, nothing else started |
| piece 2 (the same command without `--until`): the stages (PASS on the same source), `distribute --force` | PASS |
| `measure zephyr-plain zephyr` | K0; {D_h, Q_A, F} (K3), fault model "owner up" |
| `tbcheck --from <the 083 stand-in>` | PASS: ThingsBoard 4.3.1.6 (stand-in): TB-1 MET, TB-2 ABSENT, TB-3a ABSENT, TB-3b ABSENT, TB-4 MET; 140 trials, none invalid, every RPC deleted, the device back in its default modes |
| `campaign expPM_zephyr_w6`, `_winf` | PASS: 4 + 2 cells VALID; the binding check ("every contract with binding auto gets the QoS the rule allows"); activation gate PASS; order ep0 EVICON, PERIODIC_FULL and ep1 PERIODIC_FULL, EVICON (manifest and done.jsonl) |
| `campaign expPM_minio_w6`, `_winf` | PASS: 2 + 2 cells VALID |
| `report --archive-missing-ok --archive <archive> --from <078> --from <083> --from <084>` | PASS; the earlier verdicts compared with the 084 final report's claims: DIFFER (the stand-in sources hold none of the earlier plans' runs: 150 missing), logged as a FAIL and printed `REPORT DONE WITH A DIFFERENCE` |
| after the changes below: `preflight build verify distribute --force --quick`, `tbcheck --force`, `report`, two `bundle --incremental` in one minute | PASS (verify: 86 conformance tests, 0 skipped; 209 analysis tests, 2 skipped on the shortened copy); `tbcheck`: TB-1 MET, TB-2 ABSENT, TB-3a ABSENT, TB-3b ABSENT, TB-4 MET, clock offset 0.054 ms +- 0.235 ms by HTTP (after the trials 0.107 ms; the driver's ssh estimate 109.7 ms recorded, not used), TB-1's smallest margin 23.6 ms, 140 trials, none invalid; `report`: PASS with `report/PROVENANCE.md` (the four campaigns of this plan with their review run and the plan's SHA-256 from their run configs; in the copy the plan's SHA-256 differs from the configs', since the copy's plan was frozen again after its cells, which the table flags); the two bundles: `..._1648.zip` and `..._1648_2.zip` |

| Run | Policy | Cells | Clean | Queries | Fences / stops | Safety counts, late effects |
|---|---|---|---|---|---|---|
| PMz6 | EVICON / PERIODIC_FULL | 2 / 2 | 100 / 100 % | 225 / 75 | fences 23 / 139 | 0, 0 |
| PMzi | EVICON / PERIODIC_FULL | 1 / 1 | 100 / 100 % | 165 / 34 | fences 8 / 105 | 0, 0 |
| PMm6 | EVICON / PERIODIC_FULL | 1 / 1 | 99.2 / 99.2 % | 101 / 59 | stops 25 / 48 | 0, 0 |
| PMmi | EVICON / PERIODIC_FULL | 1 / 1 | 99.2 / 99.2 % | 113 / 56 | stops 24 / 49 | 0, 0 |

`report` gave `pairs_ext083.py` the four runs: PM-A1 to PM-A8 ABSENT, PM-S MET, PM-1 MET on the two episodes of PMz6
(both differences 0, a degenerate interval), PM-2 to PM-4 NOT COMPUTABLE (one episode); `policy_compare.py` PM-E1 to PM-E4; `pm085.py` the owner
calls, the break-even of PMz6 (1.29) and the same-contract check (both policies of PMz6 and PMzi: the contract file of this
run's `measure zephyr`, binding auto, QoS 1); the TB checks (marked stand-in), the report analyses, the paper tables
(`tab_extra085.tex`, `tab_pm085.tex`, `tab_tbcheck.tex`, `tab_l2_window_ci.tex`, `tab_l2_offline_bound.tex`) and the claims
check (the 18 rules of this plan).

**Changes made before freezing** (from these checks): (i) the ThingsBoard checks measure the clock offset VM-E - VM-C
themselves over HTTP to the check device (`GET /_test/clock` on VM-E, the plant's host; the shortest of 20 round trips;
u = half that round trip; before and after the trials) and TB-1 counts SUCCESSFUL as after the effect only beyond u: the
driver's ssh-based offset was +119.5 ms between two loopback addresses of one host (true offset 0), biased by about half
the ssh round trip, which would have moved every plant effect about 120 ms earlier (section 2 and the TB-1 row); (ii) a
second incremental bundle in the same minute replaced the first, whose files the bundle index already counted as sent
(the bundle after the campaigns and the one after the report): it is now named `_2`, `_3`, ...; the operator script and
the run guide ask for every bundle not sent yet; (iii) `run085.sh` ran `tbcheck` only once on PARTIAL (the driver exits
0 for PARTIAL): it now runs it once more, and the second run decides (found with a fake driver that exercised the
script's control flow: PASS, PARTIAL once and twice, an unplanned safety violation, a second start after the end); (iv)
earlier in the build: the check device logged acknowledgements without the command; the gateway's contract file is kept
outside the directory the driver empties; `pm085.py` reports the same-contract check as not available, not NO, when a
run has no gateway logs; (v) `PROVENANCE.md` and `report/PROVENANCE.md` (`scripts/provenance.py`).

## Deviations

None so far.

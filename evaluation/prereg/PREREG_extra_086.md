# Preregistration: the experiments of release 086 (the second-seed replication of the four real-owner headline contrasts; the documented shadow reconciliation baseline; the MQTT QoS 2 row; costs in physical units)

Release 086. Frozen before any of its cells runs on the testbed. The six campaign configs below carry the SHA-256 of
this file, and `evicon_review.py campaign` refuses to run them if this file, or any file listed under *Frozen files*,
has changed. Changes after freezing are recorded under *Deviations*; nothing above that section is edited.

## 1. Background (exploratory evidence, not part of the test)

The independent checks of the manuscript of 5 November 2026 (verifier B4, review comment M4; verifier B3, warnings W3 and
W4) found that the paper's headline real-owner effects each rest on eight episodes of one seed and one regime, and that
the firmware headline pairs runs of different releases. The author approved (5 November) a replication of the four
contrasts on a new seed, each within one campaign, and (verifier B2, finding B2-02) a safety fix of the authority's
fence anchor, which this plan does not test with cells (section 9 and the report analysis of section 4).

**Addendum (approved by the author on 5 November, 07:35; reviewer comment M3 and question Q6 of
`review/rev3/VERIFY_B4_reviewer.md`).** Three more items in the same release, plan and run: (A) a cost table in
physical units for the matched 085 runs and the 086 runs (estimand E-COST, no new cells; section 4); (B) a contract-map
row for MQTT QoS 2 measured with the probes of the existing MQTT rows and a directed expiry check (`measure
mosquitto-qos2`; section 3, QB); (C) a practice baseline that reproduces the documented desired/reported
reconciliation of a device shadow (policy `SHADOW_RECONCILE`, runs SHq and SHd; section 3, rules SH). The author asked
for one continuous overnight session.

The four contrasts (clean fulfilment, pp of offered, EVICON; the 085 final report, claims/CLAIMS.md):

| Contrast | Plan, ID | Runs (seed) | Estimate [95 % t] | Within run? |
|---|---|---|---|---|
| MinIO conditional PUT with stop vs plain PUT, W = 6 | 073 C3 | C `expC-minio/20261023T083658` (20261025) | +53.40 [+43.45, +63.34] | yes |
| the same, W unbounded | 077 U-1 | Rm `expR-minio-winf/20261028T145228` (20261025) | +16.50 [+11.83, +21.16] | yes |
| firmware {F} at QoS 1 vs plain firmware at QoS 0, direct channel, W = 6 | 083 T4-3 | Z6 `expT4-zephyr-w6/20261031T113212` -> C3 `expC3-ablation/20261024T161324` (20261017) | +53.65 [+45.71, +61.58] | no (releases 083 and 075) |
| the same, W unbounded | 083 T4-4 | Zi `expT4-zephyr-winf/20261031T132834` -> Rz `expR-zephyr-winf/20261028T084744` (20261017) | +18.59 [+12.23, +24.95] | no (releases 083 and 078) |

The within-run firmware contrasts against the plain firmware at QoS 1 (077 U-2 in Rz, +18.57; 075 C3-1 in C3, +53.73)
give the same sizes. Per-episode spread of the original contrasts (`sim_predictions/extra086_templates.csv`): SD 11.89
(C3), 5.58 (U-1), 9.49 (T4-3), 7.61 (T4-4) pp; within the original firmware runs 9.51 (C3) and 7.60 (Rz).

**Prediction.** A replication's prediction is the original estimate: the new campaigns change only the seed (and, for
the firmware, put both arms into one campaign). No simulator run: the simulator does not model the firmware's delivery
binding (QoS 0 against QoS 1) or MinIO's gateway, and the effect sizes come from the testbed; the power analysis
(section 5) uses the original runs' own spread.

## 2. Design

Common regime: that of the original runs (4 devices; 300 s per cell + 60 s tail; 0.5 commands/s per device; values
1-5; bound 345 per device; deadline 60 s; validity 90 s; fault windows 2/min, 1-4 s; offline 10-40 s; mix offline 0.3,
drop_up 0.2, drop_down 0.2, delay_up 0.1 (6 s), crash 0.2; authority: no assumed bound on the network delay,
SKI_RENTAL resolution, query first, costs hold 1, stop 1, fence 5, probe budget 4, the T4 retry guard on). Each config
is a copy of the original configuration of its owner and window and differs from it only in the name, the seed, the
policies (EVICON only), the contracts (firmware: the two arms of the contrast) and the plan (test K10 of
`test_release086`).

**The new seed: 20261105** (no earlier config uses it). `evaluation/sr086_schedules.py` replays every campaign config of
the release trees 062-086 through the frozen `campaign.schedule` (687 earlier configs, among them the two shadow configs of this plan, whose schedules are those of RR and RA by design; 31 distinct schedule sets of
seeds 7, 20261013, 20261014, 20261017, 20261018, 20261020, 20261024, 20261025, 20261026, 20261101, 20261102; 276 earlier
episode schedules): none of the eight SR episodes has an arrival sequence or a fault schedule equal to an earlier one,
and the eight SR fault schedules are distinct (`sim_predictions/extra086_schedules.csv`). The four SR configs share one
schedule set (the same seed and regime), as the original MinIO and firmware runs shared theirs within each owner.

| Run | Config | Runner | Owner, channel | Contracts (both in the campaign) | Policy | W | Seed | Episodes | Cells | Hours |
|---|---|---|---|---|---|---|---|---|---|---|
| **SRm6** | `expSR_minio_w6.json` | `campaign_real.py` | MinIO behind the S3 gateway, direct | MINIO_PLAIN, MINIO_COND (the 070 measured contracts) | EVICON | 6 | 20261105 | 8 | 16 | 1.95 |
| **SRmi** | `expSR_minio_winf.json` | `campaign_real.py` | as SRm6 | as SRm6 | EVICON | 1000 | 20261105 | 8 | 16 | 1.95 |
| **SRz6** | `expSR_zephyr_w6.json` | `campaign_zephyr_ext086.py` (on `campaign_zephyr_ext077.py`) | the firmware behind Mosquitto, direct | ZEPHYR_PLAIN_DIRECT_QOS0 (plain, binding auto = QoS 0), ZEPHYR_F_DIRECT (cutoff restricted to {F}, binding qos1) | EVICON | 6 | 20261105 | 8 | 16 | 2.0 |
| **SRzi** | `expSR_zephyr_winf.json` | `campaign_zephyr_ext086.py` (on `campaign_zephyr_ext077.py`) | as SRz6 | as SRz6 | EVICON | 1000 | 20261105 | 8 | 16 | 2.0 |

Total of the replication: 4 campaigns, 64 cells (7.3 min per cell, as the 085 matched runs: 1.92-1.94 h per 16 cells),
about 7.8 hours of cells. The firmware runs SRz6 and SRzi run on `evaluation/campaign_zephyr_ext086.py`: the 077 runner
unchanged, with two counters for E-COST that change nothing a cell does (after each cell's processes stop it waits 11 s
and reads the broker's `$SYS` packet counters; before the frozen runner deletes the flash files it counts the firmware's
flash writes on VM-E with `evaluation/nvs_flash.py`); about 3 min more per firmware campaign.

**The documented shadow reconciliation baseline (Addendum C).** `SHADOW_RECONCILE` (`internal/runtime/shadow_reconcile.go`,
off unless a campaign names it) follows the loop the two pages the paper cites document. AWS IoT Device Shadow: "an app
can request a change in a device's state by updating a shadow. AWS IoT publishes a message that indicates the change to
the device. The device receives this message, updates its state to match, and publishes a message with its updated
state"; "When the device reconnects, it receives the current state of its shadows so that it can update its state to
match that of its shadows"; for an app, "you might have to poll the shadow by making HTTP GET requests until the device
has updated the shadow with its new state"; "a device can safely discard any messages that contain a version number older
than the one it is tracking". Azure IoT Hub device twins: "Back-end apps can set desired properties, and the device app
can read them"; "The device app then reports the updated configuration (or an error condition using the status
property)"; "A back-end app tracks the results of the configuration operation across many devices by querying device
twins"; a reconnecting device "must retrieve the full desired properties document" and "can ignore all notifications with
$version less or equal than the version of the full retrieved document". Neither page documents a timeout after which the
back end gives up, nor a release: the desired state stays until the device reports it. So the baseline publishes each
admitted command (the desired state) with its version (identity and sequence); at the reconciliation timeout
`release_after` = 10 s after the command's creation (the timeout of the state-read release baselines, so that the
schedules and timing are theirs) and every 10 s after that it reads the reported state of every unresolved command (the
owner's receipt read, at most 16 per sweep, oldest first); a reported APPLIED or STOPPED is recorded; a command the
reported state does not show (NOT_FOUND, or the read is not answered) is published again under its own identity and
version, also after the campaign's resend window (the shadow has no validity); an UNCERTAIN report is read again at the
next timeout; it never commits STOPPED without the owner's report and sends no query, stop or fence on a guard block (the
documented loop has no resolver). The admission guard is EviCon's, as for every baseline. A re-publish is a resend of the
same identity: safe on an owner that deduplicates every identity without a horizon, unsafe on one that does not (the Go
test `TestShadowReconcileK0Unsafe086` shows the duplicate on K0). The baseline therefore runs on K2 only, the contract of
the state-read release baselines, and is not in the unsafe-by-design lists.

| Run | Config | Runner | Owner, channel | Contract | Policies | W | Seed | Episodes | Cells | Hours |
|---|---|---|---|---|---|---|---|---|---|---|
| **SHq** | `expSH_queue.json` | `campaign_ext086.py` | evicon-device, queue, the device answering reads of its reported state also while offline (`-shadow-reads`, a shadow service) | K2 | EVICON, SHADOW_RECONCILE | 6 | 20261017 | 5 | 10 | 1.2 |
| **SHd** | `expSH_direct.json` | `campaign_ext086.py` | evicon-device, direct (`-offline-mode drop`), crash orphans | K2 | EVICON, SHADOW_RECONCILE | 6 | 20261017 | 5 | 10 | 1.2 |

SHq is a copy of `expRRA_queue.json` (run RAq of plan 083) and SHd of `expRR_direct.json` (run RRd of plan 077), except
the name, the policies, the plan and (SHq) no `release_settle`: the same seed, regime, channel and 5 episodes, so that
episodes 0-4 replay the schedules of RRq, RRd, RAq and RAd (byte-identical schedule files; the comparisons with those
runs pair episodes by them). Reference runs: RRq `expRR-queue/20261029T081859`, RRd `expRR-direct/20261030T114515` (078
run), RAq `expRRA-queue/20261031T210814`, RAd `expRRA-direct/20261031T222014` (083 run).

**The MQTT QoS 2 row (Addendum B).** `python3 evicon_review.py measure mosquitto-qos2`: Mosquitto 2.0.20 on VM-B (EviCon's
compose project, as `measure mosquitto`), the reference MQTT 5 device on VM-E at QoS 2
(`conformance/reference/mqtt_device_qos2.py`: the frozen device with its command and query subscriptions and its replies
at QoS 2, persistent session), the probe on VM-C publishing its requests and subscribing its replies at QoS 2
(`conformance/evicon_probe/adapters/mqtt5_qos2.py`), config `conformance/configs/mosquitto-rpc-qos2.json`: the probes,
parameters, repetitions and fault proxy of the QoS 1 row `mosquitto-rpc.json` (P4, P2, P1, P3, P6, P9T, P7, P2N, P5N,
P9N; 3 repetitions); then, with the stack still up, the directed expiry check `conformance/reference/qos2_expiry_check.py`
(the same broker, device, plant, adapter and fault proxy): the request path delayed by a constant d for the whole trial
(every byte, so also the publisher's PUBREL), at d = 0.75 x the expiry (22.5 s) and at P9T's own delay (31.5 s), 3 trials
each, recording when the effect appears in seconds after the client's send (the plant's truth). Why: P9T delays every
request-path byte by more than the expiry and labels T when the request never takes effect; at QoS 2 that delay also holds
the PUBREL, so P9T alone cannot tell whether the expiry bounds the device's execution after the client's send (section 9).
About 30 minutes in all. The firmware is not measured at QoS 2: the measured build subscribes its topics with
a maximum QoS of 1 (`MQTT_QOS_1_AT_LEAST_ONCE` in `firmware/zephyr-cutoff/src/main.c`, so the broker delivers a QoS 2
PUBLISH to it at QoS 1), publishes its replies at QoS 1 and acknowledges only QoS 1 messages (it has no PUBREC/PUBREL/
PUBCOMP handling); a QoS 2 firmware would be a different, unmeasured build.

Reference runs of the replication (no new cells; the earliest complete run of each config, read from the archive, the 078
run and the 083 run): C `expC-minio/20261023T083658`, Rm `expR-minio-winf/20261028T145228`, C3 `expC3-ablation/20261024T161324`, Rz
`expR-zephyr-winf/20261028T084744`, Z6 `expT4-zephyr-w6/20261031T113212`, Zi `expT4-zephyr-winf/20261031T132834` (the 083 review run).

- **Within run.** SR-1 to SR-4 are within-run contrasts: both contracts on the same build (one review run of release
  086), the same day, owner configuration and schedule per episode; the firmware arms run on the contracts of `measure
  zephyr-plain zephyr` in the same review run (plain: K0; cutoff: {D_h, Q_A, F}, restricted to {F}), the plain one at
  QoS 0 by the delivery binding (auto; the driver's binding check), the {F} one at QoS 1 (qos1, the original runs' call
  shape). The cell order within an episode is the config's (plain first), as in the original runs.
- **Release 086's authority.** Every cell runs the release-086 authority, whose fence anchor is computed from evidence
  (section 9); the {F} arm fences, the plain arms and MinIO do not. Section 4 (E-A) states for every run whether the
  owner's reported state ever differed from the evidence; with no difference the anchor rule changes nothing in a cell.
- **Truth**: `evicon-plant` on VM-E (MinIO: the object versions, as in C and Rm).
- **Pairing**: within a run, the two contracts of an episode. Across seeds nothing is paired (the schedules differ):
  the replication's estimate and the original's are compared unpaired (section 4).
- **Analysis**: `evaluation/pairs_report.py --plan evaluation/prereg/PREREG_extra_086.md --run <key>=<run> ...` (frozen
  since plan 072) runs the ```pairs-spec``` block (Holm within each family); `evaluation/sr086.py` the estimands E-SR;
  `evaluation/anchor086.py` the report analysis E-A; `evaluation/policy_compare.py` (frozen since 077) the
  ```policy-compare``` block (E-SH); `evaluation/cost086.py` the estimand E-COST. `evicon_review.py report` runs all of
  them with the earliest complete run of each campaign among its sources.

## 3. Hypotheses and decision rules

Tests on per-episode paired differences (to minus from), clean fulfilment in pp of offered, Student t, alpha = 0.05;
Holm within each family. Absence and safety rules have no p-value and are in no family.

**The margin rule (fixed now, before the data).** A replication meets if its effect is at least **half the original
estimate**: `at_least m` with m = 0.5 x the original estimate of the 085 final report, rounded up to 0.01 pp (SR-1:
26.70, SR-2: 8.25, SR-3: 26.83, SR-4: 9.30). Why this rule and not "at least the original's lower 95 % bound" (43.45,
11.83, 45.71, 12.23): the headline claims a large effect, not its exact size, and a replication that is at least half as
large confirms "a large effect on a new seed"; the stricter rule demands that a second sample of 8 episodes lie above the
first sample's lower bound, which at the original effect and spread happens only with probability 0.63-0.67 (section
5): it fails the 0.80 power rule and would report a successful replication as NOT MET about one time in three. The
replication's estimate and interval are reported next to the original's whatever the verdict (section 4).

**The 0.80 rule** (as in plans 083 to 085). A paired-difference rule is a confirmatory hypothesis only if its power
under the testbed's own spread, at the predicted mean and with Holm in its family, is at least 0.80; otherwise an
estimand. Applied before freezing: SR-1 to SR-4 have power 0.99-1.00 (section 5); all four are hypotheses.

The five safety counts are bound violations, duplicate effects, logical duplicates, false STOPPED and false APPLIED, as
in every plan.

| ID | Family | Hypothesis | Decision rule |
|---|---|---|---|
| **SR-1** | sr-minio | On MinIO at W = 6, on a new seed, the conditional PUT with its stop raises clean fulfilment over the plain PUT by at least half the original estimate (073 C3: +53.40) | `at_least 26.70`, run SRm6, EVICON, MINIO_PLAIN -> MINIO_COND, `clean_pp` |
| **SR-2** | sr-minio | ... at W unbounded (077 U-1: +16.50) | `at_least 8.25`, run SRmi, EVICON, MINIO_PLAIN -> MINIO_COND, `clean_pp` |
| **SR-3** | sr-fw | On the firmware at W = 6, on a new seed and within one campaign, the fence ({F}, QoS 1) raises clean fulfilment over the plain firmware under the delivery binding (QoS 0), direct channel, by at least half the original estimate (083 T4-3: +53.65) | `at_least 26.83`, run SRz6, EVICON, ZEPHYR_PLAIN_DIRECT_QOS0 -> ZEPHYR_F_DIRECT, `clean_pp` |
| **SR-4** | sr-fw | ... at W unbounded (083 T4-4: +18.59) | `at_least 9.30`, run SRzi, EVICON, ZEPHYR_PLAIN_DIRECT_QOS0 -> ZEPHYR_F_DIRECT, `clean_pp` |
| **SR-A1 to SR-A8** | - | Every arm is safe | `absent`, the five safety counts, EVICON: SRm6 MINIO_PLAIN, SRm6 MINIO_COND, SRmi MINIO_PLAIN, SRmi MINIO_COND, SRz6 ZEPHYR_PLAIN_DIRECT_QOS0, SRz6 ZEPHYR_F_DIRECT, SRzi ZEPHYR_PLAIN_DIRECT_QOS0, SRzi ZEPHYR_F_DIRECT |
| **SR-S** | - | Safety in every cell of the four runs; the plain arms (plain PUT, the plain firmware at QoS 0) are guarded cells and must be safe (no exempt contract) | `safety` (pairs_report): the five counts 0 in every VALID cell of SRm6, SRmi, SRz6, SRzi; late effects reported separately |
| **SH-A1** | - | The documented shadow reconciliation loop is safe on K2 on the queue channel with a shadow service | `absent`, the five safety counts, run SHq, K2, SHADOW_RECONCILE |
| **SH-A2** | - | EVICON is safe in the same run | `absent`, the five safety counts, run SHq, K2, EVICON |
| **SH-A3** | - | The documented shadow reconciliation loop is safe on K2 on the direct channel with crash orphans | `absent`, the five safety counts, run SHd, K2, SHADOW_RECONCILE |
| **SH-A4** | - | EVICON is safe in the same run | `absent`, the five safety counts, run SHd, K2, EVICON |

In all, 17 rules: 4 paired-difference hypotheses (two families), 12 absence rules, 1 safety rule. The QoS 2 measurement
(QB) is a measurement with an expectation written below, not a rule.

Interpretation fixed in advance:
- **SR-1 to SR-4 MET**: the headline contrast holds on a second seed (and, for the firmware, within one campaign of one
  release): the effect is at least half the original one on a new fault and arrival schedule set. The paper cites the
  two seeds together (the replication's estimate and interval next to the original's, section 4) and may state the
  headline as replicated on two seeds of one regime.
- **NOT MET** (the Holm-adjusted p is not below 0.05: the effect may be smaller than half the original): the paper
  reports the replication next to the original in the abstract's and highlights' context and qualifies the headline
  (the magnitude is not established across seeds; the original number is a single-seed estimate). If the replication's
  95 % interval lies wholly below the margin, the paper says that the effect on the second seed is smaller than half the
  original; if it includes the margin, that the replication is inconclusive at n = 8.
- **NOT INTERPRETABLE** (more than 10 % INVALID cells after the re-run): reported as such; the headline is qualified as
  for NOT MET.
- **SR-A1 to SR-A8 PRESENT or SR-S NOT MET**: a safety failure; reported with the cells and traces, the plan of record
  stops until the cause is known (the operator script stops the session).
- **SH-A1 and SH-A3 ABSENT** (predicted, by construction: the loop never commits STOPPED without the owner's report, and
  a re-publish is the same identity, which K2 deduplicates without a horizon): the documented reconciliation loop is safe
  on an owner that deduplicates every identity and answers a reported-state read; what separates it from the state-read
  release baselines (RR, RA: violations DETECTED on the queue channel, RR also on the direct one) is that it never releases.
  Its price is reported as estimands (E-SH: clean fulfilment, unresolved commands at the end, late effects after the
  intent's validity, re-publishes), next to EVICON in the same run and RR and RA on the same schedules. **DETECTED**: the
  documented loop violated safety on K2; the cells, the traces and the violating identities are reported, the paper
  reports the baseline as unsafe there next to RR and RA, and the session stops (as for any unplanned violation).
- **SH-A2 or SH-A4 PRESENT**: a safety failure of EVICON, as SR-A.
- **QB (the QoS 2 row).** Expectation, written before the measurement and after the simulated regression (section 9):
  class K0 as the QoS 1 row: an application resend of the same identity is a new PUBLISH with a new packet identifier,
  which QoS 2 does not deduplicate (P4 duplicates: D_h CONTRADICTED), a delivered request without a reply leaves no
  receipt to query (P2, P2N: Q_A ABSENT); P9T labels T OBSERVED (the request it delays by 31.5 s never takes effect),
  and the directed expiry check shows that the label does not mean that the expiry bounds the device's execution: at
  d = 22.5 s every request takes effect after the expiry counted from the client's send (at about 2d), at d = 31.5 s none
  takes effect. Interpretation of every outcome: **a late effect at d = 22.5 s** (as expected, whatever P9T labels) ->
  QoS 2 certifies nothing after a client-visible timeout beyond the QoS 1 row: its exactly-once exchange holds per
  PUBLISH and hop within a session, not per application identity, and the message expiry does not bound when the device
  executes, counted from the client's send (a QoS 2 request took effect after its expiry); a T label of the row is
  reported only together with the check, and the paper adds the row with the check, answers Q6 with them and does not
  cite T for QoS 2 as a capability. **No late effect at d = 22.5 s** (with or without effects at 31.5 s) -> in the
  measured cases the expiry bounded execution after the client's send: the row is reported with T and the check's trials, the paper states exactly what was measured (two
  delays, 3 trials each), and whether the binding rule of section 3.7 should use it is left to the author. **Any other
  capability measured** (for example D_h with a horizon, or Q_A) -> the row is reported as measured with its rubric
  labels and probes, the paper states what QoS 2 certifies on this broker and device, and whether the binding rule of
  section 3.7 should use it is left to the author (the plan's runs use QoS 0 or 1 only). An invalid measurement (the
  rubric's INVALID, or the check's error) is run once more; the second run decides.

## 4. Estimands (reported with intervals, no claims)

| ID | Estimand |
|---|---|
| E-SR | Per contrast (`sr086.py`): the replication's effect (within run, mean of the per-episode differences, Student t 95 % interval and a paired bootstrap percentile interval, 10,000 resamples, seed 86) next to the original's (MinIO within run; firmware across runs on byte-identical schedules, and the within-run original against the plain firmware at QoS 1); the difference between the seeds (replication minus original: Welch t 95 % interval on the two sets of per-episode differences and a two-sample bootstrap, 10,000, seed 86; nothing is paired across seeds); the replication's estimate as a share of the original's; the levels of both arms in every run |
| E-SR-L | The levels and owner calls of the SR runs (pairs-spec estimands within run: confirmed and admitted fulfilment, queries, fences, stops, time to a terminal outcome, unresolved at the end, window defers) |
| E-SH | The documented shadow loop (`policy_compare.py`, block ```policy-compare```, comparisons SH-E1 to SH-E6): within SHq and SHd, EVICON -> SHADOW_RECONCILE; across runs on the same schedules, RRq RELEASE_ON_READ -> SHq SHADOW_RECONCILE, RAq RELEASE_ON_READ_ANSWERED -> SHq SHADOW_RECONCILE, RRd -> SHd, RAd -> SHd: clean and admitted fulfilment, violations and their components, late effects, unresolved at the end, mean per-episode differences with a Student t interval; and from the traces, the reads by their outcome and the re-publishes (`SHADOW_RECONCILE_RESULT`) |
| E-COST | Costs in physical units (`cost086.py`, Addendum A; reviewer comment M3(1)): for the matched 085 runs (PMz6, PMzi; PMm6, PMmi for owner calls and paused time) and the 086 runs (SRz6, SRzi, SRm6, SRmi, SHq, SHd), per arm and offered request: logical owner calls (in SHq and SHd including the shadow loop's reads of the reported state, which the row's queries do not count, and its re-publishes shown apart); MQTT PUBLISH messages from the gateway to the firmware (the gateways' operation logs, exact in 085 and 086); every MQTT packet through the broker (publishes, acknowledgements, connects, keepalives; the broker's `$SYS` counters, 086 firmware runs only: no 085 trace holds them); the firmware's flash writes (cutoff, claim mark, tombstones), counted from the flash files (086 firmware runs) and derived from the traces with the firmware's write rule (085 and 086; bounds where a fence got no answer); domain paused time with the per-identity pause fix, and per fence. Paired differences per episode with Student t and paired bootstrap intervals (10,000, seed 86); the break-even r* on logical calls next to the measured cost of a query and of a fence (or stop) in each unit. What a unit cannot give is said with the reason, never estimated |
| E-QB | The measured contract of `mosquitto-rpc-qos2` (level, capabilities, horizons, notes, the probes' outcomes) as a contract-map row next to the QoS 1 row, with the directed expiry check (per delay: trials, requests that took effect, effects after the expiry counted from the client's send, first-effect times; `report/qb086/`) |
| E-A | The report analysis of release 086 (`anchor086.py`, run over every campaign the final report reads: archive 066-076, 078, 083, 084, 085 and this plan's runs): for every completed fence, the anchor the evidence gives against the anchor the run committed (releases 061-085: the owner's reported state; 086: computed, with FENCE_ANCHOR_CHECK in the traces); counts per campaign and policy (fences completed, anchors compared, mismatches; "not available" where a lean archive keeps no journals) and every mismatch. Expected 0 mismatches ("owner up" regime): then the results of the runs of releases 066-085 do not depend on the anchor rule |

## 5. Sample size

**Prediction**: the original estimate (section 1). **Testbed spread** (`evaluation/power_extra086_testbed.py`; the
per-episode differences of the original runs, copied into `sim_predictions/extra086_templates.csv` with the SHA-256 of
their source, the 085 final report's `report/pairs/<plan>/cells.csv`): the primary templates are the within-run spread
of the original owner (C: SD 11.89; Rm: 5.58; the firmware within C3: 9.51, within Rz: 7.60), the sensitivity templates
the original contrasts themselves (MinIO: the same; firmware T4-3: 9.49, T4-4: 7.61, across runs). Each rule as
`pairs_report.py` decides it (one-sided t against the margin, Holm within its family with the other member at its
prediction), n = 8, 4,000 resamples, seed 86 (`sim_predictions/extra086_power.csv`):

| Rule | Family | Prediction | Margin (half) | Template SD, within / original | Power (within) | Power (original) | Power with the lower-95 % margin (within) | Decision |
|---|---|---|---|---|---|---|---|---|
| SR-1 | sr-minio | +53.40 | 26.70 | 11.89 / 11.89 | 1.000 | 1.000 | 0.641 (m = 43.45) | hypothesis |
| SR-2 | sr-minio | +16.50 | 8.25 | 5.58 / 5.58 | 0.999 | 0.999 | 0.668 (m = 11.83) | hypothesis |
| SR-3 | sr-fw | +53.65 | 26.83 | 9.51 / 9.49 | 1.000 | 1.000 | 0.656 (m = 45.71) | hypothesis |
| SR-4 | sr-fw | +18.59 | 9.30 | 7.60 / 7.61 | 0.993 | 0.994 | 0.665 (m = 12.23) | hypothesis |

| Rule | True effect -> power (within, n = 8) | Spread x1.5 / x2 | n = 6 / n = 10 |
|---|---|---|---|
| SR-1 | 0.75 x original: 0.906; 0.6 x: 0.331 | 0.994 / 0.912 | 1.000 / 1.000 |
| SR-2 | 0.75 x: 0.647; 0.6 x: 0.173 | 0.889 / 0.627 | 0.987 / 1.000 |
| SR-3 | 0.75 x: 1.000; 0.6 x: 0.379 | 1.000 / 1.000 | 1.000 / 1.000 |
| SR-4 | 0.75 x: 0.466; 0.6 x: 0.128 | 0.738 / 0.456 | 0.932 / 0.999 |

**SH and QB.** SH-A1 to SH-A4 are absence rules (no p-value, no power requirement, as every absence rule); 5 episodes,
the number of the state-read release baseline runs whose schedules they replay (RR, RA). QB is a measurement with fixed
probes and repetitions (those of the QoS 1 row) and the directed expiry check (two delays, 3 trials each).

**Episodes: 8**, the number of the original runs: power is at least 0.99 for every rule at n = 8 (0.93 already at
n = 6 for the weakest, SR-4), so 8 episodes meet the 0.80 rule and keep the replication the same size as the original.
The rules have little power when the true effect is 0.6 times the original or less: that is the boundary the margin
rule sets (a replication of a much smaller effect is reported NOT MET).

## 6. Exclusions, stopping and order

- INVALID cells are excluded and listed (a fault could not be applied, a process died, a firmware process exited, the
  truth could not be read). An INVALID cell is re-run once by resuming the campaign; if it is INVALID again it stays
  excluded. A run with more than 10 % excluded cells is not interpretable and its hypotheses are not decided.
- No interim analysis: `pairs_report.py` and `sr086.py` run only when every cell of a run is done. A campaign may be split
  over several sessions (resume).
- **Order** (one review run of release 086, one continuous overnight session: `run086.sh`, which resumes where it
  stopped when it is started again after an interruption): `preflight build verify distribute --new --quick` (0.15 h);
  `measure zephyr-plain zephyr` (0.65 h; the firmware contracts in this review run, needed by the firmware runs);
  `measure mosquitto-qos2` (QB with the expiry check, 0.5 h); `campaign expSR_zephyr_w6`, `expSR_zephyr_winf` (2.0 h each with the counters),
  `expSR_minio_w6`, `expSR_minio_winf` (1.95 h each), `expSH_queue`, `expSH_direct` (1.2 h each); `report --archive
  /root/evicon-archive --from /root/EVICON_TOIT_078/results/review-20261028T080322 --from <the 083 review run> --from
  <the 084 review run> --from <the 085 review run>` (0.85 h; the data freeze); `bundle --incremental` after new work.
  About 12.5 h in all (at most 13 h, the author's budget for one night). The shadow runs come last so that an unplanned
  violation there (which stops the session) cannot keep the replication from running; the QoS 2 measurement follows
  the firmware measurement on the same broker. `--until HH:MM` (Vietnam time) remains an option that stops before a
  step that would not end by then. No firmware build after the measurement (a new build makes the firmware runs refuse
  to start).
- **Not run.** The data freeze is this plan's final `report`. A campaign not completed by then is NOT RUN: its
  hypotheses are reported NOT RUN, its estimands are left out, no cell of it enters any analysis.
- **Safety stop.** A safety violation in any cell of this plan stops the session (the operator script stops and bundles);
  it is reported with the cells and traces.
- **Earlier plans.** The final report also decides every earlier plan; their 168 verdicts must equal the 085 final
  report's (`evaluation/verdicts086.py` lists every difference; a difference is logged as a FAIL and reported). The
  release-086 authority runs only this plan's cells; the earlier plans' verdicts come from their archived cells.
- The earliest complete run of each config is the preregistered one.

## 7. Frozen files

SHA-256 at freezing time (`scripts/freeze_prereg.py`); the driver checks them before running. Every file frozen by
`PREREG_extra_085.md` is frozen here again with the same SHA-256 (none changed in release 086), with that plan itself,
through the ```frozen-extra``` block; added: the schedule check of the new seed and its output, the testbed power script
with its templates and output, and the estimand script `sr086.py`, which decides nothing; for the Addendum, the
runner registration of SHADOW_RECONCILE (`ext086.py`, `campaign_ext086.py`), the firmware runner with the two cost
counters (`campaign_zephyr_ext086.py`) and its flash counter (`nvs_flash.py`), the cost estimand script (`cost086.py`),
and the QoS 2 device, adapter, config and directed expiry check. The runners the SR and SH campaigns build on
(`campaign.py`, `campaign_real.py`, `campaign_zephyr_ext077.py`, `ext083.py`, `ext084.py` and what they import) are
frozen by earlier plans and again here. The
analysis of this plan is `pairs_report.py` (frozen since 072).

The Go code is built from release 086 (the run's `source.json` records its source). Since release 085 the Go code changed
in these places only: `buildinfo.Release`; the fence anchor (`internal/runtime/fence_anchor.go`, the commit path of a
complete fence in `internal/runtime/authority.go`, `internal/evidence/fence_anchor.go`, `CommitBarrierUnanchored` and
`BarrierTokens` in `internal/journal/fence_intake.go`; the flag `-fence-anchor`, default `evidence`, approved by the
author as a safety fix; `-fence-anchor owner-state` restores releases 061-085); the shadow baseline, off unless a
campaign names SHADOW_RECONCILE (`internal/runtime/shadow_reconcile.go`; the method and `HasReconcileSweep` in
`internal/probe/policy.go`; its case in the release sweep, `internal/runtime/release_on_timeout.go`; the no-probe
condition in `internal/runtime/policy.go`; the re-publish gate on the dispatcher's `RetryAllowed`, set in
`NewAuthority` for that policy only; the sweep's start in `cmd/evicon-authority/main.go`). For every other policy
admission, the resolver and the dispatcher are unchanged; the simulator is unchanged. The firmware and the MQTT gateway are unchanged (the host library
`firmware/zephyr-cutoff/tests/host/evc_wire_shim.c` is test code and not part of the firmware build).

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
evaluation/prereg/PREREG_extra_085.md
evaluation/power_extra086_testbed.py
evaluation/sim_predictions/extra086_templates.csv
evaluation/sim_predictions/extra086_power.csv
evaluation/sr086_schedules.py
evaluation/sim_predictions/extra086_schedules.csv
evaluation/sr086.py
evaluation/ext086.py
evaluation/campaign_ext086.py
evaluation/campaign_zephyr_ext086.py
evaluation/nvs_flash.py
evaluation/cost086.py
conformance/reference/mqtt_device_qos2.py
conformance/evicon_probe/adapters/mqtt5_qos2.py
conformance/configs/mosquitto-rpc-qos2.json
conformance/reference/qos2_expiry_check.py
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
22586c782aa3ac704bcc56376324faeb7c4047da6e35c1668a65a34924bf4790  evaluation/prereg/PREREG_extra_085.md
21ca6b7e47dc2ea2078089c590f960139b03b50a7cf92c005086a7f238c765b2  evaluation/power_extra086_testbed.py
51fd53d4f79112b9e8dd1174745dff418b1f4b12b03b585d686bb442defa2765  evaluation/sim_predictions/extra086_templates.csv
3b6ec5830535a0b168e624b6f1005d16b0c26cf277f2310449d812d150050224  evaluation/sim_predictions/extra086_power.csv
fac6663d9ebce415d61cc733e25806ed36df64d30e514c6c8ff550ccbacaefac  evaluation/sr086_schedules.py
96edb19aac6c9e9b1669f01d02c23a4a53ba38595eadbeca97f6fd7209dd2d3e  evaluation/sim_predictions/extra086_schedules.csv
6dbd79f85b3032fb6eedf62526fa4e44ad6214262aac8e36aac315b5853686ef  evaluation/sr086.py
c1c3e666cc80a2cff01b2d091b0701035aa2da6ff57450a99ca23e1535082550  evaluation/ext086.py
0b2cfbc6dca643d5240ffb99b69459b4aa0ec6c403b60906219659122725bdd8  evaluation/campaign_ext086.py
d011e3bb4ca79b14277a9ca81641e1068134a335237fdec8ae4eb51421d189a9  evaluation/campaign_zephyr_ext086.py
65550bfe14dd895b9b9fb769fee8350c116c773e04bbf6c908b32aad29445c0a  evaluation/nvs_flash.py
2319f59274f33437a558ffaa2725c5ec63677981b3915a540676f2674fba328e  evaluation/cost086.py
8ca2ba7a7bd23b3e671b66b6866e85f4d7ade0a12c38470b4cc2354aefb8f0b2  conformance/reference/mqtt_device_qos2.py
b0402dbe14bf29e3adbd80c6c9725c277d06637f2549b8a3a9df0a288b693303  conformance/evicon_probe/adapters/mqtt5_qos2.py
156777a343836aeb50306a55a6003f7dbcec6430e4992a08f15ca08fc58a4cdb  conformance/configs/mosquitto-rpc-qos2.json
1007746b8b56a395ff4a4ce00535abeb8dde513953df0cf8a3d5474a9bd843ca  conformance/reference/qos2_expiry_check.py
```

## 8. Analysis spec (read by pairs_report.py and policy_compare.py)

```pairs-spec
{
  "name": "The experiments of release 086: the second-seed replication of the four real-owner headline contrasts",
  "runs": {
    "SRm6": {"campaign": "expSR-minio-w6"},
    "SRmi": {"campaign": "expSR-minio-winf"},
    "SRz6": {"campaign": "expSR-zephyr-w6"},
    "SRzi": {"campaign": "expSR-zephyr-winf"},
    "SHq": {"campaign": "expSH-queue"},
    "SHd": {"campaign": "expSH-direct"}
  },
  "alpha": 0.05,
  "max_invalid_fraction": 0.1,
  "hypotheses": [
    {"id": "SR-1", "family": "sr-minio", "run": "SRm6", "policy": "EVICON", "from": "MINIO_PLAIN", "to": "MINIO_COND", "metric": "clean_pp", "test": "at_least", "margin": 26.70},
    {"id": "SR-2", "family": "sr-minio", "run": "SRmi", "policy": "EVICON", "from": "MINIO_PLAIN", "to": "MINIO_COND", "metric": "clean_pp", "test": "at_least", "margin": 8.25},
    {"id": "SR-3", "family": "sr-fw", "run": "SRz6", "policy": "EVICON", "from": "ZEPHYR_PLAIN_DIRECT_QOS0", "to": "ZEPHYR_F_DIRECT", "metric": "clean_pp", "test": "at_least", "margin": 26.83},
    {"id": "SR-4", "family": "sr-fw", "run": "SRzi", "policy": "EVICON", "from": "ZEPHYR_PLAIN_DIRECT_QOS0", "to": "ZEPHYR_F_DIRECT", "metric": "clean_pp", "test": "at_least", "margin": 9.30},
    {"id": "SR-A1", "run": "SRm6", "test": "absent", "contract": "MINIO_PLAIN", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "SR-A2", "run": "SRm6", "test": "absent", "contract": "MINIO_COND", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "SR-A3", "run": "SRmi", "test": "absent", "contract": "MINIO_PLAIN", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "SR-A4", "run": "SRmi", "test": "absent", "contract": "MINIO_COND", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "SR-A5", "run": "SRz6", "test": "absent", "contract": "ZEPHYR_PLAIN_DIRECT_QOS0", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "SR-A6", "run": "SRz6", "test": "absent", "contract": "ZEPHYR_F_DIRECT", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "SR-A7", "run": "SRzi", "test": "absent", "contract": "ZEPHYR_PLAIN_DIRECT_QOS0", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "SR-A8", "run": "SRzi", "test": "absent", "contract": "ZEPHYR_F_DIRECT", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "SH-A1", "run": "SHq", "test": "absent", "contract": "K2", "policy": "SHADOW_RECONCILE", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "SH-A2", "run": "SHq", "test": "absent", "contract": "K2", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "SH-A3", "run": "SHd", "test": "absent", "contract": "K2", "policy": "SHADOW_RECONCILE", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "SH-A4", "run": "SHd", "test": "absent", "contract": "K2", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]}
  ],
  "safety": {"id": "SR-S", "runs": ["SRm6", "SRmi", "SRz6", "SRzi"], "exempt_contracts": []},
  "estimands": [
    {"run": "SRm6", "policy": "EVICON", "from": "MINIO_PLAIN", "to": "MINIO_COND", "metrics": ["confirmed_pp", "admitted_pp", "queries", "stops", "mean_time_to_terminal_s", "unresolved_at_end", "window_defers"]},
    {"run": "SRmi", "policy": "EVICON", "from": "MINIO_PLAIN", "to": "MINIO_COND", "metrics": ["confirmed_pp", "admitted_pp", "queries", "stops", "mean_time_to_terminal_s", "unresolved_at_end", "window_defers"]},
    {"run": "SRz6", "policy": "EVICON", "from": "ZEPHYR_PLAIN_DIRECT_QOS0", "to": "ZEPHYR_F_DIRECT", "metrics": ["confirmed_pp", "admitted_pp", "queries", "fences", "mean_time_to_terminal_s", "unresolved_at_end", "window_defers"]},
    {"run": "SRzi", "policy": "EVICON", "from": "ZEPHYR_PLAIN_DIRECT_QOS0", "to": "ZEPHYR_F_DIRECT", "metrics": ["confirmed_pp", "admitted_pp", "queries", "fences", "mean_time_to_terminal_s", "unresolved_at_end", "window_defers"]}
  ]
}
```

```policy-compare
{
  "name": "Release 086: the documented shadow reconciliation loop against EviCon and the state-read release baselines on the same schedules (PREREG_extra_086.md, E-SH)",
  "runs": {
    "SHq": {"campaign": "expSH-queue"},
    "SHd": {"campaign": "expSH-direct"},
    "RRq": {"campaign": "expRR-queue"},
    "RRd": {"campaign": "expRR-direct"},
    "RAq": {"campaign": "expRRA-queue"},
    "RAd": {"campaign": "expRRA-direct"}
  },
  "comparisons": [
    {"id": "SH-E1", "a": {"run": "SHq", "contract": "K2", "policy": "EVICON"}, "b": {"run": "SHq", "contract": "K2", "policy": "SHADOW_RECONCILE"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "SH-E2", "a": {"run": "SHd", "contract": "K2", "policy": "EVICON"}, "b": {"run": "SHd", "contract": "K2", "policy": "SHADOW_RECONCILE"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "SH-E3", "a": {"run": "RRq", "contract": "K2", "policy": "RELEASE_ON_READ"}, "b": {"run": "SHq", "contract": "K2", "policy": "SHADOW_RECONCILE"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "SH-E4", "a": {"run": "RAq", "contract": "K2", "policy": "RELEASE_ON_READ_ANSWERED"}, "b": {"run": "SHq", "contract": "K2", "policy": "SHADOW_RECONCILE"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "SH-E5", "a": {"run": "RRd", "contract": "K2", "policy": "RELEASE_ON_READ"}, "b": {"run": "SHd", "contract": "K2", "policy": "SHADOW_RECONCILE"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "SH-E6", "a": {"run": "RAd", "contract": "K2", "policy": "RELEASE_ON_READ_ANSWERED"}, "b": {"run": "SHd", "contract": "K2", "policy": "SHADOW_RECONCILE"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]}
  ]
}
```

## 9. Pre-release checks (before freezing; not results)

Checks of the code path before freezing, on one machine and on a simulated testbed. They are not results of this plan:
the regime, the scale, the timing and the platform differ from the testbed runs.

**Unit tests.** Go: every package passes (`go test ./...`, `go vet`, `gofmt`). The fence anchor (B2-02):
`TestFenceAnchorStatesUnit086` and `TestFenceAnchorStatesProperty086` (4,000 random ADD/SET batches executed in every
delivery order the model allows: the owner's true state is always a computed state, 2,188 batches with one state, which
is then sequence-order application whenever no SET applied, 1,812 with several), `TestFenceAnchorDefault086`,
`TestFenceAnchorFromEvidence086`, `TestFenceAnchorMismatchAborts086` (a reset level aborts the fence as an evidence
conflict; `-fence-anchor owner-state` adopts it), `TestFenceAnchorUndeterminedSET086`, `TestFenceAnchorPropertyE2086`
(80 random executions per case on the E2 owner with lost requests and replies, late copies and fences: honest owner
78 fences, 54 anchored, 24 unanchored, 0 anchors different from the owner's true state, 0 bound violations; reset level
70 fences, 30 aborted, 0 wrong anchors, 0 violations; owner-state anchor with the reset level 48 wrong anchors and a
bound violation in 17 of 80 executions) and `TestFenceAnchorPropertyDevice086` (60 executions per case with crash orphans
and a reboot on evicon-device: R5 and R5' with the evidence anchor 0 violations; owner-state 17 and 16 executions with a
bound violation). The shadow baseline: `TestShadowReconcilePolicy086`, `TestShadowReconcileK2Safe086`,
`TestShadowReconcileK0Unsafe086` (the K2 premise: on K0 the re-publish executes an applied command twice). Python: 238
analysis tests (among them `test_release086` K1-K23 and `test_prereg` on every plan: `PREREG_review_077.md` still
`db67640d...`, `PREREG_extra_083.md` `6e3f24a9...`, `PREREG_extra_084.md` `b1135164...`, `PREREG_extra_085.md`
`22586c78...`), 91 conformance tests, 0 skipped (among them `test_fence_anchor_reboot.py`: B2-02 end to end on the real
MQTT owner gateway, the firmware's wire path and decision core as a host library and the real authority: the evidence
anchor aborts the fence and the plant stays at 19 of 20, `-fence-anchor owner-state` lets it reach 31; and
`test_qos2_device.py`), and the firmware host tests.

**TLA+.** The fence models (`formal/EviconFence.tla`, `EviconFenceFI.tla`) never anchor on the owner's state: the
guard's reservations are the authority's statuses (APPLIED or PENDING attempts), so the evidence anchor is what they
already model and no configuration is added (brief, item 1: only if the models anchor on the owner's state).

**Analysis spec.** Synthetic runs made of renamed cells of the 085 final report (SRm6 = C's MINIO cells, SRmi = Rm's,
SRz6 = Z6's plain-QoS-0 cells and C3's {F} cells, SRzi = Zi's and Rz's; SHq = RAq's cells with RELEASE_ON_READ_ANSWERED
renamed SHADOW_RECONCILE, SHd = RAd's), with the configs' schedule files: `pairs_report.py` with this plan decided all 17
rules (SR-1 to SR-4 MET at the original estimates +53.40, +16.49, +53.65, +18.59; SR-A1 to SR-A8 ABSENT; SR-S MET;
SH-A1 PRESENT, the detection check on the renamed RA cells; SH-A2 to SH-A4 ABSENT); `policy_compare.py` paired SH-E1 to
SH-E6 on 5 episodes; `sr086.py` gave ratio 1.00 and a seed difference of 0.00 for every contrast. The earlier plans'
verdicts: the release-086 claims check on a copy of the 085 final report gives all 168 earlier hypotheses the 085
verdicts with the same estimates (`verdicts086.py`: identical). The fence anchors of the 10 campaign runs of 084 and 085
available on the build machine (FI, PF, PM): 1,091 completed fences, 1,091 compared, 0 mismatches. The cost table on the
085 matched runs (`cost086.py`): every firmware cell's flash writes recoverable exactly from its traces; EVICON against
PERIODIC_FULL per offered request: MQTT requests from the gateway +0.004 [-0.015, +0.023] (W = 6) and +0.001 [-0.014,
+0.016] (unbounded), flash writes +0.014 [+0.009, +0.019] and +0.016 [+0.012, +0.020], domain paused time +0.34 s
[+0.22, +0.46] and +0.34 s [+0.22, +0.46] (PERIODIC_FULL minus EVICON); r* on logical calls 1.48 and 1.45 (as the 085
report). Not results of this plan: the final report recomputes them.

**The QoS 2 row's T label (found in the simulated regression, before freezing).** P9T labelled T OBSERVED for
`mosquitto-rpc-qos2` in 3 of 3 repetitions (no effect of the delayed request, ever), where the QoS 1 row has no T. The
frozen probe delays every request-path byte by d = expiry + settle + 0.5 s = 31.5 s while its window is open, and the
proxy's quiesce wait after P9T (29.5 s) shows that a later request-path packet, the publisher's PUBREL, was held too: a
QoS 2 message is released to the subscriber on the PUBREL, which reached the broker about 2d after the send, after the
message's expiry counted from the broker's receipt. The label therefore says nothing about a delay below the expiry. A
directed check was added (`conformance/reference/qos2_expiry_check.py`, run by `measure mosquitto-qos2` after the
probes): on a separate Mosquitto 2.0.20 container with the QoS 2 device and plant, one trial per delay, the request
delayed by 22.5 s took effect 45.2 s after the send (after the 30 s expiry), the one delayed by 31.5 s never did. The
QB expectation and its interpretation (section 3) were written after this check and before freezing.

**Simulated testbed** (the driver end to end on one machine: sshd on five loopback addresses as VM-A to VM-E, Docker as
VM-B with Mosquitto 2.0.20 and the MinIO stand-in image; the firmware build placed as VM-D's build output; a copy of this
tree with this plan frozen in the copy, the six configs of 1 episode, 180 s + 30 s tail, and an exploratory config
`regFA_emulated` (expFI_s6's emulated owner with crash orphans, K0F, EVICON and EVICON_FI, no plan) for a fence on the
emulated owner; the operator script with the copy's paths and `--archive-missing-ok`; empty directories as the 078, 083
and 084 runs; the 085 final report's REPORT.md and claims.json as the 085 run and as its archive copy):

| Stage | Result |
|---|---|
| `run086.sh` (one invocation, 10:32): `preflight build verify distribute --new --quick` | PASS (verify: `go test`; 91 conformance tests, 0 skipped; the firmware host tests; 235 analysis tests, 0 skipped) |
| `measure zephyr-plain zephyr` | K0; {D_h, Q_A, F} (K3), fault model "owner up" |
| `measure mosquitto-qos2` | PASS: K0; D_h CONTRADICTED (P4, P5N, P6 duplicate), Q_A ABSENT, T OBSERVED by P9T (see above); late effect False |
| `campaign expSR_zephyr_w6`, `_winf` | PASS, 2 + 2 cells VALID; the binding check (plain: auto -> QoS 0); {F}: clean 367/367 in both, 137 and 138 fences, 19 completed each, `FENCE_ANCHOR_CHECK` MATCH 19 and 19; plain at QoS 0: 162/367 and 267/367; counters: broker packets plain 1,201 and 1,833, {F} 3,507 and 3,512; flash writes counted = derived (claim mark 8, cutoff 19; the derived bounds [19, 19] although 114 fences got no answer); plain: 0 |
| `campaign expSR_minio_w6`, `_winf` | PASS, 2 + 2 cells VALID; conditional PUT 367/367 and 366/367 (184 and 195 queries, 47 and 45 stops), plain PUT 162/367 and 267/367 |
| `campaign expSH_queue`, `expSH_direct` | PASS, 2 + 2 cells VALID, safety counts 0, late effects 0; SHADOW_RECONCILE: queue 365/365 (104 reads: 68 NOT_FOUND -> re-published, 34 UNCERTAIN -> read again, 2 APPLIED recorded), direct 363/365 (117 reads: 82 UNREACHABLE and 8 NOT_FOUND -> re-published, 27 UNCERTAIN); EVICON 365/365 and 320/365 |
| `campaign regFA_emulated` (no plan) | interrupted after its first cell (the session was killed at 12:32 by the build machine's job limit); the orphaned processes stopped by their pid files |
| the same command again (resume) | stages "already PASS", both measurements and the six campaigns skipped ("finished earlier in this run"), regFA resumed: PASS, EVICON 343/365 (211 fences), EVICON_FI 363/365 (491 fences), `FENCE_ANCHOR_CHECK` MATCH 29, safety 0; then `report` and `bundle` |
| refusal: the command started while `campaign expSH_queue` ran | `REFUSED: another evicon_review.py is running (pid ...)`, exit 1, nothing run |
| `report --archive-missing-ok --archive <archive> --from <078> --from <083> --from <084> --from <085>` | PASS: SR-1 to SR-4 NOT COMPUTABLE (one episode); SR-A1 to SR-A8 ABSENT; SR-S MET; SH-A1 to SH-A4 ABSENT; SH-E1, SH-E2 on one episode, SH-E3 to SH-E6 NOT RUN (the RR and RA runs are not among the stand-in sources); E-SR without originals (not among the sources); fence anchors: 67 completed fences in 7 runs, 67 compared, 0 mismatches; E-COST for the six 086 runs; the QoS 2 row in the contract map (K0, T); the earlier verdicts against the 085 final report: DIFFER (the stand-in sources hold none of the earlier plans' runs: every verdict missing, none different), logged as a FAIL, `REPORT DONE WITH A DIFFERENCE`; 4 incremental bundles |
| after two changes the regression led to (the directed expiry check of QB, section 3; the shadow loop's reads of the reported state counted as owner calls in E-COST, as the row's queries do not include them): the copy synced and its plan frozen again, `preflight build verify distribute --force --quick`, `measure mosquitto-qos2 --force`, the same command again | PASS (verify: 91 conformance tests, 0 skipped; 238 analysis tests, 0 skipped); QoS 2: the same labels, then the expiry check: d = 22.5 s: 3 of 3 requests took effect after the expiry counted from the send (at 45.2 s each), d = 31.5 s: 0 of 3 took effect (the measurement 16.5 min in all); the command skipped every step and campaign and ran `report` (now with `report/qb086/`, the `\evQosTwoExp*` macros and, for SHADOW_RECONCILE, 0.28 and 0.32 reads and 0.19 and 0.25 re-publishes per offered request) and `bundle`; the provenance table marks the copy's plan DIFFERENT from the SHA its runs carry (frozen again after the runs), as it should |

## Deviations

None so far.

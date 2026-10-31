# Preregistration: the second set of review experiments of release 083 (QueryOnly with the window-full trigger, release on an answered read, the release time of TimeoutRelease, the plain firmware under the at-most-once binding, the greedy certificate and the cost ratio theta, the window on the firmware and on ThingsBoard, the fault regime and new seeds)

Release 083. Frozen before any of its cells runs on the testbed. The thirty campaign configs below carry the
SHA-256 of this file, and `evicon_review.py campaign` refuses to run them if this file, or any file listed under
*Frozen files*, has changed. Changes after freezing are recorded under *Deviations*; nothing above that section is
edited.

## 1. Background (exploratory evidence, not part of the test)

A second internal review of the paper draft (written as a journal referee would) asked seven questions that the
existing five-VM testbed can answer (T1-T7 below) and three analyses of the existing traces (A1-A3, section 4).
The numbers quoted are from the runs of releases 072-078 as analysed by the final report of release 081
(`review-20261030T182927`: `report/pairs/*/results.json`, `report/policy_compare/PREREG_review_077/`), EVICON and
clean fulfilment in % of offered unless stated.

**(a) QueryOnly and the window-full trigger (T1).** On ThingsBoard's persistent RPC (`expTB-thingsboard/
20261029T175533`, contract {Q_A, T}, W = 6, 6 episodes) EVICON reached 74.3 % and QueryOnly (M1) 24.4 % (TB-E1:
+49.9 pp). Both query the owner and neither stops or fences; they differ in when they query. M1 queries only when
the guard blocks a candidate (some outcome of the unresolved commands would exceed the dose bound); EVICON also
queries aged ambiguous commands when a candidate is deferred because the window of W unresolved commands is full
(its pending-bound mode: any terminal answer frees a slot). At W = 6 most deferrals are window-bound, so M1 rarely
queries: in the simulator's ThingsBoard twin 2.8 queries per episode against EVICON's 89.9. The paper attributes the
gap to that trigger; the review asked whether the trigger is the whole gap. Release 083 adds **M1W**
(`evicon-authority -policy M1W`): M1 plus EVICON's window-full trigger. When a candidate is deferred with
OUTSTANDING_BUDGET_EXHAUSTED, M1W queries the oldest ambiguous commands (unresolved for at least `ambiguity_after`,
not queried within the last `ambiguity_after`, lowest sequence first, within the per-evaluation action budget
`probe_budget`) and evaluates the candidate again; it never stops or fences and has no background sweep, and on a
guard block it queries exactly as M1 does (runtime test F49; simulator policy QUERY_ONLY_W). **Run Wk**: contract K2 =
{D_h, Q_A} on the emulated owner, queue channel, W = 6, EVICON, M1, M1W,
the window experiment's seed 20261017 (episodes 0-7 replay `expW-queue-w6`'s schedules), the resend cut of 30 s of
the 077 K2 runs. **Run TBw**: the TB config (seed 20261025, episodes 0-5 replay TB's schedules), EVICON, M1, M1W.
On K2 the dispatcher re-sends an unresolved command under its identity (D_h), and most ambiguous commands resolve
without a query; the simulator therefore predicts no gap there (M1 76.4 %, M1W 76.8 %, EVICON 76.8 %) and the full
gap closed by M1W on ThingsBoard, where nothing resolves a command but a query (M1 38.4 %, M1W 73.7 %, EVICON
72.9 %).

**(b) Release on an answered read (T2).** Release 077's RELEASE_ON_READ released a command whenever a state read
did not show it. On the direct channel 419 of its 420 releases followed a read the owner did not answer
(`RELEASE_ON_READ_NOT_REPORTED_UNREACHABLE`; one followed NOT_FOUND) and 63 safety violations followed (RR-2
DETECTED, RR-E2). The review asked for the variant a careful client would use: release only when the owner,
reachable, answers that it has no record. Release 083 adds **RELEASE_ON_READ_ANSWERED** (unsafe by design: it
breaks rule R2 whenever the answer is not evidence of non-occurrence; runs only in its own cells). A command
unresolved 10 s after its creation (`-release-after`) whose last dispatch attempt ended at least 7 s ago
(`-release-settle`; no attempt in flight) is read at the owner (`GET /v1/receipts/<id>`): a valid APPLIED or
STOPPED receipt is recorded; UNCERTAIN (a claim without a receipt) marks the command as one the owner cannot
certify, and it is never released; an unanswered read is retried at the next sweep; an answered NOT_FOUND is
checked again against the settle rule, then the command is committed STOPPED and its intent re-issued under a new
identity. Its dispatcher stops re-sending a command 10 - 7 = 3 s after its creation, so that no attempt of a command
can be in flight when it is read (runtime test F50). The settle time of 7 s is above the testbed's one-way delay
bound (`delay_ms` 6000) and above the dispatcher's request timeout (2 s).

What the theory predicts. An answered NOT_FOUND is evidence of non-occurrence only when no request for the command
can still arrive and the owner has not lost the record of an effect. (i) On the queue channel the owner holds the
requests of an offline device and executes them at reconnect; a read answered while a request is held finds no
record, and the released command takes effect later. The emulated owner refuses reads while it is offline; run RAq
therefore lets it answer them from its records (`evicon-device -shadow-reads`, release 083, off by default), as a
device shadow or registry in front of a held command channel does. (ii) On the direct channel a request is either
lost or delayed by at most 6 s, less than the settle time, so an answered NOT_FOUND is evidence (absence after a
known delay bound: the forward-path proposition with a finite delta, the paper's remark on state reads). (iii) A
crash between an effect and its receipt (a crash orphan) does not lose the record on this owner: the device
journals its claim (fsync) before the effect, and a read of an orphan answers UNCERTAIN, never NOT_FOUND. The brief
expected the direct channel with orphans to be DETECTED ("the owner loses records"). That needs an owner that
forgets an effect it executed; on a contract with D_h such an owner also re-executes a resend, which breaks the
contract EVICON relies on in the same cells. The plan therefore keeps the orphan setting of `expRR-direct` and
preregisters ABSENT for it as for the owner without orphans: the remark on state reads holds whenever the owner keeps
its records, with or without orphans, and what orphans change is the number of commands a read cannot settle
(E-T2). **Runs RAq, RAd, RAo**: K2, EVICON and RELEASE_ON_READ_ANSWERED, W = 6, seed 20261017, 5 episodes (episodes
0-4 replay `expRR-queue`'s and `expRR-direct`'s schedules); RAq on the queue channel with shadow reads, RAd on the
direct channel (`-offline-mode drop`, with crash orphans), RAo on the direct channel without crash orphans (every
crash moved after the durable receipt: `-crash-point-override AFTER_RECEIPT_BEFORE_REPLY`, the setting of
`expO-noorphan`). The simulator has no model of this baseline.

**(c) The release time of TimeoutRelease (T3).** RELEASE_ON_TIMEOUT released every command still unresolved 10 s
after its creation; it violated safety in every cell (RT-1 to RT-3: 1,530 violations in 8 queue cells, 211 in 8
direct cells) and bought clean fulfilment with it (RT-E1: +38.2 pp over EVICON on K0, queue). The review asked
whether a longer release time removes the violations. The theory says it does not: on the queue channel a request
can be held for as long as the device is offline (10-40 s, back to back), and on the direct channel a command whose
reply was lost has taken effect whatever the release time. **Runs RTsq, RTsd**: RELEASE_ON_TIMEOUT with release
times 30, 60 and 120 s (policy names `RELEASE_ON_TIMEOUT_30S`, `_60S`, `_120S`, which the runner maps to
`-policy RELEASE_ON_TIMEOUT -release-after <n>s`), K0, queue and direct channel, W = 6, seed 20261017 (episodes 0-4
replay `expRT-queue`'s and `expRT-direct`'s schedules, and those of `expW-queue-w6` and `expW-direct-w6`, whose
EVICON cells are the comparison), 5 episodes. The release-077 simulator models release on timeout (its
B0_NAIVE_NEW_ID policy with a release time): violations per episode on the queue channel 35.8 / 10.4 / 4.1 / 3.6 at
10 / 30 / 60 / 120 s, on the direct channel 6.4 / 5.2 / 2.6 / 1.4; the share of episodes without a violation is at
most 12 % on the queue channel and 9 / 30 / 60 % on the direct channel at 30 / 60 / 120 s, so a run of 5 episodes
detects with probability >= 0.92 at every point.

**(d) The plain firmware under the at-most-once binding (T4).** The firmware's main effects were measured with the
plain firmware's commands at QoS 1 (C2: plain -> cutoff on the queue channel, 29.8 -> 79.3 %; C3: plain -> {F} on the
direct channel, 25.4 -> 79.1 %; Rz at W unbounded: plain 77.7 / cutoff 79.3 %, plain direct 60.7 / {F} 79.3 %).
Release 077's run Q showed that QoS 1 on a firmware without D_h duplicates commands below the runtime when a reset
loses the broker's acknowledgement (Q-1 DETECTED), and that the gateway's binding rule (QoS 0 for a contract without
D_h, `binding: auto`) restores safety (Q-2 ABSENT). The review asked for the plain-firmware baselines under the rule.
**Runs Z6 and Zi** re-run the plain firmware's EVICON cells of C2, C3 and Rz with `binding: auto` (QoS 0 for the
plain firmware), the queue channel (ZEPHYR_PLAIN_QOS0) and the direct channel (ZEPHYR_PLAIN_DIRECT_QOS0), at W = 6
(Z6: seed 20261017, episodes 0-7 replay C2's and C3's schedules) and W unbounded (Zi: replaying Rz's), on the
077 runner (`campaign_zephyr_ext077.py`, frozen) and the firmware binary measured in the same review run. The
main effects are then recomputed against the QoS 0 plain baseline: the cutoff and {F} cells of C2, C3 and Rz, paired
by schedule, with the original margins (C2-3, C2-4, C3-1: 10 pp; U-2, U-3: 5 pp; U-4: equivalence 3 pp). Under QoS 0
the broker does not keep a command for a disconnected subscriber, so the queue channel loses what it held: the
twin's prediction is that the plain firmware then behaves on the queue channel as on the direct channel (34.5 % at
W = 6, 62.6 % at W unbounded) and that U-4's equivalence no longer holds: U-4 assumed a queue that delivers what it held,
and under QoS 0 the cutoff gains at W unbounded as on the direct channel (+14.5 pp). The plan therefore tests that gain
(T4-6, `at_least 5`, U-2's and U-3's margin) instead of U-4's equivalence.

**(e) The greedy certificate, gamma and theta (T5).** The release enumerates certificate candidates in increasing
size up to gamma = 3 and stops after 20,000 checked sets or a time budget (Section 3.6); at an unbounded window the
search fails often: `EVICON_CERTIFICATE_SEARCH_FAILED` among the top defer reasons of `expS-winf` (K0F 4,369, K0S 53
DEFER decisions in 12 episodes), `expW-direct-winf` (K0F 3,829) and Rm (MINIO_PLAIN 5,164; MINIO_COND none). Section
3.6 describes, for scalar ADD domains with an upper or sum bound, a greedy certificate without that limit. Release
083 implements it as an option of the guard (`evicon-authority -certificate greedy`; default `enumerate`, the
current search; `probe.GreedyContinuationCertificate`): the unresolved commands sorted by size, the largest taken
until the deficit is covered (found by bisection with the guard itself, so the result is exactly what the guard
admits), then every member whose removal still leaves the deficit covered dropped; O(n log n) checks of the guard; it
never gives up. On any other domain the guard falls back to the enumeration. Property tests: on 600 random ADD
domains the greedy certificate exists exactly when an unbounded search finds one, has the minimum size and is
minimal by inclusion (49 of the 201 certificates were beyond gamma = 3); the guard admits after its members are
stopped, and every outcome of the remaining commands stays within the bound; 108 unresolved doses filling the bound of
345 units take 2 ms
(`internal/probe/greedy_certificate_test.go`; runtime test F51). **Run Gs**: `expS-winf`'s schedules (seed
20261017, episodes 0-7), K0S and K0F, EVICON (enumeration) and EVICON_GREEDY (`-certificate greedy`), W unbounded.
K0, which has no action a certificate could name, is left out. **Run Gm**: Rm's schedules (MinIO at W unbounded,
seed 20261025), MINIO_PLAIN and MINIO_COND, EVICON_GREEDY only; its comparison is Rm's EVICON cells, paired by
schedule. **Run Th**: theta far from the default. With lambda = 1 per mL and second, kappa_F = 5 fences after one
second of ambiguity of a 5 mL dose; EVICON_FC100 (`-fence-cost 100`) waits 20 s. K0F, EVICON and EVICON_FC100, W = 6,
the window experiment's seed 20261017 (episodes 0-7 replay `expW-queue-w6`). gamma is swept in the simulator only
(section 1(h)).

**(f) The window on the firmware and on ThingsBoard (T6).** The firmware's effects were measured at W = 6 (C2, C3)
and W unbounded (Rz), MinIO's at W = 6, 16 and unbounded (C, C16, Rm). **Runs Z3w, Z16w, Z32w**: C2's setup (queue
channel, seed 20261017, episodes 0-7 replay C2's schedules), EVICON, ZEPHYR_PLAIN_QOS0 and ZEPHYR_CUTOFF under the
binding rule (QoS 0 / QoS 1), at W = 3, 16 and 32; W = 6 and W unbounded come from C2 and Rz against Z6 and Zi.
**Runs TBw3, TBw16, TBwinf**: the TB config at W = 3, 16 and unbounded, EVICON and M1W (W = 6 is TBw). The
simulator predicts the firmware's effect to fall as the window grows (+51.7, +43.0, +29.0, +19.4, +14.5 pp at W = 3,
6, 16, 32, unbounded) and the ThingsBoard gap between M1W and EVICON to stay near 0 at every window (-0.2 to -0.7 pp).

**(g) The fault regime and new seeds (T7).** Every effect of backward evidence on the emulated owner was measured in
one regime (2 fault windows per minute, offline 10-40 s), plus run V at 1 window per minute (V-1, V-2 MET) and the
simulator's sensitivity sweep of release 077. **The T7 runs** move one factor at a time around the base regime: the
fault rate to 0.5 and 4 windows per minute (1 is run V), the offline duration to 5-15 s and 40-120 s, and two new
seeds at the base regime (20261101, 20261102); contracts K0, K0F, K0S, EVICON, on the queue channel at W = 6
(`expS-w6`'s setup, the runs T7q-<level>) and K0, K0F on the direct channel at W unbounded (`expW-direct-winf`'s
setup, T7d-<level>; at W = 6 on the direct channel the effect is window-bound, at W unbounded on the queue channel it
is near 0). The factor levels keep seed 20261017, so the arrivals are those of S and Wdi (common random numbers for
the workload) and only the fault schedule changes. As realized by the testbed's schedule generator
(`campaign.schedule`, 60 episodes x 4 devices): offline share 0.21 (base), 0.06 (0.5/min), 0.35 (4/min), 0.09
(5-15 s), 0.45 (40-120 s); crash windows per device-hour 21, 6, 35, 23, 15. The simulator, with these realized shares
(`sim_extra083.py`, `regime_patch`), predicts every effect positive at every level (section 1(h)), smallest at
0.5/min (fence +17.7, stop +17.8 pp on the queue channel; fence +4.6 pp on the direct channel). Four episodes per
level and channel keep T7 within 15 testbed hours (120 cells); section 5 gives the power that buys, and by the 0.80 rule
of section 3 the effects at 0.5/min and the direct-channel effect at 5-15 s are estimands, not hypotheses.

**(h) Simulator prediction** (`evaluation/sim_extra083.py`, release 083 simulator, twin
`experiments/sim/iot_dosing_testbed.json` with the ThingsBoard contract `experiments/sim/contracts083/
sim-tb-083.json` ({Q_A, T}, expiry 60 s), no assumed bound on the network delay, 200 episodes per scenario;
`SIMULATION_ONLY`; `evaluation/sim_predictions/extra083_summary.csv`, `extra083_effects.csv`, `extra083_power.csv`,
`SIM_EXTRA083.md`). Clean fulfilment, % of offered (EVICON unless stated):

| Scenario (runs) | Cells |
|---|---|
| t1_k2 (Wk) | K2: M1 76.4, M1W 76.8, EVICON 76.8 (queries per episode 9.6, 42.4, 59.7) |
| t1_tb (TBw) | TB_RPC: M1 38.4, M1W 73.7, EVICON 72.9 (queries 2.8, 70.2, 89.9) |
| t3_rt (RTsq, RTsd) | queue: 69.8 / 75.1 / 78.9 / 78.9 % at 10 / 30 / 60 / 120 s, violations per episode 35.8 / 10.4 / 4.1 / 3.6; direct: 75.7 / 76.6 / 77.1 / 70.7 %, 6.4 / 5.2 / 2.6 / 1.4 |
| t4_w6 (Z6) | ZEPHYR_PLAIN_QOS0 34.5 (= ZEPHYR_PLAIN_DIRECT_QOS0), ZEPHYR_PLAIN_QOS1 38.4, ZEPHYR_CUTOFF 77.0, ZEPHYR_F_DIRECT 77.1 |
| t4_winf (Zi) | ZEPHYR_PLAIN_QOS0 62.6 (= direct), ZEPHYR_PLAIN_QOS1 75.6 (confirmed 62.1), ZEPHYR_CUTOFF 77.0, ZEPHYR_F_DIRECT 77.1 |
| t5_s_winf (Gs) | K0F 76.6 with either certificate; K0S 76.6 (search failures: enumeration 5, greedy 0) |
| t5_minio (Gm) | MINIO_PLAIN 62.6 (search failures 454 / 0), MINIO_COND 77.2 (12 / 0) |
| t5_gamma (section 1(e); W = 6, unbounded) | K0F: gamma 1, 2, 3, 4 and greedy give the same clean fulfilment (72.7 % at W = 6, 76.6 % unbounded); fences per episode 165-170 (W = 6) and 212-220 (unbounded); gamma 4 at W unbounded: 24 search failures |
| t5_theta (Th) | K0F at W = 6: fence cost 0.5 / 5 / 50 / 100 / 500 and theta = 0: 72.7 / 72.7 / 71.1 / 70.9 / 68.7 / 72.5 %; fences per episode 171 / 170 / 150 / 130 / 56 / 173 |
| t6_fw (Z3w ... Z32w, with Z6, Zi) | ZEPHYR_PLAIN_QOS0 25.9 / 34.5 / 48.3 / 57.7 / 62.6 % and ZEPHYR_CUTOFF 77.0 % at W = 3 / 6 / 16 / 32 / unbounded |
| t6_tb (TBw3 ... TBwinf, with TBw) | EVICON 68.7 / 72.9 / 75.2 / 75.6 %, M1W 68.9 / 73.7 / 75.7 / 75.6 % at W = 3 / 6 / 16 / unbounded |
| t7 (T7q, T7d) | queue W = 6, K0 / K0F / K0S: base 33.0 / 72.6 / 77.3; 0.5/min 59.9 / 77.1 / 77.3; 4/min 19.0 / 65.7 / 77.3; 5-15 s 40.7 / 73.5 / 77.3; 40-120 s 31.8 / 72.2 / 72.8. Direct W unbounded, K0 / K0F: base 61.4 / 75.9; 0.5/min 72.4 / 77.0; 4/min 50.9 / 74.4; 5-15 s 69.3 / 76.2; 40-120 s 47.2 / 73.9 |

No safety violation in any EVICON, M1 or M1W cell of any scenario. The twin has no model of RELEASE_ON_READ_ANSWERED,
of the shadow reads, of the testbed's certificate time budget (its enumeration fails far less often than the
testbed's: K0F at W unbounded 0 against `expS-winf`'s thousands), or of the firmware's QoS other than through the
channel (QoS 0 is modelled as the direct channel). Mean per-episode effects and their SD are in section 5.

**Pre-release checks.** See section 9 (written before freezing; local and simulated-testbed runs, not results).

## 2. Design

Common regime (as `expW_queue_w6`, `expS_w6`, `expC2_zephyr`, `expTB_thingsboard`): 4 devices; 300 s per cell + 60 s
tail; 0.5 commands/s per device; values 1-5; bound 345 per device; deadline 60 s; validity 90 s; fault windows
2/min (T7: as its level); offline 10-40 s (T7: as its level); mix offline 0.3, drop_up 0.2, drop_down 0.2, delay_up
0.1 (6 s), crash 0.2; authority: no assumed bound on the network delay (`max_network_delay` -1ns), SKI_RENTAL
resolution, query first, costs hold 1, stop 1, fence 5 (Th: fence 100 for EVICON_FC100); the T4 retry guard on.

| Run | Config | Runner | Owner, channel | Contracts | Policies | W | Seed (replays) | Episodes | Cells | Hours |
|---|---|---|---|---|---|---|---|---|---|---|
| **Wk** | `expM1W_k2.json` | `campaign_ext083.py` | evicon-device, queue | K2 | EVICON, M1, M1W | 6 | 20261017 (Wq) | 8 | 24 | 2.9 |
| **TBw** | `expTB_m1w.json` | `campaign_tb_ext083.py` | ThingsBoard CE persistent RPC (VM-B), reference MQTT devices (VM-E), as TB | TB_RPC (`thingsboard-rpc-r0` measured in the run) | EVICON, M1, M1W | 6 | 20261025 (TB) | 6 | 18 | 2.3 |
| **RAq** | `expRRA_queue.json` | `campaign_ext083.py` | evicon-device, queue, shadow reads | K2 | EVICON, RELEASE_ON_READ_ANSWERED | 6 | 20261017 (RRq) | 5 | 10 | 1.2 |
| **RAd** | `expRRA_direct.json` | `campaign_ext083.py` | evicon-device, direct (`-offline-mode drop`) | K2 | EVICON, RELEASE_ON_READ_ANSWERED | 6 | 20261017 (RRd) | 5 | 10 | 1.2 |
| **RAo** | `expRRA_noorphan.json` | `campaign_ext083.py` | evicon-device, direct, crash after the receipt | K2 | EVICON, RELEASE_ON_READ_ANSWERED | 6 | 20261017 (RRd) | 5 | 10 | 1.2 |
| **RTsq** | `expRTs_queue.json` | `campaign_ext083.py` | evicon-device, queue | K0 | RELEASE_ON_TIMEOUT_30S, _60S, _120S | 6 | 20261017 (RTq, Wq) | 5 | 15 | 1.8 |
| **RTsd** | `expRTs_direct.json` | `campaign_ext083.py` | evicon-device, direct | K0 | RELEASE_ON_TIMEOUT_30S, _60S, _120S | 6 | 20261017 (RTd, Wd) | 5 | 15 | 1.8 |
| **Z6** | `expT4_zephyr_w6.json` | `campaign_zephyr_ext077.py` | the Zephyr firmware behind Mosquitto, binding auto (QoS 0) | ZEPHYR_PLAIN_QOS0 (queue), ZEPHYR_PLAIN_DIRECT_QOS0 (direct) | EVICON | 6 | 20261017 (C2, C3) | 8 | 16 | 1.9 |
| **Zi** | `expT4_zephyr_winf.json` | `campaign_zephyr_ext077.py` | as Z6 | as Z6 | EVICON | 1000 | 20261017 (Rz) | 8 | 16 | 1.9 |
| **Gs** | `expG_s_winf.json` | `campaign_ext083.py` | evicon-device, queue | K0S, K0F | EVICON, EVICON_GREEDY | 1000 | 20261017 (Si) | 8 | 32 | 3.8 |
| **Gm** | `expG_minio_winf.json` | `campaign_real_ext083.py` | MinIO behind the S3 gateway (as Rm) | MINIO_PLAIN, MINIO_COND (the 070 measured contracts) | EVICON_GREEDY | 1000 | 20261025 (Rm) | 8 | 16 | 1.9 |
| **Th** | `expTheta_w6.json` | `campaign_ext083.py` | evicon-device, queue | K0F | EVICON, EVICON_FC100 | 6 | 20261017 (Wq) | 8 | 16 | 1.9 |
| **Z3w, Z16w, Z32w** | `expT6_zephyr_w3/_w16/_w32.json` | `campaign_zephyr_ext077.py` | the firmware, queue, binding auto | ZEPHYR_PLAIN_QOS0 (QoS 0), ZEPHYR_CUTOFF (QoS 1) | EVICON | 3, 16, 32 | 20261017 (C2) | 8 | 16 each | 1.9 each |
| **TBw3, TBw16, TBwinf** | `expTB_w3/_w16/_winf.json` | `campaign_tb_ext083.py` | as TBw | TB_RPC | EVICON, M1W | 3, 16, 1000 | 20261025 (TB) | 6 | 12 each | 1.5 each |
| **T7q-<level>** | `expT7_q6_<level>.json` | `campaign.py` | evicon-device, queue | K0, K0F, K0S | EVICON | 6 | 20261017 (S's arrivals); seedA 20261101, seedB 20261102 | 4 | 12 each | 1.4 each |
| **T7d-<level>** | `expT7_dinf_<level>.json` | `campaign.py` | evicon-device, direct | K0, K0F | EVICON | 1000 | 20261017 (Wdi's arrivals); 20261101, 20261102 | 4 | 8 each | 1.0 each |

T7 levels: `rate05` (0.5 fault windows/min), `rate4` (4/min), `off5` (offline 5-15 s), `off40` (offline 40-120 s),
`seedA`, `seedB` (the base regime, new seeds). Total: 30 campaigns, 402 cells, about 49 testbed hours.

Reference runs (no new cells; read from the archive and the 078 run): C2 `expC2-zephyr/20261024T044342`, C3
`expC3-ablation/20261024T161324`, Rz `expR-zephyr-winf/20261028T084744`, Rm `expR-minio-winf/20261028T145228`, TB
`expTB-thingsboard/20261029T175533`, Wq `expW-queue-w6/20261021T194415`, Wd `expW-direct-w6/20261022T080622`, Wdi
`expW-direct-winf/20261022T130854`, S `expS-w6/20261022T161003`, Si `expS-winf/20261023T010003`, RTq
`expRT-queue/20261029T052600`, RTd `expRT-direct/20261029T072115`, RRq `expRR-queue/20261029T081859`, RRd
`expRR-direct/20261030T114515` (the earliest complete run of each config, as the report selects them).

- **Runners.** `campaign_ext083.py` (on the frozen `campaign_ext077.py`, on the frozen `campaign.py`),
  `campaign_real_ext083.py` (on the frozen `campaign_real.py`) and `campaign_tb_ext083.py` (on the frozen
  `campaign_tb.py`) import the frozen runners unchanged and add only the 083 policies (`ext083.py`): their authority
  arguments (M1W: `-policy M1W -ambiguity-after <a>`; RELEASE_ON_READ_ANSWERED: `-policy RELEASE_ON_READ_ANSWERED
  -release-after 10s -release-settle 7s -resolve-sweep 1s`; RELEASE_ON_TIMEOUT_<n>S: `-policy RELEASE_ON_TIMEOUT
  -release-after <n>s -resolve-sweep 1s`; EVICON_GREEDY: EVICON's arguments and `-certificate greedy`;
  EVICON_FC100: EVICON's arguments with `-fence-cost 100`; each with the run's `-resend-until`) and the device flag
  `-shadow-reads` from the config's `faults.device_args`. Every policy of earlier releases keeps its arguments
  (test H3). The firmware runs use the 077 runner unchanged, with the binding rule (`binding: auto`), and the T7 runs
  the frozen `campaign.py` unchanged. The driver picks the runner from the policies (`evicon_review.py`:
  `_emulated_runner`, `_uses_083_policies`).
- **Contracts measured in the same review run**: the firmware's (`measure zephyr-plain zephyr`, the driver's gate of
  C2/C3/Rz: MEASURED, the planned capabilities, the fault model "owner up", on the binary the runs use) and
  ThingsBoard's (`thingsboard-rpc-r0`, gate MEASURED, {Q_A, T}, "owner up"); otherwise no firmware or ThingsBoard
  cell runs. Gm uses MinIO's 070 contracts, as Rm did.
- **Truth**: `evicon-plant` on VM-E in every run (on ThingsBoard the reference devices write it; on MinIO the object
  versions, as in C and Rm).
- **Pairing**: contracts and policies of the same episode within a run; across runs, episodes whose schedule files
  `schedule_ep<e>.json` are byte-identical (common random numbers; test H5 replays them through `campaign.schedule`).
- **Activation gates** (as the runs they replay): at least one acting decision of the gated policies in every VALID
  gated cell: Gs, EVICON and EVICON_GREEDY on K0S and K0F; Th, EVICON and EVICON_FC100 on K0F; T7q, EVICON on K0S and
  K0F (as S); T7d, EVICON on K0F (as Wdi); none elsewhere.
- **Analysis**: `evaluation/pairs_ext083.py --plan evaluation/prereg/PREREG_extra_083.md --run <key>=<run> ... --out
  <dir>`. It runs the frozen `pairs_report.py` on the ```pairs-spec``` block (decided exactly as `pairs_report.py`
  alone would) and decides the ```pairs-ext``` block with pairs_report's own functions: contrasts between policies
  within a run, contrasts across runs paired by schedule, differences of contrasts (the window's direction rules),
  safety rules over groups of runs, and the extra cell metrics `search_failures` (DEFER decisions
  EVICON_CERTIFICATE_SEARCH_FAILED, from the authority traces) and `releases_without_evidence`. Holm within each
  family; the two blocks' families are disjoint. `evaluation/policy_compare.py` (frozen) runs the ```policy-compare```
  block. `evicon_review.py report` runs both with the earliest complete run of each campaign among its sources.

## 3. Hypotheses and decision rules

Tests on per-episode paired differences (to minus from), clean (or confirmed) fulfilment in pp of offered, Student
t, alpha = 0.05; Holm within each family. Detection, absence and safety rules have no p-value and are not part of a
family.

**The 0.80 rule.** A paired-difference rule is a confirmatory hypothesis only if its power under the testbed's own
spread (section 5, second table: `power_extra083_testbed.py`), at the predicted (simulator) mean and with Holm in its
family, is at least 0.80. Otherwise it is an estimand (section 4): reported with its interval and the prediction, with
no verdict. Detection, absence, safety and `search_failures` rules are not affected. Applied before freezing (section
9): T1-1 (power 0.03), T6-3 (0.68), T7-rate05-qF, -qS, -dF (0.42, 0.75, 0.07) and T7-off5-dF (0.23) became estimands
(E-T1, E-T6, E-T7); T4-6 was rewritten (U-4's equivalence assumed a lossless queue; under QoS 0 the theory predicts a
gain, `at_least 5`, power 1.00); every rule kept has power >= 0.89 (section 5).

The five safety counts are bound violations, duplicate effects, logical duplicates, false STOPPED and false
APPLIED, as in every plan. Block: S = ```pairs-spec``` (pairs_report.py), X = ```pairs-ext``` (pairs_ext083.py).

| ID | Block, family | Hypothesis | Decision rule |
|---|---|---|---|
| **T1-3** | X, trigger-k2 | On K2, QueryOnly with the window-full trigger is equivalent to EviCon | `equivalent 3`, run Wk, K2, M1W -> EVICON, `clean_pp` |
| **T1-2** | X, trigger-tb | On ThingsBoard the trigger explains the gap: M1W gains over M1 | `at_least 10`, run TBw, TB_RPC, M1 -> M1W, `clean_pp` |
| **T1-4** | X, trigger-tb | ... and M1W is equivalent to EviCon (no remaining gap) | `equivalent 5`, run TBw, TB_RPC, M1W -> EVICON, `clean_pp` |
| **X-S1** | S, - | Safety in every cell of Wk, Z6 and Zi (EVICON, M1, M1W; the plain firmware at QoS 0) | `safety` (pairs_report): the five counts 0 in every VALID cell; late effects reported separately |
| **X-S2** | X, - | Safety in every cell of the ThingsBoard runs TBw, TBw3, TBw16, TBwinf (EVICON, M1, M1W) | `safety` over the runs that were run |
| **RA-1** | S, - | RELEASE_ON_READ_ANSWERED violates safety on the queue channel (a read answered while a request is held) | `detects`, run RAq, K2/RELEASE_ON_READ_ANSWERED, any of the five safety counts |
| **RA-2** | S, - | On the direct channel with a delay bound below the settle time the answered read is evidence, also with crash orphans (the owner keeps its records) | `absent`, run RAd, K2/RELEASE_ON_READ_ANSWERED, the five safety counts |
| **RA-3** | S, - | ... and on the owner without crash orphans (the paper's remark on state reads) | `absent`, run RAo, K2/RELEASE_ON_READ_ANSWERED |
| **RA-S1 to RA-S3** | S, - | EVICON in the RA runs is safe | `absent`, runs RAq, RAd, RAo, K2/EVICON |
| **RTs-1 to RTs-3** | S, - | RELEASE_ON_TIMEOUT violates safety on the queue channel at every release time | `detects`, run RTsq, K0/RELEASE_ON_TIMEOUT_30S, _60S, _120S |
| **RTs-4 to RTs-6** | S, - | ... and on the direct channel | `detects`, run RTsd, K0/RELEASE_ON_TIMEOUT_30S, _60S, _120S |
| **T4-S1 to T4-S4** | S, - | The plain firmware under the binding rule (QoS 0) is safe on both channels at W = 6 and unbounded | `absent`, runs Z6 and Zi, ZEPHYR_PLAIN_QOS0 and ZEPHYR_PLAIN_DIRECT_QOS0 / EVICON |
| **T4-1** | X, binding | Against the QoS 0 plain baseline the cutoff still raises clean fulfilment at W = 6 (C2-3's margin) | `at_least 10`, Z6 ZEPHYR_PLAIN_QOS0 -> C2 ZEPHYR_CUTOFF, `clean_pp` |
| **T4-2** | X, binding | ... and confirmed fulfilment (C2-4) | `at_least 10`, the same arms, `confirmed_pp` |
| **T4-3** | X, binding | {F} over the QoS 0 plain firmware on the direct channel at W = 6 (C3-1) | `at_least 10`, Z6 ZEPHYR_PLAIN_DIRECT_QOS0 -> C3 ZEPHYR_F_DIRECT, `clean_pp` |
| **T4-4** | X, binding | ... at W unbounded (U-2) | `at_least 5`, Zi ZEPHYR_PLAIN_DIRECT_QOS0 -> Rz ZEPHYR_F_DIRECT, `clean_pp` |
| **T4-5** | X, binding | The cutoff raises confirmed fulfilment on the queue channel at W unbounded (U-3) | `at_least 5`, Zi ZEPHYR_PLAIN_QOS0 -> Rz ZEPHYR_CUTOFF, `confirmed_pp` |
| **T4-6** | X, binding | ... and so does clean fulfilment: under QoS 0 the queue channel loses what it held, so the cutoff gains at W unbounded as on the direct channel (replaces U-4's equivalence, which assumed a lossless queue) | `at_least 5`, Zi ZEPHYR_PLAIN_QOS0 -> Rz ZEPHYR_CUTOFF, `clean_pp` |
| **T5-1, T5-2** | X, greedy | The greedy certificate costs no clean fulfilment against the enumeration at W unbounded (non-inferiority, margin 2 pp) | `at_least -2`, run Gs, K0F and K0S, EVICON -> EVICON_GREEDY, `clean_pp` |
| **T5-3** | X, greedy | ... nor on MinIO's conditional PUT | `at_least -2`, Rm MINIO_COND/EVICON -> Gm MINIO_COND/EVICON_GREEDY, `clean_pp` |
| **T5-SF1 to T5-SF4** | X, - | The greedy certificate never fails | `absent`, runs Gs (K0F, K0S) and Gm (MINIO_PLAIN, MINIO_COND), EVICON_GREEDY, `search_failures` |
| **T5-4** | X, theta | A fence cost of 100 (theta 20 times the default) changes clean fulfilment by less than 5 pp | `equivalent 5`, run Th, K0F, EVICON -> EVICON_FC100, `clean_pp` |
| **X-S3** | X, - | Safety in every cell of Gs, Gm, Th | `safety` over the runs that were run |
| **T6-1** | X, window | On the firmware the cutoff's effect does not grow from W = 3 to W = 32 | `at_most 0` on the per-episode difference (Z32w: plain -> cutoff) - (Z3w: plain -> cutoff), `clean_pp` |
| **T6-2** | X, window | ... nor from W = 6 to W unbounded (QoS 0 plain baseline) | `at_most 0` on (Zi plain QoS 0 -> Rz cutoff) - (Z6 plain QoS 0 -> C2 cutoff), `clean_pp` |
| **X-S4** | X, - | Safety in every cell of Z3w, Z16w, Z32w | `safety` over the runs that were run |
| **T7-<level>-qF** | S, t7-<level> | At each level the fence raises clean fulfilment on the queue channel at W = 6 | `at_least 0`, run T7q-<level>, EVICON, K0 -> K0F, `clean_pp` |
| **T7-<level>-qS** | S, t7-<level> | ... and so does the stop | `at_least 0`, run T7q-<level>, K0 -> K0S |
| **T7-<level>-dF** | S, t7-<level> | ... and the fence on the direct channel at W unbounded | `at_least 0`, run T7d-<level>, K0 -> K0F |
| **X-S5** | X, - | Safety in every cell of the twelve T7 runs | `safety` over the runs that were run |

`<level>`: qF, qS and dF at rate4, off40, seedA and seedB, qF and qS at off5: 14 hypotheses in five families (t7-rate4,
t7-off5, t7-off40, t7-seedA, t7-seedB), Holm inside each level's family. The effects at rate05 and the direct effect at
off5 are estimands (E-T7). In all, 54 rules: 29 paired-difference hypotheses, 20 detection, absence and
`search_failures` rules, and 5 safety rules (before the 0.80 rule: 35, 20 and 5, 60 rules). The safety
rules of the X block (X-S2 to X-S5) are pairs_report's rule applied to the runs of the group that were run; a run not
run is listed (section 6), and the rule is NOT RUN only when none of its runs was run.

Interpretation fixed in advance:
- **T1-2 MET and T1-4 MET**: on ThingsBoard the gap between QueryOnly and EviCon is the window-full trigger; the paper
  says so and reports M1W as the stronger query-only baseline. T1-4 NOT MET with EVICON above M1W: something besides
  the trigger (the sweep, the certificate's targets) also separates them; with M1W above EVICON: EviCon's other
  queries cost fulfilment. T1-2 NOT MET: the trigger does not explain the gap; reported with E-T1.
- **T1-3 on K2**: the simulator predicts no gap on K2 (resends under D_h resolve most ambiguous commands without a
  query): T1-3 MET. NOT MET: reported with E-T1. Whether the trigger adds anything to M1 on K2 (M1 -> M1W, predicted
  +0.4 pp) is an estimand of E-T1.
- **RA-1 DETECTED, RA-2 and RA-3 ABSENT**: a state read is evidence of non-occurrence exactly when no request can still
  arrive and the owner keeps its records; a client that releases on an answered NOT_FOUND is unsafe on a channel that
  holds requests and safe on one with a delay bound below its settle time. RA-1 NOT DETECTED: no read was answered
  while a request was held in this run (reported with the releases, E-T2). RA-2 or RA-3 PRESENT: an answered read was
  not evidence on the direct channel; reported with the cells and traces, and the paper's remark is withdrawn.
- **RTs-1 to RTs-6 DETECTED**: a longer release time does not make a release without evidence safe; with E-T3 the paper
  gives the violations per episode and the clean fulfilment as functions of the release time. NOT DETECTED at some
  release time: no released command took effect in that run; reported with E-T3 (the simulator predicts detection
  with probability >= 0.92 everywhere).
- **T4-S1 to T4-S4 ABSENT**: the binding rule makes the plain firmware safe; PRESENT: a safety failure; reported and the
  plan of record stops until the cause is known.
- **T4-1 to T4-6 MET**: the firmware's main effects do not depend on the plain baseline's at-least-once binding; the
  paper reports them against the QoS 0 baseline, and at W unbounded the cutoff adds clean fulfilment on the queue
  channel too (T4-6), because at most once makes the queue lose what it held. T4-6 NOT MET with an estimate near 0: the
  queue still delivered what it held (e.g. no reconnect lost a session), and U-4's equivalence carries over.
- **T5-1 to T5-3 MET and T5-SF1 to T5-SF4 ABSENT**: the greedy certificate removes the search failures at no cost; the
  paper describes it as the release's algorithm for ADD domains. A search failure under greedy: a bug (it never gives
  up); reported and the plan stops. T5-1 to T5-3 NOT MET with a negative estimate: the targets greedy names act
  differently from the enumeration's; reported with E-T5.
- **T5-4 MET**: the threshold rule's clean fulfilment does not depend on theta within a factor of 20, as the simulator
  predicts; with E-T5 (fences, stranded capacity-time) the paper reports what theta changes. NOT MET: theta matters
  on the testbed; the paper says so.
- **T6-1, T6-2 MET**: on the firmware the effect of backward evidence falls as the window grows (it never grows); with
  E-T6 the paper gives it as a function of W. NOT MET with the estimate near 0: the effect does not depend on W in
  this range. On ThingsBoard the gap between EviCon and M1W as a function of W is an estimand (E-T6).
- **T7 families**: a level whose rules are MET shows the effects of backward evidence there; NOT MET with a positive
  estimate means too small an effect for four episodes, not a reversal; a significant negative estimate (the 95 %
  interval below 0) is reported as a reversal at that level. The estimands of E-T7 give the dose-response: the effect
  sizes at every level, next to the simulator's, including the levels and channels with no hypothesis (0.5/min on both
  channels, 5-15 s on the direct channel), which are reported through E-T7 only.
- **X-S1 to X-S5, RA-S1 to RA-S3 PRESENT anywhere**: a safety failure of EviCon (or of M1, M1W); reported with the cells
  and the traces, and the plan of record stops until the cause is known.

## 4. Estimands (reported with intervals, no claims)

| ID | Estimand |
|---|---|
| E-T1 | Within Wk and TBw: M1 -> EVICON and M1 -> M1W (clean, confirmed, admitted, window-bound defers, queries, unresolved at the end; Wk M1 -> M1W clean was T1-1, prediction +0.4 pp); TB -> TBw for EVICON and M1 (the same schedules: a replication of TB-E1); Wq -> Wk, K0 -> K2 and K0F -> K2 (EVICON: what Q_A with D_h does against no evidence and against a fence on the same schedules); policy comparisons T1-E1 to T1-E4 |
| E-T2 | Within RAq, RAd, RAo: EVICON -> RELEASE_ON_READ_ANSWERED (clean, admitted, violations, releases without evidence, unresolved at the end); RRq -> RAq and RRd -> RAd for the 077 baseline against the 083 one; RAd -> RAo (what orphans cost the baseline); the releases by the read's outcome (`policy_compare.py`); T2-E1 to T2-E5 |
| E-T3 | For each channel and release time 10 (RTq, RTd), 30, 60, 120 s (RTsq, RTsd): violations per episode, clean and admitted fulfilment, against EVICON (Wq, Wd) and against the 10 s baseline on the same schedules; T3-E1 to T3-E12 |
| E-T4 | C2 -> Z6 (ZEPHYR_PLAIN QoS 1 -> QoS 0, queue), C3 -> Z6 (ZEPHYR_PLAIN_DIRECT -> _QOS0) and Rz -> Zi (both channels): what the binding rule costs the plain firmware (clean, confirmed, admitted, window-bound defers); Z6 QoS 0 plain -> C3 ZEPHYR_F_QUEUE and ZEPHYR_QS_QUEUE; within Z6 and Zi, queue -> direct |
| E-T5 | Within Gs: search failures, fences, stops, queries, confirmed and admitted fulfilment, enumeration -> greedy; Si -> Gs for EVICON (replication); Rm -> Gm per contract (clean, search failures, stops); within Th: confirmed, fences, stranded capacity-time, mean time to a terminal outcome; Wq -> Th (EVICON, K0F replication); the simulator's gamma sweep (section 1(h)) |
| E-T6 | The firmware's effect plain (QoS 0) -> cutoff at W = 3, 6 (Z6 -> C2), 16, 32, unbounded (Zi -> Rz): clean, confirmed, admitted, window-bound defers; the difference W = 16 minus W = 3; ThingsBoard's M1W -> EVICON at W = 3, 6 (TBw), 16, unbounded (clean, confirmed, queries) and the differences W = 16 minus W = 3 and W unbounded minus W = 3 (was T6-3, prediction +0.2 pp); next to MinIO's C / C16 / Rm |
| E-T7 | Per level and channel: K0 -> K0F (and K0 -> K0S, K0F -> K0S on the queue channel): clean (at 0.5/min queue fence and stop, direct fence, and 5-15 s direct fence the estimands that were T7-rate05-qF, -qS, -dF and T7-off5-dF, predictions +17.7, +17.8, +4.6, +7.0 pp), confirmed, admitted, window-bound defers, fences reached and completed, stops reached and terminal, stranded censored capacity-time (direct); the levels of K0, K0F, K0S next to S's and Wdi's (another fault schedule: levels only) and next to the simulator's prediction of section 1(h) |
| E-P | Policy comparisons (`policy_compare.py`, section 8): per-episode differences of clean and admitted fulfilment and the safety counts, EVICON against M1, M1W and each baseline on the same schedules |
| E-SIM | Levels per run and the tested effects against the simulator's prediction of section 1(h) (`evaluation/esim083.py`, from the decided results) |

The report also runs three analyses of the raw traces of every run it reads (release 083; no claims, not part of this
plan's decisions): **A1** (`safety_total.py`) counts the distinct fault schedules behind the guarded VALID cells with
at least one fault window, N_s (cells of one episode share a schedule, and several campaigns replay the same seeded
schedules), and gives the exact one-sided 95 % bound per schedule, 1 - 0.05^(1/N_s), next to the per-cell bound;
**A2** (`overhead.py`) the share of admitted requests whose admission came after their deadline, per experiment and
policy; **A3** (`t3_attribution.py`) lists every window re-opening without committed evidence in a guarded cell with
the trace events between its DEFER and its ADMITTED and a factual explanation.

## 5. Sample size

**Under the simulator's paired differences** (resampling the 200 episodes, Holm within each family as frozen, 2,000
resamples; `SIMULATION_ONLY`; `sim_predictions/extra083_power.csv`; the estimands' simulator means are in
`extra083_effects.csv`):

| ID | Simulator mean (SD), pp | n | Power |
|---|---|---|---|
| T1-3 | +0.00 (0.04) | 8 | 1.00 |
| T1-2 | +35.77 (25.32) | 6 | 0.62 |
| T1-4 | -0.74 (5.49) | 6 | 0.87 |
| T4-1 | +43.02 (25.40) | 8 | 0.76 |
| T4-2 | +43.80 (24.96), confirmed | 8 | 0.81 |
| T4-3 | +43.09 (25.46) | 8 | 0.79 |
| T4-4 | +14.60 (11.71) | 8 | 0.43 |
| T4-5 | +16.01 (11.39), confirmed | 8 | 0.58 |
| T4-6 | +14.53 (11.56) | 8 | 0.44 |
| T5-1, T5-2, T5-3 | +0.00 (0.00), +0.00 (0.04), +0.01 (0.07) | 8 | 1.00 |
| T5-4 | -1.78 (6.53) | 8 | 0.63 |
| T6-1 | -32.27 (21.67) | 8 | 1.00 |
| T6-2 | -28.49 (20.20) | 8 | 1.00 |
| T7 queue K0 -> K0F (qF) | 4/min +47.1 (23.0), 5-15 s +33.2 (23.7), 40-120 s +41.1 (25.6), seeds +40.1 (26.3) | 4 | 0.88 / 0.50 / 0.49 / 0.44 |
| T7 queue K0 -> K0S (qS) | +58.9 (15.9), +37.1 (23.3), +41.7 (24.6), +45.0 (24.7) | 4 | 0.99 / 0.56 / 0.52 / 0.54 |
| T7 direct K0 -> K0F (dF) | 4/min +23.7 (10.1), 40-120 s +26.9 (16.1), seeds +14.7 (10.1) | 4 | 0.96 / 0.56 / 0.46 |
| estimands (no verdict) | Wk M1 -> M1W +0.44 (4.35); TB gap W unbounded - W 3 +0.23 (3.08); 0.5/min qF +17.7 (24.8), qS +17.8 (24.8), dF +4.6 (6.1); 5-15 s dF +7.0 (5.0) | | - |

The simulator's per-episode differences are bimodal and far more spread than the testbed's (C2: SD 11.3 pp against
the simulator's 25.4), so these are lower bounds. **Under the testbed's own spread**
(`evaluation/power_extra083_testbed.py`: per-episode differences of the closest earlier testbed runs, centred and
shifted to a true mean, the other members of the family at the simulator's means, Holm, 4,000 resamples; the
templates are named in the script). The power at the simulator's mean decides by the 0.80 rule of section 3:

| Rule | Template (SD, pp) | True mean -> power |
|---|---|---|
| T1-3 (`equivalent 3`, n 8) | Wqi K0F RES_PERIODIC -> EVICON (1.2); three times that | 0 (simulator): 1.00, 1: 0.99, 2: 0.69; x3: 0.62 at 0 |
| T1-2 (`at_least 10`, n 6) | TB M1 -> EVICON (12.5) | 15: 0.21, 20: 0.47, 35.8 (simulator): 1.00 |
| T1-4 (`equivalent 5`, n 6) | TB NAIVE -> EVICON (3.6); twice that | -0.74 (simulator): 0.89, 0: 0.94, 1: 0.80, 2: 0.59, 3: 0.31; x2: 0.07 at -0.74 |
| T4-1, T4-2 (`at_least 10`) | C2 plain -> cutoff, clean (11.3) and confirmed (11.8) | 15: 0.30, 20: 0.71-0.75, 43 (simulator): 1.00 |
| T4-3 (`at_least 10`) | C3 plain direct -> {F} (9.5) | 15: 0.35, 20: 0.96, 43.1: 1.00 |
| T4-4 (`at_least 5`) | Rz plain direct -> {F} (7.6) | 8: 0.22, 10: 0.52, 14.6: 1.00 |
| T4-5 (`at_least 5`, confirmed) | Rz plain direct -> cutoff, confirmed (7.2) | 8: 0.25, 10: 0.56, 16.0: 1.00 |
| T4-6 (`at_least 5`) | Rz plain direct -> cutoff, clean (7.4) | 8: 0.23, 10: 0.55, 14.5 (simulator): 1.00 |
| T5-1 to T5-3 (`at_least -2`) | Wqi K0F RES_PERIODIC -> EVICON (1.2); three times that | 0: 1.00, -1: 0.93; x3: 0.39 at 0, 0.93 at +1 |
| T5-4 (`equivalent 5`) | as T5-1 | -1.78 (simulator): 1.00; x3: 0.97 |
| T6-1, T6-2 (`at_most 0`) | per episode (C2 cutoff - C3 plain direct) - (Rz cutoff - Rz plain direct) (3.6) | -5: 1.00, -28 (simulator): 1.00 |
| T7 queue fence / stop, direct fence (`at_least 0`, n 4) | S K0 -> K0F (12.0), K0 -> K0S (9.3); Wdi K0 -> K0F (9.3) | at the simulator's means: 4/min 1.00 / 1.00 / 1.00; 5-15 s 1.00 / 1.00; 40-120 s 1.00 / 1.00 / 1.00; seeds 1.00 / 1.00 / 0.91. At 10 pp: 0.23-0.47 (queue), 0.45 (direct) |

The 0.80 rule applied to the plan as first drafted (the script's second table; power at the simulator's mean, Holm in
the draft's families):

| Rule as drafted | Predicted mean | Power | Decision |
|---|---|---|---|
| T1-1 (`at_least 0`, Wk M1 -> M1W) | +0.44 | 0.03 | estimand (E-T1) |
| T4-6 (`equivalent 3`, U-4) | +14.53 | 0.00 | rewritten as `at_least 5` (power 1.00): hypothesis |
| T6-3 (`at_most 5`, TB gap W unbounded - W 3) | +0.23 | 0.68 | estimand (E-T6) |
| T7-rate05-qF, -qS, -dF | +17.65, +17.80, +4.58 | 0.42, 0.75, 0.07 | estimands (E-T7) |
| T7-off5-dF | +6.99 | 0.23 | estimand (E-T7) |
| every other paired-difference rule | | 0.89-1.00 | hypothesis (lowest: T1-4 0.89, T7-seedA-dF and T7-seedB-dF 0.91) |

Eight episodes, as the runs Wk, Z6, Zi, Gs, Gm, Th and the firmware window runs replay; six for the ThingsBoard runs
(TB's episodes); five for the RA and RTs runs (detection and absence rules, which need no power; RR's episodes);
four for each T7 level and channel (120 cells, 14.4 h: the brief's budget of about 16 h; at the mildest level the
power is below 0.80 and its effects are estimands, section 3). The detection rules need one detecting cell (section 1(c): probability >=
0.92 in 5 episodes for RTs).

## 6. Exclusions, stopping and order

- INVALID cells are excluded and listed (a fault could not be applied, a process died, a firmware process exited, a
  ThingsBoard device could not be made, the truth could not be read). An INVALID cell is re-run once by resuming the
  campaign; if it is INVALID again it stays excluded. A run with more than 10 % excluded cells is not interpretable and
  its hypotheses are not decided.
- No interim analysis: `pairs_ext083.py` and `policy_compare.py` run only when every cell of a run is done. A campaign
  may be split over several sessions (resume).
- **Order by priority.** The campaigns run in seven sessions of one review run of release 083, in the order of the
  review's priorities, so that if testbed time runs short the most important data exist first: priority 1, T4 (Z6, Zi)
  and T1 (Wk, TBw); priority 2, T2 (RAq, RAd, RAo) and T3 (RTsq, RTsd); priority 3, T5 (Th, Gm, Gs); priority 4, T6
  (Z3w, Z16w, Z32w, TBw3, TBw16, TBwinf); priority 5, T7 (rate05, rate4, off5, off40, seedA, seedB, queue then direct).
  N1 `preflight build verify distribute --new`, `measure zephyr-plain zephyr`, `campaign expT4_zephyr_w6`,
  `expT4_zephyr_winf`, `expM1W_k2`; N2 `measure thingsboard`, `campaign expTB_m1w`, `expRRA_queue`, `expRRA_direct`,
  `expRRA_noorphan`; N3 `expRTs_queue`, `expRTs_direct`, `expTheta_w6`, `expG_minio_winf`; N4 `expG_s_winf`,
  `expT6_zephyr_w3`, `expT6_zephyr_w16`; N5 `expT6_zephyr_w32`, `expTB_w3`, `expTB_w16`, `expTB_winf`,
  `expT7_q6_rate05`; N6 `expT7_dinf_rate05`, `expT7_q6_rate4`, `expT7_dinf_rate4`, `expT7_q6_off5`, `expT7_dinf_off5`,
  `expT7_q6_off40`, `expT7_dinf_off40`; N7 `expT7_q6_seedA`, `expT7_dinf_seedA`, `expT7_q6_seedB`, `expT7_dinf_seedB`,
  `report`. Each session ends with `bundle --incremental`, and each is self-contained: its campaigns complete within it
  (a session cut short is resumed before the next session's campaigns start). A measurement precedes the runs on its
  contract; no firmware build after N1's measurement (a new build makes the firmware runs refuse to start: measured
  binary != run binary); the ThingsBoard runs use only the contract N2 measured in the same review run.
- **Not run.** The data freeze is the final `report` of this plan (N7, or the last session the testbed allows). A
  campaign that has not completed by then is reported as NOT RUN: its hypotheses are reported NOT RUN, its estimands
  are left out, and no cell of it enters any analysis (the report reads complete runs only). A safety rule of the
  ```pairs-ext``` block (X-S2 to X-S5) is decided on the runs that completed and lists the others as not run; X-S1,
  decided by `pairs_report.py`, is NOT RUN unless all of Wk, Z6 and Zi completed (all three run in N1). No partial
  results.
- The earliest complete run of each config is the preregistered one.

## 7. Frozen files

SHA-256 at freezing time (`scripts/freeze_prereg.py`); the driver checks them before running. Every file frozen by
`PREREG_review_077.md` is frozen here again with the same SHA-256 (none changed in releases 078-083), through the
```frozen-extra``` block; added: this plan's analysis (`pairs_ext083.py`), the 083 runners and their policy module
(`ext083.py`, `campaign_ext083.py`, `campaign_real_ext083.py`, `campaign_tb_ext083.py`), the simulator prediction of
section 1(h) with its script and the ThingsBoard contract of the twin, and the testbed power script of section 5.

The Go code is built from release 083 (the run's `source.json` records its source). Since release 082 the Go code
changed in these places, each off unless set: the policy M1W (a branch of the authority that the other policies never
take; test F49); the policy RELEASE_ON_READ_ANSWERED with `-release-settle` and its resend cut (a branch of the
baselines' sweep; test F50); the greedy certificate (`-certificate greedy`, default `enumerate`; test F51 and the
property tests of `internal/probe`); the device's `-shadow-reads`; the simulator's QUERY_ONLY, QUERY_ONLY_W policies
and its `certificate` option (the output of every earlier scenario is unchanged, byte for byte); and
`buildinfo.Release`.

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
```

## 8. Analysis spec (read by pairs_report.py, pairs_ext083.py and policy_compare.py)

```pairs-spec
{
  "name": "The second set of review experiments of release 083: QueryOnly with the window-full trigger, release on an answered read, the release time of TimeoutRelease, the plain firmware under the at-most-once binding, the greedy certificate and theta, the window on the firmware and ThingsBoard, the fault regime and seeds",
  "runs": {
    "Wk": {"campaign": "expM1W-k2"},
    "TBw": {"campaign": "expTB-m1w"},
    "RAq": {"campaign": "expRRA-queue"},
    "RAd": {"campaign": "expRRA-direct"},
    "RAo": {"campaign": "expRRA-noorphan"},
    "RTsq": {"campaign": "expRTs-queue"},
    "RTsd": {"campaign": "expRTs-direct"},
    "Z6": {"campaign": "expT4-zephyr-w6"},
    "Zi": {"campaign": "expT4-zephyr-winf"},
    "Gs": {"campaign": "expG-s-winf"},
    "Gm": {"campaign": "expG-minio-winf"},
    "Th": {"campaign": "expTheta-w6"},
    "Z3w": {"campaign": "expT6-zephyr-w3"},
    "Z16w": {"campaign": "expT6-zephyr-w16"},
    "Z32w": {"campaign": "expT6-zephyr-w32"},
    "TBw3": {"campaign": "expTB-w3"},
    "TBw16": {"campaign": "expTB-w16"},
    "TBwinf": {"campaign": "expTB-winf"},
    "T7q-rate05": {"campaign": "expT7-q6-rate05"},
    "T7d-rate05": {"campaign": "expT7-dinf-rate05"},
    "T7q-rate4": {"campaign": "expT7-q6-rate4"},
    "T7d-rate4": {"campaign": "expT7-dinf-rate4"},
    "T7q-off5": {"campaign": "expT7-q6-off5"},
    "T7d-off5": {"campaign": "expT7-dinf-off5"},
    "T7q-off40": {"campaign": "expT7-q6-off40"},
    "T7d-off40": {"campaign": "expT7-dinf-off40"},
    "T7q-seedA": {"campaign": "expT7-q6-seedA"},
    "T7d-seedA": {"campaign": "expT7-dinf-seedA"},
    "T7q-seedB": {"campaign": "expT7-q6-seedB"},
    "T7d-seedB": {"campaign": "expT7-dinf-seedB"},
    "C2": {"campaign": "expC2-zephyr", "reference": true},
    "C3": {"campaign": "expC3-ablation", "reference": true},
    "Rz": {"campaign": "expR-zephyr-winf", "reference": true},
    "Rm": {"campaign": "expR-minio-winf", "reference": true},
    "TB": {"campaign": "expTB-thingsboard", "reference": true},
    "Wq": {"campaign": "expW-queue-w6", "reference": true},
    "Wd": {"campaign": "expW-direct-w6", "reference": true},
    "Wdi": {"campaign": "expW-direct-winf", "reference": true},
    "S": {"campaign": "expS-w6", "reference": true},
    "Si": {"campaign": "expS-winf", "reference": true},
    "RTq": {"campaign": "expRT-queue", "reference": true},
    "RTd": {"campaign": "expRT-direct", "reference": true},
    "RRq": {"campaign": "expRR-queue", "reference": true},
    "RRd": {"campaign": "expRR-direct", "reference": true}
  },
  "alpha": 0.05,
  "max_invalid_fraction": 0.1,
  "hypotheses": [
    {"id": "RA-1", "run": "RAq", "test": "detects", "contract": "K2", "policy": "RELEASE_ON_READ_ANSWERED", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "RA-2", "run": "RAd", "test": "absent", "contract": "K2", "policy": "RELEASE_ON_READ_ANSWERED", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "RA-3", "run": "RAo", "test": "absent", "contract": "K2", "policy": "RELEASE_ON_READ_ANSWERED", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "RA-S1", "run": "RAq", "test": "absent", "contract": "K2", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "RA-S2", "run": "RAd", "test": "absent", "contract": "K2", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "RA-S3", "run": "RAo", "test": "absent", "contract": "K2", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "RTs-1", "run": "RTsq", "test": "detects", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT_30S", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "RTs-2", "run": "RTsq", "test": "detects", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT_60S", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "RTs-3", "run": "RTsq", "test": "detects", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT_120S", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "RTs-4", "run": "RTsd", "test": "detects", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT_30S", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "RTs-5", "run": "RTsd", "test": "detects", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT_60S", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "RTs-6", "run": "RTsd", "test": "detects", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT_120S", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "T4-S1", "run": "Z6", "test": "absent", "contract": "ZEPHYR_PLAIN_QOS0", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "T4-S2", "run": "Z6", "test": "absent", "contract": "ZEPHYR_PLAIN_DIRECT_QOS0", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "T4-S3", "run": "Zi", "test": "absent", "contract": "ZEPHYR_PLAIN_QOS0", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "T4-S4", "run": "Zi", "test": "absent", "contract": "ZEPHYR_PLAIN_DIRECT_QOS0", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "T7-rate4-qF", "family": "t7-rate4", "run": "T7q-rate4", "policy": "EVICON", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "at_least", "margin": 0.0},
    {"id": "T7-rate4-qS", "family": "t7-rate4", "run": "T7q-rate4", "policy": "EVICON", "from": "K0", "to": "K0S", "metric": "clean_pp", "test": "at_least", "margin": 0.0},
    {"id": "T7-rate4-dF", "family": "t7-rate4", "run": "T7d-rate4", "policy": "EVICON", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "at_least", "margin": 0.0},
    {"id": "T7-off5-qF", "family": "t7-off5", "run": "T7q-off5", "policy": "EVICON", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "at_least", "margin": 0.0},
    {"id": "T7-off5-qS", "family": "t7-off5", "run": "T7q-off5", "policy": "EVICON", "from": "K0", "to": "K0S", "metric": "clean_pp", "test": "at_least", "margin": 0.0},
    {"id": "T7-off40-qF", "family": "t7-off40", "run": "T7q-off40", "policy": "EVICON", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "at_least", "margin": 0.0},
    {"id": "T7-off40-qS", "family": "t7-off40", "run": "T7q-off40", "policy": "EVICON", "from": "K0", "to": "K0S", "metric": "clean_pp", "test": "at_least", "margin": 0.0},
    {"id": "T7-off40-dF", "family": "t7-off40", "run": "T7d-off40", "policy": "EVICON", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "at_least", "margin": 0.0},
    {"id": "T7-seedA-qF", "family": "t7-seedA", "run": "T7q-seedA", "policy": "EVICON", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "at_least", "margin": 0.0},
    {"id": "T7-seedA-qS", "family": "t7-seedA", "run": "T7q-seedA", "policy": "EVICON", "from": "K0", "to": "K0S", "metric": "clean_pp", "test": "at_least", "margin": 0.0},
    {"id": "T7-seedA-dF", "family": "t7-seedA", "run": "T7d-seedA", "policy": "EVICON", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "at_least", "margin": 0.0},
    {"id": "T7-seedB-qF", "family": "t7-seedB", "run": "T7q-seedB", "policy": "EVICON", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "at_least", "margin": 0.0},
    {"id": "T7-seedB-qS", "family": "t7-seedB", "run": "T7q-seedB", "policy": "EVICON", "from": "K0", "to": "K0S", "metric": "clean_pp", "test": "at_least", "margin": 0.0},
    {"id": "T7-seedB-dF", "family": "t7-seedB", "run": "T7d-seedB", "policy": "EVICON", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "at_least", "margin": 0.0}
  ],
  "safety": {"id": "X-S1", "runs": ["Wk", "Z6", "Zi"], "exempt_contracts": []},
  "estimands": [
    {"run": "T7q-rate05", "policy": "EVICON", "from": "K0", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers", "fences_reached_ok"]},
    {"run": "T7q-rate05", "policy": "EVICON", "from": "K0", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers", "stops_reached_terminal"]},
    {"run": "T7q-rate05", "policy": "EVICON", "from": "K0F", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "T7d-rate05", "policy": "EVICON", "from": "K0", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "stranded_censored_unit_sec"]},
    {"run": "T7q-rate4", "policy": "EVICON", "from": "K0", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers", "fences_reached_ok"]},
    {"run": "T7q-rate4", "policy": "EVICON", "from": "K0", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers", "stops_reached_terminal"]},
    {"run": "T7q-rate4", "policy": "EVICON", "from": "K0F", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "T7d-rate4", "policy": "EVICON", "from": "K0", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "stranded_censored_unit_sec"]},
    {"run": "T7q-off5", "policy": "EVICON", "from": "K0", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers", "fences_reached_ok"]},
    {"run": "T7q-off5", "policy": "EVICON", "from": "K0", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers", "stops_reached_terminal"]},
    {"run": "T7q-off5", "policy": "EVICON", "from": "K0F", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "T7d-off5", "policy": "EVICON", "from": "K0", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "stranded_censored_unit_sec"]},
    {"run": "T7q-off40", "policy": "EVICON", "from": "K0", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers", "fences_reached_ok"]},
    {"run": "T7q-off40", "policy": "EVICON", "from": "K0", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers", "stops_reached_terminal"]},
    {"run": "T7q-off40", "policy": "EVICON", "from": "K0F", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "T7d-off40", "policy": "EVICON", "from": "K0", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "stranded_censored_unit_sec"]},
    {"run": "T7q-seedA", "policy": "EVICON", "from": "K0", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers", "fences_reached_ok"]},
    {"run": "T7q-seedA", "policy": "EVICON", "from": "K0", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers", "stops_reached_terminal"]},
    {"run": "T7q-seedA", "policy": "EVICON", "from": "K0F", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "T7d-seedA", "policy": "EVICON", "from": "K0", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "stranded_censored_unit_sec"]},
    {"run": "T7q-seedB", "policy": "EVICON", "from": "K0", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers", "fences_reached_ok"]},
    {"run": "T7q-seedB", "policy": "EVICON", "from": "K0", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers", "stops_reached_terminal"]},
    {"run": "T7q-seedB", "policy": "EVICON", "from": "K0F", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "T7d-seedB", "policy": "EVICON", "from": "K0", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "stranded_censored_unit_sec"]},
    {"run": "Z3w", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_CUTOFF", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers"]},
    {"run": "Z16w", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_CUTOFF", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers"]},
    {"run": "Z32w", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_CUTOFF", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers"]},
    {"run": "Wk", "from_run": "Wq", "policy": "EVICON", "from": "K0", "to": "K2", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers"]},
    {"run": "Wk", "from_run": "Wq", "policy": "EVICON", "from": "K0F", "to": "K2", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers"]},
    {"run": "Z6", "from_run": "C2", "policy": "EVICON", "from": "ZEPHYR_PLAIN", "to": "ZEPHYR_PLAIN_QOS0", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers"]},
    {"run": "Z6", "from_run": "C3", "policy": "EVICON", "from": "ZEPHYR_PLAIN_DIRECT", "to": "ZEPHYR_PLAIN_DIRECT_QOS0", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp"]},
    {"run": "Zi", "from_run": "Rz", "policy": "EVICON", "from": "ZEPHYR_PLAIN", "to": "ZEPHYR_PLAIN_QOS0", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp"]},
    {"run": "Zi", "from_run": "Rz", "policy": "EVICON", "from": "ZEPHYR_PLAIN_DIRECT", "to": "ZEPHYR_PLAIN_DIRECT_QOS0", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp"]},
    {"run": "C3", "from_run": "Z6", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_F_QUEUE", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "C3", "from_run": "Z6", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_QS_QUEUE", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "Z6", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_PLAIN_DIRECT_QOS0", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "Zi", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_PLAIN_DIRECT_QOS0", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "Z3w", "from_run": "Z6", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_PLAIN_QOS0", "metrics": ["clean_pp"]},
    {"run": "Z32w", "from_run": "Z6", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_PLAIN_QOS0", "metrics": ["clean_pp"]}
  ]
}
```

```pairs-ext
{
  "hypotheses": [
    {"id": "T1-3", "family": "trigger-k2", "run": "Wk", "contract": "K2", "from_policy": "M1W", "to_policy": "EVICON", "metric": "clean_pp", "test": "equivalent", "margin": 3.0},
    {"id": "T1-2", "family": "trigger-tb", "run": "TBw", "contract": "TB_RPC", "from_policy": "M1", "to_policy": "M1W", "metric": "clean_pp", "test": "at_least", "margin": 10.0},
    {"id": "T1-4", "family": "trigger-tb", "run": "TBw", "contract": "TB_RPC", "from_policy": "M1W", "to_policy": "EVICON", "metric": "clean_pp", "test": "equivalent", "margin": 5.0},
    {"id": "T4-1", "family": "binding", "run": "C2", "from_run": "Z6", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_CUTOFF", "metric": "clean_pp", "test": "at_least", "margin": 10.0},
    {"id": "T4-2", "family": "binding", "run": "C2", "from_run": "Z6", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_CUTOFF", "metric": "confirmed_pp", "test": "at_least", "margin": 10.0},
    {"id": "T4-3", "family": "binding", "run": "C3", "from_run": "Z6", "policy": "EVICON", "from": "ZEPHYR_PLAIN_DIRECT_QOS0", "to": "ZEPHYR_F_DIRECT", "metric": "clean_pp", "test": "at_least", "margin": 10.0},
    {"id": "T4-4", "family": "binding", "run": "Rz", "from_run": "Zi", "policy": "EVICON", "from": "ZEPHYR_PLAIN_DIRECT_QOS0", "to": "ZEPHYR_F_DIRECT", "metric": "clean_pp", "test": "at_least", "margin": 5.0},
    {"id": "T4-5", "family": "binding", "run": "Rz", "from_run": "Zi", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_CUTOFF", "metric": "confirmed_pp", "test": "at_least", "margin": 5.0},
    {"id": "T4-6", "family": "binding", "run": "Rz", "from_run": "Zi", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_CUTOFF", "metric": "clean_pp", "test": "at_least", "margin": 5.0},
    {"id": "T5-1", "family": "greedy", "run": "Gs", "contract": "K0F", "from_policy": "EVICON", "to_policy": "EVICON_GREEDY", "metric": "clean_pp", "test": "at_least", "margin": -2.0},
    {"id": "T5-2", "family": "greedy", "run": "Gs", "contract": "K0S", "from_policy": "EVICON", "to_policy": "EVICON_GREEDY", "metric": "clean_pp", "test": "at_least", "margin": -2.0},
    {"id": "T5-3", "family": "greedy", "run": "Gm", "from_run": "Rm", "contract": "MINIO_COND", "from_policy": "EVICON", "to_policy": "EVICON_GREEDY", "metric": "clean_pp", "test": "at_least", "margin": -2.0},
    {"id": "T5-SF1", "run": "Gs", "test": "absent", "contract": "K0F", "policy": "EVICON_GREEDY", "any_of": ["search_failures"]},
    {"id": "T5-SF2", "run": "Gs", "test": "absent", "contract": "K0S", "policy": "EVICON_GREEDY", "any_of": ["search_failures"]},
    {"id": "T5-SF3", "run": "Gm", "test": "absent", "contract": "MINIO_PLAIN", "policy": "EVICON_GREEDY", "any_of": ["search_failures"]},
    {"id": "T5-SF4", "run": "Gm", "test": "absent", "contract": "MINIO_COND", "policy": "EVICON_GREEDY", "any_of": ["search_failures"]},
    {"id": "T5-4", "family": "theta", "run": "Th", "contract": "K0F", "from_policy": "EVICON", "to_policy": "EVICON_FC100", "metric": "clean_pp", "test": "equivalent", "margin": 5.0},
    {"id": "T6-1", "family": "window", "test": "at_most", "margin": 0.0, "metric": "clean_pp", "a": {"run": "Z3w", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_CUTOFF"}, "b": {"run": "Z32w", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_CUTOFF"}},
    {"id": "T6-2", "family": "window", "test": "at_most", "margin": 0.0, "metric": "clean_pp", "a": {"run": "C2", "from_run": "Z6", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_CUTOFF"}, "b": {"run": "Rz", "from_run": "Zi", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_CUTOFF"}},
    {"id": "X-S2", "test": "safety", "runs": ["TBw", "TBw3", "TBw16", "TBwinf"]},
    {"id": "X-S3", "test": "safety", "runs": ["Gs", "Gm", "Th"]},
    {"id": "X-S4", "test": "safety", "runs": ["Z3w", "Z16w", "Z32w"]},
    {"id": "X-S5", "test": "safety", "runs": ["T7q-rate05", "T7d-rate05", "T7q-rate4", "T7d-rate4", "T7q-off5", "T7d-off5", "T7q-off40", "T7d-off40", "T7q-seedA", "T7d-seedA", "T7q-seedB", "T7d-seedB"]}
  ],
  "estimands": [
    {"run": "Wk", "contract": "K2", "from_policy": "M1", "to_policy": "EVICON", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers", "queries"]},
    {"run": "Wk", "contract": "K2", "from_policy": "M1", "to_policy": "M1W", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers", "queries"]},
    {"run": "TBw", "contract": "TB_RPC", "from_policy": "M1", "to_policy": "EVICON", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers", "queries", "unresolved_at_end"]},
    {"run": "TBw", "contract": "TB_RPC", "from_policy": "M1", "to_policy": "M1W", "metrics": ["confirmed_pp", "admitted_pp", "window_defers", "queries", "unresolved_at_end"]},
    {"run": "TBw", "from_run": "TB", "contract": "TB_RPC", "from_policy": "EVICON", "to_policy": "EVICON", "metrics": ["clean_pp"]},
    {"run": "TBw", "from_run": "TB", "contract": "TB_RPC", "from_policy": "M1", "to_policy": "M1", "metrics": ["clean_pp"]},
    {"run": "RAq", "contract": "K2", "from_policy": "EVICON", "to_policy": "RELEASE_ON_READ_ANSWERED", "metrics": ["clean_pp", "admitted_pp", "violations", "releases_without_evidence", "unresolved_at_end"]},
    {"run": "RAd", "contract": "K2", "from_policy": "EVICON", "to_policy": "RELEASE_ON_READ_ANSWERED", "metrics": ["clean_pp", "admitted_pp", "violations", "releases_without_evidence", "unresolved_at_end"]},
    {"run": "RAo", "contract": "K2", "from_policy": "EVICON", "to_policy": "RELEASE_ON_READ_ANSWERED", "metrics": ["clean_pp", "admitted_pp", "violations", "releases_without_evidence", "unresolved_at_end"]},
    {"run": "RAq", "from_run": "RRq", "contract": "K2", "from_policy": "RELEASE_ON_READ", "to_policy": "RELEASE_ON_READ_ANSWERED", "metrics": ["clean_pp", "violations"]},
    {"run": "RAd", "from_run": "RRd", "contract": "K2", "from_policy": "RELEASE_ON_READ", "to_policy": "RELEASE_ON_READ_ANSWERED", "metrics": ["clean_pp", "violations"]},
    {"run": "RAo", "from_run": "RAd", "contract": "K2", "from_policy": "RELEASE_ON_READ_ANSWERED", "to_policy": "RELEASE_ON_READ_ANSWERED", "metrics": ["clean_pp", "violations", "unresolved_at_end"]},
    {"run": "RTsq", "from_run": "RTq", "contract": "K0", "from_policy": "RELEASE_ON_TIMEOUT", "to_policy": "RELEASE_ON_TIMEOUT_30S", "metrics": ["clean_pp", "admitted_pp", "violations"]},
    {"run": "RTsq", "from_run": "Wq", "contract": "K0", "from_policy": "EVICON", "to_policy": "RELEASE_ON_TIMEOUT_30S", "metrics": ["clean_pp", "admitted_pp", "violations"]},
    {"run": "RTsq", "from_run": "RTq", "contract": "K0", "from_policy": "RELEASE_ON_TIMEOUT", "to_policy": "RELEASE_ON_TIMEOUT_60S", "metrics": ["clean_pp", "admitted_pp", "violations"]},
    {"run": "RTsq", "from_run": "Wq", "contract": "K0", "from_policy": "EVICON", "to_policy": "RELEASE_ON_TIMEOUT_60S", "metrics": ["clean_pp", "admitted_pp", "violations"]},
    {"run": "RTsq", "from_run": "RTq", "contract": "K0", "from_policy": "RELEASE_ON_TIMEOUT", "to_policy": "RELEASE_ON_TIMEOUT_120S", "metrics": ["clean_pp", "admitted_pp", "violations"]},
    {"run": "RTsq", "from_run": "Wq", "contract": "K0", "from_policy": "EVICON", "to_policy": "RELEASE_ON_TIMEOUT_120S", "metrics": ["clean_pp", "admitted_pp", "violations"]},
    {"run": "RTsd", "from_run": "RTd", "contract": "K0", "from_policy": "RELEASE_ON_TIMEOUT", "to_policy": "RELEASE_ON_TIMEOUT_30S", "metrics": ["clean_pp", "admitted_pp", "violations"]},
    {"run": "RTsd", "from_run": "Wd", "contract": "K0", "from_policy": "EVICON", "to_policy": "RELEASE_ON_TIMEOUT_30S", "metrics": ["clean_pp", "admitted_pp", "violations"]},
    {"run": "RTsd", "from_run": "RTd", "contract": "K0", "from_policy": "RELEASE_ON_TIMEOUT", "to_policy": "RELEASE_ON_TIMEOUT_60S", "metrics": ["clean_pp", "admitted_pp", "violations"]},
    {"run": "RTsd", "from_run": "Wd", "contract": "K0", "from_policy": "EVICON", "to_policy": "RELEASE_ON_TIMEOUT_60S", "metrics": ["clean_pp", "admitted_pp", "violations"]},
    {"run": "RTsd", "from_run": "RTd", "contract": "K0", "from_policy": "RELEASE_ON_TIMEOUT", "to_policy": "RELEASE_ON_TIMEOUT_120S", "metrics": ["clean_pp", "admitted_pp", "violations"]},
    {"run": "RTsd", "from_run": "Wd", "contract": "K0", "from_policy": "EVICON", "to_policy": "RELEASE_ON_TIMEOUT_120S", "metrics": ["clean_pp", "admitted_pp", "violations"]},
    {"run": "Gs", "contract": "K0F", "from_policy": "EVICON", "to_policy": "EVICON_GREEDY", "metrics": ["search_failures", "fences", "queries", "confirmed_pp", "admitted_pp"]},
    {"run": "Gs", "contract": "K0S", "from_policy": "EVICON", "to_policy": "EVICON_GREEDY", "metrics": ["search_failures", "stops", "queries", "confirmed_pp", "admitted_pp"]},
    {"run": "Gs", "from_run": "Si", "contract": "K0F", "from_policy": "EVICON", "to_policy": "EVICON", "metrics": ["clean_pp", "search_failures"]},
    {"run": "Gs", "from_run": "Si", "contract": "K0S", "from_policy": "EVICON", "to_policy": "EVICON", "metrics": ["clean_pp", "search_failures"]},
    {"run": "Gm", "from_run": "Rm", "contract": "MINIO_PLAIN", "from_policy": "EVICON", "to_policy": "EVICON_GREEDY", "metrics": ["clean_pp", "search_failures"]},
    {"run": "Gm", "from_run": "Rm", "contract": "MINIO_COND", "from_policy": "EVICON", "to_policy": "EVICON_GREEDY", "metrics": ["search_failures", "confirmed_pp", "stops"]},
    {"run": "Th", "contract": "K0F", "from_policy": "EVICON", "to_policy": "EVICON_FC100", "metrics": ["confirmed_pp", "fences", "stranded_resolved_unit_sec", "mean_time_to_terminal_s"]},
    {"run": "Th", "from_run": "Wq", "contract": "K0F", "from_policy": "EVICON", "to_policy": "EVICON", "metrics": ["clean_pp"]},
    {"label": "T6 ThingsBoard W = 3: M1W -> EVICON", "run": "TBw3", "contract": "TB_RPC", "from_policy": "M1W", "to_policy": "EVICON", "metrics": ["clean_pp", "confirmed_pp", "queries"]},
    {"label": "T6 ThingsBoard W = 16: M1W -> EVICON", "run": "TBw16", "contract": "TB_RPC", "from_policy": "M1W", "to_policy": "EVICON", "metrics": ["clean_pp", "confirmed_pp", "queries"]},
    {"label": "T6 ThingsBoard W unbounded: M1W -> EVICON", "run": "TBwinf", "contract": "TB_RPC", "from_policy": "M1W", "to_policy": "EVICON", "metrics": ["clean_pp", "confirmed_pp", "queries"]},
    {"label": "T6 firmware W = 6: plain (QoS 0, Z6) -> cutoff (C2)", "run": "C2", "from_run": "Z6", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_CUTOFF", "metrics": ["clean_pp", "confirmed_pp"]},
    {"label": "T6 firmware W unbounded: plain (QoS 0, Zi) -> cutoff (Rz)", "run": "Rz", "from_run": "Zi", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_CUTOFF", "metrics": ["clean_pp", "confirmed_pp"]},
    {"label": "T6 firmware: effect at W = 16 minus at W = 3", "metrics": ["clean_pp"], "a": {"run": "Z3w", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_CUTOFF"}, "b": {"run": "Z16w", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_CUTOFF"}},
    {"label": "T6 ThingsBoard: gap at W = 16 minus at W = 3", "metrics": ["clean_pp"], "a": {"run": "TBw3", "contract": "TB_RPC", "from_policy": "M1W", "to_policy": "EVICON"}, "b": {"run": "TBw16", "contract": "TB_RPC", "from_policy": "M1W", "to_policy": "EVICON"}},
    {"label": "T6 ThingsBoard: gap at W unbounded minus at W = 3", "metrics": ["clean_pp"], "a": {"run": "TBw3", "contract": "TB_RPC", "from_policy": "M1W", "to_policy": "EVICON"}, "b": {"run": "TBwinf", "contract": "TB_RPC", "from_policy": "M1W", "to_policy": "EVICON"}}
  ]
}
```

```policy-compare
{
  "name": "Release 083: EviCon against M1 and M1W, and against the practice baselines on the same schedules (PREREG_extra_083.md, E-T1, E-T2, E-T3)",
  "runs": {
    "Wk": {"campaign": "expM1W-k2"},
    "TBw": {"campaign": "expTB-m1w"},
    "RAq": {"campaign": "expRRA-queue"},
    "RAd": {"campaign": "expRRA-direct"},
    "RAo": {"campaign": "expRRA-noorphan"},
    "RTsq": {"campaign": "expRTs-queue"},
    "RTsd": {"campaign": "expRTs-direct"},
    "TB": {"campaign": "expTB-thingsboard"},
    "Wq": {"campaign": "expW-queue-w6"},
    "Wd": {"campaign": "expW-direct-w6"},
    "RTq": {"campaign": "expRT-queue"},
    "RTd": {"campaign": "expRT-direct"},
    "RRq": {"campaign": "expRR-queue"},
    "RRd": {"campaign": "expRR-direct"}
  },
  "comparisons": [
    {"id": "T1-E1", "a": {"run": "Wk", "contract": "K2", "policy": "M1"}, "b": {"run": "Wk", "contract": "K2", "policy": "M1W"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "T1-E2", "a": {"run": "Wk", "contract": "K2", "policy": "M1W"}, "b": {"run": "Wk", "contract": "K2", "policy": "EVICON"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "T1-E3", "a": {"run": "TBw", "contract": "TB_RPC", "policy": "M1"}, "b": {"run": "TBw", "contract": "TB_RPC", "policy": "M1W"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "T1-E4", "a": {"run": "TBw", "contract": "TB_RPC", "policy": "M1W"}, "b": {"run": "TBw", "contract": "TB_RPC", "policy": "EVICON"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "T2-E1", "a": {"run": "RAq", "contract": "K2", "policy": "EVICON"}, "b": {"run": "RAq", "contract": "K2", "policy": "RELEASE_ON_READ_ANSWERED"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "T2-E2", "a": {"run": "RAd", "contract": "K2", "policy": "EVICON"}, "b": {"run": "RAd", "contract": "K2", "policy": "RELEASE_ON_READ_ANSWERED"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "T2-E3", "a": {"run": "RAo", "contract": "K2", "policy": "EVICON"}, "b": {"run": "RAo", "contract": "K2", "policy": "RELEASE_ON_READ_ANSWERED"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "T2-E4", "a": {"run": "RRq", "contract": "K2", "policy": "RELEASE_ON_READ"}, "b": {"run": "RAq", "contract": "K2", "policy": "RELEASE_ON_READ_ANSWERED"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "T2-E5", "a": {"run": "RRd", "contract": "K2", "policy": "RELEASE_ON_READ"}, "b": {"run": "RAd", "contract": "K2", "policy": "RELEASE_ON_READ_ANSWERED"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "T3-E1", "a": {"run": "Wq", "contract": "K0", "policy": "EVICON"}, "b": {"run": "RTsq", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT_30S"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "T3-E2", "a": {"run": "RTq", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT"}, "b": {"run": "RTsq", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT_30S"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "T3-E3", "a": {"run": "Wq", "contract": "K0", "policy": "EVICON"}, "b": {"run": "RTsq", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT_60S"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "T3-E4", "a": {"run": "RTq", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT"}, "b": {"run": "RTsq", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT_60S"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "T3-E5", "a": {"run": "Wq", "contract": "K0", "policy": "EVICON"}, "b": {"run": "RTsq", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT_120S"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "T3-E6", "a": {"run": "RTq", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT"}, "b": {"run": "RTsq", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT_120S"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "T3-E7", "a": {"run": "Wd", "contract": "K0", "policy": "EVICON"}, "b": {"run": "RTsd", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT_30S"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "T3-E8", "a": {"run": "RTd", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT"}, "b": {"run": "RTsd", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT_30S"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "T3-E9", "a": {"run": "Wd", "contract": "K0", "policy": "EVICON"}, "b": {"run": "RTsd", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT_60S"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "T3-E10", "a": {"run": "RTd", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT"}, "b": {"run": "RTsd", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT_60S"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "T3-E11", "a": {"run": "Wd", "contract": "K0", "policy": "EVICON"}, "b": {"run": "RTsd", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT_120S"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]},
    {"id": "T3-E12", "a": {"run": "RTd", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT"}, "b": {"run": "RTsd", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT_120S"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "late_effects", "unresolved_at_end"]}
  ]
}
```

## 9. Pre-release checks (before freezing; not results)

Checks of the code path before freezing, on one machine and on a simulated testbed. They are not results of this plan:
the regime, the scale and the timing differ from the testbed runs.

**Unit tests.** Go: every package passes (`go test ./...`, `go vet`, `gofmt`), among them the runtime tests F49 (with a
window of 2, M1 defers a candidate on a full window and never queries; M1W queries the aged ambiguous command and
admits), F50 (RELEASE_ON_READ_ANSWERED releases a command whose owner answers NOT_FOUND after the settle time, releases
nothing on an unanswered read or while an attempt is recent, answers from the records with shadow reads, cuts its
resends at release-after minus settle, refuses a settle time above the release time), F51 (a deficit of 7 units over six
doses of 2 needs four stops: the enumeration with gamma = 3 fails, the greedy certificate stops four and admits), the
greedy certificate's property tests (600 random ADD domains against an unbounded search: a certificate exists exactly
when the search finds one, of the minimum size, minimal by inclusion, 49 of the 201 beyond gamma = 3; every admission
after its stops is safe in every outcome; 108 unresolved doses in 1.7 ms; a SET history falls back), and the
simulator's `TestQueryOnlyAndGreedy083`. The simulator's output of every scenario of earlier releases is byte-identical
to release 082's. Python: 166 analysis tests (among them `test_release083` H1-H12 and `test_prereg` on every plan,
`PREREG_review_077.md` still `db67640d...`), 80 conformance tests (0 skipped, Mosquitto present), and the firmware host
tests.

**Simulator.** `sim_extra083.py` (section 1(h); 200 episodes per scenario, 32 min on two cores) and
`power_extra083_testbed.py` (section 5) on the release-083 simulator and the 081 final report's cells.

**Analysis spec.** Synthetic runs made of renamed cells of the reference runs (Wk of Wq's K0F/K0 cells, TBw and the
TB window runs of TB, RAq/RAd/RAo of RRq/RRd, RTsq/RTsd of RTq/RTd, Z6 of C2 and C3, Zi of Rz, Gs of Si, Gm of Rm, Th
of Wq, the firmware window runs of C2, the T7 runs of S and Wdi), with the reference runs' schedule files:
`pairs_ext083.py` with this plan decided every hypothesis with a test (n = 8; 6 for the ThingsBoard rules; 4 for T7),
every detection, absence and safety rule, and paired every cross-run contrast and estimand, with difference 0 where
both arms are the same cells (e.g. Si -> Gs, TB -> TBw, Wq -> Th, C2 -> Z6, RRq -> RAq, RTq -> RTsq);
`policy_compare.py` paired all 21 comparisons. The configs replay their reference runs' schedules episode by episode
and differ from them only in the planned keys (`test_release083` H5); the T7 levels change the fault schedule and keep
S's and Wdi's arrivals.

**Simulated testbed** (the driver end to end on one machine: sshd on five loopback addresses as VM-A to VM-E, Docker
as VM-B with Mosquitto, MinIO and the ThingsBoard CE 4.3.1.6 stand-in image; the frozen firmware build placed as VM-D's
build output; a copy of this tree with this plan frozen; every 083 campaign as a copy of 1 episode of 180 s + 30 s
tail, in the night order of section 6): every stage PASS, every cell VALID.

| Stage | Result |
|---|---|
| `preflight build verify distribute --new --quick` | PASS (verify: `go test`, 80 conformance tests with 0 skipped, the firmware host tests, the analysis tests) |
| `measure zephyr-plain`, `zephyr` | K0; {D_h, Q_A, F} (K3) |
| `measure thingsboard` | `thingsboard-rpc-r0`: {Q_A, T} (K2) |
| 30 `campaign` copies, in the night order of section 6 | every one PASS, 72 cells, all VALID; the driver chose `campaign_ext083.py`, `campaign_real_ext083.py`, `campaign_tb_ext083.py`, `campaign_zephyr_ext077.py` or `campaign.py` by the policies |
| `report --archive <simulated archive> --from <the 078 run>` (the planned form) | refused without `--archive-missing-ok` (the simulated archive lacks the 070 bundle, as in the 078 regression); with it PASS, every section among them this plan's pairs, policy comparisons and E-SIM |
| `bundle --incremental` | PASS after every part |

| Config (run) | Cell | Clean | Safety counts | Notes |
|---|---|---|---|---|
| expT4_zephyr_w6 (Z6) | ZEPHYR_PLAIN_QOS0 / ZEPHYR_PLAIN_DIRECT_QOS0 | 32.1 / 32.1 % | 0 | the gateway logs `qos_cmd` 0 for both |
| expT4_zephyr_winf (Zi) | ZEPHYR_PLAIN_QOS0 / ZEPHYR_PLAIN_DIRECT_QOS0 | 69.3 / 69.9 % | 0 | |
| expM1W_k2 (Wk) | K2: EVICON / M1 / M1W | 100 / 98.9 / 100 % | 0 | window DEFERs 210 / 384 / 211; 211 `QUERY_ONLY_WINDOW_TRIGGER` events (M1W), queries 1000 / 0 / 636 |
| expTB_m1w (TBw) | TB_RPC: EVICON / M1 / M1W | 98.6 / 60.2 / 98.6 % | 0 | ThingsBoard devices made and deleted per cell |
| expTB_w3, _w16, _winf | TB_RPC: EVICON / M1W | 98.3 / 98.3; 98.6 / 98.6; 98.6 / 98.6 % | 0 | M1W leaves more commands unresolved at the end (6, 23, 49; no sweep) |
| expRRA_queue (RAq) | K2: EVICON / RELEASE_ON_READ_ANSWERED | 100 / 89.6 % | 0 / 45 false STOPPED (planned) | 45 releases, each after an answered NOT_FOUND (shadow read while the request was held) |
| expRRA_direct (RAd) | K2: EVICON / RELEASE_ON_READ_ANSWERED | 87.7 / 100 % | 0 / 0 | 55 releases after an answered NOT_FOUND, none took effect later; 12 UNCERTAIN reads (orphans) |
| expRRA_noorphan (RAo) | K2: EVICON / RELEASE_ON_READ_ANSWERED | 98.1 / 100 % | 0 / 0 | |
| expRTs_queue (RTsq) | K0: 30 / 60 / 120 s | 86.0 / 99.5 / 71.8 % | 60 / 39 / 24 false STOPPED (planned) | |
| expRTs_direct (RTsd) | K0: 30 / 60 / 120 s | 94.8 / 87.7 / 63.8 % | 4 / 1 / 0 false STOPPED | 120 s in a 180 s cell: no release took effect (the plan's 300 s cells: detection probability >= 0.92 in 5 episodes) |
| expTheta_w6 (Th) | K0F: EVICON / EVICON_FC100 | 94.0 / 98.4 % | 0 | fences 214 / 174 |
| expG_minio_winf (Gm) | EVICON_GREEDY: MINIO_PLAIN / MINIO_COND | 85.9 / 99.2 % | 0 | search failures 0 / 0 |
| expG_s_winf (Gs) | K0S, K0F: EVICON / EVICON_GREEDY | 100 / 100; 99.7 / 99.7 % | 0 | search failures 0 in all four (a short cell); stops 711 / 712, fences 211 / 210 |
| expT6_zephyr_w3, _w16, _w32 | ZEPHYR_PLAIN_QOS0 / ZEPHYR_CUTOFF | 31.5 / 100; 37.0 / 100; 60.8 / 100 % | 0 | with Z6 and Zi: the plain firmware's level rises with W (31.5, 32.1, 37.0, 60.8, 69.3 %), the cutoff's stays at 100 % |
| expT7_q6_<level> (T7q) | K0 / K0S / K0F | 0.5/min 85.8 / 100 / 100; 4/min 23.0 / 100 / 78.9; 5-15 s 55.9 / 100 / 94.8; 40-120 s 35.1 / 83.8 / 82.5; seedA 32.9 / 100 / 99.8; seedB 53.2 / 100 / 98.6 % | 0 | gate PASS in every run |
| expT7_dinf_<level> (T7d) | K0 / K0F | 0.5/min 95.1 / 100; 4/min 51.2 / 89.9; 5-15 s 81.9 / 94.2; 40-120 s 40.3 / 82.7; seedA 72.3 / 99.8; seedB 87.0 / 97.8 % | 0 | gate PASS in every run |

`report` read the archive's runs, the 078 run and this run, and gave `pairs_ext083.py` all 30 runs of this plan and its
14 reference runs (the earliest complete run of each config). Every rule with a test was NOT COMPUTABLE, as expected
with one episode, and the cross-run contrasts paired no episode, since the shortened copies' schedules differ from the
reference runs' (the schedule guard; the synthetic check above pairs them). RA-1 DETECTED; RA-2, RA-3, RA-S1 to RA-S3
ABSENT; RTs-1 to RTs-5 DETECTED and RTs-6 NOT DETECTED (a 120 s release time in a 180 s cell); T4-S1 to T4-S4 and
T5-SF1 to T5-SF4 ABSENT; X-S1 to X-S5 MET. `policy_compare.py` paired its 21 comparisons, E-SIM ran, the claims check
lists this plan's 60 rules, `SAFETY.md` gives the distinct fault schedules (A1), `T3.md` lists the 083 baselines apart
(A3), and the paper tables include `tab_pairs_PREREG_extra_083.tex`, `tab_extra083_*.tex` and `tab_admit_late.tex`
(A2). The verdicts of `PREREG_review_077.md`, `PREREG_expC2_074.md`, `PREREG_expC3_075.md` and `PREREG_expW_072.md`
are those of the 081 final report; the printed {F} queue level is 79.1 (79.2 before the one-rounding fix).

**Changes made before freezing** (from these checks): the T7 queue configs keep S's contract order and activation
gate (the config check H5); the ```pairs-ext``` block's safety rules over groups of runs, decided on the runs that ran
(section 6), and X-S1 limited to the N1 runs; the test of release 083 restores the shared campaign module after
installing the runners (the earlier tests saw the 083 policies otherwise); `evaluation/esim083.py` for E-SIM; the
driver's message for a baseline run without violations names the 083 rules; an edit of `t3_attribution.py` had
dropped its report functions, which the analysis tests caught (restored with A3 before this regression).

**Pre-freeze change: the 0.80 rule** (after the checks above, before any testbed cell; section 3). A paired-difference
rule is a confirmatory hypothesis only if its power under the testbed's own spread, at the predicted mean, is at least
0.80; otherwise it is an estimand, reported with its interval and the prediction and no verdict. Applied to the plan as
drafted (section 5, third table): T1-1 (0.03), T6-3 (0.68), T7-rate05-qF, -qS, -dF (0.42, 0.75, 0.07) and T7-off5-dF
(0.23) became estimands of E-T1, E-T6 and E-T7 (their `clean_pp` contrasts are in the estimand blocks); T4-6 replaces
U-4's equivalence, which assumed a queue that delivers what it held, by the gain the theory predicts under QoS 0
(`at_least 5`, Zi ZEPHYR_PLAIN_QOS0 -> Rz ZEPHYR_CUTOFF, simulator +14.5 pp, power 1.00 with the Rz plain direct ->
cutoff template); every rule kept has power 0.89 to 1.00. The rules went from 60 (35 paired-difference) to 54 (29). The
simulator's power and effects were recomputed from the same 200-episode runs (`extra083_summary.csv` unchanged;
`extra083_power.csv` lists the 29 hypotheses), `power_extra083_testbed.py` prints the rule's table, the synthetic check
above decided the 53 rules of the two blocks and X-S1 again (n = 8 / 6 / 4) and paired the new estimands (Wk M1 -> M1W
clean, the ThingsBoard gap W unbounded minus W = 3, T7 clean per level), and the report stage, run again on the
simulated testbed's runs, decided the changed blocks (PASS; the claims check lists 54 rules of this plan).

## Deviations

None so far.

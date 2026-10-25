# Preregistration: experiment C3, the evidence of a real IoT stack decomposed (Zephyr firmware behind Mosquitto, 5-VM testbed)

Release 075. Frozen before any C3 cell runs on the testbed. The campaign config below carries the
SHA-256 of this file, and `evicon_review.py campaign` refuses to run it if this file, or any file
listed under *Frozen files*, has changed. Changes after freezing are recorded under *Deviations*;
nothing above that section is edited.

## 1. Background (exploratory evidence, not part of the test)

**Experiment C2** (release 074, N6, 24/10, 56 of 56 cells VALID) ran EviCon on the Zephyr EviCon
firmware (Zephyr v4.2.0, `native_sim/native/64`, VM-E) behind Mosquitto 2.0.20 (VM-B), in three
evidence modes of one binary, each with the contract EviCon-Probe measured for it in the same run:
plain (none, K0), cutoff (D_h >= 600 s, Q_A, F; K3), stop (D_h >= 600 s, Q_A, Q_S, F; K3). EVICON's
clean fulfilment: plain 29.8 %, cutoff 79.3 %, stop 79.3 % (C2-3 MET: +49.5 pp, 95 % [40.1, 59.0]); a
blind retry duplicated 447 real effects on the plain firmware (C2-1 DETECTED) and none on the cutoff
firmware (C2-2 ABSENT); no safety violation wherever the T4 guard was on (C2-S1 to C2-S5 ABSENT).

**C2-5 was NOT MET** (cutoff -> stop +0.00 pp, 95 % [-0.24, +0.24]). Both modes reached the regime's
ceiling (the commands whose deadline passes while the device is offline; EVICON on the cutoff mode
equalled a blind retry on the dedup firmware, 79.3 against 79.4 %). The firmware never crashed (the
measured fault model, "owner up"), so no crash orphan blocked a fence: 0 fences aborted with
`BARRIER_UNCERTAIN_RECEIPTS` in C2, against 388 in `expS-w6`, where the emulated owner crashes between
effect and receipt. C2-5 was framed as a test between the simulator (no stop-over-fence gain) and the
emulated testbed (+21.1 pp in `expS-w6`), but the two contrasts were not the same: `expS-w6` compares
fence-only with stop-only contracts under owner crashes; C2 compares two contracts that both have Q_A,
with the owner up. C2 therefore does not contradict `expS-w6`; it leaves open which evidence the real
stack's ceiling rests on.

**C3 decomposes that evidence on the same stack.** EVICON runs on restrictions of the contracts
measured in the same review run to one capability each: **{Q_A}** (evidence of what happened: a
receipt query that certifies APPLIED but never non-occurrence), **{F}** (a cutoff fence at the full
frontier: every identity at or below the cutoff gets a terminal receipt, never-started ones STOPPED),
**{Q_S}** (a per-identity stop), and on the plain mode's measured contract (none). A restricted contract
is a non-empty proper subset of a measured one: every capability it keeps was measured (its provenance
is copied and names the measured file's SHA-256), so it is sound wherever the measured one is. D_h is
dropped in every restriction; it is unusable in this regime anyway (no assumed bound on the network
delay, so T4 allows no same-identity retry), so dropping it changes no EVICON decision. The gateway
refuses every request the restricted contract does not cover (a query without Q_A, a stop without Q_S,
a barrier without F: 409), so the restriction holds at the owner too, not only in the authority.
{Q_A} and {F} run the firmware's cutoff mode, {Q_S} its stop mode.

**Two owner channels.** `queue` is C2's: an offline window resets the gateway's MQTT connections and
paho delivers what was published meanwhile after it reconnects (late). `direct` loses every message
published while offline, either way (the gateway drops it; the MQTT connections stay up): a lost
command never takes effect, as with `evicon-device -offline-mode drop` in `expW-direct-w6`. The
channel decides what Q_A can do. On the queue channel a command held while offline arrives late,
takes effect, and Q_A then certifies it APPLIED; on the direct channel it never arrives, Q_A answers
NOT_FOUND for ever (never terminal without owner expiry, which no contract here has), and the identity
holds a slot of the window of 6 unresolved commands per device until the end. F and Q_S certify
non-occurrence on either channel (a held command that arrives after the fence or the stop is rejected).

**Simulator prediction** (`evaluation/sim_expC3.py`, release 075 simulator, twin
`experiments/sim/iot_dosing_testbed.json` with window 6, no assumed delay bound, fence_uncertain
abort, the contracts `experiments/owners/zephyr/pred-zephyr-{plain,cutoff,stop}-074.json` and
`pred-zephyr-{qa,f,qs}-075.json`, 200 episodes; `SIMULATION_ONLY`; `evaluation/sim_predictions/
expC3_075_summary.csv`, `SIM_EXPC3_075.md`). Clean / confirmed clean fulfilment of EVICON, % of
offered, in the scenario `owner_up` (no owner crash: C3's fault model) and, for contrast, `owner_crash`
(the twin's crash between effect and receipt, as the emulated owner of W and S):

| Contract | direct, owner_up | queue, owner_up | direct, owner_crash | queue, owner_crash |
|---|---|---|---|---|
| plain (none) | 34.5 / 33.7 | 38.4 / 35.2 (C2's cell) | 32.5 | 37.5 |
| {Q_A} | 37.7 / 37.7 | 75.6 / 75.6 | 35.7 | 75.5 |
| {F} | 77.1 / 77.1 | 77.0 / 77.0 | **69.9** | **72.7** |
| {Q_S} | 77.2 / 77.1 | 76.9 / 76.9 | 77.1 | 76.9 |
| cutoff {D_h, Q_A, F} (C2's cell on queue) | 77.1 | 77.0 | 71.6 | 76.6 |

The simulator predicts the interaction C3 tests: Q_A alone is worth almost nothing on the direct
channel (+3.1 pp over plain) and almost the ceiling on the queue channel, at a cost in time (1,082
unit*s of stranded capacity-time per episode still unresolved at the end, against none for {F}); F
and Q_S reach the ceiling on both channels with the owner up; with owner crashes F loses 4-7 pp and
Q_S nothing. Mean per-episode
effects (owner_up): plain -> {F} direct +43.1 pp (SD 25.5), {Q_A} -> {F} direct +39.9 (26.8),
{Q_A} -> {Q_S} direct +40.0 (26.8), {Q_A} direct -> queue +38.4 (26.6), {F} -> {Q_S} direct +0.1
(0.5), queue -0.1 (0.6).

**What the emulated testbed already says** (release 072, EVICON, window 6, seed 20261017; mean of
episodes 0-7, the schedules C3 replays): `expW-direct-w6` K0 24.2 %, K0F (fence only, with owner
crashes) 55.9 %; `expW-queue-w6` K0 29.5 %, K0F 60.3 %; `expS-w6` K0S (stop only) 79.4 % (C2's
stop mode: 79.3 %). The emulated owner's contracts K0F and K0S have exactly the capability sets {F}
and {Q_S} of C3's restrictions; they differ from C3's owner chiefly in the fault model (the emulated
owner crashes between effect and receipt and leaves orphans, the firmware stays up), and otherwise in
the implementation (an emulator, against the firmware behind a broker). C3's {F} and {Q_S} cells
against those cells are therefore the testbed's estimate of what crash orphans cost each capability
(E-C3-6; where K0 and the plain mode, the same contract without evidence, agree, the implementation
adds little).

**Pre-release checks.** See section 9 (written before freezing; local and simulated-testbed runs, not
results).

## 2. Design

| Item | Value |
|---|---|
| Config | `evaluation/campaigns/expC3_ablation.json` |
| Runner | `evaluation/campaign_zephyr_ext.py`: the frozen C2 runner (`campaign_zephyr.py`, unchanged) with two additions: restricted contracts and the owner channel. Its config check runs the frozen runner's checks on every measured parent contract, then checks each restriction against its parent (MEASURED; a non-empty proper subset; the parent of the cell's firmware mode; the parent's SHA-256 named in `provenance.restricted_from`; provenance and fault model copied; no D_h horizon without D_h) |
| Owner | as C2: per device one process of the Zephyr EviCon firmware (`zephyr-cutoff.exe`, built from the frozen sources by `deploy/setup/build_iot.sh` on VM-D; SHA-256 recorded), mode `cutoff` for {Q_A} and {F}, `stop` for {Q_S}, `plain` for plain; a fresh flash file and its own topic prefix per cell; Mosquitto `eclipse-mosquitto:2.0.20` on VM-B in EviCon's compose project `evicon-probe-owner`; `evicon-plant` on VM-E is the truth |
| Gateway | one `conformance/reference/mqtt_owner_gateway_ext.py` per device on VM-C: the frozen C2 gateway (`mqtt_owner_gateway.py`, unchanged: protocol, durable record, barrier receipts) with the restricted contract (it advertises it and refuses what it does not cover, 409) and the channel of the cell (`--offline-mode reset`: queue; `drop`: direct) |
| Contracts | 7 cells per episode, `ZEPHYR_<capabilities>_<channel>`: `PLAIN_DIRECT` (plain mode, measured contract: expected none); `QA_DIRECT`, `QA_QUEUE` ({Q_A} of the cutoff mode's measured contract); `F_DIRECT`, `F_QUEUE` ({F} of the cutoff mode's); `QS_DIRECT`, `QS_QUEUE` ({Q_S} of the stop mode's). The queue-channel plain, cutoff and stop cells are C2's (same seed and schedules: cross-run estimands E-C3-5). Gate before the campaign (driver, as C2): each measured contract is MEASURED, has exactly the capabilities C2 expected (plain none; cutoff D_h, Q_A, F; stop D_h, Q_A, Q_S, F) and the measured fault model, and was measured on the binary the campaign runs; the driver writes the restrictions from those files into the campaign's contract directory (`measured-<mode>.json` and `<cell>.json`); otherwise no cell runs |
| Policy | **EVICON** only (C2 ran M1 and NAIVE; C3 asks what each capability is worth to the runtime) |
| Regime | identical to C2, `expW_queue_w6`, `expW_direct_w6` and `expS_w6`: 4 devices; 300 s per cell + 60 s tail; 0.5 commands/s per device; values 1-5; bound 345 per device; deadline 60 s; validity 90 s; fault windows 2/min; offline 10-40 s; mix offline 0.3, drop_up 0.2, drop_down 0.2, delay_up 0.1 (6 s), crash 0.2; the same authority parameters (no assumed delay bound, `max_network_delay` -1ns; window 6; SKI_RENTAL resolution, query first); **the same seed, 20261017**: episodes 0-7 replay exactly the arrivals and fault windows of those runs' episodes 0-7 |
| Faults | as C2: drop_up, drop_down, delay_up on the authority -> gateway path; crash: the gateway exits after the firmware answered APPLIED and before it replies (a client abandon), restarted after 2 s with its durable record; offline: the gateway's link to the broker is down, by the cell's channel (queue: connections reset, held messages delivered late; direct: every message published meanwhile is lost). The firmware and the broker never crash or restart: the measured fault model, "network loss/delay, client abandon; owner up" |
| Truth | `evicon-plant` on VM-E, as in every campaign |
| Episodes | **8** (0-7); 7 contracts x 1 policy, **56 cells**, about 6.7 hours (plus about 1.3 hours for the three measurements) |
| Pairing | contracts of the same episode (common random numbers); across runs, episodes whose schedule files are byte-identical |
| Activation gate | none |
| Analysis | `evaluation/pairs_report.py --plan evaluation/prereg/PREREG_expC3_075.md --run C3=<run> --run C2=<expC2-zephyr run> --run Wq=<expW-queue-w6 run> --run Wd=<expW-direct-w6 run> --run S=<expS-w6 run> --out <dir>` with the spec in section 8; `evicon_review.py report` runs it with the earliest complete runs among its sources |

## 3. Hypotheses and decision rules

Tests of `pairs_report.py` on the per-episode paired differences (to minus from), EVICON, clean
fulfilment in pp of offered, Student t, alpha = 0.05; C3-1 to C3-6 form one Holm family.

| ID | Hypothesis | Decision rule |
|---|---|---|
| **C3-1** | On the direct channel, the fence alone recovers what the plain firmware loses | `at_least 10`, `ZEPHYR_PLAIN_DIRECT -> ZEPHYR_F_DIRECT` |
| **C3-2** | On the direct channel, evidence of what happened is not enough: the fence's evidence of non-occurrence adds at least 10 pp to Q_A | `at_least 10`, `ZEPHYR_QA_DIRECT -> ZEPHYR_F_DIRECT` |
| **C3-3** | ... and so does the per-identity stop's | `at_least 10`, `ZEPHYR_QA_DIRECT -> ZEPHYR_QS_DIRECT` |
| **C3-4** | What Q_A is worth depends on the channel: on the queue channel, where lost commands arrive late, it recovers at least 10 pp more than on the direct one | `at_least 10`, `ZEPHYR_QA_DIRECT -> ZEPHYR_QA_QUEUE` |
| **C3-5** | With the owner up, the fence and the per-identity stop are equivalent on the direct channel | `equivalent 5` (TOST, +-5 pp), `ZEPHYR_F_DIRECT -> ZEPHYR_QS_DIRECT` |
| **C3-6** | ... and on the queue channel | `equivalent 5`, `ZEPHYR_F_QUEUE -> ZEPHYR_QS_QUEUE` |
| **C3-S** | Safety in every C3 cell (no negative control in C3; the T4 guard is on everywhere) | `safety` over run C3, no exempt contract: bound violations, duplicate effects, logical duplicates, false STOPPED, false APPLIED all 0 in every VALID cell. Late effects are reported separately |

Interpretation fixed in advance:
- C3-2 and C3-3 MET, C3-4 MET: on a real IoT stack, a receipt query is enough only on a channel that
  eventually delivers what it held; where the channel can lose a command, the runtime needs evidence of
  non-occurrence (a fence or a stop) to free the device's capacity safely. The paper states the channel
  as part of a contract's context. NOT MET: reported with the estimate; C3-2 or C3-3 NOT MET with an
  estimate near 0 means Q_A recovered the direct channel's ambiguous commands on the testbed (the
  commands lost while offline did not fill the window), against the simulator.
- C3-1 MET: the fence alone (one capability, measured on the firmware) gives the gain C2 found for the
  whole cutoff contract. NOT MET: reported with E-C3-5 (what {F} lacks against {D_h, Q_A, F}).
- C3-5 and C3-6 MET: with the owner up, F and Q_S are interchangeable on this stack; with E-C3-6 (the
  same capability sets on the emulated owner, which crashes) and H-S of `PREREG_expS_072.md`, the
  stop's advantage over the fence is conditional on crash orphans. The paper states it so. NOT MET
  with Q_S above F: the per-identity stop adds value without orphans too (it does not wait for a whole
  frontier). NOT MET with F above Q_S: the stop mode's limits cost on their own (a stop of an identity
  whose receipt left the RAM ring answers UNCERTAIN, and without Q_A or F nothing else resolves it;
  E-C3-3 counts them).
- C3-S PRESENT anywhere: a safety failure of EviCon under a restricted contract; reported with the
  cells and the traces, and the plan of record stops until the cause is known. A restriction is weaker
  than a measured contract, so a violation would contradict the soundness argument, not only C3.

## 4. Estimands (reported with intervals, no claims)

| ID | Estimand |
|---|---|
| E-C3-1 | `ZEPHYR_QA_QUEUE -> ZEPHYR_F_QUEUE`: clean and confirmed fulfilment, admitted, stranded capacity-time (resolved, censored), mean time to a terminal outcome, queries, fences, unresolved at the end (the cost in time of Q_A alone where it reaches the ceiling) |
| E-C3-2 | `ZEPHYR_PLAIN_DIRECT -> ZEPHYR_QA_DIRECT`: clean and confirmed fulfilment, admitted, queries, stranded capacity-time censored, unresolved at the end (what Q_A alone recovers on the direct channel); and the share of the plain -> {F} gap that {Q_A} recovers on the direct channel (`gap`) |
| E-C3-3 | `ZEPHYR_F_DIRECT -> ZEPHYR_QS_DIRECT` and `ZEPHYR_F_QUEUE -> ZEPHYR_QS_QUEUE`: confirmed fulfilment, fences, fences reached / completed, stops, stops reached / terminal, mean time to a terminal outcome, unresolved at the end |
| E-C3-4 | `ZEPHYR_F_DIRECT -> ZEPHYR_F_QUEUE` and `ZEPHYR_QS_DIRECT -> ZEPHYR_QS_QUEUE`: clean and confirmed fulfilment, mean time to a terminal outcome (the channel's effect where the contract certifies non-occurrence; the simulator predicts none) |
| E-C3-5 | Across runs, C2 -> C3 (same seed, same schedules, paired): `ZEPHYR_PLAIN` -> `ZEPHYR_PLAIN_DIRECT` (the channel, without evidence); `ZEPHYR_PLAIN` -> `ZEPHYR_QA_QUEUE` (Q_A on the queue channel); `ZEPHYR_CUTOFF` -> `ZEPHYR_QA_QUEUE` (the cutoff contract without F); `ZEPHYR_CUTOFF` -> `ZEPHYR_F_QUEUE` (without Q_A); `ZEPHYR_STOP` -> `ZEPHYR_QS_QUEUE` (the stop contract without Q_A and F); clean and confirmed fulfilment |
| E-C3-6 | Emulated owner against the firmware, the same capability set and channel (same seed, paired): `K0` of `expW-direct-w6` -> `ZEPHYR_PLAIN_DIRECT` (the same contract, none: the emulated owner's fidelity); `K0F` of `expW-direct-w6` -> `ZEPHYR_F_DIRECT`, `K0F` of `expW-queue-w6` -> `ZEPHYR_F_QUEUE`, `K0S` of `expS-w6` -> `ZEPHYR_QS_QUEUE` (the same capabilities, the emulated owner crashing, the firmware up: what crash orphans cost each capability); clean and confirmed fulfilment. Directions stated in advance (exploratory): K0 and plain equal within +-5 pp; {F} higher on the firmware by at least the simulator's owner_crash cost (4-7 pp); {Q_S} equal within +-5 pp |
| E-C3-7 | Levels per contract; the testbed against the simulator's owner_up prediction of section 1 (`predict_check.py`) |

## 5. Sample size

From the simulator's 200 per-episode paired differences (owner_up; resampling n episodes, Holm over
C3-1 to C3-6, 2,000 resamples; `SIMULATION_ONLY`; `evaluation/sim_predictions/expC3_075_power.csv`):

| n | C3-1 | C3-2 | C3-3 | C3-4 | C3-5 | C3-6 |
|---|---|---|---|---|---|---|
| 6 | 0.60 | 0.48 | 0.49 | 0.45 | 1.00 | 1.00 |
| **8** | 0.88 | 0.78 | 0.80 | 0.75 | 1.00 | 1.00 |
| 10 | 0.97 | 0.94 | 0.92 | 0.92 | 1.00 | 1.00 |

The simulator's differences are bimodal (SD about 26 pp: in some episodes no device locks up); on the
testbed, C2's EVICON plain -> cutoff differences had SD 11.3 pp (simulator: 24.6). From C2's own
per-episode differences, centred and shifted to a range of true means, resampled with the other five
hypotheses at the simulator's means (`evaluation/power_expC3_testbed.py`, 4,000 resamples;
`evaluation/sim_predictions/POWER_EXPC3_TESTBED_075.md`): an `at_least 10` rule has power 0.78 at
n = 8 for a true effect of +20 pp, 0.98 for +25 pp and 1.00 from +30 pp (the simulator predicts +38 to
+43); the equivalence rules have power 1.00 for a true difference up to 4 pp with C2's cutoff -> stop
spread (SD 0.3 pp), and, with ten times that spread (SD 2.9 pp), 1.00 at 0, 0.87 at 2 and 0.58 at
3 pp. Eight episodes, as in C and C2.

## 6. Exclusions, stopping and order

- INVALID cells are excluded and listed (a fault could not be applied, a process died, a firmware
  process exited (the owner must stay up), the truth could not be read). An INVALID cell is re-run once
  by resuming the campaign; if it is INVALID again it stays excluded. A run with more than 10 %
  excluded cells is not interpretable and its hypotheses are not decided.
- No interim analysis: `pairs_report.py` is run only when every cell is done. The campaign may be split
  over several sessions (resume; Mosquitto is started again; every cell has its own topic prefix and
  flash files).
- Order in one review run: `measure zephyr-plain zephyr zephyr-stop`, then `campaign expC3_ablation`.
  A new firmware build between them makes the campaign refuse to start (measured binary != run binary).
  The owner reboot check (`measure zephyr-reboot`, release 075) is not part of C3; it runs after the
  campaign, on the same binary, and is reported as a limitation of the measured fault model.
- The earliest complete run of `expC3_ablation` is the preregistered one.

## 7. Frozen files

SHA-256 at freezing time (`scripts/freeze_prereg.py`); the driver checks them before running. The six
files frozen for experiment A are frozen again, unchanged; added through the `frozen-extra` block: the
analysis (`pairs_report.py`, `cadence_report.py`), the C3 runner and the frozen runners it imports, the
gateway extension and the frozen gateway it runs, the code the gateway imports, the fault proxy, the
firmware sources, the three probe configs, the probe adapter, probes and rubric that measure them, the
simulator's predicted contracts, and the simulator prediction of section 1 with its script and the
testbed power script of section 5. Every file frozen by `PREREG_expC2_074.md` is frozen here too, with
the same SHA-256 (none changed in release 075).

The Go runtime is built from release 075 (unchanged since 072 except `buildinfo.Release`); the run's
`source.json` records its source.

```frozen-extra
evaluation/pairs_report.py
evaluation/cadence_report.py
evaluation/campaign_zephyr_ext.py
evaluation/campaign_zephyr.py
evaluation/campaign_real.py
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
conformance/configs/zephyr-mqtt-stop.json
conformance/configs/zephyr-mqtt-plain.json
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
experiments/owners/zephyr/pred-zephyr-plain-074.json
experiments/owners/zephyr/pred-zephyr-cutoff-074.json
experiments/owners/zephyr/pred-zephyr-stop-074.json
experiments/owners/zephyr/pred-zephyr-qa-075.json
experiments/owners/zephyr/pred-zephyr-f-075.json
experiments/owners/zephyr/pred-zephyr-qs-075.json
evaluation/sim_expC3.py
evaluation/sim_predictions/expC3_075_summary.csv
evaluation/sim_predictions/expC3_075_power.csv
evaluation/power_expC3_testbed.py
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
82a9d4f92cdc48940e262504a770a70e004d3f2f39975a98f65dde4de1b33b8c  evaluation/campaign_zephyr_ext.py
15c46ffd982baacecbec4fce2ca39e7c6ba16fc3148bfa8fb0032bc4126947db  evaluation/campaign_zephyr.py
65eb699cf290296595eb084c031570a9edcd5004a2869de15b35c1f1ef958557  evaluation/campaign_real.py
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
15735bd1bd31040f3904a96d09efaf83f59653a608f12cf76ca786e50c9d8fc5  conformance/configs/zephyr-mqtt-stop.json
fa90d3478844503b8742291204212beb0d3fe7e4e3db4563080f5ef22062be4c  conformance/configs/zephyr-mqtt-plain.json
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
4c7f4f704e78d5c4887dd0c9dc0853d135cd3a5c5fdb6c592697712a534416a5  experiments/owners/zephyr/pred-zephyr-plain-074.json
69b280454fe66f8f2def5acdb95b2c6e47378a3059a9f3eb670b2ac8bd589423  experiments/owners/zephyr/pred-zephyr-cutoff-074.json
424f40ed02618c8da661d6000f3f66f5dd505e8f3dd461c0df8d3805320ef5fe  experiments/owners/zephyr/pred-zephyr-stop-074.json
417c1d3fcce8a724f77114a16d81f7c021acc39df35faf6427ca6e657f076993  experiments/owners/zephyr/pred-zephyr-qa-075.json
827ad1ae3501b73be812e0294363e1e8fe67c49ed9ea953973f180762aa87690  experiments/owners/zephyr/pred-zephyr-f-075.json
6f1cba944074e50decbe95f898dca7411f57a9d36d517663695e83abbae7ab89  experiments/owners/zephyr/pred-zephyr-qs-075.json
b7d060157317ad35b798123490530ccce78c92e66bc1f583518ca18f3e43368e  evaluation/sim_expC3.py
351dc86ccde34eee15a51a75df83d92a4fbba0ab10138a4cda5a238257a5e7f7  evaluation/sim_predictions/expC3_075_summary.csv
bd1a6b2cd51dc48306b047e91ec573514b8a8b808edb5084a34d7b7a75155fbf  evaluation/sim_predictions/expC3_075_power.csv
3dc2c2f7bfa818c2146a82708725d4db0a3b03d665a68a49542ef506808baee4  evaluation/power_expC3_testbed.py
```

## 8. Analysis spec (read by pairs_report.py)

```pairs-spec
{
  "name": "Experiment C3: the evidence of the Zephyr firmware decomposed, two owner channels (075)",
  "runs": {"C3": {"campaign": "expC3-ablation"},
           "C2": {"campaign": "expC2-zephyr", "reference": true},
           "Wq": {"campaign": "expW-queue-w6", "reference": true},
           "Wd": {"campaign": "expW-direct-w6", "reference": true},
           "S": {"campaign": "expS-w6", "reference": true}},
  "alpha": 0.05,
  "max_invalid_fraction": 0.10,
  "hypotheses": [
    {"id": "C3-1", "family": "primary", "run": "C3", "policy": "EVICON", "from": "ZEPHYR_PLAIN_DIRECT", "to": "ZEPHYR_F_DIRECT", "metric": "clean_pp", "test": "at_least", "margin": 10.0},
    {"id": "C3-2", "family": "primary", "run": "C3", "policy": "EVICON", "from": "ZEPHYR_QA_DIRECT", "to": "ZEPHYR_F_DIRECT", "metric": "clean_pp", "test": "at_least", "margin": 10.0},
    {"id": "C3-3", "family": "primary", "run": "C3", "policy": "EVICON", "from": "ZEPHYR_QA_DIRECT", "to": "ZEPHYR_QS_DIRECT", "metric": "clean_pp", "test": "at_least", "margin": 10.0},
    {"id": "C3-4", "family": "primary", "run": "C3", "policy": "EVICON", "from": "ZEPHYR_QA_DIRECT", "to": "ZEPHYR_QA_QUEUE", "metric": "clean_pp", "test": "at_least", "margin": 10.0},
    {"id": "C3-5", "family": "primary", "run": "C3", "policy": "EVICON", "from": "ZEPHYR_F_DIRECT", "to": "ZEPHYR_QS_DIRECT", "metric": "clean_pp", "test": "equivalent", "margin": 5.0},
    {"id": "C3-6", "family": "primary", "run": "C3", "policy": "EVICON", "from": "ZEPHYR_F_QUEUE", "to": "ZEPHYR_QS_QUEUE", "metric": "clean_pp", "test": "equivalent", "margin": 5.0}
  ],
  "safety": {"id": "C3-S", "runs": ["C3"], "exempt_contracts": []},
  "estimands": [
    {"run": "C3", "policy": "EVICON", "from": "ZEPHYR_QA_QUEUE", "to": "ZEPHYR_F_QUEUE", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s", "queries", "fences", "unresolved_at_end"]},
    {"run": "C3", "policy": "EVICON", "from": "ZEPHYR_PLAIN_DIRECT", "to": "ZEPHYR_QA_DIRECT", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "queries", "stranded_censored_unit_sec", "unresolved_at_end"]},
    {"run": "C3", "policy": "EVICON", "from": "ZEPHYR_F_DIRECT", "to": "ZEPHYR_QS_DIRECT", "metrics": ["confirmed_pp", "fences", "fences_reached", "fences_reached_ok", "stops", "stops_reached", "stops_reached_terminal", "mean_time_to_terminal_s", "unresolved_at_end"]},
    {"run": "C3", "policy": "EVICON", "from": "ZEPHYR_F_QUEUE", "to": "ZEPHYR_QS_QUEUE", "metrics": ["confirmed_pp", "fences", "fences_reached", "fences_reached_ok", "stops", "stops_reached", "stops_reached_terminal", "mean_time_to_terminal_s", "unresolved_at_end"]},
    {"run": "C3", "policy": "EVICON", "from": "ZEPHYR_F_DIRECT", "to": "ZEPHYR_F_QUEUE", "metrics": ["clean_pp", "confirmed_pp", "mean_time_to_terminal_s"]},
    {"run": "C3", "policy": "EVICON", "from": "ZEPHYR_QS_DIRECT", "to": "ZEPHYR_QS_QUEUE", "metrics": ["clean_pp", "confirmed_pp", "mean_time_to_terminal_s"]},
    {"run": "C3", "from_run": "C2", "policy": "EVICON", "from": "ZEPHYR_PLAIN", "to": "ZEPHYR_PLAIN_DIRECT", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "C3", "from_run": "C2", "policy": "EVICON", "from": "ZEPHYR_PLAIN", "to": "ZEPHYR_QA_QUEUE", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "C3", "from_run": "C2", "policy": "EVICON", "from": "ZEPHYR_CUTOFF", "to": "ZEPHYR_QA_QUEUE", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "C3", "from_run": "C2", "policy": "EVICON", "from": "ZEPHYR_CUTOFF", "to": "ZEPHYR_F_QUEUE", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "C3", "from_run": "C2", "policy": "EVICON", "from": "ZEPHYR_STOP", "to": "ZEPHYR_QS_QUEUE", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "C3", "from_run": "Wd", "policy": "EVICON", "from": "K0", "to": "ZEPHYR_PLAIN_DIRECT", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "C3", "from_run": "Wd", "policy": "EVICON", "from": "K0F", "to": "ZEPHYR_F_DIRECT", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "C3", "from_run": "Wq", "policy": "EVICON", "from": "K0F", "to": "ZEPHYR_F_QUEUE", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "C3", "from_run": "S", "policy": "EVICON", "from": "K0S", "to": "ZEPHYR_QS_QUEUE", "metrics": ["clean_pp", "confirmed_pp"]}
  ],
  "gap": [
    {"run": "C3", "policy": "EVICON", "low": "ZEPHYR_PLAIN_DIRECT", "mid": "ZEPHYR_QA_DIRECT", "high": "ZEPHYR_F_DIRECT"}
  ]
}
```

## 9. Pre-release checks (before freezing; not results)

Checks of the code path before freezing, on one machine and on a simulated testbed. They are not
results of C3: the regime, the broker build, the timing and the scale differ from the testbed run.

**Firmware.** Unchanged since release 074 (every firmware source has the SHA-256 frozen by
`PREREG_expC2_074.md`). The checks below ran the build of those sources with Zephyr v4.2.0 for
`native_sim/native/64`, SHA-256 `9fe7b7d0...`; the testbed runs the build of VM-D.

**Local campaign** (one machine, local Mosquitto 2.0.18, `campaign_zephyr_ext.py` with a copy of the
config shortened to 1 episode of 90 s + 15 s tail, 6 fault windows/min, offline 5-12 s, deadline 30 s,
validity 45 s; seed 20261017; 4 devices; 184 commands offered per cell; the restricted contracts made
by `restrict_contract` from the contracts of the local 074 measurement, `PREREG_expC2_074.md` section 9):

| Contract | Clean | Unresolved at end | Queries | Fence calls (completed) | Stop calls (terminal) | Messages dropped (direct) |
|---|---|---|---|---|---|---|
| PLAIN_DIRECT | 52 (28.3 %) | 24 | 0 | 0 | 0 | 22 commands |
| QA_DIRECT | 144 (78.3 %) | 18 | 826 | 0 | 0 | 15 commands, 60 queries |
| F_DIRECT | 184 (100 %) | 0 | 0 | 62 (25) | 0 | 25 commands, 35 fences |
| QS_DIRECT | 184 (100 %) | 0 | 0 | 0 | 100 (50) | 28 commands, 41 stops |
| QA_QUEUE | 180 (97.8 %) | 4 | 283 | 0 | 0 | - |
| F_QUEUE | 184 (100 %) | 0 | 0 | 56 (25) | 0 | - |
| QS_QUEUE | 184 (100 %) | 0 | 0 | 0 | 96 (49) | - |

7 of 7 cells VALID; no bound violation, duplicate effect, logical duplicate, false STOPPED, false
APPLIED or late effect anywhere. The authority sent no request outside the restricted contract (no
query in the {F} and {Q_S} cells, no fence outside {F}, no stop outside {Q_S}); the gateway's refusal of
such a request is tested by X2. {Q_A} on the direct channel did better than the simulator predicts for
the full regime (78 against 38 %): in 90 s, three of the four devices lost 2-4 commands while offline
and so never filled their window of 6; the fourth lost 6, filled it, and answered 289 receipt queries
NOT_FOUND (12 of its commands applied). The planned regime (300 s cells, offline 10-40 s) is
the simulator's. `pairs_report.py` with this plan on that run (with the N6 `expC2-zephyr`, the N1
`expW-queue-w6`, the N2 `expW-direct-w6` and the N3 `expS-w6` runs as C2, Wq, Wd, S): C3-S MET,
C3-1 to C3-6 NOT COMPUTABLE (one episode); the cross-run estimands are empty there, as they must be
(another schedule). `expC2_summary.py --experiment C3` ran on the same run.

**Common random numbers.** `campaign.schedule` of this config gives, for episodes 0-7, files
byte-identical to `schedule_ep0.json` ... `schedule_ep7.json` of the N6 `expC2-zephyr`, N1
`expW-queue-w6`, N2 `expW-direct-w6` and N3 `expS-w6` runs (8 of 8 episodes each).

**Cross-run pairing.** A synthetic run made of EVICON cells of episodes 0-7 of those runs, renamed to
the seven C3 contracts (`K0` of `expW-direct-w6` as PLAIN_DIRECT, `K0F` as F_DIRECT, `K0F` of
`expW-queue-w6` as F_QUEUE, C2's `ZEPHYR_STOP` as QS_QUEUE, and so on), gave through this plan's
analysis every hypothesis with n = 8, the gap with n = 8, and the nine cross-run estimands with n = 8
paired episodes: difference 0 where the two arms are the same cells, and for `K0S` of `expS-w6`
against C2's stop cells -0.13 pp clean, as in C2's E-C2-6. `fidelity_check.py` gave the four C3 pairs
the same paired differences.

**Simulated testbed** (the driver end to end on one machine: sshd on five loopback addresses as VM-A
to VM-E, Docker as VM-B with a stand-in image tagged `eclipse-mosquitto:2.0.20` that runs Mosquitto
2.0.18; the firmware build `9fe7b7d0...` placed as VM-D's build output; a copy of this tree with this
plan frozen):
- `preflight build verify distribute --new --quick`: PASS (69 conformance tests, among them the
  gateway tests Z1-Z9 and X1-X4; 91 analysis tests, among them E-R1 to E-R3, S1-S6 and the release-075
  plan tests; firmware host tests).
- `measure zephyr-plain zephyr zephyr-stop --quick`, one binary: plain none (K0); cutoff D_h (600 s),
  Q_A, F (K3); stop D_h (600 s), Q_A, Q_S, F (K3); the planned fault model in all three.
- `campaign` of a copy of this config without the preregistration, 1 episode of 180 s + 60 s tail,
  the planned regime otherwise (365 commands offered per cell): the gate accepted the three measured
  contracts and the binary, and the driver wrote the six restrictions; 7 of 7 cells VALID; driver PASS
  ("safety violations only where planned (none)"). Clean fulfilment: PLAIN_DIRECT 32.0 %; QA_DIRECT
  61.9 % (2,503 queries, 22 unresolved at the end); F_DIRECT 100 % (130 fence calls, 20 completed, the
  others sent while the link was down); QS_DIRECT 99.7 % (187 stop calls, 58 reached the firmware, 53
  terminal, 5 UNCERTAIN; 1 unresolved); QA_QUEUE 98.6 % (717 queries, 5 unresolved); F_QUEUE 100 % (122
  fence calls, 20 completed); QS_QUEUE 100 % (157 stop calls, 47 reached the firmware, all terminal). No
  duplicate effect, bound violation, logical duplicate, false STOPPED, false APPLIED or late effect. On
  this single-host testbed `pairs_report.py` counts the trace-based metrics (`fences_reached`,
  `stops_reached` and their successes) twice: the runner copies VM-A's and VM-E's cell directories, which
  are one directory here. The counts above are halved accordingly; on the testbed each host has its own
  directory (N6, cutoff mode: 94 fences reached by the traces, 96 completed barriers in the gateways'
  logs).
- `measure zephyr-reboot --quick`: PASS in 25 s; safe across the reboot (one effect per identity, no
  false STOPPED or APPLIED); the durable cutoff (8) and the tombstone (c9 STOPPED) survived; every other
  identity at or below the claimed block answered UNCERTAIN (c2, c3, c7, c10, c11, c13: never executed
  again, never certified), a new fence completed, and c70, above the block, was applied.
- `report`: the first attempt with `--sources-of <the 074 simulated run>` stopped because that report
  names runs no longer on this machine (the check works as intended); with the same runs given by
  `--from` (the 074 simulated run and the N6 run): PASS; this plan's analysis on the C3 run: C3-S MET,
  C3-1 to C3-6 NOT COMPUTABLE (one episode); `expC3/`, `safety/`, `claims/`, `reboot/`, `tab_expC3.tex`
  and the `\evCthree*` and `\evSafety*` macros written. `bundle --no-remote`: PASS.

**Offline reproduction.** `scripts/make_data_archive.py pack` of the N5 (073) and N6 (074) run
directories (the credential scan found nothing), then `reproduce` from the archive alone in a fresh
copy of this tree: `report` PASS in 89 s (the C and C2 analyses, safety total, claims).

**Changes made because of these checks** (before freezing): the C3 runner's config check called itself
once installed in place of the frozen runner's (infinite recursion); it now keeps a reference to the
frozen function (test E-R2). Not frozen files: `expC2_summary.py` counts the direct channel's dropped
messages; `claims_check.py` reports a simulator claim with no scored effect as NO DATA (it said NOT
SUPPORTED); `paper_tables.py` orders the C3 table by channel and evidence.

## Deviations

None so far.

# Preregistration: experiment C2, EviCon on a real IoT stack (Zephyr firmware behind Mosquitto) on the 5-VM testbed

Release 074. Frozen before any C2 cell runs on the testbed. The campaign config below carries the
SHA-256 of this file, and `evicon_review.py campaign` refuses to run it if this file, or any file
listed under *Frozen files*, has changed. Changes after freezing are recorded under *Deviations*;
nothing above that section is edited.

## 1. Background (exploratory evidence, not part of the test)

Experiment C (release 073, 23/10) ran the EviCon runtime on contracts measured on MinIO and met every
hypothesis of its plan: on the unconditional PUT (K0) EVICON reached 22.7 % clean fulfilment, on the
conditional PUT (K3: D_h, Q_A, Q_S) 76.1 % (+53.4 pp, 95 % [43.5, 63.3]); a blind retry duplicated 70
real objects on K0 and none on K3; no safety violation wherever the T4 guard was on. MinIO is an object
store, not an IoT device. C2 asks the same questions on an IoT command path end to end: an MQTT broker
(Mosquitto 2.0.20, VM-B) and an actuator firmware on a real RTOS (the EviCon firmware,
`firmware/zephyr-cutoff`, Zephyr v4.2.0, `native_sim/native/64`: the same C code as the nRF52840 build
whose footprint the paper reports, run as Linux processes on VM-E), whose effects go to the independent
truth plant (`evicon-plant`) as in every emulated campaign.

**One firmware, three evidence modes.** Release 074 makes the device ID, the broker, the topic prefix
and the evidence mode command-line options of the firmware (`-evc_mode=plain|cutoff|stop`); the
decision core (`evc_core.c`) and the message format (`evc_proto.c`) are those measured in releases
068-070, unchanged. `cutoff` is the firmware as measured in 070 (dedup ring, receipt query, durable
cutoff fence); `stop` adds the per-identity stop (durable tombstones, already in the core behind
`EVC_F_STOP`); `plain` executes every delivered command and answers APPLIED, with no dedup, no query,
no fence and no stop (an MQTT command handler without an EviCon core). The contract of each mode is
**measured** by EviCon-Probe in the same review run, on the same firmware binary, before the campaign
(`measure zephyr`, `measure zephyr-stop`, `measure zephyr-plain`; configs `conformance/configs/
zephyr-mqtt*.json`). The C2 campaign runs only if the measured contracts are the ones this plan
expects (section 2); the measured files are copied into the campaign directory and advertised by the
gateways. Expected, from the 070 measurement and a local measurement of the 074 binary (Zephyr v4.2.0,
Mosquitto 2.0.18, one repetition, P5 shortened to 5 s, so D_h is a lower bound of 5 s there):

| Mode | Expected measured contract | Level | Local measurement of the 074 binary |
|---|---|---|---|
| plain | none | K0 | none (D_h contradicted: P4, P2, P6, P8 duplicate) |
| cutoff | D_h (lower bound 600 s), Q_A, F | K3 | D_h, Q_A, F |
| stop | D_h (lower bound 600 s), Q_A, Q_S, F | K3 | D_h, Q_A, Q_S, F |

Commands are published with **QoS 1**, as in the 068-070 measurement and on the usual IoT command
channels (AWS IoT Core and Azure IoT Hub offer QoS 0 and 1 only). QoS 1 is at least once: after a
reconnect the client library re-sends a publish whose PUBACK it has not seen. In C2 the gateway's
connections are reset only at the start of an offline window, so such a re-send needs a command in
flight at that instant (a LAN round trip, about a millisecond). A local trial with QoS 2 was rejected
before freezing: under QoS 2 the broker releases a command only after the publisher's PUBREL, so a lost
reply path also holds the command, and the probe could not observe Q_A (P2 had no positive control).

**Consequences fixed before the run.** As in C, every campaign assumes no bound on the network delay
(`max_network_delay` -1ns): with a finite measured horizon T4 allows no same-identity retry, so
EVICON cannot use D_h and resolves ambiguous commands with Q_A (APPLIED), F (a cutoff at the full
frontier, then STOPPED for every identity the firmware certifies as never started, and a new identity)
and, in the stop mode, Q_S. M1 queries and neither stops nor fences. The cutoff firmware keeps its
receipts in a RAM ring of 32 entries: an identity whose APPLIED receipt left the ring before anyone saw
it is UNCERTAIN to the firmware (its guard never executes it again), and a barrier that would cover it
aborts (`BARRIER_UNCERTAIN_RECEIPTS`). The gateway keeps every terminal receipt the firmware returned
(an IoT platform's command log) and reports, for a barrier, a receipt for every identity it forwarded at
or below the cutoff (`conformance/reference/mqtt_owner_gateway.py`), so the authority never seals an
applied identity it was not told about.

**Simulator prediction** (`evaluation/sim_expC2.py`, release 074 simulator, twin
`experiments/sim/iot_dosing_testbed.json` with window 6, queue owner channel, no assumed delay bound,
the expected contracts `experiments/owners/zephyr/pred-*.json`, 200 episodes; `SIMULATION_ONLY`).
Clean / confirmed clean fulfilment, % of offered:

| Mode | EVICON | M1 | NAIVE (simulator: B1_DURABLE_RETRY) | ORACLE |
|---|---|---|---|---|
| plain | 37.5 / 34.3 | 37.5 / 34.3 | 69.4 / 69.4, **19,816 safety violations in 200 of 200 episodes** | 76.8 |
| cutoff | 76.6 / 76.2 | 37.5 / 34.3 | 76.8 / 76.4, 0 violations | 76.8 |
| stop | 76.8 / 76.5 (41.7 stops per episode) | 37.5 / 34.3 | 76.8 / 76.4, 0 violations | 76.8 |

Mean per-episode effect for EVICON: plain -> cutoff +39.6 pp clean (SD 24.6), +42.4 pp confirmed
(25.2); cutoff -> stop +0.3 pp (SD 2.8).

**The emulated testbed disagrees with the simulator about the fence.** At the same window and channel
the emulated owner gave, for EVICON (`expW-queue-w6`, `expS-w6`, release 072, seed 20261017, 12
episodes): K0 29.0 %, K0F (fence only) 59.7 %, K0S (stop only) 77.6 %; the simulator predicted 37.5,
72.7 and 76.9. The simulator over-predicts the fence by 13 pp and predicts almost no stop-over-fence
gain (+4.2 pp), where the testbed found +21.1 pp (95 % [14.7, 27.5]). C2's cutoff -> stop comparison
(C2-5) is therefore a test between the two: the simulator predicts no difference, the emulated
testbed a large one.

**Pre-release checks.** See section 9 (written before freezing; local and simulated-testbed runs, not
results).

## 2. Design

| Item | Value |
|---|---|
| Config | `evaluation/campaigns/expC2_zephyr.json` |
| Owner | per device one process of the Zephyr EviCon firmware (`zephyr-cutoff.exe`, `native_sim/native/64`, built from this release by `deploy/setup/build_iot.sh` on VM-D and copied to VM-E by the driver; its SHA-256 is recorded) in the evidence mode of the cell's contract, a fresh flash file and its own topic prefix per cell; Mosquitto `eclipse-mosquitto:2.0.20` on VM-B in EviCon's compose project `evicon-probe-owner`, started by the driver for the campaign and brought down afterwards; `evicon-plant` on VM-E (TCP line protocol on 127.0.0.1:18093) is the truth |
| Gateway | one `conformance/reference/mqtt_owner_gateway.py` per device on VM-C: the EviCon device-owner protocol (`/v1/effects`, `/v1/receipts/<id>`, `/v1/stop`, `/v1/barriers`, `/v1/capabilities`) mapped to MQTT 3.1.1 messages on two persistent-session connections (data: commands, QoS 1; control: query, fence, stop, ping), advertising the measured contract of its mode; a durable record of the identities it forwarded and the terminal receipts the firmware returned |
| Contracts | `ZEPHYR_PLAIN` (firmware `-evc_mode=plain`, measured by `measure zephyr-plain`, expected none), `ZEPHYR_CUTOFF` (`cutoff`, `measure zephyr`, expected D_h, Q_A, F), `ZEPHYR_STOP` (`stop`, `measure zephyr-stop`, expected D_h, Q_A, Q_S, F). Gate before the campaign (driver): each measured contract is MEASURED, has exactly the expected capabilities and the measured fault model, and was measured on the firmware binary the campaign runs; otherwise no cell runs and the deviation is recorded |
| Policies | **EVICON**; **M1** (legacy selector: queries, no stop or fence, T4 guard on); **NAIVE** = M1 with the T4 retry guard off (a blind same-identity retry; the real-system negative control on the plain mode). M1 and NAIVE are not run on the stop mode (they never stop; their cells would repeat the cutoff mode's) |
| Regime | identical to `expW_queue_w6` / `expS_w6` (release 072): 4 devices; 300 s per cell + 60 s tail; 0.5 commands/s per device; values 1-5; bound 345 per device; deadline 60 s; validity 90 s; fault windows 2/min; offline 10-40 s; mix offline 0.3, drop_up 0.2, drop_down 0.2, delay_up 0.1 (6 s), crash 0.2; the same authority parameters; window **6**; **the same seed, 20261017**: episodes 0-7 replay exactly the arrivals and fault windows of those runs' episodes 0-7 (common random numbers across runs, used by the fidelity estimands E-C2-6) |
| Faults | drop_up, drop_down, delay_up: on the authority -> gateway path (the per-device fault proxy of `campaign.py`). crash: the gateway exits after the firmware answered APPLIED and before it replies, restarted after 2 s with its durable record and a new MQTT client ID (a client abandon). offline: the gateway's link to the broker is down: both MQTT connections are reset and new ones refused; the client library keeps what is published meanwhile and sends it after reconnecting, so commands arrive late (a queue channel, as `expW_queue_w6`). The firmware and the broker never crash or restart: the fault model of the measurement, "network loss/delay, client abandon; owner up" |
| Truth | `evicon-plant` on VM-E (`campaign.collect_live`), as in every emulated campaign; duplicate effects, bound violations (limit 345 per device), late effects as there |
| Episodes | **8** (0-7); 3 contracts x 3 policies minus 2 = 7 groups, **56 cells**, about 6.7 hours (plus about 1.3 hours for the three measurements) |
| Pairing | contracts of the same episode and policy (common random numbers) |
| Activation gate | none |
| Analysis | `evaluation/pairs_report.py --plan evaluation/prereg/PREREG_expC2_074.md --run C2=<run> --run W=<expW-queue-w6 run> --run S=<expS-w6 run> --out <dir>` with the spec in section 8; `evicon_review.py report` runs it with the earliest complete runs among its sources |

## 3. Hypotheses and decision rules

Tests of `pairs_report.py` on the per-episode paired differences (to minus from), Student t,
alpha = 0.05; C2-3, C2-4 and C2-5 form one Holm family.

| ID | Hypothesis | Decision rule |
|---|---|---|
| **C2-1** | On the plain firmware a blind same-identity retry duplicates real effects (negative control on the IoT stack) | `detects`: some VALID `ZEPHYR_PLAIN`/NAIVE cell has duplicate effects or a bound violation |
| **C2-2** | The cutoff firmware absorbs the same blind retries (its dedup ring and eviction guard) | `absent`: no VALID `ZEPHYR_CUTOFF`/NAIVE cell has duplicate effects, bound violations, logical duplicates, false STOPPED or false APPLIED |
| **C2-3** | With EVICON, the cutoff firmware raises clean fulfilment by at least 10 pp over the plain one | `at_least 10`, EVICON, `ZEPHYR_PLAIN -> ZEPHYR_CUTOFF`, `clean_pp` |
| **C2-4** | ... and confirmed fulfilment by at least 10 pp | `at_least 10`, EVICON, `ZEPHYR_PLAIN -> ZEPHYR_CUTOFF`, `confirmed_pp` |
| **C2-5** | With EVICON, adding the per-identity stop to the cutoff firmware raises clean fulfilment (the emulated testbed's stop-over-fence gain, against the simulator's no difference) | `at_least 0`, EVICON, `ZEPHYR_CUTOFF -> ZEPHYR_STOP`, `clean_pp` |
| **C2-S** | Safety on the IoT stack wherever the T4 guard is on | five `absent` checks (C2-S1 PLAIN/EVICON, C2-S2 PLAIN/M1, C2-S3 CUTOFF/EVICON, C2-S4 CUTOFF/M1, C2-S5 STOP/EVICON): bound violations, duplicate effects, logical duplicates, false STOPPED, false APPLIED all 0. Late effects are reported separately |

Interpretation fixed in advance:
- C2-1 DETECTED and C2-2 ABSENT: on one firmware, the evidence the device offers decides whether a
  client's retry is safe, as the call shape did on MinIO; the measured contract says which. C2-1 NOT
  DETECTED: no blind retry hit an applied command in this regime (reported; C and the 072 controls
  already showed the checker detects duplicates). C2-2 PRESENT: the firmware's dedup failed under
  these faults, a contradiction of its measurement; reported with every C2 result.
- C2-3 and C2-4 MET: the measured contract predicts what the runtime gains on a real IoT stack. NOT MET:
  reported with the estimate.
- C2-5 MET: on the real stack, as on the emulated owner, the stop is worth more than the fence; the
  simulator's model of the fence is too optimistic and the paper says so. NOT MET: the real stack sides
  with the simulator (the fence is enough when the owner certifies every identity below the cutoff);
  reported with the estimate and the fence statistics (E-C2-2).
- C2-S PRESENT anywhere: a safety failure of EviCon on the IoT stack; reported with the cells and the
  traces, and the plan of record stops until the cause is known. A duplicate effect in a guarded plain
  cell is classified (exploratory) by the dispatch attempts of the identity: one attempt means a
  client-library re-send of QoS 1 after a reset (section 1), more than one a dispatcher re-send. The
  decision of C2-S does not change with the classification.

## 4. Estimands (reported with intervals, no claims)

| ID | Estimand |
|---|---|
| E-C2-1 | EVICON, `ZEPHYR_PLAIN -> ZEPHYR_CUTOFF`: admitted, stranded capacity-time (resolved, censored), mean time to a terminal outcome, queries, fences, fences that reached the firmware and those that completed, re-issues, unresolved at the end |
| E-C2-2 | EVICON, `ZEPHYR_CUTOFF -> ZEPHYR_STOP`: confirmed fulfilment, fences, fences reached / completed, stops, stops reached / terminal, mean time to a terminal outcome, unresolved at the end |
| E-C2-3 | M1, `ZEPHYR_PLAIN -> ZEPHYR_CUTOFF`: clean and confirmed fulfilment, queries, unresolved at the end (Q_A without F or Q_S) |
| E-C2-4 | NAIVE, `ZEPHYR_PLAIN -> ZEPHYR_CUTOFF`: clean and confirmed fulfilment, duplicate effects, bound violations |
| E-C2-5 | Levels per contract and policy: EVICON against M1 and NAIVE on the same mode; the testbed against the simulator prediction of section 1 (`predict_check.py`) |
| E-C2-6 | Fidelity of the emulated owner (same seed, episodes 0-7, paired): EVICON `K0` of `expW-queue-w6` against `ZEPHYR_PLAIN` (same contract, K0); `K0F` of `expW-queue-w6` against `ZEPHYR_CUTOFF` and `K0S` of `expS-w6` against `ZEPHYR_STOP` (contracts differ: the real modes add D_h, unusable here, and Q_A); clean and confirmed fulfilment |

## 5. Sample size

From the simulator's 200 per-episode paired differences (resampling n episodes, Holm over C2-3 to
C2-5, 2,000 resamples; `SIMULATION_ONLY`):

| n | C2-3 | C2-4 | C2-5 |
|---|---|---|---|
| 6 | 0.52 | 0.60 | 0.01 |
| **8** | 0.79 | 0.85 | 0.02 |
| 10 | 0.91 | 0.95 | 0.04 |

The simulator's paired differences are bimodal (SD 24.6 pp: in some episodes no device locks up), far
more spread than the testbed's. From the emulated testbed's own per-episode differences (release 072,
EVICON; `K0 -> K0F` of `expW-queue-w6`, SD 12.4 pp; `K0F -> K0S` of `expS-w6`, SD 10.1 pp), resampled
the same way (4,000 resamples): power 0.997 for C2-3 and 1.00 for C2-5 at n = 8 (0.92 and 1.00 at
n = 6). Eight episodes, as in C. C2-5 has no power if the simulator is right; that is the point of the
test. C2-1 needs one detecting cell (the simulator's NAIVE violated safety in every episode).

## 6. Exclusions, stopping and order

- INVALID cells are excluded and listed (a fault could not be applied, a process died, a firmware
  process exited (the owner must stay up), the truth could not be read). An INVALID cell is re-run once
  by resuming the campaign; if it is INVALID again it stays excluded. A run with more than 10 %
  excluded cells is not interpretable and its hypotheses are not decided.
- No interim analysis: `pairs_report.py` is run only when every cell is done. The campaign may be split
  over several sessions (resume; Mosquitto is started again; every cell has its own topic prefix and
  flash files).
- Order in one review run: `measure zephyr-plain zephyr zephyr-stop`, then `campaign expC2_zephyr`. A
  new firmware build between them makes the campaign refuse to start (measured binary != run binary).
- The earliest complete run of `expC2_zephyr` is the preregistered one.

## 7. Frozen files

SHA-256 at freezing time (`scripts/freeze_prereg.py`); the driver checks them before running. The six
files frozen for experiment A are frozen again, unchanged; added through the `frozen-extra` block: the
analysis (`pairs_report.py`, `cadence_report.py`), the runner of this experiment and the frozen runners
it imports, the gateway and the code it runs, the fault proxy, the firmware sources, the three probe
configs, the probe adapter, probes and rubric that measure them, and the simulator's predicted contracts.

The Go runtime is built from release 074 (unchanged since 072 except `buildinfo.Release`); the run's
`source.json` records its source.

```frozen-extra
evaluation/pairs_report.py
evaluation/cadence_report.py
evaluation/campaign_zephyr.py
evaluation/campaign_real.py
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
15c46ffd982baacecbec4fce2ca39e7c6ba16fc3148bfa8fb0032bc4126947db  evaluation/campaign_zephyr.py
65eb699cf290296595eb084c031570a9edcd5004a2869de15b35c1f1ef958557  evaluation/campaign_real.py
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
```

## 8. Analysis spec (read by pairs_report.py)

```pairs-spec
{
  "name": "Experiment C2: EviCon on the Zephyr firmware behind Mosquitto (074)",
  "runs": {"C2": {"campaign": "expC2-zephyr"},
           "W": {"campaign": "expW-queue-w6", "reference": true},
           "S": {"campaign": "expS-w6", "reference": true}},
  "alpha": 0.05,
  "max_invalid_fraction": 0.10,
  "hypotheses": [
    {"id": "C2-1", "run": "C2", "test": "detects", "contract": "ZEPHYR_PLAIN", "policy": "NAIVE", "any_of": ["duplicate_effects", "bound_violations"]},
    {"id": "C2-2", "run": "C2", "test": "absent", "contract": "ZEPHYR_CUTOFF", "policy": "NAIVE", "any_of": ["duplicate_effects", "bound_violations", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "C2-3", "family": "primary", "run": "C2", "policy": "EVICON", "from": "ZEPHYR_PLAIN", "to": "ZEPHYR_CUTOFF", "metric": "clean_pp", "test": "at_least", "margin": 10.0},
    {"id": "C2-4", "family": "primary", "run": "C2", "policy": "EVICON", "from": "ZEPHYR_PLAIN", "to": "ZEPHYR_CUTOFF", "metric": "confirmed_pp", "test": "at_least", "margin": 10.0},
    {"id": "C2-5", "family": "primary", "run": "C2", "policy": "EVICON", "from": "ZEPHYR_CUTOFF", "to": "ZEPHYR_STOP", "metric": "clean_pp", "test": "at_least", "margin": 0.0},
    {"id": "C2-S1", "run": "C2", "test": "absent", "contract": "ZEPHYR_PLAIN", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "C2-S2", "run": "C2", "test": "absent", "contract": "ZEPHYR_PLAIN", "policy": "M1", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "C2-S3", "run": "C2", "test": "absent", "contract": "ZEPHYR_CUTOFF", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "C2-S4", "run": "C2", "test": "absent", "contract": "ZEPHYR_CUTOFF", "policy": "M1", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "C2-S5", "run": "C2", "test": "absent", "contract": "ZEPHYR_STOP", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]}
  ],
  "estimands": [
    {"run": "C2", "policy": "EVICON", "from": "ZEPHYR_PLAIN", "to": "ZEPHYR_CUTOFF", "metrics": ["admitted_pp", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s", "queries", "fences", "fences_reached", "fences_reached_ok", "reissues", "unresolved_at_end"]},
    {"run": "C2", "policy": "EVICON", "from": "ZEPHYR_CUTOFF", "to": "ZEPHYR_STOP", "metrics": ["confirmed_pp", "fences", "fences_reached", "fences_reached_ok", "stops", "stops_reached", "stops_reached_terminal", "mean_time_to_terminal_s", "unresolved_at_end"]},
    {"run": "C2", "policy": "M1", "from": "ZEPHYR_PLAIN", "to": "ZEPHYR_CUTOFF", "metrics": ["clean_pp", "confirmed_pp", "queries", "unresolved_at_end"]},
    {"run": "C2", "policy": "NAIVE", "from": "ZEPHYR_PLAIN", "to": "ZEPHYR_CUTOFF", "metrics": ["clean_pp", "confirmed_pp", "duplicate_effects", "bound_violations"]},
    {"run": "C2", "from_run": "W", "policy": "EVICON", "from": "K0", "to": "ZEPHYR_PLAIN", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "C2", "from_run": "W", "policy": "EVICON", "from": "K0F", "to": "ZEPHYR_CUTOFF", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "C2", "from_run": "S", "policy": "EVICON", "from": "K0S", "to": "ZEPHYR_STOP", "metrics": ["clean_pp", "confirmed_pp"]}
  ]
}
```

## 9. Pre-release checks (before freezing; not results)

Checks of the code path before freezing, on one machine and on a simulated testbed. They are not
results of C2: the regime, the broker build, the timing and the scale differ from the testbed run.

**Firmware.** The 074 firmware built with Zephyr v4.2.0 for `native_sim/native/64` (host toolchain,
no warnings) and passed the host tests (`firmware/zephyr-cutoff/tests/host`: core and parser with
ASan/UBSan; glue check of `main.c` with and without `CONFIG_ARCH_POSIX`). Each mode answered a ping
with its mode; an invalid `-evc_mode` or an out-of-range port stops the firmware; omitted options take
the Kconfig defaults. The local measurement and the local campaign below ran a build whose only
difference to the frozen `main.c` is the handling of an omitted port option (it then used port
4294967295, the option parser's reset value; every run passes both ports explicitly); the
simulated-testbed run used the frozen sources.

**Local measurement** (one machine, Mosquitto 2.0.18, one repetition, P5 shortened to 5 s, so D_h is
a lower bound of 5 s): plain: no capability (D_h contradicted: P4, P2, P6, P8 duplicate); cutoff: D_h,
Q_A, F; stop: D_h, Q_A, Q_S, F; fault model "network loss/delay, client abandon; owner up (no owner
crash or reboot)" in all three. The firmware process stayed up through every probe.

**Local campaign** (one machine, the 074 firmware, local Mosquitto 2.0.18, `campaign_zephyr.py` with a
copy of the config shortened to 1 episode of 90 s + 15 s tail, 6 fault windows/min, offline 5-12 s,
deadline 30 s, validity 45 s; seed 20261017; 4 devices; 184 commands offered per cell):

| Mode | Policy | Clean | Duplicate effects | Unresolved at end | Queries | Fences | Stops |
|---|---|---|---|---|---|---|---|
| plain | EVICON | 74 | 0 | 24 | 0 | 0 | 0 |
| plain | M1 | 74 | 0 | 24 | 0 | 0 | 0 |
| plain | NAIVE | 152 | **32** | 0 | 0 | 0 | 0 |
| cutoff | EVICON | 184 | 0 | 0 | 104 | 12 (12 completed) | 0 |
| cutoff | M1 | 74 | 0 | 24 | 0 | 0 | 0 |
| cutoff | NAIVE | 184 | 0 | 0 | 0 | 0 | 0 |
| stop | EVICON | 184 | 0 | 0 | 98 | 0 | 18 (15 reached the firmware, all terminal) |

7 of 7 cells VALID; no bound violation, false STOPPED or false APPLIED anywhere; every barrier
completed. `pairs_report.py` with this plan on that run (with the N1 `expW-queue-w6` and N3 `expS-w6`
runs as W and S): C2-1 DETECTED, C2-2 ABSENT, C2-S1 to C2-S5 ABSENT, C2-3 to C2-5 NOT COMPUTABLE (one
episode); the cross-run estimands are empty there, as they must be (another schedule).
`expC2_summary.py` and `fidelity_check.py` ran on the same run.

**Common random numbers.** `campaign.schedule` of this config gives, for episodes 0-7, files
byte-identical to `schedule_ep0.json` ... `schedule_ep7.json` of the N1 `expW-queue-w6` run and the N3
`expS-w6` run (8 of 8 episodes; 553-612 arrivals and 28-40 fault windows per episode).

**Simulated testbed** (the driver end to end on one machine: sshd on five loopback addresses as VM-A
to VM-E, Docker as VM-B with a stand-in image tagged `eclipse-mosquitto:2.0.20` that runs Mosquitto
2.0.18; the firmware built from the frozen sources, SHA-256 `9fe7b7d0...`, placed as VM-D's build
output; a copy of this tree with this plan frozen):
- `preflight build verify distribute --new --quick`: PASS (65 conformance tests, among them the gateway
  tests Z1-Z9; 78 analysis tests, among them Z-R1 to Z-R3 and the release-074 plan tests; firmware host
  tests). The first two attempts failed in `verify`: the firmware glue check (stub headers, fixed below)
  and the plan test that needs a frozen plan (the copy is now frozen first); a third was stopped
  after `verify` to fix the port default (below) and run again with the fixed firmware.
- `measure zephyr-plain zephyr zephyr-stop --quick`, one binary: plain none (K0); cutoff D_h (600 s),
  Q_A, F (K3); stop D_h (600 s), Q_A, Q_S, F (K3); the planned fault model in all three.
- `campaign` of a copy of this config without the preregistration, 1 episode of 180 s + 60 s tail,
  the planned regime otherwise (365 commands offered per cell): the gate accepted the three measured
  contracts and the binary; 7 of 7 cells VALID; driver PASS ("safety violations only where planned").
  Clean fulfilment: plain EVICON 38.4 %, M1 38.4 %, NAIVE 86.0 % with **51 duplicate effects and 2 bound
  violations**; cutoff EVICON 100 % (172 queries, 10 fences, all completed, 0 uncertain receipts), M1
  38.4 %, NAIVE 100 % with 0 duplicates; stop EVICON 100 % (161 queries, 17 stops, 16 terminal, no
  fence). No late effect, false STOPPED or false APPLIED; clock offsets 0.1 ms.
- `report --sources-of <the 073 simulated run> --from <the N5 bundle>`: PASS; this plan's analysis on
  that run: C2-1 DETECTED, C2-2 ABSENT, C2-S1 to C2-S5 ABSENT, C2-3 to C2-5 NOT COMPUTABLE (one
  episode). `bundle --no-remote`: PASS.
- `expC2_summary.py` counted the gateways' events three times on this single-host testbed (the
  runner copies each host's cell directory, and there all hosts share one); it now reads the copy from
  VM-C only (`raw/C`), local runs `raw/`. Not a frozen file; the pre-release numbers above come from
  the runner's rows.

**Cross-run pairing.** A synthetic run made of the N1 `expW-queue-w6` (K0, K0F) and N3 `expS-w6`
(K0S) EVICON cells of episodes 0-7, renamed to the three modes, gave through this plan's analysis the
three cross-run estimands of E-C2-6 with n = 8 paired episodes and difference 0, and through
`fidelity_check.py` the paired comparison with n = 8.

**Changes made because of these checks** (before freezing): commands at QoS 1 instead of QoS 2
(section 1); probe P9 added to the stop mode's configuration (without it Q_S was not observed); the
host stubs of the firmware glue check extended (`ARRAY_SIZE`, `K_MSEC`, `k_panic`, `LOG_ERR`, and
`cmdline.h`, `soc.h` for the native_sim pass; log macros format-checked); an omitted port option
takes the Kconfig default (it took the option parser's reset value, 4294967295).

## Deviations

None so far.

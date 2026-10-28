# Preregistration: the review experiments of release 077 (real owners at an unbounded window and at W = 16, an owner without crash orphans, K2 on the release-077 runtime, a milder regime, a WAN, two practice baselines, sleepy devices with the validity cut of resends, the delivery binding of MQTT commands, a device-management platform, scale, a latency control, the firmware on an emulated MCU and with authenticated messages)

Release 077. Frozen before any of its cells runs on the testbed. The seventeen campaign configs below carry
the SHA-256 of this file, and `evicon_review.py campaign` refuses to run them if this file, or any file
listed under *Frozen files*, has changed. Changes after freezing are recorded under *Deviations*;
nothing above that section is edited.

## 1. Background (exploratory evidence, not part of the test)

An internal review of the paper draft (written as a journal referee would) raised points that only new
runs can answer. Each run below answers one of them; the numbers quoted are from the runs of
releases 067-075 as analysed by the release-076 report (N8, `report/pairs/*/cells.csv`), EVICON,
episodes 0-7 unless stated.

**(a) Window.** The headline effects of backward evidence were measured at a window of W = 6 unresolved
commands per device. On the emulated owner the window decides the size of the effect: on the queue
channel the fence adds +30.7 pp at W = 6 (`expW-queue-w6/20261021T194415`, K0 29.5 % -> K0F 60.3 %) and
+0.7 pp at W unbounded (`expW-queue-winf/20261022T021601`, 77.6 -> 78.3 %, but +11.0 pp of *confirmed*
fulfilment, 59.3 -> 70.3 %, over 12 episodes); on the direct channel +31.7 pp at W = 6
(`expW-direct-w6/20261022T080622`) and +15.7 pp at W unbounded (`expW-direct-winf/20261022T130854`,
57.5 -> 73.2 %). The real owners ran at W = 6 only: MinIO PUT without / with `If-None-Match`
(`expC-minio/20261023T083658`, direct: a lost PUT never takes effect) 22.7 -> 76.1 % (+53.4 pp); the
Zephyr firmware behind Mosquitto, plain -> cutoff on the queue channel (`expC2-zephyr/20261024T044342`)
29.8 -> 79.3 % (+49.5 pp), plain -> {F} on the direct channel (`expC3-ablation/20261024T161324`) 25.4
-> 79.1 % (+53.7 pp). In the plain-mode cells most DEFER decisions were window-bound. **Runs Rm and Rz**
repeat the real-owner cells at W unbounded (`max_outstanding` 1000; the per-device bound of 345 units
then caps the reservations first), with the same seeds and therefore the same arrivals and fault
windows per episode as C, C2 and C3.

**(b) Crash orphans.** On the emulated owner a stop beat a fence by +20.7 pp at W = 6 (`expS-w6/
20261022T161003`, K0F 58.7 % -> K0S 79.4 %); on the firmware, which never crashes between effect and
receipt, the two were equivalent (C3: F_QUEUE -> QS_QUEUE +0.2 pp). The paper explained the gap by crash
orphans (a claim without a receipt aborts every fence whose frontier covers it, but not a stop of
another command); that was an inference from two different owners. **Run O** tests it on one owner:
experiment S's cells at W = 6 (same seed 20261017: episodes 0-7 replay `expS-w6`'s schedules byte for
byte), with every injected owner crash moved after the durable receipt (`evicon-device
-crash-point-override AFTER_RECEIPT_BEFORE_REPLY`, release 077; device test `TestEffectiveCrashPoint`).
The owner still crashes and restarts in every crash window, and the reply of the command in flight is
still lost, but no claim is left without a receipt. Everything else is S's.

**(c) Experiment A's K2 configuration on the release-077 runtime.** Experiment A's K2 configuration
(`expA-confirm-k2/20261017T111506`: K2 78.2 %, K3F 78.2 %, EVICON; window 16 with the 256-configuration
cap, an effective window of about six) ran on the release-067 runtime. **Run K2** repeats it on the
release-077 runtime, the runtime of every run of this plan, with an explicit window of 6 and the validity
cut of resends of section 1(j) (same seed 20261018, same arrivals and fault windows; K2, K3F; EVICON and
RES_PERIODIC). With h = infinity (K2's D_h) the dispatcher re-sends an unresolved command under its own
identity while the cut allows it; the owner's deduplication makes that safe, and late effects are
reported separately.

**(d) One regime.** Every runtime experiment used one fault regime (fault windows twice a minute, a
quarter of the time offline, owner crashes in a fifth of the windows). **Run V** is a second testbed
point with half the fault rate (one window per minute per device, the same mix and durations, so about
half the offline time and half the crashes), K0, K0F, K0S at W = 6, new seed 20261026. The simulator
sweep of section 1(h) gives the dependence on each factor.

**(e) Practice baselines.** The baselines of the draft were ablations of EviCon. Release 077 adds two
policies that model what a client does without evidence contracts, both UNSAFE BY DESIGN (they break
rule R2: they commit STOPPED without owner evidence of non-occurrence) and run only in their own cells:

- `RELEASE_ON_TIMEOUT`: a command still unresolved 10 s after its creation is committed STOPPED, its
  reservation is released, and the client re-issues the intent under a new identity (a client that
  treats a timeout as a failure). Runtime test F44. **Runs RTq and RTd**: K0 and K0F on the queue
  channel, K0 on the direct channel, W = 6, seed 20261017 (episodes 0-7 replay the schedules of
  `expW-queue-w6` and `expW-direct-w6`, whose EVICON cells are the comparison).
- `RELEASE_ON_READ`: state-read reconciliation, as a client reading a device shadow. 10 s after its
  creation a command without terminal evidence is looked up at the owner (`GET /v1/receipts/<id>`); a
  valid APPLIED or STOPPED receipt is recorded; a command the owner does not report (NOT_FOUND, or the
  owner cannot be reached, so its reported state does not show the effect) is committed STOPPED and its
  intent re-issued; an UNCERTAIN answer is read again on the next sweep. Runtime tests F45, F46. **Runs
  RRq and RRd**: contract K2 (D_h, Q_A: the owner answers receipt reads), against EVICON in the same
  run, W = 6, queue and direct channel, seed 20261017, 5 episodes.

The review also named idempotency-key retries; on the measured owners that practice is the BlindRetry
policy of experiments C and C2 (a same-identity retry with the guard off), which duplicated effects
wherever the owner does not deduplicate. It is not repeated here.

What the theory predicts. A release without evidence of non-occurrence is unsafe whenever a released
command can still take effect, or already has: on the queue channel (a request held while the owner is
offline arrives late) for both baselines; on the direct channel for RELEASE_ON_TIMEOUT (a command whose
reply was lost has taken effect). For RELEASE_ON_READ on the direct channel the answer depends on the read
itself. On the testbed's direct channel a request is either lost or delayed by at most 6 s (`delay_ms`
6000), less than the 10 s before the read, so a read that the owner answers is evidence (absence after a
known delay bound: the forward-path proposition with a finite delta); but a read that the owner does not
answer (offline, or inside a drop window) says nothing, and the baseline, like a client whose shadow has
not been updated, takes it for absence and releases a command that may have taken effect. The plan
therefore preregisters detection on the queue channel and reports the direct channel without a
directional claim (RR-2), with the share of releases that followed an unanswered read (E-P).

**(f) Latency.** The RQ4 decomposition attributed the growth of the admission latency with the load to
the `client` component (scheduled arrival -> the authority's handler starts: load generator and
network). **The latency control** (`evicon_review.py latency-control`, `evaluation/latency_control.py`)
replays the three RQ4 loads (1, 5 and 20 commands/s per device, 4 devices, the same seeded arrivals and
the same generator design as `campaign.py`) from VM-C against `evicon-noop` on VM-A, an endpoint that
admits every command at once, and splits the latency into the generator (one clock), the network (two
clocks, chrony) and the endpoint. Exploratory; no hypothesis.

**(g) Simulator prediction** (`evaluation/sim_review077.py`, release 077 simulator, twin
`experiments/sim/iot_dosing_testbed.json`, no assumed bound on the network delay, 200 episodes;
`SIMULATION_ONLY`; `evaluation/sim_predictions/review077_summary.csv`, `review077_power.csv`,
`SIM_REVIEW077.md`). Clean / confirmed fulfilment of EVICON, % of offered:

| Scenario (run) | Cells |
|---|---|
| unbounded_minio (Rm) | MINIO_PLAIN 62.6 / 61.1; MINIO_COND 77.2 / 77.2 |
| unbounded_zephyr (Rz) | ZEPHYR_PLAIN (queue) 75.6 / 62.1; ZEPHYR_CUTOFF 77.0 / 77.0; ZEPHYR_PLAIN_DIRECT 62.6 / 61.1; ZEPHYR_F_DIRECT 77.1 / 77.0 |
| noorphan (O; the twin without crashes) | K0 38.4; K0F 77.0; K0S 76.9 (with the twin's crashes: 37.5, 72.7, 76.9) |
| k2rep (K2) | K2 76.8, K3F 76.9 (EVICON and RES_PERIODIC alike) |
| mild (V; every fault rate of the twin halved) | K0 55.0; K0F 76.4; K0S 77.0 |
| wan (WAN; the twin with 100 ms +/- 25 ms one way added to every device request and reply, 200 ms to every query and control round trip) | K0 37.1; K0F 72.4; K0S 76.9 |
| release_timeout (RTq, RTd; the twin's B0_NAIVE_NEW_ID with a 10 s timeout) | K0 queue 69.8 % clean, 7,156 safety violations in 200 episodes; K0F queue the same; K0 direct 75.7 %, 1,275 violations |

The twin has no model of RELEASE_ON_READ, of the owner's crash-point override (it predicts the
no-crash limit), of the MQTT delivery binding, of ThingsBoard, of the validity cut of resends, or of the
latency control. Mean per-episode effects and their SD are in section 5.

**(h) Simulator sensitivity** (`evaluation/sim_sensitivity077.py`, exploratory, `SIMULATION_ONLY`;
`evaluation/sim_predictions/sensitivity077_summary.csv`, `SENSITIVITY077.md`): one factor at a time
around the twin (all fault rates scaled by 0.25-2; the offline share 0-40 %; the mean outage 5-120 s;
the crash rate 0-0.02 per request), at W = 6 and unbounded, queue and direct, K0, K0+F, K0+Q_S, 100
episodes per point. Reported in the paper as the dependence of the effects on the regime; no claim. What it
shows (EVICON, clean fulfilment): at W = 6 the effect of a fence over no evidence grows with the fault
scale (queue +11.7 pp at a quarter of the twin's rates, +21.7 at half, +36.7 at the twin's, +42 to +44 at
1.5-2 times) and with the offline share (+5.5 pp with no outages, +17 at 5 %, +47 at 40 %); at W unbounded
it stays near 0 on the queue channel (at most +3.3 pp) and grows on the direct channel (+4.0 to +26.0 pp
over the fault scale); the stop's advantage over the fence is 0 without crash orphans (crash rate 0:
-0.1 / +0.1 pp at W = 6) and grows with the crash rate (+15.6 / +23.4 pp at 0.02); longer outages with the
same offline share reduce every effect (queue, W = 6: +45 pp at 5 s, +15 pp at 120 s). No safety violation
at any point. Half the twin's rates (scale 0.5, the simulator's counterpart of run V) gives +21.7 pp
(queue, W = 6) for the fence and +22.1 pp for the stop.

**(i) Action costs** (`evaluation/sim_theta077.py`, exploratory, `SIMULATION_ONLY`;
`evaluation/sim_predictions/theta077_summary.csv`, `THETA077.md`). EVICON resolves an ambiguous command by
the ski-rental rule: it holds the reserved capacity and acts once the holding cost paid reaches the cost
of the action (theta = action cost / holding cost). Every campaign uses the twin's costs (holding 1 per
unit-second, stop 1, fence 5). The sweep varies one cost at a time around them (fence 0.5-100, stop
0.1-10, holding 0.25-4) and adds the immediate rule (theta = 0), at W = 6 and unbounded, queue channel,
for K0+F, K0+Q_S and K0+Q_S+F, 100 episodes per point. What it shows (EVICON): clean fulfilment hardly
depends on the costs. With Q_S (K0+Q_S, and K0+Q_S+F, where the stop is the cheaper action at every point
and no fence is issued) it is 76.0 % at W = 6 and 75.7 % at W unbounded at every value, the immediate rule
included; with the fence alone (K0+F) it moves from 73.2 % (fence cost 0.5) to 71.0 % (fence cost 100) at
W = 6 and from 75.8 % to 75.6 % at W unbounded. What the costs change is how long reserved capacity is held
before the action: for K0+F 13.7 unit-seconds per episode at fence cost 0.5, 22.5 at the twin's 5 and 75 at
100; for K0+Q_S 23 at stop cost 0.1 and 38 at 10. The results depend on theta only (holding cost h with
fence cost 5 gives the results of holding 1 with fence cost 5/h). No safety violation at any point. The
campaigns' costs are therefore not a tuned choice: any theta in the swept range gives the same clean
fulfilment within 2.2 pp.

**(j) The retry rule and sleepy devices.** The T4 guard lets the dispatcher re-send a command under its
own identity when the owner deduplicates (D_h); up to release 076 it did so for as long as the command was
unresolved, also after the intent's validity had ended. Experiment L (`expL-sleepy/20261023T*`, release
072: devices offline 60-180 s on the queue channel, longer than the 90 s validity) found late effects in
every K2 cell (203 in 8 episodes) and none under K3T; the review asked which of them the runtime caused by
re-sending after the validity, and which were requests the channel held. Release 077 (a) cuts the resends at
a validity bound (`evicon-authority -resend-until`, runtime test F47: the forward-retry rule with the
intent's validity, Eq. 4's V), and (b) records, for every effect, which dispatch attempt took effect (the
dispatcher sends its attempt number in the header `X-EviCon-Attempt`, the device logs it,
`evaluation/late_attribution.py` joins it with the authority's dispatch times and the plant). The authority
knows a command's creation, not its intent's arrival; the client submits an intent no later than its
deadline, so the cut is validity - deadline = 30 s after the creation, which is never later than the
intent's validity. **Run L2** repeats experiment L (same seed 20261024, episodes 0-7 replay L's schedules)
on the release-077 runtime with the cut. The configs whose owner deduplicates (K2 and RR) get the same cut.

**(k) The delivery binding of MQTT commands.** The C2/C3 gateway publishes commands at QoS 1 on a
persistent session, as IoT command channels do. QoS 1 is at least once: when the link is reset after the
broker took a command and before its acknowledgement reached the gateway, the client library sends it
again after reconnecting and the broker forwards it again. On a firmware without deduplication (the plain
mode) that is a second effect the runtime never dispatched: the adapter premise DRIVER_NO_DUPLICATE_EFFECT
of the contract model is broken below the runtime. In C2/C3 the gateway's link was reset only at the start
of an offline window, so a duplicate needed a command in flight at that instant (none was seen).
Release 077 measures the effect directly (`evicon_review.py qos-dup`, `conformance/reference/
qos_dup_check.py`: commands through a link that loses the broker's acknowledgement of every fifth command
and resets, a subscriber in the firmware's configuration counting deliveries, at QoS 1 and QoS 0, against
the testbed's Mosquitto) and adds a binding rule to the gateway (`mqtt_owner_gateway_ext077.py`: commands
at QoS 0, at most once, for a contract without D_h; QoS 1 with D_h, whose deduplication absorbs a
redelivery). **Run Q** puts the rule to the test on the firmware: every offline window of the queue
channel starts with a cut (the link fails right after a command reached the broker, its acknowledgement
lost), the plain firmware at QoS 1 (the premise broken on purpose: a planned violation), the plain
firmware under the rule (QoS 0), and the cutoff firmware under the rule (QoS 1), EVICON, W = 6, seed of
C2 (episodes 0-5 replay its schedules). The 077 gateway also arms the crash fault 100 ms after answering
the arming request (the 075 gateway armed it at once; an effect answered within that millisecond made
the gateway exit before its answer left, and the runner could not restart it); runs Q and Rz use it.

**(l) A device-management platform.** No cloud IoT account (AWS IoT, Azure IoT Hub) is available to the
testbed. ThingsBoard CE offers the same kind of command lifecycle as their command services: a
server-side RPC to a device is a persistent record (QUEUED, SENT, DELIVERED, SUCCESSFUL, TIMEOUT,
EXPIRED) that a client creates over REST and reads back, delivered over MQTT and expired at its
expirationTime. EviCon-Probe measured it in releases 068-076 as K2 {Q_A, T}: no D_h, a repeated request
is a new RPC. **Run TB** runs EviCon end to end on it: one ThingsBoard owner gateway per device
(`conformance/reference/tb_owner_gateway.py`: persistent two-way RPC without platform retries,
expirationTime = the request's expiry, Q_A by reading the RPC record or the device's RPC list), one
reference MQTT device per domain on VM-E executing every RPC it receives and writing the plant, offline
windows taking the device's MQTT connection to ThingsBoard down; EVICON, M1 and NAIVE (M1 without the T4
guard: a blind retry), W = 6, seed of C. The contract is measured in the same review run for that call
shape (`thingsboard-rpc-r0`, retries 0; the measurement of releases 068-076 used retries 2).

**(m) A WAN.** Every run had the authorities and the devices on one LAN. **Run WAN** repeats experiment S
at W = 6 (K0, K0+Q_S, K0+F; seed 20261017, episodes 0-7 replay S's schedules) with every request and reply
of the device API delayed 100 ms +/- 25 ms one way (`evicon-device -wan-delay`, a round trip of about
200 ms, a cellular IoT link), on top of the fault windows; paired per episode with S.

**(n) The window on a real owner.** C ran MinIO at W = 6 and run Rm runs it at W unbounded; **run C16**
adds W = 16 (EVICON, MINIO_PLAIN and MINIO_COND, seed of C), so the effect of the conditional PUT is
reported as a function of the window on the real owner.

**(o) Scale.** RQ4 measured the authority with 4 domains. **Run Scale** (`evicon_review.py scale`) repeats
the 5 commands/s per device load without faults with 8, 16 and 32 domains (one authority on VM-A and one
device on VM-E per domain, K2 and K0F, M0 and EVICON, 2 episodes of 120 s), with CPU/RSS sampling.
Exploratory; no hypothesis.

**(p) Firmware beyond native_sim.** No board with a radio is available. (1) `firmware/zephyr-mcu` builds the
firmware's sources, unchanged, for an Arm Cortex-M3 MCU emulated by QEMU (mps2-an385: Zephyr's own TCP/IP
stack and Ethernet driver instead of the host's sockets, NVS on a simulated flash) and for the nRF52840 DK
with networking (OpenThread over the 802.15.4 radio, TCP, MQTT, NVS: flash/RAM footprint);
`measure zephyr-mcu` runs EviCon-Probe's zephyr-mqtt probes, unchanged, against the emulated MCU. (2)
`firmware/zephyr-auth` adds authenticated messages to the cutoff firmware (HMAC-SHA256 under a device key
on commands, stops, fences and every reply, and a bound on how far a fence's cutoff may run ahead of the
sequences seen); `measure zephyr-auth` checks what it accepts and certifies against forged, altered,
replayed and misdirected messages, with the plant's truth.

**Pre-release checks.** See section 9 (written before freezing; local and simulated-testbed runs, not
results).

## 2. Design

Common regime (as `expW_queue_w6`, `expS_w6`, `expC_minio`, `expC2_zephyr`): 4 devices; 300 s per cell
+ 60 s tail; 0.5 commands/s per device; values 1-5; bound 345 per device; deadline 60 s; validity 90 s;
fault windows 2/min (run V: 1/min); offline 10-40 s; mix offline 0.3, drop_up 0.2, drop_down 0.2,
delay_up 0.1 (6 s), crash 0.2; authority: no assumed bound on the network delay (`max_network_delay`
-1ns), SKI_RENTAL resolution, query first, costs hold 1, stop 1, fence 5; the T4 retry guard on.

| Run | Config | Runner | Owner, channel | Contracts | Policies | W | Seed (replays) | Episodes | Cells | Hours |
|---|---|---|---|---|---|---|---|---|---|---|
| **Rm** | `expR_minio_winf.json` | `campaign_real.py` | MinIO behind the S3 gateway (as C) | MINIO_PLAIN, MINIO_COND (the 070 measured contracts) | EVICON | 1000 | 20261025 (C) | 8 | 16 | 1.9 |
| **Rz** | `expR_zephyr_winf.json` | `campaign_zephyr_ext.py` | the Zephyr firmware behind Mosquitto (as C2, C3) | ZEPHYR_PLAIN, ZEPHYR_CUTOFF (queue), ZEPHYR_PLAIN_DIRECT, ZEPHYR_F_DIRECT (direct; {F} of the cutoff mode's measured contract) | EVICON | 1000 | 20261017 (C2, C3) | 8 | 32 | 3.7 |
| **O** | `expO_noorphan.json` | `campaign.py` | evicon-device, queue, crash after the receipt | K0, K0S, K0F | EVICON | 6 | 20261017 (S) | 8 | 24 | 2.8 |
| **K2** | `expK2_w6.json` | `campaign.py` | evicon-device, queue | K2, K3F | EVICON, RES_PERIODIC | 6 | 20261018 (A-confirm-k2) | 5 | 20 | 2.3 |
| **V** | `expV_mild.json` | `campaign.py` | evicon-device, queue | K0, K0S, K0F | EVICON | 6 | 20261026 (new) | 8 | 24 | 2.8 |
| **RTq** | `expRT_queue.json` | `campaign_ext077.py` | evicon-device, queue | K0, K0F | RELEASE_ON_TIMEOUT | 6 | 20261017 (Wq) | 8 | 16 | 1.9 |
| **RTd** | `expRT_direct.json` | `campaign_ext077.py` | evicon-device, direct (`-offline-mode drop`) | K0 | RELEASE_ON_TIMEOUT | 6 | 20261017 (Wd) | 8 | 8 | 0.9 |
| **RRq** | `expRR_queue.json` | `campaign_ext077.py` | evicon-device, queue | K2 | EVICON, RELEASE_ON_READ | 6 | 20261017 | 5 | 10 | 1.2 |
| **RRd** | `expRR_direct.json` | `campaign_ext077.py` | evicon-device, direct | K2 | EVICON, RELEASE_ON_READ | 6 | 20261017 | 5 | 10 | 1.2 |
| **L2** | `expL2_sleepy.json` | `campaign_ext077.py` | evicon-device, queue, offline 60-180 s | K2, K3T | EVICON | 6 | 20261024 (L) | 8 | 16 | 1.9 |
| **Q** | `expQ_zephyr.json` | `campaign_zephyr_ext077.py` | the Zephyr firmware behind Mosquitto, queue, every offline window starting with a cut | ZEPHYR_PLAIN_QOS1 (plain, QoS 1), ZEPHYR_PLAIN_QOS0 (plain, the rule: QoS 0), ZEPHYR_CUTOFF_QOS1 (cutoff, the rule: QoS 1) | EVICON | 6 | 20261017 (C2) | 6 | 18 | 2.1 |
| **WAN** | `expWAN_s6.json` | `campaign.py` | evicon-device, queue, 100 +/- 25 ms one way | K0, K0S, K0F | EVICON | 6 | 20261017 (S) | 8 | 24 | 2.8 |
| **C16** | `expC16_minio.json` | `campaign_real.py` | MinIO behind the S3 gateway (as C) | MINIO_PLAIN, MINIO_COND | EVICON | 16 | 20261017 (C) | 8 | 16 | 1.9 |
| **TB** | `expTB_thingsboard.json` | `campaign_tb.py` | ThingsBoard CE 4.3.1.6 persistent RPC (VM-B), reference MQTT devices (VM-E), the ThingsBoard gateway (VM-C) | TB_RPC (`thingsboard-rpc-r0` measured in the run: Q_A, T) | EVICON, M1, NAIVE | 6 | 20261017 (C) | 6 | 18 | 2.3 |
| Scale | `rq4_scale_d8/d16/d32.json` | `campaign.py` (`evicon_review.py scale`) | evicon-device, no faults, 5 commands/s per device | K2, K0F | M0, EVICON | 16 | 20261020 (RQ4) | 2 | 24 | 1.4 |
| latency control | `rq4_load_r1/r5/r20.json` (unchanged) | `latency_control.py` | evicon-noop on VM-A | - | - | - | 20261020 (RQ4) | 3 per load | - | 0.4 |
| checks | - | `qos-dup`, `measure zephyr-mcu`, `measure zephyr-auth` | Mosquitto (VM-B); the firmware on QEMU mps2-an385 (VM-E); the authenticated firmware (VM-E) | - | - | - | - | - | - | 1.1 |

- **Runners.** `campaign_ext077.py` imports the frozen `campaign.py` unchanged and adds the two baseline
  policies (their authority arguments `-policy <name> -release-after 10s -resolve-sweep 1s`) and the
  validity cut of resends (`-resend-until <authority.resend_until>`: 30 s for K2, RRq, RRd and L2);
  nothing else differs. Rm and C16 use the runner of C unchanged (`campaign_real.py`).
  `campaign_zephyr_ext077.py` imports the C3 runner (on the frozen C2 runner) and adds the delivery binding
  and the offline cut (`real_owner.contracts.<c>.binding`, `real_owner.offline_cut`), with the 077 gateway
  (`mqtt_owner_gateway_ext077.py` on the 075 gateway, on the frozen C2 gateway); Rz and Q run on it, Rz with
  the C2/C3 binding (QoS 1) everywhere. `campaign_tb.py` reuses the frozen C2 runner's cell loop with its own
  testbed (the ThingsBoard devices made per cell and deleted after it).
- **Rz's and Q's contracts** are measured in the same review run on the same firmware binary (`measure
  zephyr-plain zephyr`; the driver's gate of C2/C3: MEASURED, the planned capabilities, the measured
  fault model "owner up"), and {F} is the restriction of the cutoff mode's measured contract
  (`restrict_contract`, as C3). Rm and C16 use the contracts MinIO's 070 measurement produced
  (`experiments/owners/minio/meas-minio-{plain,cond}-070.json`), as C did. TB's contract is
  `thingsboard-rpc-r0` measured in the same review run (gate: MEASURED, {Q_A, T}, "owner up"); otherwise
  no TB cell runs.
- **Truth**: `evicon-plant` on VM-E in every run (in TB the reference devices write it; in Rm and C16 MinIO's
  object versions, as in C).
- **Pairing**: contracts (and policies) of the same episode within a run; across runs, episodes whose
  schedule files `schedule_ep<e>.json` are byte-identical (common random numbers).
- **Activation gates**: O, V and WAN, at least one acting EVICON decision in every VALID K0S and K0F cell;
  K2, in every VALID K3F/EVICON cell; none elsewhere.
- **Analysis**: `evaluation/pairs_report.py --plan evaluation/prereg/PREREG_review_077.md --run <key>=<run>
  ... --out <dir>` with the spec of section 8, and `evaluation/policy_compare.py` with the
  ```policy-compare``` block of section 8 (cross-policy comparisons, no tests); `evicon_review.py
  report` runs both with the earliest complete run of each campaign among its sources.

## 3. Hypotheses and decision rules

Tests of `pairs_report.py` on per-episode paired differences (to minus from), clean (or confirmed)
fulfilment in pp of offered, Student t, alpha = 0.05; Holm within each family. Detection rules have no
p-value and are not part of a family.

| ID | Family | Hypothesis | Decision rule |
|---|---|---|---|
| **U-1** | unbounded | With the window unbounded, MinIO's conditional PUT still raises clean fulfilment: on a lossy channel stranded capacity binds the budget, not only the window | `at_least 5`, run Rm, EVICON, MINIO_PLAIN -> MINIO_COND, `clean_pp` |
| **U-2** | unbounded | ... and so does the firmware's fence on the direct channel | `at_least 5`, run Rz, ZEPHYR_PLAIN_DIRECT -> ZEPHYR_F_DIRECT, `clean_pp` |
| **U-3** | unbounded | On the queue channel at W unbounded, the cutoff contract raises *confirmed* fulfilment (the client learns the outcome) | `at_least 5`, run Rz, ZEPHYR_PLAIN -> ZEPHYR_CUTOFF, `confirmed_pp` |
| **U-4** | unbounded | ... while clean fulfilment is equivalent (late deliveries fulfil the plain firmware's requests too) | `equivalent 3` (TOST), run Rz, ZEPHYR_PLAIN -> ZEPHYR_CUTOFF, `clean_pp` |
| **O-1** | orphan | Without crash orphans, a fence and a stop are equivalent on the emulated owner | `equivalent 5`, run O, K0F -> K0S, `clean_pp` |
| **O-2** | orphan | Without crash orphans, the fence keeps its effect over no evidence | `at_least 10`, run O, K0 -> K0F, `clean_pp` |
| **K-1** | k2 | On the release-077 runtime, K2 and K3F are equivalent (EVICON) | `equivalent 2`, run K2, EVICON, K2 -> K3F, `clean_pp` |
| **K-2** | k2 | ... and under RES_PERIODIC | `equivalent 2`, run K2, RES_PERIODIC, K2 -> K3F, `clean_pp` |
| **V-1** | regime | With half the fault rate, the fence still raises clean fulfilment | `at_least 5`, run V, K0 -> K0F, `clean_pp` |
| **V-2** | regime | ... and so does the stop | `at_least 5`, run V, K0 -> K0S, `clean_pp` |
| **RT-1** | - | RELEASE_ON_TIMEOUT violates safety on the queue channel without evidence | `detects`, run RTq, K0/RELEASE_ON_TIMEOUT, any of the five safety counts |
| **RT-2** | - | ... and does so on an owner that offers a fence it does not use | `detects`, run RTq, K0F/RELEASE_ON_TIMEOUT |
| **RT-3** | - | ... and on the direct channel (a lost reply hides an effect) | `detects`, run RTd, K0/RELEASE_ON_TIMEOUT |
| **RR-1** | - | RELEASE_ON_READ violates safety on the queue channel (a request held while the owner is offline is not in its reported state) | `detects`, run RRq, K2/RELEASE_ON_READ |
| **RR-2** | - | RELEASE_ON_READ on the direct channel: exploratory, no direction claimed (section 1(e)) | `detects`, run RRd, K2/RELEASE_ON_READ; reported either way |
| **RR-S1, RR-S2** | - | EVICON on K2 in the baseline runs is safe | `absent`, runs RRq and RRd, K2/EVICON, the five safety counts |
| **WAN-1** | wan | Over a WAN (200 ms round trip) the fence keeps its effect at W = 6 | `at_least 10`, run WAN, EVICON, K0 -> K0F, `clean_pp` |
| **WAN-2** | wan | ... and so does the stop | `at_least 10`, run WAN, EVICON, K0 -> K0S, `clean_pp` |
| **L2-1** | - | With the validity cut, no late effect comes from a resend sent after the intent's validity | `late_attribution.py` on run L2: `late_resend_sent_after_validity` = 0 in every VALID K2/EVICON cell (decided by the attribution, not by `pairs_report.py`) |
| **L2-2** | - | Owner-enforced expiry (T) leaves no late effect | `absent`, run L2, K3T/EVICON, `late_effects` |
| **Q-1** | - | At QoS 1 the plain firmware's commands are duplicated below the runtime when a reset loses the broker's acknowledgement (the adapter premise broken on purpose) | `detects`, run Q, ZEPHYR_PLAIN_QOS1/EVICON, `duplicate_effects` or `bound_violations` |
| **Q-2** | - | Under the binding rule (QoS 0 without D_h) the plain firmware is safe in the same faults | `absent`, run Q, ZEPHYR_PLAIN_QOS0/EVICON, the five safety counts |
| **Q-3** | - | Under the rule (QoS 1 with D_h) the cutoff firmware is safe in the same faults | `absent`, run Q, ZEPHYR_CUTOFF_QOS1/EVICON, the five safety counts |
| **TB-1** | - | On ThingsBoard a blind retry duplicates real effects (the real-system negative control: no D_h) | `detects`, run TB, TB_RPC/NAIVE, `duplicate_effects` or `bound_violations` |
| **TB-S1, TB-S2** | - | EVICON and M1 on ThingsBoard are safe | `absent`, run TB, TB_RPC/EVICON and TB_RPC/M1, the five safety counts |
| **R-S** | - | Safety in every cell of runs Rm, Rz, O, K2, V, L2, WAN, C16 (the T4 guard is on everywhere; no negative control) | `safety` over Rm, Rz, O, K2, V, L2, WAN, C16: bound violations, duplicate effects, logical duplicates, false STOPPED, false APPLIED all 0 in every VALID cell; late effects reported separately |
| **X-Q0** | - | QoS 1 duplicates commands under the loss of acknowledgements, QoS 0 never does | `qos-dup` on the testbed's Mosquitto: commands delivered twice or more > 0 at QoS 1 and = 0 at QoS 0 |
| **X-MCU** | - | The firmware's evidence contract does not depend on the platform under it | `measure zephyr-mcu`: the emulated MCU's measured contract is MEASURED {D_h, Q_A, F} (K3), as the native measurement of the same sources |
| **X-AUTH** | - | The authenticated firmware accepts and certifies only what its key authenticates | `measure zephyr-auth`: cases A1-A13 all PASS |

The five safety counts are bound violations, duplicate effects, logical duplicates, false STOPPED and
false APPLIED, as in every plan.

Interpretation fixed in advance:
- **U-1, U-2 MET**: on real owners the value of evidence of non-occurrence does not depend on an
  arbitrary window when the channel can lose a command: without it the lost commands' reservations strand
  the budget. NOT MET with an estimate near 0: on that owner the gain at W = 6 was a window effect; the
  paper says so for that owner and reports E-U-1 to E-U-3.
- **U-3 MET, U-4 MET**: on a channel that eventually delivers what it held, an unbounded window lets the
  plain firmware's requests be fulfilled late, and what the cutoff adds is knowledge of the outcome
  (confirmed fulfilment, stranded capacity-time, E-U-4). U-4 NOT MET with the cutoff above plain: the
  cutoff adds clean fulfilment even there (the bound binds); with plain above the cutoff: the fences cost
  more than they release.
- **O-1 MET and O-2 MET**: the stop's advantage over the fence on the emulated owner (`expS-w6`) is
  caused by crash orphans; the paper states it as tested on one owner, with E-O-1 (the orphan cost per
  capability on the same schedules). O-1 NOT MET with K0S above K0F: something other than orphans also
  separates them (e.g. the frontier waits for every identity below it); with K0F above K0S: the stop
  costs on its own.
- **K-1, K-2 MET**: experiment A's K2 result holds on the release-077 runtime; NOT MET: the paper reports
  the replication instead of experiment A's K2 numbers.
- **V-1, V-2 MET**: the effects of backward evidence are not confined to the extreme regime; with E-V-1 and
  the simulator sensitivity they are reported as a function of the fault rate. NOT MET: reported with the
  estimate; the paper restricts the claim to the measured regime.
- **RT-1 to RT-3 DETECTED, RR-1 DETECTED**: the practice baselines that release without evidence of
  non-occurrence break safety where the theory says they must; with the policy comparisons of section 8
  the paper shows the fulfilment they buy with it. NOT DETECTED: no released command took effect later in
  that run (reported; the baseline was not exposed to its failure mode in this regime).
- **RR-2** DETECTED: even on a channel whose delay is bounded below the release time, a state read
  released commands that had taken effect (reads the owner did not answer taken for absence; the trace
  event `RELEASE_ON_READ` names the read's outcome); NOT DETECTED: there a read was enough, which the
  theory allows (finite delta) and which does not extend to a channel without a delay bound.
- **WAN-1, WAN-2 MET**: the effects of backward evidence do not depend on a LAN; with E-WAN-1 (WAN against
  LAN on the same schedules) the paper reports what the delay costs. NOT MET: reported with the estimate
  and E-WAN-1; the paper restricts the claim to LAN round trips.
- **L2-1 MET**: the runtime itself never makes a late effect after the intent's validity; the late effects
  that remain on K2 are requests the channel held, which only an owner-enforced expiry bounds (L2-2). NOT
  MET: a resend after the validity took effect; reported with the cells and the attempt records; the
  rule is wrong and the plan of record stops until the cause is known.
- **Q-1 DETECTED, Q-2 and Q-3 ABSENT**: the delivery binding is part of the contract: at least once is
  safe only over an owner that deduplicates, and the gateway's rule (at most once without D_h) restores
  safety on the plain firmware; E-Q-1 gives what QoS 0 costs in fulfilment. Q-1 NOT DETECTED: no cut caught
  an unacknowledged command in this run (reported with the cut counts; X-Q0 shows the mechanism).
  Q-2 or Q-3 PRESENT: a safety failure; reported and the plan of record stops until the cause is known.
- **TB-1 DETECTED, TB-S1 and TB-S2 ABSENT**: on a device-management platform, as on MinIO and the firmware,
  the evidence the platform offers decides whether a retry is safe, and EviCon is safe on its measured
  contract; E-TB gives what Q_A buys over the legacy selector. TB-1 NOT DETECTED: no blind retry hit an
  executed RPC in this regime (reported). TB-S1 or TB-S2 PRESENT: a safety failure of EviCon; reported and
  the plan of record stops until the cause is known.
- **X-Q0, X-MCU, X-AUTH** are checks of a mechanism; a FAIL is reported with its record and the claim that
  depends on it is withdrawn from the paper.
- **R-S, RR-S1, RR-S2 PRESENT anywhere**: a safety failure of EviCon; reported with the cells and the
  traces, and the plan of record stops until the cause is known.

## 4. Estimands (reported with intervals, no claims)

| ID | Estimand |
|---|---|
| E-U-1 | Across windows, C (W = 6) -> Rm (W unbounded), same seed and schedules: MINIO_PLAIN -> MINIO_PLAIN, MINIO_COND -> MINIO_COND; clean, confirmed, admitted, window-bound defers |
| E-U-2 | C2 -> Rz: ZEPHYR_PLAIN -> ZEPHYR_PLAIN, ZEPHYR_CUTOFF -> ZEPHYR_CUTOFF; C3 -> Rz: ZEPHYR_PLAIN_DIRECT -> ZEPHYR_PLAIN_DIRECT, ZEPHYR_F_DIRECT -> ZEPHYR_F_DIRECT; the same metrics |
| E-U-3 | Real owner against the emulated one at W unbounded (same seed 20261017): `expW-direct-winf` K0 -> Rz ZEPHYR_PLAIN_DIRECT and K0F -> ZEPHYR_F_DIRECT; `expW-queue-winf` K0 -> Rz ZEPHYR_PLAIN and K0F -> ZEPHYR_CUTOFF; clean and confirmed |
| E-U-4 | Within Rm and Rz, the evidence's other effects at W unbounded: confirmed, admitted, stranded capacity-time (resolved, censored), mean time to a terminal outcome, unresolved at the end, for MINIO_PLAIN -> MINIO_COND, ZEPHYR_PLAIN -> ZEPHYR_CUTOFF, ZEPHYR_PLAIN_DIRECT -> ZEPHYR_F_DIRECT |
| E-O-1 | What crash orphans cost each capability on the same owner and schedules, S -> O: K0 -> K0, K0F -> K0F, K0S -> K0S; clean, confirmed, fences, fences reached / completed, stops reached / terminal |
| E-O-2 | Emulated owner without orphans against the firmware (also without), O -> C3 (same seed): K0F -> ZEPHYR_F_QUEUE, K0S -> ZEPHYR_QS_QUEUE; clean, confirmed |
| E-O-3 | Within O, K0F -> K0S: confirmed, fences, fences reached / completed, stops, stops reached / terminal, mean time to a terminal outcome |
| E-K-1 | A-confirm-k2 (release 067 runtime, W 16 with the configuration cap) -> K2 (release 077, W 6), same seed and schedules: K2 -> K2 and K3F -> K3F for EVICON and RES_PERIODIC; clean, confirmed, late effects, reissues |
| E-V-1 | Within V: K0F -> K0S (clean, confirmed, fences reached / completed); levels of K0, K0F, K0S against S's (other seed: levels only) and against the simulator's mild scenario |
| E-P | Policy comparisons (`policy_compare.py`, section 8): per-episode differences of clean and admitted fulfilment and the safety counts between EviCon and each baseline on the same schedules |
| E-L2-1 | The late effects of L2 by the attempt that took effect (`late_attribution.py`: first send; resend sent within / after the validity), and of L by the dispatches after the validity (an upper bound: L recorded no attempt numbers) |
| E-L2-2 | L -> L2 (same seed and schedules): K2 -> K2 and K3T -> K3T; clean, confirmed, late effects, reissues, unresolved at the end |
| E-Q-1 | Within Q: ZEPHYR_PLAIN_QOS1 -> ZEPHYR_PLAIN_QOS0 and ZEPHYR_PLAIN_QOS0 -> ZEPHYR_CUTOFF_QOS1; clean, confirmed, admitted, unresolved at the end; the cuts that caught a command (the gateways' `cut` records); the duplicates of ZEPHYR_PLAIN_QOS1 with one dispatch attempt (created below the runtime) and with more |
| E-Q-2 | C2 -> Q (same seed and schedules): ZEPHYR_PLAIN -> ZEPHYR_PLAIN_QOS1 and ZEPHYR_CUTOFF -> ZEPHYR_CUTOFF_QOS1: what the cut costs; clean, confirmed |
| E-WAN-1 | S -> WAN (same seed and schedules): K0 -> K0, K0F -> K0F, K0S -> K0S; clean, confirmed, mean time to a terminal outcome, stranded capacity-time |
| E-WAN-2 | Within WAN, K0F -> K0S: clean, confirmed, fences reached / completed, stops reached / terminal |
| E-C16 | The conditional PUT's effect as a function of the window on MinIO: within C16, MINIO_PLAIN -> MINIO_COND (clean, confirmed, admitted, window-bound defers), next to C (W = 6) and Rm (W unbounded); C -> C16 and C16 -> Rm per contract |
| E-TB | Within TB (`policy_compare.py`): M1 -> EVICON and NAIVE -> EVICON on TB_RPC; clean and admitted fulfilment, the safety counts, late effects, unresolved at the end; levels next to C's MINIO_COND (another owner, same seed) |
| E-Scale | Per number of domains (4: rq4-load-r5 of the same review run; 8, 16, 32): admission latency p50 / p95 / p99, the authority's CPU per command, RSS per authority, the devices' CPU (`evaluation/overhead.py`) |
| E-THETA | The simulator's dependence on the action costs (section 1(i)) |
| E-X | qos-dup per binding (commands, resets, delivered twice or more, lost); the MCU measurement's per-capability status next to the native one; the authenticated firmware's case table; the nRF52840 footprints (networked, networked and authenticated) |
| E-L | The latency control's p50 / p95 / p99 per load, next to the RQ4 rows of the same loads |
| E-SIM | Levels per run against the simulator's prediction of section 1(g) (runs Rm, Rz, O, K2, V, RTq, RTd, WAN) |

## 5. Sample size

**Under the simulator's paired differences** (resampling the 200 episodes, Holm within each family, 2,000
resamples; `SIMULATION_ONLY`; `sim_predictions/review077_power.csv`):

| ID | Simulator mean (SD), pp | n | Power |
|---|---|---|---|
| U-1 | +14.75 (11.84) | 8 | 0.45 |
| U-2 | +14.60 (11.71) | 8 | 0.42 |
| U-3 | +14.97 (10.90), confirmed | 8 | 0.56 |
| U-4 | +1.38 (1.91) | 8 | 0.70 |
| O-1 | -0.12 (0.62) | 8 | 1.00 |
| O-2 | +39.11 (25.16) | 8 | 0.90 |
| K-1 | +0.06 (0.80) | 5 | 0.94 |
| K-2 | +0.09 (0.63) | 5 | 0.96 |
| V-1 | +21.64 (25.60) | 8 | 0.27 |
| V-2 | +22.32 (25.54) | 8 | 0.30 |
| WAN-1 | +35.66 (25.02) | 8 | 0.81 |
| WAN-2 | +40.27 (24.35) | 8 | 0.91 |

The simulator's per-episode differences are bimodal and far more spread than the testbed's (C2: SD
11.3 pp against the simulator's 24.6), so these are lower bounds. **Under the testbed's own spread**
(`evaluation/power_review077_testbed.py`: per-episode differences of the closest earlier testbed runs,
centred and shifted to a true mean, the other members of the family at the simulator's means, Holm,
4,000 resamples; the templates are named in the script):

| Rule | Template (SD, pp) | True mean -> power |
|---|---|---|
| U-1, U-2 (`at_least 5`, n 8) | `expW-direct-winf` K0 -> K0F (9.3) | 8: 0.15-0.17, 10: 0.34-0.36, 12: 0.61-0.62, 14.7 (simulator): 0.93-0.94 |
| U-3 (`at_least 5`, confirmed) | `expW-queue-winf` K0 -> K0F confirmed (6.0) | 8: 0.28, 10: 0.66, 11 (emulated owner): 0.84, 15: 1.00 |
| U-4 (`equivalent 3`) | `expW-queue-winf` K0 -> K0F (0.38) | 1.00 up to a true difference of 2 pp |
| O-1 (`equivalent 5`) | C3 F_QUEUE -> QS_QUEUE (0.64); five times that (3.2) | 1.00 up to 3 pp (0.93 at 4); x5: 0.93 at 0, 0.73 at 2, 0.46 at 3 |
| O-2 (`at_least 10`) | `expS-w6` K0 -> K0S (9.3) | 15: 0.37, 20: 0.96, 25: 1.00 |
| K-1, K-2 (`equivalent 2`, n 5) | A-confirm-k2 K2 -> K3F (0.12, 0.08); ten times that | 1.00 up to 1.5 pp; x10: 0.94-1.00 at 0, 0.35-0.73 at 1 |
| V-1, V-2 (`at_least 5`) | `expS-w6` K0 -> K0S (9.3) | 10: 0.37-0.39, 12: 0.65, 15: 0.96, 22 (simulator): 1.00 |
| WAN-1 (`at_least 10`) | `expS-w6` K0 -> K0F (12.0) | 15: 0.31, 20: 0.70, 25: 0.97, 35.7 (simulator): 1.00 |
| WAN-2 (`at_least 10`) | `expS-w6` K0 -> K0S (9.3) | 15: 0.37, 20: 0.96, 30: 1.00, 40.3 (simulator): 1.00 |

Eight episodes, as in the runs Rm, Rz, O, WAN, C16 and L2 replay; five for K2 and the RR runs, as
experiment A's K2 configuration; six for Q and TB (detection and absence rules only, which need no
power; episodes 0-5 replay C2's and C's schedules). The detection rules need one detecting cell (the simulator's baseline violated safety in
nearly every episode).

## 6. Exclusions, stopping and order

- INVALID cells are excluded and listed (a fault could not be applied, a process died, a firmware process
  exited, the truth could not be read). An INVALID cell is re-run once by resuming the campaign; if it is
  INVALID again it stays excluded. A run with more than 10 % excluded cells is not interpretable and its
  hypotheses are not decided.
- No interim analysis: `pairs_report.py` and `policy_compare.py` run only when every cell of a run is
  done. A campaign may be split over several sessions (resume).
- Order (five sessions, one review run, after the 077 firmware build on VM-D): N9 `preflight build verify
  distribute`, `measure zephyr-plain zephyr`, `campaign expR_zephyr_winf`, `campaign expQ_zephyr`; N10
  `campaign expR_minio_winf`, `campaign expC16_minio`, `campaign expO_noorphan`; N11 `campaign expV_mild`,
  `campaign expRT_queue`, `campaign expRT_direct`, `campaign expRR_queue`; N12 `measure thingsboard`,
  `campaign expTB_thingsboard`, `campaign expL2_sleepy`, `campaign expWAN_s6`; N13 `measure zephyr-mcu
  zephyr-auth`, `qos-dup`, `campaign expK2_w6`, `campaign expRR_direct`, `scale`, `latency-control`,
  `report`, `bundle`. The order within a session does not matter, except that a measurement precedes the
  runs on its contract and `report` comes last. No firmware build after N9's measurement: a new build
  makes Rz and Q refuse to start (measured binary != run binary); TB runs only on the contract the same
  review run measured.
- The earliest complete run of each config is the preregistered one.

## 7. Frozen files

SHA-256 at freezing time (`scripts/freeze_prereg.py`); the driver checks them before running. The six
files frozen for experiment A are frozen again, unchanged; added through the `frozen-extra` block: the
analyses (`pairs_report.py` with `cadence_report.py`, `policy_compare.py`, `latency_control.py`,
`late_attribution.py`), the runners (`campaign_ext077.py`, `campaign_real.py`, `campaign_zephyr.py`,
`campaign_zephyr_ext.py`, `campaign_zephyr_ext077.py`, `campaign_tb.py`), the gateways, the reference MQTT
device and what they import, the fault proxy, the checks `qos_dup_check.py` and `zephyr_auth_check.py`,
the firmware sources and the MCU and authenticated variants, the probe configs, adapters, probes and
rubric of the Zephyr and ThingsBoard measurements, the contracts Rm and C16 use and the simulator's
predicted contracts, and the simulator predictions of section 1 with their scripts and the testbed power
script of section 5.
Every file frozen by `PREREG_expC3_075.md` is frozen here too, with the same SHA-256 (none changed in
releases 076 and 077).

The Go code is built from release 077 or 078 (the run's `source.json` records its source; release 078
changes `buildinfo.Release` only). Release 078 adds the testbed set-up from the VMs' current addresses
(`deploy/setup/testbed_init.sh`, `testbed.json`) and reads the reference runs of earlier releases from
their result bundles (`evicon_review.py import-results`, `report --archive`); the runners, the analyses
and every file frozen here are those of release 077. Since release 076
the Go code changed in these places: the two baseline policies (a separate branch of the authority that EVICON
never takes; tests F44-F46); the validity cut of resends (`-resend-until`, off unless set; test F47); the
dispatch attempt number sent with every request and the device's record of the attempt that took effect
(test `TestEffectAttemptLogged`); the device's crash-point override (run O; `TestEffectiveCrashPoint`) and
WAN delay (run WAN; `TestWanHandler`), both off unless set; the control endpoint `evicon-noop`; a property
test of the admission path (F48); and `buildinfo.Release`.

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
```

## 8. Analysis spec (read by pairs_report.py and policy_compare.py)

```pairs-spec
{
  "name": "Review experiments of release 077: real owners at an unbounded window and at W = 16, no crash orphans, K2 on the release-077 runtime, a milder regime, a WAN, two practice baselines, sleepy devices with the validity cut, the delivery binding of MQTT commands, a device-management platform",
  "runs": {
    "Rm": {"campaign": "expR-minio-winf"},
    "Rz": {"campaign": "expR-zephyr-winf"},
    "O": {"campaign": "expO-noorphan"},
    "K2": {"campaign": "expK2-w6"},
    "V": {"campaign": "expV-mild"},
    "RTq": {"campaign": "expRT-queue"},
    "RTd": {"campaign": "expRT-direct"},
    "RRq": {"campaign": "expRR-queue"},
    "RRd": {"campaign": "expRR-direct"},
    "L2": {"campaign": "expL2-sleepy"},
    "Q": {"campaign": "expQ-zephyr"},
    "WAN": {"campaign": "expWAN-s6"},
    "C16": {"campaign": "expC16-minio"},
    "TB": {"campaign": "expTB-thingsboard"},
    "C": {"campaign": "expC-minio", "reference": true},
    "C2": {"campaign": "expC2-zephyr", "reference": true},
    "C3": {"campaign": "expC3-ablation", "reference": true},
    "Wqi": {"campaign": "expW-queue-winf", "reference": true},
    "Wdi": {"campaign": "expW-direct-winf", "reference": true},
    "S": {"campaign": "expS-w6", "reference": true},
    "A2": {"campaign": "expA-confirm-k2", "reference": true},
    "L": {"campaign": "expL-sleepy", "reference": true}
  },
  "alpha": 0.05,
  "max_invalid_fraction": 0.1,
  "hypotheses": [
    {"id": "U-1", "family": "unbounded", "run": "Rm", "policy": "EVICON", "from": "MINIO_PLAIN", "to": "MINIO_COND", "metric": "clean_pp", "test": "at_least", "margin": 5.0},
    {"id": "U-2", "family": "unbounded", "run": "Rz", "policy": "EVICON", "from": "ZEPHYR_PLAIN_DIRECT", "to": "ZEPHYR_F_DIRECT", "metric": "clean_pp", "test": "at_least", "margin": 5.0},
    {"id": "U-3", "family": "unbounded", "run": "Rz", "policy": "EVICON", "from": "ZEPHYR_PLAIN", "to": "ZEPHYR_CUTOFF", "metric": "confirmed_pp", "test": "at_least", "margin": 5.0},
    {"id": "U-4", "family": "unbounded", "run": "Rz", "policy": "EVICON", "from": "ZEPHYR_PLAIN", "to": "ZEPHYR_CUTOFF", "metric": "clean_pp", "test": "equivalent", "margin": 3.0},
    {"id": "O-1", "family": "orphan", "run": "O", "policy": "EVICON", "from": "K0F", "to": "K0S", "metric": "clean_pp", "test": "equivalent", "margin": 5.0},
    {"id": "O-2", "family": "orphan", "run": "O", "policy": "EVICON", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "at_least", "margin": 10.0},
    {"id": "K-1", "family": "k2", "run": "K2", "policy": "EVICON", "from": "K2", "to": "K3F", "metric": "clean_pp", "test": "equivalent", "margin": 2.0},
    {"id": "K-2", "family": "k2", "run": "K2", "policy": "RES_PERIODIC", "from": "K2", "to": "K3F", "metric": "clean_pp", "test": "equivalent", "margin": 2.0},
    {"id": "V-1", "family": "regime", "run": "V", "policy": "EVICON", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "at_least", "margin": 5.0},
    {"id": "V-2", "family": "regime", "run": "V", "policy": "EVICON", "from": "K0", "to": "K0S", "metric": "clean_pp", "test": "at_least", "margin": 5.0},
    {"id": "WAN-1", "family": "wan", "run": "WAN", "policy": "EVICON", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "at_least", "margin": 10.0},
    {"id": "WAN-2", "family": "wan", "run": "WAN", "policy": "EVICON", "from": "K0", "to": "K0S", "metric": "clean_pp", "test": "at_least", "margin": 10.0},
    {"id": "RT-1", "run": "RTq", "test": "detects", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "RT-2", "run": "RTq", "test": "detects", "contract": "K0F", "policy": "RELEASE_ON_TIMEOUT", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "RT-3", "run": "RTd", "test": "detects", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "RR-1", "run": "RRq", "test": "detects", "contract": "K2", "policy": "RELEASE_ON_READ", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "RR-2", "run": "RRd", "test": "detects", "contract": "K2", "policy": "RELEASE_ON_READ", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "RR-S1", "run": "RRq", "test": "absent", "contract": "K2", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "RR-S2", "run": "RRd", "test": "absent", "contract": "K2", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "L2-2", "run": "L2", "test": "absent", "contract": "K3T", "policy": "EVICON", "any_of": ["late_effects"]},
    {"id": "Q-1", "run": "Q", "test": "detects", "contract": "ZEPHYR_PLAIN_QOS1", "policy": "EVICON", "any_of": ["duplicate_effects", "bound_violations"]},
    {"id": "Q-2", "run": "Q", "test": "absent", "contract": "ZEPHYR_PLAIN_QOS0", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "Q-3", "run": "Q", "test": "absent", "contract": "ZEPHYR_CUTOFF_QOS1", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "TB-1", "run": "TB", "test": "detects", "contract": "TB_RPC", "policy": "NAIVE", "any_of": ["duplicate_effects", "bound_violations"]},
    {"id": "TB-S1", "run": "TB", "test": "absent", "contract": "TB_RPC", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "TB-S2", "run": "TB", "test": "absent", "contract": "TB_RPC", "policy": "M1", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]}
  ],
  "safety": {"id": "R-S", "runs": ["Rm", "Rz", "O", "K2", "V", "L2", "WAN", "C16"], "exempt_contracts": []},
  "estimands": [
    {"run": "Rm", "from_run": "C", "policy": "EVICON", "from": "MINIO_PLAIN", "to": "MINIO_PLAIN", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers"]},
    {"run": "Rm", "from_run": "C", "policy": "EVICON", "from": "MINIO_COND", "to": "MINIO_COND", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers"]},
    {"run": "Rz", "from_run": "C2", "policy": "EVICON", "from": "ZEPHYR_PLAIN", "to": "ZEPHYR_PLAIN", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers"]},
    {"run": "Rz", "from_run": "C2", "policy": "EVICON", "from": "ZEPHYR_CUTOFF", "to": "ZEPHYR_CUTOFF", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers"]},
    {"run": "Rz", "from_run": "C3", "policy": "EVICON", "from": "ZEPHYR_PLAIN_DIRECT", "to": "ZEPHYR_PLAIN_DIRECT", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers"]},
    {"run": "Rz", "from_run": "C3", "policy": "EVICON", "from": "ZEPHYR_F_DIRECT", "to": "ZEPHYR_F_DIRECT", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers"]},
    {"run": "Rz", "from_run": "Wdi", "policy": "EVICON", "from": "K0", "to": "ZEPHYR_PLAIN_DIRECT", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "Rz", "from_run": "Wdi", "policy": "EVICON", "from": "K0F", "to": "ZEPHYR_F_DIRECT", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "Rz", "from_run": "Wqi", "policy": "EVICON", "from": "K0", "to": "ZEPHYR_PLAIN", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "Rz", "from_run": "Wqi", "policy": "EVICON", "from": "K0F", "to": "ZEPHYR_CUTOFF", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "Rm", "policy": "EVICON", "from": "MINIO_PLAIN", "to": "MINIO_COND", "metrics": ["confirmed_pp", "admitted_pp", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s", "unresolved_at_end"]},
    {"run": "Rz", "policy": "EVICON", "from": "ZEPHYR_PLAIN", "to": "ZEPHYR_CUTOFF", "metrics": ["admitted_pp", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s", "unresolved_at_end"]},
    {"run": "Rz", "policy": "EVICON", "from": "ZEPHYR_PLAIN_DIRECT", "to": "ZEPHYR_F_DIRECT", "metrics": ["confirmed_pp", "admitted_pp", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s", "unresolved_at_end"]},
    {"run": "O", "from_run": "S", "policy": "EVICON", "from": "K0", "to": "K0", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "O", "from_run": "S", "policy": "EVICON", "from": "K0F", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp", "fences", "fences_reached", "fences_reached_ok"]},
    {"run": "O", "from_run": "S", "policy": "EVICON", "from": "K0S", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp", "stops", "stops_reached", "stops_reached_terminal"]},
    {"run": "C3", "from_run": "O", "policy": "EVICON", "from": "K0F", "to": "ZEPHYR_F_QUEUE", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "C3", "from_run": "O", "policy": "EVICON", "from": "K0S", "to": "ZEPHYR_QS_QUEUE", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "O", "policy": "EVICON", "from": "K0F", "to": "K0S", "metrics": ["confirmed_pp", "fences", "fences_reached", "fences_reached_ok", "stops", "stops_reached", "stops_reached_terminal", "mean_time_to_terminal_s"]},
    {"run": "K2", "from_run": "A2", "policy": "EVICON", "from": "K2", "to": "K2", "metrics": ["clean_pp", "confirmed_pp", "late_effects", "reissues"]},
    {"run": "K2", "from_run": "A2", "policy": "EVICON", "from": "K3F", "to": "K3F", "metrics": ["clean_pp", "confirmed_pp", "late_effects", "reissues"]},
    {"run": "K2", "from_run": "A2", "policy": "RES_PERIODIC", "from": "K2", "to": "K2", "metrics": ["clean_pp", "confirmed_pp", "late_effects", "reissues"]},
    {"run": "K2", "from_run": "A2", "policy": "RES_PERIODIC", "from": "K3F", "to": "K3F", "metrics": ["clean_pp", "confirmed_pp", "late_effects", "reissues"]},
    {"run": "V", "policy": "EVICON", "from": "K0F", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp", "fences_reached", "fences_reached_ok"]},
    {"run": "L2", "from_run": "L", "policy": "EVICON", "from": "K2", "to": "K2", "metrics": ["clean_pp", "confirmed_pp", "late_effects", "reissues", "unresolved_at_end"]},
    {"run": "L2", "from_run": "L", "policy": "EVICON", "from": "K3T", "to": "K3T", "metrics": ["clean_pp", "confirmed_pp", "late_effects", "reissues", "unresolved_at_end"]},
    {"run": "Q", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS1", "to": "ZEPHYR_PLAIN_QOS0", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "unresolved_at_end"]},
    {"run": "Q", "policy": "EVICON", "from": "ZEPHYR_PLAIN_QOS0", "to": "ZEPHYR_CUTOFF_QOS1", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "unresolved_at_end"]},
    {"run": "Q", "from_run": "C2", "policy": "EVICON", "from": "ZEPHYR_PLAIN", "to": "ZEPHYR_PLAIN_QOS1", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "Q", "from_run": "C2", "policy": "EVICON", "from": "ZEPHYR_CUTOFF", "to": "ZEPHYR_CUTOFF_QOS1", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "WAN", "from_run": "S", "policy": "EVICON", "from": "K0", "to": "K0", "metrics": ["clean_pp", "confirmed_pp", "mean_time_to_terminal_s", "stranded_resolved_unit_sec", "stranded_censored_unit_sec"]},
    {"run": "WAN", "from_run": "S", "policy": "EVICON", "from": "K0F", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp", "mean_time_to_terminal_s", "stranded_resolved_unit_sec", "stranded_censored_unit_sec"]},
    {"run": "WAN", "from_run": "S", "policy": "EVICON", "from": "K0S", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp", "mean_time_to_terminal_s", "stranded_resolved_unit_sec", "stranded_censored_unit_sec"]},
    {"run": "WAN", "policy": "EVICON", "from": "K0F", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp", "fences_reached", "fences_reached_ok", "stops_reached", "stops_reached_terminal"]},
    {"run": "C16", "policy": "EVICON", "from": "MINIO_PLAIN", "to": "MINIO_COND", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers"]},
    {"run": "C16", "from_run": "C", "policy": "EVICON", "from": "MINIO_PLAIN", "to": "MINIO_PLAIN", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers"]},
    {"run": "C16", "from_run": "C", "policy": "EVICON", "from": "MINIO_COND", "to": "MINIO_COND", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers"]},
    {"run": "Rm", "from_run": "C16", "policy": "EVICON", "from": "MINIO_PLAIN", "to": "MINIO_PLAIN", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers"]},
    {"run": "Rm", "from_run": "C16", "policy": "EVICON", "from": "MINIO_COND", "to": "MINIO_COND", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "window_defers"]}
  ]
}
```

```policy-compare
{
  "name": "Release 077: EviCon against the practice baselines on the same schedules, and against M1 and a blind retry on ThingsBoard (PREREG_review_077.md, E-P, E-TB)",
  "runs": {
    "RTq": {"campaign": "expRT-queue"},
    "RTd": {"campaign": "expRT-direct"},
    "RRq": {"campaign": "expRR-queue"},
    "RRd": {"campaign": "expRR-direct"},
    "Wq": {"campaign": "expW-queue-w6"},
    "Wd": {"campaign": "expW-direct-w6"},
    "TB": {"campaign": "expTB-thingsboard"}
  },
  "comparisons": [
    {"id": "RT-E1", "a": {"run": "Wq", "contract": "K0", "policy": "EVICON"}, "b": {"run": "RTq", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations"]},
    {"id": "RT-E2", "a": {"run": "Wq", "contract": "K0F", "policy": "EVICON"}, "b": {"run": "RTq", "contract": "K0F", "policy": "RELEASE_ON_TIMEOUT"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations"]},
    {"id": "RT-E3", "a": {"run": "Wd", "contract": "K0", "policy": "EVICON"}, "b": {"run": "RTd", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations"]},
    {"id": "RT-E4", "a": {"run": "Wd", "contract": "K0F", "policy": "EVICON"}, "b": {"run": "RTd", "contract": "K0", "policy": "RELEASE_ON_TIMEOUT"}, "metrics": ["clean_pp", "admitted_pp", "violations"]},
    {"id": "RR-E1", "a": {"run": "RRq", "contract": "K2", "policy": "EVICON"}, "b": {"run": "RRq", "contract": "K2", "policy": "RELEASE_ON_READ"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations"]},
    {"id": "RR-E2", "a": {"run": "RRd", "contract": "K2", "policy": "EVICON"}, "b": {"run": "RRd", "contract": "K2", "policy": "RELEASE_ON_READ"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations"]},
    {"id": "RR-E3", "a": {"run": "Wq", "contract": "K0F", "policy": "EVICON"}, "b": {"run": "RRq", "contract": "K2", "policy": "RELEASE_ON_READ"}, "metrics": ["clean_pp", "admitted_pp", "violations"]},
    {"id": "TB-E1", "a": {"run": "TB", "contract": "TB_RPC", "policy": "M1"}, "b": {"run": "TB", "contract": "TB_RPC", "policy": "EVICON"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "duplicate_effects", "late_effects", "unresolved_at_end"]},
    {"id": "TB-E2", "a": {"run": "TB", "contract": "TB_RPC", "policy": "NAIVE"}, "b": {"run": "TB", "contract": "TB_RPC", "policy": "EVICON"}, "metrics": ["clean_pp", "admitted_pp", "violations", "false_stopped", "logical_duplicates", "bound_violations", "duplicate_effects", "late_effects", "unresolved_at_end"]}
  ]
}
```

## 9. Pre-release checks (before freezing; not results)

Checks of the code path before freezing, on one machine and on a simulated testbed. They are not results of
this plan: the regime, the scale and the timing differ from the testbed runs.

**Unit tests.** Go: every package passes (`go test ./...`, `go vet`, `gofmt`), among them the runtime tests F44
(RELEASE_ON_TIMEOUT releases a lost command, re-admits its capacity, and the late original exceeds the bound), F45
(RELEASE_ON_READ records B's APPLIED receipt, releases A that the owner does not report, and the late original of A
exceeds the bound), F46 (an unreachable owner is read as "not reported"; the sweep does nothing for EVICON), F47 (with
`-resend-until` the T4 guard re-sends until the cut and never after it), F48 (a property test over random histories on
one journal: APPLIED evidence never changes the guard's verdict, STOPPED never blocks an admitted request, and every
terminal answer frees exactly one window slot), and the device's `TestEffectiveCrashPoint`, `TestEffectAttemptLogged`
and `TestWanHandler`. Python: 120 analysis tests (among them `test_release077` B1-B11 and
`test_prereg.Release077`), 80 conformance tests (among them Q1-Q6 of the 077 MQTT gateway against a local
Mosquitto: the binding rule, QoS 1 executing a command twice on the plain firmware after a lost acknowledgement, QoS 0
never; and G1-G5 of the ThingsBoard gateway against a model of ThingsBoard's REST API: a persistent RPC without
platform retries and with the request's expiry, an offline device answered UNCERTAIN until it executes, an expired RPC
never executed, the RPC found again after a gateway restart, a blind retry creating a second RPC), and the firmware
host tests, with the HMAC-SHA256 test vectors of FIPS 180-4 and RFC 4231 for the authenticated variant.

**Local campaigns** (one machine, `DEV_LOCAL`; each config shortened to 1 episode of 90 s + 15 s tail, 6 fault
windows/min, offline 5-12 s, deadline 30 s, validity 45 s; seed and the rest as planned; 4 devices; 184 commands
offered per cell):

| Config | Cell | Clean | False STOPPED | Logical duplicates | Other safety counts | Unresolved at end | Releases without evidence |
|---|---|---|---|---|---|---|---|
| expRT_queue | K0 / RELEASE_ON_TIMEOUT | 145 (78.8 %) | 47 | 39 | 0 | 0 | 62 |
| | K0F / RELEASE_ON_TIMEOUT | 144 (78.3 %) | 48 | 40 | 0 | 0 | 64 |
| expRT_direct | K0 / RELEASE_ON_TIMEOUT | 167 (90.8 %) | 10 | 10 | 0 | 0 | 89 |
| expRR_queue | K2 / EVICON | 184 (100 %) | 0 | 0 | 0 | 6 | - |
| | K2 / RELEASE_ON_READ | 178 (96.7 %) | 6 | 6 | 0 | 1 | 6 (owner unreachable) |
| expRR_direct | K2 / EVICON | 184 (100 %) | 0 | 0 | 0 | 6 | - |
| | K2 / RELEASE_ON_READ | 179 (97.3 %) | 5 | 5 | 0 | 1 | 6 (owner unreachable) |
| expO_noorphan | K0 / EVICON | 88 (47.8 %) | 0 | 0 | 0 | 24 | - |
| | K0S / EVICON | 184 (100 %) | 0 | 0 | 0 | 0 | - |
| | K0F / EVICON | 184 (100 %) | 0 | 0 | 0 | 0 | - |

Every cell VALID. In the RR cells the reads gave 51 UNCERTAIN answers (claims without a receipt after the injected
crashes, read again on later sweeps) and 6 unanswered reads (DEVICE_OFFLINE, or a drop window); every release followed
an unanswered read, and on the direct channel 5 of the 6 released commands had taken effect. The expectation for RR-2
(section 1(e)) takes this into account: the read of an owner that does not answer is what makes the state-read
baseline unsafe on the direct channel. In `expO_noorphan` the K0F and K0S cells had 6 crashes each and no claim without
a receipt (no `BARRIER_UNCERTAIN_RECEIPTS`, no `CLAIMED_WITHOUT_RECEIPT` in the traces); 85 fence calls, 25 completed.

**The validity cut, locally** (`expL2_sleepy` shortened to 1 episode of 240 s, 3 fault windows/min, offline 60-120 s,
validity 90 s, deadline 60 s; K2/EVICON; 469 commands offered; `late_attribution.py`): without the cut, 6 late effects,
5 of them first sends the channel held and 1 a resend sent after the validity; with the cut at 30 s (the plan's
setting), 6 late effects, 1 first send and 5 resends, all 5 sent within the validity, none after it; 405 clean, no
safety violation, every cell VALID. The late effects that remain are deliveries the queue held past the validity, which
the cut cannot prevent and which only an owner-enforced expiry (K3T) bounds: the reason for L2-2.

**Delivery binding, locally** (`qos_dup_check.py` against a local Mosquitto 2.0, 100 commands, every fifth one's
acknowledgement lost and the link reset): QoS 1 delivered 32 commands twice (20 resets; a reset makes the client
library resend every unacknowledged command of the session), none lost; QoS 0 delivered none twice and lost 19.

**Latency control** (one machine, `latency_control.py local`, 1 episode of 20 s per load, 4 devices): every command
matched its handler record; `noop` p50 0.05-0.07 ms; `gen` p50 3.3 ms at 1 command/s per device and 166-167 ms at 5
and 20 commands/s per device on this 2-core host while a simulator sweep ran (the generator's own delay, which is
what the control isolates; not a testbed number).

**Firmware builds** (Zephyr v4.2.0, the release's `build_zephyr.sh` steps, Arm GNU toolchain 13.2 in place of the
Zephyr SDK): `firmware/zephyr-mcu` for `mps2/an385` (FLASH 126,160 B, RAM 134,280 B) runs under `qemu-system-arm
-machine mps2-an385` with Zephyr's TCP/IP stack, reaches Mosquitto through QEMU's user-mode NAT and answers the
gateway's ping; for `nrf52840dk/nrf52840` with OpenThread (MTD), TCP, MQTT and NVS: FLASH 213,912 B (20.4 % of 1 MB),
RAM 107,008 B (40.8 % of 256 KB); `firmware/zephyr-auth` on the same board: FLASH 216,472 B, RAM 107,776 B (+2,560 B and
+768 B for the MACs and the cutoff bound). The authenticated native build passes A1-A13 locally.

**Cross-run pairing and the analysis spec.** Synthetic runs made of renamed EVICON cells of the reference runs (Rm and
C16 of C, Rz of C2 and C3, O, V and WAN of S, K2 of A-confirm-k2, L2 of L, Q of C2, TB of C, RTq/RTd of
`expW-queue-w6`/`expW-direct-w6`, RRq/RRd of their K0 and K0F cells), with the reference runs' schedule files
(`test_prereg.Release077` checks that the 077 configs give byte-identical schedules): `pairs_report.py` with this plan
decided every hypothesis with a test (n = 8; n = 5 for K-1, K-2), evaluated every detection and absence rule, and
paired every cross-run estimand, with difference 0 where both arms are the same cells; `policy_compare.py` paired every
comparison.

**Simulated testbed** (the driver end to end on one machine: sshd on five loopback addresses as VM-A to VM-E, Docker
as VM-B with Mosquitto, the S3 stand-in, MinIO and ThingsBoard CE 4.3.1.6 images; the firmware builds placed as VM-D's
build output; a copy of this tree with this plan frozen; every campaign shortened to 1 episode of 180 s, L2 300 s):
every stage PASS, every campaign cell VALID.

| Stage | Result |
|---|---|
| `preflight build verify distribute` | PASS (verify: `go test`, 80 conformance, 120 analysis and the firmware host tests) |
| `measure zephyr-plain`, `zephyr` | K0; {D_h, Q_A, F} (K3), dedup horizon 600 s |
| `measure zephyr-mcu` | {D_h, Q_A, F} (K3) on the emulated Cortex-M3, horizon 600 s, as the native measurement of the same sources |
| `measure zephyr-auth` | A1-A13 PASS |
| `measure thingsboard` | `thingsboard-rpc`, `thingsboard-rpc-oneway`, `thingsboard-rpc-r0`: {Q_A, T} (K2) |
| `qos-dup` (60 commands, 12 resets) | QoS 1: 21 delivered twice, none lost; QoS 0: none twice, 11 lost |
| `scale` (1 episode of 60 s per size) | 8, 16, 32 domains: every command clean, no safety violation; authority CPU 1.4-1.7 % per process |
| `latency-control` | `gen` p50 7.6 / 28.2 / 133.3 ms and `client` p50 8.4 / 28.9 / 133.8 ms at 1 / 5 / 20 commands/s per device (the generator's share on a 2-core host) |
| `report` | every section, among them the pairs and policy comparisons of this plan, the late-effect attribution, qos-dup, the authenticated firmware, the emulated MCU and scale |

| Config | Cell | Clean | Safety counts | Notes |
|---|---|---|---|---|
| expR_zephyr_winf | ZEPHYR_PLAIN / ZEPHYR_CUTOFF / ZEPHYR_PLAIN_DIRECT / ZEPHYR_F_DIRECT | 98.6 / 100 / 69.9 / 100 % | 0 | 9 and 137 fences |
| expQ_zephyr | ZEPHYR_PLAIN_QOS1 / ZEPHYR_PLAIN_QOS0 / ZEPHYR_CUTOFF_QOS1 | 37.5 / 32.9 / 100 % | 3 duplicate effects (QoS 1, planned) / 0 / 0 | the gateway logs `qos_cmd` 1 / 0 / 1 |
| expL2_sleepy (300 s) | K2 / K3T | 70.1 / 70.8 % | 0 | late effects 17 (1 first send, 16 resends sent within the validity, 0 after it) / 0 |
| expWAN_s6 | K0 / K0F / K0S | 38.9 / 73.2 / 100 % | 0 | the devices log the WAN delay |
| expC16_minio | MINIO_PLAIN / MINIO_COND | 79.4 / 99.2 % | 0 | |
| expTB_thingsboard | EVICON / M1 / NAIVE | 98.6 / 60.2 / 90.4 % | 0 / 0 / 31 duplicate effects, 2 bound violations (planned) | ThingsBoard devices made and deleted per cell |
| expK2_w6 | K2, K3F x EVICON, RES_PERIODIC | 100 % each | 0 | |
| expRR_queue | EVICON / RELEASE_ON_READ | 100 / 87.1 % | 0 / 64 false STOPPED, 47 logical duplicates, 1 bound violation (planned) | |
| expV_mild | K0 / K0F / K0S | 89.3 / 89.5 / 100 % | 0 | |
| expR_minio_winf | MINIO_PLAIN / MINIO_COND | 85.9 / 99.2 % | 0 | |
| expO_noorphan | K0 / K0F / K0S | 38.9 / 100 / 100 % | 0 | |
| expRT_queue | K0 / K0F (RELEASE_ON_TIMEOUT) | 81.4 / 81.9 % | 103 and 94 false STOPPED, 68 and 66 logical duplicates (planned) | |
| expRR_direct | EVICON / RELEASE_ON_READ | 100 / 98.9 % | 0 / 4 false STOPPED, 4 logical duplicates (planned) | |

`pairs_report.py` and `policy_compare.py` on these runs: every one-episode hypothesis NOT COMPUTABLE (n = 1, as expected),
Q-1 DETECTED, Q-2 and Q-3 ABSENT, L2-2 ABSENT, TB-1 DETECTED, TB-S1 and TB-S2 ABSENT, TB-E1 +38.4 pp clean for EVICON
over M1.

**Simulator.** `sim_review077.py` (section 1(g), with the WAN scenario), `sim_sensitivity077.py` (section 1(h)) and
`sim_theta077.py` (section 1(i)) ran on the release-077 simulator.

**Changes made before freezing** (from these checks): `policy_compare.py` counts the releases without evidence by
reason; `sim_sensitivity077.py` sets "always online" as the simulator expects; the expectation for RR-2 in section 1(e)
and its interpretation in section 3; the resend cut set to validity - deadline (the authority knows a command's
creation, not its intent's arrival); the 077 gateways arm the crash fault 100 ms after answering the arming request,
and `expR_zephyr_winf` runs on the 077 runner; with a loopback VM-B (the simulated testbed) the MCU image reaches the
broker through a QEMU forward (`guestfwd`), on the testbed directly; the ThingsBoard runner names its device
processes after the role their `/healthz` reports, and the driver fails a ThingsBoard run without a VALID cell.

## Deviations

None so far.

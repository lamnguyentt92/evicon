# EviCon-Probe: measuring evidence contracts (contribution C2)

EviCon-Probe measures which capabilities of the set `{D_h, Q_A, Q_S, F, X, T}` a (system, operation)
pair provides. The result is compared with the system's documentation and written out as an evidence
contract with source `MEASURED`, in the same schema as `internal/contract`. evicon-des and the EviCon
authority can use such a contract directly; the authority still checks its provenance before accepting it.

Main rule: **truth is never taken from what the client was told.** Truth is read from a separate
channel: the owner's state read out of band, or `evicon-plant` (the plant emulator).

## Layout

| Path | Content |
|---|---|
| `evicon_probe/faults.py` | Directional faults: `FaultProxy` (TCP/UDP in process, no root needed; optional TLS termination), `Toxiproxy` (VM-C), `Netem` (VM-E, with dry run) |
| `evicon_probe/probes.py` | P1–P9, P9F (fence), P9T (owner expiry), P5B (horizon bisection), and P2N/P5N/P9N (retention under the pressure of `pressure_n` intervening identities) |
| `evicon_probe/rubric.py` | Probe results → state of each capability (OBSERVED / CONTRADICTED / ABSENT / UNKNOWN / DECLARED_UNVERIFIED) with provenance, the horizon as a lower bound, deviations from the documentation, and the `MEASURED` contract |
| `evicon_probe/contract_map.py` | Contract Map: the catalog (DOCUMENTED/HYPOTHESIS) plus the measured contracts; has a `declared_unverified` column |
| `evicon_probe/adapters/` | One adapter per system (table below) |
| `reference/` | Owner-side reference applications: CoAP actuator, MQTT device (also in ThingsBoard mode), Temporal worker, DBOS app; the **S3 owner gateway** of experiment C (`s3_owner_gateway.py`, `s3_client.py`); the **MQTT owner gateway** of experiment C2 (`mqtt_owner_gateway.py`: owner protocol ↔ MQTT to the Zephyr firmware) and its extension for experiment C3 (`mqtt_owner_gateway_ext.py`: restricted contracts, direct channel); the firmware reboot check (`zephyr_reboot_check.py`); release 077: the MQTT gateway's delivery binding and offline cut (`mqtt_owner_gateway_ext077.py`), the **ThingsBoard owner gateway** (`tb_owner_gateway.py`: owner protocol ↔ persistent server-side RPC), the QoS duplicate check (`qos_dup_check.py`) and the authenticated-firmware check (`zephyr_auth_check.py`) |
| `configs/` | 30 configurations for the 5-VM testbed (IPs 192.168.107.150–154; `evicon_review.py` substitutes them from `testbed.json`). Controls carry an `expect` key (the contract they must reproduce) |
| `compose/vm-*` | docker-compose files and scripts per VM (for the external systems) |
| `tests/` | Positive and negative controls, codecs, rubric (with the regression cases in `RubricReviewRegressions`), MQTT adapter (`MqttFenceNeverInfersStopped`), SigV4, cross-check of the lattice against Go, fault proxy, compose files against the driver, owner gateways, Temporal worker sandbox |

## Adapter status

The "Code" column is the `implementation_status` each adapter class declares (printed by `list`); for
several adapters it predates their testbed measurement. The last column lists the measurements on the
5-VM testbed recorded in this tree (`CHANGELOG.md`, `experiments/owners/`, `evaluation/prereg/RESULTS_*.md`).

| Adapter | System | Code | Measured on the testbed |
|---|---|---|---|
| `evicon_endpoint` | EviCon E1/E2/E2_STOP/E3 | TESTED_LOCAL | positive controls of every review run (`controls`): E1→{D_h,Q_A}, E2→+F, E2_STOP→+Q_S+F, E3→{D_h,Q_A} with X DECLARED_UNVERIFIED |
| `evicon_device` | evicon-device K0…K4, CUTOFF | TESTED_LOCAL | negative controls of every review run: K0, finite horizon, CUTOFF without dedup, owner expiry, offline queue |
| `coap` | CoAP CON (aiocoap reference) | TESTED_TESTBED | `measure coap`, aiocoap 0.4.17: message-level horizon against application-level identity |
| `http_idempotency` | API with Idempotency-Key | TESTED_LOCAL | none (reference server in `tests/`, with retention and 409 on concurrent duplicates) |
| `mqtt5_rpc` | Mosquitto 2.x, EMQX 5.8, Zephyr firmware | TESTED_TESTBED | `measure mosquitto` (Mosquitto 2.0.20), `measure emqx` (EMQX 5.8.6); `measure zephyr`, `zephyr-stop`, `zephyr-plain`: the firmware behind Mosquitto, the three modes in the review runs of C2 and C3. The adapter never infers STOPPED from "fence OK + no receipt seen" |
| `nats_jetstream` | NATS JetStream | TESTED_TESTBED | `measure nats`, NATS JetStream 2.10.22 |
| `thingsboard_rpc` | ThingsBoard CE RPC | TESTED_LOCAL | `measure thingsboard`, ThingsBoard CE 4.3.1.6 (testbed run of release 068) |
| `leshan_execute` | Leshan + LwM2M client | UNTESTED_NEEDS_TESTBED | none: no `measure` stack |
| `matter_invoke` | chip-tool + all-clusters-app | UNTESTED_NEEDS_TESTBED | none: no `measure` stack (the apps can be built with `build_iot.sh start matter`) |
| `k8s_create` | k3s (`name` vs `generateName`) | TESTED_LOCAL | `measure k3s`, k3s v1.36.4+k3s1 API server (testbed run of release 068) |
| `s3_conditional_put` | MinIO (`If-None-Match: *`) | UNTESTED_NEEDS_TESTBED | `measure minio`, MinIO RELEASE.2025-04-22T22-12-26Z, 3 repetitions (release 070); the contracts in `experiments/owners/minio/` are used by experiment C. SigV4 is also checked against the AWS documentation example |
| `temporal_activity` | Temporal | TESTED_TESTBED | `measure temporal`, Temporal Server 1.27.1 |
| `dbos_step` | DBOS Transact | TESTED_LOCAL | `measure dbos`, DBOS Transact 3.3.0 on PostgreSQL 16.10 (testbed run of release 068) |

An `UNTESTED` adapter is written against the published API but has never met the real system. **Its
first run on the testbed is the test of the adapter.** When that run gives a strange result, fix the
adapter first and draw no conclusion about the system. For adapters that use a reference application
(`reference/`), what is measured is the contract of **broker + reference application**, not of the
broker alone.

## Installation (VM-C, where the harness runs)

The positive and negative controls need only the Python standard library. External systems need their
clients:

```bash
python3 -m venv ~/evicon-probe-venv && . ~/evicon-probe-venv/bin/activate
pip install -r conformance/requirements-conformance.txt
python3 conformance/run_tests.py --require-go    # needs Go (PATH or .tools/go); reports why any test was skipped
python3 conformance/run_probe.py list            # in conformance/: python3 -m evicon_probe list
```

## Running a measurement

The positive and negative controls run automatically with `python3 evicon_review.py controls` (see
`README.md` and `ARTIFACT.md` for the testbed procedure). The fault proxy runs in process on VM-C; no
Toxiproxy is needed.

Real systems are measured with `python3 evicon_review.py measure <stack> [<stack> ...]`. For each stack
the command:
- starts the plant and the reference application when the stack needs one (`reference/mqtt_device.py`,
  `coap_device.py`, `temporal_worker.py`, `dbos_app.py`); each answers `/healthz` with its role, release
  and pid;
- brings the system up (docker compose in EviCon's project `evicon-probe-owner` on VM-B, or Temporal's own
  dev server on VM-D) and checks readiness by protocol;
- runs the stack's configs through the in-process `FaultProxy` (it replaces the fault controller of the
  config; some configs still name Toxiproxy for manual runs);
- stops what it started (also on failure) and writes `results/LATEST/measure/<stack>/measure.md`.

A failing stack does not stop the following ones.

| Stack | Owner | Truth | Notes |
|---|---|---|---|
| `mosquitto` | Mosquitto 2.0.20 (VM-B), reference MQTT 5 device (VM-E) | plant on VM-E | `mosquitto-rpc.json`, fault proxy on port 21883 |
| `coap` | aiocoap 0.4.17 reference actuator (VM-E) | plant on VM-E | message-level and application-level identity |
| `temporal` | Temporal Server 1.27.1 (`temporal server start-dev`, VM-D:7243), activity worker on VM-A | plant on VM-E | the effect goes through VM-C to the plant; fault proxy on port 28210 |
| `emqx` | EMQX 5.8.6, port 1884 | plant on VM-E, through the reference MQTT device | as Mosquitto, same reference device |
| `nats` | NATS JetStream 2.10.22, port 4222 | number of messages of the command ID's subject in the stream | no plant; P5B brackets the duplicate window (default 120 s) |
| `minio` | MinIO, port 9000 | number of object *versions* per command ID (versioned bucket) | with and without `If-None-Match: *`; a tombstone written with `If-None-Match` is a per-identity stop |
| `dbos` | DBOS Transact 3.3.0 (`reference/dbos_app.py`, VM-A) on PostgreSQL 16.10 (VM-B:5433) | plant on VM-E; the step goes through the fault proxy VM-C:28211 | workflow id = command id; step timeout 2 s, 3 attempts |
| `k3s` | Kubernetes API server (k3s v1.36.4+k3s1, API server only, VM-B:16443) | the ConfigMap object in the API server | `k8s-name` (fixed name) and `k8s-generatename`; the fault proxy terminates TLS (`tls` option of `FaultProxy`) |
| `thingsboard` | ThingsBoard CE 4.3.1.6 + PostgreSQL 16.10 (VM-B:18088, MQTT 1885) | plant on VM-E, through `reference/mqtt_device.py --mode thingsboard` | persistent two-way and one-way RPC, and (077) two-way without platform retries (`thingsboard-rpc-r0`, the call shape of run TB); the demo passwords are replaced by random ones |
| `zephyr`, `zephyr-stop`, `zephyr-plain` | firmware `firmware/zephyr-cutoff` (Zephyr v4.2.0, `native_sim/native/64`, VM-E) in its cutoff, stop or plain mode, behind Mosquitto (VM-B) | plant on VM-E (line protocol, loopback) | query/fence on a separate connection to the broker (`control_broker`) |
| `zephyr-reboot` | the firmware in stop mode, stopped and started again on its flash file | plant on VM-E | not a contract measurement (`reference/zephyr_reboot_check.py`); `report` copies it to `report/reboot/REBOOT.md` |
| `zephyr-mcu` (077) | the firmware's sources built for `mps2/an385` (`firmware/zephyr-mcu`), an Arm Cortex-M3 under `qemu-system-arm` on VM-E, Zephyr's TCP/IP stack and LAN9118 driver | plant on VM-E (10.0.2.2 through QEMU's NAT) | `zephyr-mqtt-mcu.json`: the probes of `zephyr-mqtt`, unchanged |
| `zephyr-auth` (077) | the authenticated firmware (`firmware/zephyr-auth`), stop mode | plant on VM-E | not a contract measurement (`reference/zephyr_auth_check.py`, the device key in the environment); `report` copies it to `report/auth/AUTH.md` |

Credentials (MinIO, PostgreSQL, ThingsBoard, k3s) come from VM-C's environment or are generated at
random for each measurement, are passed on ssh stdin, and are never written to a file or log. MinIO has no
published images: the driver builds `evicon/minio-src` on VM-B from the official source
(`compose/vm-b/minio/Dockerfile`). `tests/test_deploy_files.py` checks the compose files against the
driver's owner table. Matter and LwM2M (Leshan) have no `measure` stack; their adapters stay `UNTESTED`.

To run a config by hand (one external system at a time on VM-B):

```bash
cd conformance
docker compose -f compose/vm-c/docker-compose.yml up -d           # Toxiproxy on VM-C (only for configs with "kind": "toxiproxy")
scp -r compose/vm-b root@192.168.107.152:/root/evicon-compose-vm-b
ssh root@192.168.107.152 'cd /root/evicon-compose-vm-b && docker compose --profile nats up -d'
python3 -m evicon_probe run --config configs/nats-jetstream.json --out ../results/conformance
python3 -m evicon_probe map --catalog ../experiments/contracts --measured ../results/conformance --out ../results/contract_map
```

By default `--out` is `results/conformance/` at the repository root, so that `python3 evicon_review.py
bundle` collects it. Each run writes `<out>/<name>/<timestamp>/`:
- `probe.log`: every progress line printed on screen;
- `errors.log`: each probe's errors, and the traceback if the run stopped;
- `manifest.json`: adapter, fault controller, parameters, host;
- `results.jsonl`: one line per probe and repetition;
- `rubric.json`: states, evidence, reasons, retention;
- `contract.json`: the `MEASURED` contract with `provenance`, `declared_unverified`, `fault_model`;
- `summary.json`: a summary, with the result against `expect` when the config has one.

`run` exits with code 3 when a control does not reproduce its expected contract.

Secrets (ThingsBoard, MinIO, PostgreSQL passwords) are passed in environment variables `${VAR}`, never
written into a config file.

## Reading the rubric

Each capability has one state, with the list of probes it rests on (`evidence`) and the reasons
(`reasons`). Only `OBSERVED` capabilities enter the `MEASURED` contract, with `provenance`.

| State | Meaning |
|---|---|
| `OBSERVED` | There is a positive control, and no probe with the same premise contradicts it |
| `CONTRADICTED` | At least one probe contradicts it (a duplicate effect, terminal evidence refuted by the truth, a record lost under pressure) |
| `ABSENT` | The system has no such interface |
| `UNKNOWN` | Undecided: no positive control, or the probe did not run or failed |
| `DECLARED_UNVERIFIED` | Declared by the documentation but not established by a black-box probe (X; an unbounded horizon) |

Main rules:
- **D_h** needs a positive control: P4 must give **exactly one** effect after a retry that the system
  processed (a timed-out retry does not count).
  - P4 without an effect → `UNKNOWN`, never evidence of dedup.
  - Any duplicate effect in P2/P4/P6/P8 vetoes D_h.
  - P5N also vetoes D_h: the dedup record was evicted after `pressure_n` identities.
- **P5B** bisects the horizon between the gap of P4 (no duplicate) and the gap of P5 (duplicate). Each step
  uses a fresh identity. A step moves a bound only when both sends were answered and the truth shows 1
  effect (the lower bound rises) or more (the upper bound falls). The bracket found is written to
  `horizon_interval_s`. Parameters: `bisect_resolution_s`, `bisect_max_steps`.
- **The horizon** is a lower bound: the largest gap without a duplicate, recorded with its load condition
  (`intervening`). A measurement never gives an unbounded horizon; a declared unbounded horizon goes to
  `declared_unverified` (`D_h_unbounded`).
  - Consequence: a measured T + Q_A does **not** let a client infer STOPPED from NOT_FOUND. That needs an
    unbounded horizon or a checked X.
- **X** cannot be measured black-box; a declared X → `DECLARED_UNVERIFIED`. The runtime accepts a
  `MEASURED` X only when its provenance carries crash-cut evidence (`CRASH_CUT`).
- **Q_S / F** need a STOPPED terminal confirmed by the truth (no effect afterwards). P9N checks that a
  tombstone still holds after `pressure_n` identities.
- **Retention** (`retention` in the rubric): how many intervening identities a receipt (P2N), a dedup
  record (P5N) and a tombstone (P9N) survive.
- Go checks the contract again: `bin/evicon-contract validate <contract.json>`. The authority refuses a
  `MEASURED` contract:
  - without provenance;
  - with an unbounded horizon;
  - with X but without crash-cut evidence.
- **Deviations** (`deviations`):
  - a declared capability that is refuted or absent;
  - an observed capability that is not declared;
  - a declared horizon longer than the observed one;
  - wrong terminal evidence.
- **Late effect**: an effect after the client saw an error or a timeout (P3, P7, P9*).

## Known limits

- Each probe uses a fresh identity and the probes run sequentially. Adapters whose truth is only a global
  counter (EviCon endpoint, Matter) measure deltas; the pressure probes adjust for this. Never run two
  campaigns in parallel against the same owner.
- P9/P9F only show that a late request has no effect *if it actually reaches the owner*. The harness cannot
  confirm arrival for every system.
- Toxiproxy may flush held data when a `latency` toxic is removed. P3/P9 therefore keep the toxic until the
  late-delivery time has passed, and only then `clear()`.
- A TCP stream keeps byte order. A chunk delayed in a fault window (for example an MQTT client's PUBACK)
  holds every byte after it, even after `clear()`. `FaultProxy.quiesce()` waits for that chunk to arrive
  before the next probe starts.
- MQTT: `keepalive_s` (default 60) must exceed the longest injected delay (P9T: 31.5 s). Otherwise the
  broker closes the connection and the delayed request disappears instead of arriving late.
- With Matter over netem, the delay applies equally to the Timed Request and to the Invoke. P9T therefore
  measures the real behaviour: a timed invoke does not block an exchange that is delayed as a whole. This
  is a finding to report, not a harness error.
- Running time:
  - P5 for CoAP waits 260 s by default (EXCHANGE_LIFETIME 247 s); P5B adds about 13 minutes per
    repetition (4 steps);
  - P5 for NATS waits 130 s;
  - P5 for MinIO `If-None-Match`, k8s with a fixed name and the Zephyr firmware (cutoff and stop modes)
    waits 600 s: that is the measured lower bound of a horizon declared unbounded. No finite probe can
    show "unbounded";
  - a campaign of 3 repetitions takes from a few minutes to about 20 minutes.

# Preregistration: experiment C, EviCon on a real owner (MinIO) on the 5-VM testbed

Release 073. Frozen before any C cell runs on the testbed. The campaign config below carries the SHA-256
of this file, and `evicon_review.py campaign` refuses to run it if this file, or any file listed under
*Frozen files*, has changed. Changes after freezing are recorded under *Deviations*; nothing above that
section is edited.

## 1. Background (exploratory evidence, not part of the test)

Every runtime experiment so far (A, B2, W, S, L, negative controls) drives `evicon-device`, an emulated
owner whose contract is chosen by a preset. Release 070 measured, with the probe suite, the contracts of
two ways of calling one real system, MinIO's S3 API on VM-B (`experiments/owners/minio/`):

| Call shape | Measured contract | Level |
|---|---|---|
| `PUT object` (unconditional) | none | K0 |
| `PUT object If-None-Match: *`, and a tombstone `PUT` under the same key as a stop | D_h (horizon at least 600 s, a lower bound), Q_A, Q_S | K3 |

Experiment C runs the unchanged EviCon runtime on those **measured** contracts, against MinIO itself:
the authority learns the contract from the owner's capability handshake (`/v1/capabilities`), as for
every DEVICE endpoint, and the truth comes from MinIO's object versions. The question is whether the
contract measured for a call shape predicts what the runtime can do with that call shape on the real
system, including under the faults the measurement covered.

**A consequence fixed before the run.** Every campaign assumes no bound on the network delay
(`authority.max_network_delay` -1ns). With a finite dedup horizon (MinIO's measured 600 s is a lower
bound, not "unbounded"), Theorem T4 then allows no same-identity retry: a retried request could arrive
after the horizon. So on `MINIO_COND` EviCon cannot use D_h for forward retries; it resolves ambiguous
commands with Q_A (APPLIED) and Q_S (a tombstone: STOPPED, then a new identity). The pre-release check
on the simulated testbed (below) shows exactly this: on `MINIO_COND` the T4 guard suppressed every
same-identity retry of EVICON and M1, EVICON resolved with queries and stops.

**Simulator prediction** (`evaluation/sim_expC.py`, release 073 discrete-event simulator, twin
`experiments/sim/iot_dosing_testbed.json` with window 6, direct owner channel, no assumed delay bound,
the two measured contracts, 200 episodes; `SIMULATION_ONLY`). Clean / confirmed clean fulfilment, % of
offered:

| Contract | EVICON | M1 | NAIVE (simulator: B1_DURABLE_RETRY) | ORACLE |
|---|---|---|---|---|
| MINIO_PLAIN | 32.5 / 31.6 | 32.5 / 31.6 | 74.5 / 74.5, **1,547 safety violations in 193 of 200 episodes** | 77.0 |
| MINIO_COND | 77.2 / 76.8 (51.9 stops per episode) | 32.5 / 31.6 | 76.8 / 76.4, 0 violations | 77.0 |

Mean per-episode effect `MINIO_PLAIN -> MINIO_COND` for EVICON: +45.3 pp clean (SD of the paired
difference 24.1), +45.8 pp confirmed (23.6). The simulator's M1 does not query here, so its M1 row is
not a prediction for the runtime's M1 (which queries; see the pre-release check).

**Pre-release check on the simulated testbed** (release 073, the full driver path: `evicon_review.py campaign`
with MinIO's compose project on the simulated VM-B; the container there is a **moto-based S3 stand-in, not
MinIO**, recorded as such in `owner.json`; 1 episode of 180 s, 4 devices, no capacity pressure at that
length). Clean fulfilment of 354 offered requests, %: PLAIN EVICON 57.6, M1 57.6, NAIVE 96.3 with **10
duplicate effects and 1 bound violation**; COND EVICON 99.2 (125 queries, 23 stops: 22 tombstones written
and re-issued, 1 answered APPLIED), M1 57.6 (14 commands unresolved at the end), NAIVE 99.2 with 0
duplicates. On `MINIO_COND` the T4 guard suppressed same-identity retries of EVICON and M1 (2,936 and
39,933 suppressed attempts), as stated above. No safety violation outside NAIVE on PLAIN. The unit test
`evaluation/test_campaign_real.py` (R4) runs NAIVE with reply-loss windows against a fake S3: duplicates
on the unconditional PUT, none on the conditional one.

## 2. Design

| Item | Value |
|---|---|
| Config | `evaluation/campaigns/expC_minio.json` |
| Owner | MinIO `evicon/minio-src:RELEASE.2025-04-22T22-12-26Z` (the image of the 070 measurement) on VM-B, in EviCon's compose project `evicon-probe-owner`, started by the driver for the campaign with a fresh volume and brought down afterwards. Credentials from VM-C's environment or random for the campaign, never in a file or a log |
| Gateway | one `conformance/reference/s3_owner_gateway.py` per device on VM-C: the EviCon device-owner protocol (`/v1/effects`, `/v1/receipts/<id>`, `/v1/stop`, `/v1/capabilities`) mapped to S3 calls; stateless (the object store is the only record); advertises the measured contract of its mode. A fresh versioned bucket per cell; one key prefix per device; key = command identity |
| Contracts | `MINIO_PLAIN` (gateway mode PLAIN: unconditional PUT; no query, no stop; contract `meas-minio-plain-070.json`, K0) and `MINIO_COND` (mode COND: PUT with `If-None-Match: *`, query = HEAD, stop = conditional tombstone PUT; `meas-minio-cond-070.json`, K3) |
| Policies | **EVICON**; **M1** (legacy selector: queries, no stop or fence; T4 retry guard on, as in experiment A); **NAIVE** = M1 with the T4 retry guard off (`real_owner.policies`): the dispatcher re-sends the same identity until it gets a receipt, i.e. a blind retry. NAIVE on `MINIO_PLAIN` is the real-system negative control |
| Regime | `expA_confirm_k0.json` (4 devices; 300 s per cell + 60 s tail; 0.5 commands/s per device; values 1-5; bound 345 per device; deadline 60 s; validity 90 s; fault windows 2/min; offline 10-40 s; mix offline 0.3, drop_up 0.2, drop_down 0.2, delay_up 0.1 (6 s), crash 0.2; authority parameters), window **6** (`max_outstanding`), new seed **20261025** |
| Faults | drop_up, drop_down, delay_up: on the authority -> gateway path (the per-device fault proxy of `campaign.py`). crash: the gateway exits after MinIO answered 200 to an effect PUT and before it replies (the effect exists, no receipt), restarted after 2 s. offline: the gateway -> MinIO path drops every byte in both directions (an in-process fault proxy in the gateway); requests then get 504 after 1.8 s. All inside the fault model of the 070 measurement, "network loss/delay, client abandon; owner up": MinIO never crashes; a gateway crash is a client abandon |
| Truth | MinIO's object versions of the cell's bucket, listed by the runner directly (never through a gateway or a fault proxy); an effect is a version with metadata `evicon-kind=effect` (HEAD per version), a tombstone is not an effect. Duplicate effects: identities with more than one effect version; bound violation: a device's effect values add up to more than 345; time of an effect: LastModified moved to VM-C's clock with VM-B's offset, measured per cell over an open SSH session (one LAN round trip). `raw/truth/` keeps the listing; `raw/E/plant.jsonl` is the effect log in evicon-plant's format, so `pairs_report.py` computes confirmed fulfilment unchanged |
| Episodes | **8** (0-7); 2 contracts x 3 policies = 48 cells, about 5.8 hours |
| Pairing | contracts of the same episode and policy (common random numbers: same arrivals and fault windows) |
| Activation gate | none (EVICON's activity on `MINIO_COND` is an estimand) |
| Analysis | `evaluation/pairs_report.py --plan evaluation/prereg/PREREG_expC_073.md --run C=<run> --out <dir>` with the spec in section 8; `evicon_review.py report` runs it with the earliest complete `expC-minio` run among its sources |

## 3. Hypotheses and decision rules

Tests of `pairs_report.py` on the per-episode paired differences (to minus from), Student t,
alpha = 0.05; C3 and C4 form one Holm family.

| ID | Hypothesis | Decision rule |
|---|---|---|
| **C1** | A blind same-identity retry on the unconditional PUT duplicates effects on the real system (negative control: the checker sees real violations) | `detects`: some VALID `MINIO_PLAIN`/NAIVE cell has duplicate effects or a bound violation |
| **C2** | The conditional PUT absorbs the same blind retries (the measured D_h holds within the cell length, 420 s < 600 s) | `absent`: no VALID `MINIO_COND`/NAIVE cell has duplicate effects, bound violations, logical duplicates, false STOPPED or false APPLIED |
| **C3** | With EVICON, the conditional call shape raises clean fulfilment by at least 10 pp | `at_least 10`, EVICON, `MINIO_PLAIN -> MINIO_COND`, `clean_pp` |
| **C4** | With EVICON, it raises confirmed fulfilment by at least 10 pp | `at_least 10`, EVICON, `MINIO_PLAIN -> MINIO_COND`, `confirmed_pp` |
| **C-S** | Safety on the real owner wherever the T4 guard is on: EVICON and M1 on both contracts | four `absent` checks (C-S1 PLAIN/EVICON, C-S2 PLAIN/M1, C-S3 COND/EVICON, C-S4 COND/M1): bound violations, duplicate effects, logical duplicates, false STOPPED, false APPLIED all 0. Late effects are reported separately |

Interpretation fixed in advance:
- C1 DETECTED and C2 ABSENT: on one real API, the call shape decides whether a client's retry is safe;
  the evidence contract measured for each shape says which (the paper's "same API, different
  evidence"). C1 NOT DETECTED: in this regime no blind retry hit an applied command (reported; the
  device-level negative controls of 072 already showed that the checker detects duplicates). C2
  PRESENT: MinIO's conditional create did not deduplicate under these faults, a contradiction of the
  070 measurement; every C result is then reported with it.
- C3 and C4 MET: the measured contract predicts the runtime's value on the real system (the simulator
  predicted +45 pp). NOT MET: reported with the estimate; the simulator's prediction is then wrong for
  this owner, and the paper says so.
- C-S PRESENT anywhere: a safety failure of EviCon on a real owner; reported with the cells, and the
  plan of record stops until the cause is known.

## 4. Estimands (reported with intervals, no claims)

| ID | Estimand |
|---|---|
| E-C1 | EVICON, `MINIO_PLAIN -> MINIO_COND`: admitted, stranded capacity-time (resolved, censored), mean time to a terminal outcome, queries, stops, stops that reached MinIO and their terminal answers, re-issues after STOPPED, unresolved at the end |
| E-C2 | M1, `MINIO_PLAIN -> MINIO_COND`: clean and confirmed fulfilment, queries, unresolved at the end (M1 has Q_A but no stop) |
| E-C3 | NAIVE, `MINIO_PLAIN -> MINIO_COND`: clean and confirmed fulfilment, duplicate effects, bound violations |
| E-C4 | Levels per contract and policy (`pairs_report.py` levels table): EVICON against M1 and NAIVE on the same contract, and the testbed against the simulator prediction of section 1 |

## 5. Sample size

Power from the simulator's 200 per-episode paired differences (EVICON, `MINIO_PLAIN -> MINIO_COND`),
resampling n episodes and applying C3 and C4 with Holm (2,000 resamples; `SIMULATION_ONLY`):

| n | C3 | C4 |
|---|---|---|
| 6 | 0.89 | 0.91 |
| **8** | 0.98 | 0.99 |

Eight episodes. C1 needs one detecting cell: the simulator's NAIVE violated safety in 193 of 200
episodes on `MINIO_PLAIN`.

## 6. Exclusions, stopping and order

- INVALID cells are excluded and listed (a cell is INVALID when a fault could not be applied, a process
  died, the truth listing failed, or an object no client identity explains, or a value other than the
  client's, is found in the bucket). An INVALID cell is re-run once by resuming the campaign; if it is
  INVALID again it stays excluded. A run with more than 10 % excluded cells is not interpretable and its
  hypotheses are not decided.
- No interim analysis: `pairs_report.py` is run only when every cell is done. The campaign may be split
  over several sessions (resume; MinIO is started again with a fresh volume, every cell has its own
  bucket).
- The earliest complete run of `expC_minio` is the preregistered one.

## 7. Frozen files

SHA-256 at freezing time (`scripts/freeze_prereg.py`); the driver checks them before running. The six
files frozen for experiment A are frozen again, unchanged; added through the `frozen-extra` block: the
analysis (`pairs_report.py`, `cadence_report.py`), the runner of this experiment, the gateway and the
code it runs (S3 client, SigV4 signer of the measured adapter, fault proxy, health helper), and the two
measured contracts.

The Go runtime is built from release 073; the run's `source.json` records its source.

```frozen-extra
evaluation/pairs_report.py
evaluation/cadence_report.py
evaluation/campaign_real.py
conformance/reference/s3_owner_gateway.py
conformance/reference/s3_client.py
conformance/reference/_health.py
conformance/evicon_probe/faults.py
conformance/evicon_probe/adapters/s3_conditional.py
experiments/owners/minio/meas-minio-cond-070.json
experiments/owners/minio/meas-minio-plain-070.json
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
65eb699cf290296595eb084c031570a9edcd5004a2869de15b35c1f1ef958557  evaluation/campaign_real.py
81506987d1b347fdb46a0cbbe0e7e457309414329a055cd6dc92a04afbc4c226  conformance/reference/s3_owner_gateway.py
a2314c70898999dc16892ab9815c002d972a54826004d75d7e1ba7748a9e6d4c  conformance/reference/s3_client.py
ac7f3cad94d465453b266f9eadd423275c75451714ef064d5b3adf52b3ef655d  conformance/reference/_health.py
16571e8004a5fc394dfb4a21c25bcc952e6736176f4b1ff62d7a43c4e3e903a8  conformance/evicon_probe/faults.py
50d034e96a9ccc956c576fd249bc9147e25664a6c073a39f11489454377c8506  conformance/evicon_probe/adapters/s3_conditional.py
dbe4797d7c73cf99049931ec1c5b97b1a657bd7d407b34e5f02b9049986017eb  experiments/owners/minio/meas-minio-cond-070.json
5215bebd11b54436b37063c8bcfd925327da389f9c75daf541bb68b0587d3d59  experiments/owners/minio/meas-minio-plain-070.json
```

## 8. Analysis spec (read by pairs_report.py)

```pairs-spec
{
  "name": "Experiment C: EviCon on MinIO (073)",
  "runs": {"C": {"campaign": "expC-minio"}},
  "alpha": 0.05,
  "max_invalid_fraction": 0.10,
  "hypotheses": [
    {"id": "C1", "run": "C", "test": "detects", "contract": "MINIO_PLAIN", "policy": "NAIVE", "any_of": ["duplicate_effects", "bound_violations"]},
    {"id": "C2", "run": "C", "test": "absent", "contract": "MINIO_COND", "policy": "NAIVE", "any_of": ["duplicate_effects", "bound_violations", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "C3", "family": "primary", "run": "C", "policy": "EVICON", "from": "MINIO_PLAIN", "to": "MINIO_COND", "metric": "clean_pp", "test": "at_least", "margin": 10.0},
    {"id": "C4", "family": "primary", "run": "C", "policy": "EVICON", "from": "MINIO_PLAIN", "to": "MINIO_COND", "metric": "confirmed_pp", "test": "at_least", "margin": 10.0},
    {"id": "C-S1", "run": "C", "test": "absent", "contract": "MINIO_PLAIN", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "C-S2", "run": "C", "test": "absent", "contract": "MINIO_PLAIN", "policy": "M1", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "C-S3", "run": "C", "test": "absent", "contract": "MINIO_COND", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "C-S4", "run": "C", "test": "absent", "contract": "MINIO_COND", "policy": "M1", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]}
  ],
  "estimands": [
    {"run": "C", "policy": "EVICON", "from": "MINIO_PLAIN", "to": "MINIO_COND", "metrics": ["admitted_pp", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s", "queries", "stops", "stops_reached", "stops_reached_terminal", "reissues", "unresolved_at_end"]},
    {"run": "C", "policy": "M1", "from": "MINIO_PLAIN", "to": "MINIO_COND", "metrics": ["clean_pp", "confirmed_pp", "queries", "unresolved_at_end"]},
    {"run": "C", "policy": "NAIVE", "from": "MINIO_PLAIN", "to": "MINIO_COND", "metrics": ["clean_pp", "confirmed_pp", "duplicate_effects", "bound_violations"]}
  ]
}
```

## 9. Departures from the plan of record (section 4.6), decided before freezing

- The gateways run on VM-C, not VM-B: nothing has to be deployed to VM-B besides MinIO's compose file,
  and the gateway -> MinIO path crosses the LAN as the 070 probe's did. The offline fault is applied on
  that path, inside the gateway.
- Reply loss, request loss and delay act on the authority -> gateway path (the same fault proxy as in
  every other runtime experiment) rather than between the gateway and MinIO. The gateway is stateless
  and adds no retry, so the owner sees the same thing: a lost request never reaches MinIO; a lost reply
  leaves an effect without a receipt; a delayed request reaches MinIO late.
- The naive client is the authority's own dispatcher with the T4 guard off (same identity re-sent
  until a receipt), not a separate client; window 6 (the binding window of experiments W and S).

## 10. Deviations

(none)

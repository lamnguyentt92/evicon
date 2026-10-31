# evaluation: campaign runtime, analysis, power and preregistration

| File | Role |
|---|---|
| `campaign.py` | Campaign runtime C3. The EviCon authority (VM-A) drives `evicon-device` (VM-E) under each contract, with fault injection; truth comes from `evicon-plant`. Mode `local` (development, one machine) and `remote` (testbed, SSH as root from VM-C). The fault proxy always runs in process on VM-C. Frozen by the plans; later runners import it unchanged |
| `campaign_ext077.py` | `campaign.py` plus the two unsafe practice baselines `RELEASE_ON_TIMEOUT` and `RELEASE_ON_READ` and the validity cut of resends (release 077, below) |
| `campaign_zephyr_ext077.py` | The C3 runner plus the delivery binding of MQTT commands and the offline cut (release 077, run Q) |
| `campaign_tb.py` | EviCon on ThingsBoard CE's persistent RPC (release 077, run TB), on the frozen C2 runner's cell loop |
| `ext083.py`, `campaign_ext083.py`, `campaign_real_ext083.py`, `campaign_tb_ext083.py` | The 083 policies (M1W, RELEASE_ON_READ_ANSWERED, RELEASE_ON_TIMEOUT_<n>S, EVICON_GREEDY, EVICON_FC<x>) installed on `campaign_ext077.py`, `campaign_real.py` and `campaign_tb.py`, which are imported unchanged (release 083, below) |
| `campaigns/local_smoke.json` | Pipeline check on one machine (`make campaign-local`). **Development numbers are not measurements** |
| `campaigns/pilot_pending_binding.json` | Testbed pilot, *pending-binding* regime |
| `campaigns/pilot_capacity_binding.json` | Testbed pilot, *capacity-binding* regime |
| `campaigns/expA_dosing.json` | **Experiment A on the testbed**: pairs (K0, K0F) and (K2, K3F); policies M1, EVICON, RES_PERIODIC. The bound 345 is frozen as mean demand / 1.3. Its `sim_twin` is `experiments/sim/iot_dosing_testbed.json`. Run: `python3 evicon_review.py campaign evaluation/campaigns/expA_dosing.json --episodes 5` |
| `analyze.py` | Merges `episodes.csv` (evicon-des) and `runtime_episodes.csv`. Writes `summary`, `paired` (experiment B: value of the policy), `contract_upgrade` (experiment A: value of the contract), `report.json`, figures. `--margin-pp X` adds TOST p-values (equivalence within ±X points of the offered commands) |
| `power.py` | Sample size for paired (CRN) comparisons from a pilot, with Holm and a bootstrap check. Two designs: superiority (default) and TOST equivalence (`--equivalence`). Two kinds of comparison: B (`--policy` and `--reference`) and A (`--upgrade BASE:UPGRADED`). Each group gets a status `SIZED`, `INSUFFICIENT_PILOT` (< 3 pairs), `NOT_ACTIVATED`, `UNDETERMINED`, `NOT_EQUIVALENT`; no group at all is reported as `NO_PAIRS`. `--json` writes machine-readable output |
| `sim_to_real.py` | Simulator against testbed on the clean-fulfilment rate: sign and size of each effect A (bootstrap CI) in each world, Kendall τ-b of the contract ranking. Check SR: sign under a practical margin ±2 pp (`--margin-pp`), no sign disagrees, ≥ 1 non-null effect agrees, no effect undetermined on the testbed, pooled τ-b (differences under the margin are ties) ≥ 0.6. The earlier criterion is still printed (`check_065`) |
| `PREREGISTRATION_TEMPLATE.md` | Preregistration template for a confirmatory campaign |
| `test_analysis.py`, `test_sim_to_real.py` | Unit tests of `analyze`, `power`, `sim_to_real` |

## Policies

Every policy of the EVICON family uses the **same** contract, API (query/stop/fence), retry guard and re-issue
rule. They differ only in *when* and *what* is resolved:

| Policy | Authority flags | Role |
|---|---|---|
| `EVICON` | `-resolve-mode SKI_RENTAL -resolve-trigger BLOCK_AND_SWEEP` | Proposed policy |
| `RES_IMMEDIATE` | `-resolve-mode IMMEDIATE -resolve-query-first=false` | Same-capability baseline |
| `RES_QUERY_FIRST` | `-resolve-mode IMMEDIATE -resolve-query-first=true` | Same-capability baseline |
| `RES_FIXED` | `-resolve-mode FIXED -resolve-fixed <t>` | Same-capability baseline |
| `RES_PERIODIC` | `-resolve-trigger SWEEP_ONLY -resolve-sweep <period>` | Same-capability baseline |
| `M1`, `M0` | `-policy M1`, `-policy M0` | Query selectors, without stop or fence |
| `RELEASE_ON_TIMEOUT` | `-policy RELEASE_ON_TIMEOUT -release-after 10s` | Practice baseline, **unsafe by design**: commits STOPPED without owner evidence |
| `RELEASE_ON_READ` | `-policy RELEASE_ON_READ -release-after 10s` | Practice baseline, **unsafe by design**: state-read reconciliation |
| `M1W` (083) | `-policy M1W -ambiguity-after <a>` | QueryOnly with EVICON's window-full trigger (queries aged ambiguous commands when the window defers a candidate) |
| `RELEASE_ON_READ_ANSWERED` (083) | `-policy RELEASE_ON_READ_ANSWERED -release-after 10s -release-settle 7s` | Practice baseline, **unsafe by design**: releases only on an answered NOT_FOUND, read at least the settle time after the last dispatch attempt; resends stop at release-after minus settle |
| `RELEASE_ON_TIMEOUT_<n>S` (083) | `-policy RELEASE_ON_TIMEOUT -release-after <n>s` | RELEASE_ON_TIMEOUT with the release time n s (30, 60, 120), **unsafe by design** |
| `EVICON_GREEDY` (083) | EVICON's flags and `-certificate greedy` | EVICON with the greedy certificate of an additive domain instead of the bounded enumeration |
| `EVICON_FC<x>` (083) | EVICON's flags with `-fence-cost <x>` | EVICON at another cost ratio theta (run Th: x = 100) |

The `RELEASE_*` baselines break rule R2 on purpose and run only in their own cells (`campaign_ext077.py`,
`campaign_ext083.py`); `safety_total.BASELINES` is their one definition.

The value of a *contract upgrade* (for example adding F) is measured separately. The simulator uses the contract
`"<c>+F"`; the runtime uses dedicated device presets K0F (= K0 + F), K1F (= K1 + F), K3F (= K2 + F). Every policy
runs on that contract. `analyze.py` pairs K0F/K0, K1F/K1, K3F/K2 and `<c>+F`/`<c>` by itself; add other pairs with
`--upgrade BASE:UPGRADED`.

## Cells, raw evidence and validity

- Each cell `(episode, contract, policy)` has an immutable directory `cells/ep<E>/<contract>/<policy>/`; a fresh
  plant, device and authority with their own state directories; `faults.jsonl` (planned and actual time of every
  fault); `clients.jsonl` (the clients' logs); the full raw evidence.
- After each cell the CSV and `done.jsonl` are written at once. `--resume <run>` continues from the next cell.
- Logs in the run directory: `campaign.log` (every line printed on screen), `errors.log` (the full reason of every
  INVALID cell, with the traceback of any error); in the cell, `problems.txt` and the `problems` field of `row.json`.
  The CSV keeps only the first 300 characters of `cell_status`. A process that does not come up is reported at once,
  with its state (`exited`/`running`) and the last 15 lines of its own log (`<wd>/<name>.log`).
- The paths `--out`, `--resume` and `--bin-dir` are made absolute; processes start after `cd` into the cell directory.
- The fault windows of one device do not overlap. Fault kinds: `drop_up`, `drop_down`, `delay_up`, `offline`,
  `crash_after_effect` (the device process really crashes between effect and receipt, then restarts). Extra device
  flags come from `faults.device_args` (e.g. `-offline-mode drop`: the direct channel).
- A cell is marked `cell_status = INVALID:<reason>` when a fault injection failed, the truth or the authority's state
  could not be read (HTTP status is checked), a trace is missing, or a process died. `analyze.py` excludes these
  cells and lists them.
- Processes are stopped only through their pid file (no `pkill`). Before a run, the script checks the ports are free.

## Metrics

| Group | Column | Definition |
|---|---|---|
| Primary | `clean_fulfilled` | Intent with **exactly one** effect, over all its identities, before `arrival + deadline` |
| Primary | `stranded_resolved_unit_sec` | Σ value × (time of terminal evidence − admission), for identities without an effect |
| Primary | `stranded_censored_unit_sec` | As above, but without terminal evidence by the end of the cell (censored; the command may still take effect late) |
| Safety (totals) | `bound_violations`, `duplicate_effects`, `logical_duplicates`, `false_stopped`, `false_applied` | Checked against the plant over the whole history, after the drain |
| Validity | `late_effects` (effect after the validity window), `late_effects_after_client_gave_up` | Validity violations; reported separately, not added to safety. `report.json`: `groups_with_late_effects`, `late_effects_total` |
| Deadline | `fulfilled_late` | Exactly one effect but after the deadline; **not** a validity violation. `report.json`: `groups_with_fulfilled_late`, `fulfilled_late_total` |
| Activation | `evicon_decisions`, `decisions_acting`, `decisions_by_mode`, `decisions_by_action`, `defer_reasons`, `activated` | Telemetry from the authority trace |
| Cost | `queries`, `stops`, `fences` (and the `*_terminal`/`*_ok` columns), `reissues`, `dispatch_attempts`, `retry_suppressed_t4` | Mean per cell |

The simulator uses the same names for `clean_fulfilled` and stranded. In the simulator,
`safety_violations = bound + duplicate + false_stopped`; a late effect is a validity violation, reported separately.
`gap_to_full_info_reference` is signed: ORACLE is a full-information reference, not an offline optimum.

## Workflow

1. **Pilot**: `python3 evicon_review.py pilot`, or
   `python3 evaluation/campaign.py --config evaluation/campaigns/pilot_pending_binding.json`
   then the capacity-binding config.
2. **Activation gate**: `activation.json` must PASS for both pilots. Otherwise do **not** open the confirmatory
   campaign; look at `defer_reasons` and `decisions_by_mode`.
3. **Power**:
   - B: `python3 evaluation/power.py --csv <pilot>/runtime_episodes.csv --policy EVICON --reference RES_IMMEDIATE --delta 0.05 --relative`;
   - A: `python3 evaluation/power.py --csv <run>/runtime_episodes.csv --upgrade K0:K0F --upgrade K2:K3F --policy EVICON --delta-pp 10 --comparisons 2`;
   - equivalence (H-A2, H-B): add `--equivalence --delta-pp 2`.

   The driver's `campaign` command runs all three when the campaign is complete.
4. **Fill in and freeze the preregistration**: `python3 scripts/freeze_prereg.py <plan>` writes the SHA-256 of the
   frozen files into the plan and the plan's SHA-256 into the configs; commit.
5. **Run the confirmatory campaign** with `episodes = n` from step 3.
6. **Analyse**: `analyze.py --csv … --reference RES_IMMEDIATE --reference M1`.

## Testbed time

Each cell takes about `duration_s + deadline_s + tail_s + 10 s`:
- pilot: about 2.5 minutes per cell; 3 episodes × 3 contracts × 4 policies = 36 cells, about 1.5 hours per pilot;
- `expA_dosing`: about 7 minutes per cell; 5 episodes × 4 contracts × 3 policies = 60 cells, about 7 hours;
- the nine release-077 campaigns: 0.9 to 3.7 hours each (`prereg/PREREG_review_077.md`, section 2).

## Runtime semantics worth knowing

- **Activation**: EVICON runs when the guard blocks (`CERTIFICATE`), when the outstanding budget is full
  (`PENDING_BOUND`), and from the background sweep (`SWEEP`, every 1 s by default).
- **Fence-then-retry**: an owner-certified barrier seals the admitted commands with `seq ≤ cutoff` that the owner does
  not report. The client then re-sends the intent under a new identity and sequence. `logical_duplicates` must be 0.
- **A barrier that cannot complete**: an owner that does not answer gets the barrier again with the same token, also
  from the sweep; an UNCERTAIN answer for an identity (an orphaned claim after a crash) **aborts** the barrier
  (nothing sealed or anchored; the identity is marked owner-uncertain). So on an owner that cannot reconcile, one
  orphaned claim below the frontier makes every full-frontier fence impossible to complete.
- **T4 retry guard** (`-retry-guard`): for an owner without D_h, or with a finite D_h while the delay is unbounded,
  the outbox never re-sends the same identity.
- **Owner expiry**: for a contract with T, the outbox sets `expires_at_ms = creation + expiry`.
- **Campaign client**: each device has one ordered submitter that assigns the sequence at send time. The authority
  refuses a non-monotone sequence.

## Experiment A, confirmatory (067)

- `prereg/PREREG_expA_confirm_067.md` (hypotheses, decision rules, sample size, exclusions, frozen files);
  `campaigns/expA_confirm_k0.json`, `campaigns/expA_confirm_k2.json`. `../scripts/freeze_prereg.py` freezes a plan;
  the driver refuses a confirmatory config whose plan or frozen files changed. `test_prereg.py` tests this for every plan.
- `prereg/RESULTS_expA_confirm_067.md`: the outcome under the plan's decision rules, *Deviations* (a device-emulator
  bug found after the run) and a post hoc sensitivity analysis, regenerated by `confirm_report.py`
  (`evicon_review.py report`). `prereg/REPLICATION_expA_k0_069.md`: the K0 replication on the fixed emulator
  (decided post hoc, decision rule written before the run; replicated), added by `confirm_report.py --k0-rep <run>`.

## Audit, RQ4 and paper tables

- `fence_audit.py`: every fence call in the authority traces, classified by error text; `EVIDENCE_CONFLICT`, stuck
  domains, receipts reversed in the device journal.
- `campaigns/rq4_load_r{1,5,20}.json`: fault-free load campaigns for RQ4 (`evicon_review.py overhead`);
  `rq4_sampler.py` reads `/proc` of the EviCon processes (CPU, RSS, I/O) every second on VM-A and VM-E, read only.
- `overhead.py`: admission/decision/round-trip/terminal latency, fences/queries/stops per command, fence pause time,
  CPU/RSS, trace events and journal size per command; admission split `admit = in + decide + out`,
  `in = client + lock + eval` (from the `ADMISSION_RECEIVED` trace event).
- `timeline.py`: one real episode (faults, clients, authority, device journal, plant truth on one time axis), table
  and figure. `paper_tables.py`: reads `report/`, writes `report/paper/` (LaTeX tables, `numbers.tex` with the `\ev...`
  macros quoted in the paper, PDF figures). Every number of the paper is regenerated by `evicon_review.py report`.

## Experiment B2: cadence (071)

- `campaigns/expB2_cadence.json`: K0F; EVICON at a 10 s cadence against RES_PERIODIC; 32 episodes; the seed of
  `expA_confirm_k0`. `prereg/PREREG_expB2_cadence_071.md`: H-C (equivalence within ±5 points), H-S, estimands E1–E3.
  `cadence_report.py`: the analysis, with a difference in differences against the K0 run at a 1 s cadence.

## Experiments W, S, L and negative controls (072)

- `campaigns/expW_{queue,direct}_{w6,winf}.json` (window and owner channel), `expS_{w6,winf}.json` (stop against
  fence), `expL_sleepy.json` (exploratory), `negctl_072.json`; plans `prereg/PREREG_expW_072.md`, `PREREG_expS_072.md`,
  `PLAN_expL_072.md`, `NEGCTL_072.md`. `pairs_report.py`: paired analysis driven by the ```pairs-spec``` block of a
  frozen plan; `sim_sweep.py`: the simulator sweep behind the plans; `t3_attribution.py`: Theorem 3 on the traces;
  `evidence_latency.py`: the time of one query, stop or fence per measured system.

## Experiment C: MinIO as a real owner (073)

- `campaign_real.py`: reuses `campaign.py` (import only). Owner: one S3 gateway per device
  (`conformance/reference/s3_owner_gateway.py`, VM-C) in front of MinIO (VM-B); one versioned bucket per cell.
  Truth: the bucket's object versions, read directly from MinIO, written to `raw/truth/` and `raw/E/plant.jsonl`
  (evicon-plant format, so `pairs_report.py` computes confirmed fulfilment as for every campaign).
  Pseudo-policy `real_owner.policies`: `NAIVE` = M1 with the T4 guard off.
- `campaigns/expC_minio.json`, `prereg/PREREG_expC_073.md`: 48 cells, window 6, seed 20261025; the plan also freezes
  the runner, the gateway and the two MinIO contracts measured in 070 (`experiments/owners/minio/`).
- `sim_expC.py`: the prediction; `sim_predictions/`: every frozen prediction. `predict_check.py`: every testbed
  result next to its prediction (levels, effects, sign and three-way direction, inside the 95 % interval, MAE,
  Spearman). `expC_summary.py`: one row per call shape and policy.
- Tests: `test_campaign_real.py` (R1–R4), `conformance/tests/test_s3_owner_gateway.py` (G1–G7, fake S3 `fake_s3.py`).

## Experiment C2: Zephyr firmware behind Mosquitto (074), and owner fidelity

- `campaign_zephyr.py`: reuses `campaign.py` and `campaign_real.py` (import only). Owner: one firmware process per
  device (`native_sim`, VM-E) in the evidence mode of the contract (`-evc_mode plain|cutoff|stop`), with its own flash
  file and topic prefix per cell; Mosquitto on VM-B; one HTTP↔MQTT gateway per device on VM-C
  (`conformance/reference/mqtt_owner_gateway.py`). Truth: `evicon-plant` on VM-E.
- `campaigns/expC2_zephyr.json`, `prereg/PREREG_expC2_074.md`: 56 cells, window 6, **seed 20261017 as `expW_queue_w6`
  and `expS_w6`**, so episodes 0-7 replay their fault schedules exactly. Each mode's contract is measured in the same
  review run (`measure zephyr-plain zephyr zephyr-stop`); the driver runs only on contracts measured as planned, on
  the same firmware binary.
- `sim_expC2.py`: the prediction; `expC2_summary.py`: one row per mode and policy, with the commands whose retry the
  T4 guard withheld and what the gateways saw (barriers, stops, link resets). `fidelity_check.py`: emulated against
  real owner at the same configuration (Welch interval, TOST ±5/±10 points; paired when the schedules are identical).
- `report --sources-of <release directory>`: re-reads every run that release's newest report read; `report --archive
  DIR` (078): the same for an `import-results` archive (the runs its newest report read).
- Tests: `test_campaign_zephyr.py` (Z-R1…Z-R3), `conformance/tests/test_mqtt_owner_gateway.py` (Z1–Z9), `test_predict.py`.

## Experiment C3, safety total, claims check (075-076)

- `campaign_zephyr_ext.py`: the C2 runner with contracts restricted to {Q_A}, {F} or {Q_S} and an owner channel
  (queue, or direct: `mqtt_owner_gateway_ext.py --offline-mode drop`); `campaigns/expC3_ablation.json`,
  `prereg/PREREG_expC3_075.md`, `sim_expC3.py`, `power_expC3_testbed.py`, `expC2_summary.py --experiment C3`.
- `safety_total.py`: safety counts of every VALID cell of every TESTBED campaign, with the exact one-sided 95 % bound
  for zero events. `claims_check.py`: every hypothesis with its verdict, and the cross-cutting claims, checked against
  the report. `results_record.py`: writes `prereg/RESULTS_<plan>.md` (and `prereg/notes/`) from a report.
- Tests: `test_release075.py`, `test_campaign_zephyr_ext.py`.

## Release 077: review experiments (`prereg/PREREG_review_077.md`)

One plan, seventeen campaigns and three checks, each testing how far an earlier result depends on its setting or
answering a question of the review. The plan holds the hypotheses (families U, O, K, V, WAN; detection rules RT, RR,
Q, TB; absence rules RR-S, L2, Q, TB; safety R-S; checks X-Q0, X-MCU, X-AUTH), the simulator prediction, the power under the
simulator's and the testbed's spread, and the analysis spec (```pairs-spec```, ```policy-compare```). The configs
carry the plan's SHA-256 once it is frozen; the earliest complete run of each is the preregistered one.

| Config | Run | Runner | What it tests | Contracts × policies, episodes |
|---|---|---|---|---|
| `expR_minio_winf` | Rm | `campaign_real.py` | C at an unbounded window (`max_outstanding` 1000), seed of C | MINIO_PLAIN, MINIO_COND × EVICON, 8 |
| `expR_zephyr_winf` | Rz | `campaign_zephyr_ext.py` | C2/C3 at an unbounded window, seed 20261017 | ZEPHYR_PLAIN, ZEPHYR_CUTOFF (queue), ZEPHYR_PLAIN_DIRECT, ZEPHYR_F_DIRECT × EVICON, 8 |
| `expO_noorphan` | O | `campaign.py` | S at W = 6 without crash orphans | K0, K0S, K0F × EVICON, 8 |
| `expK2_w6` | K2 | `campaign.py` | A's K2 configuration on the current runtime, explicit window 6, seed 20261018 | K2, K3F × EVICON, RES_PERIODIC, 5 |
| `expV_mild` | V | `campaign.py` | half the fault rate (1 window per minute per device), new seed 20261026 | K0, K0S, K0F × EVICON, 8 |
| `expRT_queue` | RTq | `campaign_ext077.py` | RELEASE_ON_TIMEOUT, queue channel, schedules of `expW_queue_w6` | K0, K0F × RELEASE_ON_TIMEOUT, 8 |
| `expRT_direct` | RTd | `campaign_ext077.py` | RELEASE_ON_TIMEOUT, direct channel (`-offline-mode drop`) | K0 × RELEASE_ON_TIMEOUT, 8 |
| `expRR_queue` | RRq | `campaign_ext077.py` | RELEASE_ON_READ against EVICON in the same run, queue channel | K2 × EVICON, RELEASE_ON_READ, 5 |
| `expRR_direct` | RRd | `campaign_ext077.py` | the same, direct channel | K2 × EVICON, RELEASE_ON_READ, 5 |
| `expL2_sleepy` | L2 | `campaign_ext077.py` | experiment L with the validity cut of resends (`resend_until` 30 s), seed of L | K2, K3T × EVICON, 8 |
| `expQ_zephyr` | Q | `campaign_zephyr_ext077.py` | the delivery binding of MQTT commands, every offline window starting with a cut, seed of C2 | ZEPHYR_PLAIN_QOS1, ZEPHYR_PLAIN_QOS0, ZEPHYR_CUTOFF_QOS1 × EVICON, 6 |
| `expWAN_s6` | WAN | `campaign.py` | S at W = 6 over a WAN (`-wan-delay 100ms -wan-jitter 25ms`), seed of S | K0, K0S, K0F × EVICON, 8 |
| `expC16_minio` | C16 | `campaign_real.py` | C at W = 16, seed of C | MINIO_PLAIN, MINIO_COND × EVICON, 8 |
| `expTB_thingsboard` | TB | `campaign_tb.py` | ThingsBoard persistent RPC (`thingsboard-rpc-r0` measured in the run), seed of C | TB_RPC × EVICON, M1, NAIVE, 6 |
| `rq4_scale_d{8,16,32}` | Scale | `campaign.py` (`evicon_review.py scale`) | the RQ4 load with 8, 16, 32 domains | K2, K0F × M0, EVICON, 2 |

- `campaign_ext077.py` imports the frozen `campaign.py` unchanged, adds the two baselines to its policy list with
  their authority arguments (`-policy <name> -release-after 10s -resolve-sweep 1s`) and calls its `main`; the driver
  uses it for any config that names one of them. `RELEASE_ON_TIMEOUT`: a command unresolved 10 s after its creation
  is committed STOPPED and its intent re-issued under a new identity. `RELEASE_ON_READ`: after 10 s the owner's
  receipt is read; APPLIED or STOPPED is recorded, NOT_FOUND or an unreachable owner is committed STOPPED and
  re-issued, UNCERTAIN is read again. `safety_total.py` counts their cells as unsafe by design (079: apart from the
  negative controls, whose every cell must detect a violation).
- Run O passes `-crash-point-override AFTER_RECEIPT_BEFORE_REPLY` to `evicon-device` (`faults.device_args`; flag in
  `cmd/evicon-device/main.go`, test `TestEffectiveCrashPoint`): the owner still crashes in every crash window, but
  only after the receipt is durable, so no claim is left without a receipt. Run Rz needs `measure zephyr-plain zephyr`
  in the same review run on the same firmware binary; Rm uses the 070 MinIO contracts, as C did.
- `policy_compare.py`: comparisons across policies and runs (`pairs_report.py` compares contracts under one policy),
  from the plan's ```policy-compare``` block; an episode counts only when both cells are VALID and the
  `schedule_ep<e>.json` files are byte-identical; mean per-episode difference b − a with a 95 % Student-t interval,
  per-arm means and safety totals; no test. `report` runs it.
- `latency_control.py` with `cmd/evicon-noop` (`evicon_review.py latency-control`, about 25 min, exploratory): replays
  `rq4_load_r{1,5,20}` from VM-C with the generator of `campaign.py` against `evicon-noop` on VM-A, which admits every
  command at once; p50/p95/p99 of `gen_ms` (generator), `net_ms` (network, chrony-synchronised clocks), `client_ms`,
  `admit_ms`, `noop_ms`, next to the RQ4 rows (`--overhead`).
- `sim_review077.py`: the plan's simulator prediction (SIMULATION_ONLY, 200 episodes;
  `sim_predictions/review077_{summary,power}.csv`, `SIM_REVIEW077.md`); no model of RELEASE_ON_READ, of the
  crash-point override or of the latency control. `sim_sensitivity077.py` (exploratory): one factor at a time around
  the twin (fault rates × 0.25–2, offline share 0–40 %, mean outage 5–120 s, crash rate 0–0.02 per request), W = 6
  and unbounded, queue and direct, K0, K0+F, K0+Q_S. `power_review077_testbed.py`: power from the per-episode spread
  of the closest earlier testbed runs (the table of the plan's section 5).
- `late_attribution.py`: the late effects of every emulated-owner run split by the dispatch attempt that took
  effect (the device records it from release 077 on: `<device>.effects.jsonl`), and resends by when they were sent
  (within or after the intent's validity); runs before 077: the dispatches after the validity (an upper bound).
  `report` runs it (`report/late/LATE_ATTRIBUTION.md`).
- `sim_theta077.py` (exploratory): the action costs of the ski-rental rule (fence, stop, holding) one at a time,
  and the immediate rule, W = 6 and unbounded, queue (`sim_predictions/theta077_summary.csv`, `THETA077.md`).
- Tests: `test_release077.py` (B1–B11); Go runtime tests F44–F48.

## Release 078: testbed set-up and the archive of earlier runs

- `deploy/setup/testbed_init.sh` (`detect`, `scan`, `write`, `keys`, `check`) writes `testbed.json` from the VMs'
  current addresses and installs VM-C's SSH key; `deploy/setup/testbed_env.py` gives its values to the setup scripts.
  The driver and the setup scripts require the file on the testbed and check it (IPv4, distinct, VM-C's address is
  local); the shipped configs' addresses are replaced in one pass, whole addresses only.
- `scripts/import_results.py` (`evicon_review.py import-results <bundles> --archive DIR [--lean]`): result bundles of
  earlier releases -> `DIR/runs/review-*` (checksums checked, incremental bundles merged, a later bundle's file never
  replaced by an earlier one, missing schedule files regenerated with `campaign.schedule` and archived ones compared
  byte for byte), `DIR/ARCHIVE.md` with the reference runs of `PREREG_review_077.md`; `report --archive DIR` reads
  the runs the newest report in the archive read. `scripts/make_data_archive.py pack` packs archived runs under the release directory of their bundles.
- Tests: `test_release078.py` (C1-C5).

## Release 079: the report's safety total and the archive's completeness

- `safety_total.py`: four kinds of cell. Negative controls (NEG_*, a blind retry on an owner without dedup in C and C2):
  every cell must detect a violation. Unsafe by design (`planned_unsafe`: the practice baselines, NAIVE on ThingsBoard's
  RPC, QoS 1 on the plain firmware under the offline cut): a violation allowed, not required in every cell; the plans'
  detection rules (RT-1 … RR-2, TB-1, Q-1) decide. Blind retry on owners with dedup. Guarded: every other cell.
  `evicon_review.py` takes the planned groups of a real-owner campaign from `safety_total.planned_unsafe` (one rule).
- `import_results.py`, `report --archive`: the campaign runs the newest report in the archive names must all be there.
- Tests: `test_release079.py` (D1-D3).

## Release 080: E-SIM and the remaining checks of the 077 plan

- `esim077.py`: estimand E-SIM, the runs of `PREREG_review_077.md` against `sim_predictions/review077_summary.csv`
  (levels, effects, release-on-timeout safety); reuses `predict_check.check` with the plan's run-to-scenario map.
- `claims_check.py`: L2-1 (from `late/late_attribution.json`), X-Q0, X-MCU, X-AUTH in Part 1.
- Tests: `test_release080.py` (E1-E3).

## Release 081: Theorem 3 without the practice baselines; K-1, K-2 by the plan's rule

- `t3_attribution.py`, `claims_check.py` (claim T3), `paper_tables.py` (T3 macros): the cells of RELEASE_ON_TIMEOUT and
  RELEASE_ON_READ (unsafe by design, the same set as `safety_total.BASELINES`) are listed apart and left out of the
  totals and of the prediction, with their own totals (`baseline_total`).
- `paper_tables.py`: K-1, K-2 of `PREREG_review_077.md` not both MET -> the K2 rows of `tab_expA.tex` and the K2 points
  of `fig_expA_effects.pdf` come from the plan-077 run (the plan's rule: the replication replaces experiment A's K2
  numbers).
- Tests: `test_release081.py` (F1-F3).

## Release 083: the second set of review experiments (`prereg/PREREG_extra_083.md`)

Seven questions of the second internal review, answered on the five-VM testbed (T1-T7), three analyses of the raw
traces (A1-A3) and three fixes of the paper tables. 30 campaign configs, 402 cells, seven sessions ordered by
priority (T4 and T1; T2 and T3; T5; T6; T7); a campaign not complete at the data freeze is reported as NOT RUN.
54 rules: 29 paired-difference hypotheses, 20 detection, absence and search-failure rules, 5 safety rules. By the plan's
0.80 rule a paired-difference rule is a hypothesis only if its power under the testbed's own spread at the predicted
mean is at least 0.80 (`power_extra083_testbed.py`); the others (T1-1, T6-3, the 0.5/min effects and the 5-15 s direct
effect of T7) are estimands.

| Item | Runs (configs) | What is new |
|---|---|---|
| T1 | Wk (`expM1W_k2`), TBw (`expTB_m1w`) | policy M1W (runtime test F49; simulator QUERY_ONLY_W) |
| T2 | RAq, RAd, RAo (`expRRA_queue`, `_direct`, `_noorphan`) | policy RELEASE_ON_READ_ANSWERED (F50); `evicon-device -shadow-reads` |
| T3 | RTsq, RTsd (`expRTs_queue`, `_direct`) | release-time variants of RELEASE_ON_TIMEOUT (runner mapping) |
| T4 | Z6, Zi (`expT4_zephyr_w6`, `_winf`) | the plain firmware at QoS 0 (`binding: auto`, the 077 runner) |
| T5 | Gs, Gm, Th (`expG_s_winf`, `expG_minio_winf`, `expTheta_w6`) | `-certificate greedy` (property tests, F51; simulator `certificate`), EVICON_FC100 |
| T6 | Z3w, Z16w, Z32w; TBw3, TBw16, TBwinf | the window on the firmware and on ThingsBoard |
| T7 | T7q-<level>, T7d-<level> (`expT7_q6_*`, `expT7_dinf_*`) | fault rate 0.5 and 4/min, offline 5-15 and 40-120 s, two seeds (frozen `campaign.py`) |

- **Analysis**: `pairs_ext083.py --plan prereg/PREREG_extra_083.md --run KEY=DIR ... --out DIR` runs the frozen
  `pairs_report.py` on the ```pairs-spec``` block and decides the ```pairs-ext``` block (policy contrasts, cross-run
  contrasts paired by schedule, differences of contrasts, safety groups, trace counts `search_failures`,
  `releases_without_evidence`); `policy_compare.py` runs the ```policy-compare``` block; `esim083.py` is E-SIM. The
  driver's `report` runs all three on the earliest complete run of each campaign among its sources.
- **Prediction and power**: `sim_extra083.py` (`sim_predictions/extra083_summary.csv`, `extra083_effects.csv`,
  `extra083_power.csv`, `SIM_EXTRA083.md`; the T7 levels as the testbed's schedules realize them),
  `power_extra083_testbed.py` (power under the testbed's own per-episode spread).
- **A1** `safety_total.py`: distinct fault schedules behind the guarded cells with faults, bound per schedule
  1 - 0.05^(1/N_s). **A2** `overhead.py`: admissions after the deadline (`admitted_after_deadline`, share).
  **A3** `t3_attribution.py`: window re-openings without committed evidence, with trace context and explanation.
- **paper_tables.py**: one rounding (`exact_contrast` from `cells.csv`; unrounded means in `expC2_summary.py`,
  `overhead.py`), the K2 replication's label in `tab_fence_cost.tex`, the firmware counted once in the contract map,
  `tab_extra083_*.tex`, `tab_admit_late.tex`.
- Tests: `test_release083.py` (H1-H12).

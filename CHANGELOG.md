# Changelog

## 083 — 2026-10-31

The second set of review experiments (T1-T7, plan `evaluation/prereg/PREREG_extra_083.md`, frozen before its first
cell; 30 campaign configs, 402 cells in seven sessions ordered by priority), three analyses of the raw traces (A1-A3)
and three fixes of the paper-table generator. Every file an earlier plan freezes is byte-identical
(`PREREG_review_077.md`: SHA `db67640d...`); every policy and default of earlier releases behaves as before (the
simulator's output of every earlier scenario is byte-identical to release 082's).

- **T1, QueryOnly with the window-full trigger** (`evicon-authority -policy M1W`; `internal/runtime/query_only_window.go`):
  M1 plus EVICON's trigger on a candidate deferred by a full window (it queries the oldest ambiguous commands, within
  the probe budget; never stops or fences). Runtime test F49; simulator policies QUERY_ONLY and QUERY_ONLY_W. Runs Wk
  (K2, queue, W = 6, the window experiment's schedules) and TBw (ThingsBoard, TB's schedules), EVICON, M1, M1W.
- **T2, release on an answered read** (`-policy RELEASE_ON_READ_ANSWERED -release-settle 7s`; unsafe by design): a
  command is released only when the owner, reachable, answers NOT_FOUND to a read made 10 s after its creation and at
  least the settle time after its last dispatch attempt ended; its resends stop at release-after minus settle. The
  emulated owner's `-shadow-reads` (off by default) answers receipt reads while it is offline, as a device shadow
  does. Runtime test F50. Runs RAq (queue, shadow reads), RAd (direct), RAo (direct, no crash orphans), RR's schedules.
- **T3, the release time of TimeoutRelease**: the policy names `RELEASE_ON_TIMEOUT_30S`, `_60S`, `_120S` run
  RELEASE_ON_TIMEOUT with that release time (runner mapping only). Runs RTsq, RTsd on RT's schedules.
- **T4, the plain firmware under the at-most-once binding**: the plain firmware's EVICON cells of C2, C3 and Rz again
  with the 077 gateway's `binding: auto` (QoS 0 without D_h), runs Z6 (W = 6) and Zi (W unbounded), on the 077 runner;
  the main effects recomputed against them with the original margins.
- **T5, the greedy certificate** (`-certificate greedy`, default `enumerate`; `internal/probe/greedy_certificate.go`):
  for scalar ADD domains with an upper or sum bound, the largest unresolved commands until the deficit is covered
  (bisection with the guard), then every redundant member dropped; never gives up; other domains fall back to the
  enumeration. Property tests against an unbounded search (minimum size, minimal by inclusion, safe admissions, 108
  unresolved doses in 2 ms) and runtime test F51; simulator option `certificate`. Runs Gs (K0S, K0F at W unbounded), Gm (MinIO at
  W unbounded, greedy against Rm's enumeration) and Th (a fence cost of 100, theta 20 times the default). The
  simulator sweeps gamma 1-4 and greedy.
- **T6, the window on the firmware and ThingsBoard**: runs Z3w, Z16w, Z32w (plain at QoS 0 and cutoff) and TBw3, TBw16,
  TBwinf (EVICON, M1W).
- **T7, the fault regime and new seeds**: the fault rate 0.5 and 4 per minute, offline 5-15 s and 40-120 s, seeds
  20261101 and 20261102; K0, K0S, K0F on the queue channel at W = 6 and K0, K0F on the direct channel at W unbounded;
  4 episodes each (frozen `campaign.py`).
- **The plan's 0.80 rule** (before freezing): a paired-difference rule is a confirmatory hypothesis only if its power
  under the testbed's own spread at the predicted mean is at least 0.80 (`power_extra083_testbed.py` prints the rule's
  table); otherwise it is an estimand, reported with its interval and the prediction. T1-1, T6-3, T7-rate05-qF/-qS/-dF
  and T7-off5-dF became estimands; T4-6 (U-4's equivalence, which assumed a lossless queue) tests the cutoff's gain at
  W unbounded under QoS 0 (`at_least 5`, power 1.00). 54 rules: 29 paired-difference hypotheses, 20 detection,
  absence and search-failure rules, 5 safety rules.
- **Runners** (new modules; the frozen runners are imported unchanged): `evaluation/ext083.py` (the 083 policies'
  authority arguments, installed on `campaign_ext077.py`), `campaign_ext083.py`, `campaign_real_ext083.py`,
  `campaign_tb_ext083.py`; the driver picks them by the config's policies.
- **Analysis**: `evaluation/pairs_ext083.py` runs the frozen `pairs_report.py` on the plan's ```pairs-spec``` block and
  decides its ```pairs-ext``` block: contrasts between policies, contrasts across runs paired by schedule, differences of
  contrasts (the window's direction), safety rules over groups of runs (a run not run is listed; no partial results),
  and the trace counts `search_failures` and `releases_without_evidence`; `evaluation/esim083.py` (E-SIM). The report
  runs both, `policy_compare.py` on the plan's ```policy-compare``` block, and claims_check lists every verdict.
  Simulator prediction `evaluation/sim_extra083.py` (`sim_predictions/extra083_*.csv`, `SIM_EXTRA083.md`); power under
  the testbed's spread `evaluation/power_extra083_testbed.py`.
- **A1** (`safety_total.py`): the distinct fault schedules behind the guarded cells with faults and the exact bound per
  schedule, 1 - 0.05^(1/N_s) (`SAFETY.md`, `safety.json`, `\evSafetySchedules`, `\evSafetyBoundSchedulePct`). **A2**
  (`overhead.py`): the admissions after the request's deadline per campaign and policy (`tab_admit_late.tex`,
  `\evAdmitLateSharePct`). **A3** (`t3_attribution.py`): every window re-opening without committed evidence in a guarded
  cell, with the trace events between its DEFER and its admission and a factual explanation.
- **The practice baselines have one definition** (`safety_total.BASELINES`, read by `t3_attribution.py`,
  `claims_check.py` and the driver), now with RELEASE_ON_READ_ANSWERED and the release-time variants.
- **paper_tables.py**: one rounding (the plans' means are recomputed unrounded from `cells.csv`, `expC2_summary.py` and
  `overhead.py` keep unrounded values: the {F} queue level 79.147 prints 79.1, not 79.2); `tab_fence_cost.tex` labels the
  second K2 run "K2 config, replication"; the firmware on native_sim and on the emulated Cortex-M3 is one system
  (`\evSystems` 10, `\evThirdPartySystems` 9); tables and macros of the 083 plan (`tab_extra083_*.tex`).
- Version 083 (`VERSION`, `buildinfo.Release`, `CITATION.cff`, the VM set-up banners); the operator's
  run guide of 083 replaces that of 081. Tests: `evaluation/test_release083.py` (H1-H12);
  `test_release077` B2 and `test_release082` G1 accept the later releases' baselines and plans.

## 082 — 2026-10-30

The artifact's documents for the final report. No change to the runtime, the runners, the gateways, the firmware,
the analyses, the plans or any file a plan freezes; the final report (run review-20261030T182927) was produced by
release 081, whose analysis this release keeps unchanged.

- `evaluation/prereg/RESULTS_review_077.md`: the outcome of `PREREG_review_077.md`, copied from the final report by
  `evaluation/results_record.py` (bundle `EVICON_081_result.zip`, SHA-256 `e8e9ad5d...`), with an interpretation note
  written after the run (`evaluation/prereg/notes/PREREG_review_077.md`: K-2, Theorem 3 on the traces, E-SIM).
- `ARTIFACT.md`: release 082; the releases that collected the data (066-078) and the release whose analysis produced
  the final report (081); the 077 plan's outcome; 80 EviCon-Probe tests.
- `formal/TLC_RESULTS.txt`, `formal/tlc_logs/`: TLC 1.7.4 on the released model (identical in 081 and 082): the five safe
  configurations end without error (`safe`: 2,891,569 distinct states, depth 30), the three negative controls violate
  their invariant within 7 steps.
- `CITATION.cff`: version 082. Tests: `evaluation/test_release082.py` (G1).

## 081 — 2026-10-30

Theorem 3's check without the practice baselines, and the 077 plan's rule for K-1, K-2 in the paper tables. No change
to the runtime, the runners, the gateways, the firmware, the plans or any file a plan freezes; the runs of
`PREREG_review_077.md` are made with release 078.

- **Theorem 3 without the practice baselines** (`evaluation/t3_attribution.py`, `claims_check.py`, `paper_tables.py`).
  The cells of RELEASE_ON_TIMEOUT and RELEASE_ON_READ release a request without evidence of non-occurrence by design
  (the plan's detection rules RT-1 to RT-3, RR-1, RR-2), so they are outside the theorem's premise, as `safety_total.py`
  already treats them (079). They were counted as counterexamples: `T3.md` said "Prediction FAILS" for the five RT and
  RR runs, the claim T3 read NOT SUPPORTED and `\evTThreeCounterexamples` was 168. They are now listed apart, like the
  negative controls, with their own totals (`baseline_total`; `\evTThreeBaselineReopen`, `\evTThreeBaselineRuns`); a
  guarded counterexample is still reported and still fails the claim.
- **K-1, K-2 by the plan's rule** (`paper_tables.py`): with K-1, K-2 not both MET, the plan has the paper report the
  replication instead of experiment A's K2 numbers, so the K2 rows of `tab_expA.tex` and the K2 points of
  `fig_expA_effects.pdf` are the plan-077 run's (K-1, K-2), and the 072 K2 replication's macros are left out.
- `CITATION.cff`: version 081.
- `conformance/tests/test_mqtt_owner_gateway_ext077.py` (test only): at QoS 1 the cut test waits up to 12 s for the
  broker's re-sent copy instead of a fixed 4 s (a loaded host reconnects later; it missed once while a report ran on
  the same machine). The gateway and every frozen file are unchanged.
- Tests: `evaluation/test_release081.py` (F1-F3).

## 080 — 2026-10-30

The last estimand and checks of `PREREG_review_077.md` in the report. No change to the runtime, the runners, the
gateways, the firmware, the plans or any file a plan freezes; the runs of the plan are made with release 078.

- **E-SIM** (`evaluation/esim077.py`): the runs Rm, Rz, O, K2, V, WAN, RTq and RTd against the simulator prediction of
  the plan's section 1(g) (`sim_predictions/review077_summary.csv`): levels, effects with `predict_check.py`'s agreement
  rules, and the release-on-timeout runs' safety violations per episode. An estimand: the scoring of the simulator claim
  is unchanged. `report` runs it on the earliest complete run of each config (`report/esim077/ESIM077.md`).
- **The plan's remaining verdicts in the claims check** (`claims_check.py`): L2-1 by the plan's rule on the output of the
  frozen `late_attribution.py` (no late effect from a resend sent after the intent's validity in any K2/EVICON cell of
  run L2), X-Q0 (qos-dup), X-MCU (the emulated MCU's measured contract, now copied into `report/mcu/measure.json`) and
  X-AUTH (cases A1-A13).
- Tests: `evaluation/test_release080.py` (E1-E3).

## 079 — 2026-10-29

The report's safety total and the archive's completeness. No change to the runtime, the runners, the gateways, the
firmware, the plans or any file a plan freezes; the runs of `PREREG_review_077.md` are made with release 078 and
reported with this release.

- **Cells unsafe by design apart in the safety total** (`evaluation/safety_total.py`). A cell planned to be able to
  violate safety without a violation being required in every cell (the practice baselines RELEASE_ON_TIMEOUT and
  RELEASE_ON_READ, a blind retry on ThingsBoard's RPC, the plain firmware with QoS 1 under the offline cut) is counted
  in its own group, next to the guarded cells and to the negative controls (NEG_*, a blind retry on an owner without
  dedup), whose every cell must detect a violation. The groups are those the driver plans for each real-owner
  campaign: `evicon_review.py` takes them from `safety_total.planned_unsafe` (test D1: the same groups as before on
  every shipped config). `SAFETY.md` lists them per campaign; `paper_tables.py` adds `\evUnsafeByDesign`.
- **Campaign runs checked in the archive** (`scripts/import_results.py`, `report --archive`): every campaign run the
  newest report in the archive names must be there, not only its review run (a review run is present when only some
  of its incremental bundles were imported).
- **`deploy/setup/vm-c.sh`** installs the mosquitto binary for the gateway tests of release 077 and disables the broker
  service it installed (an installed mosquitto is left as it is).
- Tests: `evaluation/test_release079.py` (D1-D3); `test_release077` B4 expects the baselines in the new group.

## 078 — 2026-10-28

Testbed set-up from the VMs' current addresses, and the runs of earlier releases read from their result bundles.
No change to the Go code (except `buildinfo.Release`), the runners, the analyses or any file a plan freezes; the
077 plan states the release (section 7) and was frozen again before its first run; every other plan still verifies.

- **`deploy/setup/testbed_init.sh`.** `detect` (VM-C's address, interface, network), `scan` (hosts with SSH on that
  network, with hostname and banner), `write --vm-a --vm-b --vm-d --vm-e [--vm-c] [--ssh-user]` (`testbed.json`,
  VM-C's address detected; the previous file kept as `testbed.json.bak-<time>`), `keys [--login USER]
  [--reset-host-keys]` (VM-C's ed25519 key in root's `authorized_keys` on every VM, directly or through a sudo
  user; host keys accepted on first use, a changed host key reported), `check` (SSH, hostname, OS, CPUs, memory,
  disk, clock offset to VM-C, NTP, HTTPS to github.com, with the resources each role needs).
- **`testbed.json` required and checked.** On the testbed the driver, `setup_all.sh`, `build_iot.sh` and the
  per-VM scripts take the addresses only from `testbed.json` (`deploy/setup/testbed_env.py`); they stop when it is
  missing, when an address is not IPv4, when two roles share one, or when `vm_c` is not an address of the machine.
  The shipped configs' addresses are replaced in one pass and as whole addresses, so a new address may equal
  another role's shipped one. `build_zephyr.sh` takes the broker address only from `build_iot.sh`.
- **`evicon_review.py import-results <bundles or directories> --archive DIR [--lean]`** (`scripts/import_results.py`):
  each bundle checked against its `.sha256` file when present (a mismatch skips it), every member checked before
  anything is written (no absolute path, `..` or link), the incremental bundles of one review run merged oldest
  first, a file a later bundle wrote never replaced by an earlier bundle (`DIR/provenance.json`), files already
  present skipped by size and CRC; schedule files a bundle left out regenerated from the run's `config.json` with
  `campaign.schedule` (listed in `schedule_regenerated.json`), archived ones compared byte for byte; `DIR/ARCHIVE.md`
  and `archive.json`: bundles, review runs with their release, campaign runs (cells, VALID, complete, traces,
  schedules) and the reference runs of `PREREG_review_077.md`. `--lean` keeps the authority traces of the reference
  runs and the RQ4 load runs only; without it the free space is checked first.
- **`report --archive DIR`** reads the runs the newest report in the archive read and that report's own run (as
  `--sources-of` reads a release directory), before the runs of `--from`; a run it names that the archive lacks
  stops the report with the bundle to import (`--archive-missing-ok`: go on, listed as WARN). `import-results`
  lists these runs too, and warns about zips in a directory whose name is not a bundle's.
  `scripts/make_data_archive.py pack` packs an archived run under the release directory of its bundles.
- Tests: `evaluation/test_release078.py` (C1-C5).

## 077 — 2026-10-25

The review experiments: new preregistered runs for every question the internal review of the paper draft could
not answer from the existing data (`evaluation/prereg/PREREG_review_077.md`: seventeen campaign configs, three
checks, five testbed sessions). Every file frozen by the 067-075 plans is byte-identical to 076, and the C3 plan
still verifies.

- **Real owners at an unbounded window and at W = 16.** `expR_minio_winf` (MinIO PUT with and without
  `If-None-Match`, as C) and `expR_zephyr_winf` (the firmware plain and cutoff on the queue channel, plain and {F} on
  the direct channel, as C2 and C3) at `max_outstanding` 1000, with the seeds and therefore the schedules of C, C2
  and C3; `expC16_minio` at W = 16.
- **No crash orphans.** `expO_noorphan`: experiment S at W = 6 with every injected owner crash moved after the
  durable receipt (`evicon-device -crash-point-override AFTER_RECEIPT_BEFORE_REPLY`, test `TestEffectiveCrashPoint`).
- **K2 on the release-077 runtime.** `expK2_w6`: experiment A's K2 configuration (seed 20261018) at an explicit
  window of 6.
- **A milder regime and a WAN.** `expV_mild`: half the fault rate, new seed. `expWAN_s6`: experiment S at W = 6 with
  every request and reply of the device API delayed 100 ms +/- 25 ms one way (`evicon-device -wan-delay -wan-jitter
  -wan-seed`, test `TestWanHandler`).
- **The retry rule.** `evicon-authority -resend-until <d>`: the T4 guard re-sends a command under its own identity
  only until d after its creation (Eq. 4 with the intent's validity; runtime test F47); the configs whose owner
  deduplicates use validity - deadline = 30 s, which is never later than the intent's validity. The dispatcher sends
  its attempt number (`X-EviCon-Attempt`, trace field `attempt`) and `evicon-device` records which attempt took
  effect (`<device>.effects.jsonl`, test `TestEffectAttemptLogged`); `evaluation/late_attribution.py` splits the late
  effects into first sends and resends sent within or after the validity (runs before 077: the dispatches after the
  validity, an upper bound); `report` runs it. `expL2_sleepy`: experiment L with the cut.
- **Property test of the admission path** (F48): on the journal, the belief and the window check, APPLIED evidence
  never changes the guard's verdict, STOPPED never blocks an admitted request, and every terminal answer frees one
  window slot.
- **The delivery binding of MQTT commands.** `conformance/reference/qos_dup_check.py` and `evicon_review.py qos-dup`:
  QoS 1 against QoS 0 through a link that loses the broker's acknowledgement and resets.
  `conformance/reference/mqtt_owner_gateway_ext077.py` (on the 075 gateway): commands at QoS 0 for a contract without
  D_h and at QoS 1 with it (binding `auto`, or `qos1` / `qos0` per contract), the offline cut (the link fails right
  after a command reached the broker), and the crash fault armed 100 ms after the arming request is answered.
  `evaluation/campaign_zephyr_ext077.py` (on the C3 runner) and `expQ_zephyr`; `expR_zephyr_winf` runs on it with the
  C2/C3 binding.
- **A device-management platform.** `conformance/reference/tb_owner_gateway.py` (ThingsBoard CE persistent two-way
  RPC without platform retries; Q_A from the RPC record), `evaluation/campaign_tb.py` (ThingsBoard devices per cell,
  the reference MQTT device per domain, device tokens on stdin), `expTB_thingsboard` (EVICON, M1, NAIVE), the probe
  config `thingsboard-rpc-r0` (the gateway's call shape) in `measure thingsboard`; the driver's `campaign` stage for
  `real_owner.system` thingsboard.
- **Two practice baselines, unsafe by design** (`internal/runtime/release_on_timeout.go`, tests F44-F46):
  `RELEASE_ON_TIMEOUT` and `RELEASE_ON_READ` (state-read reconciliation), in their own campaigns (`expRT_queue`,
  `expRT_direct`, `expRR_queue`, `expRR_direct`) on `evaluation/campaign_ext077.py`; `evaluation/policy_compare.py`
  compares them with EviCon on byte-identical schedules.
- **Scale.** `evicon_review.py scale`: the RQ4 load with 8, 16 and 32 domains (`rq4_scale_d*.json`) with CPU/RSS
  sampling. **Latency control.** `evicon_review.py latency-control` against `cmd/evicon-noop`.
- **Firmware beyond native_sim.** `firmware/zephyr-mcu`: the firmware's sources, unchanged, for `mps2/an385` (an Arm
  Cortex-M3 under QEMU, Zephyr's own TCP/IP stack and LAN9118 driver) and for `nrf52840dk/nrf52840` with OpenThread,
  TCP and MQTT (footprint); `measure zephyr-mcu` runs the zephyr-mqtt probes against the emulated MCU.
  `firmware/zephyr-auth`: HMAC-SHA256 (`evc_auth.c`, FIPS/RFC 4231 vectors in `tests/test_auth.c`) on commands, stops,
  fences and every reply, and a bound on a fence's cutoff; `measure zephyr-auth` with
  `conformance/reference/zephyr_auth_check.py` (forged, altered, replayed and misdirected messages). Built by
  `deploy/setup/build_zephyr.sh` after the native build (status `MCU_BUILD_OK`; a random device key per build, mode
  0600, never in a command line or the results); `vm-e.sh` installs `qemu-system-arm`.
- **Simulator.** `evaluation/sim_review077.py` (prediction and power, with the WAN scenario),
  `evaluation/sim_sensitivity077.py` (fault regime), `evaluation/sim_theta077.py` (action costs),
  `evaluation/power_review077_testbed.py` (power from the testbed's own spread).
- Driver: the warning about other programs sharing the measured cores lists every non-EviCon process above 10 % CPU;
  the bundle's environment snapshot lists the busiest processes; firewall ranges for 32 domains; `report` adds the
  late-effect attribution, qos-dup, the authenticated firmware, the emulated MCU (with the networked nRF52840
  footprint) and scale; a campaign on ThingsBoard without a VALID cell fails its stage.
- Documentation: English READMEs for `conformance/`, `evaluation/` and the firmware variants; `README.md` and
  `ARTIFACT.md` for 077; documents of earlier releases removed.
- Tests: `evaluation/test_release077.py` (B1-B11), `test_prereg.Release077`, conformance tests of the 077 gateway
  (against a local Mosquitto) and of the ThingsBoard gateway (against a model of its REST API), Go tests F47, F48,
  `TestEffectAttemptLogged`, `TestWanHandler`.

## 076 — 2026-10-25

The final artifact release: analysis and documents only. No Go change (only `buildinfo.Release`), no firmware,
simulator, runner, gateway or probe change; every file frozen by the 067-075 plans is byte-identical to 075.

- `evaluation/claims_check.py`: experiment A's secondary hypotheses keep the verdicts of `confirm_report.py` (H-B
  INCONCLUSIVE with its estimate in commands per episode; SR "K0 MET; K2 not applicable"); release 075 listed both as
  NOT MET. The simulator claim names every effect whose sign the simulator missed.
- `evaluation/expC2_summary.py`: UNCERTAIN stops split by the firmware's reason (`TOMBSTONE_TABLE_FULL`,
  `FUTURE_BLOCKED_PAST_UNKNOWN`), the device-episodes whose tombstone table filled, the number of devices per group.
  `evaluation/paper_tables.py`: macros `\evCthree<Qs><Ch>TombFullStops`, `\evCthree<Qs><Ch>TombFullDevEp`.
- `evaluation/results_record.py`: writes its own release number; `evaluation/prereg/RESULTS_*.md` for W, S, L, the
  negative controls, B2, C, C2 and C3 regenerated from the N7 report (`EVICON_075_result_20261024T2331.zip`), with the
  C3 interpretation note (`evaluation/prereg/notes/PREREG_expC3_075.md`).
- `ARTIFACT.md`, `README.md`: the C3 outcomes.
- Tests: S2 (experiment A's verdicts, the simulator's sign misses), S7 (stop reasons).

## 075 — 2026-10-24

Experiment C3: the evidence of the Zephyr firmware decomposed on the real IoT stack of C2, on two owner channels,
preregistered; what the firmware certifies across an owner reboot; safety summed over every testbed campaign; the
paper's claims checked against the report; the outcome of every plan recorded next to it; a data archive with an
offline reproduction of the report.

No Go change (only `buildinfo.Release`); no firmware change. Every file frozen by the 067-074 plans is byte-identical
to 074, and the C2 plan still verifies.

Experiment C3 (`evaluation/prereg/PREREG_expC3_075.md`, `evaluation/campaigns/expC3_ablation.json`)
- EVICON on the plain mode's measured contract and on restrictions of the cutoff and stop modes' measured contracts to
  {Q_A}, {F} and {Q_S}, on the queue channel of C2 and on a direct channel (an offline window loses every message: a
  lost command never takes effect); 7 contracts x 8 episodes = 56 cells; the regime and seed of C2, W and S (episodes
  0-7 replay their schedules byte for byte). Hypotheses C3-1 to C3-6 (Holm) and C3-S (safety in every cell).
- `evaluation/campaign_zephyr_ext.py`: the frozen C2 runner with restricted contracts (`restrict_to`, checked against
  the parent measured contract: MEASURED, a non-empty proper subset, the parent's SHA-256 in
  `provenance.restricted_from`, provenance and fault model copied, no D_h horizon without D_h) and the owner channel
  (`channel`: queue or direct). The frozen runner's own checks run first, on every parent.
- `conformance/reference/mqtt_owner_gateway_ext.py`: the frozen C2 gateway with a restricted contract (advertised;
  every request it does not cover is refused with 409) and `--offline-mode drop` (the direct channel). Tests X1-X4.
- Driver: the C3 runner for configs with `restrict_to` or `channel`; the restricted contracts are written from the
  contracts measured in the same review run (`measured-<mode>.json`, `<cell>.json` in the campaign's contract directory).
- Simulator prediction (`evaluation/sim_expC3.py`, `sim_predictions/expC3_075_*`, `SIM_EXPC3_075.md`), with an owner
  crash scenario for contrast; power from the testbed's own spread (`evaluation/power_expC3_testbed.py`,
  `POWER_EXPC3_TESTBED_075.md`); predicted contracts `experiments/owners/zephyr/pred-zephyr-{qa,f,qs}-075.json`.

Owner reboot (`measure zephyr-reboot`, `conformance/reference/zephyr_reboot_check.py`)
- The firmware is killed and started again on the same flash file between two scripted phases; the check records what
  it answers for every identity class (applied before the reboot, sealed by the cutoff, tombstoned, applied after the
  cutoff, never sent inside the claimed block, replays, a new fence, new identities inside and above the block) and
  counts effects per identity from the plant. `report` copies it to `report/reboot/REBOOT.md`. Not a contract
  measurement: the measured fault model is "owner up".

Analysis
- `evaluation/safety_total.py`: safety counts of every VALID cell of every TESTBED campaign, guarded cells against the
  negative controls and against blind retry on owners with dedup, with the exact one-sided 95 % bound for zero events;
  `report/safety/`, macros `\evSafety*`.
- `evaluation/claims_check.py`: every preregistered hypothesis with its verdict (MET or not), and the cross-cutting
  claims (safety, Theorem 3, contracts, simulator, fidelity, reboot) checked against their files; `report/claims/`.
- `evaluation/results_record.py`: `evaluation/prereg/RESULTS_<plan>.md` for every plan, copied from a report, with the
  plan's deviations and a separate interpretation note (`evaluation/prereg/notes/`). Records written for W, S, L, the
  negative controls, B2, C and C2 from the N6 report; the C2 note records why C2-5 was not met.
- `expC2_summary.py --experiment C3` (counts the direct channel's dropped messages); `paper_tables.py`: `tab_expC3.tex`,
  `\evCthree*`, the safety macros; the contract map's Zephyr rows by mode; `predict_check.py` and `fidelity_check`
  pairs for C3.

Artifact
- `ARTIFACT.md` (claims -> experiments -> files -> commands), `CITATION.cff`, `scripts/make_data_archive.py` (`pack`
  on VM-C, with a credential scan; `reproduce` anywhere: the report regenerated from the deposited runs alone).

Tests: `evaluation/test_release075.py` (S1-S6), `evaluation/test_campaign_zephyr_ext.py` (E-R1 to E-R3),
`conformance/tests/test_mqtt_owner_gateway_ext.py` (X1-X4), `test_prereg` (075: the C3 plan frozen with every file of
the C2 plan, the C2 plan byte-identical, the C3 config C2's except where planned, the schedules of C2, W and S replayed).

## 074 — 2026-10-23

Experiment C2: EviCon on a real IoT stack (the Zephyr EviCon firmware behind Mosquitto), preregistered; fidelity of
the emulated owner against real owners at the same configuration.

No Go change (only `buildinfo.Release`): the runtime, the simulator and every file frozen by the 067-073 plans are
byte-identical to 073.

Firmware (`firmware/zephyr-cutoff`)
- `src/main.c`: native_sim command-line options `-evc_device`, `-evc_ns` (topic prefix), `-evc_broker`,
  `-evc_broker_port`, `-evc_plant`, `-evc_plant_port` and **`-evc_mode=plain|cutoff|stop`** (defaults: the Kconfig
  values of 068-073, mode `cutoff`). `cutoff` is the firmware measured in 070; `stop` sets `EVC_F_STOP` (the
  per-identity stop already in the core: durable tombstones); `plain` executes every delivered command and answers
  APPLIED (no dedup, query, fence or stop; it subscribes to the command and ping topics only). New `ping` topic
  (device, mode, level, cutoff) for readiness. `evc_core.c` and `evc_proto.c` unchanged. Defaults are applied after
  option parsing (the native_sim parser resets option destinations: strings to NULL, unsigned numbers to
  UINT32_MAX); an invalid `-evc_mode` or an out-of-range port stops the firmware.
- The firmware must be rebuilt from 074 (`build_iot.sh start`); `measure` refuses a binary without `-evc_mode`.

Measurement
- `conformance/configs/zephyr-mqtt.json` (cutoff), `zephyr-mqtt-stop.json` (stop, with P9 for Q_S),
  `zephyr-mqtt-plain.json` (plain, declares nothing): commands at QoS 1 (as in 068-070). `measure zephyr-stop` and
  `measure zephyr-plain` are new stacks; every Zephyr stack starts the firmware in its mode and checks it by ping.
- QoS 2 was tried and rejected before freezing: the broker holds a command until the publisher's PUBREL, so a lost reply
  path also holds the command and P2 (Q_A) had no positive control.

Experiment C2
- `conformance/reference/mqtt_owner_gateway.py`: one gateway per device (VM-C) speaking the `evicon-device` owner
  protocol and mapping it to the firmware's MQTT messages: two persistent-session MQTT 3.1.1 connections (data:
  commands; control: query, fence, stop, ping), a durable JSONL record of forwarded identities and terminal receipts,
  barrier = fence plus a receipt for every forwarded identity at or below the cutoff (cached, queried, or UNCERTAIN), so
  the authority never seals an applied identity. Test-only admin: offline (the link to the broker is reset and new
  connections refused; paho re-sends what was published meanwhile: a queue channel) and crash after APPLIED.
- `evaluation/campaign_zephyr.py`: runner on the frozen `campaign.py`/`campaign_real.py` (imported): per cell fresh
  firmware processes (VM-E, own flash files, own topic prefix), gateways (VM-C), fault proxies, authorities (VM-A);
  truth from `evicon-plant` (TCP line protocol on VM-E's loopback); `exclude_cells`; a firmware exit makes the cell INVALID.
- Driver: `campaign expC2_zephyr` brings Mosquitto up on VM-B (EviCon's compose project), copies the firmware to VM-E,
  and runs only on the three contracts measured **in the same review run on the same firmware binary**, with exactly
  the planned capabilities and fault model (`_measured_zephyr_contracts`); the contracts are copied into the run.
- `evaluation/campaigns/expC2_zephyr.json` + `evaluation/prereg/PREREG_expC2_074.md`: 3 modes x {EVICON, M1, NAIVE}
  minus STOP x {M1, NAIVE}, 8 episodes, 56 cells; the regime and **seed of `expW_queue_w6`/`expS_w6`** (episodes 0-7
  replay their schedules byte for byte). C2-1 (NAIVE on plain duplicates), C2-2 (NAIVE on cutoff does not), C2-3/C2-4
  (EVICON plain -> cutoff, clean and confirmed, >= 10 pp), C2-5 (cutoff -> stop >= 0: the emulated testbed's
  stop-over-fence gain against the simulator's none), Holm over C2-3..5; C2-S1..5 safety wherever T4 is on.
- `evaluation/sim_expC2.py`: the simulator prediction and power quoted in the plan (`evaluation/sim_predictions/
  expC2_074_*`, `SIM_EXPC2_074.md`); predicted contracts `experiments/owners/zephyr/pred-zephyr-*-074.json`.
- `evaluation/expC2_summary.py`: one row per mode and policy, plus what the gateways saw (crashes, link resets,
  barriers, stops).

Analysis
- `evaluation/fidelity_check.py`: emulated owner against real owner at the same configuration (Welch, TOST at +-5 and
  +-10 pp; paired when the schedules are byte-identical). On N5: MinIO PUT 22.7 % against emulated K0 (direct, W = 6)
  22.5 %, TOST +-10 pp p = 0.024.
- `evaluation/predict_check.py`: C2 predictions; a three-way direction column (up, down, none within +-2 pp) next to
  the sign.
- `evaluation/expC_summary.py`: "T4: commands withheld" counts commands (the old column counted dispatcher passes).
- `paper_tables.py`: `tab_expC2.tex`, macros `\evCtwo*`.
- `evicon_review.py report --sources-of <release dir>`: re-reads every run the newest report of that release read.

Tests: `conformance/tests/test_mqtt_owner_gateway.py` (Z1-Z9 against a model of `evc_core`; real TCP link; HTTP
handler), `evaluation/test_campaign_zephyr.py` (Z-R1..Z-R3: config rules, cells, the measured-contract gate),
`test_predict` (074), `test_prereg` (C2 plan frozen; 073 plan byte-identical; config = `expW_queue_w6` except where
planned; schedules replay W and S). `firmware/zephyr-cutoff/tests/host`: the glue check compiles `main.c` also with
`CONFIG_ARCH_POSIX` (the native_sim options), against stub headers extended for 074 (`cmdline.h`, `soc.h`, `ARRAY_SIZE`,
`K_MSEC`, `k_panic`, `LOG_ERR`).

## 073 — 2026-10-22

Experiment C: EviCon on a real owner (MinIO), preregistered; simulator predictions against the testbed.

No Go change (only `buildinfo.Release`): the runtime, the simulator and every file frozen by the 067-072 plans are
byte-identical to 072, so the 072 configs run unchanged with 073.

Experiment C
- `conformance/reference/s3_owner_gateway.py`: one stateless S3 owner gateway per device. Speaks the device-owner
  protocol of `evicon-device` and maps it to MinIO: effect = PUT of `<device>/<command id>` (COND: `If-None-Match: *`;
  PLAIN: unconditional), query = HEAD, stop = conditional tombstone PUT, no fence. Advertises the contract that release
  070 MEASURED for its call shape (`experiments/owners/minio/`, byte-identical copies of the 070 `contract.json`).
  Test-only admin: offline (an in-process fault proxy drops the gateway -> MinIO path both ways), crash between the S3 200
  and the reply. Credentials only from its environment. Intent hash = Go's `CanonicalHash` (test vectors from Go).
- `conformance/reference/s3_client.py`: SigV4 (the measured adapter's signer), PUT/HEAD/ListObjectVersions with paging,
  versioned buckets; nothing deletes.
- `evaluation/campaign_real.py`: runner on the frozen `campaign.py` (imported, not edited): gateways on VM-C, a fresh
  versioned bucket per cell, truth from MinIO's object versions listed directly (duplicate effects, bound, first effect
  time on VM-C's clock via VM-B's offset; `raw/truth/` and an evicon-plant-format effect log for `pairs_report.py`),
  pseudo-policies (`NAIVE` = M1 without the T4 retry guard: blind same-identity retry).
- Driver: a config with `real_owner` brings MinIO up on VM-B in EviCon's compose project (as `measure minio`), runs
  `campaign_real.py`, brings MinIO down (also on failure). A safety violation outside the planned negative control
  (NAIVE on PLAIN) fails the campaign.
- `evaluation/campaigns/expC_minio.json` + `evaluation/prereg/PREREG_expC_073.md`: 2 call shapes x {EVICON, M1, NAIVE} x 8
  episodes, window 6, seed 20261025; C1 (NAIVE on PLAIN duplicates on the real system), C2 (NAIVE on COND does not), C3/C4
  (EVICON PLAIN -> COND, clean and confirmed, >= 10 pp, Holm), C-S1..4 (safety wherever T4 is on). Frozen with the runner,
  the gateway, the code it runs and the measured contracts.
- `evaluation/sim_expC.py`: the simulator prediction quoted in the plan (EVICON +45 pp; NAIVE on PLAIN unsafe in 193/200
  episodes). Finding fixed in the plan: with no delay bound (`max_network_delay` -1ns) the finite measured horizon allows no
  same-identity retry (T4), so EviCon on MinIO resolves with Q_A and Q_S (tombstones).
- `evaluation/expC_summary.py`: one row per call shape and policy.

Analysis
- `evaluation/predict_check.py`: every testbed result next to the simulator prediction frozen in its plan
  (`evaluation/sim_predictions/`: the 072 sweep, `sim_expC.py`): levels and effects, sign agreement, prediction inside the
  testbed's 95 % interval, MAE, Spearman. On N1-N2: 10/10 effects with the same sign, Spearman 0.95; the simulator
  over-predicts levels at W = 6 by about 7 pp.
- `paper_tables.py`: `tab_predict.tex`, `tab_levels.tex`, `fig_phase.pdf` (clean fulfilment against the window, simulator
  lines with the testbed means), `tab_expC.tex`; macros `\evPred*`, `\evC*`.
- `t3_attribution.py`: negative-control contracts (`NEG_*`) are listed but left out of every total (072 counted 19
  re-tests from them).
- `report` runs `predict_check.py` and `expC_summary.py`.

Tests: `conformance/tests/test_s3_owner_gateway.py` (G1-G7, fake S3 in `conformance/tests/fake_s3.py`),
`evaluation/test_campaign_real.py` (R1-R4, R4 runs two real local cells against the fake S3), `test_prereg` (C plan frozen,
measured contracts byte-identical to 070, 072 plans byte-identical, the driver's real-owner violation rule).
VERSION 073.

## 072 — 2026-10-21

Window made explicit; experiments W, S, L and negative controls, preregistered; new analyses.

**Behaviour change.** The belief of an ADD-only domain is now built in product form (one configuration),
so the window of unresolved commands is exactly `authority.max_outstanding`. Up to 071 the 256-configuration
enumeration cap bounded it at about 6 in the dosing domain; configs of earlier releases run with 072 at their
`max_outstanding` (16). Reproduce an old run with `max_outstanding` 6 or with release <= 071. The simulator also
uses the product form, so the 067 plan's twin gives different numbers with 072 binaries.

Go
- `internal/evidence`: product-form belief for ADD-only domains (`buildAddOnlyProductBelief`); test
  `product_test.go` (10,000 random histories, same guard verdict as the enumeration; no configuration cap).
- `cmd/evicon-device`, `internal/device`: presets K0S ({Q_S}), NEG_SEAL and NEG_DEDUP (negative controls);
  tests D11-D13.
- Rule (A4): the runtime takes the age of a query answer at send time (F41) and never commits NOT_FOUND as
  STOPPED; the simulator anchors expiry at the first send and ages answers at the query send time.
- `internal/contract`: Q_S and F require an orphan-recognition premise; re-issue only while
  `validity - max_network_delay` has not passed; test that `Admit` is monotone under `Includes`.
- `internal/sim`: `<base>+QS` contract upgrade.
- **Safety fix (found by the pre-release check, 4 duplicate effects on K0S):** a command closed by terminal evidence
  (a stop or query answering APPLIED) could be dispatched again: the T4 guard allowed any command that was no longer
  unresolved, and the dispatcher, working on an outbox snapshot, overwrote the DONE state. An owner without dedup then
  executed it twice. Now the T4 guard refuses terminal commands, `journal.ClaimDispatchAttempt` opens an attempt only for
  an open item whose command is not terminal (one statement; otherwise the item is closed, trace
  `OUTBOX_SKIPPED_TERMINAL`), and a dispatch failure never reopens a DONE item. Test F43; F38 rewritten (a conflict can
  only come from a dispatch in flight). Earlier runs are unaffected: every contract they used with a query or stop also
  had dedup, or had only a fence; no run had a duplicate effect.
- `internal/runtime`: an EVICON DEFER caused by the window keeps the cause first
  (`OUTSTANDING_BUDGET_EXHAUSTED;<EVICON reason>`); test F42.
- Tests updated for the new semantics: F20, five `internal/evidence` tests; F29 adds K0S.

Python
- `evaluation/pairs_report.py`: generic paired analysis driven by a ```pairs-spec``` block of a frozen plan;
  confirmed fulfilment from `clients.jsonl` + `plant.jsonl`; owner-reached stop/fence success from traces;
  `at_least`/`at_most`/`equivalent`/`rate_greater`/`detects`/`absent`, Holm per family; cross-run estimands
  on byte-identical schedules; gap recovered.
- Experiments: `expW_{queue,direct}_{w6,winf}` (`PREREG_expW_072.md`), `expS_{w6,winf}` (`PREREG_expS_072.md`),
  `expL_sleepy` (`PLAN_expL_072.md`, exploratory), `negctl_072` (`NEGCTL_072.md`), frozen with `pairs_report.py`
  and `cadence_report.py`.
- Driver: a campaign whose contracts are all `NEG_*` passes only if the checker reports violations; `report`
  runs `pairs_report` for every plan with a pairs-spec, `t3_attribution.py`, `evidence_latency.py` and the K2
  replication (`confirm_report.py --k2-rep`).
- `paper_tables.py`: `tab_pairs_<plan>.tex`, `tab_evidence_latency.tex`, T3 macros, K2 replication column.
- `evicon_review.py bundle --incremental`: only files new or changed since the last bundle.
- `evaluation/sim_sweep.py`: the simulator sweep and the power of the 072 decision rules behind the 072 plans.
- Tests: `evaluation/test_pairs_report.py`; `test_prereg` (072 plans frozen, configs differ from
  `expA_confirm_k0` only where planned, 067 and 071 plans unchanged).
- VERSION 072.

## 071 — 2026-10-21

Experiment B2 (resolution cadence), preregistered, to resolve H-B.

On K0F, EVICON fences about 4x as often as RES_PERIODIC for the same clean fulfilment. The reason
is cadence: a fence that reaches an offline owner leaves a pending barrier, which the EVICON sweep
retries every 1 s and RES_PERIODIC every 10 s. In return, EVICON has about 23 % less censored
stranded capacity-time in both runs.

No Go change (only `buildinfo.Release`); the six files frozen by the 067 plan and the plan itself
are unchanged.

- `evaluation/campaigns/expB2_cadence.json`: K0F, EVICON with `resolve_sweep` 10 s against
  RES_PERIODIC (10 s), 32 episodes. Same regime and seed as `expA_confirm_k0`, so episodes 0-15
  replay the schedules of the 069 replication run.
- `evaluation/prereg/PREREG_expB2_cadence_071.md`: H-C (equivalence within ±5 pp, 90 % t
  interval), H-S, estimands E1-E3. The plan discloses that the margin was chosen after the H-B
  results.
- `evaluation/cadence_report.py`, frozen through a new optional `frozen-extra` block of
  `scripts/freeze_prereg.py`. Student t with no scipy. Cross-run pairing only on byte-identical
  schedule files.
- `report` runs it; `paper_tables.py` writes `tab_cadence.tex`, adds the `\evCad*` macros and adds
  B2 to the fence-cost table.
- Tests: `test_reports` (B2 decision rules, pairing, t distribution), `test_prereg` (067 plan
  hash unchanged, B2 frozen, B2 config equals the K0 config except the cadence).
- VERSION 071.

## 070 — 2026-10-21

For the paper; no fixes. The 069 testbed run (2026-10-20) passed all 18 stages, and the replication of
`expA_confirm_k0` on the fixed emulator replicated (EVICON +30.68 pp, RES_PERIODIC +30.38 pp, 0 receipt
flips, 0 stuck cells). The six frozen files and the Go runtime are unchanged (only `buildinfo.Release`).

- `evaluation/confirm_report.py`: `--k0-rep <run>` (repeatable) adds a replication section, using the
  same frozen plan and a decision rule written before the run (`evaluation/prereg/REPLICATION_expA_k0_069.md`).
  A replication replicates when H-A1, H-A1c and H-S hold on it alone and its fence audit shows 0
  receipt flips and 0 stuck cells. The K0 part is factored into `k0_part()`; the primary output is
  unchanged line for line.
- `evicon_review.py report`: the earliest run of each config is the primary (preregistered) one;
  later K0 runs are passed as replications (069 used the newest run).
- `fence_audit.py`, `overhead.py`: two runs of one campaign are reported apart
  (`campaign@<stamp>`) instead of being merged.
- Contract Map: columns `measured_in` (release, run, stamp, parsed from the path),
  `D_h_lower_s`/`D_h_upper_s`, `declared_level`, `late_effect` (from `rubric.json`).
- `conformance/configs/{minio-conditional,k8s-name,zephyr-mqtt}.json`: P5 with `beyond_gap_s` 600,
  so the measured D_h lower bound is 600 s instead of 2 s; duration estimates updated.
- `evaluation/paper_tables.py` (run by `report`): LaTeX tables (Contract Map, experiment A with
  replication, RQ4, fence cost, footprint), `numbers.tex` macros, PDF figures (validated
  colour-blind-safe palette, marker shapes, TrueType fonts embedded).
- `timeline.py`: also writes `timeline.pdf`.
- Tests: `evaluation/test_reports.py` (replication pass/fail, run labels, paper tables),
  `conformance/tests/test_adapters_offline.py` (measured_in, horizon interval).
- VERSION 070, `buildinfo.Release` 070.

## 069 — 2026-10-19

Fixes what the 068 testbed run (2026-10-19) exposed and sharpens the RQ4 latency breakdown. No new module; the six files frozen by
`PREREG_expA_confirm_067.md` are unchanged (byte-identical to 068).

- Temporal reference worker (068 regression, `measure temporal` FAIL on the testbed): the
  proxy-free opener installed at import time made the Temporal workflow sandbox reject the module
  (`RestrictedWorkflowAccessError: Cannot access urllib.request.ProxyHandler.__call__`). The opener
  is now built lazily by the activity. New `conformance/tests/test_reference_temporal.py` runs the
  sandbox's `prepare_workflow` on the worker module and checks the opener ignores proxy variables
  (fails on 068, passes on 069). The 068 self-test had skipped `measure temporal` and `coap`; the
  069 self-test measures every stack.
- MinIO image build: `docker build --network=host` plus VM-B's proxy variables as build args, in
  `deploy/setup/vm-b.sh` and the driver (VM-B containers could not resolve DNS). The Dockerfile
  reports a failed clone and a wrong tag commit separately.
- `setup_all.sh`: the summary reports `OK, n warning(s)` and lists the WARN lines.
- Leftover sweep: only `/var/lib/evicon/{controls,campaigns,measure,overhead}`, never `builds`
  (068 preflight stopped the Zephyr build wrapper). `build_iot.sh` detects a running build by its
  `build_*.sh` process too.
- Rubric: the reason for a refuted Q_A when the query answers APPLIED without terminal evidence says
  so (MinIO unconditional PUT); levels unchanged.
- Image provenance: falls back to id + digests when the labelled format prints nothing (068: empty
  for NATS), records errors, and lists the image of every `evicon-probe-owner` container.
- `overhead`: WARN when `echo-*` processes run on VM-A/B/E (never stopped).
- Authority: one trace event `ADMISSION_RECEIVED` per command request, with `wait_ns` (handler
  entry -> request read: the authority lock and the body); behaviour unchanged. Go test F40.
- `evaluation/overhead.py`: admission latency split `admit = in + decide + out` with
  `in = client + lock + eval` (scheduled arrival -> handler start; lock; request read ->
  ADMISSION_PREPARE), from the trace wall clock against the client's; columns, a markdown table and
  `test_overhead_split`. Release 068 traces give `in`, `decide`, `out` only. On the 068 testbed data
  `decide` stays at 1.8-3.8 ms p50 from 1 to 20 commands/s per device while `in` grows to 1.4-1.8 s;
  the 069 traces attribute that growth to the load generator or to the authority. `terminal_ms` is
  documented as bounded below by the client poll interval (`retry_s`).
- VERSION 069, `buildinfo.Release` 069.

## 068 — 2026-10-18

Fixes what the 067 confirmatory campaigns exposed and adds the modules the TOIT evaluation still
needed (four more measured systems, RQ4 overhead, paper report, exploratory twin v2). The six
files frozen by `PREREG_expA_confirm_067.md` are unchanged (byte-identical to 067).

- Device emulator (067 finding, ep5/ep7/ep12/ep14 K0F): a late copy of an APPLIED command that
  arrived after the cutoff, at a device without dedup, was sealed STOPPED and overwrote the APPLIED
  receipt. `internal/device`: a record with seq <= cutoff is final (the late copy gets the stored
  receipt), and a terminal receipt never flips (`ErrTerminalReversal`). Tests D09, D10.
- Authority: owner evidence contradicting a committed terminal outcome is now `409
  EVIDENCE_CONFLICT` (trace event `EVIDENCE_CONFLICT`, conflicted domain recorded) instead of an
  HTTP 500 retried every second; `resolveLocked`, `queryDirect`, `stopOne`, the policy query path
  and the outbox escalate instead of retrying; the committed outcome is kept. Journal: typed
  `ErrTerminalReversal` (three sites), `MarkOutboxConflict`. Tests F37-F39. With a correct device
  this branch never runs, so experiment A behaviour is unchanged.
- Query selectors (M-policies) no longer query an owner without Q_A (`NO_QUERY_IN_CONTRACT`); 067:
  2 of 16 K0 episodes sent 46-66 queries answered 409 UNSUPPORTED, outcomes unchanged.
- `measure dbos` (DBOS Transact 3.3.0 on PostgreSQL 16.10; app on VM-A, effect through VM-C to the
  plant), `measure k3s` (k3s v1.36.4+k3s1 API server only, token-auth-file; ConfigMap by name and
  by generateName; the fault proxy terminates TLS so faults hit HTTP, not the handshake),
  `measure thingsboard` (ThingsBoard CE 4.3.1.6 + PostgreSQL 16.10, schema with demo data, demo
  passwords replaced by random ones, probe device created; persistent two-way and one-way RPC),
  `measure zephyr` (the cutoff firmware, Zephyr v4.2.0 native_sim/native/64, behind Mosquitto; data
  path through the proxy, query/fence on a direct connection). All credentials random per
  measurement or from VM-C's environment, passed on ssh stdin, never in a file or log.
- MinIO: no published images any more (Docker Hub removed, quay 401, dl.min.io 410): built on VM-B
  from the official source of RELEASE.2025-04-22T22-12-26Z (tag commit checked) by `vm-b.sh` or
  `measure minio`; `MINIO_IMAGE` overrides.
- Image variables set on VM-C are passed explicitly to VM-B (067: an override only worked in VM-B's
  environment). One owner table (STACK_PROCS/STACK_FREE/STACK_DEPLOY) instead of per-stack branches.
  Owner readiness by protocol (PostgreSQL SSLRequest, Kubernetes /readyz with the probe token,
  ThingsBoard REST login); a container that exits while the driver waits is reported at once.
- The driver's own HTTP(S) requests never use http(s)_proxy (urllib ignores CIDR entries of
  no_proxy; found when testing k3s); the reference DBOS app and Temporal worker likewise.
- RQ4: `evicon_review.py overhead` runs three load campaigns without faults
  (`evaluation/campaigns/rq4_load_r{1,5,20}.json`) with a /proc sampler of EviCon's own processes on
  VM-A and VM-E (`evaluation/rq4_sampler.py`, one samples file per configuration and start), then
  `evaluation/overhead.py` (admission and decision latency from the traces, fences/queries/decisions
  per command, CPU and RSS per process) over them and over the experiment A campaigns.
- `evicon_review.py report [--from RUN ...]`: fence/evidence audit (`evaluation/fence_audit.py`,
  classified by error text: the 066 analysis missed 637 lowercase "terminal outcome reversal"
  errors), the confirmatory results against the plan (`evaluation/confirm_report.py`), overhead,
  the contract map over several runs (newest measurement per contract id; `run_probe.py map
  --measured` is repeatable), the firmware footprint, and `evaluation/timeline.py`: one real
  ambiguity episode (fault, client, authority, owner journal, plant truth on one time axis; table
  and draft figure). `evaluation/prereg/RESULTS_expA_confirm_067.md`
  (generated, with a Deviations section and the post hoc sensitivity analysis).
- `evicon_review.py twin2 [--from RUN ...]`: exploratory twin v2 (`experiments/sim/iot_dosing_testbed_v2.json`,
  offline periods uniform in [10, 40] s; `OfflineUniformMS` in `internal/sim`), SIMULATION_ONLY.
- Driver analysis fixes: power expectations per config; the A-equivalence check only for K2;
  `sr_not_applicable` when a config predicts no non-zero effect; pooled sim-to-real over campaigns
  (reported separately, post hoc); testbed command failures carry the error tail.
- Firmware: `boards/native_sim.conf` disables NET_DRIVERS, NET_SOCKETS_OFFLOAD and ETH_NATIVE_TAP
  (without them Kconfig silently dropped NSOS); `boards/native_sim_native_64.conf` (the 64-bit board
  does not read native_sim.conf). `build_zephyr.sh`: 64-bit native build with the host toolchain,
  broker from testbed.json, project filter (cmsis_6, hal_nordic), SDK download fallback with
  checksums, stable output names. `build_iot.sh`: Zephyr only by default, on VM-D; Matter optional.
- Rubric: deviations kept the closing parenthesis of their reason (`rstrip(" ()")` cut it).
- `build_iot.sh start` (067) waited for the whole build: `cd && nohup ... &` backgrounded a
  subshell that kept ssh's output open; now only nohup is backgrounded and `start` returns.
- `measure a b c`: a failing stack no longer stops the following ones (rc 1 at the end).
- Found by the 068 simulated-testbed runs and fixed before release: the ThingsBoard device was
  checked against the wrong /healthz role; `overhead`/`report`/`twin2` called a late-bound
  closure; the trimmed `docker-compose.yml` kept a second copy of the old services. New
  `conformance/tests/test_deploy_files.py` parses every compose file and checks the driver's owner
  table against it (service, profile, published ports, image variables, required secrets).
- Adapters: DBOS, k8s, ThingsBoard rewritten and run against the real systems in the build
  environment (TESTED_LOCAL); CoAP, MQTT, NATS, Temporal marked TESTED_TESTBED (measured on the
  5 VMs in 065-067); FaultProxy `tls` option; MQTT `control_broker`.
- Setup: `vm-a.sh` installs dbos==3.3.0 (Python >= 3.10); `vm-b.sh` pulls PostgreSQL, k3s,
  ThingsBoard, builds MinIO, opens 5433, 16443, 18088, 1885; `vm-c.sh` needs openssl; `vm-e.sh`
  opens 18202; `setup_all.sh` copies the compose directory to VM-B.
- Not in 068: no `measure` for Matter (build only) or LwM2M/Leshan (no pinned Leshan build could be
  obtained or checked in the build environment); their adapters stay UNTESTED.
- VERSION 068.

## 067 — 2026-10-16

- Confirmatory experiment A with a frozen preregistration: `evaluation/prereg/PREREG_expA_confirm_067.md`
  (hypotheses H-A1, H-A1c, H-A2, H-S, H-B, SR with decision rules on named result files; n from the
  066 pilot), `evaluation/campaigns/expA_confirm_k0.json` (K0/K0F, 16 episodes, seed 20261017) and
  `expA_confirm_k2.json` (K2/K3F, 5 episodes, seed 20261018). `scripts/freeze_prereg.py` writes the
  SHA-256 of the analysis files into the plan and the plan's SHA-256 into the configs;
  `evicon_review.py campaign` refuses a preregistered config whose plan or frozen files changed, and
  refuses --episodes/--quick for it. Campaign config key `preregistration`.
- `measure emqx|nats|minio`: owners in EviCon's compose project on VM-B with protocol readiness checks
  (MQTT CONNECT, NATS INFO, MinIO health); the project's own leftover containers/volumes are removed by
  label first; MinIO credentials from VM-C's environment or random per measurement, passed on ssh
  stdin, never in a file or log. NATS config gets P5B. `vm-b.sh` pulls the four images and opens ufw.
- S3 adapter: explicit signed Content-Type on requests with a body (urllib's unsigned form type made
  an S3 server misroute the versioning request); `?versioning` like botocore. Result manifests no
  longer record access keys.
- `deploy/setup/build_iot.sh` (start/status/fetch) with `build_zephyr.sh` (Zephyr v4.2.0, SDK,
  native_sim firmware, nrf52840dk footprint sweep) and `build_matter.sh` (connectedhomeip v1.4.2.0,
  chip-all-clusters-app, chip-tool); background builds on VM-D by default; status in bundles.
- `power.py`: NO_ACTION_IN_CONTRACT for resolver groups without any evidence action; INFEASIBLE for
  n > 5000 instead of a 10-minute bootstrap.
- VERSION 067.

## 066 — 2026-10-16

Supersedes 065 (including the "065 fix 3" package of 2026-10-16, which was not run). Fixes what
the 065 testbed run (first round + experiment A, 60 cells) exposed.

- FaultProxy (found in the 065 first testbed run): the TCP proxy opened the upstream connection at
  once while the request bytes were delayed, so a server with a header timeout (Go
  ReadHeaderTimeout 2 s in evicon-plant/device/endpoint/authority) closed it before a delayed
  request arrived on a new connection: "delay" acted as "drop" (all controls: P3/P9/P9F/P9T had
  0 effects, so the late-request branch of Q_S/F/T was never exercised; Temporal P2/P3/P9;
  campaign delay_up windows). Now a connection opened during a request delay reaches the server
  after that delay (as with a delayed SYN), one opened during a request drop reaches it only with
  the first bytes that get through, and a server close after a dropped reply is not passed to
  the client (it times out). Rubric: `D_h_unbounded` no longer listed as unverified when D_h is
  contradicted; deviation when the measured horizon is much longer than declared. Catalog
  `hyp-coap-con`: 247 s (RFC 7252 EXCHANGE_LIFETIME), was 45 s.
- Runtime: barrier livelock. A fence aborted by the owner (UNCERTAIN crash orphan) left an
  ABORTED token `evicon-{mode}-{origin}-r{revision}`; the revision does not move while admission
  is blocked and `BeginBarrier` refuses an ABORTED token forever (065 ep0/K0F/EVICON: 331 x 409
  BARRIER_ABORTED). A fresh token `-a<n>` is used now; test `TestF36AbortedFenceGetsAFreshToken`.
- Simulator: `network.late_replies` ("deliver" default | "drop": a reply after
  `dispatch_timeout_ms` is lost and the command stays ambiguous) and `authority.fence_uncertain`
  ("partial" default | "abort": a fence aborts when a frontier attempt has an UNCERTAIN receipt);
  new metrics `late_replies_dropped`, `fence_aborts`. Defaults keep every other regime unchanged.
- Experiment A twin `experiments/sim/iot_dosing_testbed.json` aligned with the testbed: drop late
  replies, 2 s dispatch timeout, fence abort on UNCERTAIN, fault rates measured from the 065 fault
  logs (offline 20.5 %, mean 23.7 s; drop_up 1.38 %, drop_down 1.79 %, delay 1.04 %; crash 0.5 %
  of commands). On the 065 data: K0 0.375 vs testbed 0.279 (065 twin: 0.683).
- `sim_to_real.py`: sign with a practical margin (`--margin-pp`, default 2) and Kendall tau-b
  with ties under the margin; check = no sign disagrees, >= 1 non-null effect agrees, no
  undetermined testbed effect, pooled tau-b >= 0.6. The 065 criterion is still reported
  (`check_065`). Revised after seeing the 065 data; the 066 testbed run is the confirmatory test.
- `analyze.py`: resolver groups whose every decision was ESCALATE are `NO_ACTION_IN_CONTRACT`
  (`resolver_groups_no_action_in_contract`), no longer "not activated".
- VERSION 066, `buildinfo.Release` 066.

## 065 — 2026-10-14 (setup fix 2026-10-15, fix 2 2026-10-15)

- Fix 2: `measure temporal` starts its own Temporal (`temporal server start-dev`, Temporal CLI
  1.3.0 = Server 1.27.1, in-memory, 0.0.0.0:7243) on VM-D and stops it afterwards; the existing
  Temporal on VM-D:7233 (an older deployment on the testbed) did not answer gRPC from VM-C or VM-D and is no
  longer used. VM-D needs no Docker. `scripts/ensure_temporal_cli.py` (pinned, SHA-256 of the
  release's checksums.txt; `--archive` offline); `setup_all.sh` copies the CLI to VM-D and
  starts/checks/stops the server once; testbed key `temporal_address` measures an existing
  Temporal instead. `deploy/temporal-postgres` removed.
- Fix 2, latent issues: pid-file stops require the process to still run in its workdir
  (`/proc/<pid>/cwd`), so a stale pid file never signals another program after a reboot;
  `preflight`, `controls`, `pilot`, `campaign` and `measure` stop EviCon's own leftovers of an
  interrupted run (a campaign interrupted by a VM-C power loss can be resumed); `measure coap`
  checks a real CoAP round trip from VM-C after start (udp6 only, falls back to
  simplesocketserver on VM-E's address); one
  `evicon_review.py` at a time (`/run/lock/evicon-review.lock`); `ufw` allow rules on every VM
  when ufw is active; `python3 -m pip`; `bundle` asks the VMs even without a review run and
  collects measure logs from VM-A/VM-D, firewall state and the Temporal CLI version; unique task
  queue per `measure temporal`; `setup_all.sh` validates VM names and testbed.json.
- Setup fix after the first installation on the 5 VMs: `apt_install` waits for another
  apt/dpkg (unattended-upgrades) instead of failing on the dpkg lock. Temporal is checked from
  VM-C with the Python SDK (`deploy/setup/temporal_check.py`: serving + namespace "default",
  created when missing), the path `measure temporal` uses; `vm-d.sh` only makes sure a
  Temporal holds 7233 (never touching a foreign one) and has `--diagnose`. A Temporal CLI
  container on VM-D's host network could not reach a healthy server, so checking there was
  misleading. `measure temporal` checks `describe_namespace` before starting the worker.
- New `evicon_review.py measure <mosquitto|coap|temporal>` (C2 on real systems): starts the plant
  and a reference device/worker, brings Mosquitto 2.0.20 up on VM-B with docker compose (project
  `evicon-probe-owner`, fresh state per run), uses Temporal on VM-D, checks every process's
  identity (`/healthz` of the reference apps), probes through the in-process fault proxy (no
  Toxiproxy), stops what it started (also on Ctrl-C/SIGTERM), fetches logs, and writes
  `measure/<stack>/measure.md` and a Contract Map.
- New `evicon_review.py campaign <config> [--episodes N] [--max-cells N] [--new-campaign]`:
  long campaigns with resume, binary checks, analysis, sample-size planning and the
  simulator-vs-testbed comparison when complete.
- Experiment A on the testbed: `evaluation/campaigns/expA_dosing.json` (K0/K0F, K2/K3F; M1, EVICON,
  RES_PERIODIC; binding bound 345), its simulator twin `experiments/sim/iot_dosing_testbed.json`,
  and `evaluation/sim_to_real.py` (effect signs, Kendall tau-b).
- Device presets K0F and K1F; controls `device-k0f` ({F}) and `device-k1f` ({D_h, F}).
- Probe P5B: bisects the dedup horizon between the P4 and P5 gaps with fresh identities.
- `power.py`: every group gets a status (`INSUFFICIENT_PILOT`, `NOT_ACTIVATED`, `UNDETERMINED`,
  `NOT_EQUIVALENT`, `SIZED`) and `NO_PAIRS` is explicit; experiment A sizing (`--upgrade`),
  TOST sizing (`--equivalence`), `--delta-pp`, `--json`. The driver's `analyze` writes a WARN
  when nothing could be sized.
- `analyze.py`: `late_effects` (validity) separated from `fulfilled_late` (deadline) in
  `report.json`; runtime upgrade pairs K0F/K0, K1F/K1, K3F/K2 and `--upgrade`; TOST p-values
  with `--margin-pp`.
- Pilots use 3 episodes.
- Fixed in the MQTT adapter (first run against a real broker): lost response subscription after
  a reconnect, `loop_stop()` hanging on unacknowledged QoS 1 messages, keepalive shorter than
  the injected delays.
- The fault proxy waits for bytes delayed by one probe before the next probe starts (an ordered
  TCP stream carried a 7.5 s delay into the next probe).
- VM-B compose: MinIO moved to `docker-compose.minio.yml` (its required credentials made
  `--profile mosquitto up` fail).
- `deploy/setup/`: idempotent setup scripts per VM, `setup_all.sh` from VM-C.

## 064 — 2026-10-13

- Fixed controls measuring another program: on the testbed, an endpoint from an older deployment
  held VM-B:18081, every `evicon-endpoint` failed to bind, and the readiness check accepted the
  other program's `/healthz` (E1 passed by coincidence, E2/E2-stop/E3 failed).
  - Every `/healthz` (plant, device, endpoint, authority) now names `role`, `release` and `pid`;
    readiness accepts only the process just started and fails at once otherwise, naming the
    port's owner (`ss -ltnp`).
  - `preflight` fails (no longer warns) when a port EviCon needs is held, and names the owner.
  - The VM-B endpoint port moved from 18081 to 18181 (18080-18083 and 19080-19083 are held by an older deployment).
- The result bundle lists every listening port with its program on each VM.
- Release zips keep their UTC timestamps (no `zip -X`): the 062 "clock skew" warning came from
  local (+07) zip times read as UTC on VM-C, not from VM-C's clock.

## 063 — 2026-10-13

- Fixed `make campaign-local` (every cell INVALID "not ready"): a relative `--out` broke the
  plant's log path because processes start after `cd <cell dir>`; campaign paths are now absolute.
- Fixed start-up errors being hidden: readiness checks fail fast when the process exited and
  report its state and log tail; full INVALID reasons go to `errors.log` and `problems.txt`.
- Fixed 16/36 EviCon-Probe tests silently skipped under `make verify` (Go only in `.tools/go`);
  `conformance/run_tests.py --require-go` fails on Go skips and reports every skip reason.
- Fixed Python 3.10 parsing of Go RFC 3339 nanosecond timestamps (time-to-terminal and stranded
  metrics were dropped on Ubuntu 22.04).
- Testbed HTTP traffic never uses `http_proxy` (Go `internal/httpx`, Python
  `evicon_probe.use_direct_http`).
- Closed fault-proxy connections and test servers (ResourceWarning noise on Python 3.10).
- `buildinfo.Release` follows `VERSION` (tested); release zip files carry past mtimes.
- Logging to files: `results/logs/make-*.log`, `campaign.log`/`errors.log`, driver `errors.log`
  and `results/logs/driver.log`, probe `probe.log`/`errors.log`.
- New `python3 evicon_review.py bundle` / `make bundle`: one `EVICON_<release>_result.zip` with
  results, logs, a VM-C environment snapshot and the VMs' state over SSH.

## 062 — 2026-10-13 (EviCon)

- EviCon (Evidence Contracts): module `evicon`, binaries `evicon-*`, policy `EVICON`,
  EviCon-Probe, firmware prefix `evc_`, TLA+ `EviconFence`.
- Fixed review findings RV01–RV10: resolver activation on a
  full outstanding budget and from a background sweep; five-state rubric with provenance and
  pressure probes; MEASURED contracts require provenance; firmware claim high-water mark,
  eviction watermark, certified queries and a durable tombstone table; MQTT fence never infers
  STOPPED; crash-orphan handling in simulator, runtime (barrier abort) and TLA+;
  same-capability baselines and contract upgrades as contracts; unified metrics; rewritten
  campaign with immutable cells, INVALID marking, resume and crash faults; narrowed claims.
- Fixed: a fence whose owner call failed left the domain paused; barrier retries were refused
  after any commit; campaign reissue sequences broke monotonicity; P5N counted the first
  effect twice on `evicon-device` (found by the driver's controls gate).
- The reviewer driver records a source fingerprint per stage and flags resumed runs whose
  stages saw different code (`source.json`); preflight lists files that differ from
  `MANIFEST.sha256`.
- TLC pinned to tla2tools v1.7.4.
- Added `evicon_review.py`, the single reviewer driver.

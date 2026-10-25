# EviCon artifact: claims, evidence, and how to check them

Release **077** of the EviCon research prototype (evidence contracts for safe continuation after
ambiguous remote effects). This file is for artifact reviewers: it maps every claim of the paper to the
experiment that tests it, the preregistration that fixed the test before the run, the file that holds
the result, and the command that regenerates that file. `README.md` describes the code; section 5 the
testbed.

## 1. Three ways to check

| Level | Needs | Time | What it checks |
|---|---|---|---|
| **A. Offline, from the deposited data** | Python 3.10+, the data archive (`scripts/make_data_archive.py`) | minutes to an hour (the C and C2 runs: 1.5 min on 4 cores) | Every number of the paper is regenerated from the raw testbed traces: `python3 scripts/make_data_archive.py reproduce --archive <data> --work <empty dir>` runs `evicon_review.py report` over the deposited runs and writes `report/` (all preregistered analyses, the paper tables and number macros in `report/paper/`, the claims check in `report/claims/CLAIMS.md`) |
| **B. One machine** | Linux, Go >= 1.22 (or `make go-toolchain`), Python 3.10+, Java 17 for TLC | tens of minutes | `make build verify` (Go tests, 65+ EviCon-Probe tests, firmware host tests with ASan/UBSan, analysis tests including every plan's freeze check); `make tlc` (bounded TLA+ model with negative controls); `python3 evicon_review.py all --local --quick` (the whole driver on one host). Results are labelled `DEV_LOCAL`: they check the code path, they are not measurements |
| **C. The 5-VM testbed** | five Ubuntu 22.04 VMs on one LAN (VM-C: 4 vCPU, 8 GB), Docker on VM-B, root on VM-C | one night per experiment | `bash deploy/setup/setup_all.sh`, then the run guide of the release; every result is labelled `TESTBED` |

Labels: only `TESTBED` numbers are measurements. `SIMULATION_ONLY` numbers come from the simulator
(`cmd/evicon-des`) and are predictions; `DEV_LOCAL` numbers come from one machine. The driver writes
the label into every run's `manifest.json`, and every table carries it.

## 2. Claims and where their evidence is

Report paths are relative to a report directory (`results/review-*/report/`, or the one level A
writes). Every preregistered hypothesis, MET or not, is listed with its verdict in
`report/claims/CLAIMS.md` (part 1); the cross-cutting claims are checked there mechanically (part 2).

| # | Claim | Experiment and plan (frozen before its run) | Result file | Outcome recorded in |
|---|---|---|---|---|
| 1 | No safety violation (dose-bound violation, duplicate effect, logical duplicate, false STOPPED, false APPLIED) in any testbed cell where EviCon's guards are on; the checker detects violations where they are planned | every campaign; negative controls `NEGCTL_072.md`, NAIVE cells of C and C2 | `safety/SAFETY.md` (totals, exact 95 % bound), each plan's safety rule | `RESULTS_NEGCTL_072.md`, `RESULTS_expC_073.md`, `RESULTS_expC2_074.md` |
| 2 | The measured contract, not the system, decides what a client may do after a timeout: the same API offers different contracts for different calls | EviCon-Probe on the real systems of `measure` (Mosquitto, CoAP, Temporal, EMQX, NATS, MinIO, DBOS, k3s, ThingsBoard, the Zephyr firmware), C (MinIO), C2 (Zephyr firmware, three modes of one binary) | `contract_map.md`, `pairs/PREREG_expC_073/`, `pairs/PREREG_expC2_074/` | `RESULTS_expC_073.md`, `RESULTS_expC2_074.md` |
| 3 | With evidence of non-occurrence (fence, stop) the runtime recovers the capacity a lost command strands; evidence of occurrence alone (Q_A) does so only where the channel eventually delivers what it held | A (`PREREG_expA_confirm_067.md`), W (`PREREG_expW_072.md`), S (`PREREG_expS_072.md`), C3 (`PREREG_expC3_075.md`: C3-1 to C3-4) | `confirm_expA/RESULTS.md`, `pairs/PREREG_expW_072/`, `pairs/PREREG_expS_072/`, `pairs/PREREG_expC3_075/` | `RESULTS_expA_confirm_067.md`, `RESULTS_expW_072.md`, `RESULTS_expS_072.md`, `RESULTS_expC3_075.md` |
| 4 | A per-command stop beats a fence when owner crashes leave orphans (S); with the owner up the two are equivalent (C2-5 at the ceiling; C3-5, C3-6), and each has its own failure: the fence under crash orphans, the stop when the owner's durable tombstone storage is full | S, C2, C3 (C3-5, C3-6; E-C3-6 against the emulated owner) | `pairs/PREREG_expS_072/`, `pairs/PREREG_expC2_074/`, `pairs/PREREG_expC3_075/`, `fence_audit/`, `expC3/EXPC3.md` | `RESULTS_expS_072.md`, `RESULTS_expC2_074.md`, `RESULTS_expC3_075.md` (interpretation notes) |
| 5 | Theorem 3: a blocked request is never re-opened on APPLIED-only evidence or on no evidence | every campaign's authority traces | `t3/T3.md` | claims check |
| 6 | The extra fences of the runtime against periodic reconciliation are a matter of cadence, not of decision logic | B2 (`PREREG_expB2_cadence_071.md`) | `cadence/RESULTS.md` | `RESULTS_expB2_cadence_071.md` |
| 7 | The simulator, frozen in each plan before its run, predicts the direction of the testbed effects; where it misses, the miss is named | every plan's frozen simulator prediction | `predict/PREDICT.md` | claims check (part 2 lists every sign miss) |
| 8 | The emulated owner reproduces a real owner with the same contract (and, with the same capabilities, differs only where its crash orphans matter: the fence) | C, C2, C3 against W and S (same seed, same schedules) | `fidelity/FIDELITY.md` | `RESULTS_expC2_074.md`, `RESULTS_expC3_075.md` |
| 9 | Overhead of the authority: admission latency split into its parts, under load | RQ4 (`rq4_load_*`) | `overhead/`, `latency/` | report |
| 10 | The firmware fits a constrained device | nrf52840dk build of `firmware/zephyr-cutoff` | `footprint.txt` | report |
| 11 | Limits: sleepy devices (late effects), the firmware's RAM receipt ring (C2) and durable tombstone table (C3), an owner reboot (outside the measured fault model) | L (`PLAN_expL_072.md`, exploratory), C2, C3, `measure zephyr-reboot` | `pairs/PLAN_expL_072/`, `expC3/EXPC3.md`, `reboot/REBOOT.md` | `RESULTS_expL_072.md`, `RESULTS_expC3_075.md` |
| 13 | Release 077 (`PREREG_review_077.md`, runs pending): the value of evidence of non-occurrence on the real owners at an unbounded window (U-1 to U-4) and at W = 16 (E-C16); the stop's advantage over the fence caused by crash orphans, tested on one owner (O-1, O-2); experiment A's K2 result on the release-077 runtime (K-1, K-2); the effects at half the fault rate (V-1, V-2) and over a WAN (WAN-1, WAN-2); two practice baselines that release without evidence violate safety where the theory says they must (RT-1 to RT-3, RR-1, RR-2); with the validity cut no late effect comes from a resend (L2-1, L2-2); the delivery binding of MQTT commands decides safety on an owner without D_h (Q-1 to Q-3, X-Q0); EviCon is safe on ThingsBoard's persistent RPC and a blind retry is not (TB-1, TB-S1, TB-S2); the authority's cost with up to 32 domains; the share of the load generator in the admission latency; the firmware's contract on an emulated MCU (X-MCU) and its authenticated messages (X-AUTH) | `PREREG_review_077.md` (seventeen campaigns), `qos-dup`, `scale`, `latency-control`, `measure zephyr-mcu zephyr-auth thingsboard` | `pairs/PREREG_review_077/`, `policy_compare/PREREG_review_077/`, `late/`, `qos_dup/`, `auth/`, `mcu/`, `scale/`, `latency_control/` | `RESULTS_review_077.md` (after the runs) |
| 12 | The fence protocol is safe in a bounded model (2 intents, at most 2 attempts, at most 1 owner crash; not a proof about the Go or C code), and the three negative-control configurations, each without one safeguard, violate an invariant | TLA+ `formal/EviconFence.tla`, 8 configurations (5 safe, 3 negative controls) | `results/formal/summary.txt` (`make tlc`; the driver's `formal` stage writes `formal/summary.txt` of its run); development run: `formal/TLC_RESULTS_DEV.txt` | |

## 3. Preregistrations

Every confirmatory experiment has a plan in `evaluation/prereg/`, frozen by `scripts/freeze_prereg.py`
before its first cell ran: the plan's ```frozen-files``` block holds the SHA-256 of every file the
analysis and the runner depend on, and the campaign config holds the SHA-256 of the plan. The driver
refuses to run a campaign whose plan or frozen files changed, and the analysis checks them again
(`evaluation/test_prereg.py` checks every plan of every release, byte for byte). A plan is never edited
after its run: its outcome is recorded next to it, in `RESULTS_<plan>.md`, copied from the report by
`evaluation/results_record.py`, with the plan's Deviations section and, separately marked, the
interpretation written after the run. The earliest complete run of a campaign is the preregistered one.

| Plan | Experiment | Testbed run (release) | Outcome |
|---|---|---|---|
| `PREREG_expA_confirm_067.md` | A: fence on an emulated owner, confirmatory | 067 | all primary hypotheses MET |
| `REPLICATION_expA_k0_069.md` | A, K0 replicated on the release-069 emulator | 069 | replicated |
| `PREREG_expB2_cadence_071.md` | B2: resolution cadence | 071 | `RESULTS_expB2_cadence_071.md` |
| `PREREG_expW_072.md`, `PREREG_expS_072.md`, `PLAN_expL_072.md`, `NEGCTL_072.md` | W, S, L (exploratory), negative controls | 072 | `RESULTS_*_072.md` |
| `PREREG_expC_073.md` | C: MinIO behind owner gateways | 073 | `RESULTS_expC_073.md` |
| `PREREG_expC2_074.md` | C2: Zephyr firmware behind Mosquitto | 074 | `RESULTS_expC2_074.md` (C2-5 NOT MET; interpretation note) |
| `PREREG_expC3_075.md` | C3: the firmware's evidence decomposed, two channels | 075 | `RESULTS_expC3_075.md` (C3-1 to C3-6 and C3-S MET; interpretation note) |
| `PREREG_review_077.md` | Real owners at W unbounded and W = 16, no crash orphans, K2 on the 077 runtime, mild regime, WAN, practice baselines, sleepy devices with the validity cut, MQTT delivery binding, ThingsBoard, scale, latency control, firmware on an emulated MCU and with authenticated messages | 077 | pending |

Naming: the experiments C, C2 and C3 are the real-owner experiments (MinIO; Zephyr; Zephyr ablation).
They are unrelated to the contributions C1-C3 of `README.md` (contract model, measurement, runtime).

## 4. Reproducing a number

1. Find the number in `report/paper/numbers.tex` (macros) or a `report/paper/tab_*.tex` table; the
   generator is `evaluation/paper_tables.py`, which reads only files of the report.
2. The report file it reads (section 2) names its source run and campaign; the campaign directory holds
   `config.json` (with the plan's SHA-256), `manifest.json` (release, label, binaries), the schedules
   (`schedule_ep*.json`: arrivals and fault windows, reproducible from the config's seed by
   `evaluation/campaign.py`), `runtime_episodes.csv` (one row per cell), and per cell the raw traces:
   the authority's decisions (`raw/**/authority*.jsonl`), what each client saw (`clients.jsonl`), the
   owner gateways' logs and, as the truth, the plant's effect log (`raw/E/plant.jsonl`).
3. The safety counts of a cell are recomputed from the plant log and the clients' log by
   `evaluation/campaign.py` (`collect_live`); `pairs_report.py` recounts clean fulfilment from the same
   files and lists any mismatch with the runner's row.

## 5. Testbed and requirements

Five Ubuntu 22.04 VMs on one LAN: VM-C (harness and driver, 4 vCPU, 8 GB), VM-A (authorities), VM-B (Docker:
Mosquitto, MinIO and the other systems measured), VM-D (firmware builds, Temporal), VM-E (owners and the
plant). The driver runs on VM-C as root and reaches the others over SSH (key-based, `BatchMode`). Addresses
and paths come from an optional `testbed.json` next to `evicon_review.py`, a JSON object with any of the
keys `vm_a` ... `vm_e`, `ssh_user`, `remote_root`, `state_dir` and `temporal_address` (defaults:
192.168.107.150-154, `root`, `/root/evicon`, `/var/lib/evicon`, empty). EviCon stops only processes it
started (by their pid files under its state directory) and removes only its own state directories.


Go 1.27.1 on the testbed (`go.mod`: >= 1.22); Python 3.10 on the testbed (the analysis and the probe core use the
standard library only; the adapters' packages are pinned in `conformance/requirements-conformance.txt`); Zephyr v4.2.0 with its SDK for the firmware
(`deploy/setup/build_iot.sh`; release 077: also for mps2/an385, run by `qemu-system-arm` on VM-E, and for the
nRF52840 DK with OpenThread); Mosquitto 2.0.20, MinIO, EMQX, NATS, ThingsBoard, Temporal, DBOS, k3s as
Docker images on VM-B (`conformance/compose/`); TLA+ tools v1.7.4. No hardware board and no cloud IoT account
are used: the firmware runs as a native_sim process and on an emulated MCU, and the device-management platform
is a self-hosted ThingsBoard CE. Each run records the versions it
used (`bundle/environment.json`, `build.json`, `source.json`).

## 6. Data

`python3 scripts/make_data_archive.py pack --sources-of <release directory> --out <dir>` (on VM-C)
writes one zip per testbed run the newest report read, with `MANIFEST.sha256`, `SOURCES.txt` and a
README, after scanning every text file for credentials. EviCon passes secrets only through environment
variables; no run directory should contain one, and the pack stops if the scan finds anything that looks
like one.

## 7. License and citation

MIT (`LICENSE`); `CITATION.cff`. Upstream components fetched at build or run time keep their own licenses.

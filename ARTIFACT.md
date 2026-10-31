# EviCon artifact: claims, evidence, and how to check them

Release **083** of the EviCon research prototype (evidence contracts for safe continuation after
ambiguous remote effects). The testbed data were collected with releases 066-078 (each run's
`manifest.json` and `source.json` record the release and source that collected it); the final report of
those data, run `review-20261030T182927`, regenerates every number from them with the analysis of release 081.
Release 083 adds the second set of review experiments (`PREREG_extra_083.md`, claim 14), whose testbed runs
are pending; its report adds them to the same sources (`report --archive ... --from <the 078 run>` in the 083
review run). This file is for artifact reviewers: it maps every claim of the paper to the
experiment that tests it, the preregistration that fixed the test before the run, the file that holds
the result, and the command that regenerates that file. `README.md` describes the code; section 5 the
testbed.

## 1. Three ways to check

| Level | Needs | Time | What it checks |
|---|---|---|---|
| **A. Offline, from the deposited data** | Python 3.10+, the data archive (`scripts/make_data_archive.py`) | minutes to an hour (the C and C2 runs: 1.5 min on 4 cores) | Every number of the paper is regenerated from the raw testbed traces: `python3 scripts/make_data_archive.py reproduce --archive <data> --work <empty dir>` runs `evicon_review.py report` over the deposited runs and writes `report/` (all preregistered analyses, the paper tables and number macros in `report/paper/`, the claims check in `report/claims/CLAIMS.md`) |
| **B. One machine** | Linux, Go >= 1.22 (or `make go-toolchain`), Python 3.10+, Java 17 for TLC | tens of minutes | `make build verify` (Go tests, 80 EviCon-Probe tests, firmware host tests with ASan/UBSan, analysis tests including every plan's freeze check); `make tlc` (bounded TLA+ model with negative controls); `python3 evicon_review.py all --local --quick` (the whole driver on one host). Results are labelled `DEV_LOCAL`: they check the code path, they are not measurements |
| **C. The 5-VM testbed** | five Ubuntu 22.04 VMs on one LAN (VM-C: 4 vCPU, 8 GB), Docker on VM-B, root on VM-C | one night per experiment | `bash deploy/setup/testbed_init.sh` (addresses and SSH keys), `bash deploy/setup/setup_all.sh`, then the run guide of the release; every result is labelled `TESTBED` |

Labels: only `TESTBED` numbers are measurements. `SIMULATION_ONLY` numbers come from the simulator
(`cmd/evicon-des`) and are predictions; `DEV_LOCAL` numbers come from one machine. The driver writes
the label into every run's `manifest.json`, and every table carries it.

## 2. Claims and where their evidence is

Report paths are relative to a report directory (`results/review-*/report/`, or the one level A
writes). Every preregistered hypothesis, MET or not, is listed with its verdict in
`report/claims/CLAIMS.md` (part 1); the cross-cutting claims are checked there mechanically (part 2).

| # | Claim | Experiment and plan (frozen before its run) | Result file | Outcome recorded in |
|---|---|---|---|---|
| 1 | No safety violation (dose-bound violation, duplicate effect, logical duplicate, false STOPPED, false APPLIED) in any testbed cell where EviCon's guards are on; the checker detects violations where they are planned | every campaign; negative controls `NEGCTL_072.md`, NAIVE cells of C and C2; cells unsafe by design (the practice baselines of 077, NAIVE on ThingsBoard, QoS 1 on the plain firmware) apart | `safety/SAFETY.md` (totals, exact 95 % bound per cell and, 083, per distinct fault schedule), each plan's safety rule | `RESULTS_NEGCTL_072.md`, `RESULTS_expC_073.md`, `RESULTS_expC2_074.md` |
| 2 | The measured contract, not the system, decides what a client may do after a timeout: the same API offers different contracts for different calls | EviCon-Probe on the real systems of `measure` (Mosquitto, CoAP, Temporal, EMQX, NATS, MinIO, DBOS, k3s, ThingsBoard, the Zephyr firmware), C (MinIO), C2 (Zephyr firmware, three modes of one binary) | `contract_map.md`, `pairs/PREREG_expC_073/`, `pairs/PREREG_expC2_074/` | `RESULTS_expC_073.md`, `RESULTS_expC2_074.md` |
| 3 | With evidence of non-occurrence (fence, stop) the runtime recovers the capacity a lost command strands; evidence of occurrence alone (Q_A) does so only where the channel eventually delivers what it held | A (`PREREG_expA_confirm_067.md`), W (`PREREG_expW_072.md`), S (`PREREG_expS_072.md`), C3 (`PREREG_expC3_075.md`: C3-1 to C3-4) | `confirm_expA/RESULTS.md`, `pairs/PREREG_expW_072/`, `pairs/PREREG_expS_072/`, `pairs/PREREG_expC3_075/` | `RESULTS_expA_confirm_067.md`, `RESULTS_expW_072.md`, `RESULTS_expS_072.md`, `RESULTS_expC3_075.md` |
| 4 | A per-command stop beats a fence when owner crashes leave orphans (S); with the owner up the two are equivalent (C2-5 at the ceiling; C3-5, C3-6), and each has its own failure: the fence under crash orphans, the stop when the owner's durable tombstone storage is full | S, C2, C3 (C3-5, C3-6; E-C3-6 against the emulated owner) | `pairs/PREREG_expS_072/`, `pairs/PREREG_expC2_074/`, `pairs/PREREG_expC3_075/`, `fence_audit/`, `expC3/EXPC3.md` | `RESULTS_expS_072.md`, `RESULTS_expC2_074.md`, `RESULTS_expC3_075.md` (interpretation notes) |
| 5 | Theorem 3: a blocked request is never re-opened on APPLIED-only evidence or on no evidence | every campaign's authority traces (the practice baselines, which release without evidence by design, are listed apart: RELEASE_ON_TIMEOUT and RELEASE_ON_READ of 077, RELEASE_ON_READ_ANSWERED and the release-time variants of 083); window re-openings without committed evidence listed with their trace context (A3, 083) | `t3/T3.md` | claims check |
| 6 | The extra fences of the runtime against periodic reconciliation are a matter of cadence, not of decision logic | B2 (`PREREG_expB2_cadence_071.md`) | `cadence/RESULTS.md` | `RESULTS_expB2_cadence_071.md` |
| 7 | The simulator, frozen in each plan before its run, predicts the direction of the testbed effects; where it misses, the miss is named | every plan's frozen simulator prediction | `predict/PREDICT.md` | claims check (part 2 lists every sign miss) |
| 8 | The emulated owner reproduces a real owner with the same contract (and, with the same capabilities, differs only where its crash orphans matter: the fence) | C, C2, C3 against W and S (same seed, same schedules) | `fidelity/FIDELITY.md` | `RESULTS_expC2_074.md`, `RESULTS_expC3_075.md` |
| 9 | Overhead of the authority: admission latency split into its parts, under load; the share of admissions after the request's deadline (A2, 083) | RQ4 (`rq4_load_*`), every campaign | `overhead/`, `latency/` | report |
| 10 | The firmware fits a constrained device | nrf52840dk build of `firmware/zephyr-cutoff` | `footprint.txt` | report |
| 11 | Limits: sleepy devices (late effects), the firmware's RAM receipt ring (C2) and durable tombstone table (C3), an owner reboot (outside the measured fault model) | L (`PLAN_expL_072.md`, exploratory), C2, C3, `measure zephyr-reboot` | `pairs/PLAN_expL_072/`, `expC3/EXPC3.md`, `reboot/REBOOT.md` | `RESULTS_expL_072.md`, `RESULTS_expC3_075.md` |
| 12 | The fence protocol is safe in a bounded model (2 intents, at most 2 attempts, at most 1 owner crash; not a proof about the Go or C code), and the three negative-control configurations, each without one safeguard, violate an invariant | TLA+ `formal/EviconFence.tla`, 8 configurations (5 safe, 3 negative controls) | `formal/TLC_RESULTS.txt` and `formal/tlc_logs/` (TLC 1.7.4 on the released model; `make tlc` or the driver's `formal` stage writes `results/formal/summary.txt` of a new run); development run: `formal/TLC_RESULTS_DEV.txt` | the paper's Table D.1 |
| 13 | Release 077 (`PREREG_review_077.md`, run with release 078): the value of evidence of non-occurrence on the real owners at an unbounded window (U-1 to U-4) and at W = 16 (E-C16); the stop's advantage over the fence caused by crash orphans, tested on one owner (O-1, O-2); experiment A's K2 result on the release-077 runtime (K-1, K-2); the effects at half the fault rate (V-1, V-2) and over a WAN (WAN-1, WAN-2); two practice baselines that release without evidence violate safety where the theory says they must (RT-1 to RT-3, RR-1, RR-2); with the validity cut no late effect comes from a resend (L2-1, L2-2); the delivery binding of MQTT commands decides safety on an owner without D_h (Q-1 to Q-3, X-Q0); EviCon is safe on ThingsBoard's persistent RPC and a blind retry is not (TB-1, TB-S1, TB-S2); the authority's cost with up to 32 domains; the share of the load generator in the admission latency; the firmware's contract on an emulated MCU (X-MCU) and its authenticated messages (X-AUTH) | `PREREG_review_077.md` (seventeen campaigns), `qos-dup`, `scale`, `latency-control`, `measure zephyr-mcu zephyr-auth thingsboard` | `pairs/PREREG_review_077/`, `policy_compare/PREREG_review_077/`, `late/`, `qos_dup/`, `auth/`, `mcu/`, `scale/`, `latency_control/`, `esim077/` | `RESULTS_review_077.md` (32 of 33 as planned, K-2 NOT MET; interpretation note) |
| 14 | Release 083 (`PREREG_extra_083.md`, runs pending): the gap between QueryOnly and EviCon is the window-full trigger (T1-2 to T1-4, M1W); a release on an answered state read is unsafe on a channel that holds requests and safe with a delay bound below its settle time (RA-1 to RA-3); a longer release time does not make TimeoutRelease safe (RTs-1 to RTs-6); the firmware's main effects hold against the plain firmware under the at-most-once binding, and the cutoff gains at an unbounded window on the queue channel too (T4-1 to T4-6, T4-S1 to T4-S4); the greedy certificate removes the search failures at no cost, and theta far from the default changes little (T5-1 to T5-4, T5-SF1 to T5-SF4); the effect does not grow with the window on the firmware (T6-1, T6-2); the effects hold across fault rates, outage durations and seeds (14 T7 rules); 54 rules in all, a paired-difference rule being a hypothesis only with power >= 0.80 under the testbed's spread (the others, e.g. the effects at half a fault window per minute, are estimands); safety in every guarded cell (X-S1 to X-S5, RA-S1 to RA-S3) | `PREREG_extra_083.md` (thirty campaigns) | `pairs/PREREG_extra_083/`, `policy_compare/PREREG_extra_083/`, `esim083/`, `safety/SAFETY.md` (A1), `overhead/` (A2), `t3/T3.md` (A3) | pending (the 083 runs; a campaign not run by the data freeze is reported as not run) |

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
| `PREREG_review_077.md` | Real owners at W unbounded and W = 16, no crash orphans, K2 on the 077 runtime, mild regime, WAN, practice baselines, sleepy devices with the validity cut, MQTT delivery binding, ThingsBoard, scale, latency control, firmware on an emulated MCU and with authenticated messages | 078 | `RESULTS_review_077.md` (32 of 33 as preregistered, K-2 NOT MET; interpretation note) |
| `PREREG_extra_083.md` | QueryOnly with the window-full trigger, release on an answered read, the release time of TimeoutRelease, the plain firmware under the at-most-once binding, the greedy certificate and theta, the window on the firmware and ThingsBoard, the fault regime and seeds | 083 (pending) | pending |

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
and paths come from `testbed.json` next to `evicon_review.py`, a JSON object with the keys `vm_a` ... `vm_e`
and optionally `ssh_user`, `remote_root`, `state_dir` and `temporal_address` (defaults: `root`,
`/root/evicon`, `/var/lib/evicon`, empty). `deploy/setup/testbed_init.sh` writes it from the VMs' current
addresses and installs VM-C's SSH key on the other four:

    bash deploy/setup/testbed_init.sh detect          # VM-C's address, interface and network
    bash deploy/setup/testbed_init.sh scan            # hosts with SSH on that network (hostname, banner)
    bash deploy/setup/testbed_init.sh write --vm-a <IP> --vm-b <IP> --vm-d <IP> --vm-e <IP>
    bash deploy/setup/testbed_init.sh keys [--login <user>]   # key-based root SSH from VM-C to every VM
    bash deploy/setup/testbed_init.sh check           # SSH, OS, CPUs, memory, disk, clock offset, internet

On the testbed the file is required: the driver, `setup_all.sh` and `build_iot.sh` stop when it is missing,
when two roles share an address, or when `vm_c` is not an address of the machine they run on (a file
copied from another installation). The campaign and probe configs name the VMs by the addresses
192.168.107.150-154; the driver replaces them with the testbed's addresses when it distributes them (one
pass, whole addresses only). The firmware is built for VM-B's address in `testbed.json`
(`build_iot.sh`), so a change of VM-B's address needs a new build. EviCon stops only processes it
started (by their pid files under its state directory) and removes only its own state directories.

Runs of earlier releases that a report compares against (the reference runs of a plan) are read from
their result bundles: `python3 evicon_review.py import-results <bundles or directories> --archive DIR
[--lean]` (`scripts/import_results.py`) checks each bundle against its `.sha256` file, merges the
incremental bundles of one review run, regenerates schedule files that a bundle left out with the
frozen `campaign.py` (and checks the archived ones byte for byte), and lists in `DIR/ARCHIVE.md` which
reference runs are present; `python3 evicon_review.py report --archive DIR` then reads the runs the newest
report in the archive read (as `--sources-of` does for a release directory) and the current run, and stops
when one of them is missing. `--lean` keeps the authority traces of the reference runs and the RQ4 load runs
only (about 10 GB instead of 18 GB for the bundles of releases 065-076); the fence audit and the late
attribution then cover those runs only.


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
writes one zip per testbed run the newest report read (also the runs it read from an `import-results`
archive, each under the release directory of its bundles), with `MANIFEST.sha256`, `SOURCES.txt` and a
README, after scanning every text file for credentials. EviCon passes secrets only through environment
variables; no run directory should contain one, and the pack stops if the scan finds anything that looks
like one.

## 7. License and citation

MIT (`LICENSE`); `CITATION.cff`. Upstream components fetched at build or run time keep their own licenses.

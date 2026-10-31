# EviCon — Evidence Contracts for safe continuation after ambiguous remote effects

Release **083** (research prototype). Artifact reviewers: start with `ARTIFACT.md` (claims, evidence, how to
check them); `CHANGELOG.md` lists what each release changed.

**Question.** A client sends a command to a remote effect owner (a device, a broker plus
device, a cloud API). The client times out, and the command is now *ambiguous*: it may or may
not have taken effect. What evidence must the owner provide so that the client can keep
working without ever violating an invariant (here: a cumulative bound such as a dose budget)?

**Approach.**
- **Contract model.** An owner offers a set of evidence capabilities:
  - `D_h`: same-identity dedup within horizon h;
  - `Q_A`: an identity-scoped query that can return APPLIED;
  - `Q_S`: a per-identity terminal stop;
  - `F`: an owner-certified fence;
  - `X`: atomic effect + receipt;
  - `T`: owner-enforced expiry.

  The model states which continuation actions each set makes safe and where evidence cannot
  help. Code: `internal/contract`, `internal/resolve`, `formal/` (bounded TLA+ model with
  negative controls).
- **Measurement.** EviCon-Probe measures the contract of a (system, operation) pair black-box
  under injected faults. Truth is read from an independent channel, never from what the client
  was told. Every capability has provenance, and declarations are never promoted to
  measurements. Code: `conformance/`.
- **Runtime.** An authority admits commands with a relational guard and resolves ambiguity
  only with validated owner evidence (query, per-identity stop, certified fence). Code:
  `internal/runtime`, `cmd/evicon-*`, `evaluation/`. A device emulator, a plant (truth)
  emulator and a Zephyr firmware (`firmware/zephyr-cutoff`) provide the owner side.

**Evaluation.** Every confirmatory experiment is preregistered in `evaluation/prereg/` and frozen by
SHA-256 before its first cell runs; its outcome is recorded next to it (`RESULTS_*.md`). The experiments
run on a 5-VM testbed against emulated owners and real ones (MinIO's S3 API; the Zephyr firmware behind
Mosquitto; ThingsBoard's persistent RPC), with negative controls of the checker. Release 077 adds the plan
`PREREG_review_077.md`: the real owners at an unbounded window of unresolved commands and MinIO at W = 16, an
emulated owner whose crashes leave no orphans, experiment A's K2 configuration on the release-077 runtime, a
regime with half the fault rate, a WAN between authorities and devices, two practice baselines that release
without evidence (`RELEASE_ON_TIMEOUT`, and `RELEASE_ON_READ`, a state read such as a device shadow), sleepy
devices with the validity cut of resends and the attribution of every late effect to the attempt that caused
it, the delivery binding of MQTT commands (QoS 1 against QoS 0), EviCon end to end on ThingsBoard CE's
persistent server-side RPC, the authority with up to 32 domains, a control of the admission-latency
decomposition against a no-op endpoint (`cmd/evicon-noop`), the firmware on an emulated Arm Cortex-M3 MCU
(QEMU) and with authenticated messages. Release 083 adds the plan `PREREG_extra_083.md`: QueryOnly with EviCon's
window-full trigger (`M1W`), a practice baseline that releases only on an answered state read, the release time of
`RELEASE_ON_TIMEOUT`, the plain firmware under the at-most-once binding, the greedy certificate of additive domains
(`-certificate greedy`) and the cost ratio theta, the window on the firmware and on ThingsBoard, and the effects
across fault rates, outage durations and seeds. Numbers produced by the simulator (`SIMULATION_ONLY`) or on one
machine (`DEV_LOCAL`) are not measurements.

## Layout

| Path | Content |
|---|---|
| `cmd/evicon-{authority,endpoint,device,plant,des,contract,noop}` | Binaries (`evicon-noop`: the latency control's endpoint) |
| `internal/` | Go core: domain, belief, guard (`analyzer`), journal/outbox, contract lattice, resolver, runtime (with the unsafe practice baselines of releases 077 and 083 in `release_on_timeout.go`), device, plant, simulator |
| `conformance/` | EviCon-Probe (Python): adapters, configs, reference apps and owner gateways (S3 for MinIO, MQTT for the firmware, REST for ThingsBoard), checks (`qos_dup_check.py`, `zephyr_auth_check.py`, `zephyr_reboot_check.py`), compose files for VM-B, tests |
| `evaluation/` | Campaign runners and configs, analyses (`pairs_report.py`, `policy_compare.py`, `late_attribution.py`, `latency_control.py`, overhead, safety total, claims check, paper tables), simulator predictions and sweeps, power, preregistrations and their results (`prereg/`) |
| `experiments/` | Contract catalog, simulator regimes, query cost model, MinIO contracts measured in 070 (`owners/minio/`), the simulator's inputs for the firmware (`owners/zephyr/`) |
| `firmware/zephyr-cutoff/` | Zephyr firmware (evidence modes plain, cutoff, stop; built with Zephyr v4.2.0 for native_sim/native/64 and nrf52840dk; host tests) |
| `firmware/zephyr-mcu/`, `firmware/zephyr-auth/` | The same sources for MCU boards (QEMU mps2-an385, nRF52840 DK with OpenThread); the firmware with authenticated messages (HMAC-SHA256) |
| `formal/` | TLA+ model `EviconFence` and TLC runner |
| `docs/spec/` | API and semantics specifications |
| `evicon_review.py` | **Reviewer driver**: one command on VM-C for every stage, measurement, campaign and report |
| `ARTIFACT.md`, `CITATION.cff` | Claims -> evidence map for artifact review; citation metadata |
| `deploy/setup/` | Testbed addresses and SSH keys (`testbed_init.sh`), per-VM environment setup (`vm-{a,b,c,d,e}.sh`, `setup_all.sh` from VM-C, `build_iot.sh` for the firmware) |

## Quick start (one machine, development)

```bash
make go-toolchain     # pinned Go, only if no Go >= 1.22 is installed
make build verify     # binaries + all development tests
make campaign-local   # a small runtime campaign on 127.0.0.1 (DEV_LOCAL)
python3 evicon_review.py all --local --quick            # the reviewer pipeline, every role on this host
python3 evicon_review.py latency-control --local --quick  # the latency control on this host
```

On the 5-VM testbed (VM-C as root; addresses in `testbed.json`, see `ARTIFACT.md` section 5):

```bash
bash deploy/setup/testbed_init.sh detect             # VM-C's address and network
bash deploy/setup/testbed_init.sh write --vm-a <IP> --vm-b <IP> --vm-d <IP> --vm-e <IP>   # -> testbed.json
bash deploy/setup/testbed_init.sh keys               # VM-C's SSH key on the four other VMs
bash deploy/setup/testbed_init.sh check              # SSH, resources, clock offset, internet
bash deploy/setup/setup_all.sh                       # environment of all five VMs, once
python3 evicon_review.py all --quick                 # regression: build, tests, controls, pilots
bash deploy/setup/build_iot.sh start                 # Zephyr firmware, in the background on VM-D
python3 evicon_review.py plan                        # every stage, measurement and campaign with its duration
python3 evicon_review.py measure zephyr-plain zephyr # contracts of the firmware, measured in this run
python3 evicon_review.py campaign expR_zephyr_winf   # one preregistered campaign (resumable)
python3 evicon_review.py latency-control             # the RQ4 loads against a no-op endpoint
python3 evicon_review.py import-results <bundles> --archive /root/evicon-archive   # runs of earlier releases
python3 evicon_review.py report --archive /root/evicon-archive   # every analysis and the paper tables
```

Every command writes its output to files under `results/` (`results/logs/make-*.log`, per-run
`errors.log`, `campaign.log`, `probe.log`). To collect results:

```bash
python3 evicon_review.py bundle      # -> EVICON_<release>_result.zip (results, logs, environment snapshot)
```

Secrets (MinIO credentials) are passed only through environment variables; no file of the tree or of a
run contains one.

## License

MIT (see `LICENSE`). Upstream components that are fetched at build or run time (Go
toolchain, TLA+ tools, container images, Python packages) keep their own licenses.

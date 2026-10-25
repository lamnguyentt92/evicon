# Zephyr EviCon firmware: contracts for experiment C2 (release 074)

Experiment C2 (`evaluation/prereg/PREREG_expC2_074.md`) runs the EviCon runtime against the Zephyr EviCon
firmware (`firmware/zephyr-cutoff`, `native_sim/native/64`, one process per device on VM-E) behind
Mosquitto 2.0.20 on VM-B. One firmware binary serves three evidence modes (`-evc_mode`):

| Mode | What the firmware does | Measured by | Expected contract |
|---|---|---|---|
| `plain` | executes every delivered command, answers APPLIED; no dedup, no query, no fence, no stop | `measure zephyr-plain` (`conformance/configs/zephyr-mqtt-plain.json`) | none (K0) |
| `cutoff` | the firmware of 068-070: dedup ring with eviction guard, receipt query, durable cutoff fence | `measure zephyr` (`zephyr-mqtt.json`) | D_h (lower bound 600 s), Q_A, F (K3) |
| `stop` | cutoff plus the per-identity stop (durable tombstone table) | `measure zephyr-stop` (`zephyr-mqtt-stop.json`) | D_h, Q_A, Q_S, F (K3) |

**The campaign does not use the files in this directory.** It runs on the contracts EviCon-Probe measures
for each mode in the same review run, on the same firmware binary; the driver checks them against the
plan before any cell runs and copies them into the campaign directory (`contracts/`), from where each
gateway advertises the one of its mode.

The `pred-zephyr-*-074.json` files here are the **simulator's inputs** for the prediction frozen in the
plan (`evaluation/sim_expC2.py`, `evaluation/sim_predictions/expC2_074_*`): the expected contracts,
source `HYPOTHESIS`. They are frozen by the C2 plan; do not edit them.

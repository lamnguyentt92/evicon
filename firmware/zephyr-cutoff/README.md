# Zephyr firmware with a durable cutoff

Actuator firmware for Zephyr. It runs as a Linux process (board `native_sim`) on VM-E. The same code is also
built for a real board, only to report its ROM/RAM footprint; it is not flashed.

Release 077 builds these sources, unchanged, in two more variants: `../zephyr-mcu` (an Arm Cortex-M3 MCU emulated by
QEMU with Zephyr's own TCP/IP stack, measured by `measure zephyr-mcu`; the nRF52840 DK with OpenThread for the
networked footprint) and `../zephyr-auth` (authenticated messages, checked by `measure zephyr-auth`).

## What is tested and what is not

| Component | Status |
|---|---|
| `src/evc_core.c/.h`: APPLIED/STOPPED/UNCERTAIN decisions, durable cutoff, claim high-water mark, eviction watermark, durable tombstone table, guard against re-execution, expiry, bound | **Tested on the host** with gcc, ASan and UBSan (`tests/host`, `make`), including the counterexamples for receipt eviction and tombstone retention in `tests/host/test_evc_core.c` |
| `src/evc_proto.c/.h`: flat JSON parser without dynamic allocation, reply formatting | **Tested on the host**, including malformed input: missing braces, extra commas, numeric overflow, escapes |
| `src/main.c`, `src/footprint_main.c`: Zephyr glue (settings/NVS, MQTT 3.1.1, socket to evicon-plant) | **Built with Zephyr v4.2.0** (SDK 0.17.2) for `native_sim/native/64` (64-bit Linux process, host sockets through NSOS) and for `nrf52840dk/nrf52840` (footprint only); the host tests also compile `main.c` against stub headers |
| `native_sim/native/64` + Mosquitto + EviCon-Probe | **Measured end to end on the testbed**: `measure zephyr`, `zephyr-stop`, `zephyr-plain` (one binary, three modes); runtime campaigns C2, C3 |
| `build.sh`, `CMakeLists.txt`, `Kconfig`, `prj.conf`, `boards/*.conf` | **Used for every build.** `boards/native_sim.conf` disables `NET_DRIVERS`, `NET_SOCKETS_OFFLOAD`, `ETH_NATIVE_TAP` (without them Kconfig silently drops NSOS); `boards/native_sim_native_64.conf` exists because the 64-bit board does not read `native_sim.conf` |

Build on the testbed: `bash deploy/setup/build_iot.sh start` (from VM-C; runs in the background on VM-D, 20–60
minutes). The script writes the Zephyr revision and the firmware's SHA-256 to `/var/lib/evicon/builds/zephyr.status`;
`measure zephyr` records both in `measure.json`.

### Footprint on `nrf52840dk/nrf52840` (built in the build environment with `footprint.conf`)

| Ring (`EVC_RING_SLOTS`) | FLASH | RAM | `size`: text / data / bss |
|---:|---:|---:|---|
| 1 | 30,952 B (2.95 %) | 9,792 B (3.74 %) | 30,672 / 276 / 9,483 |
| 8 | 30,968 B | 10,368 B (3.96 %) | 30,684 / 276 / 10,043 |
| 32 | 30,968 B | 12 KB (4.69 %) | 30,684 / 276 / 11,963 |
| 128 | 30,968 B | 19,968 B (7.62 %) | 30,684 / 276 / 19,643 |

Each slot of the receipt ring costs about 80 bytes of RAM ((19,643 − 9,483) / 127); flash hardly changes. The
numbers from the testbed build (`build_iot.sh fetch` → `results/logs/iot-build/footprint.txt`) are the ones the
paper uses.

## What the firmware certifies

A cutoff blocks the future, but **cannot recover the past**. The firmware answers only what it can actually
certify:

| State | Durable? | Role |
|---|---|---|
| `cutoff` | Yes, written before it applies | F: no command with `seq ≤ cutoff` that has not started will ever start |
| `hw_limit` (claim high-water mark) | Yes, written in blocks of `EVC_HW_BLOCK` (64) **before** any effect | Upper bound of every seq that may have taken effect |
| tombstone table (`EVC_TOMB_SLOTS`, default 16) | Yes, written before STOPPED is answered | Q_S; the ring cannot evict it; full → `TOMBSTONE_TABLE_FULL` (not terminal); a fence collects the tombstones below the cutoff |
| receipt ring (`EVC_RING_SLOTS`, default 32) | No (RAM) | Q_A, D_h bounded by the number of slots |
| `evicted_max` (eviction watermark) | No; equal to `hw_limit` after a reboot | APPLIED receipts with seq ≤ this level may have been lost |

A query (`evicon/<dev>/query {"cmd","seq"}`) returns one of:

| Case | Result |
|---|---|
| The identity is in the ring or in the tombstone table | Its receipt: APPLIED or STOPPED |
| Not present, `seq > evicted_max` and `seq ≤ cutoff` | `STOPPED / SEALED_NOT_STARTED`: certainly not started, and will never start |
| Not present, `seq > evicted_max` and `seq > cutoff` | `NOT_FOUND / NOT_STARTED`: certainly not started, but **not yet** terminal |
| Not present, `seq ≤ evicted_max` | `UNCERTAIN / RECEIPT_MAY_BE_EVICTED` |

Two more rules:
- **Guard** (`CONFIG_EVC_GUARD_EVICTED`, on by default): a command with `seq ≤ evicted_max` whose receipt is gone is
  **not** executed again; the firmware answers UNCERTAIN. After a reboot, clients send new commands with a seq above
  `hw_limit`.
- **Stop** of an identity whose past cannot be determined: the future is blocked, but the firmware answers
  `UNCERTAIN / FUTURE_BLOCKED_PAST_UNKNOWN`, not STOPPED.

The `mqtt5_rpc` adapter only forwards these results. It never infers STOPPED from "fence OK + no receipt seen".

**Actual state cost** (printed by the host test, ring 32, tombstones 16): 920 durable bytes and 3,520 bytes of RAM
for the core. This is **not** an "8-byte cutoff": the durable part holds the cutoff, the high-water mark and the
tombstone table. `./build.sh sweep <board>` measures the real ROM/RAM on a board.

## Declared and measured contract

| Capability | Mechanism | Limit |
|---|---|---|
| F | `evicon/<dev>/fence {"cutoff":N}`, written durably first; monotone | Certifies only identities that have not started or still have a receipt |
| Q_A | query carrying the seq | Receipts in the RAM ring; after eviction or reboot the answer is UNCERTAIN |
| D_h | Replay from the ring, plus the guard | Never executes twice; an old identity may get UNCERTAIN instead of its original outcome |
| Q_S | `evicon/<dev>/stop` (`CONFIG_EVC_STOP` on) | Terminal when the past is known and the tombstone table has room |
| T | `expires_at_ms` (`CONFIG_EVC_EXPIRY` on) | Needs CLOCK_REALTIME synchronised by SNTP |

The measurement config is `conformance/configs/zephyr-mqtt.json`. It applies a pressure of 64 identities, twice
the ring, to show the real retention (P2N/P5N/P9N).

## Running on the testbed

Measured on the 5 VMs, cutoff mode: **K3** (D_h, Q_A, F); P9F: 0 effects after the fence; receipts are lost after
64 intervening identities (ring 32) while the dedup guard holds.

```bash
# [VM-C] build in the background on VM-D (broker = VM-B in testbed.json)
bash deploy/setup/build_iot.sh start
bash deploy/setup/build_iot.sh status      # wait for the line BUILD_OK
python3 evicon_review.py measure zephyr     # copies the firmware to VM-E, starts plant + Mosquitto, measures
bash deploy/setup/build_iot.sh fetch        # footprint.txt, rom/ram reports -> results/logs/iot-build/
```

By hand on one machine (without the driver):

```bash
/root/evicon/bin/evicon-plant -log /tmp/plant.jsonl -http 127.0.0.1:18090 -tcp 127.0.0.1:18092 &
./build.sh native
./build.sh run zephyr-1          # the flash file keeps the durable state across restarts
./build.sh sweep nrf52840dk/nrf52840
```

## Three evidence modes (experiments C2 and C3)

One `native_sim` binary serves several devices and three evidence modes, chosen by command-line options. Real
boards use the Kconfig values. `evc_core.c` and `evc_proto.c` are the same in every mode:

| Option | Meaning | Default |
|---|---|---|
| `-evc_device=<id>` | device ID: topic segment, device name sent to the plant | `CONFIG_EVC_DEVICE_ID` |
| `-evc_ns=<prefix>` | topic prefix; a campaign uses one prefix per cell, so messages the broker kept from one cell never reach the next | `evicon` |
| `-evc_broker=<ipv4>`, `-evc_broker_port=<n>` | MQTT broker | `CONFIG_EVC_BROKER_ADDR`, `_PORT` |
| `-evc_plant=<ipv4>`, `-evc_plant_port=<n>` | line protocol of `evicon-plant` | `CONFIG_EVC_PLANT_ADDR`, `_PORT` |
| `-evc_mode=cutoff\|stop\|plain` | evidence mode | `cutoff` (`stop` if `CONFIG_EVC_STOP=y`) |

| Mode | What the firmware does | Expected contract |
|---|---|---|
| `cutoff` | dedup ring with guard, query, durable fence cutoff | D_h, Q_A, F (K3) |
| `stop` | `cutoff` plus per-identity stop (durable tombstone table, `EVC_F_STOP`) | D_h, Q_A, Q_S, F (K3) |
| `plain` | executes every command it receives and answers APPLIED; no dedup, query, fence or stop (those topics are not subscribed) | none (K0) |

Every mode answers `<ns>/<dev>/ping` (health, never evidence) with its device, mode and level. The native_sim
option parser resets every destination (strings to NULL, unsigned numbers to UINT32_MAX) before it reads the
command line, so the defaults are applied **after** parsing (`apply_defaults()`); an invalid `-evc_mode` or an
out-of-range port stops the firmware.

**Measured on the build machine before release** (Zephyr v4.2.0, `native_sim/native/64`, Mosquitto 2.0.18, one
repetition per mode, P5 shortened to 5 s; `DEV_LOCAL`): plain has no capability (D_h refuted: P4, P2, P6, P8 show
duplicate effects); cutoff {D_h, Q_A, F}; stop {D_h, Q_A, Q_S, F}. The testbed measurement of the C2 review run
gave the same contracts (`evaluation/prereg/RESULTS_expC2_074.md`). Commands are sent at QoS 1. QoS 2 was rejected: the broker forwards a command only
after PUBREL, so a lost reply path also holds the command, and the probe cannot observe Q_A.

On the testbed: `measure zephyr-plain zephyr zephyr-stop`, then `campaign expC2_zephyr`
(`evaluation/prereg/PREREG_expC2_074.md`) and `campaign expC3_ablation` (`evaluation/prereg/PREREG_expC3_075.md`:
the measured contracts restricted to {Q_A}, {F}, {Q_S}, on the queue channel and on a direct channel that loses
commands). Run Rz of `evaluation/prereg/PREREG_review_077.md` (`campaign expR_zephyr_winf`) repeats C2 and C3 cells at
an unbounded window. The authority drives the firmware through the HTTP↔MQTT gateway
`conformance/reference/mqtt_owner_gateway.py` on VM-C (one gateway per device; `mqtt_owner_gateway_ext.py` for C3
and Rz); the gateway advertises the measured contract.

Limits. The firmware never crashed in C2 or C3 (the fault model of the measurement: owner up). Receipts
live in the RAM ring: a firmware crash loses them. The tombstone table is finite: in C3, on the direct channel with a
{Q_S}-only contract (no cutoff ever collects tombstones), it filled in 10 of 32 device-episodes and 272 stops were
answered UNCERTAIN `TOMBSTONE_TABLE_FULL` (`evaluation/prereg/RESULTS_expC3_075.md`). Across an owner reboot
(`measure zephyr-reboot`, outside the measured fault model) the firmware stays safe and loses liveness for the
identities of its claimed block (`report/reboot/REBOOT.md`).

## Why the firmware speaks MQTT 3.1.1 and not the EviCon protocol

The firmware is an ordinary MQTT device: per-device topics, the reply topic in the payload. The authority's
protocol (`/v1/effects`, `/v1/receipts`, `/v1/barriers`, `/v1/stop`) is translated to MQTT by the gateway on VM-C.
Experiments A, B2, W, S and L run on `evicon-device`; the firmware is the owner in C2, C3 and run Rz, and is also
measured by EviCon-Probe and for its footprint.

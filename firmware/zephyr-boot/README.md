# The EviCon cutoff firmware with the boot report (release 087)

`PREREG_extra_087.md` item B (owner reboots as a campaign fault; reviewer comment M3). This application builds the
**frozen sources of `../zephyr-cutoff/src` unchanged** (`main.c`, `evc_core.c`, `evc_proto.c`; frozen by the plans
074-086) plus `src/evc_boot_report.c`. `CMakeLists.txt` compiles `main.c` with three call redirections, so that no source
line changes:

| Call in `main.c` | Redirected to | What it adds |
|---|---|---|
| `evc_core_init` | `evc_boot_core_init` | records the claim floor F, the claim mark restored at this boot (the persisted `hw_limit`; 0 on a fresh flash), then calls `evc_core_init` |
| `mqtt_publish` | `evc_boot_mqtt_publish` | every reply (cmd, query, stop, fence, ping) carries `"floor": F` |
| `mqtt_subscribe` | `evc_boot_mqtt_subscribe` | after every (re)connect's subscription, a hello `{"device", "hello": true, "floor": F}` on `<ns>/<dev>/hello` (QoS 1) |

The decision core, the durable state (cutoff, claim mark, tombstones) and the flash writes are those of the measured
firmware. Why the floor: the level of the current boot is the sum of the values of the identities above F that took effect
in this boot, and every identity above F that took effect did so in this boot (an earlier boot executed only sequences at
or below its claim mark, persisted before every effect; with the eviction guard nothing at or below F executes after the
boot), so F alone attributes the level, for any number of reboots.

**The measured build is not rebuilt.** `deploy/setup/build_zephyr_boot.sh` (started by `bash deploy/setup/build_iot.sh
start zephyr_boot` on VM-C) builds only this application, for `native_sim/native/64`, in the Zephyr workspace of the
083-086 build, into `zephyr-boot.exe`; it records the SHA-256 of the measured `zephyr-cutoff.exe` before and after and fails
if it changed. `evicon_review.py measure zephyr-boot` measures this build with the probes of the measured build (the
firmware gate runs on both builds); the campaign `expRB_zephyr_w6` runs it.

**Host tests** (`tests/host`, no Zephyr SDK): `make -C tests/host` runs `test_evc_boot_report` (the floor appended to every
reply format of the frozen `evc_proto.c`, the hello topic and body, the floor of a first boot and of a reboot, against the
frozen decision core; ASan/UBSan), a syntax and type check of the frozen `main.c` with the three redirections against the
cutoff firmware's stub headers (not a Zephyr build), and the host library `libevcbootshim.so` of the wire path with the
floor (`evc_boot_shim.c`: the frozen `../zephyr-cutoff/tests/host/evc_wire_shim.c` included unchanged), which
`conformance/tests/test_reboot_recovery087.py` builds and loads.

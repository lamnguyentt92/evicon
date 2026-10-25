# Authenticated EviCon firmware (release 077)

`firmware/zephyr-auth` is the cutoff firmware with authenticated messages. Its `src/main_auth.c` is
`firmware/zephyr-cutoff/src/main.c` with three additions, each marked `AUTH 077`; the decision core
`evc_core.c` and the message format `evc_proto.c` are the cutoff firmware's, unchanged.
`src/evc_auth.c` is HMAC-SHA256 (FIPS 180-4, RFC 2104), tested against the FIPS and RFC 4231 vectors
(`tests/test_auth.c`).

1. **Requests.** A command, a stop and a fence must carry `"mac"`: HMAC-SHA256 under the device key of
   `kind \n ns \n device \n cmd \n seq \n value \n cutoff \n token \n expires_at_ms` (decimal numbers,
   empty strings for absent fields). Without a valid MAC the firmware changes nothing and answers
   `{"outcome":"REJECTED","reason":"UNAUTHENTICATED"}`, which is never evidence. The MAC binds the
   device and the topic prefix, so a request made for one device is refused by another. Queries and
   pings are read-only and need no MAC. Replaying an authentic request is harmless: a command is
   deduplicated, a stop is idempotent and a fence's cutoff only grows.
2. **Cutoff bound.** A fence whose cutoff is more than `CONFIG_EVC_AUTH_CUTOFF_AHEAD` (default 64, at
   least the authority's outstanding window) above the highest sequence the firmware has seen is
   refused (`CUTOFF_TOO_FAR_AHEAD`). Even an authentic but wrong cutoff then seals at most that many
   future identities.
3. **Replies.** Every reply carries `"mac"`: HMAC-SHA256 under the same key of
   `reply \n ns \n device \n` followed by the reply's bytes before its `"mac"` field. A gateway accepts
   evidence only when the MAC is valid, so a receipt forged by another client of the broker is ignored.

The device key (`CONFIG_EVC_AUTH_KEY`, 32 bytes as hex) is provisioned at build time.
`deploy/setup/build_zephyr.sh` makes a random key per build, keeps it next to the image on the build
VM (mode 0600) and gives it to the build in a conf file of mode 0600, never on a command line. The
driver reads it over SSH and passes it to `conformance/reference/zephyr_auth_check.py` in the
environment (`EVC_AUTH_KEY`); it is never written to the results.

`python3 evicon_review.py measure zephyr-auth` runs the native build on VM-E in stop mode and the check
(`A1`-`A13`: forged, altered, replayed and misdirected messages, the cutoff bound, the plant's truth).
The build also reports the nRF52840 footprint of the networked authenticated image next to the
networked image without authentication (`footprint-auth.txt`, `footprint-networked.txt`).

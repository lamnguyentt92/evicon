# Preregistration: the experiments of release 087 (the documented shadow loop on the real owners; owner reboots as a campaign fault, with the reboot recovery)

Release 087. Frozen once, after the simulated-testbed regression (section 9) and before any of its cells runs on the
testbed. The three campaign configs below carry the SHA-256 of this file, and `evicon_review.py campaign` refuses to run
them if this file, or any file listed under *Frozen files*, has changed. Changes after freezing are recorded under
*Deviations*; nothing above that section is edited.

## 1. Background (exploratory evidence, not part of the test)

The simulated review of the JNCA submission (`review/rev3/VERIFY_F4_reviewer.md`) asked for two experiments, which the
author approved on 9 November 2026:

- **M2 (item A).** Release 086's documented shadow reconciliation loop (`SHADOW_RECONCILE`, plan 086 rules SH) ran only on
  the emulated owner K2. The paper says that our firmware (its eviction rule: condition (i) of Phi1) and MinIO's conditional
  `PUT` (a conditional create of an existing key fails) suppress every copy of an identity by design, so that a loop that
  re-publishes the same identity is safe there, but the loop was never run there. The reviewer also asked what the loop
  costs on the firmware, where it never releases: a command whose receipt has left the ring is answered UNCERTAIN and could
  hold its budget for good. The cadence confound (finding F3-06: the loop reconciles every 10 s, EviCon sweeps every 1 s)
  is addressed by a second arm at EviCon's cadence.
- **M3 (item B).** The 083 reboot check (`measure zephyr-reboot`) restarts the firmware once: the durable cutoff survives,
  the RAM level resets, and the eviction watermark rises to the claim mark. With release 086's evidence anchor a fence after
  a reboot aborts on the mismatch between the reset level and the evidence, and the domain then admits nothing until an
  operator reconciles it (`conformance/tests/test_fence_anchor_reboot.py`); every new identity of the client inside the
  claimed range is refused as well. The paper's supplement describes a remedy (advance the sequence above the claim mark)
  that was not implemented. Release 087 implements a recovery (section 2.3) and runs owner reboots as a campaign fault.

**The two regimes.** Item A runs in the regime under which every contract of the paper was measured: "network loss/delay,
client abandon; owner up (no owner crash or reboot)". Item B runs **outside** it: the owner (the firmware process) is
stopped and started again with its flash file during the cells. No measured contract covers that regime; what the firmware
certifies across a reboot is the 083 reboot check's measurement, and what the authority does with it is item B's subject.
The firmware gate (section 6) measures both builds in the "owner up" regime.

**Predictions.** No simulator models these owners' reconciliation or reboots; the predictions come from the testbed runs
of plans 085 and 086 on the same schedules (seed 20261105, the SR runs) and from the mechanism, and are stated per rule in
section 3.

## 2. Design

Common regime: that of the SR runs of plan 086 (4 devices; 300 s per cell + 60 s tail; 0.5 commands/s per device; values
1-5; bound 345 per device; deadline 60 s; validity 90 s; fault windows 2/min, 1-4 s; offline 10-40 s; mix offline 0.3,
drop_up 0.2, drop_down 0.2, delay_up 0.1 (6 s), crash 0.2; authority: no assumed bound on the network delay, SKI_RENTAL
resolution, query first, costs hold 1, stop 1, fence 5, probe budget 4, the T4 retry guard on; window W = 6; the direct
channel; commands at QoS 1 on the firmware). Each config is a copy of `expSR_zephyr_w6.json` or `expSR_minio_w6.json` and
differs from it only where the table says (test K3 of `test_release087`).

**The seed: 20261105, reused** (the SR seed of plan 086). Every rule of this plan is decided within one run (same build,
day, owner configuration and schedule per episode), so the seed does not affect its validity. Reusing it makes the
arrival and fault schedules of every episode byte-identical to those of SRz6 and SRm6 (episodes 0-7; test K4), so the SR
runs' EVICON cells pair with the new arms by schedule at no cost (estimands only), and the power templates (section 5)
come from the same schedule set. The reboot fault of RBz6 draws its random times from a stream of its own
(`<seed>/<episode>/reboots/<device>`), so the arrivals and fault windows stay those of the SR schedules.

| Run | Config | Runner | Owner, build | Contract(s) | Policies | Seed | Episodes | Cells | Hours |
|---|---|---|---|---|---|---|---|---|---|
| **SHRz6** | `expSHR_zephyr_w6.json` | `campaign_zephyr_ext087.py` (on the frozen `campaign_zephyr_ext086.py`) | the cutoff firmware, the measured build (SHA-256 `907b2ccd...`, the build of the 083-086 runs) | ZEPHYR_CUTOFF_DIRECT: mode CUTOFF, the measured {D_h, Q_A, F} unrestricted, binding qos1, direct | EVICON, SHADOW_RECONCILE, SHADOW_RECONCILE_1S | 20261105 | 8 | 24 | 3.0 |
| **SHRm6** | `expSHR_minio_w6.json` | `campaign_real_ext087.py` (on the frozen `campaign_real.py`) | MinIO behind the S3 gateway | MINIO_COND (conditional PUT with the tombstone stop; the 070 measured contract {D_h, Q_A, Q_S}) | the same three | 20261105 | 8 | 24 | 2.9 |
| **RBz6** | `expRB_zephyr_w6.json` | `campaign_zephyr_ext087.py` | the boot-report build (`firmware/zephyr-boot`) | NEG_ZEPHYR_F_BOOT and ZEPHYR_F_BOOT: mode CUTOFF, the boot build's measured contract restricted to {F}, binding qos1, direct | arm (i) NEG_ZEPHYR_F_BOOT / EVICON_FI_REC_OS; arm (ii) ZEPHYR_F_BOOT / EVICON_FI; arm (iii) ZEPHYR_F_BOOT / EVICON_FI_REC (`exclude_cells` removes the other three pairs) | 20261105 | 6 | 18 | 2.25 |

In all, 66 cells, about 8.2 hours of cells (7.5 min per firmware cell with the counters, 7.2 min per MinIO cell, as in
the 086 run).

### 2.1 Item A: the documented shadow loop on the real owners

`SHADOW_RECONCILE` is release 086's code unchanged (`internal/runtime/shadow_reconcile.go`): every command is published
with its identity and sequence; release_after after its creation (authority.release_after = 10 s, the documented
reconciliation timeout of plan 086) and every release_after after that the loop reads the owner's reported state of every
unresolved command (at most 16 per sweep, oldest first); a reported APPLIED or STOPPED is recorded; a command the reported
state does not show (NOT_FOUND, or the read is not answered) is published again under its own identity; an UNCERTAIN
report is read again at the next timeout; nothing is released, no query, stop or fence is sent on a guard block.
`SHADOW_RECONCILE_1S` is the same policy with release_after = 1 s, EviCon's sweep cadence (F3-06). The reported-state read
is each owner's per-identity receipt read: on the firmware the gateway's query (`<ns>/<dev>/query`, Q_A), on MinIO the
S3 gateway's receipt read (the receipt object). A re-publish is the same identity (command ID, sequence, intent hash): the
gateways refuse a different sequence or intent (IDENTITY_CONFLICT).

**Why the unrestricted contract on the firmware** (coordinator decision D1). The SR runs restrict the firmware's measured
contract to {F}; with {F} the gateway refuses every receipt read (409), so the loop would never read a reported state and
would re-publish every command every 10 s, which is not the documented loop. Both policies therefore run on the measured
contract {D_h, Q_A, F}; EVICON there may query before it fences (it differs from SRz6's EVICON on {F}; the link to the
replicated headline is the estimand E-SHR-X across runs).

**Premises of the loop's safety on these owners.** The loop's owner-facing actions are reads (no effect) and re-publishes
of the same identity; it commits only an owner's APPLIED or STOPPED receipt. It can violate safety only if one identity
takes effect twice, or if a recorded receipt is false.
- *P-i (condition (i) of Phi1): every copy of an identity, however late, takes effect at most once.* Firmware: the receipt
  ring deduplicates without a time horizon (`CONFIG_EVC_DEDUP_HORIZON_MS=-1`), an identity at or below the eviction
  watermark and not in the ring is refused (`CONFIG_EVC_GUARD_EVICTED=y`), and the claim mark is persisted before every
  effect (both values are read from the build's own configuration by the driver). MinIO: a conditional create
  (`If-None-Match: *`) of an existing key fails with 412, and nothing deletes a key during a cell.
- *DR: a terminal receipt is true and never contradicted* (the firmware's ring and the gateway's cache; MinIO's object or
  tombstone at the key).
With the cutoff firmware and no fence the firmware never answers STOPPED, and on MinIO the loop writes no tombstone, so the
loop commits only APPLIED receipts. Late effects (a re-publish taking effect after the intent's validity) are possible and
are not violations; they are reported.

### 2.2 Item B: owner reboots as a campaign fault

**The fault `owner_reboot`** (`campaign_zephyr_ext087.py`, config key `real_owner.owner_reboot`). Per device and episode two
reboots: a "quiet" one, in the longest gap between two scheduled arrivals of the device that starts at or after 60 s and
ends by 145 s, 1.5 s after the arrival that opens it (no scheduled command of the device in flight); and a "random" one,
uniform in [150, 240] s from the stream `<seed>/<episode>/reboots/<device>`. Each reboot stops the device's firmware process
by its pid file on VM-E (SIGTERM, after 1 s SIGKILL; only while that process still runs in the cell's directory, the rule
of `campaign.Host.stop_all`), waits 2 s, and starts it again with the same arguments and the same flash file (the
mechanism of the 083 reboot check): the durable cutoff, claim mark and tombstones survive, the RAM ring and level do not.
The runner waits for the firmware's ping, records the reboot in the cell's `reboots.jsonl` (planned time, the pid stopped
and how, the restart, the first answer and the floor it reports) and checks the restarted process at the end of the cell.
A reboot that cannot be applied makes the cell INVALID. Eight reboots per cell, the same times in every arm.

**The boot-report build** (coordinator decision D2). The firmware sources are frozen by plans 074-086, so the variant is a
separate application, `firmware/zephyr-boot/`, that compiles the frozen sources of `firmware/zephyr-cutoff/src` unchanged
plus `src/evc_boot_report.c`; three calls of `main.c` are redirected at compile time (`evc_core_init`, `mqtt_publish`,
`mqtt_subscribe`), so no source line changes: every reply carries `"floor": F`, the claim mark restored at this boot (0 on
a fresh flash), and after every (re)connect the firmware publishes a hello `{"device", "hello": true, "floor": F}` on
`<ns>/<dev>/hello`. The decision core, the durable state and the flash writes are the measured firmware's. It is built by
`deploy/setup/build_zephyr_boot.sh` (`build_iot.sh start zephyr_boot`, before the session) in the kept Zephyr workspace of
the 083-086 build; the measured build `zephyr-cutoff.exe` is not rebuilt (the build script records its SHA-256 before and
after and fails if it changed; the driver refuses any measured build whose SHA-256 is not `907b2ccd...`). The firmware gate
measures **both builds** (`measure zephyr zephyr-boot`, the probes of `zephyr-mqtt`); the plan expects {D_h, Q_A, F} for
both (K3, "owner up"), and records both measured contracts in the run (`measure/zephyr/`, `measure/zephyr-boot/`). RBz6
runs only on the boot build's measured contract restricted to {F}; if the boot build measures anything else, RBz6 does not
run (the driver refuses it) and item B is NOT RUN.

### 2.3 The reboot recovery (authority option `-reboot-recovery`, off by default)

`internal/runtime/reboot_recovery.go`; the gateway `conformance/reference/mqtt_owner_gateway_ext087.py`. Why the floor:
the level of the current boot is the sum of the values of the identities above the floor F that took effect in this boot,
and every identity above F that took effect did so in this boot (an earlier boot executed only sequences at or below its
claim mark, which is persisted before every effect and never falls; with the eviction guard nothing at or below F executes
after the boot). So F alone attributes the level, for any number of reboots.

| Step | What | Rests on |
|---|---|---|
| GW-1 | the gateway tracks the floor from every firmware reply and hello (keeps the maximum; durably in its record) | - |
| GW-2 | the gateway reports `claim_floor` in every answer: receipts, refusals, and the barrier answer (the floor of that fence reply, so that level and floor come from the same boot) | - |
| GW-3 | (gateway switch `gateway.recovery`, per policy) an effect request for an identity the gateway never forwarded, with a sequence at or below the known floor, is not published: a durable sealed record (fsync), answered STOPPED; that identity is never forwarded later | GP (the gateway is the only publisher on the cell's command topic), the gateway's durable record |
| REC-1 | the authority learns the floor from `claim_floor` in any owner answer (dispatch replies of every status, receipt reads, stops, fence answers); durable per domain, never falls; trace OWNER_FLOOR | nothing (liveness only) |
| REC-2 | the sequence advance: a new identity's owner sequence is the client's sequence plus a durable offset; when that is at or below the known floor the offset grows so that it lands at floor + 1; owner sequences stay strictly increasing (and the client's own monotonicity is checked as before); the intent hash is over the owner sequence; a replay of the same request maps to the same stored intent; trace SEQUENCE_ADVANCED | nothing (a stale floor only makes a command refused) |
| REC-3 | the anchor of a complete fence after a reboot: with `claim_floor` F in the answer, the reported level is compared with the sum of the values of the identities APPLIED with F < sequence <= cutoff, not with the whole anchor; a match commits the **evidence anchor** of release 086 (never the reported level), a mismatch is an evidence conflict and aborts as in 086; F = 0 gives the 086 comparison; additive histories only (otherwise 086); FENCE_ANCHOR_CHECK outcomes MATCH_FLOOR / MISMATCH_FLOOR | FC, DR, CUT for safety; FB for the check |
| REC-4 | the window: an identity that a complete fence answer listed UNCERTAIN (rule R5') lies at or below a cutoff the owner installed durably before answering, so it cannot take effect any more; it leaves the window of unresolved commands. **REC-4 changes only the window accounting of identities at or below a durably installed cutoff; their reservation (budget) is kept; the bound argument does not use the window** | CUT, FC |

Premises: **FC** (the gateway's fence answer lists every identity at or below the cutoff it forwarded; its forward log is
written with fsync before the first publish); **GP**; **DR**; **CUT** (the cutoff is persisted before the fence reply;
afterwards nothing at or below it takes effect); **FB** (with the eviction guard, no identity at or below the current floor
takes effect in this or a later boot, and the reported level counts exactly the identities above the floor applied in this
boot). The safety of REC-3 rests on FC, DR and CUT, as for the evidence anchor of release 086: the committed anchor is the
previous anchor plus the APPLIED values at or below the cutoff, which is the true state when every effect at or below the
cutoff is listed and true and nothing unlisted takes effect later; the reported level is never committed. The
floor-relative check is the detector (valid under FB): it still detects inconsistencies among the identities applied since
the boot and violations of FB itself; what it no longer detects is an inconsistency confined to identities applied before
the boot. With `-fence-anchor owner-state` the steps REC-1, REC-2 and REC-4 run and the reported level is adopted: the
negative control (arm (i); FENCE_ANCHOR_CHECK MISMATCH_OWNER_STATE_ADOPTED when the level is not the evidence anchor).

**What the recovery cannot resolve.** An identity forwarded before the reboot without a cached terminal receipt (in flight
at the crash, published while the firmware was down, or published after the restart before the gateway learned the floor)
and at or below the floor is refused by the firmware for good: nothing distinguishes "took effect before the crash" from
"never took effect". It stays UNCERTAIN with its budget reserved (REC-4 frees its window slot). Expected: about 0 per
quiet reboot; about 1.5 identities (4.5 units) per random reboot (0.5 commands/s during about 3 s of downtime).

**The arms.** Every arm uses rule R5' (per-identity intake), so the arms differ only in the anchor and the recovery:
(i) `NEG_ZEPHYR_F_BOOT` / `EVICON_FI_REC_OS` (`-reboot-recovery -fence-anchor owner-state`, gateway seal on), the negative
control, labelled `NEG_` so that the safety total classifies it as a control (coordinator decision D5; without REC-2 the
claimed range fills the window before any post-reboot fence completes and the owner-state anchor would never be exercised);
(ii) `ZEPHYR_F_BOOT` / `EVICON_FI` (release 086: the evidence anchor, no recovery, no seal); (iii) `ZEPHYR_F_BOOT` /
`EVICON_FI_REC` (`-reboot-recovery`, gateway seal on).

### 2.4 Analysis

`evaluation/pairs_ext083.py --plan evaluation/prereg/PREREG_extra_087.md` (frozen since plan 083) decides the
```pairs-spec``` block through the frozen `pairs_report.py` and the ```pairs-ext``` block (policy contrasts, the RB safety
rule); Holm within each family; the two blocks' families are disjoint. `evaluation/sh087.py` reports E-SHR,
`evaluation/rb087.py` E-RB and the check RB-C1. `evicon_review.py report` runs all of them with the earliest complete run of
each campaign among its sources.

## 3. Hypotheses, checks and decision rules

Tests on per-episode paired differences (to minus from), clean fulfilment in pp of offered, Student t, alpha = 0.05;
TOST for equivalence; Holm within each family. Absence, detection, safety and check rules have no p-value and are in no
family. **The 0.80 rule** (as in plans 083-086): a paired-difference rule is a confirmatory hypothesis only if its power
under the testbed's own spread (section 5), at the predicted mean and with Holm in its family, is at least 0.80; otherwise
an estimand. Applied before freezing: SHR-1 to SHR-4 have power 1.00 and RB-1 0.96 (n = 6) under their primary
templates; all five are hypotheses. The five safety counts are bound violations, duplicate effects, logical duplicates, false STOPPED and false
APPLIED. Block: S = ```pairs-spec```, X = ```pairs-ext```, R = `rb087.py`.

**Rule IDs.** The rules of item A carry the prefix SHR (plan 086's SH-A1 to SH-A4 and SH-E1 to SH-E6 exist); the rules of
item B the prefix RB; the recovery steps are REC-1 to REC-4. Every ID of this plan (rules, runs, campaigns, estimands) is
checked against every ID of the plans 066-086 and of the 086 final report's claims list (test K1 of `test_release087`).

| ID | Block, family | Hypothesis or check | Decision rule | Prediction |
|---|---|---|---|---|
| **SHR-1** | X, shr-fw | On the cutoff firmware at W = 6, the documented shadow loop at its 10 s timeout is equivalent to EviCon (within 3 pp) | `equivalent 3`, run SHRz6, ZEPHYR_CUTOFF_DIRECT, EVICON -> SHADOW_RECONCILE, `clean_pp` | -1.0 |
| **SHR-2** | X, shr-fw | ... at EviCon's 1 s cadence | `equivalent 3`, run SHRz6, EVICON -> SHADOW_RECONCILE_1S | 0.0 |
| **SHR-3** | X, shr-minio | On MinIO's conditional PUT at W = 6, the loop at 10 s is equivalent to EviCon | `equivalent 3`, run SHRm6, MINIO_COND, EVICON -> SHADOW_RECONCILE | -1.0 |
| **SHR-4** | X, shr-minio | ... at 1 s | `equivalent 3`, run SHRm6, EVICON -> SHADOW_RECONCILE_1S | 0.0 |
| **SHR-A1 to SHR-A6** | S | Every arm of item A is safe | `absent`, the five safety counts: SHRz6 EVICON, SHADOW_RECONCILE, SHADOW_RECONCILE_1S (ZEPHYR_CUTOFF_DIRECT); SHRm6 the same (MINIO_COND) | ABSENT (by P-i) |
| **SHR-S** | S | Safety in every cell of SHRz6 and SHRm6 (no exempt contract) | `safety` | MET |
| **RB-1** | X, rb | With owner reboots, the recovery raises clean fulfilment over release 086 by at least 15 pp (half the prediction) | `at_least 15`, run RBz6, ZEPHYR_F_BOOT, EVICON_FI -> EVICON_FI_REC, `clean_pp` | +30 |
| **RB-A1** | S | The recovery (arm (iii)) is safe across owner reboots | `absent`, the five safety counts, RBz6 ZEPHYR_F_BOOT / EVICON_FI_REC | ABSENT |
| **RB-A2** | S | Release 086 (arm (ii)) is safe across owner reboots | `absent`, the five safety counts, RBz6 ZEPHYR_F_BOOT / EVICON_FI | ABSENT |
| **RB-D1** | S | The negative control (arm (i)) violates the bound | `detects` bound_violations, RBz6 NEG_ZEPHYR_F_BOOT / EVICON_FI_REC_OS | DETECTED |
| **RB-S** | X | Safety in every cell of RBz6 except the negative control | `safety`, run RBz6, exempt NEG_ZEPHYR_F_BOOT | MET |
| **RB-C1** | R (check) | The floor-relative check never fails in the recovery arm | the FENCE_ANCHOR_CHECK outcomes MISMATCH_FLOOR in RBz6 ZEPHYR_F_BOOT / EVICON_FI_REC: 0 | 0 |

In all, 17 rules: 5 paired-difference hypotheses (three families), 8 absence rules, 1 detection rule, 2 safety rules, and
the check RB-C1.

**Prediction basis.** Item A: on these schedules both EVICON arms of plan 086 sit at the same budget-limited level (SR:
76.8 % firmware {F}, 76.6 % MinIO conditional); the 086 loop matched EVICON on the queue channel (SHq: -0.03 pp, SD 0.08);
its +9.3 pp on SHd came from lost commands that EVICON on K2 (no stop or fence, resend cut at 30 s) could not resolve while
the loop re-published them past the cut; on the firmware and MinIO EVICON has a fence or a stop for them. The 10 s loop
keeps a lost command in a window slot up to 10 s longer than EVICON's 1 s sweep: -1. Item B: after a reboot arm (ii)
admits nothing that takes effect until the device's sequence passes the floor (the claim mark, up to 64 identities
ahead), which within a cell happens for few devices; arm (iii) resumes within seconds and stays near SRz6's
budget-limited 76.8 %. The design predicted +50 (arm (ii) about 20 %); the simulated testbed's full-length episode
(section 9: episode 0 at 300 s + 60 s with the reboot ranges of this plan) gave arm (ii) 44.5 %, arm (iii) 74.9 %,
+30.4, because the first reboot falls at 110-130 s and arm (ii) keeps what it admitted before it: +30, and the rule's
margin is half of it (15; written after that episode, before freezing). Arm (i): DETECTED, with less confidence than
the design assumed (TLC: the owner-state anchor after a reboot violates the bound; the conformance test: 49 units against
a budget of 30). The control needs a complete fence after a reboot (one whose batch lies above the floor), which adopts
the reset level, and enough arrivals after it for the under-count to carry the device past its bound; in the simulated
testbed one of two cells had such fences (3 adoptions, under-counts of 63 to 126 units, but 180-s cells never approach
the bound) and the full-length cell had none.

**Interpretation fixed in advance.**
- **SHR-1 to SHR-4 MET**: on our firmware and on MinIO, a per-identity shadow loop (reported state = the per-identity
  receipt, re-publish of the same identity, no release) reaches EviCon's clean fulfilment within 3 pp at W = 6, at the
  documented 10 s timeout and at EviCon's 1 s cadence. The paper states that these owners meet the loop's premise by
  design (the firmware's eviction rule, MinIO's conditional create) and that there the loop is as good as EviCon; EviCon's
  distinct value is then where suppression is bounded or absent and where capacity must be released; the cost of never
  releasing is reported from E-SHR (stranded identities and budget).
- **NOT MET, interval wholly below -3**: the loop costs fulfilment on that owner (the cadence, when only the 10 s arm
  fails; never releasing, when both fail); reported with the estimands. **NOT MET, interval wholly above +3**: the loop is
  better there; the paper says so. **NOT MET, interval across a margin**: inconclusive at n = 8 (the rules' limit under the
  pessimistic template, section 5).
- **SHR-A1 to SHR-A6 PRESENT, or SHR-S NOT MET** (the loop violated safety on a real owner): the premise P-i does not hold there. On the
  firmware the paper must withdraw condition (i) as a design premise of the firmware, move every firmware cell published at
  QoS 1 (the cutoff arms of 083-085, the {F} arms of SR, the ablation, the firmware experiment) from the Theorem-1 subset
  to the dagger subset, and say that QoS 1 to the firmware is not justified; on MinIO, MINIO_COND's D_h premise and the
  MinIO results' safety basis are restated as measured only (h >= 600 s), not structural. The session stops (section 6);
  the violating identities are reported.
- **RB-1 MET**: with the recovery the firmware keeps admitting after owner reboots without an operator, safely (with
  RB-A1). **NOT MET** (the Holm-adjusted p is not below 0.05): the recovery restores less than predicted; the paper reports
  the measured gain with E-RB. **NOT INTERPRETABLE** (more than 10 % INVALID cells after the re-run): said so.
- **RB-A1 or RB-A2 PRESENT, or RB-S NOT MET**: a safety failure across reboots; the session stops; the paper may not claim
  safety across reboots for that arm; the cells and traces are reported.
- **RB-D1 DETECTED**: owner-state anchoring after a reboot violates the bound on the testbed; the evidence anchor with the
  floor-relative check is what makes the recovery safe. **NOT DETECTED**: report arm (i)'s post-reboot complete fences
  (FENCE_ANCHOR_CHECK MISMATCH_OWNER_STATE_ADOPTED) and the plant's margin to the bound: with none, the control was not
  exercised; with some, the testbed was not sensitive; either way the paper does not cite item B as a negative-control
  demonstration (TLC and the conformance test remain).
- **RB-C1 > 0**: FB does not hold as designed (or the gateway reported a wrong floor); the cells are listed; the recovery's
  check is not usable as described, and the paper says so (its safety rests on FC, DR and CUT, not on the check).

## 4. Estimands (reported with intervals, no claims)

| ID | Estimand |
|---|---|
| E-SHR | Item A (`sh087.py`), per owner, the three arms and the contrasts EVICON -> SHADOW_RECONCILE, EVICON -> SHADOW_RECONCILE_1S, SHADOW_RECONCILE -> SHADOW_RECONCILE_1S (the cadence contrast), per-episode differences with Student t 95 % intervals: clean and admitted fulfilment; owner calls per offered request (dispatch attempts, queries, stops, fences and the loop's reads of the reported state); the loop's reads by outcome (APPLIED, STOPPED, NOT_FOUND, UNREACHABLE, UNCERTAIN) and re-publishes; unresolved identities at the end and the budget they hold (the reviewer's question); stranded capacity-time; mean time to a terminal outcome; late effects; window defers; on the firmware the broker's packets and the firmware's flash writes per offered request (the 086 counters) |
| E-SHR-X | Across runs on byte-identical schedules (informational): each SHRz6 arm against SRz6's ZEPHYR_F_DIRECT / EVICON cells, each SHRm6 arm against SRm6's MINIO_COND / EVICON cells (`sh087.py`), and EVICON on {D_h, Q_A, F} against EVICON on {F} (```pairs-spec``` estimands) |
| E-RB | Item B (`rb087.py`), per arm and per reboot kind (quiet, random): the device's admissions from the restart to its next reboot or the end of the cell (count, pp of its arrivals in that interval, and those that took effect), the time from the restart to the first admission and to the first admission that took effect; identities left unresolved at the end and the budget they hold, by whether the plant shows an effect; clean fulfilment; the recovery's trace counts (OWNER_FLOOR, SEQUENCE_ADVANCED, FENCE_ANCHOR_CHECK outcomes, EVIDENCE_CONFLICT, BARRIER_ANCHOR_COMMITTED) and the gateway's seals; arm (iii) against SRz6's ZEPHYR_F_DIRECT / EVICON cells (episodes 0-5, the cost of the reboots, across runs) |
| E-GATE | The two measured contracts of the firmware gate (the measured build and the boot-report build: capabilities, horizons, the probes' outcomes), side by side in the run's contract map |

## 5. Sample size

**Templates** (`evaluation/power_extra087_testbed.py`; per-episode differences copied into
`sim_predictions/extra087_templates.csv` with the SHA-256 of their source, the 086 final report's
`report/pairs/<plan>/cells.csv`): the primary template of item A is the within-run difference of two guarded policies on
the same owner and schedules (plan 085: PMz6 EVICON -> PERIODIC_FULL, SD 0.245 pp, for SHR-1 and SHR-2; PMm6, SD 0.116 pp,
for SHR-3 and SHR-4), with sensitivity spread factors 3 and 10; the pessimistic template is SHd of plan 086 (the loop
against EVICON on K2 with crash orphans, SD 9.25). RB-1's template is SRz6's within-run firmware contrast (SD 9.15), spread
factors 1 and 2. Each rule as `pairs_report.py` decides it (TOST or one-sided t, Holm with the other member of the family at
its prediction), 4,000 resamples, seed 87 (`sim_predictions/extra087_power.csv`):

| Rule | Prediction | Template (SD) | n = 8 | n = 6 | spread x3, n = 8 | spread x10, n = 8 / 6 | true mean 1 pp lower, x10, n = 8 | pessimistic (SHd), n = 8 |
|---|---|---|---|---|---|---|---|---|
| SHR-1 | -1 | pm_fw (0.245) | 1.000 | 1.000 | 1.000 | 0.658 / 0.444 | 0.287 | 0.000 |
| SHR-2 | 0 | pm_fw (0.245) | 1.000 | 1.000 | 1.000 | 0.876 / 0.557 | 0.595 | 0.000 |
| SHR-3 | -1 | pm_minio (0.116) | 1.000 | 1.000 | 1.000 | 0.990 / 0.831 | 0.736 | 0.000 |
| SHR-4 | 0 | pm_minio (0.116) | 1.000 | 1.000 | 1.000 | 1.000 / 0.993 | 0.985 | 0.001 |

| Rule | Template (SD) | +30, n = 4 / 6 / 8 | +30, spread x2, n = 4 / 6 / 8 | +25, n = 6 / 8; x2, n = 6 / 8 | +20, n = 6 / 8 |
|---|---|---|---|---|---|
| RB-1 (`at_least 15`) | srz6 (9.15) | 0.641 / 0.958 / 0.991 | 0.535 / 0.507 / 0.674 | 0.734 / 0.844; 0.423 / 0.439 | 0.411 / 0.440 |

**Episodes.** Item A: **8** (the SR count; all four rules pass the 0.80 rule already at n = 6 under the primary template,
and n = 8 is markedly better at spread x10). The pessimistic template is the rules' limit: if the loop made per-episode
differences as variable as on K2 with orphans, equivalence within 3 pp could not be shown at n = 8 (the result would read
"inconclusive"). The 1 s arm is added (its rules pass the power rule and the hours fit, section 6). Item B: **6** (power
0.96 at the prediction and 0.73 at +25; n = 4 gives only 0.64 at the prediction; at twice the spread 0.51, and 0.67 at
n = 8, which would add 6 cells and 0.75 h), which also gives 48 reboots per arm. Absence, detection, safety and check rules have no power requirement. **Windows: W = 6 only** (coordinator
decision D4): on these owners EVICON's window binds at W = 6 only (SR window defers per cell: firmware 13.0, MinIO 0.75 at
W = 6, 0 unbounded), so a never-released identity costs a window slot and budget at W = 6 and only budget without a window;
W = 6 shows both. Runs without a window are an optional second session (the run guide), not part of this plan.

## 6. Exclusions, stopping and order

- INVALID cells are excluded and listed (a fault, a reboot included, could not be applied, a process died, a firmware
  process is not running at the end, the truth could not be read). An INVALID cell is re-run once by resuming the
  campaign; if it is INVALID again it stays excluded. A run with more than 10 % excluded cells is not interpretable and
  its hypotheses are not decided.
- No interim analysis: the rules are decided only when every cell of a run is done. A campaign may be split over several
  sessions (resume).
- **Order** (one review run of release 087, one overnight session, `run087.sh`, which resumes where it stopped when it is
  started again): before the session, on VM-C: `bash deploy/setup/build_iot.sh start zephyr_boot` until BOOT_BUILD_OK
  (about 0.2 h; the measured build is not rebuilt); `preflight build verify distribute --new --quick` (0.2 h); the firmware
  gate `measure zephyr zephyr-boot` (1.15 h; both builds; every firmware campaign refuses to start without its build's
  measured contract in this review run); `campaign expSHR_zephyr_w6` (3.0 h), `expSHR_minio_w6` (2.9 h) (item A first: an
  unplanned violation there, a P-i failure, stops the session before item B, which it would undermine); `campaign
  expRB_zephyr_w6` (2.25 h); `report --archive /root/evicon-archive --from <078> --from <083> --from <084> --from <085>
  --from <086>` (0.95 h, the data freeze); `bundle --incremental` after new work. About 10.5 h in all (the budget is 12 h).
  If the night is short: `--until HH:MM` stops before a step that would not end by then, and the same command later
  resumes; the fallback that keeps one night is to drop nothing but to split: (1) stages, gate, SHRz6, SHRm6 (about 7.3 h);
  (2) RBz6, report, bundles (about 3.5 h), in the same review run.
- **Not run.** The data freeze is this plan's final `report`. A campaign not completed by then is NOT RUN: its rules are
  reported NOT RUN, its estimands are left out.
- **Safety stop.** A safety violation in any cell of this plan outside the negative control (arm (i)) stops the session
  (the operator script stops and bundles); it is reported with the cells and traces. A violation in arm (i) is the
  control's planned outcome (the driver's planned negative control: every `NEG_` contract of a real-owner campaign).
- **Earlier plans.** The final report also decides every earlier plan; their 185 verdicts must equal the 086 final report's
  (`evaluation/verdicts087.py` lists every difference; a difference is logged as a FAIL and reported). The release-087
  authority runs only this plan's cells; the earlier plans' verdicts come from their archived cells.
- The earliest complete run of each config is the preregistered one.

## 7. Frozen files

SHA-256 at freezing time (`scripts/freeze_prereg.py`); the driver checks them before running. Every file frozen by
`PREREG_extra_086.md` is frozen here again with the same SHA-256 (none changed in release 087), with that plan itself,
through the ```frozen-extra``` block; added: the runner registration of the 087 policies (`ext087.py`), the firmware and
MinIO runners of this plan (`campaign_zephyr_ext087.py`, `campaign_real_ext087.py`), the 087 gateway, the boot-report
firmware application and its build script, the testbed power script with its templates and output, the claim-ID snapshot
of the uniqueness check, and the estimand scripts `sh087.py` and `rb087.py` (which decide nothing but the check RB-C1).

The Go code is built from release 087 (the run's `source.json` records its source). Since release 086 the Go code changed
in these places only: `buildinfo.Release`; the reboot recovery, off unless `-reboot-recovery` is given
(`internal/runtime/reboot_recovery.go`; its hooks in `internal/runtime/authority.go`: the dispatcher's owner-report hook,
the sequence advance before the monotonicity check, the client-sequence record, the window exclusion, the floor-relative
check of a complete fence; the floor of a stop answer in `internal/runtime/evicon.go`; `internal/journal/owner_floor.go`,
whose tables are created on first use only; the optional field `claim_floor` of `protocol.Receipt` and
`protocol.BarrierReceipt`; the optional `OwnerReport` hook of `internal/outbox/dispatcher.go`; the flag in
`cmd/evicon-authority/main.go`); and, in `internal/runtime/fence_anchor.go`, the FENCE_ANCHOR_CHECK outcomes MATCH_FLOOR,
MISMATCH_FLOOR and, under `-fence-anchor owner-state` with a floor, MISMATCH_OWNER_STATE_ADOPTED (trace only). For every
other policy, admission, the resolver and the dispatcher are unchanged; the simulator is unchanged. The measured firmware
and the 077 gateway are unchanged.

```frozen-extra
evaluation/pairs_report.py
evaluation/cadence_report.py
evaluation/policy_compare.py
evaluation/latency_control.py
evaluation/campaign_ext077.py
evaluation/campaign_real.py
evaluation/campaign_zephyr.py
evaluation/campaign_zephyr_ext.py
conformance/reference/mqtt_owner_gateway_ext.py
conformance/reference/mqtt_owner_gateway.py
conformance/reference/s3_owner_gateway.py
conformance/reference/s3_client.py
conformance/reference/_health.py
conformance/evicon_probe/faults.py
conformance/evicon_probe/adapters/mqtt5.py
conformance/evicon_probe/probes.py
conformance/evicon_probe/rubric.py
conformance/configs/zephyr-mqtt.json
conformance/configs/zephyr-mqtt-plain.json
conformance/configs/zephyr-mqtt-stop.json
firmware/zephyr-cutoff/src/main.c
firmware/zephyr-cutoff/src/evc_core.c
firmware/zephyr-cutoff/src/evc_core.h
firmware/zephyr-cutoff/src/evc_proto.c
firmware/zephyr-cutoff/src/evc_proto.h
firmware/zephyr-cutoff/Kconfig
firmware/zephyr-cutoff/prj.conf
firmware/zephyr-cutoff/CMakeLists.txt
firmware/zephyr-cutoff/boards/native_sim.conf
firmware/zephyr-cutoff/boards/native_sim_native_64.conf
experiments/owners/minio/meas-minio-plain-070.json
experiments/owners/minio/meas-minio-cond-070.json
experiments/owners/zephyr/pred-zephyr-plain-074.json
experiments/owners/zephyr/pred-zephyr-cutoff-074.json
experiments/owners/zephyr/pred-zephyr-f-075.json
experiments/owners/zephyr/pred-zephyr-stop-074.json
experiments/owners/zephyr/pred-zephyr-qa-075.json
experiments/owners/zephyr/pred-zephyr-qs-075.json
evaluation/sim_expC3.py
evaluation/sim_predictions/expC3_075_summary.csv
evaluation/sim_predictions/expC3_075_power.csv
evaluation/power_expC3_testbed.py
evaluation/sim_review077.py
evaluation/sim_predictions/review077_summary.csv
evaluation/sim_predictions/review077_power.csv
evaluation/sim_sensitivity077.py
evaluation/sim_predictions/sensitivity077_summary.csv
evaluation/power_review077_testbed.py
evaluation/late_attribution.py
evaluation/campaign_zephyr_ext077.py
evaluation/campaign_tb.py
conformance/reference/mqtt_owner_gateway_ext077.py
conformance/reference/tb_owner_gateway.py
conformance/reference/mqtt_device.py
conformance/reference/qos_dup_check.py
conformance/reference/zephyr_auth_check.py
conformance/evicon_probe/adapters/thingsboard.py
conformance/configs/thingsboard-rpc-r0.json
conformance/configs/zephyr-mqtt-mcu.json
firmware/zephyr-mcu/CMakeLists.txt
firmware/zephyr-mcu/Kconfig
firmware/zephyr-mcu/prj.conf
firmware/zephyr-mcu/boards/mps2_an385.conf
firmware/zephyr-mcu/boards/mps2_an385.overlay
firmware/zephyr-mcu/boards/nrf52840dk_nrf52840.conf
firmware/zephyr-auth/CMakeLists.txt
firmware/zephyr-auth/Kconfig
firmware/zephyr-auth/prj.conf
firmware/zephyr-auth/src/main_auth.c
firmware/zephyr-auth/src/evc_auth.c
firmware/zephyr-auth/src/evc_auth.h
evaluation/sim_theta077.py
evaluation/sim_predictions/theta077_summary.csv
evaluation/pairs_ext083.py
evaluation/ext083.py
evaluation/campaign_ext083.py
evaluation/campaign_real_ext083.py
evaluation/campaign_tb_ext083.py
evaluation/sim_extra083.py
evaluation/power_extra083_testbed.py
evaluation/sim_predictions/extra083_summary.csv
evaluation/sim_predictions/extra083_effects.csv
evaluation/sim_predictions/extra083_power.csv
experiments/sim/contracts083/sim-tb-083.json
evaluation/prereg/PREREG_extra_083.md
evaluation/ext084.py
evaluation/campaign_ext084.py
evaluation/campaign_real_ext084.py
evaluation/campaign_zephyr_ext084.py
conformance/reference/delivery_binding.py
experiments/owners/bindings/delivery-binding-084.json
evaluation/sim_extra084.py
evaluation/power_extra084_testbed.py
evaluation/sim_predictions/extra084_summary.csv
evaluation/sim_predictions/extra084_effects.csv
evaluation/sim_predictions/extra084_power.csv
evaluation/sim_predictions/extra084_calibration.csv
evaluation/prereg/PREREG_extra_084.md
evaluation/ext085.py
evaluation/campaign_zephyr_ext085.py
evaluation/campaign_real_ext085.py
conformance/tb_rpc_checks.py
conformance/reference/mqtt_device_tbcheck.py
evaluation/power_extra085_testbed.py
evaluation/sim_predictions/extra085_templates.csv
evaluation/sim_predictions/extra085_power.csv
evaluation/prereg/PREREG_extra_085.md
evaluation/power_extra086_testbed.py
evaluation/sim_predictions/extra086_templates.csv
evaluation/sim_predictions/extra086_power.csv
evaluation/sr086_schedules.py
evaluation/sim_predictions/extra086_schedules.csv
evaluation/sr086.py
evaluation/ext086.py
evaluation/campaign_ext086.py
evaluation/campaign_zephyr_ext086.py
evaluation/nvs_flash.py
evaluation/cost086.py
conformance/reference/mqtt_device_qos2.py
conformance/evicon_probe/adapters/mqtt5_qos2.py
conformance/configs/mosquitto-rpc-qos2.json
conformance/reference/qos2_expiry_check.py
evaluation/prereg/PREREG_extra_086.md
evaluation/ext087.py
evaluation/campaign_zephyr_ext087.py
evaluation/campaign_real_ext087.py
conformance/reference/mqtt_owner_gateway_ext087.py
firmware/zephyr-boot/CMakeLists.txt
firmware/zephyr-boot/Kconfig
firmware/zephyr-boot/prj.conf
firmware/zephyr-boot/boards/native_sim.conf
firmware/zephyr-boot/boards/native_sim_native_64.conf
firmware/zephyr-boot/src/evc_boot_report.c
firmware/zephyr-boot/src/evc_boot_report.h
deploy/setup/build_zephyr_boot.sh
evaluation/power_extra087_testbed.py
evaluation/sim_predictions/extra087_templates.csv
evaluation/sim_predictions/extra087_power.csv
evaluation/sim_predictions/extra087_claim_ids.csv
evaluation/sh087.py
evaluation/rb087.py
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
d518a157cba3e013b66e0e10d7442a559381e5bdadfdd0fa537f1dcfca36de64  evaluation/policy_compare.py
e0c27967a9403464c871f6b4cdd7bc53dbc85627ad37eaaf13ded0316c0e620e  evaluation/latency_control.py
39a410da83d6dd03e33c6bceffa42cb4d5ad27a1fe65a7a7c7853f237ea6ae05  evaluation/campaign_ext077.py
65eb699cf290296595eb084c031570a9edcd5004a2869de15b35c1f1ef958557  evaluation/campaign_real.py
15c46ffd982baacecbec4fce2ca39e7c6ba16fc3148bfa8fb0032bc4126947db  evaluation/campaign_zephyr.py
82a9d4f92cdc48940e262504a770a70e004d3f2f39975a98f65dde4de1b33b8c  evaluation/campaign_zephyr_ext.py
a134ecd20a151bfffa4f44867674937150e7490595bca3a28ac2f77c6783cd23  conformance/reference/mqtt_owner_gateway_ext.py
211dc8580291a6ce2b6cbf1e9033ecbf6db81a23f4ea5cc36310a8aa07153b9e  conformance/reference/mqtt_owner_gateway.py
81506987d1b347fdb46a0cbbe0e7e457309414329a055cd6dc92a04afbc4c226  conformance/reference/s3_owner_gateway.py
a2314c70898999dc16892ab9815c002d972a54826004d75d7e1ba7748a9e6d4c  conformance/reference/s3_client.py
ac7f3cad94d465453b266f9eadd423275c75451714ef064d5b3adf52b3ef655d  conformance/reference/_health.py
16571e8004a5fc394dfb4a21c25bcc952e6736176f4b1ff62d7a43c4e3e903a8  conformance/evicon_probe/faults.py
4a2c0b57cce7789658804819a3c2c387ed6f5b3deb7a978eb6fb29d505a501a1  conformance/evicon_probe/adapters/mqtt5.py
35f2552a7f6ba243e74a4f41d9c3233a8a56fcc3491a70aeeee1b444783dae75  conformance/evicon_probe/probes.py
ab366e75137b452c9c6a457c4a2399b015c8079f5ca5ea2d75db4c89ee71d72f  conformance/evicon_probe/rubric.py
9fce6819d5f860c4f343dee5354736ee24ede3f9a9208e3fd2df69105be594b1  conformance/configs/zephyr-mqtt.json
fa90d3478844503b8742291204212beb0d3fe7e4e3db4563080f5ef22062be4c  conformance/configs/zephyr-mqtt-plain.json
15735bd1bd31040f3904a96d09efaf83f59653a608f12cf76ca786e50c9d8fc5  conformance/configs/zephyr-mqtt-stop.json
6dcb257e6bdd4bbb24db92666a850acff76120f97418d4f39b4f4b9bcb8dc08a  firmware/zephyr-cutoff/src/main.c
b0b0a23b8a75699c8508f79c965ae30c4fcaeffcd6e0e72e0038c3af3908d371  firmware/zephyr-cutoff/src/evc_core.c
d4cab58a0c5c88c4fae4a63837add862cd383947d67b05aa2ba6d17e0bd4f647  firmware/zephyr-cutoff/src/evc_core.h
db075ae7ca471cd754e5bc653c9ae7f4d8219288ac301d40939cd6d8ff4e79d1  firmware/zephyr-cutoff/src/evc_proto.c
ff525dc620a20b48e97d40a90ba79db8c9088accbe49b936e14cbc881d7d1982  firmware/zephyr-cutoff/src/evc_proto.h
473360c3058bd8341b2bc00d6fd3d6f57f2520da643268499019169a20a3347f  firmware/zephyr-cutoff/Kconfig
4999fdc6ecd47ed875847ef7f8b8649c87bf1c3a72e650549fd546f0cfec99aa  firmware/zephyr-cutoff/prj.conf
224b9a65e66babd878e1124f06018d8b4b99dc75ec0f46718bee213749a91ec7  firmware/zephyr-cutoff/CMakeLists.txt
6d5a779db9b05857f8832d53791f2b801fe9a7188929dc633d2fd8dd53220784  firmware/zephyr-cutoff/boards/native_sim.conf
1cb500fef6054174ffb46e2acd2f09af0e4c166addd7bd76a4f291bcd1409ee9  firmware/zephyr-cutoff/boards/native_sim_native_64.conf
5215bebd11b54436b37063c8bcfd925327da389f9c75daf541bb68b0587d3d59  experiments/owners/minio/meas-minio-plain-070.json
dbe4797d7c73cf99049931ec1c5b97b1a657bd7d407b34e5f02b9049986017eb  experiments/owners/minio/meas-minio-cond-070.json
4c7f4f704e78d5c4887dd0c9dc0853d135cd3a5c5fdb6c592697712a534416a5  experiments/owners/zephyr/pred-zephyr-plain-074.json
69b280454fe66f8f2def5acdb95b2c6e47378a3059a9f3eb670b2ac8bd589423  experiments/owners/zephyr/pred-zephyr-cutoff-074.json
827ad1ae3501b73be812e0294363e1e8fe67c49ed9ea953973f180762aa87690  experiments/owners/zephyr/pred-zephyr-f-075.json
424f40ed02618c8da661d6000f3f66f5dd505e8f3dd461c0df8d3805320ef5fe  experiments/owners/zephyr/pred-zephyr-stop-074.json
417c1d3fcce8a724f77114a16d81f7c021acc39df35faf6427ca6e657f076993  experiments/owners/zephyr/pred-zephyr-qa-075.json
6f1cba944074e50decbe95f898dca7411f57a9d36d517663695e83abbae7ab89  experiments/owners/zephyr/pred-zephyr-qs-075.json
b7d060157317ad35b798123490530ccce78c92e66bc1f583518ca18f3e43368e  evaluation/sim_expC3.py
351dc86ccde34eee15a51a75df83d92a4fbba0ab10138a4cda5a238257a5e7f7  evaluation/sim_predictions/expC3_075_summary.csv
bd1a6b2cd51dc48306b047e91ec573514b8a8b808edb5084a34d7b7a75155fbf  evaluation/sim_predictions/expC3_075_power.csv
3dc2c2f7bfa818c2146a82708725d4db0a3b03d665a68a49542ef506808baee4  evaluation/power_expC3_testbed.py
e3374cae6c6f3f1dee7642cfb595b8dd2f23623b8b570957183d5b77dec194c4  evaluation/sim_review077.py
fd82e23455a282f75ba6118b4a16fbda8f6cd0920acca847b88181d5edb8f738  evaluation/sim_predictions/review077_summary.csv
d77b9617e20dbe4d7c91f981ba3004a0e96e36a9999614373e6d3f65e386d0e8  evaluation/sim_predictions/review077_power.csv
d41be299f833b056b39346fb06c032e8f0e25158aae5016220640cdf56bc67dd  evaluation/sim_sensitivity077.py
9e5191abe6cc26987a74bd0463230a93fd1aaeebc3d71953ab16cadb266b083b  evaluation/sim_predictions/sensitivity077_summary.csv
425e37a34890777a4609263ade04f87dcf7104c87b2276ef4e99082425439d0d  evaluation/power_review077_testbed.py
f8077caa11e3d582ed2ff846a9482023b1b328e63a6d2302c5e4aaf86d38a408  evaluation/late_attribution.py
c28e85309cabea1df4fce7a9382e5c86b0682d60b944d3e8395d7267b90644d5  evaluation/campaign_zephyr_ext077.py
917feaa3d964587af256cd12abbaefa609e836abc4e854b460e4c8a54dc1d074  evaluation/campaign_tb.py
768ac1cc78b3c2a669ff64a2ba9df2f497bcfbfbc4881a4dd14fa212dc76e1ce  conformance/reference/mqtt_owner_gateway_ext077.py
aabf42a23350634563fdb01eeca3ee9742db9a20d311e3cda2ffd04d6ad79ee6  conformance/reference/tb_owner_gateway.py
53957cf6f6aaaaa72f607fa4af74c1b373c8e39304db4820c031056cb4ee9813  conformance/reference/mqtt_device.py
5a45d5a98208081115c100981d8254707a9d1cf36d0522954045b427f5fab653  conformance/reference/qos_dup_check.py
f00efde4083569081f5f7c6979f2720adfb1f708b0eecaba048c60d887690e44  conformance/reference/zephyr_auth_check.py
24821287a662cfa8ca35403883d342adeeea99c8b4000c9fef2c42af725a717b  conformance/evicon_probe/adapters/thingsboard.py
1cb10d8e5cb762fcd21bc198eafc97dc8fb8dbf1d6ac0d752bea1e49267b34f6  conformance/configs/thingsboard-rpc-r0.json
5a4e715730ad05a187a63830fba249fd5fc84a7ad4f7435bf89b6a35944d51b6  conformance/configs/zephyr-mqtt-mcu.json
530590774973bca3634f97a363f6c67cd4910c8e8de39c071821d6ff0a54ae21  firmware/zephyr-mcu/CMakeLists.txt
73f6a1cb8607aa1731a9d99d55d649ecd8cc02a3bb116aafe500e0967693227e  firmware/zephyr-mcu/Kconfig
fbdb3dc4647a8648e94dd7ade7da4d5c69897b5d43d3cbb2d6bd7e3c9a004714  firmware/zephyr-mcu/prj.conf
e9ec5c7a03564e018dd5fb602b76dde394728816f4900977b774f4478e131e90  firmware/zephyr-mcu/boards/mps2_an385.conf
fe7f30175c18cbdb25a4186bd0e2e0565562bf9ea154865dd61df8c30adc1d55  firmware/zephyr-mcu/boards/mps2_an385.overlay
5cdab9552fe8d677d8944beffbf24dc461d61d3e2f9d6d8274f2099ee7726fd5  firmware/zephyr-mcu/boards/nrf52840dk_nrf52840.conf
c40236ccc4995f1543cb213da551517c6d88b5c49958f431fb8654d4f15a0d7b  firmware/zephyr-auth/CMakeLists.txt
99f00ecdd3196adf78376f52f539c6aa5bbf1b3fa5832c1ce557476ed01cf2cc  firmware/zephyr-auth/Kconfig
4999fdc6ecd47ed875847ef7f8b8649c87bf1c3a72e650549fd546f0cfec99aa  firmware/zephyr-auth/prj.conf
88045177009497ea74a121760160b7745cf6e8873d1f2a9d7750c79e1f66b579  firmware/zephyr-auth/src/main_auth.c
0a539591ba86f008a58411293a2bb7e9822be2f6f3b522fa99575e5793430c4d  firmware/zephyr-auth/src/evc_auth.c
20218119e87bacfd5fb0e200c6e49a924cb095caa649f5352b764ee84da0c7a5  firmware/zephyr-auth/src/evc_auth.h
e2ae26348ffbb11a8b9ebea824d4d489f10c931ccc66fad46f02d41cced5bd87  evaluation/sim_theta077.py
6b5f1ba7ccb4510708c3cf84185d903922b32c4e2d7a55edeebba2838573c113  evaluation/sim_predictions/theta077_summary.csv
392e2164e58418e934a4ed74f06e26f918e289f3ff8372b453dce68aa705d87a  evaluation/pairs_ext083.py
97e44f2f5efd8a92586abd2e7c4dac54ed3a8ee2705b416d466b5e7ab99403a8  evaluation/ext083.py
d44aaccfebcc4e84429867a00ca6b9b9343205f77f1124956ce28be3bf64ce3a  evaluation/campaign_ext083.py
5b0a73ffa072194e429ab3366c3cc90de1236f4459ed2ce0b469212493476a9b  evaluation/campaign_real_ext083.py
8b21e5b6f2a1b7a307231c6251228c2aad65f20a94cb0dc3f343f704e86ba446  evaluation/campaign_tb_ext083.py
531bf189aaf8766eaecc477935071da682c622fedce3eb81b29e92909d7dc368  evaluation/sim_extra083.py
21bb6fde62dea576e359625b193c3ed1f8679498fbf868268d30e92721171c0e  evaluation/power_extra083_testbed.py
78af26fbc0b8527a98aa77f268faff856ef02fd70dc9ce6590139a6b43fb0047  evaluation/sim_predictions/extra083_summary.csv
bcc677dbf1d11ce757594e9e3c06af08ea1866d6ed659babad58382ca743fc4a  evaluation/sim_predictions/extra083_effects.csv
713fd57dbf5ee1b77fd0f46baefa9ef95e39be40a43ad0d0978d13340826c489  evaluation/sim_predictions/extra083_power.csv
ab5a95b4cfa46e914237441ec12abf8f87f8dbefcfcf03e96acc8a260fc463b4  experiments/sim/contracts083/sim-tb-083.json
6e3f24a918de659c1feb8aaa886d9e9681195353528b5df10e615e8897ff3a06  evaluation/prereg/PREREG_extra_083.md
978b9a87c610e61c666b4b5ef508c1f2ed601898282d87c15844942c16947981  evaluation/ext084.py
37aa3dba4b539c0fe5d805c294c71b32e3e87374470540046706da764a70e50a  evaluation/campaign_ext084.py
377867c4ea88ead592c86ef11408e13fc54f9e07da3efdd8debf091d5ac1005e  evaluation/campaign_real_ext084.py
7fa7b4578beaf7d0eca26fd7380646396e1552140a950206db3773c45d9d7a33  evaluation/campaign_zephyr_ext084.py
fd4c103efdbe7d1309374af8922447bc1bdfe62b53a25616bd4b0b2b04638e10  conformance/reference/delivery_binding.py
5448902de4a52e80e850541ceac35397a623382ce1ea1521919c7fa2b98ffca2  experiments/owners/bindings/delivery-binding-084.json
7edb29309405c0b59947a08afb734cd88b99c108b64bac9a1f53e6f2405cb4c2  evaluation/sim_extra084.py
66aaf008c4a0827e2ebcd66af573ff1a01967712980df55d0f99f66d3488e369  evaluation/power_extra084_testbed.py
7e71e974a5e0513128596e9dfab28b86f907b17004c59776f6648e4dd8824a9f  evaluation/sim_predictions/extra084_summary.csv
cfa9bde3a40802c4907809917ebc3703ba836e53244156294f3404024e74adb2  evaluation/sim_predictions/extra084_effects.csv
8fc3d9dd083832bab155fa0af06aa04cca189bf784756275c300d96549ff3b31  evaluation/sim_predictions/extra084_power.csv
3a49d43232def54eee9469bf1efdb368ad9d2ea99acc7cf56e0a5b70e5a91daa  evaluation/sim_predictions/extra084_calibration.csv
b11351645c1e0f05f4a270412539a072f17148ea8607223dcb6182a171505345  evaluation/prereg/PREREG_extra_084.md
7bda8a41bed46610890e66c10c3789e228421b9c2fe33c3a11495409984720b4  evaluation/ext085.py
94afd4d630bf56f850aa89fa668ecc583548ff84a1bbbb13666bfccb17753bf4  evaluation/campaign_zephyr_ext085.py
029aa988ee7bbf2a12ef445e7417194937bac7dea85880a44c9350cb2c6274ca  evaluation/campaign_real_ext085.py
ac2e0ffc87abcdabd6ed0e86245acb3fa4b6049d75bfc96e9dc52ba9d41117de  conformance/tb_rpc_checks.py
bfa513619f58c3da77df9bb172ebcb6eb15213598d407049abb4d467f87689bf  conformance/reference/mqtt_device_tbcheck.py
2b56c5b87c2d718b1f589233ae16e7aa4fd79e72669da7fffd9261cc5a4683ff  evaluation/power_extra085_testbed.py
ce0de1a0f4716a0fe72d8ace491f7f99410aeb8c889bbbd805c1267cc00f02f7  evaluation/sim_predictions/extra085_templates.csv
c6fc4be8418e265169b6b90b8c21cc98715a84cafafdd069ab925dbe34315fd7  evaluation/sim_predictions/extra085_power.csv
22586c782aa3ac704bcc56376324faeb7c4047da6e35c1668a65a34924bf4790  evaluation/prereg/PREREG_extra_085.md
21ca6b7e47dc2ea2078089c590f960139b03b50a7cf92c005086a7f238c765b2  evaluation/power_extra086_testbed.py
51fd53d4f79112b9e8dd1174745dff418b1f4b12b03b585d686bb442defa2765  evaluation/sim_predictions/extra086_templates.csv
3b6ec5830535a0b168e624b6f1005d16b0c26cf277f2310449d812d150050224  evaluation/sim_predictions/extra086_power.csv
fac6663d9ebce415d61cc733e25806ed36df64d30e514c6c8ff550ccbacaefac  evaluation/sr086_schedules.py
96edb19aac6c9e9b1669f01d02c23a4a53ba38595eadbeca97f6fd7209dd2d3e  evaluation/sim_predictions/extra086_schedules.csv
6dbd79f85b3032fb6eedf62526fa4e44ad6214262aac8e36aac315b5853686ef  evaluation/sr086.py
c1c3e666cc80a2cff01b2d091b0701035aa2da6ff57450a99ca23e1535082550  evaluation/ext086.py
0b2cfbc6dca643d5240ffb99b69459b4aa0ec6c403b60906219659122725bdd8  evaluation/campaign_ext086.py
d011e3bb4ca79b14277a9ca81641e1068134a335237fdec8ae4eb51421d189a9  evaluation/campaign_zephyr_ext086.py
65550bfe14dd895b9b9fb769fee8350c116c773e04bbf6c908b32aad29445c0a  evaluation/nvs_flash.py
2319f59274f33437a558ffaa2725c5ec63677981b3915a540676f2674fba328e  evaluation/cost086.py
8ca2ba7a7bd23b3e671b66b6866e85f4d7ade0a12c38470b4cc2354aefb8f0b2  conformance/reference/mqtt_device_qos2.py
b0402dbe14bf29e3adbd80c6c9725c277d06637f2549b8a3a9df0a288b693303  conformance/evicon_probe/adapters/mqtt5_qos2.py
156777a343836aeb50306a55a6003f7dbcec6430e4992a08f15ca08fc58a4cdb  conformance/configs/mosquitto-rpc-qos2.json
1007746b8b56a395ff4a4ce00535abeb8dde513953df0cf8a3d5474a9bd843ca  conformance/reference/qos2_expiry_check.py
d28956e98e4fff3ba032f14e1a3a48c1e92d9e0ffa1b75e14d9a36ca3adba9c6  evaluation/prereg/PREREG_extra_086.md
57499a577e00ab0c93c6aada40cdb8eedcd9303bfd31c079798cb578ba84f91b  evaluation/ext087.py
ad3cff22798b727ea6098457d86d0360932e47e2d63c3b256f829f5e44016801  evaluation/campaign_zephyr_ext087.py
ba56822fcc4f2406228ab20c17f568da778759381c5e5ad99b8587b4fff91490  evaluation/campaign_real_ext087.py
470192f8bfeb26367597d2f87c7c6ba89054b54c32c98674771135d0d51927f4  conformance/reference/mqtt_owner_gateway_ext087.py
d6415b4b8d0b0939c841b9c4a6a9bd07ef3de649f51cbe0debc1fc6bc76a579e  firmware/zephyr-boot/CMakeLists.txt
35a588c60442ec2d36cce5b1dfb844ff96798ddb6e1d006f1d5325186976732f  firmware/zephyr-boot/Kconfig
95481a4c3826b8160c4870a5204f63268e77d886dd3a350184a87cf6dc24016d  firmware/zephyr-boot/prj.conf
6d5a779db9b05857f8832d53791f2b801fe9a7188929dc633d2fd8dd53220784  firmware/zephyr-boot/boards/native_sim.conf
1cb500fef6054174ffb46e2acd2f09af0e4c166addd7bd76a4f291bcd1409ee9  firmware/zephyr-boot/boards/native_sim_native_64.conf
5b0133a8affe3348c380ef43d42e035d39875add2483866ae1f45e4a0c2febe3  firmware/zephyr-boot/src/evc_boot_report.c
c85b8618962e54e6959aeb490cc13fc4467f8aeea91e4f395e767d6cf38ab8fe  firmware/zephyr-boot/src/evc_boot_report.h
0ae9da4323ed0cba0acb2bb54b304bac73915d581365dcb7fb0b0fc15674177a  deploy/setup/build_zephyr_boot.sh
b6732a6f8b46becc4c50276c0863b97ba6cbe1ad174e29448d8417678981fb4f  evaluation/power_extra087_testbed.py
875fcf57b759190f0ad93a9afbfeb9047f5101a705b84aa0ebf82519c77d1d16  evaluation/sim_predictions/extra087_templates.csv
ec885d4bf00ac2eeaad74759996553b1f53ab24d5dbdd0b2de86706342e5312e  evaluation/sim_predictions/extra087_power.csv
a933faa18bd49ffa8e856959b7d04a17fd5bca0680c6094026cc323c1cdb7c45  evaluation/sim_predictions/extra087_claim_ids.csv
539fde5dc24d59724a39546cc2e34d786d17f8c34250e6ed90d14ba8b05f863e  evaluation/sh087.py
4684beb209c226ad1026297a4c4712a8a50fdcf251ad606a33adbc13778c575e  evaluation/rb087.py
```

## 8. Analysis spec (read by pairs_ext083.py)

```pairs-spec
{
  "name": "The experiments of release 087: the documented shadow loop on the real owners; owner reboots with the reboot recovery",
  "runs": {
    "SHRz6": {"campaign": "expSHR-zephyr-w6"},
    "SHRm6": {"campaign": "expSHR-minio-w6"},
    "RBz6": {"campaign": "expRB-zephyr-w6"},
    "SRz6": {"campaign": "expSR-zephyr-w6", "reference": true},
    "SRm6": {"campaign": "expSR-minio-w6", "reference": true}
  },
  "alpha": 0.05,
  "max_invalid_fraction": 0.1,
  "hypotheses": [
    {"id": "SHR-A1", "run": "SHRz6", "test": "absent", "contract": "ZEPHYR_CUTOFF_DIRECT", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "SHR-A2", "run": "SHRz6", "test": "absent", "contract": "ZEPHYR_CUTOFF_DIRECT", "policy": "SHADOW_RECONCILE", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "SHR-A3", "run": "SHRz6", "test": "absent", "contract": "ZEPHYR_CUTOFF_DIRECT", "policy": "SHADOW_RECONCILE_1S", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "SHR-A4", "run": "SHRm6", "test": "absent", "contract": "MINIO_COND", "policy": "EVICON", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "SHR-A5", "run": "SHRm6", "test": "absent", "contract": "MINIO_COND", "policy": "SHADOW_RECONCILE", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "SHR-A6", "run": "SHRm6", "test": "absent", "contract": "MINIO_COND", "policy": "SHADOW_RECONCILE_1S", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "RB-A1", "run": "RBz6", "test": "absent", "contract": "ZEPHYR_F_BOOT", "policy": "EVICON_FI_REC", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "RB-A2", "run": "RBz6", "test": "absent", "contract": "ZEPHYR_F_BOOT", "policy": "EVICON_FI", "any_of": ["bound_violations", "duplicate_effects", "logical_duplicates", "false_stopped", "false_applied"]},
    {"id": "RB-D1", "run": "RBz6", "test": "detects", "contract": "NEG_ZEPHYR_F_BOOT", "policy": "EVICON_FI_REC_OS", "any_of": ["bound_violations"]}
  ],
  "safety": {"id": "SHR-S", "runs": ["SHRz6", "SHRm6"], "exempt_contracts": []},
  "estimands": [
    {"run": "SHRz6", "from_run": "SRz6", "policy": "EVICON", "from": "ZEPHYR_F_DIRECT", "to": "ZEPHYR_CUTOFF_DIRECT", "metrics": ["clean_pp", "confirmed_pp", "queries", "fences", "unresolved_at_end"]},
    {"run": "SHRm6", "from_run": "SRm6", "policy": "EVICON", "from": "MINIO_COND", "to": "MINIO_COND", "metrics": ["clean_pp", "confirmed_pp", "queries", "stops", "unresolved_at_end"]}
  ]
}
```

```pairs-ext
{
  "hypotheses": [
    {"id": "SHR-1", "family": "shr-fw", "run": "SHRz6", "contract": "ZEPHYR_CUTOFF_DIRECT", "from_policy": "EVICON", "to_policy": "SHADOW_RECONCILE", "metric": "clean_pp", "test": "equivalent", "margin": 3.0},
    {"id": "SHR-2", "family": "shr-fw", "run": "SHRz6", "contract": "ZEPHYR_CUTOFF_DIRECT", "from_policy": "EVICON", "to_policy": "SHADOW_RECONCILE_1S", "metric": "clean_pp", "test": "equivalent", "margin": 3.0},
    {"id": "SHR-3", "family": "shr-minio", "run": "SHRm6", "contract": "MINIO_COND", "from_policy": "EVICON", "to_policy": "SHADOW_RECONCILE", "metric": "clean_pp", "test": "equivalent", "margin": 3.0},
    {"id": "SHR-4", "family": "shr-minio", "run": "SHRm6", "contract": "MINIO_COND", "from_policy": "EVICON", "to_policy": "SHADOW_RECONCILE_1S", "metric": "clean_pp", "test": "equivalent", "margin": 3.0},
    {"id": "RB-1", "family": "rb", "run": "RBz6", "contract": "ZEPHYR_F_BOOT", "from_policy": "EVICON_FI", "to_policy": "EVICON_FI_REC", "metric": "clean_pp", "test": "at_least", "margin": 15.0},
    {"id": "RB-S", "test": "safety", "runs": ["RBz6"], "exempt_contracts": ["NEG_ZEPHYR_F_BOOT"]}
  ],
  "estimands": [
    {"label": "E-SHR cadence, firmware: SHADOW_RECONCILE -> SHADOW_RECONCILE_1S (within run)", "run": "SHRz6", "contract": "ZEPHYR_CUTOFF_DIRECT", "from_policy": "SHADOW_RECONCILE", "to_policy": "SHADOW_RECONCILE_1S", "metrics": ["clean_pp", "admitted_pp", "mean_time_to_terminal_s", "unresolved_at_end", "late_effects"]},
    {"label": "E-SHR cadence, MinIO: SHADOW_RECONCILE -> SHADOW_RECONCILE_1S (within run)", "run": "SHRm6", "contract": "MINIO_COND", "from_policy": "SHADOW_RECONCILE", "to_policy": "SHADOW_RECONCILE_1S", "metrics": ["clean_pp", "admitted_pp", "mean_time_to_terminal_s", "unresolved_at_end", "late_effects"]},
    {"label": "E-SHR firmware: EVICON -> SHADOW_RECONCILE (within run)", "run": "SHRz6", "contract": "ZEPHYR_CUTOFF_DIRECT", "from_policy": "EVICON", "to_policy": "SHADOW_RECONCILE", "metrics": ["confirmed_pp", "admitted_pp", "queries", "fences", "mean_time_to_terminal_s", "unresolved_at_end", "late_effects"]},
    {"label": "E-SHR MinIO: EVICON -> SHADOW_RECONCILE (within run)", "run": "SHRm6", "contract": "MINIO_COND", "from_policy": "EVICON", "to_policy": "SHADOW_RECONCILE", "metrics": ["confirmed_pp", "admitted_pp", "queries", "stops", "mean_time_to_terminal_s", "unresolved_at_end", "late_effects"]},
    {"label": "E-RB the recovery against release 086 under owner reboots (within run)", "run": "RBz6", "contract": "ZEPHYR_F_BOOT", "from_policy": "EVICON_FI", "to_policy": "EVICON_FI_REC", "metrics": ["confirmed_pp", "admitted_pp", "fences", "mean_time_to_terminal_s", "unresolved_at_end", "late_effects"]},
    {"label": "E-RB the cost of the reboots (across runs: SRz6 {F} EVICON -> RBz6 the recovery)", "run": "RBz6", "from_run": "SRz6", "from": "ZEPHYR_F_DIRECT", "to": "ZEPHYR_F_BOOT", "from_policy": "EVICON", "to_policy": "EVICON_FI_REC", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "unresolved_at_end"]}
  ]
}
```

## 9. Pre-release checks (before freezing; not results)

Checks of the code paths before freezing, on one machine and on a simulated testbed. They are not results of this plan:
the regime, the scale, the timing and the platform differ from the testbed runs.

**Unit tests.** Go: every package passes (`go test ./...`, `go vet`, `gofmt`). The recovery: `TestRebootRecoveryDefaultOff087`
(without the flag no floor is recorded, no sequence is advanced, the journal has no recovery table),
`TestRecSequenceAdvance087` (the floor learned from the replies, the next identity advanced to floor + 1, replays mapped),
`TestRecFloorCheck087` (a reboot inside a history with a budget of 30: the recovery commits MATCH_FLOOR and the evidence
anchor and stays within the budget; release 086 commits no anchor after the reboot; owner-state with the recovery traces
MISMATCH_OWNER_STATE_ADOPTED and exceeds the budget), `TestRecWindow087` (REC-4: an identity a complete fence listed
UNCERTAIN leaves the window under the recovery only, and stays pending with its reservation), and
`TestRebootRecoveryProperty087` (a Go model of the firmware's decision rules: ring, claim mark in blocks, eviction guard,
cutoff, reboot; the gateway's cache and seal; 60 random histories per case with losses, held and late copies, orphans,
fences and reboots, half of them quiet): the recovery 0 executions with a bound violation, 0 false STOPPED, 0 false
APPLIED, 0 duplicates, 339 admissions after a reboot, REC-4 active in 5 executions; release 086 0 violations, 204
admissions after a reboot; owner-state with the recovery a bound violation in 40 of 60 executions. Conformance:
`test_reboot_recovery087.py` (the 087 gateway, the boot build's wire path and decision core as a host library, the real
authority; budget 30, four doses (19), a reboot (floor 65), the third command after the reboot lost): the recovery seals
client sequence 5 at the gateway, learns the floor, advances to 66, commits MATCH_FLOOR and ends at 29 of 30; release 086
admits nothing that takes effect after the reboot (19); owner-state with the recovery reaches 49. Python:
`test_release087` K1-K22 (28 tests; among them the uniqueness of every ID of this plan against the plans 066-086 and the 086 final
report's 184 claim IDs, and the gateway's GW-1 to GW-3 against a firmware model) and every earlier release's tests.

**TLA+** (`formal/TLC_RESULTS_REBOOT.txt`): the reboot model `formal/EviconFenceReboot.tla` (5 sequences, bound 3, claim
block 1, at most 2 reboots and 2 deliveries per identity): the recovery (with NeverConflicted: the floor check never fails
under the eviction guard), the recovery without the check and release 086 report no error; the owner-state anchor with the
recovery violates BoundSafety (the negative control); the firmware without the eviction guard violates
AtMostOncePerIntent (FB is a premise); the two witnesses (an anchor committed after a reboot under the recovery; release
086 conflicted after a reboot) are reachable.

**Analysis spec.** Synthetic runs made of renamed cells of the 086 runs (SHRz6 = SRz6's {F} EVICON cells as all three
arms; SHRm6 = SRm6's MINIO_COND EVICON cells as all three arms; RBz6 = SRz6's plain-QoS-0 cells as EVICON_FI, its {F}
cells as EVICON_FI_REC and, with a planted bound violation, as the negative control; episodes 0-5), with the runs'
schedule files: `pairs_ext083.py` with this plan decided all 16 block rules (SHR-1 to SHR-4 MET at 0.00, RB-1 MET at
+46.24 [35.19, 57.30], SHR-A1 to SHR-A6, RB-A1, RB-A2 ABSENT, RB-D1 DETECTED, SHR-S, RB-S MET) and paired the cross-run
estimands with SRz6 and SRm6 on 8 and 6 episodes; `rb087.py` RB-C1 MET (0). (RB-1 was then `at_least 25`; the
estimate does not depend on the margin.)

**Earlier verdicts, claim IDs, credential scan.** The release-087 claims check on a copy of the 086 final report
(`verdicts087.py` against the original): all 185 earlier hypotheses get the 086 verdicts with the same estimates (185
compared, 0 differ, 0 missing); the six cross-cutting claims are identical. The claim-ID snapshot
(`sim_predictions/extra087_claim_ids.csv`: 191 rows, 184 distinct IDs, from the 086 final report's `claims.json`) is
what K1 checks this plan's IDs against. `make_data_archive.py`: the four 086 result bundles on the build machine scan
clean (the 086 scan flagged 64 files of one bundle, every hit an authority fence token).

**Simulated testbed** (the driver end to end on one machine: sshd on five loopback addresses as VM-A to VM-E, Docker as
VM-B with Mosquitto 2.0.20 and the MinIO stand-in image; the native_sim builds of `firmware/zephyr-cutoff` and, by
`deploy/setup/build_zephyr_boot.sh`, of `firmware/zephyr-boot` as VM-C's build output, the local measured build's SHA-256
given as `EVICON_EXPECT_FIRMWARE_SHA256`; a copy of this tree with this plan frozen in the copy; the three configs of 1
episode, 180 s + 30 s tail, with the reboot ranges scaled to [40, 100] s and [110, 170] s; the operator script with the
copy's paths, `measure ... --quick` and `--archive-missing-ok`; as the earlier runs: the 084, 085 and 086 runs from their
result bundles (084: the six campaign runs, the measurement and the final report; 085: the four PM runs, the testbed
check and the final report; 086: the six campaign runs and the final report, which is also the archive's copy), empty
directories as the 078 and 083 runs):

| Stage | Result |
|---|---|
| `run087.sh` (09/11 23:25): `preflight build verify distribute --new --quick` | preflight, build PASS; verify FAIL (analysis): `test_release079` D1, the driver's planned-unsafe groups had been extended inside the release-079 rule; fixed by leaving `_expected_unsafe` as it was and adding the negative-control cells (`_negative_controls`) at its three call sites; the other three failures were the copy not yet synced |
| the same command again (11/11 09:24) | PASS (verify: `go test`; 94 conformance tests, 0 skipped; the firmware host tests of both applications; 265 analysis tests) |
| `measure zephyr zephyr-boot` | both builds K3: {D_h, Q_A, F}, late effect True; the boot build recorded with its own SHA-256 and the default build's SHA-256 unchanged before and after it was built |
| `campaign expSHR_zephyr_w6` | PASS, 3 cells VALID, safety 0: EVICON 367/367 (17 fences, 196 queries), SHADOW_RECONCILE 363/367, SHADOW_RECONCILE_1S 367/367; owner calls per offered request 1.72 / 1.69 / 2.74 (reads 0 / 0.36 / 1.35, re-publishes 0 / 0.33 / 1.32); mean time to terminal 2.28 / 4.11 / 3.30 s; window defers 11 / 291 / 257; broker packets per offered request 10.7 / 9.2 / 9.5, flash writes 0.068 / 0.022 / 0.022; nothing unresolved at the end |
| `campaign expSHR_minio_w6` | PASS, 3 cells VALID, safety 0: EVICON 367/367 (48 stops, 186 queries), SHADOW_RECONCILE 365/367, SHADOW_RECONCILE_1S 367/367; owner calls per offered request 1.77 / 1.70 / 2.51 (reads 0 / 0.37 / 1.13); one late effect, in the SHADOW_RECONCILE (10 s) cell |
| `campaign expRB_zephyr_w6` | PASS, 3 cells VALID, 8 reboots per cell, none failed (SIGTERM by the pid file; the first answer 4.5-5.5 s after the stop; the floor 65 at the quiet reboot and 130 at the random one, 65 at both in arm (ii), whose sequence never passes the floor); (i) 355/367, FENCE_ANCHOR_CHECK MATCH 7 and MISMATCH_OWNER_STATE_ADOPTED 3 (all three after a reboot), no bound violation; (ii) 131/367, one MISMATCH after a reboot with EVIDENCE_CONFLICT, no admission after a reboot took effect (quiet reboots: 3.75 admissions on average, random: 0); (iii) 356/367, every complete fence MATCH_FLOOR (10, 7 of them after a reboot), 8 seals, after a quiet reboot 39 admissions (35.25 took effect), the first 9.9 s after the restart (median), after a random one 21 (19.5), 1.8 s; 11 identities (25 units) unresolved at the end in (i) and (iii), 24 (62 units) in (ii) |
| `report --archive-missing-ok ...` | PASS (235 s with the 084 run): SHR-A1 to SHR-A6, RB-A1, RB-A2 ABSENT; SHR-S, RB-S MET; SHR-1 to SHR-4, RB-1 NOT COMPUTABLE (one episode; RB-1's difference +61.31); RB-D1 NOT DETECTED (see below); RB-C1 MET (0); E-SHR and E-RB written; fence anchors: 3 of 1,500 completed fences committed an anchor that differs from the evidence, all three in arm (i) (the control adopts the reset level: expected); the earlier plans: 48 hypotheses compared with the 086 final report (every rule of plans 084, 085 and 086), 44 identical, 4 NOT RUN (PF-1 to PF-4: their reference runs C2, Rz, C, Rm are in the archived 074, 073 and 078 runs, not on the build machine), 137 missing (the plans up to 083: their runs are in the testbed's archive only); logged as a FAIL, `REPORT DONE WITH A DIFFERENCE`, by construction of the stand-ins; the same comparison with the 084 and 085 final reports; bundles written |
| exploratory, no plan: `regRB_full` (RBz6's config at full length, 300 s + 60 s tail, this plan's reboot ranges, episode 0 only; in the same review run) | PASS, 3 cells VALID, 8 reboots each (the quiet ones at 110-130 s), safety 0: (i) 450/602 (74.8 %), (ii) 268/602 (44.5 %), (iii) 451/602 (74.9 %), (iii) - (ii) = +30.4; 10 complete fences per arm, every one before the device's first reboot: after the reboots no fence completed in any arm (each batch still held identities the reset ring left UNCERTAIN), so arm (i) adopted nothing; after a quiet reboot (iii) admitted 48.25 (45.75 took effect), the first 8.3 s after the restart, after a random one 11.5 (10), 19.2 s; (ii) 15.25 (11: one device of four, whose sequence passed the floor) and 0 |
| after the regression's findings (the trace copies, RB-1's prediction and margin; see below): the copy synced and its plan frozen again, `report` and `bundle` again | PASS; the numbers in this table are those of this last report |

**RB-D1 in the regression.** In the 180-s cells a device's offered units (about 275) stay below its bound (345): the bound
never binds and arm (i) cannot exceed it, although three of its fences after a reboot adopted reset levels (under-counts
of 63 to 126 units). In the full-length cell no fence completed after a reboot in any arm. The prediction DETECTED is kept with that caveat (section 3);
if no cell of RBz6 adopts a reset level, the outcome reads "the control was not exercised" (section 3, NOT DETECTED).

**Found in the regression and changed before freezing.** (1) The estimand scripts read every copy of a trace: a simulated
testbed (one machine as every VM) fetches each cell file once per VM directory, so `sh087.py` and `rb087.py` counted the
authority traces twice and the gateway logs three times (the first report showed 0.72 reads per offered request for the
10 s loop where there were 0.36, and 24 seals where there were 8); both now read one copy per file name, the writing VM's
(`sh087.one_copy`; test K17). On the testbed each file exists once (in the 086 runs the authority traces are under A
only), so the change does not alter a testbed number; the frozen scripts of earlier plans were not changed. (2) RB-1: the design's +50 assumed that arm (ii) stops at the first
reboot; the full-length episode gave +30.4 (arm (ii) keeps what it admitted before its first reboot at 110-130 s);
prediction +30, margin 15 (half), power recomputed (section 5: 0.96 at n = 6).

**Reading the report.** The anchor check of release 086 (`report/anchor086/`) compares every completed fence's committed
anchor with the evidence; arm (i) commits the owner's reported level after a reboot by design, so its mismatches are
listed there and the report warns; they belong to the negative control and are not a finding about arms (ii) and (iii).
`rb087.py`'s "admitted pp of arrivals" counts every admission in the interval after a restart, re-issues after a STOPPED
included, so it can exceed 100.

## Deviations

None so far.

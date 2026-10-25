Written on 25/10/2026, after the N7 run and before release 076; the numbers are those of the analysis above and of
the campaign's own files (`report/expC3/EXPC3.md`, the gateways' logs). Statements marked post hoc were not in the plan.

**Outcome.** 56 of 56 cells VALID; C3-1 to C3-6 MET; C3-S MET (no safety violation and no late effect in any cell);
Theorem 3 held on the traces (99 re-tests after APPLIED-only evidence, all BLOCK).

**The fence alone (C3-1, E-C3-5).** With the measured cutoff contract restricted to {F}, EVICON's clean fulfilment rose
by 53.7 pp (95 % [45.8, 61.7]) over the plain firmware on the direct channel, and on the queue channel {F} alone
equalled C2's full cutoff contract {D_h, Q_A, F} (C2 -> C3, paired: -0.15 pp [-0.59, +0.28]). With the owner up and
no bound on the network delay, the fence is the whole value of the cutoff contract; D_h is unusable (T4) and Q_A adds
nothing measurable on top of F.

**Evidence of occurrence alone depends on the channel (C3-2 to C3-4, E-C3-1, E-C3-2).** {Q_A} reached 48.4 % on the
direct channel, against 79.1 % for {F} (+30.7 pp [16.9, 44.5]) and 76.8 % for {Q_S} (+28.4 [14.8, 42.1]); on the
queue channel it reached 77.2 % (+28.8 [15.4, 42.3] over direct). On the direct channel Q_A recovered 43 % [27, 58] of
the plain -> {F} gap: it resolves the ambiguity of commands that took effect but whose reply was lost (a dropped reply,
a client abandon after APPLIED), and nothing else; a command lost while offline is NOT_FOUND for ever and holds a slot
of the window. On the queue channel, where lost commands arrive late, Q_A came within 1.9 pp of {F}, but left 11
commands per episode unresolved at the end and 9,135 unit*s of stranded capacity-time still open, against none for {F}.

**The fence and the stop with the owner up (C3-5, C3-6).** Equivalent within +-5 pp on both channels: direct -2.29 pp
(90 % [-4.76, +0.18], TOST p = 0.038), queue +0.22 (90 % [-0.21, +0.65]). The direction on the direct channel, the
stop below the fence, has a cause we found after the run (post hoc, from the gateways' logs): **the firmware's
tombstone table filled.** Every stop of a command the firmware never saw takes a durable tombstone; the table has 16
entries per device (`EVC_TOMB_SLOTS`, the Kconfig default) and only a cutoff collects them, which a {Q_S}-only
contract never issues. On the direct channel the table filled in 10 of 32 device-episodes, and 272 stops were answered
UNCERTAIN `TOMBSTONE_TABLE_FULL` (51 more `FUTURE_BLOCKED_PAST_UNKNOWN`: the receipt may have left the RAM ring). Per
episode, {Q_S} - {F}: -10.1, -2.5, -1.0 and -5.2 pp in the four episodes with 27-105 refused stops (0, 3, 5, 6); -0.2,
+0.3 and +0.2 pp in the three without any (1, 2, 4); +0.2 pp in episode 7 (4 refused stops on one device). On the queue
channel lost commands arrive late and many stops found them APPLIED; one stop was refused, on one device. A fence keeps
one durable value per device; a stop keeps one entry per stopped identity until a cutoff collects it. The simulator,
which has no tombstone capacity, predicted +0.1 pp; it is one of its two sign misses (with the same comparison on
confirmed fulfilment).

**Crash orphans decide the stop's advantage (E-C3-6).** Against the emulated owner of `expW-*-w6` and `expS-w6` (same
seed, same schedules, the same capability sets; the emulated owner crashes between effect and receipt, the firmware does
not), paired, n = 8: no evidence, K0 against the plain firmware, +1.1 pp [-1.1, +3.4] (TOST +-5 pp p = 0.002); the
stop, K0S against {Q_S} on the queue channel, -0.06 pp [-0.23, +0.11]; the fence, K0F against {F}, +23.2 pp [16.2,
30.1] on the direct channel and +18.9 pp [11.8, 26.0] on the queue channel. The emulated owner agrees with the firmware
wherever orphans do not matter, and the fence loses about 20 pp to them (fence audit: 0 `BARRIER_UNCERTAIN_RECEIPTS` in
C3, 388 in `expS-w6`). The +21.1 pp of the stop over the fence in `expS-w6` is therefore the orphan effect: with the
owner up the two are equivalent (C3-5, C3-6, and C2-5), and each fails in its own way, the fence under crash orphans,
the stop when the owner's durable stop storage is exhausted. The simulator predicted an orphan cost of 4-7 pp for the
fence (owner_crash against owner_up); the emulated owner against the firmware differed by three to
four and a half times that.

**Limits.** One broker (Mosquitto) and one firmware, in `native_sim` on Linux processes (the nRF52840 build is measured
for footprint only); four devices; the Kconfig defaults for the tombstone table (16) and the receipt ring (32). An owner reboot is outside the
measured fault model: the firmware stays safe and loses liveness for the identities of its claimed block
(`report/reboot/REBOOT.md`).

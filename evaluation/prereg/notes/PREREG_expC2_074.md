Written on 24/10/2026, after the N6 run and before release 075; the numbers are those of the analysis above.

**C2-1 and C2-2.** On one firmware binary, the evidence mode decides whether a client's blind retry is
safe: NAIVE duplicated 447 real effects and exceeded the dose bound for every device in every episode on
the plain mode (32 of 32 device-episodes), and duplicated nothing on the cutoff mode. The contract
EviCon-Probe measured for each mode (none; D_h, Q_A, F) says which, as the call shape did on MinIO in
experiment C.

**C2-3 and C2-4.** With the measured cutoff contract, EVICON's clean fulfilment rose by 49.5 pp (95 %
[40.1, 59.0]) over the plain mode's, with no same-identity retry (T4 forbids it without a delay bound):
244 receipt queries and 15.8 fences per episode, every fence that reached the firmware completed (94 of
94; 126 calls in all, the others found the device offline). EVICON on the cutoff mode (79.3 %) equalled
the blind retry on the same dedup firmware (79.4 %), the regime's ceiling: commands whose deadline passes
while the device is offline are lost to every policy.

**C2-5 (NOT MET, +0.00 pp, 95 % [-0.24, +0.24]).** The two modes reached the same ceiling. The plan
(section 1) framed C2-5 as a test between the simulator, which predicted no stop-over-fence gain, and
the emulated testbed, which found +21.1 pp in `expS-w6`. That framing was imperfect, and we record it
here rather than in the plan:

- the contrasts differ. `expS-w6` compares a fence-only contract (K0F) with a stop-only one (K0S),
  without Q_A; C2 compares {D_h, Q_A, F} with {D_h, Q_A, Q_S, F}, where Q_A resolves most ambiguous
  commands before either a fence or a stop is needed;
- the fault models differ. The emulated owner of `expS-w6` crashes between effect and receipt and
  leaves crash orphans, identities whose outcome the owner itself cannot certify; a fence that would
  cover one aborts (388 `BARRIER_UNCERTAIN_RECEIPTS` in `expS-w6`; there the fence's success rate was
  4 % of all calls, 25 % of those that reached the owner, and EVICON deferred 10,060 times on
  `FENCE_BLOCKED_BY_OWNER_UNCERTAIN`). The firmware never crashes in C2 (the measured fault model,
  "owner up"), so no orphan exists: 0 such aborts.

C2-5 NOT MET therefore does not contradict `expS-w6`: it shows that the stop's advantage over the
fence is not unconditional. The emulated owner agrees with the firmware wherever the two have the same
contract and the orphans do not matter (E-C2-6, paired, n = 8, the same schedules: K0 against the plain
mode +0.2 pp [-0.9, +1.3]; K0S against the stop mode -0.1 pp [-0.3, +0.1]; TOST +-5 pp p < 0.001 for
both); where they differ in both the contract (Q_A) and the fault model (orphans), K0F lies 19.0 pp
[11.8, 26.2] below the cutoff mode. Experiment C3 (release 075, `PREREG_expC3_075.md`) tests the
conditional on the same stack: EVICON on restrictions of the measured contracts to {Q_A}, {F} and
{Q_S}, on the queue channel of C2 and on a direct channel that loses commands.

**Limits seen in C2** (reported with the results): the stop mode keeps receipts in a RAM ring of 32
entries, so 18 of the 180 stops that reached the firmware answered UNCERTAIN (a receipt may have left the
ring) and 3 commands stayed unresolved at the end over the 8 stop cells; an owner reboot is outside the
measured fault model (release 075 measures what the firmware certifies across one: `measure
zephyr-reboot`, `report/reboot/REBOOT.md`).

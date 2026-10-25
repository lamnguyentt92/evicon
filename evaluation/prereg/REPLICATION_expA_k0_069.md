# Replication of confirmatory experiment A, K0 config, on release 069

Status: **post hoc decision.** It was taken on 20/10/2026, after the 067 confirmatory results were known. The paper reports
it as a replication of the same frozen plan. It is not a new preregistration.

The sections "Why" to "How it is reported" were written **before** the run started:
- the project note `EVICON_expA_k0_replication_plan.md` is time-stamped 2026-10-20 07:23 UTC;
- the campaign started at 07:33 UTC (`stages.json`).

They are copied here unchanged in substance. The "Result" section was added on 21/10/2026, after the run.

## Why

The confirmatory campaign `expA-confirm-k0` (release 067, run `20261016T234202`) ran on a device emulator with a defect.
Release 068 fixed it (device tests D09, D10). In that run:

- 6 device receipts flipped from APPLIED to STOPPED;
- 4 cells had a domain stuck on the same failed fence;
- 5 of the 16 episodes were affected.

A post hoc sensitivity analysis without the affected episodes changed little: +29.83 pp against +29.89 pp. Running the
same config again on the fixed emulator removes the threat entirely.

`expA-confirm-k2` (K2/K3F) had no receipt flips and is not repeated.

## Design

- **Plan:** `evaluation/prereg/PREREG_expA_confirm_067.md`, SHA-256
  `d3ff30681d44146272080effd73ae78978c7587fc250d17ac6b7e446544ea79e`. The six frozen files are byte-identical to 067, and
  the driver checks their hashes before the campaign.
- **Config:** `evaluation/campaigns/expA_confirm_k0.json`, unchanged: 16 episodes, the same seeds, contracts, policies and
  faults.
- **Release:** 069, run `review-20261020T011256`.
- **Command:** `python3 evicon_review.py campaign expA_confirm_k0`.

## Decision rule (fixed before the data)

The replication **replicates** when two conditions hold.

1. The three primary hypotheses that the K0 config decides hold on the replication run alone, with the rules of section 3
   of the plan:
   - H-A1, for EVICON and for RES_PERIODIC;
   - H-A1c;
   - H-S.

   H-A2 needs the K2 config and is not part of the replication.
2. The fence audit of the replication run shows 0 device receipt flips and 0 stuck cells. Otherwise the emulator is still
   faulty, the defect is reported, and the run is not used as evidence.

Reported descriptively:

- H-B (EVICON against RES_PERIODIC on K0F), with fence counts;
- fence success rate;
- time a domain was paused.

## How it is reported

- The primary result stays the 067 run, analysed exactly as the plan says.
- The 069 run is reported next to it as a replication: `report/confirm_expA/RESULTS.md`, section "Replication", and
  `report/paper/tab_expA.tex`.
- The two runs are not pooled into the primary result.
- If the two runs disagree on a hypothesis, both are reported and the claim is narrowed.

## Result (added 21/10/2026, after the run)

Run `expA-confirm-k0/20261020T073331`, release 069: 96 cells, 0 INVALID, 11 h 33 min.

| | 067 (preregistered) | 069 (replication) |
|---|---|---|
| H-A1 EVICON, K0 → K0F (pp, 95 % CI) | +29.89 (+23.50, +36.02), met | +30.68 (+23.98, +37.10), met |
| H-A1 RES_PERIODIC | +28.12 (+20.65, +35.62), met | +30.38 (+23.18, +37.52), met |
| H-A1c M1 | −0.05 (−0.32, +0.20), met | +0.00 (−0.04, +0.06), met |
| H-S (K0 config) | 0 violations, 0 late effects | 0 violations, 0 late effects |
| H-B EVICON − RES_PERIODIC on K0F (pp) | +1.74 (−2.89, +6.66), inconclusive | +0.85 (−2.33, +3.72), inconclusive |
| fences per episode, EVICON / RES_PERIODIC | 258 / 52 | 204 / 50 |
| fence audit: receipt flips, stuck cells | 6, 4 | 0, 0 |

**Verdict: replicates.**

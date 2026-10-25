# Preregistration — EviCon, confirmatory campaign

> Fill in and freeze this file **before** running the confirmatory campaign. Freeze it with
> `python3 scripts/freeze_prereg.py <plan>` (it writes the SHA-256 of every frozen file into the plan and the
> plan's SHA-256 into every config that names it) and commit the filled-in file **before** the first run.
> Record every later change in the section "Deviations from the preregistration" at the end of the file. Do not
> edit the sections above it.
>
> Prerequisites:
> - `evicon_review.py controls` PASS;
> - both pilots pass the activation gate (`activation.json`);
> - `power.py` gives a determined sample size, not `UNDETERMINED`/`NOT_ACTIVATED`.

## 0. Identification

| Item | Value |
|---|---|
| Release | from the file `VERSION` (e.g. 077) |
| SHA-256 of the source zip | `<fill in>` |
| Freeze date | `<YYYY-MM-DD>` |
| Frozen by | `<name>` |
| sha256 of `experiments/sim/*.json` | `<fill in>` |
| sha256 of the confirmatory campaign config | `<fill in>` |
| sha256 of `conformance/configs/*.json` | `<fill in>` |
| sha256 of the binaries (`results/<run>/build.json`) | `<fill in>` |
| Pilot run used for the sample size | `<results/review-…>` |
| Versions of the external systems (image digest) | `<fill in from the manifest>` |

## 1. Research questions and hypotheses

Every comparison is paired on the same episode (CRN). The two kinds of comparison are **never** mixed:
- **Experiment A, value of the contract**: same policy, different contracts (for example `c` and `c+F`, or K2
  and K3F).
- **Experiment B, value of the policy**: same contract, same API, same retry guard; EVICON against
  `RES_IMMEDIATE`, `RES_QUERY_FIRST`, `RES_FIXED`, `RES_PERIODIC`.

Questions:
- **RQ1 (C2).** Which capabilities do the systems of group R provide? Does the documentation match?
  - A capability is reported as present only when its state is `OBSERVED` over 3 repetitions, with provenance.
  - Every deviation must be reproducible from `results.jsonl` with its manifest.
- **RQ2 (C1, simulator).** On contracts without backward evidence (K0/K1/K2), EVICON equals the simulator baseline B2 (`B2_LEDGER`; theorems T2/T3). On
  `c+F`, measure the difference from `c` (experiment A).
- **RQ3 (C3, testbed).**
  - **H3-A**: in the same regime, `clean_fulfilled` of EVICON on K3F/K3S against K2 (experiment A), minimum
    Δ `<δA>`.
  - **H3-B**: EVICON against the same-capability baseline `<RES_…>` (experiment B), minimum Δ `<δB>`,
    direction `<…>`.

    Register in advance the possibility of **no** difference as well (equivalence margin `<ε>`, TOST).
    Simulator results suggest that EVICON and RES_IMMEDIATE may coincide.
- **RQ4 (safety, hard criterion).** Every VALID cell of every policy of the EVICON family has
  `bound_violations = duplicate_effects = logical_duplicates = false_stopped = false_applied = 0`. A single
  violation refutes the safety claim.

## 2. Metrics

| Kind | Metric | Definition |
|---|---|---|
| Primary | `clean_fulfilled` | Intent with exactly one effect (over all identities) before `arrival + deadline` |
| Primary | `stranded_resolved_unit_sec` | Σ value × (terminal evidence − admission) for identities without an effect |
| Secondary | `stranded_censored_unit_sec` | As above, but without terminal evidence by the end of the cell |
| Safety | `bound_violations`, `duplicate_effects`, `logical_duplicates`, `false_stopped`, `false_applied` | Read from the plant/checker; reported as totals, never averaged |
| Validity | `late_effects`, `fulfilled_late` | Reported separately, not added to safety |
| Activation | `decisions_acting`, `decisions_by_mode` | EVICON cells that never act are reported separately |
| Cost | `queries`, `stops`, `fences`, `reissues` | Mean per cell |

## 3. Design

- **Regime**: `<pending-binding / capacity-binding>`, parameters from the frozen config. The bound and the load
  are not tuned after seeing results.
- **Cell**: contract × policy × episode.
- **CRN**: the same seed, hence the same arrivals, values and fault schedule for every policy and contract.
- **Run order**: `<fixed by the config / shuffled with seed …>`.
- **Sample size**: `<n>` episodes, from `power.py` on the pilot `<run>`, with power 0.8, Δ = `<δ>`, α = 0.05,
  Holm over m = `<m>` comparisons.

## 4. Analysis

- `analyze.py`:
  - paired differences, 95 % bootstrap CI;
  - p-values from a t test on the pairs, with Holm within each table (A and B separately);
  - report effects, not only p-values.
- Equivalence: TOST with margin `<ε>`, based on the 90 % CI.
- **Exclusions**: only cells the campaign marked `INVALID` (reason in `cell_status`). When a cell of an episode is
  excluded, that episode is excluded for **every** policy. Report the number of excluded cells.
- `SIMULATION_ONLY`, `DEV_LOCAL` and `TESTBED` are never mixed in one table or figure.

## 5. Stopping rule

- Run exactly `<n>` episodes; do not look at results midway to decide whether to stop.
- Stop early only if EVICON violates safety (RQ4). Then:
  - keep the data as it is;
  - fix the fault;
  - re-run the **whole** campaign with the new release.

## 6. Exploratory analyses (not confirmatory)

List them in advance, for example: sensitivity to `ambiguity_after`, to the fence cost, to the crash rate.

## Deviations from the preregistration

| Date | Section | Change | Reason |
|---|---|---|---|
| | | | |

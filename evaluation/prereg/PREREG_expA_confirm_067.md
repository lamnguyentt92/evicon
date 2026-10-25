# Preregistration: confirmatory experiment A (contract value) on the 5-VM testbed

Release 067. Frozen on 2026-10-16, before any confirmatory cell runs. The two campaign configs
below carry the SHA-256 of this file, and `evicon_review.py campaign` refuses to run them if
this file, or any file listed under *Frozen files*, has changed. Changes after freezing are
recorded under *Deviations* at the end; nothing above that section is edited.

## 1. Background (exploratory evidence, not part of the test)

Two pilot runs of `evaluation/campaigns/expA_dosing.json` (seed 20261014, episodes 0-4, 60 cells
each) were made with releases 065 and 066:

| Clean fulfilment | M1 | EVICON | RES_PERIODIC |
|---|---|---|---|
| K0 | 0.279 / 0.281 | 0.279 / 0.279 | 0.280 / 0.279 |
| K0F | 0.279 / 0.278 | 0.598 / 0.596 | 0.560 / 0.578 |
| K2 | 0.781 / 0.781 | 0.781 / 0.781 | 0.781 / 0.781 |
| K3F | 0.781 / 0.781 | 0.781 / 0.781 | 0.781 / 0.781 |

(065 / 066.) Both pilots had 0 safety violations. The sizes below come from the 066 pilot.
The pilots are not pooled with the confirmatory data.

## 2. Design

| Item | Value |
|---|---|
| Configs | `evaluation/campaigns/expA_confirm_k0.json`, `evaluation/campaigns/expA_confirm_k2.json` |
| Regime | as `expA_dosing.json`: 4 devices; 300 s per cell + 60 s tail; 0.5 commands/s per device; values 1-5; bound 345 per device (frozen in 065); deadline 60 s; validity 90 s; fault windows 2/min (1-4 s), offline 10-40 s, mix offline 0.3, drop_up 0.2, drop_down 0.2, delay_up 0.1 (6 s), crash between effect and receipt 0.2; authority as in `expA_dosing.json` |
| Contracts and episodes | `expA_confirm_k0`: K0, K0F, **16 episodes**, seed **20261017**. `expA_confirm_k2`: K2, K3F, **5 episodes**, seed **20261018** |
| Policies | M1 (no backward evidence), EVICON, RES_PERIODIC |
| Cells | 96 + 30 = 126; every policy and contract runs the same seeded workload and fault schedule per episode (common random numbers) |
| Activation gate | at least one acting EVICON decision in every VALID K0F (resp. K3F) cell |
| Unit of analysis | one episode: the paired difference between two cells of the same episode and policy |
| Primary metric | `clean_fulfilled`: exactly one effect over every identity of the request, before the deadline and within validity; reported as percentage points (pp) of offered requests |
| Analysis | produced by the driver when every cell is done: `analysis/contract_upgrade.csv`, `analysis/paired.csv`, `analysis/report.json`, `analysis/sim_to_real/sim_to_real.md`; bootstrap 2000, equivalence margin 2 pp |

## 3. Hypotheses and decision rules

| ID | Hypothesis | Decision rule (files of section 2) |
|---|---|---|
| **H-A1** | Without a forward path, an owner fence raises clean fulfilment by at least 10 pp, for EVICON and for RES_PERIODIC | For each of EVICON, RES_PERIODIC: `contract_upgrade.csv` K0 -> K0F `clean_fulfilled` has `mean_diff` > 0 and `p_holm` < 0.05, **and** the lower bound of the testbed CI in `sim_to_real.md` (K0 -> K0F, pp) is >= 10 pp |
| **H-A1c** (control) | A policy that never uses F gains nothing from it | M1, K0 -> K0F: `equivalent_at_0.05` is True (TOST, ±2 pp) |
| **H-A2** | With dedup + query (K2), a fence adds nothing | For each of EVICON, RES_PERIODIC: K2 -> K3F `equivalent_at_0.05` is True (TOST, ±2 pp, Holm) |
| **H-S** | Safety holds in every cell | `report.json`: `groups_with_safety_violations` empty (bound, duplicate, logical duplicate, false STOPPED, false APPLIED all 0); late effects reported separately |
| H-B (secondary) | On K0F, EVICON is not better than RES_PERIODIC; it uses more fences | Reported from `paired.csv` (K0F, EVICON vs RES_PERIODIC): mean difference, CI, TOST; fence counts per policy. No claim of superiority unless `p_holm` < 0.05; no claim of equivalence unless TOST passes |
| SR (secondary) | The simulator twin predicts the signs and the contract ranking | `sim_to_real.md`, criterion 066 (margin 2 pp): no sign disagrees, >= 1 non-null effect agrees, no undetermined testbed effect, pooled tau-b (ties < margin) >= 0.6. Twin: `experiments/sim/iot_dosing_testbed.json`, 200 episodes. Absolute levels are reported, not tested |
| Mechanism (descriptive) | Fences fail on crash orphans | Fence success rate, `BARRIER_UNCERTAIN_RECEIPTS` counts, `unresolved_at_end` |

## 4. Sample size

From `power_A.txt` of the 066 pilot (alpha 0.05, power 0.8, Holm over the comparisons,
delta 10 pp): EVICON needs n = 10, RES_PERIODIC n = 16 (K0 -> K0F). All policies run 16
episodes. K2 -> K3F equivalence (±2 pp) needs n = 2; 5 episodes are run.

## 5. Exclusions, stopping and order

- Cells marked INVALID by the campaign runner are excluded and listed. An INVALID cell is re-run
  once by resuming the campaign; if it is INVALID again it stays excluded. If more than 10 % of
  the cells of a config are excluded, the result of that config is reported as not interpretable.
- No interim analysis: the driver analyses only when every cell is done. The campaign may be
  split over several sessions with `--max-cells` (resume).
- Run order: `expA_confirm_k0` first, then `expA_confirm_k2`.

## 6. Frozen files

SHA-256 at freezing time (`scripts/freeze_prereg.py`); the driver checks them before running.
The Go runtime and the simulator twin are built from the release that runs the campaign; the run's
`source.json` records that source. A code change after freezing needs a new release and is listed
under *Deviations*.

```frozen-files
1e506bf933854ea67a90ef508fa383bc5dedbb70b51a4896eb6c21efa0e7c125  evaluation/analyze.py
05f26bb9e57df331acbf8d44294324222a5d1cd5ff978b4336caa69212f7cb06  evaluation/power.py
88b7aa80a31fb16de96df62844d889dea4cd343c61162c8fbcd74ad39f76a821  evaluation/sim_to_real.py
7d928b8062787b6ba5985003f54fd1e1ae2398afb29af986c1205a8dffed0962  evaluation/campaign.py
e234ea14c1f7e3b3e012f2501cfd82262597409c2bd82d2002350592c527ec28  experiments/sim/iot_dosing_testbed.json
9af132fbb904bbe940dab6a56491479bd46e8f1ec62f85d04516fec74474ed02  evaluation/campaigns/expA_dosing.json
```

## 7. Deviations

None at freezing time.

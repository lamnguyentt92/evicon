# Preregistration: experiment S (per-command stop against owner fence) on the 5-VM testbed

Release 072. Frozen on 2026-10-21, before any S cell runs. The two campaign configs below carry the
SHA-256 of this file, and `evicon_review.py campaign` refuses to run them if this file, or any file
listed under *Frozen files*, has changed. Changes after freezing are recorded under *Deviations*;
nothing above that section is edited.

## 1. Background (exploratory evidence, not part of the test)

Without a forward path (no dedup, no query), two capabilities give backward evidence: a per-command
stop Q_S ("make this command never take effect, or tell me it already did") and an owner fence F
("seal everything below this frontier"). The paper's proposition on crash orphans predicts how they
differ when a command crashed between its effect and its receipt (an orphan: the owner holds a claim
without a receipt and cannot say whether the effect happened):

- a fence whose frontier covers an orphan is aborted by the owner and resolves nothing
  (`BARRIER_UNCERTAIN_RECEIPTS`); the orphan stays and blocks the next fences too;
- a stop of the orphan itself returns "not terminal" (`STOP_NOT_TERMINAL:CLAIMED_WITHOUT_RECEIPT`),
  but a stop of any other command still resolves it.

Testbed evidence so far (exploratory):
- K0F, experiment A, 069 run: of the fence attempts that reached the owner (answered 2xx or 409),
  27 % (EVICON) and 30 % (RES_PERIODIC) succeeded; most other attempts never reached it
  (DEVICE_OFFLINE: 2,594 of 3,256 EVICON fence calls).
- K3S, 066 pilot: of the stops that reached the owner, 45 % were terminal; the rest were
  `STOP_NOT_TERMINAL:CLAIMED_WITHOUT_RECEIPT`.

Simulator (release 072, twin with the window and contract changed, 200 episodes, EVICON, queue
channel; `SIMULATION_ONLY`). Clean fulfilment, % of offered; mean per-episode effect with the SD of
the paired difference:

| W | K0 | K0F | K0S | K0 -> K0S | K0F -> K0S | Stranded capacity-time, censored: K0 / K0F / K0S |
|---|---|---|---|---|---|---|
| 6 | 37.5 | 72.7 | 76.9 | +39.9 (24.5) | +4.4 (11.8) | 633 / 328 / 0 |
| unbounded | 75.6 | 76.6 | 76.6 | +1.0 (0.9); confirmed +14.6 (10.5) | +0.1 (2.2) | 1,093 / 410 / 0 |

The K0S device preset (`cmd/evicon-device -contract K0S`, release 072) advertises Q_S only; the
runtime and the device emulator support it since 072 (device test D11, runtime test F29).

## 2. Design

| Item | Value |
|---|---|
| Configs | `evaluation/campaigns/expS_w6.json`, `evaluation/campaigns/expS_winf.json` |
| Regime | identical to `expA_confirm_k0.json`, **same seed 20261017**, queue channel; the crash between effect and receipt stays at 0.2 of the fault windows, so orphans occur |
| Window | `max_outstanding` 6 (expS-w6) or 1000 (expS-winf) |
| Contracts | K0, K0S (= {Q_S}), K0F (= {F}) |
| Policy | EVICON |
| Episodes | **12** per config; 36 + 36 = 72 cells, about 8.6 hours |
| Pairing | within a run, the contracts of the same episode (common random numbers); across runs (estimands only) episodes with byte-identical schedule files |
| Activation gate | at least one acting EVICON decision in every VALID K0S and K0F cell |
| Metrics | `clean_pp`, `confirmed_pp` and the trace-derived success rates of `pairs_report.py`: `stops_reached_terminal / stops_reached` (stops the owner answered STOPPED, APPLIED or not-terminal) and `fences_reached_ok / fences_reached` (fences the owner answered 2xx or 409) |
| Analysis | `evaluation/pairs_report.py --plan evaluation/prereg/PREREG_expS_072.md --run s6=<run> --run sinf=<run> --out <dir>`, spec in section 8; `evicon_review.py report` runs it |

## 3. Hypotheses and decision rules

Tests of `pairs_report.py` on per-episode paired differences, α = 0.05, each hypothesis its own family.

| ID | Hypothesis | Decision rule |
|---|---|---|
| **H-S1** | W = 6: a per-command stop raises clean fulfilment by at least 10 pp over K0 | `at_least 10`, K0 -> K0S, `clean_pp`, run s6 |
| **H-S2** | W = 6: a stop that reaches the owner succeeds more often than a fence that reaches it (crash orphans block whole fences, but only their own stop) | `rate_greater`, run s6: K0S `stops_reached_terminal / stops_reached` minus K0F `fences_reached_ok / fences_reached`, episodes resampled jointly (bootstrap 4,000); MET when p < 0.05 |
| **H-S4** | Unbounded window: the stop still raises confirmed fulfilment by at least 5 pp | `at_least 5`, K0 -> K0S, `confirmed_pp`, run sinf |
| **H-S5** | Unbounded window: the stop does not change clean fulfilment | `equivalent 3` (TOST, ±3 pp), K0 -> K0S, `clean_pp`, run sinf |
| **H-S** | Safety holds in every VALID cell of both runs | bound violations, duplicate effects, logical duplicates, false STOPPED, false APPLIED all 0 |

Interpretation fixed in advance: if H-S5 is NOT MET with K0S behind, a stop costs fulfilment when the
window does not bind (it stops requests that the queue channel would still have delivered, which must
then be re-issued); the paper reports this as the cost of Q_S, with its size (E-S2 reports re-issues).

H-S3 of the plan of record (K0S at least as good as K0F at W = 6) is **not** tested: the simulator
predicts +4.4 pp with a paired SD of 11.8 pp, power 0.18 at 12 episodes. It is reported as estimand
E-S1 with its interval.

## 4. Estimands (reported with intervals, no claims)

| ID | Estimand |
|---|---|
| E-S1 | K0F -> K0S at W = 6 and unbounded: `clean_pp`, `confirmed_pp`, stranded capacity-time (resolved, censored), mean time to a terminal outcome |
| E-S2 | K0 -> K0F and K0 -> K0S, the same metrics plus stops, fences and re-issues after STOPPED per episode |
| E-S3 | Window effect, unbounded minus W = 6, K0S and K0F, across runs |
| E-S4 | Success rates, all attempts and attempts that reached the owner, per contract (levels table) |

## 5. Sample size

From the simulator's per-episode paired differences (resampling, decision rules above): at 12
episodes H-S1 has power 0.99, H-S4 0.97 and H-S5 above 0.95 (paired SD 0.9 pp against a ±3 pp margin).
H-S2 has no simulator counterpart (the simulator does not log per-attempt outcomes); the testbed
evidence of section 1 (about 45 % against 27-30 %, hundreds of attempts per run) makes 12 episodes
ample if the difference is of that size.

## 6. Exclusions, stopping and order

- INVALID cells are excluded and listed; re-run once by resuming; more than 10 % excluded makes the
  run not interpretable.
- No interim analysis. `expS_w6` first, then `expS_winf`. The earliest complete run of each config is
  the preregistered one.

## 7. Frozen files

SHA-256 at freezing time (`scripts/freeze_prereg.py`). The six files frozen for experiment A, plus
`pairs_report.py` and `cadence_report.py` (imported by it). The Go runtime is built from release 072.

```frozen-extra
evaluation/pairs_report.py
evaluation/cadence_report.py
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
```

## 8. Analysis spec (read by pairs_report.py)

```pairs-spec
{
  "name": "Experiment S: per-command stop against owner fence (072)",
  "runs": {"s6": {"campaign": "expS-w6"}, "sinf": {"campaign": "expS-winf"}},
  "alpha": 0.05,
  "max_invalid_fraction": 0.10,
  "hypotheses": [
    {"id": "H-S1", "family": "H-S1", "run": "s6", "policy": "EVICON", "from": "K0", "to": "K0S", "metric": "clean_pp", "test": "at_least", "margin": 10},
    {"id": "H-S2", "family": "H-S2", "run": "s6", "test": "rate_greater",
     "a": {"contract": "K0S", "policy": "EVICON", "num": "stops_reached_terminal", "den": "stops_reached"},
     "b": {"contract": "K0F", "policy": "EVICON", "num": "fences_reached_ok", "den": "fences_reached"}},
    {"id": "H-S4", "family": "H-S4", "run": "sinf", "policy": "EVICON", "from": "K0", "to": "K0S", "metric": "confirmed_pp", "test": "at_least", "margin": 5},
    {"id": "H-S5", "family": "H-S5", "run": "sinf", "policy": "EVICON", "from": "K0", "to": "K0S", "metric": "clean_pp", "test": "equivalent", "margin": 3}
  ],
  "safety": {"id": "H-S", "runs": ["s6", "sinf"]},
  "estimands": [
    {"run": "s6", "policy": "EVICON", "from": "K0F", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s"]},
    {"run": "sinf", "policy": "EVICON", "from": "K0F", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s"]},
    {"run": "s6", "policy": "EVICON", "from": "K0", "to": "K0S", "metrics": ["confirmed_pp", "admitted_pp", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s", "stops", "reissues", "window_defers"]},
    {"run": "s6", "policy": "EVICON", "from": "K0", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s", "fences", "window_defers"]},
    {"run": "sinf", "policy": "EVICON", "from": "K0", "to": "K0S", "metrics": ["admitted_pp", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s", "stops", "reissues", "window_defers"]},
    {"run": "sinf", "policy": "EVICON", "from": "K0", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp", "admitted_pp", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s", "fences", "window_defers"]},
    {"run": "sinf", "from_run": "s6", "policy": "EVICON", "from": "K0S", "to": "K0S", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "sinf", "from_run": "s6", "policy": "EVICON", "from": "K0F", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp"]}
  ]
}
```

## 9. Deviations

(none)

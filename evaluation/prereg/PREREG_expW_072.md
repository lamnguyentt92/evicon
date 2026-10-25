# Preregistration: experiment W (window and owner channel) on the 5-VM testbed

Release 072. Frozen on 2026-10-21, before any W cell runs. The four campaign configs below carry the
SHA-256 of this file, and `evicon_review.py campaign` refuses to run them if this file, or any file
listed under *Frozen files*, has changed. Changes after freezing are recorded under *Deviations*;
nothing above that section is edited.

## 1. Background (exploratory evidence, not part of the test)

**The window finding.** Up to release 071 the runtime enumerated the configurations of the unresolved
commands of a domain and deferred an admission when their number would exceed 256
(`CONFIGURATION_BUDGET_EXHAUSTED`). In the dosing domain this capped the number of unresolved commands
per device at about six, well below the configured `max_outstanding` of 16. In experiment A every K0
cell deferred about 2,050 decisions per episode for this reason (069 run), against about 800 on K0F.
The confirmed effect of the fence (K0 -> K0F, +30.7 pp for EVICON and +30.4 pp for RES_PERIODIC in
the 069 run; +29.9 and +28.1 pp in the 067 run) is therefore an effect *at a window of about six*.

Release 072 computes the belief of an ADD-only domain in product form (one configuration; executable
check `internal/evidence/product_test.go`: 10,000 random histories, same guard verdict as the
enumeration), so the window is now exactly `authority.max_outstanding`. Configs of releases up to 071
ran at an effective window of about 6; the same configs run with release 072 have a window of 16.

**Simulator sweep** (release 072 discrete-event simulator, twin `experiments/sim/iot_dosing_testbed.json`
with only the window and the channel changed, 200 episodes, EVICON; `SIMULATION_ONLY`). Clean
fulfilment / confirmed clean fulfilment, % of offered:

| Channel | W | K0 | K0F | K0 + Q_S | K2 | ORACLE |
|---|---|---|---|---|---|---|
| queue | 1 | 11.4 / 10.9 | 58.6 / 58.3 | 58.7 / 58.4 | 58.4 / 58.1 | 77.0 |
| queue | 6 | 37.5 / 34.3 | 72.7 / 71.8 | 76.9 / 76.5 | 76.8 / 76.4 | 76.8 |
| queue | 16 | 56.1 / 48.6 | 76.2 / 75.0 | 76.7 / 76.3 | 76.7 / 76.3 | 76.7 |
| queue | unbounded | 75.6 / 61.7 | 76.6 / 75.4 | 76.6 / 76.2 | 76.6 / 76.1 | 76.6 |
| direct | 6 | 32.5 / 31.5 | 69.9 / 69.4 | 77.1 / 76.8 | 76.3 / 75.9 | 77.0 |
| direct | unbounded | 62.3 / 60.5 | 75.6 / 75.0 | 77.3 / 76.9 | 76.5 / 76.1 | 76.9 |

Mean per-episode effect K0 -> K0F (pp; SD of the paired difference in brackets): queue W = 6
+35.6 (25.3); queue unbounded +1.0 (2.2), confirmed +13.7 (10.7); direct W = 6 +37.9 (26.3);
direct unbounded +13.4 (11.5). RES_PERIODIC: queue W = 6 +34.1, queue unbounded +1.0 (clean) and
+13.3 (confirmed). The simulator over-predicted the testbed effect at W = 6 by about 5 pp
(+35.6 against +30.7), and its per-episode SD is larger than the testbed's (25 against 14-16 pp).

Reading: the clean-fulfilment value of a fence on K0 is a *window* effect. With a channel that
eventually delivers (queue) and an unbounded window, K0 already fulfils almost every request that
the bound allows; the fence then buys *knowledge* (confirmed fulfilment) and released capacity, not
fulfilment. With a lossy channel (direct: a request sent to an offline device is lost), lost requests
keep their reservation forever under K0, and the fence keeps a clean-fulfilment value at any window.

## 2. Design

| Item | Value |
|---|---|
| Configs | `expW_queue_w6.json`, `expW_queue_winf.json`, `expW_direct_w6.json`, `expW_direct_winf.json` (in `evaluation/campaigns/`) |
| Regime | identical to `expA_confirm_k0.json` (4 devices; 300 s per cell + 60 s tail; 0.5 commands/s per device; values 1-5; bound 345; deadline 60 s; validity 90 s; fault windows 2/min, offline 10-40 s, mix offline 0.3, drop_up 0.2, drop_down 0.2, delay_up 0.1, crash between effect and receipt 0.2; authority parameters) and the **same seed, 20261017**. Only `authority.max_outstanding` and the device channel differ |
| Window W | `max_outstanding` **6** (the effective window of experiment A) or **1000** (unbounded: the bound of 345 units caps the reservations first) |
| Channel | **queue**: `evicon-device` default (`-offline-mode queue`), a request sent while the device is offline is delivered when it reconnects, as in experiment A. **direct**: `-offline-mode drop` (`faults.device_args`), such a request is lost |
| Contracts | K0, K0F |
| Policies | queue: EVICON, RES_PERIODIC. direct: EVICON |
| Episodes | **12** per config (0-11); 48 + 48 + 24 + 24 = 144 cells, about 17 hours |
| Pairing | within a run: contracts of the same episode and policy (common random numbers). Across runs (estimands only): episodes whose `schedule_ep<e>.json` files are byte-identical; with one seed the four runs and the 067/069 runs of `expA_confirm_k0` share episodes 0-11 |
| Activation gate | at least one acting EVICON decision in every VALID K0F cell |
| Primary metrics | `clean_pp`: `clean_fulfilled` of `campaign.py` in percentage points of offered requests. `confirmed_pp`: clean, and the client saw terminal APPLIED for the identity that took effect within the deadline (`pairs_report.py`, recomputed from `clients.jsonl` and `plant.jsonl`) |
| Analysis | `evaluation/pairs_report.py --plan evaluation/prereg/PREREG_expW_072.md --run q6=<run> --run qinf=<run> --run d6=<run> --run dinf=<run> --out <dir>`, with the spec in section 8. `evicon_review.py report` runs it with the earliest complete run of each config among its sources |

## 3. Hypotheses and decision rules

Tests of `pairs_report.py` on the per-episode paired differences (to minus from), Student t, α = 0.05;
Holm within each hypothesis over its policies.

| ID | Hypothesis | Decision rule |
|---|---|---|
| **H-W1** | Queue, W = 6: the fence raises clean fulfilment by at least 10 pp (replicates experiment A at its effective window) | `at_least 10`, K0 -> K0F, `clean_pp`, run q6, EVICON and RES_PERIODIC; MET for a policy when its Holm-adjusted p < 0.05 |
| **H-W2** | Queue, unbounded window: the fence does not change clean fulfilment | `equivalent 3` (TOST, ±3 pp), K0 -> K0F, `clean_pp`, run qinf, EVICON and RES_PERIODIC |
| **H-W3** | Queue, unbounded window: the fence still raises confirmed fulfilment, by at least 5 pp | `at_least 5`, K0 -> K0F, `confirmed_pp`, run qinf, EVICON and RES_PERIODIC |
| **H-W4** | Direct, unbounded window: the fence raises clean fulfilment by at least 5 pp (lost requests strand capacity under K0) | `at_least 5`, K0 -> K0F, `clean_pp`, run dinf, EVICON |
| **H-W5** | Direct, W = 6: the fence raises clean fulfilment by at least 10 pp | `at_least 10`, K0 -> K0F, `clean_pp`, run d6, EVICON |
| **H-S** | Safety holds in every VALID cell of the four runs, at both windows (Theorem 2 needs no window) | `pairs_report.py` safety: bound violations, duplicate effects, logical duplicates, false STOPPED, false APPLIED all 0. Late effects reported separately |

Interpretation fixed in advance:
- H-W1, H-W2 and H-W3 MET: the clean-fulfilment value of F in experiment A is a window effect; with an
  unbounded window on a delivering channel, F's value is knowledge (confirmation) and capacity, not
  fulfilment. The paper reports experiment A as "at W = 6" and this result as the window dependence.
- H-W2 NOT MET with K0F ahead: the window explains part of the effect; the paper reports the residual.
- H-W2 NOT MET with K0F behind: when the window no longer binds, a fence costs fulfilment. It seals
  requests that the queue channel would still have delivered (they become STOPPED and must be
  re-issued; E-W2 reports the re-issues) and it pauses admissions while a barrier is pending. The
  paper reports this as the cost of F, with its size. (Seen in the 072 pre-release check on the
  simulated testbed, 1 episode of 180 s without capacity pressure: RES_PERIODIC 344 against 360 clean
  requests; EVICON 364 against 360. The simulator twin, with capacity pressure, predicts +1.0 pp.)
- H-W4 MET: on a lossy channel F keeps a fulfilment value at any window. NOT MET: the direct channel
  does not strand enough capacity in this regime; reported as such.

## 4. Estimands (reported with intervals, no claims)

| ID | Estimand |
|---|---|
| E-W1 | Window effect, unbounded minus W = 6, same contract and policy, across runs (same seed, identical schedules): queue and direct, K0 and K0F, `clean_pp` and `confirmed_pp` |
| E-W2 | Per run, K0 -> K0F: `confirmed_pp`, admitted, stranded capacity-time (resolved, censored), mean time to a terminal outcome, fences, re-issues after STOPPED, window-bound DEFER decisions |
| E-W3 | Channel effect, direct minus queue, same W, EVICON, across runs: K0 and K0F, `clean_pp` |
| E-W4 | Consistency with experiment A: run q6 minus the earliest `expA-confirm-k0` run among the sources (same seed, effective window about 6 there), K0 and K0F, EVICON and RES_PERIODIC, `clean_pp` (expected about 0) |
| E-W5 | Fence success rate, all attempts and attempts that reached the owner (levels table) |

## 5. Sample size

Power from the simulator's 200 per-episode paired differences, resampling n episodes and applying the
decision rules above with Holm (2,000 resamples; `SIMULATION_ONLY`, conservative because the
simulator's per-episode SD is larger than the testbed's):

| n | H-W1 (EVICON / RES) | H-W2 | H-W3 | H-W4 | H-W5 |
|---|---|---|---|---|---|
| 8 | 0.76 / 0.71 | 0.83 / 0.88 | 0.57 / 0.53 | 0.58 | 0.85 |
| **12** | 0.96 / 0.94 | 0.90 / 0.91 | 0.88 / 0.85 | 0.83 | 0.97 |
| 16 | 0.99 / 0.99 | 0.91 / 0.92 | 0.97 / 0.97 | 0.94 | 1.00 |

Twelve episodes per config. With the testbed's SD (about 0.6 of the simulator's in experiment A) the
power is higher.

## 6. Exclusions, stopping and order

- INVALID cells are excluded and listed. An INVALID cell is re-run once by resuming the campaign; if it
  is INVALID again it stays excluded. A run with more than 10 % excluded cells is not interpretable and
  its hypotheses are not decided.
- No interim analysis: `pairs_report.py` is run only when every cell of a run is done. A campaign may
  be split over several sessions (resume).
- Order: `expW_queue_w6`, then `expW_queue_winf` and `expW_direct_w6`, then `expW_direct_winf`.
  The earliest complete run of each config is the preregistered one.

## 7. Frozen files

SHA-256 at freezing time (`scripts/freeze_prereg.py`); the driver checks them before running. The six
files frozen for experiment A are frozen again here, unchanged. `pairs_report.py` and
`cadence_report.py` (whose t-distribution and bootstrap functions it imports) are added through the
`frozen-extra` block.

The Go runtime is built from release 072; the run's `source.json` records its source.

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
  "name": "Experiment W: window and owner channel (072)",
  "runs": {"q6": {"campaign": "expW-queue-w6"}, "qinf": {"campaign": "expW-queue-winf"},
           "d6": {"campaign": "expW-direct-w6"}, "dinf": {"campaign": "expW-direct-winf"},
           "expA": {"campaign": "expA-confirm-k0", "reference": true}},
  "alpha": 0.05,
  "max_invalid_fraction": 0.10,
  "hypotheses": [
    {"id": "H-W1/EVICON", "family": "H-W1", "run": "q6", "policy": "EVICON", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "at_least", "margin": 10},
    {"id": "H-W1/RES_PERIODIC", "family": "H-W1", "run": "q6", "policy": "RES_PERIODIC", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "at_least", "margin": 10},
    {"id": "H-W2/EVICON", "family": "H-W2", "run": "qinf", "policy": "EVICON", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "equivalent", "margin": 3},
    {"id": "H-W2/RES_PERIODIC", "family": "H-W2", "run": "qinf", "policy": "RES_PERIODIC", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "equivalent", "margin": 3},
    {"id": "H-W3/EVICON", "family": "H-W3", "run": "qinf", "policy": "EVICON", "from": "K0", "to": "K0F", "metric": "confirmed_pp", "test": "at_least", "margin": 5},
    {"id": "H-W3/RES_PERIODIC", "family": "H-W3", "run": "qinf", "policy": "RES_PERIODIC", "from": "K0", "to": "K0F", "metric": "confirmed_pp", "test": "at_least", "margin": 5},
    {"id": "H-W4", "family": "H-W4", "run": "dinf", "policy": "EVICON", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "at_least", "margin": 5},
    {"id": "H-W5", "family": "H-W5", "run": "d6", "policy": "EVICON", "from": "K0", "to": "K0F", "metric": "clean_pp", "test": "at_least", "margin": 10}
  ],
  "safety": {"id": "H-S", "runs": ["q6", "qinf", "d6", "dinf"]},
  "estimands": [
    {"run": "qinf", "from_run": "q6", "policy": "EVICON", "from": "K0", "to": "K0", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "qinf", "from_run": "q6", "policy": "EVICON", "from": "K0F", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "qinf", "from_run": "q6", "policy": "RES_PERIODIC", "from": "K0", "to": "K0", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "qinf", "from_run": "q6", "policy": "RES_PERIODIC", "from": "K0F", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "dinf", "from_run": "d6", "policy": "EVICON", "from": "K0", "to": "K0", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "dinf", "from_run": "d6", "policy": "EVICON", "from": "K0F", "to": "K0F", "metrics": ["clean_pp", "confirmed_pp"]},
    {"run": "q6", "policy": "EVICON", "from": "K0", "to": "K0F", "metrics": ["confirmed_pp", "admitted_pp", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s", "fences", "reissues", "window_defers"]},
    {"run": "q6", "policy": "RES_PERIODIC", "from": "K0", "to": "K0F", "metrics": ["confirmed_pp", "admitted_pp", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s", "fences", "reissues", "window_defers"]},
    {"run": "qinf", "policy": "EVICON", "from": "K0", "to": "K0F", "metrics": ["admitted_pp", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s", "fences", "reissues", "window_defers"]},
    {"run": "qinf", "policy": "RES_PERIODIC", "from": "K0", "to": "K0F", "metrics": ["admitted_pp", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s", "fences", "reissues", "window_defers"]},
    {"run": "d6", "policy": "EVICON", "from": "K0", "to": "K0F", "metrics": ["confirmed_pp", "admitted_pp", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s", "fences", "reissues", "window_defers"]},
    {"run": "dinf", "policy": "EVICON", "from": "K0", "to": "K0F", "metrics": ["confirmed_pp", "admitted_pp", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s", "fences", "reissues", "window_defers"]},
    {"run": "d6", "from_run": "q6", "policy": "EVICON", "from": "K0", "to": "K0", "metrics": ["clean_pp"]},
    {"run": "d6", "from_run": "q6", "policy": "EVICON", "from": "K0F", "to": "K0F", "metrics": ["clean_pp"]},
    {"run": "dinf", "from_run": "qinf", "policy": "EVICON", "from": "K0", "to": "K0", "metrics": ["clean_pp"]},
    {"run": "dinf", "from_run": "qinf", "policy": "EVICON", "from": "K0F", "to": "K0F", "metrics": ["clean_pp"]},
    {"run": "q6", "from_run": "expA", "policy": "EVICON", "from": "K0", "to": "K0", "metrics": ["clean_pp"]},
    {"run": "q6", "from_run": "expA", "policy": "EVICON", "from": "K0F", "to": "K0F", "metrics": ["clean_pp"]},
    {"run": "q6", "from_run": "expA", "policy": "RES_PERIODIC", "from": "K0", "to": "K0", "metrics": ["clean_pp"]},
    {"run": "q6", "from_run": "expA", "policy": "RES_PERIODIC", "from": "K0F", "to": "K0F", "metrics": ["clean_pp"]}
  ]
}
```

## 9. Deviations

(none)

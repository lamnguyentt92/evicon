# Plan: experiment L (sleepy devices, late effects) on the 5-VM testbed: EXPLORATORY

Release 072. Written and frozen on 2026-10-21, before any L cell runs, like a preregistration (the
config carries the SHA-256 of this file and the driver checks it), but **exploratory**: its checks
are descriptive, they are not confirmatory hypotheses, and the paper reports them as such.

## 1. Background

The theory table of the paper says that owner-enforced expiry T **bounds late effects only**: an
owner that drops a command older than its expiry never executes it late, but T alone does not tell
the authority anything (it releases no capacity unless the expiry is revealed, ER = T ∧ (X ∨ (Q_A ∧
D_∞))). A broker-side message expiry is not the same thing: the device can still execute what it
already received (paper, HM2).

Sleepy devices are the IoT case: a device that sleeps longer than a request's validity receives the
request, queued by the channel, after the validity has passed.

Simulator (release 072, twin with offline periods of mean 120 s and window 6, 200 episodes, EVICON;
`SIMULATION_ONLY`): K2 63.1 % clean, **2.42 late effects per device-episode** (89 of 200 episodes
with at least one); K3T (K2 + expiry 30 s) 63.7 % clean, **0 late effects**, with capacity held until
the expiry is revealed (stranded capacity-time, resolved, 1,602 against 0 unit·s).

In the runtime (release 072) an expired command is resolved only by the owner's own answer
(STOPPED for a command it dropped as expired); NOT_FOUND is never turned into STOPPED (paper rule
(A4) is applied by the simulator only; runtime test F41).

## 2. Design

| Item | Value |
|---|---|
| Config | `evaluation/campaigns/expL_sleepy.json` |
| Regime | `expA_confirm_k0.json` except: offline periods **60-180 s** (longer than the 90 s validity), window 6, new seed **20261024**; queue channel |
| Contracts | K2 (D_∞, Q_A), K3T (D_∞, Q_A, T with 30 s expiry) |
| Policy | EVICON |
| Episodes | 8; 16 cells, about 2 hours |
| Analysis | `evaluation/pairs_report.py --plan evaluation/prereg/PLAN_expL_072.md --run L=<run> --out <dir>` |

## 3. Descriptive checks (exploratory)

| ID | Expectation | Check |
|---|---|---|
| L1 | Without T, a sleepy device executes requests after their validity | `detects`: some VALID K2 cell has `late_effects` > 0 |
| L2 | With T, it never does | `absent`: no VALID K3T cell has `late_effects` > 0 |
| H-S | Safety (late effects excluded: they are the measured quantity) | bound violations, duplicate effects, logical duplicates, false STOPPED, false APPLIED all 0 |

Estimands: K2 -> K3T `clean_pp`, `confirmed_pp`, late effects, stranded capacity-time (resolved,
censored), mean time to a terminal outcome.

## 4. Exclusions

INVALID cells are excluded and listed; more than 10 % excluded makes the run not interpretable.

## 5. Frozen files

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

## 6. Analysis spec (read by pairs_report.py)

```pairs-spec
{
  "name": "Experiment L: sleepy devices and late effects (072, exploratory)",
  "runs": {"L": {"campaign": "expL-sleepy"}},
  "alpha": 0.05,
  "max_invalid_fraction": 0.10,
  "hypotheses": [
    {"id": "L1", "run": "L", "test": "detects", "contract": "K2", "policy": "EVICON", "any_of": ["late_effects"]},
    {"id": "L2", "run": "L", "test": "absent", "contract": "K3T", "policy": "EVICON", "any_of": ["late_effects"]}
  ],
  "safety": {"id": "H-S", "runs": ["L"]},
  "estimands": [
    {"run": "L", "policy": "EVICON", "from": "K2", "to": "K3T", "metrics": ["clean_pp", "confirmed_pp", "late_effects", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "mean_time_to_terminal_s"]}
  ]
}
```

## 7. Deviations

(none)

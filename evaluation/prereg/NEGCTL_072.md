# Plan: negative controls of the testbed checker

Release 072. Frozen on 2026-10-21, before the negative-control cells run (the config carries the
SHA-256 of this file and the driver checks it).

## 1. Why

Every confirmatory result so far reports "0 safety violations". That statement is only as strong
as the checker's ability to see a violation when there is one. The TLA+ model has negative controls
(`sealorphans`, `blindretry`): variants that break a premise and must produce a counterexample. This
is their testbed counterpart: devices that **lie about their own contract** on purpose.

| Preset (`cmd/evicon-device -contract`) | Advertises | Breaks it by | Expected violation |
|---|---|---|---|
| `NEG_SEAL` | F (owner fence) | sealing a crash orphan (a claim whose effect happened, receipt missing) as STOPPED, with reason `NEG_CONTROL_SEALED_ORPHAN` | false STOPPED; with `reissue_after_stopped`, a logical duplicate (the request takes effect under two identities) and possibly a bound violation |
| `NEG_DEDUP` | D_∞, Q_A (K2) | executing a duplicate delivery of an applied command again | duplicate effects; possibly a bound violation |

Device tests D12 and D13 (release 072) check each preset in isolation.

## 2. Design

| Item | Value |
|---|---|
| Config | `evaluation/campaigns/negctl_072.json` |
| Regime | `expA_confirm_k0.json`, same seed 20261017 (crash between effect and receipt 0.2 of the fault windows, reply loss 0.2), window 6 |
| Contracts | NEG_SEAL, NEG_DEDUP |
| Policy | EVICON |
| Episodes | 4; 8 cells, about 1 hour |
| Driver | a campaign whose contracts are all `NEG_*` is a negative control: `evicon_review.py campaign` PASSES when the checker reports violations and FAILS when it reports none (release 072) |

## 3. Rule

| ID | Rule |
|---|---|
| NEG-SEAL | `detects`: some VALID NEG_SEAL cell has false STOPPED or a logical duplicate (or a bound violation) |
| NEG-DEDUP | `detects`: some VALID NEG_DEDUP cell has duplicate effects (or a bound violation) |

If either rule fails, the harness is not sensitive to that violation: every "0 violations" result is
re-examined and the plan of record stops until the cause is fixed.

## 4. Frozen files

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

## 5. Analysis spec (read by pairs_report.py)

```pairs-spec
{
  "name": "Negative controls of the testbed checker (072)",
  "runs": {"neg": {"campaign": "negctl-072"}},
  "alpha": 0.05,
  "max_invalid_fraction": 0.25,
  "hypotheses": [
    {"id": "NEG-SEAL", "run": "neg", "test": "detects", "contract": "NEG_SEAL", "policy": "EVICON", "any_of": ["false_stopped", "logical_duplicates", "bound_violations"]},
    {"id": "NEG-DEDUP", "run": "neg", "test": "detects", "contract": "NEG_DEDUP", "policy": "EVICON", "any_of": ["duplicate_effects", "bound_violations"]}
  ]
}
```

## 6. Deviations

(none)

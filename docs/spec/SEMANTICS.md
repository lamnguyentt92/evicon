# Executable semantics of the admission core

> The domain, hard-bound grammar, belief and guard that
> `internal/{domain,belief,evidence,analyzer}` implement. The evidence-contract
> layer is specified in `internal/contract/lattice.go`.

## 1. Domain and state

A domain has one state owner, generation `g`, and a resource state `x ∈ Z^m`, with `m ∈ {1,2}` in the prototype. Commands are identified by stable command IDs within a generation.

The supported hard-bound grammar is:

```text
H(x) = componentMin <= x <= componentMax  AND  sum(x) <= sumMax
```

Each bound family is optional. Omitted bounds are represented by absence, never by sentinel infinity values.

The operation grammar is deliberately restricted to:

- `ADD(v)`, with every component `v[j] >= 0`;
- `SET(v)`, replacing the whole vector.

Negative ADD is outside the current prototype semantics and is rejected by command validation.

## 2. Command lifecycle

Every tracked command has exactly one explicit model status in every configuration:

- `PENDING`: the effect may still occur;
- `APPLIED`: the effect is already reflected in the configuration state and must not be applied again;
- `STOPPED`: the effect has not occurred and can no longer occur in that configuration.

A configuration is invalid if a command in the registry has no status. Absence is never interpreted as terminal completion.

`APPLIED` and `STOPPED` are terminal in this executable core. P1/P2 must justify how runtime evidence is allowed to establish those states for each backend profile.

## 3. Fixed epoch and SET ordering

Release 005 uses fixed-epoch command application:

```text
command.generation == domain.generation
command.epoch      == configuration.installedEpoch
```

Both stale (`command.epoch < installedEpoch`) and future (`command.epoch > installedEpoch`) commands are unable to effect the current configuration. This intentionally fixes the v004 mismatch where future-epoch commands were treated as applicable.

A pending SET additionally requires:

```text
command.sequence >= configuration.setFrontier
```

When such a SET applies, `setFrontier` becomes the maximum of the existing frontier and that SET sequence. ADD does not change the frontier.

Release 005 does not contain an epoch-install transition. A later runtime must not silently make a future-epoch command executable without an explicit, specified epoch-install boundary.

## 4. Transition relation

For a pending command, the core admits two one-step terminal possibilities:

1. `PENDING -> STOPPED`; or
2. `PENDING -> APPLIED` if the application guards above hold.

For `APPLIED`:

```text
ADD(v): x' = x + v
SET(v): x' = v
```

All arithmetic that represents state uses checked signed 64-bit integer semantics. An arithmetic overflow is not a safety counterexample. Analysis returns `DEFER` with reason `ARITHMETIC_OVERFLOW`.

## 5. Belief and robust admission

A belief `B_t` is a non-empty set of configurations not yet ruled out. Adding candidate `a` inserts `a:PENDING` into every configuration.

The safety condition is:

```text
Safe(B_t, a) iff every reachable configuration from Insert(B_t, a)
satisfies H at every intermediate state.
```

The executable finite analyzer is the reference implementation of this reachability definition for bounded state spaces.

Coverage is a runtime obligation:

```text
true_configuration_t ∈ B_t
```

Release 005 validates supplied beliefs but does not yet construct them from reservation/network/evidence traces. Online belief construction is a P1/P2 requirement and must not be claimed from this release.

## 6. Analyzer outcome semantics

Analyzer output deliberately separates proof status from the admission decision.

| Result verdict | Meaning | Admission mapping |
|---|---|---|
| `SAFE` | Analyzer established safety in its declared scope | `ADMIT` |
| `COUNTEREXAMPLE` | Analyzer has an exact/reachable violating witness in its declared scope | `BLOCK` |
| `CONSERVATIVE_BLOCK` | A sound over-approximation cannot establish safety; a violating concrete execution need not exist | `BLOCK` |
| `DEFER` | Resource/arithmetic budget prevents a sound decision | `DEFER` |
| `NOT_APPLICABLE` | Input is outside analyzer/contract scope | `REJECT` |

In this release, JoinedGuard and IntervalGuard can return `CONSERVATIVE_BLOCK`. Finite, Scalar, SumGuard, and RelationalGuard operate as exact analyzers only under their documented assumptions and therefore report `COUNTEREXAMPLE` for bound failure in that scope.

## 7. Relational envelope theorem scope

For one concrete configuration `z`, define:

- `Q_z`: current state plus the target of each still-applicable pending SET;
- `A_z`: still-applicable pending non-negative ADD commands, including the candidate when applicable;
- `s_z`: component-wise sum of ADD vectors in `A_z`.

Under all assumptions below:

1. generation is fixed;
2. application requires exact epoch equality;
3. ADD vectors are non-negative;
4. SET replaces the whole vector;
5. no additional state-dependent precondition/dependency exists;
6. pending commands may STOP independently;
7. ADD has no order restriction beyond generation/epoch;
8. SET frontier is enforced exactly as in the transition engine;
9. arithmetic does not overflow int64;
10. the only constraints are component lower/upper bounds and an optional sum upper bound;

then the current prototype uses the envelope condition:

```text
RobustSafe({z}, a) iff for every b in Q_z: H(b) and H(b + s_z).
```

Why the endpoints suffice: for any selected base, every reachable ADD suffix contributes a component-wise subset of `s_z`. Non-negative ADD makes upper constraints monotone; lower constraints are tightest at the base. Because pending effects may independently stop, the base and the all-applicable-ADD extreme are reachable under the stated operation model.

This theorem is not claimed for negative ADD, partial SET, state-dependent command preconditions, cross-domain atomicity, or stronger operation grammars.

## 8. Analysis budgets

The finite reference defaults remain:

- maximum outstanding tracked commands including candidate: 4;
- maximum retained configurations: 256;
- sensitivity configuration budget: 4096 where explicitly requested.

Crossing a configured cap returns `DEFER`; no world is discarded to manufacture an `ADMIT` result.

## 9. Evidence semantics reserved for P1/P2

The runtime plan distinguishes terminal evidence `APPLIED` and `STOPPED` from `PENDING`, `UNCERTAIN`, `NOT_FOUND`, timeout, and ordinary state reads. Release 005 does not implement receipt or barrier protocols, so it does not use those observations to narrow beliefs.

Future P1/P2 reducers must preserve coverage, reject identity/version mismatch, reject empty-belief updates as model/contract inconsistency, and serialize evidence/admission by domain revision as specified in the approved plan.

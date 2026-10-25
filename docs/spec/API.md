# HTTP API

All bodies are JSON with `schema_version: 1`. Identities are `(domain_id, generation,
command_id)`; `intent_hash` binds an identity to its logical intent and may be omitted on
submission (it is then computed from the canonical intent and must match if supplied).

## Authority (`evicon-authority`)

| Route | Purpose |
|---|---|
| `POST /v1/commands` | Submit a command. Decision `ADMITTED` (202), `DEFER` (503, with a reason such as `OUTSTANDING_BUDGET_EXHAUSTED`, `BARRIER_IN_PROGRESS`, `EVICON_*`) or `REJECT` (e.g. `SEQUENCE_NOT_MONOTONIC`). Sequences must exceed every admitted sequence of the domain |
| `GET /v1/commands/{id}` | Status of one identity: `PENDING`, `APPLIED`, `STOPPED` |
| `POST /v1/evidence/query` | Operator-triggered receipt query for one identity |
| `POST /v1/barriers` | Operator-triggered full-frontier barrier (pause, owner fence, anchor) |
| `GET /healthz` | Liveness |
| `GET /_debug/runtime`, `GET /_debug/belief` | Artifact-only introspection; never read by admission |

Flags that matter for EVICON: `-policy EVICON`, `-resolve-mode`, `-resolve-trigger`,
`-resolve-sweep`, `-ambiguity-after`, `-retry-guard`, `-evidence-contract-file` (`MEASURED`
contracts need per-capability provenance).

## Owner protocol (implemented by `evicon-endpoint` E1/E2/E3 and `evicon-device`)

| Route | Capability | Purpose |
|---|---|---|
| `POST /v1/effects` | — | Execute a command; returns a receipt (`APPLIED`, `STOPPED`, or non-terminal `UNCERTAIN`) |
| `GET /v1/receipts/{id}` | Q_A | Receipt of one identity; `NOT_FOUND` and `UNCERTAIN` are never terminal by themselves |
| `POST /v1/stop` | Q_S | Per-identity stop: returns `APPLIED` if the effect happened, `STOPPED` if it can never happen, `UNCERTAIN` if the owner cannot certify the past (crash orphan) |
| `POST /v1/barriers` | F | Install a durable cutoff and return receipts for every identity at or below it. A non-terminal receipt makes the authority abort the barrier |
| `GET /v1/capabilities` | — | Handshake: profile, domain, bounds, and (devices) the advertised evidence contract |
| `GET /v1/state` | — | Ordinary state read (not an anchor) |
| `GET /healthz` | — | Liveness |

Devices are served under `/d/<device-id>/…`. Test-only admin routes (separate listener):
`POST /_test/online?v=true|false`, `POST /_test/crash?at=<point>|NONE`, `GET /_debug/state`.

## Plant (`evicon-plant`, truth channel)

| Route | Purpose |
|---|---|
| `POST /v1/apply` (and the line protocol on `-unix`/`-tcp`) | Record a physical effect `{device, cmd, value}` |
| `GET /v1/truth?cmd=&device=` | Effects of one identity, first/last effect time |
| `GET /v1/truth/summary` | Totals, identities with duplicate effects, per-device level, max level and limit check |
| `POST /v1/limits?device=&limit=` | Register the safety limit checked by the summary |

## Firmware (MQTT 3.1.1, `firmware/zephyr-cutoff`)

Topics `evicon/<dev>/{cmd,query,stop,fence}`, flat JSON, reply topic in the payload; see
`src/evc_proto.h` and `firmware/zephyr-cutoff/README.md`. Queries carry `seq`.

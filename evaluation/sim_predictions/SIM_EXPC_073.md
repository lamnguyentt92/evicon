# Simulator prediction for experiment C (release 073, SIMULATION_ONLY)

200 episodes; base `iot_dosing_testbed.json`, window 6, direct owner channel; contracts as measured on MinIO in 070.

| contract | policy (sim) | clean % | confirmed % | stranded resolved / censored (unit*s per episode) | stops / episode | safety violations (episodes) | late effects |
|---|---|---|---|---|---|---|---|
| MINIO_PLAIN | EVICON (EVICON) | 32.45 | 31.55 | 0.0 / 3900.7 | 0.0 | 0 (0) | 0 |
| MINIO_PLAIN | M1 (M1) | 32.45 | 31.55 | 0.0 / 3900.7 | 0.0 | 0 (0) | 0 |
| MINIO_PLAIN | NAIVE (B1_DURABLE_RETRY) | 74.54 | 74.54 | 0.0 / 0.0 | 0.0 | 1547 (193) | 26 |
| MINIO_PLAIN | ORACLE (ORACLE) | 77.01 | 77.01 | 539.0 / 0.0 | 0.0 | 0 (0) | 1 |
| MINIO_COND | EVICON (EVICON) | 77.17 | 76.79 | 694.8 / 0.0 | 51.9 | 0 (0) | 0 |
| MINIO_COND | M1 (M1) | 32.45 | 31.55 | 0.0 / 3900.7 | 0.0 | 0 (0) | 0 |
| MINIO_COND | NAIVE (B1_DURABLE_RETRY) | 76.75 | 76.35 | 0.0 / 0.0 | 0.0 | 0 (0) | 23 |
| MINIO_COND | ORACLE (ORACLE) | 77.01 | 77.01 | 539.0 / 0.0 | 0.0 | 0 (0) | 1 |

## Power of the C decision rules under the simulator's paired differences (Holm over C3-C5)

| n | ID | policy | comparison | metric | rule | sim mean (SD) | power |
|---|---|---|---|---|---|---|---|
| 6 | C3 | EVICON | MINIO_PLAIN -> MINIO_COND | clean_fulfilled | at_least 10 | +45.26 (24.05) | 0.89 |
| 6 | C4 | EVICON | MINIO_PLAIN -> MINIO_COND | clean_fulfilled_confirmed | at_least 10 | +45.79 (23.55) | 0.91 |
| 8 | C3 | EVICON | MINIO_PLAIN -> MINIO_COND | clean_fulfilled | at_least 10 | +45.26 (24.05) | 0.98 |
| 8 | C4 | EVICON | MINIO_PLAIN -> MINIO_COND | clean_fulfilled_confirmed | at_least 10 | +45.79 (23.55) | 0.99 |

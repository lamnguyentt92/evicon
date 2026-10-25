# Simulator prediction for experiment C2 (release 074, SIMULATION_ONLY)

200 episodes; base `iot_dosing_testbed.json`, window 6, queue owner channel; the contracts C2 expects the Zephyr firmware's modes to measure (experiments/owners/zephyr/pred-*.json).

| contract | policy (sim) | clean % | confirmed % | stranded resolved / censored (unit*s per episode) | stops / episode | safety violations (episodes) | late effects |
|---|---|---|---|---|---|---|---|
| ZEPHYR_PLAIN | EVICON (EVICON) | 37.46 | 34.28 | 0.0 / 633.5 | 0.0 | 0 (0) | 10 |
| ZEPHYR_PLAIN | M1 (M1) | 37.46 | 34.28 | 0.0 / 633.5 | 0.0 | 0 (0) | 10 |
| ZEPHYR_PLAIN | NAIVE (B1_DURABLE_RETRY) | 69.41 | 69.41 | 0.0 / 0.0 | 0.0 | 19816 (200) | 529 |
| ZEPHYR_PLAIN | ORACLE (ORACLE) | 76.83 | 76.83 | 0.0 / 0.0 | 0.0 | 0 (0) | 18 |
| ZEPHYR_CUTOFF | EVICON (EVICON) | 76.56 | 76.18 | 36.8 / 231.3 | 0.0 | 0 (0) | 6 |
| ZEPHYR_CUTOFF | M1 (M1) | 37.46 | 34.28 | 0.0 / 633.5 | 0.0 | 0 (0) | 10 |
| ZEPHYR_CUTOFF | NAIVE (B1_DURABLE_RETRY) | 76.83 | 76.44 | 0.0 / 0.0 | 0.0 | 0 (0) | 18 |
| ZEPHYR_CUTOFF | ORACLE (ORACLE) | 76.83 | 76.83 | 0.0 / 0.0 | 0.0 | 0 (0) | 18 |
| ZEPHYR_STOP | EVICON (EVICON) | 76.83 | 76.46 | 31.5 / 0.0 | 41.7 | 0 (0) | 18 |
| ZEPHYR_STOP | M1 (M1) | 37.46 | 34.28 | 0.0 / 633.5 | 0.0 | 0 (0) | 10 |
| ZEPHYR_STOP | NAIVE (B1_DURABLE_RETRY) | 76.83 | 76.44 | 0.0 / 0.0 | 0.0 | 0 (0) | 18 |
| ZEPHYR_STOP | ORACLE (ORACLE) | 76.83 | 76.83 | 0.0 / 0.0 | 0.0 | 0 (0) | 18 |

## Power of the C2 decision rules under the simulator's paired differences (Holm over C2-3 to C2-5)

| n | ID | policy | comparison | metric | rule | sim mean (SD) | power |
|---|---|---|---|---|---|---|---|
| 6 | C2-3 | EVICON | ZEPHYR_PLAIN -> ZEPHYR_CUTOFF | clean_fulfilled | at_least 10 | +39.60 (24.64) | 0.52 |
| 6 | C2-4 | EVICON | ZEPHYR_PLAIN -> ZEPHYR_CUTOFF | clean_fulfilled_confirmed | at_least 10 | +42.42 (25.16) | 0.6 |
| 6 | C2-5 | EVICON | ZEPHYR_CUTOFF -> ZEPHYR_STOP | clean_fulfilled | at_least 0 | +0.28 (2.84) | 0.01 |
| 8 | C2-3 | EVICON | ZEPHYR_PLAIN -> ZEPHYR_CUTOFF | clean_fulfilled | at_least 10 | +39.60 (24.64) | 0.79 |
| 8 | C2-4 | EVICON | ZEPHYR_PLAIN -> ZEPHYR_CUTOFF | clean_fulfilled_confirmed | at_least 10 | +42.42 (25.16) | 0.85 |
| 8 | C2-5 | EVICON | ZEPHYR_CUTOFF -> ZEPHYR_STOP | clean_fulfilled | at_least 0 | +0.28 (2.84) | 0.02 |
| 10 | C2-3 | EVICON | ZEPHYR_PLAIN -> ZEPHYR_CUTOFF | clean_fulfilled | at_least 10 | +39.60 (24.64) | 0.91 |
| 10 | C2-4 | EVICON | ZEPHYR_PLAIN -> ZEPHYR_CUTOFF | clean_fulfilled_confirmed | at_least 10 | +42.42 (25.16) | 0.95 |
| 10 | C2-5 | EVICON | ZEPHYR_CUTOFF -> ZEPHYR_STOP | clean_fulfilled | at_least 0 | +0.28 (2.84) | 0.04 |

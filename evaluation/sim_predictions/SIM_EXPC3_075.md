# Simulator prediction for experiment C3 (release 075, SIMULATION_ONLY)

200 episodes per scenario; base `iot_dosing_testbed.json`, window 6, fence_uncertain abort, both owner channels; owner_up: no owner crash (the fault model of C3); owner_crash: the twin's owner crashes between effect and receipt.

| scenario | contract | policy | clean % | confirmed % | stranded resolved / censored (unit*s per episode) | stops / episode | owner crashes / episode | safety violations | late effects |
|---|---|---|---|---|---|---|---|---|---|
| owner_up | ZEPHYR_PLAIN_DIRECT | EVICON | 34.52 | 33.72 | 0.0 / 3888.4 | 0.0 | 0.0 | 0 | 0 |
| owner_up | ZEPHYR_PLAIN_DIRECT | ORACLE | 77.02 | 77.02 | 533.7 / 0.0 | 0.0 | 0.0 | 0 | 1 |
| owner_up | ZEPHYR_QA_DIRECT | EVICON | 37.66 | 37.65 | 0.0 / 4436.0 | 0.0 | 0.0 | 0 | 0 |
| owner_up | ZEPHYR_QA_DIRECT | ORACLE | 77.02 | 77.02 | 533.7 / 0.0 | 0.0 | 0.0 | 0 | 1 |
| owner_up | ZEPHYR_F_DIRECT | EVICON | 77.05 | 77.05 | 353.2 / 0.0 | 0.0 | 0.0 | 0 | 0 |
| owner_up | ZEPHYR_F_DIRECT | ORACLE | 77.02 | 77.02 | 533.7 / 0.0 | 0.0 | 0.0 | 0 | 1 |
| owner_up | ZEPHYR_QS_DIRECT | EVICON | 77.16 | 77.14 | 703.2 / 0.0 | 53.2 | 0.0 | 0 | 0 |
| owner_up | ZEPHYR_QS_DIRECT | ORACLE | 77.02 | 77.02 | 533.7 / 0.0 | 0.0 | 0.0 | 0 | 1 |
| owner_up | ZEPHYR_CUTOFF_DIRECT | EVICON | 77.05 | 77.05 | 358.9 / 0.0 | 0.0 | 0.0 | 0 | 0 |
| owner_up | ZEPHYR_CUTOFF_DIRECT | ORACLE | 77.02 | 77.02 | 533.7 / 0.0 | 0.0 | 0.0 | 0 | 1 |
| owner_up | ZEPHYR_PLAIN_QUEUE | EVICON | 38.36 | 35.24 | 0.0 / 638.1 | 0.0 | 0.0 | 0 | 10 |
| owner_up | ZEPHYR_PLAIN_QUEUE | ORACLE | 76.84 | 76.84 | 0.0 / 0.0 | 0.0 | 0.0 | 0 | 18 |
| owner_up | ZEPHYR_QA_QUEUE | EVICON | 75.63 | 75.6 | 0.0 / 1081.6 | 0.0 | 0.0 | 0 | 16 |
| owner_up | ZEPHYR_QA_QUEUE | ORACLE | 76.84 | 76.84 | 0.0 / 0.0 | 0.0 | 0.0 | 0 | 18 |
| owner_up | ZEPHYR_F_QUEUE | EVICON | 76.98 | 76.98 | 31.8 / 0.0 | 0.0 | 0.0 | 0 | 6 |
| owner_up | ZEPHYR_F_QUEUE | ORACLE | 76.84 | 76.84 | 0.0 / 0.0 | 0.0 | 0.0 | 0 | 18 |
| owner_up | ZEPHYR_QS_QUEUE | EVICON | 76.87 | 76.85 | 33.0 / 0.0 | 44.2 | 0.0 | 0 | 18 |
| owner_up | ZEPHYR_QS_QUEUE | ORACLE | 76.84 | 76.84 | 0.0 / 0.0 | 0.0 | 0.0 | 0 | 18 |
| owner_up | ZEPHYR_CUTOFF_QUEUE | EVICON | 76.98 | 76.97 | 31.5 / 0.0 | 0.0 | 0.0 | 0 | 6 |
| owner_up | ZEPHYR_CUTOFF_QUEUE | ORACLE | 76.84 | 76.84 | 0.0 / 0.0 | 0.0 | 0.0 | 0 | 18 |
| owner_crash | ZEPHYR_PLAIN_DIRECT | EVICON | 32.45 | 31.55 | 0.0 / 3900.7 | 0.0 | 0.26 | 0 | 0 |
| owner_crash | ZEPHYR_PLAIN_DIRECT | ORACLE | 77.01 | 77.01 | 539.0 / 0.0 | 0.0 | 0.56 | 0 | 1 |
| owner_crash | ZEPHYR_QA_DIRECT | EVICON | 35.72 | 35.52 | 0.0 / 4446.4 | 0.0 | 0.28 | 0 | 0 |
| owner_crash | ZEPHYR_QA_DIRECT | ORACLE | 77.01 | 77.01 | 539.0 / 0.0 | 0.0 | 0.56 | 0 | 1 |
| owner_crash | ZEPHYR_F_DIRECT | EVICON | 69.87 | 69.37 | 286.0 / 894.6 | 0.0 | 0.51 | 0 | 0 |
| owner_crash | ZEPHYR_F_DIRECT | ORACLE | 77.01 | 77.01 | 539.0 / 0.0 | 0.0 | 0.56 | 0 | 1 |
| owner_crash | ZEPHYR_QS_DIRECT | EVICON | 77.13 | 76.75 | 690.3 / 0.0 | 53.3 | 0.54 | 0 | 0 |
| owner_crash | ZEPHYR_QS_DIRECT | ORACLE | 77.01 | 77.01 | 539.0 / 0.0 | 0.0 | 0.56 | 0 | 1 |
| owner_crash | ZEPHYR_CUTOFF_DIRECT | EVICON | 71.59 | 71.24 | 299.6 / 820.3 | 0.0 | 0.53 | 0 | 0 |
| owner_crash | ZEPHYR_CUTOFF_DIRECT | ORACLE | 77.01 | 77.01 | 539.0 / 0.0 | 0.0 | 0.56 | 0 | 1 |
| owner_crash | ZEPHYR_PLAIN_QUEUE | EVICON | 37.46 | 34.28 | 0.0 / 633.5 | 0.0 | 0.31 | 0 | 10 |
| owner_crash | ZEPHYR_PLAIN_QUEUE | ORACLE | 76.83 | 76.83 | 0.0 / 0.0 | 0.0 | 0.56 | 0 | 18 |
| owner_crash | ZEPHYR_QA_QUEUE | EVICON | 75.52 | 75.14 | 0.0 / 1072.4 | 0.0 | 0.56 | 0 | 16 |
| owner_crash | ZEPHYR_QA_QUEUE | ORACLE | 76.83 | 76.83 | 0.0 / 0.0 | 0.0 | 0.56 | 0 | 18 |
| owner_crash | ZEPHYR_F_QUEUE | EVICON | 72.66 | 71.8 | 24.3 / 327.8 | 0.0 | 0.53 | 0 | 6 |
| owner_crash | ZEPHYR_F_QUEUE | ORACLE | 76.83 | 76.83 | 0.0 / 0.0 | 0.0 | 0.56 | 0 | 18 |
| owner_crash | ZEPHYR_QS_QUEUE | EVICON | 76.86 | 76.49 | 31.4 / 0.0 | 43.9 | 0.56 | 0 | 18 |
| owner_crash | ZEPHYR_QS_QUEUE | ORACLE | 76.83 | 76.83 | 0.0 / 0.0 | 0.0 | 0.56 | 0 | 18 |
| owner_crash | ZEPHYR_CUTOFF_QUEUE | EVICON | 76.56 | 76.18 | 36.8 / 231.3 | 0.0 | 0.56 | 0 | 6 |
| owner_crash | ZEPHYR_CUTOFF_QUEUE | ORACLE | 76.83 | 76.83 | 0.0 / 0.0 | 0.0 | 0.56 | 0 | 18 |

## Power of the C3 decision rules under the simulator's paired differences (Holm over C3-1 to C3-6)

| scenario | n | ID | comparison | rule | sim mean (SD) | power |
|---|---|---|---|---|---|---|
| owner_up | 6 | C3-1 | ZEPHYR_PLAIN_DIRECT -> ZEPHYR_F_DIRECT | at_least 10 | +43.09 (25.46) | 0.6 |
| owner_up | 6 | C3-2 | ZEPHYR_QA_DIRECT -> ZEPHYR_F_DIRECT | at_least 10 | +39.88 (26.79) | 0.48 |
| owner_up | 6 | C3-3 | ZEPHYR_QA_DIRECT -> ZEPHYR_QS_DIRECT | at_least 10 | +39.99 (26.80) | 0.49 |
| owner_up | 6 | C3-4 | ZEPHYR_QA_DIRECT -> ZEPHYR_QA_QUEUE | at_least 10 | +38.43 (26.59) | 0.45 |
| owner_up | 6 | C3-5 | ZEPHYR_F_DIRECT -> ZEPHYR_QS_DIRECT | equivalent 5 | +0.10 (0.48) | 1.0 |
| owner_up | 6 | C3-6 | ZEPHYR_F_QUEUE -> ZEPHYR_QS_QUEUE | equivalent 5 | -0.12 (0.62) | 1.0 |
| owner_up | 8 | C3-1 | ZEPHYR_PLAIN_DIRECT -> ZEPHYR_F_DIRECT | at_least 10 | +43.09 (25.46) | 0.88 |
| owner_up | 8 | C3-2 | ZEPHYR_QA_DIRECT -> ZEPHYR_F_DIRECT | at_least 10 | +39.88 (26.79) | 0.78 |
| owner_up | 8 | C3-3 | ZEPHYR_QA_DIRECT -> ZEPHYR_QS_DIRECT | at_least 10 | +39.99 (26.80) | 0.8 |
| owner_up | 8 | C3-4 | ZEPHYR_QA_DIRECT -> ZEPHYR_QA_QUEUE | at_least 10 | +38.43 (26.59) | 0.75 |
| owner_up | 8 | C3-5 | ZEPHYR_F_DIRECT -> ZEPHYR_QS_DIRECT | equivalent 5 | +0.10 (0.48) | 1.0 |
| owner_up | 8 | C3-6 | ZEPHYR_F_QUEUE -> ZEPHYR_QS_QUEUE | equivalent 5 | -0.12 (0.62) | 1.0 |
| owner_up | 10 | C3-1 | ZEPHYR_PLAIN_DIRECT -> ZEPHYR_F_DIRECT | at_least 10 | +43.09 (25.46) | 0.97 |
| owner_up | 10 | C3-2 | ZEPHYR_QA_DIRECT -> ZEPHYR_F_DIRECT | at_least 10 | +39.88 (26.79) | 0.94 |
| owner_up | 10 | C3-3 | ZEPHYR_QA_DIRECT -> ZEPHYR_QS_DIRECT | at_least 10 | +39.99 (26.80) | 0.92 |
| owner_up | 10 | C3-4 | ZEPHYR_QA_DIRECT -> ZEPHYR_QA_QUEUE | at_least 10 | +38.43 (26.59) | 0.92 |
| owner_up | 10 | C3-5 | ZEPHYR_F_DIRECT -> ZEPHYR_QS_DIRECT | equivalent 5 | +0.10 (0.48) | 1.0 |
| owner_up | 10 | C3-6 | ZEPHYR_F_QUEUE -> ZEPHYR_QS_QUEUE | equivalent 5 | -0.12 (0.62) | 1.0 |
| owner_crash | 6 | C3-1 | ZEPHYR_PLAIN_DIRECT -> ZEPHYR_F_DIRECT | at_least 10 | +37.89 (26.34) | 0.28 |
| owner_crash | 6 | C3-2 | ZEPHYR_QA_DIRECT -> ZEPHYR_F_DIRECT | at_least 10 | +34.58 (27.95) | 0.2 |
| owner_crash | 6 | C3-3 | ZEPHYR_QA_DIRECT -> ZEPHYR_QS_DIRECT | at_least 10 | +41.92 (25.67) | 0.43 |
| owner_crash | 6 | C3-4 | ZEPHYR_QA_DIRECT -> ZEPHYR_QA_QUEUE | at_least 10 | +40.28 (25.54) | 0.38 |
| owner_crash | 6 | C3-5 | ZEPHYR_F_DIRECT -> ZEPHYR_QS_DIRECT | equivalent 5 | +7.34 (14.81) | 0.18 |
| owner_crash | 6 | C3-6 | ZEPHYR_F_QUEUE -> ZEPHYR_QS_QUEUE | equivalent 5 | +4.35 (11.83) | 0.35 |
| owner_crash | 8 | C3-1 | ZEPHYR_PLAIN_DIRECT -> ZEPHYR_F_DIRECT | at_least 10 | +37.89 (26.34) | 0.54 |
| owner_crash | 8 | C3-2 | ZEPHYR_QA_DIRECT -> ZEPHYR_F_DIRECT | at_least 10 | +34.58 (27.95) | 0.38 |
| owner_crash | 8 | C3-3 | ZEPHYR_QA_DIRECT -> ZEPHYR_QS_DIRECT | at_least 10 | +41.92 (25.67) | 0.71 |
| owner_crash | 8 | C3-4 | ZEPHYR_QA_DIRECT -> ZEPHYR_QA_QUEUE | at_least 10 | +40.28 (25.54) | 0.66 |
| owner_crash | 8 | C3-5 | ZEPHYR_F_DIRECT -> ZEPHYR_QS_DIRECT | equivalent 5 | +7.34 (14.81) | 0.13 |
| owner_crash | 8 | C3-6 | ZEPHYR_F_QUEUE -> ZEPHYR_QS_QUEUE | equivalent 5 | +4.35 (11.83) | 0.31 |
| owner_crash | 10 | C3-1 | ZEPHYR_PLAIN_DIRECT -> ZEPHYR_F_DIRECT | at_least 10 | +37.89 (26.34) | 0.76 |
| owner_crash | 10 | C3-2 | ZEPHYR_QA_DIRECT -> ZEPHYR_F_DIRECT | at_least 10 | +34.58 (27.95) | 0.57 |
| owner_crash | 10 | C3-3 | ZEPHYR_QA_DIRECT -> ZEPHYR_QS_DIRECT | at_least 10 | +41.92 (25.67) | 0.87 |
| owner_crash | 10 | C3-4 | ZEPHYR_QA_DIRECT -> ZEPHYR_QA_QUEUE | at_least 10 | +40.28 (25.54) | 0.86 |
| owner_crash | 10 | C3-5 | ZEPHYR_F_DIRECT -> ZEPHYR_QS_DIRECT | equivalent 5 | +7.34 (14.81) | 0.1 |
| owner_crash | 10 | C3-6 | ZEPHYR_F_QUEUE -> ZEPHYR_QS_QUEUE | equivalent 5 | +4.35 (11.83) | 0.24 |

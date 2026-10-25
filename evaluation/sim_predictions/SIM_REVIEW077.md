# Simulator prediction for PREREG_review_077.md (release 077, SIMULATION_ONLY)

200 episodes per scenario; base `iot_dosing_testbed.json`; no assumed bound on the network delay.

| scenario | cell | policy | clean % | confirmed % | stranded censored (unit*s / episode) | safety violations | unsafe releases | late effects |
|---|---|---|---|---|---|---|---|---|
| unbounded_zephyr | ZEPHYR_CUTOFF | EVICON | 76.98 | 76.97 | 0.0 | 0 | 0 | 6 |
| unbounded_zephyr | ZEPHYR_F_DIRECT | EVICON | 77.05 | 77.04 | 0.0 | 0 | 0 | 0 |
| unbounded_zephyr | ZEPHYR_PLAIN | EVICON | 75.61 | 62.1 | 1092.8 | 0 | 0 | 65 |
| unbounded_zephyr | ZEPHYR_PLAIN_DIRECT | EVICON | 62.55 | 61.07 | 15788.4 | 0 | 0 | 0 |
| unbounded_minio | MINIO_COND | EVICON | 77.21 | 77.2 | 0.0 | 0 | 0 | 0 |
| unbounded_minio | MINIO_PLAIN | EVICON | 62.55 | 61.07 | 15788.4 | 0 | 0 | 0 |
| noorphan | K0 | EVICON | 38.36 | 35.24 | 638.1 | 0 | 0 | 10 |
| noorphan | K0F | EVICON | 76.98 | 76.98 | 0.0 | 0 | 0 | 6 |
| noorphan | K0S | EVICON | 76.87 | 76.85 | 0.0 | 0 | 0 | 18 |
| orphan | K0 | EVICON | 37.46 | 34.28 | 633.5 | 0 | 0 | 10 |
| orphan | K0F | EVICON | 72.66 | 71.8 | 327.8 | 0 | 0 | 6 |
| orphan | K0S | EVICON | 76.86 | 76.49 | 0.0 | 0 | 0 | 18 |
| k2rep | K2 | EVICON | 76.8 | 76.42 | 0.0 | 0 | 0 | 18 |
| k2rep | K2 | RES_PERIODIC | 76.8 | 76.42 | 0.0 | 0 | 0 | 18 |
| k2rep | K3F | EVICON | 76.86 | 76.48 | 0.0 | 0 | 0 | 18 |
| k2rep | K3F | RES_PERIODIC | 76.89 | 76.51 | 0.0 | 0 | 0 | 15 |
| mild | K0 | EVICON | 55.03 | 52.65 | 453.2 | 0 | 0 | 9 |
| mild | K0F | EVICON | 76.35 | 76.04 | 78.3 | 0 | 0 | 9 |
| mild | K0S | EVICON | 77.04 | 76.88 | 3.4 | 0 | 0 | 13 |
| wan | K0 | EVICON | 37.09 | 33.9 | 628.9 | 0 | 0 | 10 |
| wan | K0F | EVICON | 72.4 | 71.51 | 323.4 | 0 | 0 | 6 |
| wan | K0S | EVICON | 76.86 | 76.48 | 0.0 | 0 | 0 | 18 |
| release_timeout | K0F_QUEUE | RELEASE_ON_TIMEOUT | 69.8 | 69.56 | 0.0 | 7156 | 4069 | 155 |
| release_timeout | K0_DIRECT | RELEASE_ON_TIMEOUT | 75.7 | 75.55 | 0.0 | 1275 | 6135 | 0 |
| release_timeout | K0_QUEUE | RELEASE_ON_TIMEOUT | 69.8 | 69.56 | 0.0 | 7156 | 4069 | 155 |

## Decision rules of the plan under the simulator's paired differences (Holm within each family)

| ID | family | comparison | metric | rule | n | sim mean (SD) | power |
|---|---|---|---|---|---|---|---|
| U-1 | unbounded | EVICON: MINIO_PLAIN -> MINIO_COND (unbounded_minio) | clean_fulfilled | at_least 5 | 8 | +14.75 (11.84) | 0.45 |
| U-2 | unbounded | EVICON: ZEPHYR_PLAIN_DIRECT -> ZEPHYR_F_DIRECT (unbounded_zephyr) | clean_fulfilled | at_least 5 | 8 | +14.60 (11.71) | 0.42 |
| U-3 | unbounded | EVICON: ZEPHYR_PLAIN -> ZEPHYR_CUTOFF (unbounded_zephyr) | clean_fulfilled_confirmed | at_least 5 | 8 | +14.97 (10.90) | 0.56 |
| U-4 | unbounded | EVICON: ZEPHYR_PLAIN -> ZEPHYR_CUTOFF (unbounded_zephyr) | clean_fulfilled | equivalent 3 | 8 | +1.38 (1.91) | 0.7 |
| O-1 | orphan | EVICON: K0F -> K0S (noorphan) | clean_fulfilled | equivalent 5 | 8 | -0.12 (0.62) | 1.0 |
| O-2 | orphan | EVICON: K0 -> K0F (noorphan) | clean_fulfilled | at_least 10 | 8 | +39.11 (25.16) | 0.9 |
| K-1 | k2 | EVICON: K2 -> K3F (k2rep) | clean_fulfilled | equivalent 2 | 5 | +0.06 (0.80) | 0.94 |
| K-2 | k2 | RES_PERIODIC: K2 -> K3F (k2rep) | clean_fulfilled | equivalent 2 | 5 | +0.09 (0.63) | 0.96 |
| V-1 | regime | EVICON: K0 -> K0F (mild) | clean_fulfilled | at_least 5 | 8 | +21.64 (25.60) | 0.27 |
| V-2 | regime | EVICON: K0 -> K0S (mild) | clean_fulfilled | at_least 5 | 8 | +22.32 (25.54) | 0.3 |
| WAN-1 | wan | EVICON: K0 -> K0F (wan) | clean_fulfilled | at_least 10 | 8 | +35.66 (25.02) | 0.81 |
| WAN-2 | wan | EVICON: K0 -> K0S (wan) | clean_fulfilled | at_least 10 | 8 | +40.27 (24.35) | 0.91 |

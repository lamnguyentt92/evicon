# Simulator prediction for PREREG_extra_084.md (release 084, SIMULATION_ONLY)

200 episodes per scenario; base `iot_dosing_testbed.json`; no assumed bound on the network delay.

## Levels

| scenario | cell | policy | clean % | confirmed % | safety violations | queries / ep | stops / ep | fences / ep | fence aborts / ep | owner calls / ep | owner crashes / ep |
|---|---|---|---|---|---|---|---|---|---|---|---|
| a_s6 | K0F | EVICON | 54.12 | 51.24 | 0 | 0.0 | 0.0 | 344.0 | 342.6 | 344.0 | 2.21 |
| a_s6 | K0F | EVICON_FI | 76.13 | 74.09 | 0 | 0.0 | 0.0 | 7.5 | 0.0 | 7.5 | 3.04 |
| a_s6 | K0S | EVICON | 75.98 | 73.94 | 0 | 0.0 | 41.25 | 0.0 | 0.0 | 41.2 | 3.04 |
| a_s6 | K0S | EVICON_FI | 75.98 | 73.94 | 0 | 0.0 | 41.25 | 0.0 | 0.0 | 41.2 | 3.04 |
| a_sinf | K0F | EVICON | 75.68 | 69.75 | 0 | 0.0 | 0.0 | 575.3 | 573.57 | 575.3 | 3.07 |
| a_sinf | K0F | EVICON_FI | 76.96 | 74.91 | 0 | 0.0 | 0.0 | 7.4 | 0.0 | 7.4 | 3.07 |
| a_sinf | K0S | EVICON | 76.6 | 74.47 | 0 | 0.0 | 103.86 | 0.0 | 0.0 | 103.9 | 3.08 |
| a_sinf | K0S | EVICON_FI | 76.6 | 74.47 | 0 | 0.0 | 103.86 | 0.0 | 0.0 | 103.9 | 3.08 |
| a_s6_base | K0F | EVICON | 72.66 | 71.8 | 0 | 0.0 | 0.0 | 169.6 | 165.46 | 169.6 | 0.53 |
| a_s6_base | K0F | EVICON_FI | 76.98 | 76.61 | 0 | 0.0 | 0.0 | 5.7 | 0.0 | 5.7 | 0.55 |
| a_s6_base | K0S | EVICON | 76.86 | 76.49 | 0 | 0.0 | 43.88 | 0.0 | 0.0 | 43.9 | 0.56 |
| a_s6_base | K0S | EVICON_FI | 76.86 | 76.49 | 0 | 0.0 | 43.88 | 0.0 | 0.0 | 43.9 | 0.56 |
| a_sinf_base | K0F | EVICON | 76.57 | 75.39 | 0 | 0.0 | 0.0 | 219.8 | 215.59 | 219.8 | 0.56 |
| a_sinf_base | K0F | EVICON_FI | 76.98 | 76.61 | 0 | 0.0 | 0.0 | 5.7 | 0.0 | 5.7 | 0.55 |
| a_sinf_base | K0S | EVICON | 76.62 | 76.22 | 0 | 0.0 | 96.12 | 0.0 | 0.0 | 96.1 | 0.56 |
| a_sinf_base | K0S | EVICON_FI | 76.62 | 76.22 | 0 | 0.0 | 96.12 | 0.0 | 0.0 | 96.1 | 0.56 |
| c_fw_w6 | ZEPHYR_CUTOFF | EVICON | 76.98 | 76.97 | 0 | 25.0 | 0.0 | 3.7 | 0.0 | 28.6 | 0.0 |
| c_fw_w6 | ZEPHYR_CUTOFF | PERIODIC_FULL | 76.97 | 76.97 | 0 | 26.5 | 0.0 | 3.7 | 0.0 | 30.2 | 0.0 |
| c_fw_winf | ZEPHYR_CUTOFF | EVICON | 76.98 | 76.97 | 0 | 25.0 | 0.0 | 3.7 | 0.0 | 28.7 | 0.0 |
| c_fw_winf | ZEPHYR_CUTOFF | PERIODIC_FULL | 76.97 | 76.97 | 0 | 26.6 | 0.0 | 3.7 | 0.0 | 30.3 | 0.0 |
| c_minio_w6 | MINIO_COND | EVICON | 77.18 | 77.16 | 0 | 54.6 | 51.5 | 0.0 | 0.0 | 106.2 | 0.0 |
| c_minio_w6 | MINIO_COND | PERIODIC_FULL | 77.18 | 77.16 | 0 | 53.4 | 51.95 | 0.0 | 0.0 | 105.4 | 0.0 |
| c_minio_winf | MINIO_COND | EVICON | 77.21 | 77.2 | 0 | 119.0 | 113.93 | 0.0 | 0.0 | 232.9 | 0.0 |
| c_minio_winf | MINIO_COND | PERIODIC_FULL | 77.22 | 77.21 | 0 | 116.3 | 115.18 | 0.0 | 0.0 | 231.5 | 0.0 |

## Effects (per-episode paired differences, pp of offered)

| effect | from -> to | metric | n | mean | SD |
|---|---|---|---|---|---|
| (a) W=6 K0F R5 -> R5' | K0F/EVICON -> K0F/EVICON_FI | clean_fulfilled | 200 | +22.22 | 19.08 |
| (a) W=6 stop -> K0F R5' | K0S/EVICON -> K0F/EVICON_FI | clean_fulfilled | 200 | +0.16 | 1.05 |
| (a) W=6 K0F R5 -> stop (the stop experiment's contrast) | K0F/EVICON -> K0S/EVICON | clean_fulfilled | 200 | +22.06 | 19.02 |
| (a) W=6 K0F R5 -> R5' confirmed | K0F/EVICON -> K0F/EVICON_FI | clean_fulfilled_confirmed | 200 | +23.08 | 19.10 |
| (a) W=inf K0F R5 -> R5' | K0F/EVICON -> K0F/EVICON_FI | clean_fulfilled | 200 | +1.29 | 1.61 |
| (a) W=inf stop -> K0F R5' | K0S/EVICON -> K0F/EVICON_FI | clean_fulfilled | 200 | +0.37 | 1.94 |
| (a) W=inf K0F R5 -> stop (the stop experiment's contrast) | K0F/EVICON -> K0S/EVICON | clean_fulfilled | 200 | +0.92 | 2.77 |
| (a) W=inf K0F R5 -> R5' confirmed | K0F/EVICON -> K0F/EVICON_FI | clean_fulfilled_confirmed | 200 | +5.18 | 4.98 |
| (a) W=6, base twin K0F R5 -> R5' | K0F/EVICON -> K0F/EVICON_FI | clean_fulfilled | 200 | +4.47 | 11.78 |
| (a) W=6, base twin stop -> K0F R5' | K0S/EVICON -> K0F/EVICON_FI | clean_fulfilled | 200 | +0.12 | 0.63 |
| (a) W=6, base twin K0F R5 -> stop (the stop experiment's contrast) | K0F/EVICON -> K0S/EVICON | clean_fulfilled | 200 | +4.35 | 11.83 |
| (a) W=6, base twin K0F R5 -> R5' confirmed | K0F/EVICON -> K0F/EVICON_FI | clean_fulfilled_confirmed | 200 | +4.98 | 12.40 |
| (a) W=inf, base twin K0F R5 -> R5' | K0F/EVICON -> K0F/EVICON_FI | clean_fulfilled | 200 | +0.41 | 0.99 |
| (a) W=inf, base twin stop -> K0F R5' | K0S/EVICON -> K0F/EVICON_FI | clean_fulfilled | 200 | +0.36 | 1.87 |
| (a) W=inf, base twin K0F R5 -> stop (the stop experiment's contrast) | K0F/EVICON -> K0S/EVICON | clean_fulfilled | 200 | +0.05 | 2.19 |
| (a) W=inf, base twin K0F R5 -> R5' confirmed | K0F/EVICON -> K0F/EVICON_FI | clean_fulfilled_confirmed | 200 | +1.25 | 2.42 |
| (c) ZEPHYR_CUTOFF W=6 EVICON -> PERIODIC_FULL | ZEPHYR_CUTOFF/EVICON -> ZEPHYR_CUTOFF/PERIODIC_FULL | clean_fulfilled | 200 | -0.01 | 0.09 |
| (c) ZEPHYR_CUTOFF W=6 EVICON -> PERIODIC_FULL confirmed | ZEPHYR_CUTOFF/EVICON -> ZEPHYR_CUTOFF/PERIODIC_FULL | clean_fulfilled_confirmed | 200 | -0.01 | 0.09 |
| (c) ZEPHYR_CUTOFF W=inf EVICON -> PERIODIC_FULL | ZEPHYR_CUTOFF/EVICON -> ZEPHYR_CUTOFF/PERIODIC_FULL | clean_fulfilled | 200 | -0.01 | 0.09 |
| (c) ZEPHYR_CUTOFF W=inf EVICON -> PERIODIC_FULL confirmed | ZEPHYR_CUTOFF/EVICON -> ZEPHYR_CUTOFF/PERIODIC_FULL | clean_fulfilled_confirmed | 200 | -0.01 | 0.09 |
| (c) MINIO_COND W=6 EVICON -> PERIODIC_FULL | MINIO_COND/EVICON -> MINIO_COND/PERIODIC_FULL | clean_fulfilled | 200 | -0.00 | 0.36 |
| (c) MINIO_COND W=6 EVICON -> PERIODIC_FULL confirmed | MINIO_COND/EVICON -> MINIO_COND/PERIODIC_FULL | clean_fulfilled_confirmed | 200 | -0.00 | 0.36 |
| (c) MINIO_COND W=inf EVICON -> PERIODIC_FULL | MINIO_COND/EVICON -> MINIO_COND/PERIODIC_FULL | clean_fulfilled | 200 | +0.02 | 0.29 |
| (c) MINIO_COND W=inf EVICON -> PERIODIC_FULL confirmed | MINIO_COND/EVICON -> MINIO_COND/PERIODIC_FULL | clean_fulfilled_confirmed | 200 | +0.02 | 0.29 |

## The plan's paired-difference hypotheses under the simulator's paired differences (Holm within each family)

| ID | family | comparison | rule | n | sim mean (SD) | power |
|---|---|---|---|---|---|---|
| FI-1 | fi-w6 | K0F/EVICON -> K0F/EVICON_FI (a_s6) | at_least 10.0 | 8 | +22.22 (19.08) | 0.46 |
| PF-1 | pf-fw | ZEPHYR_CUTOFF/EVICON -> ZEPHYR_CUTOFF/PERIODIC_FULL (c_fw_w6) | equivalent 3.0 | 8 | -0.01 (0.09) | 1.0 |
| PF-2 | pf-fw | ZEPHYR_CUTOFF/EVICON -> ZEPHYR_CUTOFF/PERIODIC_FULL (c_fw_winf) | equivalent 3.0 | 8 | -0.01 (0.09) | 1.0 |
| PF-3 | pf-minio | MINIO_COND/EVICON -> MINIO_COND/PERIODIC_FULL (c_minio_w6) | equivalent 3.0 | 8 | -0.00 (0.36) | 1.0 |
| PF-4 | pf-minio | MINIO_COND/EVICON -> MINIO_COND/PERIODIC_FULL (c_minio_winf) | equivalent 3.0 | 8 | +0.02 (0.29) | 1.0 |

## Fit of the twin's crash probability (CAL_CRASH = 0.028)

| crash_mid_prob | K0F (R5) -> K0S, twin | testbed (S, episodes 0-7) |
|---|---|---|
| 0.02 | +15.55 | +20.7 |
| 0.025 | +18.09 | +20.7 |
| 0.03 | +22.80 | +20.7 |
| 0.035 | +25.57 | +20.7 |

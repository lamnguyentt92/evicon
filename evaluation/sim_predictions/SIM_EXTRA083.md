# Simulator prediction for PREREG_extra_083.md (release 083, SIMULATION_ONLY)

200 episodes per scenario; base `iot_dosing_testbed.json`; no assumed bound on the network delay.

## Levels

| scenario | cell | policy | clean % | confirmed % | safety violations (per episode) | unsafe releases | certificate errors | queries / ep | fences / ep | late effects |
|---|---|---|---|---|---|---|---|---|---|---|
| t1_k2 | K2 | EVICON | 76.8 | 76.42 | 0 (0.0) | 0 | 0 | 59.7 | 0.0 | 18 |
| t1_k2 | K2 | M1 | 76.39 | 75.99 | 0 (0.0) | 0 | 0 | 9.6 | 0.0 | 18 |
| t1_k2 | K2 | M1W | 76.8 | 76.41 | 0 (0.0) | 0 | 0 | 42.4 | 0.0 | 18 |
| t1_tb | TB_RPC | EVICON | 72.9 | 72.88 | 0 (0.0) | 0 | 0 | 89.9 | 0.0 | 0 |
| t1_tb | TB_RPC | M1 | 38.36 | 35.34 | 0 (0.0) | 0 | 0 | 2.8 | 0.0 | 0 |
| t1_tb | TB_RPC | M1W | 73.67 | 72.46 | 0 (0.0) | 0 | 0 | 70.2 | 0.0 | 0 |
| t3_rt | K0_DIRECT | RELEASE_ON_TIMEOUT_10S | 75.7 | 75.55 | 1275 (6.38) | 6135 | 0 | 0.0 | 0.0 | 0 |
| t3_rt | K0_DIRECT | RELEASE_ON_TIMEOUT_120S | 70.69 | 69.15 | 285 (1.43) | 1779 | 0 | 0.0 | 0.0 | 0 |
| t3_rt | K0_DIRECT | RELEASE_ON_TIMEOUT_30S | 76.62 | 76.07 | 1047 (5.24) | 3445 | 0 | 0.0 | 0.0 | 0 |
| t3_rt | K0_DIRECT | RELEASE_ON_TIMEOUT_60S | 77.12 | 75.41 | 527 (2.63) | 2769 | 0 | 0.0 | 0.0 | 0 |
| t3_rt | K0_QUEUE | RELEASE_ON_TIMEOUT_10S | 69.8 | 69.56 | 7156 (35.78) | 4069 | 0 | 0.0 | 0.0 | 155 |
| t3_rt | K0_QUEUE | RELEASE_ON_TIMEOUT_120S | 78.85 | 77.17 | 718 (3.59) | 793 | 0 | 0.0 | 0.0 | 33 |
| t3_rt | K0_QUEUE | RELEASE_ON_TIMEOUT_30S | 75.06 | 74.65 | 2069 (10.35) | 1426 | 0 | 0.0 | 0.0 | 71 |
| t3_rt | K0_QUEUE | RELEASE_ON_TIMEOUT_60S | 78.94 | 77.26 | 823 (4.12) | 925 | 0 | 0.0 | 0.0 | 41 |
| t4_w6 | ZEPHYR_CUTOFF | EVICON | 76.98 | 76.97 | 0 (0.0) | 0 | 0 | 25.0 | 3.7 | 6 |
| t4_w6 | ZEPHYR_F_DIRECT | EVICON | 77.05 | 77.05 | 0 (0.0) | 0 | 0 | 0.0 | 5.4 | 0 |
| t4_w6 | ZEPHYR_PLAIN_DIRECT_QOS0 | EVICON | 34.52 | 33.72 | 0 (0.0) | 0 | 0 | 0.0 | 0.0 | 0 |
| t4_w6 | ZEPHYR_PLAIN_QOS0 | EVICON | 34.52 | 33.72 | 0 (0.0) | 0 | 0 | 0.0 | 0.0 | 0 |
| t4_w6 | ZEPHYR_PLAIN_QOS1 | EVICON | 38.36 | 35.24 | 0 (0.0) | 0 | 0 | 0.0 | 0.0 | 10 |
| t4_winf | ZEPHYR_CUTOFF | EVICON | 76.98 | 76.97 | 0 (0.0) | 0 | 0 | 25.0 | 3.7 | 6 |
| t4_winf | ZEPHYR_F_DIRECT | EVICON | 77.05 | 77.04 | 0 (0.0) | 0 | 0 | 0.0 | 5.4 | 0 |
| t4_winf | ZEPHYR_PLAIN_DIRECT_QOS0 | EVICON | 62.55 | 61.07 | 0 (0.0) | 0 | 454 | 0.0 | 0.0 | 0 |
| t4_winf | ZEPHYR_PLAIN_QOS0 | EVICON | 62.55 | 61.07 | 0 (0.0) | 0 | 454 | 0.0 | 0.0 | 0 |
| t4_winf | ZEPHYR_PLAIN_QOS1 | EVICON | 75.61 | 62.1 | 0 (0.0) | 0 | 405 | 0.0 | 0.0 | 65 |
| t5_s_winf | K0F | EVICON | 76.57 | 75.39 | 0 (0.0) | 0 | 0 | 0.0 | 219.8 | 6 |
| t5_s_winf | K0F | EVICON_GREEDY | 76.57 | 75.39 | 0 (0.0) | 0 | 0 | 0.0 | 219.8 | 6 |
| t5_s_winf | K0S | EVICON | 76.62 | 76.22 | 0 (0.0) | 0 | 5 | 0.0 | 0.0 | 65 |
| t5_s_winf | K0S | EVICON_GREEDY | 76.62 | 76.23 | 0 (0.0) | 0 | 0 | 0.0 | 0.0 | 65 |
| t5_minio | MINIO_COND | EVICON | 77.21 | 77.2 | 0 (0.0) | 0 | 12 | 119.0 | 0.0 | 0 |
| t5_minio | MINIO_COND | EVICON_GREEDY | 77.22 | 77.21 | 0 (0.0) | 0 | 0 | 119.0 | 0.0 | 0 |
| t5_minio | MINIO_PLAIN | EVICON | 62.55 | 61.07 | 0 (0.0) | 0 | 454 | 0.0 | 0.0 | 0 |
| t5_minio | MINIO_PLAIN | EVICON_GREEDY | 62.55 | 61.07 | 0 (0.0) | 0 | 0 | 0.0 | 0.0 | 0 |
| t5_gamma_w6 | K0F | EVICON_G1 | 72.66 | 71.8 | 0 (0.0) | 0 | 0 | 0.0 | 165.1 | 6 |
| t5_gamma_w6 | K0F | EVICON_G2 | 72.66 | 71.8 | 0 (0.0) | 0 | 0 | 0.0 | 169.6 | 6 |
| t5_gamma_w6 | K0F | EVICON_G3 | 72.66 | 71.8 | 0 (0.0) | 0 | 0 | 0.0 | 169.6 | 6 |
| t5_gamma_w6 | K0F | EVICON_G4 | 72.66 | 71.8 | 0 (0.0) | 0 | 0 | 0.0 | 169.6 | 6 |
| t5_gamma_w6 | K0F | EVICON_GREEDY | 72.66 | 71.8 | 0 (0.0) | 0 | 0 | 0.0 | 169.4 | 6 |
| t5_gamma_winf | K0F | EVICON_G1 | 76.57 | 75.39 | 0 (0.0) | 0 | 0 | 0.0 | 211.7 | 6 |
| t5_gamma_winf | K0F | EVICON_G2 | 76.57 | 75.39 | 0 (0.0) | 0 | 0 | 0.0 | 218.7 | 6 |
| t5_gamma_winf | K0F | EVICON_G3 | 76.57 | 75.39 | 0 (0.0) | 0 | 0 | 0.0 | 219.8 | 6 |
| t5_gamma_winf | K0F | EVICON_G4 | 76.57 | 75.38 | 0 (0.0) | 0 | 24 | 0.0 | 214.9 | 6 |
| t5_gamma_winf | K0F | EVICON_GREEDY | 76.57 | 75.39 | 0 (0.0) | 0 | 0 | 0.0 | 219.8 | 6 |
| t5_theta | K0F | EVICON_FC0.5 | 72.68 | 71.82 | 0 (0.0) | 0 | 0 | 0.0 | 171.1 | 4 |
| t5_theta | K0F | EVICON_FC100 | 70.88 | 69.72 | 0 (0.0) | 0 | 0 | 0.0 | 130.4 | 16 |
| t5_theta | K0F | EVICON_FC5 | 72.66 | 71.8 | 0 (0.0) | 0 | 0 | 0.0 | 169.6 | 6 |
| t5_theta | K0F | EVICON_FC50 | 71.05 | 69.99 | 0 (0.0) | 0 | 0 | 0.0 | 150.4 | 13 |
| t5_theta | K0F | EVICON_FC500 | 68.73 | 66.84 | 0 (0.0) | 0 | 0 | 0.0 | 56.4 | 15 |
| t5_theta | K0F | EVICON_THETA0 | 72.48 | 71.62 | 0 (0.0) | 0 | 0 | 0.0 | 172.6 | 4 |
| t6_fw | ZEPHYR_CUTOFF | EVICON@W16 | 76.98 | 76.97 | 0 (0.0) | 0 | 0 | 25.0 | 3.7 | 6 |
| t6_fw | ZEPHYR_CUTOFF | EVICON@W3 | 76.99 | 76.99 | 0 (0.0) | 0 | 0 | 22.7 | 3.7 | 6 |
| t6_fw | ZEPHYR_CUTOFF | EVICON@W32 | 76.98 | 76.97 | 0 (0.0) | 0 | 0 | 25.0 | 3.7 | 6 |
| t6_fw | ZEPHYR_CUTOFF | EVICON@W6 | 76.98 | 76.97 | 0 (0.0) | 0 | 0 | 25.0 | 3.7 | 6 |
| t6_fw | ZEPHYR_CUTOFF | EVICON@Winf | 76.98 | 76.97 | 0 (0.0) | 0 | 0 | 25.0 | 3.7 | 6 |
| t6_fw | ZEPHYR_PLAIN_QOS0 | EVICON@W16 | 48.28 | 47.16 | 0 (0.0) | 0 | 0 | 0.0 | 0.0 | 0 |
| t6_fw | ZEPHYR_PLAIN_QOS0 | EVICON@W3 | 25.9 | 25.34 | 0 (0.0) | 0 | 0 | 0.0 | 0.0 | 0 |
| t6_fw | ZEPHYR_PLAIN_QOS0 | EVICON@W32 | 57.74 | 56.38 | 0 (0.0) | 0 | 113 | 0.0 | 0.0 | 0 |
| t6_fw | ZEPHYR_PLAIN_QOS0 | EVICON@W6 | 34.52 | 33.72 | 0 (0.0) | 0 | 0 | 0.0 | 0.0 | 0 |
| t6_fw | ZEPHYR_PLAIN_QOS0 | EVICON@Winf | 62.55 | 61.07 | 0 (0.0) | 0 | 454 | 0.0 | 0.0 | 0 |
| t6_tb | TB_RPC | EVICON@W16 | 75.18 | 75.16 | 0 (0.0) | 0 | 0 | 133.3 | 0.0 | 0 |
| t6_tb | TB_RPC | EVICON@W3 | 68.7 | 68.69 | 0 (0.0) | 0 | 0 | 64.5 | 0.0 | 0 |
| t6_tb | TB_RPC | EVICON@W6 | 72.9 | 72.88 | 0 (0.0) | 0 | 0 | 89.9 | 0.0 | 0 |
| t6_tb | TB_RPC | EVICON@Winf | 75.6 | 75.56 | 0 (0.0) | 0 | 12 | 157.4 | 0.0 | 0 |
| t6_tb | TB_RPC | M1W@W16 | 75.65 | 71.66 | 0 (0.0) | 0 | 0 | 90.1 | 0.0 | 0 |
| t6_tb | TB_RPC | M1W@W3 | 68.94 | 68.5 | 0 (0.0) | 0 | 0 | 53.6 | 0.0 | 0 |
| t6_tb | TB_RPC | M1W@W6 | 73.67 | 72.46 | 0 (0.0) | 0 | 0 | 70.2 | 0.0 | 0 |
| t6_tb | TB_RPC | M1W@Winf | 75.61 | 65.56 | 0 (0.0) | 0 | 0 | 75.3 | 0.0 | 0 |
| t7_base_q6 | K0 | EVICON | 32.99 | 29.55 | 0 (0.0) | 0 | 0 | 0.0 | 0.0 | 0 |
| t7_base_q6 | K0F | EVICON | 72.59 | 71.72 | 0 (0.0) | 0 | 0 | 0.0 | 174.6 | 0 |
| t7_base_q6 | K0S | EVICON | 77.28 | 76.9 | 0 (0.0) | 0 | 0 | 0.0 | 0.0 | 0 |
| t7_base_dinf | K0 | EVICON | 61.37 | 59.57 | 0 (0.0) | 0 | 596 | 0.0 | 0.0 | 0 |
| t7_base_dinf | K0F | EVICON | 75.88 | 75.23 | 0 (0.0) | 0 | 5 | 0.0 | 218.0 | 0 |
| t7_rate05_q6 | K0 | EVICON | 59.89 | 57.87 | 0 (0.0) | 0 | 0 | 0.0 | 0.0 | 0 |
| t7_rate05_q6 | K0F | EVICON | 77.11 | 76.91 | 0 (0.0) | 0 | 0 | 0.0 | 106.8 | 0 |
| t7_rate05_q6 | K0S | EVICON | 77.26 | 77.14 | 0 (0.0) | 0 | 0 | 0.0 | 0.0 | 0 |
| t7_rate05_dinf | K0 | EVICON | 72.43 | 71.84 | 0 (0.0) | 0 | 64 | 0.0 | 0.0 | 0 |
| t7_rate05_dinf | K0F | EVICON | 76.98 | 76.84 | 0 (0.0) | 0 | 0 | 0.0 | 123.2 | 0 |
| t7_rate4_q6 | K0 | EVICON | 18.95 | 15.34 | 0 (0.0) | 0 | 0 | 0.0 | 0.0 | 0 |
| t7_rate4_q6 | K0F | EVICON | 65.67 | 64.28 | 0 (0.0) | 0 | 0 | 0.0 | 134.7 | 0 |
| t7_rate4_q6 | K0S | EVICON | 77.26 | 76.65 | 0 (0.0) | 0 | 0 | 0.0 | 0.0 | 0 |
| t7_rate4_dinf | K0 | EVICON | 50.87 | 48.26 | 0 (0.0) | 0 | 1025 | 0.0 | 0.0 | 0 |
| t7_rate4_dinf | K0F | EVICON | 74.38 | 73.09 | 0 (0.0) | 0 | 38 | 0.0 | 192.5 | 0 |
| t7_off5_15_q6 | K0 | EVICON | 40.73 | 37.48 | 0 (0.0) | 0 | 0 | 0.0 | 0.0 | 0 |
| t7_off5_15_q6 | K0F | EVICON | 73.47 | 72.62 | 0 (0.0) | 0 | 0 | 0.0 | 198.1 | 0 |
| t7_off5_15_q6 | K0S | EVICON | 77.26 | 76.87 | 0 (0.0) | 0 | 0 | 0.0 | 0.0 | 0 |
| t7_off5_15_dinf | K0 | EVICON | 69.3 | 67.22 | 0 (0.0) | 0 | 142 | 0.0 | 0.0 | 0 |
| t7_off5_15_dinf | K0F | EVICON | 76.22 | 75.59 | 0 (0.0) | 0 | 0 | 0.0 | 236.0 | 0 |
| t7_off40_120_q6 | K0 | EVICON | 31.77 | 30.15 | 0 (0.0) | 0 | 0 | 0.0 | 0.0 | 223 |
| t7_off40_120_q6 | K0F | EVICON | 72.17 | 71.53 | 0 (0.0) | 0 | 0 | 0.0 | 94.8 | 207 |
| t7_off40_120_q6 | K0S | EVICON | 72.82 | 72.51 | 0 (0.0) | 0 | 0 | 0.0 | 0.0 | 403 |
| t7_off40_120_dinf | K0 | EVICON | 47.22 | 46.21 | 0 (0.0) | 0 | 864 | 0.0 | 0.0 | 0 |
| t7_off40_120_dinf | K0F | EVICON | 73.86 | 73.43 | 0 (0.0) | 0 | 1 | 0.0 | 107.8 | 0 |

## Effects (per-episode paired differences, clean fulfilment in pp)

| effect | from -> to | n | mean | SD |
|---|---|---|---|---|
| T1 K2 M1 -> M1W (estimand, was T1-1) | K2/M1 -> K2/M1W | 200 | +0.44 | 4.35 |
| T1 K2 M1 -> EVICON | K2/M1 -> K2/EVICON | 200 | +0.44 | 4.35 |
| T1 ThingsBoard M1 -> EVICON | TB_RPC/M1 -> TB_RPC/EVICON | 200 | +35.03 | 25.60 |
| T7 queue W=6 base | K0/EVICON -> K0F/EVICON | 200 | +40.12 | 26.30 |
| T7 queue W=6 base | K0/EVICON -> K0S/EVICON | 200 | +44.95 | 24.69 |
| T7 direct W=inf base | K0/EVICON -> K0F/EVICON | 200 | +14.66 | 10.09 |
| T7 queue W=6 rate05 | K0/EVICON -> K0F/EVICON | 200 | +17.65 | 24.80 |
| T7 queue W=6 rate05 | K0/EVICON -> K0S/EVICON | 200 | +17.80 | 24.81 |
| T7 direct W=inf rate05 | K0/EVICON -> K0F/EVICON | 200 | +4.58 | 6.05 |
| T7 queue W=6 rate4 | K0/EVICON -> K0F/EVICON | 200 | +47.10 | 23.02 |
| T7 queue W=6 rate4 | K0/EVICON -> K0S/EVICON | 200 | +58.91 | 15.86 |
| T7 direct W=inf rate4 | K0/EVICON -> K0F/EVICON | 200 | +23.72 | 10.10 |
| T7 queue W=6 off5_15 | K0/EVICON -> K0F/EVICON | 200 | +33.20 | 23.65 |
| T7 queue W=6 off5_15 | K0/EVICON -> K0S/EVICON | 200 | +37.10 | 23.27 |
| T7 direct W=inf off5_15 | K0/EVICON -> K0F/EVICON | 200 | +6.99 | 4.96 |
| T7 queue W=6 off40_120 | K0/EVICON -> K0F/EVICON | 200 | +41.14 | 25.56 |
| T7 queue W=6 off40_120 | K0/EVICON -> K0S/EVICON | 200 | +41.73 | 24.55 |
| T7 direct W=inf off40_120 | K0/EVICON -> K0F/EVICON | 200 | +26.89 | 16.12 |
| T6 firmware W=3 | ZEPHYR_PLAIN_QOS0/EVICON@W3 -> ZEPHYR_CUTOFF/EVICON@W3 | 200 | +51.68 | 22.04 |
| T6 firmware W=6 | ZEPHYR_PLAIN_QOS0/EVICON@W6 -> ZEPHYR_CUTOFF/EVICON@W6 | 200 | +43.02 | 25.40 |
| T6 firmware W=16 | ZEPHYR_PLAIN_QOS0/EVICON@W16 -> ZEPHYR_CUTOFF/EVICON@W16 | 200 | +28.98 | 26.45 |
| T6 firmware W=32 | ZEPHYR_PLAIN_QOS0/EVICON@W32 -> ZEPHYR_CUTOFF/EVICON@W32 | 200 | +19.41 | 20.47 |
| T6 firmware W=inf | ZEPHYR_PLAIN_QOS0/EVICON@Winf -> ZEPHYR_CUTOFF/EVICON@Winf | 200 | +14.53 | 11.56 |
| T6 ThingsBoard W=3 | TB_RPC/M1W@W3 -> TB_RPC/EVICON@W3 | 200 | -0.23 | 3.13 |
| T6 ThingsBoard W=6 | TB_RPC/M1W@W6 -> TB_RPC/EVICON@W6 | 200 | -0.74 | 5.49 |
| T6 ThingsBoard W=16 | TB_RPC/M1W@W16 -> TB_RPC/EVICON@W16 | 200 | -0.44 | 4.05 |
| T6 ThingsBoard W=inf | TB_RPC/M1W@Winf -> TB_RPC/EVICON@Winf | 200 | -0.01 | 0.06 |
| T6 ThingsBoard gap W=inf minus W=3 (estimand, was T6-3) | gap at W=3 -> gap at W=inf | 200 | +0.23 | 3.08 |
| T3 K0_QUEUE 10 s -> 30 s | K0_QUEUE/RELEASE_ON_TIMEOUT_10S -> K0_QUEUE/RELEASE_ON_TIMEOUT_30S | 200 | +5.31 | 4.40 |
| T3 K0_DIRECT 10 s -> 30 s | K0_DIRECT/RELEASE_ON_TIMEOUT_10S -> K0_DIRECT/RELEASE_ON_TIMEOUT_30S | 200 | +0.94 | 1.75 |
| T3 K0_QUEUE 10 s -> 60 s | K0_QUEUE/RELEASE_ON_TIMEOUT_10S -> K0_QUEUE/RELEASE_ON_TIMEOUT_60S | 200 | +9.22 | 5.92 |
| T3 K0_DIRECT 10 s -> 60 s | K0_DIRECT/RELEASE_ON_TIMEOUT_10S -> K0_DIRECT/RELEASE_ON_TIMEOUT_60S | 200 | +1.38 | 6.22 |
| T3 K0_QUEUE 10 s -> 120 s | K0_QUEUE/RELEASE_ON_TIMEOUT_10S -> K0_QUEUE/RELEASE_ON_TIMEOUT_120S | 200 | +9.11 | 5.81 |
| T3 K0_DIRECT 10 s -> 120 s | K0_DIRECT/RELEASE_ON_TIMEOUT_10S -> K0_DIRECT/RELEASE_ON_TIMEOUT_120S | 200 | -5.15 | 13.97 |
| T5 gamma 3 -> 1 (W=6) | K0F/EVICON_G3 -> K0F/EVICON_G1 | 200 | +0.00 | 0.00 |
| T5 gamma 3 -> 1 (W=inf) | K0F/EVICON_G3 -> K0F/EVICON_G1 | 200 | +0.00 | 0.00 |
| T5 gamma 3 -> 2 (W=6) | K0F/EVICON_G3 -> K0F/EVICON_G2 | 200 | +0.00 | 0.00 |
| T5 gamma 3 -> 2 (W=inf) | K0F/EVICON_G3 -> K0F/EVICON_G2 | 200 | +0.00 | 0.00 |
| T5 gamma 3 -> 4 (W=6) | K0F/EVICON_G3 -> K0F/EVICON_G4 | 200 | +0.00 | 0.00 |
| T5 gamma 3 -> 4 (W=inf) | K0F/EVICON_G3 -> K0F/EVICON_G4 | 200 | +0.00 | 0.00 |
| T5 gamma 3 -> greedy (W=6) | K0F/EVICON_G3 -> K0F/EVICON_GREEDY | 200 | +0.00 | 0.00 |
| T5 gamma 3 -> greedy (W=inf) | K0F/EVICON_G3 -> K0F/EVICON_GREEDY | 200 | +0.00 | 0.00 |
| T5 fence cost 5 -> THETA0 | K0F/EVICON_FC5 -> K0F/EVICON_THETA0 | 200 | -0.15 | 4.16 |
| T5 fence cost 5 -> FC0.5 | K0F/EVICON_FC5 -> K0F/EVICON_FC0.5 | 200 | +0.01 | 3.33 |
| T5 fence cost 5 -> FC50 | K0F/EVICON_FC5 -> K0F/EVICON_FC50 | 200 | -1.61 | 6.52 |
| T5 fence cost 5 -> FC100 | K0F/EVICON_FC5 -> K0F/EVICON_FC100 | 200 | -1.78 | 6.53 |
| T5 fence cost 5 -> FC500 | K0F/EVICON_FC5 -> K0F/EVICON_FC500 | 200 | -3.95 | 10.74 |

## Decision rules of the plan under the simulator's paired differences (Holm within each family)

| ID | family | comparison | metric | rule | n | sim mean (SD) | power |
|---|---|---|---|---|---|---|---|
| T1-3 | trigger-k2 | K2/M1W -> K2/EVICON (t1_k2) | clean_fulfilled | equivalent 3.0 | 8 | +0.00 (0.04) | 1.0 |
| T1-2 | trigger-tb | TB_RPC/M1 -> TB_RPC/M1W (t1_tb) | clean_fulfilled | at_least 10.0 | 6 | +35.77 (25.32) | 0.62 |
| T1-4 | trigger-tb | TB_RPC/M1W -> TB_RPC/EVICON (t1_tb) | clean_fulfilled | equivalent 5.0 | 6 | -0.74 (5.49) | 0.87 |
| T4-1 | binding | ZEPHYR_PLAIN_QOS0/EVICON -> ZEPHYR_CUTOFF/EVICON (t4_w6) | clean_fulfilled | at_least 10.0 | 8 | +43.02 (25.40) | 0.76 |
| T4-2 | binding | ZEPHYR_PLAIN_QOS0/EVICON -> ZEPHYR_CUTOFF/EVICON (t4_w6) | clean_fulfilled_confirmed | at_least 10.0 | 8 | +43.80 (24.96) | 0.81 |
| T4-3 | binding | ZEPHYR_PLAIN_DIRECT_QOS0/EVICON -> ZEPHYR_F_DIRECT/EVICON (t4_w6) | clean_fulfilled | at_least 10.0 | 8 | +43.09 (25.46) | 0.79 |
| T4-4 | binding | ZEPHYR_PLAIN_DIRECT_QOS0/EVICON -> ZEPHYR_F_DIRECT/EVICON (t4_winf) | clean_fulfilled | at_least 5.0 | 8 | +14.60 (11.71) | 0.43 |
| T4-5 | binding | ZEPHYR_PLAIN_QOS0/EVICON -> ZEPHYR_CUTOFF/EVICON (t4_winf) | clean_fulfilled_confirmed | at_least 5.0 | 8 | +16.01 (11.39) | 0.58 |
| T4-6 | binding | ZEPHYR_PLAIN_QOS0/EVICON -> ZEPHYR_CUTOFF/EVICON (t4_winf) | clean_fulfilled | at_least 5.0 | 8 | +14.53 (11.56) | 0.44 |
| T5-1 | greedy | K0F/EVICON -> K0F/EVICON_GREEDY (t5_s_winf) | clean_fulfilled | at_least -2.0 | 8 | +0.00 (0.00) | 1.0 |
| T5-2 | greedy | K0S/EVICON -> K0S/EVICON_GREEDY (t5_s_winf) | clean_fulfilled | at_least -2.0 | 8 | +0.00 (0.04) | 1.0 |
| T5-3 | greedy | MINIO_COND/EVICON -> MINIO_COND/EVICON_GREEDY (t5_minio) | clean_fulfilled | at_least -2.0 | 8 | +0.01 (0.07) | 1.0 |
| T5-4 | theta | K0F/EVICON_FC5 -> K0F/EVICON_FC100 (t5_theta) | clean_fulfilled | equivalent 5.0 | 8 | -1.78 (6.53) | 0.63 |
| T6-1 | window | ZEPHYR_PLAIN_QOS0/EVICON@W3 -> ZEPHYR_CUTOFF/EVICON@W3 -> ZEPHYR_PLAIN_QOS0/EVICON@W32 -> ZEPHYR_CUTOFF/EVICON@W32 (did t6_fw/t6_fw) | clean_fulfilled | at_most 0.0 | 8 | -32.27 (21.67) | 1.0 |
| T6-2 | window | ZEPHYR_PLAIN_QOS0/EVICON@W6 -> ZEPHYR_CUTOFF/EVICON@W6 -> ZEPHYR_PLAIN_QOS0/EVICON@Winf -> ZEPHYR_CUTOFF/EVICON@Winf (did t6_fw/t6_fw) | clean_fulfilled | at_most 0.0 | 8 | -28.49 (20.20) | 1.0 |
| T7-rate4-qF | t7-rate4 | K0/EVICON -> K0F/EVICON (t7_rate4_q6) | clean_fulfilled | at_least 0.0 | 4 | +47.10 (23.02) | 0.88 |
| T7-rate4-qS | t7-rate4 | K0/EVICON -> K0S/EVICON (t7_rate4_q6) | clean_fulfilled | at_least 0.0 | 4 | +58.91 (15.86) | 0.99 |
| T7-rate4-dF | t7-rate4 | K0/EVICON -> K0F/EVICON (t7_rate4_dinf) | clean_fulfilled | at_least 0.0 | 4 | +23.72 (10.10) | 0.96 |
| T7-off5-qF | t7-off5 | K0/EVICON -> K0F/EVICON (t7_off5_15_q6) | clean_fulfilled | at_least 0.0 | 4 | +33.20 (23.65) | 0.5 |
| T7-off5-qS | t7-off5 | K0/EVICON -> K0S/EVICON (t7_off5_15_q6) | clean_fulfilled | at_least 0.0 | 4 | +37.10 (23.27) | 0.56 |
| T7-off40-qF | t7-off40 | K0/EVICON -> K0F/EVICON (t7_off40_120_q6) | clean_fulfilled | at_least 0.0 | 4 | +41.14 (25.56) | 0.49 |
| T7-off40-qS | t7-off40 | K0/EVICON -> K0S/EVICON (t7_off40_120_q6) | clean_fulfilled | at_least 0.0 | 4 | +41.73 (24.55) | 0.52 |
| T7-off40-dF | t7-off40 | K0/EVICON -> K0F/EVICON (t7_off40_120_dinf) | clean_fulfilled | at_least 0.0 | 4 | +26.89 (16.12) | 0.56 |
| T7-seedA-qF | t7-seedA | K0/EVICON -> K0F/EVICON (t7_base_q6) | clean_fulfilled | at_least 0.0 | 4 | +40.12 (26.30) | 0.44 |
| T7-seedA-qS | t7-seedA | K0/EVICON -> K0S/EVICON (t7_base_q6) | clean_fulfilled | at_least 0.0 | 4 | +44.95 (24.69) | 0.54 |
| T7-seedA-dF | t7-seedA | K0/EVICON -> K0F/EVICON (t7_base_dinf) | clean_fulfilled | at_least 0.0 | 4 | +14.66 (10.09) | 0.46 |
| T7-seedB-qF | t7-seedB | K0/EVICON -> K0F/EVICON (t7_base_q6) | clean_fulfilled | at_least 0.0 | 4 | +40.12 (26.30) | 0.44 |
| T7-seedB-qS | t7-seedB | K0/EVICON -> K0S/EVICON (t7_base_q6) | clean_fulfilled | at_least 0.0 | 4 | +44.95 (24.69) | 0.54 |
| T7-seedB-dF | t7-seedB | K0/EVICON -> K0F/EVICON (t7_base_dinf) | clean_fulfilled | at_least 0.0 | 4 | +14.66 (10.09) | 0.46 |

## T7: the testbed's fault levels as realized by its schedules (campaign.schedule, 60 episodes x 4 devices)

| level | offline share | drop_up | drop_down | delay_up | crash windows / device-hour | mean offline (s) |
|---|---|---|---|---|---|---|
| base | 0.211 | 0.016 | 0.015 | 0.008 | 21.3 | 25.3 |
| rate05 | 0.060 | 0.004 | 0.004 | 0.003 | 5.7 | 24.8 |
| rate4 | 0.345 | 0.025 | 0.025 | 0.012 | 35.1 | 25.5 |
| off5_15 | 0.091 | 0.017 | 0.016 | 0.008 | 22.8 | 10.1 |
| off40_120 | 0.450 | 0.012 | 0.011 | 0.006 | 14.7 | 80.9 |

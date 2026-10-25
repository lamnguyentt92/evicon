# Simulator sweep (release 072, SIMULATION_ONLY)

200 episodes per point; base `/home/claude/work/EVICON_TOIT_072/experiments/sim/iot_dosing_testbed.json`.

## EVICON, clean / confirmed fulfilment (% of offered)

| point | sim-k0 | sim-k0+F | sim-k0+QS | sim-k2 | sim-k2+F | ORACLE |
|---|---|---|---|---|---|---|
| channelqueue_w1 | 11.38 / 10.87 | 58.63 / 58.33 | 58.69 / 58.39 | 58.43 / 58.12 | 58.43 / 58.12 | 77.04 |
| channelqueue_w2 | 20.26 / 19.2 | 62.88 / 62.43 | 73.91 / 73.53 | 73.86 / 73.48 | 73.65 / 73.26 | 76.98 |
| channelqueue_w4 | 30.37 / 28.22 | 68.98 / 68.28 | 76.91 / 76.53 | 76.87 / 76.49 | 76.98 / 76.59 | 76.9 |
| channelqueue_w6 | 37.46 / 34.28 | 72.66 / 71.8 | 76.86 / 76.49 | 76.8 / 76.42 | 76.86 / 76.48 | 76.83 |
| channelqueue_w16 | 56.07 / 48.57 | 76.16 / 75.02 | 76.7 / 76.32 | 76.66 / 76.26 | 76.78 / 76.38 | 76.67 |
| channelqueue_w1000000 | 75.59 / 61.75 | 76.57 / 75.39 | 76.62 / 76.22 | 76.59 / 76.13 | 76.69 / 76.22 | 76.6 |
| channeldirect_w1 | 10.97 / 10.66 | 58.08 / 57.77 | 57.92 / 57.61 | 56.43 / 56.12 | 57.49 / 57.17 | 77.02 |
| channeldirect_w2 | 18.04 / 17.55 | 59.21 / 58.88 | 73.91 / 73.52 | 72.98 / 72.59 | 73.51 / 73.1 | 77.06 |
| channeldirect_w4 | 26.05 / 25.31 | 65.46 / 65.04 | 77.08 / 76.69 | 76.12 / 75.71 | 76.91 / 76.49 | 77.04 |
| channeldirect_w6 | 32.45 / 31.55 | 69.87 / 69.37 | 77.13 / 76.75 | 76.31 / 75.91 | 76.84 / 76.43 | 77.01 |
| channeldirect_w16 | 47.24 / 45.93 | 74.79 / 74.18 | 77.21 / 76.84 | 76.37 / 76.0 | 76.78 / 76.4 | 77.01 |
| channeldirect_w1000000 | 62.26 / 60.51 | 75.57 / 74.96 | 77.25 / 76.88 | 76.46 / 76.1 | 76.68 / 76.3 | 76.92 |
| pressure0.8_w6 | 39.16 / 35.89 | 89.21 / 87.97 | 98.43 / 97.87 | 98.8 / 98.25 | 97.0 / 96.45 | 98.85 |
| pressure0.8_w1000000 | 97.51 / 79.83 | 97.22 / 95.48 | 98.84 / 98.27 | 98.81 / 98.15 | 97.03 / 96.4 | 98.84 |
| pressure1.0_w6 | 39.03 / 35.77 | 87.23 / 86.03 | 95.48 / 94.94 | 95.57 / 95.03 | 94.57 / 94.04 | 95.57 |
| pressure1.0_w1000000 | 94.14 / 77.16 | 94.51 / 92.85 | 95.45 / 94.89 | 95.39 / 94.78 | 94.52 / 93.92 | 95.42 |
| pressure2.0_w6 | 31.98 / 29.2 | 49.28 / 48.77 | 50.11 / 49.86 | 50.08 / 49.82 | 50.08 / 49.83 | 50.09 |
| pressure2.0_w1000000 | 49.24 / 40.68 | 50.05 / 49.47 | 49.92 / 49.64 | 49.91 / 49.58 | 49.92 / 49.6 | 49.92 |
| loss0.05_w6 | 28.57 / 26.23 | 69.21 / 68.54 | 76.85 / 76.49 | 76.78 / 76.41 | 76.83 / 76.45 | 76.83 |
| loss0.05_w1000000 | 72.64 / 59.34 | 75.75 / 74.71 | 76.63 / 76.24 | 76.58 / 76.13 | 76.64 / 76.18 | 76.6 |
| loss0.1_w6 | 20.14 / 18.44 | 65.12 / 64.5 | 76.87 / 76.48 | 76.76 / 76.39 | 76.75 / 76.37 | 76.83 |
| loss0.1_w1000000 | 68.89 / 56.22 | 74.73 / 73.68 | 76.66 / 76.26 | 76.57 / 76.12 | 76.54 / 76.07 | 76.6 |
| crash0.0_w6 | 38.36 / 35.24 | 76.98 / 76.98 | 76.87 / 76.85 | 76.81 / 76.79 | 76.81 / 76.8 | 76.84 |
| crash0.0_w1000000 | 75.61 / 62.1 | 76.98 / 76.98 | 76.64 / 76.6 | 76.6 / 76.52 | 76.6 / 76.52 | 76.62 |
| crash0.02_w6 | 34.42 / 31.08 | 60.22 / 57.86 | 76.66 / 75.23 | 76.6 / 75.17 | 76.83 / 75.4 | 76.81 |
| crash0.02_w1000000 | 75.57 / 60.68 | 75.91 / 71.73 | 76.6 / 75.12 | 76.56 / 75.0 | 76.87 / 75.35 | 76.58 |
| offline_mean_ms120000_w6 | - | - | - | 63.1 / 62.72 | - | 63.34 |

## Power of the 072 decision rules under the simulator's paired differences

| n | ID | point | policy | comparison | metric | rule | sim mean (SD) | power |
|---|---|---|---|---|---|---|---|---|
| 8 | H-W1 | channelqueue_w6 | EVICON | sim-k0 -> sim-k0+F | clean_fulfilled | at_least 10 | +35.56 (25.25) | 0.76 |
| 8 | H-W1 | channelqueue_w6 | RES_PERIODIC | sim-k0 -> sim-k0+F | clean_fulfilled | at_least 10 | +34.08 (25.81) | 0.71 |
| 8 | H-W2 | channelqueue_w1000000 | EVICON | sim-k0 -> sim-k0+F | clean_fulfilled | equivalent 3 | +0.99 (2.21) | 0.83 |
| 8 | H-W2 | channelqueue_w1000000 | RES_PERIODIC | sim-k0 -> sim-k0+F | clean_fulfilled | equivalent 3 | +0.95 (1.99) | 0.88 |
| 8 | H-W3 | channelqueue_w1000000 | EVICON | sim-k0 -> sim-k0+F | clean_fulfilled_confirmed | at_least 5 | +13.71 (10.74) | 0.57 |
| 8 | H-W3 | channelqueue_w1000000 | RES_PERIODIC | sim-k0 -> sim-k0+F | clean_fulfilled_confirmed | at_least 5 | +13.29 (10.62) | 0.53 |
| 8 | H-W4 | channeldirect_w1000000 | EVICON | sim-k0 -> sim-k0+F | clean_fulfilled | at_least 5 | +13.39 (11.48) | 0.58 |
| 8 | H-W5 | channeldirect_w6 | EVICON | sim-k0 -> sim-k0+F | clean_fulfilled | at_least 10 | +37.89 (26.34) | 0.85 |
| 8 | H-S1 | channelqueue_w6 | EVICON | sim-k0 -> sim-k0+QS | clean_fulfilled | at_least 10 | +39.91 (24.55) | 0.93 |
| 8 | H-S4 | channelqueue_w1000000 | EVICON | sim-k0 -> sim-k0+QS | clean_fulfilled_confirmed | at_least 5 | +14.57 (10.49) | 0.82 |
| 8 | H-S5 | channelqueue_w1000000 | EVICON | sim-k0 -> sim-k0+QS | clean_fulfilled | equivalent 3 | +1.04 (0.88) | 0.99 |
| 8 | E-S1 | channelqueue_w6 | EVICON | sim-k0+F -> sim-k0+QS | clean_fulfilled | at_least 0 | +4.35 (11.83) | 0.07 |
| 12 | H-W1 | channelqueue_w6 | EVICON | sim-k0 -> sim-k0+F | clean_fulfilled | at_least 10 | +35.56 (25.25) | 0.96 |
| 12 | H-W1 | channelqueue_w6 | RES_PERIODIC | sim-k0 -> sim-k0+F | clean_fulfilled | at_least 10 | +34.08 (25.81) | 0.94 |
| 12 | H-W2 | channelqueue_w1000000 | EVICON | sim-k0 -> sim-k0+F | clean_fulfilled | equivalent 3 | +0.99 (2.21) | 0.9 |
| 12 | H-W2 | channelqueue_w1000000 | RES_PERIODIC | sim-k0 -> sim-k0+F | clean_fulfilled | equivalent 3 | +0.95 (1.99) | 0.91 |
| 12 | H-W3 | channelqueue_w1000000 | EVICON | sim-k0 -> sim-k0+F | clean_fulfilled_confirmed | at_least 5 | +13.71 (10.74) | 0.88 |
| 12 | H-W3 | channelqueue_w1000000 | RES_PERIODIC | sim-k0 -> sim-k0+F | clean_fulfilled_confirmed | at_least 5 | +13.29 (10.62) | 0.85 |
| 12 | H-W4 | channeldirect_w1000000 | EVICON | sim-k0 -> sim-k0+F | clean_fulfilled | at_least 5 | +13.39 (11.48) | 0.83 |
| 12 | H-W5 | channeldirect_w6 | EVICON | sim-k0 -> sim-k0+F | clean_fulfilled | at_least 10 | +37.89 (26.34) | 0.97 |
| 12 | H-S1 | channelqueue_w6 | EVICON | sim-k0 -> sim-k0+QS | clean_fulfilled | at_least 10 | +39.91 (24.55) | 0.99 |
| 12 | H-S4 | channelqueue_w1000000 | EVICON | sim-k0 -> sim-k0+QS | clean_fulfilled_confirmed | at_least 5 | +14.57 (10.49) | 0.97 |
| 12 | H-S5 | channelqueue_w1000000 | EVICON | sim-k0 -> sim-k0+QS | clean_fulfilled | equivalent 3 | +1.04 (0.88) | 1.0 |
| 12 | E-S1 | channelqueue_w6 | EVICON | sim-k0+F -> sim-k0+QS | clean_fulfilled | at_least 0 | +4.35 (11.83) | 0.18 |
| 16 | H-W1 | channelqueue_w6 | EVICON | sim-k0 -> sim-k0+F | clean_fulfilled | at_least 10 | +35.56 (25.25) | 0.99 |
| 16 | H-W1 | channelqueue_w6 | RES_PERIODIC | sim-k0 -> sim-k0+F | clean_fulfilled | at_least 10 | +34.08 (25.81) | 0.99 |
| 16 | H-W2 | channelqueue_w1000000 | EVICON | sim-k0 -> sim-k0+F | clean_fulfilled | equivalent 3 | +0.99 (2.21) | 0.91 |
| 16 | H-W2 | channelqueue_w1000000 | RES_PERIODIC | sim-k0 -> sim-k0+F | clean_fulfilled | equivalent 3 | +0.95 (1.99) | 0.92 |
| 16 | H-W3 | channelqueue_w1000000 | EVICON | sim-k0 -> sim-k0+F | clean_fulfilled_confirmed | at_least 5 | +13.71 (10.74) | 0.97 |
| 16 | H-W3 | channelqueue_w1000000 | RES_PERIODIC | sim-k0 -> sim-k0+F | clean_fulfilled_confirmed | at_least 5 | +13.29 (10.62) | 0.97 |
| 16 | H-W4 | channeldirect_w1000000 | EVICON | sim-k0 -> sim-k0+F | clean_fulfilled | at_least 5 | +13.39 (11.48) | 0.94 |
| 16 | H-W5 | channeldirect_w6 | EVICON | sim-k0 -> sim-k0+F | clean_fulfilled | at_least 10 | +37.89 (26.34) | 1.0 |
| 16 | H-S1 | channelqueue_w6 | EVICON | sim-k0 -> sim-k0+QS | clean_fulfilled | at_least 10 | +39.91 (24.55) | 1.0 |
| 16 | H-S4 | channelqueue_w1000000 | EVICON | sim-k0 -> sim-k0+QS | clean_fulfilled_confirmed | at_least 5 | +14.57 (10.49) | 1.0 |
| 16 | H-S5 | channelqueue_w1000000 | EVICON | sim-k0 -> sim-k0+QS | clean_fulfilled | equivalent 3 | +1.04 (0.88) | 1.0 |
| 16 | E-S1 | channelqueue_w6 | EVICON | sim-k0+F -> sim-k0+QS | clean_fulfilled | at_least 0 | +4.35 (11.83) | 0.32 |

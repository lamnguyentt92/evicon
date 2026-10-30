Written on 30/10/2026, after the final report of release 081 (run review-20261030T182927); the numbers are those of
the analysis above and of the report's own files (`report/esim077/`, `report/t3/`, `report/safety/`, the campaign's
`runtime_episodes.csv`). Statements marked post hoc were not in the plan.

**Outcome.** Every campaign of the plan ran with release 078 in run review-20261028T080322, every cell VALID. The 33
hypotheses of the plan are decided: 32 as preregistered and K-2 NOT MET. L2-1 MET (by the plan's rule on the output of
`late_attribution.py`); X-Q0, X-MCU and X-AUTH PASS. Safety over every testbed campaign: no violation in any guarded
cell (1050 cells, 751,258 admitted commands); the practice baselines violate safety in every run where the plan
requires it (RT-1 to RT-3, RR-1, RR-2 DETECTED).

**K-2 (NOT MET, post hoc explanation).** RES_PERIODIC, K2 -> K3F: +3.96 pp (95 % t [-1.95, +9.88], n = 5; per episode
+4.6, +3.6, +11.6, 0, 0), outside the equivalence margin of 2 pp. Under K2 the periodic baseline has no evidence action:
it never queries, and K2 offers a query but no fence, so every decision of those cells is ESCALATE (no query, no fence)
and an unresolved command keeps its slot of the window until a receipt arrives (window defers 398 per episode, against
43 with K3F). EviCon queries under the same contract and K2 = K3F exactly (K-1 MET). Against experiment A (E-K-1, same
seed and schedules) only the K2/RES_PERIODIC cells changed (-3.97 pp clean); the other cells moved by at most 0.1 pp.
The configuration differs from experiment A in two ways, an explicit window of 6 and the validity cut of resends at
30 s; the run does not separate them. By the plan's rule, the paper reports this run's K2 numbers instead of experiment
A's.

**Theorem 3 on the traces.** 37 runs, 1116 re-tests after APPLIED-only evidence, all BLOCK; no capacity re-opening
without evidence of non-occurrence in a guarded cell. The practice baselines RELEASE_ON_TIMEOUT and RELEASE_ON_READ
release without evidence by design and are outside the theorem's premise: 163 re-openings without evidence in their 4
runs (counted apart since release 081).

**E-SIM (estimand).** Against the simulator prediction of section 1(g): 30 effects scored, all with the predicted sign,
26 with the predicted direction, 17 inside the testbed's 95 % interval; mean absolute error 6.05 pp, Spearman 0.95. The
simulator underestimates the harm of release on timeout (safety violations per episode: testbed 191.2 against 35.8 on
the queue channel, 26.4 against 6.4 on the direct one); the direction is right.

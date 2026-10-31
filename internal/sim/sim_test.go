package sim

import (
	"encoding/json"
	"path/filepath"
	"runtime"
	"testing"

	"evicon/internal/contract"
)

func root(t *testing.T) string {
	_, f, _, _ := runtime.Caller(0)
	return filepath.Join(filepath.Dir(f), "..", "..")
}

func catalog(t *testing.T) string { return DefaultCatalogDir(root(t)) }

func loadCfg(t *testing.T, name string) Config {
	c, err := LoadConfig(filepath.Join(root(t), "experiments", "sim", name))
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func shortIoT(t *testing.T, contract string, policies ...string) Config {
	c := loadCfg(t, "iot_dosing.json")
	c.Sweep = nil
	c.Owner.Contract = contract
	c.Episodes = 3
	c.Workload.DurationMS = 180000
	c.Workload.TailMS = 60000
	c.Workload.ComponentMax = []int64{220}
	c.Policies = policies
	return c
}

func byPolicy(rows []Row) map[string][]Row {
	m := map[string][]Row{}
	for _, r := range rows {
		m[r.Policy] = append(m[r.Policy], r)
	}
	return m
}

func sum(rows []Row, f func(Row) int) int {
	t := 0
	for _, r := range rows {
		t += f(r)
	}
	return t
}

func TestDeterministicCRN(t *testing.T) {
	c := shortIoT(t, "sim-k0+F", PolicyB2Ledger, PolicyEvicon, PolicyResImmediate, PolicyOracle)
	a, err := Run(c, catalog(t), 2)
	if err != nil {
		t.Fatal(err)
	}
	b, err := Run(c, catalog(t), 1)
	if err != nil {
		t.Fatal(err)
	}
	ja, _ := json.Marshal(a)
	jb, _ := json.Marshal(b)
	if string(ja) != string(jb) {
		t.Fatal("simulation must be deterministic and independent of worker count")
	}
	// common random numbers: every policy sees the same offered workload
	for _, r := range a {
		if r.Offered != a[0].Offered && r.Episode == a[0].Episode {
			t.Fatal("offered load differs across policies within an episode")
		}
	}
}

// D0: the replay of the 059 regime must reproduce selector parity (T3) on the
// ADD-only workloads: M1-M4 admit and fulfil exactly the same requests.
func TestReplay059SelectorParity(t *testing.T) {
	base := loadCfg(t, "replay059.json")
	cfgs, err := base.Expand()
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range cfgs {
		if c.Workload.SetFraction > 0 {
			continue
		}
		c.Episodes = 40
		rows, err := Run(c, catalog(t), 2)
		if err != nil {
			t.Fatal(err)
		}
		bp := byPolicy(rows)
		for ep := 0; ep < c.Episodes; ep++ {
			ref := -1
			for _, p := range []string{PolicyReplayM1, PolicyReplayM2, PolicyReplayM3, PolicyReplayM4} {
				for _, r := range bp[p] {
					if r.Episode != ep {
						continue
					}
					if ref < 0 {
						ref = r.CleanFulfilled
					} else if r.CleanFulfilled != ref {
						t.Fatalf("%s episode %d: %s fulfilled %d != %d", c.Regime, ep, p, r.CleanFulfilled, ref)
					}
				}
			}
		}
	}
}

// EviCon must never produce bound violations, duplicate effects or false STOPPED
// evidence on any contract (T1 under the simulated premises).
func TestEviconSoundAcrossContracts(t *testing.T) {
	contracts := []string{"sim-k0", "sim-k1-h45s", "sim-k2", "sim-k3-stop", "sim-k3-fence", "sim-k3-expiry", "sim-k4", "sim-device-cutoff", "hyp-matter-timed-invoke"}
	for _, ct := range append(contracts, "sim-k0+F", "sim-k2+F") {
		c := shortIoT(t, ct, append([]string{PolicyB2Ledger, PolicyOracle}, ResolverPolicies...)...)
		rows, err := Run(c, catalog(t), 2)
		if err != nil {
			t.Fatal(err)
		}
		for _, r := range rows {
			if r.BoundViolations != 0 || r.DuplicateEffects != 0 || r.FalseStopped != 0 {
				t.Fatalf("%s %s ep%d unsound: bound=%d dup=%d falseStopped=%d", ct, r.Policy, r.Episode, r.BoundViolations, r.DuplicateEffects, r.FalseStopped)
			}
		}
	}
}

func TestEviconSoundInCloudRegimeWithCrashes(t *testing.T) {
	c := loadCfg(t, "cloud_quota.json")
	c.Sweep = nil
	c.Episodes = 2
	c.Workload.DurationMS = 10000
	c.Owner.CrashMidProb = 0.05
	for _, reconcile := range []bool{false, true} {
		c.Owner.OrphanReconcile = reconcile
		for _, ct := range []string{"E1", "E2", "E2_STOP", "E3", "E1+F"} {
			c.Owner.Contract = ct
			c.Policies = append([]string{PolicyB2Ledger}, ResolverPolicies...)
			rows, err := Run(c, catalog(t), 2)
			if err != nil {
				t.Fatal(err)
			}
			for _, r := range rows {
				if r.SafetyViolations != 0 || r.FalseStopped != 0 {
					t.Fatalf("%s %s reconcile=%v: safety=%d falseStopped=%d", ct, r.Policy, reconcile, r.SafetyViolations, r.FalseStopped)
				}
			}
		}
	}
}

// Without dedup, blind same-identity retry duplicates effects and naive
// new-identity retry violates the bound (negative controls).
func TestUnsafeBaselinesAreDetected(t *testing.T) {
	c := shortIoT(t, "sim-k0", PolicyB0Naive, PolicyB1Durable)
	rows, err := Run(c, catalog(t), 2)
	if err != nil {
		t.Fatal(err)
	}
	bp := byPolicy(rows)
	if sum(bp[PolicyB1Durable], func(r Row) int { return r.DuplicateEffects }) == 0 {
		t.Fatal("B1 on K0 must produce duplicate effects")
	}
	if sum(bp[PolicyB0Naive], func(r Row) int { return r.SafetyViolations + r.UnsafeReleases }) == 0 {
		t.Fatal("B0 must produce violations or false STOPPED evidence")
	}
}

// T2 illustration and the contract-upgrade experiment (review RV07): on K0
// no policy has backward evidence, so EVICON equals the safe ledger; on the
// SAME owner upgraded with a fence (K0+F), every policy runs on the upgraded
// contract and fence-then-retry recovers work. The gain belongs to the
// contract, not to the policy.
func TestFenceThenRetryRecoversWorkWithoutDedup(t *testing.T) {
	f := func(rows []Row, p string) int {
		return sum(byPolicy(rows)[p], func(r Row) int { return r.CleanFulfilled })
	}
	k0, err := Run(shortIoT(t, "sim-k0", PolicyB2Ledger, PolicyEvicon), catalog(t), 2)
	if err != nil {
		t.Fatal(err)
	}
	if f(k0, PolicyEvicon) != f(k0, PolicyB2Ledger) {
		t.Fatalf("EVICON has no backward evidence on K0 and must equal the ledger: %d vs %d", f(k0, PolicyEvicon), f(k0, PolicyB2Ledger))
	}
	kf, err := Run(shortIoT(t, "sim-k0+F", PolicyB2Ledger, PolicyEvicon), catalog(t), 2)
	if err != nil {
		t.Fatal(err)
	}
	for _, r := range kf {
		if r.Contract != "sim-k0+F" {
			t.Fatalf("upgraded cell must keep its effective contract id, got %s", r.Contract)
		}
	}
	if f(kf, PolicyEvicon) <= f(k0, PolicyEvicon) {
		t.Fatalf("the fence upgrade should recover work: %d vs %d", f(kf, PolicyEvicon), f(k0, PolicyEvicon))
	}
	if sum(byPolicy(kf)[PolicyEvicon], func(r Row) int { return r.Fences }) == 0 {
		t.Fatal("expected fences")
	}
}

// Review RV07: the same-capability baselines use the same contract and the
// same actions; only timing differs. They must all be safe and must act.
func TestSameCapabilityBaselines(t *testing.T) {
	c := shortIoT(t, "sim-k3-stop", ResolverPolicies...)
	rows, err := Run(c, catalog(t), 2)
	if err != nil {
		t.Fatal(err)
	}
	bp := byPolicy(rows)
	for _, p := range ResolverPolicies {
		if sum(bp[p], func(r Row) int { return r.SafetyViolations }) != 0 {
			t.Fatalf("%s unsafe", p)
		}
		if sum(bp[p], func(r Row) int { return r.Stops }) == 0 {
			t.Fatalf("%s never used the stop it is allowed to use", p)
		}
		for _, r := range bp[p] {
			if r.Contract != "sim-k3-stop" {
				t.Fatalf("%s ran on %s", p, r.Contract)
			}
		}
	}
	if _, err := ParseConfig([]byte(`{"schema_version":1,"regime":"x","episodes":1,"policies":["EVICON_KF"],"workload":{"dimension":1,"initial":[0],"value_min":[1],"value_max":[1],"rate_per_sec":1,"duration_ms":10,"deadline_ms":1},"owner":{"contract":"sim-k0"}}`)); err == nil {
		t.Fatal("EVICON_KF must be rejected (contract change disguised as a policy)")
	}
}

// Review RV06: with crashes between effect and receipt, an owner that cannot
// reconcile orphans leaves them UNCERTAIN (like evicon-device D08); one that
// can resolves them. Both are safe; only the reconciling owner resolves more.
func TestCrashOrphanReconcileIsAContractDifference(t *testing.T) {
	c := loadCfg(t, "cloud_quota.json")
	c.Sweep = nil
	c.Episodes = 3
	c.Workload.DurationMS = 20000
	c.Owner.CrashMidProb = 0.1
	c.Owner.Contract = "E1+F"
	c.Policies = []string{PolicyEvicon}
	unres := map[bool]int{}
	for _, reconcile := range []bool{false, true} {
		c.Owner.OrphanReconcile = reconcile
		rows, err := Run(c, catalog(t), 2)
		if err != nil {
			t.Fatal(err)
		}
		for _, r := range rows {
			if r.SafetyViolations != 0 {
				t.Fatalf("reconcile=%v unsafe: %+v", reconcile, r)
			}
			unres[reconcile] += r.UnresolvedAtEnd
		}
		if sum(rows, func(r Row) int { return r.OwnerCrashes }) == 0 {
			t.Fatal("expected owner crashes")
		}
	}
	if unres[false] <= unres[true] {
		t.Fatalf("orphans must stay unresolved without reconciliation: without=%d with=%d", unres[false], unres[true])
	}
}

func TestOwnerExpiryPreventsLateEffects(t *testing.T) {
	c := shortIoT(t, "sim-k3-expiry", PolicyB2Ledger, PolicyEvicon, PolicyOracle)
	rows, err := Run(c, catalog(t), 2)
	if err != nil {
		t.Fatal(err)
	}
	for _, r := range rows {
		if r.LateEffects != 0 {
			t.Fatalf("%s: owner expiry (30 s) < validity (90 s) must prevent late effects", r.Policy)
		}
	}
}

func TestConfigValidationAndSweep(t *testing.T) {
	bad := []string{
		`{"schema_version":2}`,
		`{"schema_version":1,"regime":"x","episodes":1,"policies":["NOPE"],"workload":{"dimension":1,"initial":[0],"value_min":[1],"value_max":[1],"rate_per_sec":1,"duration_ms":10,"deadline_ms":1},"owner":{"contract":"sim-k0"}}`,
		`{"schema_version":1,"regime":"x","episodes":1,"policies":["ORACLE"],"workload":{"dimension":1,"initial":[0],"value_min":[2],"value_max":[1],"rate_per_sec":1,"duration_ms":10,"deadline_ms":1},"owner":{"contract":"sim-k0"}}`,
		`{"schema_version":1,"regime":"x","episodes":1,"policies":["ORACLE"],"workload":{"dimension":1,"initial":[0],"value_min":[1],"value_max":[1],"rate_per_sec":1,"duration_ms":10,"deadline_ms":1},"network":{"req_loss_prob":2},"owner":{"contract":"sim-k0"}}`,
		`{"schema_version":1,"regime":"x","episodes":1,"policies":["ORACLE"],"unknown":1}`,
	}
	for i, b := range bad {
		if _, err := ParseConfig([]byte(b)); err == nil {
			t.Fatalf("bad config %d accepted", i)
		}
	}
	c := loadCfg(t, "iot_dosing.json")
	cs, err := c.Expand()
	if err != nil || len(cs) != len(c.Sweep) {
		t.Fatalf("expand: %v %d", err, len(cs))
	}
	if cs[0].Owner.Contract != "sim-k0" || cs[0].Owner.Channel != "queue" {
		t.Fatalf("sweep patch must merge, got %+v", cs[0].Owner)
	}
}

func TestResolveContractAliases(t *testing.T) {
	e2, err := ResolveContract("E2", catalog(t))
	if err != nil || e2.Has("Q_S") {
		t.Fatalf("060 E2 must not have Q_S: %v %v", e2.Capabilities, err)
	}
	e2s, err := ResolveContract("E2_STOP", catalog(t))
	if err != nil || !e2s.Has("Q_S") || !e2s.Has("F") {
		t.Fatalf("E2_STOP must have Q_S and F: %v %v", e2s.Capabilities, err)
	}
	if _, err := ResolveContract("does-not-exist", catalog(t)); err == nil {
		t.Fatal("unknown contract must fail")
	}
}

// T4: with a bounded network delay and a finite dedup horizon, horizon-aware
// forward retry never duplicates; blind durable retry beyond the horizon does.
func TestT4HorizonAwareRetry(t *testing.T) {
	c := shortIoT(t, "sim-k1-h45s", PolicyEvicon, PolicyB1Durable)
	c.Owner.Channel = "direct"
	c.Owner.OnlineMeanMS, c.Owner.OfflineMeanMS = 0, 0
	c.Network.ReqLateProb = 0
	c.Network.RespLossProb = 0.3 // many lost replies -> many retries
	c.Authority.MaxRetryMS = 600000
	rows, err := Run(c, catalog(t), 2)
	if err != nil {
		t.Fatal(err)
	}
	bp := byPolicy(rows)
	if sum(bp[PolicyEvicon], func(r Row) int { return r.DuplicateEffects }) != 0 {
		t.Fatal("horizon-aware retry must not duplicate under bounded delay")
	}
	if sum(bp[PolicyEvicon], func(r Row) int { return r.Retries }) == 0 {
		t.Fatal("expected forward retries inside the horizon")
	}
}

// Release 066: the experiment A twin models two testbed mechanisms the 065 twin
// missed. A reply after the authority's 2 s dispatch timeout is lost (the HTTP
// client closed the connection), and a fence aborts when the owner cannot
// certify an identity in its frontier (crash orphan). Defaults keep the old
// behaviour; dropping late replies never adds clean fulfilment; both stay safe.
func TestTestbedTwinMechanisms066(t *testing.T) {
	base := loadCfg(t, "iot_dosing_testbed.json")
	base.Sweep = nil
	base.Episodes = 3
	base.Workload.DurationMS = 180000
	base.Policies = []string{PolicyReplayM1, PolicyEvicon}
	run := func(contract, late, fence string) []Row {
		c := base
		c.Owner.Contract = contract
		c.Network.LateReplies = late
		c.Authority.FenceUncertain = fence
		rows, err := Run(c, catalog(t), 2)
		if err != nil {
			t.Fatal(err)
		}
		for _, r := range rows {
			if r.SafetyViolations != 0 || r.FalseStopped != 0 || r.DuplicateEffects != 0 {
				t.Fatalf("%s late=%s fence=%s %s: unsafe %+v", contract, late, fence, r.Policy, r)
			}
		}
		return rows
	}
	clean := func(rows []Row) int { return sum(rows, func(r Row) int { return r.CleanFulfilled }) }
	twin := byPolicy(run("sim-k0+F", "drop", "abort"))[PolicyEvicon]
	if sum(twin, func(r Row) int { return r.LateRepliesDropped }) == 0 {
		t.Fatal("late_replies=drop with a 2 s dispatch timeout must drop some replies")
	}
	if sum(twin, func(r Row) int { return r.FenceAborts }) == 0 {
		t.Fatal("fence_uncertain=abort with owner crashes must abort some fences")
	}
	old := byPolicy(run("sim-k0+F", "", ""))[PolicyEvicon]
	if n := sum(old, func(r Row) int { return r.LateRepliesDropped + r.FenceAborts }); n != 0 {
		t.Fatalf("defaults must keep the 065 behaviour, got %d drops/aborts", n)
	}
	k0drop := clean(byPolicy(run("sim-k0", "drop", "abort"))[PolicyReplayM1])
	k0deliver := clean(byPolicy(run("sim-k0", "deliver", "abort"))[PolicyReplayM1])
	t.Logf("K0+F EVICON: late replies dropped %d, fence aborts %d, clean %d (065 defaults: clean %d); K0 M1 clean: drop %d, deliver %d",
		sum(twin, func(r Row) int { return r.LateRepliesDropped }), sum(twin, func(r Row) int { return r.FenceAborts }), clean(twin), clean(old), k0drop, k0deliver)
	if k0drop > k0deliver {
		t.Fatalf("losing late replies cannot add clean fulfilment on K0: drop %d > deliver %d", k0drop, k0deliver)
	}
	for _, b := range []string{`"network":{"late_replies":"later"}`, `"authority":{"fence_uncertain":"ignore"}`} {
		cfg := `{"schema_version":1,"regime":"x","episodes":1,"policies":["ORACLE"],"workload":{"dimension":1,"initial":[0],"value_min":[1],"value_max":[1],"rate_per_sec":1,"duration_ms":10,"deadline_ms":1},"owner":{"contract":"sim-k0"},` + b + `}`
		if _, err := ParseConfig([]byte(cfg)); err == nil {
			t.Fatalf("invalid option accepted: %s", b)
		}
	}
}

// Release 068 (exploratory twin v2): offline periods drawn uniformly in [lo, hi] instead of
// exponentially; the frozen twin (no offline_uniform_ms) keeps its exponential schedule.
func TestOfflineUniform068(t *testing.T) {
	c := loadCfg(t, "iot_dosing_testbed.json")
	exp := buildEpisode(c, 0).schedule
	c.Owner.OfflineUniformMS = []float64{10000, 40000}
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	uni := buildEpisode(c, 0).schedule
	if len(uni) < 3 || len(exp) < 3 {
		t.Fatalf("schedules too short: %d %d", len(uni), len(exp))
	}
	outside := 0
	for i := 1; i < len(exp); i++ {
		if g := exp[i].start - exp[i-1].end; g < 10_000_000 || g > 40_000_000 {
			outside++
		}
	}
	for i := 1; i < len(uni); i++ {
		g := uni[i].start - uni[i-1].end // offline gap in microseconds
		if g < 10_000_000 || g > 40_000_000 {
			t.Fatalf("uniform offline gap %d us outside [10 s, 40 s]", g)
		}
	}
	if outside == 0 {
		t.Fatal("the exponential schedule should have offline gaps outside [10 s, 40 s]")
	}
	c.Owner.OfflineUniformMS = []float64{40000, 10000}
	if err := c.Validate(); err == nil {
		t.Fatal("lo > hi must be rejected")
	}
}

// Release 072: "<base>+QS" extends an owner with a per-identity stop
// (experiment S); EVICON then resolves by stops and recovers work on K0.
func TestStopUpgradeSuffix(t *testing.T) {
	c, err := ResolveContract("sim-k0+QS", catalog(t))
	if err != nil || !c.Has(contract.CapStop) || c.Has(contract.CapFence) || c.Level() != contract.K3 {
		t.Fatalf("sim-k0+QS: %+v %v", c, err)
	}
	if _, err := ResolveContract("sim-k3-stop+QS", catalog(t)); err == nil {
		t.Fatal("an owner that already has Q_S cannot be upgraded with it")
	}
	f := func(rows []Row, p string) int {
		return sum(byPolicy(rows)[p], func(r Row) int { return r.CleanFulfilled })
	}
	k0, err := Run(shortIoT(t, "sim-k0", PolicyEvicon), catalog(t), 2)
	if err != nil {
		t.Fatal(err)
	}
	ks, err := Run(shortIoT(t, "sim-k0+QS", PolicyEvicon), catalog(t), 2)
	if err != nil {
		t.Fatal(err)
	}
	if sum(byPolicy(ks)[PolicyEvicon], func(r Row) int { return r.Stops }) == 0 {
		t.Fatal("expected stops on sim-k0+QS")
	}
	if f(ks, PolicyEvicon) < f(k0, PolicyEvicon) {
		t.Fatalf("a stop upgrade must not lose work: %d vs %d", f(ks, PolicyEvicon), f(k0, PolicyEvicon))
	}
}

// Release 083 (PREREG_extra_083.md T1, T5): the QueryOnly policies and the greedy certificate in the twin.
// On K2 at a window of 6 the window-full trigger can only help (QUERY_ONLY_W >= QUERY_ONLY in clean
// fulfilment); at an unbounded window the greedy certificate never fails a search; nothing is unsafe; the
// defaults (enumeration, no QueryOnly policy) are unchanged.
func TestQueryOnlyAndGreedy083(t *testing.T) {
	base := loadCfg(t, "iot_dosing_testbed.json")
	base.Sweep = nil
	base.Episodes = 4
	base.Workload.DurationMS = 180000
	base.Authority.MaxOutstanding = 6
	base.Authority.AssumedMaxDelayMS = -1
	c := base
	c.Owner.Contract = "sim-k2"
	c.Policies = []string{PolicyQueryOnly, PolicyQueryOnlyW, PolicyEvicon}
	rows, err := Run(c, catalog(t), 2)
	if err != nil {
		t.Fatal(err)
	}
	bp := byPolicy(rows)
	clean := func(rs []Row) int { return sum(rs, func(r Row) int { return r.CleanFulfilled }) }
	for p, rs := range bp {
		if s := sum(rs, func(r Row) int { return r.SafetyViolations + r.FalseStopped + r.DuplicateEffects }); s != 0 {
			t.Fatalf("%s unsafe: %d", p, s)
		}
	}
	if clean(bp[PolicyQueryOnlyW]) < clean(bp[PolicyQueryOnly]) {
		t.Fatalf("the window trigger cannot cost clean fulfilment: QUERY_ONLY %d, QUERY_ONLY_W %d", clean(bp[PolicyQueryOnly]), clean(bp[PolicyQueryOnlyW]))
	}
	if sum(bp[PolicyQueryOnlyW], func(r Row) int { return r.Queries }) <= sum(bp[PolicyQueryOnly], func(r Row) int { return r.Queries }) {
		t.Fatal("QUERY_ONLY_W must query more than QUERY_ONLY (the window-full trigger)")
	}
	t.Logf("K2 W=6 clean: QUERY_ONLY %d, QUERY_ONLY_W %d, EVICON %d", clean(bp[PolicyQueryOnly]), clean(bp[PolicyQueryOnlyW]), clean(bp[PolicyEvicon]))

	g := base
	g.Owner.Contract = "sim-k0+F"
	g.Owner.Channel = "direct"
	g.Authority.MaxOutstanding = 1000000
	g.Policies = []string{PolicyEvicon}
	enum, err := Run(g, catalog(t), 2)
	if err != nil {
		t.Fatal(err)
	}
	g.Authority.Certificate = "greedy"
	if err := g.Validate(); err != nil {
		t.Fatal(err)
	}
	greedy, err := Run(g, catalog(t), 2)
	if err != nil {
		t.Fatal(err)
	}
	if n := sum(greedy, func(r Row) int { return r.CertificateErrors }); n != 0 {
		t.Fatalf("the greedy certificate never fails a search, got %d errors", n)
	}
	if s := sum(greedy, func(r Row) int { return r.SafetyViolations + r.FalseStopped + r.DuplicateEffects }); s != 0 {
		t.Fatalf("greedy unsafe: %d", s)
	}
	t.Logf("K0+F direct W=inf: certificate errors enumerate %d / greedy 0; clean enumerate %d, greedy %d",
		sum(enum, func(r Row) int { return r.CertificateErrors }), clean(enum), clean(greedy))
	bad := base
	bad.Authority.Certificate = "fast"
	if err := bad.Validate(); err == nil {
		t.Fatal("unknown certificate search accepted")
	}
}

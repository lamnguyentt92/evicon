package contract

import (
	"path/filepath"
	"runtime"
	"testing"
)

func repoRoot(t *testing.T) string {
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("caller")
	}
	return filepath.Join(filepath.Dir(file), "..", "..")
}

func TestLevelChain(t *testing.T) {
	cases := []struct {
		caps []EvidenceCapability
		h    int64
		want Level
	}{
		{nil, 0, K0},
		{[]EvidenceCapability{CapDedup}, 1000, K1},
		{[]EvidenceCapability{CapDedup, CapQueryApplied}, UnboundedHorizonMS, K2},
		{[]EvidenceCapability{CapDedup, CapQueryApplied, CapStop}, UnboundedHorizonMS, K3},
		{[]EvidenceCapability{CapQueryApplied, CapFence}, 0, K3},
		{[]EvidenceCapability{CapDedup, CapQueryApplied, CapAtomic}, UnboundedHorizonMS, K4},
	}
	for _, tc := range cases {
		c := EvidenceContract{SchemaVersion: 1, ID: "x", Source: SourceHypothesis, Capabilities: tc.caps, DedupHorizonMS: tc.h}
		if err := c.Validate(); err != nil {
			t.Fatalf("%v: %v", tc.caps, err)
		}
		if got := c.Level(); got != tc.want {
			t.Fatalf("%v: level %s want %s", tc.caps, got, tc.want)
		}
	}
}

func TestValidateRejectsInconsistentHorizon(t *testing.T) {
	bad := []EvidenceContract{
		{SchemaVersion: 1, ID: "a", Source: SourceHypothesis, Capabilities: []EvidenceCapability{CapDedup}},
		{SchemaVersion: 1, ID: "b", Source: SourceHypothesis, DedupHorizonMS: 10},
		{SchemaVersion: 1, ID: "c", Source: SourceHypothesis, Capabilities: []EvidenceCapability{CapExpiry}},
		{SchemaVersion: 1, ID: "d", Source: SourceHypothesis, Capabilities: []EvidenceCapability{CapAtomic}},
		{SchemaVersion: 1, ID: "e", Source: "ASSUMED"},
		{SchemaVersion: 1, ID: "f", Source: SourceHypothesis, Capabilities: []EvidenceCapability{"Z"}},
	}
	for _, c := range bad {
		if err := c.Validate(); err == nil {
			t.Fatalf("expected validation error for %s", c.ID)
		}
	}
}

// Safety rules of plan section 6.2: timeouts, NOT_FOUND, ordinary reads and
// non-owner expiry never become STOPPED evidence.
func TestAdmitNeverTreatsTimeoutAsStopped(t *testing.T) {
	all := EvidenceContract{SchemaVersion: 1, ID: "all", Source: SourceHypothesis,
		Capabilities:   []EvidenceCapability{CapDedup, CapQueryApplied, CapStop, CapFence, CapAtomic, CapExpiry},
		DedupHorizonMS: UnboundedHorizonMS, ExpiryMS: 1000, ClockSkewMS: 100}
	for _, o := range []Outcome{OutcomeTimeout, OutcomeNotFound, OutcomePending, OutcomeUncertain, OutcomeExpired} {
		for _, ch := range []EvidenceChannel{ChannelDispatchReply, ChannelQuery, ChannelStop, ChannelFence, ChannelExpiryClock, ChannelCancelAPI} {
			if o == OutcomeNotFound && ch == ChannelQuery {
				continue // covered by TestNotFoundTerminalOnlyAfterOwnerExpiry
			}
			if a := all.Admit(o, ch, 1<<40); a.Admissible {
				t.Fatalf("%s via %s must never be admissible", o, ch)
			}
		}
	}
	noT := EvidenceContract{SchemaVersion: 1, ID: "noT", Source: SourceHypothesis,
		Capabilities: []EvidenceCapability{CapDedup, CapQueryApplied, CapStop, CapFence, CapAtomic}, DedupHorizonMS: UnboundedHorizonMS}
	if noT.Admit(OutcomeNotFound, ChannelQuery, 1<<40).Admissible {
		t.Fatal("NOT_FOUND without owner expiry must never be terminal (request may still be in flight)")
	}
}

func TestAdmitStoppedRequiresBackwardCapability(t *testing.T) {
	k2 := EvidenceContract{SchemaVersion: 1, ID: "k2", Source: SourceHypothesis, Capabilities: []EvidenceCapability{CapDedup, CapQueryApplied}, DedupHorizonMS: UnboundedHorizonMS}
	k2finite := EvidenceContract{SchemaVersion: 1, ID: "k2f", Source: SourceHypothesis, Capabilities: []EvidenceCapability{CapDedup, CapQueryApplied}, DedupHorizonMS: 60000}
	if k2finite.Admit(OutcomeStopped, ChannelQuery, 0).Admissible {
		t.Fatal("finite-horizon K2 query STOPPED must not be admissible")
	}
	if k2.Admit(OutcomeCanceled, ChannelCancelAPI, 0).Admissible {
		t.Fatal("a cancel ack without Q_S must not be terminal")
	}
	if !k2.Admit(OutcomeApplied, ChannelQuery, 0).Admissible {
		t.Fatal("K2 APPLIED must be admissible")
	}
	stop := k2.Extend("k3", CapStop)
	if a := stop.Admit(OutcomeStopped, ChannelStop, 0); !a.Admissible || a.As != OutcomeStopped {
		t.Fatalf("Q_S stop must be admissible: %+v", a)
	}
	if stop.Admit(OutcomeStopped, ChannelFence, 0).Admissible {
		t.Fatal("fence evidence requires F")
	}
	fence := k2.Extend("k3f", CapFence)
	if !fence.Admit(OutcomeStopped, ChannelFence, 0).Admissible {
		t.Fatal("F fence evidence must be admissible")
	}
}

func TestNotFoundTerminalOnlyAfterOwnerExpiry(t *testing.T) {
	onlyT := EvidenceContract{SchemaVersion: 1, ID: "t", Source: SourceHypothesis, Capabilities: []EvidenceCapability{CapExpiry}, ExpiryMS: 5000, ClockSkewMS: 200}
	if onlyT.Admit(OutcomeExpired, ChannelExpiryClock, 1<<40).Admissible {
		t.Fatal("expiry alone never reveals the outcome")
	}
	if !onlyT.NoFutureEffect(5200) || onlyT.NoFutureEffect(5100) {
		t.Fatal("expiry bounds late effects after tau+epsilon")
	}
	if onlyT.BackwardEvidence() || onlyT.Level() != K0 {
		t.Fatalf("T alone is not backward evidence: level %s", onlyT.Level())
	}
	tq := EvidenceContract{SchemaVersion: 1, ID: "tq", Source: SourceHypothesis, Capabilities: []EvidenceCapability{CapDedup, CapQueryApplied, CapExpiry}, DedupHorizonMS: UnboundedHorizonMS, ExpiryMS: 5000, ClockSkewMS: 200,
		RecordRetentionMS: UnboundedRetentionMS} // release 084: ER needs r = infinity
	if tq.Admit(OutcomeNotFound, ChannelQuery, 5100).Admissible {
		t.Fatal("must wait tau+epsilon")
	}
	if a := tq.Admit(OutcomeNotFound, ChannelQuery, 5200); !a.Admissible || a.As != OutcomeStopped {
		t.Fatalf("NOT_FOUND after owner expiry is STOPPED: %+v", a)
	}
	if tq.Level() != K3 {
		t.Fatalf("T+Q_A+D_h is K3, got %s", tq.Level())
	}
	finite := tq
	finite.DedupHorizonMS = 1000
	finite.RecordRetentionMS = 1000 // release 084: the record, not the suppression horizon, must be kept
	if finite.Admit(OutcomeNotFound, ChannelQuery, 1<<40).Admissible {
		t.Fatal("a forgotten (finite-retention) record could make NOT_FOUND lie")
	}
	broker := EvidenceContract{SchemaVersion: 1, ID: "b", Source: SourceHypothesis}
	if broker.Admit(OutcomeExpired, ChannelExpiryClock, 1<<40).Admissible || broker.NoFutureEffect(1<<40) {
		t.Fatal("expiry without owner-enforced T is never evidence")
	}
}

func TestStoppedReplyAdmissibleWithDurableDedup(t *testing.T) {
	k2 := EvidenceContract{SchemaVersion: 1, ID: "k2", Source: SourceHypothesis, Capabilities: []EvidenceCapability{CapDedup, CapQueryApplied}, DedupHorizonMS: UnboundedHorizonMS}
	if !k2.Admit(OutcomeStopped, ChannelDispatchReply, 0).Admissible {
		t.Fatal("owner-written STOPPED with unbounded dedup is terminal")
	}
	k1 := EvidenceContract{SchemaVersion: 1, ID: "k1", Source: SourceHypothesis, Capabilities: []EvidenceCapability{CapDedup}, DedupHorizonMS: 1000}
	if k1.Admit(OutcomeStopped, ChannelDispatchReply, 0).Admissible {
		t.Fatal("finite dedup: a later duplicate may still apply")
	}
}

// Theorem T4: forward retry deadline = first send + min(h, validity) - delay.
func TestForwardRetryDeadline(t *testing.T) {
	c := EvidenceContract{SchemaVersion: 1, ID: "h", Source: SourceHypothesis, Capabilities: []EvidenceCapability{CapDedup}, DedupHorizonMS: 45000}
	tm := CommandTiming{FirstSendMS: 1000, MaxNetworkDelayMS: 500}
	if got := c.ForwardRetryDeadline(tm); got != 1000+45000-500 {
		t.Fatalf("deadline %d", got)
	}
	tm.IntentValidUntilMS = 20000
	if got := c.ForwardRetryDeadline(tm); got != 19500 {
		t.Fatalf("validity must bound deadline: %d", got)
	}
	u := EvidenceContract{SchemaVersion: 1, ID: "u", Source: SourceHypothesis, Capabilities: []EvidenceCapability{CapDedup}, DedupHorizonMS: UnboundedHorizonMS}
	if got := u.ForwardRetryDeadline(CommandTiming{FirstSendMS: 5}); got != -1 {
		t.Fatalf("unbounded %d", got)
	}
	none := EvidenceContract{SchemaVersion: 1, ID: "n", Source: SourceHypothesis}
	if got := none.ForwardRetryDeadline(CommandTiming{FirstSendMS: 5}); got >= 5 {
		t.Fatalf("no dedup must forbid retry: %d", got)
	}
	tm2 := CommandTiming{FirstSendMS: 0, NowMS: 50000, MaxNetworkDelayMS: 500}
	if c.Compile(tm2).Allows(ActionForwardRetry) {
		t.Fatal("retry outside horizon must not be allowed")
	}
	tm2.NowMS = 1000
	if !c.Compile(tm2).Allows(ActionForwardRetry) {
		t.Fatal("retry inside horizon must be allowed")
	}
}

func TestForwardRetryWithUnboundedDelay(t *testing.T) {
	finite := EvidenceContract{SchemaVersion: 1, ID: "f", Source: SourceHypothesis, Capabilities: []EvidenceCapability{CapDedup}, DedupHorizonMS: 45000}
	if d := finite.ForwardRetryDeadline(CommandTiming{FirstSendMS: 100, MaxNetworkDelayMS: -1}); d >= 100 {
		t.Fatalf("finite horizon with unbounded delay must forbid retry: %d", d)
	}
	unb := EvidenceContract{SchemaVersion: 1, ID: "u", Source: SourceHypothesis, Capabilities: []EvidenceCapability{CapDedup}, DedupHorizonMS: UnboundedHorizonMS}
	if d := unb.ForwardRetryDeadline(CommandTiming{FirstSendMS: 100, MaxNetworkDelayMS: -1}); d != -1 {
		t.Fatalf("unbounded horizon without validity stays unbounded: %d", d)
	}
	if d := unb.ForwardRetryDeadline(CommandTiming{FirstSendMS: 100, MaxNetworkDelayMS: -1, IntentValidUntilMS: 5000}); d != 5000 {
		t.Fatalf("validity bounds the retry window: %d", d)
	}
}

func TestCompileEscalatesWithoutBackwardEvidence(t *testing.T) {
	k2 := EvidenceContract{SchemaVersion: 1, ID: "k2", Source: SourceHypothesis, Capabilities: []EvidenceCapability{CapDedup, CapQueryApplied}, DedupHorizonMS: UnboundedHorizonMS}
	s := k2.Compile(CommandTiming{NowMS: 10})
	if !s.Allows(ActionEscalate) || s.Allows(ActionStop) || s.Allows(ActionFence) {
		t.Fatalf("K2 actions %+v", s.Allowed)
	}
	s = k2.Compile(CommandTiming{NowMS: 10, TerminalKnown: true, TerminalStopped: true, IntentValidUntilMS: 100})
	if !s.Allows(ActionReissueNewID) {
		t.Fatal("terminal STOPPED with valid intent allows re-issue under a new identity")
	}
	s = k2.Compile(CommandTiming{NowMS: 200, TerminalKnown: true, TerminalStopped: true, IntentValidUntilMS: 100})
	if s.Allows(ActionReissueNewID) {
		t.Fatal("expired intent must not be re-issued")
	}
}

func TestRuntimeContractDropsUnbackedCapabilities(t *testing.T) {
	declared, err := EvidenceContractForProfile("E2")
	if err != nil {
		t.Fatal(err)
	}
	// The 060 E2 profile has fence premises but not the per-identity stop.
	rc, err := RuntimeContract("e2-runtime", declared, ProfileE2("e2-test"))
	if err != nil {
		t.Fatal(err)
	}
	if rc.Has(CapStop) {
		t.Fatal("Q_S must be dropped without PER_IDENTITY_STOP premise")
	}
	if !rc.Has(CapFence) || !rc.Has(CapDedup) || !rc.Has(CapQueryApplied) {
		t.Fatalf("E2 runtime caps %v", rc.Capabilities)
	}
	p := ProfileE2("e2-061")
	p.Premises = append(p.Premises, PremisePerIdentityStop)
	rc, err = RuntimeContract("e2-runtime", declared, p)
	if err != nil {
		t.Fatal(err)
	}
	if !rc.Has(CapStop) {
		t.Fatal("Q_S expected with PER_IDENTITY_STOP")
	}
	e3, _ := EvidenceContractForProfile("E3")
	rc3, err := RuntimeContract("e3-runtime", e3, ProfileE3("e3"))
	if err != nil {
		t.Fatal(err)
	}
	if rc3.Level() != K4 || rc3.BackwardEvidence() {
		t.Fatalf("E3 should be K4 without backward evidence: %v", rc3.Capabilities)
	}
	e0, _ := EvidenceContractForProfile("E0")
	rc0, err := RuntimeContract("e0", e0, ProfileE0("e0"))
	if err != nil || rc0.Level() != K0 {
		t.Fatalf("E0 %v %v", rc0, err)
	}
}

func TestIncludesAndExtend(t *testing.T) {
	k1 := EvidenceContract{SchemaVersion: 1, ID: "k1", Source: SourceHypothesis, Capabilities: []EvidenceCapability{CapDedup}, DedupHorizonMS: 1000}
	k1long := EvidenceContract{SchemaVersion: 1, ID: "k1l", Source: SourceHypothesis, Capabilities: []EvidenceCapability{CapDedup}, DedupHorizonMS: UnboundedHorizonMS}
	if !k1long.Includes(k1) || k1.Includes(k1long) {
		t.Fatal("horizon order")
	}
	f := k1.Extend("k1f", CapFence)
	if !f.Includes(k1) || k1.Includes(f) || f.Level() != K3 {
		t.Fatal("extend")
	}
}

func TestCatalogLoads(t *testing.T) {
	cs, err := LoadEvidenceContracts(filepath.Join(repoRoot(t), "experiments", "contracts"))
	if err != nil {
		t.Fatal(err)
	}
	if len(cs) < 20 {
		t.Fatalf("catalog too small: %d", len(cs))
	}
	for _, c := range cs {
		if c.Source == SourceRuntimeVerified || c.Source == SourceMeasured {
			t.Fatalf("catalog entry %s must not claim %s before the measurement study", c.ID, c.Source)
		}
	}
}

// Review RV03: MEASURED capabilities need per-capability provenance, never an
// unbounded measured horizon, and X only with crash-cut evidence.
func TestMeasuredContractNeedsProvenance(t *testing.T) {
	base := EvidenceContract{SchemaVersion: 1, ID: "m", System: "s", Source: SourceMeasured,
		Capabilities: []EvidenceCapability{CapDedup, CapQueryApplied}, DedupHorizonMS: 30000,
		Provenance: map[string]string{"D_h": "P4,P6,P8", "Q_A": "P2"}}
	if err := base.Validate(); err != nil {
		t.Fatalf("valid measured contract rejected: %v", err)
	}
	noProv := base
	noProv.Provenance = map[string]string{"D_h": "P4"}
	if noProv.Validate() == nil {
		t.Fatal("Q_A without provenance must be rejected")
	}
	unb := base
	unb.DedupHorizonMS = UnboundedHorizonMS
	if unb.Validate() == nil {
		t.Fatal("measured unbounded horizon must be rejected")
	}
	declX := base
	declX.Capabilities = []EvidenceCapability{CapDedup, CapQueryApplied, CapAtomic}
	declX.Provenance = map[string]string{"D_h": "P4", "Q_A": "P2", "X": "DECLARED_NOT_CONTRADICTED"}
	if declX.Validate() == nil {
		t.Fatal("declared X must not be MEASURED")
	}
	declX.Provenance["X"] = "P4"
	if declX.Validate() == nil {
		t.Fatal("X without crash-cut evidence must be rejected")
	}
	both := base
	both.DeclaredUnverified = []string{"Q_A"}
	if both.Validate() == nil {
		t.Fatal("a capability cannot also be declared_unverified")
	}
}

// Review RV10: Includes compares expiry, skew and fault model, not only the set.
func TestIncludesComparesParameters(t *testing.T) {
	a := EvidenceContract{SchemaVersion: 1, ID: "a", Source: SourceHypothesis, Capabilities: []EvidenceCapability{CapExpiry}, ExpiryMS: 1000, ClockSkewMS: 10}
	b := a
	b.ID, b.ExpiryMS = "b", 5000
	if !a.Includes(b) || b.Includes(a) {
		t.Fatal("a shorter owner expiry includes a longer one, not vice versa")
	}
	c := a
	c.ID, c.ClockSkewMS = "c", 100
	if c.Includes(a) {
		t.Fatal("a larger skew bound must not include a smaller one")
	}
	d := a
	d.ID, d.FaultModel = "d", "crash-stop;no-reboot"
	e := a
	e.ID, e.FaultModel = "e", "crash-recovery"
	if d.Includes(e) || e.Includes(d) {
		t.Fatal("different fault models are incomparable")
	}
	if !d.Includes(a) {
		t.Fatal("an unspecified fault model on the right is included")
	}
}

// Release 072 (paper, Definition "capabilities"): Q_S and F are backed only
// when the owner recognizes crash orphans (durable claim before effect, or
// atomic effect+receipt).
func TestStopAndFenceRequireOrphanRecognition(t *testing.T) {
	base := []Premise{PremisePerIdentityStop, PremiseOwnerBlocksLateAttempt, PremiseStopConfirmedBeforeEffect,
		PremiseDurableCutoff, PremiseDrainInflightEffects, PremiseSerializedStateRead}
	backed, _ := Backed([]EvidenceCapability{CapStop, CapFence}, AdapterProfile{Premises: base})
	if len(backed) != 0 {
		t.Fatalf("without orphan recognition Q_S/F must be unbacked, got %v", backed)
	}
	for _, extra := range []Premise{PremiseDurableClaimBeforeEffect, PremiseAtomicEffectReceipt} {
		backed, _ = Backed([]EvidenceCapability{CapStop, CapFence}, AdapterProfile{Premises: append(append([]Premise(nil), base...), extra)})
		if len(backed) != 2 {
			t.Fatalf("with %s Q_S and F must be backed, got %v", extra, backed)
		}
	}
}

// Release 072: a re-issue must be sent by V - delta when the delay is bounded.
func TestReissueRespectsValidityMargin(t *testing.T) {
	c := EvidenceContract{SchemaVersion: 1, ID: "f", Source: SourceRuntimeVerified, Capabilities: []EvidenceCapability{CapFence}}
	tm := CommandTiming{FirstSendMS: 0, IntentValidUntilMS: 10000, MaxNetworkDelayMS: 1000, TerminalKnown: true, TerminalStopped: true}
	tm.NowMS = 9000
	if !c.Compile(tm).Allows(ActionReissueNewID) {
		t.Fatal("re-issue at V - delta must be allowed")
	}
	tm.NowMS = 9500
	if c.Compile(tm).Allows(ActionReissueNewID) {
		t.Fatal("re-issue after V - delta could land after V")
	}
	tm.MaxNetworkDelayMS = -1 // unbounded: no margin can help; the bound is V
	if !c.Compile(tm).Allows(ActionReissueNewID) {
		t.Fatal("unbounded delay: re-issue until V")
	}
}

// Release 072 (paper, Lemma "monotonicity"): an observation admissible under
// a contract is admissible, with the same status, under every contract that
// includes it.
func TestAdmitIsMonotoneUnderIncludes(t *testing.T) {
	caps := allCapabilities
	outcomes := []Outcome{OutcomeApplied, OutcomeStopped, OutcomeUncertain, OutcomePending, OutcomeNotFound, OutcomeTimeout, OutcomeExpired, OutcomeCanceled}
	channels := []EvidenceChannel{ChannelDispatchReply, ChannelQuery, ChannelStop, ChannelFence, ChannelExpiryClock, ChannelCancelAPI}
	mk := func(mask int, h, e, eps int64) EvidenceContract {
		c := EvidenceContract{SchemaVersion: 1, ID: "x", Source: SourceRuntimeVerified}
		for i, k := range caps {
			if mask&(1<<i) != 0 {
				c.Capabilities = append(c.Capabilities, k)
			}
		}
		if c.Has(CapDedup) {
			c.DedupHorizonMS = h
		}
		if c.Has(CapExpiry) {
			c.ExpiryMS, c.ClockSkewMS = e, eps
		}
		return c
	}
	horizons := []int64{1000, 60000, UnboundedHorizonMS}
	n := 0
	for m := 0; m < 1<<len(caps); m++ {
		for m2 := 0; m2 < 1<<len(caps); m2++ {
			if m2&m != m {
				continue
			}
			for _, h := range horizons {
				for _, h2 := range horizons {
					c, c2 := mk(m, h, 30000, 500), mk(m2, h2, 20000, 200)
					if !c2.Includes(c) {
						continue
					}
					for _, o := range outcomes {
						for _, ch := range channels {
							for _, age := range []int64{0, 20000, 31000, 90000} {
								a, a2 := c.Admit(o, ch, age), c2.Admit(o, ch, age)
								if a.Admissible && (!a2.Admissible || a2.As != a.As) {
									t.Fatalf("not monotone: %v ⊑ %v, %s on %s age %d: %+v vs %+v", c.Capabilities, c2.Capabilities, o, ch, age, a, a2)
								}
								n++
							}
						}
					}
				}
			}
		}
	}
	if n < 100000 {
		t.Fatalf("too few cases: %d", n)
	}
}

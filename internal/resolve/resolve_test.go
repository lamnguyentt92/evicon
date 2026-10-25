package resolve

import (
	"math"
	"math/rand"
	"testing"

	"evicon/internal/contract"
)

func k(caps ...contract.EvidenceCapability) contract.EvidenceContract {
	c := contract.EvidenceContract{SchemaVersion: 1, ID: "t", Source: contract.SourceHypothesis, Capabilities: caps}
	if c.Has(contract.CapDedup) {
		c.DedupHorizonMS = contract.UnboundedHorizonMS
	}
	if c.Has(contract.CapExpiry) {
		c.ExpiryMS = 1000
	}
	return c
}

// T5: the ski-rental threshold C/r is 2-competitive for every arrival time R.
func TestSkiRentalIsTwoCompetitive(t *testing.T) {
	rnd := rand.New(rand.NewSource(7))
	for i := 0; i < 20000; i++ {
		r := 0.01 + rnd.Float64()*50
		c := 0.01 + rnd.Float64()*100
		thr := Threshold(Policy{Mode: ModeSkiRental}, r, c)
		var R int64
		switch rnd.Intn(3) {
		case 0:
			R = NeverMS
		case 1:
			R = thr + int64(rnd.Intn(5)) - 2
			if R < 0 {
				R = 0
			}
		default:
			R = int64(rnd.ExpFloat64() * 5000)
		}
		alg := Cost(thr, R, r, c)
		opt := OfflineOptimum(R, r, c)
		// +1 ms rounding slack from ceil(threshold).
		slack := r / 1000.0
		if alg > 2*opt+slack+1e-9 {
			t.Fatalf("ratio violated: r=%.3f c=%.3f thr=%d R=%d alg=%.4f opt=%.4f", r, c, thr, R, alg, opt)
		}
	}
}

func TestFixedThresholdIsNotCompetitive(t *testing.T) {
	// A fixed timeout far above C/r pays r*T + C when evidence never arrives.
	r, c := 10.0, 1.0
	thr := Threshold(Policy{Mode: ModeFixed, FixedMS: 60000}, r, c)
	if Cost(thr, NeverMS, r, c)/OfflineOptimum(NeverMS, r, c) < 100 {
		t.Fatal("expected a large ratio for a fixed long timeout")
	}
}

func TestDistributionThresholdNoWorseThanSkiRentalInExpectation(t *testing.T) {
	rnd := rand.New(rand.NewSource(11))
	for trial := 0; trial < 200; trial++ {
		samples := make([]int64, 200)
		for i := range samples {
			if rnd.Float64() < 0.2 {
				samples[i] = NeverMS
			} else {
				samples[i] = int64(rnd.ExpFloat64() * float64(500+rnd.Intn(5000)))
			}
		}
		r := 0.1 + rnd.Float64()*5
		c := 0.1 + rnd.Float64()*20
		td := Threshold(Policy{Mode: ModeDistribution, SamplesMS: samples}, r, c)
		ts := Threshold(Policy{Mode: ModeSkiRental}, r, c)
		if ExpectedCost(samples, td, r, c) > ExpectedCost(samples, ts, r, c)+1e-9 {
			t.Fatalf("distribution threshold worse than ski-rental on its own samples")
		}
	}
}

func TestDecideNeverActsWithoutSufficientSet(t *testing.T) {
	in := Input{NowMS: 100000, Contract: k(contract.CapDedup, contract.CapQueryApplied, contract.CapStop, contract.CapFence), Blocked: true,
		Ambiguous: []Ambiguous{{ID: "a", Sequence: 1, Magnitude: 5}}}
	d, err := Decide(in, Costs{HoldPerUnitPerSec: 1, StopCost: 1, FenceCost: 1}, Policy{Mode: ModeImmediate})
	if err != nil || d.Action != contract.ActionWait || d.Reason != "NO_SUFFICIENT_SET" {
		t.Fatalf("%+v %v", d, err)
	}
}

func TestDecidePrefersStopThenFenceThenEscalate(t *testing.T) {
	amb := []Ambiguous{{ID: "a", Sequence: 3, Magnitude: 4, AmbiguousSinceMS: 0, InCertificate: true}, {ID: "b", Sequence: 5, Magnitude: 2, AmbiguousSinceMS: 100, InCertificate: true}}
	costs := Costs{HoldPerUnitPerSec: 1, StopCost: 0.5, FenceCost: 3}
	pol := Policy{Mode: ModeSkiRental}
	in := Input{NowMS: 10000, Ambiguous: amb, Blocked: true}

	in.Contract = k(contract.CapDedup, contract.CapQueryApplied, contract.CapStop, contract.CapFence)
	d, _ := Decide(in, costs, pol)
	if d.Action != contract.ActionStop || len(d.Targets) != 2 {
		t.Fatalf("expected STOP: %+v", d)
	}
	in.Contract = k(contract.CapDedup, contract.CapQueryApplied, contract.CapFence)
	d, _ = Decide(in, costs, pol)
	if d.Action != contract.ActionFence || d.FenceCutoff != 5 {
		t.Fatalf("expected FENCE cutoff 5: %+v", d)
	}
	in.Contract = k(contract.CapDedup, contract.CapQueryApplied)
	d, _ = Decide(in, costs, pol)
	if d.Action != contract.ActionEscalate {
		t.Fatalf("expected ESCALATE without backward evidence: %+v", d)
	}
	in.Contract = k(contract.CapExpiry)
	d, _ = Decide(in, costs, pol)
	if d.Action != contract.ActionEscalate {
		t.Fatalf("expiry alone reveals no outcome: %+v", d)
	}
	// T + truthful query: wait for tau+eps after send, then query (NOT_FOUND -> STOPPED).
	in.Contract = k(contract.CapDedup, contract.CapQueryApplied, contract.CapExpiry) // expiry 1000 ms
	in.QueryBudget = 2
	in.NowMS = 500
	d, _ = Decide(in, costs, pol)
	if d.Action != contract.ActionWait || d.Reason != "WAIT_FOR_OWNER_EXPIRY" {
		t.Fatalf("expected expiry wait: %+v", d)
	}
	in.NowMS = 5000
	d, _ = Decide(in, costs, pol)
	if d.Action != contract.ActionQuery || d.Reason != "QUERY_AFTER_OWNER_EXPIRY" {
		t.Fatalf("expected query after expiry: %+v", d)
	}
}

func TestDecideRespectsThreshold(t *testing.T) {
	amb := []Ambiguous{{ID: "a", Sequence: 1, Magnitude: 2, AmbiguousSinceMS: 1000, InCertificate: true}}
	costs := Costs{HoldPerUnitPerSec: 1, FenceCost: 4} // r=2/s, C=4 -> 2000 ms
	in := Input{NowMS: 2500, Ambiguous: amb, Blocked: true, Contract: k(contract.CapFence)}
	d, _ := Decide(in, costs, Policy{Mode: ModeSkiRental})
	if d.Action != contract.ActionWait || d.ThresholdMS != 2000 || d.AgeMS != 1500 {
		t.Fatalf("%+v", d)
	}
	in.NowMS = 3000
	d, _ = Decide(in, costs, Policy{Mode: ModeSkiRental})
	if d.Action != contract.ActionFence {
		t.Fatalf("%+v", d)
	}
	in.FenceInProgress = true
	d, _ = Decide(in, costs, Policy{Mode: ModeSkiRental})
	if d.Action != contract.ActionWait {
		t.Fatalf("fence in progress must wait: %+v", d)
	}
}

func TestQueryFirst(t *testing.T) {
	amb := []Ambiguous{{ID: "a", Sequence: 1, Magnitude: 2, InCertificate: true}, {ID: "b", Sequence: 2, Magnitude: 2, InCertificate: true, Queried: true}}
	in := Input{NowMS: 10, Ambiguous: amb, Blocked: true, QueryBudget: 2, Contract: k(contract.CapDedup, contract.CapQueryApplied, contract.CapStop)}
	d, _ := Decide(in, Costs{HoldPerUnitPerSec: 1, StopCost: 1}, Policy{Mode: ModeImmediate, QueryFirst: true})
	if d.Action != contract.ActionQuery || len(d.Targets) != 1 || d.Targets[0] != "a" {
		t.Fatalf("%+v", d)
	}
	in.QueryBudget = 0
	d, _ = Decide(in, Costs{HoldPerUnitPerSec: 1, StopCost: 1}, Policy{Mode: ModeImmediate, QueryFirst: true})
	if d.Action != contract.ActionStop {
		t.Fatalf("%+v", d)
	}
}

func TestRetryDecisionHorizon(t *testing.T) {
	c := contract.EvidenceContract{SchemaVersion: 1, ID: "h", Source: contract.SourceHypothesis, Capabilities: []contract.EvidenceCapability{contract.CapDedup, contract.CapQueryApplied, contract.CapFence}, DedupHorizonMS: 45000}
	tm := contract.CommandTiming{FirstSendMS: 0, NowMS: 1000, MaxNetworkDelayMS: 1000}
	if d := RetryDecision(c, tm, 0, 500); d.Action != contract.ActionForwardRetry {
		t.Fatalf("%+v", d)
	}
	if d := RetryDecision(c, tm, 800, 500); d.Action != contract.ActionWait {
		t.Fatalf("%+v", d)
	}
	tm.NowMS = 50000
	if d := RetryDecision(c, tm, 0, 500); d.Action != contract.ActionFence {
		t.Fatalf("outside horizon must hand over to fence: %+v", d)
	}
	none := contract.EvidenceContract{SchemaVersion: 1, ID: "n", Source: contract.SourceHypothesis}
	if d := RetryDecision(none, tm, 0, 500); d.Action != contract.ActionEscalate {
		t.Fatalf("%+v", d)
	}
}

func TestThresholdEdgeCases(t *testing.T) {
	if Threshold(Policy{Mode: ModeSkiRental}, 0, 5) != NeverMS {
		t.Fatal("zero holding cost never resolves")
	}
	if Threshold(Policy{Mode: ModeNever}, 10, 1) != NeverMS || Threshold(Policy{Mode: ModeImmediate}, 10, 1) != 0 {
		t.Fatal("modes")
	}
	if !math.IsInf(Cost(NeverMS, NeverMS, 1, 1), 1) {
		t.Fatal("never resolving with never arriving evidence costs infinity")
	}
	if (Policy{Mode: ModeDistribution}).Validate() == nil || (Policy{Mode: "X"}).Validate() == nil {
		t.Fatal("validate")
	}
}

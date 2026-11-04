package resolve

import (
	"fmt"
	"math/rand"
	"reflect"
	"testing"

	"evicon/internal/contract"
)

// The ThingsBoard contract of the 083/084 TB runs ({Q_A, T}, expiry 60 s, record retention not declared: r = 0) and
// the strict contract {Q_A} (review M3b; the same shapes as internal/outbox's TestExpiresAfterDispatchIdentical085).
func tbContracts085() (withT, strict contract.EvidenceContract) {
	withT = contract.EvidenceContract{SchemaVersion: 1, ID: "meas-thingsboard-ce-server-side-rpc-actuate-persistent-twoway", System: "ThingsBoard CE",
		Source: contract.SourceMeasured, Capabilities: []contract.EvidenceCapability{contract.CapQueryApplied, contract.CapExpiry}, ExpiryMS: 60000,
		Provenance: map[string]string{"Q_A": "P2,P2N:receipts_retained=True@32 x3 repetitions", "T": "P9T x3 repetitions",
			"T_expiry": "P9T:expiry=60.0s;request_delay_ms=61500"},
		FaultModel: "network loss/delay, client abandon; owner up (no owner crash or reboot)"}
	strict = withT
	strict.ID += "-strict"
	strict.Capabilities = []contract.EvidenceCapability{contract.CapQueryApplied}
	strict.ExpiryMS = 0
	strict.Provenance = map[string]string{"Q_A": withT.Provenance["Q_A"]}
	return withT, strict
}

func randTiming(rng *rand.Rand) contract.CommandTiming {
	first := rng.Int63n(1_000_000)
	t := contract.CommandTiming{FirstSendMS: first, NowMS: first + rng.Int63n(400_000) - 20_000, MaxNetworkDelayMS: []int64{-1, 0, 200, 6000, 61_000}[rng.Intn(5)]}
	if rng.Intn(3) > 0 {
		t.IntentValidUntilMS = first + rng.Int63n(200_000)
	}
	t.TerminalKnown = rng.Intn(3) == 0
	t.TerminalStopped = t.TerminalKnown && rng.Intn(2) == 0
	return t
}

// TestTTLChangesNoDecision085 (review M3b, PREREG_extra_085.md section 9): T with r = 0 changes no decision of
// the ThingsBoard contract. Over random command timings (around and far beyond the 60 s expiry, every delay bound,
// with and without validity and terminal evidence): the compiled action sets are equal (allowed actions, reasons, the
// forward-retry deadline), so are the dispatch-side retry decisions and the resolver's decisions for random blocked
// inputs, and the admission of every outcome on every evidence channel at every age is the same decision (admissible,
// as what); only the reason text of an EXPIRED observation on the expiry clock names T.
func TestTTLChangesNoDecision085(t *testing.T) {
	withT, strict := tbContracts085()
	for _, c := range []contract.EvidenceContract{withT, strict} {
		if err := c.Validate(); err != nil {
			t.Fatal(err)
		}
	}
	if withT.Level() != strict.Level() || withT.BackwardEvidence() || withT.ExpiryRevealsStopped() {
		t.Fatalf("levels %s / %s; backward %v; ER %v", withT.Level(), strict.Level(), withT.BackwardEvidence(), withT.ExpiryRevealsStopped())
	}
	rng := rand.New(rand.NewSource(85))
	outcomes := []contract.Outcome{contract.OutcomeApplied, contract.OutcomeStopped, contract.OutcomeUncertain, contract.OutcomePending,
		contract.OutcomeNotFound, contract.OutcomeTimeout, contract.OutcomeExpired, contract.OutcomeCanceled}
	channels := []contract.EvidenceChannel{contract.ChannelDispatchReply, contract.ChannelQuery, contract.ChannelStop, contract.ChannelFence,
		contract.ChannelExpiryClock, contract.ChannelCancelAPI}
	pols := []Policy{{Mode: ModeSkiRental, QueryFirst: true}, {Mode: ModeImmediate, QueryFirst: true}, {Mode: ModeFixed, FixedMS: 5000},
		{Mode: ModeNever}, {Mode: ModeSkiRental}}
	const n = 20000
	for i := 0; i < n; i++ {
		tm := randTiming(rng)
		a, b := withT.Compile(tm), strict.Compile(tm)
		if !reflect.DeepEqual(a, b) {
			t.Fatalf("timing %+v: Compile differs:\n%+v\n%+v", tm, a, b)
		}
		last := tm.FirstSendMS + rng.Int63n(120_000)
		iv := []int64{0, 500, 2000, 10_000}[rng.Intn(4)]
		if ra, rb := RetryDecision(withT, tm, last, iv), RetryDecision(strict, tm, last, iv); !reflect.DeepEqual(ra, rb) {
			t.Fatalf("timing %+v: RetryDecision differs: %+v / %+v", tm, ra, rb)
		}
		age := tm.NowMS - tm.FirstSendMS
		for _, o := range outcomes {
			for _, ch := range channels {
				x, y := withT.Admit(o, ch, age), strict.Admit(o, ch, age)
				if x.Admissible != y.Admissible || x.As != y.As {
					t.Fatalf("admit %s on %s at age %d ms: %+v / %+v", o, ch, age, x, y)
				}
				if x.Reason != y.Reason && !(o == contract.OutcomeExpired && ch == contract.ChannelExpiryClock) {
					t.Fatalf("admit %s on %s: reasons %q / %q", o, ch, x.Reason, y.Reason)
				}
			}
		}
		amb := make([]Ambiguous, 1+rng.Intn(5))
		for j := range amb {
			fs := tm.NowMS - rng.Int63n(200_000)
			amb[j] = Ambiguous{ID: fmt.Sprintf("c%d", j), Sequence: uint64(j + 1), Magnitude: 1 + rng.Int63n(5), AmbiguousSinceMS: fs + rng.Int63n(3000),
				FirstSendMS: fs, LastAttemptMS: fs + rng.Int63n(5000), Queried: rng.Intn(2) == 0, StopAttempted: rng.Intn(4) == 0,
				InCertificate: rng.Intn(4) > 0}
		}
		in := Input{NowMS: tm.NowMS, Ambiguous: amb, Blocked: rng.Intn(5) > 0, QueryBudget: rng.Intn(4), MaxNetworkDelayMS: tm.MaxNetworkDelayMS,
			FenceInProgress: rng.Intn(6) == 0}
		costs := Costs{HoldPerUnitPerSec: 1, QueryCost: 0.01, StopCost: 1, FenceCost: 5}
		pol := pols[rng.Intn(len(pols))]
		inA, inB := in, in
		inA.Contract, inB.Contract = withT, strict
		da, ea := Decide(inA, costs, pol)
		db, eb := Decide(inB, costs, pol)
		if (ea == nil) != (eb == nil) || !reflect.DeepEqual(da, db) {
			t.Fatalf("input %+v policy %+v: Decide differs: %+v %v / %+v %v", in, pol, da, ea, db, eb)
		}
	}
}

package probe

import (
	"fmt"
	"math/rand"
	"sort"
	"testing"
	"time"

	"evicon/internal/domain"
	"evicon/internal/journal"
	"evicon/internal/protocol"
)

// randomAddHistory builds a random ADD-only dosing history: an anchor, terminal commands (APPLIED or STOPPED)
// and unresolved ones, values 1..maxV, a per-device bound, and a candidate.
func randomAddHistory(rng *rand.Rand, n int, maxV int64) (journal.Domain, []journal.CommandRecord, protocol.CommandRequest) {
	bound := int64(10 + rng.Intn(40))
	d := journal.Domain{ID: "g", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{bound},
		Anchor: []int64{int64(rng.Intn(int(bound) / 2))}}
	var cs []journal.CommandRecord
	for i := 0; i < n; i++ {
		v := 1 + rng.Int63n(maxV)
		st := "PENDING"
		switch rng.Intn(5) {
		case 0:
			st = "APPLIED"
		case 1:
			st = "STOPPED"
		}
		cs = append(cs, journal.CommandRecord{DomainID: "g", CommandID: fmt.Sprintf("c%02d", i), Generation: 1, Epoch: 1, Sequence: uint64(i + 1),
			Operation: domain.OpAdd, Value: []int64{v}, IntentHash: fmt.Sprintf("h%02d", i), Reservation: []int64{v}, Status: st, Decision: "ADMITTED"})
	}
	cand := protocol.CommandRequest{SchemaVersion: 1, DomainID: "g", Generation: 1, CommandID: "cand", Operation: domain.OpAdd,
		Value: []int64{1 + rng.Int63n(maxV)}, Epoch: 1, Sequence: uint64(n + 1), IntentHash: "hcand"}
	return d, cs, cand
}

// bruteForceMinCertificate searches every subset of the unresolved commands assumed STOPPED (APPLIED
// assumptions cannot help an ADD domain with an upper bound: Theorem 3) and returns the smallest size of a
// certificate, or -1 when none exists.
func bruteForceMinCertificate(t *testing.T, d journal.Domain, cs []journal.CommandRecord, req protocol.CommandRequest) int {
	t.Helper()
	u := Unresolved(cs, nil)
	best := -1
	for mask := 1; mask < 1<<len(u); mask++ {
		var as []TerminalAssumption
		for i := range u {
			if mask&(1<<i) != 0 {
				as = append(as, TerminalAssumption{CommandID: u[i].CommandID, Outcome: "STOPPED"})
			}
		}
		if best >= 0 && len(as) >= best {
			continue
		}
		ok, err := inspectAssumptions(d, cs, req, as, 256)
		if err != nil {
			t.Fatal(err)
		}
		if ok {
			best = len(as)
		}
	}
	return best
}

// T5 property (release 083): on random additive domains the greedy certificate exists iff an unbounded
// search finds one, has the minimum size, is minimal by inclusion and verifies; where the bounded
// enumeration (gamma = 3) finds a certificate, the minimum sizes agree.
func TestGreedyCertificateMatchesUnboundedSearch(t *testing.T) {
	rng := rand.New(rand.NewSource(20261030))
	found, none, beyond := 0, 0, 0
	for trial := 0; trial < 600; trial++ {
		d, cs, req := randomAddHistory(rng, 3+rng.Intn(9), int64(2+rng.Intn(9)))
		safe, err := inspectAssumptions(d, cs, req, nil, 256)
		if err != nil {
			t.Fatal(err)
		}
		certs, err := GreedyContinuationCertificate(d, cs, req, 256)
		if err != nil {
			t.Fatalf("trial %d: %v", trial, err)
		}
		if safe {
			if len(certs) != 0 {
				t.Fatalf("trial %d: a safe candidate needs no certificate, got %+v", trial, certs)
			}
			continue
		}
		want := bruteForceMinCertificate(t, d, cs, req)
		if want < 0 {
			none++
			if len(certs) != 0 {
				t.Fatalf("trial %d: no certificate exists, greedy returned %+v", trial, certs)
			}
			continue
		}
		if len(certs) != 1 {
			t.Fatalf("trial %d: a certificate of size %d exists, greedy returned %d", trial, want, len(certs))
		}
		c := certs[0]
		found++
		if len(c.Required) != want {
			t.Fatalf("trial %d: greedy size %d, minimum %d", trial, len(c.Required), want)
		}
		for _, x := range c.Required {
			if x.Outcome != "STOPPED" {
				t.Fatalf("trial %d: non-STOPPED member %+v", trial, x)
			}
		}
		if ok, err := VerifyContinuationCertificate(d, cs, req, c, 256); err != nil || !ok {
			t.Fatalf("trial %d: greedy certificate does not verify: ok=%v err=%v", trial, ok, err)
		}
		for i := range c.Required { // minimal by inclusion
			rest := append(append([]TerminalAssumption(nil), c.Required[:i]...), c.Required[i+1:]...)
			if ok, _ := inspectAssumptions(d, cs, req, rest, 256); ok && len(rest) > 0 {
				t.Fatalf("trial %d: member %s is redundant", trial, c.Required[i].CommandID)
			}
		}
		enum, err := EnumerateContinuationCertificates(d, cs, req, 3, 256, 20000, 5*time.Second)
		if err == nil && len(enum) > 0 {
			if len(enum[0].Required) != want {
				t.Fatalf("trial %d: enumeration minimum %d, unbounded minimum %d", trial, len(enum[0].Required), want)
			}
		} else if want > 3 {
			beyond++
		}
	}
	if found < 100 || none < 10 || beyond < 5 {
		t.Fatalf("weak sample: %d with a certificate, %d without, %d beyond gamma=3", found, none, beyond)
	}
	t.Logf("%d blocked candidates with a certificate (%d beyond the enumeration's gamma = 3), %d without", found, beyond, none)
}

// T5 property (release 083): admission after the greedy certificate's members are STOPPED is safe in every
// outcome of the remaining unresolved commands (the guard decides; the certificate only names targets).
func TestGreedyCertificateAdmissionsAreSafe(t *testing.T) {
	rng := rand.New(rand.NewSource(83))
	checked := 0
	for trial := 0; trial < 400; trial++ {
		d, cs, req := randomAddHistory(rng, 3+rng.Intn(8), int64(2+rng.Intn(7)))
		certs, err := GreedyContinuationCertificate(d, cs, req, 256)
		if err != nil || len(certs) == 0 {
			continue
		}
		stop := map[string]bool{}
		for _, x := range certs[0].Required {
			stop[x.CommandID] = true
		}
		after := cloneCommands(cs)
		for i := range after {
			if stop[after[i].CommandID] {
				after[i].Status = "STOPPED"
			}
		}
		ok, err := projectedSafe(d, after, req, 256)
		if err != nil || !ok {
			t.Fatalf("trial %d: the guard does not admit after the certificate's stops (ok=%v err=%v)", trial, ok, err)
		}
		// every outcome of the commands still unresolved, then the candidate applied
		var rest []journal.CommandRecord
		base := d.Anchor[0]
		for _, c := range after {
			switch c.Status {
			case "APPLIED":
				base += c.Value[0]
			case "PENDING":
				rest = append(rest, c)
			}
		}
		for mask := 0; mask < 1<<len(rest); mask++ {
			x := base + req.Value[0]
			for i, c := range rest {
				if mask&(1<<i) != 0 {
					x += c.Value[0]
				}
			}
			if x > d.ComponentMax[0] || x < d.ComponentMin[0] {
				t.Fatalf("trial %d: an outcome reaches %d outside [%d, %d]", trial, x, d.ComponentMin[0], d.ComponentMax[0])
			}
		}
		checked++
	}
	if checked < 100 {
		t.Fatalf("only %d admissions checked", checked)
	}
}

// The greedy certificate scales: hundreds of small unresolved doses (where the enumeration's state cap is
// exceeded) are handled without a bound, and the result has the minimum size computed from the sizes alone.
func TestGreedyCertificateLargeWindow(t *testing.T) {
	rng := rand.New(rand.NewSource(7))
	d := journal.Domain{ID: "g", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{345}, Anchor: []int64{0}}
	var cs []journal.CommandRecord
	var vals []int64
	var sum int64
	for i := 0; i < 300 && sum < 345; i++ {
		v := 1 + rng.Int63n(5)
		if sum+v > 345 {
			v = 345 - sum
		}
		sum += v
		vals = append(vals, v)
		cs = append(cs, journal.CommandRecord{DomainID: "g", CommandID: fmt.Sprintf("c%03d", i), Generation: 1, Epoch: 1, Sequence: uint64(i + 1),
			Operation: domain.OpAdd, Value: []int64{v}, IntentHash: fmt.Sprintf("h%03d", i), Reservation: []int64{v}, Status: "PENDING", Decision: "ADMITTED"})
	}
	req := protocol.CommandRequest{SchemaVersion: 1, DomainID: "g", Generation: 1, CommandID: "cand", Operation: domain.OpAdd, Value: []int64{20}, Epoch: 1, Sequence: 999, IntentHash: "hc"}
	start := time.Now()
	certs, err := GreedyContinuationCertificate(d, cs, req, 256)
	took := time.Since(start)
	if err != nil || len(certs) != 1 {
		t.Fatalf("certs=%d err=%v", len(certs), err)
	}
	sort.Slice(vals, func(i, j int) bool { return vals[i] > vals[j] })
	need, k := sum+20-345, 0
	for acc := int64(0); acc < need; k++ {
		acc += vals[k]
	}
	if len(certs[0].Required) != k {
		t.Fatalf("size %d, minimum %d", len(certs[0].Required), k)
	}
	if _, err := EnumerateContinuationCertificates(d, cs, req, 3, 256, 20000, 5*time.Second); err == nil && k <= 3 {
		t.Log("enumeration also succeeded")
	}
	if took > 500*time.Millisecond {
		t.Fatalf("greedy certificate took %s for %d unresolved commands", took, len(cs))
	}
	t.Logf("%d unresolved, deficit %d, certificate of %d in %s", len(cs), need, k, took)
}

func TestGreedyCertificateNotAdditive(t *testing.T) {
	d := journal.Domain{ID: "g", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{10}, Anchor: []int64{0}}
	cs := []journal.CommandRecord{{DomainID: "g", CommandID: "s", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpSet, Value: []int64{5},
		IntentHash: "h", Reservation: []int64{5}, Status: "PENDING", Decision: "ADMITTED"}}
	req := protocol.CommandRequest{SchemaVersion: 1, DomainID: "g", Generation: 1, CommandID: "c", Operation: domain.OpAdd, Value: []int64{8}, Epoch: 1, Sequence: 2, IntentHash: "hc"}
	if _, err := GreedyContinuationCertificate(d, cs, req, 256); err != ErrNotAdditive {
		t.Fatalf("SET history: err=%v, want ErrNotAdditive", err)
	}
	d2 := journal.Domain{ID: "g", Generation: 1, InstalledEpoch: 1, Dimension: 2, ComponentMax: []int64{10, 10}, Anchor: []int64{0, 0}}
	if GreedyApplies(d2, nil, protocol.CommandRequest{Operation: domain.OpAdd, Value: []int64{1, 1}}) {
		t.Fatal("two bounded components: the greedy certificate does not apply")
	}
}

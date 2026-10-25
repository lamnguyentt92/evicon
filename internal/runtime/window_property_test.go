package runtime

import (
	"fmt"
	"math/rand"
	"path/filepath"
	"testing"

	"evicon/internal/analyzer"
	"evicon/internal/journal"
	"evicon/internal/probe"
	"evicon/internal/protocol"
)

// F48 (release 077): Theorem T3 and the outstanding window, on the runtime's own admission path
// (journal store, belief reconstruction, window check, relational guard), not only on the analyzer.
// For random non-negative ADD histories under an upper bound, with a random window W, evidence is
// committed one command at a time in random order and a fixed candidate is evaluated after every
// step by two authorities on the same store: one with W and one without a window. At every step:
//
//	P1  APPLIED evidence never changes the guard's verdict (neither way): the belief already
//	    contained the configuration in which the command took effect.
//	P2  STOPPED evidence never turns an admitted candidate into a blocked one.
//	P3  every terminal answer (APPLIED or STOPPED) frees exactly one window slot: the verdict with
//	    W is OUTSTANDING_BUDGET_EXHAUSTED exactly when unresolved + 1 > W, and the guard's verdict
//	    otherwise.
//
// So under a window, APPLIED can re-open a request only through P3 (a slot), never through the
// bound; only STOPPED re-opens a request that the bound blocked. The coverage counters check that
// none of the three is vacuous. The admission verdict (evaluateCandidate) does not depend on the
// resolution policy; the authorities are built with one that needs no owner contract.
func TestF48TerminalEvidenceAndWindow(t *testing.T) {
	rng := rand.New(rand.NewSource(20261025))
	var cases, appliedSteps, stoppedReopens, windowFrees, appliedWindowReopens int
	for cases < 400 {
		w := 1 + rng.Intn(6)
		max := int64(6 + rng.Intn(20))
		anchor := int64(rng.Intn(4))
		dir := t.TempDir()
		st, err := journal.Open(filepath.Join(dir, "a.db"))
		if err != nil {
			t.Fatal(err)
		}
		d := journal.Domain{ID: "dose-1", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0},
			ComponentMax: []int64{max}, Anchor: []int64{anchor}, ContractHash: "f48"}
		mk := func(window int) *Authority {
			a, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, EndpointURL: "http://127.0.0.1:1",
				Policy: probe.M0NoProbe, ProbeBudget: 4, MaxOutstanding: window, MaxConfigurations: 256})
			if err != nil {
				t.Fatal(err)
			}
			return a
		}
		aw, ainf := mk(w), mk(1<<20)
		// a safe history, admitted as the runtime admits: the guard without a window, then at most W unresolved
		var unresolved []protocol.CommandRequest
		n := 1 + rng.Intn(8)
		for j := 0; j < n; j++ {
			req := dose(t, fmt.Sprintf("c%d", j), int64(rng.Intn(6)), uint64(j+1))
			dom, _ := st.GetDomain(d.ID)
			r, err := ainf.evaluateCandidate(dom, req)
			if err != nil {
				t.Fatal(err)
			}
			if r.Verdict != analyzer.Safe || len(unresolved) >= w {
				continue
			}
			rec := journal.CommandRecord{DomainID: d.ID, CommandID: req.CommandID, Generation: 1, Epoch: 1, Sequence: req.Sequence,
				Operation: req.Operation, Value: req.Value, IntentHash: req.IntentHash, Reservation: req.Value, Status: "PENDING",
				Decision: "ADMITTED", Reason: "RELATIONAL_BELIEF_SAFE"}
			if _, err := st.ReserveAndEnqueue(rec, "http://127.0.0.1:1", "test", nil); err != nil {
				t.Fatal(err)
			}
			unresolved = append(unresolved, req)
		}
		if len(unresolved) == 0 {
			st.Close()
			continue
		}
		cand := dose(t, "cand", int64(1+rng.Intn(8)), uint64(n+1))
		eval := func() (analyzer.Result, analyzer.Result) {
			dom, err := st.GetDomain(d.ID)
			if err != nil {
				t.Fatal(err)
			}
			rw, err := aw.evaluateCandidate(dom, cand)
			if err != nil {
				t.Fatal(err)
			}
			rg, err := ainf.evaluateCandidate(dom, cand)
			if err != nil {
				t.Fatal(err)
			}
			return rw, rg
		}
		check := func(step string, rw, rg analyzer.Result, open int) {
			if open+1 > w {
				if rw.Verdict != analyzer.Deferred || rw.Reason != "OUTSTANDING_BUDGET_EXHAUSTED" {
					t.Fatalf("case %d %s: %d unresolved, W=%d: want the window to defer, got %+v", cases, step, open, w, rw)
				}
			} else if rw.Verdict != rg.Verdict || rw.Reason != rg.Reason {
				t.Fatalf("case %d %s: %d unresolved < W=%d: the window must not change the guard's verdict: %+v vs %+v", cases, step, open, w, rw, rg)
			}
		}
		rw, rg := eval()
		check("start", rw, rg, len(unresolved))
		rng.Shuffle(len(unresolved), func(i, j int) { unresolved[i], unresolved[j] = unresolved[j], unresolved[i] })
		for k, u := range unresolved {
			outcome := "APPLIED"
			if rng.Intn(2) == 0 {
				outcome = "STOPPED"
			}
			if _, err := st.RecordEvidence(journal.ReceiptRecord{DomainID: d.ID, CommandID: u.CommandID, IntentHash: u.IntentHash, Outcome: outcome,
				OwnerVersion: uint64(k + 1), ReceiptVersion: 1, State: []int64{0}}, "F48", "OWNER_RECEIPT", true); err != nil {
				t.Fatal(err)
			}
			open := len(unresolved) - k - 1
			nw, ng := eval()
			step := fmt.Sprintf("%s %s", outcome, u.CommandID)
			check(step, nw, ng, open)
			switch outcome {
			case "APPLIED":
				appliedSteps++
				if ng.Verdict != rg.Verdict { // P1
					t.Fatalf("case %d %s: APPLIED changed the guard's verdict %s -> %s (value %d, bound %d, anchor %d, cand %d)",
						cases, step, rg.Verdict, ng.Verdict, u.Value[0], max, anchor, cand.Value[0])
				}
				if rw.Reason == "OUTSTANDING_BUDGET_EXHAUSTED" && nw.Verdict == analyzer.Safe {
					appliedWindowReopens++ // re-opened by the slot it freed (P3), not by the bound
				}
			case "STOPPED":
				if rg.Verdict == analyzer.Safe && ng.Verdict != analyzer.Safe { // P2
					t.Fatalf("case %d %s: STOPPED blocked an admitted candidate: %+v", cases, step, ng)
				}
				if rg.Verdict != analyzer.Safe && ng.Verdict == analyzer.Safe {
					stoppedReopens++
				}
			}
			if rw.Reason == "OUTSTANDING_BUDGET_EXHAUSTED" && nw.Reason != "OUTSTANDING_BUDGET_EXHAUSTED" {
				windowFrees++
			}
			rw, rg = nw, ng
		}
		st.Close()
		cases++
	}
	if appliedSteps < 300 || stoppedReopens < 30 || windowFrees < 30 || appliedWindowReopens < 10 {
		t.Fatalf("weak coverage: %d APPLIED steps, %d re-opened by STOPPED, %d window slots freed, %d re-opened by an APPLIED slot",
			appliedSteps, stoppedReopens, windowFrees, appliedWindowReopens)
	}
	t.Logf("%d cases: %d APPLIED steps (verdict unchanged), %d re-opened by STOPPED, %d window slots freed (%d by APPLIED re-opened the request)",
		cases, appliedSteps, stoppedReopens, windowFrees, appliedWindowReopens)
}

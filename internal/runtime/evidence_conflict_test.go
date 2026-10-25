package runtime

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"evicon/internal/contract"
	"evicon/internal/device"
	"evicon/internal/journal"
	"evicon/internal/probe"
	"evicon/internal/protocol"
	"evicon/internal/trace"
)

// F37 (release 068): an owner whose barrier receipt contradicts a terminal outcome the
// authority has committed (067 testbed: the K0F emulator sealed a late copy of an APPLIED
// command as STOPPED). The authority keeps the committed outcome, aborts the barrier (the
// domain is not left paused), records EVIDENCE_CONFLICT, and the resolver escalates
// instead of fencing again every second.
func TestF37EvidenceConflictAbortsBarrierAndEscalates(t *testing.T) {
	r := newDeviceRig(t, func(c *device.Config) { c.Fence = true }) // K0F
	var mu sync.Mutex
	barriers := 0
	inner := r.srv.Config.Handler
	r.srv.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if req.URL.Path != "/v1/barriers" {
			inner.ServeHTTP(w, req)
			return
		}
		mu.Lock()
		barriers++
		mu.Unlock()
		rec := httptest.NewRecorder()
		inner.ServeHTTP(rec, req)
		var br protocol.BarrierReceipt
		if rec.Code != 200 || json.Unmarshal(rec.Body.Bytes(), &br) != nil {
			w.WriteHeader(rec.Code)
			_, _ = w.Write(rec.Body.Bytes())
			return
		}
		for i := range br.Receipts { // a faulty owner: "A" applied, reported STOPPED
			if br.Receipts[i].CommandID == "A" {
				br.Receipts[i].Outcome, br.Receipts[i].Reason = "STOPPED", "SEALED_BY_FENCE"
			}
		}
		writeJSON(w, 200, br)
	})
	a, st, disp := deviceAuthority(t, r)
	tracePath := filepath.Join(t.TempDir(), "trace.jsonl")
	a.cfg.Trace = trace.New(tracePath, "authority-test", "f37", "EVICON", 0)

	if code, out := invokeCandidate(t, a, dose(t, "A", 2, 1)); code != 202 || out.Decision != "ADMITTED" {
		t.Fatalf("A: %d %+v", code, out)
	}
	_ = disp.DispatchOnce(context.Background())
	if ra, _ := st.GetCommand("dose-1", "A"); ra.Status != "APPLIED" {
		t.Fatalf("A must be APPLIED, got %s", ra.Status)
	}
	r.drop["B"] = true // B is lost on the way: ambiguous
	if code, out := invokeCandidate(t, a, dose(t, "B", 2, 2)); code != 202 {
		t.Fatalf("B: %d %+v", code, out)
	}
	_ = disp.DispatchOnce(context.Background())

	if err := a.SweepOnce(); err != nil { // aged B: the resolver fences
		t.Fatal(err)
	}
	if barriers != 1 {
		t.Fatalf("expected one barrier call, got %d", barriers)
	}
	if ra, _ := st.GetCommand("dose-1", "A"); ra.Status != "APPLIED" {
		t.Fatalf("the committed APPLIED must stay, got %s", ra.Status)
	}
	if rb, _ := st.GetCommand("dose-1", "B"); rb.Status == "STOPPED" {
		t.Fatal("nothing may be sealed by a barrier that contradicts committed evidence")
	}
	if _, pending, err := st.PendingBarrier("dose-1"); err != nil || pending {
		t.Fatalf("the barrier must be aborted, not left pending (domain paused): pending=%v err=%v", pending, err)
	}
	if !a.evidenceConflicted("dose-1") {
		t.Fatal("the domain must be marked conflicted")
	}
	for i := 0; i < 3; i++ {
		if err := a.SweepOnce(); err != nil {
			t.Fatal(err)
		}
	}
	if barriers != 1 {
		t.Fatalf("the resolver must escalate, not fence again: %d barrier calls", barriers)
	}
	_, out := invokeCandidate(t, a, dose(t, "C", 1, 3))
	if strings.Contains(out.Reason, "BARRIER_IN_PROGRESS") {
		t.Fatalf("admission must not stay paused: %+v", out)
	}
	raw, _ := os.ReadFile(tracePath)
	if !bytes.Contains(raw, []byte(`"event":"EVIDENCE_CONFLICT"`)) || !bytes.Contains(raw, []byte("committed=APPLIED;owner=STOPPED")) {
		t.Fatalf("trace must record the conflict:\n%s", tail(raw, 1200))
	}
	if !bytes.Contains(raw, []byte("EVIDENCE_CONFLICT;targets=")) {
		t.Fatal("the escalation must be a recorded EVICON_DECISION")
	}
}

// F38 (release 068): a dispatch reply that contradicts the committed outcome leaves the
// outbox (no endless re-dispatch) and is recorded as EVIDENCE_CONFLICT. Release 072: a
// terminal command is never sent again, so the contradiction can only come from a dispatch
// already in flight when the terminal evidence is committed; the test commits it from the
// device side of that dispatch.
func TestF38DispatchReplyConflictLeavesOutbox(t *testing.T) {
	r := newDeviceRig(t, func(c *device.Config) { c.Fence = true; c.Query = true }) // CUTOFF
	a, st, disp := deviceAuthority(t, r)
	tracePath := filepath.Join(t.TempDir(), "trace.jsonl")
	disp.Trace = trace.New(tracePath, "dispatcher-test", "f38", "EVICON", 0)
	if code, out := invokeCandidate(t, a, dose(t, "A", 2, 1)); code != 202 {
		t.Fatalf("A: %d %+v", code, out)
	}
	// while the dispatch of A is in flight, the authority learns STOPPED for A (as a faulty
	// owner might report); the device then applies A and replies APPLIED
	r.beforeEffect = func(cr protocol.CommandRequest) {
		if cr.CommandID != "A" {
			return
		}
		cmd, _ := st.GetCommand("dose-1", "A")
		if _, err := st.RecordEvidence(journalReceipt(cmd, "STOPPED"), "TEST", "TEST", false); err != nil {
			t.Error(err)
		}
	}
	_ = disp.DispatchOnce(context.Background())
	r.beforeEffect = nil
	_ = disp.DispatchOnce(context.Background())
	if n, _ := st.PendingOutboxCount(); n != 0 {
		t.Fatalf("the conflicted item must leave the outbox, %d pending", n)
	}
	raw, _ := os.ReadFile(tracePath)
	if !bytes.Contains(raw, []byte(`"event":"EVIDENCE_CONFLICT"`)) {
		t.Fatalf("trace must record the conflict:\n%s", tail(raw, 1200))
	}
	if got := r.plant.Truth("A", "dose-1").Effects; got != 1 {
		t.Fatalf("A took effect %d times, want 1", got)
	}
}

// F43 (release 072): a command that terminal evidence closed is never dispatched again,
// even when the evidence did not close its outbox item and the dispatcher works on an
// older snapshot. 072 pre-release check: on K0S (stop, no dedup) a stop answered APPLIED,
// the T4 guard allowed the retry because the command was no longer unresolved, and the
// device executed the command a second time.
func TestF43TerminalCommandIsNeverResent(t *testing.T) {
	r := newDeviceRig(t, func(c *device.Config) { c.Stop = true }) // K0S: no dedup
	a, st, disp := deviceAuthority(t, r)
	tracePath := filepath.Join(t.TempDir(), "trace.jsonl")
	disp.Trace = trace.New(tracePath, "dispatcher-test", "f43", "EVICON", 0)
	ec := a.cfg.EvidenceContract
	if ec.Has(contract.CapDedup) {
		t.Fatal("test needs a contract without dedup")
	}
	disp.RetryAllowed = RetryAllowedFunc(st, ec, -1)
	r.drop["A"] = true // the first dispatch fails at the transport
	if code, out := invokeCandidate(t, a, dose(t, "A", 2, 1)); code != 202 {
		t.Fatalf("A: %d %+v", code, out)
	}
	_ = disp.DispatchOnce(context.Background())
	r.drop["A"] = false
	if _, err := r.dev.Apply(dose(t, "A", 2, 1)); err != nil { // the request was delivered after all
		t.Fatal(err)
	}
	// terminal evidence from another path (a stop answered APPLIED) that leaves the item open
	cmd, _ := st.GetCommand("dose-1", "A")
	rc := journalReceipt(cmd, "APPLIED")
	rc.State = []int64{2}
	if _, err := st.RecordEvidence(rc, "TEST", "POLICY_STOP", false); err != nil {
		t.Fatal(err)
	}
	if RetryAllowedFunc(st, ec, -1)("dose-1", "A", 1) {
		t.Fatal("the T4 guard must refuse to re-send a terminal command")
	}
	disp.RetryAllowed = func(string, string, int64) bool { return true } // even a permissive guard
	_ = disp.DispatchOnce(context.Background())
	_ = disp.DispatchOnce(context.Background())
	if got := r.plant.Truth("A", "dose-1").Effects; got != 1 {
		t.Fatalf("A took effect %d times, want 1", got)
	}
	if n, _ := st.PendingOutboxCount(); n != 0 {
		t.Fatalf("the closed item must leave the outbox, %d pending", n)
	}
	raw, _ := os.ReadFile(tracePath)
	if !bytes.Contains(raw, []byte(`"event":"OUTBOX_SKIPPED_TERMINAL"`)) {
		t.Fatalf("trace must record the skip:\n%s", tail(raw, 1200))
	}
}

func journalReceipt(cmd journal.CommandRecord, outcome string) journal.ReceiptRecord {
	return journal.ReceiptRecord{DomainID: cmd.DomainID, CommandID: cmd.CommandID, IntentHash: cmd.IntentHash, Outcome: outcome, OwnerVersion: 99, ReceiptVersion: 99, State: []int64{0}}
}

func tail(b []byte, n int) string {
	if len(b) > n {
		b = b[len(b)-n:]
	}
	return string(b)
}

var _ = io.Discard

// F39 (release 068): a query selector (M1) does not query an owner whose contract has no
// Q_A (067 confirmatory campaign: M1 sent queries that a K0 device answered 409).
func TestF39QuerySelectorSkipsOwnerWithoutQuery(t *testing.T) {
	r := newDeviceRig(t, func(c *device.Config) {}) // K0
	queries := 0
	inner := r.srv.Config.Handler
	r.srv.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if strings.HasPrefix(req.URL.Path, "/v1/receipts/") {
			queries++
		}
		inner.ServeHTTP(w, req)
	})
	a, _, disp := deviceAuthority(t, r)
	a.cfg.Policy = probe.M1
	r.drop["A"] = true
	if code, out := invokeCandidate(t, a, dose(t, "A", 8, 1)); code != 202 {
		t.Fatalf("A: %d %+v", code, out)
	}
	_ = disp.DispatchOnce(context.Background())
	_, out := invokeCandidate(t, a, dose(t, "C", 8, 2))
	if out.Decision == "ADMITTED" {
		t.Fatalf("C must not be admitted while A is ambiguous: %+v", out)
	}
	if queries != 0 || !strings.Contains(out.Reason, "NO_QUERY_IN_CONTRACT") {
		t.Fatalf("M1 must not query a K0 owner: %d queries, %+v", queries, out)
	}
}

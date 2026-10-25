package runtime

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"

	"evicon/internal/domain"
	"evicon/internal/journal"
	"evicon/internal/probe"
)

func setupBeliefDebugAuthority(t *testing.T, records []journal.CommandRecord, cap int) *Authority {
	t.Helper()
	d := journal.Domain{ID: "d", Generation: 1, InstalledEpoch: 1, Dimension: 1, Anchor: []int64{0}, ContractHash: "belief-debug"}
	st, err := journal.Open(filepath.Join(t.TempDir(), "authority.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = st.Close() })
	if err := st.EnsureDomain(d); err != nil {
		t.Fatal(err)
	}
	for i, rec := range records {
		dom, err := st.GetDomain(d.ID)
		if err != nil {
			t.Fatal(err)
		}
		if rec.DomainID == "" {
			rec.DomainID = d.ID
		}
		if rec.Generation == 0 {
			rec.Generation = 1
		}
		if rec.Epoch == 0 {
			rec.Epoch = 1
		}
		if rec.Sequence == 0 {
			rec.Sequence = uint64(i + 1)
		}
		if rec.IntentHash == "" {
			rec.IntentHash = fmt.Sprintf("h-%d", i)
		}
		rec.Reservation = append([]int64(nil), rec.Value...)
		rec.Status = "PENDING"
		rec.Decision = "ADMITTED"
		rec.Reason = "test"
		if _, err := st.ReserveAndEnqueueAtRevision(rec, "http://unused", "TEST", dom.Revision, nil); err != nil {
			t.Fatal(err)
		}
		if err := st.RecordReceipt(journal.ReceiptRecord{DomainID: d.ID, CommandID: rec.CommandID, IntentHash: rec.IntentHash, Outcome: "APPLIED", OwnerVersion: uint64(i + 1), ReceiptVersion: 1, State: []int64{0}}); err != nil {
			t.Fatal(err)
		}
	}
	a, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, Profile: "E2", Policy: probe.M0, QueryCosts: probe.DefaultCostModel(), MaxConfigurations: cap, MaxOutstanding: 4})
	if err != nil {
		t.Fatal(err)
	}
	return a
}

func getBeliefDebug(t *testing.T, a *Authority) (int, map[string]any) {
	t.Helper()
	rr := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/_debug/belief", nil)
	a.Handler().ServeHTTP(rr, req)
	var out map[string]any
	if err := json.Unmarshal(rr.Body.Bytes(), &out); err != nil {
		t.Fatal(err)
	}
	return rr.Code, out
}

func TestBeliefDebugTerminalHistoryReportsExactStructuredStats(t *testing.T) {
	var records []journal.CommandRecord
	for i := 0; i < 9; i++ {
		records = append(records, journal.CommandRecord{CommandID: fmt.Sprintf("a%d", i), Operation: domain.OpAdd, Value: []int64{1}})
	}
	a := setupBeliefDebugAuthority(t, records, 256)
	code, out := getBeliefDebug(t, a)
	if code != 200 {
		t.Fatalf("code=%d body=%v", code, out)
	}
	if out["status"] != "OK" || out["enumeration_algorithm"] != "terminal_exact_v1" || out["enumeration_complete"] != true {
		t.Fatalf("bad envelope: %v", out)
	}
	if out["configuration_count"] != float64(1) {
		t.Fatalf("want exact count 1: %v", out)
	}
	cfgs, ok := out["configurations"].([]any)
	if !ok || len(cfgs) != 1 {
		t.Fatalf("bad configurations: %T %v", out["configurations"], out["configurations"])
	}
}

func TestBeliefDebugTrueFinalStateBudgetIsStructured503(t *testing.T) {
	records := []journal.CommandRecord{{CommandID: "set", Operation: domain.OpSet, Value: []int64{0}}}
	for i := 0; i < 9; i++ {
		records = append(records, journal.CommandRecord{CommandID: fmt.Sprintf("a%d", i), Operation: domain.OpAdd, Value: []int64{int64(1 << i)}})
	}
	a := setupBeliefDebugAuthority(t, records, 256)
	code, out := getBeliefDebug(t, a)
	if code != 503 {
		t.Fatalf("code=%d body=%v", code, out)
	}
	if out["status"] != "ENUMERATION_BUDGET_EXCEEDED" || out["error_kind"] != "BELIEF_ENUMERATION_BUDGET" || out["enumeration_complete"] != false {
		t.Fatalf("bad budget envelope: %v", out)
	}
	if out["configuration_count"] != nil {
		t.Fatalf("budget failure must not invent count: %v", out)
	}
}

package runtime

import (
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"
	"time"

	"evicon/internal/contract"
	"evicon/internal/journal"
	"evicon/internal/probe"
	"evicon/internal/protocol"
)

// F41 (release 072): the runtime never turns NOT_FOUND into STOPPED, even
// for a contract with owner expiry that reveals it (ER). Rule (A4) of the
// paper is applied by the simulator only; the runtime is conservative, which
// is sound (fewer admissible observations). The age that the runtime passes
// to Admit is taken when the query is sent (release 072); an answer produced
// before the owner's deadline but received after it is never evidence.
func TestF41ExpiryAgeIsTakenAtQuerySend(t *testing.T) {
	const expiry = 400 * time.Millisecond
	for _, tc := range []struct {
		name      string
		waitAfter time.Duration // between command creation and the query
		delay     time.Duration // owner answer delay
		want      string
	}{
		{"answer produced before the deadline, received after", 250 * time.Millisecond, 300 * time.Millisecond, "PENDING"},
		{"query sent after the deadline", 450 * time.Millisecond, 0, "PENDING"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			d := journal.Domain{ID: "dose-1", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{100}, Anchor: []int64{0}, ContractHash: "f41"}
			st, err := journal.Open(filepath.Join(t.TempDir(), "authority.db"))
			if err != nil {
				t.Fatal(err)
			}
			defer st.Close()
			if err := st.EnsureDomain(d); err != nil {
				t.Fatal(err)
			}
			r := protocol.CommandRequest{SchemaVersion: 1, DomainID: d.ID, Generation: 1, CommandID: "A", Operation: "ADD", Value: []int64{3}, Epoch: 1, Sequence: 1}
			if err := r.ValidateAndFillHash(1); err != nil {
				t.Fatal(err)
			}
			dom, _ := st.GetDomain(d.ID)
			rec := journal.CommandRecord{DomainID: d.ID, CommandID: "A", Generation: 1, Epoch: 1, Sequence: 1, Operation: r.Operation, Value: r.Value, IntentHash: r.IntentHash, Reservation: r.Value, Status: "PENDING", Decision: "ADMITTED", Reason: "seed"}
			if _, err := st.ReserveAndEnqueueAtRevision(rec, "http://unused", runtimeAnalyzer, dom.Revision, nil); err != nil {
				t.Fatal(err)
			}
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
				time.Sleep(tc.delay)
				http.NotFound(w, req)
			}))
			defer srv.Close()
			ec := contract.EvidenceContract{SchemaVersion: 1, ID: "k3t-like", System: "test", Source: contract.SourceRuntimeVerified,
				Capabilities:   []contract.EvidenceCapability{contract.CapDedup, contract.CapQueryApplied, contract.CapExpiry},
				DedupHorizonMS: contract.UnboundedHorizonMS, ExpiryMS: expiry.Milliseconds(), ClockSkewMS: 0,
				RecordRetentionMS: contract.UnboundedRetentionMS}
			if !ec.ExpiryRevealsStopped() {
				t.Fatal("test contract must satisfy ER")
			}
			a, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, EndpointURL: srv.URL, Profile: "DEVICE", Policy: probe.EVICON,
				EvidenceContract: ec, ProbeBudget: 2, MaxConfigurations: 256, MaxOutstanding: 8})
			if err != nil {
				t.Fatal(err)
			}
			time.Sleep(tc.waitAfter)
			cmd, _ := st.GetCommand(d.ID, "A")
			stats := policyStats{}
			if err := a.queryDirect("TEST", cmd, &stats); err != nil {
				t.Fatal(err)
			}
			got, _ := st.GetCommand(d.ID, "A")
			if got.Status != tc.want {
				t.Fatalf("status %s, want %s", got.Status, tc.want)
			}
		})
	}
}

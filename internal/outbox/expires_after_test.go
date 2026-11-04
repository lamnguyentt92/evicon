package outbox

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"testing"
	"time"

	"evicon/internal/contract"
	"evicon/internal/domain"
	"evicon/internal/journal"
)

// tbContract084 is the shape of ThingsBoard's measured contract of the 083/084 TB runs (thingsboard-rpc-r0:
// persistent two-way RPC, MEASURED {Q_A, T}, expiry 60 s, no record retention declared: r = 0).
func tbContract084() contract.EvidenceContract {
	return contract.EvidenceContract{SchemaVersion: 1, ID: "meas-thingsboard-ce-server-side-rpc-actuate-persistent-twoway", System: "ThingsBoard CE",
		Source: contract.SourceMeasured, Capabilities: []contract.EvidenceCapability{contract.CapQueryApplied, contract.CapExpiry}, ExpiryMS: 60000,
		Provenance: map[string]string{"Q_A": "P2,P2N:receipts_retained=True@32 x3 repetitions", "T": "P9T x3 repetitions",
			"T_expiry": "P9T:expiry=60.0s;request_delay_ms=61500"},
		FaultModel: "network loss/delay, client abandon; owner up (no owner crash or reboot)"}
}

// tbStrict085 is the strict contract the paper's reclassification gives the same owner: {Q_A} only.
func tbStrict085() contract.EvidenceContract {
	c := tbContract084()
	c.ID += "-strict"
	c.Capabilities = []contract.EvidenceCapability{contract.CapQueryApplied}
	c.ExpiryMS = 0
	delete(c.Provenance, "T")
	delete(c.Provenance, "T_expiry")
	return c
}

// TestDispatchExpiry085: the rule of releases 061-084, moved into DispatchExpiry unchanged.
func TestDispatchExpiry085(t *testing.T) {
	withT, strict := tbContract084(), tbStrict085()
	none := contract.EvidenceContract{ID: "k0", Source: contract.SourceRuntimeVerified}
	for _, c := range []struct {
		name string
		flag time.Duration
		ec   contract.EvidenceContract
		want time.Duration
	}{
		{"contract T, default flag", ExpiryFromContract, withT, 60 * time.Second},
		{"no T, default flag", ExpiryFromContract, strict, 0},
		{"no contract, default flag", ExpiryFromContract, none, 0},
		{"-expires-after wins over T", 30 * time.Second, withT, 30 * time.Second},
		{"-expires-after 0 removes T's expiry", 0, withT, 0},
		{"-expires-after without T (transport metadata)", 60 * time.Second, strict, 60 * time.Second},
		{"-expires-after without a contract", 45 * time.Second, none, 45 * time.Second},
	} {
		if got := DispatchExpiry(c.flag, c.ec); got != c.want {
			t.Fatalf("%s: %s, want %s", c.name, got, c.want)
		}
	}
}

// capture is an owner endpoint that records every request byte for byte (method, path, headers, body) and answers
// with an APPLIED receipt.
type capture struct {
	mu   sync.Mutex
	reqs [][]byte
}

func (c *capture) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	raw, _ := httputil.DumpRequest(r, true)
	c.mu.Lock()
	c.reqs = append(c.reqs, raw)
	c.mu.Unlock()
	w.Header().Set("content-type", "application/json")
	_, _ = w.Write([]byte(`{"schema_version":1,"domain_id":"tb-1","generation":1,"command_id":"c1","intent_hash":"h1","outcome":"APPLIED","owner_version":1,"receipt_version":1,"state":[1],"reason":"TB_RPC_SUCCESSFUL"}`))
}

func copyFile(t *testing.T, src, dst string) {
	t.Helper()
	b, err := os.ReadFile(src)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(dst, b, 0o644); err != nil {
		t.Fatal(err)
	}
}

// TestExpiresAfterDispatchIdentical085 (review M3b, PREREG_extra_085.md section 9): for the ThingsBoard contract, the
// 084 configuration {Q_A, T(60 s)} with r = 0 and the strict contract {Q_A} with -expires-after 60s send byte-identical
// dispatch requests for the same journal state: the same body (expires_at_ms = creation + 60 s included), headers and
// path. The journal is written once and copied, so both dispatchers see the same command and creation time.
func TestExpiresAfterDispatchIdentical085(t *testing.T) {
	dir := t.TempDir()
	cap1 := &capture{}
	srv := httptest.NewServer(cap1)
	defer srv.Close()
	base := filepath.Join(dir, "base.db")
	st, err := journal.Open(base)
	if err != nil {
		t.Fatal(err)
	}
	max := int64(345)
	if err := st.EnsureDomain(journal.Domain{ID: "tb-1", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{345},
		SumMax: &max, Anchor: []int64{0}, ContractHash: "h"}); err != nil {
		t.Fatal(err)
	}
	rec := journal.CommandRecord{DomainID: "tb-1", CommandID: "c1", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{1},
		IntentHash: "h1", Reservation: []int64{1}, Status: "PENDING", Decision: "ADMITTED", Reason: "test"}
	if _, err := st.ReserveAndEnqueue(rec, srv.URL, "test", nil); err != nil {
		t.Fatal(err)
	}
	created, err := st.CommandCreatedTimes("tb-1")
	if err != nil {
		t.Fatal(err)
	}
	if err := st.Close(); err != nil {
		t.Fatal(err)
	}
	for _, sfx := range []string{"-wal", "-shm"} {
		if _, err := os.Stat(base + sfx); err == nil {
			t.Fatalf("journal not checkpointed on close (%s left)", sfx)
		}
	}
	var bodies [][]byte
	for i, cfg := range []struct {
		ec   contract.EvidenceContract
		flag time.Duration
	}{{tbContract084(), ExpiryFromContract}, {tbStrict085(), 60 * time.Second}} {
		if err := cfg.ec.Validate(); err != nil {
			t.Fatal(err)
		}
		exp := DispatchExpiry(cfg.flag, cfg.ec)
		if exp != 60*time.Second {
			t.Fatalf("config %d: expiry %s", i, exp)
		}
		path := filepath.Join(dir, "arm"+string(rune('a'+i))+".db")
		copyFile(t, base, path)
		s, err := journal.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		d := New(s, 10*time.Millisecond, nil)
		d.ExpiresAfter = exp
		if err := d.DispatchOnce(context.Background()); err != nil {
			t.Fatal(err)
		}
		if c, err := s.GetCommand("tb-1", "c1"); err != nil || c.Status != "APPLIED" {
			t.Fatalf("config %d: %+v %v", i, c, err)
		}
		s.Close()
		cap1.mu.Lock()
		if len(cap1.reqs) != i+1 {
			t.Fatalf("config %d: %d requests", i, len(cap1.reqs))
		}
		bodies = append(bodies, cap1.reqs[i])
		cap1.mu.Unlock()
	}
	if !bytes.Equal(bodies[0], bodies[1]) {
		t.Fatalf("dispatch requests differ:\n%s\n---\n%s", bodies[0], bodies[1])
	}
	ms := created["c1"].Add(60 * time.Second).UnixMilli()
	if !bytes.Contains(bodies[0], []byte(`"expires_at_ms":`+strconv.FormatInt(ms, 10))) {
		t.Fatalf("expires_at_ms != creation + 60 s (%d): %s", ms, bodies[0])
	}
}

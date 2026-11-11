package runtime

// Release 087 (reboot_recovery.go, PREREG_extra_087.md item B): tests of the reboot recovery against a Go model of
// the cutoff firmware's decision rules (src/evc_core.c: receipt ring without a time horizon, claim mark persisted in
// blocks before an effect, the eviction guard, the durable cutoff; a reboot keeps cutoff and claim mark and loses the
// ring and the RAM level, and the boot-report variant answers with its claim floor) behind a model of the MQTT owner
// gateway (its durable forward log and receipt cache, the fence answer it composes, and the recovery switch GW-3: an
// identity never forwarded at or below the known floor is sealed STOPPED and never forwarded). The plant's truth is the
// model's own effect count per identity.
//
//   TestRebootRecoveryDefaultOff087    without the flag: no dispatcher hook, the client's sequence stored as sent, and
//                                      the journal keeps the 086 schema (no recovery table)
//   TestRecSequenceAdvance087          REC-1/REC-2: a reported floor moves new owner sequences above it; replays map
//                                      back; an out-of-order client sequence is refused
//   TestRecFloorCheck087               the B2-02 scenario after a reboot: REC-3 commits the evidence anchor
//                                      (MATCH_FLOOR) and admission goes on within the bound; release 086 aborts the
//                                      fence (evidence conflict); owner-state with the recovery adopts the reset level
//                                      and the plant exceeds the bound (negative control)
//   TestRecWindow087                   REC-4: an identity a complete fence listed UNCERTAIN leaves the window, its
//                                      reservation stays in the belief (the bound still counts it)
//   TestRebootRecoveryProperty087      random executions with lost requests, late copies, lost replies, fences and
//                                      one or two reboots: the recovery never violates the bound and never commits a
//                                      false STOPPED or APPLIED and admits after reboots; 086 without the recovery is
//                                      safe and loses admissions; owner-state with the recovery violates the bound

import (
	"context"
	"encoding/json"
	"fmt"
	"math/rand"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"evicon/internal/contract"
	"evicon/internal/journal"
	"evicon/internal/outbox"
	"evicon/internal/probe"
	"evicon/internal/protocol"
	"evicon/internal/resolve"
	"evicon/internal/sqlitecgo"
	"evicon/internal/trace"
)

type fwRcpt struct {
	seq     uint64
	outcome string
}

type fwHeld struct {
	id     string
	seq    uint64
	value  int64
	intent string
}

// fwRig: the firmware model, the gateway model and the plant's truth.
type fwRig struct {
	mu  sync.Mutex
	rng *rand.Rand
	// firmware, durable
	cutoff, hw uint64
	// firmware, RAM
	ring    map[string]fwRcpt
	order   []string
	evicted uint64
	level   int64
	ov      uint64
	floor   uint64
	block   uint64
	ringN   int
	down    bool
	// truth
	effects map[string]int
	total   int64
	// gateway
	fwd         map[string]fwHeld
	cache       map[string]protocol.Receipt
	gwFloor     uint64
	recoveryGW  bool // GW-3
	reportFloor bool // the boot-report firmware (floor in every answer, hello on reconnect)
	lossy       bool
	holdNext    bool     // the next effect request is held on the way (in flight)
	held        []fwHeld // copies on the way, delivered late (or after a reboot)
	srv         *httptest.Server
	reboots     int
}

func newFWRig(t *testing.T, rng *rand.Rand, block uint64, ringN int, reportFloor, recoveryGW, lossy bool) *fwRig {
	t.Helper()
	r := &fwRig{rng: rng, ring: map[string]fwRcpt{}, effects: map[string]int{}, fwd: map[string]fwHeld{}, cache: map[string]protocol.Receipt{},
		block: block, ringN: ringN, reportFloor: reportFloor, recoveryGW: recoveryGW, lossy: lossy}
	mux := http.NewServeMux()
	mux.HandleFunc("/v1/effects", r.effect)
	mux.HandleFunc("/v1/receipts/", r.query)
	mux.HandleFunc("/v1/barriers", r.barrier)
	mux.HandleFunc("/v1/stop", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 409, map[string]any{"error": "STOP_UNSUPPORTED"})
	})
	r.srv = httptest.NewServer(mux)
	t.Cleanup(r.srv.Close)
	return r
}

// ---- firmware (src/evc_core.c semantics; horizon -1, guard on, cutoff mode, no bound, no expiry) ----
func (r *fwRig) store(id string, seq uint64, outcome string) {
	if _, ok := r.ring[id]; !ok {
		if len(r.order) >= r.ringN {
			old := r.order[0]
			r.order = r.order[1:]
			if o := r.ring[old]; o.outcome == "APPLIED" && o.seq > r.evicted {
				r.evicted = o.seq
			}
			delete(r.ring, old)
		}
		r.order = append(r.order, id)
	}
	r.ring[id] = fwRcpt{seq: seq, outcome: outcome}
	r.ov++
}

func (r *fwRig) fwCommand(id string, seq uint64, v int64) string {
	if p, ok := r.ring[id]; ok {
		return p.outcome // the ring deduplicates every copy (no time horizon)
	}
	if seq <= r.evicted {
		return "UNCERTAIN" // the eviction guard: refused, never executed
	}
	if seq <= r.cutoff {
		r.store(id, seq, "STOPPED")
		return "STOPPED"
	}
	if seq > r.hw {
		r.hw = seq + r.block // claim mark, durable before the effect
	}
	r.effects[id]++
	r.total += v
	r.level += v
	r.store(id, seq, "APPLIED")
	return "APPLIED"
}

func (r *fwRig) fwQuery(id string, seq uint64) string {
	if p, ok := r.ring[id]; ok {
		return p.outcome
	}
	if seq <= r.evicted {
		return "UNCERTAIN"
	}
	if seq <= r.cutoff {
		return "STOPPED"
	}
	return "NOT_FOUND"
}

// reboot: cutoff and claim mark survive; ring, level and owner version do not; the floor is the claim mark. The
// boot-report firmware's hello tells the gateway the new floor at once.
func (r *fwRig) reboot() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.floor, r.evicted = r.hw, r.hw
	r.ring, r.order, r.level, r.ov = map[string]fwRcpt{}, nil, 0, 0
	r.reboots++
	if r.reportFloor && r.floor > r.gwFloor {
		r.gwFloor = r.floor
	}
}

// ---- gateway ----
func (r *fwRig) seen() {
	if r.reportFloor && r.floor > r.gwFloor {
		r.gwFloor = r.floor
	}
}

func (r *fwRig) claim(m map[string]any) map[string]any {
	if r.reportFloor {
		m["claim_floor"] = r.gwFloor
	}
	return m
}

func (r *fwRig) receipt(id, outcome string, level int64) protocol.Receipt {
	f := r.fwd[id]
	rc := protocol.Receipt{SchemaVersion: 1, DomainID: "dose-1", Generation: 1, CommandID: id, IntentHash: f.intent, Outcome: outcome, Reason: "FIRMWARE"}
	if outcome == "APPLIED" || outcome == "STOPPED" {
		rc.OwnerVersion, rc.ReceiptVersion, rc.State = r.ov+1, r.ov+1, []int64{level}
	}
	if r.reportFloor {
		fl := r.gwFloor
		rc.ClaimFloor = &fl
	}
	return rc
}

func (r *fwRig) take(id, outcome string) {
	if outcome == "APPLIED" || outcome == "STOPPED" {
		if _, ok := r.cache[id]; !ok {
			r.cache[id] = r.receipt(id, outcome, r.level)
		}
	}
}

func (r *fwRig) effect(w http.ResponseWriter, req *http.Request) {
	var cr protocol.CommandRequest
	_ = json.NewDecoder(req.Body).Decode(&cr)
	r.mu.Lock()
	defer r.mu.Unlock()
	if c, ok := r.cache[cr.CommandID]; ok && c.Reason == "GATEWAY_SEALED_BELOW_FLOOR" {
		writeJSON(w, 200, c)
		return
	}
	if _, fw := r.fwd[cr.CommandID]; !fw && r.recoveryGW && r.reportFloor && cr.Sequence <= r.gwFloor {
		// GW-3: never forwarded, at or below the floor: sealed (durably, in the gateway's record) and never forwarded
		r.fwd[cr.CommandID] = fwHeld{id: cr.CommandID, seq: cr.Sequence, value: cr.Value[0], intent: cr.IntentHash}
		rc := r.receipt(cr.CommandID, "STOPPED", 0)
		rc.Reason = "GATEWAY_SEALED_BELOW_FLOOR"
		r.cache[cr.CommandID] = rc
		writeJSON(w, 200, rc)
		return
	}
	if f, ok := r.fwd[cr.CommandID]; ok && f.seq != cr.Sequence {
		writeJSON(w, 409, map[string]any{"error": "IDENTITY_CONFLICT"})
		return
	}
	r.fwd[cr.CommandID] = fwHeld{id: cr.CommandID, seq: cr.Sequence, value: cr.Value[0], intent: cr.IntentHash}
	fate := 100
	if r.lossy {
		fate = r.rng.Intn(100)
	}
	if r.down || fate < 10 { // lost on the way (or the firmware is down and the session is not resumed: lost)
		writeJSON(w, 504, r.claim(map[string]any{"error": "OWNER_TIMEOUT"}))
		return
	}
	if r.holdNext || fate < 20 { // held: delivered late, possibly after a reboot
		r.holdNext = false
		r.held = append(r.held, fwHeld{id: cr.CommandID, seq: cr.Sequence, value: cr.Value[0], intent: cr.IntentHash})
		writeJSON(w, 504, r.claim(map[string]any{"error": "OWNER_TIMEOUT"}))
		return
	}
	out := r.fwCommand(cr.CommandID, cr.Sequence, cr.Value[0])
	r.seen()
	r.take(cr.CommandID, out)
	if fate < 30 { // the reply to the authority is lost (the gateway has it)
		writeJSON(w, 504, r.claim(map[string]any{"error": "OWNER_TIMEOUT"}))
		return
	}
	if out == "UNCERTAIN" {
		writeJSON(w, 503, r.claim(map[string]any{"error": "OWNER_UNCERTAIN", "detail": "RECEIPT_MAY_BE_EVICTED"}))
		return
	}
	writeJSON(w, 200, r.cache[cr.CommandID])
}

func (r *fwRig) query(w http.ResponseWriter, req *http.Request) {
	id := strings.TrimPrefix(req.URL.Path, "/v1/receipts/")
	r.mu.Lock()
	defer r.mu.Unlock()
	f, ok := r.fwd[id]
	if !ok {
		writeJSON(w, 404, r.claim(map[string]any{"error": "NOT_FOUND"}))
		return
	}
	if c, ok := r.cache[id]; ok {
		writeJSON(w, 200, c)
		return
	}
	out := r.fwQuery(id, f.seq)
	r.seen()
	r.take(id, out)
	switch out {
	case "APPLIED", "STOPPED":
		writeJSON(w, 200, r.cache[id])
	case "NOT_FOUND":
		writeJSON(w, 404, r.claim(map[string]any{"error": "NOT_FOUND"}))
	default:
		writeJSON(w, 200, r.receipt(id, "UNCERTAIN", 0))
	}
}

func (r *fwRig) barrier(w http.ResponseWriter, req *http.Request) {
	var br protocol.BarrierRequest
	_ = json.NewDecoder(req.Body).Decode(&br)
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.down {
		writeJSON(w, 504, r.claim(map[string]any{"error": "OWNER_TIMEOUT"}))
		return
	}
	if br.CutoffSequence > r.cutoff {
		r.cutoff = br.CutoffSequence // durable before the answer
	}
	r.ov++
	r.seen()
	ids := []string{}
	for id, f := range r.fwd {
		if f.seq <= br.CutoffSequence {
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	rc := protocol.BarrierReceipt{SchemaVersion: 1, DomainID: br.DomainID, Generation: br.Generation, Token: br.Token, CutoffSequence: br.CutoffSequence,
		OwnerVersion: r.ov, State: []int64{r.level}, Reason: "FIRMWARE_CUTOFF_INSTALLED"}
	for _, id := range ids {
		if c, ok := r.cache[id]; ok {
			rc.Receipts = append(rc.Receipts, c)
			continue
		}
		out := r.fwQuery(id, r.fwd[id].seq)
		r.take(id, out)
		if c, ok := r.cache[id]; ok {
			rc.Receipts = append(rc.Receipts, c)
		} else {
			rc.Receipts = append(rc.Receipts, r.receipt(id, "UNCERTAIN", 0))
		}
	}
	if r.reportFloor {
		fl := r.floor
		rc.ClaimFloor = &fl
	}
	writeJSON(w, 200, rc)
}

func (r *fwRig) deliverHeld(n int) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.down {
		return
	}
	if n > len(r.held) {
		n = len(r.held)
	}
	for _, h := range r.held[:n] {
		out := r.fwCommand(h.id, h.seq, h.value) // a late copy; its reply reaches the gateway
		r.seen()
		r.take(h.id, out)
	}
	r.held = r.held[n:]
}

func fwContract() contract.EvidenceContract {
	return contract.EvidenceContract{SchemaVersion: 1, ID: "test-zephyr-boot+only-F", System: "test: boot-report firmware model", Source: contract.SourceMeasured,
		Capabilities: []contract.EvidenceCapability{contract.CapFence}, Provenance: map[string]string{"F": "P9F"},
		FaultModel: "network loss/delay, client abandon; owner up (no owner crash or reboot)"}
}

type recOpts struct {
	recovery bool
	anchor   string
	bound    int64
	maxOut   int
	trace    *trace.Writer
}

func recAuthority(t *testing.T, r *fwRig, o recOpts) (*Authority, *journal.Store, *outbox.Dispatcher, string) {
	t.Helper()
	d := journal.Domain{ID: "dose-1", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{o.bound}, Anchor: []int64{0}, ContractHash: "rec-087"}
	dbp := filepath.Join(t.TempDir(), "authority.db")
	st, err := journal.Open(dbp)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	ec := fwContract()
	disp := outbox.New(st, time.Hour, nil)
	disp.RetryAllowed = RetryAllowedFunc(st, ec, -1)
	a, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, EndpointURL: r.srv.URL, Profile: "DEVICE", Policy: probe.EVICON, Dispatcher: disp, Trace: o.trace,
		ProbeBudget: 4, MaxOutstanding: o.maxOut, MaxConfigurations: 256, PlannerComputeBudget: 50 * time.Millisecond, EvidenceContract: ec,
		AmbiguityAfter: time.Millisecond, FenceIntake: FenceIntakePerIdentity, FenceAnchor: o.anchor, RebootRecovery: o.recovery,
		ResolvePolicy: resolve.Policy{Mode: resolve.ModeImmediate, QueryFirst: true}, ResolveCosts: resolve.Costs{HoldPerUnitPerSec: 1, QueryCost: 0.01, StopCost: 0.01, FenceCost: 0.01}})
	if err != nil {
		t.Fatal(err)
	}
	return a, st, disp, dbp
}

// submit sends a new intent under a new identity with the client's next sequence; it returns the decision.
func submit(t *testing.T, a *Authority, disp *outbox.Dispatcher, id string, v int64, clientSeq uint64) string {
	t.Helper()
	_, out := invokeCandidate(t, a, dose(t, id, v, clientSeq))
	_ = disp.DispatchOnce(context.Background())
	return out.Decision
}

func TestRebootRecoveryDefaultOff087(t *testing.T) {
	r := newFWRig(t, rand.New(rand.NewSource(1)), 4, 32, true, false, false)
	a, st, disp, dbp := recAuthority(t, r, recOpts{bound: 20, maxOut: 6})
	if a.cfg.RebootRecovery || disp.OwnerReport != nil {
		t.Fatal("the recovery must be off by default")
	}
	r.hw, r.floor, r.gwFloor = 40, 40, 40 // an owner that reports a floor: ignored without the flag
	if dec := submit(t, a, disp, "c1", 1, 1); dec != "ADMITTED" {
		t.Fatal(dec)
	}
	c, err := st.GetCommand("dose-1", "c1")
	if err != nil || c.Sequence != 1 {
		t.Fatalf("the client's sequence must be stored as sent: %+v %v", c, err)
	}
	db, err := sqlitecgo.OpenReadOnly(dbp)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	rows, err := db.Query(`SELECT name FROM sqlite_master WHERE name IN ('owner_floor','client_sequences')`)
	if err != nil || len(rows) != 0 {
		t.Fatalf("without the recovery the journal keeps the 086 schema: %v %v", rows, err)
	}
}

func TestRecSequenceAdvance087(t *testing.T) {
	r := newFWRig(t, rand.New(rand.NewSource(2)), 4, 32, true, true, false)
	tw, tp := openTrace(t)
	a, st, disp, _ := recAuthority(t, r, recOpts{recovery: true, bound: 100, maxOut: 6, trace: tw})
	if dec := submit(t, a, disp, "c1", 1, 1); dec != "ADMITTED" {
		t.Fatal(dec)
	}
	a.noteOwnerFloor("dose-1", 50, "TEST")
	if dec := submit(t, a, disp, "c2", 1, 2); dec != "ADMITTED" {
		t.Fatal(dec)
	}
	if dec := submit(t, a, disp, "c3", 1, 3); dec != "ADMITTED" {
		t.Fatal(dec)
	}
	for id, want := range map[string]uint64{"c1": 1, "c2": 51, "c3": 52} {
		c, err := st.GetCommand("dose-1", id)
		if err != nil || c.Sequence != want {
			t.Fatalf("%s: owner sequence %d (%v), want %d", id, c.Sequence, err, want)
		}
		h := dose(t, id, 1, want)
		if c.IntentHash != h.IntentHash {
			t.Fatalf("%s: the intent hash must be computed over the owner sequence", id)
		}
	}
	// a replay of c2's request (client sequence 2) is the same intent
	if _, out := invokeCandidate(t, a, dose(t, "c2", 1, 2)); out.Reason != "IDEMPOTENT_REPLAY" {
		t.Fatalf("replay: %+v", out)
	}
	// an out-of-order client sequence for a new identity is refused
	if _, out := invokeCandidate(t, a, dose(t, "c4", 1, 2)); out.Decision != "REJECT" || out.Reason != "SEQUENCE_NOT_MONOTONIC" {
		t.Fatalf("stale client sequence: %+v", out)
	}
	// a lower floor report is ignored; the offset never falls
	a.noteOwnerFloor("dose-1", 10, "TEST")
	if dec := submit(t, a, disp, "c5", 1, 4); dec != "ADMITTED" {
		t.Fatal(dec)
	}
	if c, _ := st.GetCommand("dose-1", "c5"); c.Sequence != 53 {
		t.Fatalf("c5: %d, want 53", c.Sequence)
	}
	ev := traceEvents(t, tp)
	adv, fl := 0, 0
	for _, e := range ev {
		switch e.Event {
		case "SEQUENCE_ADVANCED":
			adv++
		case "OWNER_FLOOR":
			fl++
		}
	}
	if adv != 1 || fl != 2 { // OWNER_FLOOR: the first report (floor 0, from c1's reply) and the raise to 50
		t.Fatalf("SEQUENCE_ADVANCED %d (want 1), OWNER_FLOOR %d (want 2)", adv, fl)
	}
}

// recScenario: four doses (5, 5, 5, 4 = 19 of a budget of 30) applied; a reboot (level 0, claim floor 9); the client's
// next identity (inside the claimed range); a 1-unit command whose request is lost (ambiguous: the sweep fences);
// eight candidates of 2 units.
func recScenario(t *testing.T, recovery bool, anchor string) (r *fwRig, ev []trace.Event, admittedAfter int, st *journal.Store) {
	t.Helper()
	r = newFWRig(t, rand.New(rand.NewSource(3)), 8, 32, true, recovery, false)
	tw, tp := openTrace(t)
	a, st, disp, _ := recAuthority(t, r, recOpts{recovery: recovery, anchor: anchor, bound: 30, maxOut: 6, trace: tw})
	seq := uint64(0)
	for i, v := range []int64{5, 5, 5, 4} {
		seq++
		if dec := submit(t, a, disp, fmt.Sprintf("d%d", i+1), v, seq); dec != "ADMITTED" {
			t.Fatalf("d%d: %s", i+1, dec)
		}
	}
	if r.total != 19 {
		t.Fatalf("plant %d", r.total)
	}
	r.reboot()
	// the next identity: under the recovery the gateway seals it (it lies in the claimed range) and the authority
	// learns the floor from that answer; the client then sends the intent again under a new identity, now above
	// the floor. Without the recovery the firmware refuses it (UNCERTAIN, never executed).
	seq++
	_ = submit(t, a, disp, "x1", 1, seq)
	if c, _ := st.GetCommand("dose-1", "x1"); c.Status == "STOPPED" {
		seq++
		_ = submit(t, a, disp, "x1-r1", 1, seq)
	}
	// a 1-unit command whose request is lost: ambiguous, so the next candidate is blocked and the resolver fences
	r.mu.Lock()
	r.down = true
	r.mu.Unlock()
	seq++
	_ = submit(t, a, disp, "lost", 1, seq)
	r.mu.Lock()
	r.down = false
	r.mu.Unlock()
	for i := 0; i < 8; i++ {
		seq++
		id := fmt.Sprintf("c%d", i)
		dec := submit(t, a, disp, id, 2, seq)
		_ = a.SweepOnce()
		_ = disp.DispatchOnce(context.Background())
		if dec == "ADMITTED" {
			admittedAfter++
		}
	}
	return r, traceEvents(t, tp), admittedAfter, st
}

func outcomes(ev []trace.Event, name string) []string {
	out := []string{}
	for _, e := range ev {
		if e.Event == name {
			out = append(out, e.Outcome)
		}
	}
	return out
}

func TestRecFloorCheck087(t *testing.T) {
	// release 087: the recovery commits the evidence anchor after the reboot and the bound holds
	r, ev, _, st := recScenario(t, true, FenceAnchorEvidence)
	if r.total > 30 {
		t.Fatalf("recovery: the plant exceeded the budget: %d", r.total)
	}
	checks := outcomes(ev, "FENCE_ANCHOR_CHECK")
	if len(checks) == 0 || checks[0] != "MATCH_FLOOR" {
		t.Fatalf("recovery: the first fence after the reboot must be MATCH_FLOOR: %v", checks)
	}
	for _, c := range checks {
		if strings.HasPrefix(c, "MISMATCH") {
			t.Fatalf("recovery: %v", checks)
		}
	}
	d, _ := st.GetDomain("dose-1")
	if d.CutoffSequence == 0 || d.Anchor[0] < 19 {
		t.Fatalf("recovery: the evidence anchor (>= 19) must be committed, got %v at cutoff %d", d.Anchor, d.CutoffSequence)
	}
	if len(outcomes(ev, "OWNER_FLOOR")) == 0 || len(outcomes(ev, "SEQUENCE_ADVANCED")) == 0 {
		t.Fatal("recovery: the floor must be learned and the sequence advanced")
	}
	// release 086 (no recovery): the same reboot strands the new identity; the plant stays within the budget
	r, ev, _, _ = recScenario(t, false, FenceAnchorEvidence)
	if r.total > 30 {
		t.Fatalf("086: the plant exceeded the budget: %d", r.total)
	}
	for _, c := range outcomes(ev, "FENCE_ANCHOR_CHECK") {
		if c == "MATCH_FLOOR" || c == "MATCH" {
			t.Fatalf("086: no fence after the reboot may anchor: %v", outcomes(ev, "FENCE_ANCHOR_CHECK"))
		}
	}
	// negative control: owner-state anchoring with the recovery adopts the reset level and exceeds the budget
	r, ev, _, _ = recScenario(t, true, FenceAnchorOwnerState)
	if r.total <= 30 {
		t.Fatalf("owner-state with the recovery: the plant must exceed the budget (negative control), got %d; anchor checks %v",
			r.total, outcomes(ev, "FENCE_ANCHOR_CHECK"))
	}
	adopted := false
	for _, c := range outcomes(ev, "FENCE_ANCHOR_CHECK") {
		adopted = adopted || c == "MISMATCH_OWNER_STATE_ADOPTED"
	}
	if !adopted {
		t.Fatalf("owner-state with the recovery: the adoption of the reset level must be traced: %v", outcomes(ev, "FENCE_ANCHOR_CHECK"))
	}
}

func TestRecWindow087(t *testing.T) {
	for _, recovery := range []bool{false, true} {
		r := newFWRig(t, rand.New(rand.NewSource(4)), 4, 32, true, recovery, false)
		a, st, disp, _ := recAuthority(t, r, recOpts{recovery: recovery, anchor: FenceAnchorEvidence, bound: 30, maxOut: 1})
		if dec := submit(t, a, disp, "a1", 3, 1); dec != "ADMITTED" {
			t.Fatal(dec)
		}
		// h1's request is on the way when the firmware reboots; delivered after the reboot it is refused (UNCERTAIN
		// for good: inside the claimed range, never executed, never certified)
		r.mu.Lock()
		r.holdNext = true
		r.mu.Unlock()
		if dec := submit(t, a, disp, "h1", 5, 2); dec != "ADMITTED" {
			t.Fatal(dec)
		}
		r.reboot()
		r.deliverHeld(1 << 10)
		for i := 0; i < 3; i++ {
			_ = a.SweepOnce() // a fence: h1 listed UNCERTAIN (rule R5')
		}
		c, _ := st.GetCommand("dose-1", "h1")
		if c.Status != "PENDING" || !a.isFenceUncertain("h1") || r.effects["h1"] != 0 {
			t.Fatalf("recovery=%v: h1 must stay pending, fence-uncertain and without effect: %s %v %d", recovery, c.Status, a.isFenceUncertain("h1"), r.effects["h1"])
		}
		d, _ := st.GetDomain("dose-1")
		res, err := a.evaluateCandidate(d, dose(t, "probe", 1, 1000))
		if err != nil {
			t.Fatal(err)
		}
		windowFull := res.Reason == "OUTSTANDING_BUDGET_EXHAUSTED"
		if recovery == windowFull {
			t.Fatalf("recovery=%v: REC-4 window verdict %+v", recovery, res)
		}
		if recovery {
			// REC-4 keeps the budget: 3 applied + h1's 5 reserved + 23 > 30 must be blocked, 22 fits
			res, err := a.evaluateCandidate(d, dose(t, "big", 23, 1001))
			if err != nil {
				t.Fatal(err)
			}
			if !blockedVerdict(res) {
				t.Fatalf("REC-4 must keep h1's reservation: a candidate of 23 was %+v", res)
			}
			res, err = a.evaluateCandidate(d, dose(t, "fits", 22, 1002))
			if err != nil || res.Verdict != analyzerSafe {
				t.Fatalf("a candidate of 22 fits with h1 reserved: %+v %v", res, err)
			}
		}
	}
}

type recOutcome struct {
	violations, falseStopped, falseApplied, duplicates int
	admittedAfterReboot, reboots                       int
	fenceUncertain                                     int // identities a complete fence listed UNCERTAIN (REC-4 leaves them out of the window)
}

func runRecExecution(t *testing.T, seed int64, recovery bool, anchor string) recOutcome {
	t.Helper()
	rng := rand.New(rand.NewSource(seed))
	// the claim block exceeds the window, as on the firmware (64 against W = 6)
	r := newFWRig(t, rng, uint64(8+rng.Intn(5)), 4+rng.Intn(8), true, recovery, true)
	bound := int64(12 + rng.Intn(10))
	a, st, disp, _ := recAuthority(t, r, recOpts{recovery: recovery, anchor: anchor, bound: bound, maxOut: 3 + rng.Intn(4)})
	steps := 40 + rng.Intn(20)
	rb1 := 5 + rng.Intn(10)
	rb2 := -1
	if rng.Intn(2) == 0 {
		rb2 = rb1 + 8 + rng.Intn(15)
	}
	var seq uint64
	ids := []string{}
	var o recOutcome
	for i := 0; i < steps; i++ {
		if i == rb1 || i == rb2 {
			if rng.Intn(2) == 0 {
				// a reboot in a quiet moment: every copy on the way delivered and the domain resolved first
				r.deliverHeld(1 << 20)
				for k := 0; k < 3; k++ {
					_ = disp.DispatchOnce(context.Background())
					_ = a.SweepOnce()
				}
			}
			r.reboot()
			o.reboots++
		}
		switch k := rng.Intn(10); {
		case k < 6:
			seq++
			id := fmt.Sprintf("q%d", seq)
			if submit(t, a, disp, id, int64(1+rng.Intn(3)), seq) == "ADMITTED" {
				ids = append(ids, id)
				if o.reboots > 0 {
					o.admittedAfterReboot++
				}
			}
		case k < 8:
			_ = a.SweepOnce()
			_ = disp.DispatchOnce(context.Background())
		default:
			r.deliverHeld(1 + rng.Intn(2))
		}
	}
	r.deliverHeld(1 << 20)
	for i := 0; i < 3; i++ {
		_ = disp.DispatchOnce(context.Background())
		_ = a.SweepOnce()
	}
	a.queriedMu.Lock()
	o.fenceUncertain = len(a.fenceUncertain)
	a.queriedMu.Unlock()
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.total > bound {
		o.violations++
	}
	for _, id := range ids {
		c, err := st.GetCommand("dose-1", id)
		if err != nil {
			t.Fatal(err)
		}
		eff := r.effects[id]
		if c.Status == "STOPPED" && eff > 0 {
			o.falseStopped++
		}
		if c.Status == "APPLIED" && eff == 0 {
			o.falseApplied++
		}
		if eff > 1 {
			o.duplicates++
		}
	}
	return o
}

func TestRebootRecoveryProperty087(t *testing.T) {
	n := 60
	if testing.Short() {
		n = 20
	}
	type agg struct{ execs, violating, falseStopped, falseApplied, duplicates, admittedAfter, withFenceUncertain int }
	res := map[string]*agg{}
	for _, m := range []struct {
		name     string
		recovery bool
		anchor   string
	}{{"recovery", true, FenceAnchorEvidence}, {"086", false, FenceAnchorEvidence}, {"owner-state+recovery", true, FenceAnchorOwnerState}} {
		g := &agg{}
		res[m.name] = g
		for s := 0; s < n; s++ {
			o := runRecExecution(t, int64(8700+s), m.recovery, m.anchor)
			g.execs++
			if o.violations > 0 {
				g.violating++
			}
			g.falseStopped += o.falseStopped
			g.falseApplied += o.falseApplied
			g.duplicates += o.duplicates
			g.admittedAfter += o.admittedAfterReboot
			if o.fenceUncertain > 0 {
				g.withFenceUncertain++
			}
		}
		t.Logf("%s: %d executions, %d with a bound violation, false STOPPED %d, false APPLIED %d, duplicates %d, admissions after a reboot %d, "+
			"executions with an identity a complete fence listed UNCERTAIN (REC-4 active under the recovery) %d",
			m.name, g.execs, g.violating, g.falseStopped, g.falseApplied, g.duplicates, g.admittedAfter, g.withFenceUncertain)
	}
	for _, name := range []string{"recovery", "086"} {
		g := res[name]
		if g.violating+g.falseStopped+g.falseApplied+g.duplicates != 0 {
			t.Fatalf("%s must be safe: %+v", name, *g)
		}
	}
	if g := res["owner-state+recovery"]; g.violating == 0 {
		t.Fatalf("negative control: owner-state anchoring with the recovery must violate the bound in some execution: %+v", *g)
	}
	if res["recovery"].withFenceUncertain == 0 {
		t.Fatal("REC-4 must be exercised: no execution of the recovery had an identity listed UNCERTAIN by a complete fence")
	}
	if res["recovery"].admittedAfter <= res["086"].admittedAfter {
		t.Fatalf("the recovery must admit more after reboots than release 086: %d vs %d", res["recovery"].admittedAfter, res["086"].admittedAfter)
	}
}

const analyzerSafe = "SAFE"

package sim

import (
	"sort"

	"evicon/internal/contract"
	"evicon/internal/domain"
)

// ownerRecord is the owner's per-identity record (claim / receipt / tombstone).
type ownerRecord struct {
	key        string
	seq        uint64
	status     string // CLAIMED (effect, receipt lost in crash), APPLIED, STOPPED
	effect     bool
	firstSeen  int64
	logicalReq int
}

// owner is the TRUTH effect owner. Authorities never read it directly; they
// only see messages it sends back over the simulated network.
type owner struct {
	c        contract.EvidenceContract
	spec     OwnerSpec
	bound    domain.HardBound
	state    domain.State
	frontier uint64 // SET frontier
	cutoff   uint64 // fence cutoff (F)
	records  map[string]*ownerRecord
	tombs    map[string]bool
	downTill int64 // crash recovery (us)
	busyTill int64 // sequential processing (us)
	queue    []*message
	schedule []interval // online intervals (us); nil = always online
}

type interval struct{ start, end int64 }

func newOwner(c contract.EvidenceContract, spec OwnerSpec, w WorkloadSpec, schedule []interval) *owner {
	b := domain.HardBound{ComponentMin: w.ComponentMin, ComponentMax: w.ComponentMax, SumMax: w.SumMax}
	return &owner{c: c, spec: spec, bound: b, state: domain.State(append([]int64(nil), w.Initial...)),
		records: map[string]*ownerRecord{}, tombs: map[string]bool{}, schedule: schedule}
}

func (o *owner) tracksIDs() bool {
	return o.c.Has(contract.CapDedup) || o.c.Has(contract.CapQueryApplied) || o.c.Has(contract.CapStop) || o.c.Has(contract.CapFence) || o.c.Has(contract.CapAtomic)
}

// online reports owner availability (device schedule and crash recovery).
func (o *owner) online(t int64) bool {
	if t < o.downTill {
		return false
	}
	if o.schedule == nil {
		return true
	}
	i := sort.Search(len(o.schedule), func(i int) bool { return o.schedule[i].end > t })
	return i < len(o.schedule) && o.schedule[i].start <= t
}

// nextOnline returns the earliest time >= t at which the owner is online.
func (o *owner) nextOnline(t int64) int64 {
	if t < o.downTill {
		t = o.downTill
	}
	if o.schedule == nil {
		return t
	}
	i := sort.Search(len(o.schedule), func(i int) bool { return o.schedule[i].end > t })
	if i >= len(o.schedule) {
		return -1
	}
	if o.schedule[i].start > t {
		return o.schedule[i].start
	}
	return t
}

// lookupDedup returns the record only while it is inside the dedup horizon.
// Outside a finite horizon the dedup cache has forgotten the identity and a
// late duplicate executes again. The durable effect/receipt record itself is
// kept for queries, stop and fence (owners keep receipts longer than caches).
func (o *owner) lookupDedup(key string, t int64) *ownerRecord {
	r := o.records[key]
	if r == nil {
		return nil
	}
	if o.c.Has(contract.CapDedup) && o.c.DedupHorizonMS != contract.UnboundedHorizonMS {
		if t-r.firstSeen > o.c.DedupHorizonMS*1000 {
			return nil
		}
	}
	return r
}

type ownerResult struct {
	outcome   string // APPLIED, STOPPED, UNCERTAIN, NOT_FOUND, NONE (no reply)
	reason    string
	effect    bool
	crashed   bool
	duplicate bool
	cancelled bool // stopped only because of a cutoff/tombstone (would otherwise have run)
}

// command processes one command delivery at time t. f carries the attempt's
// pre-sampled fates (terminal reject, crash).
func (o *owner) command(m *message, t int64, f fates) ownerResult {
	sealed := o.tombs[m.ownerKey] || (o.c.Has(contract.CapFence) && m.seq <= o.cutoff)
	if r := o.records[m.ownerKey]; r != nil && sealed {
		// A known identity always answers with its receipt; the cutoff and
		// tombstones only block identities that never took effect. A crash
		// orphan (effect, receipt lost) is UNCERTAIN unless the owner can
		// reconcile it (OrphanReconcile).
		if r.status == "CLAIMED" && !o.spec.OrphanReconcile {
			return ownerResult{outcome: "UNCERTAIN", reason: "ORPHAN_CLAIM_SEALED"}
		}
		if r.effect {
			r.status = "APPLIED"
			return ownerResult{outcome: "APPLIED", reason: "RECEIPT_REPLAY_AFTER_CUTOFF"}
		}
		return ownerResult{outcome: "STOPPED", reason: "SEALED"}
	}
	if sealed {
		if o.tracksIDs() && o.records[m.ownerKey] == nil {
			o.records[m.ownerKey] = &ownerRecord{key: m.ownerKey, seq: m.seq, status: "STOPPED", firstSeen: t, logicalReq: m.req}
		}
		return ownerResult{outcome: "STOPPED", reason: "SEALED", cancelled: true}
	}
	if o.c.Has(contract.CapDedup) {
		if r := o.lookupDedup(m.ownerKey, t); r != nil {
			switch r.status {
			case "APPLIED", "STOPPED":
				return ownerResult{outcome: r.status, reason: "DEDUP_REPLAY"}
			default:
				return ownerResult{outcome: "UNCERTAIN", reason: "CLAIM_WITHOUT_RECEIPT"}
			}
		}
	}
	// Release 072: the expiry is an absolute deadline carried by the request
	// and fixed at the identity's first send (as evicon-authority's
	// ExpiresAtMS); before 072 each resend restarted the window, which made
	// rule (A4) unsound for resent identities (paper, Lemma adm).
	anchor := m.sendTime
	if m.att != nil && m.att.firstSend > 0 && m.att.firstSend < anchor {
		anchor = m.att.firstSend
	}
	if o.c.Has(contract.CapExpiry) && t > anchor+o.c.ExpiryMS*1000 {
		o.record(m, t, "STOPPED", false)
		return ownerResult{outcome: "STOPPED", reason: "OWNER_EXPIRED", cancelled: true}
	}
	if f.reject {
		o.record(m, t, "STOPPED", false)
		return ownerResult{outcome: "STOPPED", reason: "TERMINAL_REJECT"}
	}
	var next domain.State
	var err error
	if m.op == domain.OpSet && m.seq <= o.frontier {
		o.record(m, t, "STOPPED", false)
		return ownerResult{outcome: "STOPPED", reason: "STALE_SET"}
	}
	next, err = domain.Apply(o.state, domain.Command{ID: m.ownerKey, Operation: m.op, Value: m.value})
	if err != nil {
		o.record(m, t, "STOPPED", false)
		return ownerResult{outcome: "STOPPED", reason: "OVERFLOW"}
	}
	if o.spec.EnforcesBound {
		if ok, _ := o.bound.Check(next); !ok {
			o.record(m, t, "STOPPED", false)
			return ownerResult{outcome: "STOPPED", reason: "ENDPOINT_BOUND_REJECT"}
		}
	}
	dup := false
	if r := o.records[m.ownerKey]; r != nil && r.effect {
		dup = true
	}
	o.state = next
	if m.op == domain.OpSet {
		o.frontier = m.seq
	}
	if f.crashMid && !o.c.Has(contract.CapAtomic) {
		o.record(m, t, "CLAIMED", true)
		o.downTill = t + o.spec.CrashRecoveryMS*1000
		return ownerResult{outcome: "NONE", reason: "CRASH_AFTER_EFFECT_BEFORE_RECEIPT", effect: true, crashed: true, duplicate: dup}
	}
	o.record(m, t, "APPLIED", true)
	return ownerResult{outcome: "APPLIED", reason: "EFFECT", effect: true, duplicate: dup}
}

func (o *owner) record(m *message, t int64, status string, effect bool) {
	if !o.tracksIDs() {
		return
	}
	r := o.records[m.ownerKey]
	if r == nil {
		r = &ownerRecord{key: m.ownerKey, seq: m.seq, firstSeen: t, logicalReq: m.req}
		o.records[m.ownerKey] = r
	}
	r.status = status
	r.effect = r.effect || effect
}

func (o *owner) query(key string, t int64) string {
	r := o.records[key]
	if r == nil {
		if o.tombs[key] {
			return "STOPPED"
		}
		return "NOT_FOUND"
	}
	// A tombstone blocks the future of an identity; it never overrides what the
	// record says about its past (a crash orphan stays UNCERTAIN).
	switch r.status {
	case "APPLIED", "STOPPED":
		return r.status
	case "CLAIMED":
		if o.spec.OrphanReconcile && r.effect {
			return "APPLIED"
		}
		return "UNCERTAIN"
	default:
		return "UNCERTAIN"
	}
}

// stop is the per-identity Q_S operation, serialized with effects.
func (o *owner) stop(key string, seq uint64, req int, t int64) string {
	if r := o.records[key]; r != nil {
		if r.status == "CLAIMED" && !o.spec.OrphanReconcile {
			o.tombs[key] = true // the future is blocked; the past stays unknown
			return "UNCERTAIN"
		}
		if r.effect {
			r.status = "APPLIED"
			return "APPLIED"
		}
		r.status = "STOPPED"
		o.tombs[key] = true
		return "STOPPED"
	}
	o.tombs[key] = true
	o.records[key] = &ownerRecord{key: key, seq: seq, status: "STOPPED", firstSeen: t, logicalReq: req}
	return "STOPPED"
}

// fence installs cutoff w, reconciles every record <= w, and returns receipts.
func (o *owner) fence(w uint64) map[string]string {
	if w > o.cutoff {
		o.cutoff = w
	}
	out := map[string]string{}
	for k, r := range o.records {
		if r.seq > w {
			continue
		}
		if r.status == "CLAIMED" && !o.spec.OrphanReconcile {
			out[k] = "UNCERTAIN" // reported, never sealed as STOPPED
			continue
		}
		if r.effect {
			r.status = "APPLIED"
		} else if r.status != "STOPPED" {
			r.status = "STOPPED"
		}
		out[k] = r.status
	}
	return out
}

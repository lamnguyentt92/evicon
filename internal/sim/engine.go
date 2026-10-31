package sim

import (
	"container/heap"
	"errors"
	"fmt"
	"math"
	"sort"
	"time"

	"evicon/internal/analyzer"
	"evicon/internal/belief"
	"evicon/internal/contract"
	"evicon/internal/domain"
	"evicon/internal/evidence"
	"evicon/internal/journal"
	"evicon/internal/probe"
	"evicon/internal/protocol"
	"evicon/internal/resolve"
)

// ---------------------------------------------------------------------------
// Episode inputs shared by every policy (common random numbers).

type request struct {
	idx      int
	arrival  int64 // us
	op       domain.Operation
	value    []int64
	deadline int64 // absolute us
	validity int64 // absolute us; 0 = none
}

type episodeInput struct {
	reqs     []request
	schedule []interval
	endUS    int64
}

func buildEpisode(cfg Config, ep int) episodeInput {
	r := crn{seed: cfg.Seed, episode: ep}
	w := cfg.Workload
	durUS := w.DurationMS * 1000
	var reqs []request
	t := 0.0
	period := 1000.0 / w.RatePerSec // ms
	for i := 0; ; i++ {
		var arr float64
		if w.Poisson {
			t += r.exp("arrival", i, 0, period)
			arr = t
		} else {
			arr = float64(i) * period
		}
		if ms(arr) >= durUS {
			break
		}
		op := domain.OpAdd
		if w.SetFraction > 0 && r.u("op", i, 0) < w.SetFraction {
			op = domain.OpSet
		}
		val := make([]int64, w.Dimension)
		for d := 0; d < w.Dimension; d++ {
			if op == domain.OpSet {
				val[d] = r.intn("setval", i, d, w.SetMin[d], w.SetMax[d])
			} else {
				val[d] = r.intn("val", i, d, w.ValueMin[d], w.ValueMax[d])
			}
		}
		rq := request{idx: i, arrival: ms(arr), op: op, value: val}
		rq.deadline = rq.arrival + w.DeadlineMS*1000
		if w.ValidityMS > 0 {
			rq.validity = rq.arrival + w.ValidityMS*1000
		}
		reqs = append(reqs, rq)
	}
	end := durUS + w.TailMS*1000
	var sched []interval
	o := cfg.Owner
	if o.OnlineMeanMS > 0 {
		cur := int64(0)
		for k := 0; cur < end+1000*1000*3600; k++ {
			on := ms(r.exp("online", k, 0, o.OnlineMeanMS))
			if on < 1000 {
				on = 1000
			}
			sched = append(sched, interval{cur, cur + on})
			off := ms(r.exp("offline", k, 0, o.OfflineMeanMS))
			if len(o.OfflineUniformMS) == 2 {
				lo, hi := o.OfflineUniformMS[0], o.OfflineUniformMS[1]
				off = ms(lo + (hi-lo)*r.u("offline-u", k, 0))
			}
			cur += on + off
			if len(sched) > 1_000_000 {
				break
			}
		}
	}
	return episodeInput{reqs: reqs, schedule: sched, endUS: end}
}

type fates struct {
	reqLost   bool
	reqDelay  int64
	reject    bool
	crashMid  bool
	respLost  bool
	respDelay int64
}

func (rn *run) fate(req, j int) fates {
	n := rn.cfg.Network
	r := rn.r
	f := fates{}
	u := r.u("req", req, j)
	f.reqDelay = ms(n.OneWayBaseMS + r.exp("reqjit", req, j, n.OneWayJitterMS))
	if u < n.ReqLossProb {
		f.reqLost = true
	} else if u < n.ReqLossProb+n.ReqLateProb {
		f.reqDelay += ms(r.exp("reqlate", req, j, n.ReqLateMeanMS))
	}
	// Terminal rejection is a property of the logical request (validation /
	// service rule), so a re-issue under a new identity is rejected again.
	f.reject = r.u("reject", req, 0) < rn.cfg.Owner.TerminalRejectProb
	f.crashMid = r.u("crash", req, j) < rn.cfg.Owner.CrashMidProb
	v := r.u("resp", req, j)
	f.respDelay = ms(n.OneWayBaseMS + r.exp("respjit", req, j, n.OneWayJitterMS))
	if v < n.RespLossProb {
		f.respLost = true
	} else if v < n.RespLossProb+n.RespHideProb {
		f.respDelay += n.RespHideMS * 1000
	}
	return f
}

// ---------------------------------------------------------------------------
// Events.

type evKind int

const (
	evArrival evKind = iota
	evOwnerDeliver
	evOwnerWake
	evCommandReply
	evQueryReply
	evStopReply
	evFenceReply
	evDispatchTimeout
	evControlTimeout
	evRetry
	evCancelAt
	evTick
	evOracleStopped
)

type msgKind int

const (
	mCommand msgKind = iota
	mQuery
	mStop
	mFence
	mCancel
)

type message struct {
	kind     msgKind
	att      *attempt
	ownerKey string
	seq      uint64
	op       domain.Operation
	value    []int64
	req      int
	sendTime int64
	fenceW   uint64
	ctl      *control
	f        fates
}

type event struct {
	t        int64
	order    int64
	kind     evKind
	msg      *message
	att      *attempt
	req      int
	outcome  string
	reason   string
	receipts map[string]string
	ctl      *control
	sent     int64 // command replies: send time of the request message they answer
}

type eventQueue []*event

func (q eventQueue) Len() int { return len(q) }
func (q eventQueue) Less(i, j int) bool {
	if q[i].t != q[j].t {
		return q[i].t < q[j].t
	}
	return q[i].order < q[j].order
}
func (q eventQueue) Swap(i, j int) { q[i], q[j] = q[j], q[i] }
func (q *eventQueue) Push(x any)   { *q = append(*q, x.(*event)) }
func (q *eventQueue) Pop() any {
	old := *q
	n := len(old)
	x := old[n-1]
	*q = old[:n-1]
	return x
}

// ---------------------------------------------------------------------------
// Authority-side state.

type attempt struct {
	key            string
	ownerKey       string
	req            int
	j              int
	seq            uint64
	op             domain.Operation
	value          []int64
	reserveAt      int64
	firstSend      int64
	lastSend       int64
	status         string // authority view
	released       bool   // authority marked STOPPED without admissible evidence (unsafe policies)
	terminalAt     int64
	ambiguousSince int64
	queriedAt      int64
	stopIssued     bool
	escalated      bool
	truthEffect    bool
	retries        int
	uncertain      int
}

type control struct {
	id       int
	kind     msgKind
	att      *attempt
	w        uint64
	done     bool
	sentAt   int64
	attempts int
}

type reqState struct {
	waiting      bool
	done         bool
	dropped      bool
	everBlocked  bool
	admitted     int
	effects      int
	firstEffect  int64
	late         bool
	confirmAt    int64
	attempts     int // fate index counter (sends of any attempt of this request)
	lastBlockWhy string
}

type run struct {
	cfg        Config
	policy     string
	ep         int
	r          crn
	c          contract.EvidenceContract
	in         episodeInput
	own        *owner
	q          eventQueue
	order      int64
	now        int64
	dom        journal.Domain
	cmds       []journal.CommandRecord
	cmdIdx     map[string]int
	atts       map[string]*attempt
	rs         []reqState
	waiting    []int
	nextSeq    uint64
	paused     bool
	pauseAt    int64
	ctlSeq     int
	ctls       map[int]*control
	wakeAt     int64
	m          Row
	maxDelayMS int64
	blockedReq int
	tickN      int64
	reuseKeys  map[int]string
	// performance: version-based caching (semantics unchanged)
	ver       uint64
	evalVer   uint64
	beliefVer uint64
	belief    belief.BeliefSet
	beliefErr error
	pending   map[string]*attempt
	compactAt int // index into cmds of the first non-compacted record
	certVer   uint64
	certReq   int
	certCache map[string]bool
	// resolver policy of this run (EVICON or a same-capability baseline)
	resPol       resolve.Policy
	nextPeriodic int64
}

func (rn *run) push(e *event) {
	rn.order++
	e.order = rn.order
	heap.Push(&rn.q, e)
}

func (rn *run) isOracle() bool { return rn.policy == PolicyOracle }
func (rn *run) isReplay() bool {
	switch rn.policy {
	case PolicyReplayM0, PolicyReplayM1, PolicyReplayM2, PolicyReplayM3, PolicyReplayM4:
		return true
	}
	return false
}

// isEvicon is true for EVICON and its same-capability baselines (RES_*): all
// resolve ambiguity with backward evidence under the cell's contract.
func (rn *run) isEvicon() bool { return IsResolver(rn.policy) }

// resolverPolicy fixes when/what each resolver policy acts on; everything
// else (contract, APIs, retry guard, reissue rule) is shared.
func resolverPolicy(policy string, a AuthoritySpec) resolve.Policy {
	switch policy {
	case PolicyResImmediate, PolicyResPeriodic:
		return resolve.Policy{Mode: resolve.ModeImmediate, QueryFirst: false}
	case PolicyResQueryFirst:
		return resolve.Policy{Mode: resolve.ModeImmediate, QueryFirst: true}
	case PolicyResFixed:
		return resolve.Policy{Mode: resolve.ModeFixed, FixedMS: a.FixedThresholdMS, QueryFirst: true}
	default:
		return a.Resolve
	}
}
func (rn *run) horizonAware() bool {
	return rn.isEvicon() || rn.policy == PolicyB2Ledger || rn.policy == PolicyB5SelectorM4 || rn.isReplay() || rn.isQueryOnly()
}

// isQueryOnly (release 083): QUERY_ONLY and QUERY_ONLY_W.
func (rn *run) isQueryOnly() bool {
	return rn.policy == PolicyQueryOnly || rn.policy == PolicyQueryOnlyW
}

// ---------------------------------------------------------------------------
// Running one (episode, policy).

func runEpisode(cfg Config, base contract.EvidenceContract, policy string, ep int, in episodeInput) (Row, error) {
	c := base // the contract is a property of the cell, never of the policy (review RV07)
	rn := &run{cfg: cfg, policy: policy, ep: ep, r: crn{seed: cfg.Seed, episode: ep}, c: c, in: in,
		cmdIdx: map[string]int{}, atts: map[string]*attempt{}, ctls: map[int]*control{}, blockedReq: -1, reuseKeys: map[int]string{}, pending: map[string]*attempt{}, certReq: -2}
	rn.own = newOwner(c, cfg.Owner, cfg.Workload, in.schedule)
	rn.resPol = resolverPolicy(policy, cfg.Authority)
	rn.nextPeriodic = cfg.Authority.PeriodicMS * 1000
	w := cfg.Workload
	rn.dom = journal.Domain{ID: "sim", Generation: 1, InstalledEpoch: 1, Dimension: w.Dimension,
		ComponentMin: w.ComponentMin, ComponentMax: w.ComponentMax, SumMax: w.SumMax,
		Anchor: append([]int64(nil), w.Initial...)}
	rn.rs = make([]reqState, len(in.reqs))
	rn.maxDelayMS = assumedMaxDelayMS(cfg)
	rn.m = Row{Regime: cfg.Regime, Policy: policy, Episode: ep, Contract: c.ID, Level: string(c.Level()), Offered: len(in.reqs)}
	for i, rq := range in.reqs {
		rn.push(&event{t: rq.arrival, kind: evArrival, req: i})
	}
	if !cfg.Authority.SingleShotDecision || rn.isEvicon() || policy == PolicyB2Ledger || policy == PolicyB5SelectorM4 {
		rn.push(&event{t: cfg.Authority.TickMS * 1000, kind: evTick})
	}
	for rn.q.Len() > 0 {
		e := heap.Pop(&rn.q).(*event)
		if e.t > in.endUS {
			break
		}
		rn.now = e.t
		if err := rn.handle(e); err != nil {
			return Row{}, fmt.Errorf("episode %d policy %s: %w", ep, policy, err)
		}
	}
	rn.finish()
	return rn.m, nil
}

func assumedMaxDelayMS(cfg Config) int64 {
	if cfg.Authority.AssumedMaxDelayMS != 0 {
		return cfg.Authority.AssumedMaxDelayMS
	}
	if cfg.Owner.Channel == "queue" {
		if cfg.Owner.BrokerExpiryMS <= 0 {
			return -1
		}
		return cfg.Owner.BrokerExpiryMS + int64(math.Ceil(cfg.Network.OneWayBaseMS+6*cfg.Network.OneWayJitterMS+1))
	}
	return int64(math.Ceil(cfg.Network.OneWayBaseMS + 6*cfg.Network.OneWayJitterMS + 1))
}

func (rn *run) handle(e *event) error {
	switch e.kind {
	case evArrival:
		rs := &rn.rs[e.req]
		rs.waiting = true
		rn.waiting = append(rn.waiting, e.req)
		if rn.cfg.Authority.SingleShotDecision {
			return rn.singleShot(e.req)
		}
		return rn.tryAdmit(true)
	case evOwnerDeliver:
		rn.ownerDeliver(e.msg)
	case evOwnerWake:
		rn.wakeAt = 0
		rn.flushQueue()
	case evCommandReply:
		return rn.onCommandReply(e)
	case evQueryReply:
		return rn.onQueryReply(e)
	case evStopReply:
		return rn.onStopReply(e)
	case evFenceReply:
		return rn.onFenceReply(e)
	case evDispatchTimeout:
		return rn.onDispatchTimeout(e.att)
	case evControlTimeout:
		return rn.onControlTimeout(e.ctl)
	case evRetry:
		return rn.onRetry(e.att)
	case evCancelAt:
		return rn.onCancel(e.att)
	case evTick:
		return rn.onTick()
	case evOracleStopped:
		if e.att.status == "PENDING" {
			return rn.setStatus(e.att, "STOPPED", "ORACLE_LOST")
		}
	}
	return nil
}

// ---------------------------------------------------------------------------
// Admission.

func (rn *run) candidate(req int) protocol.CommandRequest {
	rq := rn.in.reqs[req]
	id := fmt.Sprintf("cand-%d", req)
	return protocol.CommandRequest{SchemaVersion: 1, DomainID: "sim", Generation: 1, CommandID: id, Operation: rq.op,
		Value: append([]int64(nil), rq.value...), Epoch: 1, Sequence: rn.nextSeq + 1, IntentHash: "h-" + id}
}

func (rn *run) unresolved() int { return len(rn.pending) }

func (rn *run) bump() { rn.ver++ }

// activeCmds returns the records after the compaction cutoff (BuildBelief would
// skip the compacted prefix anyway; slicing only avoids rescanning it).
func (rn *run) activeCmds() []journal.CommandRecord { return rn.cmds[rn.compactAt:] }

// compact folds a terminal ADD-only prefix into the anchor. For non-negative
// ADD this is exact (addition commutes and STOPPED contributes nothing). A SET
// stops prefix compaction; a fully terminal history with SET is compacted only
// when the belief has a unique state (the runtime's rule).
func (rn *run) compact() {
	// An APPLIED ADD may have been applied AFTER a later-sequence SET that is
	// still in the active window (arrival reordering), so prefix folding is
	// exact only while the active window contains no SET at all.
	hasSet := false
	for _, c := range rn.activeCmds() {
		if c.Operation == domain.OpSet {
			hasSet = true
			break
		}
	}
	for !hasSet && rn.compactAt < len(rn.cmds) {
		c := rn.cmds[rn.compactAt]
		if c.Status != "APPLIED" && c.Status != "STOPPED" || c.Operation != domain.OpAdd {
			break
		}
		if c.Status == "APPLIED" {
			for i, v := range c.Value {
				rn.dom.Anchor[i] += v
			}
		}
		rn.dom.CutoffSequence = c.Sequence
		rn.compactAt++
	}
	if rn.compactAt < len(rn.cmds) && len(rn.pending) == 0 {
		b, err := evidence.BuildBelief(rn.dom, rn.activeCmds(), rn.cfg.Authority.MaxConfigurations)
		if err != nil || len(b.Configurations) == 0 {
			return
		}
		anchor := b.Configurations[0].State.Clone()
		for _, cfg := range b.Configurations[1:] {
			for i := range anchor {
				if anchor[i] != cfg.State[i] {
					return
				}
			}
		}
		last := rn.cmds[len(rn.cmds)-1]
		rn.dom.Anchor = []int64(anchor)
		rn.dom.CutoffSequence = last.Sequence
		rn.compactAt = len(rn.cmds)
	}
}

// beliefInputs returns the (domain, records) given to the runtime reducer.
// For an ADD-only active window, terminal records are folded into the anchor
// (APPLIED adds its vector, STOPPED contributes nothing) and only PENDING
// records are passed on. For non-negative ADD this yields the same reachable
// states and therefore the same guard verdict and certificates; it only avoids
// re-enumerating terminal statuses. Windows with SET use the full records.
func (rn *run) beliefInputs() (journal.Domain, []journal.CommandRecord) {
	act := rn.activeCmds()
	for _, c := range act {
		if c.Operation == domain.OpSet {
			return rn.dom, act
		}
	}
	d := rn.dom
	d.Anchor = append([]int64(nil), rn.dom.Anchor...)
	pend := make([]journal.CommandRecord, 0, len(rn.pending))
	for _, c := range act {
		switch c.Status {
		case "APPLIED":
			for i, v := range c.Value {
				d.Anchor[i] += v
			}
		case "STOPPED":
		default:
			pend = append(pend, c)
		}
	}
	return d, pend
}

// evaluate returns the guard verdict for request req against the authority's
// current belief (exactly the runtime's evaluateCandidate path).
func (rn *run) evaluate(req int) (analyzer.Result, error) {
	if rn.unresolved()+1 > rn.cfg.Authority.MaxOutstanding {
		return analyzer.Result{Verdict: analyzer.Deferred, Reason: "OUTSTANDING_BUDGET_EXHAUSTED"}, nil
	}
	if rn.beliefVer != rn.ver || rn.ver == 0 {
		d, cs := rn.beliefInputs()
		rn.belief, rn.beliefErr = evidence.BuildBelief(d, cs, rn.cfg.Authority.MaxConfigurations)
		rn.beliefVer = rn.ver
	}
	b, err := rn.belief, rn.beliefErr
	if errors.Is(err, evidence.ErrBeliefBudget) {
		return analyzer.Result{Verdict: analyzer.Deferred, Reason: "CONFIGURATION_BUDGET_EXHAUSTED"}, nil
	}
	if errors.Is(err, evidence.ErrModelInconsistency) {
		rn.m.ModelInconsistency++
		return analyzer.Result{Verdict: analyzer.Deferred, Reason: "MODEL_INCONSISTENCY"}, nil
	}
	if err != nil {
		return analyzer.Result{}, err
	}
	bound := domain.HardBound{ComponentMin: rn.dom.ComponentMin, ComponentMax: rn.dom.ComponentMax, SumMax: rn.dom.SumMax}
	cand := rn.candidate(req)
	return analyzer.RelationalGuard(b, cand.DomainCommand(), bound)
}

func (rn *run) countDefer(reason string) {
	rn.m.DeferredDecisions++
	switch reason {
	case "OUTSTANDING_BUDGET_EXHAUSTED":
		rn.m.DeferredOutstanding++
	case "CONFIGURATION_BUDGET_EXHAUSTED":
		rn.m.DeferredBeliefCap++
	}
}

func blocked(r analyzer.Result) bool {
	return r.Verdict == analyzer.Counterexample || r.Verdict == analyzer.ConservativeBlock
}

func (rn *run) tryAdmitAll() error { return rn.tryAdmit(true) }

func (rn *run) dropExpired() {
	keep := rn.waiting[:0]
	for _, req := range rn.waiting {
		rs := &rn.rs[req]
		if rs.done || !rs.waiting {
			continue
		}
		if rn.now > rn.in.reqs[req].deadline {
			rs.waiting = false
			rs.dropped = true
			continue
		}
		keep = append(keep, req)
	}
	rn.waiting = keep
}

func (rn *run) tryAdmit(force bool) error {
	if rn.paused {
		return nil
	}
	if !force && rn.evalVer == rn.ver {
		rn.dropExpired()
		return nil
	}
	defer func() { rn.evalVer = rn.ver }()
	rn.blockedReq = -1
	keep := rn.waiting[:0]
	for _, req := range rn.waiting {
		rs := &rn.rs[req]
		if rs.done || !rs.waiting {
			continue
		}
		if rn.now > rn.in.reqs[req].deadline {
			rs.waiting = false
			rs.dropped = true
			continue
		}
		res, err := rn.evaluate(req)
		if err != nil {
			return err
		}
		rn.m.Decisions++
		if res.Verdict == analyzer.Safe {
			if rs.everBlocked {
				rn.m.AdmittedAfterBlock++
			}
			rn.admit(req, rn.reuseKeys[req])
			continue
		}
		if blocked(res) {
			rn.m.BlockedDecisions++
			rs.everBlocked = true
			if rn.blockedReq < 0 {
				rn.blockedReq = req
			}
		} else {
			rn.countDefer(res.Reason)
		}
		rs.lastBlockWhy = res.Reason
		keep = append(keep, req)
	}
	rn.waiting = keep
	return nil
}

func (rn *run) admit(req int, reuseOwnerKey string) *attempt {
	rs := &rn.rs[req]
	rs.waiting = false
	rs.admitted++
	rn.nextSeq++
	rq := rn.in.reqs[req]
	key := fmt.Sprintf("r%d-a%d", req, rs.admitted)
	ok := key
	if reuseOwnerKey != "" {
		ok = reuseOwnerKey
	}
	a := &attempt{key: key, ownerKey: ok, req: req, seq: rn.nextSeq, op: rq.op, value: append([]int64(nil), rq.value...),
		reserveAt: rn.now, status: "PENDING", ambiguousSince: -1, queriedAt: -1}
	rn.atts[key] = a
	rn.pending[key] = a
	rn.bump()
	rn.cmdIdx[key] = len(rn.cmds)
	rn.cmds = append(rn.cmds, journal.CommandRecord{DomainID: "sim", CommandID: key, Generation: 1, Epoch: 1, Sequence: a.seq,
		Operation: a.op, Value: append([]int64(nil), a.value...), IntentHash: "h-" + key, Reservation: append([]int64(nil), a.value...),
		Status: "PENDING", Decision: "ADMITTED"})
	rn.m.Admitted++
	rn.send(a)
	return a
}

// singleShot reproduces the 059 harness: one decision at arrival with
// synchronous selector probing on BLOCK; DEFER/BLOCK is final.
func (rn *run) singleShot(req int) error {
	res, err := rn.evaluate(req)
	if err != nil {
		return err
	}
	rn.m.Decisions++
	rs := &rn.rs[req]
	if res.Verdict == analyzer.Safe {
		rn.admit(req, "")
		return nil
	}
	if blocked(res) && rn.isReplay() {
		// Probing is modelled as instantaneous owner reads at decision time
		// (query RTT << deadline in the replay regime).
		rn.m.SelectorActivations++
		budget := rn.cfg.Authority.QueryBudget
		excluded := map[string]bool{}
		for budget > 0 {
			method := probe.Method(rn.policy)
			if rn.policy == PolicyReplayM0 {
				method = probe.M1
			}
			cand := rn.candidate(req)
			bd, bc := rn.beliefInputs()
			ranked, err := probe.SelectSimple(method, bd, bc, cand, rn.costModel(), excluded, rn.cfg.Authority.MaxConfigurations)
			if errors.Is(err, evidence.ErrBeliefBudget) || errors.Is(err, evidence.ErrModelInconsistency) {
				rn.m.SelectorErrors++
				break
			}
			if err != nil {
				return err
			}
			if len(ranked) == 0 {
				break
			}
			n := 1
			if rn.policy == PolicyReplayM1 {
				n = len(ranked)
			}
			if n > budget {
				n = budget
			}
			for _, c := range ranked[:n] {
				excluded[c.CommandID] = true
				budget--
				rn.m.Queries++
				a := rn.atts[c.CommandID]
				out := "NOT_FOUND"
				if rn.c.Has(contract.CapQueryApplied) || rn.c.Has(contract.CapAtomic) {
					out = rn.own.query(a.ownerKey, rn.now)
				}
				if adm := rn.c.Admit(contract.Outcome(out), contract.ChannelQuery, (rn.now-a.firstSend)/1000); adm.Admissible && a.status == "PENDING" {
					if err := rn.setStatus(a, string(adm.As), "QUERY"); err != nil {
						return err
					}
				}
			}
			res, err = rn.evaluate(req)
			if err != nil {
				return err
			}
			if res.Verdict == analyzer.Safe {
				rn.m.AdmittedAfterBlock++
				rn.admit(req, "")
				return nil
			}
			if !blocked(res) {
				break
			}
		}
	}
	if blocked(res) {
		rn.m.BlockedDecisions++
	} else {
		rn.countDefer(res.Reason)
	}
	rs.waiting = false
	rs.dropped = true
	return nil
}

func (rn *run) costModel() probe.CostModel {
	cm := probe.CostModel{DefaultUS: int64(rn.cfg.Network.QueryRTTMS * 1000), ByCommand: map[string]int64{}}
	if len(rn.cfg.Network.QueryRTTClassMS) > 0 {
		for k, a := range rn.pending {
			cls := rn.cfg.Network.QueryRTTClassMS[int(rn.r.u("qclass", a.req, 0)*float64(len(rn.cfg.Network.QueryRTTClassMS)))%len(rn.cfg.Network.QueryRTTClassMS)]
			cm.ByCommand[k] = cls * 1000
		}
	}
	if cm.DefaultUS <= 0 {
		cm.DefaultUS = 1000
	}
	return cm
}

// ---------------------------------------------------------------------------
// Dispatch and owner side.

func (rn *run) send(a *attempt) {
	rs := &rn.rs[a.req]
	j := rs.attempts
	rs.attempts++
	f := rn.fate(a.req, j)
	if a.firstSend == 0 && a.lastSend == 0 {
		a.firstSend = rn.now
	}
	a.lastSend = rn.now
	a.j = j
	m := &message{kind: mCommand, att: a, ownerKey: a.ownerKey, seq: a.seq, op: a.op, value: a.value, req: a.req, sendTime: rn.now, f: f}
	if f.reqLost {
		rn.m.LostRequests++
		if rn.isOracle() {
			rn.push(&event{t: rn.now, kind: evOracleStopped, att: a})
		}
	} else {
		rn.push(&event{t: rn.now + f.reqDelay, kind: evOwnerDeliver, msg: m})
	}
	if !rn.isOracle() {
		rn.push(&event{t: rn.now + rn.cfg.Authority.DispatchTimeoutMS*1000, kind: evDispatchTimeout, att: a})
	}
}

func (rn *run) ownerDeliver(m *message) {
	if !rn.own.online(rn.now) || len(rn.own.queue) > 0 {
		if rn.cfg.Owner.Channel == "direct" {
			rn.dropMessage(m, "OWNER_UNAVAILABLE")
			return
		}
		rn.own.queue = append(rn.own.queue, m)
		rn.scheduleWake()
		return
	}
	rn.process(m, rn.now)
}

func (rn *run) scheduleWake() {
	if rn.wakeAt != 0 {
		return
	}
	t := rn.own.nextOnline(rn.now)
	if t < 0 {
		return
	}
	if t <= rn.now {
		t = rn.now + 1
	}
	rn.wakeAt = t
	rn.push(&event{t: t, kind: evOwnerWake})
}

func (rn *run) flushQueue() {
	t := rn.now
	for len(rn.own.queue) > 0 {
		if !rn.own.online(t) {
			rn.scheduleWake()
			return
		}
		m := rn.own.queue[0]
		rn.own.queue = rn.own.queue[1:]
		if m.kind == mCommand && rn.cfg.Owner.BrokerExpiryMS > 0 && t > m.sendTime+rn.cfg.Owner.BrokerExpiryMS*1000 {
			rn.m.BrokerExpired++
			rn.dropMessage(m, "BROKER_EXPIRED")
			continue
		}
		t = rn.process(m, t)
	}
}

func (rn *run) dropMessage(m *message, why string) {
	switch m.kind {
	case mCommand:
		rn.m.LostRequests++
		if rn.isOracle() {
			rn.push(&event{t: rn.now, kind: evOracleStopped, att: m.att})
		}
	default:
		// control messages: the sender's control timeout handles the loss.
	}
}

// process executes one message at the owner at time >= t and returns the time
// at which the owner is free again.
func (rn *run) process(m *message, t int64) int64 {
	o := rn.own
	start := t
	if o.busyTill > start {
		start = o.busyTill
	}
	done := start + ms(rn.cfg.Owner.ProcessingMS)
	o.busyTill = done
	switch m.kind {
	case mCommand:
		res := o.command(m, done, m.f)
		if res.effect {
			rn.truthEffect(m, done)
		}
		if res.cancelled {
			rn.m.Cancellations++
		}
		if res.crashed {
			rn.m.OwnerCrashes++
			if rn.isOracle() {
				rn.push(&event{t: done, kind: evCommandReply, att: m.att, outcome: "APPLIED", reason: "ORACLE"})
			}
			return done
		}
		if rn.isOracle() {
			rn.push(&event{t: done, kind: evCommandReply, att: m.att, outcome: res.outcome, reason: res.reason})
			return done
		}
		if m.f.respLost {
			rn.m.LostReplies++
			return done
		}
		rn.push(&event{t: done + m.f.respDelay, kind: evCommandReply, att: m.att, outcome: res.outcome, reason: res.reason, sent: m.sendTime})
	case mQuery:
		out := o.query(m.ownerKey, done)
		rn.controlReply(m, done, &event{kind: evQueryReply, att: m.att, outcome: out, ctl: m.ctl})
	case mStop:
		out := o.stop(m.ownerKey, m.seq, m.req, done)
		rn.controlReply(m, done, &event{kind: evStopReply, att: m.att, outcome: out, ctl: m.ctl})
	case mFence:
		rec := o.fence(m.fenceW)
		rn.controlReply(m, done, &event{kind: evFenceReply, receipts: rec, ctl: m.ctl})
	case mCancel:
		// Best-effort cancel (B3): removes a still-queued copy at the broker; a
		// delivered command is unaffected. Owners with Q_S treat it as stop.
		if o.c.Has(contract.CapStop) {
			o.stop(m.ownerKey, m.seq, m.req, done)
		}
	}
	return done
}

func (rn *run) controlReply(m *message, done int64, e *event) {
	n := rn.cfg.Network
	if rn.r.u("ctlresp", m.req, int(rn.order%1000003)) < n.ControlLossProb {
		return
	}
	e.t = done + ms(n.ControlRTTMS/2)
	rn.push(e)
}

func (rn *run) sendControl(kind msgKind, a *attempt, w uint64) *control {
	rn.ctlSeq++
	ctl := &control{id: rn.ctlSeq, kind: kind, att: a, w: w, sentAt: rn.now}
	rn.ctls[ctl.id] = ctl
	rn.transmitControl(ctl)
	return ctl
}

func (rn *run) transmitControl(ctl *control) {
	ctl.attempts++
	n := rn.cfg.Network
	m := &message{kind: ctl.kind, att: ctl.att, req: -1, sendTime: rn.now, fenceW: ctl.w, ctl: ctl}
	if ctl.att != nil {
		m.ownerKey, m.seq, m.req = ctl.att.ownerKey, ctl.att.seq, ctl.att.req
	}
	rid := m.req
	if rid < 0 {
		rid = 1 << 20
	}
	if rn.r.u("ctlreq", rid, ctl.id*16+ctl.attempts) >= n.ControlLossProb {
		rtt := n.ControlRTTMS
		if ctl.kind == mQuery {
			rtt = rn.queryRTT(ctl.att)
		}
		rn.push(&event{t: rn.now + ms(rtt/2), kind: evOwnerDeliver, msg: m})
	}
	rn.push(&event{t: rn.now + n.ControlTimeoutMS*1000, kind: evControlTimeout, ctl: ctl})
}

func (rn *run) queryRTT(a *attempt) float64 {
	n := rn.cfg.Network
	if len(n.QueryRTTClassMS) > 0 && a != nil {
		i := int(rn.r.u("qclass", a.req, 0)*float64(len(n.QueryRTTClassMS))) % len(n.QueryRTTClassMS)
		return float64(n.QueryRTTClassMS[i])
	}
	return n.QueryRTTMS
}

// ---------------------------------------------------------------------------
// Truth accounting.

func (rn *run) truthEffect(m *message, t int64) {
	rs := &rn.rs[m.req]
	rs.effects++
	if rs.effects == 1 {
		rs.firstEffect = t
	} else {
		rn.m.DuplicateEffects++
	}
	rq := rn.in.reqs[m.req]
	if rq.validity > 0 && t > rq.validity {
		rs.late = true
		rn.m.LateEffects++
	}
	if m.att != nil {
		m.att.truthEffect = true
	}
	if ok, err := rn.own.bound.Check(rn.own.state); err == nil && !ok {
		rn.m.BoundViolations++
	}
}

// ---------------------------------------------------------------------------
// Evidence at the authority.

// reissuableSources: STOPPED evidence obtained by backward resolution of an
// ambiguous command (the intent was never rejected by the owner).
var reissuableSources = map[string]bool{"STOP": true, "FENCE": true, "EXPIRY_QUERY": true, "ORACLE_LOST": true}

func (rn *run) setStatus(a *attempt, status, source string) error {
	if a.status != "PENDING" {
		return nil
	}
	a.status = status
	a.terminalAt = rn.now
	i := rn.cmdIdx[a.key]
	rn.cmds[i].Status = status
	delete(rn.pending, a.key)
	rn.bump()
	rn.compact()
	rs := &rn.rs[a.req]
	switch status {
	case "APPLIED":
		if rs.confirmAt == 0 {
			rs.confirmAt = rn.now
		}
		rs.done = true
	case "STOPPED":
		if a.released || !reissuableSources[source] {
			break
		}
		rn.maybeReissue(a)
	}
	if rn.cfg.Authority.SingleShotDecision && !rn.isEvicon() && rn.policy != PolicyB2Ledger && rn.policy != PolicyB5SelectorM4 {
		return nil
	}
	return rn.tryAdmitAll()
}

// release marks an attempt STOPPED in the authority's belief WITHOUT
// admissible evidence (unsafe baselines only).
func (rn *run) release(a *attempt) {
	if a.status != "PENDING" {
		return
	}
	a.released = true
	a.status = "STOPPED"
	a.terminalAt = rn.now
	rn.cmds[rn.cmdIdx[a.key]].Status = "STOPPED"
	delete(rn.pending, a.key)
	rn.bump()
	rn.compact()
	rn.m.UnsafeReleases++
}

func (rn *run) maybeReissue(a *attempt) {
	if !(rn.isOracle() || rn.isEvicon() || rn.cfg.Authority.ReissueAfterStopped) {
		return
	}
	rs := &rn.rs[a.req]
	rq := rn.in.reqs[a.req]
	if rs.done || rs.waiting || rn.now > rq.deadline || (rq.validity > 0 && rn.now > rq.validity-rn.cfg.Authority.ValidityGuardMS*1000) {
		return
	}
	// Only re-issue when no other attempt of this request is still pending.
	for _, x := range rn.pending {
		if x.req == a.req {
			return
		}
	}
	rs.waiting = true
	rn.waiting = append(rn.waiting, a.req)
	rn.m.Reissues++
}

func (rn *run) onCommandReply(e *event) error {
	a := e.att
	if rn.cfg.Network.LateReplies == "drop" && !rn.isOracle() && rn.now-e.sent > rn.cfg.Authority.DispatchTimeoutMS*1000 {
		rn.m.LateRepliesDropped++ // synchronous RPC: the client stopped waiting for this reply
		return nil
	}
	if a.released || a.status != "PENDING" {
		return nil
	}
	if rn.isOracle() {
		if e.outcome == "APPLIED" || e.outcome == "STOPPED" {
			return rn.setStatus(a, e.outcome, "ORACLE")
		}
		return nil
	}
	if e.outcome == "UNCERTAIN" {
		a.uncertain++
	}
	adm := rn.c.Admit(contract.Outcome(e.outcome), contract.ChannelDispatchReply, (rn.now-a.firstSend)/1000)
	if !adm.Admissible {
		return nil
	}
	return rn.setStatus(a, string(adm.As), "DISPATCH_REPLY")
}

func (rn *run) onQueryReply(e *event) error {
	if e.ctl.done {
		return nil
	}
	e.ctl.done = true
	a := e.att
	if a.released || a.status != "PENDING" {
		return nil
	}
	// Release 072: the age of (A4) is taken when the query was SENT (a lower
	// bound on when the owner answered), not when the reply arrived.
	adm := rn.c.Admit(contract.Outcome(e.outcome), contract.ChannelQuery, (e.ctl.sentAt-a.firstSend)/1000)
	if !adm.Admissible {
		return nil
	}
	src := "QUERY"
	if e.outcome == "NOT_FOUND" {
		src = "EXPIRY_QUERY"
	}
	return rn.setStatus(a, string(adm.As), src)
}

func (rn *run) onStopReply(e *event) error {
	if e.ctl.done {
		return nil
	}
	e.ctl.done = true
	a := e.att
	if a.released || a.status != "PENDING" {
		return nil
	}
	adm := rn.c.Admit(contract.Outcome(e.outcome), contract.ChannelStop, (rn.now-a.firstSend)/1000)
	if !adm.Admissible {
		return nil
	}
	return rn.setStatus(a, string(adm.As), "STOP")
}

func (rn *run) onFenceReply(e *event) error {
	ctl := e.ctl
	if ctl.done {
		return nil
	}
	ctl.done = true
	keys := make([]string, 0, len(rn.pending))
	for k := range rn.pending {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	if rn.cfg.Authority.FenceUncertain == "abort" {
		for _, k := range keys {
			a := rn.atts[k]
			if a.status != "PENDING" || a.released || a.seq > ctl.w {
				continue
			}
			if e.receipts[a.ownerKey] == "UNCERTAIN" {
				// the owner cannot certify every identity below the cutoff: the runtime
				// aborts the barrier and resolves nothing (admission resumes)
				rn.m.FenceAborts++
				rn.m.FencePauseUS += rn.now - rn.pauseAt
				rn.paused = false
				return rn.tryAdmitAll()
			}
		}
	}
	for _, k := range keys {
		a := rn.atts[k]
		if a.status != "PENDING" || a.released || a.seq > ctl.w {
			continue
		}
		out := "STOPPED"
		if v, ok := e.receipts[a.ownerKey]; ok {
			out = v
		}
		adm := rn.c.Admit(contract.Outcome(out), contract.ChannelFence, (rn.now-a.firstSend)/1000)
		if !adm.Admissible {
			continue
		}
		a.status = string(adm.As)
		a.terminalAt = rn.now
		rn.cmds[rn.cmdIdx[a.key]].Status = a.status
		delete(rn.pending, a.key)
		rn.bump()
		if a.status == "APPLIED" {
			rs := &rn.rs[a.req]
			if rs.confirmAt == 0 {
				rs.confirmAt = rn.now
			}
			rs.done = true
		} else {
			rn.maybeReissue(a)
		}
	}
	rn.compact()
	rn.m.FencePauseUS += rn.now - rn.pauseAt
	rn.paused = false
	return rn.tryAdmitAll()
}

func (rn *run) onControlTimeout(ctl *control) error {
	if ctl.done {
		return nil
	}
	switch ctl.kind {
	case mQuery:
		ctl.done = true
		if ctl.att != nil {
			ctl.att.queriedAt = -1
		}
	case mStop:
		ctl.done = true
		if ctl.att != nil {
			ctl.att.stopIssued = false
		}
	case mFence:
		rn.m.FenceRetries++
		rn.transmitControl(ctl) // same token/cutoff; admission stays paused
	}
	return nil
}

// ---------------------------------------------------------------------------
// Timeouts, retries and policy behaviour.

func (rn *run) onDispatchTimeout(a *attempt) error {
	if a.status != "PENDING" {
		return nil
	}
	if a.ambiguousSince < 0 {
		a.ambiguousSince = rn.now
		rn.m.Ambiguous++
	}
	auth := rn.cfg.Authority
	switch rn.policy {
	case PolicyB0Naive:
		rn.release(a)
		rn.reissueNow(a.req, "")
	case PolicyB3Cancel:
		delay := (auth.CancelAfterMS - auth.DispatchTimeoutMS) * 1000
		if delay < 0 {
			delay = 0
		}
		rn.push(&event{t: rn.now + delay, kind: evCancelAt, att: a})
	case PolicyB4Unsafe:
		rn.release(a)
		rn.reissueNow(a.req, a.ownerKey)
	default:
		rn.push(&event{t: rn.now, kind: evRetry, att: a})
	}
	return nil
}

func (rn *run) reissueNow(req int, ownerKey string) {
	rs := &rn.rs[req]
	rq := rn.in.reqs[req]
	if rs.done || rn.now > rq.deadline {
		return
	}
	rn.m.Reissues++
	if ownerKey != "" {
		// B4: re-admitted later under the same idempotency key.
		rs.waiting = true
		rn.waiting = append(rn.waiting, req)
		rn.reuseKey(req, ownerKey)
		return
	}
	rs.waiting = true
	rn.waiting = append(rn.waiting, req)
}

func (rn *run) reuseKey(req int, key string) { rn.reuseKeys[req] = key }

func (rn *run) onCancel(a *attempt) error {
	if a.status != "PENDING" {
		return nil
	}
	// remove a still-queued copy at the broker (best effort)
	q := rn.own.queue[:0]
	removed := false
	for _, m := range rn.own.queue {
		if m.kind == mCommand && m.ownerKey == a.ownerKey {
			removed = true
			continue
		}
		q = append(q, m)
	}
	rn.own.queue = q
	if removed {
		rn.m.Cancellations++
	}
	rn.sendControl(mCancel, a, 0)
	rn.release(a)
	rn.reissueNow(a.req, "")
	return nil
}

func (rn *run) timing(a *attempt) contract.CommandTiming {
	rq := rn.in.reqs[a.req]
	t := contract.CommandTiming{FirstSendMS: a.firstSend / 1000, NowMS: rn.now / 1000, MaxNetworkDelayMS: rn.maxDelayMS}
	if rq.validity > 0 {
		t.IntentValidUntilMS = rq.validity / 1000
	}
	return t
}

func (rn *run) onRetry(a *attempt) error {
	if a.status != "PENDING" || a.released {
		return nil
	}
	auth := rn.cfg.Authority
	switch {
	case rn.policy == PolicyB1Durable:
		if rn.now-a.firstSend > auth.MaxRetryMS*1000 {
			return nil
		}
		if rn.now-a.lastSend >= auth.RetryIntervalMS*1000 || a.retries == 0 {
			a.retries++
			rn.m.Retries++
			rn.send(a)
		}
	case rn.horizonAware():
		d := resolve.RetryDecision(rn.c, rn.timing(a), a.lastSend/1000, auth.RetryIntervalMS)
		if d.Action == contract.ActionForwardRetry {
			a.retries++
			rn.m.Retries++
			rn.send(a)
		} else if d.Action == contract.ActionWait && d.Reason == "RETRY_BACKOFF" {
			// fallthrough to reschedule
		} else {
			return nil // horizon exhausted: hand over to resolution / ledger
		}
	default:
		return nil
	}
	rn.push(&event{t: rn.now + auth.RetryIntervalMS*1000, kind: evRetry, att: a})
	return nil
}

func (rn *run) onTick() error {
	rn.tickN++
	auth := rn.cfg.Authority
	if !auth.SingleShotDecision {
		if err := rn.tryAdmit(false); err != nil {
			return err
		}
	}
	if rn.isEvicon() || rn.policy == PolicyB2Ledger || rn.policy == PolicyB5SelectorM4 {
		rn.reconcileQueries()
	}
	if rn.policy == PolicyB5SelectorM4 && rn.blockedReq >= 0 {
		if err := rn.selectorProbe(rn.blockedReq); err != nil {
			return err
		}
	}
	if rn.isQueryOnly() {
		rn.queryOnlyStep()
	}
	if rn.isEvicon() {
		if rn.policy == PolicyResPeriodic {
			rn.validitySafety()
			if rn.now >= rn.nextPeriodic {
				rn.nextPeriodic = rn.now + auth.PeriodicMS*1000
				if err := rn.resolveStep(); err != nil {
					return err
				}
			}
		} else if err := rn.resolveStep(); err != nil {
			return err
		}
	}
	rn.escalations()
	rn.push(&event{t: rn.now + auth.TickMS*1000, kind: evTick})
	return nil
}

func (rn *run) ambiguousAttempts() []*attempt {
	out := []*attempt{}
	for _, a := range rn.pending {
		if a.status == "PENDING" && !a.released && a.ambiguousSince >= 0 {
			out = append(out, a)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].seq < out[j].seq })
	return out
}

func (rn *run) canQuery() bool {
	return rn.c.Has(contract.CapQueryApplied) || rn.c.Has(contract.CapAtomic)
}

func (rn *run) reconcileQueries() {
	if !rn.canQuery() {
		return
	}
	budget := rn.cfg.Authority.QueryBudget
	for _, a := range rn.ambiguousAttempts() {
		if budget <= 0 {
			return
		}
		if a.queriedAt >= 0 && rn.now-a.queriedAt < rn.cfg.Authority.ReconcileIntervalMS*1000 {
			continue
		}
		a.queriedAt = rn.now
		budget--
		rn.m.Queries++
		rn.sendControl(mQuery, a, 0)
	}
}

// queryOnlyStep (release 083): QUERY_ONLY queries the ambiguous commands (oldest first, not queried in the
// last reconcile interval, within the query budget of a tick) when a waiting request is blocked by the
// guard; QUERY_ONLY_W also does so when a waiting request is deferred because the window is full.
func (rn *run) queryOnlyStep() {
	if !rn.canQuery() {
		return
	}
	trigger := rn.blockedReq >= 0
	if !trigger && rn.policy == PolicyQueryOnlyW {
		for _, req := range rn.waiting {
			if rs := rn.rs[req]; rs.waiting && !rs.done && rs.lastBlockWhy == "OUTSTANDING_BUDGET_EXHAUSTED" {
				trigger = true
				break
			}
		}
	}
	if !trigger {
		return
	}
	budget := rn.cfg.Authority.QueryBudget
	for _, a := range rn.ambiguousAttempts() {
		if budget <= 0 {
			return
		}
		if a.queriedAt >= 0 && rn.now-a.queriedAt < rn.cfg.Authority.ReconcileIntervalMS*1000 {
			continue
		}
		a.queriedAt = rn.now
		budget--
		rn.m.Queries++
		rn.sendControl(mQuery, a, 0)
	}
}

func (rn *run) selectorProbe(req int) error {
	if !rn.canQuery() {
		return nil
	}
	rn.m.SelectorActivations++
	excluded := map[string]bool{}
	for _, a := range rn.pending {
		if a.queriedAt >= 0 && rn.now-a.queriedAt < rn.cfg.Authority.ReconcileIntervalMS*1000 {
			excluded[a.key] = true
		}
	}
	bd, bc := rn.beliefInputs()
	ranked, err := probe.SelectSimple(probe.M4, bd, bc, rn.candidate(req), rn.costModel(), excluded, rn.cfg.Authority.MaxConfigurations)
	if errors.Is(err, evidence.ErrBeliefBudget) || errors.Is(err, evidence.ErrModelInconsistency) {
		rn.m.SelectorErrors++
		return nil
	}
	if err != nil || len(ranked) == 0 {
		return err
	}
	a := rn.atts[ranked[0].CommandID]
	a.queriedAt = rn.now
	rn.m.Queries++
	rn.sendControl(mQuery, a, 0)
	return nil
}

// certificateTargets returns commands whose STOPPED outcome appears in a
// minimal continuation certificate for the earliest blocked request.
func (rn *run) certificateTargets() (map[string]bool, error) {
	out := map[string]bool{}
	if rn.blockedReq < 0 {
		return out, nil
	}
	if rn.certVer == rn.ver && rn.certReq == rn.blockedReq && rn.certCache != nil {
		return rn.certCache, nil
	}
	defer func() { rn.certVer, rn.certReq, rn.certCache = rn.ver, rn.blockedReq, out }()
	depth := rn.cfg.Authority.CertificateDepth
	if depth > 4 {
		depth = 4
	}
	bd, bc := rn.beliefInputs()
	var certs []probe.ContinuationCertificate
	var err error
	if rn.cfg.Authority.Certificate == "greedy" { // release 083
		certs, err = probe.GreedyContinuationCertificate(bd, bc, rn.candidate(rn.blockedReq), rn.cfg.Authority.MaxConfigurations)
		if errors.Is(err, probe.ErrNotAdditive) {
			certs, err = probe.EnumerateContinuationCertificates(bd, bc, rn.candidate(rn.blockedReq), depth, rn.cfg.Authority.MaxConfigurations, 20000, 5*time.Second)
		}
	} else {
		certs, err = probe.EnumerateContinuationCertificates(bd, bc, rn.candidate(rn.blockedReq), depth, rn.cfg.Authority.MaxConfigurations, 20000, 5*time.Second)
	}
	if err != nil {
		rn.m.CertificateErrors++
		return out, nil
	}
	for _, c := range certs {
		for _, x := range c.Required {
			if x.Outcome == "STOPPED" {
				out[x.CommandID] = true
			}
		}
	}
	if len(out) > 0 {
		rn.m.CertificateHits++
	}
	return out, nil
}

// validitySafety stops or fences pending commands whose validity window is
// about to close, so that a delayed copy can never take effect late. It is a
// safety action and bypasses the cost threshold.
func (rn *run) validitySafety() {
	if !(rn.c.Has(contract.CapStop) || rn.c.Has(contract.CapFence)) {
		return
	}
	guard := rn.cfg.Authority.ValidityGuardMS * 1000
	keys := make([]string, 0)
	for k, a := range rn.pending {
		rq := rn.in.reqs[a.req]
		if rq.validity > 0 && !a.stopIssued && rn.now >= rq.validity-guard {
			keys = append(keys, k)
		}
	}
	if len(keys) == 0 {
		return
	}
	sort.Strings(keys)
	if rn.c.Has(contract.CapStop) {
		for _, k := range keys {
			a := rn.atts[k]
			a.stopIssued = true
			rn.m.Stops++
			rn.m.ValidityActions++
			rn.sendControl(mStop, a, 0)
		}
		return
	}
	if !rn.paused {
		for _, k := range keys {
			rn.atts[k].stopIssued = true
		}
		rn.paused = true
		rn.pauseAt = rn.now
		rn.m.Fences++
		rn.m.ValidityActions++
		rn.sendControl(mFence, nil, rn.nextSeq)
	}
}

func (rn *run) resolveStep() error {
	rn.validitySafety()
	amb := rn.ambiguousAttempts()
	if len(amb) == 0 {
		return nil
	}
	targets := map[string]bool{}
	if rn.policy == PolicyResPeriodic {
		for _, a := range amb { // periodic: every aged ambiguous command, no certificate
			targets[a.key] = true
		}
	} else {
		t, err := rn.certificateTargets()
		if err != nil {
			return err
		}
		targets = t
	}
	auth := rn.cfg.Authority
	in := resolve.Input{NowMS: rn.now / 1000, Contract: rn.c, Blocked: true, QueryBudget: auth.QueryBudget,
		MaxNetworkDelayMS: rn.maxDelayMS, FenceInProgress: rn.paused}
	anyTarget := false
	for _, a := range amb {
		self := false
		rq := rn.in.reqs[a.req]
		stillWanted := rn.now <= rq.deadline && (rq.validity == 0 || rn.now <= rq.validity)
		if stillWanted || a.uncertain >= auth.FutileRetryReplies {
			futile := a.uncertain >= auth.FutileRetryReplies
			if d := resolve.RetryDecision(rn.c, rn.timing(a), a.lastSend/1000, auth.RetryIntervalMS); futile || d.Action != contract.ActionForwardRetry && !(d.Action == contract.ActionWait && d.Reason == "RETRY_BACKOFF") {
				self = true
			}
		}
		inCert := targets[a.key] || self
		anyTarget = anyTarget || inCert
		in.Ambiguous = append(in.Ambiguous, resolve.Ambiguous{ID: a.key, Sequence: a.seq, Magnitude: l1(a.value),
			AmbiguousSinceMS: a.ambiguousSince / 1000, FirstSendMS: a.firstSend / 1000, LastAttemptMS: a.lastSend / 1000,
			Queried: a.queriedAt >= 0 && rn.now-a.queriedAt < auth.ReconcileIntervalMS*1000, StopAttempted: a.stopIssued, InCertificate: inCert})
	}
	if !anyTarget {
		return nil
	}
	d, err := resolve.Decide(in, auth.Costs, rn.resPol)
	if err != nil {
		return err
	}
	switch d.Action {
	case contract.ActionQuery:
		for _, id := range d.Targets {
			a := rn.atts[id]
			a.queriedAt = rn.now
			rn.m.Queries++
			rn.sendControl(mQuery, a, 0)
		}
	case contract.ActionStop:
		for _, id := range d.Targets {
			a := rn.atts[id]
			a.stopIssued = true
			rn.m.Stops++
			rn.sendControl(mStop, a, 0)
		}
	case contract.ActionFence:
		w := rn.nextSeq
		rn.paused = true
		rn.pauseAt = rn.now
		rn.m.Fences++
		rn.sendControl(mFence, nil, w)
	case contract.ActionEscalate:
		// counted in escalations()
	}
	return nil
}

func (rn *run) escalations() {
	limit := rn.cfg.Authority.EscalateAfterMS * 1000
	manual := rn.cfg.Authority.ManualResolveAfterMS * 1000
	for _, a := range rn.ambiguousAttempts() {
		if !a.escalated && rn.now-a.ambiguousSince >= limit {
			a.escalated = true
			rn.m.Escalations++
		}
		if manual > 0 && a.escalated && rn.now-a.ambiguousSince >= limit+manual && a.status == "PENDING" {
			rn.m.ManualResolutions++
			if a.truthEffect {
				_ = rn.setStatus(a, "APPLIED", "MANUAL")
			} else {
				rn.own.tombs[a.ownerKey] = true
				_ = rn.setStatus(a, "STOPPED", "STOP")
			}
		}
	}
}

func l1(v []int64) int64 {
	var s int64
	for _, x := range v {
		if x < 0 {
			x = -x
		}
		s += x
	}
	return s
}

// ---------------------------------------------------------------------------
// Episode accounting.

func (rn *run) finish() {
	end := rn.in.endUS
	keys := make([]string, 0, len(rn.atts))
	for k := range rn.atts {
		keys = append(keys, k)
	}
	sort.Strings(keys) // deterministic floating-point accumulation order
	for _, k := range keys {
		a := rn.atts[k]
		// Reservation time of capacity that never produced an effect, from
		// admission (reserveAt) to terminal evidence (resolved) or to the end
		// of the episode (censored: the command may still take effect later).
		if !a.truthEffect {
			mag := float64(l1(a.value))
			rel := end
			if a.status != "PENDING" {
				rel = a.terminalAt
				rn.m.StrandedResolvedUnitSec += mag * float64(rel-a.reserveAt) / 1e6
			} else {
				rn.m.StrandedCensoredUnitSec += mag * float64(rel-a.reserveAt) / 1e6
			}
			if a.ambiguousSince >= 0 && rel > a.ambiguousSince {
				rn.m.StrandedAmbiguousUnitSec += mag * float64(rel-a.ambiguousSince) / 1e6
			}
		}
		if a.status == "PENDING" {
			rn.m.UnresolvedAtEnd++
		}
		if a.status == "STOPPED" && a.truthEffect && !a.released {
			rn.m.FalseStopped++
		}
	}
	for i, rs := range rn.rs {
		rq := rn.in.reqs[i]
		// clean useful work: exactly one effect (over all identities of the
		// request), before the deadline and inside the validity window
		if rs.effects == 1 && rs.firstEffect <= rq.deadline && !rs.late {
			rn.m.CleanFulfilled++
			if rs.confirmAt > 0 && rs.confirmAt <= rq.deadline {
				rn.m.CleanFulfilledConfirmed++
			}
		} else if rs.effects == 1 {
			rn.m.FulfilledLate++
		}
		if rs.effects > 1 {
			rn.m.DuplicatedRequests++
		}
	}
	// Safety (fixed before any run): bound violations, duplicate effects and
	// false STOPPED evidence. Late effects are validity violations, reported
	// separately (late_effects), never folded into or hidden by this number.
	rn.m.SafetyViolations = rn.m.BoundViolations + rn.m.DuplicateEffects + rn.m.FalseStopped
}

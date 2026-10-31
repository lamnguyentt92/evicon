package runtime

import (
	"bytes"
	"encoding/json"
	"errors"
	"evicon/internal/httpx"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"evicon/internal/analyzer"
	"evicon/internal/belief"
	"evicon/internal/buildinfo"
	"evicon/internal/contract"
	"evicon/internal/domain"
	"evicon/internal/evidence"
	"evicon/internal/fault"
	"evicon/internal/journal"
	"evicon/internal/outbox"
	"evicon/internal/probe"
	"evicon/internal/protocol"
	"evicon/internal/resolve"
	"evicon/internal/trace"
)

const statsSchemaVersion = 2

type AuthorityConfig struct {
	Store                *journal.Store
	Domain               journal.Domain
	EndpointURL          string
	Profile              string
	Dispatcher           *outbox.Dispatcher
	Faults               *fault.Controller
	Trace                *trace.Writer
	MaxConfigurations    int
	MaxOutstanding       int
	Policy               probe.Method
	LedgerReconcile      probe.Method
	QueryCosts           probe.CostModel
	OutcomeModel         probe.OutcomeModel
	ProbeBudget          int
	PlannerDepth         int
	PlannerComputeBudget time.Duration
	QueryConcurrency     int
	PlannerCache         bool
	// Release 061 (policy EVICON). EvidenceContract must be derived from the
	// endpoint's verified premises (contract.RuntimeContract); it decides which
	// observations may filter the belief and which resolution actions exist.
	EvidenceContract contract.EvidenceContract
	ResolvePolicy    resolve.Policy
	ResolveCosts     resolve.Costs
	// AmbiguityAfter: a command without terminal evidence this long after its
	// reservation counts as ambiguous for the resolution threshold.
	AmbiguityAfter   time.Duration
	CertificateDepth int
	// ResolveTrigger selects when EVICON-family policies act: TriggerBlockAndSweep
	// (default: on a blocked candidate and in the background sweep) or
	// TriggerSweepOnly (periodic baseline: only the background sweep resolves).
	ResolveTrigger string
	// ReleaseAfter (release 077) is the timeout of the RELEASE_ON_TIMEOUT and RELEASE_ON_READ
	// baselines: a command without terminal evidence this long after its creation is committed
	// STOPPED without owner evidence (RELEASE_ON_READ first reads the owner's receipt). Unused by
	// every other policy.
	ReleaseAfter time.Duration
	// ReleaseSettle (release 083, policy RELEASE_ON_READ_ANSWERED only) is the delay bound the
	// baseline assumes: its dispatcher re-sends a command only until ReleaseAfter - ReleaseSettle after
	// its creation, and an answered NOT_FOUND releases a command only when the read was sent at least
	// ReleaseSettle after the command's last dispatch attempt. 0 = ReleaseAfter.
	ReleaseSettle time.Duration
	// Certificate (release 083, EVICON only) selects the certificate search of a guard block:
	// CertificateEnumerate (default: the bounded enumeration of releases 061-082) or CertificateGreedy
	// (the greedy certificate of additive domains; other domains fall back to the enumeration).
	Certificate string
	// Test-only synchronization hook used to force a domain revision change
	// after analysis and verify that the authority recomputes before commit.
	AfterPolicyEvaluationForTest func()
}

type Authority struct {
	cfg     AuthorityConfig
	mu      sync.Mutex
	hc      *http.Client
	planner *probe.Planner
	// release 061+: resolver bookkeeping (recent queries per command)
	queriedMu sync.Mutex
	queriedAt map[string]time.Time
	stoppedAt map[string]time.Time
	// ownerUncertain: identities the owner answered UNCERTAIN for on a stop or
	// a fence (it cannot certify their past); backward resolution skips them
	// for OwnerUncertainRetry.
	ownerUncertain map[string]time.Time
	// conflicted (release 068): domains whose owner evidence contradicted a committed
	// terminal outcome; the resolver escalates for them instead of acting.
	conflicted map[string]time.Time
}

// Resolve triggers (release 062).
const (
	TriggerBlockAndSweep = "BLOCK_AND_SWEEP"
	TriggerSweepOnly     = "SWEEP_ONLY"
	// release 083: the certificate search of EVICON's resolver
	CertificateEnumerate = "enumerate"
	CertificateGreedy    = "greedy"
)

const runtimeAnalyzer = "RELATIONAL_ONLINE_BELIEF"

func NewAuthority(cfg AuthorityConfig) (*Authority, error) {
	if cfg.Store == nil {
		return nil, fmt.Errorf("store required")
	}
	if cfg.Profile == "" {
		cfg.Profile = "E3"
	}
	if cfg.Profile != "E1" && cfg.Profile != "E2" && cfg.Profile != "E3" && cfg.Profile != "LINUX_TC_E2_LIKE" && cfg.Profile != "DEVICE" {
		return nil, fmt.Errorf("unsupported profile %s", cfg.Profile)
	}
	if cfg.Profile == "DEVICE" && cfg.EvidenceContract.ID == "" {
		// a device's evidence semantics are only known through its contract
		// (EVICON uses it for admissibility; every policy uses it for the T4
		// retry guard and owner expiry)
		return nil, fmt.Errorf("profile DEVICE requires an evidence contract")
	}
	if cfg.MaxConfigurations <= 0 {
		cfg.MaxConfigurations = 256
	}
	if cfg.MaxOutstanding <= 0 {
		cfg.MaxOutstanding = 4
	}
	if cfg.Policy == "" {
		cfg.Policy = probe.M0
	}
	if _, err := probe.ParseMethod(string(cfg.Policy)); err != nil {
		return nil, err
	}
	if cfg.LedgerReconcile == "" {
		cfg.LedgerReconcile = probe.M1
	}
	if cfg.LedgerReconcile != probe.M1 && cfg.LedgerReconcile != probe.M2 && cfg.LedgerReconcile != probe.M3 && cfg.LedgerReconcile != probe.M4 {
		return nil, fmt.Errorf("ledger reconciliation must be M1..M4")
	}
	if cfg.QueryCosts.DefaultUS <= 0 {
		cfg.QueryCosts = probe.DefaultCostModel()
	}
	if cfg.Policy == probe.M4P || cfg.Policy == probe.M5V2 || cfg.Policy == probe.M5V2D1 {
		if cfg.OutcomeModel.Hash == "" || cfg.OutcomeModel.Spec.Profile != cfg.Profile {
			return nil, fmt.Errorf("M4P/M5V2 requires a frozen development outcome model for matching profile")
		}
	}
	if cfg.ProbeBudget <= 0 {
		cfg.ProbeBudget = 2
	}
	if cfg.Policy == probe.EVICON {
		if cfg.EvidenceContract.ID == "" {
			return nil, fmt.Errorf("EVICON requires an evidence contract derived from verified endpoint premises")
		}
		if err := cfg.EvidenceContract.Validate(); err != nil {
			return nil, err
		}
		// Evidence-backed sources only: verified by the owner's own tests, or
		// measured black-box by EviCon-Probe. Documentation and hypotheses never
		// decide which observations may filter the belief.
		if cfg.EvidenceContract.Source != contract.SourceRuntimeVerified && cfg.EvidenceContract.Source != contract.SourceMeasured {
			return nil, fmt.Errorf("EVICON evidence contract must be RUNTIME_VERIFIED or MEASURED, got %s", cfg.EvidenceContract.Source)
		}
		if cfg.ResolvePolicy.Mode == "" {
			cfg.ResolvePolicy.Mode = resolve.ModeSkiRental
		}
		if err := cfg.ResolvePolicy.Validate(); err != nil {
			return nil, err
		}
		if cfg.ResolveCosts.HoldPerUnitPerSec == 0 {
			cfg.ResolveCosts = resolve.Costs{HoldPerUnitPerSec: 1, QueryCost: 0.01, StopCost: 1, FenceCost: 5}
		}
		if cfg.AmbiguityAfter <= 0 {
			cfg.AmbiguityAfter = 2 * time.Second
		}
		switch cfg.ResolveTrigger {
		case "":
			cfg.ResolveTrigger = TriggerBlockAndSweep
		case TriggerBlockAndSweep, TriggerSweepOnly:
		default:
			return nil, fmt.Errorf("unknown resolve trigger %q", cfg.ResolveTrigger)
		}
		if cfg.CertificateDepth <= 0 {
			cfg.CertificateDepth = 3
		}
	}
	if probe.IsReleaseBaseline(cfg.Policy) && cfg.ReleaseAfter <= 0 {
		cfg.ReleaseAfter = 10 * time.Second
	}
	if cfg.Policy == probe.M1W && cfg.AmbiguityAfter <= 0 {
		cfg.AmbiguityAfter = 2 * time.Second // release 083: EVICON's default
	}
	if cfg.Policy == probe.ReleaseOnReadAnswered {
		if cfg.ReleaseSettle <= 0 {
			cfg.ReleaseSettle = cfg.ReleaseAfter
		}
		if cfg.ReleaseSettle > cfg.ReleaseAfter {
			return nil, fmt.Errorf("release settle %s exceeds release-after %s", cfg.ReleaseSettle, cfg.ReleaseAfter)
		}
	}
	switch cfg.Certificate {
	case "":
		cfg.Certificate = CertificateEnumerate
	case CertificateEnumerate, CertificateGreedy:
	default:
		return nil, fmt.Errorf("unknown certificate search %q (want %s or %s)", cfg.Certificate, CertificateEnumerate, CertificateGreedy)
	}
	if cfg.PlannerDepth <= 0 {
		cfg.PlannerDepth = 2
	}
	if cfg.PlannerComputeBudget <= 0 {
		cfg.PlannerComputeBudget = 5 * time.Millisecond
	}
	if cfg.QueryConcurrency <= 0 {
		cfg.QueryConcurrency = 2
	}
	if err := cfg.Store.EnsureDomain(cfg.Domain); err != nil {
		return nil, err
	}
	if cfg.Trace != nil {
		enabled := cfg.PlannerCache
		if err := cfg.Trace.Write(trace.Event{DomainID: cfg.Domain.ID, Event: "PLANNER_CONFIGURATION", Policy: string(cfg.Policy),
			OutcomeModelHash: cfg.OutcomeModel.Hash, PlannerDepth: cfg.PlannerDepth, PlannerBudgetNS: cfg.PlannerComputeBudget.Nanoseconds(), PlannerCacheEnabled: &enabled}); err != nil {
			return nil, err
		}
	}
	return &Authority{cfg: cfg, hc: httpx.Client(2 * time.Second), planner: probe.NewPlanner(cfg.QueryCosts, cfg.MaxConfigurations, cfg.PlannerCache)}, nil
}

func (a *Authority) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"ok": true, "role": "authority", "pid": os.Getpid(), "profile": a.cfg.Profile, "release": buildinfo.Release, "build_id": buildinfo.PatchID, "policy": a.cfg.Policy})
	})
	mux.HandleFunc("/v1/commands", a.handleCommands)
	mux.HandleFunc("/v1/commands/", a.handleGetCommand)
	mux.HandleFunc("/v1/evidence/query", a.handleEvidenceQuery)
	mux.HandleFunc("/v1/barriers", a.handleBarrier)
	mux.HandleFunc("/_debug/runtime", a.handleRuntime)
	mux.HandleFunc("/_debug/belief", a.handleBelief)
	return mux
}

func (a *Authority) rebuildBelief() (journal.Domain, int, error) {
	d, err := a.cfg.Store.GetDomain(a.cfg.Domain.ID)
	if err != nil {
		return journal.Domain{}, 0, err
	}
	cmds, err := a.cfg.Store.ListCommandsForBelief(d.ID, d.CutoffSequence)
	if err != nil {
		return journal.Domain{}, 0, err
	}
	b, err := evidence.BuildBelief(d, cmds, a.cfg.MaxConfigurations)
	if err != nil {
		return d, 0, err
	}
	if a.cfg.Trace != nil {
		_ = a.cfg.Trace.Write(trace.Event{DomainID: d.ID, Event: "BELIEF_REBUILT", Revision: d.Revision, Reason: fmt.Sprintf("configurations=%d", len(b.Configurations))})
	}
	return d, len(b.Configurations), nil
}

func sameState(a, b domain.State) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// maybeCompactTerminalHistory projects fully terminal active history behind a
// durable local checkpoint only when the reconstructed belief has one unique
// state. Command/decision/observation audit rows are never deleted.
func (a *Authority) maybeCompactTerminalHistory(d journal.Domain) (journal.Domain, error) {
	cmds, err := a.cfg.Store.ListCommandsForBelief(d.ID, d.CutoffSequence)
	if err != nil || len(cmds) == 0 {
		return d, err
	}
	var maxSeq uint64
	for _, c := range cmds {
		if c.Status != "APPLIED" && c.Status != "STOPPED" {
			return d, nil
		}
		if c.Sequence > maxSeq {
			maxSeq = c.Sequence
		}
	}
	b, err := evidence.BuildBelief(d, cmds, a.cfg.MaxConfigurations)
	if err != nil {
		return d, nil
	} // compaction is optional; never weaken coverage on failure
	if len(b.Configurations) == 0 {
		return d, evidence.ErrModelInconsistency
	}
	anchor := b.Configurations[0].State.Clone()
	for _, cfg := range b.Configurations[1:] {
		if !sameState(anchor, cfg.State) {
			return d, nil
		}
	}
	rev, err := a.cfg.Store.CompactTerminalSingleton(d.ID, d.Revision, maxSeq, []int64(anchor))
	if errors.Is(err, journal.ErrRevisionConflict) {
		return a.cfg.Store.GetDomain(d.ID)
	}
	if err != nil {
		return d, err
	}
	if a.cfg.Trace != nil {
		_ = a.cfg.Trace.Write(trace.Event{DomainID: d.ID, Event: "TERMINAL_HISTORY_COMPACTED", Revision: rev, Reason: fmt.Sprintf("cutoff=%d", maxSeq)})
	}
	return a.cfg.Store.GetDomain(d.ID)
}

func (a *Authority) evaluateCandidate(d journal.Domain, req protocol.CommandRequest) (analyzer.Result, error) {
	cmds, err := a.cfg.Store.ListCommandsForBelief(d.ID, d.CutoffSequence)
	if err != nil {
		return analyzer.Result{}, err
	}
	unresolved := 0
	for _, c := range cmds {
		if c.Status != "APPLIED" && c.Status != "STOPPED" {
			unresolved++
		}
	}
	if unresolved+1 > a.cfg.MaxOutstanding {
		return analyzer.Result{Verdict: analyzer.Deferred, Reason: "OUTSTANDING_BUDGET_EXHAUSTED"}, nil
	}
	b, err := evidence.BuildBelief(d, cmds, a.cfg.MaxConfigurations)
	if errors.Is(err, evidence.ErrBeliefBudget) {
		return analyzer.Result{Verdict: analyzer.Deferred, Reason: "CONFIGURATION_BUDGET_EXHAUSTED"}, nil
	}
	if err != nil {
		return analyzer.Result{}, err
	}
	bound := domain.HardBound{ComponentMin: d.ComponentMin, ComponentMax: d.ComponentMax, SumMax: d.SumMax}
	return analyzer.RelationalGuard(b, req.DomainCommand(), bound)
}

func (a *Authority) persistDecision(req protocol.CommandRequest, decision, reason string, revision uint64, analyzerName string) error {
	if analyzerName == "" {
		analyzerName = runtimeAnalyzer
	}
	if err := a.cfg.Store.RecordDecision(req.DomainID, req.CommandID, decision, analyzerName, reason, revision); err != nil {
		return err
	}
	if a.cfg.Trace != nil {
		_ = a.cfg.Trace.Write(trace.Event{
			DomainID:  req.DomainID,
			CommandID: req.CommandID,
			Event:     "DECISION_RECORDED",
			Decision:  decision,
			Analyzer:  analyzerName,
			Revision:  revision,
			Reason:    reason,
		})
	}
	return nil
}

func (a *Authority) handleCommands(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", 405)
		return
	}
	recv := time.Now()
	a.mu.Lock()
	defer a.mu.Unlock()
	var req protocol.CommandRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	if a.cfg.Trace != nil {
		// Release 069 (RQ4): the request has been read; wait_ns is the time from the handler's entry
		// to here (the authority's lock, then the body), so evaluation/overhead.py can split the
		// admission latency into client and network, lock, policy evaluation and commit.
		_ = a.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "ADMISSION_RECEIVED", WaitNS: time.Since(recv).Nanoseconds()})
	}
	if req.DomainID != a.cfg.Domain.ID {
		http.Error(w, "unknown domain", 404)
		return
	}
	d0, err := a.cfg.Store.GetDomain(req.DomainID)
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	if err = req.ValidateAndFillHash(d0.Dimension); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	if (a.cfg.Profile == "LINUX_TC_E2_LIKE" || a.cfg.Profile == "DEVICE") && req.Operation != domain.OpAdd {
		if er := a.persistDecision(req, "REJECT", "UNSUPPORTED_OPERATION_FOR_PROFILE", d0.Revision, "CAPABILITY_VALIDATION"); er != nil {
			http.Error(w, er.Error(), 500)
			return
		}
		writeJSON(w, 409, protocol.AuthorityResponse{SchemaVersion: 1, DomainID: req.DomainID, CommandID: req.CommandID, IntentHash: req.IntentHash, Decision: "REJECT", Status: "NOT_ADMITTED", Reason: "UNSUPPORTED_OPERATION_FOR_PROFILE", Revision: d0.Revision})
		return
	}
	if existing, er := a.cfg.Store.GetCommand(req.DomainID, req.CommandID); er == nil {
		if existing.IntentHash != req.IntentHash {
			d, _ := a.cfg.Store.GetDomain(req.DomainID)
			if err := a.persistDecision(req, "REJECT", "IDENTITY_CONFLICT", d.Revision, "IDENTITY_VALIDATION"); err != nil {
				http.Error(w, err.Error(), 500)
				return
			}
			if a.cfg.Trace != nil {
				_ = a.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "IDENTITY_CONFLICT_REJECTED"})
			}
			writeJSON(w, 409, map[string]any{"error": "IDENTITY_CONFLICT"})
			return
		}
		d, _ := a.cfg.Store.GetDomain(req.DomainID)
		writeJSON(w, 200, protocol.AuthorityResponse{SchemaVersion: 1, DomainID: req.DomainID, CommandID: req.CommandID, IntentHash: req.IntentHash, Decision: existing.Decision, Status: existing.Status, Reason: "IDEMPOTENT_REPLAY", Revision: d.Revision})
		return
	} else if !errors.Is(er, journal.ErrNotFound) {
		http.Error(w, er.Error(), 500)
		return
	}

	if br, pending, er := a.cfg.Store.PendingBarrier(req.DomainID); er != nil {
		http.Error(w, er.Error(), 500)
		return
	} else if pending {
		d, _ := a.cfg.Store.GetDomain(req.DomainID)
		_ = a.cfg.Store.RecordDecisionDetailed(req.DomainID, req.CommandID, "DEFER", runtimeAnalyzer, "BARRIER_IN_PROGRESS", d.Revision, string(a.cfg.Policy), 0, 0, 0)
		if a.cfg.Trace != nil {
			_ = a.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "DECISION_RECORDED", Decision: "DEFER", Analyzer: runtimeAnalyzer, Revision: d.Revision, Reason: "BARRIER_IN_PROGRESS", Policy: string(a.cfg.Policy)})
		}
		writeJSON(w, 503, protocol.AuthorityResponse{SchemaVersion: 1, DomainID: req.DomainID, CommandID: req.CommandID, IntentHash: req.IntentHash, Decision: "DEFER", Status: "NOT_ADMITTED", Reason: "BARRIER_IN_PROGRESS:" + br.Token, Revision: d.Revision})
		return
	}

	d0, err = a.maybeCompactTerminalHistory(d0)
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	maxSeq, err := a.cfg.Store.MaxAdmittedSequence(req.DomainID)
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	if req.Sequence <= d0.CutoffSequence || req.Sequence <= maxSeq {
		reason := "SEQUENCE_NOT_MONOTONIC"
		if er := a.persistDecision(req, "REJECT", reason, d0.Revision, "SEQUENCE_VALIDATION"); er != nil {
			http.Error(w, er.Error(), 500)
			return
		}
		writeJSON(w, 409, protocol.AuthorityResponse{SchemaVersion: 1, DomainID: req.DomainID, CommandID: req.CommandID, IntentHash: req.IntentHash, Decision: "REJECT", Status: "NOT_ADMITTED", Reason: reason, Revision: d0.Revision})
		return
	}

	rec := journal.CommandRecord{DomainID: req.DomainID, CommandID: req.CommandID, Generation: req.Generation, Epoch: req.Epoch, Sequence: req.Sequence, Operation: req.Operation, Value: append([]int64(nil), req.Value...), IntentHash: req.IntentHash, Reservation: append([]int64(nil), req.Value...), Status: "PENDING", Decision: "ADMITTED", Reason: "RELATIONAL_BELIEF_SAFE", TraceID: req.TraceID}
	for retry := 0; retry < 4; retry++ {
		d, er := a.cfg.Store.GetDomain(req.DomainID)
		if er != nil {
			http.Error(w, er.Error(), 500)
			return
		}
		if req.Generation != d.Generation || req.Epoch != d.InstalledEpoch {
			if er := a.persistDecision(req, "REJECT", "GENERATION_OR_EPOCH_MISMATCH", d.Revision, "IDENTITY_VALIDATION"); er != nil {
				http.Error(w, er.Error(), 500)
				return
			}
			writeJSON(w, 409, protocol.AuthorityResponse{SchemaVersion: 1, DomainID: req.DomainID, CommandID: req.CommandID, IntentHash: req.IntentHash, Decision: "REJECT", Status: "NOT_ADMITTED", Reason: "GENERATION_OR_EPOCH_MISMATCH", Revision: d.Revision})
			return
		}
		res, pst, er := a.evaluateWithPolicy(d, req)
		if a.cfg.AfterPolicyEvaluationForTest != nil {
			a.cfg.AfterPolicyEvaluationForTest()
		}
		if errors.Is(er, evidence.ErrModelInconsistency) {
			if er2 := a.cfg.Store.RecordDecisionDetailed(req.DomainID, req.CommandID, "DEFER", runtimeAnalyzer, "MODEL_INCONSISTENCY", d.Revision, string(a.cfg.Policy), pst.ProbeCount, pst.ProbeCostUS, pst.SelectorNS); er2 != nil {
				http.Error(w, er2.Error(), 500)
				return
			}
			if a.cfg.Trace != nil {
				_ = a.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "DECISION_RECORDED", Decision: "DEFER", Analyzer: runtimeAnalyzer, Revision: d.Revision, Reason: "MODEL_INCONSISTENCY", Policy: string(a.cfg.Policy)})
			}
			writeJSON(w, 500, protocol.AuthorityResponse{SchemaVersion: 1, DomainID: req.DomainID, CommandID: req.CommandID, IntentHash: req.IntentHash, Decision: "DEFER", Status: "NOT_ADMITTED", Reason: "MODEL_INCONSISTENCY", Revision: d.Revision})
			return
		}
		if er != nil {
			http.Error(w, er.Error(), 500)
			return
		}
		d, er = a.cfg.Store.GetDomain(req.DomainID)
		if er != nil {
			http.Error(w, er.Error(), 500)
			return
		}
		if d.Revision != pst.AnalyzedRevision {
			if a.cfg.Trace != nil {
				_ = a.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "REVISION_RECOMPUTE", Revision: d.Revision, Reason: fmt.Sprintf("analyzed_revision=%d", pst.AnalyzedRevision), Policy: string(a.cfg.Policy)})
			}
			continue
		}
		switch res.Verdict {
		case analyzer.Counterexample, analyzer.ConservativeBlock:
			if er := a.cfg.Store.RecordDecisionDetailed(req.DomainID, req.CommandID, "BLOCK", runtimeAnalyzer, res.Reason, d.Revision, string(a.cfg.Policy), pst.ProbeCount, pst.ProbeCostUS, pst.SelectorNS); er != nil {
				http.Error(w, er.Error(), 500)
				return
			}
			if a.cfg.Trace != nil {
				_ = a.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "DECISION_RECORDED", Decision: "BLOCK", Analyzer: runtimeAnalyzer, Revision: d.Revision, Reason: res.Reason, Policy: string(a.cfg.Policy)})
			}
			writeJSON(w, 409, protocol.AuthorityResponse{SchemaVersion: 1, DomainID: req.DomainID, CommandID: req.CommandID, IntentHash: req.IntentHash, Decision: "BLOCK", Status: "NOT_ADMITTED", Reason: res.Reason, Revision: d.Revision})
			return
		case analyzer.Deferred:
			if er := a.cfg.Store.RecordDecisionDetailed(req.DomainID, req.CommandID, "DEFER", runtimeAnalyzer, res.Reason, d.Revision, string(a.cfg.Policy), pst.ProbeCount, pst.ProbeCostUS, pst.SelectorNS); er != nil {
				http.Error(w, er.Error(), 500)
				return
			}
			if a.cfg.Trace != nil {
				_ = a.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "DECISION_RECORDED", Decision: "DEFER", Analyzer: runtimeAnalyzer, Revision: d.Revision, Reason: res.Reason, Policy: string(a.cfg.Policy)})
			}
			writeJSON(w, 503, protocol.AuthorityResponse{SchemaVersion: 1, DomainID: req.DomainID, CommandID: req.CommandID, IntentHash: req.IntentHash, Decision: "DEFER", Status: "NOT_ADMITTED", Reason: res.Reason, Revision: d.Revision})
			return
		case analyzer.NotApplicable:
			if er := a.cfg.Store.RecordDecisionDetailed(req.DomainID, req.CommandID, "REJECT", runtimeAnalyzer, res.Reason, d.Revision, string(a.cfg.Policy), pst.ProbeCount, pst.ProbeCostUS, pst.SelectorNS); er != nil {
				http.Error(w, er.Error(), 500)
				return
			}
			if a.cfg.Trace != nil {
				_ = a.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "DECISION_RECORDED", Decision: "REJECT", Analyzer: runtimeAnalyzer, Revision: d.Revision, Reason: res.Reason, Policy: string(a.cfg.Policy)})
			}
			writeJSON(w, 409, protocol.AuthorityResponse{SchemaVersion: 1, DomainID: req.DomainID, CommandID: req.CommandID, IntentHash: req.IntentHash, Decision: "REJECT", Status: "NOT_ADMITTED", Reason: res.Reason, Revision: d.Revision})
			return
		}
		if pst.ProbeCount > 0 {
			rec.Reason = "RELATIONAL_BELIEF_SAFE_AFTER_EVIDENCE"
		}
		if a.cfg.Trace != nil {
			_ = a.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "ADMISSION_PREPARE", Revision: pst.AnalyzedRevision, Policy: string(a.cfg.Policy)})
		}
		rev, er := a.cfg.Store.ReserveAndEnqueueAtRevision(rec, a.cfg.EndpointURL, runtimeAnalyzer, pst.AnalyzedRevision, func() error {
			if a.cfg.Faults != nil {
				a.cfg.Faults.Hit("BEFORE_RESERVATION_COMMIT", req.CommandID)
			}
			return nil
		})
		if errors.Is(er, journal.ErrRevisionConflict) {
			if a.cfg.Trace != nil {
				_ = a.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "REVISION_RETRY", Revision: d.Revision})
			}
			continue
		}
		if er != nil {
			http.Error(w, er.Error(), 500)
			return
		}
		if er := a.cfg.Store.AnnotateLatestDecision(req.DomainID, req.CommandID, string(a.cfg.Policy), pst.ProbeCount, pst.ProbeCostUS, pst.SelectorNS); er != nil {
			http.Error(w, er.Error(), 500)
			return
		}
		if a.cfg.Trace != nil {
			_ = a.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "RESERVATION_COMMITTED", Revision: rev, Policy: string(a.cfg.Policy)})
			_ = a.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "DECISION_RECORDED", Decision: "ADMITTED", Analyzer: runtimeAnalyzer, Revision: rev, Reason: rec.Reason, Policy: string(a.cfg.Policy)})
		}
		if a.cfg.Faults != nil {
			a.cfg.Faults.Hit("AFTER_RESERVATION_COMMIT_BEFORE_DISPATCH", req.CommandID)
		}
		if a.cfg.Dispatcher != nil {
			a.cfg.Dispatcher.Notify()
		}
		writeJSON(w, 202, protocol.AuthorityResponse{SchemaVersion: 1, DomainID: req.DomainID, CommandID: req.CommandID, IntentHash: req.IntentHash, Decision: "ADMITTED", Status: "PENDING", Reason: rec.Reason, Revision: rev})
		return
	}
	d, _ := a.cfg.Store.GetDomain(req.DomainID)
	if err := a.persistDecision(req, "DEFER", "REVISION_CONFLICT_BUDGET", d.Revision, runtimeAnalyzer); err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	writeJSON(w, 503, protocol.AuthorityResponse{SchemaVersion: 1, DomainID: req.DomainID, CommandID: req.CommandID, IntentHash: req.IntentHash, Decision: "DEFER", Status: "NOT_ADMITTED", Reason: "REVISION_CONFLICT_BUDGET", Revision: d.Revision})
}

func (a *Authority) handleGetCommand(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", 405)
		return
	}
	id := strings.TrimPrefix(r.URL.Path, "/v1/commands/")
	if id == "" {
		http.Error(w, "command id required", 400)
		return
	}
	rec, err := a.cfg.Store.GetCommand(a.cfg.Domain.ID, id)
	if errors.Is(err, journal.ErrNotFound) {
		http.Error(w, "not found", 404)
		return
	}
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	d, _ := a.cfg.Store.GetDomain(a.cfg.Domain.ID)
	writeJSON(w, 200, protocol.AuthorityResponse{SchemaVersion: 1, DomainID: rec.DomainID, CommandID: rec.CommandID, IntentHash: rec.IntentHash, Decision: rec.Decision, Status: rec.Status, Reason: rec.Reason, Revision: d.Revision})
}

func (a *Authority) fetchReceiptMeasured(cmd journal.CommandRecord) (protocol.Receipt, int, int64, int64, error) {
	u := strings.TrimRight(a.cfg.EndpointURL, "/") + "/v1/receipts/" + cmd.CommandID
	req, _ := http.NewRequest(http.MethodGet, u, nil)
	requestBytes := int64(len(http.MethodGet) + 1 + len(req.URL.RequestURI()))
	resp, err := a.hc.Do(req)
	if err != nil {
		return protocol.Receipt{}, 0, requestBytes, 0, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	responseBytes := int64(len(raw))
	if resp.StatusCode == 404 {
		return protocol.Receipt{SchemaVersion: 1, DomainID: cmd.DomainID, Generation: cmd.Generation, CommandID: cmd.CommandID, IntentHash: cmd.IntentHash, Outcome: "NOT_FOUND", Reason: "NO_OWNER_RECORD"}, 404, requestBytes, responseBytes, nil
	}
	if resp.StatusCode/100 != 2 {
		return protocol.Receipt{}, resp.StatusCode, requestBytes, responseBytes, fmt.Errorf("endpoint status %d: %s", resp.StatusCode, string(raw))
	}
	var rc protocol.Receipt
	if err = json.Unmarshal(raw, &rc); err != nil {
		return protocol.Receipt{}, resp.StatusCode, requestBytes, responseBytes, err
	}
	return rc, resp.StatusCode, requestBytes, responseBytes, nil
}

func (a *Authority) fetchReceipt(cmd journal.CommandRecord) (protocol.Receipt, int, error) {
	rc, status, _, _, err := a.fetchReceiptMeasured(cmd)
	return rc, status, err
}

func validateReceipt(cmd journal.CommandRecord, rc protocol.Receipt, dim int) error {
	return protocol.ValidateReceipt(rc, cmd.DomainID, cmd.Generation, cmd.CommandID, cmd.IntentHash, dim)
}

func (a *Authority) handleEvidenceQuery(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", 405)
		return
	}
	var q protocol.EvidenceQueryRequest
	if err := json.NewDecoder(r.Body).Decode(&q); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	if q.DomainID == "" {
		q.DomainID = a.cfg.Domain.ID
	}
	if q.SchemaVersion == 0 {
		q.SchemaVersion = 1
	}
	if q.SchemaVersion != 1 || q.DomainID != a.cfg.Domain.ID || q.CommandID == "" {
		http.Error(w, "invalid evidence query", 400)
		return
	}
	cmd, err := a.cfg.Store.GetCommand(q.DomainID, q.CommandID)
	if err != nil {
		if errors.Is(err, journal.ErrNotFound) {
			http.Error(w, "not found", 404)
		} else {
			http.Error(w, err.Error(), 500)
		}
		return
	}
	rc, _, err := a.fetchReceipt(cmd)
	if err != nil {
		writeJSON(w, 503, map[string]any{"outcome": "UNCERTAIN", "reason": err.Error()})
		return
	}
	if err = validateReceipt(cmd, rc, a.cfg.Domain.Dimension); err != nil {
		if a.cfg.Trace != nil {
			_ = a.cfg.Trace.Write(trace.Event{DomainID: cmd.DomainID, CommandID: cmd.CommandID, Event: "EVIDENCE_REJECTED", Reason: err.Error()})
		}
		writeJSON(w, 409, map[string]any{"error": "INVALID_EVIDENCE", "reason": err.Error()})
		return
	}
	rev, err := a.cfg.Store.RecordEvidence(journal.ReceiptRecord{DomainID: rc.DomainID, CommandID: rc.CommandID, IntentHash: rc.IntentHash, Outcome: rc.Outcome, OwnerVersion: rc.OwnerVersion, ReceiptVersion: rc.ReceiptVersion, State: rc.State}, rc.Reason, "RECEIPT_QUERY", false)
	if errors.Is(err, journal.ErrTerminalReversal) {
		a.noteEvidenceConflict(cmd.DomainID, cmd.CommandID, "RECEIPT_QUERY", cmd.Status, rc.Outcome, err.Error())
		writeJSON(w, 409, map[string]any{"error": "EVIDENCE_CONFLICT", "conflicts": []evidenceConflict{{CommandID: cmd.CommandID, Authority: cmd.Status, Owner: rc.Outcome}}})
		return
	}
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	if a.cfg.Trace != nil {
		_ = a.cfg.Trace.Write(trace.Event{DomainID: cmd.DomainID, CommandID: cmd.CommandID, Event: "EVIDENCE_COMMITTED", Revision: rev, Reason: rc.Outcome})
	}
	writeJSON(w, 200, map[string]any{"receipt": rc, "revision": rev})
}

func (a *Authority) handleBarrier(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", 405)
		return
	}
	if a.cfg.Profile != "E2" && !(a.cfg.Profile == "DEVICE" && a.cfg.EvidenceContract.Has(contract.CapFence)) {
		writeJSON(w, 409, map[string]any{"error": "BARRIER_UNSUPPORTED", "profile": a.cfg.Profile})
		return
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	var br protocol.BarrierRequest
	if err := json.NewDecoder(r.Body).Decode(&br); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	status, out := a.runBarrierLocked(br)
	writeJSON(w, status, out)
}

// runBarrierLocked executes the E2 barrier (owner-certified fence). The
// caller must hold a.mu. It is shared by POST /v1/barriers and the 061 EVICON
// resolution policy.
func (a *Authority) runBarrierLocked(br protocol.BarrierRequest) (int, any) {
	if br.SchemaVersion == 0 {
		br.SchemaVersion = protocol.SchemaVersion
	}
	if br.SchemaVersion != protocol.SchemaVersion {
		return 400, map[string]any{"error": "INVALID_BARRIER_SCHEMA"}
	}
	if br.DomainID == "" {
		br.DomainID = a.cfg.Domain.ID
	}
	if br.DomainID != a.cfg.Domain.ID || br.Token == "" {
		return 400, map[string]any{"error": "invalid barrier request"}
	}
	d, err := a.cfg.Store.GetDomain(br.DomainID)
	if err != nil {
		return 500, map[string]any{"error": err.Error()}
	}
	if br.Generation == 0 {
		br.Generation = d.Generation
	}
	if br.Generation != d.Generation {
		return 409, map[string]any{"error": "BARRIER_GENERATION_MISMATCH"}
	}

	// Token history is consulted before resolving an automatic cutoff. A retry
	// of t1 after t2 therefore replays t1's original meaning instead of creating
	// a new pending t1 at the current frontier.
	if prev, er := a.cfg.Store.GetBarrierLifecycle(br.DomainID, d.Generation, br.Token); er == nil {
		if br.CutoffSequence != 0 && br.CutoffSequence != prev.CutoffSequence {
			return 409, map[string]any{"error": "BARRIER_TOKEN_CONFLICT"}
		}
		br.CutoffSequence = prev.CutoffSequence
		if prev.Status == "COMPLETE" {
			return 200, map[string]any{"barrier": protocol.BarrierReceipt{SchemaVersion: 1, DomainID: prev.DomainID, Generation: prev.Generation, Token: prev.Token, CutoffSequence: prev.CutoffSequence, State: prev.Anchor, OwnerVersion: prev.OwnerVersion, Reason: "AUTHORITY_BARRIER_REPLAY"}, "revision": prev.CompleteRevision}
		}
	} else if !errors.Is(er, journal.ErrNotFound) {
		return 500, map[string]any{"error": er.Error()}
	} else {
		fullCut, er := a.cfg.Store.MaxAdmittedSequence(br.DomainID)
		if er != nil {
			return 500, map[string]any{"error": er.Error()}
		}
		if br.CutoffSequence == 0 {
			br.CutoffSequence = fullCut
		} else if br.CutoffSequence != fullCut {
			return 409, map[string]any{"error": "PARTIAL_BARRIER_UNSUPPORTED", "requested_cutoff": br.CutoffSequence, "full_frontier": fullCut}
		}
	}

	err = a.cfg.Store.BeginBarrier(br.DomainID, br.Token, d.Generation, br.CutoffSequence, d.Revision)
	if errors.Is(err, journal.ErrBarrierAlreadyComplete) {
		prev, er := a.cfg.Store.GetBarrierLifecycle(br.DomainID, d.Generation, br.Token)
		if er != nil {
			return 500, map[string]any{"error": er.Error()}
		}
		return 200, map[string]any{"barrier": protocol.BarrierReceipt{SchemaVersion: 1, DomainID: prev.DomainID, Generation: prev.Generation, Token: prev.Token, CutoffSequence: prev.CutoffSequence, State: prev.Anchor, OwnerVersion: prev.OwnerVersion, Reason: "AUTHORITY_BARRIER_REPLAY"}, "revision": prev.CompleteRevision}
	}
	if errors.Is(err, journal.ErrBarrierInProgress) {
		return 409, map[string]any{"error": "BARRIER_IN_PROGRESS"}
	}
	if errors.Is(err, journal.ErrBarrierAborted) {
		return 409, map[string]any{"error": "BARRIER_ABORTED"}
	}
	if errors.Is(err, journal.ErrIdentityConflict) {
		return 409, map[string]any{"error": "BARRIER_TOKEN_CONFLICT"}
	}
	if errors.Is(err, journal.ErrRevisionConflict) {
		return 409, map[string]any{"error": "BARRIER_REVISION_CONFLICT"}
	}
	if err != nil {
		return 500, map[string]any{"error": err.Error()}
	}
	if a.cfg.Trace != nil {
		_ = a.cfg.Trace.Write(trace.Event{DomainID: br.DomainID, Event: "BARRIER_PAUSE_COMMITTED", Revision: d.Revision, Reason: br.Token})
	}
	raw, _ := json.Marshal(br)
	u := strings.TrimRight(a.cfg.EndpointURL, "/") + "/v1/barriers"
	req, _ := http.NewRequest(http.MethodPost, u, bytes.NewReader(raw))
	req.Header.Set("content-type", "application/json")
	resp, err := a.hc.Do(req)
	if err != nil {
		return 503, map[string]any{"error": "BARRIER_QUERY_FAILED", "reason": err.Error()}
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode/100 != 2 {
		return resp.StatusCode, map[string]any{"error": string(body)}
	}
	var rc protocol.BarrierReceipt
	if err = json.Unmarshal(body, &rc); err != nil {
		return 500, map[string]any{"error": err.Error()}
	}
	if rc.SchemaVersion != protocol.SchemaVersion || rc.DomainID != br.DomainID || rc.Generation != d.Generation || rc.Token != br.Token || rc.CutoffSequence != br.CutoffSequence || rc.OwnerVersion == 0 || len(rc.State) != d.Dimension {
		return 409, map[string]any{"error": "INVALID_BARRIER_EVIDENCE"}
	}
	bound := domain.HardBound{ComponentMin: d.ComponentMin, ComponentMax: d.ComponentMax, SumMax: d.SumMax}
	ok, er := bound.Check(domain.State(rc.State))
	if er != nil || !ok {
		return 409, map[string]any{"error": "INVALID_BARRIER_STATE"}
	}
	recs := make([]journal.ReceiptRecord, 0, len(rc.Receipts))
	var uncertain []string
	var conflicts []evidenceConflict
	for _, rr := range rc.Receipts {
		cmd, er := a.cfg.Store.GetCommand(br.DomainID, rr.CommandID)
		if errors.Is(er, journal.ErrNotFound) {
			continue
		}
		if er != nil {
			return 500, map[string]any{"error": er.Error()}
		}
		if !(rr.Outcome == "APPLIED" || rr.Outcome == "STOPPED") && rr.CommandID == cmd.CommandID && rr.IntentHash == cmd.IntentHash {
			// The owner cannot certify this identity (e.g. a claim left by a
			// crash between effect and receipt). Its state cannot be anchored.
			uncertain = append(uncertain, rr.CommandID)
			continue
		}
		if er = protocol.ValidateReceipt(rr, cmd.DomainID, cmd.Generation, cmd.CommandID, cmd.IntentHash, d.Dimension); er != nil {
			_ = a.cfg.Store.AbortBarrier(br.DomainID, d.Generation, br.Token, "INVALID_BARRIER_RECEIPT")
			a.traceBarrierAbort(br, "INVALID_BARRIER_RECEIPT:"+rr.CommandID)
			return 409, map[string]any{"error": "INVALID_BARRIER_RECEIPT", "command_id": rr.CommandID}
		}
		if terminalStatus(cmd.Status) && cmd.Status != rr.Outcome {
			// Release 068: the owner contradicts a terminal outcome the authority has already
			// committed (067 testbed: an emulator sealed a late copy of an APPLIED command as
			// STOPPED). Terminal evidence is monotone, so this barrier cannot complete; it is
			// aborted and the conflict escalated instead of retried every second.
			conflicts = append(conflicts, evidenceConflict{CommandID: rr.CommandID, Authority: cmd.Status, Owner: rr.Outcome})
			continue
		}
		recs = append(recs, journal.ReceiptRecord{DomainID: rr.DomainID, CommandID: rr.CommandID, IntentHash: rr.IntentHash, Outcome: rr.Outcome, OwnerVersion: rr.OwnerVersion, ReceiptVersion: rr.ReceiptVersion, State: rr.State})
	}
	if len(conflicts) > 0 {
		return a.barrierConflict(br, d.Generation, conflicts)
	}
	if len(uncertain) > 0 {
		// Release 062 (review RV06): a full-frontier fence cannot complete while
		// the owner cannot certify an identity below it. Abort (un-pause, seal
		// nothing, anchor nothing); the identities stay ambiguous.
		if er := a.cfg.Store.AbortBarrier(br.DomainID, d.Generation, br.Token, "OWNER_UNCERTAIN"); er != nil {
			return 500, map[string]any{"error": er.Error()}
		}
		a.markOwnerUncertain(uncertain...)
		a.traceBarrierAbort(br, "OWNER_UNCERTAIN:"+strings.Join(uncertain, ","))
		return 409, map[string]any{"error": "BARRIER_UNCERTAIN_RECEIPTS", "command_ids": uncertain}
	}
	// EVICON seals unreported identities below an owner-certified cutoff so
	// that clients learn STOPPED and can retry the intent (fence-then-retry).
	rev, sealed, err := a.cfg.Store.CommitBarrierLifecycleSeal(br.DomainID, br.Token, d.Generation, rc.CutoffSequence, rc.State, rc.OwnerVersion, recs,
		a.cfg.Policy == probe.EVICON && a.cfg.EvidenceContract.Has(contract.CapFence))
	if err == nil && a.cfg.Trace != nil {
		for _, r := range recs {
			_ = a.cfg.Trace.Write(trace.Event{DomainID: br.DomainID, CommandID: r.CommandID, Event: "EVIDENCE_COMMITTED", Revision: rev, Outcome: r.Outcome, Reason: "BARRIER_RECEIPT:" + br.Token})
		}
		for _, cid := range sealed {
			_ = a.cfg.Trace.Write(trace.Event{DomainID: br.DomainID, CommandID: cid, Event: "EVIDENCE_COMMITTED", Revision: rev, Outcome: "STOPPED", Reason: "BARRIER_SEALED_UNCLAIMED:" + br.Token})
		}
	}
	if errors.Is(err, journal.ErrBarrierAlreadyComplete) {
		prev, _ := a.cfg.Store.GetBarrierLifecycle(br.DomainID, d.Generation, br.Token)
		return 200, map[string]any{"barrier": protocol.BarrierReceipt{SchemaVersion: 1, DomainID: prev.DomainID, Generation: prev.Generation, Token: prev.Token, CutoffSequence: prev.CutoffSequence, State: prev.Anchor, OwnerVersion: prev.OwnerVersion, Reason: "AUTHORITY_BARRIER_REPLAY"}, "revision": prev.CompleteRevision}
	}
	if errors.Is(err, journal.ErrTerminalReversal) {
		// a terminal outcome committed concurrently (dispatch reply) contradicts the owner
		return a.barrierConflict(br, d.Generation, []evidenceConflict{{Detail: err.Error()}})
	}
	if err != nil {
		return 500, map[string]any{"error": err.Error()}
	}
	if a.cfg.Trace != nil {
		_ = a.cfg.Trace.Write(trace.Event{DomainID: br.DomainID, Event: "BARRIER_ANCHOR_COMMITTED", Revision: rev, Reason: br.Token})
	}
	return 200, map[string]any{"barrier": rc, "revision": rev}
}

func (a *Authority) handleRuntime(w http.ResponseWriter, r *http.Request) {
	counts, err := a.cfg.Store.Counts()
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	pragmas, err := a.cfg.Store.Pragmas()
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	pending, _ := a.cfg.Store.PendingOutboxCount()
	dom, _ := a.cfg.Store.GetDomain(a.cfg.Domain.ID)
	writeJSON(w, 200, map[string]any{"counts": counts, "pragmas": pragmas, "profile": a.cfg.Profile, "pending_outbox": pending, "domain_revision": dom.Revision, "cutoff_sequence": dom.CutoffSequence})
}
func (a *Authority) handleBelief(w http.ResponseWriter, r *http.Request) {
	// The debug snapshot is measurement evidence, so bind the domain metadata,
	// command rows and rebuilt belief to one stable semantic revision. Store
	// mutations advance Domain.Revision; a bounded double-read detects a
	// concurrent mutation without making this read-only endpoint take locks or
	// modify runtime state.
	for attempt := 0; attempt < 3; attempt++ {
		d, err := a.cfg.Store.GetDomain(a.cfg.Domain.ID)
		if err != nil {
			writeJSON(w, 500, map[string]any{"schema_version": statsSchemaVersion, "status": "INTERNAL_ERROR", "error_kind": "DOMAIN_READ_ERROR", "error": err.Error(), "configuration_count": nil})
			return
		}
		cmds, err := a.cfg.Store.ListCommandsForBelief(d.ID, d.CutoffSequence)
		if err != nil {
			writeJSON(w, 500, map[string]any{"schema_version": statsSchemaVersion, "status": "INTERNAL_ERROR", "error_kind": "COMMAND_READ_ERROR", "error": err.Error(), "domain_revision": d.Revision, "cutoff_sequence": d.CutoffSequence, "configuration_count": nil})
			return
		}
		b, stats, buildErr := evidence.BuildBeliefWithStats(d, cmds, a.cfg.MaxConfigurations)
		after, err := a.cfg.Store.GetDomain(d.ID)
		if err != nil {
			writeJSON(w, 500, map[string]any{"schema_version": statsSchemaVersion, "status": "INTERNAL_ERROR", "error_kind": "DOMAIN_RECHECK_ERROR", "error": err.Error(), "domain_revision": d.Revision, "cutoff_sequence": d.CutoffSequence, "configuration_count": nil})
			return
		}
		if after.Revision != d.Revision || after.CutoffSequence != d.CutoffSequence {
			if attempt < 2 {
				continue
			}
			writeJSON(w, 409, map[string]any{
				"schema_version": statsSchemaVersion, "status": "SNAPSHOT_CHANGED_DURING_READ", "error_kind": "BELIEF_SNAPSHOT_RACE",
				"error": "domain revision/cutoff changed while rebuilding online belief", "enumeration_algorithm": stats.Algorithm,
				"enumeration_limit": stats.Limit, "explored_configurations": stats.ExploredConfigurations,
				"enumeration_complete": false, "configuration_count": nil, "domain_revision": d.Revision,
				"observed_domain_revision_after": after.Revision, "cutoff_sequence": d.CutoffSequence,
				"observed_cutoff_sequence_after": after.CutoffSequence,
			})
			return
		}
		base := map[string]any{
			"schema_version":          statsSchemaVersion,
			"domain_id":               d.ID,
			"enumeration_algorithm":   stats.Algorithm,
			"enumeration_limit":       stats.Limit,
			"explored_configurations": stats.ExploredConfigurations,
			"enumeration_complete":    stats.Complete,
			"configuration_count":     stats.ConfigurationCount,
			"domain_revision":         d.Revision,
			"cutoff_sequence":         d.CutoffSequence,
		}
		if buildErr != nil {
			statusCode := 500
			status := "INTERNAL_ERROR"
			kind := "BELIEF_BUILD_ERROR"
			switch {
			case errors.Is(buildErr, evidence.ErrBeliefBudget):
				statusCode, status, kind = 503, "ENUMERATION_BUDGET_EXCEEDED", "BELIEF_ENUMERATION_BUDGET"
			case errors.Is(buildErr, evidence.ErrModelInconsistency):
				statusCode, status, kind = 409, "MODEL_INCONSISTENCY", "MODEL_INCONSISTENCY"
			case errors.Is(buildErr, evidence.ErrInvalidCommand):
				statusCode, status, kind = 500, "INVALID_DURABLE_COMMAND", "INVALID_COMMAND"
			case errors.Is(buildErr, domain.ErrInt64Overflow):
				statusCode, status, kind = 500, "MODEL_ARITHMETIC_ERROR", "INT64_OVERFLOW"
			}
			base["status"] = status
			base["error_kind"] = kind
			base["error"] = buildErr.Error()
			base["configurations"] = []belief.Configuration{}
			writeJSON(w, statusCode, base)
			return
		}
		base["status"] = "OK"
		base["generation"] = b.Generation
		base["dimension"] = b.Dimension
		base["commands"] = b.Registry
		base["configurations"] = b.Configurations
		writeJSON(w, 200, base)
		return
	}
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("content-type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

// evidenceConflict is one identity whose owner evidence contradicts the committed
// terminal outcome (release 068).
type evidenceConflict struct {
	CommandID string `json:"command_id,omitempty"`
	Authority string `json:"authority,omitempty"` // committed outcome
	Owner     string `json:"owner,omitempty"`     // outcome the owner now reports
	Detail    string `json:"detail,omitempty"`
}

func terminalStatus(s string) bool { return s == "APPLIED" || s == "STOPPED" }

// barrierConflict aborts the barrier (un-pause, seal and anchor nothing), records each
// conflict (trace EVIDENCE_CONFLICT) and marks the domain so that the resolver escalates
// instead of fencing again. Answer: 409 EVIDENCE_CONFLICT.
func (a *Authority) barrierConflict(br protocol.BarrierRequest, generation uint64, conflicts []evidenceConflict) (int, any) {
	if er := a.cfg.Store.AbortBarrier(br.DomainID, generation, br.Token, "EVIDENCE_CONFLICT"); er != nil {
		return 500, map[string]any{"error": er.Error()}
	}
	for _, c := range conflicts {
		a.noteEvidenceConflict(br.DomainID, c.CommandID, "BARRIER:"+br.Token, c.Authority, c.Owner, c.Detail)
	}
	a.traceBarrierAbort(br, "EVIDENCE_CONFLICT")
	return 409, map[string]any{"error": "EVIDENCE_CONFLICT", "conflicts": conflicts}
}

// noteEvidenceConflict records one contradiction between owner evidence and a committed
// terminal outcome. The authority keeps the committed outcome (terminal evidence is
// monotone), stops backward resolution for the domain and leaves the conflict to an
// operator: no evidence from an owner that contradicts itself can be trusted.
func (a *Authority) noteEvidenceConflict(domainID, commandID, channel, committed, owner, detail string) {
	a.queriedMu.Lock()
	if a.conflicted == nil {
		a.conflicted = map[string]time.Time{}
	}
	if _, seen := a.conflicted[domainID]; !seen {
		a.conflicted[domainID] = time.Now()
	}
	a.queriedMu.Unlock()
	if a.cfg.Trace != nil {
		_ = a.cfg.Trace.Write(trace.Event{DomainID: domainID, CommandID: commandID, Event: "EVIDENCE_CONFLICT", Policy: string(a.cfg.Policy), Outcome: owner,
			Reason: truncate(fmt.Sprintf("channel=%s;committed=%s;owner=%s;%s", channel, committed, owner, detail), 400)})
	}
}

// evidenceConflicted reports whether owner evidence for the domain has contradicted a
// committed terminal outcome during this authority's lifetime (release 068).
func (a *Authority) evidenceConflicted(domainID string) bool {
	a.queriedMu.Lock()
	defer a.queriedMu.Unlock()
	_, ok := a.conflicted[domainID]
	return ok
}

func (a *Authority) traceBarrierAbort(br protocol.BarrierRequest, reason string) {
	if a.cfg.Trace != nil {
		_ = a.cfg.Trace.Write(trace.Event{DomainID: br.DomainID, Event: "BARRIER_ABORTED", Reason: truncate(br.Token+":"+reason, 400)})
	}
}

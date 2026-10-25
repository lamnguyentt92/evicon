package trace

import (
	"encoding/json"
	"os"
	"sync"
	"time"
)

type Writer struct {
	mu        sync.Mutex
	path      string
	processID string
	runID     string
	method    string
	seed      int64
	sequence  uint64
}

type Event struct {
	RunID          string `json:"run_id"`
	Method         string `json:"method"`
	Seed           int64  `json:"seed"`
	ProcessID      string `json:"process_id"`
	DomainID       string `json:"domain_id,omitempty"`
	CommandID      string `json:"command_id,omitempty"`
	Event          string `json:"event"`
	Decision       string `json:"decision,omitempty"`
	Analyzer       string `json:"analyzer,omitempty"`
	LocalSequence  uint64 `json:"local_sequence"`
	MonotonicNS    int64  `json:"monotonic_ns"`
	WallTime       string `json:"wall_time"`
	Revision       uint64 `json:"revision,omitempty"`
	Reason         string `json:"reason,omitempty"`
	Policy         string `json:"policy,omitempty"`
	QueryCommandID string `json:"query_command_id,omitempty"`
	ProbeIndex     int64  `json:"probe_index,omitempty"`
	// Attempt (release 077): the dispatch attempt of an OUTBOX_DISPATCH_ATTEMPT (1 = first send).
	Attempt               int64    `json:"attempt,omitempty"`
	ExpectedCostUS        int64    `json:"expected_cost_us,omitempty"`
	ActualCostUS          int64    `json:"actual_cost_us,omitempty"`
	QueryRequestBytes     int64    `json:"query_request_bytes,omitempty"`
	QueryResponseBytes    int64    `json:"query_response_bytes,omitempty"`
	Outcome               string   `json:"outcome,omitempty"`
	Informative           *bool    `json:"informative,omitempty"`
	OutcomeModelHash      string   `json:"outcome_model_hash,omitempty"`
	PredictedSafeProb     *float64 `json:"predicted_safe_probability,omitempty"`
	PredictedUtility      *float64 `json:"predicted_utility,omitempty"`
	PlannerFallbackReason string   `json:"planner_fallback_reason,omitempty"`
	PlannerDepth          int      `json:"planner_depth,omitempty"`
	PlannerCacheHit       *bool    `json:"planner_cache_hit,omitempty"`
	PlannerElapsedNS      int64    `json:"planner_elapsed_ns,omitempty"`
	PlannerBudgetNS       int64    `json:"planner_budget_ns,omitempty"`
	PlannerCacheEnabled   *bool    `json:"planner_cache_enabled,omitempty"`
	FaultIntervalID       uint64   `json:"fault_interval_id,omitempty"`
	FaultToken            string   `json:"fault_token,omitempty"`
	EffectiveStartUTC     string   `json:"effective_start_utc,omitempty"`
	EffectiveEndUTC       string   `json:"effective_end_utc,omitempty"`
	DecisionUTC           string   `json:"decision_utc,omitempty"`
	DecisionMonotonicNS   int64    `json:"decision_monotonic_ns,omitempty"`
	WaitNS                int64    `json:"wait_ns,omitempty"`
}

var start = time.Now()

// MonotonicNS uses the same process-local clock as persisted trace events.
func MonotonicNS() int64 { return time.Since(start).Nanoseconds() }

func (w *Writer) RunID() string {
	if w == nil {
		return ""
	}
	return w.runID
}

func New(path, processID, runID, method string, seed int64) *Writer {
	return &Writer{path: path, processID: processID, runID: runID, method: method, seed: seed}
}
func (w *Writer) Write(e Event) error {
	if w == nil || w.path == "" {
		return nil
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	e.RunID = w.runID
	e.Method = w.method
	e.Seed = w.seed
	e.ProcessID = w.processID
	e.LocalSequence = w.sequence
	w.sequence++
	e.MonotonicNS = time.Since(start).Nanoseconds()
	e.WallTime = time.Now().UTC().Format(time.RFC3339Nano)
	b, err := json.Marshal(e)
	if err != nil {
		return err
	}
	f, err := os.OpenFile(w.path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	if _, err = f.Write(append(b, '\n')); err != nil {
		return err
	}
	return f.Sync()
}

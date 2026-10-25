package main

import (
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"sync"
	"time"

	"evicon/internal/trace"
)

const maxEvidenceOutageDuration = 24 * time.Hour

// evidenceOutageController is evaluation-only. Each armed interval has a
// monotonically increasing interval ID. START is durably traced before the
// interval becomes visible to receipt handlers; END is emitted exactly once by
// either the timer or lazy expiry. Old timer callbacks cannot close a newer
// interval.
type evidenceOutageController struct {
	mu                sync.Mutex
	active            bool
	token             string
	intervalID        uint64
	startedAt         time.Time
	until             time.Time
	requestedDuration time.Duration
	timer             *time.Timer
	ended             bool
	traceError        string
	tr                *trace.Writer
	domainID          string
	writeEvent        func(trace.Event) error
	maxTraceLatencyNS int64
}

type evidenceOutageRequest struct {
	Token      string `json:"token"`
	DurationMS int64  `json:"duration_ms"`
}

func newEvidenceOutageController(tr *trace.Writer, domainID string) *evidenceOutageController {
	return &evidenceOutageController{tr: tr, domainID: domainID, writeEvent: tr.Write}
}

func (c *evidenceOutageController) emitLocked(e trace.Event) error {
	start := time.Now()
	var err error
	if c.writeEvent != nil {
		err = c.writeEvent(e)
	}
	lag := time.Since(start).Nanoseconds()
	if lag > c.maxTraceLatencyNS {
		c.maxTraceLatencyNS = lag
	}
	if err != nil {
		c.traceError = err.Error()
	}
	if lag > int64(100*time.Millisecond) {
		c.traceError = "trace persistence exceeds registered 100ms bound"
	}
	return err
}

func validDurationMS(ms int64) (time.Duration, error) {
	if ms <= 0 {
		return 0, fmt.Errorf("duration_ms must be positive")
	}
	if ms > int64(maxEvidenceOutageDuration/time.Millisecond) {
		return 0, fmt.Errorf("duration_ms exceeds maximum")
	}
	if ms > math.MaxInt64/int64(time.Millisecond) {
		return 0, fmt.Errorf("duration_ms overflows time.Duration")
	}
	return time.Duration(ms) * time.Millisecond, nil
}

func (c *evidenceOutageController) arm(token string, duration time.Duration) (map[string]any, error) {
	if token == "" {
		return nil, fmt.Errorf("token required")
	}
	if duration <= 0 || duration > maxEvidenceOutageDuration {
		return nil, fmt.Errorf("duration out of range")
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	now := time.Now()
	if c.active {
		if c.token == token {
			if c.requestedDuration != duration {
				return nil, fmt.Errorf("same token reused with different duration")
			}
			return c.snapshotLocked(now), nil
		}
		return nil, fmt.Errorf("another evidence outage is already active")
	}
	// Idempotent replay of a completed request returns the original interval;
	// it never creates a second scientific outage with the same identity.
	if c.ended && c.token == token {
		if c.requestedDuration != duration {
			return nil, fmt.Errorf("same token reused with different duration")
		}
		return c.snapshotLocked(now), nil
	}
	c.intervalID++
	id := c.intervalID
	c.token = token
	c.requestedDuration = duration
	c.ended = false
	c.traceError = ""
	c.maxTraceLatencyNS = 0
	c.active = false
	// START is serialized while holding c.mu. Receipt handlers cannot observe
	// active=true until the trace record has been accepted by the writer.
	start := time.Now()
	end := start.Add(duration)
	{
		if err := c.emitLocked(trace.Event{DomainID: c.domainID, Event: "EVIDENCE_OUTAGE_STARTED", FaultIntervalID: id, FaultToken: token,
			EffectiveStartUTC: start.UTC().Format(time.RFC3339Nano), EffectiveEndUTC: end.UTC().Format(time.RFC3339Nano), Reason: fmt.Sprintf("duration_ms=%d", duration.Milliseconds())}); err != nil {
			c.token = ""
			c.requestedDuration = 0
			return nil, fmt.Errorf("trace START: %w", err)
		}
	}
	c.startedAt = start
	c.until = end
	c.active = true
	if c.timer != nil {
		c.timer.Stop()
	}
	remaining := time.Until(end)
	if remaining < 0 {
		remaining = 0
	}
	c.timer = time.AfterFunc(remaining, func() { c.finish(id, token, "timer") })
	return c.snapshotLocked(time.Now()), nil
}

func (c *evidenceOutageController) finish(id uint64, token, reason string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.finishLocked(id, token, reason)
}

func (c *evidenceOutageController) finishLocked(id uint64, token, reason string) {
	if id != c.intervalID || token != c.token || c.ended {
		return
	}
	// finish is idempotent and owns the only transition that emits END.
	c.active = false
	c.ended = true
	effectiveEnd := c.until
	{
		if err := c.emitLocked(trace.Event{DomainID: c.domainID, Event: "EVIDENCE_OUTAGE_ENDED", FaultIntervalID: id, FaultToken: token,
			EffectiveStartUTC: c.startedAt.UTC().Format(time.RFC3339Nano), EffectiveEndUTC: effectiveEnd.UTC().Format(time.RFC3339Nano), Reason: reason}); err != nil {
			c.traceError = fmt.Sprintf("trace END: %v", err)
		}
	}
}

func (c *evidenceOutageController) isActive() (bool, string, uint64, time.Time) {
	c.mu.Lock()
	now := time.Now()
	if c.active && !now.Before(c.until) {
		c.finishLocked(c.intervalID, c.token, "lazy-expiry")
	}
	active, tok, id, until := c.active, c.token, c.intervalID, c.until
	c.mu.Unlock()
	return active, tok, id, until
}

// blockReceipt serializes the decision and durable event with END. No handler
// can append BLOCKED after a timer has already ended its interval.
func (c *evidenceOutageController) blockReceipt(commandID string) (bool, map[string]any, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	now := time.Now() // after acquiring the mutex, never a stale waiting timestamp
	if c.active && !now.Before(c.until) {
		c.finishLocked(c.intervalID, c.token, "lazy-expiry")
	}
	if !c.active {
		return false, nil, nil
	}
	e := trace.Event{DomainID: c.domainID, CommandID: commandID, Event: "EVIDENCE_QUERY_BLOCKED_BY_OUTAGE", FaultIntervalID: c.intervalID, FaultToken: c.token,
		EffectiveStartUTC: c.startedAt.UTC().Format(time.RFC3339Nano), EffectiveEndUTC: c.until.UTC().Format(time.RFC3339Nano),
		DecisionUTC: now.UTC().Format(time.RFC3339Nano), DecisionMonotonicNS: trace.MonotonicNS(), Reason: "receipt-evidence-only"}
	if err := c.emitLocked(e); err != nil {
		return true, nil, err
	}
	return true, map[string]any{"error": "EVIDENCE_OUTAGE_ACTIVE", "token": c.token, "interval_id": c.intervalID, "command_id": commandID,
		"decision_utc": e.DecisionUTC, "decision_monotonic_ns": e.DecisionMonotonicNS}, nil
}

func (c *evidenceOutageController) snapshotLocked(now time.Time) map[string]any {
	remaining := int64(0)
	if c.active && c.until.After(now) {
		remaining = c.until.Sub(now).Milliseconds()
	}
	return map[string]any{"schema_version": 3, "run_id": c.tr.RunID(), "active": c.active, "ended": c.ended, "token": c.token, "interval_id": c.intervalID,
		"domain_id": c.domainID, "started_at_utc": c.startedAt.UTC().Format(time.RFC3339Nano), "until_utc": c.until.UTC().Format(time.RFC3339Nano), "remaining_ms": remaining, "duration_ms": c.requestedDuration.Milliseconds(), "trace_error": c.traceError, "max_trace_latency_ns": c.maxTraceLatencyNS}
}
func (c *evidenceOutageController) snapshot() map[string]any {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.snapshotLocked(time.Now())
}

func (c *evidenceOutageController) register(mux *http.ServeMux) {
	mux.HandleFunc("/_test/evidence-outage", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			writeJSON(w, http.StatusOK, c.snapshot())
		case http.MethodPost:
			var req evidenceOutageRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				http.Error(w, err.Error(), http.StatusBadRequest)
				return
			}
			d, err := validDurationMS(req.DurationMS)
			if err != nil {
				http.Error(w, err.Error(), http.StatusBadRequest)
				return
			}
			out, err := c.arm(req.Token, d)
			if err != nil {
				http.Error(w, err.Error(), http.StatusConflict)
				return
			}
			writeJSON(w, http.StatusOK, out)
		default:
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		}
	})
}

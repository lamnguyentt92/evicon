package main

import (
	"errors"
	"evicon/internal/trace"
	"strings"
	"testing"
	"time"
)

func TestOutageDecisionAndEndAreSerialized(t *testing.T) {
	c := newEvidenceOutageController(nil, "d")
	entered, release, finished := make(chan struct{}), make(chan struct{}), make(chan struct{})
	var events []trace.Event
	c.writeEvent = func(e trace.Event) error {
		if e.Event == "EVIDENCE_QUERY_BLOCKED_BY_OUTAGE" {
			close(entered)
			<-release
		}
		events = append(events, e)
		return nil
	}
	if _, err := c.arm("ordered", time.Second); err != nil {
		t.Fatal(err)
	}
	blockedDone := make(chan error, 1)
	go func() {
		blocked, body, err := c.blockReceipt("control")
		if !blocked || body == nil {
			err = errors.New("block decision missing")
		}
		blockedDone <- err
	}()
	<-entered
	go func() { c.finish(1, "ordered", "test-timer"); close(finished) }()
	select {
	case <-finished:
		t.Fatal("END passed the in-progress BLOCKED persistence")
	case <-time.After(10 * time.Millisecond):
	}
	close(release)
	if err := <-blockedDone; err != nil {
		t.Fatal(err)
	}
	<-finished
	if len(events) != 3 || events[1].Event != "EVIDENCE_QUERY_BLOCKED_BY_OUTAGE" || events[2].Event != "EVIDENCE_OUTAGE_ENDED" {
		t.Fatalf("wrong event order: %+v", events)
	}
	decision, err := time.Parse(time.RFC3339Nano, events[1].DecisionUTC)
	if err != nil || decision.Before(c.startedAt) || !decision.Before(c.until) || events[1].DecisionMonotonicNS <= 0 {
		t.Fatalf("invalid decision clock: %+v", events[1])
	}
}

func TestOutageMutexWaitCannotUseStaleNow(t *testing.T) {
	c := newEvidenceOutageController(nil, "d")
	if _, err := c.arm("wait", 20*time.Millisecond); err != nil {
		t.Fatal(err)
	}
	c.mu.Lock()
	done := make(chan bool, 1)
	go func() { blocked, _, _ := c.blockReceipt("late"); done <- blocked }()
	time.Sleep(35 * time.Millisecond)
	c.mu.Unlock()
	if <-done {
		t.Fatal("handler waiting past expiry used stale pre-lock time")
	}
}

func TestOutageBlockedWriteErrorPropagates(t *testing.T) {
	c := newEvidenceOutageController(nil, "d")
	c.writeEvent = func(e trace.Event) error {
		if e.Event == "EVIDENCE_QUERY_BLOCKED_BY_OUTAGE" {
			return errors.New("fsync failed")
		}
		return nil
	}
	if _, err := c.arm("error", time.Second); err != nil {
		t.Fatal(err)
	}
	blocked, _, err := c.blockReceipt("control")
	if !blocked || err == nil || c.snapshot()["trace_error"] != "fsync failed" {
		t.Fatal("BLOCKED trace failure was discarded")
	}
	c.finish(1, "error", "cleanup")
}

func TestOutageSlowStartAndEndInvalidatePersistenceBound(t *testing.T) {
	for _, phase := range []string{"EVIDENCE_OUTAGE_STARTED", "EVIDENCE_OUTAGE_ENDED"} {
		t.Run(phase, func(t *testing.T) {
			c := newEvidenceOutageController(nil, "d")
			c.writeEvent = func(e trace.Event) error {
				if e.Event == phase {
					time.Sleep(110 * time.Millisecond)
				}
				return nil
			}
			if _, err := c.arm("slow", time.Second); err != nil {
				t.Fatal(err)
			}
			c.finish(1, "slow", "cleanup")
			if !strings.Contains(c.snapshot()["trace_error"].(string), "100ms") {
				t.Fatal("slow persistence did not invalidate interval")
			}
		})
	}
}

func TestEvidenceOutageIntervalAndIdempotentToken(t *testing.T) {
	c := newEvidenceOutageController(nil, "d")
	if _, err := c.arm("x", 30*time.Millisecond); err != nil {
		t.Fatal(err)
	}
	active, token, _, _ := c.isActive()
	if !active || token != "x" {
		t.Fatalf("unexpected active/token %v %q", active, token)
	}
	if _, err := c.arm("x", 30*time.Millisecond); err != nil {
		t.Fatalf("same token must be idempotent: %v", err)
	}
	if _, err := c.arm("y", 30*time.Millisecond); err == nil {
		t.Fatal("different active token must conflict")
	}
	time.Sleep(45 * time.Millisecond)
	active, _, _, _ = c.isActive()
	if active {
		t.Fatal("outage must end after requested duration")
	}
}

func TestEvidenceOutageStaleFinishCannotCloseNewInterval(t *testing.T) {
	c := newEvidenceOutageController(nil, "d")
	first, err := c.arm("first", 5*time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	id1 := first["interval_id"].(uint64)
	time.Sleep(10 * time.Millisecond)
	if active, _, _, _ := c.isActive(); active {
		t.Fatal("first interval should have expired")
	}
	second, err := c.arm("second", 50*time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	id2 := second["interval_id"].(uint64)
	if id2 <= id1 {
		t.Fatalf("interval IDs must increase: first=%d second=%d", id1, id2)
	}
	c.finish(id1, "first", "simulated-stale-callback")
	active, tok, gotID, _ := c.isActive()
	if !active || tok != "second" || gotID != id2 {
		t.Fatalf("stale callback closed/replaced new interval: active=%v token=%q id=%d", active, tok, gotID)
	}
}

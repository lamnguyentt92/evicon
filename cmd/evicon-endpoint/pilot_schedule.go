package main

import (
	"encoding/json"
	"fmt"
	"os"
	"sync"
	"time"
)

type pilotCommandBehavior struct {
	ForcedOutcome       string `json:"forced_outcome,omitempty"`
	Reason              string `json:"reason,omitempty"`
	EffectReplyHideMS   int    `json:"effect_reply_hide_ms,omitempty"`
	EffectReplyDelayMS  int    `json:"effect_reply_delay_ms,omitempty"` // one-shot post-commit reply delay for acknowledged recovery cuts
	ReceiptQueryDelayMS int    `json:"receipt_query_delay_ms,omitempty"`
}

type pilotScheduleFile struct {
	SchemaVersion int                             `json:"schema_version"`
	Commands      map[string]pilotCommandBehavior `json:"commands"`
}

type pilotSchedule struct {
	file      pilotScheduleFile
	mu        sync.Mutex
	hideUntil map[string]time.Time
	delayed   map[string]bool
}

func loadPilotSchedule(path string) (*pilotSchedule, error) {
	if path == "" {
		return nil, nil
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var f pilotScheduleFile
	if err := json.Unmarshal(b, &f); err != nil {
		return nil, err
	}
	if f.SchemaVersion != 1 {
		return nil, fmt.Errorf("pilot schedule schema_version must be 1")
	}
	if f.Commands == nil {
		f.Commands = map[string]pilotCommandBehavior{}
	}
	for id, x := range f.Commands {
		if x.ForcedOutcome != "" && x.ForcedOutcome != "STOPPED" {
			return nil, fmt.Errorf("pilot schedule %s: only forced_outcome STOPPED is supported", id)
		}
		if x.EffectReplyHideMS < 0 || x.EffectReplyDelayMS < 0 || x.ReceiptQueryDelayMS < 0 {
			return nil, fmt.Errorf("pilot schedule %s: delays must be non-negative", id)
		}
	}
	return &pilotSchedule{file: f, hideUntil: map[string]time.Time{}, delayed: map[string]bool{}}, nil
}

func (p *pilotSchedule) behavior(id string) (pilotCommandBehavior, bool) {
	if p == nil {
		return pilotCommandBehavior{}, false
	}
	x, ok := p.file.Commands[id]
	return x, ok
}

func (p *pilotSchedule) shouldHideEffectReply(id string, ms int) bool {
	if p == nil || ms <= 0 {
		return false
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	until, ok := p.hideUntil[id]
	now := time.Now()
	if !ok {
		until = now.Add(time.Duration(ms) * time.Millisecond)
		p.hideUntil[id] = until
	}
	return now.Before(until)
}

func (p *pilotSchedule) shouldDelayEffectReply(id string, ms int) bool {
	if p == nil || ms <= 0 {
		return false
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.delayed[id] {
		return false
	}
	p.delayed[id] = true
	return true
}

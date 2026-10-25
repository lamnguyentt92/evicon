package main

import (
	"testing"
	"time"
)

func TestEffectReplyDelayIsOneShotPerCommand(t *testing.T) {
	p := &pilotSchedule{hideUntil: map[string]time.Time{}, delayed: map[string]bool{}}
	if !p.shouldDelayEffectReply("A", 1000) {
		t.Fatal("first delay must be enabled")
	}
	if p.shouldDelayEffectReply("A", 1000) {
		t.Fatal("retry with same command ID must not be delayed again")
	}
	if !p.shouldDelayEffectReply("B", 1000) {
		t.Fatal("different command must receive its own one-shot delay")
	}
}

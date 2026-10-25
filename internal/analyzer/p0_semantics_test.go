package analyzer

import (
	"math"
	"testing"

	"evicon/internal/belief"
	"evicon/internal/domain"
)

func TestFiniteOverflowDefersInsteadOfBlocking(t *testing.T) {
	old := domain.Command{ID: "old", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{1}}
	b := belief.BeliefSet{
		Generation: 1, Dimension: 1, Registry: map[string]domain.Command{"old": old},
		Configurations: []belief.Configuration{{State: []int64{math.MaxInt64}, Commands: map[string]domain.CommandStatus{"old": domain.Pending}, InstalledEpoch: 1}},
	}
	max := int64(math.MaxInt64)
	candidate := domain.Command{ID: "candidate", Generation: 1, Epoch: 1, Sequence: 2, Operation: domain.OpAdd, Value: []int64{0}}
	r, err := Finite(b, candidate, domain.HardBound{SumMax: &max}, DefaultFiniteOptions())
	if err != nil || r.Verdict != Deferred || r.Reason != "ARITHMETIC_OVERFLOW" {
		t.Fatalf("overflow must DEFER, got %+v err=%v", r, err)
	}
}

func TestFiniteOutstandingBudgetExhaustionDefers(t *testing.T) {
	old := domain.Command{ID: "old", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{1}}
	b := belief.BeliefSet{
		Generation: 1, Dimension: 1, Registry: map[string]domain.Command{"old": old},
		Configurations: []belief.Configuration{{State: []int64{0}, Commands: map[string]domain.CommandStatus{"old": domain.Pending}, InstalledEpoch: 1}},
	}
	max := int64(100)
	candidate := domain.Command{ID: "candidate", Generation: 1, Epoch: 1, Sequence: 2, Operation: domain.OpAdd, Value: []int64{1}}
	r, err := Finite(b, candidate, domain.HardBound{SumMax: &max}, FiniteOptions{MaxConfigurations: 256, MaxOutstanding: 1})
	if err != nil || r.Verdict != Deferred || r.Reason != "OUTSTANDING_BUDGET_EXHAUSTED" {
		t.Fatalf("budget exhaustion must DEFER without truncating worlds, got %+v err=%v", r, err)
	}
}

func TestIntervalAndJoinedUseConservativeBlock(t *testing.T) {
	b := belief.BeliefSet{
		Generation: 1, Dimension: 2, Registry: map[string]domain.Command{},
		Configurations: []belief.Configuration{
			{State: []int64{0, 5}, Commands: map[string]domain.CommandStatus{}, InstalledEpoch: 1},
			{State: []int64{5, 0}, Commands: map[string]domain.CommandStatus{}, InstalledEpoch: 1},
		},
	}
	max := int64(10)
	candidate := domain.Command{ID: "c", Generation: 1, Epoch: 1, Operation: domain.OpAdd, Value: []int64{3, 0}}
	ri, _, err := IntervalGuard(b, candidate, domain.HardBound{SumMax: &max})
	if err != nil || ri.Verdict != ConservativeBlock {
		t.Fatalf("interval precision loss must be a conservative block, got %+v err=%v", ri, err)
	}
}

package transition

import (
	"testing"

	"evicon/internal/belief"
	"evicon/internal/domain"
)

func TestFixedEpochRequiresEquality(t *testing.T) {
	cfg := belief.Configuration{State: []int64{0}, Commands: map[string]domain.CommandStatus{}, InstalledEpoch: 2}
	for _, epoch := range []uint64{1, 3} {
		cmd := domain.Command{ID: "c", Generation: 1, Epoch: epoch, Sequence: 1, Operation: domain.OpAdd, Value: []int64{1}}
		if CanApply(cfg, cmd, 1) {
			t.Fatalf("epoch %d must not apply to installed epoch 2", epoch)
		}
	}
	cmd := domain.Command{ID: "c", Generation: 1, Epoch: 2, Sequence: 1, Operation: domain.OpAdd, Value: []int64{1}}
	if !CanApply(cfg, cmd, 1) {
		t.Fatal("matching epoch must be applicable")
	}
}

func TestFutureEpochPendingHasNoAppliedSuccessor(t *testing.T) {
	cmd := domain.Command{ID: "future", Generation: 1, Epoch: 2, Sequence: 1, Operation: domain.OpAdd, Value: []int64{1}}
	cfg := belief.Configuration{State: []int64{0}, Commands: map[string]domain.CommandStatus{"future": domain.Pending}, InstalledEpoch: 1}
	eng := Engine{Generation: 1, Registry: map[string]domain.Command{"future": cmd}}
	succ, err := eng.Successors(cfg)
	if err != nil {
		t.Fatal(err)
	}
	if len(succ) != 1 || succ[0].Commands["future"] != domain.Stopped {
		t.Fatalf("future-epoch command must only have STOPPED successor, got %+v", succ)
	}
}

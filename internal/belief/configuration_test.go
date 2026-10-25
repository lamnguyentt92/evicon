package belief

import (
	"strings"
	"testing"

	"evicon/internal/domain"
)

func TestConfigurationRequiresStatusForEveryTrackedCommand(t *testing.T) {
	reg := map[string]domain.Command{
		"a": {ID: "a", Generation: 1, Epoch: 1, Operation: domain.OpAdd, Value: []int64{1}},
	}
	cfg := Configuration{State: []int64{0}, Commands: map[string]domain.CommandStatus{}, InstalledEpoch: 1}
	err := cfg.Validate(1, reg)
	if err == nil || !strings.Contains(err.Error(), "status registry mismatch") {
		t.Fatalf("missing status must be rejected, got %v", err)
	}
}

func TestConfigurationRejectsUnknownStatusEntry(t *testing.T) {
	cfg := Configuration{State: []int64{0}, Commands: map[string]domain.CommandStatus{"ghost": domain.Pending}, InstalledEpoch: 1}
	err := cfg.Validate(1, map[string]domain.Command{})
	if err == nil {
		t.Fatal("unknown status entry must be rejected")
	}
}

func TestBeliefRejectsEmptyConfigurationSet(t *testing.T) {
	b := BeliefSet{Generation: 1, Dimension: 1, Registry: map[string]domain.Command{}, Configurations: nil}
	err := b.Validate()
	if err == nil || !strings.Contains(err.Error(), "at least one configuration") {
		t.Fatalf("empty belief must be rejected, got %v", err)
	}
}

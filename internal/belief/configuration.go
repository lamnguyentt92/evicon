package belief

import (
	"fmt"
	"sort"
	"strconv"
	"strings"

	"evicon/internal/domain"
)

type Configuration struct {
	State          domain.State                    `json:"state"`
	Commands       map[string]domain.CommandStatus `json:"commands"`
	InstalledEpoch uint64                          `json:"installedEpoch"`
	SetFrontier    uint64                          `json:"setFrontier"`
}

func (c Configuration) Clone() Configuration {
	out := Configuration{
		State:          c.State.Clone(),
		Commands:       make(map[string]domain.CommandStatus, len(c.Commands)),
		InstalledEpoch: c.InstalledEpoch,
		SetFrontier:    c.SetFrontier,
	}
	for k, v := range c.Commands {
		out.Commands[k] = v
	}
	return out
}

func (c Configuration) Key() string {
	var b strings.Builder
	b.WriteString("s=")
	for i, v := range c.State {
		if i > 0 {
			b.WriteByte(',')
		}
		b.WriteString(strconv.FormatInt(v, 10))
	}
	b.WriteString("|e=")
	b.WriteString(strconv.FormatUint(c.InstalledEpoch, 10))
	b.WriteString("|f=")
	b.WriteString(strconv.FormatUint(c.SetFrontier, 10))
	ids := make([]string, 0, len(c.Commands))
	for id := range c.Commands {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	b.WriteString("|c=")
	for i, id := range ids {
		if i > 0 {
			b.WriteByte(';')
		}
		b.WriteString(id)
		b.WriteByte(':')
		b.WriteString(string(c.Commands[id]))
	}
	return b.String()
}

func (c Configuration) Validate(dim int, registry map[string]domain.Command) error {
	if len(c.State) != dim {
		return fmt.Errorf("configuration state dimension %d, want %d", len(c.State), dim)
	}
	// Every tracked command must have an explicit status in every world. An
	// absent command ID must never be interpreted as terminal: doing so could
	// silently drop an effect that may still occur.
	if len(c.Commands) != len(registry) {
		return fmt.Errorf("configuration status registry mismatch: have %d statuses, want %d", len(c.Commands), len(registry))
	}
	for id := range registry {
		st, ok := c.Commands[id]
		if !ok {
			return fmt.Errorf("configuration missing status for tracked command %s", id)
		}
		if st != domain.Pending && st != domain.Applied && st != domain.Stopped {
			return fmt.Errorf("command %s has invalid status %q", id, st)
		}
	}
	for id := range c.Commands {
		if _, ok := registry[id]; !ok {
			return fmt.Errorf("configuration references unknown command %s", id)
		}
	}
	return nil
}

type BeliefSet struct {
	Generation     uint64                    `json:"generation"`
	Dimension      int                       `json:"dimension"`
	Registry       map[string]domain.Command `json:"commands"`
	Configurations []Configuration           `json:"configurations"`
}

func (b BeliefSet) Validate() error {
	if b.Dimension <= 0 || b.Dimension > 2 {
		return fmt.Errorf("v0.1 supports dimension 1 or 2, got %d", b.Dimension)
	}
	if len(b.Configurations) == 0 {
		return fmt.Errorf("belief set must contain at least one configuration")
	}
	for id, cmd := range b.Registry {
		if id != cmd.ID {
			return fmt.Errorf("registry key %q differs from command ID %q", id, cmd.ID)
		}
		if err := cmd.Validate(b.Dimension); err != nil {
			return err
		}
	}
	for i, cfg := range b.Configurations {
		if err := cfg.Validate(b.Dimension, b.Registry); err != nil {
			return fmt.Errorf("configuration %d: %w", i, err)
		}
	}
	return nil
}

func (b BeliefSet) WithCandidate(candidate domain.Command) (BeliefSet, error) {
	if err := candidate.Validate(b.Dimension); err != nil {
		return BeliefSet{}, err
	}
	out := BeliefSet{
		Generation:     b.Generation,
		Dimension:      b.Dimension,
		Registry:       make(map[string]domain.Command, len(b.Registry)+1),
		Configurations: make([]Configuration, len(b.Configurations)),
	}
	for k, v := range b.Registry {
		out.Registry[k] = v
	}
	if existing, ok := out.Registry[candidate.ID]; ok {
		if !sameCommand(existing, candidate) {
			return BeliefSet{}, fmt.Errorf("candidate ID conflict for %s", candidate.ID)
		}
	} else {
		out.Registry[candidate.ID] = candidate
	}
	for i, cfg := range b.Configurations {
		clone := cfg.Clone()
		if st, ok := clone.Commands[candidate.ID]; ok {
			if st != domain.Pending {
				return BeliefSet{}, fmt.Errorf("candidate %s already has status %s", candidate.ID, st)
			}
		} else {
			clone.Commands[candidate.ID] = domain.Pending
		}
		out.Configurations[i] = clone
	}
	return out, nil
}

func sameCommand(a, b domain.Command) bool {
	if a.ID != b.ID || a.Generation != b.Generation || a.Epoch != b.Epoch || a.Sequence != b.Sequence || a.Operation != b.Operation || len(a.Value) != len(b.Value) {
		return false
	}
	for i := range a.Value {
		if a.Value[i] != b.Value[i] {
			return false
		}
	}
	return true
}

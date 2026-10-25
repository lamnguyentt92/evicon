package transition

import (
	"fmt"
	"sort"

	"evicon/internal/belief"
	"evicon/internal/domain"
)

type Engine struct {
	Generation uint64
	Registry   map[string]domain.Command
}

// Successors returns every one-step continuation allowed by the fixed-epoch
// TOIT P0 semantics. A pending command may become terminal STOPPED, or may
// apply once only when generation, installed epoch, and SET frontier permit it.
func (e Engine) Successors(cfg belief.Configuration) ([]belief.Configuration, error) {
	ids := make([]string, 0, len(cfg.Commands))
	for id, st := range cfg.Commands {
		if st == domain.Pending {
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)

	out := make([]belief.Configuration, 0, len(ids)*2)
	for _, id := range ids {
		cmd, ok := e.Registry[id]
		if !ok {
			return nil, fmt.Errorf("missing command %s in registry", id)
		}

		stopped := cfg.Clone()
		stopped.Commands[id] = domain.Stopped
		out = append(out, stopped)

		if !CanApply(cfg, cmd, e.Generation) {
			continue
		}
		nextState, err := domain.Apply(cfg.State, cmd)
		if err != nil {
			return nil, err
		}
		applied := cfg.Clone()
		applied.State = nextState
		applied.Commands[id] = domain.Applied
		if cmd.Operation == domain.OpSet && cmd.Sequence > applied.SetFrontier {
			applied.SetFrontier = cmd.Sequence
		}
		out = append(out, applied)
	}
	return out, nil
}

// CanApply is the executable fixed-epoch specification used by both the
// transition engine and analyzer helpers. Epoch equality is intentional:
// stale and future-epoch commands cannot effect the current owner epoch.
func CanApply(cfg belief.Configuration, cmd domain.Command, generation uint64) bool {
	if cmd.Generation != generation {
		return false
	}
	if cmd.Epoch != cfg.InstalledEpoch {
		return false
	}
	if cmd.Operation == domain.OpSet && cmd.Sequence < cfg.SetFrontier {
		return false
	}
	return true
}

package evidence

import (
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"

	"evicon/internal/belief"
	"evicon/internal/domain"
	"evicon/internal/journal"
	"evicon/internal/transition"
)

var (
	ErrBeliefBudget       = errors.New("belief configuration budget exhausted")
	ErrModelInconsistency = errors.New("evidence eliminates every possible configuration")
	ErrInvalidCommand     = errors.New("invalid command in belief journal")
)

// BuildStats describes the exact bounded-enumeration work performed by
// BuildBeliefWithStats.  Limit constrains materialized configurations/states;
// Complete is false whenever exact enumeration did not finish.
type BuildStats struct {
	Algorithm              string `json:"enumeration_algorithm"`
	Limit                  int    `json:"enumeration_limit"`
	ExploredConfigurations int    `json:"explored_configurations"`
	Complete               bool   `json:"enumeration_complete"`
	ConfigurationCount     *int   `json:"configuration_count"`
}

func countPtr(n int) *int { return &n }

// BuildBelief preserves the historical API for admission/planner callers.
func BuildBelief(d journal.Domain, commands []journal.CommandRecord, maxConfigurations int) (belief.BeliefSet, error) {
	b, _, err := BuildBeliefWithStats(d, commands, maxConfigurations)
	return b, err
}

// BuildBeliefWithStats reconstructs the online belief from a durable anchor
// plus every admitted command after the anchor cutoff.  It first uses an exact
// all-terminal reduction which avoids enumerating terminal-status prefixes. If
// that reduction is not safely applicable, it falls back to the executable
// transition BFS used by the decision path.
func BuildBeliefWithStats(d journal.Domain, commands []journal.CommandRecord, maxConfigurations int) (belief.BeliefSet, BuildStats, error) {
	if maxConfigurations <= 0 {
		maxConfigurations = 256
	}
	registry, known, allTerminal, err := buildRegistry(d, commands)
	if err != nil {
		return belief.BeliefSet{}, BuildStats{Algorithm: "validation_v1", Limit: maxConfigurations}, err
	}
	if allTerminal {
		if b, st, applicable, err := buildFullyTerminalBelief(d, registry, known, maxConfigurations); applicable {
			return b, st, err
		}
	}
	// Release 072: an ADD-only history is represented in product form (one
	// configuration with every unresolved command pending). The relational
	// guard over it gives the verdict of the enumerated belief (paper,
	// Lemma "product form"; TestProductFormMatchesEnumeration), in linear
	// time, so no configuration cap applies. The number of unresolved
	// commands per domain is then bounded only by -max-outstanding (the
	// window W). Histories with a SET that is not STOPPED are enumerated.
	if b, st, applicable, err := buildAddOnlyProductBelief(d, registry, known, maxConfigurations); applicable {
		return b, st, err
	}
	if len(known) > 0 {
		if b, st, applicable, err := buildAddOnlyReducedBelief(d, registry, known, maxConfigurations); applicable {
			return b, st, err
		}
	}
	return buildTransitionBelief(d, registry, known, maxConfigurations)
}

// buildAddOnlyProductBelief returns the product-form belief of an ADD-only
// history: the anchor plus every APPLIED vector as the state, every
// unresolved command PENDING, STOPPED commands kept as STOPPED. Applicable
// only when every command that is not STOPPED is an ADD.
func buildAddOnlyProductBelief(d journal.Domain, registry map[string]domain.Command, known map[string]domain.CommandStatus, limit int) (belief.BeliefSet, BuildStats, bool, error) {
	stats := BuildStats{Algorithm: "add_only_product_v1", Limit: limit}
	for id, cmd := range registry {
		if cmd.Operation != domain.OpAdd && known[id] != domain.Stopped {
			return belief.BeliefSet{}, stats, false, nil
		}
	}
	base := belief.Configuration{State: domain.State(append([]int64(nil), d.Anchor...)), Commands: make(map[string]domain.CommandStatus, len(registry)), InstalledEpoch: d.InstalledEpoch, SetFrontier: d.CutoffSequence}
	ids := make([]string, 0, len(registry))
	for id := range registry {
		ids = append(ids, id)
	}
	sort.Strings(ids) // deterministic error reporting
	for _, id := range ids {
		cmd := registry[id]
		st, ok := known[id]
		if !ok {
			base.Commands[id] = domain.Pending
			continue
		}
		base.Commands[id] = st
		if st == domain.Applied {
			if cmd.Generation != d.Generation || cmd.Epoch != d.InstalledEpoch {
				return belief.BeliefSet{}, stats, true, ErrModelInconsistency
			}
			next, err := domain.Apply(base.State, cmd)
			if err != nil {
				return belief.BeliefSet{}, stats, true, ErrModelInconsistency
			}
			base.State = next
		}
	}
	// Fail closed as the enumeration does: the state with every applicable
	// unresolved ADD applied must be representable.
	worst := base.State.Clone()
	for _, id := range ids {
		cmd := registry[id]
		if base.Commands[id] != domain.Pending || cmd.Generation != d.Generation || cmd.Epoch != d.InstalledEpoch {
			continue
		}
		for i, v := range cmd.Value {
			next, ok := domain.AddChecked(worst[i], v)
			if !ok {
				return belief.BeliefSet{}, stats, true, fmt.Errorf("%w: unresolved ADD %s", domain.ErrInt64Overflow, id)
			}
			worst[i] = next
		}
	}
	b := belief.BeliefSet{Generation: d.Generation, Dimension: d.Dimension, Registry: registry, Configurations: []belief.Configuration{base}}
	if err := b.Validate(); err != nil {
		return belief.BeliefSet{}, stats, true, err
	}
	stats.ExploredConfigurations = 1
	stats.Complete = true
	stats.ConfigurationCount = countPtr(1)
	return b, stats, true, nil
}

func buildRegistry(d journal.Domain, commands []journal.CommandRecord) (map[string]domain.Command, map[string]domain.CommandStatus, bool, error) {
	registry := make(map[string]domain.Command, len(commands))
	known := make(map[string]domain.CommandStatus, len(commands))
	allTerminal := true
	for _, r := range commands {
		if r.Sequence <= d.CutoffSequence {
			continue
		}
		cmd := domain.Command{ID: r.CommandID, Generation: r.Generation, Epoch: r.Epoch, Sequence: r.Sequence, Operation: r.Operation, Value: append([]int64(nil), r.Value...)}
		if err := cmd.Validate(d.Dimension); err != nil {
			return nil, nil, false, fmt.Errorf("%w %s: %v", ErrInvalidCommand, r.CommandID, err)
		}
		if prior, ok := registry[cmd.ID]; ok {
			if prior.Generation != cmd.Generation || prior.Epoch != cmd.Epoch || prior.Sequence != cmd.Sequence || prior.Operation != cmd.Operation || !sameVec(prior.Value, cmd.Value) {
				return nil, nil, false, fmt.Errorf("%w: conflicting duplicate command %s", ErrInvalidCommand, cmd.ID)
			}
		}
		registry[cmd.ID] = cmd
		switch r.Status {
		case "APPLIED":
			known[cmd.ID] = domain.Applied
		case "STOPPED":
			known[cmd.ID] = domain.Stopped
		default:
			allTerminal = false
		}
	}
	return registry, known, allTerminal && len(known) == len(registry), nil
}

func sameVec(a, b []int64) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func stateKey(s domain.State) string {
	var b strings.Builder
	for i, v := range s {
		if i > 0 {
			b.WriteByte(',')
		}
		b.WriteString(strconv.FormatInt(v, 10))
	}
	return b.String()
}

// buildFullyTerminalBelief is an exact fast path for a fully terminal history.
// APPLIED SET commands must occur in increasing sequence order. Therefore the
// largest-sequence APPLIED SET is the last SET in every valid execution, and
// each APPLIED ADD may occur either before or after that SET.  With no APPLIED
// SET, every APPLIED ADD contributes to the unique final state.
//
// To preserve the transition engine's overflow behavior, the fast path is used
// only when every anchor/SET base plus the sum of all APPLIED ADDs is provably
// representable in int64. Otherwise we fall back to transition BFS.
func buildFullyTerminalBelief(d journal.Domain, registry map[string]domain.Command, known map[string]domain.CommandStatus, limit int) (belief.BeliefSet, BuildStats, bool, error) {
	return BuildFullyTerminalBeliefExact(d.Generation, d.Dimension, d.InstalledEpoch, d.CutoffSequence, domain.State(append([]int64(nil), d.Anchor...)), registry, known, limit)
}

// BuildFullyTerminalBeliefExact is the pure semantic terminal-history reducer
// shared by the native authority and the Temporal workflow state machine.  It
// has no storage/network dependency; callers provide the durable anchor,
// registry, and terminal evidence explicitly.
func BuildFullyTerminalBeliefExact(generation uint64, dimension int, installedEpoch, cutoffSequence uint64, anchor domain.State, registry map[string]domain.Command, known map[string]domain.CommandStatus, limit int) (belief.BeliefSet, BuildStats, bool, error) {
	stats := BuildStats{Algorithm: "terminal_exact_v1", Limit: limit}
	appliedAdds := make([]domain.Command, 0)
	appliedSets := make([]domain.Command, 0)
	for id, cmd := range registry {
		st, ok := known[id]
		if !ok {
			return belief.BeliefSet{}, stats, false, nil
		}
		if st != domain.Applied {
			continue
		}
		if cmd.Generation != generation || cmd.Epoch != installedEpoch {
			return belief.BeliefSet{}, stats, true, ErrModelInconsistency
		}
		switch cmd.Operation {
		case domain.OpAdd:
			appliedAdds = append(appliedAdds, cmd)
		case domain.OpSet:
			appliedSets = append(appliedSets, cmd)
		default:
			return belief.BeliefSet{}, stats, true, fmt.Errorf("%w: unsupported operation %q", ErrInvalidCommand, cmd.Operation)
		}
	}
	sort.Slice(appliedAdds, func(i, j int) bool { return appliedAdds[i].ID < appliedAdds[j].ID })
	sort.Slice(appliedSets, func(i, j int) bool {
		if appliedSets[i].Sequence == appliedSets[j].Sequence {
			return appliedSets[i].ID < appliedSets[j].ID
		}
		return appliedSets[i].Sequence < appliedSets[j].Sequence
	})
	for i := 1; i < len(appliedSets); i++ {
		if appliedSets[i-1].Sequence == appliedSets[i].Sequence {
			return belief.BeliefSet{}, stats, false, nil
		}
	}
	total := make(domain.State, dimension)
	for _, add := range appliedAdds {
		for i, v := range add.Value {
			next, ok := domain.AddChecked(total[i], v)
			if !ok {
				return belief.BeliefSet{}, stats, false, nil
			}
			total[i] = next
		}
	}
	bases := []domain.State{domain.State(append([]int64(nil), anchor...))}
	for _, set := range appliedSets {
		bases = append(bases, domain.State(set.Value).Clone())
	}
	for _, base := range bases {
		probe := domain.Command{ID: "__overflow_probe__", Operation: domain.OpAdd, Value: append([]int64(nil), total...)}
		if _, err := domain.Apply(base, probe); err != nil {
			return belief.BeliefSet{}, stats, false, nil
		}
	}
	terminalStatuses := make(map[string]domain.CommandStatus, len(registry))
	for id := range registry {
		terminalStatuses[id] = known[id]
	}
	frontier := cutoffSequence
	states := map[string]domain.State{}
	if len(appliedSets) == 0 {
		state := domain.State(append([]int64(nil), anchor...))
		for _, add := range appliedAdds {
			next, err := domain.Apply(state, add)
			if err != nil {
				return belief.BeliefSet{}, stats, true, err
			}
			state = next
		}
		states[stateKey(state)] = state
	} else {
		lastSet := appliedSets[len(appliedSets)-1]
		frontier = lastSet.Sequence
		base := domain.State(lastSet.Value).Clone()
		states[stateKey(base)] = base
		for _, add := range appliedAdds {
			keys := make([]string, 0, len(states))
			for k := range states {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			for _, k := range keys {
				next, err := domain.Apply(states[k], add)
				if err != nil {
					return belief.BeliefSet{}, stats, true, err
				}
				nk := stateKey(next)
				if _, exists := states[nk]; exists {
					continue
				}
				if len(states) >= limit {
					stats.ExploredConfigurations = len(states)
					return belief.BeliefSet{}, stats, true, ErrBeliefBudget
				}
				states[nk] = next
			}
		}
	}
	keys := make([]string, 0, len(states))
	for k := range states {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	configs := make([]belief.Configuration, 0, len(keys))
	for _, k := range keys {
		statuses := make(map[string]domain.CommandStatus, len(terminalStatuses))
		for id, st := range terminalStatuses {
			statuses[id] = st
		}
		configs = append(configs, belief.Configuration{State: states[k].Clone(), Commands: statuses, InstalledEpoch: installedEpoch, SetFrontier: frontier})
	}
	if len(configs) == 0 {
		return belief.BeliefSet{}, stats, true, ErrModelInconsistency
	}
	b := belief.BeliefSet{Generation: generation, Dimension: dimension, Registry: registry, Configurations: configs}
	if err := b.Validate(); err != nil {
		return belief.BeliefSet{}, stats, true, fmt.Errorf("rebuilt belief invalid: %w", err)
	}
	stats.ExploredConfigurations = len(configs)
	stats.Complete = true
	stats.ConfigurationCount = countPtr(len(configs))
	return b, stats, true, nil
}

// buildAddOnlyReducedBelief exactly folds already terminal ADD history into
// the anchor. ADDs are non-negative and commute in the supported fixed-epoch
// domain, so terminal APPLIED order does not carry correlation once no SET is
// present. STOPPED commands are retained as terminal statuses without any
// arithmetic. Only unresolved ADDs are enumerated.
func buildAddOnlyReducedBelief(d journal.Domain, registry map[string]domain.Command, known map[string]domain.CommandStatus, limit int) (belief.BeliefSet, BuildStats, bool, error) {
	return BuildAddOnlyReducedBeliefExact(d.Generation, d.Dimension, d.InstalledEpoch, d.CutoffSequence, domain.State(d.Anchor), registry, known, limit)
}

// BuildAddOnlyReducedBeliefExact is the shared pure partial-history reducer.
// A terminal STOPPED SET stays in the registry and cannot inhibit ADD folding.
func BuildAddOnlyReducedBeliefExact(generation uint64, dimension int, epoch, cutoff uint64, anchor domain.State, registry map[string]domain.Command, known map[string]domain.CommandStatus, limit int) (belief.BeliefSet, BuildStats, bool, error) {
	d := journal.Domain{Generation: generation, Dimension: dimension, InstalledEpoch: epoch, CutoffSequence: cutoff, Anchor: anchor}
	stats := BuildStats{Algorithm: "add_only_reduced_v1", Limit: limit}
	for id, cmd := range registry {
		if cmd.Operation != domain.OpAdd && known[id] != domain.Stopped {
			return belief.BeliefSet{}, stats, false, nil
		}
	}
	base := belief.Configuration{State: domain.State(append([]int64(nil), d.Anchor...)), Commands: make(map[string]domain.CommandStatus, len(registry)), InstalledEpoch: d.InstalledEpoch, SetFrontier: d.CutoffSequence}
	for id, cmd := range registry {
		st, ok := known[id]
		if !ok {
			base.Commands[id] = domain.Pending
			continue
		}
		base.Commands[id] = st
		if st == domain.Applied {
			if cmd.Generation != d.Generation || cmd.Epoch != d.InstalledEpoch {
				return belief.BeliefSet{}, stats, true, ErrModelInconsistency
			}
			next, err := domain.Apply(base.State, cmd)
			if err != nil {
				return belief.BeliefSet{}, stats, true, ErrModelInconsistency
			}
			base.State = next
		}
	}
	engine := transition.Engine{Generation: d.Generation, Registry: registry}
	queue := []belief.Configuration{base}
	seen := map[string]belief.Configuration{base.Key(): base}
	for len(queue) > 0 {
		cur := queue[0]
		queue = queue[1:]
		succ, err := successorsConstrained(engine, cur, known)
		if err != nil {
			return belief.BeliefSet{}, stats, true, err
		}
		for _, n := range succ {
			k := n.Key()
			if _, ok := seen[k]; ok {
				continue
			}
			if len(seen) >= limit {
				stats.ExploredConfigurations = len(seen)
				return belief.BeliefSet{}, stats, true, ErrBeliefBudget
			}
			seen[k] = n
			queue = append(queue, n)
		}
	}
	keys := make([]string, 0, len(seen))
	for k := range seen {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	configs := make([]belief.Configuration, 0, len(keys))
	for _, k := range keys {
		configs = append(configs, seen[k])
	}
	b := belief.BeliefSet{Generation: d.Generation, Dimension: d.Dimension, Registry: registry, Configurations: configs}
	if err := b.Validate(); err != nil {
		return belief.BeliefSet{}, stats, true, err
	}
	stats.ExploredConfigurations = len(seen)
	stats.Complete = true
	stats.ConfigurationCount = countPtr(len(configs))
	return b, stats, true, nil
}

func successorsConstrained(engine transition.Engine, cfg belief.Configuration, known map[string]domain.CommandStatus) ([]belief.Configuration, error) {
	ids := make([]string, 0, len(cfg.Commands))
	for id, st := range cfg.Commands {
		if st == domain.Pending {
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	out := make([]belief.Configuration, 0, len(ids)*2)
	for _, id := range ids {
		cmd, ok := engine.Registry[id]
		if !ok {
			return nil, fmt.Errorf("missing command %s in registry", id)
		}
		want, has := known[id]
		if !has || want == domain.Stopped {
			stopped := cfg.Clone()
			stopped.Commands[id] = domain.Stopped
			out = append(out, stopped)
			if has {
				continue
			}
		}
		if has && want != domain.Applied {
			continue
		}
		if !transition.CanApply(cfg, cmd, engine.Generation) {
			continue
		}
		next, err := domain.Apply(cfg.State, cmd)
		if err != nil {
			return nil, err
		}
		applied := cfg.Clone()
		applied.State = next
		applied.Commands[id] = domain.Applied
		if cmd.Operation == domain.OpSet && cmd.Sequence > applied.SetFrontier {
			applied.SetFrontier = cmd.Sequence
		}
		out = append(out, applied)
	}
	return out, nil
}

func buildTransitionBelief(d journal.Domain, registry map[string]domain.Command, known map[string]domain.CommandStatus, limit int) (belief.BeliefSet, BuildStats, error) {
	stats := BuildStats{Algorithm: "transition_bfs_v1", Limit: limit}
	base := belief.Configuration{State: domain.State(append([]int64(nil), d.Anchor...)), Commands: make(map[string]domain.CommandStatus, len(registry)), InstalledEpoch: d.InstalledEpoch, SetFrontier: d.CutoffSequence}
	for id := range registry {
		if known[id] == domain.Stopped {
			base.Commands[id] = domain.Stopped
		} else {
			base.Commands[id] = domain.Pending
		}
	}
	engine := transition.Engine{Generation: d.Generation, Registry: registry}
	queue := []belief.Configuration{base}
	seen := map[string]belief.Configuration{base.Key(): base}
	for len(queue) > 0 {
		cur := queue[0]
		queue = queue[1:]
		succ, err := successorsConstrained(engine, cur, known)
		if err != nil {
			stats.ExploredConfigurations = len(seen)
			return belief.BeliefSet{}, stats, err
		}
		for _, n := range succ {
			compatible := true
			for id, want := range known {
				st := n.Commands[id]
				if st != domain.Pending && st != want {
					compatible = false
					break
				}
			}
			if !compatible {
				continue
			}
			k := n.Key()
			if _, ok := seen[k]; ok {
				continue
			}
			if len(seen) >= limit {
				stats.ExploredConfigurations = len(seen)
				return belief.BeliefSet{}, stats, ErrBeliefBudget
			}
			seen[k] = n
			queue = append(queue, n)
		}
	}
	keys := make([]string, 0, len(seen))
	for k := range seen {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	configs := make([]belief.Configuration, 0, len(seen))
	for _, k := range keys {
		cfg := seen[k]
		ok := true
		for id, want := range known {
			if cfg.Commands[id] != want {
				ok = false
				break
			}
		}
		if ok {
			configs = append(configs, cfg)
		}
	}
	if len(configs) == 0 {
		stats.ExploredConfigurations = len(seen)
		return belief.BeliefSet{}, stats, ErrModelInconsistency
	}
	b := belief.BeliefSet{Generation: d.Generation, Dimension: d.Dimension, Registry: registry, Configurations: configs}
	if err := b.Validate(); err != nil {
		stats.ExploredConfigurations = len(seen)
		return belief.BeliefSet{}, stats, fmt.Errorf("rebuilt belief invalid: %w", err)
	}
	stats.ExploredConfigurations = len(seen)
	stats.Complete = true
	stats.ConfigurationCount = countPtr(len(configs))
	return b, stats, nil
}

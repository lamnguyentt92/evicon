package analyzer

import (
	"errors"

	"evicon/internal/belief"
	"evicon/internal/domain"
	"evicon/internal/transition"
)

// RelationalGuard is the primary analyzer for the fixed-epoch P0 domain.
// Under the assumptions documented in docs/spec/SEMANTICS.md, its envelope is
// exact for whole-vector SET plus non-negative ADD and the supported bound
// grammar. A bound failure therefore denotes a reachable counterexample, not a
// conservative abstraction block.
func RelationalGuard(b belief.BeliefSet, candidate domain.Command, bound domain.HardBound) (Result, error) {
	if err := b.Validate(); err != nil {
		return Result{}, err
	}
	if err := candidate.Validate(b.Dimension); err != nil {
		return Result{}, err
	}
	if err := bound.Validate(b.Dimension); err != nil {
		return Result{}, err
	}
	if candidate.Generation != b.Generation {
		return Result{Verdict: NotApplicable, Reason: "INVALID_GENERATION"}, nil
	}
	if candidate.Operation == domain.OpAdd && !domain.IsNonNegativeAdd(candidate) {
		return Result{Verdict: NotApplicable, Reason: "RELATIONAL_REQUIRES_NONNEGATIVE_ADD"}, nil
	}
	for _, cmd := range b.Registry {
		if cmd.Operation == domain.OpAdd && !domain.IsNonNegativeAdd(cmd) {
			return Result{Verdict: NotApplicable, Reason: "RELATIONAL_EXISTING_NEGATIVE_ADD"}, nil
		}
	}

	for _, cfg := range b.Configurations {
		holds, err := bound.Check(cfg.State)
		if errors.Is(err, domain.ErrInt64Overflow) {
			return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW"}, nil
		}
		if err != nil {
			return Result{}, err
		}
		if !holds {
			bad := cfg.Clone()
			return Result{Verdict: Counterexample, Reason: "INITIAL_BOUND_VIOLATION", CounterexampleConfiguration: &bad, CounterexampleState: bad.State.Clone()}, nil
		}

		bases := []domain.State{cfg.State.Clone()}
		addTotal := make(domain.State, b.Dimension)

		for id, st := range cfg.Commands {
			if st != domain.Pending {
				continue
			}
			cmd := b.Registry[id]
			if !mixedCanApply(cfg, cmd, b.Generation) {
				continue
			}
			switch cmd.Operation {
			case domain.OpSet:
				base := domain.State(cmd.Value).Clone()
				holds, err := bound.Check(base)
				if errors.Is(err, domain.ErrInt64Overflow) {
					return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW"}, nil
				}
				if err != nil {
					return Result{}, err
				}
				if !holds {
					return Result{Verdict: Counterexample, Reason: "REACHABLE_SET_BASE_VIOLATION", CounterexampleState: base}, nil
				}
				bases = append(bases, base)
			case domain.OpAdd:
				for i, v := range cmd.Value {
					next, ok := domain.AddChecked(addTotal[i], v)
					if !ok {
						return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW"}, nil
					}
					addTotal[i] = next
				}
			}
		}

		if mixedCanApply(cfg, candidate, b.Generation) {
			switch candidate.Operation {
			case domain.OpSet:
				base := domain.State(candidate.Value).Clone()
				holds, err := bound.Check(base)
				if errors.Is(err, domain.ErrInt64Overflow) {
					return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW"}, nil
				}
				if err != nil {
					return Result{}, err
				}
				if !holds {
					return Result{Verdict: Counterexample, Reason: "REACHABLE_CANDIDATE_SET_VIOLATION", CounterexampleState: base}, nil
				}
				bases = append(bases, base)
			case domain.OpAdd:
				for i, v := range candidate.Value {
					next, ok := domain.AddChecked(addTotal[i], v)
					if !ok {
						return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW"}, nil
					}
					addTotal[i] = next
				}
			}
		}

		for _, base := range bases {
			worst := base.Clone()
			for i, v := range addTotal {
				next, ok := domain.AddChecked(worst[i], v)
				if !ok {
					return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW"}, nil
				}
				worst[i] = next
			}
			holds, err := bound.Check(worst)
			if errors.Is(err, domain.ErrInt64Overflow) {
				return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW"}, nil
			}
			if err != nil {
				return Result{}, err
			}
			if !holds {
				return Result{Verdict: Counterexample, Reason: "RELATIONAL_ENVELOPE_COUNTEREXAMPLE", CounterexampleState: worst}, nil
			}
		}
	}

	return Result{Verdict: Safe, Reason: "RELATIONAL_ENVELOPE_SAFE"}, nil
}

func mixedCanApply(cfg belief.Configuration, cmd domain.Command, generation uint64) bool {
	return transition.CanApply(cfg, cmd, generation)
}

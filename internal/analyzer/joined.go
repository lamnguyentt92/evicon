package analyzer

import (
	"errors"

	"evicon/internal/belief"
	"evicon/internal/domain"
)

// JoinedGuard deliberately joins belief worlds. It is a conservative negative
// control: a joined-envelope failure is not a concrete execution witness.
func JoinedGuard(b belief.BeliefSet, candidate domain.Command, bound domain.HardBound) (Result, error) {
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
		return Result{Verdict: NotApplicable, Reason: "JOINED_REQUIRES_NONNEGATIVE_ADD"}, nil
	}
	for _, cmd := range b.Registry {
		if cmd.Operation == domain.OpAdd && !domain.IsNonNegativeAdd(cmd) {
			return Result{Verdict: NotApplicable, Reason: "JOINED_EXISTING_NEGATIVE_ADD"}, nil
		}
	}

	bases := make([]domain.State, 0, len(b.Configurations)+len(b.Registry)+1)
	possibleAdds := make(map[string]domain.Command)
	candidateCanApply := false

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
		bases = append(bases, cfg.State.Clone())

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
				possibleAdds[id] = cmd
			}
		}

		if mixedCanApply(cfg, candidate, b.Generation) {
			candidateCanApply = true
		}
	}

	if candidateCanApply {
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
			possibleAdds[candidate.ID] = candidate
		}
	}

	addTotal := make(domain.State, b.Dimension)
	for _, cmd := range possibleAdds {
		for i, v := range cmd.Value {
			next, ok := domain.AddChecked(addTotal[i], v)
			if !ok {
				return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW"}, nil
			}
			addTotal[i] = next
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
			return Result{Verdict: ConservativeBlock, Reason: "JOINED_CANNOT_ESTABLISH_BOUND"}, nil
		}
	}

	return Result{Verdict: Safe, Reason: "JOINED_ENVELOPE_SAFE"}, nil
}

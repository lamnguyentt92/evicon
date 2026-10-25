package analyzer

import (
	"errors"

	"evicon/internal/belief"
	"evicon/internal/domain"
)

// SumGuard is exact for non-negative ADD with a pure sum_max upper bound
// (component minima are also supported because ADD is monotone non-decreasing).
func SumGuard(b belief.BeliefSet, candidate domain.Command, bound domain.HardBound) (Result, error) {
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
	if bound.SumMax == nil || len(bound.ComponentMax) != 0 || !domain.IsNonNegativeAdd(candidate) {
		return Result{Verdict: NotApplicable, Reason: "SUM_GUARD_REQUIRES_SUM_MAX_NO_COMPONENT_MAX_AND_NONNEGATIVE_ADD"}, nil
	}
	for _, cmd := range b.Registry {
		if !domain.IsNonNegativeAdd(cmd) {
			return Result{Verdict: NotApplicable, Reason: "SUM_GUARD_EXISTING_NON_ADD"}, nil
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

		worst := cfg.State.Clone()
		for id, st := range cfg.Commands {
			if st != domain.Pending {
				continue
			}
			cmd := b.Registry[id]
			if !additiveCanApply(cfg, cmd, b.Generation) {
				continue
			}
			worst, err = domain.Apply(worst, cmd)
			if errors.Is(err, domain.ErrInt64Overflow) {
				return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW"}, nil
			}
			if err != nil {
				return Result{}, err
			}
		}
		if additiveCanApply(cfg, candidate, b.Generation) {
			worst, err = domain.Apply(worst, candidate)
			if errors.Is(err, domain.ErrInt64Overflow) {
				return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW"}, nil
			}
			if err != nil {
				return Result{}, err
			}
		}
		holds, err = bound.Check(worst)
		if errors.Is(err, domain.ErrInt64Overflow) {
			return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW"}, nil
		}
		if err != nil {
			return Result{}, err
		}
		if !holds {
			return Result{Verdict: Counterexample, Reason: "SUM_UPPER_BOUND_VIOLATION", CounterexampleState: worst.Clone()}, nil
		}
	}
	return Result{Verdict: Safe, Reason: "SUM_UPPER_BOUND_SAFE"}, nil
}

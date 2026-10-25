package analyzer

import (
	"errors"

	"evicon/internal/belief"
	"evicon/internal/domain"
)

// Scalar implements the exact non-negative ADD fast path for one-dimensional domains.
func Scalar(b belief.BeliefSet, candidate domain.Command, bound domain.HardBound) (Result, error) {
	if err := b.Validate(); err != nil {
		return Result{}, err
	}
	if err := candidate.Validate(b.Dimension); err != nil {
		return Result{}, err
	}
	if b.Dimension != 1 || !domain.IsNonNegativeAdd(candidate) {
		return Result{Verdict: NotApplicable, Reason: "SCALAR_FAST_PATH_REQUIRES_1D_NONNEGATIVE_ADD"}, nil
	}
	if candidate.Generation != b.Generation {
		return Result{Verdict: NotApplicable, Reason: "INVALID_GENERATION"}, nil
	}
	if err := bound.Validate(1); err != nil {
		return Result{}, err
	}

	for _, cmd := range b.Registry {
		if !domain.IsNonNegativeAdd(cmd) {
			return Result{Verdict: NotApplicable, Reason: "EXISTING_COMMAND_NOT_NONNEGATIVE_ADD"}, nil
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
			next, err := domain.Apply(worst, cmd)
			if errors.Is(err, domain.ErrInt64Overflow) {
				return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW"}, nil
			}
			if err != nil {
				return Result{}, err
			}
			worst = next
		}
		next := worst
		if additiveCanApply(cfg, candidate, b.Generation) {
			var err error
			next, err = domain.Apply(worst, candidate)
			if errors.Is(err, domain.ErrInt64Overflow) {
				return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW"}, nil
			}
			if err != nil {
				return Result{}, err
			}
		}
		holds, err = bound.Check(next)
		if errors.Is(err, domain.ErrInt64Overflow) {
			return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW"}, nil
		}
		if err != nil {
			return Result{}, err
		}
		if !holds {
			bad := cfg.Clone()
			bad.State = next.Clone()
			return Result{Verdict: Counterexample, Reason: "SCALAR_UPPER_BOUND_VIOLATION", CounterexampleConfiguration: &bad, CounterexampleState: next.Clone()}, nil
		}
	}
	return Result{Verdict: Safe, Reason: "SCALAR_UPPER_BOUND_SAFE"}, nil
}

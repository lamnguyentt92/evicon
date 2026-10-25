package analyzer

import (
	"errors"

	"evicon/internal/belief"
	"evicon/internal/domain"
	"evicon/internal/transition"
)

type FiniteOptions struct {
	MaxConfigurations int
	MaxOutstanding    int
}

func DefaultFiniteOptions() FiniteOptions {
	return FiniteOptions{MaxConfigurations: 256, MaxOutstanding: 4}
}

func Finite(b belief.BeliefSet, candidate domain.Command, bound domain.HardBound, opts FiniteOptions) (Result, error) {
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
	if opts.MaxConfigurations <= 0 {
		opts.MaxConfigurations = 256
	}
	if opts.MaxOutstanding <= 0 {
		opts.MaxOutstanding = 4
	}

	withCandidate, err := b.WithCandidate(candidate)
	if err != nil {
		return Result{}, err
	}
	engine := transition.Engine{Generation: withCandidate.Generation, Registry: withCandidate.Registry}

	queue := make([]belief.Configuration, 0, len(withCandidate.Configurations))
	seen := make(map[string]struct{})
	maxOutstanding := 0

	push := func(cfg belief.Configuration) (bool, string) {
		outstanding := countPending(cfg)
		if outstanding > maxOutstanding {
			maxOutstanding = outstanding
		}
		if outstanding > opts.MaxOutstanding {
			return false, "OUTSTANDING_BUDGET_EXHAUSTED"
		}
		key := cfg.Key()
		if _, exists := seen[key]; exists {
			return false, ""
		}
		if len(seen) >= opts.MaxConfigurations {
			return false, "CONFIGURATION_BUDGET_EXHAUSTED"
		}
		seen[key] = struct{}{}
		queue = append(queue, cfg)
		return true, ""
	}

	for _, cfg := range withCandidate.Configurations {
		holds, err := bound.Check(cfg.State)
		if errors.Is(err, domain.ErrInt64Overflow) {
			return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW", Explored: len(seen), MaxOutstanding: maxOutstanding}, nil
		}
		if err != nil {
			return Result{}, err
		}
		if !holds {
			bad := cfg.Clone()
			return Result{Verdict: Counterexample, Reason: "INITIAL_BOUND_VIOLATION", CounterexampleConfiguration: &bad, CounterexampleState: bad.State.Clone()}, nil
		}
		if _, reason := push(cfg.Clone()); reason != "" {
			return Result{Verdict: Deferred, Reason: reason, Explored: len(seen), MaxOutstanding: maxOutstanding}, nil
		}
	}

	for len(queue) > 0 {
		cfg := queue[0]
		queue = queue[1:]
		succ, err := engine.Successors(cfg)
		if errors.Is(err, domain.ErrInt64Overflow) {
			return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW", Explored: len(seen), MaxOutstanding: maxOutstanding}, nil
		}
		if err != nil {
			return Result{}, err
		}
		for _, next := range succ {
			holds, err := bound.Check(next.State)
			if errors.Is(err, domain.ErrInt64Overflow) {
				return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW", Explored: len(seen), MaxOutstanding: maxOutstanding}, nil
			}
			if err != nil {
				return Result{}, err
			}
			if !holds {
				bad := next.Clone()
				return Result{Verdict: Counterexample, Reason: "REACHABLE_BOUND_VIOLATION", Explored: len(seen), MaxOutstanding: maxOutstanding, CounterexampleConfiguration: &bad, CounterexampleState: bad.State.Clone()}, nil
			}
			if _, reason := push(next); reason != "" {
				return Result{Verdict: Deferred, Reason: reason, Explored: len(seen), MaxOutstanding: maxOutstanding}, nil
			}
		}
	}

	return Result{Verdict: Safe, Reason: "ALL_REACHABLE_CONFIGURATIONS_SAFE", Explored: len(seen), MaxOutstanding: maxOutstanding}, nil
}

func countPending(cfg belief.Configuration) int {
	n := 0
	for _, st := range cfg.Commands {
		if st == domain.Pending {
			n++
		}
	}
	return n
}

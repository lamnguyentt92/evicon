package analyzer

import (
	"errors"
	"math"

	"evicon/internal/belief"
	"evicon/internal/domain"
)

type Interval struct {
	Min []int64 `json:"min"`
	Max []int64 `json:"max"`
}

// IntervalGuard is the independent interval negative-control abstraction. A
// failure of the joined box to satisfy H is a CONSERVATIVE_BLOCK unless an
// actual current configuration already violates H.
func IntervalGuard(b belief.BeliefSet, candidate domain.Command, bound domain.HardBound) (Result, Interval, error) {
	if err := b.Validate(); err != nil {
		return Result{}, Interval{}, err
	}
	if err := candidate.Validate(b.Dimension); err != nil {
		return Result{}, Interval{}, err
	}
	if err := bound.Validate(b.Dimension); err != nil {
		return Result{}, Interval{}, err
	}
	if candidate.Generation != b.Generation {
		return Result{Verdict: NotApplicable, Reason: "INVALID_GENERATION"}, Interval{}, nil
	}
	if !domain.IsNonNegativeAdd(candidate) {
		return Result{Verdict: NotApplicable, Reason: "INTERVAL_SUPPORTS_NONNEGATIVE_ADD"}, Interval{}, nil
	}
	for _, cmd := range b.Registry {
		if !domain.IsNonNegativeAdd(cmd) {
			return Result{Verdict: NotApplicable, Reason: "INTERVAL_EXISTING_NON_ADD"}, Interval{}, nil
		}
	}

	iv := Interval{Min: make([]int64, b.Dimension), Max: make([]int64, b.Dimension)}
	for i := 0; i < b.Dimension; i++ {
		iv.Min[i] = math.MaxInt64
		iv.Max[i] = math.MinInt64
	}
	for _, cfg := range b.Configurations {
		holds, err := bound.Check(cfg.State)
		if errors.Is(err, domain.ErrInt64Overflow) {
			return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW"}, iv, nil
		}
		if err != nil {
			return Result{}, iv, err
		}
		if !holds {
			bad := cfg.Clone()
			return Result{Verdict: Counterexample, Reason: "INITIAL_BOUND_VIOLATION", CounterexampleConfiguration: &bad, CounterexampleState: bad.State.Clone()}, iv, nil
		}
		for i, v := range cfg.State {
			if v < iv.Min[i] {
				iv.Min[i] = v
			}
			if v > iv.Max[i] {
				iv.Max[i] = v
			}
		}
	}

	pendingAny := make(map[string]bool)
	for _, cfg := range b.Configurations {
		for id, st := range cfg.Commands {
			if st == domain.Pending && additiveCanApply(cfg, b.Registry[id], b.Generation) {
				pendingAny[id] = true
			}
		}
	}
	for id := range pendingAny {
		cmd := b.Registry[id]
		for i, v := range cmd.Value {
			next, ok := domain.AddChecked(iv.Max[i], v)
			if !ok {
				return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW"}, iv, nil
			}
			iv.Max[i] = next
		}
	}
	candidateCanApply := false
	for _, cfg := range b.Configurations {
		if additiveCanApply(cfg, candidate, b.Generation) {
			candidateCanApply = true
			break
		}
	}
	if candidateCanApply {
		for i, v := range candidate.Value {
			next, ok := domain.AddChecked(iv.Max[i], v)
			if !ok {
				return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW"}, iv, nil
			}
			iv.Max[i] = next
		}
	}

	ok, overflow := intervalSatisfiesBound(iv, bound)
	if overflow {
		return Result{Verdict: Deferred, Reason: "ARITHMETIC_OVERFLOW"}, iv, nil
	}
	if !ok {
		return Result{Verdict: ConservativeBlock, Reason: "INTERVAL_CANNOT_ESTABLISH_BOUND"}, iv, nil
	}
	return Result{Verdict: Safe, Reason: "INTERVAL_ESTABLISHES_BOUND"}, iv, nil
}

func intervalSatisfiesBound(iv Interval, bound domain.HardBound) (bool, bool) {
	if len(bound.ComponentMin) != 0 {
		for i := range iv.Min {
			if iv.Min[i] < bound.ComponentMin[i] {
				return false, false
			}
		}
	}
	if len(bound.ComponentMax) != 0 {
		for i := range iv.Max {
			if iv.Max[i] > bound.ComponentMax[i] {
				return false, false
			}
		}
	}
	if bound.SumMax != nil {
		sum, ok := domain.SumChecked(iv.Max)
		if !ok {
			return false, true
		}
		if sum > *bound.SumMax {
			return false, false
		}
	}
	return true, false
}

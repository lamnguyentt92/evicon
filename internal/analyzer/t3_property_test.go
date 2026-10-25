package analyzer

import (
	"fmt"
	"math/rand"
	"testing"

	"evicon/internal/belief"
	"evicon/internal/domain"
)

// Theorem T3 (plan v2, section 4.3): for non-negative ADD under upper bounds
// only, APPLIED evidence for a pending command never turns a blocked candidate
// into an admitted one; only STOPPED evidence can. This property test checks
// the statement against BOTH RelationalGuard and the finite reference
// enumeration on random beliefs, and also checks that STOPPED does enable
// admission in a non-trivial fraction of cases (the theorem is not vacuous).
func TestT3AppliedNeverEnablesUnderUpperBounds(t *testing.T) {
	rnd := rand.New(rand.NewSource(61))
	opts := FiniteOptions{MaxConfigurations: 4096, MaxOutstanding: 6}
	stoppedEnables, cases := 0, 0
	for iter := 0; iter < 4000; iter++ {
		dim := 1 + rnd.Intn(2)
		x := make(domain.State, dim)
		for i := range x {
			x[i] = int64(rnd.Intn(10))
		}
		k := 1 + rnd.Intn(4)
		reg := map[string]domain.Command{}
		statuses := map[string]domain.CommandStatus{}
		for j := 0; j < k; j++ {
			v := make([]int64, dim)
			for i := range v {
				v[i] = int64(rnd.Intn(6))
			}
			id := fmt.Sprintf("c%d", j)
			reg[id] = domain.Command{ID: id, Generation: 1, Epoch: 1, Sequence: uint64(j + 1), Operation: domain.OpAdd, Value: v}
			statuses[id] = domain.Pending
		}
		cv := make([]int64, dim)
		for i := range cv {
			cv[i] = int64(rnd.Intn(6))
		}
		cand := domain.Command{ID: "cand", Generation: 1, Epoch: 1, Sequence: uint64(k + 1), Operation: domain.OpAdd, Value: cv}
		maxv := make([]int64, dim)
		for i := range maxv {
			maxv[i] = x[i] + int64(rnd.Intn(20))
		}
		sum := int64(0)
		for _, v := range x {
			sum += v
		}
		sumMax := sum + int64(rnd.Intn(25))
		bound := domain.HardBound{ComponentMax: maxv, SumMax: &sumMax}
		b := belief.BeliefSet{Generation: 1, Dimension: dim, Registry: reg,
			Configurations: []belief.Configuration{{State: x.Clone(), Commands: statuses, InstalledEpoch: 1}}}

		before := verdicts(t, b, cand, bound, opts)
		for id, c := range reg {
			applied := cloneBelief(b)
			applied.Configurations[0].Commands[id] = domain.Applied
			next, _ := domain.Apply(applied.Configurations[0].State, c)
			applied.Configurations[0].State = next
			stopped := cloneBelief(b)
			stopped.Configurations[0].Commands[id] = domain.Stopped

			a := verdicts(t, applied, cand, bound, opts)
			s := verdicts(t, stopped, cand, bound, opts)
			cases++
			for name, v := range before {
				if v != Safe && a[name] == Safe {
					t.Fatalf("%s: APPLIED(%s) enabled a blocked candidate (x=%v reg=%v cand=%v bound=%v)", name, id, x, reg, cv, bound)
				}
				if v == Safe && a[name] != Safe {
					t.Fatalf("%s: APPLIED evidence must not disable a safe candidate", name)
				}
			}
			if before["relational"] != Safe && s["relational"] == Safe {
				stoppedEnables++
			}
			if a["relational"] != a["finite"] || s["relational"] != s["finite"] {
				t.Fatalf("relational and finite disagree on ADD-only upper bounds: %v %v / %v %v", a["relational"], a["finite"], s["relational"], s["finite"])
			}
		}
	}
	if stoppedEnables == 0 {
		t.Fatal("STOPPED evidence never enabled admission: the generator is vacuous")
	}
	t.Logf("cases=%d stopped-enables=%d (%.1f%%)", cases, stoppedEnables, 100*float64(stoppedEnables)/float64(cases))
}

// With a lower bound, APPLIED evidence can matter (the restriction in T3 is
// necessary): a state below the minimum that a pending ADD would lift.
func TestT3RestrictionLowerBoundsCanUseApplied(t *testing.T) {
	min := []int64{5}
	max := []int64{100}
	bound := domain.HardBound{ComponentMin: min, ComponentMax: max}
	reg := map[string]domain.Command{"c": {ID: "c", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{4}}}
	b := belief.BeliefSet{Generation: 1, Dimension: 1, Registry: reg,
		Configurations: []belief.Configuration{{State: domain.State{3}, Commands: map[string]domain.CommandStatus{"c": domain.Pending}, InstalledEpoch: 1}}}
	cand := domain.Command{ID: "cand", Generation: 1, Epoch: 1, Sequence: 2, Operation: domain.OpAdd, Value: []int64{1}}
	r, err := RelationalGuard(b, cand, bound)
	if err != nil || r.Verdict == Safe {
		t.Fatalf("expected block below the lower bound: %+v %v", r, err)
	}
	applied := cloneBelief(b)
	applied.Configurations[0].Commands["c"] = domain.Applied
	applied.Configurations[0].State = domain.State{7}
	r, err = RelationalGuard(applied, cand, bound)
	if err != nil || r.Verdict != Safe {
		t.Fatalf("APPLIED evidence should enable admission under a lower bound: %+v %v", r, err)
	}
}

func verdicts(t *testing.T, b belief.BeliefSet, cand domain.Command, bound domain.HardBound, opts FiniteOptions) map[string]Verdict {
	t.Helper()
	r, err := RelationalGuard(b, cand, bound)
	if err != nil {
		t.Fatal(err)
	}
	f, err := Finite(b, cand, bound, opts)
	if err != nil {
		t.Fatal(err)
	}
	norm := func(v Verdict) Verdict {
		if v == ConservativeBlock {
			return Counterexample
		}
		return v
	}
	return map[string]Verdict{"relational": norm(r.Verdict), "finite": norm(f.Verdict)}
}

func cloneBelief(b belief.BeliefSet) belief.BeliefSet {
	out := belief.BeliefSet{Generation: b.Generation, Dimension: b.Dimension, Registry: map[string]domain.Command{}}
	for k, v := range b.Registry {
		out.Registry[k] = v
	}
	for _, c := range b.Configurations {
		out.Configurations = append(out.Configurations, c.Clone())
	}
	return out
}

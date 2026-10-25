package analyzer

import (
	"testing"

	"evicon/internal/belief"
	"evicon/internal/domain"
)

func ptr(v int64) *int64 { return &v }

func scalarBelief(state int64, status domain.CommandStatus) belief.BeliefSet {
	old := domain.Command{ID: "old", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{5}}
	return belief.BeliefSet{
		Generation: 1,
		Dimension:  1,
		Registry:   map[string]domain.Command{"old": old},
		Configurations: []belief.Configuration{{
			State: []int64{state}, Commands: map[string]domain.CommandStatus{"old": status}, InstalledEpoch: 1,
		}},
	}
}

func TestScalarSafeAndUnsafe(t *testing.T) {
	b := scalarBelief(0, domain.Pending)
	bound := domain.HardBound{ComponentMin: []int64{0}, SumMax: ptr(10)}
	safe := domain.Command{ID: "c3", Generation: 1, Epoch: 1, Sequence: 2, Operation: domain.OpAdd, Value: []int64{3}}
	r, err := Scalar(b, safe, bound)
	if err != nil || r.Verdict != Safe {
		t.Fatalf("expected SAFE, got %+v err=%v", r, err)
	}
	bad := safe
	bad.ID = "c7"
	bad.Value = []int64{7}
	r, err = Scalar(b, bad, bound)
	if err != nil || r.Verdict != Unsafe {
		t.Fatalf("expected UNSAFE, got %+v err=%v", r, err)
	}
}

func TestAppliedNotDoubleCounted(t *testing.T) {
	b := scalarBelief(5, domain.Applied)
	bound := domain.HardBound{ComponentMin: []int64{0}, SumMax: ptr(10)}
	c := domain.Command{ID: "c", Generation: 1, Epoch: 1, Sequence: 2, Operation: domain.OpAdd, Value: []int64{3}}
	r, err := Scalar(b, c, bound)
	if err != nil || r.Verdict != Safe {
		t.Fatalf("expected SAFE because old effect is already in state, got %+v err=%v", r, err)
	}
}

func TestCorrelationSeparatesIntervalFromFiniteAndSum(t *testing.T) {
	b := belief.BeliefSet{
		Generation: 1, Dimension: 2, Registry: map[string]domain.Command{},
		Configurations: []belief.Configuration{
			{State: []int64{0, 5}, Commands: map[string]domain.CommandStatus{}, InstalledEpoch: 1},
			{State: []int64{5, 0}, Commands: map[string]domain.CommandStatus{}, InstalledEpoch: 1},
		},
	}
	bound := domain.HardBound{ComponentMin: []int64{0, 0}, SumMax: ptr(10)}
	c := domain.Command{ID: "c", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{3, 0}}

	ri, _, err := IntervalGuard(b, c, bound)
	if err != nil || ri.Verdict != ConservativeBlock {
		t.Fatalf("interval should block due lost correlation, got %+v err=%v", ri, err)
	}
	rs, err := SumGuard(b, c, bound)
	if err != nil || rs.Verdict != Safe {
		t.Fatalf("sum guard should establish sum bound, got %+v err=%v", rs, err)
	}
	rf, err := Finite(b, c, bound, DefaultFiniteOptions())
	if err != nil || rf.Verdict != Safe {
		t.Fatalf("finite should be safe, got %+v err=%v", rf, err)
	}
}

func TestIntermediateViolationDetected(t *testing.T) {
	old := domain.Command{ID: "add5", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{5}}
	b := belief.BeliefSet{
		Generation: 1, Dimension: 1, Registry: map[string]domain.Command{"add5": old},
		Configurations: []belief.Configuration{{State: []int64{7}, Commands: map[string]domain.CommandStatus{"add5": domain.Pending}, InstalledEpoch: 1}},
	}
	bound := domain.HardBound{ComponentMin: []int64{0}, SumMax: ptr(10)}
	reset := domain.Command{ID: "reset", Generation: 1, Epoch: 1, Sequence: 2, Operation: domain.OpSet, Value: []int64{0}}
	r, err := Finite(b, reset, bound, DefaultFiniteOptions())
	if err != nil || r.Verdict != Unsafe {
		t.Fatalf("expected intermediate violation, got %+v err=%v", r, err)
	}
}

func TestGenerationMismatchNotApplicable(t *testing.T) {
	b := belief.BeliefSet{Generation: 2, Dimension: 1, Registry: map[string]domain.Command{}, Configurations: []belief.Configuration{{State: []int64{0}, Commands: map[string]domain.CommandStatus{}}}}
	c := domain.Command{ID: "stale", Generation: 1, Operation: domain.OpAdd, Value: []int64{1}}
	r, err := Finite(b, c, domain.HardBound{SumMax: ptr(10)}, DefaultFiniteOptions())
	if err != nil || r.Verdict != NotApplicable || r.Reason != "INVALID_GENERATION" {
		t.Fatalf("unexpected result %+v err=%v", r, err)
	}
}

func TestScalarChecksInitialConfigurationBound(t *testing.T) {
	b := belief.BeliefSet{
		Generation: 1, Dimension: 1, Registry: map[string]domain.Command{},
		Configurations: []belief.Configuration{{State: []int64{-1}, Commands: map[string]domain.CommandStatus{}, InstalledEpoch: 1}},
	}
	bound := domain.HardBound{ComponentMin: []int64{0}, SumMax: ptr(10)}
	c := domain.Command{ID: "repair", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{2}}
	r, err := Scalar(b, c, bound)
	if err != nil || r.Verdict != Unsafe || r.Reason != "INITIAL_BOUND_VIOLATION" {
		t.Fatalf("initial violation must be detected before a repairing ADD, got %+v err=%v", r, err)
	}
}

func TestSumGuardRejectsUnsupportedComponentMax(t *testing.T) {
	b := belief.BeliefSet{
		Generation: 1, Dimension: 2, Registry: map[string]domain.Command{},
		Configurations: []belief.Configuration{{State: []int64{1, 1}, Commands: map[string]domain.CommandStatus{}, InstalledEpoch: 1}},
	}
	bound := domain.HardBound{ComponentMin: []int64{0, 0}, ComponentMax: []int64{2, 10}, SumMax: ptr(20)}
	c := domain.Command{ID: "c", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{2, 0}}
	r, err := SumGuard(b, c, bound)
	if err != nil || r.Verdict != NotApplicable {
		t.Fatalf("sum-only abstraction must not claim component_max support, got %+v err=%v", r, err)
	}
}

func TestAdditiveBaselinesRejectCandidateGenerationMismatch(t *testing.T) {
	b := belief.BeliefSet{
		Generation: 2, Dimension: 2, Registry: map[string]domain.Command{},
		Configurations: []belief.Configuration{{State: []int64{0, 0}, Commands: map[string]domain.CommandStatus{}, InstalledEpoch: 1}},
	}
	bound := domain.HardBound{ComponentMin: []int64{0, 0}, SumMax: ptr(10)}
	c := domain.Command{ID: "stale", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{1, 1}}
	ri, _, err := IntervalGuard(b, c, bound)
	if err != nil || ri.Verdict != NotApplicable || ri.Reason != "INVALID_GENERATION" {
		t.Fatalf("unexpected interval result %+v err=%v", ri, err)
	}
	rs, err := SumGuard(b, c, bound)
	if err != nil || rs.Verdict != NotApplicable || rs.Reason != "INVALID_GENERATION" {
		t.Fatalf("unexpected sum result %+v err=%v", rs, err)
	}
}

func TestRelationalSupportsMixedSetAddSafe(t *testing.T) {
	oldSet := domain.Command{ID: "set2", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpSet, Value: []int64{2}}
	b := belief.BeliefSet{
		Generation: 1, Dimension: 1, Registry: map[string]domain.Command{"set2": oldSet},
		Configurations: []belief.Configuration{{State: []int64{5}, Commands: map[string]domain.CommandStatus{"set2": domain.Pending}, InstalledEpoch: 1}},
	}
	bound := domain.HardBound{ComponentMin: []int64{0}, ComponentMax: []int64{10}, SumMax: ptr(10)}
	candidate := domain.Command{ID: "add3", Generation: 1, Epoch: 1, Sequence: 2, Operation: domain.OpAdd, Value: []int64{3}}
	rr, err := RelationalGuard(b, candidate, bound)
	if err != nil || rr.Verdict != Safe {
		t.Fatalf("relational mixed envelope should be SAFE, got %+v err=%v", rr, err)
	}
	rf, err := Finite(b, candidate, bound, DefaultFiniteOptions())
	if err != nil || rf.Verdict != Safe {
		t.Fatalf("finite should agree SAFE, got %+v err=%v", rf, err)
	}
}

func TestRelationalDetectsIntermediateMixedViolation(t *testing.T) {
	old := domain.Command{ID: "add5", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{5}}
	b := belief.BeliefSet{
		Generation: 1, Dimension: 1, Registry: map[string]domain.Command{"add5": old},
		Configurations: []belief.Configuration{{State: []int64{7}, Commands: map[string]domain.CommandStatus{"add5": domain.Pending}, InstalledEpoch: 1}},
	}
	bound := domain.HardBound{ComponentMin: []int64{0}, SumMax: ptr(10)}
	reset := domain.Command{ID: "reset", Generation: 1, Epoch: 1, Sequence: 2, Operation: domain.OpSet, Value: []int64{0}}
	r, err := RelationalGuard(b, reset, bound)
	if err != nil || r.Verdict != Counterexample || r.Reason != "RELATIONAL_ENVELOPE_COUNTEREXAMPLE" {
		t.Fatalf("expected mixed envelope violation, got %+v err=%v", r, err)
	}
}

func TestRelationalSupportsComponentMax(t *testing.T) {
	b := belief.BeliefSet{
		Generation: 1, Dimension: 2, Registry: map[string]domain.Command{},
		Configurations: []belief.Configuration{{State: []int64{1, 1}, Commands: map[string]domain.CommandStatus{}, InstalledEpoch: 1}},
	}
	bound := domain.HardBound{ComponentMin: []int64{0, 0}, ComponentMax: []int64{2, 10}, SumMax: ptr(20)}
	candidate := domain.Command{ID: "c", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{2, 0}}
	r, err := RelationalGuard(b, candidate, bound)
	if err != nil || r.Verdict != Unsafe {
		t.Fatalf("relational guard must enforce component_max, got %+v err=%v", r, err)
	}
}

func TestRelationalRejectsNegativeAdd(t *testing.T) {
	b := belief.BeliefSet{
		Generation: 1, Dimension: 1, Registry: map[string]domain.Command{},
		Configurations: []belief.Configuration{{State: []int64{5}, Commands: map[string]domain.CommandStatus{}, InstalledEpoch: 1}},
	}
	bound := domain.HardBound{ComponentMin: []int64{0}, SumMax: ptr(10)}
	candidate := domain.Command{ID: "neg", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{-1}}
	_, err := RelationalGuard(b, candidate, bound)
	if err == nil {
		t.Fatal("negative ADD must be rejected by prototype command validation")
	}
}

func TestJoinedLosesCommandStateCorrelationButRemainsConservative(t *testing.T) {
	old := domain.Command{ID: "old", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{5, 0}}
	b := belief.BeliefSet{
		Generation: 1,
		Dimension:  2,
		Registry:   map[string]domain.Command{"old": old},
		Configurations: []belief.Configuration{
			{State: []int64{5, 1}, Commands: map[string]domain.CommandStatus{"old": domain.Pending}, InstalledEpoch: 1},
			{State: []int64{10, 1}, Commands: map[string]domain.CommandStatus{"old": domain.Applied}, InstalledEpoch: 1},
		},
	}
	bound := domain.HardBound{ComponentMin: []int64{0, 0}, ComponentMax: []int64{12, 4}}
	candidate := domain.Command{ID: "candidate", Generation: 1, Epoch: 1, Sequence: 2, Operation: domain.OpAdd, Value: []int64{2, 0}}

	joined, err := JoinedGuard(b, candidate, bound)
	if err != nil || joined.Verdict != ConservativeBlock {
		t.Fatalf("joined guard should conservatively block after losing world correlation, got %+v err=%v", joined, err)
	}
	rel, err := RelationalGuard(b, candidate, bound)
	if err != nil || rel.Verdict != Safe {
		t.Fatalf("relational guard should preserve per-world correlation and admit, got %+v err=%v", rel, err)
	}
	finite, err := Finite(b, candidate, bound, DefaultFiniteOptions())
	if err != nil || finite.Verdict != Safe {
		t.Fatalf("finite should agree SAFE, got %+v err=%v", finite, err)
	}
}

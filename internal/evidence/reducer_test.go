package evidence

import (
	"errors"
	"evicon/internal/domain"
	"evicon/internal/journal"
	"fmt"
	"math"
	"testing"
)

func TestBuildBeliefPreservesAmbiguity(t *testing.T) {
	d := journal.Domain{ID: "d", Generation: 1, InstalledEpoch: 1, Dimension: 1, Anchor: []int64{4}}
	cmds := []journal.CommandRecord{{DomainID: "d", CommandID: "a", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{4}, Status: "PENDING", Decision: "ADMITTED"}}
	b, err := BuildBelief(d, cmds, 256)
	if err != nil {
		t.Fatal(err)
	}
	// release 072: ADD-only histories are in product form; the ambiguity is
	// kept as a PENDING command whose application the guard always considers
	for _, c := range b.Configurations {
		if c.Commands["a"] != domain.Pending || c.State[0] != 4 {
			t.Fatalf("ambiguous command must stay PENDING over the anchor, got %+v", c)
		}
	}
	// the enumerated (reachability-closed) belief has both possibilities
	reg := mustRegistry(t, d, cmds)
	e, _, ok, err := BuildAddOnlyReducedBeliefExact(1, 1, 1, 0, domain.State{4}, reg, map[string]domain.CommandStatus{}, 256)
	if !ok || err != nil {
		t.Fatalf("enumeration: ok=%v err=%v", ok, err)
	}
	seen := map[int64]bool{}
	for _, c := range e.Configurations {
		seen[c.State[0]] = true
	}
	if !seen[4] || !seen[8] {
		t.Fatalf("expected anchor and applied possibilities, got %v", seen)
	}
}
func TestBuildBeliefFiltersStopped(t *testing.T) {
	d := journal.Domain{ID: "d", Generation: 1, InstalledEpoch: 1, Dimension: 1, Anchor: []int64{4}}
	cmds := []journal.CommandRecord{{DomainID: "d", CommandID: "a", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{4}, Status: "STOPPED", Decision: "ADMITTED"}}
	b, err := BuildBelief(d, cmds, 256)
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range b.Configurations {
		if c.State[0] != 4 {
			t.Fatalf("STOPPED command changed state: %v", c.State)
		}
	}
}

func terminalRecords(n int, op domain.Operation, values func(int) []int64) []journal.CommandRecord {
	out := make([]journal.CommandRecord, 0, n)
	for i := 0; i < n; i++ {
		out = append(out, journal.CommandRecord{DomainID: "d", CommandID: fmt.Sprintf("c%02d", i), Generation: 1, Epoch: 1, Sequence: uint64(i + 1), Operation: op, Value: values(i), Status: "APPLIED", Decision: "ADMITTED"})
	}
	return out
}

func TestTerminalFastPathAvoidsIntermediateBudgetExplosion(t *testing.T) {
	d := journal.Domain{ID: "d", Generation: 1, InstalledEpoch: 1, Dimension: 1, Anchor: []int64{0}}
	cmds := terminalRecords(9, domain.OpAdd, func(int) []int64 { return []int64{1} })
	if _, _, err := buildTransitionBelief(d, mustRegistry(t, d, cmds), mustKnown(t, d, cmds), 256); !errors.Is(err, ErrBeliefBudget) {
		t.Fatalf("reference BFS should exhaust 256 visited states for 9 terminal ADDs, got %v", err)
	}
	b, stats, err := BuildBeliefWithStats(d, cmds, 256)
	if err != nil {
		t.Fatal(err)
	}
	if stats.Algorithm != "terminal_exact_v1" || !stats.Complete || stats.ConfigurationCount == nil || *stats.ConfigurationCount != 1 {
		t.Fatalf("unexpected stats: %+v", stats)
	}
	if len(b.Configurations) != 1 || b.Configurations[0].State[0] != 9 {
		t.Fatalf("want exact singleton state 9, got %+v", b.Configurations)
	}
}

func TestTerminalSetAddPreservesOrderingAmbiguity(t *testing.T) {
	d := journal.Domain{ID: "d", Generation: 1, InstalledEpoch: 1, Dimension: 1, Anchor: []int64{0}}
	cmds := []journal.CommandRecord{
		{DomainID: "d", CommandID: "set", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpSet, Value: []int64{10}, Status: "APPLIED", Decision: "ADMITTED"},
		{DomainID: "d", CommandID: "add", Generation: 1, Epoch: 1, Sequence: 2, Operation: domain.OpAdd, Value: []int64{1}, Status: "APPLIED", Decision: "ADMITTED"},
	}
	b, stats, err := BuildBeliefWithStats(d, cmds, 256)
	if err != nil {
		t.Fatal(err)
	}
	got := map[int64]bool{}
	for _, c := range b.Configurations {
		got[c.State[0]] = true
	}
	if len(got) != 2 || !got[10] || !got[11] {
		t.Fatalf("want states {10,11}, got %v", got)
	}
	if stats.ConfigurationCount == nil || *stats.ConfigurationCount != 2 {
		t.Fatalf("bad stats %+v", stats)
	}
}

func TestTerminalFastPathStillFailsWhenFinalStateSetExceedsBudget(t *testing.T) {
	d := journal.Domain{ID: "d", Generation: 1, InstalledEpoch: 1, Dimension: 1, Anchor: []int64{0}}
	cmds := []journal.CommandRecord{{DomainID: "d", CommandID: "set", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpSet, Value: []int64{0}, Status: "APPLIED", Decision: "ADMITTED"}}
	for i := 0; i < 9; i++ {
		cmds = append(cmds, journal.CommandRecord{DomainID: "d", CommandID: fmt.Sprintf("a%d", i), Generation: 1, Epoch: 1, Sequence: uint64(i + 2), Operation: domain.OpAdd, Value: []int64{1 << i}, Status: "APPLIED", Decision: "ADMITTED"})
	}
	_, stats, err := BuildBeliefWithStats(d, cmds, 256)
	if !errors.Is(err, ErrBeliefBudget) {
		t.Fatalf("want real final-state budget exhaustion, got %v", err)
	}
	if stats.Algorithm != "terminal_exact_v1" || stats.Complete {
		t.Fatalf("bad stats %+v", stats)
	}
}

func TestNonTerminalHistoryUsesReferenceBFS(t *testing.T) {
	d := journal.Domain{ID: "d", Generation: 1, InstalledEpoch: 1, Dimension: 1, Anchor: []int64{0}}
	cmds := []journal.CommandRecord{{DomainID: "d", CommandID: "p", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpSet, Value: []int64{1}, Status: "PENDING", Decision: "ADMITTED"}}
	_, stats, err := BuildBeliefWithStats(d, cmds, 256)
	if err != nil {
		t.Fatal(err)
	}
	if stats.Algorithm != "transition_bfs_v1" {
		t.Fatalf("want BFS for an unresolved SET history, got %+v", stats)
	}
	cmds[0].Operation = domain.OpAdd
	if _, stats, err = BuildBeliefWithStats(d, cmds, 256); err != nil || stats.Algorithm != "add_only_product_v1" {
		t.Fatalf("want product form for an unresolved ADD history, got %+v, %v", stats, err)
	}
}

func TestAppliedGenerationMismatchIsModelInconsistency(t *testing.T) {
	d := journal.Domain{ID: "d", Generation: 1, InstalledEpoch: 1, Dimension: 1, Anchor: []int64{0}}
	cmds := []journal.CommandRecord{{DomainID: "d", CommandID: "x", Generation: 2, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{1}, Status: "APPLIED", Decision: "ADMITTED"}}
	_, _, err := BuildBeliefWithStats(d, cmds, 256)
	if !errors.Is(err, ErrModelInconsistency) {
		t.Fatalf("want model inconsistency, got %v", err)
	}
}

func mustRegistry(t *testing.T, d journal.Domain, cmds []journal.CommandRecord) map[string]domain.Command {
	t.Helper()
	r, _, _, err := buildRegistry(d, cmds)
	if err != nil {
		t.Fatal(err)
	}
	return r
}
func mustKnown(t *testing.T, d journal.Domain, cmds []journal.CommandRecord) map[string]domain.CommandStatus {
	t.Helper()
	_, k, _, err := buildRegistry(d, cmds)
	if err != nil {
		t.Fatal(err)
	}
	return k
}

func TestTerminalFastPathMatchesReferenceBFSOnSmallExhaustiveCases(t *testing.T) {
	for dim := 1; dim <= 2; dim++ {
		for n := 0; n <= 5; n++ {
			choices := 1
			for i := 0; i < n; i++ {
				choices *= 4
			}
			for mask := 0; mask < choices; mask++ {
				x := mask
				cmds := make([]journal.CommandRecord, 0, n)
				for i := 0; i < n; i++ {
					choice := x % 4
					x /= 4
					op := domain.OpAdd
					status := "APPLIED"
					if choice >= 2 {
						op = domain.OpSet
					}
					if choice%2 == 1 {
						status = "STOPPED"
					}
					value := make([]int64, dim)
					for j := range value {
						if op == domain.OpAdd {
							value[j] = int64((i+j)%2 + 1)
						} else {
							value[j] = int64(5 + i + j)
						}
					}
					cmds = append(cmds, journal.CommandRecord{DomainID: "d", CommandID: fmt.Sprintf("c%d", i), Generation: 1, Epoch: 1, Sequence: uint64(i + 1), Operation: op, Value: value, Status: status, Decision: "ADMITTED"})
				}
				anchor := make([]int64, dim)
				d := journal.Domain{ID: "d", Generation: 1, InstalledEpoch: 1, Dimension: dim, Anchor: anchor}
				reg, known, _, err := buildRegistry(d, cmds)
				if err != nil {
					t.Fatal(err)
				}
				ref, _, err := buildTransitionBelief(d, reg, known, 10000)
				if err != nil {
					t.Fatalf("reference dim=%d n=%d mask=%d: %v", dim, n, mask, err)
				}
				got, stats, err := BuildBeliefWithStats(d, cmds, 10000)
				if err != nil {
					t.Fatalf("fast dim=%d n=%d mask=%d: %v", dim, n, mask, err)
				}
				if stats.Algorithm != "terminal_exact_v1" {
					t.Fatalf("expected terminal fast path dim=%d n=%d mask=%d: %+v", dim, n, mask, stats)
				}
				rkeys := map[string]bool{}
				for _, c := range ref.Configurations {
					rkeys[c.Key()] = true
				}
				gkeys := map[string]bool{}
				for _, c := range got.Configurations {
					gkeys[c.Key()] = true
				}
				if len(rkeys) != len(gkeys) {
					t.Fatalf("configuration count mismatch dim=%d n=%d mask=%d ref=%d got=%d", dim, n, mask, len(rkeys), len(gkeys))
				}
				for k := range rkeys {
					if !gkeys[k] {
						t.Fatalf("missing configuration dim=%d n=%d mask=%d key=%s", dim, n, mask, k)
					}
				}
			}
		}
	}
}

func TestAddOnlyPartialTerminalHistoryDoesNotExplode(t *testing.T) {
	d := journal.Domain{ID: "d", Generation: 1, InstalledEpoch: 1, Dimension: 1, Anchor: []int64{0}}
	cmds := terminalRecords(8, domain.OpAdd, func(int) []int64 { return []int64{1} })
	cmds = append(cmds, journal.CommandRecord{DomainID: "d", CommandID: "pending", Generation: 1, Epoch: 1, Sequence: 9, Operation: domain.OpAdd, Value: []int64{1}, Status: "PENDING", Decision: "ADMITTED"})
	b, stats, err := BuildBeliefWithStats(d, cmds, 256)
	if err != nil {
		t.Fatal(err)
	}
	if stats.Algorithm != "add_only_product_v1" || len(b.Configurations) != 1 {
		t.Fatalf("want one product configuration, got %+v (%d)", stats, len(b.Configurations))
	}
	// the reduced enumeration (kept for SET-free partial histories) still gives 3
	reg := mustRegistry(t, d, cmds)
	known := map[string]domain.CommandStatus{}
	for _, c := range cmds {
		if c.Status == "APPLIED" || c.Status == "STOPPED" {
			known[c.CommandID] = domain.CommandStatus(c.Status)
		}
	}
	e, _, ok, err := BuildAddOnlyReducedBeliefExact(1, 1, 1, 0, domain.State{0}, reg, known, 256)
	if !ok || err != nil || len(e.Configurations) != 3 {
		t.Fatalf("want 3 pending/stopped/applied configurations, got %d (ok=%v err=%v)", len(e.Configurations), ok, err)
	}
}

func TestStoppedOverflowBranchIsNotEvaluated(t *testing.T) {
	d := journal.Domain{ID: "d", Generation: 1, InstalledEpoch: 1, Dimension: 1, Anchor: []int64{math.MaxInt64}}
	cmds := []journal.CommandRecord{{DomainID: "d", CommandID: "x", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{1}, Status: "STOPPED", Decision: "ADMITTED"}}
	b, _, err := BuildBeliefWithStats(d, cmds, 256)
	if err != nil {
		t.Fatal(err)
	}
	if len(b.Configurations) != 1 || b.Configurations[0].State[0] != math.MaxInt64 {
		t.Fatalf("unexpected belief %+v", b.Configurations)
	}
}

func TestUnresolvedOverflowStillFailsClosed(t *testing.T) {
	d := journal.Domain{ID: "d", Generation: 1, InstalledEpoch: 1, Dimension: 1, Anchor: []int64{math.MaxInt64}}
	cmds := []journal.CommandRecord{{DomainID: "d", CommandID: "x", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{1}, Status: "PENDING", Decision: "ADMITTED"}}
	_, _, err := BuildBeliefWithStats(d, cmds, 256)
	if !errors.Is(err, domain.ErrInt64Overflow) {
		t.Fatalf("want overflow, got %v", err)
	}
}

func TestMixedHistoryKnownStoppedSetsDoNotConsumeConfigurationBudget(t *testing.T) {
	d := journal.Domain{ID: "d", Generation: 1, InstalledEpoch: 1, Dimension: 1, Anchor: []int64{4}}
	cmds := make([]journal.CommandRecord, 0, 10)
	for i := 0; i < 9; i++ {
		cmds = append(cmds, journal.CommandRecord{DomainID: "d", CommandID: fmt.Sprintf("s%d", i), Generation: 1, Epoch: 1, Sequence: uint64(i + 1), Operation: domain.OpSet, Value: []int64{int64(100 + i)}, Status: "STOPPED", Decision: "ADMITTED"})
	}
	cmds = append(cmds, journal.CommandRecord{DomainID: "d", CommandID: "p", Generation: 1, Epoch: 1, Sequence: 10, Operation: domain.OpAdd, Value: []int64{1}, Status: "PENDING", Decision: "ADMITTED"})
	b, stats, err := BuildBeliefWithStats(d, cmds, 256)
	if err != nil {
		t.Fatal(err)
	}
	if stats.Algorithm != "add_only_product_v1" {
		t.Fatalf("STOPPED SETs should preserve the ADD-only (product) path, got %+v", stats)
	}
	if len(b.Configurations) != 1 || b.Configurations[0].Commands["p"] != domain.Pending {
		t.Fatalf("want one configuration with p pending; got %d", len(b.Configurations))
	}
	for _, cfg := range b.Configurations {
		for i := 0; i < 9; i++ {
			if got := cfg.Commands[fmt.Sprintf("s%d", i)]; got != domain.Stopped {
				t.Fatalf("known STOPPED SET not fixed in base: %s", got)
			}
		}
	}
}

func TestFullyTerminalMixedHistorySharedExactReducer(t *testing.T) {
	d := journal.Domain{ID: "d", Generation: 1, InstalledEpoch: 1, Dimension: 1, Anchor: []int64{0}}
	cmds := []journal.CommandRecord{
		{DomainID: "d", CommandID: "s0", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpSet, Value: []int64{7}, Status: "STOPPED", Decision: "ADMITTED"},
		{DomainID: "d", CommandID: "s1", Generation: 1, Epoch: 1, Sequence: 2, Operation: domain.OpSet, Value: []int64{10}, Status: "APPLIED", Decision: "ADMITTED"},
		{DomainID: "d", CommandID: "a", Generation: 1, Epoch: 1, Sequence: 3, Operation: domain.OpAdd, Value: []int64{1}, Status: "APPLIED", Decision: "ADMITTED"},
	}
	b, stats, err := BuildBeliefWithStats(d, cmds, 256)
	if err != nil {
		t.Fatal(err)
	}
	if stats.Algorithm != "terminal_exact_v1" {
		t.Fatalf("want terminal exact reducer, got %+v", stats)
	}
	got := map[int64]bool{}
	for _, cfg := range b.Configurations {
		got[cfg.State[0]] = true
		if cfg.Commands["s0"] != domain.Stopped || cfg.Commands["s1"] != domain.Applied || cfg.Commands["a"] != domain.Applied {
			t.Fatalf("terminal statuses not preserved: %+v", cfg.Commands)
		}
	}
	if len(got) != 2 || !got[10] || !got[11] {
		t.Fatalf("want exact final state set {10,11}, got %v", got)
	}
}

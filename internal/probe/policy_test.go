package probe

import (
	"errors"
	"evicon/internal/journal"
	"evicon/internal/protocol"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"
)

func d1() journal.Domain {
	return journal.Domain{ID: "d", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{12}, Anchor: []int64{4}}
}
func req(id string, v int64) protocol.CommandRequest {
	r := protocol.CommandRequest{SchemaVersion: 1, DomainID: "d", Generation: 1, CommandID: id, Operation: "ADD", Value: []int64{v}, Epoch: 1, Sequence: 100}
	_ = r.ValidateAndFillHash(1)
	return r
}
func cmdsAB() []journal.CommandRecord {
	return []journal.CommandRecord{
		{DomainID: "d", CommandID: "A", Generation: 1, Epoch: 1, Sequence: 1, Operation: "ADD", Value: []int64{4}, IntentHash: "a", Status: "PENDING", Decision: "ADMITTED"},
		{DomainID: "d", CommandID: "B", Generation: 1, Epoch: 1, Sequence: 2, Operation: "ADD", Value: []int64{1}, IntentHash: "b", Status: "PENDING", Decision: "ADMITTED"},
	}
}

func TestM2Largest(t *testing.T) {
	got, err := SelectSimple(M2, d1(), cmdsAB(), req("C", 4), DefaultCostModel(), nil, 256)
	if err != nil || len(got) < 2 || got[0].CommandID != "A" {
		t.Fatalf("got=%v err=%v", got, err)
	}
}
func TestM3Cheapest(t *testing.T) {
	c := DefaultCostModel()
	c.ByCommand = map[string]int64{"A": 5000, "B": 1000}
	got, err := SelectSimple(M3, d1(), cmdsAB(), req("C", 4), c, nil, 256)
	if err != nil || got[0].CommandID != "B" {
		t.Fatalf("got=%v err=%v", got, err)
	}
}
func TestM4DeficitCostPrefersUsefulRelief(t *testing.T) {
	c := DefaultCostModel()
	c.ByCommand = map[string]int64{"A": 1000, "B": 1000}
	got, err := SelectSimple(M4, d1(), cmdsAB(), req("C", 4), c, nil, 256)
	if err != nil || len(got) == 0 || got[0].CommandID != "A" {
		t.Fatalf("got=%v err=%v", got, err)
	}
}
func TestM5PlannerFindsCheapestFeasibleTerminalPath(t *testing.T) {
	c := DefaultCostModel()
	c.ByCommand = map[string]int64{"A": 4000, "B": 1000}
	p := NewPlanner(c, 256, false)
	plan, ok, err := p.Plan("k", d1(), cmdsAB(), req("C", 4), 2, nil, 100*time.Millisecond)
	if err != nil || !ok || len(plan.Steps) == 0 {
		t.Fatalf("plan=%+v ok=%v err=%v", plan, ok, err)
	}
	if plan.Steps[0].CommandID != "B" {
		t.Fatalf("expected B cheapest first, got %+v", plan)
	}
}
func TestM5PlannerNeverUsesPredictionAsEvidence(t *testing.T) {
	c := DefaultCostModel()
	p := NewPlanner(c, 256, false)
	in := cmdsAB()
	_, _, err := p.Plan("k", d1(), in, req("C", 4), 2, nil, 100*time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	for _, x := range in {
		if x.Status != "PENDING" {
			t.Fatalf("planner mutated durable input: %+v", in)
		}
	}
}

func TestM5DepthTwoCanRequireCoordinatedEvidence(t *testing.T) {
	d := d1()
	d.Anchor = []int64{4}
	d.ComponentMax = []int64{12}
	cs := []journal.CommandRecord{
		{DomainID: "d", CommandID: "A", Generation: 1, Epoch: 1, Sequence: 1, Operation: "ADD", Value: []int64{3}, IntentHash: "a", Status: "PENDING", Decision: "ADMITTED"},
		{DomainID: "d", CommandID: "B", Generation: 1, Epoch: 1, Sequence: 2, Operation: "ADD", Value: []int64{3}, IntentHash: "b", Status: "PENDING", Decision: "ADMITTED"},
	}
	cand := req("C", 7)
	p := NewPlanner(DefaultCostModel(), 256, false)
	if _, ok, err := p.Plan("d1", d, cs, cand, 1, nil, 100*time.Millisecond); err != nil || ok {
		t.Fatalf("depth1 should have no feasible path: ok=%v err=%v", ok, err)
	}
	plan, ok, err := p.Plan("d2", d, cs, cand, 2, nil, 100*time.Millisecond)
	if err != nil || !ok || len(plan.Steps) != 2 {
		t.Fatalf("depth2 plan=%+v ok=%v err=%v", plan, ok, err)
	}
}

func TestM5PlannerCacheScopedByCallerKey(t *testing.T) {
	c := DefaultCostModel()
	p := NewPlanner(c, 256, true)
	plan, ok, err := p.Plan("rev1", d1(), cmdsAB(), req("C", 4), 2, nil, 100*time.Millisecond)
	if err != nil || !ok || plan.CacheHit {
		t.Fatalf("first plan=%+v ok=%v err=%v", plan, ok, err)
	}
	plan2, ok2, err2 := p.Plan("rev1", d1(), cmdsAB(), req("C", 4), 2, nil, 100*time.Millisecond)
	if err2 != nil || !ok2 || !plan2.CacheHit {
		t.Fatalf("cached plan=%+v ok=%v err=%v", plan2, ok2, err2)
	}
}

func TestM5PlannerComputeBudgetReturnsDeferSignal(t *testing.T) {
	p := NewPlanner(DefaultCostModel(), 256, false)
	_, _, err := p.Plan("tiny", d1(), cmdsAB(), req("C", 4), 2, nil, time.Nanosecond)
	if !errors.Is(err, ErrPlannerBudget) {
		t.Fatalf("err=%v, want ErrPlannerBudget", err)
	}
}

func TestLoadCostModelSchemaTwoCalibrationOutput(t *testing.T) {
	d := t.TempDir()
	p := filepath.Join(d, "cost.json")
	raw := []byte(`{"schema_version":2,"profile":"E2","placement":"pilot","default_cost_us":500,"costs_us":{"A":400},"measurement":{"statistic":"p95"}}`)
	if err := os.WriteFile(p, raw, 0o644); err != nil {
		t.Fatal(err)
	}
	m, err := LoadCostModel(p)
	if err != nil {
		t.Fatal(err)
	}
	if m.DefaultUS != 500 || m.CostUS("A") != 400 {
		t.Fatalf("unexpected model: %+v", m)
	}
}

func TestPlannerCacheRejectsNonEquivalentInputs(t *testing.T) {
	for _, name := range []string{"revision", "anchor", "bound", "command", "candidate", "excluded", "depth", "cost", "cap"} {
		t.Run(name, func(t *testing.T) {
			p := NewPlanner(DefaultCostModel(), 256, true)
			d, cs, c, depth, excluded := d1(), cmdsAB(), req("C", 4), 2, map[string]bool{}
			if _, ok, err := p.Plan("same", d, cs, c, depth, excluded, time.Second); err != nil || !ok {
				t.Fatalf("warm cache: %v %v", ok, err)
			}
			switch name {
			case "revision":
				d.Revision++
			case "anchor":
				d.Anchor = []int64{3}
			case "bound":
				d.ComponentMax = []int64{11}
			case "command":
				cs[0].Status = "STOPPED"
			case "candidate":
				c = req("D", 5)
			case "excluded":
				excluded["A"] = true
			case "depth":
				depth = 1
			case "cost":
				p.Costs.ByCommand = map[string]int64{"A": 3000, "B": 700}
			case "cap":
				p.MaxConfigurations = 1
			}
			got, ok, err := p.Plan("same", d, cs, c, depth, excluded, time.Second)
			fresh := NewPlanner(p.Costs, p.MaxConfigurations, false)
			want, wok, werr := fresh.Plan("same", d, cs, c, depth, excluded, time.Second)
			if got.CacheHit || ok != wok || (err == nil) != (werr == nil) || got.CostUS != want.CostUS || !reflect.DeepEqual(got.Steps, want.Steps) {
				t.Fatalf("stale cache: got=%+v %v %v want=%+v %v %v", got, ok, err, want, wok, werr)
			}
		})
	}
}

func TestPlannerCacheCopiesValuesAndBoundsMemory(t *testing.T) {
	p := NewPlanner(DefaultCostModel(), 256, true)
	first, ok, err := p.Plan("same", d1(), cmdsAB(), req("C", 4), 2, nil, time.Second)
	if err != nil || !ok {
		t.Fatal(err)
	}
	id := first.Steps[0].CommandID
	first.Steps[0].CommandID = "corrupt"
	second, _, _ := p.Plan("same", d1(), cmdsAB(), req("C", 4), 2, nil, time.Second)
	if !second.CacheHit || second.Steps[0].CommandID != id {
		t.Fatalf("stored alias: %+v", second)
	}
	second.Steps[0].CommandID = "corrupt"
	third, _, _ := p.Plan("same", d1(), cmdsAB(), req("C", 4), 2, nil, time.Second)
	if third.Steps[0].CommandID != id {
		t.Fatal("returned alias")
	}
	_, _, err = p.Plan("same", d1(), cmdsAB(), req("C", 4), 2, nil, time.Nanosecond)
	if !errors.Is(err, ErrPlannerBudget) {
		t.Fatalf("lookup bypasses budget: %v", err)
	}
	for i := 0; i < 300; i++ {
		if _, _, err = p.Plan(fmt.Sprint(i), d1(), cmdsAB(), req("C", 4), 2, nil, time.Second); err != nil {
			t.Fatal(err)
		}
		if len(p.cache) > 256 {
			t.Fatal("unbounded cache")
		}
	}
}

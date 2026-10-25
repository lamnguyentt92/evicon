package probe

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func toyModel(t *testing.T) OutcomeModel {
	t.Helper()
	f := OutcomeFile{SchemaVersion: 1, Profile: "E2", BucketThreshold: 2, DeadlineUS: 20000, CostPenaltyPerUS: 0.00001, MinimumSamples: 5, Split: "development", TrainingSetSHA256: strings.Repeat("a", 64), Rows: map[string]OutcomePrior{
		"ADD:LARGE": {Applied: .95, Stopped: .05, LatencyUS: 5000, Samples: 100},
		"ADD:SMALL": {Applied: .10, Stopped: .90, LatencyUS: 15000, Samples: 100},
	}}
	dir := t.TempDir()
	path := filepath.Join(dir, "model.json")
	b, _ := json.Marshal(f)
	if err := os.WriteFile(path, b, 0600); err != nil {
		t.Fatal(err)
	}
	m, err := LoadOutcomeModel(path)
	if err != nil {
		t.Fatal(err)
	}
	return m
}

func TestM5V2SelectsHigherProbabilityEvidenceDespiteCost(t *testing.T) {
	costs := DefaultCostModel()
	costs.ByCommand = map[string]int64{"A": 5000, "B": 15000}
	model := toyModel(t)
	before := cmdsAB()
	a := AdaptivePlanner{Model: model, Costs: costs, MaxConfigurations: 256}
	choice, ok, err := a.Choose(d1(), before, req("C", 4), 2, nil, 250*time.Millisecond)
	if err != nil || !ok || choice.CommandID != "B" {
		t.Fatalf("choice=%+v ok=%v err=%v", choice, ok, err)
	}
	for _, c := range before {
		if c.Status != "PENDING" {
			t.Fatalf("hypothetical outcome leaked into input: %+v", before)
		}
	}
}
func TestM4PUsesSameModelAndRanksStopProbability(t *testing.T) {
	costs := DefaultCostModel()
	costs.ByCommand = map[string]int64{"A": 5000, "B": 15000}
	ranking, err := SelectM4P(d1(), cmdsAB(), req("C", 4), costs, toyModel(t), nil, 256)
	if err != nil || len(ranking) != 2 || ranking[0].CommandID != "B" {
		t.Fatalf("ranking=%+v err=%v", ranking, err)
	}
}
func TestM5V2RejectsNoTimeOrComputeBudget(t *testing.T) {
	m := toyModel(t)
	m.Spec.DeadlineUS = 1
	a := AdaptivePlanner{Model: m, Costs: DefaultCostModel(), MaxConfigurations: 256}
	_, ok, err := a.Choose(d1(), cmdsAB(), req("C", 4), 2, nil, time.Second)
	if err != nil || ok {
		t.Fatalf("deadline proxy must reject: %v %v", ok, err)
	}
	_, _, err = a.Choose(d1(), cmdsAB(), req("C", 4), 2, nil, 0)
	if err != ErrPlannerBudget {
		t.Fatalf("wanted budget fail-closed got %v", err)
	}
}
func TestOutcomeModelRejectsLeakageAndInvalidProbabilities(t *testing.T) {
	x := toyModel(t)
	x.Spec.Split = "final"
	b, _ := json.Marshal(x.Spec)
	f := filepath.Join(t.TempDir(), "bad.json")
	os.WriteFile(f, b, 0600)
	if _, err := LoadOutcomeModel(f); err == nil {
		t.Fatal("accepted final-labelled model")
	}
	x.Spec.Split = "development"
	row := x.Spec.Rows["ADD:LARGE"]
	row.Stopped = .99
	x.Spec.Rows["ADD:LARGE"] = row
	b, _ = json.Marshal(x.Spec)
	os.WriteFile(f, b, 0600)
	if _, err := LoadOutcomeModel(f); err == nil {
		t.Fatal("accepted non-normalized model")
	}
}
func TestM5V2TwoWitnessesAndNoUnverifiedOutcome(t *testing.T) {
	d := d1()
	d.ComponentMax = []int64{12}
	cs := cmdsAB()
	cs[0].Value = []int64{3}
	cs[1].Value = []int64{3}
	m := toyModel(t)
	m.Spec.CostPenaltyPerUS = 0 // two rare STOPPED outcomes still form a valid mechanism witness
	a := AdaptivePlanner{Model: m, Costs: DefaultCostModel(), MaxConfigurations: 256}
	choice, ok, err := a.Choose(d, cs, req("C", 7), 2, nil, time.Second)
	if err != nil || !ok || choice.CommandID == "" {
		t.Fatalf("two-step select failed %+v %v %v", choice, ok, err)
	}
	safe, err := projectedSafe(d, cs, req("C", 7), 256)
	if err != nil || safe {
		t.Fatalf("hypothetical plan cannot authorize SAFE: safe=%v err=%v", safe, err)
	}
}

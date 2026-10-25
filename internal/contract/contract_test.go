package contract

import (
	"testing"

	"evicon/internal/domain"
)

func testContract(required ...Guarantee) Contract {
	m := int64(12)
	return Contract{ID: "c", Version: "1", RequiredGuarantees: required, Bound: domain.HardBound{SumMax: &m}}
}

func TestE1RuleCatalogSupportsOnlyItsPremises(t *testing.T) {
	cap := Capability{SupportedOperations: []domain.Operation{domain.OpAdd, domain.OpSet}, MaxDimension: 2, Adapter: ProfileE1("e1-test")}
	plan, err := Compile(testContract(GuaranteeEffectAtMostOnce, GuaranteeTruthfulApplied, GuaranteeTerminalStopped), cap, 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.UnmetGuarantees) != 1 || plan.UnmetGuarantees[0] != GuaranteeTerminalStopped {
		t.Fatalf("E1 must not claim terminal STOPPED, got %+v", plan)
	}
	if len(plan.SatisfiedGuarantees) != 2 {
		t.Fatalf("E1 should satisfy at-most-once and truthful APPLIED, got %+v", plan)
	}
	if plan.ContractHash == "" || plan.Profile != "E1" || plan.AdapterVersion != "e1-test" {
		t.Fatalf("plan must export profile/version/hash, got %+v", plan)
	}
}

func TestE2SupportsBarrierAndStoppedRules(t *testing.T) {
	cap := Capability{SupportedOperations: []domain.Operation{domain.OpAdd, domain.OpSet}, MaxDimension: 2, Adapter: ProfileE2("e2-test")}
	plan, err := Compile(testContract(GuaranteeTerminalStopped, GuaranteeValidAnchor, GuaranteeOrderedSet), cap, 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.UnmetGuarantees) != 0 || len(plan.SatisfiedGuarantees) != 3 {
		t.Fatalf("E2 should satisfy requested barrier/stopped/order guarantees, got %+v", plan)
	}
}

func TestE3AtomicEffectReceiptSupportsTruthAndAtMostOnce(t *testing.T) {
	cap := Capability{SupportedOperations: []domain.Operation{domain.OpAdd, domain.OpSet}, MaxDimension: 2, Adapter: ProfileE3("e3-test")}
	plan, err := Compile(testContract(GuaranteeEffectAtMostOnce, GuaranteeTruthfulApplied, GuaranteeOrderedSet), cap, 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.UnmetGuarantees) != 0 {
		t.Fatalf("E3 should satisfy requested guarantees, got %+v", plan)
	}
}

func TestE0DoesNotGainGuaranteeByLabel(t *testing.T) {
	cap := Capability{SupportedOperations: []domain.Operation{domain.OpAdd}, MaxDimension: 1, Adapter: ProfileE0("e0-test")}
	plan, err := Compile(testContract(GuaranteeEffectAtMostOnce), cap, 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.UnmetGuarantees) != 1 {
		t.Fatalf("E0 must not claim at-most-once, got %+v", plan)
	}
}

func TestSpecificationProfilesDoNotPreclaimVerifiedFaultTests(t *testing.T) {
	for _, p := range []AdapterProfile{ProfileE0("e0"), ProfileE1("e1"), ProfileE2("e2"), ProfileE3("e3")} {
		if len(p.VerifiedTestIDs) != 0 {
			t.Fatalf("P0 profile %s must not preclaim runtime fault tests: %+v", p.Profile, p.VerifiedTestIDs)
		}
	}
}

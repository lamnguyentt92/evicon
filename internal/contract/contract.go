package contract

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"

	"evicon/internal/domain"
)

// Guarantee names are intentionally descriptive. P0 removes the old single-
// letter A/F/S/D/X/O/B labels because the TOIT plan requires every claimed
// guarantee to have explicit premises and verification evidence.
type Guarantee string

const (
	GuaranteeEffectAtMostOnce Guarantee = "EFFECT_AT_MOST_ONCE"
	GuaranteeTruthfulApplied  Guarantee = "TRUTHFUL_APPLIED"
	GuaranteeTerminalStopped  Guarantee = "TERMINAL_STOPPED"
	GuaranteeOrderedSet       Guarantee = "ORDERED_SET"
	GuaranteeValidAnchor      Guarantee = "VALID_ANCHOR"
)

type Premise string

const (
	PremiseDurableClaimBeforeEffect          Premise = "DURABLE_CLAIM_BEFORE_EFFECT"
	PremiseStableCommandID                   Premise = "STABLE_COMMAND_ID"
	PremiseNoReplayAfterCrash                Premise = "NO_REPLAY_AFTER_CRASH"
	PremiseDriverNoDuplicateEffect           Premise = "DRIVER_NO_DUPLICATE_EFFECT"
	PremiseReceiptAfterEffect                Premise = "RECEIPT_COMMIT_AFTER_EFFECT"
	PremiseAtomicEffectReceipt               Premise = "ATOMIC_EFFECT_AND_RECEIPT"
	PremiseStopConfirmedBeforeEffect         Premise = "STOP_CONFIRMED_BEFORE_EFFECT"
	PremiseOwnerBlocksLateAttempt            Premise = "OWNER_BLOCKS_LATE_ATTEMPT"
	PremiseSerializedGenerationEpochSequence Premise = "SERIALIZED_GENERATION_EPOCH_SEQUENCE"
	PremiseDurableCutoff                     Premise = "DURABLE_CUTOFF"
	PremiseDrainInflightEffects              Premise = "DRAIN_INFLIGHT_EFFECTS"
	PremiseSerializedStateRead               Premise = "SERIALIZED_STATE_READ"
)

type Contract struct {
	ID                 string           `json:"id"`
	Version            string           `json:"version"`
	RequiredGuarantees []Guarantee      `json:"requiredGuarantees,omitempty"`
	Bound              domain.HardBound `json:"bound"`
}

type AdapterProfile struct {
	AdapterVersion  string    `json:"adapterVersion"`
	Profile         string    `json:"profile"`
	Premises        []Premise `json:"premises"`
	VerifiedTestIDs []string  `json:"verifiedTestIds,omitempty"`
}

type Capability struct {
	SupportedOperations []domain.Operation `json:"supportedOperations"`
	MaxDimension        int                `json:"maxDimension"`
	Adapter             AdapterProfile     `json:"adapter"`
}

type RuleEvidence struct {
	Guarantee       Guarantee `json:"guarantee"`
	Satisfied       bool      `json:"satisfied"`
	SatisfiedBy     []Premise `json:"satisfiedBy,omitempty"`
	MissingPremises []Premise `json:"missingPremises,omitempty"`
	VerifiedTestIDs []string  `json:"verifiedTestIds,omitempty"`
}

type Plan struct {
	ContractID          string         `json:"contractId"`
	ContractVersion     string         `json:"contractVersion"`
	ContractHash        string         `json:"contractHash"`
	AdapterVersion      string         `json:"adapterVersion"`
	Profile             string         `json:"profile"`
	SatisfiedGuarantees []Guarantee    `json:"satisfiedGuarantees,omitempty"`
	UnmetGuarantees     []Guarantee    `json:"unmetGuarantees,omitempty"`
	RuleEvidence        []RuleEvidence `json:"ruleEvidence,omitempty"`
}

type rule struct {
	Guarantee Guarantee
	// Every group is an alternative; all premises in one group are required.
	AnyOf [][]Premise
}

var catalog = []rule{
	{
		Guarantee: GuaranteeEffectAtMostOnce,
		AnyOf: [][]Premise{
			{
				PremiseDurableClaimBeforeEffect,
				PremiseStableCommandID,
				PremiseNoReplayAfterCrash,
				PremiseDriverNoDuplicateEffect,
			},
			{PremiseStableCommandID, PremiseAtomicEffectReceipt},
		},
	},
	{
		Guarantee: GuaranteeTruthfulApplied,
		AnyOf: [][]Premise{
			{PremiseReceiptAfterEffect},
			{PremiseAtomicEffectReceipt},
		},
	},
	{
		Guarantee: GuaranteeTerminalStopped,
		AnyOf: [][]Premise{{
			PremiseStopConfirmedBeforeEffect,
			PremiseOwnerBlocksLateAttempt,
		}},
	},
	{
		Guarantee: GuaranteeOrderedSet,
		AnyOf:     [][]Premise{{PremiseSerializedGenerationEpochSequence}},
	},
	{
		Guarantee: GuaranteeValidAnchor,
		AnyOf: [][]Premise{{
			PremiseDurableCutoff,
			PremiseDrainInflightEffects,
			PremiseSerializedStateRead,
		}},
	},
}

func RuleCatalog() []Guarantee {
	out := make([]Guarantee, 0, len(catalog))
	for _, r := range catalog {
		out = append(out, r.Guarantee)
	}
	return out
}

func Compile(c Contract, k Capability, dim int) (Plan, error) {
	if c.ID == "" {
		return Plan{}, fmt.Errorf("contract ID is required")
	}
	if k.MaxDimension < dim {
		return Plan{}, fmt.Errorf("capability max dimension %d is smaller than %d", k.MaxDimension, dim)
	}
	if err := c.Bound.Validate(dim); err != nil {
		return Plan{}, err
	}
	if k.Adapter.AdapterVersion == "" || k.Adapter.Profile == "" {
		return Plan{}, fmt.Errorf("adapterVersion and profile are required")
	}

	hash, err := Hash(c)
	if err != nil {
		return Plan{}, err
	}
	plan := Plan{
		ContractID:      c.ID,
		ContractVersion: c.Version,
		ContractHash:    hash,
		AdapterVersion:  k.Adapter.AdapterVersion,
		Profile:         k.Adapter.Profile,
	}
	premises := make(map[Premise]bool, len(k.Adapter.Premises))
	for _, p := range k.Adapter.Premises {
		premises[p] = true
	}

	for _, g := range c.RequiredGuarantees {
		r, ok := lookupRule(g)
		if !ok {
			return Plan{}, fmt.Errorf("unknown required guarantee %q", g)
		}
		ev := evaluateRule(r, premises)
		ev.VerifiedTestIDs = append([]string(nil), k.Adapter.VerifiedTestIDs...)
		plan.RuleEvidence = append(plan.RuleEvidence, ev)
		if ev.Satisfied {
			plan.SatisfiedGuarantees = append(plan.SatisfiedGuarantees, g)
		} else {
			plan.UnmetGuarantees = append(plan.UnmetGuarantees, g)
		}
	}
	sort.Slice(plan.SatisfiedGuarantees, func(i, j int) bool { return plan.SatisfiedGuarantees[i] < plan.SatisfiedGuarantees[j] })
	sort.Slice(plan.UnmetGuarantees, func(i, j int) bool { return plan.UnmetGuarantees[i] < plan.UnmetGuarantees[j] })
	return plan, nil
}

func lookupRule(g Guarantee) (rule, bool) {
	for _, r := range catalog {
		if r.Guarantee == g {
			return r, true
		}
	}
	return rule{}, false
}

func evaluateRule(r rule, have map[Premise]bool) RuleEvidence {
	bestMissing := []Premise(nil)
	for _, group := range r.AnyOf {
		missing := make([]Premise, 0)
		matched := make([]Premise, 0, len(group))
		for _, p := range group {
			if have[p] {
				matched = append(matched, p)
			} else {
				missing = append(missing, p)
			}
		}
		if len(missing) == 0 {
			return RuleEvidence{Guarantee: r.Guarantee, Satisfied: true, SatisfiedBy: matched}
		}
		if bestMissing == nil || len(missing) < len(bestMissing) {
			bestMissing = missing
		}
	}
	return RuleEvidence{Guarantee: r.Guarantee, Satisfied: false, MissingPremises: bestMissing}
}

func Hash(c Contract) (string, error) {
	b, err := json.Marshal(c)
	if err != nil {
		return "", err
	}
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:]), nil
}

func SupportsOperation(k Capability, op domain.Operation) bool {
	for _, x := range k.SupportedOperations {
		if x == op {
			return true
		}
	}
	return false
}

// CoreModelProfile is only for the scenario/model runner. It does not claim
// durable runtime guarantees; those are introduced in P1/P2 with real adapters.
func CoreModelProfile() AdapterProfile {
	return AdapterProfile{AdapterVersion: "core-model-005", Profile: "CORE_MODEL"}
}

// ProfileE0/ProfileE1/ProfileE2/ProfileE3 are P0 specification templates.
// They enumerate premises required by the plan, but intentionally leave
// VerifiedTestIDs empty until P1/P2 implement the corresponding runtime fault
// tests. A profile name alone is not evidence that its premises hold.
func ProfileE0(adapterVersion string) AdapterProfile {
	return AdapterProfile{AdapterVersion: adapterVersion, Profile: "E0"}
}

func ProfileE1(adapterVersion string) AdapterProfile {
	return AdapterProfile{
		AdapterVersion: adapterVersion,
		Profile:        "E1",
		Premises: []Premise{
			PremiseDurableClaimBeforeEffect,
			PremiseStableCommandID,
			PremiseNoReplayAfterCrash,
			PremiseDriverNoDuplicateEffect,
			PremiseReceiptAfterEffect,
		},
	}
}

func ProfileE2(adapterVersion string) AdapterProfile {
	p := ProfileE1(adapterVersion)
	p.Profile = "E2"
	p.Premises = append(p.Premises,
		PremiseStopConfirmedBeforeEffect,
		PremiseOwnerBlocksLateAttempt,
		PremiseSerializedGenerationEpochSequence,
		PremiseDurableCutoff,
		PremiseDrainInflightEffects,
		PremiseSerializedStateRead,
	)
	return p
}

func ProfileE3(adapterVersion string) AdapterProfile {
	return AdapterProfile{
		AdapterVersion: adapterVersion,
		Profile:        "E3",
		Premises: []Premise{
			PremiseStableCommandID,
			PremiseAtomicEffectReceipt,
			PremiseSerializedGenerationEpochSequence,
		},
	}
}

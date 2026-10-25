package adapter

import (
	"fmt"
	"strings"

	"evicon/internal/contract"
)

// RuntimeEvidenceContract maps a runtime alias (E0, E1, E2, E2_STOP, E3) to
// the declared evidence contract and the adapter profile whose verified
// premises back it. Capabilities that the profile cannot back are dropped by
// contract.RuntimeContract (e.g. Q_S on legacy E2).
//
// The returned profile is the profile the endpoint must advertise; callers
// that talk to a remote endpoint must check that every premise is present in
// the capability handshake before trusting the contract.
func RuntimeEvidenceContract(alias string) (contract.EvidenceContract, contract.AdapterProfile, error) {
	name := strings.ToUpper(strings.TrimSpace(alias))
	prof := name
	if name == "E2_STOP" {
		prof = "E2"
	}
	var ap contract.AdapterProfile
	switch name {
	case "E0":
		ap = contract.ProfileE0("sim-e0")
	case "E1":
		ap = P2E1Profile()
	case "E2":
		ap = P2E2Profile()
	case "E2_STOP":
		ap = E2StopProfile()
	case "E3":
		ap = P1E3Profile()
	default:
		return contract.EvidenceContract{}, contract.AdapterProfile{}, fmt.Errorf("unknown runtime evidence contract alias %q (want E0, E1, E2, E2_STOP or E3)", alias)
	}
	declared, err := contract.EvidenceContractForProfile(prof)
	if err != nil {
		return contract.EvidenceContract{}, contract.AdapterProfile{}, err
	}
	c, err := contract.RuntimeContract("runtime-"+name, declared, ap)
	return c, ap, err
}

// EndpointProfileFor returns the endpoint -profile value an alias runs on.
func EndpointProfileFor(alias string) string {
	n := strings.ToUpper(strings.TrimSpace(alias))
	if n == "E2_STOP" {
		return "E2"
	}
	return n
}

// MissingPremises lists premises of want that the advertised set lacks.
func MissingPremises(want contract.AdapterProfile, advertised []string) []string {
	have := map[string]bool{}
	for _, p := range advertised {
		have[p] = true
	}
	var miss []string
	for _, p := range want.Premises {
		if !have[string(p)] {
			miss = append(miss, string(p))
		}
	}
	return miss
}

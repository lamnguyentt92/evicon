package adapter

import "evicon/internal/contract"

func P2E1Profile() contract.AdapterProfile {
	p := contract.ProfileE1("e1-split-sqlite-cgo-008-patch1")
	p.VerifiedTestIDs = []string{"F04", "F06", "P2_RECEIPT_VALIDATION_PATHS"}
	return p
}

func P2E2Profile() contract.AdapterProfile {
	p := contract.ProfileE2("e2-split-sqlite-cgo-008-patch1")
	p.VerifiedTestIDs = []string{"F02", "F03", "F05", "F07", "F08", "F11", "F12", "F17", "F18", "P2-E2-BARRIER", "P2_PARTIAL_BARRIER", "P2_BARRIER_RESUME_WITH_NEW_WORK", "P2_AUTHORITY_RESTART_DURING_BARRIER", "P2_TERMINAL_COMPACTION_E2"}
	return p
}

// P1/P2 retain E3 as the strong software-owner reference profile.
func P1E3Profile() contract.AdapterProfile {
	return contract.AdapterProfile{
		AdapterVersion: "e3-sqlite-cgo-008-patch1",
		Profile:        "E3",
		Premises: []contract.Premise{
			contract.PremiseStableCommandID,
			contract.PremiseAtomicEffectReceipt,
			contract.PremiseSerializedGenerationEpochSequence,
		},
		VerifiedTestIDs: []string{"F01", "F09", "F10", "F15", "F16", "F17_E3", "P1-E3-SET-ORDERING", "P2_SEQUENCE_ZERO_OR_MISSING", "P2_TERMINAL_COMPACTION_E3"},
	}
}

// LinuxTCProfile is deliberately weaker than E2. Each ADD(k) is mapped to a
// deterministic HTB child class after a durable intent, but the kernel mutation
// and terminal receipt are not atomic. Same-ID replay may re-execute a
// convergent class replace. Do not upgrade this profile to E2/E3 without new
// implementation evidence.
func LinuxTCProfile() contract.AdapterProfile {
	return contract.AdapterProfile{
		AdapterVersion: "linux-tc-set-v1-016",
		Profile:        "LINUX_TC_E2_LIKE",
		Premises: []contract.Premise{
			contract.PremiseDurableClaimBeforeEffect,
			contract.PremiseStableCommandID,
			contract.PremiseReceiptAfterEffect,
			contract.PremiseSerializedGenerationEpochSequence,
		},
		VerifiedTestIDs: []string{"L-B1", "L-B2", "L-B3", "L-B4", "L-B5", "L-B6", "L-B7", "L-B8", "L-B9"},
	}
}

// E2StopProfile is the 061 E2 profile with the per-identity terminal stop
// (POST /v1/stop). The extra premise is backed by tests F21-F23 in
// internal/adapter/e12_stop_test.go; RuntimeContract only grants Q_S when the
// premise is present.
func E2StopProfile() contract.AdapterProfile {
	p := P2E2Profile()
	p.AdapterVersion = "e2-split-sqlite-cgo-061-stop"
	p.Premises = append(p.Premises, contract.PremisePerIdentityStop)
	p.VerifiedTestIDs = append(append([]string(nil), p.VerifiedTestIDs...), "F21", "F22", "F23")
	return p
}

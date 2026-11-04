package outbox

import (
	"time"

	"evicon/internal/contract"
)

// ExpiryFromContract is the default of the authority's -expires-after flag: the expiry of every dispatch comes from
// the evidence contract.
const ExpiryFromContract time.Duration = -1

// DispatchExpiry gives the dispatcher's ExpiresAfter from the authority's -expires-after flag and its evidence
// contract, by the rule of releases 061-084 (cmd/evicon-authority/main.go; release 085 moved it here unchanged so that
// it is tested): expiresAfter >= 0 is used as given; otherwise the contract's owner expiry when the contract has T
// (expiry_ms); otherwise none (0).
//
// Release 085 (review M3b): -expires-after is transport metadata. It only sets expires_at_ms = creation +
// ExpiresAfter on every dispatch (a platform such as ThingsBoard turns it into the RPC's expirationTime); it is not a
// capability and never enters the contract, its compilation, admission, resolution or release (none of them reads the
// dispatcher's ExpiresAfter). A strict contract without T plus -expires-after 60s therefore dispatches exactly as a
// contract with T(60 s) and no declared record retention does (TestExpiresAfterDispatchIdentical085), and the two
// contracts compile to the same decisions (internal/resolve TestTTLChangesNoDecision085).
func DispatchExpiry(expiresAfter time.Duration, ec contract.EvidenceContract) time.Duration {
	switch {
	case expiresAfter >= 0:
		return expiresAfter
	case ec.Has(contract.CapExpiry):
		return time.Duration(ec.ExpiryMS) * time.Millisecond
	}
	return 0
}

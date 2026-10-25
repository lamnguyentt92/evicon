package probe

// EviCon 060 experimental evidence transition verifier. This is a read-only
// check of two journal snapshots: caller MUST ensure the snapshots originate
// from a trusted authority. No remote receipt signature, barrier or admission
// is produced or validated by this package.
import (
	"encoding/json"
	"fmt"

	"evicon/internal/journal"
	"evicon/internal/protocol"
)

type DischargeAssessment struct {
	Status                        string `json:"status"`
	Reason                        string `json:"reason"`
	ConditionalCertificateMatched bool   `json:"conditional_certificate_matched"`
	CurrentGuardSafe              bool   `json:"current_guard_safe"`
	AdmissionAuthorized           bool   `json:"admission_authorized"`
	TrustedSourceVerified         bool   `json:"trusted_source_verified"`
	InitialRevision               uint64 `json:"initial_revision"`
	ObservedRevision              uint64 `json:"observed_revision"`
}

// AssessCertificateDischarge requires that the ONLY differences between the
// pre/post observations are the revisions and exact terminal status transitions
// promised by the certificate. This intentionally rejects otherwise-legal
// interleavings; a false DEFER is preferable to spurious evidence acceptance.
// Observed snapshots supplied by a caller are not independently authenticated.
func AssessCertificateDischarge(before journal.Domain, pre []journal.CommandRecord, after journal.Domain, post []journal.CommandRecord, candidate protocol.CommandRequest, cert ContinuationCertificate, maxCfg int) (DischargeAssessment, error) {
	r := DischargeAssessment{Status: "DEFER", Reason: "UNVERIFIED_OR_CHANGED_SNAPSHOT", InitialRevision: before.Revision, ObservedRevision: after.Revision}
	good, err := VerifyContinuationCertificate(before, pre, candidate, cert, maxCfg)
	if err != nil {
		return r, err
	}
	if !good {
		r.Reason = "INVALID_CONDITIONAL_CERTIFICATE"
		return r, nil
	}
	oldAfter := after
	oldAfter.Revision = before.Revision
	a, _ := json.Marshal(before)
	b, _ := json.Marshal(oldAfter)
	if after.Revision <= before.Revision || string(a) != string(b) {
		r.Reason = "REVISION_OR_DOMAIN_CONTRACT_CHANGED"
		return r, nil
	}
	if len(pre) != len(post) {
		r.Reason = "COMMAND_SET_CHANGED"
		return r, nil
	}
	required := map[string]string{}
	for _, assumption := range cert.Required {
		required[assumption.CommandID] = assumption.Outcome
	}
	observed := map[string]journal.CommandRecord{}
	for _, cmd := range post {
		if cmd.CommandID == "" {
			return r, fmt.Errorf("empty observed command id")
		}
		if _, ok := observed[cmd.CommandID]; ok {
			r.Reason = "DUPLICATE_COMMAND_ID"
			return r, nil
		}
		observed[cmd.CommandID] = cmd
	}
	for _, old := range pre {
		if old.CommandID == candidate.CommandID {
			r.Reason = "CANDIDATE_ALREADY_PRESENT"
			return r, nil
		}
		cur, ok := observed[old.CommandID]
		if !ok {
			r.Reason = "COMMAND_SET_CHANGED"
			return r, nil
		}
		expectedStatus := old.Status
		if outcome, ok := required[old.CommandID]; ok {
			expectedStatus = outcome
		}
		if cur.Status != expectedStatus {
			r.Reason = "REQUIRED_OUTCOME_NOT_OBSERVED"
			return r, nil
		}
		// Compare all immutable command attributes, including hashes, sequence,
		// reservation and decision; journal status/reason may change on receipt.
		x := old
		y := cur
		x.Status = ""
		y.Status = ""
		x.Reason = ""
		y.Reason = ""
		aa, _ := json.Marshal(x)
		bb, _ := json.Marshal(y)
		if string(aa) != string(bb) {
			r.Reason = "COMMAND_IDENTITY_CHANGED"
			return r, nil
		}
	}
	// The caller can choose to verify provenance separately. This function
	// cannot promote an unauthenticated JSON snapshot into trusted receipts.
	r.ConditionalCertificateMatched = true
	safe, err := projectedSafe(after, post, candidate, maxCfg)
	if err != nil {
		return r, err
	}
	r.CurrentGuardSafe = safe
	if !safe {
		r.Reason = "CURRENT_GUARD_NOT_SAFE"
		return r, nil
	}
	r.Status = "CONDITIONAL_MATCH_ONLY"
	r.Reason = "MATCHED_JOURNAL_SNAPSHOT_BUT_NO_RECEIPT_AUTHENTICATION_OR_ADMISSION"
	return r, nil
}

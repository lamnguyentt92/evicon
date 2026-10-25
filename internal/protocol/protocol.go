package protocol

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"

	"evicon/internal/domain"
)

const SchemaVersion = 1

type CommandRequest struct {
	SchemaVersion int              `json:"schema_version"`
	DomainID      string           `json:"domain_id"`
	Generation    uint64           `json:"generation"`
	CommandID     string           `json:"command_id"`
	Operation     domain.Operation `json:"operation"`
	Value         []int64          `json:"value"`
	Epoch         uint64           `json:"epoch"`
	Sequence      uint64           `json:"sequence"`
	IntentHash    string           `json:"intent_hash,omitempty"`
	TraceID       string           `json:"trace_id,omitempty"`
	// ExpiresAtMS (release 061, optional): absolute owner-enforced deadline
	// (Unix ms). Owners with capability T must not start the effect after it.
	// Not part of the intent hash: it bounds *when*, not *what*.
	ExpiresAtMS int64 `json:"expires_at_ms,omitempty"`
}

type canonicalIntent struct {
	SchemaVersion int              `json:"schema_version"`
	DomainID      string           `json:"domain_id"`
	Generation    uint64           `json:"generation"`
	CommandID     string           `json:"command_id"`
	Operation     domain.Operation `json:"operation"`
	Value         []int64          `json:"value"`
	Epoch         uint64           `json:"epoch"`
	Sequence      uint64           `json:"sequence"`
}

func (r CommandRequest) CanonicalHash() (string, error) {
	c := canonicalIntent{SchemaVersion: r.SchemaVersion, DomainID: r.DomainID, Generation: r.Generation, CommandID: r.CommandID, Operation: r.Operation, Value: r.Value, Epoch: r.Epoch, Sequence: r.Sequence}
	b, err := json.Marshal(c)
	if err != nil {
		return "", err
	}
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:]), nil
}
func (r *CommandRequest) ValidateAndFillHash(dim int) error {
	if r.SchemaVersion == 0 {
		r.SchemaVersion = SchemaVersion
	}
	if r.SchemaVersion != SchemaVersion {
		return fmt.Errorf("unsupported schema_version %d", r.SchemaVersion)
	}
	if r.DomainID == "" || r.CommandID == "" {
		return fmt.Errorf("domain_id and command_id are required")
	}
	if r.Sequence == 0 {
		return fmt.Errorf("sequence must be greater than zero")
	}
	cmd := domain.Command{ID: r.CommandID, Generation: r.Generation, Epoch: r.Epoch, Sequence: r.Sequence, Operation: r.Operation, Value: r.Value}
	if err := cmd.Validate(dim); err != nil {
		return err
	}
	h, err := r.CanonicalHash()
	if err != nil {
		return err
	}
	if r.IntentHash != "" && r.IntentHash != h {
		return fmt.Errorf("intent_hash mismatch")
	}
	r.IntentHash = h
	return nil
}
func (r CommandRequest) DomainCommand() domain.Command {
	return domain.Command{ID: r.CommandID, Generation: r.Generation, Epoch: r.Epoch, Sequence: r.Sequence, Operation: r.Operation, Value: append([]int64(nil), r.Value...)}
}

type Receipt struct {
	SchemaVersion  int     `json:"schema_version"`
	DomainID       string  `json:"domain_id"`
	Generation     uint64  `json:"generation"`
	CommandID      string  `json:"command_id"`
	IntentHash     string  `json:"intent_hash"`
	Outcome        string  `json:"outcome"`
	OwnerVersion   uint64  `json:"owner_version"`
	ReceiptVersion uint64  `json:"receipt_version"`
	State          []int64 `json:"state"`
	Reason         string  `json:"reason,omitempty"`
}

// ValidateReceipt validates application evidence independently of its ingress
// path. Dispatch replies, explicit receipt queries, and barrier reconciliation
// must all use the same rules before evidence can narrow the authority belief.
func ValidateReceipt(rc Receipt, expectedDomain string, expectedGeneration uint64, expectedCommandID, expectedIntentHash string, dim int) error {
	if rc.SchemaVersion != SchemaVersion {
		return fmt.Errorf("unsupported receipt schema_version %d", rc.SchemaVersion)
	}
	if rc.DomainID != expectedDomain || rc.Generation != expectedGeneration || rc.CommandID != expectedCommandID || rc.IntentHash != expectedIntentHash {
		return fmt.Errorf("evidence identity/version mismatch")
	}
	switch rc.Outcome {
	case "APPLIED", "STOPPED":
		if rc.ReceiptVersion == 0 {
			return fmt.Errorf("terminal receipt_version must be non-zero")
		}
		if rc.OwnerVersion == 0 {
			return fmt.Errorf("terminal owner_version must be non-zero")
		}
		if dim > 0 && len(rc.State) != dim {
			return fmt.Errorf("terminal receipt state dimension %d, want %d", len(rc.State), dim)
		}
	case "UNCERTAIN", "PENDING", "NOT_FOUND":
	default:
		return fmt.Errorf("unsupported evidence outcome %q", rc.Outcome)
	}
	return nil
}

type AuthorityResponse struct {
	SchemaVersion int    `json:"schema_version"`
	DomainID      string `json:"domain_id"`
	CommandID     string `json:"command_id"`
	IntentHash    string `json:"intent_hash"`
	Decision      string `json:"decision"`
	Status        string `json:"status"`
	Reason        string `json:"reason"`
	Revision      uint64 `json:"revision"`
}

type JobCapabilities struct {
	Enabled             bool   `json:"enabled"`
	GrantOperation      string `json:"grant_operation,omitempty"`
	CompletionTask      string `json:"completion_task,omitempty"`
	OwnerVerifiesResult bool   `json:"owner_verifies_result"`
	LeaseFencing        bool   `json:"lease_fencing"`
}

type EndpointCapabilities struct {
	SchemaVersion       int                `json:"schema_version"`
	Profile             string             `json:"profile"`
	AdapterVersion      string             `json:"adapter_version"`
	DomainID            string             `json:"domain_id"`
	Generation          uint64             `json:"generation"`
	InstalledEpoch      uint64             `json:"installed_epoch"`
	Dimension           int                `json:"dimension"`
	SupportedOperations []domain.Operation `json:"supported_operations"`
	ComponentMin        []int64            `json:"component_min,omitempty"`
	ComponentMax        []int64            `json:"component_max,omitempty"`
	SumMax              *int64             `json:"sum_max,omitempty"`
	Premises            []string           `json:"premises,omitempty"`
	VerifiedTestIDs     []string           `json:"verified_test_ids,omitempty"`
	Jobs                *JobCapabilities   `json:"jobs,omitempty"`
	// EvidenceContract (release 061, optional): the owner's declared evidence
	// contract (internal/contract JSON). Authorities only trust it together
	// with the verified premises/test IDs and an explicit operator flag.
	EvidenceContract json.RawMessage `json:"evidence_contract,omitempty"`
}

type EvidenceQueryRequest struct {
	SchemaVersion int    `json:"schema_version"`
	DomainID      string `json:"domain_id"`
	CommandID     string `json:"command_id"`
}

type BarrierRequest struct {
	SchemaVersion  int    `json:"schema_version"`
	DomainID       string `json:"domain_id"`
	Generation     uint64 `json:"generation"`
	Token          string `json:"token"`
	CutoffSequence uint64 `json:"cutoff_sequence"`
}

type BarrierReceipt struct {
	SchemaVersion  int       `json:"schema_version"`
	DomainID       string    `json:"domain_id"`
	Generation     uint64    `json:"generation"`
	Token          string    `json:"token"`
	CutoffSequence uint64    `json:"cutoff_sequence"`
	OwnerVersion   uint64    `json:"owner_version"`
	State          []int64   `json:"state"`
	Receipts       []Receipt `json:"receipts,omitempty"`
	Reason         string    `json:"reason"`
}

// StopRequest asks the owner for a per-identity terminal stop (Q_S, release
// 061). The owner answers with a terminal receipt: APPLIED if the identity
// already took effect, otherwise STOPPED after durably recording a tombstone
// that makes every later attempt with this identity STOPPED.
type StopRequest struct {
	SchemaVersion int    `json:"schema_version"`
	DomainID      string `json:"domain_id"`
	Generation    uint64 `json:"generation"`
	CommandID     string `json:"command_id"`
	IntentHash    string `json:"intent_hash"`
	Sequence      uint64 `json:"sequence"`
}

func (r StopRequest) Validate() error {
	if r.SchemaVersion != SchemaVersion {
		return fmt.Errorf("unsupported stop schema_version %d", r.SchemaVersion)
	}
	if r.DomainID == "" || r.CommandID == "" || r.IntentHash == "" {
		return fmt.Errorf("stop requires domain_id, command_id and intent_hash")
	}
	if r.Sequence == 0 {
		return fmt.Errorf("stop sequence must be greater than zero")
	}
	return nil
}

package contract

import (
	"encoding/json"
	"strings"
	"testing"
)

// Release 084 (paper verification): expiry-revealed evidence is ER = T and (X or (Q_A and r = infinity)), where r is the
// retention of the owner's records. Unbounded suppression (D_h with h = infinity) is not enough: an owner may suppress
// forever and discard records, and then a NOT_FOUND after the expiry would be admitted although the record of an applied
// identity may be gone.
func TestExpiryRevealedNeedsUnboundedRetention084(t *testing.T) {
	base := EvidenceContract{SchemaVersion: 1, ID: "dq-t", Source: SourceHypothesis,
		Capabilities: []EvidenceCapability{CapDedup, CapQueryApplied, CapExpiry}, DedupHorizonMS: UnboundedHorizonMS, ExpiryMS: 5000, ClockSkewMS: 200}
	for _, r := range []int64{0, 1000, 3600000} { // not declared, finite
		c := base
		c.RecordRetentionMS = r
		if err := c.Validate(); err != nil {
			t.Fatal(err)
		}
		if c.ExpiryRevealsStopped() || c.BackwardEvidence() || c.Level() != K2 {
			t.Fatalf("D_h(h=inf) + Q_A + T with retention %d must not reveal STOPPED: level %s", r, c.Level())
		}
		if a := c.Admit(OutcomeNotFound, ChannelQuery, 1<<40); a.Admissible {
			t.Fatalf("retention %d: NOT_FOUND after the expiry admitted as %s", r, a.As)
		}
	}
	keep := base
	keep.RecordRetentionMS = UnboundedRetentionMS
	if !keep.ExpiryRevealsStopped() || keep.Level() != K3 {
		t.Fatal("with r = infinity the contract has ER (K3)")
	}
	if a := keep.Admit(OutcomeNotFound, ChannelQuery, 5200); !a.Admissible || a.As != OutcomeStopped {
		t.Fatalf("r = infinity: NOT_FOUND after the expiry is STOPPED: %+v", a)
	}
	// r = infinity alone, without T, is no backward evidence; X keeps ER without a retention declaration
	noT := keep
	noT.Capabilities = []EvidenceCapability{CapDedup, CapQueryApplied}
	noT.ExpiryMS = 0
	if noT.ExpiryRevealsStopped() || noT.Level() != K2 {
		t.Fatal("retention without owner expiry reveals nothing")
	}
	x := EvidenceContract{SchemaVersion: 1, ID: "x-t", Source: SourceHypothesis, Capabilities: []EvidenceCapability{CapDedup, CapAtomic, CapExpiry},
		DedupHorizonMS: UnboundedHorizonMS, ExpiryMS: 5000}
	if !x.ExpiryRevealsStopped() {
		t.Fatal("T and X keep ER")
	}
	// the cutoff firmware's shape: unbounded suppression, finite records (a 32-entry ring); with a hypothetical T it gets no ER
	fw := EvidenceContract{SchemaVersion: 1, ID: "fw-t", Source: SourceHypothesis, Capabilities: []EvidenceCapability{CapDedup, CapQueryApplied, CapFence, CapExpiry},
		DedupHorizonMS: UnboundedHorizonMS, ExpiryMS: 5000}
	if fw.ExpiryRevealsStopped() {
		t.Fatal("unbounded suppression with finite retention has no ER")
	}
}

func TestMeasuredNeverUnboundedRetention084(t *testing.T) {
	m := EvidenceContract{SchemaVersion: 1, ID: "m", Source: SourceMeasured, Capabilities: []EvidenceCapability{CapDedup, CapQueryApplied, CapExpiry},
		DedupHorizonMS: 600000, ExpiryMS: 5000, RecordRetentionMS: UnboundedRetentionMS,
		Provenance: map[string]string{"D_h": "P5", "Q_A": "P4", "T": "P9", "T_expiry": "P9"}}
	err := m.Validate()
	if err == nil || !strings.Contains(err.Error(), "unbounded record retention") {
		t.Fatalf("a measured contract must not declare unbounded retention: %v", err)
	}
	m.RecordRetentionMS = 0
	if err := m.Validate(); err != nil {
		t.Fatal(err)
	}
	if m.ExpiryRevealsStopped() {
		t.Fatal("a measured contract never has the query branch of ER")
	}
	bad := m
	bad.RecordRetentionMS = -2
	if bad.Validate() == nil {
		t.Fatal("retention below -1 is invalid")
	}
}

func TestRetentionInIncludesAndJSON084(t *testing.T) {
	adv := EvidenceContract{SchemaVersion: 1, ID: "adv", Source: SourceRuntimeVerified, Capabilities: []EvidenceCapability{CapDedup, CapQueryApplied, CapExpiry},
		DedupHorizonMS: UnboundedHorizonMS, ExpiryMS: 30000, RecordRetentionMS: UnboundedRetentionMS}
	file := adv
	file.RecordRetentionMS = 0
	if !adv.Includes(file) {
		t.Fatal("a file without a retention declaration is included (as before 084)")
	}
	if !adv.Includes(adv) {
		t.Fatal("reflexive")
	}
	if file.Includes(adv) {
		t.Fatal("an owner that declares no retention does not include one that declares r = infinity")
	}
	fin := adv
	fin.RecordRetentionMS = 1000
	if fin.Includes(adv) || !adv.Includes(fin) {
		t.Fatal("finite retention is below r = infinity")
	}
	b, _ := json.Marshal(file)
	if strings.Contains(string(b), "record_retention_ms") {
		t.Fatal("an undeclared retention is omitted: contracts of earlier releases serialize as before")
	}
}

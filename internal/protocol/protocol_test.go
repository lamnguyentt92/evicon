package protocol

import "testing"

func TestCommandRejectsZeroSequence(t *testing.T) {
	r := CommandRequest{SchemaVersion: 1, DomainID: "d", Generation: 1, CommandID: "c", Operation: "ADD", Value: []int64{1}, Epoch: 1, Sequence: 0}
	if err := r.ValidateAndFillHash(1); err == nil {
		t.Fatal("expected zero sequence rejection")
	}
}

func TestReceiptValidatorIsStrictAndShared(t *testing.T) {
	good := Receipt{SchemaVersion: 1, DomainID: "d", Generation: 1, CommandID: "c", IntentHash: "h", Outcome: "STOPPED", OwnerVersion: 1, ReceiptVersion: 1, State: []int64{0}}
	if err := ValidateReceipt(good, "d", 1, "c", "h", 1); err != nil {
		t.Fatal(err)
	}
	bad := good
	bad.SchemaVersion = 99
	if err := ValidateReceipt(bad, "d", 1, "c", "h", 1); err == nil {
		t.Fatal("expected schema rejection")
	}
	bad = good
	bad.ReceiptVersion = 0
	if err := ValidateReceipt(bad, "d", 1, "c", "h", 1); err == nil {
		t.Fatal("expected terminal version rejection")
	}
	bad = good
	bad.State = nil
	if err := ValidateReceipt(bad, "d", 1, "c", "h", 1); err == nil {
		t.Fatal("expected state dimension rejection")
	}
}

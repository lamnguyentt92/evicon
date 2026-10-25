package probe

import (
	"evicon/internal/journal"
	"testing"
	"time"
)

func testCertificates(t *testing.T, d journal.Domain, cs []journal.CommandRecord, candV int64, depth int) []ContinuationCertificate {
	t.Helper()
	certs, err := EnumerateContinuationCertificates(d, cs, req("C", candV), depth, 256, 1000, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range certs {
		safe, err := VerifyContinuationCertificate(d, cs, req("C", candV), c, 256)
		if err != nil || !safe {
			t.Fatalf("invalid certificate: %+v %v", c, err)
		}
	}
	for _, c := range cs {
		if c.Status != "PENDING" {
			t.Fatalf("planner mutated tracked status: %+v", cs)
		}
	}
	return certs
}
func Test059SingleWitnessBoundToRevision(t *testing.T) {
	d := d1()
	cs := cmdsAB()
	cs[0].Value = []int64{3}
	cs[1].Value = []int64{3}
	certs := testCertificates(t, d, cs, 7, 2)
	if len(certs) == 0 {
		t.Fatal("expected witness")
	}
	for _, c := range certs {
		if len(c.Required) == 1 {
			t.Fatal("single terminal outcome should not make 4+3+3+7 safe")
		}
		old := d
		old.Revision++
		valid, err := VerifyContinuationCertificate(old, cs, req("C", 4), c, 256)
		if err != nil || valid {
			t.Fatalf("revision mismatch admitted cert: %v, %v", valid, err)
		}
	}
}
func Test059SingleStoppedWitness(t *testing.T) {
	d := d1()
	cs := cmdsAB()
	certs := testCertificates(t, d, cs, 4, 1)
	if len(certs) == 0 {
		t.Fatal("expected single sufficient STOPPED")
	}
	one := false
	for _, c := range certs {
		if len(c.Required) == 1 && c.Required[0].Outcome == "STOPPED" {
			one = true
		}
	}
	if !one {
		t.Fatal("no single STOPPED witness")
	}
}
func Test059BarrierNeverBecomesEvidence(t *testing.T) {
	d := d1()
	cs := cmdsAB()
	cs[0].Value = []int64{3}
	cs[1].Value = []int64{3}
	certs := testCertificates(t, d, cs, 4, 2)
	costs := DefaultCostModel()
	costs.ByCommand = map[string]int64{"A": 1200, "B": 1000}
	query := SuggestReconciliation(certs, costs, ReconciliationCapability{})
	if query.Kind != "QUERY" || !query.PredictionOnly {
		t.Fatalf("bad query action %+v", query)
	}
	barrier := SuggestReconciliation(certs, costs, ReconciliationCapability{VerifiedTerminalBarrier: true, BarrierExpectedCostUS: 100, BarrierLostWorkPenaltyUS: 10})
	if barrier.Kind != "BARRIER" || !barrier.PredictionOnly {
		t.Fatalf("bad barrier action %+v", barrier)
	}
	if ok, err := projectedSafe(d, cs, req("C", 4), 256); err != nil || ok {
		t.Fatalf("barrier prediction must not admit: %v %v", ok, err)
	}
	noCertificate := SuggestReconciliation(nil, costs, ReconciliationCapability{VerifiedTerminalBarrier: true, BarrierExpectedCostUS: 1})
	if noCertificate.Kind != "DEFER" {
		t.Fatal("never infer progress without a conditional witness")
	}
}
func Test059CapFailsClosed(t *testing.T) {
	_, err := EnumerateContinuationCertificates(d1(), cmdsAB(), req("C", 4), 2, 256, 1, time.Second)
	if err == nil {
		t.Fatal("cannot silently truncate witnesses")
	}
	_, err = EnumerateContinuationCertificates(d1(), cmdsAB(), req("C", 4), 2, 256, 1000, 0)
	if err == nil {
		t.Fatal("invalid budget must fail")
	}
}
func Test059TamperedCertificateRejected(t *testing.T) {
	d := d1()
	cs := cmdsAB()
	certs := testCertificates(t, d, cs, 4, 2)
	if len(certs) == 0 {
		t.Fatal("expected witnesses")
	}
	c := certs[0]
	c.Required[0].Outcome = "UNCERTAIN"
	if ok, err := VerifyContinuationCertificate(d, cs, req("C", 4), c, 256); err == nil && ok {
		t.Fatal("invalid evidence accepted")
	}
}

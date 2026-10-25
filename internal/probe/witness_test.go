package probe

import (
	"testing"
	"time"
)

func TestCertifiedWitnessRequiresTwoActualTerminalAssumptions(t *testing.T) {
	d := d1()
	d.ComponentMax = []int64{12}
	cs := cmdsAB()
	cs[0].Value = []int64{3}
	cs[1].Value = []int64{3}
	certs, err := ExtractWitnesses(d, cs, req("C", 7), 2, 16, 256, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if len(certs) != 1 || len(certs[0].Assumptions) != 2 {
		t.Fatalf("expected only joint terminal witness, got %+v", certs)
	}
	for _, a := range certs[0].Assumptions {
		if a.Outcome != "STOPPED" {
			t.Fatalf("unexpected witness %+v", certs)
		}
	}
	before, err := projectedSafe(d, cs, req("C", 7), 256)
	if err != nil || before {
		t.Fatalf("hypothetical witness must not make original candidate SAFE: %v %v", before, err)
	}
}
func TestWitnessFailsClosedOnResourceCaps(t *testing.T) {
	if _, err := ExtractWitnesses(d1(), cmdsAB(), req("C", 4), 2, 0, 256, time.Second); err == nil {
		t.Fatal("accepted missing certificate cap")
	}
}

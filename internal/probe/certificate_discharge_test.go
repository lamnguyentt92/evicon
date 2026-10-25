package probe

import (
	"testing"
	"time"
)

func Test060DischargeCurrentTerminalEvidenceOnly(t *testing.T) {
	d := d1()
	cs := cmdsAB()
	candidate := req("C", 4)
	certificates, err := EnumerateContinuationCertificates(d, cs, candidate, 2, 256, 1000, time.Second)
	if err != nil || len(certificates) == 0 {
		t.Fatalf("cert enumeration %v %d", err, len(certificates))
	}
	var chosen *ContinuationCertificate
	for i := range certificates {
		if len(certificates[i].Required) == 1 && certificates[i].Required[0].Outcome == "STOPPED" {
			chosen = &certificates[i]
			break
		}
	}
	if chosen == nil {
		t.Fatal("no single STOPPED certificate")
	}
	after := d
	after.Revision++
	post := cloneCommands(cs)
	for i := range post {
		if post[i].CommandID == chosen.Required[0].CommandID {
			post[i].Status = "STOPPED"
			post[i].Reason = "OWNER_TERMINAL_RECEIPT"
		}
	}
	a, e := AssessCertificateDischarge(d, cs, after, post, candidate, *chosen, 256)
	if e != nil || a.Status != "CONDITIONAL_MATCH_ONLY" || !a.ConditionalCertificateMatched || !a.CurrentGuardSafe || a.AdmissionAuthorized || a.TrustedSourceVerified {
		t.Fatalf("bad discharge: %+v %v", a, e)
	}
	t.Run("stale revision", func(t *testing.T) {
		x := d
		r, _ := AssessCertificateDischarge(d, cs, x, post, candidate, *chosen, 256)
		if r.Status != "DEFER" {
			t.Fatal(r)
		}
	})
	t.Run("missing receipt", func(t *testing.T) {
		r, _ := AssessCertificateDischarge(d, cs, after, cs, candidate, *chosen, 256)
		if r.Status != "DEFER" {
			t.Fatal(r)
		}
	})
	t.Run("interleaving", func(t *testing.T) {
		x := cloneCommands(post)
		for i := range x {
			if x[i].CommandID != chosen.Required[0].CommandID {
				x[i].Status = "APPLIED"
			}
		}
		r, _ := AssessCertificateDischarge(d, cs, after, x, candidate, *chosen, 256)
		if r.Status != "DEFER" {
			t.Fatal(r)
		}
	})
	t.Run("tampered payload", func(t *testing.T) {
		x := cloneCommands(post)
		x[0].Value = []int64{999}
		r, _ := AssessCertificateDischarge(d, cs, after, x, candidate, *chosen, 256)
		if r.Status != "DEFER" {
			t.Fatal(r)
		}
	})
	t.Run("new command", func(t *testing.T) {
		x := append(cloneCommands(post), post[0])
		x[len(x)-1].CommandID = "X"
		r, _ := AssessCertificateDischarge(d, cs, after, x, candidate, *chosen, 256)
		if r.Status != "DEFER" {
			t.Fatal(r)
		}
	})
	t.Run("changed contract", func(t *testing.T) {
		x := after
		x.ContractHash = "bad"
		r, _ := AssessCertificateDischarge(d, cs, x, post, candidate, *chosen, 256)
		if r.Status != "DEFER" {
			t.Fatal(r)
		}
	})
	t.Run("changed generation", func(t *testing.T) {
		x := after
		x.Generation++
		r, _ := AssessCertificateDischarge(d, cs, x, post, candidate, *chosen, 256)
		if r.Status != "DEFER" {
			t.Fatal(r)
		}
	})
	t.Run("no admission on barrier prediction", func(t *testing.T) {
		r, _ := AssessCertificateDischarge(d, cs, after, cs, candidate, *chosen, 256)
		if r.AdmissionAuthorized || r.TrustedSourceVerified {
			t.Fatal(r)
		}
	})
}

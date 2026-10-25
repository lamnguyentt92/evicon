package runtime

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"evicon/internal/device"
	"evicon/internal/trace"
)

// F40 (release 069, RQ4): an admission traces ADMISSION_RECEIVED (the request has been read;
// wait_ns = handler entry -> read, i.e. the authority's lock and the body) before
// ADMISSION_PREPARE and DECISION_RECORDED, so evaluation/overhead.py can split the admission
// latency into client and network, lock, policy evaluation and commit.
func TestF40AdmissionReceivedTraced(t *testing.T) {
	r := newDeviceRig(t, func(c *device.Config) {})
	a, _, _ := deviceAuthority(t, r)
	tracePath := filepath.Join(t.TempDir(), "trace.jsonl")
	a.cfg.Trace = trace.New(tracePath, "authority-test", "f40", "EVICON", 0)
	if code, out := invokeCandidate(t, a, dose(t, "A", 2, 1)); code != 202 || out.Decision != "ADMITTED" {
		t.Fatalf("A: %d %+v", code, out)
	}
	f, err := os.Open(tracePath)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var order []string
	var recv trace.Event
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		var e trace.Event
		if err := json.Unmarshal(sc.Bytes(), &e); err != nil {
			t.Fatal(err)
		}
		if e.CommandID != "A" {
			continue
		}
		switch e.Event {
		case "ADMISSION_RECEIVED":
			recv = e
			order = append(order, e.Event)
		case "ADMISSION_PREPARE", "DECISION_RECORDED":
			order = append(order, e.Event)
		}
	}
	if len(order) != 3 || order[0] != "ADMISSION_RECEIVED" || order[1] != "ADMISSION_PREPARE" || order[2] != "DECISION_RECORDED" {
		t.Fatalf("admission trace order: %v", order)
	}
	if recv.WaitNS <= 0 || recv.DomainID == "" {
		t.Fatalf("ADMISSION_RECEIVED must carry the domain and a positive wait_ns: %+v", recv)
	}
}

package plant

import (
	"bufio"
	"encoding/json"
	"evicon/internal/buildinfo"
	"net"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestApplyTruthReplayAndOnce(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "plant.jsonl")
	p, err := Open(path, true)
	if err != nil {
		t.Fatal(err)
	}
	p.SetLimit("d1", 5)
	for _, c := range []string{"a", "b", "a"} {
		if r := p.Apply(ApplyRequest{Device: "d1", Cmd: c, Value: 2}); !r.OK {
			t.Fatal(r.Error)
		}
	}
	if r := p.Apply(ApplyRequest{Device: "d1", Cmd: "b", Value: 2, Once: true}); !r.Duplicate || r.Total != 3 {
		t.Fatalf("once must suppress: %+v", r)
	}
	if tr := p.Truth("a", ""); tr.Effects != 2 {
		t.Fatalf("truth a = %d", tr.Effects)
	}
	s := p.Summary()
	if s.Total != 3 || s.Duplicates != 1 || !s.Devices[0].Violations || s.Devices[0].MaxLevel != 6 {
		t.Fatalf("summary %+v", s)
	}
	_ = p.Close()
	// torn trailing line after a crash is tolerated on replay
	f, _ := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0)
	_, _ = f.WriteString(`{"seq":4,"t_ns":1,"dev`)
	_ = f.Close()
	q, err := Open(path, false)
	if err != nil {
		t.Fatal(err)
	}
	defer q.Close()
	if q.Truth("a", "d1").Effects != 2 || q.Summary().Total != 3 {
		t.Fatalf("replay mismatch: %+v", q.Summary())
	}
	if r := q.Apply(ApplyRequest{Device: "d1", Cmd: "c", Value: 1}); r.Seq != 4 {
		t.Fatalf("seq must continue after replay, got %d", r.Seq)
	}
}

func TestHTTPAndLines(t *testing.T) {
	p, err := Open(filepath.Join(t.TempDir(), "p.jsonl"), false)
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close()
	srv := httptest.NewServer(p.Handler())
	defer srv.Close()
	resp, err := srv.Client().Post(srv.URL+"/v1/apply", "application/json", strings.NewReader(`{"op":"apply","device":"x","cmd":"k","value":1}`))
	if err != nil || resp.StatusCode != 200 {
		t.Fatalf("%v %v", resp, err)
	}
	sock := filepath.Join(t.TempDir(), "plant.sock")
	l, err := net.Listen("unix", sock)
	if err != nil {
		t.Skip("unix sockets unavailable:", err)
	}
	go p.ServeLines(l)
	defer l.Close()
	c, err := net.Dial("unix", sock)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	_, _ = c.Write([]byte(`{"op":"apply","device":"x","cmd":"k","value":1}` + "\n" + `{"op":"nope","device":"x","cmd":"k"}` + "\n"))
	rd := bufio.NewReader(c)
	var r1, r2 ApplyResult
	l1, _ := rd.ReadBytes('\n')
	l2, _ := rd.ReadBytes('\n')
	_ = json.Unmarshal(l1, &r1)
	_ = json.Unmarshal(l2, &r2)
	if !r1.OK || r1.Effects != 2 || r2.OK {
		t.Fatalf("%+v %+v", r1, r2)
	}
	tr, err := srv.Client().Get(srv.URL + "/v1/truth?cmd=k&device=x")
	if err != nil {
		t.Fatal(err)
	}
	var tt Truth
	_ = json.NewDecoder(tr.Body).Decode(&tt)
	if tt.Effects != 2 {
		t.Fatalf("truth %+v", tt)
	}
}

// /healthz identifies the program (release 064): a harness must be able to tell
// its own process from another program already bound to the port.
func TestHealthzIdentifiesTheProcess(t *testing.T) {
	p, err := Open(filepath.Join(t.TempDir(), "plant.jsonl"), false)
	if err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewServer(p.Handler())
	defer srv.Close()
	resp, err := srv.Client().Get(srv.URL + "/healthz")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var h map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&h); err != nil {
		t.Fatal(err)
	}
	if h["role"] != "plant" || h["release"] != buildinfo.Release || int(h["pid"].(float64)) != os.Getpid() {
		t.Fatalf("healthz = %v", h)
	}
}

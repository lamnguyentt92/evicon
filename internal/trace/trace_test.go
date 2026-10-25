package trace

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestLocalSequenceIncrements(t *testing.T) {
	p := filepath.Join(t.TempDir(), "trace.jsonl")
	w := New(p, "p", "r", "m", 1)
	if err := w.Write(Event{Event: "A"}); err != nil {
		t.Fatal(err)
	}
	if err := w.Write(Event{Event: "B"}); err != nil {
		t.Fatal(err)
	}
	f, err := os.Open(p)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	var seq []uint64
	for sc.Scan() {
		var e Event
		if err := json.Unmarshal(sc.Bytes(), &e); err != nil {
			t.Fatal(err)
		}
		seq = append(seq, e.LocalSequence)
	}
	if len(seq) != 2 || seq[0] != 0 || seq[1] != 1 {
		t.Fatalf("sequences=%v", seq)
	}
}

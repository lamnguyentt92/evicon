// evicon-contract: validate evidence contracts and print their lattice level.
//
//	evicon-contract validate experiments/contracts conformance/out/**/contract.json
//	evicon-contract levels   experiments/contracts
//
// Each argument is a directory (all *.json inside) or a single file. Output of
// "levels" is one JSON object per contract: id, source, level, capabilities,
// backward_evidence, expiry_reveals_stopped. It is the authoritative lattice
// used by EviCon-Probe's Python mirror (conformance/evicon_probe/rubric.level).
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"evicon/internal/contract"
)

func load(args []string) ([]contract.EvidenceContract, error) {
	var files []string
	for _, a := range args {
		st, err := os.Stat(a)
		if err != nil {
			return nil, err
		}
		if st.IsDir() {
			m, _ := filepath.Glob(filepath.Join(a, "*.json"))
			files = append(files, m...)
		} else {
			files = append(files, a)
		}
	}
	sort.Strings(files)
	var out []contract.EvidenceContract
	for _, f := range files {
		raw, err := os.ReadFile(f)
		if err != nil {
			return nil, err
		}
		var c contract.EvidenceContract
		dec := json.NewDecoder(strings.NewReader(string(raw)))
		dec.DisallowUnknownFields()
		if err := dec.Decode(&c); err != nil {
			return nil, fmt.Errorf("%s: %w", f, err)
		}
		if c.SchemaVersion != 1 {
			return nil, fmt.Errorf("%s: schema_version must be 1", f)
		}
		if err := c.Validate(); err != nil {
			return nil, fmt.Errorf("%s: %w", f, err)
		}
		out = append(out, c)
	}
	return out, nil
}

func main() {
	if len(os.Args) < 3 || (os.Args[1] != "validate" && os.Args[1] != "levels") {
		fmt.Fprintln(os.Stderr, "usage: evicon-contract validate|levels <dir|file>...")
		os.Exit(2)
	}
	cs, err := load(os.Args[2:])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if os.Args[1] == "validate" {
		fmt.Printf("%d contract(s) valid\n", len(cs))
		return
	}
	enc := json.NewEncoder(os.Stdout)
	for _, c := range cs {
		_ = enc.Encode(map[string]any{"id": c.ID, "source": c.Source, "level": c.Level(), "capabilities": c.Capabilities,
			"backward_evidence": c.BackwardEvidence(), "expiry_reveals_stopped": c.ExpiryRevealsStopped()})
	}
}

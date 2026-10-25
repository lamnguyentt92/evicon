package sim

import (
	"fmt"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"

	"evicon/internal/adapter"
	"evicon/internal/contract"
)

// ResolveContract maps a config contract name to an evidence contract.
// Runtime profile aliases (E0..E3, E2_STOP) are derived from the adapter
// premises exactly as the runtime does; other names come from the catalog.
//
// A "+F" suffix (e.g. "sim-k2+F") is the contract-upgrade experiment (review
// RV07): the base owner extended with an owner-certified fence. Every policy
// of the cell then runs on the upgraded contract; rows keep the suffix.
func ResolveContract(name, catalogDir string) (contract.EvidenceContract, error) {
	if base, ok := strings.CutSuffix(name, "+F"); ok {
		c, err := ResolveContract(base, catalogDir)
		if err != nil {
			return c, err
		}
		if c.Has(contract.CapFence) {
			return c, fmt.Errorf("contract %s already has F", base)
		}
		return c.Extend(name, contract.CapFence), nil
	}
	if base, ok := strings.CutSuffix(name, "+QS"); ok {
		// release 072: the base owner extended with a per-identity stop (experiment S)
		c, err := ResolveContract(base, catalogDir)
		if err != nil {
			return c, err
		}
		if c.Has(contract.CapStop) {
			return c, fmt.Errorf("contract %s already has Q_S", base)
		}
		return c.Extend(name, contract.CapStop), nil
	}
	switch name {
	case "E0", "E1", "E2", "E3", "E2_STOP":
		c, _, err := adapter.RuntimeEvidenceContract(name)
		return c, err
	}
	cs, err := contract.LoadEvidenceContracts(catalogDir)
	if err != nil {
		return contract.EvidenceContract{}, err
	}
	for _, c := range cs {
		if c.ID == name {
			return c, nil
		}
	}
	return contract.EvidenceContract{}, fmt.Errorf("contract %q not found in %s", name, catalogDir)
}

// Run executes every (episode, policy) of one config. Episodes are independent
// and processed in parallel; results are sorted deterministically.
func Run(cfg Config, catalogDir string, workers int) ([]Row, error) {
	base, err := ResolveContract(cfg.Owner.Contract, catalogDir)
	if err != nil {
		return nil, err
	}
	if workers <= 0 {
		workers = runtime.NumCPU()
	}
	type job struct{ ep int }
	jobs := make(chan job)
	var mu sync.Mutex
	var rows []Row
	var firstErr error
	var wg sync.WaitGroup
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := range jobs {
				in := buildEpisode(cfg, j.ep)
				local := make([]Row, 0, len(cfg.Policies))
				for _, p := range cfg.Policies {
					r, err := runEpisode(cfg, base, p, j.ep, in)
					if err != nil {
						mu.Lock()
						if firstErr == nil {
							firstErr = err
						}
						mu.Unlock()
						continue
					}
					local = append(local, r)
				}
				mu.Lock()
				rows = append(rows, local...)
				mu.Unlock()
			}
		}()
	}
	for ep := 0; ep < cfg.Episodes; ep++ {
		jobs <- job{ep}
	}
	close(jobs)
	wg.Wait()
	if firstErr != nil {
		return nil, firstErr
	}
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].Episode != rows[j].Episode {
			return rows[i].Episode < rows[j].Episode
		}
		return rows[i].Policy < rows[j].Policy
	})
	return rows, nil
}

// DefaultCatalogDir resolves experiments/contracts relative to root.
func DefaultCatalogDir(root string) string {
	return filepath.Join(root, "experiments", "contracts")
}

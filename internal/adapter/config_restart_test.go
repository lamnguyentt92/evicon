package adapter

import (
	"path/filepath"
	"strings"
	"testing"

	"evicon/internal/domain"
)

func TestOwnerRestartRejectsConfigurationConflictAndAllowsStateProgress(t *testing.T) {
	for _, profile := range []string{"E2", "E3"} {
		t.Run(profile, func(t *testing.T) {
			dir := t.TempDir()
			bound := domain.HardBound{ComponentMin: []int64{0}, ComponentMax: []int64{12}}
			open := func(b domain.HardBound, initial []int64) (func() error, error) {
				if profile == "E3" {
					o, err := OpenE3(E3Config{DBPath: filepath.Join(dir, "owner.db"), DomainID: "d", Generation: 1, InstalledEpoch: 1, InitialState: initial, Bound: b})
					if err != nil {
						return nil, err
					}
					return o.Close, nil
				}
				o, err := OpenE12(E12Config{Profile: profile, ClaimDBPath: filepath.Join(dir, "claims.db"), EffectsDBPath: filepath.Join(dir, "owner.db"), DomainID: "d", Generation: 1, InstalledEpoch: 1, InitialState: initial, Bound: b})
				if err != nil {
					return nil, err
				}
				return o.Close, nil
			}
			closeOwner, err := open(bound, []int64{0})
			if err != nil {
				t.Fatal(err)
			}
			closeOwner()

			// Initial state is bootstrap metadata, not a restart equality check;
			// the durable state may legitimately have progressed.
			closeOwner, err = open(bound, []int64{99})
			if err != nil {
				t.Fatalf("same durable contract should reopen regardless of bootstrap initial state: %v", err)
			}
			closeOwner()

			changed := domain.HardBound{ComponentMin: []int64{0}, ComponentMax: []int64{5}}
			if closeOwner, err = open(changed, []int64{0}); err == nil {
				closeOwner()
				t.Fatal("changed component bound was accepted")
			} else if !strings.Contains(err.Error(), "CONFIGURATION_CONFLICT") {
				t.Fatalf("unexpected error: %v", err)
			}

			sum := int64(10)
			changed = bound
			changed.SumMax = &sum
			if closeOwner, err = open(changed, []int64{0}); err == nil {
				closeOwner()
				t.Fatal("changed sum bound was accepted")
			}
		})
	}
}

package analyzer

import (
	"evicon/internal/belief"
	"evicon/internal/domain"
	"evicon/internal/transition"
)

func additiveCanApply(cfg belief.Configuration, cmd domain.Command, generation uint64) bool {
	return cmd.Operation == domain.OpAdd && transition.CanApply(cfg, cmd, generation)
}

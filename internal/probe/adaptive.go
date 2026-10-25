package probe

import (
	"errors"
	"fmt"
	"math"
	"sort"
	"time"

	"evicon/internal/analyzer"
	"evicon/internal/domain"
	"evicon/internal/evidence"
	"evicon/internal/journal"
	"evicon/internal/protocol"
)

// AdaptiveChoice is a selection recommendation, NEVER admission evidence.
// SuccessProb is a model estimate for obtaining SAFE *after valid evidence*.
type AdaptiveChoice struct {
	CommandID           string
	SuccessProb         float64
	ExpectedQueryCostUS float64
	Utility             float64
	ModelHash           string
	WitnessOutcomes     []string // hypothetical witness only, never persisted
}

type AdaptivePlanner struct {
	Model             OutcomeModel
	Costs             CostModel
	MaxConfigurations int
}

func projectedSafe(d journal.Domain, cs []journal.CommandRecord, req protocol.CommandRequest, maxCfg int) (bool, error) {
	b, err := evidence.BuildBelief(d, cs, maxCfg)
	if errors.Is(err, evidence.ErrModelInconsistency) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	r, err := analyzer.RelationalGuard(b, req.DomainCommand(), domain.HardBound{ComponentMin: d.ComponentMin, ComponentMax: d.ComponentMax, SumMax: d.SumMax})
	if err != nil {
		return false, err
	}
	return r.Verdict == analyzer.Safe, nil
}

func assumedOutcome(cs []journal.CommandRecord, id, out string) []journal.CommandRecord {
	r := cloneCommands(cs)
	for i := range r {
		if r[i].CommandID == id {
			r[i].Status = out
			break
		}
	}
	return r
}

// M4P uses exactly the same frozen probability model as M5V2. It strengthens
// M4 by weighting each hypothetical STOPPED-relief with its estimated chance.
// This intentionally remains a one-step greedy baseline.
func SelectM4P(d journal.Domain, cs []journal.CommandRecord, req protocol.CommandRequest, costs CostModel, model OutcomeModel, excluded map[string]bool, maxCfg int) ([]journal.CommandRecord, error) {
	if model.Spec.Profile == "" {
		return nil, fmt.Errorf("G1 model required")
	}
	base, err := Deficit(d, cs, req, maxCfg)
	if err != nil {
		return nil, err
	}
	type ranked struct {
		c     journal.CommandRecord
		score float64
		cost  int64
	}
	all := []ranked{}
	for _, c := range Unresolved(cs, excluded) {
		p, ok := model.Prior(c)
		if !ok {
			continue
		}
		stopped, err := Deficit(d, assumedOutcome(cs, c.CommandID, "STOPPED"), req, maxCfg)
		if errors.Is(err, evidence.ErrModelInconsistency) {
			continue
		}
		if err != nil {
			return nil, err
		}
		delta := math.Max(0, base-stopped)
		score := p.Stopped * delta / float64(costs.CostUS(c.CommandID))
		all = append(all, ranked{c, score, costs.CostUS(c.CommandID)})
	}
	sort.Slice(all, func(i, j int) bool {
		if all[i].score == all[j].score {
			if all[i].cost == all[j].cost {
				return all[i].c.CommandID < all[j].c.CommandID
			}
			return all[i].cost < all[j].cost
		}
		return all[i].score > all[j].score
	})
	result := make([]journal.CommandRecord, len(all))
	for i, x := range all {
		result[i] = x.c
	}
	return result, nil
}

// Choose performs short-horizon adaptive decision-tree search. Its branches are
// hypothetical. Only the first *query ID* leaves this function; subsequent
// actions are replanned from validated, committed receipts by the runtime.
// DeadlineUS is a configured development proxy, NOT the HTTP request deadline.
// The global computeBudget bounds the search; calls into the guard cannot be
// preempted, and any overrun is discarded without admission.
func (p AdaptivePlanner) Choose(d journal.Domain, cmds []journal.CommandRecord, req protocol.CommandRequest, depth int, excluded map[string]bool, computeBudget time.Duration) (AdaptiveChoice, bool, error) {
	if depth < 1 || len(p.Model.Spec.Rows) == 0 {
		return AdaptiveChoice{}, false, fmt.Errorf("G1 planner requires depth/model")
	}
	if computeBudget <= 0 {
		return AdaptiveChoice{}, false, ErrPlannerBudget
	}
	started := time.Now()
	timeout := func() bool { return time.Since(started) > computeBudget }
	var visit func([]journal.CommandRecord, map[string]bool, int, int64) (float64, float64, string, error)
	visit = func(cs []journal.CommandRecord, seen map[string]bool, left int, remainingUS int64) (float64, float64, string, error) {
		if timeout() {
			return 0, 0, "", ErrPlannerBudget
		}
		safe, err := projectedSafe(d, cs, req, p.MaxConfigurations)
		if err != nil {
			return 0, 0, "", err
		}
		if timeout() {
			return 0, 0, "", ErrPlannerBudget
		}
		if safe {
			return 1, 0, "", nil
		}
		if left == 0 || remainingUS <= 0 {
			return 0, 0, "", nil
		}
		bestUtility := 0.0
		bestProb := 0.0
		bestCost := 0.0
		bestID := ""
		for _, c := range Unresolved(cs, seen) {
			prior, ok := p.Model.Prior(c)
			if !ok || prior.LatencyUS > remainingUS {
				continue
			}
			queryCost := float64(p.Costs.CostUS(c.CommandID))
			choices := []struct {
				name string
				prob float64
			}{{"STOPPED", prior.Stopped}, {"APPLIED", prior.Applied}, {"UNCERTAIN", prior.Uncertain}, {"TIMEOUT", prior.Timeout}}
			nextSeen := make(map[string]bool, len(seen)+1)
			for k, v := range seen {
				nextSeen[k] = v
			}
			nextSeen[c.CommandID] = true
			prob := 0.0
			futureCost := 0.0
			for _, o := range choices {
				if o.prob <= 0 {
					continue
				}
				child := cs
				if o.name == "STOPPED" || o.name == "APPLIED" {
					child = assumedOutcome(cs, c.CommandID, o.name)
				}
				cp, cc, _, err := visit(child, nextSeen, left-1, remainingUS-prior.LatencyUS)
				if err != nil {
					return 0, 0, "", err
				}
				prob += o.prob * cp
				futureCost += o.prob * cc
			}
			cost := queryCost + futureCost
			utility := prob - p.Model.Spec.CostPenaltyPerUS*cost
			if bestID == "" || utility > bestUtility+1e-12 || (math.Abs(utility-bestUtility) <= 1e-12 && (prob > bestProb+1e-12 || (math.Abs(prob-bestProb) <= 1e-12 && (cost < bestCost || (cost == bestCost && c.CommandID < bestID))))) {
				bestID = c.CommandID
				bestUtility = utility
				bestProb = prob
				bestCost = cost
			}
		}
		if bestUtility <= 0 {
			return 0, 0, "", nil
		}
		return bestProb, bestCost, bestID, nil
	}
	seen := make(map[string]bool)
	for k, v := range excluded {
		seen[k] = v
	}
	prob, cost, id, err := visit(cloneCommands(cmds), seen, depth, p.Model.Spec.DeadlineUS)
	if err != nil {
		return AdaptiveChoice{}, false, err
	}
	if timeout() {
		return AdaptiveChoice{}, false, ErrPlannerBudget
	}
	if id == "" {
		return AdaptiveChoice{}, false, nil
	}
	return AdaptiveChoice{CommandID: id, SuccessProb: prob, ExpectedQueryCostUS: cost, Utility: prob - p.Model.Spec.CostPenaltyPerUS*cost, ModelHash: p.Model.Hash}, true, nil
}

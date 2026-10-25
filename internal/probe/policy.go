package probe

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sort"
	"strings"
	"sync"
	"time"

	"evicon/internal/analyzer"
	"evicon/internal/domain"
	"evicon/internal/evidence"
	"evicon/internal/journal"
	"evicon/internal/protocol"
	"evicon/internal/transition"
)

type Method string

const (
	M0        Method = "M0"
	M0NoProbe Method = "M0-NO-PROBE"
	M1        Method = "M1"
	M2        Method = "M2"
	M3        Method = "M3"
	M4        Method = "M4"
	M5        Method = "M5"
	M4P       Method = "M4P"    // G1 experimental: probability-aware M4
	M5V2      Method = "M5V2"   // G1 experimental: bounded adaptive planner
	M5V2D1    Method = "M5V2D1" // G1 ablation: same model, depth forced to one
	// EVICON (release 061): evidence-contract policy. On BLOCK it computes
	// continuation certificates and resolves their members with the action the
	// evidence contract allows (query, per-identity stop, owner fence).
	EVICON Method = "EVICON"
	// RELEASE_ON_TIMEOUT (release 077): baseline of common client practice, UNSAFE BY DESIGN.
	// A command without terminal evidence ReleaseAfter after its creation is treated as failed:
	// the authority commits STOPPED without any owner evidence (it breaks rule R2 on purpose),
	// releases its reservation, and the client re-issues the intent. Only for the baseline
	// campaign of PREREG_review_077.md; never a runtime policy.
	ReleaseOnTimeout Method = "RELEASE_ON_TIMEOUT"
	// RELEASE_ON_READ (release 077): baseline of state-read reconciliation (a device shadow),
	// UNSAFE BY DESIGN. ReleaseAfter after its creation, a command without terminal evidence is
	// looked up at the owner: an APPLIED or STOPPED receipt is recorded; a command the owner does
	// not report (NOT_FOUND, or the owner cannot be reached, so its reported state does not show
	// the effect) is treated as failed and committed STOPPED without evidence of non-occurrence.
	// Only for the baseline campaigns of PREREG_review_077.md; never a runtime policy.
	ReleaseOnRead Method = "RELEASE_ON_READ"
)

// IsReleaseBaseline reports the two unsafe baselines of release 077, which commit STOPPED
// without owner evidence of non-occurrence.
func IsReleaseBaseline(m Method) bool { return m == ReleaseOnTimeout || m == ReleaseOnRead }

func ParseMethod(s string) (Method, error) {
	m := Method(strings.ToUpper(strings.TrimSpace(s)))
	switch m {
	case M0, M0NoProbe, M1, M2, M3, M4, M5, M4P, M5V2, M5V2D1, EVICON, ReleaseOnTimeout, ReleaseOnRead:
		return m, nil
	default:
		return "", fmt.Errorf("unsupported policy %q", s)
	}
}

type CostFile struct {
	SchemaVersion int              `json:"schema_version"`
	Profile       string           `json:"profile,omitempty"`
	Placement     string           `json:"placement,omitempty"`
	DefaultCostUS int64            `json:"default_cost_us"`
	CostsUS       map[string]int64 `json:"costs_us,omitempty"`
}

type CostModel struct {
	DefaultUS int64
	ByCommand map[string]int64
	Hash      string
	Source    string
}

func DefaultCostModel() CostModel {
	return CostModel{DefaultUS: 1000, ByCommand: map[string]int64{}, Hash: "builtin-default-1000us", Source: "builtin"}
}

func LoadCostModel(path string) (CostModel, error) {
	if strings.TrimSpace(path) == "" {
		return DefaultCostModel(), nil
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return CostModel{}, err
	}
	var f CostFile
	if err := json.Unmarshal(raw, &f); err != nil {
		return CostModel{}, err
	}
	if f.SchemaVersion != 1 && f.SchemaVersion != 2 {
		return CostModel{}, fmt.Errorf("cost file schema_version=%d, want 1 or 2", f.SchemaVersion)
	}
	if f.DefaultCostUS <= 0 {
		return CostModel{}, fmt.Errorf("default_cost_us must be >0")
	}
	for id, v := range f.CostsUS {
		if v <= 0 {
			return CostModel{}, fmt.Errorf("cost for %s must be >0", id)
		}
	}
	h := sha256.Sum256(raw)
	return CostModel{DefaultUS: f.DefaultCostUS, ByCommand: f.CostsUS, Hash: hex.EncodeToString(h[:]), Source: path}, nil
}

func (c CostModel) CostUS(commandID string) int64 {
	if v, ok := c.ByCommand[commandID]; ok && v > 0 {
		return v
	}
	if c.DefaultUS > 0 {
		return c.DefaultUS
	}
	return 1000
}

func Unresolved(cmds []journal.CommandRecord, excluded map[string]bool) []journal.CommandRecord {
	out := make([]journal.CommandRecord, 0)
	for _, c := range cmds {
		if c.Status == "APPLIED" || c.Status == "STOPPED" {
			continue
		}
		if excluded != nil && excluded[c.CommandID] {
			continue
		}
		out = append(out, c)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].CommandID < out[j].CommandID })
	return out
}

func obligationMagnitude(c journal.CommandRecord) int64 {
	var s int64
	for _, v := range c.Value {
		if v < 0 {
			v = -v
		}
		if n, ok := domain.AddChecked(s, v); ok {
			s = n
		} else {
			return int64(^uint64(0) >> 1)
		}
	}
	return s
}

// SelectSimple returns a deterministic ranking for M1-M4. M1 returns all
// unresolved commands in command-ID order; the caller may issue them in a
// bounded concurrent batch. M2-M4 return the same list ranked by their policy.
func SelectSimple(method Method, d journal.Domain, cmds []journal.CommandRecord, candidate protocol.CommandRequest, costs CostModel, excluded map[string]bool, maxCfg int) ([]journal.CommandRecord, error) {
	u := Unresolved(cmds, excluded)
	switch method {
	case M1:
		return u, nil
	case M2:
		sort.SliceStable(u, func(i, j int) bool {
			ai, aj := obligationMagnitude(u[i]), obligationMagnitude(u[j])
			if ai == aj {
				return u[i].CommandID < u[j].CommandID
			}
			return ai > aj
		})
		return u, nil
	case M3:
		sort.SliceStable(u, func(i, j int) bool {
			ci, cj := costs.CostUS(u[i].CommandID), costs.CostUS(u[j].CommandID)
			if ci == cj {
				return u[i].CommandID < u[j].CommandID
			}
			return ci < cj
		})
		return u, nil
	case M4:
		baseDef, err := Deficit(d, cmds, candidate, maxCfg)
		if err != nil {
			return nil, err
		}
		type scored struct {
			c      journal.CommandRecord
			score  float64
			relief float64
			cost   int64
		}
		ss := make([]scored, 0, len(u))
		for _, c := range u {
			hypot := cloneCommands(cmds)
			for i := range hypot {
				if hypot[i].CommandID == c.CommandID {
					hypot[i].Status = "STOPPED"
				}
			}
			after, err := Deficit(d, hypot, candidate, maxCfg)
			if errors.Is(err, evidence.ErrModelInconsistency) {
				continue
			}
			if err != nil {
				return nil, err
			}
			relief := baseDef - after
			if relief < 0 {
				relief = 0
			}
			norm := 0.0
			if baseDef > 0 {
				norm = relief / baseDef
			}
			cost := costs.CostUS(c.CommandID)
			ss = append(ss, scored{c: c, score: norm / float64(cost), relief: relief, cost: cost})
		}
		sort.SliceStable(ss, func(i, j int) bool {
			if ss[i].score == ss[j].score {
				if ss[i].relief == ss[j].relief {
					if ss[i].cost == ss[j].cost {
						return ss[i].c.CommandID < ss[j].c.CommandID
					}
					return ss[i].cost < ss[j].cost
				}
				return ss[i].relief > ss[j].relief
			}
			return ss[i].score > ss[j].score
		})
		out := make([]journal.CommandRecord, len(ss))
		for i := range ss {
			out[i] = ss[i].c
		}
		return out, nil
	default:
		return nil, fmt.Errorf("SelectSimple unsupported for %s", method)
	}
}

func cloneCommands(in []journal.CommandRecord) []journal.CommandRecord {
	out := make([]journal.CommandRecord, len(in))
	copy(out, in)
	for i := range out {
		out[i].Value = append([]int64(nil), in[i].Value...)
		out[i].Reservation = append([]int64(nil), in[i].Reservation...)
	}
	return out
}

// Deficit measures the maximum supported-bound violation of the exact
// relational envelope. Zero means SAFE for the supported P0 operation/bound
// grammar. It is used only for the M4 ranking heuristic, never as evidence.
func Deficit(d journal.Domain, cmds []journal.CommandRecord, candidate protocol.CommandRequest, maxCfg int) (float64, error) {
	b, err := evidence.BuildBelief(d, cmds, maxCfg)
	if err != nil {
		return 0, err
	}
	bound := domain.HardBound{ComponentMin: d.ComponentMin, ComponentMax: d.ComponentMax, SumMax: d.SumMax}
	cand := candidate.DomainCommand()
	maxDef := float64(0)
	for _, cfg := range b.Configurations {
		bases := []domain.State{cfg.State.Clone()}
		addTotal := make(domain.State, b.Dimension)
		for id, st := range cfg.Commands {
			if st != domain.Pending {
				continue
			}
			cmd := b.Registry[id]
			if !transition.CanApply(cfg, cmd, b.Generation) {
				continue
			}
			if cmd.Operation == domain.OpSet {
				bases = append(bases, domain.State(cmd.Value).Clone())
			} else {
				for i, v := range cmd.Value {
					n, ok := domain.AddChecked(addTotal[i], v)
					if !ok {
						return 0, domain.ErrInt64Overflow
					}
					addTotal[i] = n
				}
			}
		}
		if transition.CanApply(cfg, cand, b.Generation) {
			if cand.Operation == domain.OpSet {
				bases = append(bases, domain.State(cand.Value).Clone())
			} else {
				for i, v := range cand.Value {
					n, ok := domain.AddChecked(addTotal[i], v)
					if !ok {
						return 0, domain.ErrInt64Overflow
					}
					addTotal[i] = n
				}
			}
		}
		for _, base := range bases {
			if v := stateDeficit(base, bound); v > maxDef {
				maxDef = v
			}
			worst := base.Clone()
			for i, v := range addTotal {
				n, ok := domain.AddChecked(worst[i], v)
				if !ok {
					return 0, domain.ErrInt64Overflow
				}
				worst[i] = n
			}
			if v := stateDeficit(worst, bound); v > maxDef {
				maxDef = v
			}
		}
	}
	return maxDef, nil
}

func stateDeficit(s domain.State, b domain.HardBound) float64 {
	var d float64
	if len(b.ComponentMin) > 0 {
		for i, v := range s {
			if v < b.ComponentMin[i] {
				d += float64(b.ComponentMin[i] - v)
			}
		}
	}
	if len(b.ComponentMax) > 0 {
		for i, v := range s {
			if v > b.ComponentMax[i] {
				d += float64(v - b.ComponentMax[i])
			}
		}
	}
	if b.SumMax != nil {
		if sum, ok := domain.SumChecked(s); ok && sum > *b.SumMax {
			d += float64(sum - *b.SumMax)
		}
	}
	return d
}

type Step struct {
	CommandID string `json:"command_id"`
	Outcome   string `json:"outcome"`
	CostUS    int64  `json:"cost_us"`
}
type Plan struct {
	Steps    []Step `json:"steps"`
	CostUS   int64  `json:"cost_us"`
	CacheHit bool   `json:"cache_hit"`
}

var ErrPlannerBudget = errors.New("planner computation budget exhausted")

// Planner implements the optimistic bounded lookahead from Eq. (6). APPLIED
// and STOPPED branches are hypothetical only; callers must query real evidence
// before changing durable state.
type Planner struct {
	Costs             CostModel
	MaxConfigurations int
	CacheEnabled      bool
	mu                sync.Mutex
	cache             map[string]Plan
}

func NewPlanner(costs CostModel, maxCfg int, cache bool) *Planner {
	return &Planner{Costs: costs, MaxConfigurations: maxCfg, CacheEnabled: cache, cache: map[string]Plan{}}
}

func (p *Planner) Plan(cacheKey string, d journal.Domain, cmds []journal.CommandRecord, candidate protocol.CommandRequest, depth int, excluded map[string]bool, computeBudget time.Duration) (Plan, bool, error) {
	if depth <= 0 {
		return Plan{}, false, nil
	}
	if computeBudget <= 0 {
		computeBudget = 5 * time.Millisecond
	}
	deadline := time.Now().Add(computeBudget)
	// Caller labels cannot establish equivalent beliefs: bind all semantic inputs.
	if p.CacheEnabled {
		raw, err := json.Marshal(struct {
			Caller                   string
			Domain                   journal.Domain
			Commands                 []journal.CommandRecord
			Candidate                protocol.CommandRequest
			Depth, MaxConfigurations int
			Excluded                 map[string]bool
			Costs                    CostModel
		}{cacheKey, d, cmds, candidate, depth, p.MaxConfigurations, excluded, p.Costs})
		if err != nil {
			return Plan{}, false, err
		}
		h := sha256.Sum256(raw)
		cacheKey = hex.EncodeToString(h[:])
		p.mu.Lock()
		v, ok := p.cache[cacheKey]
		p.mu.Unlock()
		if time.Now().After(deadline) {
			return Plan{}, false, ErrPlannerBudget
		}
		if ok {
			v.Steps = append([]Step(nil), v.Steps...)
			v.CacheHit = true
			if time.Now().After(deadline) {
				return Plan{}, false, ErrPlannerBudget
			}
			return v, len(v.Steps) > 0, nil
		}
	}
	type node struct {
		cmds  []journal.CommandRecord
		steps []Step
		cost  int64
	}
	best := Plan{CostUS: int64(^uint64(0) >> 1)}
	found := false
	var dfs func(node, int) error
	dfs = func(n node, left int) error {
		if time.Now().After(deadline) {
			return ErrPlannerBudget
		}
		b, err := evidence.BuildBelief(d, n.cmds, p.MaxConfigurations)
		if time.Now().After(deadline) {
			return ErrPlannerBudget
		}
		if errors.Is(err, evidence.ErrModelInconsistency) {
			return nil
		}
		if err != nil {
			return err
		}
		bound := domain.HardBound{ComponentMin: d.ComponentMin, ComponentMax: d.ComponentMax, SumMax: d.SumMax}
		ar, err := analyzer.RelationalGuard(b, candidate.DomainCommand(), bound)
		if time.Now().After(deadline) {
			return ErrPlannerBudget
		}
		if err != nil {
			return err
		}
		if ar.Verdict == analyzer.Safe {
			if len(n.steps) > 0 && (!found || n.cost < best.CostUS || (n.cost == best.CostUS && pathLess(n.steps, best.Steps))) {
				best = Plan{Steps: append([]Step(nil), n.steps...), CostUS: n.cost}
				found = true
			}
			return nil
		}
		if left == 0 {
			return nil
		}
		unresolved := Unresolved(n.cmds, excluded)
		// Search cheap evidence first so a low-cost feasible plan is found early
		// and can prune more expensive branches. This changes only traversal
		// order, not the objective or deterministic final tie-break.
		sort.SliceStable(unresolved, func(i, j int) bool {
			ci, cj := p.Costs.CostUS(unresolved[i].CommandID), p.Costs.CostUS(unresolved[j].CommandID)
			if ci == cj {
				return unresolved[i].CommandID < unresolved[j].CommandID
			}
			return ci < cj
		})
		used := map[string]bool{}
		for _, s := range n.steps {
			used[s.CommandID] = true
		}
		for _, c := range unresolved {
			if used[c.CommandID] {
				continue
			}
			cost := p.Costs.CostUS(c.CommandID)
			if found && n.cost+cost > best.CostUS {
				continue
			}
			// STOPPED is explored first because it is the branch that can remove an
			// outstanding obligation. APPLIED is still explored and the final
			// result is selected by cost + pathLess, so semantics are unchanged.
			for _, outcome := range []string{"STOPPED", "APPLIED"} {
				cc := cloneCommands(n.cmds)
				for i := range cc {
					if cc[i].CommandID == c.CommandID {
						cc[i].Status = outcome
					}
				}
				st := Step{CommandID: c.CommandID, Outcome: outcome, CostUS: cost}
				if err := dfs(node{cmds: cc, steps: append(append([]Step(nil), n.steps...), st), cost: n.cost + cost}, left-1); err != nil {
					return err
				}
			}
		}
		return nil
	}
	if err := dfs(node{cmds: cloneCommands(cmds)}, depth); err != nil {
		return Plan{}, false, err
	}
	if time.Now().After(deadline) {
		return Plan{}, false, ErrPlannerBudget
	}
	if !found {
		return Plan{}, false, nil
	}
	if p.CacheEnabled {
		p.mu.Lock()
		// Bound memory even when revisions/candidates never repeat.
		if len(p.cache) >= 256 {
			p.cache = map[string]Plan{}
		}
		stored := best
		stored.Steps = append([]Step(nil), best.Steps...)
		p.cache[cacheKey] = stored
		p.mu.Unlock()
	}
	if time.Now().After(deadline) {
		return Plan{}, false, ErrPlannerBudget
	}
	return best, true, nil
}

func pathLess(a, b []Step) bool {
	for i := 0; i < len(a) && i < len(b); i++ {
		if a[i].CommandID != b[i].CommandID {
			return a[i].CommandID < b[i].CommandID
		}
		if a[i].Outcome != b[i].Outcome {
			return a[i].Outcome < b[i].Outcome
		}
	}
	return len(a) < len(b)
}

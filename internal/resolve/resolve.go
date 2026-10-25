// Package resolve chooses how to resolve ambiguous commands that block a
// candidate: WAIT, QUERY, FORWARD_RETRY, STOP (per-identity, Q_S) or FENCE
// (owner-certified frontier, F). It is a pure decision function: it never
// creates evidence and never admits a command. Every chosen action must be in
// the contract's compiled ActionSet; the caller executes it and feeds the
// validated evidence back through the normal reducer/guard path.
//
// Theory (plan v2, section 4.3, T5): for a single ambiguous command with
// holding cost rate r and backward-resolution cost C, resolving at time C/r is
// 2-competitive against the offline optimum min(r*R, C), where R is the
// unknown time at which natural terminal evidence would have arrived.
package resolve

import (
	"errors"
	"fmt"
	"math"
	"sort"

	"evicon/internal/contract"
)

type ThresholdMode string

const (
	ModeSkiRental    ThresholdMode = "SKI_RENTAL"   // threshold = C / r (2-competitive)
	ModeDistribution ThresholdMode = "DISTRIBUTION" // argmin expected cost under empirical R
	ModeFixed        ThresholdMode = "FIXED"        // common practice: fixed timeout
	ModeImmediate    ThresholdMode = "IMMEDIATE"    // resolve as soon as blocked
	ModeNever        ThresholdMode = "NEVER"        // never use backward evidence
)

type Policy struct {
	Mode ThresholdMode `json:"mode"`
	// FixedMS is used by ModeFixed.
	FixedMS int64 `json:"fixed_ms,omitempty"`
	// SamplesMS are empirical natural-resolution delays R (ms since the command
	// became ambiguous; math.MaxInt64 encodes "never resolves naturally").
	SamplesMS []int64 `json:"samples_ms,omitempty"`
	// QueryFirst queries unqueried targets before any backward action.
	QueryFirst bool `json:"query_first"`
}

func (p Policy) Validate() error {
	switch p.Mode {
	case ModeSkiRental, ModeImmediate, ModeNever:
	case ModeFixed:
		if p.FixedMS < 0 {
			return errors.New("fixed threshold must be >= 0")
		}
	case ModeDistribution:
		if len(p.SamplesMS) == 0 {
			return errors.New("distribution mode requires samples")
		}
	default:
		return fmt.Errorf("unknown threshold mode %q", p.Mode)
	}
	return nil
}

type Costs struct {
	// HoldPerUnitPerSec: cost of keeping one unit of capacity reserved for one
	// second (opportunity cost of blocked work).
	HoldPerUnitPerSec float64 `json:"hold_per_unit_per_sec"`
	QueryCost         float64 `json:"query_cost"`
	// StopCost: fixed cost of one per-identity stop (RTT + expected re-issue
	// delay if the command was still in flight).
	StopCost float64 `json:"stop_cost"`
	// FenceCost: fixed cost of one fence (admission pause + expected
	// cancellation of in-flight work covered by the frontier).
	FenceCost float64 `json:"fence_cost"`
}

type Ambiguous struct {
	ID                 string `json:"id"`
	Sequence           uint64 `json:"sequence"`
	Magnitude          int64  `json:"magnitude"`
	AmbiguousSinceMS   int64  `json:"ambiguous_since_ms"`
	FirstSendMS        int64  `json:"first_send_ms"`
	LastAttemptMS      int64  `json:"last_attempt_ms"`
	IntentValidUntilMS int64  `json:"intent_valid_until_ms,omitempty"`
	Queried            bool   `json:"queried"`
	StopAttempted      bool   `json:"stop_attempted"`
	// InCertificate marks members of a minimal sufficient set: resolving them
	// to STOPPED would make the blocked candidate safe.
	InCertificate bool `json:"in_certificate"`
}

type Input struct {
	NowMS             int64                     `json:"now_ms"`
	Contract          contract.EvidenceContract `json:"contract"`
	Ambiguous         []Ambiguous               `json:"ambiguous"`
	Blocked           bool                      `json:"blocked"`
	QueryBudget       int                       `json:"query_budget"`
	MaxNetworkDelayMS int64                     `json:"max_network_delay_ms"`
	FenceInProgress   bool                      `json:"fence_in_progress"`
}

type Decision struct {
	Action      contract.Action `json:"action"`
	Targets     []string        `json:"targets,omitempty"`
	FenceCutoff uint64          `json:"fence_cutoff,omitempty"`
	ThresholdMS int64           `json:"threshold_ms"`
	AgeMS       int64           `json:"age_ms"`
	Reason      string          `json:"reason"`
}

const NeverMS = int64(math.MaxInt64)

// Threshold returns the resolution threshold (ms after the oldest target
// became ambiguous) for backward cost C and holding rate r (per second).
func Threshold(p Policy, holdRatePerSec, backwardCost float64) int64 {
	switch p.Mode {
	case ModeImmediate:
		return 0
	case ModeNever:
		return NeverMS
	case ModeFixed:
		return p.FixedMS
	case ModeDistribution:
		return distributionThreshold(p.SamplesMS, holdRatePerSec, backwardCost)
	default: // ski-rental
		if holdRatePerSec <= 0 {
			return NeverMS
		}
		t := backwardCost / holdRatePerSec * 1000.0
		if t >= float64(NeverMS) {
			return NeverMS
		}
		if t < 0 {
			return 0
		}
		return int64(math.Ceil(t))
	}
}

// ExpectedCost is the expected cost of resolving at threshold t when natural
// evidence arrives at R ~ samples: E[r*min(R,t) + C*1{R>t}].
func ExpectedCost(samples []int64, t int64, holdRatePerSec, backwardCost float64) float64 {
	if len(samples) == 0 {
		return math.Inf(1)
	}
	total := 0.0
	for _, R := range samples {
		total += Cost(t, R, holdRatePerSec, backwardCost)
	}
	return total / float64(len(samples))
}

// Cost is the realized cost of threshold t when natural evidence arrives at R.
func Cost(t, R int64, holdRatePerSec, backwardCost float64) float64 {
	if R == NeverMS {
		if t == NeverMS {
			return math.Inf(1)
		}
		return holdRatePerSec*float64(t)/1000.0 + backwardCost
	}
	if R <= t {
		return holdRatePerSec * float64(R) / 1000.0
	}
	if t == NeverMS {
		return math.Inf(1)
	}
	return holdRatePerSec*float64(t)/1000.0 + backwardCost
}

// OfflineOptimum is min(r*R, C): the clairvoyant either waits or resolves at 0.
func OfflineOptimum(R int64, holdRatePerSec, backwardCost float64) float64 {
	wait := math.Inf(1)
	if R != NeverMS {
		wait = holdRatePerSec * float64(R) / 1000.0
	}
	return math.Min(wait, backwardCost)
}

func distributionThreshold(samples []int64, r, c float64) int64 {
	cands := []int64{0}
	for _, s := range samples {
		if s != NeverMS && s >= 0 {
			cands = append(cands, s)
		}
	}
	cands = append(cands, NeverMS)
	sort.Slice(cands, func(i, j int) bool { return cands[i] < cands[j] })
	best, bestT := math.Inf(1), NeverMS
	for _, t := range cands {
		ec := ExpectedCost(samples, t, r, c)
		if ec < best-1e-12 {
			best, bestT = ec, t
		}
	}
	return bestT
}

// Decide selects the next resolution action for a blocked candidate.
func Decide(in Input, costs Costs, pol Policy) (Decision, error) {
	if err := pol.Validate(); err != nil {
		return Decision{}, err
	}
	if err := in.Contract.Validate(); err != nil {
		return Decision{}, err
	}
	if !in.Blocked || len(in.Ambiguous) == 0 {
		return Decision{Action: contract.ActionWait, Reason: "NOTHING_TO_RESOLVE"}, nil
	}
	targets := make([]Ambiguous, 0, len(in.Ambiguous))
	for _, a := range in.Ambiguous {
		if a.InCertificate {
			targets = append(targets, a)
		}
	}
	if len(targets) == 0 {
		// No sufficient set exists: even all-STOPPED would not admit the
		// candidate (true exhaustion). Evidence cannot help (T3).
		return Decision{Action: contract.ActionWait, Reason: "NO_SUFFICIENT_SET"}, nil
	}
	sort.Slice(targets, func(i, j int) bool {
		if targets[i].Sequence != targets[j].Sequence {
			return targets[i].Sequence < targets[j].Sequence
		}
		return targets[i].ID < targets[j].ID
	})
	c := in.Contract
	canQuery := c.Has(contract.CapQueryApplied) || c.Has(contract.CapAtomic)
	if pol.QueryFirst && canQuery && in.QueryBudget > 0 {
		q := []string{}
		for _, t := range targets {
			if !t.Queried {
				q = append(q, t.ID)
			}
		}
		if len(q) > in.QueryBudget {
			q = q[:in.QueryBudget]
		}
		if len(q) > 0 {
			return Decision{Action: contract.ActionQuery, Targets: q, Reason: "QUERY_UNQUERIED_TARGETS"}, nil
		}
	}

	oldest := targets[0].AmbiguousSinceMS
	var magnitude int64
	for _, t := range targets {
		if t.AmbiguousSinceMS < oldest {
			oldest = t.AmbiguousSinceMS
		}
		magnitude += t.Magnitude
	}
	age := in.NowMS - oldest
	if age < 0 {
		age = 0
	}
	rate := costs.HoldPerUnitPerSec * float64(magnitude)

	switch {
	case c.Has(contract.CapStop):
		pending := []string{}
		for _, t := range targets {
			if !t.StopAttempted {
				pending = append(pending, t.ID)
			}
		}
		thr := Threshold(pol, rate, costs.StopCost*float64(len(targets)))
		if len(pending) == 0 {
			return Decision{Action: contract.ActionWait, ThresholdMS: thr, AgeMS: age, Reason: "STOP_ALREADY_ISSUED"}, nil
		}
		if thr != NeverMS && age >= thr {
			return Decision{Action: contract.ActionStop, Targets: pending, ThresholdMS: thr, AgeMS: age, Reason: "STOP_THRESHOLD_REACHED"}, nil
		}
		return Decision{Action: contract.ActionWait, ThresholdMS: thr, AgeMS: age, Reason: "STOP_THRESHOLD_NOT_REACHED"}, nil
	case c.Has(contract.CapFence):
		thr := Threshold(pol, rate, costs.FenceCost)
		if in.FenceInProgress {
			return Decision{Action: contract.ActionWait, ThresholdMS: thr, AgeMS: age, Reason: "FENCE_IN_PROGRESS"}, nil
		}
		if thr != NeverMS && age >= thr {
			var cut uint64
			ids := make([]string, len(targets))
			for i, t := range targets {
				ids[i] = t.ID
				if t.Sequence > cut {
					cut = t.Sequence
				}
			}
			return Decision{Action: contract.ActionFence, Targets: ids, FenceCutoff: cut, ThresholdMS: thr, AgeMS: age, Reason: "FENCE_THRESHOLD_REACHED"}, nil
		}
		return Decision{Action: contract.ActionWait, ThresholdMS: thr, AgeMS: age, Reason: "FENCE_THRESHOLD_NOT_REACHED"}, nil
	case c.ExpiryRevealsStopped():
		// Owner expiry + truthful query: after tau+epsilon since the *send*,
		// NOT_FOUND becomes terminal STOPPED. Query the expired targets.
		thr := c.ExpiryMS + c.ClockSkewMS
		expired := []string{}
		for _, t := range targets {
			if !t.Queried && c.NoFutureEffect(in.NowMS-t.FirstSendMS) {
				expired = append(expired, t.ID)
			}
		}
		if len(expired) > 0 && in.QueryBudget > 0 {
			if len(expired) > in.QueryBudget {
				expired = expired[:in.QueryBudget]
			}
			return Decision{Action: contract.ActionQuery, Targets: expired, ThresholdMS: thr, AgeMS: age, Reason: "QUERY_AFTER_OWNER_EXPIRY"}, nil
		}
		return Decision{Action: contract.ActionWait, ThresholdMS: thr, AgeMS: age, Reason: "WAIT_FOR_OWNER_EXPIRY"}, nil
	default:
		return Decision{Action: contract.ActionEscalate, ThresholdMS: NeverMS, AgeMS: age, Reason: "NO_BACKWARD_EVIDENCE_T2"}, nil
	}
}

// RetryDecision decides the dispatch-side action for one command without a
// terminal receipt: forward retry with the same identity while T4 allows it,
// otherwise hand over to backward resolution (or wait).
func RetryDecision(c contract.EvidenceContract, t contract.CommandTiming, lastAttemptMS, retryIntervalMS int64) Decision {
	set := c.Compile(t)
	if t.TerminalKnown {
		if set.Allows(contract.ActionReissueNewID) {
			return Decision{Action: contract.ActionReissueNewID, Reason: "TERMINAL_STOPPED_REISSUE"}
		}
		return Decision{Action: contract.ActionWait, Reason: "TERMINAL"}
	}
	if set.Allows(contract.ActionForwardRetry) {
		if t.NowMS-lastAttemptMS >= retryIntervalMS {
			return Decision{Action: contract.ActionForwardRetry, Reason: "WITHIN_T4_HORIZON"}
		}
		return Decision{Action: contract.ActionWait, Reason: "RETRY_BACKOFF"}
	}
	switch {
	case set.Allows(contract.ActionStop):
		return Decision{Action: contract.ActionStop, Reason: "HORIZON_EXPIRED_BACKWARD_STOP"}
	case set.Allows(contract.ActionFence):
		return Decision{Action: contract.ActionFence, Reason: "HORIZON_EXPIRED_BACKWARD_FENCE"}
	case c.ExpiryRevealsStopped():
		if c.NoFutureEffect(t.NowMS - t.FirstSendMS) {
			return Decision{Action: contract.ActionQuery, Reason: "HORIZON_EXPIRED_QUERY_AFTER_OWNER_EXPIRY"}
		}
		return Decision{Action: contract.ActionWait, Reason: "HORIZON_EXPIRED_WAIT_OWNER_EXPIRY"}
	default:
		return Decision{Action: contract.ActionEscalate, Reason: "HORIZON_EXPIRED_NO_BACKWARD_EVIDENCE"}
	}
}

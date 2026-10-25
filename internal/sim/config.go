// Package sim is the EviCon v2 discrete-event simulator (evicon-des).
//
// It reuses the runtime decision core unchanged: belief reconstruction
// (evidence.BuildBelief), admission (analyzer.RelationalGuard), the M1-M4
// query selectors (probe.SelectSimple), continuation certificates
// (probe.EnumerateContinuationCertificates), the evidence-contract lattice
// (contract) and the resolution policy (resolve). Only the network, the
// effect owner, devices and the clock are simulated.
//
// Every number produced by this package is a SIMULATION result for regime
// exploration, power analysis and the D0 gate. It is never a measured result.
package sim

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"

	"evicon/internal/resolve"
)

type WorkloadSpec struct {
	Name         string  `json:"name"`
	Dimension    int     `json:"dimension"`
	Initial      []int64 `json:"initial"`
	ComponentMin []int64 `json:"component_min,omitempty"`
	ComponentMax []int64 `json:"component_max,omitempty"`
	SumMax       *int64  `json:"sum_max,omitempty"`
	ValueMin     []int64 `json:"value_min"`
	ValueMax     []int64 `json:"value_max"`
	SetFraction  float64 `json:"set_fraction,omitempty"`
	SetMin       []int64 `json:"set_min,omitempty"`
	SetMax       []int64 `json:"set_max,omitempty"`
	RatePerSec   float64 `json:"rate_per_sec"`
	// Poisson arrivals when true; fixed period (as in the 059 harness) otherwise.
	Poisson    bool  `json:"poisson"`
	DurationMS int64 `json:"duration_ms"`
	TailMS     int64 `json:"tail_ms"`
	// DeadlineMS: service deadline after arrival (useful work must apply by then).
	DeadlineMS int64 `json:"deadline_ms"`
	// ValidityMS: an effect later than arrival+validity is a LATE-EFFECT safety
	// violation (e.g. a dose delivered too late). 0 disables the check.
	ValidityMS int64 `json:"validity_ms,omitempty"`
}

type NetworkSpec struct {
	OneWayBaseMS     float64 `json:"one_way_base_ms"`
	OneWayJitterMS   float64 `json:"one_way_jitter_ms"`
	ReqLossProb      float64 `json:"req_loss_prob"`
	ReqLateProb      float64 `json:"req_late_prob"`
	ReqLateMeanMS    float64 `json:"req_late_mean_ms"`
	RespLossProb     float64 `json:"resp_loss_prob"`
	RespHideProb     float64 `json:"resp_hide_prob"`
	RespHideMS       int64   `json:"resp_hide_ms"`
	QueryRTTMS       float64 `json:"query_rtt_ms"`
	QueryRTTClassMS  []int64 `json:"query_rtt_class_ms,omitempty"`
	QueryLossProb    float64 `json:"query_loss_prob"`
	ControlRTTMS     float64 `json:"control_rtt_ms"`
	ControlLossProb  float64 `json:"control_loss_prob"`
	ControlTimeoutMS int64   `json:"control_timeout_ms"`
	// LateReplies: what happens to a command reply that arrives after the authority's dispatch
	// timeout for that send. "deliver" (default) models a reply channel that outlives the
	// request (a broker response topic): the late reply still resolves the ambiguity. "drop"
	// models synchronous RPC (HTTP with a client timeout, as evicon-authority -> evicon-device on
	// the testbed): the client has given up and closed the connection, so the reply is lost
	// (release 066, found by comparing experiment A on the testbed with its twin).
	LateReplies string `json:"late_replies,omitempty"`
}

type OwnerSpec struct {
	// Contract is an id from experiments/contracts or a profile alias.
	Contract           string  `json:"contract"`
	TerminalRejectProb float64 `json:"terminal_reject_prob"`
	EnforcesBound      bool    `json:"enforces_bound"`
	CrashMidProb       float64 `json:"crash_mid_prob"`
	CrashRecoveryMS    int64   `json:"crash_recovery_ms"`
	OnlineMeanMS       float64 `json:"online_mean_ms"`
	OfflineMeanMS      float64 `json:"offline_mean_ms"`
	// OfflineUniformMS [lo, hi] (release 068, exploratory twin v2): offline periods uniform in
	// [lo, hi] ms instead of exponential with OfflineMeanMS. The testbed's fault runner draws
	// offline windows uniformly (experiment A: 10-40 s); an exponential draw has the same mean but
	// many short and a few very long outages. Empty keeps the exponential model (frozen twin).
	OfflineUniformMS []float64 `json:"offline_uniform_ms,omitempty"`
	// Channel: "direct" (messages to an unavailable owner are lost) or "queue"
	// (a broker holds them FIFO and delivers on reconnect).
	Channel        string  `json:"channel"`
	BrokerExpiryMS int64   `json:"broker_expiry_ms,omitempty"`
	ProcessingMS   float64 `json:"processing_ms"`
	// OrphanReconcile (review RV06): when true, the owner can reconcile a
	// claim left by a crash between effect and receipt with its actuator state
	// and answer stop/fence/query with the true outcome. When false (default;
	// the Go evicon-device and the Zephyr firmware behave this way) such an
	// identity stays UNCERTAIN: a stop or fence blocks its future but cannot
	// certify its past.
	OrphanReconcile bool `json:"orphan_reconcile,omitempty"`
}

type AuthoritySpec struct {
	DispatchTimeoutMS   int64          `json:"dispatch_timeout_ms"`
	RetryIntervalMS     int64          `json:"retry_interval_ms"`
	MaxRetryMS          int64          `json:"max_retry_ms"`
	CancelAfterMS       int64          `json:"cancel_after_ms"`
	ReconcileIntervalMS int64          `json:"reconcile_interval_ms"`
	EscalateAfterMS     int64          `json:"escalate_after_ms"`
	QueryBudget         int            `json:"query_budget"`
	QueryConcurrency    int            `json:"query_concurrency"`
	MaxOutstanding      int            `json:"max_outstanding"`
	MaxConfigurations   int            `json:"max_configurations"`
	SingleShotDecision  bool           `json:"single_shot_decision"`
	TickMS              int64          `json:"tick_ms"`
	Resolve             resolve.Policy `json:"resolve"`
	Costs               resolve.Costs  `json:"costs"`
	CertificateDepth    int            `json:"certificate_depth"`
	ReissueAfterStopped bool           `json:"reissue_after_stopped"`
	// AssumedMaxDelayMS is the T4 delay bound delta_max the authority assumes
	// (0 = derive from the channel; -1 = unbounded). The simulated network may
	// violate it; such premise violations show up as duplicates/late effects.
	AssumedMaxDelayMS int64 `json:"assumed_max_delay_ms"`
	// ValidityGuardMS: EviCon stops/fences a pending command this long before
	// its validity window closes (safety use of backward evidence).
	ValidityGuardMS int64 `json:"validity_guard_ms"`
	// ManualResolveAfterMS (>0): an escalated ambiguous command is resolved by
	// a human operator after this delay (models "ledger + operations"). The
	// operator learns the truth and cancels the command at the owner.
	ManualResolveAfterMS int64 `json:"manual_resolve_after_ms"`
	// FutileRetryReplies: after this many UNCERTAIN replies to same-identity
	// retries (claim without receipt), EviCon treats forward retry as futile and
	// resolves the command backward (stop/fence) if the contract allows.
	FutileRetryReplies int `json:"futile_retry_replies"`
	// Same-capability resolution baselines (review RV07): RES_FIXED waits
	// FixedThresholdMS after ambiguity onset; RES_PERIODIC resolves every
	// ambiguous command every PeriodicMS, independent of blocked candidates.
	// FenceUncertain: what a fence does when the owner's barrier receipt reports an identity
	// below the cutoff as UNCERTAIN (a crash orphan). "partial" (default): the certified
	// identities become terminal and the orphan stays ambiguous. "abort": the whole barrier is
	// aborted and nothing is resolved, which is what evicon-authority does (release 062
	// AbortBarrier; release 066 makes the twin match the runtime).
	FenceUncertain   string          `json:"fence_uncertain,omitempty"`
	FixedThresholdMS int64           `json:"fixed_threshold_ms,omitempty"`
	PeriodicMS       int64           `json:"periodic_ms,omitempty"`
	Extra            json.RawMessage `json:"extra,omitempty"`
}

type Config struct {
	SchemaVersion int           `json:"schema_version"`
	Regime        string        `json:"regime"`
	Seed          int64         `json:"seed"`
	Episodes      int           `json:"episodes"`
	Policies      []string      `json:"policies"`
	Workload      WorkloadSpec  `json:"workload"`
	Network       NetworkSpec   `json:"network"`
	Owner         OwnerSpec     `json:"owner"`
	Authority     AuthoritySpec `json:"authority"`
	// Sweep (optional): list of named overrides applied on top of this config.
	Sweep []Override `json:"sweep,omitempty"`
	Notes string     `json:"notes,omitempty"`
}

// Override is a shallow JSON merge applied to a copy of the base config.
type Override struct {
	Name  string          `json:"name"`
	Patch json.RawMessage `json:"patch"`
}

const (
	PolicyOracle       = "ORACLE"
	PolicyB0Naive      = "B0_NAIVE_NEW_ID"
	PolicyB1Durable    = "B1_DURABLE_RETRY"
	PolicyB2Ledger     = "B2_LEDGER"
	PolicyB3Cancel     = "B3_TIMEOUT_CANCEL"
	PolicyB4Unsafe     = "B4_UNSAFE_FRONTIER" // simplified negative control, NOT an Atomix implementation
	PolicyB5SelectorM4 = "B5_SELECTOR_M4"     // ledger + M4 query selector (not M5/M5V2)
	PolicyEvicon       = "EVICON"
	// Same contract, same APIs, same retry guard and reissue rule as EVICON;
	// only when/what to resolve differs (review RV07, experiment B).
	PolicyResImmediate        = "RES_IMMEDIATE"   // stop/fence as soon as a certificate member is ambiguous
	PolicyResQueryFirst       = "RES_QUERY_FIRST" // query first, then stop/fence immediately
	PolicyResFixed            = "RES_FIXED"       // query first, stop/fence after a fixed wait
	PolicyResPeriodic         = "RES_PERIODIC"    // every periodic_ms: stop/fence all aged ambiguous commands
	PolicyReplayM0            = "M0"
	PolicyReplayM1            = "M1"
	PolicyReplayM2            = "M2"
	PolicyReplayM3            = "M3"
	PolicyReplayM4            = "M4"
	defaultTickMS       int64 = 50
)

var knownPolicies = map[string]bool{
	PolicyOracle: true, PolicyB0Naive: true, PolicyB1Durable: true, PolicyB2Ledger: true, PolicyB3Cancel: true,
	PolicyB4Unsafe: true, PolicyB5SelectorM4: true, PolicyEvicon: true,
	PolicyResImmediate: true, PolicyResQueryFirst: true, PolicyResFixed: true, PolicyResPeriodic: true,
	PolicyReplayM0: true, PolicyReplayM1: true, PolicyReplayM2: true, PolicyReplayM3: true, PolicyReplayM4: true,
}

// ResolverPolicies are the policies that use backward evidence through package
// resolve under the cell's contract.
var ResolverPolicies = []string{PolicyEvicon, PolicyResImmediate, PolicyResQueryFirst, PolicyResFixed, PolicyResPeriodic}

func IsResolver(p string) bool {
	for _, x := range ResolverPolicies {
		if p == x {
			return true
		}
	}
	return false
}

func IsUnsafeByDesign(p string) bool {
	return p == PolicyB0Naive || p == PolicyB3Cancel || p == PolicyB4Unsafe || p == PolicyB1Durable
}

func (c *Config) applyDefaults() {
	a := &c.Authority
	if a.DispatchTimeoutMS <= 0 {
		a.DispatchTimeoutMS = 1000
	}
	if a.RetryIntervalMS <= 0 {
		a.RetryIntervalMS = 1000
	}
	if a.MaxRetryMS <= 0 {
		a.MaxRetryMS = 600000
	}
	if a.CancelAfterMS <= 0 {
		a.CancelAfterMS = a.DispatchTimeoutMS
	}
	if a.ReconcileIntervalMS <= 0 {
		a.ReconcileIntervalMS = 2000
	}
	if a.EscalateAfterMS <= 0 {
		a.EscalateAfterMS = 60000
	}
	if a.QueryConcurrency <= 0 {
		a.QueryConcurrency = 2
	}
	if a.MaxOutstanding <= 0 {
		a.MaxOutstanding = 4
	}
	if a.MaxConfigurations <= 0 {
		a.MaxConfigurations = 256
	}
	if a.TickMS <= 0 {
		a.TickMS = defaultTickMS
	}
	if a.FutileRetryReplies <= 0 {
		a.FutileRetryReplies = 2
	}
	if a.ValidityGuardMS <= 0 {
		a.ValidityGuardMS = 2*int64(c.Network.ControlRTTMS) + 1000
	}
	if a.CertificateDepth <= 0 {
		a.CertificateDepth = 3
	}
	if a.FixedThresholdMS <= 0 {
		a.FixedThresholdMS = 5000
	}
	if a.PeriodicMS <= 0 {
		a.PeriodicMS = 10000
	}
	if a.Resolve.Mode == "" {
		a.Resolve.Mode = resolve.ModeSkiRental
	}
	if a.Costs.HoldPerUnitPerSec == 0 {
		a.Costs.HoldPerUnitPerSec = 1
	}
	if a.Costs.StopCost == 0 {
		a.Costs.StopCost = 0.5
	}
	if a.Costs.FenceCost == 0 {
		a.Costs.FenceCost = 2
	}
	n := &c.Network
	if n.QueryRTTMS <= 0 && len(n.QueryRTTClassMS) == 0 {
		n.QueryRTTMS = 2 * (n.OneWayBaseMS + 1)
	}
	if n.ControlRTTMS <= 0 {
		n.ControlRTTMS = n.QueryRTTMS
		if n.ControlRTTMS <= 0 {
			n.ControlRTTMS = 2
		}
	}
	if n.ControlTimeoutMS <= 0 {
		n.ControlTimeoutMS = a.DispatchTimeoutMS
	}
	if c.Owner.Channel == "" {
		c.Owner.Channel = "direct"
	}
	if c.Owner.CrashRecoveryMS <= 0 {
		c.Owner.CrashRecoveryMS = 2000
	}
	if c.Owner.ProcessingMS <= 0 {
		c.Owner.ProcessingMS = 0.5
	}
}

func (c Config) Validate() error {
	if c.SchemaVersion != 1 {
		return errors.New("schema_version must be 1")
	}
	if strings.TrimSpace(c.Regime) == "" {
		return errors.New("regime is required")
	}
	if c.Episodes <= 0 {
		return errors.New("episodes must be > 0")
	}
	if len(c.Policies) == 0 {
		return errors.New("policies required")
	}
	for _, p := range c.Policies {
		if p == "EVICON_KF" {
			return fmt.Errorf("policy %s was removed in release 062 (it upgraded the owner contract): compare EVICON on contract \"<id>+F\" instead", p)
		}
		if !knownPolicies[p] {
			return fmt.Errorf("unknown policy %q", p)
		}
	}
	w := c.Workload
	if w.Dimension < 1 || w.Dimension > 2 {
		return errors.New("workload dimension must be 1 or 2")
	}
	for name, v := range map[string][]int64{"initial": w.Initial, "value_min": w.ValueMin, "value_max": w.ValueMax} {
		if len(v) != w.Dimension {
			return fmt.Errorf("workload %s dimension mismatch", name)
		}
	}
	for i := 0; i < w.Dimension; i++ {
		if w.ValueMin[i] < 0 || w.ValueMax[i] < w.ValueMin[i] {
			return errors.New("workload ADD values must satisfy 0 <= min <= max")
		}
	}
	if w.SetFraction < 0 || w.SetFraction > 1 {
		return errors.New("set_fraction must be in [0,1]")
	}
	if w.SetFraction > 0 && (len(w.SetMin) != w.Dimension || len(w.SetMax) != w.Dimension) {
		return errors.New("SET workloads need set_min/set_max")
	}
	if w.RatePerSec <= 0 || w.DurationMS <= 0 || w.DeadlineMS <= 0 || w.TailMS < 0 {
		return errors.New("rate, duration and deadline must be > 0")
	}
	n := c.Network
	for name, p := range map[string]float64{"req_loss_prob": n.ReqLossProb, "req_late_prob": n.ReqLateProb, "resp_loss_prob": n.RespLossProb,
		"resp_hide_prob": n.RespHideProb, "query_loss_prob": n.QueryLossProb, "control_loss_prob": n.ControlLossProb,
		"terminal_reject_prob": c.Owner.TerminalRejectProb, "crash_mid_prob": c.Owner.CrashMidProb} {
		if p < 0 || p > 1 {
			return fmt.Errorf("%s must be in [0,1]", name)
		}
	}
	if n.ReqLossProb+n.ReqLateProb > 1 || n.RespLossProb+n.RespHideProb > 1 {
		return errors.New("loss + late/hide probabilities must not exceed 1")
	}
	if fu := c.Authority.FenceUncertain; fu != "" && fu != "partial" && fu != "abort" {
		return errors.New("authority fence_uncertain must be partial or abort")
	}
	if n.LateReplies != "" && n.LateReplies != "deliver" && n.LateReplies != "drop" {
		return errors.New("network late_replies must be deliver or drop")
	}
	if c.Owner.Channel != "direct" && c.Owner.Channel != "queue" {
		return errors.New("owner channel must be direct or queue")
	}
	if (c.Owner.OnlineMeanMS > 0) != (c.Owner.OfflineMeanMS > 0) {
		return errors.New("online_mean_ms and offline_mean_ms must both be set or both zero")
	}
	if u := c.Owner.OfflineUniformMS; len(u) > 0 {
		if len(u) != 2 || u[0] <= 0 || u[1] < u[0] || c.Owner.OnlineMeanMS <= 0 {
			return errors.New("offline_uniform_ms must be [lo, hi] with 0 < lo <= hi, and online_mean_ms set")
		}
	}
	if err := c.Authority.Resolve.Validate(); err != nil {
		return err
	}
	return nil
}

func LoadConfig(path string) (Config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return Config{}, err
	}
	return ParseConfig(raw)
}

func ParseConfig(raw []byte) (Config, error) {
	var c Config
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&c); err != nil {
		return Config{}, err
	}
	c.applyDefaults()
	return c, c.Validate()
}

// Expand returns the base config plus one config per sweep override.
func (c Config) Expand() ([]Config, error) {
	if len(c.Sweep) == 0 {
		return []Config{c}, nil
	}
	base, err := json.Marshal(c)
	if err != nil {
		return nil, err
	}
	out := make([]Config, 0, len(c.Sweep))
	for _, ov := range c.Sweep {
		var m map[string]any
		if err := json.Unmarshal(base, &m); err != nil {
			return nil, err
		}
		var patch map[string]any
		if err := json.Unmarshal(ov.Patch, &patch); err != nil {
			return nil, fmt.Errorf("sweep %s: %w", ov.Name, err)
		}
		deepMerge(m, patch)
		delete(m, "sweep")
		m["regime"] = c.Regime + "/" + ov.Name
		raw, _ := json.Marshal(m)
		var cc Config
		if err := json.Unmarshal(raw, &cc); err != nil {
			return nil, err
		}
		cc.applyDefaults()
		if err := cc.Validate(); err != nil {
			return nil, fmt.Errorf("sweep %s: %w", ov.Name, err)
		}
		out = append(out, cc)
	}
	return out, nil
}

func deepMerge(dst, src map[string]any) {
	for k, v := range src {
		if sv, ok := v.(map[string]any); ok {
			if dv, ok := dst[k].(map[string]any); ok {
				deepMerge(dv, sv)
				continue
			}
		}
		dst[k] = v
	}
}

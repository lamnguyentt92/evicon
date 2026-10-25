package contract

// Evidence-contract lattice (EviCon v2, release 061).
//
// An evidence contract states which *outcome evidence* an effect owner can
// provide after a client-visible timeout. It is deliberately separate from the
// guarantee catalog in contract.go: guarantees describe what the owner promises
// about effects; capabilities describe what evidence the client can obtain and
// therefore which continuation actions are safe.
//
// Capabilities are a set, not a total order. Level() maps a set to the
// representative chain K0..K4 used in the paper; K-labels are names for
// representative sets, not a ranking (a K4 contract without Q_S/F cannot cancel
// in-flight work). Includes() is a preorder on (capabilities, dedup horizon,
// owner expiry, clock-skew bound, fault model); no join/meet is defined. A
// capability declared by a contract is only usable at runtime when it is backed
// by verified adapter premises (see Backed) or measured with per-capability
// provenance (see Validate).

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type EvidenceCapability string

const (
	// CapDedup (D_h): retries with the same identity that reach the owner within
	// the dedup horizon produce at most one effect and return the original
	// outcome. Outside the horizon a retry may execute again.
	CapDedup EvidenceCapability = "D_h"
	// CapQueryApplied (Q_A): an identity-scoped status query can return terminal
	// APPLIED. NOT_FOUND / PENDING / UNCERTAIN answers are never terminal.
	CapQueryApplied EvidenceCapability = "Q_A"
	// CapStop (Q_S): an owner-enforced per-identity terminal stop. After a
	// successful stop the identity can never apply; the stop returns APPLIED if
	// the effect had already happened, otherwise STOPPED.
	CapStop EvidenceCapability = "Q_S"
	// CapFence (F): an owner-certified frontier w. Every identity with sequence
	// <= w is terminal; identities not yet started never start.
	CapFence EvidenceCapability = "F"
	// CapAtomic (X): effect and receipt commit atomically; a retry with the same
	// identity always returns the same terminal outcome.
	CapAtomic EvidenceCapability = "X"
	// CapExpiry (T): the owner enforces a validity window tau: an identity that
	// has not started by send_time + tau never starts. Evidence is implied only
	// after tau + clock-skew bound, and only when the owner (not a broker or the
	// client) enforces the window.
	CapExpiry EvidenceCapability = "T"
)

var allCapabilities = []EvidenceCapability{CapDedup, CapQueryApplied, CapStop, CapFence, CapAtomic, CapExpiry}

// UnboundedHorizonMS marks a dedup horizon that never expires (durable claims
// retained for the generation lifetime, e.g. EviCon E1/E2/E3).
const UnboundedHorizonMS int64 = -1

type Level string

const (
	K0 Level = "K0" // no evidence
	K1 Level = "K1" // dedup only
	K2 Level = "K2" // + identity-scoped APPLIED query
	K3 Level = "K3" // + terminal backward evidence (stop, fence or owner expiry)
	K4 Level = "K4" // atomic effect + receipt
)

// EvidenceSource records how a contract entry was obtained. Only
// RUNTIME_VERIFIED and MEASURED entries may drive the runtime; DOCUMENTED
// entries are inputs for the measurement study and the paper's group-D table.
type EvidenceSource string

const (
	SourceRuntimeVerified EvidenceSource = "RUNTIME_VERIFIED"
	SourceMeasured        EvidenceSource = "MEASURED"
	SourceDocumented      EvidenceSource = "DOCUMENTED"
	SourceHypothesis      EvidenceSource = "HYPOTHESIS"
)

type EvidenceContract struct {
	SchemaVersion   int                  `json:"schema_version"`
	ID              string               `json:"id"`
	System          string               `json:"system"`
	Operation       string               `json:"operation,omitempty"`
	Source          EvidenceSource       `json:"source"`
	Capabilities    []EvidenceCapability `json:"capabilities"`
	DedupHorizonMS  int64                `json:"dedup_horizon_ms"`
	ExpiryMS        int64                `json:"expiry_ms,omitempty"`
	ClockSkewMS     int64                `json:"clock_skew_ms,omitempty"`
	QueryOnlyAfter  string               `json:"query_semantics,omitempty"`
	Premises        []Premise            `json:"premises,omitempty"`
	VerifiedTestIDs []string             `json:"verified_test_ids,omitempty"`
	References      []string             `json:"references,omitempty"`
	Notes           string               `json:"notes,omitempty"`
	// Release 062 (review RV02/RV03/RV10).
	// Provenance maps each capability (and "D_h_horizon", "T_expiry") to the
	// evidence that supports it, e.g. "P4,P6,P8" or "P5:gap=30s,intervening=0".
	// MEASURED contracts must give provenance for every capability.
	Provenance map[string]string `json:"provenance,omitempty"`
	// DeclaredUnverified lists capabilities the system documents but the
	// measurement could not establish (e.g. X, an unbounded horizon). They are
	// never part of Capabilities and never drive the runtime.
	DeclaredUnverified []string `json:"declared_unverified,omitempty"`
	// FaultModel names the fault model under which the contract holds (e.g.
	// "crash-stop;no-reboot;loss+delay"). Contracts under different fault
	// models are incomparable in Includes.
	FaultModel string `json:"fault_model,omitempty"`
}

func (c EvidenceContract) Has(cap EvidenceCapability) bool {
	for _, x := range c.Capabilities {
		if x == cap {
			return true
		}
	}
	return false
}

func (c EvidenceContract) Validate() error {
	if strings.TrimSpace(c.ID) == "" {
		return errors.New("evidence contract id is required")
	}
	switch c.Source {
	case SourceRuntimeVerified, SourceMeasured, SourceDocumented, SourceHypothesis:
	default:
		return fmt.Errorf("contract %s: unsupported source %q", c.ID, c.Source)
	}
	seen := map[EvidenceCapability]bool{}
	for _, x := range c.Capabilities {
		known := false
		for _, k := range allCapabilities {
			if x == k {
				known = true
			}
		}
		if !known {
			return fmt.Errorf("contract %s: unknown capability %q", c.ID, x)
		}
		if seen[x] {
			return fmt.Errorf("contract %s: duplicate capability %q", c.ID, x)
		}
		seen[x] = true
	}
	if c.Has(CapDedup) {
		if c.DedupHorizonMS == 0 || c.DedupHorizonMS < UnboundedHorizonMS {
			return fmt.Errorf("contract %s: D_h requires dedup_horizon_ms > 0 or -1 (unbounded)", c.ID)
		}
	} else if c.DedupHorizonMS != 0 {
		return fmt.Errorf("contract %s: dedup_horizon_ms set without D_h", c.ID)
	}
	if c.Has(CapExpiry) && c.ExpiryMS <= 0 {
		return fmt.Errorf("contract %s: T requires expiry_ms > 0", c.ID)
	}
	if c.ClockSkewMS < 0 {
		return fmt.Errorf("contract %s: clock_skew_ms must be >= 0", c.ID)
	}
	if c.Has(CapAtomic) && !c.Has(CapDedup) {
		return fmt.Errorf("contract %s: X (atomic receipt) implies same-identity replay; declare D_h", c.ID)
	}
	for _, d := range c.DeclaredUnverified {
		if c.Has(EvidenceCapability(d)) {
			return fmt.Errorf("contract %s: %s is both a capability and declared_unverified", c.ID, d)
		}
	}
	if c.Source == SourceMeasured {
		if err := c.validateMeasured(); err != nil {
			return err
		}
	}
	// Note: Q_S and F answer from the owner's durable effect/receipt records,
	// which are independent of the (possibly short) dedup cache horizon.
	return nil
}

// validateMeasured enforces review RV03: a black-box measurement can support a
// capability only with recorded probe evidence; it can never establish an
// unbounded dedup horizon, and X needs crash-cut evidence (not "no violation
// observed").
func (c EvidenceContract) validateMeasured() error {
	for _, x := range c.Capabilities {
		pv := strings.TrimSpace(c.Provenance[string(x)])
		if pv == "" {
			return fmt.Errorf("contract %s: MEASURED capability %s has no provenance", c.ID, x)
		}
		if strings.Contains(strings.ToUpper(pv), "DECLARED") {
			return fmt.Errorf("contract %s: MEASURED capability %s rests on a declaration (%s)", c.ID, x, pv)
		}
	}
	if c.Has(CapDedup) && c.DedupHorizonMS == UnboundedHorizonMS {
		return fmt.Errorf("contract %s: a measurement cannot establish an unbounded dedup horizon; use the observed lower bound", c.ID)
	}
	if c.Has(CapAtomic) && !strings.Contains(strings.ToUpper(c.Provenance[string(CapAtomic)]), "CRASH_CUT") {
		return fmt.Errorf("contract %s: MEASURED X requires crash-cut evidence", c.ID)
	}
	if c.Has(CapExpiry) && strings.TrimSpace(c.Provenance["T_expiry"]) == "" && strings.TrimSpace(c.Provenance[string(CapExpiry)]) == "" {
		return fmt.Errorf("contract %s: MEASURED T requires provenance", c.ID)
	}
	return nil
}

// Level maps the capability set to the representative chain.
func (c EvidenceContract) Level() Level {
	switch {
	case c.Has(CapAtomic):
		return K4
	case c.BackwardEvidence():
		return K3
	case c.Has(CapQueryApplied):
		return K2
	case c.Has(CapDedup):
		return K1
	default:
		return K0
	}
}

// BackwardEvidence reports whether the contract can ever turn an ambiguous
// command into terminal STOPPED evidence (Theorem T2 premise). Owner expiry
// alone only bounds *when* an effect can still happen; it reveals STOPPED only
// together with a truthful identity-scoped query whose NOT_FOUND answer
// implies "no effect" (atomic receipt, or durable claim before effect).
func (c EvidenceContract) BackwardEvidence() bool {
	return c.Has(CapStop) || c.Has(CapFence) || c.ExpiryRevealsStopped()
}

// ExpiryRevealsStopped: owner expiry plus truthful query makes NOT_FOUND terminal.
func (c EvidenceContract) ExpiryRevealsStopped() bool {
	return c.Has(CapExpiry) && (c.Has(CapAtomic) || (c.Has(CapQueryApplied) && c.Has(CapDedup) && c.DedupHorizonMS == UnboundedHorizonMS))
}

// NoFutureEffect reports whether an identity sent ageMS ago can no longer
// start at the owner (owner-enforced expiry plus clock-skew bound). This bounds
// late effects even when it does not reveal the outcome.
func (c EvidenceContract) NoFutureEffect(ageMS int64) bool {
	return c.Has(CapExpiry) && ageMS >= c.ExpiryMS+c.ClockSkewMS
}

// durableTerminalReceipts: an owner-written terminal receipt is final for the
// identity when same-identity replays are deduplicated forever.
func (c EvidenceContract) durableTerminalReceipts() bool {
	return c.Has(CapAtomic) || c.Has(CapStop) || c.Has(CapFence) ||
		(c.Has(CapDedup) && c.DedupHorizonMS == UnboundedHorizonMS && c.Has(CapQueryApplied))
}

// Includes is a preorder: c >= o when c offers every capability of o with a
// dedup horizon at least as long, an owner expiry at most as long (a shorter
// window bounds late effects earlier), a clock-skew bound at most as large,
// and the same fault model (an empty fault model on o means "unspecified").
func (c EvidenceContract) Includes(o EvidenceContract) bool {
	for _, x := range o.Capabilities {
		if !c.Has(x) {
			return false
		}
	}
	if o.Has(CapDedup) && !horizonAtLeast(c.DedupHorizonMS, o.DedupHorizonMS) {
		return false
	}
	if o.Has(CapExpiry) && (c.ExpiryMS > o.ExpiryMS || c.ClockSkewMS > o.ClockSkewMS) {
		return false
	}
	if o.FaultModel != "" && c.FaultModel != o.FaultModel {
		return false
	}
	return true
}

func horizonAtLeast(a, b int64) bool {
	if a == UnboundedHorizonMS {
		return true
	}
	if b == UnboundedHorizonMS {
		return false
	}
	return a >= b
}

// Extend returns c plus the given capabilities (used for EVICON+F: the owner
// extended with a fence/cutoff).
func (c EvidenceContract) Extend(id string, caps ...EvidenceCapability) EvidenceContract {
	out := c
	out.ID = id
	out.Capabilities = append([]EvidenceCapability(nil), c.Capabilities...)
	for _, x := range caps {
		if !out.Has(x) {
			out.Capabilities = append(out.Capabilities, x)
		}
	}
	sortCaps(out.Capabilities)
	return out
}

func sortCaps(caps []EvidenceCapability) {
	order := map[EvidenceCapability]int{}
	for i, k := range allCapabilities {
		order[k] = i
	}
	sort.Slice(caps, func(i, j int) bool { return order[caps[i]] < order[caps[j]] })
}

// ---------------------------------------------------------------------------
// Evidence admissibility: which observed outcomes may narrow the belief.

type Outcome string

const (
	OutcomeApplied   Outcome = "APPLIED"
	OutcomeStopped   Outcome = "STOPPED"
	OutcomeUncertain Outcome = "UNCERTAIN"
	OutcomePending   Outcome = "PENDING"
	OutcomeNotFound  Outcome = "NOT_FOUND"
	OutcomeTimeout   Outcome = "TIMEOUT"  // client/platform timeout (504, TIMED_OUT ...)
	OutcomeExpired   Outcome = "EXPIRED"  // validity window elapsed
	OutcomeCanceled  Outcome = "CANCELED" // a cancel API acknowledged
)

type EvidenceChannel string

const (
	ChannelDispatchReply EvidenceChannel = "DISPATCH_REPLY"
	ChannelQuery         EvidenceChannel = "QUERY"
	ChannelStop          EvidenceChannel = "STOP"
	ChannelFence         EvidenceChannel = "FENCE"
	ChannelExpiryClock   EvidenceChannel = "EXPIRY_CLOCK"
	ChannelCancelAPI     EvidenceChannel = "CANCEL_API"
)

// Admissibility is the decision whether an observation may filter the belief
// and, if so, to which terminal status.
type Admissibility struct {
	Admissible bool    `json:"admissible"`
	As         Outcome `json:"as,omitempty"`
	Reason     string  `json:"reason"`
}

// Admit applies the safety rules of section 6.2 of the v2 plan:
//   - an ordinary read, NOT_FOUND, PENDING, UNCERTAIN or a timeout is never
//     terminal evidence;
//   - APPLIED is admissible from any identity-scoped channel of an owner whose
//     receipts are truthful (receipt committed after/with the effect);
//   - STOPPED is admissible only when the contract provides backward evidence
//     on that channel (Q_S stop, F fence, owner-enforced expiry after
//     tau+epsilon, or an atomic owner's terminal reply);
//   - an acknowledged cancel is admissible only when the cancel *is* Q_S.
func (c EvidenceContract) Admit(o Outcome, ch EvidenceChannel, ageMS int64) Admissibility {
	switch o {
	case OutcomeNotFound:
		// NOT_FOUND is terminal STOPPED only after the owner can no longer start
		// the identity AND the owner's records are truthful about effects.
		if ch == ChannelQuery && c.ExpiryRevealsStopped() && c.NoFutureEffect(ageMS) {
			return Admissibility{Admissible: true, As: OutcomeStopped, Reason: "NOT_FOUND_AFTER_OWNER_EXPIRY"}
		}
		return Admissibility{Reason: "NON_TERMINAL_OBSERVATION"}
	case OutcomeUncertain, OutcomePending, OutcomeTimeout:
		return Admissibility{Reason: "NON_TERMINAL_OBSERVATION"}
	case OutcomeApplied:
		if c.Has(CapQueryApplied) || c.Has(CapAtomic) || c.Has(CapStop) || c.Has(CapFence) || ch == ChannelDispatchReply {
			return Admissibility{Admissible: true, As: OutcomeApplied, Reason: "IDENTITY_SCOPED_APPLIED"}
		}
		return Admissibility{Reason: "APPLIED_WITHOUT_TRUTHFUL_RECEIPT_CAPABILITY"}
	case OutcomeStopped:
		switch ch {
		case ChannelStop:
			if c.Has(CapStop) {
				return Admissibility{Admissible: true, As: OutcomeStopped, Reason: "OWNER_ENFORCED_STOP"}
			}
		case ChannelFence:
			if c.Has(CapFence) {
				return Admissibility{Admissible: true, As: OutcomeStopped, Reason: "OWNER_CERTIFIED_FENCE"}
			}
		case ChannelDispatchReply, ChannelQuery:
			// A terminal STOPPED receipt written by the owner (validation reject,
			// sealed-by-cutoff, atomic reject) is terminal only when the owner
			// guarantees no later attempt with the same identity can apply.
			if c.durableTerminalReceipts() {
				return Admissibility{Admissible: true, As: OutcomeStopped, Reason: "OWNER_TERMINAL_RECEIPT"}
			}
		}
		return Admissibility{Reason: "STOPPED_NOT_BACKED_BY_CONTRACT"}
	case OutcomeExpired:
		// Expiry bounds late effects (NoFutureEffect) but never reveals whether
		// the effect happened before the window closed.
		if ch == ChannelExpiryClock && c.Has(CapExpiry) {
			return Admissibility{Reason: "EXPIRY_BOUNDS_LATE_EFFECT_ONLY"}
		}
		return Admissibility{Reason: "EXPIRY_NOT_OWNER_ENFORCED"}
	case OutcomeCanceled:
		// That the cancel API *is* the owner's per-identity stop (serialized
		// with effects, durable tombstone) is an adapter premise: an adapter
		// must map a cancel acknowledgement to this channel only then (paper,
		// rule A5). No runtime adapter uses this channel in release 072.
		if ch == ChannelCancelAPI && c.Has(CapStop) {
			return Admissibility{Admissible: true, As: OutcomeStopped, Reason: "CANCEL_IS_OWNER_ENFORCED_STOP"}
		}
		return Admissibility{Reason: "CANCEL_ACK_NOT_TERMINAL"}
	}
	return Admissibility{Reason: "UNKNOWN_OUTCOME"}
}

// ---------------------------------------------------------------------------
// Action compilation for one ambiguous command.

type Action string

const (
	ActionWait         Action = "WAIT"
	ActionQuery        Action = "QUERY"
	ActionForwardRetry Action = "FORWARD_RETRY"
	ActionStop         Action = "STOP"
	ActionFence        Action = "FENCE"
	ActionReissueNewID Action = "REISSUE_NEW_ID"
	ActionEscalate     Action = "ESCALATE"
)

// CommandTiming carries the per-command clock facts needed by Compile.
type CommandTiming struct {
	// FirstSendMS is when the first attempt for this identity left the client.
	FirstSendMS int64 `json:"first_send_ms"`
	// NowMS is the decision time.
	NowMS int64 `json:"now_ms"`
	// IntentValidUntilMS: after this time the effect is no longer wanted and
	// a late effect is harmful (0 = no application validity window).
	IntentValidUntilMS int64 `json:"intent_valid_until_ms,omitempty"`
	// MaxNetworkDelayMS bounds one-way delivery for the retry-horizon margin.
	MaxNetworkDelayMS int64 `json:"max_network_delay_ms"`
	// TerminalKnown is true when admissible terminal evidence already exists.
	TerminalKnown bool `json:"terminal_known"`
	// TerminalStopped is true when the admissible terminal evidence is STOPPED.
	TerminalStopped bool `json:"terminal_stopped"`
}

type ActionSet struct {
	Allowed []Action          `json:"allowed"`
	Reasons map[Action]string `json:"reasons"`
	// ForwardRetryDeadlineMS: last send time at which a same-identity retry is
	// still provably at-most-once and not late (Theorem T4). -1 = unbounded.
	ForwardRetryDeadlineMS int64 `json:"forward_retry_deadline_ms"`
}

func (s ActionSet) Allows(a Action) bool {
	for _, x := range s.Allowed {
		if x == a {
			return true
		}
	}
	return false
}

// ForwardRetryDeadline implements Theorem T4: a same-identity retry is safe
// only if it reaches the owner within min(h, tau_intent) of the first send,
// minus the one-way delay margin. Returns -1 when unbounded, or a value below
// FirstSendMS when no forward retry is ever safe (no D_h).
//
// MaxNetworkDelayMS < 0 declares that one-way delivery delay is unbounded (for
// example a broker queue in front of a sleepy device without broker expiry).
// Then no finite dedup horizon makes a retry provably at-most-once, and the
// validity window gives no retry margin (late effects can only be prevented by
// owner expiry or a fence, never by the client's retry schedule).
func (c EvidenceContract) ForwardRetryDeadline(t CommandTiming) int64 {
	if !c.Has(CapDedup) {
		return t.FirstSendMS - 1
	}
	unboundedDelay := t.MaxNetworkDelayMS < 0
	deadline := int64(-1)
	if c.DedupHorizonMS != UnboundedHorizonMS {
		if unboundedDelay {
			return t.FirstSendMS - 1
		}
		deadline = t.FirstSendMS + c.DedupHorizonMS - t.MaxNetworkDelayMS
	}
	if t.IntentValidUntilMS > 0 {
		margin := t.MaxNetworkDelayMS
		if unboundedDelay {
			margin = 0
		}
		v := t.IntentValidUntilMS - margin
		if deadline == -1 || v < deadline {
			deadline = v
		}
	}
	return deadline
}

// Compile returns the actions that are safe for one ambiguous command under
// the contract. It never claims an action is *useful*; package resolve decides
// among the allowed actions.
func (c EvidenceContract) Compile(t CommandTiming) ActionSet {
	s := ActionSet{Reasons: map[Action]string{}}
	add := func(a Action, why string) {
		s.Allowed = append(s.Allowed, a)
		s.Reasons[a] = why
	}
	s.ForwardRetryDeadlineMS = c.ForwardRetryDeadline(t)
	if t.TerminalKnown {
		// Release 072: a re-issue sent after V - delta can take effect after
		// the end of validity V; with an unbounded delay no margin helps and
		// the bound is V itself (paper, Section "continuation actions").
		reissueBy := t.IntentValidUntilMS
		if reissueBy > 0 && t.MaxNetworkDelayMS > 0 {
			reissueBy -= t.MaxNetworkDelayMS
		}
		if t.TerminalStopped && (t.IntentValidUntilMS == 0 || t.NowMS <= reissueBy) {
			add(ActionReissueNewID, "TERMINAL_STOPPED_INTENT_STILL_VALID")
		}
		add(ActionWait, "TERMINAL_EVIDENCE_PRESENT")
		return s
	}
	add(ActionWait, "ALWAYS_SAFE")
	if c.Has(CapDedup) {
		if s.ForwardRetryDeadlineMS == -1 || t.NowMS <= s.ForwardRetryDeadlineMS {
			add(ActionForwardRetry, "WITHIN_MIN_HORIZON_AND_VALIDITY")
		} else {
			s.Reasons[ActionForwardRetry] = "BLOCKED:RETRY_OUTSIDE_HORIZON_OR_VALIDITY"
		}
	} else {
		s.Reasons[ActionForwardRetry] = "BLOCKED:NO_DEDUP"
	}
	if c.Has(CapQueryApplied) || c.Has(CapAtomic) {
		add(ActionQuery, "IDENTITY_SCOPED_QUERY")
	}
	if c.Has(CapStop) {
		add(ActionStop, "OWNER_ENFORCED_STOP")
	}
	if c.Has(CapFence) {
		add(ActionFence, "OWNER_CERTIFIED_FENCE")
	}
	if !c.BackwardEvidence() {
		add(ActionEscalate, "NO_BACKWARD_EVIDENCE_T2")
	}
	return s
}

// ---------------------------------------------------------------------------
// Deriving capabilities from verified adapter premises.

// premise requirements for each capability (all premises in one group needed;
// alternatives in separate groups).
var capabilityPremises = map[EvidenceCapability][][]Premise{
	CapDedup: {
		{PremiseStableCommandID, PremiseDurableClaimBeforeEffect, PremiseNoReplayAfterCrash},
		{PremiseStableCommandID, PremiseAtomicEffectReceipt},
	},
	CapQueryApplied: {
		{PremiseReceiptAfterEffect},
		{PremiseAtomicEffectReceipt},
	},
	// Release 072: Q_S and F also require that the owner recognizes crash
	// orphans (a durable claim before every effect, or atomic effect+receipt),
	// so that a stop or a fence reports UNCERTAIN for an orphan instead of
	// sealing it as STOPPED (review RV04; paper, Definition "capabilities").
	CapStop: {
		{PremisePerIdentityStop, PremiseOwnerBlocksLateAttempt, PremiseStopConfirmedBeforeEffect, PremiseDurableClaimBeforeEffect},
		{PremisePerIdentityStop, PremiseOwnerBlocksLateAttempt, PremiseStopConfirmedBeforeEffect, PremiseAtomicEffectReceipt},
	},
	CapFence: {
		{PremiseDurableCutoff, PremiseDrainInflightEffects, PremiseSerializedStateRead, PremiseOwnerBlocksLateAttempt, PremiseDurableClaimBeforeEffect},
		{PremiseDurableCutoff, PremiseDrainInflightEffects, PremiseSerializedStateRead, PremiseOwnerBlocksLateAttempt, PremiseAtomicEffectReceipt},
	},
	CapAtomic: {
		{PremiseAtomicEffectReceipt, PremiseStableCommandID},
	},
	CapExpiry: {
		{PremiseOwnerEnforcedExpiry, PremiseBoundedClockSkew},
	},
}

// PremisePerIdentityStop: the owner provides an identity-scoped stop which,
// serialized with effect execution, either reports APPLIED or durably records
// a tombstone that makes every later attempt with that identity STOPPED.
const (
	PremisePerIdentityStop     Premise = "PER_IDENTITY_STOP"
	PremiseOwnerEnforcedExpiry Premise = "OWNER_ENFORCED_EXPIRY"
	PremiseBoundedClockSkew    Premise = "BOUNDED_CLOCK_SKEW"
)

// Backed returns the capabilities of the declared set that are supported by
// the adapter premises, and the declared ones that are not (and must be
// dropped before runtime use).
func Backed(declared []EvidenceCapability, adapter AdapterProfile) (backed []EvidenceCapability, unbacked []EvidenceCapability) {
	have := map[Premise]bool{}
	for _, p := range adapter.Premises {
		have[p] = true
	}
	for _, c := range declared {
		ok := false
		for _, group := range capabilityPremises[c] {
			all := true
			for _, p := range group {
				if !have[p] {
					all = false
					break
				}
			}
			if all {
				ok = true
				break
			}
		}
		if ok {
			backed = append(backed, c)
		} else {
			unbacked = append(unbacked, c)
		}
	}
	return
}

// RuntimeContract derives the evidence contract a runtime adapter may use from
// its verified premises. Unbacked declared capabilities are removed and named
// in Notes so that a configuration cannot silently upgrade a profile.
func RuntimeContract(id string, declared EvidenceContract, adapter AdapterProfile) (EvidenceContract, error) {
	if err := declared.Validate(); err != nil {
		return EvidenceContract{}, err
	}
	backed, unbacked := Backed(declared.Capabilities, adapter)
	out := declared
	out.ID = id
	out.Capabilities = backed
	out.Source = SourceRuntimeVerified
	out.Premises = append([]Premise(nil), adapter.Premises...)
	out.VerifiedTestIDs = append([]string(nil), adapter.VerifiedTestIDs...)
	if !out.Has(CapDedup) {
		out.DedupHorizonMS = 0
	}
	if !out.Has(CapExpiry) {
		out.ExpiryMS = 0
	}
	if len(unbacked) > 0 {
		names := make([]string, len(unbacked))
		for i, u := range unbacked {
			names[i] = string(u)
		}
		out.Notes = strings.TrimSpace(out.Notes + " DROPPED_UNBACKED=" + strings.Join(names, ","))
	}
	if out.Has(CapAtomic) && !out.Has(CapDedup) {
		out.Capabilities = append(out.Capabilities, CapDedup)
		out.DedupHorizonMS = UnboundedHorizonMS
	}
	sortCaps(out.Capabilities)
	return out, out.Validate()
}

// ---------------------------------------------------------------------------
// Reference contracts for the EviCon runtime profiles.

// EvidenceContractForProfile returns the declared evidence contract for the
// runtime profiles. E2 declares Q_S only from release 061, which adds the
// per-identity stop endpoint; RuntimeContract drops it unless the adapter
// profile carries PremisePerIdentityStop.
func EvidenceContractForProfile(profile string) (EvidenceContract, error) {
	switch profile {
	case "E0":
		return EvidenceContract{SchemaVersion: 1, ID: "evicon-e0", System: "EviCon E0", Source: SourceRuntimeVerified}, nil
	case "E1":
		return EvidenceContract{SchemaVersion: 1, ID: "evicon-e1", System: "EviCon E1", Source: SourceRuntimeVerified,
			Capabilities: []EvidenceCapability{CapDedup, CapQueryApplied}, DedupHorizonMS: UnboundedHorizonMS,
			Notes: "claim before effect; crash between effect and receipt leaves UNCERTAIN forever (no backward evidence)"}, nil
	case "E2":
		return EvidenceContract{SchemaVersion: 1, ID: "evicon-e2", System: "EviCon E2", Source: SourceRuntimeVerified,
			Capabilities: []EvidenceCapability{CapDedup, CapQueryApplied, CapStop, CapFence}, DedupHorizonMS: UnboundedHorizonMS}, nil
	case "E3":
		return EvidenceContract{SchemaVersion: 1, ID: "evicon-e3", System: "EviCon E3", Source: SourceRuntimeVerified,
			Capabilities: []EvidenceCapability{CapDedup, CapQueryApplied, CapAtomic}, DedupHorizonMS: UnboundedHorizonMS,
			Notes: "atomic receipt; an in-flight request that never arrived is NOT_FOUND, not STOPPED"}, nil
	}
	return EvidenceContract{}, fmt.Errorf("no evidence contract for profile %q", profile)
}

// ---------------------------------------------------------------------------
// Catalog loading (experiments/contracts/*.json).

func LoadEvidenceContracts(dir string) ([]EvidenceContract, error) {
	matches, err := filepath.Glob(filepath.Join(dir, "*.json"))
	if err != nil {
		return nil, err
	}
	sort.Strings(matches)
	out := make([]EvidenceContract, 0, len(matches))
	ids := map[string]string{}
	for _, m := range matches {
		raw, err := os.ReadFile(m)
		if err != nil {
			return nil, err
		}
		var c EvidenceContract
		dec := json.NewDecoder(strings.NewReader(string(raw)))
		dec.DisallowUnknownFields()
		if err := dec.Decode(&c); err != nil {
			return nil, fmt.Errorf("%s: %w", m, err)
		}
		if c.SchemaVersion != 1 {
			return nil, fmt.Errorf("%s: schema_version must be 1", m)
		}
		if err := c.Validate(); err != nil {
			return nil, fmt.Errorf("%s: %w", m, err)
		}
		if prev, ok := ids[c.ID]; ok {
			return nil, fmt.Errorf("duplicate contract id %s in %s and %s", c.ID, prev, m)
		}
		ids[c.ID] = m
		sortCaps(c.Capabilities)
		out = append(out, c)
	}
	return out, nil
}

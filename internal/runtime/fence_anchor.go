package runtime

// Release 086 (verifier finding B2-02, approved by the author on 5 November 2026): the anchor of a complete
// fence is computed from evidence.
//
// Releases 061-085 replaced the authority's anchor by the owner's reported `state` after a complete fence
// (rule R5, and R5' when no listed identity is UNCERTAIN) and dropped every command at or below the cutoff
// from the belief. Soundness then rested on a premise the paper does not state: the owner's aggregate state
// is true. The firmware's level is RAM only (firmware/zephyr-cutoff/src/evc_core.c; the gateway copies it
// into the fence answer), so after a reboot a complete fence could re-anchor to 0 and forget delivered doses.
//
// With FenceAnchorEvidence (the default from release 086) the authority computes the new anchor from
// evidence: the previous anchor plus every identity above the previous cutoff and at or below the fence's
// cutoff that is APPLIED (a receipt of this answer or terminal evidence already committed), applied by the
// domain's exact terminal reducer (evidence.FenceAnchorStates: ADD and SET, every dimension); every other
// identity at or below the cutoff is STOPPED (listed STOPPED, committed STOPPED, or unlisted: under FC it
// never took effect) and contributes nothing. Then
//   - the owner's reported state is not a state the evidence allows: an evidence conflict; the fence is
//     aborted as every evidence conflict is (trace EVIDENCE_CONFLICT with both vectors, BARRIER_ABORTED, no
//     seal, no anchor change, the domain marked conflicted so the resolver escalates), and the reported
//     state is never adopted;
//   - an identity at or below the cutoff without terminal knowledge after the intake (impossible under FC;
//     checked anyway) or an inconsistent history: the fence is aborted (FENCE_ANCHOR_UNKNOWN /
//     FENCE_ANCHOR_INCONSISTENT);
//   - exactly one state: it is committed as the anchor (CommitBarrierLifecycleSeal, as before, with the
//     computed vector instead of the reported one);
//   - several states (a SET domain whose evidence leaves the order of an applied ADD relative to the last
//     applied SET open): every answer is taken in and the unlisted identities sealed as on completion, but no
//     anchor is committed (the PARTIAL lifecycle of rule R5'; the belief keeps those commands by status).
// Every complete fence writes FENCE_ANCHOR_CHECK (both vectors, the batch and the result) in both modes.
// FenceAnchorOwnerState (`-fence-anchor owner-state`) restores the anchor of releases 061-085 for
// regression tests; it changes nothing else.

import (
	"errors"
	"fmt"
	"strconv"
	"strings"

	"evicon/internal/domain"
	"evicon/internal/evidence"
	"evicon/internal/journal"
	"evicon/internal/protocol"
	"evicon/internal/trace"
)

// fenceAnchor is the evidence about the owner's state after a complete fence answer.
type fenceAnchor struct {
	States       []domain.State // the states the evidence allows (sorted); one = the anchor
	Unknown      []string       // identities at or below the cutoff without terminal knowledge
	Inconsistent string         // a history no state is consistent with
	Batch        int            // identities above the previous cutoff and at or below the cutoff
	Applied      int            // of which APPLIED
	PrevCutoff   uint64
	Undetermined bool // more states than the configuration budget
	// release 087 (REC-3, -reboot-recovery only): the comparison of the reported level with the identities applied
	// above the owner's claim floor; nil: the comparison of release 086
	Floor *floorCheck
}

// Contains: the reported state is one the evidence allows.
func (f fenceAnchor) Contains(s []int64) bool {
	for _, x := range f.States {
		if sameState(x, domain.State(s)) {
			return true
		}
	}
	return false
}

func (f fenceAnchor) statesJSON() [][]int64 {
	out := make([][]int64, 0, len(f.States))
	for _, x := range f.States {
		out = append(out, append([]int64(nil), x...))
	}
	return out
}

// StatesString: "[340]" for one state, "[5]|[7]" for several (at most 8 shown).
func (f fenceAnchor) StatesString() string {
	parts := []string{}
	for i, x := range f.States {
		if i == 8 {
			parts = append(parts, fmt.Sprintf("...(%d)", len(f.States)))
			break
		}
		parts = append(parts, vecString(x))
	}
	if len(parts) == 0 {
		return "[]"
	}
	return strings.Join(parts, "|")
}

func vecString(v []int64) string {
	s := make([]string, len(v))
	for i, x := range v {
		s[i] = strconv.FormatInt(x, 10)
	}
	return "[" + strings.Join(s, ",") + "]"
}

// fenceBatch returns the commands above the domain's cutoff and at or below the fence's cutoff with their
// knowledge after the intake of the answer's terminal receipts recs: the listed outcome, else the committed
// terminal status, else STOPPED (unlisted under FC: it never took effect; sealed when the policy seals).
// unknown lists identities whose listed outcome is not terminal (impossible here: such answers take the
// UNCERTAIN path before).
func (a *Authority) fenceBatch(d journal.Domain, cutoff uint64, recs []journal.ReceiptRecord) ([]journal.CommandRecord, []string, error) {
	cmds, err := a.cfg.Store.ListCommandsForBelief(d.ID, d.CutoffSequence)
	if err != nil {
		return nil, nil, err
	}
	listed := make(map[string]string, len(recs))
	for _, r := range recs {
		listed[r.CommandID] = r.Outcome
	}
	var batch []journal.CommandRecord
	var unknown []string
	for _, c := range cmds {
		if c.Sequence > cutoff {
			continue // none under a full-frontier fence (the commit checks the frontier)
		}
		st := c.Status
		if o, ok := listed[c.CommandID]; ok {
			st = o
		} else if !terminalStatus(st) {
			st = "STOPPED"
		}
		if !terminalStatus(st) {
			unknown = append(unknown, c.CommandID)
			continue
		}
		c.Status = st
		batch = append(batch, c)
	}
	return batch, unknown, nil
}

// computeFenceAnchor computes what the evidence says about the owner's state after the complete answer
// whose terminal receipts are recs (fence cutoff `cutoff`, domain d before the fence).
func (a *Authority) computeFenceAnchor(d journal.Domain, cutoff uint64, recs []journal.ReceiptRecord) (fenceAnchor, error) {
	batch, unknown, err := a.fenceBatch(d, cutoff, recs)
	if err != nil {
		return fenceAnchor{}, err
	}
	fa := fenceAnchor{Unknown: unknown, Batch: len(batch) + len(unknown), PrevCutoff: d.CutoffSequence}
	for _, c := range batch {
		if c.Status == "APPLIED" {
			fa.Applied++
		}
	}
	if len(unknown) > 0 {
		return fa, nil
	}
	states, err := evidence.FenceAnchorStates(d, batch, a.cfg.MaxConfigurations)
	switch {
	case errors.Is(err, evidence.ErrBeliefBudget):
		fa.Undetermined = true // more states than the budget: no anchor can be named
		return fa, nil
	case err != nil:
		fa.Inconsistent = err.Error()
		return fa, nil
	}
	fa.States = states
	return fa, nil
}

func (f fenceAnchor) result(reported []int64, mode string) string {
	switch {
	case len(f.Unknown) > 0:
		return "UNKNOWN"
	case f.Inconsistent != "":
		return "INCONSISTENT"
	case f.Undetermined:
		return "UNDETERMINED"
	case f.Floor != nil && mode == FenceAnchorOwnerState && !f.Contains(reported):
		// release 087, the negative control of plan 087 (-reboot-recovery -fence-anchor owner-state): the reported
		// state is adopted although it is not the evidence anchor; the floor check is in the reason
		return "MISMATCH_OWNER_STATE_ADOPTED"
	case f.Floor != nil && f.Floor.Match:
		return "MATCH_FLOOR"
	case f.Floor != nil:
		return "MISMATCH_FLOOR"
	case !f.Contains(reported):
		if mode == FenceAnchorOwnerState {
			return "MISMATCH_OWNER_STATE_ADOPTED"
		}
		return "MISMATCH"
	case len(f.States) > 1:
		return "UNDETERMINED"
	}
	return "MATCH"
}

// traceFenceAnchor writes FENCE_ANCHOR_CHECK for a complete fence answer: the anchor mode, the computed
// anchor(s) and the reported state, the batch, and the result (MATCH, MISMATCH, UNDETERMINED, UNKNOWN,
// INCONSISTENT; MISMATCH_OWNER_STATE_ADOPTED under -fence-anchor owner-state).
func (a *Authority) traceFenceAnchor(br protocol.BarrierRequest, d journal.Domain, rc protocol.BarrierReceipt, f fenceAnchor) {
	if a.cfg.Trace == nil {
		return
	}
	_ = a.cfg.Trace.Write(trace.Event{DomainID: br.DomainID, Event: "FENCE_ANCHOR_CHECK", Revision: d.Revision, Policy: string(a.cfg.Policy),
		Outcome: f.result(rc.State, a.cfg.FenceAnchor),
		Reason: truncate(fmt.Sprintf("token=%s;mode=%s;computed=%s;reported=%s;previous=%s;prev_cutoff=%d;cutoff=%d;batch=%d;applied=%d;%sunknown=%s",
			br.Token, a.cfg.FenceAnchor, f.StatesString(), vecString(rc.State), vecString(d.Anchor), f.PrevCutoff, rc.CutoffSequence, f.Batch, f.Applied,
			f.floorText(), strings.Join(f.Unknown, ",")), 400)})
}

// commitUnanchoredLocked ends the PENDING barrier br whose evidence allows several states: every terminal
// answer is taken in and (seal) every unlisted identity at or below the cutoff sealed STOPPED, as on
// completion, but the anchor and the cutoff of the domain stay (lifecycle PARTIAL, as rule R5'). Caller
// holds a.mu.
func (a *Authority) commitUnanchoredLocked(br protocol.BarrierRequest, d journal.Domain, rc protocol.BarrierReceipt, recs []journal.ReceiptRecord, seal bool, states int) (int, any) {
	rev, sealed, err := a.cfg.Store.CommitBarrierUnanchored(br.DomainID, br.Token, d.Generation, rc.CutoffSequence, rc.State, rc.OwnerVersion, recs, seal)
	if errors.Is(err, journal.ErrTerminalReversal) {
		return a.barrierConflict(br, d.Generation, []evidenceConflict{{Detail: err.Error()}})
	}
	if errors.Is(err, journal.ErrBarrierPartial) {
		return 409, map[string]any{"error": "BARRIER_PARTIAL"}
	}
	if errors.Is(err, journal.ErrBarrierAlreadyComplete) {
		prev, _ := a.cfg.Store.GetBarrierLifecycle(br.DomainID, d.Generation, br.Token)
		return 200, map[string]any{"barrier": protocol.BarrierReceipt{SchemaVersion: 1, DomainID: prev.DomainID, Generation: prev.Generation, Token: prev.Token, CutoffSequence: prev.CutoffSequence, State: prev.Anchor, OwnerVersion: prev.OwnerVersion, Reason: "AUTHORITY_BARRIER_REPLAY"}, "revision": prev.CompleteRevision}
	}
	if err != nil {
		return 500, map[string]any{"error": err.Error()}
	}
	if a.cfg.Trace != nil {
		for _, r := range recs {
			_ = a.cfg.Trace.Write(trace.Event{DomainID: br.DomainID, CommandID: r.CommandID, Event: "EVIDENCE_COMMITTED", Revision: rev, Outcome: r.Outcome, Reason: "BARRIER_RECEIPT:" + br.Token})
		}
		for _, cid := range sealed {
			_ = a.cfg.Trace.Write(trace.Event{DomainID: br.DomainID, CommandID: cid, Event: "EVIDENCE_COMMITTED", Revision: rev, Outcome: "STOPPED", Reason: "BARRIER_SEALED_UNCLAIMED:" + br.Token})
		}
		_ = a.cfg.Trace.Write(trace.Event{DomainID: br.DomainID, Event: "BARRIER_PER_IDENTITY_COMMITTED", Revision: rev,
			Reason: truncate(fmt.Sprintf("%s:ANCHOR_UNDETERMINED:states=%d", br.Token, states), 400)})
	}
	return 200, map[string]any{"barrier": rc, "revision": rev, "intake": "UNANCHORED", "anchor_states": states, "terminal": len(recs), "sealed": len(sealed)}
}

// floorText (release 087): "floor=F;expected_level=[..];" for a REC-3 comparison, "" otherwise.
func (f fenceAnchor) floorText() string {
	if f.Floor == nil {
		return ""
	}
	return fmt.Sprintf("floor=%d;expected_level=%s;", f.Floor.Floor, vecString(f.Floor.Expected))
}

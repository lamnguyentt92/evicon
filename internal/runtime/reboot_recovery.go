package runtime

// Release 087 (PREREG_extra_087.md item B, reviewer comment M3; EVICON_087_DESIGN.md section 2): the reboot
// recovery, `evicon-authority -reboot-recovery`, off by default. Without the flag nothing below runs and the
// authority behaves exactly as release 086.
//
// The cutoff firmware keeps its cutoff, its claim mark and its tombstones in flash and its receipt ring and its
// level in RAM. After a reboot it restores the claim mark and treats every sequence at or below it as possibly
// started (the "claim floor" F): such an identity is never executed again and never certified. Release 086 then
// loses liveness: every new identity of the client inside the claimed range is refused; and a complete fence aborts
// as an evidence conflict, because the reported level counts only the effects since the boot. The owner of the
// boot-report firmware (firmware/zephyr-boot) and its gateway report F with their answers ("claim_floor").
//
//   REC-1  learn the floor: every owner answer that carries claim_floor (dispatch replies of every status, receipt
//          reads, stops, fence answers) raises the domain's durable floor record (journal table owner_floor; it never
//          falls). Liveness only.
//   REC-2  sequence advance: a new identity's owner sequence is the client's sequence plus a durable offset; when that
//          is at or below the known floor, the offset grows so that the identity lands at floor + 1. Owner sequences
//          stay strictly increasing (the monotonicity check runs on them, and on the client's sequences as before);
//          the intent hash is computed over the owner sequence; the client's sequence is recorded so that a replay of
//          the same request maps to the same stored intent. Liveness only: a stale floor makes an identity refused by
//          the owner, never executed twice.
//   REC-3  the anchor of a complete fence after a reboot: with claim_floor F in the answer, the reported level is
//          compared with the sum of the values of the identities APPLIED with F < sequence <= cutoff (the effects the
//          owner's level counts since its boot), not with the whole anchor. A match commits the evidence anchor of
//          release 086 (never the reported level); a mismatch is an evidence conflict, aborted as in release 086.
//          Safety rests on FC, DR and the durable cutoff, as for the evidence anchor of release 086; the check is the
//          detector (valid when the owner's level counts exactly the identities above its floor applied since its
//          boot). With F = 0 the comparison is the one of release 086. Additive histories only; otherwise 086.
//   REC-4  window accounting: an identity that a complete fence answer listed UNCERTAIN (rule R5', per-identity
//          intake) lies at or below a cutoff the owner installed durably before answering, so it cannot take effect
//          any more; it no longer counts toward the window of unresolved commands. Its reservation (budget) stays in
//          the belief: the bound argument does not use the window.
//
// With -fence-anchor owner-state the steps REC-1, REC-2 and REC-4 run and the reported state is adopted (the
// negative control of plan 087, arm (i)): after a reboot it adopts the reset level.

import (
	"encoding/json"
	"errors"
	"fmt"
	"strconv"

	"evicon/internal/journal"
	"evicon/internal/protocol"
	"evicon/internal/trace"
)

// claimFloorOf returns the claim_floor of an owner answer body (any JSON object), if it carries one.
func claimFloorOf(body []byte) (uint64, bool) {
	var x struct {
		ClaimFloor *json.Number `json:"claim_floor"`
	}
	if len(body) == 0 || json.Unmarshal(body, &x) != nil || x.ClaimFloor == nil {
		return 0, false
	}
	v, err := strconv.ParseUint(x.ClaimFloor.String(), 10, 64)
	if err != nil {
		return 0, false
	}
	return v, true
}

// ownerReport is the dispatcher's OwnerReport hook under the recovery (REC-1).
func (a *Authority) ownerReport(domainID string, status int, body []byte) {
	if f, ok := claimFloorOf(body); ok {
		a.noteOwnerFloor(domainID, f, fmt.Sprintf("DISPATCH_REPLY_%d", status))
	}
}

// noteOwnerFloorBody takes in the claim floor of a receipt read, stop or fence answer (REC-1).
func (a *Authority) noteOwnerFloorBody(domainID string, body []byte, source string) {
	if !a.cfg.RebootRecovery {
		return
	}
	if f, ok := claimFloorOf(body); ok {
		a.noteOwnerFloor(domainID, f, source)
	}
}

// noteOwnerFloor raises the durable floor record (REC-1) and traces OWNER_FLOOR when it changes.
func (a *Authority) noteOwnerFloor(domainID string, floor uint64, source string) {
	if !a.cfg.RebootRecovery || domainID != a.cfg.Domain.ID {
		return
	}
	prev, raised, err := a.cfg.Store.RaiseOwnerFloor(domainID, floor)
	if err != nil {
		if a.cfg.Trace != nil {
			_ = a.cfg.Trace.Write(trace.Event{DomainID: domainID, Event: "OWNER_FLOOR_ERROR", Reason: truncate(err.Error(), 200)})
		}
		return
	}
	if raised && a.cfg.Trace != nil {
		_ = a.cfg.Trace.Write(trace.Event{DomainID: domainID, Event: "OWNER_FLOOR", Policy: string(a.cfg.Policy),
			Reason: fmt.Sprintf("floor=%d;previous=%d;source=%s", floor, prev, source)})
	}
}

// recMapReplay (REC-2): for a request whose identity was admitted under the recovery with the same client sequence,
// put the stored owner sequence in the request (and its intent hash), so that the release-061 replay check compares
// like with like. Returns the client sequence the request carried.
func (a *Authority) recMapReplay(req *protocol.CommandRequest, dim int) (uint64, error) {
	clientSeq := req.Sequence
	c, owner, err := a.cfg.Store.ClientSequence(req.DomainID, req.CommandID)
	if errors.Is(err, journal.ErrNotFound) {
		return clientSeq, nil
	}
	if err != nil {
		return clientSeq, err
	}
	if c == req.Sequence && owner != req.Sequence {
		req.Sequence, req.IntentHash = owner, ""
		if err := req.ValidateAndFillHash(dim); err != nil {
			return clientSeq, err
		}
	}
	return clientSeq, nil
}

// recAdvance (REC-2): the owner sequence of a new identity. It returns the owner sequence written into req (with its
// intent hash) and whether the client's sequence is out of order (not above every client sequence recorded).
func (a *Authority) recAdvance(req *protocol.CommandRequest, dim int) (stale bool, err error) {
	clientSeq := req.Sequence
	maxClient, err := a.cfg.Store.MaxClientSequence(req.DomainID)
	if err != nil {
		return false, err
	}
	if maxClient > 0 && clientSeq <= maxClient {
		return true, nil
	}
	of, err := a.cfg.Store.GetOwnerFloor(req.DomainID)
	if err != nil {
		return false, err
	}
	owner := clientSeq + of.Offset
	if of.Known && owner <= of.Floor {
		off := of.Floor + 1 - clientSeq
		if err := a.cfg.Store.RaiseSequenceOffset(req.DomainID, off); err != nil {
			return false, err
		}
		if a.cfg.Trace != nil {
			_ = a.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "SEQUENCE_ADVANCED", Policy: string(a.cfg.Policy),
				Reason: fmt.Sprintf("client=%d;owner=%d;floor=%d;offset=%d;previous_offset=%d", clientSeq, of.Floor+1, of.Floor, off, of.Offset)})
		}
		owner = of.Floor + 1
	}
	if owner != req.Sequence {
		req.Sequence, req.IntentHash = owner, ""
		if err := req.ValidateAndFillHash(dim); err != nil {
			return false, err
		}
	}
	return false, nil
}

// floorCheck (REC-3) is the comparison of a complete fence answer under the recovery. ok = false when the check does
// not apply (no claim floor in the answer, a non-additive history, an identity without terminal knowledge): the caller
// then keeps the comparison of release 086.
type floorCheck struct {
	Floor    uint64
	Expected []int64
	Match    bool
}

func (a *Authority) recFloorCheck(d journal.Domain, rc protocol.BarrierReceipt, batch []journal.CommandRecord) (floorCheck, bool, error) {
	if !a.cfg.RebootRecovery || rc.ClaimFloor == nil {
		return floorCheck{}, false, nil
	}
	f := *rc.ClaimFloor
	fc := floorCheck{Floor: f}
	// the identities F < sequence <= cutoff: those at or below the domain's cutoff from the journal (terminal: a cutoff
	// moves only on a complete fence or a fully terminal compaction), the others with their knowledge after this intake
	byID := map[string]journal.CommandRecord{}
	for _, c := range batch {
		byID[c.CommandID] = c
	}
	rng, err := a.cfg.Store.ListCommandsInRange(d.ID, f, rc.CutoffSequence)
	if err != nil {
		return fc, false, err
	}
	sel := make([]journal.CommandRecord, 0, len(rng))
	for _, c := range rng {
		if b, ok := byID[c.CommandID]; ok {
			c = b
		}
		if !terminalStatus(c.Status) {
			return fc, false, nil
		}
		sel = append(sel, c)
	}
	sum, err := journal.AdditiveSum(sel, d.Dimension)
	if errors.Is(err, journal.ErrNotAdditive) {
		return fc, false, nil
	}
	if err != nil {
		return fc, false, err
	}
	fc.Expected = sum
	fc.Match = len(rc.State) == len(sum)
	for i := range sum {
		if fc.Match && rc.State[i] != sum[i] {
			fc.Match = false
		}
	}
	return fc, true, nil
}

// recWindowExcluded (REC-4): an identity a complete fence answer listed UNCERTAIN under rule R5' leaves the window.
func (a *Authority) recWindowExcluded(id string) bool {
	return a.cfg.RebootRecovery && a.isFenceUncertain(id)
}

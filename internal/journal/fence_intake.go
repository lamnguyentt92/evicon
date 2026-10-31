package journal

import (
	"fmt"

	"evicon/internal/sqlitecgo"
)

// CommitBarrierPerIdentity (release 084, rule R5': -fence-intake per-identity) ends a PENDING fence
// whose owner answer is complete (premise FC) but lists identities the owner cannot certify (UNCERTAIN,
// e.g. crash orphans). Within one transaction it
//   - takes in every listed terminal answer (APPLIED/STOPPED) exactly as CommitBarrierLifecycleSeal does;
//   - seals every admitted, non-terminal identity at or below the cutoff that the answer does not list
//     as STOPPED (evidence kind BARRIER_SEALED): under FC it was never claimed, and the installed cutoff
//     keeps it from ever taking effect;
//   - leaves every identity listed UNCERTAIN untouched (pending, its reservation kept);
//   - anchors nothing: the domain's anchor and cutoff stay as they are, because an identity at or below
//     the cutoff (an UNCERTAIN one) is still unresolved; the belief keeps counting every command above the
//     old cutoff by its status;
//   - marks the lifecycle PARTIAL, which un-pauses admission like COMPLETE and gives the token a fixed
//     meaning (BeginBarrier answers ErrBarrierPartial to a retry).
//
// Returns the new revision and the sealed identities (in sequence order).
func (s *Store) CommitBarrierPerIdentity(domainID, token string, generation, cutoff uint64, ownerState []int64, ownerVersion uint64, receipts []ReceiptRecord, uncertain []string) (uint64, []string, error) {
	statej, _ := js(ownerState)
	t := now()
	var newRev uint64
	var sealed []string
	err := s.db.WithTx(func(tx *sqlitecgo.Tx) error {
		br, err := tx.Query(`SELECT cutoff_sequence,status FROM authority_barrier_history WHERE domain_id=? AND generation=? AND token=?`, domainID, generation, token)
		if err != nil {
			return err
		}
		if len(br) == 0 {
			return fmt.Errorf("missing authority barrier lifecycle")
		}
		bc, _ := sqlitecgo.Int64At(br[0], 0)
		bs, _ := sqlitecgo.TextAt(br[0], 1)
		if uint64(bc) != cutoff {
			return fmt.Errorf("barrier lifecycle mismatch")
		}
		switch bs {
		case "COMPLETE":
			return ErrBarrierAlreadyComplete
		case "PARTIAL":
			return ErrBarrierPartial
		case "ABORTED":
			return ErrBarrierAborted
		}
		mx, err := tx.Query(`SELECT MAX(sequence) FROM commands WHERE domain_id=? AND decision='ADMITTED'`, domainID)
		if err != nil {
			return err
		}
		var maxSeq uint64
		if len(mx) > 0 && mx[0][0].Kind == sqlitecgo.Integer {
			v, _ := sqlitecgo.Int64At(mx[0], 0)
			maxSeq = uint64(v)
		}
		if maxSeq != cutoff {
			return fmt.Errorf("barrier cutoff %d no longer matches admitted frontier %d", cutoff, maxSeq)
		}
		dr, err := tx.Query(`SELECT generation,revision,cutoff_sequence FROM domains WHERE domain_id=?`, domainID)
		if err != nil {
			return err
		}
		if len(dr) == 0 {
			return ErrNotFound
		}
		g, _ := sqlitecgo.Int64At(dr[0], 0)
		rv, _ := sqlitecgo.Int64At(dr[0], 1)
		oldCut, _ := sqlitecgo.Int64At(dr[0], 2)
		if uint64(g) != generation {
			return fmt.Errorf("barrier generation mismatch")
		}
		if cutoff < uint64(oldCut) {
			return fmt.Errorf("barrier cutoff regression")
		}
		newRev = uint64(rv) + 1
		listed := map[string]bool{}
		for _, id := range uncertain {
			listed[id] = true
		}
		for _, rc := range receipts {
			if rc.Outcome != "APPLIED" && rc.Outcome != "STOPPED" {
				return fmt.Errorf("barrier returned non-terminal outcome %s", rc.Outcome)
			}
			if rc.ReceiptVersion == 0 || rc.OwnerVersion == 0 {
				return fmt.Errorf("barrier terminal receipt missing version")
			}
			if listed[rc.CommandID] {
				return fmt.Errorf("barrier lists %s twice (terminal and uncertain, or two terminal answers)", rc.CommandID)
			}
			listed[rc.CommandID] = true
			rows, er := tx.Query(`SELECT intent_hash,status FROM commands WHERE domain_id=? AND command_id=?`, domainID, rc.CommandID)
			if er != nil {
				return er
			}
			if len(rows) == 0 {
				continue
			}
			ih, _ := sqlitecgo.TextAt(rows[0], 0)
			old, _ := sqlitecgo.TextAt(rows[0], 1)
			if ih != rc.IntentHash {
				return ErrIdentityConflict
			}
			if (old == "APPLIED" || old == "STOPPED") && old != rc.Outcome {
				return fmt.Errorf("%w: %s %s -> %s", ErrTerminalReversal, rc.CommandID, old, rc.Outcome)
			}
			sj, _ := js(rc.State)
			if _, er = tx.Exec(`UPDATE commands SET status=?,updated_at=? WHERE domain_id=? AND command_id=?`, rc.Outcome, t, domainID, rc.CommandID); er != nil {
				return er
			}
			if _, er = tx.Exec(`UPDATE outbox SET state='DONE',last_error='',updated_at=? WHERE domain_id=? AND command_id=?`, t, domainID, rc.CommandID); er != nil {
				return er
			}
			if _, er = tx.Exec(`INSERT INTO observations(domain_id,command_id,intent_hash,outcome,owner_version,receipt_version,state_json,reason,evidence_kind,applied_revision,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)`, domainID, rc.CommandID, rc.IntentHash, rc.Outcome, rc.OwnerVersion, rc.ReceiptVersion, sj, "BARRIER_RECONCILIATION", "BARRIER", newRev, t); er != nil {
				return er
			}
		}
		rows, er := tx.Query(`SELECT command_id,intent_hash FROM commands WHERE domain_id=? AND decision='ADMITTED' AND sequence<=? AND status NOT IN ('APPLIED','STOPPED') ORDER BY sequence,command_id`, domainID, cutoff)
		if er != nil {
			return er
		}
		for _, r := range rows {
			cid, _ := sqlitecgo.TextAt(r, 0)
			ih, _ := sqlitecgo.TextAt(r, 1)
			if listed[cid] {
				continue // listed: terminal (taken in above) or UNCERTAIN (kept pending)
			}
			if _, er = tx.Exec(`UPDATE commands SET status='STOPPED',updated_at=? WHERE domain_id=? AND command_id=?`, t, domainID, cid); er != nil {
				return er
			}
			if _, er = tx.Exec(`UPDATE outbox SET state='DONE',last_error='',updated_at=? WHERE domain_id=? AND command_id=?`, t, domainID, cid); er != nil {
				return er
			}
			if _, er = tx.Exec(`INSERT INTO observations(domain_id,command_id,intent_hash,outcome,owner_version,receipt_version,state_json,reason,evidence_kind,applied_revision,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)`, domainID, cid, ih, "STOPPED", ownerVersion, ownerVersion, statej, "BARRIER_SEALED_UNCLAIMED", "BARRIER_SEALED", newRev, t); er != nil {
				return er
			}
			sealed = append(sealed, cid)
		}
		if _, err = tx.Exec(`UPDATE domains SET revision=?,updated_at=? WHERE domain_id=?`, newRev, t, domainID); err != nil {
			return err
		}
		_, err = tx.Exec(`UPDATE authority_barrier_history SET status='PARTIAL',owner_version=?,complete_revision=?,updated_at=? WHERE domain_id=? AND generation=? AND token=?`, ownerVersion, newRev, t, domainID, generation, token)
		return err
	})
	if err != nil {
		sealed = nil
	}
	return newRev, sealed, err
}

// BarrierStatusCounts (release 084) counts the fence lifecycles of a domain that completed with
// per-identity intake (PARTIAL) and that were aborted (ABORTED); for tests and audits.
func (s *Store) BarrierStatusCounts(domainID string) (partial, aborted int, err error) {
	rows, err := s.db.Query(`SELECT status,COUNT(*) FROM authority_barrier_history WHERE domain_id=? GROUP BY status`, domainID)
	if err != nil {
		return 0, 0, err
	}
	for _, r := range rows {
		st, _ := sqlitecgo.TextAt(r, 0)
		n, _ := sqlitecgo.Int64At(r, 1)
		switch st {
		case "PARTIAL":
			partial = int(n)
		case "ABORTED":
			aborted = int(n)
		}
	}
	return partial, aborted, nil
}

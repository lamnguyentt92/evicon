package journal

// Release 087 (PREREG_extra_087.md, item B; reviewer comment M3): the durable records of the reboot recovery
// (`evicon-authority -reboot-recovery`, off by default).
//
//   owner_floor       per domain: the highest claim floor an owner reported (the claim mark the owner restored at its
//                     last boot; no identity with a sequence at or below it takes effect any more; step REC-1) and
//                     the sequence offset the authority adds to a client's sequence so that a new identity lands above
//                     that floor (step REC-2). Both only ever grow.
//   client_sequences  per admitted identity under the recovery: the sequence the client sent and the owner sequence the
//                     authority stored, so that a replay of the same request maps to the same stored intent.
//
// The tables are created on first use only, so a journal of an authority without the recovery keeps exactly the
// schema of release 086.

import (
	"errors"
	"sync"

	"evicon/internal/domain"
	"evicon/internal/sqlitecgo"
)

// OwnerFloor is a domain's reboot-recovery record.
type OwnerFloor struct {
	Floor  uint64 // highest claim floor reported by the owner (0: none reported, or the owner's first boot)
	Offset uint64 // owner sequence = client sequence + Offset
	Known  bool   // a floor was reported at least once
}

var recTablesMu sync.Mutex

func (s *Store) ensureRecTables() error {
	recTablesMu.Lock()
	defer recTablesMu.Unlock()
	for _, stmt := range []string{
		`CREATE TABLE IF NOT EXISTS owner_floor (
            domain_id TEXT PRIMARY KEY,
            floor INTEGER NOT NULL,
            sequence_offset INTEGER NOT NULL DEFAULT 0,
            known INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )`,
		`CREATE TABLE IF NOT EXISTS client_sequences (
            domain_id TEXT NOT NULL,
            command_id TEXT NOT NULL,
            client_sequence INTEGER NOT NULL,
            owner_sequence INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(domain_id, command_id)
        )`,
	} {
		if _, err := s.db.Exec(stmt); err != nil {
			return err
		}
	}
	return nil
}

// GetOwnerFloor returns the domain's record (zero values when there is none).
func (s *Store) GetOwnerFloor(domainID string) (OwnerFloor, error) {
	if err := s.ensureRecTables(); err != nil {
		return OwnerFloor{}, err
	}
	rows, err := s.db.Query(`SELECT floor, sequence_offset, known FROM owner_floor WHERE domain_id=?`, domainID)
	if err != nil || len(rows) == 0 {
		return OwnerFloor{}, err
	}
	f, _ := sqlitecgo.Int64At(rows[0], 0)
	o, _ := sqlitecgo.Int64At(rows[0], 1)
	k, _ := sqlitecgo.Int64At(rows[0], 2)
	return OwnerFloor{Floor: uint64(f), Offset: uint64(o), Known: k != 0}, nil
}

// RaiseOwnerFloor records a floor the owner reported. It returns the floor before and whether the record changed (the
// floor grew, or it is the first report). A lower report than the recorded one is ignored (the owner's claim mark never
// falls, so a lower value is a stale answer).
func (s *Store) RaiseOwnerFloor(domainID string, floor uint64) (prev uint64, raised bool, err error) {
	if err = s.ensureRecTables(); err != nil {
		return 0, false, err
	}
	err = s.db.WithTx(func(tx *sqlitecgo.Tx) error {
		rows, er := tx.Query(`SELECT floor, known FROM owner_floor WHERE domain_id=?`, domainID)
		if er != nil {
			return er
		}
		if len(rows) == 0 {
			raised = true
			_, er = tx.Exec(`INSERT INTO owner_floor(domain_id,floor,sequence_offset,known,updated_at) VALUES(?,?,0,1,?)`, domainID, int64(floor), now())
			return er
		}
		f, _ := sqlitecgo.Int64At(rows[0], 0)
		k, _ := sqlitecgo.Int64At(rows[0], 1)
		prev = uint64(f)
		if floor > prev || k == 0 {
			if floor < prev {
				floor = prev
			}
			raised = true
			_, er = tx.Exec(`UPDATE owner_floor SET floor=?, known=1, updated_at=? WHERE domain_id=?`, int64(floor), now(), domainID)
		}
		return er
	})
	return prev, raised, err
}

// RaiseSequenceOffset sets the domain's sequence offset to offset if that is larger than the recorded one.
func (s *Store) RaiseSequenceOffset(domainID string, offset uint64) error {
	if err := s.ensureRecTables(); err != nil {
		return err
	}
	return s.db.WithTx(func(tx *sqlitecgo.Tx) error {
		rows, er := tx.Query(`SELECT sequence_offset FROM owner_floor WHERE domain_id=?`, domainID)
		if er != nil {
			return er
		}
		if len(rows) == 0 {
			_, er = tx.Exec(`INSERT INTO owner_floor(domain_id,floor,sequence_offset,known,updated_at) VALUES(?,0,?,0,?)`, domainID, int64(offset), now())
			return er
		}
		o, _ := sqlitecgo.Int64At(rows[0], 0)
		if offset > uint64(o) {
			_, er = tx.Exec(`UPDATE owner_floor SET sequence_offset=?, updated_at=? WHERE domain_id=?`, int64(offset), now(), domainID)
		}
		return er
	})
}

// RecordClientSequence keeps the client's sequence of an identity admitted under the recovery.
func (s *Store) RecordClientSequence(domainID, commandID string, client, owner uint64) error {
	if err := s.ensureRecTables(); err != nil {
		return err
	}
	_, err := s.db.Exec(`INSERT OR IGNORE INTO client_sequences(domain_id,command_id,client_sequence,owner_sequence,created_at) VALUES(?,?,?,?,?)`,
		domainID, commandID, int64(client), int64(owner), now())
	return err
}

// ClientSequence returns the client sequence recorded for an identity (ErrNotFound when none is recorded).
func (s *Store) ClientSequence(domainID, commandID string) (client, owner uint64, err error) {
	if err = s.ensureRecTables(); err != nil {
		return 0, 0, err
	}
	rows, err := s.db.Query(`SELECT client_sequence, owner_sequence FROM client_sequences WHERE domain_id=? AND command_id=?`, domainID, commandID)
	if err != nil {
		return 0, 0, err
	}
	if len(rows) == 0 {
		return 0, 0, ErrNotFound
	}
	c, _ := sqlitecgo.Int64At(rows[0], 0)
	o, _ := sqlitecgo.Int64At(rows[0], 1)
	return uint64(c), uint64(o), nil
}

// MaxClientSequence is the largest client sequence recorded for the domain (0 when none).
func (s *Store) MaxClientSequence(domainID string) (uint64, error) {
	if err := s.ensureRecTables(); err != nil {
		return 0, err
	}
	rows, err := s.db.Query(`SELECT MAX(client_sequence) FROM client_sequences WHERE domain_id=?`, domainID)
	if err != nil || len(rows) == 0 || rows[0][0].Kind == sqlitecgo.Null {
		return 0, err
	}
	v, err := sqlitecgo.Int64At(rows[0], 0)
	return uint64(v), err
}

// ListCommandsInRange returns the admitted commands with lo < sequence <= hi, in sequence order.
func (s *Store) ListCommandsInRange(domainID string, lo, hi uint64) ([]CommandRecord, error) {
	if hi <= lo {
		return nil, nil
	}
	cmds, err := s.ListCommandsForBelief(domainID, lo)
	if err != nil {
		return nil, err
	}
	out := cmds[:0]
	for _, c := range cmds {
		if c.Sequence <= hi {
			out = append(out, c)
		}
	}
	return out, nil
}

// ErrNotAdditive: a range holds an operation other than ADD (the level-floor relation of the reboot recovery is additive).
var ErrNotAdditive = errors.New("not an additive history")

// AdditiveSum adds the values of the commands whose status is APPLIED (dimension dim); ErrNotAdditive for any other
// operation.
func AdditiveSum(cmds []CommandRecord, dim int) ([]int64, error) {
	sum := make([]int64, dim)
	for _, c := range cmds {
		if c.Operation != domain.OpAdd {
			return nil, ErrNotAdditive
		}
		if c.Status != "APPLIED" {
			continue
		}
		for i := 0; i < dim && i < len(c.Value); i++ {
			sum[i] += c.Value[i]
		}
	}
	return sum, nil
}

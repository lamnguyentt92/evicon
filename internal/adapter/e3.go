package adapter

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"evicon/internal/domain"
	"evicon/internal/fault"
	"evicon/internal/jobs"
	"evicon/internal/protocol"
	"evicon/internal/sqlitecgo"
	"evicon/internal/trace"
)

var ErrIdentityConflict = errors.New("identity conflict")

type E3Config struct {
	DBPath         string
	DomainID       string
	Generation     uint64
	InstalledEpoch uint64
	InitialState   []int64
	Bound          domain.HardBound
	Faults         *fault.Controller
	Trace          *trace.Writer
	Jobs           bool
}

type E3Owner struct {
	db  *sqlitecgo.DB
	cfg E3Config
}

func OpenE3(cfg E3Config) (*E3Owner, error) {
	if err := os.MkdirAll(filepath.Dir(cfg.DBPath), 0o755); err != nil {
		return nil, err
	}
	db, err := sqlitecgo.Open(cfg.DBPath)
	if err != nil {
		return nil, err
	}
	o := &E3Owner{db: db, cfg: cfg}
	if err = o.configure(); err != nil {
		db.Close()
		return nil, err
	}
	if err = o.migrate(); err != nil {
		db.Close()
		return nil, err
	}
	if err = jobs.Configure(o.db, cfg.Jobs); err != nil {
		o.Close()
		return nil, err
	}
	if err = o.ensureDomain(); err != nil {
		db.Close()
		return nil, err
	}
	return o, nil
}
func (o *E3Owner) Close() error { return o.db.Close() }
func (o *E3Owner) configure() error {
	if _, e := o.db.Exec("PRAGMA journal_mode=WAL"); e != nil {
		return e
	}
	if _, e := o.db.Exec("PRAGMA synchronous=FULL"); e != nil {
		return e
	}
	if _, e := o.db.Exec("PRAGMA foreign_keys=ON"); e != nil {
		return e
	}
	return nil
}
func (o *E3Owner) migrate() error {
	stmts := []string{
		`CREATE TABLE IF NOT EXISTS domains(domain_id TEXT PRIMARY KEY,generation INTEGER NOT NULL,installed_epoch INTEGER NOT NULL,set_frontier INTEGER NOT NULL,state_json TEXT NOT NULL,component_min_json TEXT NOT NULL,component_max_json TEXT NOT NULL,sum_max INTEGER NULL,owner_version INTEGER NOT NULL,updated_at TEXT NOT NULL)`,
		`CREATE TABLE IF NOT EXISTS receipts(domain_id TEXT NOT NULL,command_id TEXT NOT NULL,intent_hash TEXT NOT NULL,outcome TEXT NOT NULL,owner_version INTEGER NOT NULL,receipt_version INTEGER NOT NULL,state_json TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(domain_id,command_id))`,
		`CREATE TABLE IF NOT EXISTS effects(id INTEGER PRIMARY KEY AUTOINCREMENT,domain_id TEXT NOT NULL,command_id TEXT NOT NULL,intent_hash TEXT NOT NULL,operation TEXT NOT NULL,value_json TEXT NOT NULL,state_before_json TEXT NOT NULL,state_after_json TEXT NOT NULL,owner_version INTEGER NOT NULL,created_at TEXT NOT NULL,UNIQUE(domain_id,command_id))`,
	}
	for _, s := range stmts {
		if _, e := o.db.Exec(s); e != nil {
			return e
		}
	}
	return nil
}
func (o *E3Owner) ensureDomain() error {
	statej, _ := json.Marshal(o.cfg.InitialState)
	minj, _ := json.Marshal(o.cfg.Bound.ComponentMin)
	maxj, _ := json.Marshal(o.cfg.Bound.ComponentMax)
	var sum any
	if o.cfg.Bound.SumMax != nil {
		sum = *o.cfg.Bound.SumMax
	}
	t := time.Now().UTC().Format(time.RFC3339Nano)
	return o.db.WithTx(func(tx *sqlitecgo.Tx) error {
		rows, e := tx.Query(`SELECT generation,installed_epoch,state_json,component_min_json,component_max_json,sum_max FROM domains WHERE domain_id=?`, o.cfg.DomainID)
		if e != nil {
			return e
		}
		if len(rows) > 0 {
			g, _ := sqlitecgo.Int64At(rows[0], 0)
			ep, _ := sqlitecgo.Int64At(rows[0], 1)
			storedState, _ := sqlitecgo.TextAt(rows[0], 2)
			storedMin, _ := sqlitecgo.TextAt(rows[0], 3)
			storedMax, _ := sqlitecgo.TextAt(rows[0], 4)
			storedSum := rows[0][5]
			var durableState []int64
			if e := json.Unmarshal([]byte(storedState), &durableState); e != nil {
				return e
			}
			sumMatches := (o.cfg.Bound.SumMax == nil && storedSum.Kind == sqlitecgo.Null) ||
				(o.cfg.Bound.SumMax != nil && storedSum.Kind == sqlitecgo.Integer && storedSum.I64 == *o.cfg.Bound.SumMax)
			if uint64(g) != o.cfg.Generation || uint64(ep) != o.cfg.InstalledEpoch || len(durableState) != len(o.cfg.InitialState) ||
				storedMin != string(minj) || storedMax != string(maxj) || !sumMatches {
				return fmt.Errorf("CONFIGURATION_CONFLICT: durable E3 domain differs from requested generation/epoch/dimension/bounds")
			}
			return nil
		}
		_, e = tx.Exec(`INSERT INTO domains(domain_id,generation,installed_epoch,set_frontier,state_json,component_min_json,component_max_json,sum_max,owner_version,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)`, o.cfg.DomainID, o.cfg.Generation, o.cfg.InstalledEpoch, 0, string(statej), string(minj), string(maxj), sum, 0, t)
		return e
	})
}
func (o *E3Owner) Apply(req protocol.CommandRequest) (protocol.Receipt, error) {
	if req.DomainID != o.cfg.DomainID {
		return protocol.Receipt{}, fmt.Errorf("unknown domain")
	}
	if err := req.ValidateAndFillHash(len(o.cfg.InitialState)); err != nil {
		return protocol.Receipt{}, err
	}
	var receipt protocol.Receipt
	replayed := false
	err := o.db.WithTx(func(tx *sqlitecgo.Tx) error {
		rows, e := tx.Query(`SELECT intent_hash,outcome,owner_version,receipt_version,state_json,reason FROM receipts WHERE domain_id=? AND command_id=?`, req.DomainID, req.CommandID)
		if e != nil {
			return e
		}
		if len(rows) > 0 {
			ih, _ := sqlitecgo.TextAt(rows[0], 0)
			if ih != req.IntentHash {
				return ErrIdentityConflict
			}
			out, _ := sqlitecgo.TextAt(rows[0], 1)
			ov, _ := sqlitecgo.Int64At(rows[0], 2)
			rv, _ := sqlitecgo.Int64At(rows[0], 3)
			sj, _ := sqlitecgo.TextAt(rows[0], 4)
			reason, _ := sqlitecgo.TextAt(rows[0], 5)
			var state []int64
			if e = json.Unmarshal([]byte(sj), &state); e != nil {
				return e
			}
			receipt = protocol.Receipt{SchemaVersion: 1, DomainID: req.DomainID, Generation: req.Generation, CommandID: req.CommandID, IntentHash: req.IntentHash, Outcome: out, OwnerVersion: uint64(ov), ReceiptVersion: uint64(rv), State: state, Reason: reason}
			replayed = true
			return nil
		}
		drows, e := tx.Query(`SELECT generation,installed_epoch,set_frontier,state_json,component_min_json,component_max_json,sum_max,owner_version FROM domains WHERE domain_id=?`, req.DomainID)
		if e != nil {
			return e
		}
		if len(drows) == 0 {
			return fmt.Errorf("domain not found")
		}
		r := drows[0]
		gen, _ := sqlitecgo.Int64At(r, 0)
		ep, _ := sqlitecgo.Int64At(r, 1)
		frontier, _ := sqlitecgo.Int64At(r, 2)
		sj, _ := sqlitecgo.TextAt(r, 3)
		minj, _ := sqlitecgo.TextAt(r, 4)
		maxj, _ := sqlitecgo.TextAt(r, 5)
		ov, _ := sqlitecgo.Int64At(r, 7)
		var state, mins, maxs []int64
		if e = json.Unmarshal([]byte(sj), &state); e != nil {
			return e
		}
		_ = json.Unmarshal([]byte(minj), &mins)
		_ = json.Unmarshal([]byte(maxj), &maxs)
		var sm *int64
		if r[6].Kind == sqlitecgo.Integer {
			v := r[6].I64
			sm = &v
		}
		b := domain.HardBound{ComponentMin: mins, ComponentMax: maxs, SumMax: sm}
		outcome := "APPLIED"
		reason := "ATOMIC_EFFECT_AND_RECEIPT"
		next := state
		if uint64(gen) != req.Generation || uint64(ep) != req.Epoch {
			outcome = "STOPPED"
			reason = "GENERATION_OR_EPOCH_MISMATCH"
		} else if req.Operation == domain.OpSet && req.Sequence <= uint64(frontier) {
			outcome = "STOPPED"
			reason = "STALE_SET_SEQUENCE"
		} else {
			next, e = domain.Apply(domain.State(state), req.DomainCommand())
			if e != nil {
				return e
			}
			ok, e := b.Check(next)
			if e != nil {
				return e
			}
			if !ok {
				outcome = "STOPPED"
				reason = "ENDPOINT_BOUND_REJECT"
				next = state
			}
		}
		nextVersion := uint64(ov) + 1
		t := time.Now().UTC().Format(time.RFC3339Nano)
		nextj, _ := json.Marshal(next)
		if outcome == "APPLIED" {
			beforej, _ := json.Marshal(state)
			valuej, _ := json.Marshal(req.Value)
			nextFrontier := frontier
			if req.Operation == domain.OpSet {
				nextFrontier = int64(req.Sequence)
			}
			if _, e = tx.Exec(`UPDATE domains SET state_json=?,set_frontier=?,owner_version=?,updated_at=? WHERE domain_id=?`, string(nextj), nextFrontier, nextVersion, t, req.DomainID); e != nil {
				return e
			}
			if _, e = tx.Exec(`INSERT INTO effects(domain_id,command_id,intent_hash,operation,value_json,state_before_json,state_after_json,owner_version,created_at) VALUES(?,?,?,?,?,?,?,?,?)`, req.DomainID, req.CommandID, req.IntentHash, string(req.Operation), string(valuej), string(beforej), string(nextj), nextVersion, t); e != nil {
				return e
			}
			if o.cfg.Jobs {
				if e = jobs.Enqueue(tx, req, t); e != nil {
					return e
				}
			}
		} else {
			if _, e = tx.Exec(`UPDATE domains SET owner_version=?,updated_at=? WHERE domain_id=?`, nextVersion, t, req.DomainID); e != nil {
				return e
			}
		}
		if _, e = tx.Exec(`INSERT INTO receipts(domain_id,command_id,intent_hash,outcome,owner_version,receipt_version,state_json,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?)`, req.DomainID, req.CommandID, req.IntentHash, outcome, nextVersion, 1, string(nextj), reason, t); e != nil {
			return e
		}
		if o.cfg.Trace != nil {
			_ = o.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "E3_TX_PRECOMMIT", Reason: outcome})
		}
		if o.cfg.Faults != nil {
			o.cfg.Faults.Hit("BEFORE_E3_COMMIT", req.CommandID)
		}
		receipt = protocol.Receipt{SchemaVersion: 1, DomainID: req.DomainID, Generation: req.Generation, CommandID: req.CommandID, IntentHash: req.IntentHash, Outcome: outcome, OwnerVersion: nextVersion, ReceiptVersion: 1, State: append([]int64(nil), next...), Reason: reason}
		return nil
	})
	if err != nil {
		return protocol.Receipt{}, err
	}
	if o.cfg.Trace != nil {
		event := "E3_TX_COMMITTED"
		if replayed {
			event = "IDEMPOTENT_RECEIPT_REPLAY"
		}
		_ = o.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: event, Reason: receipt.Outcome})
	}
	if !replayed && o.cfg.Faults != nil {
		o.cfg.Faults.Hit("AFTER_E3_COMMIT_BEFORE_REPLY", req.CommandID)
	}
	return receipt, nil
}
func (o *E3Owner) GetReceipt(domainID, commandID string) (protocol.Receipt, error) {
	rows, e := o.db.Query(`SELECT intent_hash,outcome,owner_version,receipt_version,state_json,reason FROM receipts WHERE domain_id=? AND command_id=?`, domainID, commandID)
	if e != nil {
		return protocol.Receipt{}, e
	}
	if len(rows) == 0 {
		return protocol.Receipt{}, os.ErrNotExist
	}
	r := rows[0]
	ih, _ := sqlitecgo.TextAt(r, 0)
	out, _ := sqlitecgo.TextAt(r, 1)
	ov, _ := sqlitecgo.Int64At(r, 2)
	rv, _ := sqlitecgo.Int64At(r, 3)
	sj, _ := sqlitecgo.TextAt(r, 4)
	reason, _ := sqlitecgo.TextAt(r, 5)
	var state []int64
	_ = json.Unmarshal([]byte(sj), &state)
	return protocol.Receipt{SchemaVersion: 1, DomainID: domainID, Generation: o.cfg.Generation, CommandID: commandID, IntentHash: ih, Outcome: out, OwnerVersion: uint64(ov), ReceiptVersion: uint64(rv), State: state, Reason: reason}, nil
}
func (o *E3Owner) ReadState() (state []int64, ownerVersion uint64, cutoff uint64, err error) {
	rows, e := o.db.Query(`SELECT state_json,owner_version FROM domains WHERE domain_id=?`, o.cfg.DomainID)
	if e != nil || len(rows) == 0 {
		return nil, 0, 0, e
	}
	sj, _ := sqlitecgo.TextAt(rows[0], 0)
	ov, _ := sqlitecgo.Int64At(rows[0], 1)
	if e = json.Unmarshal([]byte(sj), &state); e != nil {
		return nil, 0, 0, e
	}
	// E3 has no E2-style barrier cutoff. Keep the field at zero while
	// reporting the durable owner_version from the state owner.
	return state, uint64(ov), 0, nil
}

func (o *E3Owner) Snapshot() (state []int64, effects int64, receipts int64, err error) {
	rows, e := o.db.Query(`SELECT state_json FROM domains WHERE domain_id=?`, o.cfg.DomainID)
	if e != nil || len(rows) == 0 {
		return nil, 0, 0, e
	}
	sj, _ := sqlitecgo.TextAt(rows[0], 0)
	if e = json.Unmarshal([]byte(sj), &state); e != nil {
		return nil, 0, 0, e
	}
	rows, e = o.db.Query(`SELECT COUNT(*) FROM effects WHERE domain_id=?`, o.cfg.DomainID)
	if e != nil {
		return nil, 0, 0, e
	}
	effects, _ = sqlitecgo.Int64At(rows[0], 0)
	rows, e = o.db.Query(`SELECT COUNT(*) FROM receipts WHERE domain_id=?`, o.cfg.DomainID)
	if e != nil {
		return nil, 0, 0, e
	}
	receipts, _ = sqlitecgo.Int64At(rows[0], 0)
	return
}

// ForceStop records a terminal STOPPED receipt without applying an effect.
// It is used only by the controlled pilot endpoint schedule to model a
// predetermined backend validation/service rejection that is independent of
// the evidence-selection method. The normal runtime never calls this method.
func (o *E3Owner) ForceStop(req protocol.CommandRequest, reason string) (protocol.Receipt, error) {
	if req.DomainID != o.cfg.DomainID {
		return protocol.Receipt{}, fmt.Errorf("unknown domain")
	}
	if err := req.ValidateAndFillHash(len(o.cfg.InitialState)); err != nil {
		return protocol.Receipt{}, err
	}
	var receipt protocol.Receipt
	err := o.db.WithTx(func(tx *sqlitecgo.Tx) error {
		rows, e := tx.Query(`SELECT intent_hash,outcome,owner_version,receipt_version,state_json,reason FROM receipts WHERE domain_id=? AND command_id=?`, req.DomainID, req.CommandID)
		if e != nil {
			return e
		}
		if len(rows) > 0 {
			ih, _ := sqlitecgo.TextAt(rows[0], 0)
			if ih != req.IntentHash {
				return ErrIdentityConflict
			}
			out, _ := sqlitecgo.TextAt(rows[0], 1)
			ov, _ := sqlitecgo.Int64At(rows[0], 2)
			rv, _ := sqlitecgo.Int64At(rows[0], 3)
			sj, _ := sqlitecgo.TextAt(rows[0], 4)
			rs, _ := sqlitecgo.TextAt(rows[0], 5)
			var state []int64
			if e = json.Unmarshal([]byte(sj), &state); e != nil {
				return e
			}
			receipt = protocol.Receipt{SchemaVersion: 1, DomainID: req.DomainID, Generation: req.Generation, CommandID: req.CommandID, IntentHash: req.IntentHash, Outcome: out, OwnerVersion: uint64(ov), ReceiptVersion: uint64(rv), State: state, Reason: rs}
			return nil
		}
		drows, e := tx.Query(`SELECT generation,installed_epoch,state_json,owner_version FROM domains WHERE domain_id=?`, req.DomainID)
		if e != nil {
			return e
		}
		if len(drows) == 0 {
			return fmt.Errorf("domain not found")
		}
		gen, _ := sqlitecgo.Int64At(drows[0], 0)
		ep, _ := sqlitecgo.Int64At(drows[0], 1)
		sj, _ := sqlitecgo.TextAt(drows[0], 2)
		ov, _ := sqlitecgo.Int64At(drows[0], 3)
		if uint64(gen) != req.Generation || uint64(ep) != req.Epoch {
			return fmt.Errorf("pilot force-stop generation/epoch mismatch")
		}
		var state []int64
		if e = json.Unmarshal([]byte(sj), &state); e != nil {
			return e
		}
		nextVersion := uint64(ov) + 1
		t := time.Now().UTC().Format(time.RFC3339Nano)
		if _, e = tx.Exec(`UPDATE domains SET owner_version=?,updated_at=? WHERE domain_id=?`, nextVersion, t, req.DomainID); e != nil {
			return e
		}
		if _, e = tx.Exec(`INSERT INTO receipts(domain_id,command_id,intent_hash,outcome,owner_version,receipt_version,state_json,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?)`, req.DomainID, req.CommandID, req.IntentHash, "STOPPED", nextVersion, 1, sj, reason, t); e != nil {
			return e
		}
		receipt = protocol.Receipt{SchemaVersion: 1, DomainID: req.DomainID, Generation: req.Generation, CommandID: req.CommandID, IntentHash: req.IntentHash, Outcome: "STOPPED", OwnerVersion: nextVersion, ReceiptVersion: 1, State: append([]int64(nil), state...), Reason: reason}
		return nil
	})
	if err != nil {
		return protocol.Receipt{}, err
	}
	if o.cfg.Trace != nil {
		_ = o.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "PILOT_FORCED_STOPPED", Reason: reason})
	}
	return receipt, nil
}

func (o *E3Owner) JobStore() *jobs.Store { return jobs.New(o.db, o.cfg.DomainID, o.cfg.Faults) }

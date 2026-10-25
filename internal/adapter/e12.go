package adapter

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"

	"evicon/internal/domain"
	"evicon/internal/fault"
	"evicon/internal/jobs"
	"evicon/internal/protocol"
	"evicon/internal/sqlitecgo"
	"evicon/internal/trace"
)

type E12Config struct {
	Profile        string
	ClaimDBPath    string
	EffectsDBPath  string
	DomainID       string
	Generation     uint64
	InstalledEpoch uint64
	InitialState   []int64
	Bound          domain.HardBound
	EffectDelay    time.Duration
	Faults         *fault.Controller
	Trace          *trace.Writer
	Jobs           bool
}

type E12Owner struct {
	mu      sync.Mutex
	claims  *sqlitecgo.DB
	effects *sqlitecgo.DB
	cfg     E12Config
}

func OpenE12(cfg E12Config) (*E12Owner, error) {
	if cfg.Profile != "E1" && cfg.Profile != "E2" {
		return nil, fmt.Errorf("E12 owner requires profile E1 or E2")
	}
	for _, p := range []string{cfg.ClaimDBPath, cfg.EffectsDBPath} {
		if p == "" {
			return nil, fmt.Errorf("claim/effects db paths are required")
		}
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			return nil, err
		}
	}
	cdb, err := sqlitecgo.Open(cfg.ClaimDBPath)
	if err != nil {
		return nil, err
	}
	edb, err := sqlitecgo.Open(cfg.EffectsDBPath)
	if err != nil {
		cdb.Close()
		return nil, err
	}
	o := &E12Owner{claims: cdb, effects: edb, cfg: cfg}
	if err = o.configure(); err != nil {
		o.Close()
		return nil, err
	}
	if err = o.migrate(); err != nil {
		o.Close()
		return nil, err
	}
	if err = jobs.Configure(o.effects, cfg.Jobs); err != nil {
		o.Close()
		return nil, err
	}
	if err = o.ensureDomain(); err != nil {
		o.Close()
		return nil, err
	}
	return o, nil
}

func (o *E12Owner) Close() error {
	var first error
	if o.claims != nil {
		if err := o.claims.Close(); err != nil {
			first = err
		}
	}
	if o.effects != nil {
		if err := o.effects.Close(); err != nil && first == nil {
			first = err
		}
	}
	return first
}

func configureSQLite(db *sqlitecgo.DB) error {
	if _, e := db.Exec("PRAGMA journal_mode=WAL"); e != nil {
		return e
	}
	if _, e := db.Exec("PRAGMA synchronous=FULL"); e != nil {
		return e
	}
	if _, e := db.Exec("PRAGMA foreign_keys=ON"); e != nil {
		return e
	}
	return nil
}
func (o *E12Owner) configure() error {
	if err := configureSQLite(o.claims); err != nil {
		return err
	}
	return configureSQLite(o.effects)
}

func (o *E12Owner) migrate() error {
	claimStmts := []string{
		`CREATE TABLE IF NOT EXISTS claims(domain_id TEXT NOT NULL,command_id TEXT NOT NULL,intent_hash TEXT NOT NULL,sequence INTEGER NOT NULL,status TEXT NOT NULL,receipt_version INTEGER NOT NULL,owner_version INTEGER NOT NULL,state_json TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(domain_id,command_id))`,
		`CREATE TABLE IF NOT EXISTS barriers(domain_id TEXT NOT NULL,token TEXT NOT NULL,cutoff_sequence INTEGER NOT NULL,status TEXT NOT NULL,state_json TEXT NOT NULL,owner_version INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(domain_id,token))`,
	}
	effectStmts := []string{
		`CREATE TABLE IF NOT EXISTS domains(domain_id TEXT PRIMARY KEY,generation INTEGER NOT NULL,installed_epoch INTEGER NOT NULL,set_frontier INTEGER NOT NULL,cutoff_sequence INTEGER NOT NULL,state_json TEXT NOT NULL,component_min_json TEXT NOT NULL,component_max_json TEXT NOT NULL,sum_max INTEGER NULL,owner_version INTEGER NOT NULL,updated_at TEXT NOT NULL)`,
		`CREATE TABLE IF NOT EXISTS effects(id INTEGER PRIMARY KEY AUTOINCREMENT,domain_id TEXT NOT NULL,command_id TEXT NOT NULL,intent_hash TEXT NOT NULL,operation TEXT NOT NULL,value_json TEXT NOT NULL,state_before_json TEXT NOT NULL,state_after_json TEXT NOT NULL,owner_version INTEGER NOT NULL,created_at TEXT NOT NULL,UNIQUE(domain_id,command_id))`,
	}
	for _, s := range claimStmts {
		if _, e := o.claims.Exec(s); e != nil {
			return e
		}
	}
	for _, s := range effectStmts {
		if _, e := o.effects.Exec(s); e != nil {
			return e
		}
	}
	return nil
}

func (o *E12Owner) ensureDomain() error {
	statej, _ := json.Marshal(o.cfg.InitialState)
	minj, _ := json.Marshal(o.cfg.Bound.ComponentMin)
	maxj, _ := json.Marshal(o.cfg.Bound.ComponentMax)
	var sum any
	if o.cfg.Bound.SumMax != nil {
		sum = *o.cfg.Bound.SumMax
	}
	t := time.Now().UTC().Format(time.RFC3339Nano)
	return o.effects.WithTx(func(tx *sqlitecgo.Tx) error {
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
				return fmt.Errorf("CONFIGURATION_CONFLICT: durable %s domain differs from requested generation/epoch/dimension/bounds", o.cfg.Profile)
			}
			return nil
		}
		_, e = tx.Exec(`INSERT INTO domains(domain_id,generation,installed_epoch,set_frontier,cutoff_sequence,state_json,component_min_json,component_max_json,sum_max,owner_version,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)`, o.cfg.DomainID, o.cfg.Generation, o.cfg.InstalledEpoch, 0, 0, string(statej), string(minj), string(maxj), sum, 0, t)
		return e
	})
}

func (o *E12Owner) existingClaim(req protocol.CommandRequest) (protocol.Receipt, bool, error) {
	rows, e := o.claims.Query(`SELECT intent_hash,status,receipt_version,owner_version,state_json,reason FROM claims WHERE domain_id=? AND command_id=?`, req.DomainID, req.CommandID)
	if e != nil {
		return protocol.Receipt{}, false, e
	}
	if len(rows) == 0 {
		return protocol.Receipt{}, false, nil
	}
	ih, _ := sqlitecgo.TextAt(rows[0], 0)
	if ih != req.IntentHash {
		return protocol.Receipt{}, true, ErrIdentityConflict
	}
	st, _ := sqlitecgo.TextAt(rows[0], 1)
	rv, _ := sqlitecgo.Int64At(rows[0], 2)
	ov, _ := sqlitecgo.Int64At(rows[0], 3)
	sj, _ := sqlitecgo.TextAt(rows[0], 4)
	reason, _ := sqlitecgo.TextAt(rows[0], 5)
	var state []int64
	_ = json.Unmarshal([]byte(sj), &state)
	if st == "APPLIED" || st == "STOPPED" {
		return protocol.Receipt{SchemaVersion: 1, DomainID: req.DomainID, Generation: req.Generation, CommandID: req.CommandID, IntentHash: req.IntentHash, Outcome: st, OwnerVersion: uint64(ov), ReceiptVersion: uint64(rv), State: state, Reason: reason}, true, nil
	}
	return protocol.Receipt{SchemaVersion: 1, DomainID: req.DomainID, Generation: req.Generation, CommandID: req.CommandID, IntentHash: req.IntentHash, Outcome: "UNCERTAIN", OwnerVersion: uint64(ov), ReceiptVersion: uint64(rv), State: nil, Reason: "CLAIM_WITHOUT_TERMINAL_RECEIPT"}, true, nil
}

func (o *E12Owner) insertClaim(req protocol.CommandRequest) error {
	t := time.Now().UTC().Format(time.RFC3339Nano)
	_, e := o.claims.Exec(`INSERT INTO claims(domain_id,command_id,intent_hash,sequence,status,receipt_version,owner_version,state_json,reason,created_at,updated_at) VALUES(?,?,?,?,?,0,0,'[]','CLAIMED',?,?)`, req.DomainID, req.CommandID, req.IntentHash, req.Sequence, "CLAIMED", t, t)
	return e
}

func (o *E12Owner) writeTerminal(req protocol.CommandRequest, outcome, reason string, state []int64, ownerVersion uint64) (protocol.Receipt, error) {
	sj, _ := json.Marshal(state)
	t := time.Now().UTC().Format(time.RFC3339Nano)
	_, e := o.claims.Exec(`UPDATE claims SET status=?,receipt_version=1,owner_version=?,state_json=?,reason=?,updated_at=? WHERE domain_id=? AND command_id=?`, outcome, ownerVersion, string(sj), reason, t, req.DomainID, req.CommandID)
	if e != nil {
		return protocol.Receipt{}, e
	}
	return protocol.Receipt{SchemaVersion: 1, DomainID: req.DomainID, Generation: req.Generation, CommandID: req.CommandID, IntentHash: req.IntentHash, Outcome: outcome, OwnerVersion: ownerVersion, ReceiptVersion: 1, State: append([]int64(nil), state...), Reason: reason}, nil
}

func (o *E12Owner) Apply(req protocol.CommandRequest) (protocol.Receipt, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if req.DomainID != o.cfg.DomainID {
		return protocol.Receipt{}, fmt.Errorf("unknown domain")
	}
	if err := req.ValidateAndFillHash(len(o.cfg.InitialState)); err != nil {
		return protocol.Receipt{}, err
	}
	if rc, ok, e := o.existingClaim(req); ok {
		if e != nil {
			return protocol.Receipt{}, e
		}
		if o.cfg.Trace != nil {
			ev := "IDEMPOTENT_RECEIPT_REPLAY"
			if rc.Outcome == "UNCERTAIN" {
				ev = "CLAIM_REPLAY_UNCERTAIN"
			}
			_ = o.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: ev, Reason: rc.Outcome})
		}
		return rc, nil
	}
	if o.cfg.Profile == "E2" {
		rows, e := o.claims.Query(`SELECT cutoff_sequence FROM barriers WHERE domain_id=? AND status='INSTALLING' ORDER BY created_at DESC LIMIT 1`, req.DomainID)
		if e != nil {
			return protocol.Receipt{}, e
		}
		if len(rows) > 0 {
			co, _ := sqlitecgo.Int64At(rows[0], 0)
			if req.Sequence > uint64(co) {
				return protocol.Receipt{}, fmt.Errorf("barrier in progress")
			}
		}
	}
	if err := o.insertClaim(req); err != nil {
		return protocol.Receipt{}, err
	}
	if o.cfg.Trace != nil {
		_ = o.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "CLAIM_COMMITTED"})
	}
	if o.cfg.Faults != nil {
		o.cfg.Faults.Hit("AFTER_CLAIM_COMMIT_BEFORE_EFFECT", req.CommandID)
	}
	if o.cfg.EffectDelay > 0 {
		time.Sleep(o.cfg.EffectDelay)
	}

	var outcome, reason string
	var next []int64
	var ownerVersion uint64
	err := o.effects.WithTx(func(tx *sqlitecgo.Tx) error {
		rows, e := tx.Query(`SELECT generation,installed_epoch,set_frontier,cutoff_sequence,state_json,component_min_json,component_max_json,sum_max,owner_version FROM domains WHERE domain_id=?`, req.DomainID)
		if e != nil {
			return e
		}
		if len(rows) == 0 {
			return fmt.Errorf("domain not found")
		}
		r := rows[0]
		gen, _ := sqlitecgo.Int64At(r, 0)
		ep, _ := sqlitecgo.Int64At(r, 1)
		frontier, _ := sqlitecgo.Int64At(r, 2)
		cutoff, _ := sqlitecgo.Int64At(r, 3)
		sj, _ := sqlitecgo.TextAt(r, 4)
		minj, _ := sqlitecgo.TextAt(r, 5)
		maxj, _ := sqlitecgo.TextAt(r, 6)
		ov, _ := sqlitecgo.Int64At(r, 8)
		var state, mins, maxs []int64
		_ = json.Unmarshal([]byte(sj), &state)
		_ = json.Unmarshal([]byte(minj), &mins)
		_ = json.Unmarshal([]byte(maxj), &maxs)
		var sm *int64
		if r[7].Kind == sqlitecgo.Integer {
			v := r[7].I64
			sm = &v
		}
		b := domain.HardBound{ComponentMin: mins, ComponentMax: maxs, SumMax: sm}
		outcome = "APPLIED"
		reason = "E12_EFFECT_THEN_RECEIPT"
		next = append([]int64(nil), state...)
		if uint64(gen) != req.Generation || uint64(ep) != req.Epoch {
			outcome = "STOPPED"
			reason = "GENERATION_OR_EPOCH_MISMATCH"
		} else if req.Sequence <= uint64(cutoff) {
			outcome = "STOPPED"
			reason = "SEALED_BY_BARRIER"
		} else if req.Operation == domain.OpSet && req.Sequence <= uint64(frontier) {
			outcome = "STOPPED"
			reason = "STALE_SET_SEQUENCE"
		} else {
			var er error
			next, er = domain.Apply(domain.State(state), req.DomainCommand())
			if er != nil {
				return er
			}
			ok, er := b.Check(next)
			if er != nil {
				return er
			}
			if !ok {
				outcome = "STOPPED"
				reason = "ENDPOINT_BOUND_REJECT"
				next = state
			}
		}
		ownerVersion = uint64(ov) + 1
		t := time.Now().UTC().Format(time.RFC3339Nano)
		nextj, _ := json.Marshal(next)
		if outcome == "APPLIED" {
			beforej, _ := json.Marshal(state)
			valuej, _ := json.Marshal(req.Value)
			nf := frontier
			if req.Operation == domain.OpSet {
				nf = int64(req.Sequence)
			}
			if _, e = tx.Exec(`UPDATE domains SET state_json=?,set_frontier=?,owner_version=?,updated_at=? WHERE domain_id=?`, string(nextj), nf, ownerVersion, t, req.DomainID); e != nil {
				return e
			}
			if _, e = tx.Exec(`INSERT INTO effects(domain_id,command_id,intent_hash,operation,value_json,state_before_json,state_after_json,owner_version,created_at) VALUES(?,?,?,?,?,?,?,?,?)`, req.DomainID, req.CommandID, req.IntentHash, string(req.Operation), string(valuej), string(beforej), string(nextj), ownerVersion, t); e != nil {
				return e
			}
			if o.cfg.Jobs {
				if e = jobs.Enqueue(tx, req, t); e != nil {
					return e
				}
			}
		} else {
			if _, e = tx.Exec(`UPDATE domains SET owner_version=?,updated_at=? WHERE domain_id=?`, ownerVersion, t, req.DomainID); e != nil {
				return e
			}
		}
		return nil
	})
	if err != nil {
		return protocol.Receipt{}, err
	}
	if o.cfg.Trace != nil {
		_ = o.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "EFFECT_PHASE_COMMITTED", Reason: outcome})
	}
	if o.cfg.Faults != nil {
		o.cfg.Faults.Hit("AFTER_EFFECT_COMMIT_BEFORE_RECEIPT", req.CommandID)
	}
	rc, err := o.writeTerminal(req, outcome, reason, next, ownerVersion)
	if err != nil {
		return protocol.Receipt{}, err
	}
	if o.cfg.Trace != nil {
		_ = o.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "RECEIPT_COMMITTED", Reason: outcome})
	}
	if o.cfg.Faults != nil {
		o.cfg.Faults.Hit("AFTER_RECEIPT_COMMIT_BEFORE_REPLY", req.CommandID)
	}
	return rc, nil
}

func (o *E12Owner) GetReceipt(domainID, commandID string) (protocol.Receipt, error) {
	rows, e := o.claims.Query(`SELECT intent_hash,status,receipt_version,owner_version,state_json,reason FROM claims WHERE domain_id=? AND command_id=?`, domainID, commandID)
	if e != nil {
		return protocol.Receipt{}, e
	}
	if len(rows) == 0 {
		return protocol.Receipt{}, os.ErrNotExist
	}
	ih, _ := sqlitecgo.TextAt(rows[0], 0)
	st, _ := sqlitecgo.TextAt(rows[0], 1)
	rv, _ := sqlitecgo.Int64At(rows[0], 2)
	ov, _ := sqlitecgo.Int64At(rows[0], 3)
	sj, _ := sqlitecgo.TextAt(rows[0], 4)
	reason, _ := sqlitecgo.TextAt(rows[0], 5)
	var state []int64
	_ = json.Unmarshal([]byte(sj), &state)
	if st != "APPLIED" && st != "STOPPED" {
		st = "UNCERTAIN"
		state = nil
		reason = "CLAIM_WITHOUT_TERMINAL_RECEIPT"
	}
	return protocol.Receipt{SchemaVersion: 1, DomainID: domainID, Generation: o.cfg.Generation, CommandID: commandID, IntentHash: ih, Outcome: st, OwnerVersion: uint64(ov), ReceiptVersion: uint64(rv), State: state, Reason: reason}, nil
}

func (o *E12Owner) ReadState() (state []int64, ownerVersion uint64, cutoff uint64, err error) {
	rows, e := o.effects.Query(`SELECT state_json,owner_version,cutoff_sequence FROM domains WHERE domain_id=?`, o.cfg.DomainID)
	if e != nil || len(rows) == 0 {
		return nil, 0, 0, e
	}
	sj, _ := sqlitecgo.TextAt(rows[0], 0)
	ov, _ := sqlitecgo.Int64At(rows[0], 1)
	co, _ := sqlitecgo.Int64At(rows[0], 2)
	e = json.Unmarshal([]byte(sj), &state)
	return state, uint64(ov), uint64(co), e
}

func (o *E12Owner) Barrier(req protocol.BarrierRequest) (protocol.BarrierReceipt, error) {
	if o.cfg.Profile != "E2" {
		return protocol.BarrierReceipt{}, fmt.Errorf("barrier unsupported by %s", o.cfg.Profile)
	}
	if req.SchemaVersion == 0 {
		req.SchemaVersion = 1
	}
	if req.SchemaVersion != 1 || req.DomainID != o.cfg.DomainID || req.Generation != o.cfg.Generation || req.Token == "" {
		return protocol.BarrierReceipt{}, fmt.Errorf("invalid barrier request")
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	t := time.Now().UTC().Format(time.RFC3339Nano)
	rows, e := o.claims.Query(`SELECT cutoff_sequence,status,state_json,owner_version FROM barriers WHERE domain_id=? AND token=?`, req.DomainID, req.Token)
	if e != nil {
		return protocol.BarrierReceipt{}, e
	}
	resuming := false
	if len(rows) > 0 {
		co, _ := sqlitecgo.Int64At(rows[0], 0)
		if uint64(co) != req.CutoffSequence {
			return protocol.BarrierReceipt{}, ErrIdentityConflict
		}
		status, _ := sqlitecgo.TextAt(rows[0], 1)
		if status == "COMPLETE" {
			sj, _ := sqlitecgo.TextAt(rows[0], 2)
			ov, _ := sqlitecgo.Int64At(rows[0], 3)
			var state []int64
			if e := json.Unmarshal([]byte(sj), &state); e != nil {
				return protocol.BarrierReceipt{}, e
			}
			claimRows, e := o.claims.Query(`SELECT command_id FROM claims WHERE domain_id=? AND sequence<=? AND status IN ('APPLIED','STOPPED') ORDER BY sequence,command_id`, req.DomainID, req.CutoffSequence)
			if e != nil {
				return protocol.BarrierReceipt{}, e
			}
			recs := make([]protocol.Receipt, 0, len(claimRows))
			for _, cr := range claimRows {
				cid, _ := sqlitecgo.TextAt(cr, 0)
				rc, er := o.GetReceipt(req.DomainID, cid)
				if er != nil {
					return protocol.BarrierReceipt{}, er
				}
				recs = append(recs, rc)
			}
			if o.cfg.Trace != nil {
				_ = o.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, Event: "BARRIER_REPLAY", Reason: req.Token})
			}
			return protocol.BarrierReceipt{SchemaVersion: 1, DomainID: req.DomainID, Generation: req.Generation, Token: req.Token, CutoffSequence: req.CutoffSequence, OwnerVersion: uint64(ov), State: state, Receipts: recs, Reason: "QUIESCENT_ANCHOR_REPLAY"}, nil
		}
		resuming = true
	} else {
		if _, e = o.claims.Exec(`INSERT INTO barriers(domain_id,token,cutoff_sequence,status,state_json,owner_version,created_at,updated_at) VALUES(?,?,?,'INSTALLING','[]',0,?,?)`, req.DomainID, req.Token, req.CutoffSequence, t, t); e != nil {
			return protocol.BarrierReceipt{}, e
		}
	}
	// Install the durable cutoff at the same serialized effect-owner boundary.
	// When resuming an INSTALLING barrier after a crash, do not manufacture a
	// second cutoff commit if the requested cutoff is already durable.
	cutoffAlreadyDurable := false
	if e = o.effects.WithTx(func(tx *sqlitecgo.Tx) error {
		rr, er := tx.Query(`SELECT cutoff_sequence,owner_version FROM domains WHERE domain_id=?`, req.DomainID)
		if er != nil {
			return er
		}
		if len(rr) == 0 {
			return fmt.Errorf("domain not found")
		}
		co, _ := sqlitecgo.Int64At(rr[0], 0)
		ov, _ := sqlitecgo.Int64At(rr[0], 1)
		if uint64(co) >= req.CutoffSequence {
			cutoffAlreadyDurable = true
			return nil
		}
		nco := co
		if int64(req.CutoffSequence) > nco {
			nco = int64(req.CutoffSequence)
		}
		_, er = tx.Exec(`UPDATE domains SET cutoff_sequence=?,owner_version=?,updated_at=? WHERE domain_id=?`, nco, ov+1, t, req.DomainID)
		return er
	}); e != nil {
		return protocol.BarrierReceipt{}, e
	}
	if resuming && cutoffAlreadyDurable {
		if o.cfg.Trace != nil {
			_ = o.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, Event: "BARRIER_RESUME", Reason: req.Token})
		}
	} else if o.cfg.Trace != nil {
		_ = o.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, Event: "BARRIER_CUTOFF_COMMITTED", Reason: req.Token})
	}
	if !cutoffAlreadyDurable && o.cfg.Faults != nil {
		o.cfg.Faults.Hit("AFTER_BARRIER_CUTOFF_COMMIT_BEFORE_REPLY", req.Token)
	}

	// With the owner mutex held, no effect is in flight. Reconcile every claim
	// covered by the cutoff against durable effects truth at the owner.
	claims, e := o.claims.Query(`SELECT command_id,intent_hash,status FROM claims WHERE domain_id=? AND sequence<=? ORDER BY sequence,command_id`, req.DomainID, req.CutoffSequence)
	if e != nil {
		return protocol.BarrierReceipt{}, e
	}
	recs := make([]protocol.Receipt, 0, len(claims))
	for _, cr := range claims {
		cid, _ := sqlitecgo.TextAt(cr, 0)
		ih, _ := sqlitecgo.TextAt(cr, 1)
		st, _ := sqlitecgo.TextAt(cr, 2)
		if st == "APPLIED" || st == "STOPPED" {
			rc, _ := o.GetReceipt(req.DomainID, cid)
			recs = append(recs, rc)
			continue
		}
		erows, er := o.effects.Query(`SELECT state_after_json,owner_version FROM effects WHERE domain_id=? AND command_id=?`, req.DomainID, cid)
		if er != nil {
			return protocol.BarrierReceipt{}, er
		}
		outcome := "STOPPED"
		reason := "BARRIER_CONFIRMED_NOT_EFFECTED"
		var state []int64
		var ov uint64
		if len(erows) > 0 {
			outcome = "APPLIED"
			reason = "BARRIER_RECONCILED_APPLIED"
			sj, _ := sqlitecgo.TextAt(erows[0], 0)
			_ = json.Unmarshal([]byte(sj), &state)
			vv, _ := sqlitecgo.Int64At(erows[0], 1)
			ov = uint64(vv)
		} else {
			state, ov, _, er = o.ReadState()
			if er != nil {
				return protocol.BarrierReceipt{}, er
			}
		}
		rr := protocol.CommandRequest{SchemaVersion: 1, DomainID: req.DomainID, Generation: req.Generation, CommandID: cid, IntentHash: ih}
		rc, er := o.writeTerminal(rr, outcome, reason, state, ov)
		if er != nil {
			return protocol.BarrierReceipt{}, er
		}
		recs = append(recs, rc)
	}
	state, ov, cutoff, e := o.ReadState()
	if e != nil {
		return protocol.BarrierReceipt{}, e
	}
	sj, _ := json.Marshal(state)
	if _, e = o.claims.Exec(`UPDATE barriers SET status='COMPLETE',state_json=?,owner_version=?,updated_at=? WHERE domain_id=? AND token=?`, string(sj), ov, time.Now().UTC().Format(time.RFC3339Nano), req.DomainID, req.Token); e != nil {
		return protocol.BarrierReceipt{}, e
	}
	if o.cfg.Trace != nil {
		_ = o.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, Event: "BARRIER_COMPLETE", Reason: req.Token})
	}
	return protocol.BarrierReceipt{SchemaVersion: 1, DomainID: req.DomainID, Generation: req.Generation, Token: req.Token, CutoffSequence: cutoff, OwnerVersion: ov, State: state, Receipts: recs, Reason: "QUIESCENT_ANCHOR"}, nil
}

func (o *E12Owner) Snapshot() (state []int64, effects, claims, terminal int64, cutoff uint64, err error) {
	state, _, cutoff, err = o.ReadState()
	if err != nil {
		return
	}
	rows, e := o.effects.Query(`SELECT COUNT(*) FROM effects WHERE domain_id=?`, o.cfg.DomainID)
	if e != nil {
		err = e
		return
	}
	effects, _ = sqlitecgo.Int64At(rows[0], 0)
	rows, e = o.claims.Query(`SELECT COUNT(*),SUM(CASE WHEN status IN ('APPLIED','STOPPED') THEN 1 ELSE 0 END) FROM claims WHERE domain_id=?`, o.cfg.DomainID)
	if e != nil {
		err = e
		return
	}
	claims, _ = sqlitecgo.Int64At(rows[0], 0)
	if rows[0][1].Kind == sqlitecgo.Integer {
		terminal = rows[0][1].I64
	}
	return
}

func IsIdentityConflict(err error) bool { return errors.Is(err, ErrIdentityConflict) }

// ForceStop records a terminal STOPPED receipt without applying an effect.
// It is a controlled-pilot-only backend behavior used to model a predetermined
// terminal validation/service rejection independent of the method under test.
func (o *E12Owner) ForceStop(req protocol.CommandRequest, reason string) (protocol.Receipt, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if req.DomainID != o.cfg.DomainID {
		return protocol.Receipt{}, fmt.Errorf("unknown domain")
	}
	if err := req.ValidateAndFillHash(len(o.cfg.InitialState)); err != nil {
		return protocol.Receipt{}, err
	}
	if rc, ok, e := o.existingClaim(req); ok {
		return rc, e
	}
	if err := o.insertClaim(req); err != nil {
		return protocol.Receipt{}, err
	}
	var state []int64
	var ownerVersion uint64
	err := o.effects.WithTx(func(tx *sqlitecgo.Tx) error {
		rows, e := tx.Query(`SELECT generation,installed_epoch,state_json,owner_version FROM domains WHERE domain_id=?`, req.DomainID)
		if e != nil {
			return e
		}
		if len(rows) == 0 {
			return fmt.Errorf("domain not found")
		}
		gen, _ := sqlitecgo.Int64At(rows[0], 0)
		ep, _ := sqlitecgo.Int64At(rows[0], 1)
		sj, _ := sqlitecgo.TextAt(rows[0], 2)
		ov, _ := sqlitecgo.Int64At(rows[0], 3)
		if uint64(gen) != req.Generation || uint64(ep) != req.Epoch {
			return fmt.Errorf("pilot force-stop generation/epoch mismatch")
		}
		if e = json.Unmarshal([]byte(sj), &state); e != nil {
			return e
		}
		ownerVersion = uint64(ov) + 1
		t := time.Now().UTC().Format(time.RFC3339Nano)
		_, e = tx.Exec(`UPDATE domains SET owner_version=?,updated_at=? WHERE domain_id=?`, ownerVersion, t, req.DomainID)
		return e
	})
	if err != nil {
		return protocol.Receipt{}, err
	}
	rc, err := o.writeTerminal(req, "STOPPED", reason, state, ownerVersion)
	if err == nil && o.cfg.Trace != nil {
		_ = o.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "PILOT_FORCED_STOPPED", Reason: reason})
	}
	return rc, err
}

func (o *E12Owner) JobStore() *jobs.Store { return jobs.New(o.effects, o.cfg.DomainID, o.cfg.Faults) }

// Stop implements the per-identity terminal stop (Q_S, release 061) for E2.
// It is serialized with Apply by the owner mutex, so no effect for this
// identity can be in flight while it runs:
//   - terminal receipt present        -> return it (idempotent);
//   - claim without receipt (crash)   -> reconcile against durable effects:
//     effect present => APPLIED, otherwise STOPPED (no replay after crash);
//   - unknown identity                -> durable tombstone claim STOPPED, so a
//     delayed request with this identity can never take effect.
//
// A stop never deletes or rewrites an effect, and a different intent hash for
// the same identity is an identity conflict.
func (o *E12Owner) Stop(req protocol.StopRequest) (protocol.Receipt, error) {
	if o.cfg.Profile != "E2" {
		return protocol.Receipt{}, fmt.Errorf("per-identity stop unsupported by %s", o.cfg.Profile)
	}
	if req.SchemaVersion == 0 {
		req.SchemaVersion = protocol.SchemaVersion
	}
	if err := req.Validate(); err != nil {
		return protocol.Receipt{}, err
	}
	if req.DomainID != o.cfg.DomainID || req.Generation != o.cfg.Generation {
		return protocol.Receipt{}, fmt.Errorf("stop domain/generation mismatch")
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	cr := protocol.CommandRequest{SchemaVersion: 1, DomainID: req.DomainID, Generation: req.Generation, CommandID: req.CommandID, IntentHash: req.IntentHash, Sequence: req.Sequence}
	rows, e := o.claims.Query(`SELECT intent_hash,status FROM claims WHERE domain_id=? AND command_id=?`, req.DomainID, req.CommandID)
	if e != nil {
		return protocol.Receipt{}, e
	}
	if len(rows) > 0 {
		ih, _ := sqlitecgo.TextAt(rows[0], 0)
		st, _ := sqlitecgo.TextAt(rows[0], 1)
		if ih != req.IntentHash {
			return protocol.Receipt{}, ErrIdentityConflict
		}
		if st == "APPLIED" || st == "STOPPED" {
			return o.GetReceipt(req.DomainID, req.CommandID)
		}
		// Claim without terminal receipt: decide from durable effect truth.
		erows, er := o.effects.Query(`SELECT state_after_json,owner_version FROM effects WHERE domain_id=? AND command_id=?`, req.DomainID, req.CommandID)
		if er != nil {
			return protocol.Receipt{}, er
		}
		if len(erows) > 0 {
			sj, _ := sqlitecgo.TextAt(erows[0], 0)
			var state []int64
			_ = json.Unmarshal([]byte(sj), &state)
			vv, _ := sqlitecgo.Int64At(erows[0], 1)
			rc, er := o.writeTerminal(cr, "APPLIED", "STOP_RECONCILED_APPLIED", state, uint64(vv))
			o.traceStop(req, "APPLIED")
			return rc, er
		}
		state, ov, _, er := o.ReadState()
		if er != nil {
			return protocol.Receipt{}, er
		}
		if ov == 0 {
			ov = 1
		}
		rc, er := o.writeTerminal(cr, "STOPPED", "STOP_CONFIRMED_NOT_EFFECTED", state, ov)
		o.traceStop(req, "STOPPED")
		return rc, er
	}
	// Unknown identity: durable tombstone. The claim row doubles as the
	// tombstone because Apply consults existing claims before any effect.
	state, ov, _, er := o.ReadState()
	if er != nil {
		return protocol.Receipt{}, er
	}
	if ov == 0 {
		ov = 1
	}
	if err := o.insertClaim(cr); err != nil {
		return protocol.Receipt{}, err
	}
	if o.cfg.Faults != nil {
		o.cfg.Faults.Hit("AFTER_STOP_TOMBSTONE_CLAIM_BEFORE_RECEIPT", req.CommandID)
	}
	rc, er := o.writeTerminal(cr, "STOPPED", "STOP_TOMBSTONE", state, ov)
	if er != nil {
		return protocol.Receipt{}, er
	}
	if o.cfg.Faults != nil {
		o.cfg.Faults.Hit("AFTER_STOP_RECEIPT_BEFORE_REPLY", req.CommandID)
	}
	o.traceStop(req, "STOPPED")
	return rc, nil
}

func (o *E12Owner) traceStop(req protocol.StopRequest, outcome string) {
	if o.cfg.Trace != nil {
		_ = o.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "PER_IDENTITY_STOP", Reason: outcome})
	}
}

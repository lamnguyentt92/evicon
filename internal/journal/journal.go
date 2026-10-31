package journal

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"evicon/internal/domain"
	"evicon/internal/sqlitecgo"
)

const SchemaVersion = 7

var (
	ErrNotFound               = errors.New("not found")
	ErrIdentityConflict       = errors.New("command identity conflict")
	ErrRevisionConflict       = errors.New("domain revision conflict")
	ErrBarrierInProgress      = errors.New("barrier already in progress")
	ErrBarrierAlreadyComplete = errors.New("barrier already complete")
	ErrBarrierAborted         = errors.New("barrier aborted")
	// ErrBarrierPartial (release 084): the token's fence completed with per-identity intake (rule R5',
	// -fence-intake per-identity): terminal answers taken in, unlisted identities sealed, no anchor. The
	// token keeps that meaning; a retry cannot give it a new one.
	ErrBarrierPartial = errors.New("barrier completed with per-identity intake")
	// ErrTerminalReversal: owner evidence contradicts a committed terminal outcome
	// (APPLIED vs STOPPED for one identity). Terminal evidence is monotone, so the
	// write is refused; release 068 wraps it so callers can escalate instead of retrying.
	ErrTerminalReversal = errors.New("terminal outcome reversal")
)

type Domain struct {
	ID             string  `json:"domainId"`
	Generation     uint64  `json:"generation"`
	InstalledEpoch uint64  `json:"installedEpoch"`
	Revision       uint64  `json:"revision"`
	Dimension      int     `json:"dimension"`
	ComponentMin   []int64 `json:"componentMin,omitempty"`
	ComponentMax   []int64 `json:"componentMax,omitempty"`
	SumMax         *int64  `json:"sumMax,omitempty"`
	Anchor         []int64 `json:"anchor"`
	CutoffSequence uint64  `json:"cutoffSequence"`
	ContractHash   string  `json:"contractHash"`
}

type CommandRecord struct {
	DomainID    string           `json:"domainId"`
	CommandID   string           `json:"commandId"`
	Generation  uint64           `json:"generation"`
	Epoch       uint64           `json:"epoch"`
	Sequence    uint64           `json:"sequence"`
	Operation   domain.Operation `json:"operation"`
	Value       []int64          `json:"value"`
	IntentHash  string           `json:"intentHash"`
	Reservation []int64          `json:"reservation"`
	Status      string           `json:"status"`
	Decision    string           `json:"decision"`
	Reason      string           `json:"reason"`
	TraceID     string           `json:"traceId,omitempty"`
}

type OutboxRecord struct {
	DomainID     string `json:"domainId"`
	CommandID    string `json:"commandId"`
	EndpointURL  string `json:"endpointUrl"`
	State        string `json:"state"`
	AttemptCount int64  `json:"attemptCount"`
	LastError    string `json:"lastError,omitempty"`
}

type ReceiptRecord struct {
	DomainID       string  `json:"domainId"`
	CommandID      string  `json:"commandId"`
	IntentHash     string  `json:"intentHash"`
	Outcome        string  `json:"outcome"`
	OwnerVersion   uint64  `json:"ownerVersion"`
	ReceiptVersion uint64  `json:"receiptVersion"`
	State          []int64 `json:"state"`
}

type ProbeRecord struct {
	DomainID           string `json:"domainId"`
	CandidateID        string `json:"candidateId"`
	CandidateHash      string `json:"candidateHash"`
	ProbeIndex         int64  `json:"probeIndex"`
	QueriedCommand     string `json:"queriedCommand"`
	Policy             string `json:"policy"`
	ExpectedCostUS     int64  `json:"expectedCostUs"`
	ActualCostUS       int64  `json:"actualCostUs"`
	Outcome            string `json:"outcome"`
	Informative        bool   `json:"informative"`
	BeliefChanged      bool   `json:"beliefChanged"`
	VerdictChanged     bool   `json:"verdictChanged"`
	RevisionBefore     uint64 `json:"revisionBefore"`
	RevisionAfter      uint64 `json:"revisionAfter"`
	Reason             string `json:"reason,omitempty"`
	Status             string `json:"status"`
	QueryRequestBytes  int64  `json:"queryRequestBytes"`
	QueryResponseBytes int64  `json:"queryResponseBytes"`
}

type BarrierLifecycle struct {
	DomainID         string  `json:"domainId"`
	Token            string  `json:"token"`
	Generation       uint64  `json:"generation"`
	CutoffSequence   uint64  `json:"cutoffSequence"`
	StartRevision    uint64  `json:"startRevision"`
	Status           string  `json:"status"`
	Anchor           []int64 `json:"anchor,omitempty"`
	OwnerVersion     uint64  `json:"ownerVersion,omitempty"`
	CompleteRevision uint64  `json:"completeRevision,omitempty"`
}

type Store struct{ db *sqlitecgo.DB }

func OpenReadOnly(path string) (*Store, error) {
	db, err := sqlitecgo.OpenReadOnly(path)
	if err != nil {
		return nil, err
	}
	return &Store{db: db}, nil
}

func Open(path string) (*Store, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	db, err := sqlitecgo.Open(path)
	if err != nil {
		return nil, err
	}
	s := &Store{db: db}
	if err := s.configure(); err != nil {
		db.Close()
		return nil, err
	}
	if err := s.migrate(); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) Close() error          { return s.db.Close() }
func (s *Store) SQLiteVersion() string { return sqlitecgo.Version() }

func (s *Store) configure() error {
	if _, err := s.db.Exec("PRAGMA journal_mode=WAL"); err != nil {
		return err
	}
	if _, err := s.db.Exec("PRAGMA synchronous=FULL"); err != nil {
		return err
	}
	if _, err := s.db.Exec("PRAGMA foreign_keys=ON"); err != nil {
		return err
	}
	if _, err := s.db.Exec("PRAGMA wal_autocheckpoint=1000"); err != nil {
		return err
	}
	rows, err := s.db.Query("PRAGMA journal_mode")
	if err != nil || len(rows) != 1 {
		return fmt.Errorf("verify journal_mode: %w", err)
	}
	mode, _ := sqlitecgo.TextAt(rows[0], 0)
	if mode != "wal" {
		return fmt.Errorf("journal_mode=%q, want wal", mode)
	}
	rows, err = s.db.Query("PRAGMA synchronous")
	if err != nil || len(rows) != 1 {
		return fmt.Errorf("verify synchronous: %w", err)
	}
	syncVal, _ := sqlitecgo.Int64At(rows[0], 0)
	// SQLite numeric value 2 is FULL.
	if syncVal != 2 {
		return fmt.Errorf("synchronous=%d, want FULL(2)", syncVal)
	}
	return nil
}

func (s *Store) migrate() error {
	stmts := []string{
		`CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)`,
		`CREATE TABLE IF NOT EXISTS domains (
            domain_id TEXT PRIMARY KEY,
            generation INTEGER NOT NULL,
            installed_epoch INTEGER NOT NULL,
            revision INTEGER NOT NULL,
            dimension INTEGER NOT NULL,
            component_min_json TEXT NOT NULL,
            component_max_json TEXT NOT NULL,
            sum_max INTEGER NULL,
            anchor_json TEXT NOT NULL,
            cutoff_sequence INTEGER NOT NULL DEFAULT 0,
            contract_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )`,
		`CREATE TABLE IF NOT EXISTS commands (
            domain_id TEXT NOT NULL,
            command_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            epoch INTEGER NOT NULL,
            sequence INTEGER NOT NULL,
            operation TEXT NOT NULL,
            value_json TEXT NOT NULL,
            intent_hash TEXT NOT NULL,
            reservation_json TEXT NOT NULL,
            status TEXT NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT NOT NULL,
            trace_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(domain_id, command_id),
            FOREIGN KEY(domain_id) REFERENCES domains(domain_id)
        )`,
		`CREATE TABLE IF NOT EXISTS outbox (
            domain_id TEXT NOT NULL,
            command_id TEXT NOT NULL,
            endpoint_url TEXT NOT NULL,
            state TEXT NOT NULL,
            attempt_count INTEGER NOT NULL,
            last_error TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(domain_id, command_id),
            FOREIGN KEY(domain_id, command_id) REFERENCES commands(domain_id, command_id)
        )`,
		`CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain_id TEXT NOT NULL,
            command_id TEXT NOT NULL,
            intent_hash TEXT NOT NULL,
            outcome TEXT NOT NULL,
            owner_version INTEGER NOT NULL,
            receipt_version INTEGER NOT NULL,
            state_json TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            evidence_kind TEXT NOT NULL DEFAULT 'RECEIPT',
            applied_revision INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )`,
		`CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain_id TEXT NOT NULL,
            command_id TEXT NOT NULL,
            verdict TEXT NOT NULL,
            analyzer TEXT NOT NULL,
            reason TEXT NOT NULL,
            revision INTEGER NOT NULL,
            policy TEXT NOT NULL DEFAULT '',
            probe_count INTEGER NOT NULL DEFAULT 0,
            probe_cost_us INTEGER NOT NULL DEFAULT 0,
            selector_ns INTEGER NOT NULL DEFAULT 0,
            analyzed_revision INTEGER NOT NULL DEFAULT 0,
            commit_expected_revision INTEGER NOT NULL DEFAULT 0,
            committed_revision INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )`,
		`CREATE TABLE IF NOT EXISTS authority_barriers (
            domain_id TEXT PRIMARY KEY,
            token TEXT NOT NULL,
            generation INTEGER NOT NULL,
            cutoff_sequence INTEGER NOT NULL,
            start_revision INTEGER NOT NULL,
            status TEXT NOT NULL,
            anchor_json TEXT NOT NULL DEFAULT '[]',
            complete_revision INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(domain_id) REFERENCES domains(domain_id)
        )`,
		`CREATE TABLE IF NOT EXISTS authority_barrier_history (
            domain_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            token TEXT NOT NULL,
            cutoff_sequence INTEGER NOT NULL,
            start_revision INTEGER NOT NULL,
            status TEXT NOT NULL,
            anchor_json TEXT NOT NULL DEFAULT '[]',
            owner_version INTEGER NOT NULL DEFAULT 0,
            complete_revision INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(domain_id,generation,token),
            FOREIGN KEY(domain_id) REFERENCES domains(domain_id)
        )`,
		`CREATE INDEX IF NOT EXISTS idx_barrier_history_pending ON authority_barrier_history(domain_id,generation,status)`,
		`CREATE TABLE IF NOT EXISTS probe_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            candidate_hash TEXT NOT NULL,
            probe_index INTEGER NOT NULL,
            queried_command_id TEXT NOT NULL,
            policy TEXT NOT NULL,
            expected_cost_us INTEGER NOT NULL,
            actual_cost_us INTEGER NOT NULL,
            outcome TEXT NOT NULL,
            informative INTEGER NOT NULL,
            belief_changed INTEGER NOT NULL DEFAULT 0,
            verdict_changed INTEGER NOT NULL DEFAULT 0,
            revision_before INTEGER NOT NULL,
            revision_after INTEGER NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'COMPLETED',
            started_at TEXT NOT NULL DEFAULT '',
            completed_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(domain_id,candidate_id,candidate_hash,probe_index)
        )`,
		`CREATE INDEX IF NOT EXISTS idx_outbox_state ON outbox(state, updated_at)`,
		`CREATE INDEX IF NOT EXISTS idx_commands_domain ON commands(domain_id, created_at)`,
		`CREATE INDEX IF NOT EXISTS idx_probe_candidate ON probe_attempts(domain_id,candidate_id,candidate_hash,probe_index)`,
	}
	for _, stmt := range stmts {
		if _, err := s.db.Exec(stmt); err != nil {
			return err
		}
	}
	// Schema-v2 migration from 006-durable databases. Fresh databases already
	// contain these columns; duplicate-column errors are benign.
	for _, stmt := range []string{
		`ALTER TABLE probe_attempts ADD COLUMN query_request_bytes INTEGER NOT NULL DEFAULT 0`,
		`ALTER TABLE probe_attempts ADD COLUMN query_response_bytes INTEGER NOT NULL DEFAULT 0`,
		`ALTER TABLE domains ADD COLUMN cutoff_sequence INTEGER NOT NULL DEFAULT 0`,
		`ALTER TABLE observations ADD COLUMN reason TEXT NOT NULL DEFAULT ''`,
		`ALTER TABLE observations ADD COLUMN evidence_kind TEXT NOT NULL DEFAULT 'RECEIPT'`,
		`ALTER TABLE decisions ADD COLUMN policy TEXT NOT NULL DEFAULT ''`,
		`ALTER TABLE decisions ADD COLUMN probe_count INTEGER NOT NULL DEFAULT 0`,
		`ALTER TABLE decisions ADD COLUMN probe_cost_us INTEGER NOT NULL DEFAULT 0`,
		`ALTER TABLE decisions ADD COLUMN selector_ns INTEGER NOT NULL DEFAULT 0`,
		`ALTER TABLE authority_barriers ADD COLUMN anchor_json TEXT NOT NULL DEFAULT '[]'`,
		`ALTER TABLE authority_barriers ADD COLUMN complete_revision INTEGER NOT NULL DEFAULT 0`,
		`ALTER TABLE probe_attempts ADD COLUMN status TEXT NOT NULL DEFAULT 'COMPLETED'`,
		`ALTER TABLE probe_attempts ADD COLUMN started_at TEXT NOT NULL DEFAULT ''`,
		`ALTER TABLE probe_attempts ADD COLUMN completed_at TEXT NOT NULL DEFAULT ''`,
		`ALTER TABLE probe_attempts ADD COLUMN belief_changed INTEGER NOT NULL DEFAULT 0`,
		`ALTER TABLE probe_attempts ADD COLUMN verdict_changed INTEGER NOT NULL DEFAULT 0`,
		`ALTER TABLE decisions ADD COLUMN analyzed_revision INTEGER NOT NULL DEFAULT 0`,
		`ALTER TABLE decisions ADD COLUMN commit_expected_revision INTEGER NOT NULL DEFAULT 0`,
		`ALTER TABLE decisions ADD COLUMN committed_revision INTEGER NOT NULL DEFAULT 0`,
	} {
		if _, err := s.db.Exec(stmt); err != nil && !strings.Contains(strings.ToLower(err.Error()), "duplicate column") {
			return err
		}
	}
	// Preserve legacy 008-patch1 barrier lifecycle when opening an existing DB.
	// The old table stored only one row/domain; copying it into token history is
	// conservative and, critically, does not silently drop a pending pause.
	if _, err := s.db.Exec(`INSERT OR IGNORE INTO authority_barrier_history(domain_id,generation,token,cutoff_sequence,start_revision,status,anchor_json,owner_version,complete_revision,created_at,updated_at)
		SELECT domain_id,generation,token,cutoff_sequence,start_revision,status,anchor_json,0,complete_revision,created_at,updated_at FROM authority_barriers WHERE token<>''`); err != nil {
		return err
	}
	if _, err := s.db.Exec(`INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)`, fmt.Sprintf("%d", SchemaVersion)); err != nil {
		return err
	}
	return nil
}

func now() string              { return time.Now().UTC().Format(time.RFC3339Nano) }
func js(v any) (string, error) { b, err := json.Marshal(v); return string(b), err }
func parseVec(s string) ([]int64, error) {
	var v []int64
	err := json.Unmarshal([]byte(s), &v)
	return v, err
}

func (s *Store) EnsureDomain(d Domain) error {
	minj, _ := js(d.ComponentMin)
	maxj, _ := js(d.ComponentMax)
	anchorj, _ := js(d.Anchor)
	var sum any
	if d.SumMax != nil {
		sum = *d.SumMax
	}
	t := now()
	return s.db.WithTx(func(tx *sqlitecgo.Tx) error {
		rows, err := tx.Query(`SELECT generation,installed_epoch,dimension,component_min_json,component_max_json,sum_max,anchor_json,cutoff_sequence,contract_hash FROM domains WHERE domain_id=?`, d.ID)
		if err != nil {
			return err
		}
		if len(rows) == 0 {
			_, err = tx.Exec(`INSERT INTO domains(domain_id,generation,installed_epoch,revision,dimension,component_min_json,component_max_json,sum_max,anchor_json,cutoff_sequence,contract_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)`,
				d.ID, d.Generation, d.InstalledEpoch, d.Revision, d.Dimension, minj, maxj, sum, anchorj, d.CutoffSequence, d.ContractHash, t, t)
			return err
		}
		gen, _ := sqlitecgo.Int64At(rows[0], 0)
		ep, _ := sqlitecgo.Int64At(rows[0], 1)
		dim, _ := sqlitecgo.Int64At(rows[0], 2)
		storedMin, _ := sqlitecgo.TextAt(rows[0], 3)
		storedMax, _ := sqlitecgo.TextAt(rows[0], 4)
		storedSum := rows[0][5]
		ch, _ := sqlitecgo.TextAt(rows[0], 8)
		sumMatches := (d.SumMax == nil && storedSum.Kind == sqlitecgo.Null) ||
			(d.SumMax != nil && storedSum.Kind == sqlitecgo.Integer && storedSum.I64 == *d.SumMax)
		if uint64(gen) != d.Generation || uint64(ep) != d.InstalledEpoch || int(dim) != d.Dimension ||
			storedMin != minj || storedMax != maxj || !sumMatches || ch != d.ContractHash {
			return fmt.Errorf("CONFIGURATION_CONFLICT: durable authority domain %s differs from requested generation/epoch/dimension/bounds/contract", d.ID)
		}
		return nil
	})
}

func (s *Store) GetDomain(id string) (Domain, error) {
	rows, err := s.db.Query(`SELECT domain_id,generation,installed_epoch,revision,dimension,component_min_json,component_max_json,sum_max,anchor_json,cutoff_sequence,contract_hash FROM domains WHERE domain_id=?`, id)
	if err != nil {
		return Domain{}, err
	}
	if len(rows) == 0 {
		return Domain{}, ErrNotFound
	}
	r := rows[0]
	did, _ := sqlitecgo.TextAt(r, 0)
	gen, _ := sqlitecgo.Int64At(r, 1)
	ep, _ := sqlitecgo.Int64At(r, 2)
	rev, _ := sqlitecgo.Int64At(r, 3)
	dim, _ := sqlitecgo.Int64At(r, 4)
	minj, _ := sqlitecgo.TextAt(r, 5)
	maxj, _ := sqlitecgo.TextAt(r, 6)
	anchorj, _ := sqlitecgo.TextAt(r, 8)
	cutoff, _ := sqlitecgo.Int64At(r, 9)
	ch, _ := sqlitecgo.TextAt(r, 10)
	var mins, maxs, anchor []int64
	if err := json.Unmarshal([]byte(minj), &mins); err != nil {
		return Domain{}, err
	}
	if err := json.Unmarshal([]byte(maxj), &maxs); err != nil {
		return Domain{}, err
	}
	if err := json.Unmarshal([]byte(anchorj), &anchor); err != nil {
		return Domain{}, err
	}
	var sum *int64
	if r[7].Kind == sqlitecgo.Integer {
		v := r[7].I64
		sum = &v
	}
	return Domain{ID: did, Generation: uint64(gen), InstalledEpoch: uint64(ep), Revision: uint64(rev), Dimension: int(dim), ComponentMin: mins, ComponentMax: maxs, SumMax: sum, Anchor: anchor, CutoffSequence: uint64(cutoff), ContractHash: ch}, nil
}

// ReserveAndEnqueue atomically commits the durable obligation and outbox item.
// beforeCommit is a fault hook invoked inside the transaction after all writes
// but before COMMIT. Process termination at that hook must leave no durable row.
func (s *Store) ReserveAndEnqueue(cmd CommandRecord, endpointURL string, analyzer string, beforeCommit func() error) (uint64, error) {
	return s.reserveAndEnqueue(cmd, endpointURL, analyzer, nil, beforeCommit)
}

func (s *Store) ReserveAndEnqueueAtRevision(cmd CommandRecord, endpointURL string, analyzer string, expectedRevision uint64, beforeCommit func() error) (uint64, error) {
	return s.reserveAndEnqueue(cmd, endpointURL, analyzer, &expectedRevision, beforeCommit)
}

// RecordDecision durably records an admission attempt that does not create a
// command/outbox obligation (for example BLOCK, DEFER, or REJECT). The domain
// revision is the revision of the snapshot on which the decision was made;
// recording the audit row does not itself advance that revision because it
// does not change the semantic state of the domain.
func (s *Store) RecordDecision(domainID, commandID, verdict, analyzer, reason string, revision uint64) error {
	_, err := s.db.Exec(
		`INSERT INTO decisions(domain_id,command_id,verdict,analyzer,reason,revision,created_at) VALUES(?,?,?,?,?,?,?)`,
		domainID, commandID, verdict, analyzer, reason, revision, now(),
	)
	return err
}

func (s *Store) reserveAndEnqueue(cmd CommandRecord, endpointURL string, analyzer string, expectedRevision *uint64, beforeCommit func() error) (uint64, error) {
	valuej, _ := js(cmd.Value)
	resj, _ := js(cmd.Reservation)
	t := now()
	var newRev uint64
	err := s.db.WithTx(func(tx *sqlitecgo.Tx) error {
		rows, err := tx.Query(`SELECT intent_hash FROM commands WHERE domain_id=? AND command_id=?`, cmd.DomainID, cmd.CommandID)
		if err != nil {
			return err
		}
		if len(rows) > 0 {
			existing, _ := sqlitecgo.TextAt(rows[0], 0)
			if existing != cmd.IntentHash {
				return ErrIdentityConflict
			}
			return fmt.Errorf("command already exists")
		}
		drows, err := tx.Query(`SELECT revision FROM domains WHERE domain_id=?`, cmd.DomainID)
		if err != nil {
			return err
		}
		if len(drows) == 0 {
			return ErrNotFound
		}
		rev, _ := sqlitecgo.Int64At(drows[0], 0)
		if expectedRevision != nil && uint64(rev) != *expectedRevision {
			return ErrRevisionConflict
		}
		newRev = uint64(rev) + 1
		if _, err = tx.Exec(`INSERT INTO commands(domain_id,command_id,generation,epoch,sequence,operation,value_json,intent_hash,reservation_json,status,decision,reason,trace_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`, cmd.DomainID, cmd.CommandID, cmd.Generation, cmd.Epoch, cmd.Sequence, string(cmd.Operation), valuej, cmd.IntentHash, resj, cmd.Status, cmd.Decision, cmd.Reason, cmd.TraceID, t, t); err != nil {
			return err
		}
		if _, err = tx.Exec(`INSERT INTO outbox(domain_id,command_id,endpoint_url,state,attempt_count,last_error,created_at,updated_at) VALUES(?,?,?,?,0,'',?,?)`, cmd.DomainID, cmd.CommandID, endpointURL, "PENDING", t, t); err != nil {
			return err
		}
		if _, err = tx.Exec(`INSERT INTO decisions(domain_id,command_id,verdict,analyzer,reason,revision,analyzed_revision,commit_expected_revision,committed_revision,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)`, cmd.DomainID, cmd.CommandID, cmd.Decision, analyzer, cmd.Reason, newRev, uint64(rev), uint64(rev), newRev, t); err != nil {
			return err
		}
		if _, err = tx.Exec(`UPDATE domains SET revision=?,updated_at=? WHERE domain_id=?`, newRev, t, cmd.DomainID); err != nil {
			return err
		}
		if beforeCommit != nil {
			if err := beforeCommit(); err != nil {
				return err
			}
		}
		return nil
	})
	return newRev, err
}

func (s *Store) GetCommand(domainID, commandID string) (CommandRecord, error) {
	rows, err := s.db.Query(`SELECT domain_id,command_id,generation,epoch,sequence,operation,value_json,intent_hash,reservation_json,status,decision,reason,trace_id FROM commands WHERE domain_id=? AND command_id=?`, domainID, commandID)
	if err != nil {
		return CommandRecord{}, err
	}
	if len(rows) == 0 {
		return CommandRecord{}, ErrNotFound
	}
	r := rows[0]
	did, _ := sqlitecgo.TextAt(r, 0)
	cid, _ := sqlitecgo.TextAt(r, 1)
	gen, _ := sqlitecgo.Int64At(r, 2)
	ep, _ := sqlitecgo.Int64At(r, 3)
	seq, _ := sqlitecgo.Int64At(r, 4)
	op, _ := sqlitecgo.TextAt(r, 5)
	vj, _ := sqlitecgo.TextAt(r, 6)
	ih, _ := sqlitecgo.TextAt(r, 7)
	rj, _ := sqlitecgo.TextAt(r, 8)
	st, _ := sqlitecgo.TextAt(r, 9)
	dec, _ := sqlitecgo.TextAt(r, 10)
	reason, _ := sqlitecgo.TextAt(r, 11)
	trace, _ := sqlitecgo.TextAt(r, 12)
	v, err := parseVec(vj)
	if err != nil {
		return CommandRecord{}, err
	}
	res, err := parseVec(rj)
	if err != nil {
		return CommandRecord{}, err
	}
	return CommandRecord{DomainID: did, CommandID: cid, Generation: uint64(gen), Epoch: uint64(ep), Sequence: uint64(seq), Operation: domain.Operation(op), Value: v, IntentHash: ih, Reservation: res, Status: st, Decision: dec, Reason: reason, TraceID: trace}, nil
}

func (s *Store) ListCountedCommands(domainID string) ([]CommandRecord, error) {
	rows, err := s.db.Query(`SELECT domain_id,command_id,generation,epoch,sequence,operation,value_json,intent_hash,reservation_json,status,decision,reason,trace_id FROM commands WHERE domain_id=? AND decision='ADMITTED' AND status!='STOPPED' ORDER BY sequence,command_id`, domainID)
	if err != nil {
		return nil, err
	}
	out := make([]CommandRecord, 0, len(rows))
	for _, r := range rows {
		did, _ := sqlitecgo.TextAt(r, 0)
		cid, _ := sqlitecgo.TextAt(r, 1)
		gen, _ := sqlitecgo.Int64At(r, 2)
		ep, _ := sqlitecgo.Int64At(r, 3)
		seq, _ := sqlitecgo.Int64At(r, 4)
		op, _ := sqlitecgo.TextAt(r, 5)
		vj, _ := sqlitecgo.TextAt(r, 6)
		ih, _ := sqlitecgo.TextAt(r, 7)
		rj, _ := sqlitecgo.TextAt(r, 8)
		st, _ := sqlitecgo.TextAt(r, 9)
		dec, _ := sqlitecgo.TextAt(r, 10)
		reason, _ := sqlitecgo.TextAt(r, 11)
		trace, _ := sqlitecgo.TextAt(r, 12)
		v, _ := parseVec(vj)
		res, _ := parseVec(rj)
		out = append(out, CommandRecord{DomainID: did, CommandID: cid, Generation: uint64(gen), Epoch: uint64(ep), Sequence: uint64(seq), Operation: domain.Operation(op), Value: v, IntentHash: ih, Reservation: res, Status: st, Decision: dec, Reason: reason, TraceID: trace})
	}
	return out, nil
}

func (s *Store) PendingOutbox(limit int) ([]OutboxRecord, error) {
	rows, err := s.db.Query(`SELECT domain_id,command_id,endpoint_url,state,attempt_count,last_error FROM outbox WHERE state!='DONE' ORDER BY created_at LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	out := make([]OutboxRecord, 0, len(rows))
	for _, r := range rows {
		did, _ := sqlitecgo.TextAt(r, 0)
		cid, _ := sqlitecgo.TextAt(r, 1)
		url, _ := sqlitecgo.TextAt(r, 2)
		st, _ := sqlitecgo.TextAt(r, 3)
		n, _ := sqlitecgo.Int64At(r, 4)
		le, _ := sqlitecgo.TextAt(r, 5)
		out = append(out, OutboxRecord{DomainID: did, CommandID: cid, EndpointURL: url, State: st, AttemptCount: n, LastError: le})
	}
	return out, nil
}

func (s *Store) BeginDispatchAttempt(domainID, commandID string) error {
	_, err := s.ClaimDispatchAttempt(domainID, commandID)
	return err
}

// ClaimDispatchAttempt (release 072) starts a dispatch attempt only if the outbox item is
// still open and its command is not terminal, in one statement. Before 072 the dispatcher
// worked on a snapshot of the outbox: terminal evidence committed by a query or stop after the
// snapshot (outbox DONE, command APPLIED) was overwritten by the next attempt, and an owner
// without dedup executed the command a second time (072 pre-release check, contract K0S).
func (s *Store) ClaimDispatchAttempt(domainID, commandID string) (bool, error) {
	n, err := s.db.Exec(`UPDATE outbox SET state='ATTEMPTING',attempt_count=attempt_count+1,updated_at=?
		WHERE domain_id=? AND command_id=? AND state!='DONE'
		AND NOT EXISTS (SELECT 1 FROM commands c WHERE c.domain_id=outbox.domain_id AND c.command_id=outbox.command_id AND c.status IN ('APPLIED','STOPPED'))`,
		now(), domainID, commandID)
	if err != nil {
		return false, err
	}
	if n == 0 {
		// terminal or already done: close the item so it is not offered again
		if _, err := s.db.Exec(`UPDATE outbox SET state='DONE',updated_at=? WHERE domain_id=? AND command_id=? AND state!='DONE'`, now(), domainID, commandID); err != nil {
			return false, err
		}
	}
	return n > 0, nil
}

// OutboxAttempt (release 083) returns the state, the attempt count and the time of the last change of a
// command's outbox item (set when an attempt is claimed and when it fails). ErrNotFound when the command
// has no outbox item.
func (s *Store) OutboxAttempt(domainID, commandID string) (string, int64, time.Time, error) {
	rows, err := s.db.Query(`SELECT state,attempt_count,updated_at FROM outbox WHERE domain_id=? AND command_id=?`, domainID, commandID)
	if err != nil {
		return "", 0, time.Time{}, err
	}
	if len(rows) == 0 {
		return "", 0, time.Time{}, ErrNotFound
	}
	st, _ := sqlitecgo.TextAt(rows[0], 0)
	n, _ := sqlitecgo.Int64At(rows[0], 1)
	ts, _ := sqlitecgo.TextAt(rows[0], 2)
	t, err := time.Parse(time.RFC3339Nano, ts)
	if err != nil {
		return "", 0, time.Time{}, fmt.Errorf("outbox %s updated_at %q: %w", commandID, ts, err)
	}
	return st, n, t, nil
}

func (s *Store) RecordDispatchFailure(domainID, commandID, lastErr string) error {
	// release 072: never reopen an item that terminal evidence closed meanwhile
	_, err := s.db.Exec(`UPDATE outbox SET state='RETRY',last_error=?,updated_at=? WHERE domain_id=? AND command_id=? AND state!='DONE'`, lastErr, now(), domainID, commandID)
	return err
}

// MarkOutboxConflict (release 068) stops re-dispatching a command whose owner reply
// contradicted its committed terminal outcome: re-sending cannot resolve a contradiction,
// and the conflict is escalated (trace EVIDENCE_CONFLICT) rather than retried forever.
func (s *Store) MarkOutboxConflict(domainID, commandID, reason string) error {
	_, err := s.db.Exec(`UPDATE outbox SET state='DONE',last_error=?,updated_at=? WHERE domain_id=? AND command_id=?`, "EVIDENCE_CONFLICT:"+reason, now(), domainID, commandID)
	return err
}

// RecordAttempt is retained for source compatibility; it records a failed retry.
func (s *Store) RecordAttempt(domainID, commandID, lastErr string) error {
	if err := s.BeginDispatchAttempt(domainID, commandID); err != nil {
		return err
	}
	return s.RecordDispatchFailure(domainID, commandID, lastErr)
}

func (s *Store) RecordReceipt(rc ReceiptRecord) error {
	statej, _ := js(rc.State)
	t := now()
	return s.db.WithTx(func(tx *sqlitecgo.Tx) error {
		rows, err := tx.Query(`SELECT intent_hash,status FROM commands WHERE domain_id=? AND command_id=?`, rc.DomainID, rc.CommandID)
		if err != nil {
			return err
		}
		if len(rows) == 0 {
			return ErrNotFound
		}
		ih, _ := sqlitecgo.TextAt(rows[0], 0)
		old, _ := sqlitecgo.TextAt(rows[0], 1)
		if ih != rc.IntentHash {
			return ErrIdentityConflict
		}
		if (old == "APPLIED" || old == "STOPPED") && (rc.Outcome == "APPLIED" || rc.Outcome == "STOPPED") && old != rc.Outcome {
			return fmt.Errorf("%w: %s %s -> %s", ErrTerminalReversal, rc.CommandID, old, rc.Outcome)
		}
		drows, err := tx.Query(`SELECT revision,dimension FROM domains WHERE domain_id=?`, rc.DomainID)
		if err != nil {
			return err
		}
		rev, _ := sqlitecgo.Int64At(drows[0], 0)
		newRev := rev + 1
		if _, err = tx.Exec(`UPDATE commands SET status=?,updated_at=? WHERE domain_id=? AND command_id=?`, rc.Outcome, t, rc.DomainID, rc.CommandID); err != nil {
			return err
		}
		if _, err = tx.Exec(`UPDATE outbox SET state='DONE',last_error='',updated_at=? WHERE domain_id=? AND command_id=?`, t, rc.DomainID, rc.CommandID); err != nil {
			return err
		}
		if _, err = tx.Exec(`INSERT INTO observations(domain_id,command_id,intent_hash,outcome,owner_version,receipt_version,state_json,applied_revision,created_at) VALUES(?,?,?,?,?,?,?,?,?)`, rc.DomainID, rc.CommandID, rc.IntentHash, rc.Outcome, rc.OwnerVersion, rc.ReceiptVersion, statej, newRev, t); err != nil {
			return err
		}
		if _, err = tx.Exec(`UPDATE domains SET revision=?,updated_at=? WHERE domain_id=?`, newRev, t, rc.DomainID); err != nil {
			return err
		}
		return nil
	})
}

func (s *Store) Counts() (map[string]int64, error) {
	out := map[string]int64{}
	for _, tbl := range []string{"domains", "commands", "outbox", "observations", "decisions", "probe_attempts"} {
		rows, err := s.db.Query(`SELECT COUNT(*) FROM ` + tbl)
		if err != nil {
			return nil, err
		}
		n, _ := sqlitecgo.Int64At(rows[0], 0)
		out[tbl] = n
	}
	return out, nil
}

func (s *Store) Pragmas() (map[string]string, error) {
	out := map[string]string{"sqlite_version": s.SQLiteVersion()}
	rows, err := s.db.Query("PRAGMA journal_mode")
	if err != nil {
		return nil, err
	}
	m, _ := sqlitecgo.TextAt(rows[0], 0)
	out["journal_mode"] = m
	rows, err = s.db.Query("PRAGMA synchronous")
	if err != nil {
		return nil, err
	}
	n, _ := sqlitecgo.Int64At(rows[0], 0)
	out["synchronous"] = fmt.Sprint(n)
	return out, nil
}

// ListCommandsForBelief returns durable commands that happened after the
// current anchor cutoff. Commands at or before the cutoff are already covered
// by the certified anchor and must not be re-applied by the online reducer.
func (s *Store) ListCommandsForBelief(domainID string, cutoff uint64) ([]CommandRecord, error) {
	rows, err := s.db.Query(`SELECT domain_id,command_id,generation,epoch,sequence,operation,value_json,intent_hash,reservation_json,status,decision,reason,trace_id FROM commands WHERE domain_id=? AND decision='ADMITTED' AND sequence>? ORDER BY sequence,command_id`, domainID, cutoff)
	if err != nil {
		return nil, err
	}
	out := make([]CommandRecord, 0, len(rows))
	for _, r := range rows {
		did, _ := sqlitecgo.TextAt(r, 0)
		cid, _ := sqlitecgo.TextAt(r, 1)
		gen, _ := sqlitecgo.Int64At(r, 2)
		ep, _ := sqlitecgo.Int64At(r, 3)
		seq, _ := sqlitecgo.Int64At(r, 4)
		op, _ := sqlitecgo.TextAt(r, 5)
		vj, _ := sqlitecgo.TextAt(r, 6)
		ih, _ := sqlitecgo.TextAt(r, 7)
		rj, _ := sqlitecgo.TextAt(r, 8)
		st, _ := sqlitecgo.TextAt(r, 9)
		dec, _ := sqlitecgo.TextAt(r, 10)
		reason, _ := sqlitecgo.TextAt(r, 11)
		tr, _ := sqlitecgo.TextAt(r, 12)
		v, e := parseVec(vj)
		if e != nil {
			return nil, e
		}
		res, e := parseVec(rj)
		if e != nil {
			return nil, e
		}
		out = append(out, CommandRecord{DomainID: did, CommandID: cid, Generation: uint64(gen), Epoch: uint64(ep), Sequence: uint64(seq), Operation: domain.Operation(op), Value: v, IntentHash: ih, Reservation: res, Status: st, Decision: dec, Reason: reason, TraceID: tr})
	}
	return out, nil
}

// RecordEvidence persists validated evidence. Non-terminal observations never
// release an obligation. Terminal evidence is monotone; APPLIED and STOPPED
// cannot reverse one another.
func (s *Store) RecordEvidence(rc ReceiptRecord, reason, kind string, markOutboxDone bool) (uint64, error) {
	switch rc.Outcome {
	case "APPLIED", "STOPPED":
		if rc.ReceiptVersion == 0 || rc.OwnerVersion == 0 {
			return 0, fmt.Errorf("terminal evidence requires positive receipt_version and owner_version")
		}
	case "UNCERTAIN", "PENDING", "NOT_FOUND":
	default:
		return 0, fmt.Errorf("unsupported evidence outcome %q", rc.Outcome)
	}
	statej, _ := js(rc.State)
	t := now()
	var newRev uint64
	err := s.db.WithTx(func(tx *sqlitecgo.Tx) error {
		rows, err := tx.Query(`SELECT intent_hash,status FROM commands WHERE domain_id=? AND command_id=?`, rc.DomainID, rc.CommandID)
		if err != nil {
			return err
		}
		if len(rows) == 0 {
			return ErrNotFound
		}
		ih, _ := sqlitecgo.TextAt(rows[0], 0)
		old, _ := sqlitecgo.TextAt(rows[0], 1)
		if ih != rc.IntentHash {
			return ErrIdentityConflict
		}
		terminal := rc.Outcome == "APPLIED" || rc.Outcome == "STOPPED"
		if (old == "APPLIED" || old == "STOPPED") && terminal && old != rc.Outcome {
			return fmt.Errorf("%w: %s %s -> %s", ErrTerminalReversal, rc.CommandID, old, rc.Outcome)
		}
		vers, err := tx.Query(`SELECT MAX(receipt_version) FROM observations WHERE domain_id=? AND command_id=?`, rc.DomainID, rc.CommandID)
		if err != nil {
			return err
		}
		if len(vers) > 0 && vers[0][0].Kind == sqlitecgo.Integer && rc.ReceiptVersion > 0 && rc.ReceiptVersion < uint64(vers[0][0].I64) {
			return fmt.Errorf("stale receipt version %d", rc.ReceiptVersion)
		}
		drows, err := tx.Query(`SELECT revision,dimension FROM domains WHERE domain_id=?`, rc.DomainID)
		if err != nil {
			return err
		}
		if len(drows) == 0 {
			return ErrNotFound
		}
		rev, _ := sqlitecgo.Int64At(drows[0], 0)
		dim, _ := sqlitecgo.Int64At(drows[0], 1)
		if terminal && dim > 0 && len(rc.State) != int(dim) {
			return fmt.Errorf("terminal evidence state dimension %d, want %d", len(rc.State), dim)
		}
		newRev = uint64(rev) + 1
		if terminal {
			if _, err = tx.Exec(`UPDATE commands SET status=?,updated_at=? WHERE domain_id=? AND command_id=?`, rc.Outcome, t, rc.DomainID, rc.CommandID); err != nil {
				return err
			}
		}
		if markOutboxDone {
			if _, err = tx.Exec(`UPDATE outbox SET state='DONE',last_error='',updated_at=? WHERE domain_id=? AND command_id=?`, t, rc.DomainID, rc.CommandID); err != nil {
				return err
			}
		}
		if _, err = tx.Exec(`INSERT INTO observations(domain_id,command_id,intent_hash,outcome,owner_version,receipt_version,state_json,reason,evidence_kind,applied_revision,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)`, rc.DomainID, rc.CommandID, rc.IntentHash, rc.Outcome, rc.OwnerVersion, rc.ReceiptVersion, statej, reason, kind, newRev, t); err != nil {
			return err
		}
		_, err = tx.Exec(`UPDATE domains SET revision=?,updated_at=? WHERE domain_id=?`, newRev, t, rc.DomainID)
		return err
	})
	return newRev, err
}

// RecordReceipt remains the P1 compatibility entry point.
func (s *Store) RecordReceiptV2(rc ReceiptRecord) error {
	_, err := s.RecordEvidence(rc, "ENDPOINT_REPLY", "RECEIPT", true)
	return err
}

// CommitBarrier atomically replaces the authority anchor with a certified E2
// barrier state and applies the terminal receipts covered by that cutoff.
func (s *Store) CommitBarrier(domainID string, generation uint64, cutoff uint64, anchor []int64, receipts []ReceiptRecord) (uint64, error) {
	anchorj, _ := js(anchor)
	t := now()
	var newRev uint64
	err := s.db.WithTx(func(tx *sqlitecgo.Tx) error {
		drows, err := tx.Query(`SELECT generation,revision,cutoff_sequence FROM domains WHERE domain_id=?`, domainID)
		if err != nil {
			return err
		}
		if len(drows) == 0 {
			return ErrNotFound
		}
		gen, _ := sqlitecgo.Int64At(drows[0], 0)
		if uint64(gen) != generation {
			return fmt.Errorf("barrier generation mismatch")
		}
		rev, _ := sqlitecgo.Int64At(drows[0], 1)
		oldCut, _ := sqlitecgo.Int64At(drows[0], 2)
		if cutoff < uint64(oldCut) {
			return fmt.Errorf("barrier cutoff regression")
		}
		newRev = uint64(rev) + 1
		for _, rc := range receipts {
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
			if rc.Outcome != "APPLIED" && rc.Outcome != "STOPPED" {
				return fmt.Errorf("barrier returned non-terminal outcome %s", rc.Outcome)
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
		_, err = tx.Exec(`UPDATE domains SET anchor_json=?,cutoff_sequence=?,revision=?,updated_at=? WHERE domain_id=?`, anchorj, cutoff, newRev, t, domainID)
		return err
	})
	return newRev, err
}

func (s *Store) GetBarrierLifecycle(domainID string, generation uint64, token string) (BarrierLifecycle, error) {
	rows, err := s.db.Query(`SELECT domain_id,token,generation,cutoff_sequence,start_revision,status,anchor_json,owner_version,complete_revision FROM authority_barrier_history WHERE domain_id=? AND generation=? AND token=?`, domainID, generation, token)
	if err != nil {
		return BarrierLifecycle{}, err
	}
	if len(rows) == 0 {
		return BarrierLifecycle{}, ErrNotFound
	}
	did, _ := sqlitecgo.TextAt(rows[0], 0)
	tok, _ := sqlitecgo.TextAt(rows[0], 1)
	gen, _ := sqlitecgo.Int64At(rows[0], 2)
	co, _ := sqlitecgo.Int64At(rows[0], 3)
	sr, _ := sqlitecgo.Int64At(rows[0], 4)
	st, _ := sqlitecgo.TextAt(rows[0], 5)
	aj, _ := sqlitecgo.TextAt(rows[0], 6)
	ov, _ := sqlitecgo.Int64At(rows[0], 7)
	cr, _ := sqlitecgo.Int64At(rows[0], 8)
	var anchor []int64
	_ = json.Unmarshal([]byte(aj), &anchor)
	return BarrierLifecycle{DomainID: did, Token: tok, Generation: uint64(gen), CutoffSequence: uint64(co), StartRevision: uint64(sr), Status: st, Anchor: anchor, OwnerVersion: uint64(ov), CompleteRevision: uint64(cr)}, nil
}

// PendingBarrier returns the one unresolved lifecycle for the domain. Completed
// barrier history is retained by token so an old retry cannot acquire a new meaning.
func (s *Store) PendingBarrier(domainID string) (BarrierLifecycle, bool, error) {
	rows, err := s.db.Query(`SELECT domain_id,token,generation,cutoff_sequence,start_revision,status,anchor_json,owner_version,complete_revision FROM authority_barrier_history WHERE domain_id=? AND status='PENDING' ORDER BY created_at LIMIT 2`, domainID)
	if err != nil {
		return BarrierLifecycle{}, false, err
	}
	if len(rows) == 0 {
		return BarrierLifecycle{}, false, nil
	}
	if len(rows) > 1 {
		return BarrierLifecycle{}, false, fmt.Errorf("multiple pending barriers for domain %s", domainID)
	}
	did, _ := sqlitecgo.TextAt(rows[0], 0)
	tok, _ := sqlitecgo.TextAt(rows[0], 1)
	gen, _ := sqlitecgo.Int64At(rows[0], 2)
	co, _ := sqlitecgo.Int64At(rows[0], 3)
	sr, _ := sqlitecgo.Int64At(rows[0], 4)
	st, _ := sqlitecgo.TextAt(rows[0], 5)
	aj, _ := sqlitecgo.TextAt(rows[0], 6)
	ov, _ := sqlitecgo.Int64At(rows[0], 7)
	cr, _ := sqlitecgo.Int64At(rows[0], 8)
	var anchor []int64
	_ = json.Unmarshal([]byte(aj), &anchor)
	return BarrierLifecycle{DomainID: did, Token: tok, Generation: uint64(gen), CutoffSequence: uint64(co), StartRevision: uint64(sr), Status: st, Anchor: anchor, OwnerVersion: uint64(ov), CompleteRevision: uint64(cr)}, true, nil
}

// BeginBarrier records a token-specific lifecycle before contacting the owner.
// The exact token is checked before any new full-frontier meaning can be assigned.
func (s *Store) BeginBarrier(domainID, token string, generation, cutoff, expectedRevision uint64) error {
	t := now()
	return s.db.WithTx(func(tx *sqlitecgo.Tx) error {
		dr, err := tx.Query(`SELECT generation,revision FROM domains WHERE domain_id=?`, domainID)
		if err != nil {
			return err
		}
		if len(dr) == 0 {
			return ErrNotFound
		}
		g, _ := sqlitecgo.Int64At(dr[0], 0)
		rv, _ := sqlitecgo.Int64At(dr[0], 1)
		if uint64(g) != generation {
			return fmt.Errorf("barrier generation mismatch")
		}
		// A retry of an existing token keeps its original cutoff; it is allowed
		// whatever the current revision (receipts may have committed while the
		// barrier was pending), otherwise a failed owner call would leave the
		// domain paused forever (release 062).
		ex, err := tx.Query(`SELECT cutoff_sequence,status FROM authority_barrier_history WHERE domain_id=? AND generation=? AND token=?`, domainID, generation, token)
		if err != nil {
			return err
		}
		if len(ex) > 0 {
			ec, _ := sqlitecgo.Int64At(ex[0], 0)
			st, _ := sqlitecgo.TextAt(ex[0], 1)
			if uint64(ec) != cutoff {
				return ErrIdentityConflict
			}
			if st == "COMPLETE" {
				return ErrBarrierAlreadyComplete
			}
			if st == "ABORTED" {
				return ErrBarrierAborted
			}
			if st == "PARTIAL" {
				return ErrBarrierPartial
			}
			return nil
		}
		if uint64(rv) != expectedRevision {
			return ErrRevisionConflict
		}
		pending, err := tx.Query(`SELECT token FROM authority_barrier_history WHERE domain_id=? AND generation=? AND status='PENDING'`, domainID, generation)
		if err != nil {
			return err
		}
		if len(pending) > 0 {
			return ErrBarrierInProgress
		}
		_, err = tx.Exec(`INSERT INTO authority_barrier_history(domain_id,generation,token,cutoff_sequence,start_revision,status,created_at,updated_at) VALUES(?,?,?,?,?,'PENDING',?,?)`, domainID, generation, token, cutoff, expectedRevision, t, t)
		return err
	})
}

// AbortBarrier ends a PENDING lifecycle without anchoring or sealing anything
// (release 062): used when the owner cannot certify every identity below the
// cutoff (e.g. a crash orphan answers UNCERTAIN). Admission resumes; the token
// stays in history as ABORTED so a retry cannot give it a new meaning.
func (s *Store) AbortBarrier(domainID string, generation uint64, token, reason string) error {
	_, err := s.db.Exec(`UPDATE authority_barrier_history SET status='ABORTED',updated_at=? WHERE domain_id=? AND generation=? AND token=? AND status='PENDING'`, now(), domainID, generation, token)
	return err
}

// CommitBarrierLifecycle commits only the exact token/cutoff lifecycle and keeps
// completed history so retries remain replayable after later barriers.
func (s *Store) CommitBarrierLifecycle(domainID, token string, generation, cutoff uint64, anchor []int64, ownerVersion uint64, receipts []ReceiptRecord) (uint64, error) {
	rev, _, err := s.CommitBarrierLifecycleSeal(domainID, token, generation, cutoff, anchor, ownerVersion, receipts, false)
	return rev, err
}

// CommitBarrierLifecycleSeal (release 061) optionally seals unreported
// identities: after an owner-certified cutoff whose receipt lists every owner
// claim at or below the cutoff (all terminal - the commit already refuses
// non-terminal outcomes), an admitted command at or below the cutoff that the
// owner did not report was never claimed and can never take effect. It is
// recorded STOPPED with evidence kind BARRIER_SEALED so that clients learn the
// terminal outcome (fence-then-retry). With seal=false the 060 behaviour is
// kept: such commands stay non-terminal and are only excluded from the belief.
func (s *Store) CommitBarrierLifecycleSeal(domainID, token string, generation, cutoff uint64, anchor []int64, ownerVersion uint64, receipts []ReceiptRecord, seal bool) (uint64, []string, error) {
	anchorj, _ := js(anchor)
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
		if bs == "COMPLETE" {
			return ErrBarrierAlreadyComplete
		}
		if bs == "PARTIAL" {
			return ErrBarrierPartial
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
		for _, rc := range receipts {
			if rc.Outcome != "APPLIED" && rc.Outcome != "STOPPED" {
				return fmt.Errorf("barrier returned non-terminal outcome %s", rc.Outcome)
			}
			if rc.ReceiptVersion == 0 || rc.OwnerVersion == 0 {
				return fmt.Errorf("barrier terminal receipt missing version")
			}
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
		if seal {
			reported := map[string]bool{}
			for _, rc := range receipts {
				reported[rc.CommandID] = true
			}
			rows, er := tx.Query(`SELECT command_id,intent_hash FROM commands WHERE domain_id=? AND decision='ADMITTED' AND sequence<=? AND status NOT IN ('APPLIED','STOPPED')`, domainID, cutoff)
			if er != nil {
				return er
			}
			for _, r := range rows {
				cid, _ := sqlitecgo.TextAt(r, 0)
				ih, _ := sqlitecgo.TextAt(r, 1)
				if reported[cid] {
					continue
				}
				if _, er = tx.Exec(`UPDATE commands SET status='STOPPED',updated_at=? WHERE domain_id=? AND command_id=?`, t, domainID, cid); er != nil {
					return er
				}
				if _, er = tx.Exec(`UPDATE outbox SET state='DONE',last_error='',updated_at=? WHERE domain_id=? AND command_id=?`, t, domainID, cid); er != nil {
					return er
				}
				if _, er = tx.Exec(`INSERT INTO observations(domain_id,command_id,intent_hash,outcome,owner_version,receipt_version,state_json,reason,evidence_kind,applied_revision,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)`, domainID, cid, ih, "STOPPED", ownerVersion, ownerVersion, anchorj, "BARRIER_SEALED_UNCLAIMED", "BARRIER_SEALED", newRev, t); er != nil {
					return er
				}
				sealed = append(sealed, cid)
			}
		}
		if _, err = tx.Exec(`UPDATE domains SET anchor_json=?,cutoff_sequence=?,revision=?,updated_at=? WHERE domain_id=?`, anchorj, cutoff, newRev, t, domainID); err != nil {
			return err
		}
		_, err = tx.Exec(`UPDATE authority_barrier_history SET status='COMPLETE',anchor_json=?,owner_version=?,complete_revision=?,updated_at=? WHERE domain_id=? AND generation=? AND token=?`, anchorj, ownerVersion, newRev, t, domainID, generation, token)
		return err
	})
	if err != nil {
		sealed = nil
	}
	return newRev, sealed, err
}

// CompactTerminalSingleton advances the local analysis anchor only when every
// admitted command after the current cutoff is terminal and the reducer has
// established one unique state. Audit rows remain intact; only active analysis
// history is projected behind the cutoff.
func (s *Store) CompactTerminalSingleton(domainID string, expectedRevision, newCutoff uint64, anchor []int64) (uint64, error) {
	anchorj, _ := js(anchor)
	t := now()
	var newRev uint64
	err := s.db.WithTx(func(tx *sqlitecgo.Tx) error {
		dr, err := tx.Query(`SELECT revision,cutoff_sequence FROM domains WHERE domain_id=?`, domainID)
		if err != nil {
			return err
		}
		if len(dr) == 0 {
			return ErrNotFound
		}
		rv, _ := sqlitecgo.Int64At(dr[0], 0)
		oldCut, _ := sqlitecgo.Int64At(dr[0], 1)
		if uint64(rv) != expectedRevision {
			return ErrRevisionConflict
		}
		if newCutoff <= uint64(oldCut) {
			return nil
		}
		rows, err := tx.Query(`SELECT COUNT(*),SUM(CASE WHEN status NOT IN ('APPLIED','STOPPED') THEN 1 ELSE 0 END),MAX(sequence) FROM commands WHERE domain_id=? AND decision='ADMITTED' AND sequence>?`, domainID, oldCut)
		if err != nil {
			return err
		}
		count, _ := sqlitecgo.Int64At(rows[0], 0)
		var nonterm int64
		if rows[0][1].Kind == sqlitecgo.Integer {
			nonterm = rows[0][1].I64
		}
		var maxSeq int64
		if rows[0][2].Kind == sqlitecgo.Integer {
			maxSeq = rows[0][2].I64
		}
		if count == 0 || nonterm != 0 || uint64(maxSeq) != newCutoff {
			return fmt.Errorf("terminal compaction precondition failed")
		}
		newRev = expectedRevision + 1
		_, err = tx.Exec(`UPDATE domains SET anchor_json=?,cutoff_sequence=?,revision=?,updated_at=? WHERE domain_id=?`, anchorj, newCutoff, newRev, t, domainID)
		return err
	})
	return newRev, err
}

func (s *Store) OutboxAttemptCount(domainID, commandID string) (int64, error) {
	rows, err := s.db.Query(`SELECT attempt_count FROM outbox WHERE domain_id=? AND command_id=?`, domainID, commandID)
	if err != nil {
		return 0, err
	}
	if len(rows) == 0 {
		return 0, ErrNotFound
	}
	return sqlitecgo.Int64At(rows[0], 0)
}

func (s *Store) PendingOutboxCount() (int64, error) {
	rows, err := s.db.Query(`SELECT COUNT(*) FROM outbox WHERE state!='DONE'`)
	if err != nil {
		return 0, err
	}
	if len(rows) == 0 {
		return 0, nil
	}
	return sqlitecgo.Int64At(rows[0], 0)
}

func (s *Store) MaxAdmittedSequence(domainID string) (uint64, error) {
	rows, err := s.db.Query(`SELECT MAX(sequence) FROM commands WHERE domain_id=? AND decision='ADMITTED'`, domainID)
	if err != nil {
		return 0, err
	}
	if len(rows) == 0 || rows[0][0].Kind == sqlitecgo.Null {
		return 0, nil
	}
	v, err := sqlitecgo.Int64At(rows[0], 0)
	return uint64(v), err
}

// RecordDecisionDetailed records policy/probe accounting for P3 decisions.
func (s *Store) RecordDecisionDetailed(domainID, commandID, verdict, analyzer, reason string, revision uint64, policy string, probeCount, probeCostUS, selectorNS int64) error {
	_, err := s.db.Exec(
		`INSERT INTO decisions(domain_id,command_id,verdict,analyzer,reason,revision,policy,probe_count,probe_cost_us,selector_ns,analyzed_revision,commit_expected_revision,committed_revision,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
		domainID, commandID, verdict, analyzer, reason, revision, policy, probeCount, probeCostUS, selectorNS, revision, revision, revision, now(),
	)
	return err
}

// AnnotateLatestDecision adds P3 policy/probe metadata to the most recent
// durable decision for a logical candidate. The decision itself is already
// committed atomically with an ADMITTED reservation by ReserveAndEnqueue.
func (s *Store) AnnotateLatestDecision(domainID, commandID, policy string, probeCount, probeCostUS, selectorNS int64) error {
	_, err := s.db.Exec(`UPDATE decisions SET policy=?,probe_count=?,probe_cost_us=?,selector_ns=? WHERE id=(SELECT id FROM decisions WHERE domain_id=? AND command_id=? ORDER BY id DESC LIMIT 1)`, policy, probeCount, probeCostUS, selectorNS, domainID, commandID)
	return err
}

func (s *Store) ReserveProbeAttempts(records []ProbeRecord) error {
	if len(records) == 0 {
		return nil
	}
	t := now()
	return s.db.WithTx(func(tx *sqlitecgo.Tx) error {
		for _, p := range records {
			_, err := tx.Exec(`INSERT INTO probe_attempts(domain_id,candidate_id,candidate_hash,probe_index,queried_command_id,policy,expected_cost_us,actual_cost_us,outcome,informative,belief_changed,verdict_changed,revision_before,revision_after,reason,status,started_at,completed_at,created_at) VALUES(?,?,?,?,?,?,?,0,'UNRESOLVED',0,0,0,?,?,?,'RESERVED','','',?)`, p.DomainID, p.CandidateID, p.CandidateHash, p.ProbeIndex, p.QueriedCommand, p.Policy, p.ExpectedCostUS, p.RevisionBefore, p.RevisionBefore, "RESERVED_BEFORE_NETWORK_SEND", t)
			if err != nil {
				return err
			}
		}
		return nil
	})
}

func (s *Store) MarkProbeStarted(domainID, candidateID, candidateHash string, index int64) error {
	r, err := s.db.Exec(`UPDATE probe_attempts SET status='STARTED',started_at=?,reason='NETWORK_QUERY_STARTED' WHERE domain_id=? AND candidate_id=? AND candidate_hash=? AND probe_index=? AND status='RESERVED'`, now(), domainID, candidateID, candidateHash, index)
	if err != nil {
		return err
	}
	_ = r
	return nil
}

func (s *Store) CompleteProbeAttempt(p ProbeRecord) error {
	inf, beliefChanged, verdictChanged := int64(0), int64(0), int64(0)
	if p.Informative {
		inf = 1
	}
	if p.BeliefChanged {
		beliefChanged = 1
	}
	if p.VerdictChanged {
		verdictChanged = 1
	}
	_, err := s.db.Exec(`UPDATE probe_attempts SET actual_cost_us=?,outcome=?,informative=?,belief_changed=?,verdict_changed=?,revision_after=?,reason=?,status='COMPLETED',completed_at=?,query_request_bytes=?,query_response_bytes=? WHERE domain_id=? AND candidate_id=? AND candidate_hash=? AND probe_index=? AND status IN ('RESERVED','STARTED')`, p.ActualCostUS, p.Outcome, inf, beliefChanged, verdictChanged, p.RevisionAfter, p.Reason, now(), p.QueryRequestBytes, p.QueryResponseBytes, p.DomainID, p.CandidateID, p.CandidateHash, p.ProbeIndex)
	return err
}

// RecordProbeAttempt is retained for compatibility with tests/tools that create
// an already-completed probe record in one step.
func (s *Store) RecordProbeAttempt(p ProbeRecord) error {
	if p.Status == "" {
		p.Status = "COMPLETED"
	}
	if err := s.ReserveProbeAttempts([]ProbeRecord{p}); err != nil {
		return err
	}
	return s.CompleteProbeAttempt(p)
}

func (s *Store) ProbeUsage(domainID, candidateID, candidateHash string) (count int64, totalCostUS int64, err error) {
	rows, err := s.db.Query(`SELECT COUNT(*),COALESCE(SUM(actual_cost_us),0) FROM probe_attempts WHERE domain_id=? AND candidate_id=? AND candidate_hash=?`, domainID, candidateID, candidateHash)
	if err != nil {
		return 0, 0, err
	}
	if len(rows) == 0 {
		return 0, 0, nil
	}
	count, _ = sqlitecgo.Int64At(rows[0], 0)
	totalCostUS, _ = sqlitecgo.Int64At(rows[0], 1)
	return
}
func (s *Store) ProbeQueriedIDs(domainID, candidateID, candidateHash string) (map[string]bool, error) {
	rows, err := s.db.Query(`SELECT DISTINCT queried_command_id FROM probe_attempts WHERE domain_id=? AND candidate_id=? AND candidate_hash=?`, domainID, candidateID, candidateHash)
	if err != nil {
		return nil, err
	}
	out := map[string]bool{}
	for _, r := range rows {
		id, _ := sqlitecgo.TextAt(r, 0)
		out[id] = true
	}
	return out, nil
}

type DecisionAudit struct {
	Verdict     string `json:"verdict"`
	Analyzer    string `json:"analyzer"`
	Reason      string `json:"reason"`
	Revision    uint64 `json:"revision"`
	Policy      string `json:"policy"`
	ProbeCount  int64  `json:"probeCount"`
	ProbeCostUS int64  `json:"probeCostUs"`
	SelectorNS  int64  `json:"selectorNs"`
}

func (s *Store) ListProbeAttempts(domainID, candidateID, candidateHash string) ([]ProbeRecord, error) {
	rows, err := s.db.Query(`SELECT domain_id,candidate_id,candidate_hash,probe_index,queried_command_id,policy,expected_cost_us,actual_cost_us,outcome,informative,belief_changed,verdict_changed,revision_before,revision_after,reason,status FROM probe_attempts WHERE domain_id=? AND candidate_id=? AND candidate_hash=? ORDER BY probe_index`, domainID, candidateID, candidateHash)
	if err != nil {
		return nil, err
	}
	out := make([]ProbeRecord, 0, len(rows))
	for _, r := range rows {
		did, _ := sqlitecgo.TextAt(r, 0)
		cid, _ := sqlitecgo.TextAt(r, 1)
		ch, _ := sqlitecgo.TextAt(r, 2)
		idx, _ := sqlitecgo.Int64At(r, 3)
		qid, _ := sqlitecgo.TextAt(r, 4)
		pol, _ := sqlitecgo.TextAt(r, 5)
		ex, _ := sqlitecgo.Int64At(r, 6)
		ac, _ := sqlitecgo.Int64At(r, 7)
		outcome, _ := sqlitecgo.TextAt(r, 8)
		inf, _ := sqlitecgo.Int64At(r, 9)
		bc, _ := sqlitecgo.Int64At(r, 10)
		vc, _ := sqlitecgo.Int64At(r, 11)
		rb, _ := sqlitecgo.Int64At(r, 12)
		ra, _ := sqlitecgo.Int64At(r, 13)
		reason, _ := sqlitecgo.TextAt(r, 14)
		status, _ := sqlitecgo.TextAt(r, 15)
		out = append(out, ProbeRecord{DomainID: did, CandidateID: cid, CandidateHash: ch, ProbeIndex: idx, QueriedCommand: qid, Policy: pol, ExpectedCostUS: ex, ActualCostUS: ac, Outcome: outcome, Informative: inf != 0, BeliefChanged: bc != 0, VerdictChanged: vc != 0, RevisionBefore: uint64(rb), RevisionAfter: uint64(ra), Reason: reason, Status: status})
	}
	return out, nil
}

func (s *Store) LatestDecision(domainID, commandID string) (DecisionAudit, error) {
	rows, err := s.db.Query(`SELECT verdict,analyzer,reason,revision,policy,probe_count,probe_cost_us,selector_ns FROM decisions WHERE domain_id=? AND command_id=? ORDER BY id DESC LIMIT 1`, domainID, commandID)
	if err != nil {
		return DecisionAudit{}, err
	}
	if len(rows) == 0 {
		return DecisionAudit{}, ErrNotFound
	}
	v, _ := sqlitecgo.TextAt(rows[0], 0)
	a, _ := sqlitecgo.TextAt(rows[0], 1)
	re, _ := sqlitecgo.TextAt(rows[0], 2)
	rv, _ := sqlitecgo.Int64At(rows[0], 3)
	pol, _ := sqlitecgo.TextAt(rows[0], 4)
	pc, _ := sqlitecgo.Int64At(rows[0], 5)
	cost, _ := sqlitecgo.Int64At(rows[0], 6)
	sn, _ := sqlitecgo.Int64At(rows[0], 7)
	return DecisionAudit{Verdict: v, Analyzer: a, Reason: re, Revision: uint64(rv), Policy: pol, ProbeCount: pc, ProbeCostUS: cost, SelectorNS: sn}, nil
}

// CommandCreatedTimes returns created_at for every non-terminal command of a
// domain (release 061: ambiguity age for the EVICON resolution policy).
func (s *Store) CommandCreatedTimes(domainID string) (map[string]time.Time, error) {
	rows, err := s.db.Query(`SELECT command_id,created_at FROM commands WHERE domain_id=? AND status NOT IN ('APPLIED','STOPPED')`, domainID)
	if err != nil {
		return nil, err
	}
	out := make(map[string]time.Time, len(rows))
	for _, r := range rows {
		id, _ := sqlitecgo.TextAt(r, 0)
		ts, _ := sqlitecgo.TextAt(r, 1)
		t, err := time.Parse(time.RFC3339Nano, ts)
		if err != nil {
			return nil, fmt.Errorf("command %s created_at %q: %w", id, ts, err)
		}
		out[id] = t
	}
	return out, nil
}

// Package jobs implements the application queue colocated with the grant owner.
// A grant and its job registration commit together; execution may repeat after
// lease expiry, but a fenced result has one durable publication per command.
package jobs

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"evicon/internal/domain"
	"evicon/internal/fault"
	"evicon/internal/protocol"
	"evicon/internal/sqlitecgo"
)

const Rounds = 1024

var (
	ErrLease          = errors.New("stale or expired lease")
	ErrResultMismatch = errors.New("job result checksum mismatch")
	ErrInvalidDigest  = errors.New("invalid SHA256 digest")
)

type Job struct {
	DomainID     string `json:"domain_id"`
	CommandID    string `json:"command_id"`
	IntentHash   string `json:"intent_hash"`
	Status       string `json:"job_status"`
	Payload      string `json:"payload"`
	Rounds       int64  `json:"rounds"`
	Attempts     int64  `json:"attempts"`
	LeaseToken   string `json:"lease_token,omitempty"`
	LeaseUntil   int64  `json:"lease_until_unix_ns"`
	Result       string `json:"result_sha256,omitempty"`
	RegisteredAt string `json:"registered_at"`
	CompletedAt  string `json:"completed_at,omitempty"`
}

type Store struct {
	db       *sqlitecgo.DB
	domainID string
	faults   *fault.Controller
}

// Configure pins application mode to the DB. Enabling on a nonempty core DB
// would silently omit older grants, so migration requires a fresh episode.
func Configure(db *sqlitecgo.DB, enabled bool) error {
	if _, e := db.Exec(`CREATE TABLE IF NOT EXISTS application_meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL)`); e != nil {
		return e
	}
	return db.WithTx(func(tx *sqlitecgo.Tx) error {
		rows, e := tx.Query(`SELECT value FROM application_meta WHERE key='jobs_enabled'`)
		if e != nil {
			return e
		}
		if len(rows) > 0 {
			v, _ := sqlitecgo.Int64At(rows[0], 0)
			if (v == 1) != enabled {
				return fmt.Errorf("job mode differs from durable owner configuration")
			}
			return nil
		}
		if enabled {
			rows, e = tx.Query(`SELECT count(*) FROM effects`)
			if e != nil {
				return e
			}
			n, _ := sqlitecgo.Int64At(rows[0], 0)
			if n != 0 {
				return fmt.Errorf("job mode needs a fresh owner database")
			}
		}
		if _, e = tx.Exec(`INSERT INTO application_meta VALUES('jobs_enabled',?)`, enabled); e != nil {
			return e
		}
		_, e = tx.Exec(`CREATE TABLE IF NOT EXISTS jobs(domain_id TEXT NOT NULL,command_id TEXT NOT NULL,intent_hash TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('QUEUED','RUNNING','COMPLETED')),payload TEXT NOT NULL,rounds INTEGER NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,lease_token TEXT NOT NULL DEFAULT '',lease_until INTEGER NOT NULL DEFAULT 0,result_sha256 TEXT NOT NULL DEFAULT '',registered_at TEXT NOT NULL,completed_at TEXT NOT NULL DEFAULT '',PRIMARY KEY(domain_id,command_id),FOREIGN KEY(domain_id,command_id) REFERENCES effects(domain_id,command_id))`)
		return e
	})
}

func Enqueue(tx *sqlitecgo.Tx, req protocol.CommandRequest, at string) error {
	// Configuration SET is still supported by the core, but is not a job grant.
	if req.Operation != domain.OpAdd {
		return nil
	}
	_, e := tx.Exec(`INSERT INTO jobs(domain_id,command_id,intent_hash,status,payload,rounds,registered_at) VALUES(?,?,?,'QUEUED',?,?,?)`, req.DomainID, req.CommandID, req.IntentHash, "EviCon-CHECKSUM-V1\n"+req.IntentHash+"\n", Rounds, at)
	return e
}
func New(db *sqlitecgo.DB, domainID string, fc *fault.Controller) *Store {
	return &Store{db: db, domainID: domainID, faults: fc}
}

const columns = `domain_id,command_id,intent_hash,status,payload,rounds,attempts,lease_token,lease_until,result_sha256,registered_at,completed_at`

func decode(r sqlitecgo.Row) Job {
	return Job{r[0].Text, r[1].Text, r[2].Text, r[3].Text, r[4].Text, r[5].I64, r[6].I64, r[7].Text, r[8].I64, r[9].Text, r[10].Text, r[11].Text}
}
func (s *Store) Get(id string) (Job, error) {
	rows, e := s.db.Query(`SELECT `+columns+` FROM jobs WHERE domain_id=? AND command_id=?`, s.domainID, id)
	if e != nil {
		return Job{}, e
	}
	if len(rows) == 0 {
		return Job{}, os.ErrNotExist
	}
	return decode(rows[0]), nil
}
func (s *Store) Claim(lease time.Duration) (Job, error) {
	if lease < 50*time.Millisecond || lease > time.Minute {
		return Job{}, fmt.Errorf("lease must be 50ms..1m")
	}
	var j Job
	e := s.db.WithTx(func(tx *sqlitecgo.Tx) error {
		now := time.Now().UnixNano()
		rows, e := tx.Query(`SELECT `+columns+` FROM jobs WHERE domain_id=? AND (status='QUEUED' OR (status='RUNNING' AND lease_until<=?)) ORDER BY registered_at,command_id LIMIT 1`, s.domainID, now)
		if e != nil {
			return e
		}
		if len(rows) == 0 {
			return os.ErrNotExist
		}
		j = decode(rows[0])
		var token [16]byte
		if _, e = rand.Read(token[:]); e != nil {
			return e
		}
		j.Status = "RUNNING"
		j.Attempts++
		j.LeaseToken = hex.EncodeToString(token[:])
		j.LeaseUntil = now + int64(lease)
		_, e = tx.Exec(`UPDATE jobs SET status='RUNNING',attempts=?,lease_token=?,lease_until=? WHERE domain_id=? AND command_id=?`, j.Attempts, j.LeaseToken, j.LeaseUntil, s.domainID, j.CommandID)
		return e
	})
	if e == nil && s.faults != nil {
		s.faults.Hit("AFTER_JOB_LEASE_COMMIT_BEFORE_REPLY", j.CommandID)
	}
	return j, e
}
func (s *Store) Complete(id, token, digest string) (Job, error) {
	raw, e := hex.DecodeString(digest)
	if e != nil || len(raw) != 32 || digest != strings.ToLower(digest) {
		return Job{}, ErrInvalidDigest
	}
	var j Job
	e = s.db.WithTx(func(tx *sqlitecgo.Tx) error {
		rows, e := tx.Query(`SELECT `+columns+` FROM jobs WHERE domain_id=? AND command_id=?`, s.domainID, id)
		if e != nil {
			return e
		}
		if len(rows) == 0 {
			return os.ErrNotExist
		}
		j = decode(rows[0])
		if token == "" || token != j.LeaseToken {
			return ErrLease
		}
		expected, er := Checksum(j)
		if er != nil {
			return er
		}
		if digest != expected {
			return ErrResultMismatch
		}
		if j.Status == "COMPLETED" {
			if j.Result != digest {
				return ErrLease
			}
			return nil
		}
		if j.Status != "RUNNING" || j.LeaseUntil <= time.Now().UnixNano() {
			return ErrLease
		}
		j.Status = "COMPLETED"
		j.Result = digest
		j.CompletedAt = time.Now().UTC().Format(time.RFC3339Nano)
		_, e = tx.Exec(`UPDATE jobs SET status='COMPLETED',result_sha256=?,completed_at=? WHERE domain_id=? AND command_id=?`, digest, j.CompletedAt, s.domainID, id)
		return e
	})
	if e == nil && s.faults != nil {
		s.faults.Hit("AFTER_JOB_RESULT_COMMIT_BEFORE_REPLY", id)
	}
	return j, e
}
func Checksum(j Job) (string, error) {
	if j.Rounds < 1 || j.Rounds > Rounds || len(j.Payload) > 4096 {
		return "", fmt.Errorf("unsupported checksum task bounds")
	}
	h := sha256.New()
	for i := int64(0); i < j.Rounds; i++ {
		_, _ = io.WriteString(h, j.Payload)
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
func (s *Store) Register(mux *http.ServeMux) {
	respond := func(w http.ResponseWriter, code int, v any) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(code)
		_ = json.NewEncoder(w).Encode(v)
	}
	failure := func(w http.ResponseWriter, e error) {
		code := 500
		if errors.Is(e, os.ErrNotExist) {
			code = 404
		}
		if errors.Is(e, ErrLease) {
			code = 409
		}
		if errors.Is(e, ErrResultMismatch) {
			code = 422
		}
		if errors.Is(e, ErrInvalidDigest) {
			code = 400
		}
		respond(w, code, map[string]string{"error": e.Error()})
	}
	mux.HandleFunc("/v1/jobs/", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" {
			w.WriteHeader(405)
			return
		}
		j, e := s.Get(strings.TrimPrefix(r.URL.Path, "/v1/jobs/"))
		if e != nil {
			failure(w, e)
			return
		}
		j.LeaseToken = ""
		respond(w, 200, j)
	})
	mux.HandleFunc("/v1/job-claims", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			w.WriteHeader(405)
			return
		}
		var b struct {
			LeaseMS int64 `json:"lease_ms"`
		}
		if e := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&b); e != nil || b.LeaseMS < 50 || b.LeaseMS > 60000 {
			respond(w, 400, map[string]string{"error": "lease_ms must be 50..60000"})
			return
		}
		j, e := s.Claim(time.Duration(b.LeaseMS) * time.Millisecond)
		if errors.Is(e, os.ErrNotExist) {
			w.WriteHeader(204)
			return
		}
		if e != nil {
			failure(w, e)
			return
		}
		respond(w, 200, j)
	})
	mux.HandleFunc("/v1/job-results", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			w.WriteHeader(405)
			return
		}
		var b struct {
			CommandID string `json:"command_id"`
			Token     string `json:"lease_token"`
			Digest    string `json:"result_sha256"`
		}
		if e := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&b); e != nil {
			respond(w, 400, map[string]string{"error": e.Error()})
			return
		}
		if strings.TrimSpace(b.CommandID) == "" || strings.TrimSpace(b.Token) == "" || strings.TrimSpace(b.Digest) == "" {
			respond(w, 400, map[string]string{"error": "command_id, lease_token, and result_sha256 are required"})
			return
		}
		j, e := s.Complete(b.CommandID, b.Token, b.Digest)
		if e != nil {
			failure(w, e)
			return
		}
		j.LeaseToken = ""
		respond(w, 200, j)
	})
}

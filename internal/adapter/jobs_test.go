package adapter

import (
	"errors"
	"evicon/internal/domain"
	"evicon/internal/jobs"
	"evicon/internal/protocol"
	"evicon/internal/sqlitecgo"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestJobGrantAtomicReplayAndFencedResult(t *testing.T) {
	for _, profile := range []string{"E2", "E3"} {
		t.Run(profile, func(t *testing.T) {
			dir := t.TempDir()
			bound := domain.HardBound{ComponentMin: []int64{0}, ComponentMax: []int64{12}}
			var apply func(protocol.CommandRequest) (protocol.Receipt, error)
			var stop func(protocol.CommandRequest, string) (protocol.Receipt, error)
			var store *jobs.Store
			if profile == "E3" {
				o, e := OpenE3(E3Config{DBPath: filepath.Join(dir, "o.db"), DomainID: "d", Generation: 1, InstalledEpoch: 1, InitialState: []int64{0}, Bound: bound, Jobs: true})
				if e != nil {
					t.Fatal(e)
				}
				defer o.Close()
				apply = o.Apply
				stop = o.ForceStop
				store = o.JobStore()
			} else {
				o, e := OpenE12(E12Config{Profile: profile, ClaimDBPath: filepath.Join(dir, "c.db"), EffectsDBPath: filepath.Join(dir, "o.db"), DomainID: "d", Generation: 1, InstalledEpoch: 1, InitialState: []int64{0}, Bound: bound, Jobs: true})
				if e != nil {
					t.Fatal(e)
				}
				defer o.Close()
				apply = o.Apply
				stop = o.ForceStop
				store = o.JobStore()
			}
			r := req("job", domain.OpAdd, 3, 1)
			for i := 0; i < 2; i++ {
				rc, e := apply(r)
				if e != nil || rc.Outcome != "APPLIED" {
					t.Fatalf("%+v %v", rc, e)
				}
			}
			if _, e := stop(req("rejected", domain.OpAdd, 2, 2), "VALIDATION_REJECT"); e != nil {
				t.Fatal(e)
			}
			if _, e := store.Get("rejected"); e == nil {
				t.Fatal("STOPPED received a job")
			}
			first, e := store.Claim(50 * time.Millisecond)
			if e != nil {
				t.Fatal(e)
			}
			time.Sleep(60 * time.Millisecond)
			second, e := store.Claim(time.Second)
			if e != nil {
				t.Fatal(e)
			}
			if second.Attempts != 2 || second.LeaseToken == first.LeaseToken {
				t.Fatal("lease not fenced")
			}
			digest, _ := jobs.Checksum(second)
			if _, e = store.Complete(second.CommandID, second.LeaseToken, strings.Repeat("0", 64)); !errors.Is(e, jobs.ErrResultMismatch) {
				t.Fatal("incorrect checksum accepted", e)
			}
			if j, ge := store.Get(second.CommandID); ge != nil || j.Status != "RUNNING" {
				t.Fatal("checksum rejection changed durable lifecycle", j, ge)
			}
			if _, e = store.Complete(first.CommandID, first.LeaseToken, digest); !errors.Is(e, jobs.ErrLease) {
				t.Fatal("stale worker accepted", e)
			}
			for i := 0; i < 2; i++ {
				j, e := store.Complete(second.CommandID, second.LeaseToken, digest)
				if e != nil || j.Status != "COMPLETED" {
					t.Fatal(j, e)
				}
			}
			if _, e = store.Claim(time.Second); e == nil {
				t.Fatal("completed job leased again")
			}
		})
	}
}
func TestJobConcurrentClaimAndModePin(t *testing.T) {
	path := filepath.Join(t.TempDir(), "o.db")
	cfg := E3Config{DBPath: path, DomainID: "d", Generation: 1, InstalledEpoch: 1, InitialState: []int64{0}, Bound: domain.HardBound{ComponentMax: []int64{12}}, Jobs: true}
	o, e := OpenE3(cfg)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = o.Apply(req("job", domain.OpAdd, 1, 1)); e != nil {
		t.Fatal(e)
	}
	var wg sync.WaitGroup
	var mu sync.Mutex
	claims := 0
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, e := o.JobStore().Claim(time.Second); e == nil {
				mu.Lock()
				claims++
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	if claims != 1 {
		t.Fatal(claims)
	}
	o.Close()
	cfg.Jobs = false
	if bad, e := OpenE3(cfg); e == nil {
		bad.Close()
		t.Fatal("durable mode changed")
	}
	cfg.Jobs = true
	o, e = OpenE3(cfg)
	if e != nil {
		t.Fatal(e)
	}
	defer o.Close()
	j, e := o.JobStore().Get("job")
	if e != nil || j.Attempts != 1 {
		t.Fatal(j, e)
	}
}

// An application insert failure must not leave a committed grant behind.
func TestJobRegistrationFailureRollsBackGrant(t *testing.T) {
	for _, profile := range []string{"E2", "E3"} {
		t.Run(profile, func(t *testing.T) {
			dir := t.TempDir()
			b := domain.HardBound{ComponentMax: []int64{12}}
			var apply func(protocol.CommandRequest) (protocol.Receipt, error)
			var db *sqlitecgo.DB
			if profile == "E3" {
				o, e := OpenE3(E3Config{DBPath: filepath.Join(dir, "o.db"), DomainID: "d", Generation: 1, InstalledEpoch: 1, InitialState: []int64{0}, Bound: b, Jobs: true})
				if e != nil {
					t.Fatal(e)
				}
				defer o.Close()
				db = o.db
				apply = o.Apply
			} else {
				o, e := OpenE12(E12Config{Profile: profile, ClaimDBPath: filepath.Join(dir, "c.db"), EffectsDBPath: filepath.Join(dir, "o.db"), DomainID: "d", Generation: 1, InstalledEpoch: 1, InitialState: []int64{0}, Bound: b, Jobs: true})
				if e != nil {
					t.Fatal(e)
				}
				defer o.Close()
				db = o.effects
				apply = o.Apply
			}
			if _, e := db.Exec(`CREATE TRIGGER reject_registration BEFORE INSERT ON jobs BEGIN SELECT RAISE(ABORT,'test registration failure'); END`); e != nil {
				t.Fatal(e)
			}
			if _, e := apply(req("job", domain.OpAdd, 3, 1)); e == nil {
				t.Fatal("injected failure ignored")
			}
			for _, table := range []string{"jobs", "effects"} {
				rows, e := db.Query("SELECT count(*) FROM " + table)
				if e != nil || rows[0][0].I64 != 0 {
					t.Fatal(table, rows, e)
				}
			}
			rows, e := db.Query("SELECT state_json FROM domains")
			if e != nil || rows[0][0].Text != "[0]" {
				t.Fatal(rows, e)
			}
		})
	}
}

func TestJobResultHTTPStatusMapping(t *testing.T) {
	dir := t.TempDir()
	o, err := OpenE3(E3Config{DBPath: filepath.Join(dir, "o.db"), DomainID: "d", Generation: 1, InstalledEpoch: 1, InitialState: []int64{0}, Bound: domain.HardBound{ComponentMin: []int64{0}, ComponentMax: []int64{12}}, Jobs: true})
	if err != nil {
		t.Fatal(err)
	}
	defer o.Close()
	if _, err = o.Apply(req("job-http", domain.OpAdd, 1, 1)); err != nil {
		t.Fatal(err)
	}
	claimed, err := o.JobStore().Claim(time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	mux := http.NewServeMux()
	o.JobStore().Register(mux)
	srv := httptest.NewServer(mux)
	defer srv.Close()
	post := func(body string) int {
		resp, e := http.Post(srv.URL+"/v1/job-results", "application/json", strings.NewReader(body))
		if e != nil {
			t.Fatal(e)
		}
		defer resp.Body.Close()
		return resp.StatusCode
	}
	if got := post(`{"command_id":"job-http","lease_token":"` + claimed.LeaseToken + `","result_sha256":"not-a-digest"}`); got != http.StatusBadRequest {
		t.Fatalf("malformed digest status=%d", got)
	}
	if got := post(`{"command_id":"job-http","lease_token":"` + claimed.LeaseToken + `","result_sha256":"` + strings.Repeat("0", 64) + `"}`); got != http.StatusUnprocessableEntity {
		t.Fatalf("wrong valid digest status=%d", got)
	}
	if got := post(`{"command_id":"job-http","lease_token":"` + claimed.LeaseToken + `"}`); got != http.StatusBadRequest {
		t.Fatalf("missing digest status=%d", got)
	}
}

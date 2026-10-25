package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"evicon/internal/adapter"
	"evicon/internal/buildinfo"
	"evicon/internal/contract"
	"evicon/internal/domain"
	"evicon/internal/fault"
	"evicon/internal/jobs"
	"evicon/internal/protocol"
	"evicon/internal/trace"
)

func main() {
	listen := flag.String("listen", "127.0.0.1:18181", "effect API listen address")
	admin := flag.String("admin-listen", "", "optional test-only fault API listen address")
	profile := flag.String("profile", "E3", "endpoint profile: E1, E2, or E3")
	db := flag.String("db", "./data/endpoint.db", "E3 SQLite path; also base name for E1/E2 defaults")
	claimDB := flag.String("claim-db", "", "E1/E2 claim+receipt SQLite path")
	effectsDB := flag.String("effects-db", "", "E1/E2 effect-state SQLite path")
	domainID := flag.String("domain", "atlas-001", "domain ID")
	generation := flag.Uint64("generation", 1, "domain generation")
	epoch := flag.Uint64("epoch", 1, "installed epoch")
	initial := flag.String("initial", "0", "comma-separated initial state")
	cmin := flag.String("component-min", "0", "component minima; empty disables")
	cmax := flag.String("component-max", "12", "component maxima; empty disables")
	summax := flag.String("sum-max", "", "optional sum max")
	effectDelay := flag.Duration("effect-delay", 0, "test-only delay between durable claim and effect for E1/E2")
	tracePath := flag.String("trace", "", "JSONL trace path")
	faultAck := flag.String("fault-ack", "", "fault acknowledgement JSONL path")
	runID := flag.String("run-id", "p2-local", "trace run ID")
	traceSeed := flag.Int64("trace-seed", 0, "nonnegative experiment seed recorded in the endpoint JSONL trace")
	pilotSchedulePath := flag.String("pilot-schedule", "", "test/pilot-only endpoint behavior schedule JSON")
	jobMode := flag.Bool("jobs", false, "enable durable checksum job registration for ADD grants")
	perIdentityStop := flag.Bool("per-identity-stop", false, "061: expose POST /v1/stop (Q_S) on E2 and advertise PER_IDENTITY_STOP; default off keeps 060 behavior")
	flag.Parse()
	if *perIdentityStop && strings.ToUpper(*profile) != "E2" {
		log.Fatal("per-identity-stop is supported by profile E2 only")
	}
	if *traceSeed < 0 {
		log.Fatal("trace-seed must be nonnegative")
	}
	psched, e := loadPilotSchedule(*pilotSchedulePath)
	if e != nil {
		log.Fatalf("pilot schedule: %v", e)
	}
	p := strings.ToUpper(*profile)
	if *jobMode && p == "E1" {
		log.Fatal("job integration supports E2/E3 only")
	}
	if p != "E1" && p != "E2" && p != "E3" {
		log.Fatalf("unsupported profile %q", *profile)
	}
	st := mustVec(*initial)
	mins := optVec(*cmin)
	maxs := optVec(*cmax)
	if len(mins) > 0 && len(mins) != len(st) {
		log.Fatal("component-min dimension mismatch")
	}
	if len(maxs) > 0 && len(maxs) != len(st) {
		log.Fatal("component-max dimension mismatch")
	}
	var sm *int64
	if strings.TrimSpace(*summax) != "" {
		v, e := strconv.ParseInt(*summax, 10, 64)
		if e != nil {
			log.Fatal(e)
		}
		sm = &v
	}
	fc := fault.New(*faultAck)
	tr := trace.New(*tracePath, "endpoint-"+strconv.Itoa(os.Getpid()), *runID, "P2_"+p, *traceSeed)
	outage := newEvidenceOutageController(tr, *domainID)
	bound := domain.HardBound{ComponentMin: mins, ComponentMax: maxs, SumMax: sm}
	var apply func(protocol.CommandRequest) (protocol.Receipt, error)
	var forceStop func(protocol.CommandRequest, string) (protocol.Receipt, error)
	var getReceipt func(string, string) (protocol.Receipt, error)
	var readState func() ([]int64, uint64, uint64, error)
	var barrier func(protocol.BarrierRequest) (protocol.BarrierReceipt, error)
	var stop func(protocol.StopRequest) (protocol.Receipt, error)
	var snapshot func() (map[string]any, error)
	var cap contract.AdapterProfile
	var closeFn func() error
	var jobStore *jobs.Store
	if p == "E3" {
		o, e := adapter.OpenE3(adapter.E3Config{DBPath: *db, DomainID: *domainID, Generation: *generation, InstalledEpoch: *epoch, InitialState: st, Bound: bound, Faults: fc, Trace: tr, Jobs: *jobMode})
		if e != nil {
			log.Fatal(e)
		}
		closeFn = o.Close
		if *jobMode {
			jobStore = o.JobStore()
		}
		apply = o.Apply
		forceStop = o.ForceStop
		getReceipt = o.GetReceipt
		cap = adapter.P1E3Profile()
		readState = o.ReadState
		snapshot = func() (map[string]any, error) {
			s, effects, receipts, e := o.Snapshot()
			return map[string]any{"state": s, "effects": effects, "receipts": receipts, "profile": "E3"}, e
		}
	} else {
		base := strings.TrimSuffix(*db, filepath.Ext(*db))
		cpath := *claimDB
		epath := *effectsDB
		if cpath == "" {
			cpath = base + "-claims.db"
		}
		if epath == "" {
			epath = base + "-effects.db"
		}
		o, e := adapter.OpenE12(adapter.E12Config{Profile: p, ClaimDBPath: cpath, EffectsDBPath: epath, DomainID: *domainID, Generation: *generation, InstalledEpoch: *epoch, InitialState: st, Bound: bound, EffectDelay: *effectDelay, Faults: fc, Trace: tr, Jobs: *jobMode})
		if e != nil {
			log.Fatal(e)
		}
		closeFn = o.Close
		if *jobMode {
			jobStore = o.JobStore()
		}
		apply = o.Apply
		forceStop = o.ForceStop
		getReceipt = o.GetReceipt
		readState = o.ReadState
		barrier = o.Barrier
		if p == "E1" {
			cap = adapter.P2E1Profile()
		} else if *perIdentityStop {
			cap = adapter.E2StopProfile()
			stop = o.Stop
		} else {
			cap = adapter.P2E2Profile()
		}
		snapshot = func() (map[string]any, error) {
			s, effects, claims, terminal, cutoff, e := o.Snapshot()
			return map[string]any{"state": s, "effects": effects, "claims": claims, "terminal_receipts": terminal, "cutoff_sequence": cutoff, "profile": p}, e
		}
	}
	defer closeFn()
	mux := http.NewServeMux()
	if jobStore != nil {
		jobStore.Register(mux)
	}
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"ok": true, "role": "endpoint", "pid": os.Getpid(), "profile": p, "release": buildinfo.Release, "build_id": buildinfo.PatchID, "jobs_enabled": *jobMode})
	})
	mux.HandleFunc("/v1/capabilities", func(w http.ResponseWriter, r *http.Request) {
		premises := make([]string, len(cap.Premises))
		for i, x := range cap.Premises {
			premises[i] = string(x)
		}
		var jobCaps *protocol.JobCapabilities
		if *jobMode {
			jobCaps = &protocol.JobCapabilities{Enabled: true, GrantOperation: string(domain.OpAdd), CompletionTask: "SHA256_REPEAT_1024", OwnerVerifiesResult: true, LeaseFencing: true}
		}
		writeJSON(w, 200, protocol.EndpointCapabilities{SchemaVersion: 1, Profile: p, AdapterVersion: cap.AdapterVersion, DomainID: *domainID, Generation: *generation, InstalledEpoch: *epoch, Dimension: len(st), SupportedOperations: []domain.Operation{domain.OpAdd, domain.OpSet}, ComponentMin: mins, ComponentMax: maxs, SumMax: sm, Premises: premises, VerifiedTestIDs: append([]string(nil), cap.VerifiedTestIDs...), Jobs: jobCaps})
	})
	mux.HandleFunc("/v1/stop", func(w http.ResponseWriter, r *http.Request) {
		if stop == nil {
			writeJSON(w, 409, map[string]any{"error": "STOP_UNSUPPORTED", "profile": p})
			return
		}
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", 405)
			return
		}
		var sr protocol.StopRequest
		if e := json.NewDecoder(r.Body).Decode(&sr); e != nil {
			http.Error(w, e.Error(), 400)
			return
		}
		rc, e := stop(sr)
		if e != nil {
			if errors.Is(e, adapter.ErrIdentityConflict) {
				writeJSON(w, 409, map[string]any{"error": "IDENTITY_CONFLICT"})
				return
			}
			http.Error(w, e.Error(), 400)
			return
		}
		writeJSON(w, 200, rc)
	})
	mux.HandleFunc("/v1/effects", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", 405)
			return
		}
		var req protocol.CommandRequest
		if e := json.NewDecoder(r.Body).Decode(&req); e != nil {
			http.Error(w, e.Error(), 400)
			return
		}
		var rc protocol.Receipt
		var e error
		beh, hasBeh := psched.behavior(req.CommandID)
		if hasBeh && beh.ForcedOutcome == "STOPPED" {
			reason := beh.Reason
			if reason == "" {
				reason = "PILOT_PREDETERMINED_REJECT"
			}
			rc, e = forceStop(req, reason)
		} else {
			rc, e = apply(req)
		}
		if e != nil {
			if errors.Is(e, adapter.ErrIdentityConflict) {
				_ = tr.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "IDENTITY_CONFLICT_REJECTED"})
				writeJSON(w, 409, map[string]any{"error": "IDENTITY_CONFLICT"})
				return
			}
			http.Error(w, e.Error(), 400)
			return
		}
		if hasBeh && psched.shouldDelayEffectReply(req.CommandID, beh.EffectReplyDelayMS) {
			if tr != nil {
				_ = tr.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "PILOT_EFFECT_REPLY_DELAY_STARTED", Reason: fmt.Sprintf("delay_ms=%d", beh.EffectReplyDelayMS)})
			}
			time.Sleep(time.Duration(beh.EffectReplyDelayMS) * time.Millisecond)
		}
		if hasBeh && psched.shouldHideEffectReply(req.CommandID, beh.EffectReplyHideMS) {
			if tr != nil {
				_ = tr.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "PILOT_EFFECT_REPLY_HIDDEN", Reason: fmt.Sprintf("hide_ms=%d", beh.EffectReplyHideMS)})
			}
			writeJSON(w, 503, map[string]any{"error": "PILOT_EFFECT_REPLY_HIDDEN"})
			return
		}
		writeJSON(w, 200, rc)
	})
	mux.HandleFunc("/v1/receipts/", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", 405)
			return
		}
		id := strings.TrimPrefix(r.URL.Path, "/v1/receipts/")
		if active, body, err := outage.blockReceipt(id); active {
			if err != nil {
				writeJSON(w, 500, map[string]any{"error": "OUTAGE_TRACE_FAILURE", "reason": err.Error()})
				return
			}
			writeJSON(w, http.StatusServiceUnavailable, body)
			return
		}
		if beh, ok := psched.behavior(id); ok && beh.ReceiptQueryDelayMS > 0 {
			time.Sleep(time.Duration(beh.ReceiptQueryDelayMS) * time.Millisecond)
		}
		rc, e := getReceipt(*domainID, id)
		if tr != nil {
			_ = tr.Write(trace.Event{DomainID: *domainID, CommandID: id, Event: "RECEIPT_QUERY_SERVED"})
		}
		if e != nil {
			if errors.Is(e, os.ErrNotExist) {
				http.Error(w, "not found", 404)
				return
			}
			http.Error(w, e.Error(), 500)
			return
		}
		writeJSON(w, 200, rc)
	})
	mux.HandleFunc("/v1/state", func(w http.ResponseWriter, r *http.Request) {
		s, ov, co, e := readState()
		if e != nil {
			http.Error(w, e.Error(), 500)
			return
		}
		writeJSON(w, 200, map[string]any{"schema_version": 1, "domain_id": *domainID, "generation": *generation, "state": s, "owner_version": ov, "cutoff_sequence": co, "evidence": "ORDINARY_READ_NOT_ANCHOR"})
	})
	mux.HandleFunc("/v1/barriers", func(w http.ResponseWriter, r *http.Request) {
		if p != "E2" || barrier == nil {
			writeJSON(w, 409, map[string]any{"error": "BARRIER_UNSUPPORTED", "profile": p})
			return
		}
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", 405)
			return
		}
		var br protocol.BarrierRequest
		if e := json.NewDecoder(r.Body).Decode(&br); e != nil {
			http.Error(w, e.Error(), 400)
			return
		}
		rc, e := barrier(br)
		if e != nil {
			if errors.Is(e, adapter.ErrIdentityConflict) {
				writeJSON(w, 409, map[string]any{"error": "BARRIER_TOKEN_CONFLICT"})
				return
			}
			http.Error(w, e.Error(), 400)
			return
		}
		writeJSON(w, 200, rc)
	})
	mux.HandleFunc("/_debug/state", func(w http.ResponseWriter, r *http.Request) {
		v, e := snapshot()
		if e != nil {
			http.Error(w, e.Error(), 500)
			return
		}
		writeJSON(w, 200, v)
	})
	srv := &http.Server{Addr: *listen, Handler: mux, ReadHeaderTimeout: 2 * time.Second}
	if *admin != "" {
		adminMux := http.NewServeMux()
		fh := fc.Handler()
		adminMux.Handle("/healthz", fh)
		adminMux.Handle("/_test/faults", fh)
		adminMux.Handle("/_test/faults/clear", fh)
		outage.register(adminMux)
		go func() {
			as := &http.Server{Addr: *admin, Handler: adminMux, ReadHeaderTimeout: 2 * time.Second}
			if e := as.ListenAndServe(); e != nil && e != http.ErrServerClosed {
				log.Printf("admin server: %v", e)
			}
		}()
	}
	go func() {
		log.Printf("evicon-endpoint %s listening on %s", p, *listen)
		if e := srv.ListenAndServe(); e != nil && e != http.ErrServerClosed {
			log.Fatalf("server: %v", e)
		}
	}()
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig
	_ = srv.Close()
}
func mustVec(s string) []int64 {
	v := optVec(s)
	if len(v) == 0 {
		log.Fatal("state vector required")
	}
	return v
}
func optVec(s string) []int64 {
	if strings.TrimSpace(s) == "" {
		return nil
	}
	parts := strings.Split(s, ",")
	out := make([]int64, len(parts))
	for i, p := range parts {
		v, e := strconv.ParseInt(strings.TrimSpace(p), 10, 64)
		if e != nil {
			log.Fatalf("bad vector %q: %v", s, e)
		}
		out[i] = v
	}
	return out
}
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("content-type", "application/json")
	w.WriteHeader(status)
	if e := json.NewEncoder(w).Encode(v); e != nil {
		fmt.Fprintln(os.Stderr, e)
	}
}

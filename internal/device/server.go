package device

import (
	"bufio"
	"encoding/json"
	"errors"
	"evicon/internal/buildinfo"
	"fmt"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"evicon/internal/domain"
	"evicon/internal/plant"
	"evicon/internal/protocol"
)

// Handler serves the EviCon endpoint protocol for one device:
//
//	GET  /v1/capabilities  profile DEVICE, ADD only, declared evidence contract
//	POST /v1/effects       CommandRequest -> Receipt
//	GET  /v1/receipts/<id> Q_A (404 when unknown, 409 when unsupported)
//	POST /v1/stop          Q_S (409 STOP_UNSUPPORTED otherwise)
//	POST /v1/barriers      F   (409 BARRIER_UNSUPPORTED otherwise)
//	GET  /v1/state         ordinary read (not evidence)
//
// Test-only control lives on a separate admin handler (AdminHandler).
func (d *Device) Handler(adapterVersion string) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"ok": true, "role": "device", "pid": os.Getpid(), "release": buildinfo.Release, "device": d.cfg.DeviceID, "online": d.Online()})
	})
	mux.HandleFunc("/v1/capabilities", func(w http.ResponseWriter, r *http.Request) {
		c := d.Contract()
		raw, _ := json.Marshal(c)
		premises := make([]string, len(c.Premises))
		for i, p := range c.Premises {
			premises[i] = string(p)
		}
		var cmax []int64
		if d.cfg.Bound > 0 {
			cmax = []int64{d.cfg.Bound}
		}
		writeJSON(w, 200, protocol.EndpointCapabilities{SchemaVersion: 1, Profile: "DEVICE", AdapterVersion: adapterVersion, DomainID: d.cfg.DomainID,
			Generation: d.cfg.Generation, InstalledEpoch: d.cfg.InstalledEpoch, Dimension: 1, SupportedOperations: []domain.Operation{domain.OpAdd},
			ComponentMin: []int64{0}, ComponentMax: cmax, Premises: premises, VerifiedTestIDs: c.VerifiedTestIDs, EvidenceContract: raw})
	})
	mux.HandleFunc("/v1/effects", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", 405)
			return
		}
		var req protocol.CommandRequest
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<16)).Decode(&req); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		attempt, _ := strconv.ParseInt(r.Header.Get("X-EviCon-Attempt"), 10, 64)
		rc, err := d.ApplyAttempt(req, attempt)
		writeResult(w, rc, err)
	})
	mux.HandleFunc("/v1/receipts/", func(w http.ResponseWriter, r *http.Request) {
		id := strings.TrimPrefix(r.URL.Path, "/v1/receipts/")
		rc, err := d.Receipt(id)
		if errors.Is(err, os.ErrNotExist) {
			http.Error(w, "not found", 404)
			return
		}
		writeResult(w, rc, err)
	})
	mux.HandleFunc("/v1/stop", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", 405)
			return
		}
		var req protocol.StopRequest
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<16)).Decode(&req); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		rc, err := d.Stop(req)
		if errors.Is(err, ErrUnsupported) {
			writeJSON(w, 409, map[string]any{"error": "STOP_UNSUPPORTED"})
			return
		}
		writeResult(w, rc, err)
	})
	mux.HandleFunc("/v1/barriers", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", 405)
			return
		}
		var req protocol.BarrierRequest
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<16)).Decode(&req); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		br, err := d.Barrier(req)
		switch {
		case errors.Is(err, ErrUnsupported):
			writeJSON(w, 409, map[string]any{"error": "BARRIER_UNSUPPORTED"})
		case errors.Is(err, ErrIdentityConflict):
			writeJSON(w, 409, map[string]any{"error": "BARRIER_TOKEN_CONFLICT"})
		case errors.Is(err, ErrOffline):
			writeJSON(w, 503, map[string]any{"error": "DEVICE_OFFLINE"})
		case err != nil:
			http.Error(w, err.Error(), 400)
		default:
			writeJSON(w, 200, br)
		}
	})
	mux.HandleFunc("/v1/state", func(w http.ResponseWriter, r *http.Request) {
		lvl, ov, co, _ := d.State()
		writeJSON(w, 200, map[string]any{"schema_version": 1, "domain_id": d.cfg.DomainID, "generation": d.cfg.Generation, "state": []int64{lvl},
			"owner_version": ov, "cutoff_sequence": co, "evidence": "ORDINARY_READ_NOT_ANCHOR"})
	})
	return mux
}

// AdminHandler is test-only control: POST /_test/online?v=true|false,
// POST /_test/crash?at=<point>&cmd=<id> (arms a one-shot crash; at=NONE
// disarms), GET /_debug/state.
func (d *Device) AdminHandler(arm func(point, cmd string)) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/_test/online", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", 405)
			return
		}
		v := r.URL.Query().Get("v")
		if v != "true" && v != "false" {
			http.Error(w, "v must be true or false", 400)
			return
		}
		d.SetOnline(v == "true")
		writeJSON(w, 200, map[string]any{"online": v == "true"})
	})
	mux.HandleFunc("/_test/crash", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || arm == nil {
			http.Error(w, "unavailable", 405)
			return
		}
		at := r.URL.Query().Get("at")
		switch at {
		case CrashAfterClaimBeforeEffect, CrashAfterEffectBeforeReceipt, CrashAfterReceiptBeforeReply:
		case "NONE": // disarm
			at = ""
		default:
			http.Error(w, "unknown crash point", 400)
			return
		}
		arm(at, r.URL.Query().Get("cmd"))
		writeJSON(w, 200, map[string]any{"armed": at})
	})
	mux.HandleFunc("/_debug/state", func(w http.ResponseWriter, r *http.Request) {
		lvl, ov, co, eff := d.State()
		writeJSON(w, 200, map[string]any{"device": d.cfg.DeviceID, "level": lvl, "owner_version": ov, "cutoff_sequence": co,
			"effects_this_process": eff, "online": d.Online(), "contract": d.Contract()})
	})
	return mux
}

func writeResult(w http.ResponseWriter, rc protocol.Receipt, err error) {
	switch {
	case err == nil:
		writeJSON(w, 200, rc)
	case errors.Is(err, ErrIdentityConflict):
		writeJSON(w, 409, map[string]any{"error": "IDENTITY_CONFLICT"})
	case errors.Is(err, ErrUnsupported):
		writeJSON(w, 409, map[string]any{"error": "UNSUPPORTED"})
	case errors.Is(err, ErrOffline):
		writeJSON(w, 503, map[string]any{"error": "DEVICE_OFFLINE"})
	default:
		http.Error(w, err.Error(), 400)
	}
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

// SocketPlant writes effects to evicon-plant over its line protocol (Unix
// socket path or host:port). One connection, re-dialled on error.
type SocketPlant struct {
	Network, Addr string
	mu            sync.Mutex
	conn          net.Conn
	rd            *bufio.Reader
}

func (s *SocketPlant) Apply(device, cmd string, value int64, once bool) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	for attempt := 0; attempt < 2; attempt++ {
		if s.conn == nil {
			c, err := net.DialTimeout(s.Network, s.Addr, 2*time.Second)
			if err != nil {
				return err
			}
			s.conn, s.rd = c, bufio.NewReader(c)
		}
		b, _ := json.Marshal(plant.ApplyRequest{Op: "apply", Device: device, Cmd: cmd, Value: value, Once: once})
		_ = s.conn.SetDeadline(time.Now().Add(5 * time.Second))
		if _, err := s.conn.Write(append(b, '\n')); err != nil {
			s.conn.Close()
			s.conn = nil
			continue
		}
		line, err := s.rd.ReadBytes('\n')
		if err != nil {
			// The write may have reached the plant: never retry blindly.
			s.conn.Close()
			s.conn = nil
			return fmt.Errorf("plant reply lost (effect may have happened): %w", err)
		}
		var res plant.ApplyResult
		if err := json.Unmarshal(line, &res); err != nil {
			return err
		}
		if !res.OK {
			return errors.New(res.Error)
		}
		return nil
	}
	return errors.New("plant unreachable")
}

// LocalPlant adapts an in-process plant (tests, single-host demos).
type LocalPlant struct{ P *plant.Plant }

func (l LocalPlant) Apply(device, cmd string, value int64, once bool) error {
	r := l.P.Apply(plant.ApplyRequest{Op: "apply", Device: device, Cmd: cmd, Value: value, Once: once})
	if !r.OK {
		return errors.New(r.Error)
	}
	return nil
}

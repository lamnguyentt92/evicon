package plant

import (
	"bufio"
	"encoding/json"
	"errors"
	"evicon/internal/buildinfo"
	"net"
	"net/http"
	"os"
	"strconv"
)

// Handler serves the plant over HTTP: POST /v1/apply (writer side, for
// workflow activities and reference apps), GET /v1/truth?cmd=&device= and
// GET /v1/truth/summary (reader side, for the harness/checker), POST
// /v1/limits?device=&limit= to register a safety limit.
func (p *Plant) Handler() http.Handler {
	mux := http.NewServeMux()
	// /healthz names the program, release and pid, so a harness can tell that the
	// answer comes from the process it started and not from another program
	// already bound to the port.
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"ok": true, "role": "plant", "pid": os.Getpid(), "release": buildinfo.Release})
	})
	mux.HandleFunc("/v1/apply", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", 405)
			return
		}
		var req ApplyRequest
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<16)).Decode(&req); err != nil {
			writeJSON(w, 400, ApplyResult{Error: err.Error()})
			return
		}
		res := p.Apply(req)
		code := 200
		if !res.OK {
			code = 400
		}
		writeJSON(w, code, res)
	})
	mux.HandleFunc("/v1/truth", func(w http.ResponseWriter, r *http.Request) {
		cmd := r.URL.Query().Get("cmd")
		if cmd == "" {
			http.Error(w, "cmd required", 400)
			return
		}
		writeJSON(w, 200, p.Truth(cmd, r.URL.Query().Get("device")))
	})
	mux.HandleFunc("/v1/truth/summary", func(w http.ResponseWriter, r *http.Request) { writeJSON(w, 200, p.Summary()) })
	mux.HandleFunc("/v1/limits", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", 405)
			return
		}
		d := r.URL.Query().Get("device")
		l, err := strconv.ParseInt(r.URL.Query().Get("limit"), 10, 64)
		if d == "" || err != nil || l <= 0 {
			http.Error(w, "device and positive limit required", 400)
			return
		}
		p.SetLimit(d, l)
		writeJSON(w, 200, map[string]any{"ok": true})
	})
	return mux
}

// ServeLines serves the line-delimited JSON protocol on a listener (Unix
// socket on VM-E): one ApplyRequest per line, one ApplyResult per line.
func (p *Plant) ServeLines(l net.Listener) error {
	for {
		c, err := l.Accept()
		if err != nil {
			if errors.Is(err, net.ErrClosed) {
				return nil
			}
			return err
		}
		go func(c net.Conn) {
			defer c.Close()
			sc := bufio.NewScanner(c)
			sc.Buffer(make([]byte, 1<<16), 1<<16)
			enc := json.NewEncoder(c)
			for sc.Scan() {
				var req ApplyRequest
				if err := json.Unmarshal(sc.Bytes(), &req); err != nil {
					_ = enc.Encode(ApplyResult{Error: err.Error()})
					continue
				}
				if err := enc.Encode(p.Apply(req)); err != nil {
					return
				}
			}
		}(c)
	}
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

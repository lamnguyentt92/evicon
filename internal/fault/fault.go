package fault

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"sync"
	"time"
)

type Controller struct {
	mu        sync.Mutex
	cut       string
	commandID string
	exitCode  int
	ackPath   string
}

type armRequest struct {
	Cut       string `json:"cut"`
	CommandID string `json:"command_id,omitempty"`
	ExitCode  int    `json:"exit_code,omitempty"`
}

func New(ackPath string) *Controller { return &Controller{exitCode: 86, ackPath: ackPath} }

func (c *Controller) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok\n"))
	})
	mux.HandleFunc("/_test/faults", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", 405)
			return
		}
		var req armRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		if req.Cut == "" {
			http.Error(w, "cut required", 400)
			return
		}
		c.mu.Lock()
		c.cut = req.Cut
		c.commandID = req.CommandID
		if req.ExitCode != 0 {
			c.exitCode = req.ExitCode
		}
		c.mu.Unlock()
		w.Header().Set("content-type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"armed": true, "cut": req.Cut, "command_id": req.CommandID})
	})
	mux.HandleFunc("/_test/faults/clear", func(w http.ResponseWriter, r *http.Request) {
		c.mu.Lock()
		c.cut = ""
		c.commandID = ""
		c.mu.Unlock()
		w.WriteHeader(204)
	})
	return mux
}

func (c *Controller) Hit(cut, commandID string) {
	c.mu.Lock()
	armed := c.cut == cut && (c.commandID == "" || c.commandID == commandID)
	exitCode := c.exitCode
	if armed {
		c.cut = ""
		c.commandID = ""
	}
	ack := c.ackPath
	c.mu.Unlock()
	if !armed {
		return
	}
	if ack != "" {
		f, err := os.OpenFile(ack, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
		if err == nil {
			_, _ = fmt.Fprintf(f, "{\"cut\":%q,\"command_id\":%q,\"wall_time\":%q}\n", cut, commandID, time.Now().UTC().Format(time.RFC3339Nano))
			_ = f.Sync()
			_ = f.Close()
		}
	}
	os.Exit(exitCode)
}

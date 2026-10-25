// Command evicon-noop is the control endpoint of the release-077 latency control (PREREG_review_077.md,
// evaluation/latency_control.py). It answers the authority's client API with no work at all: every
// POST /v1/commands is ADMITTED at once, every GET /v1/commands/<id> is APPLIED. It records, per
// request, the wall clock when its handler started and when it replied, so that the load generator's and
// the network's share of the client-observed admission latency can be measured without the authority.
// It listens on several addresses (one per emulated device, as one authority per device does).
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"io"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"evicon/internal/buildinfo"
)

type record struct {
	Listen        string `json:"listen"`
	Method        string `json:"method"`
	CommandID     string `json:"command_id"`
	HandlerUnixNS int64  `json:"handler_start_unix_ns"`
	ReplyUnixNS   int64  `json:"reply_unix_ns"`
}

func main() {
	listen := flag.String("listen", "127.0.0.1:19000", "comma-separated listen addresses")
	out := flag.String("log", "noop.jsonl", "per-request log (JSON lines)")
	version := flag.Bool("version", false, "print the release and exit")
	flag.Parse()
	if *version {
		log.Printf("evicon-noop %s", buildinfo.Release)
		return
	}
	f, err := os.Create(*out)
	if err != nil {
		log.Fatal(err)
	}
	w := bufio.NewWriterSize(f, 1<<16)
	var mu sync.Mutex
	write := func(r record) {
		b, _ := json.Marshal(r)
		mu.Lock()
		_, _ = w.Write(append(b, '\n'))
		mu.Unlock()
	}
	flush := func() {
		mu.Lock()
		_ = w.Flush()
		mu.Unlock()
	}
	go func() {
		for range time.Tick(time.Second) {
			flush()
		}
	}()
	var srvs []*http.Server
	for _, addr := range strings.Split(*listen, ",") {
		addr := strings.TrimSpace(addr)
		mux := http.NewServeMux()
		mux.HandleFunc("/healthz", func(rw http.ResponseWriter, _ *http.Request) { _, _ = rw.Write([]byte("ok\n")) })
		mux.HandleFunc("/v1/commands", func(rw http.ResponseWriter, req *http.Request) {
			start := time.Now().UnixNano()
			var body struct {
				CommandID string `json:"command_id"`
			}
			raw, _ := io.ReadAll(io.LimitReader(req.Body, 1<<20))
			_ = json.Unmarshal(raw, &body)
			rw.Header().Set("Content-Type", "application/json")
			rw.WriteHeader(http.StatusAccepted)
			_, _ = rw.Write([]byte(`{"decision":"ADMITTED","reason":"NOOP_CONTROL"}`))
			write(record{Listen: addr, Method: req.Method, CommandID: body.CommandID, HandlerUnixNS: start, ReplyUnixNS: time.Now().UnixNano()})
		})
		mux.HandleFunc("/v1/commands/", func(rw http.ResponseWriter, req *http.Request) {
			rw.Header().Set("Content-Type", "application/json")
			_, _ = rw.Write([]byte(`{"status":"APPLIED"}`))
		})
		s := &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 2 * time.Second}
		srvs = append(srvs, s)
		go func() {
			if e := s.ListenAndServe(); e != nil && e != http.ErrServerClosed {
				log.Fatalf("listen %s: %v", addr, e)
			}
		}()
	}
	log.Printf("evicon-noop %s listening on %s; log %s", buildinfo.Release, *listen, *out)
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig
	for _, s := range srvs {
		_ = s.Close()
	}
	flush()
	_ = f.Close()
}

// evicon-plant: the plant emulator (independent truth channel) for IoT
// experiments on VM-E. Devices write effects through a Unix socket; workflow
// activities/reference apps may write through HTTP; the harness and checker
// only read GET /v1/truth and /v1/truth/summary.
package main

import (
	"flag"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"evicon/internal/plant"
)

func main() {
	logPath := flag.String("log", "./data/plant.jsonl", "durable effect log (JSONL)")
	httpAddr := flag.String("http", "127.0.0.1:18090", "HTTP listen address (truth API; also /v1/apply)")
	unixPath := flag.String("unix", "", "optional Unix socket for device effect writes")
	tcpAddr := flag.String("tcp", "", "optional TCP listen address for the same line protocol (Zephyr native_sim firmware, offloaded sockets)")
	fsync := flag.Bool("fsync", true, "fsync every effect before acknowledging")
	flag.Parse()
	p, err := plant.Open(*logPath, *fsync)
	if err != nil {
		log.Fatal(err)
	}
	defer p.Close()
	if *unixPath != "" {
		_ = os.Remove(*unixPath)
		l, err := net.Listen("unix", *unixPath)
		if err != nil {
			log.Fatal(err)
		}
		_ = os.Chmod(*unixPath, 0o666)
		defer l.Close()
		go func() {
			if err := p.ServeLines(l); err != nil {
				log.Printf("unix server: %v", err)
			}
		}()
	}
	if *tcpAddr != "" {
		l, err := net.Listen("tcp", *tcpAddr)
		if err != nil {
			log.Fatal(err)
		}
		defer l.Close()
		go func() {
			if err := p.ServeLines(l); err != nil {
				log.Printf("tcp server: %v", err)
			}
		}()
	}
	srv := &http.Server{Addr: *httpAddr, Handler: p.Handler(), ReadHeaderTimeout: 2 * time.Second}
	go func() {
		log.Printf("evicon-plant log=%s http=%s unix=%s fsync=%v", *logPath, *httpAddr, *unixPath, *fsync)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatal(err)
		}
	}()
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig
	_ = srv.Close()
}

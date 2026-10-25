// evicon-device: a fleet of configurable IoT actuator emulators (release 062).
//
// One process serves -count devices. Device i has ID <prefix><i>, its own
// durable journal and its own domain (domain ID = device ID), and is reached
// at http://<listen>/d/<id>/v1/... (the EviCon endpoint protocol), so one EviCon
// authority per device uses -endpoint-url http://<listen>/d/<id>.
// Physical effects go to evicon-plant (-plant unix:/path or tcp:host:port).
//
// Contract presets (-contract): K0, K0F (fence only), K1 (45 s horizon),
// K1F (45 s horizon + fence), K2, K3S (stop), K3F (fence), K3T (owner expiry
// 30 s), K4 (atomic), CUTOFF (query+fence, no dedup; the classic "device
// cutoff" firmware). Individual flags override. K0F and K1F (release 065) are
// the fence upgrades of K0 and K1 for experiment A on the testbed.
package main

import (
	"bytes"
	"evicon/internal/buildinfo"
	"flag"
	"fmt"
	"log"
	"math/rand"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"evicon/internal/device"
)

var adapterVersion = "evicon-device-" + buildinfo.Release

type preset struct {
	horizon                    time.Duration
	query, stop, fence, atomic bool
	expiry                     time.Duration
	negSeal, negReexec         bool // negative controls (release 072)
}

var presets = map[string]preset{
	"K0":     {},
	"K0F":    {fence: true},
	"K0S":    {stop: true}, // release 072: per-identity stop without dedup or query (experiment S)
	"K1":     {horizon: 45 * time.Second},
	"K1F":    {horizon: 45 * time.Second, fence: true},
	"K2":     {horizon: -1, query: true},
	"K3S":    {horizon: -1, query: true, stop: true},
	"K3F":    {horizon: -1, query: true, fence: true},
	"K3T":    {horizon: -1, query: true, expiry: 30 * time.Second},
	"K4":     {horizon: -1, query: true, atomic: true},
	"CUTOFF": {query: true, fence: true},
	// Release 072 negative controls: same advertised contract as K0F / K2, one
	// owner premise broken. Only for the negative-control campaign.
	"NEG_SEAL":  {fence: true, negSeal: true},
	"NEG_DEDUP": {horizon: -1, query: true, negReexec: true},
}

func main() {
	listen := flag.String("listen", "127.0.0.1:18100", "device API listen address (all devices, path-prefixed)")
	admin := flag.String("admin-listen", "", "test-only admin listen address (online/offline, crash points)")
	count := flag.Int("count", 1, "number of devices")
	prefix := flag.String("id-prefix", "pump-", "device ID prefix")
	stateDir := flag.String("state-dir", "./data/devices", "journal directory")
	plantAddr := flag.String("plant", "unix:./data/plant.sock", "plant address: unix:<path> or tcp:<host:port>")
	contractName := flag.String("contract", "K2", "preset: K0, K0F, K0S, K1, K1F, K2, K3S, K3F, K3T, K4, CUTOFF; negative controls NEG_SEAL, NEG_DEDUP")
	horizon := flag.Duration("dedup-horizon", 0, "override: dedup horizon (negative = unbounded, 0 = keep preset)")
	noDedup := flag.Bool("no-dedup", false, "override: disable dedup")
	expiry := flag.Duration("expiry", 0, "override: enforced owner expiry advertised as T (0 = keep preset)")
	skew := flag.Duration("clock-skew", 500*time.Millisecond, "clock-skew bound advertised with T")
	bound := flag.Int64("bound", 0, "per-device cumulative limit enforced by the device (0 = none; EviCon does not rely on it)")
	effectDelay := flag.Duration("effect-delay", 0, "delay between claim and physical effect")
	offline := flag.String("offline-mode", "queue", "queue (late delivery on reconnect) or drop")
	maxHold := flag.Duration("max-hold", 10*time.Minute, "queue mode: longest a request is held while offline")
	crashOverride := flag.String("crash-point-override", "", "release 077: crash at this point whenever any crash is armed ("+
		"AFTER_RECEIPT_BEFORE_REPLY makes every injected crash leave a durable receipt, so no crash orphan arises; empty = as armed)")
	wanDelay := flag.Duration("wan-delay", 0, "release 077: one-way network delay added to every request and to every reply of the "+
		"device API (a WAN between the authority and the device; /healthz is not delayed; 0 = none)")
	wanJitter := flag.Duration("wan-jitter", 0, "release 077: uniform jitter of -wan-delay, each way (delay +/- jitter, never below 0)")
	wanSeed := flag.Int64("wan-seed", 1, "release 077: seed of the jitter")
	flag.Parse()

	if *wanDelay < 0 || *wanJitter < 0 {
		log.Fatal("-wan-delay and -wan-jitter must not be negative")
	}
	if err := validCrashOverride(*crashOverride); err != nil {
		log.Fatal(err)
	}
	pr, ok := presets[strings.ToUpper(*contractName)]
	if !ok {
		log.Fatalf("unknown contract preset %q", *contractName)
	}
	if *horizon != 0 {
		pr.horizon = *horizon
	}
	if *noDedup {
		pr.horizon = 0
	}
	if *expiry != 0 {
		pr.expiry = *expiry
	}
	network, addr, found := strings.Cut(*plantAddr, ":")
	if !found || (network != "unix" && network != "tcp") {
		log.Fatal("-plant must be unix:<path> or tcp:<host:port>")
	}
	var armMu sync.Mutex
	armed := map[string]string{} // device|cmd -> point
	mux := http.NewServeMux()
	adm := http.NewServeMux()
	devs := map[string]*device.Device{}
	for i := 1; i <= *count; i++ {
		id := fmt.Sprintf("%s%d", *prefix, i)
		devID := id
		cfg := device.Config{DeviceID: id, DomainID: id, StateDir: *stateDir, DedupHorizon: pr.horizon, Query: pr.query, Stop: pr.stop,
			Fence: pr.fence, Atomic: pr.atomic, EnforceExpiry: pr.expiry > 0, ExpiryHint: pr.expiry, ClockSkew: *skew, Bound: *bound,
			EffectDelay: *effectDelay, OfflineMode: *offline, MaxHold: *maxHold, Plant: &device.SocketPlant{Network: network, Addr: addr},
			NegSealOrphans: pr.negSeal, NegReexecute: pr.negReexec,
			CrashHook: func(point, cmd string) {
				armMu.Lock()
				p, hit := armed[devID+"|"+cmd]
				if !hit {
					p, hit = armed[devID+"|*"]
				}
				armMu.Unlock()
				p = effectiveCrashPoint(p, *crashOverride)
				if hit && p == point {
					log.Printf("CRASH device=%s cmd=%s point=%s", devID, cmd, point)
					os.Exit(3)
				}
			}}
		d, err := device.Open(cfg)
		if err != nil {
			log.Fatalf("device %s: %v", id, err)
		}
		defer d.Close()
		devs[id] = d
		mux.Handle("/d/"+id+"/", http.StripPrefix("/d/"+id, d.Handler(adapterVersion)))
		adm.Handle("/d/"+id+"/", http.StripPrefix("/d/"+id, d.AdminHandler(func(point, cmd string) {
			if cmd == "" {
				cmd = "*"
			}
			armMu.Lock()
			if point == "" {
				delete(armed, devID+"|"+cmd)
			} else {
				armed[devID+"|"+cmd] = point
			}
			armMu.Unlock()
		})))
	}
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintf(w, `{"ok":true,"role":"device","pid":%d,"release":%q,"devices":%d,"contract":%q}`+"\n", os.Getpid(), buildinfo.Release, len(devs), strings.ToUpper(*contractName))
	})
	if *admin != "" {
		go func() {
			s := &http.Server{Addr: *admin, Handler: adm, ReadHeaderTimeout: 2 * time.Second}
			if err := s.ListenAndServe(); err != nil && err != http.ErrServerClosed {
				log.Printf("admin: %v", err)
			}
		}()
	}
	var api http.Handler = mux
	if *wanDelay > 0 || *wanJitter > 0 {
		api = wanHandler(mux, *wanDelay, *wanJitter, rand.New(rand.NewSource(*wanSeed)))
		log.Printf("WAN emulation: every request and every reply delayed %s +/- %s (one way)", *wanDelay, *wanJitter)
	}
	srv := &http.Server{Addr: *listen, Handler: api, ReadHeaderTimeout: 2 * time.Second}
	go func() {
		log.Printf("evicon-device %d device(s) contract=%s listen=%s plant=%s", len(devs), *contractName, *listen, *plantAddr)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatal(err)
		}
	}()
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig
	_ = srv.Close()
}

// effectiveCrashPoint (release 077) maps an armed crash point to the point where the device
// crashes: the armed one, or the override when one is set. With the override
// AFTER_RECEIPT_BEFORE_REPLY the device still crashes and restarts in every crash window of a
// campaign, but only after the receipt is durable, so it never leaves a crash orphan (experiment
// NoOrphan of PREREG_review_077.md).
func effectiveCrashPoint(armed, override string) string {
	if override == "" || armed == "" {
		return armed
	}
	return override
}

func validCrashOverride(o string) error {
	switch o {
	case "", device.CrashAfterClaimBeforeEffect, device.CrashAfterEffectBeforeReceipt, device.CrashAfterReceiptBeforeReply:
		return nil
	}
	return fmt.Errorf("-crash-point-override %q: want %s, %s or %s", o, device.CrashAfterClaimBeforeEffect,
		device.CrashAfterEffectBeforeReceipt, device.CrashAfterReceiptBeforeReply)
}

// bufferedResponse holds a handler's reply until the WAN delay of the way back has passed.
type bufferedResponse struct {
	h    http.Header
	code int
	body bytes.Buffer
}

func (b *bufferedResponse) Header() http.Header { return b.h }
func (b *bufferedResponse) WriteHeader(c int) {
	if b.code == 0 {
		b.code = c
	}
}
func (b *bufferedResponse) Write(p []byte) (int, error) {
	if b.code == 0 {
		b.code = http.StatusOK
	}
	return b.body.Write(p)
}

// wanHandler (release 077) emulates a WAN between the authority and the device: a request reaches the device
// one one-way delay after it arrived, and its reply leaves one one-way delay after the device answered. A
// client that gives up meanwhile does not stop the request (as on a real network). /healthz is not delayed.
func wanHandler(next http.Handler, delay, jitter time.Duration, rnd *rand.Rand) http.Handler {
	var mu sync.Mutex
	oneWay := func() time.Duration {
		if jitter <= 0 {
			return delay
		}
		mu.Lock()
		x := time.Duration(rnd.Int63n(int64(2*jitter)+1)) - jitter
		mu.Unlock()
		if delay+x < 0 {
			return 0
		}
		return delay + x
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/healthz" {
			next.ServeHTTP(w, r)
			return
		}
		time.Sleep(oneWay())
		b := &bufferedResponse{h: http.Header{}}
		next.ServeHTTP(b, r)
		time.Sleep(oneWay())
		for k, v := range b.h {
			w.Header()[k] = v
		}
		if b.code == 0 {
			b.code = http.StatusOK
		}
		w.WriteHeader(b.code)
		_, _ = w.Write(b.body.Bytes())
	})
}

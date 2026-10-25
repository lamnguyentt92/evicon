package main

import (
	"math/rand"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"evicon/internal/device"
)

// Release 077: the crash-point override keeps every injected crash but moves it after the
// durable receipt (experiment NoOrphan); without an override the armed point is used.
func TestEffectiveCrashPoint(t *testing.T) {
	cases := []struct{ armed, override, want string }{
		{device.CrashAfterEffectBeforeReceipt, "", device.CrashAfterEffectBeforeReceipt},
		{device.CrashAfterEffectBeforeReceipt, device.CrashAfterReceiptBeforeReply, device.CrashAfterReceiptBeforeReply},
		{"", device.CrashAfterReceiptBeforeReply, ""}, // disarmed stays disarmed
	}
	for _, c := range cases {
		if got := effectiveCrashPoint(c.armed, c.override); got != c.want {
			t.Fatalf("effectiveCrashPoint(%q, %q) = %q, want %q", c.armed, c.override, got, c.want)
		}
	}
	if validCrashOverride("NOPE") == nil || validCrashOverride(device.CrashAfterReceiptBeforeReply) != nil || validCrashOverride("") != nil {
		t.Fatal("validCrashOverride")
	}
}

// TestWanHandler (release 077): the WAN emulation delays the request and the reply by one one-way delay each
// (with jitter within the bound), keeps status, headers and body, and does not delay /healthz.
func TestWanHandler(t *testing.T) {
	inner := http.NewServeMux()
	inner.HandleFunc("/v1/x", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-T", "1")
		w.WriteHeader(202)
		_, _ = w.Write([]byte("ok"))
	})
	inner.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte("h")) })
	h := wanHandler(inner, 40*time.Millisecond, 10*time.Millisecond, rand.New(rand.NewSource(1)))
	for i := 0; i < 5; i++ {
		rr := httptest.NewRecorder()
		t0 := time.Now()
		h.ServeHTTP(rr, httptest.NewRequest("POST", "/v1/x", nil))
		el := time.Since(t0)
		if rr.Code != 202 || rr.Body.String() != "ok" || rr.Header().Get("X-T") != "1" {
			t.Fatalf("reply changed: %d %q %v", rr.Code, rr.Body.String(), rr.Header())
		}
		if el < 60*time.Millisecond || el > 200*time.Millisecond {
			t.Fatalf("round trip %s, want 2 x (40 +/- 10) ms", el)
		}
	}
	rr := httptest.NewRecorder()
	t0 := time.Now()
	h.ServeHTTP(rr, httptest.NewRequest("GET", "/healthz", nil))
	if time.Since(t0) > 20*time.Millisecond || rr.Body.String() != "h" {
		t.Fatalf("/healthz delayed or changed")
	}
}

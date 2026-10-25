// Package httpx builds the HTTP clients used for testbed traffic.
//
// Testbed peers (VM-A..VM-E, 127.0.0.1) are always reached directly. The
// default transport would honour HTTP_PROXY/HTTPS_PROXY, which lab VMs often
// set for package downloads; requests to 192.168.x.y would then go to that
// proxy and fail or be delayed in ways that look like owner faults.
package httpx

import (
	"net/http"
	"time"
)

// Client returns a client with the given timeout that never uses a proxy.
func Client(timeout time.Duration) *http.Client {
	t := http.DefaultTransport.(*http.Transport).Clone()
	t.Proxy = nil
	return &http.Client{Timeout: timeout, Transport: t}
}

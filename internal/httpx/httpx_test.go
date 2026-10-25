package httpx

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestClientIgnoresProxyEnvironment(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(204) }))
	defer srv.Close()
	// A proxy that does not exist: a proxied request would fail.
	t.Setenv("HTTP_PROXY", "http://127.0.0.1:1")
	t.Setenv("http_proxy", "http://127.0.0.1:1")
	t.Setenv("NO_PROXY", "")
	t.Setenv("no_proxy", "")
	c := Client(2 * time.Second)
	if c.Transport.(*http.Transport).Proxy != nil {
		t.Fatal("transport has a proxy function")
	}
	resp, err := c.Get(srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 204 {
		t.Fatalf("status %d", resp.StatusCode)
	}
}

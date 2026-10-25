package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"evicon/internal/domain"
	"evicon/internal/protocol"
)

func TestValidateEndpointLinuxTCAddAllocation(t *testing.T) {
	cap := protocol.EndpointCapabilities{SchemaVersion: protocol.SchemaVersion, Profile: "LINUX_TC_E2_LIKE", AdapterVersion: "linux-test", DomainID: "linux-edge-001", Generation: 1, InstalledEpoch: 1, Dimension: 1, SupportedOperations: []domain.Operation{domain.OpAdd}, ComponentMin: []int64{0}, ComponentMax: []int64{100000}}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/capabilities" {
			http.NotFound(w, r)
			return
		}
		_ = json.NewEncoder(w).Encode(cap)
	}))
	defer srv.Close()
	if _, err := validateEndpoint(srv.URL, "LINUX_TC_E2_LIKE", "linux-edge-001", 1, 1, 1, []int64{0}, []int64{100000}, nil); err != nil {
		t.Fatal(err)
	}
}
func TestValidateEndpointRegularProfilesStillRequireAddAndSet(t *testing.T) {
	cap := protocol.EndpointCapabilities{SchemaVersion: protocol.SchemaVersion, Profile: "E3", AdapterVersion: "e3-test", DomainID: "d", Generation: 1, InstalledEpoch: 1, Dimension: 1, SupportedOperations: []domain.Operation{domain.OpSet}, ComponentMin: []int64{0}, ComponentMax: []int64{12}}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _ = json.NewEncoder(w).Encode(cap) }))
	defer srv.Close()
	if _, err := validateEndpoint(srv.URL, "E3", "d", 1, 1, 1, []int64{0}, []int64{12}, nil); err == nil {
		t.Fatal("E3 must still require ADD+SET")
	}
}

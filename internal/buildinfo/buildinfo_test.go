package buildinfo

import (
	"os"
	"strings"
	"testing"
)

func TestReleaseMatchesVersionFile(t *testing.T) {
	b, err := os.ReadFile("../../VERSION")
	if err != nil {
		t.Fatal(err)
	}
	if v := strings.TrimSpace(string(b)); v != Release {
		t.Fatalf("buildinfo.Release=%q but VERSION=%q", Release, v)
	}
}

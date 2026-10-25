package probe

import "testing"

// D1 is an explicit experimental method rather than a magic environment flag.
// The runtime forces depth=1 while retaining the full M5V2 evidence guard.
func TestM5V2D1IsParsedAsDistinctExperimentalMethod(t *testing.T) {
	m, err := ParseMethod("M5V2D1")
	if err != nil || m != M5V2D1 {
		t.Fatalf("D1 missing: method=%q err=%v", m, err)
	}
	if m == M5V2 || m == M4P {
		t.Fatal("D1 ablation aliased another method")
	}
}

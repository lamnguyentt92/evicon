package domain

import (
	"math"
	"testing"
)

func TestAddCheckedOverflow(t *testing.T) {
	if _, ok := AddChecked(math.MaxInt64, 1); ok {
		t.Fatal("expected overflow")
	}
	if _, ok := AddChecked(math.MinInt64, -1); ok {
		t.Fatal("expected underflow")
	}
}

func TestHardBound(t *testing.T) {
	m := int64(10)
	b := HardBound{ComponentMin: []int64{0, 0}, SumMax: &m}
	if !b.Holds(State{3, 5}) {
		t.Fatal("3+5 should satisfy bound")
	}
	if b.Holds(State{8, 5}) {
		t.Fatal("8+5 should violate sum bound")
	}
}

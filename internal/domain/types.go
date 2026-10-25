package domain

import (
	"errors"
	"fmt"
	"math"
	"strings"
)

var ErrInt64Overflow = errors.New("int64 overflow")

type Operation string

const (
	OpAdd Operation = "ADD"
	OpSet Operation = "SET"
)

type CommandStatus string

const (
	Pending CommandStatus = "PENDING"
	Applied CommandStatus = "APPLIED"
	Stopped CommandStatus = "STOPPED"
)

type State []int64

func (s State) Clone() State {
	out := make(State, len(s))
	copy(out, s)
	return out
}

type Command struct {
	ID         string    `json:"id"`
	Generation uint64    `json:"generation"`
	Epoch      uint64    `json:"epoch"`
	Sequence   uint64    `json:"sequence"`
	Operation  Operation `json:"operation"`
	Value      []int64   `json:"value"`
}

func (c Command) Validate(dim int) error {
	if strings.TrimSpace(c.ID) == "" {
		return errors.New("command id is required")
	}
	if c.Operation != OpAdd && c.Operation != OpSet {
		return fmt.Errorf("unsupported operation %q", c.Operation)
	}
	if len(c.Value) != dim {
		return fmt.Errorf("command %s has dimension %d, want %d", c.ID, len(c.Value), dim)
	}
	// The TOIT prototype scope is explicitly restricted to non-negative ADD.
	if c.Operation == OpAdd {
		for i, v := range c.Value {
			if v < 0 {
				return fmt.Errorf("command %s ADD component %d is negative: %d", c.ID, i, v)
			}
		}
	}
	return nil
}

type HardBound struct {
	ComponentMin []int64 `json:"componentMin,omitempty"`
	ComponentMax []int64 `json:"componentMax,omitempty"`
	SumMax       *int64  `json:"sumMax,omitempty"`
}

func (b HardBound) Validate(dim int) error {
	if len(b.ComponentMin) != 0 && len(b.ComponentMin) != dim {
		return fmt.Errorf("componentMin dimension %d, want %d", len(b.ComponentMin), dim)
	}
	if len(b.ComponentMax) != 0 && len(b.ComponentMax) != dim {
		return fmt.Errorf("componentMax dimension %d, want %d", len(b.ComponentMax), dim)
	}
	for i := 0; i < dim; i++ {
		if len(b.ComponentMin) != 0 && len(b.ComponentMax) != 0 && b.ComponentMin[i] > b.ComponentMax[i] {
			return fmt.Errorf("component %d has min > max", i)
		}
	}
	return nil
}

// Check evaluates H(x) while preserving the distinction between a real bound
// violation and arithmetic that cannot be represented in the Go int64 model.
func (b HardBound) Check(state State) (bool, error) {
	if len(b.ComponentMin) != 0 {
		if len(b.ComponentMin) != len(state) {
			return false, fmt.Errorf("componentMin dimension %d, state dimension %d", len(b.ComponentMin), len(state))
		}
		for i, v := range state {
			if v < b.ComponentMin[i] {
				return false, nil
			}
		}
	}
	if len(b.ComponentMax) != 0 {
		if len(b.ComponentMax) != len(state) {
			return false, fmt.Errorf("componentMax dimension %d, state dimension %d", len(b.ComponentMax), len(state))
		}
		for i, v := range state {
			if v > b.ComponentMax[i] {
				return false, nil
			}
		}
	}
	if b.SumMax != nil {
		sum, ok := SumChecked(state)
		if !ok {
			return false, ErrInt64Overflow
		}
		if sum > *b.SumMax {
			return false, nil
		}
	}
	return true, nil
}

// Holds is retained as a convenience for tests/helpers. Arithmetic overflow is
// deliberately not treated as a proven safety violation; callers that need to
// distinguish the cases must use Check.
func (b HardBound) Holds(state State) bool {
	ok, err := b.Check(state)
	return err == nil && ok
}

func AddChecked(a, b int64) (int64, bool) {
	if b > 0 && a > math.MaxInt64-b {
		return 0, false
	}
	if b < 0 && a < math.MinInt64-b {
		return 0, false
	}
	return a + b, true
}

func SumChecked(values []int64) (int64, bool) {
	var total int64
	for _, v := range values {
		next, ok := AddChecked(total, v)
		if !ok {
			return 0, false
		}
		total = next
	}
	return total, true
}

func Apply(state State, cmd Command) (State, error) {
	if len(state) != len(cmd.Value) {
		return nil, fmt.Errorf("dimension mismatch: state=%d command=%d", len(state), len(cmd.Value))
	}
	switch cmd.Operation {
	case OpSet:
		return State(cmd.Value).Clone(), nil
	case OpAdd:
		out := state.Clone()
		for i := range out {
			v, ok := AddChecked(out[i], cmd.Value[i])
			if !ok {
				return nil, fmt.Errorf("%w at component %d", ErrInt64Overflow, i)
			}
			out[i] = v
		}
		return out, nil
	default:
		return nil, fmt.Errorf("unsupported operation %q", cmd.Operation)
	}
}

func IsNonNegativeAdd(c Command) bool {
	if c.Operation != OpAdd {
		return false
	}
	for _, v := range c.Value {
		if v < 0 {
			return false
		}
	}
	return true
}

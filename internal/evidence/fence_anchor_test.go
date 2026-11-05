package evidence

// Release 086 (verifier finding B2-02): FenceAnchorStates, the anchor of a complete fence computed from evidence.

import (
	"math/rand"
	"testing"

	"evicon/internal/domain"
	"evicon/internal/journal"
)

func anchorDomain(anchor []int64, cutoff uint64) journal.Domain {
	return journal.Domain{ID: "d", Generation: 1, InstalledEpoch: 1, Dimension: len(anchor), Anchor: anchor, CutoffSequence: cutoff}
}

func rec(id string, seq uint64, op domain.Operation, v []int64, status string) journal.CommandRecord {
	return journal.CommandRecord{DomainID: "d", CommandID: id, Generation: 1, Epoch: 1, Sequence: seq, Operation: op, Value: v, Status: status, Decision: "ADMITTED"}
}

func TestFenceAnchorStatesUnit086(t *testing.T) {
	// ADD, two dimensions: the previous anchor plus the APPLIED vectors; STOPPED contributes nothing
	got, err := FenceAnchorStates(anchorDomain([]int64{10, 1}, 4), []journal.CommandRecord{
		rec("a", 5, domain.OpAdd, []int64{3, 0}, "APPLIED"), rec("b", 6, domain.OpAdd, []int64{2, 2}, "STOPPED"),
		rec("c", 7, domain.OpAdd, []int64{0, 5}, "APPLIED")}, 256)
	if err != nil || len(got) != 1 || got[0][0] != 13 || got[0][1] != 6 {
		t.Fatalf("ADD batch: %v %v", got, err)
	}
	// an empty batch keeps the previous anchor
	if got, err := FenceAnchorStates(anchorDomain([]int64{7}, 3), nil, 256); err != nil || len(got) != 1 || got[0][0] != 7 {
		t.Fatalf("empty batch: %v %v", got, err)
	}
	// SETs only: the largest-sequence APPLIED SET is the state
	if got, err := FenceAnchorStates(anchorDomain([]int64{7}, 0), []journal.CommandRecord{
		rec("s1", 1, domain.OpSet, []int64{4}, "APPLIED"), rec("s2", 2, domain.OpSet, []int64{9}, "APPLIED"), rec("s3", 3, domain.OpSet, []int64{1}, "STOPPED")}, 256); err != nil || len(got) != 1 || got[0][0] != 9 {
		t.Fatalf("SET batch: %v %v", got, err)
	}
	// a SET and an ADD: the ADD may have taken effect before or after the SET: two states
	if got, err := FenceAnchorStates(anchorDomain([]int64{0}, 0), []journal.CommandRecord{
		rec("s", 1, domain.OpSet, []int64{5}, "APPLIED"), rec("a", 2, domain.OpAdd, []int64{2}, "APPLIED")}, 256); err != nil || len(got) != 2 || got[0][0] != 5 || got[1][0] != 7 {
		t.Fatalf("SET+ADD batch: %v %v", got, err)
	}
	// no terminal knowledge, or an identity covered by the previous anchor: an error
	if _, err := FenceAnchorStates(anchorDomain([]int64{0}, 0), []journal.CommandRecord{rec("x", 1, domain.OpAdd, []int64{1}, "PENDING")}, 256); err == nil {
		t.Fatal("a PENDING identity must be refused")
	}
	if _, err := FenceAnchorStates(anchorDomain([]int64{0}, 5), []journal.CommandRecord{rec("x", 3, domain.OpAdd, []int64{1}, "APPLIED")}, 256); err == nil {
		t.Fatal("an identity at or below the previous cutoff must be refused")
	}
	// an APPLIED command of another generation is inconsistent
	bad := rec("g", 1, domain.OpAdd, []int64{1}, "APPLIED")
	bad.Generation = 2
	if _, err := FenceAnchorStates(anchorDomain([]int64{0}, 0), []journal.CommandRecord{bad}, 256); err == nil {
		t.Fatal("an APPLIED command of another generation must be inconsistent")
	}
}

// TestFenceAnchorStatesProperty086: random batches (1 or 2 dimensions, ADD and SET, losses), each executed by a random
// owner the model allows (any delivery order; a SET below the SET frontier is refused; a lost request never takes
// effect): the owner's true state is always one of the computed states (FC and DR: the evidence is every identity's
// true outcome), the computed state is unique and equals sequence-order application whenever no SET applied, and the
// sum bound of the true state is the sum bound of a computed state.
func TestFenceAnchorStatesProperty086(t *testing.T) {
	rng := rand.New(rand.NewSource(86))
	unique, multi := 0, 0
	for trial := 0; trial < 4000; trial++ {
		dim := 1 + rng.Intn(2) // the belief supports dimensions 1 and 2
		prev := make([]int64, dim)
		for i := range prev {
			prev[i] = int64(rng.Intn(20))
		}
		cut := uint64(rng.Intn(5))
		n := 1 + rng.Intn(7)
		cmds := make([]journal.CommandRecord, 0, n)
		for i := 0; i < n; i++ {
			op := domain.OpAdd
			if rng.Intn(4) == 0 {
				op = domain.OpSet
			}
			v := make([]int64, dim)
			for j := range v {
				v[j] = int64(rng.Intn(6))
			}
			cmds = append(cmds, rec("c"+string(rune('a'+i)), cut+uint64(i)+1, op, v, ""))
		}
		// one execution of the owner: a random delivery order of the commands that are not lost
		order := rng.Perm(n)
		state := domain.State(append([]int64(nil), prev...))
		frontier := cut
		status := make([]string, n)
		appliedSet := false
		for _, i := range order {
			c := cmds[i]
			if rng.Intn(4) == 0 { // lost: never takes effect (unlisted or STOPPED in the fence answer)
				status[i] = "STOPPED"
				continue
			}
			if c.Operation == domain.OpSet && c.Sequence < frontier {
				status[i] = "STOPPED" // refused by the SET frontier
				continue
			}
			next, err := domain.Apply(state, domain.Command{ID: c.CommandID, Operation: c.Operation, Value: c.Value, Generation: 1, Epoch: 1, Sequence: c.Sequence})
			if err != nil {
				t.Fatal(err)
			}
			state = next
			if c.Operation == domain.OpSet {
				frontier = c.Sequence
				appliedSet = true
			}
			status[i] = "APPLIED"
		}
		batch := make([]journal.CommandRecord, n)
		for i := range cmds {
			batch[i] = cmds[i]
			batch[i].Status = status[i]
		}
		got, err := FenceAnchorStates(anchorDomain(prev, cut), batch, 4096)
		if err != nil {
			t.Fatalf("trial %d: %v", trial, err)
		}
		found := false
		for _, s := range got {
			if sameVec(s, state) {
				found = true
			}
		}
		if !found {
			t.Fatalf("trial %d: the owner's true state %v is not among the computed %v (batch %+v)", trial, state, got, batch)
		}
		if !appliedSet {
			seqOrder := domain.State(append([]int64(nil), prev...))
			for _, c := range batch {
				if c.Status == "APPLIED" {
					seqOrder, _ = domain.Apply(seqOrder, domain.Command{Operation: c.Operation, Value: c.Value})
				}
			}
			if len(got) != 1 || !sameVec(got[0], seqOrder) {
				t.Fatalf("trial %d: ADD-only batch: computed %v, sequence order %v", trial, got, seqOrder)
			}
		}
		if len(got) == 1 {
			unique++
		} else {
			multi++
		}
		sum := int64(17)
		b := domain.HardBound{SumMax: &sum}
		okTrue, _ := b.Check(state)
		okAny := false
		for _, s := range got {
			if ok, _ := b.Check(s); ok == okTrue {
				okAny = true
			}
		}
		if !okAny {
			t.Fatalf("trial %d: the sum bound of the true state is not that of any computed state", trial)
		}
	}
	if unique == 0 || multi == 0 {
		t.Fatalf("the trials must contain unique (%d) and several-state (%d) batches", unique, multi)
	}
	t.Logf("4000 random batches: %d with one computed state (= the owner's true state), %d with several (the true state among them)", unique, multi)
}

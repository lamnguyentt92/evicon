// Package device is the configurable IoT device emulator of release 061 (plan
// v2.1 section 7.5): one actuator ("dosing pump") whose evidence contract is
// chosen by configuration, so the same EviCon authority can be run against a
// fleet of K0..K4 owners on the VMware testbed.
//
//	D_h  DedupHorizon  >0 finite, <0 unbounded, 0 none
//	Q_A  Query         receipts answer GET /v1/receipts/<id>
//	Q_S  Stop          POST /v1/stop records a durable per-identity tombstone
//	F    Fence         POST /v1/barriers installs a durable sequence cutoff
//	X    Atomic        claim-before-effect + idempotent plant write: a
//	                   same-identity replay can never duplicate the effect
//	T    enforced      requests carrying expires_at_ms are rejected after it
//
// Physical effects go to the plant (internal/plant) over its socket; the
// device journal (JSONL + fsync) holds claims, receipts, tombstones and the
// cutoff, and is replayed on restart. The device speaks the EviCon endpoint
// protocol (internal/protocol) so the runtime authority can drive it.
package device

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"evicon/internal/contract"
	"evicon/internal/domain"
	"evicon/internal/protocol"
)

var (
	ErrIdentityConflict = errors.New("identity conflict")
	ErrOffline          = errors.New("device offline")
	ErrUnsupported      = errors.New("unsupported by this device contract")
	// ErrTerminalReversal: refused write of the opposite terminal outcome (release 068).
	ErrTerminalReversal = errors.New("device: terminal outcome reversal refused")
)

func terminal(status string) bool { return status == "APPLIED" || status == "STOPPED" }

// Plant is the effect sink (internal/plant over a socket, or in-process).
type Plant interface {
	Apply(device, cmd string, value int64, once bool) error
}

type Config struct {
	DeviceID       string
	DomainID       string
	Generation     uint64
	InstalledEpoch uint64
	StateDir       string
	DedupHorizon   time.Duration
	Query          bool
	Stop           bool
	Fence          bool
	Atomic         bool
	EnforceExpiry  bool
	// ExpiryHint is advertised as expiry_ms in the declared contract (T).
	ExpiryHint  time.Duration
	ClockSkew   time.Duration
	Bound       int64 // max cumulative level; 0 = unbounded
	EffectDelay time.Duration
	// OfflineMode: "queue" holds effect requests until the device is back
	// (broker-like late delivery); "drop" refuses them while offline.
	OfflineMode string
	MaxHold     time.Duration
	// Release 072, negative controls only (never in a measured or confirmatory
	// configuration). They break one owner premise while advertising the same
	// contract, so that the testbed checker must report a violation:
	//   NegSealOrphans: a fence reports a crash orphan (claim without receipt)
	//     as STOPPED instead of UNCERTAIN (the RV04 bug; TLA+ "sealorphans").
	//   NegReexecute: a delivery of an already APPLIED identity executes again
	//     although D_h is advertised (TLA+ "blindretry").
	NegSealOrphans bool
	NegReexecute   bool
	Plant          Plant
	// CrashHook is called at named crash points; the binary exits there.
	CrashHook func(point, commandID string)
	Now       func() time.Time
}

const (
	CrashAfterClaimBeforeEffect   = "AFTER_CLAIM_BEFORE_EFFECT"
	CrashAfterEffectBeforeReceipt = "AFTER_EFFECT_BEFORE_RECEIPT"
	CrashAfterReceiptBeforeReply  = "AFTER_RECEIPT_BEFORE_REPLY"
)

type record struct {
	ID         string `json:"id"`
	Hash       string `json:"hash"`
	Sequence   uint64 `json:"seq"`
	Value      int64  `json:"value"`
	Status     string `json:"status"` // CLAIMED, APPLIED, STOPPED
	Reason     string `json:"reason,omitempty"`
	OwnerVer   uint64 `json:"owner_version,omitempty"`
	ReceiptVer uint64 `json:"receipt_version,omitempty"`
	Level      int64  `json:"level,omitempty"`
	AtMS       int64  `json:"at_ms"`
	Durable    bool   `json:"durable_terminal,omitempty"` // tombstone/fence/expiry: never evicted
}

type entry struct {
	Kind   string  `json:"k"` // REC, CUTOFF
	Rec    *record `json:"r,omitempty"`
	Cutoff uint64  `json:"c,omitempty"`
	Token  string  `json:"tok,omitempty"`
	Req    uint64  `json:"req,omitempty"` // cutoff requested under Token
	Level  int64   `json:"lvl,omitempty"`
	OwnerV uint64  `json:"ov,omitempty"`
}

type Device struct {
	cfg     Config
	mu      sync.Mutex
	cond    *sync.Cond
	journal *os.File
	recs    map[string]*record
	cutoff  uint64
	tokens  map[string]uint64
	level   int64
	ownerV  uint64
	online  bool
	effects int
	// preEffect marks claims of this process whose plant write has not
	// started; only those may still be stopped or fenced as STOPPED.
	preEffect map[string]bool
}

// effectRecord (release 077) is one line of <StateDir>/<DeviceID>.effects.jsonl: which dispatch
// attempt of a command took effect (0: the request carried no attempt number).
type effectRecord struct {
	CommandID string `json:"command_id"`
	Attempt   int64  `json:"attempt"`
	AtUnixMS  int64  `json:"at_unix_ms"`
}

// logEffect appends the attempt that took effect; best effort (the plant log stays the truth).
// The caller holds d.mu.
func (d *Device) logEffect(id string, attempt int64) {
	f, err := os.OpenFile(filepath.Join(d.cfg.StateDir, d.cfg.DeviceID+".effects.jsonl"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	b, _ := json.Marshal(effectRecord{CommandID: id, Attempt: attempt, AtUnixMS: d.cfg.Now().UnixMilli()})
	_, _ = f.Write(append(b, '\n'))
}

func Open(cfg Config) (*Device, error) {
	if cfg.DeviceID == "" || cfg.DomainID == "" || cfg.StateDir == "" || cfg.Plant == nil {
		return nil, fmt.Errorf("device id, domain, state dir and plant are required")
	}
	if cfg.Generation == 0 {
		cfg.Generation = 1
	}
	if cfg.InstalledEpoch == 0 {
		cfg.InstalledEpoch = 1
	}
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	if cfg.OfflineMode == "" {
		cfg.OfflineMode = "queue"
	}
	if cfg.OfflineMode != "queue" && cfg.OfflineMode != "drop" {
		return nil, fmt.Errorf("offline mode must be queue or drop")
	}
	if cfg.MaxHold <= 0 {
		cfg.MaxHold = 10 * time.Minute
	}
	if cfg.Atomic && cfg.DedupHorizon >= 0 && cfg.DedupHorizon != 0 {
		return nil, fmt.Errorf("atomic (X) devices deduplicate forever; set dedup horizon < 0")
	}
	if cfg.Atomic && cfg.DedupHorizon == 0 {
		cfg.DedupHorizon = -1
	}
	if err := os.MkdirAll(cfg.StateDir, 0o755); err != nil {
		return nil, err
	}
	d := &Device{cfg: cfg, recs: map[string]*record{}, tokens: map[string]uint64{}, online: true, preEffect: map[string]bool{}}
	d.cond = sync.NewCond(&d.mu)
	path := filepath.Join(cfg.StateDir, cfg.DeviceID+".journal")
	if err := d.replay(path); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return nil, err
	}
	d.journal = f
	if err := d.recoverAtomic(); err != nil {
		return nil, err
	}
	return d, nil
}

func (d *Device) Close() error {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.journal == nil {
		return nil
	}
	err := d.journal.Close()
	d.journal = nil
	return err
}

func (d *Device) replay(path string) error {
	f, err := os.Open(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	for sc.Scan() {
		var e entry
		if err := json.Unmarshal(sc.Bytes(), &e); err != nil {
			if !sc.Scan() {
				break // torn final line after a crash
			}
			return fmt.Errorf("device journal corrupt: %v", err)
		}
		d.applyEntry(e)
	}
	return sc.Err()
}

func (d *Device) applyEntry(e entry) {
	switch e.Kind {
	case "REC":
		r := *e.Rec
		d.recs[r.ID] = &r
	case "CUTOFF":
		if e.Cutoff > d.cutoff {
			d.cutoff = e.Cutoff
		}
		if e.Token != "" {
			d.tokens[e.Token] = e.Req
		}
	}
	if e.Level != 0 || e.OwnerV != 0 {
		d.level = e.Level
	}
	if e.OwnerV > d.ownerV {
		d.ownerV = e.OwnerV
	}
}

func (d *Device) write(e entry) error {
	if d.journal == nil {
		return errors.New("device closed")
	}
	b, _ := json.Marshal(e)
	if _, err := d.journal.Write(append(b, '\n')); err != nil {
		return err
	}
	if err := d.journal.Sync(); err != nil {
		return err
	}
	d.applyEntry(e)
	return nil
}

func (d *Device) crash(point, id string) {
	if d.cfg.CrashHook != nil {
		d.cfg.CrashHook(point, id)
	}
}

// recoverAtomic completes claims left by a crash on an atomic (X) device:
// the plant write is idempotent per identity, so re-applying is safe and
// yields the receipt that should have been written atomically.
func (d *Device) recoverAtomic() error {
	if !d.cfg.Atomic {
		return nil
	}
	ids := make([]string, 0)
	for id, r := range d.recs {
		if r.Status == "CLAIMED" {
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	for _, id := range ids {
		r := d.recs[id]
		if err := d.cfg.Plant.Apply(d.cfg.DeviceID, id, r.Value, true); err != nil {
			return fmt.Errorf("atomic recovery of %s: %w", id, err)
		}
		if err := d.finish(r, "APPLIED", "ATOMIC_RECOVERY", r.Value); err != nil {
			return err
		}
	}
	return nil
}

func (d *Device) finish(r *record, status, reason string, delta int64) error {
	if cur := d.recs[r.ID]; cur != nil && cur.Hash == r.Hash && terminal(cur.Status) && terminal(status) && cur.Status != status {
		// Release 068 invariant: the device never journals the opposite terminal outcome for
		// one identity (its receipts are owner evidence). A re-execution after the device
		// forgot an identity (no dedup) is APPLIED again, never STOPPED over APPLIED.
		return fmt.Errorf("%w: %s %s -> %s", ErrTerminalReversal, r.ID, cur.Status, status)
	}
	nr := *r
	nr.Status, nr.Reason = status, reason
	d.ownerV++
	lvl := d.level
	if status == "APPLIED" {
		lvl += delta
	}
	nr.OwnerVer, nr.ReceiptVer, nr.Level, nr.AtMS = d.ownerV, d.ownerV, lvl, d.cfg.Now().UnixMilli()
	return d.write(entry{Kind: "REC", Rec: &nr, Level: lvl, OwnerV: d.ownerV})
}

func (d *Device) receipt(r *record) protocol.Receipt {
	out := protocol.Receipt{SchemaVersion: 1, DomainID: d.cfg.DomainID, Generation: d.cfg.Generation, CommandID: r.ID, IntentHash: r.Hash, Reason: r.Reason}
	switch r.Status {
	case "APPLIED", "STOPPED":
		out.Outcome, out.OwnerVersion, out.ReceiptVersion, out.State = r.Status, r.OwnerVer, r.ReceiptVer, []int64{r.Level}
	default:
		out.Outcome = "UNCERTAIN"
		if out.Reason == "" {
			out.Reason = "CLAIMED_WITHOUT_RECEIPT"
		}
	}
	return out
}

func (d *Device) dedupLive(r *record, now time.Time) bool {
	if r.Durable {
		return true // tombstones, fences and expiries are owner records, not cache
	}
	h := d.cfg.DedupHorizon
	switch {
	case h < 0:
		return true
	case h == 0:
		return false
	default:
		return now.Sub(time.UnixMilli(r.AtMS)) <= h
	}
}

// SetOnline toggles connectivity; queued requests resume when back online.
func (d *Device) SetOnline(v bool) {
	d.mu.Lock()
	d.online = v
	d.cond.Broadcast()
	d.mu.Unlock()
}

func (d *Device) Online() bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	return d.online
}

func (d *Device) waitOnlineLocked() error {
	if d.online {
		return nil
	}
	if d.cfg.OfflineMode == "drop" {
		return ErrOffline
	}
	deadline := d.cfg.Now().Add(d.cfg.MaxHold)
	t := time.AfterFunc(d.cfg.MaxHold, func() { d.mu.Lock(); d.cond.Broadcast(); d.mu.Unlock() })
	defer t.Stop()
	for !d.online {
		if d.cfg.Now().After(deadline) {
			return ErrOffline
		}
		d.cond.Wait()
	}
	return nil
}

// Apply executes one command request (POST /v1/effects).
func (d *Device) Apply(req protocol.CommandRequest) (protocol.Receipt, error) {
	return d.ApplyAttempt(req, 0)
}

// ApplyAttempt is Apply with the dispatch attempt the request carried (release 077; 0 = unknown),
// recorded when this request is the one that takes effect.
func (d *Device) ApplyAttempt(req protocol.CommandRequest, attempt int64) (protocol.Receipt, error) {
	if err := req.ValidateAndFillHash(1); err != nil {
		return protocol.Receipt{}, err
	}
	if req.DomainID != d.cfg.DomainID || req.Generation != d.cfg.Generation {
		return protocol.Receipt{}, fmt.Errorf("domain/generation mismatch")
	}
	if req.Operation != domain.OpAdd {
		return protocol.Receipt{}, fmt.Errorf("device actuators accept ADD only")
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if err := d.waitOnlineLocked(); err != nil {
		return protocol.Receipt{}, err
	}
	now := d.cfg.Now()
	if r, ok := d.recs[req.CommandID]; ok {
		if r.Hash != req.IntentHash {
			return protocol.Receipt{}, ErrIdentityConflict
		}
		if d.cfg.NegReexecute && r.Status == "APPLIED" {
			// negative control: the advertised dedup is not honoured
			if err := d.cfg.Plant.Apply(d.cfg.DeviceID, r.ID, r.Value, d.cfg.Atomic); err != nil {
				return protocol.Receipt{}, fmt.Errorf("plant: %w", err)
			}
			d.effects++
			d.level += r.Value
			d.logEffect(r.ID, attempt)
			return d.receipt(r), nil
		}
		if d.dedupLive(r, now) {
			return d.receipt(r), nil // replay (or UNCERTAIN for a crash-orphaned claim)
		}
		if d.cfg.Fence && r.Sequence <= d.cutoff {
			// Release 068: a record at or below an installed cutoff is owner evidence that the
			// barrier has reported (F: durable terminal receipts for every identity at or below
			// the cutoff). A late copy gets that receipt back. Before 068 a device without dedup
			// "forgot" the record here and a late copy of an APPLIED command was sealed as
			// STOPPED below, overwriting the APPLIED receipt: a false STOPPED from the owner,
			// which the authority refused ("terminal outcome reversal") while the domain stayed
			// blocked (067 confirmatory campaign, 6 of 126 cells).
			return d.receipt(r), nil
		}
		// evicted from the dedup horizon: the device has forgotten it.
	}
	r := &record{ID: req.CommandID, Hash: req.IntentHash, Sequence: req.Sequence, Value: req.Value[0], AtMS: now.UnixMilli()}
	if d.cfg.Fence && req.Sequence <= d.cutoff {
		r.Durable = true
		return d.terminalLocked(r, "STOPPED", "SEALED_BY_FENCE")
	}
	if d.cfg.EnforceExpiry && req.ExpiresAtMS > 0 && now.UnixMilli() > req.ExpiresAtMS {
		r.Durable = true
		return d.terminalLocked(r, "STOPPED", "EXPIRED_AT_DEVICE")
	}
	if d.cfg.Bound > 0 && d.level+r.Value > d.cfg.Bound {
		return d.terminalLocked(r, "STOPPED", "BOUND_REJECT")
	}
	claim := *r
	claim.Status = "CLAIMED"
	if err := d.write(entry{Kind: "REC", Rec: &claim}); err != nil {
		return protocol.Receipt{}, err
	}
	d.crash(CrashAfterClaimBeforeEffect, r.ID)
	if d.cfg.EffectDelay > 0 {
		d.preEffect[r.ID] = true
		d.mu.Unlock()
		time.Sleep(d.cfg.EffectDelay)
		d.mu.Lock()
		delete(d.preEffect, r.ID)
		// a stop or fence may have resolved the claim meanwhile
		if cur := d.recs[r.ID]; cur != nil && cur.Status != "CLAIMED" {
			return d.receipt(cur), nil
		}
	}
	if err := d.cfg.Plant.Apply(d.cfg.DeviceID, r.ID, r.Value, d.cfg.Atomic); err != nil {
		return protocol.Receipt{}, fmt.Errorf("plant: %w", err)
	}
	d.effects++
	d.logEffect(r.ID, attempt)
	d.crash(CrashAfterEffectBeforeReceipt, r.ID)
	if err := d.finish(&claim, "APPLIED", "APPLIED", r.Value); err != nil {
		return protocol.Receipt{}, err
	}
	d.crash(CrashAfterReceiptBeforeReply, r.ID)
	return d.receipt(d.recs[r.ID]), nil
}

func (d *Device) terminalLocked(r *record, status, reason string) (protocol.Receipt, error) {
	if err := d.finish(r, status, reason, 0); err != nil {
		return protocol.Receipt{}, err
	}
	return d.receipt(d.recs[r.ID]), nil
}

// Receipt answers GET /v1/receipts/<id> (Q_A).
func (d *Device) Receipt(id string) (protocol.Receipt, error) {
	if !d.cfg.Query {
		return protocol.Receipt{}, ErrUnsupported
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if !d.online {
		return protocol.Receipt{}, ErrOffline
	}
	r, ok := d.recs[id]
	if !ok {
		return protocol.Receipt{}, os.ErrNotExist
	}
	return d.receipt(r), nil
}

// Stop records a durable per-identity tombstone (Q_S).
func (d *Device) Stop(req protocol.StopRequest) (protocol.Receipt, error) {
	if !d.cfg.Stop {
		return protocol.Receipt{}, ErrUnsupported
	}
	if err := req.Validate(); err != nil {
		return protocol.Receipt{}, err
	}
	if req.DomainID != d.cfg.DomainID || req.Generation != d.cfg.Generation {
		return protocol.Receipt{}, fmt.Errorf("domain/generation mismatch")
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if !d.online {
		return protocol.Receipt{}, ErrOffline
	}
	if r, ok := d.recs[req.CommandID]; ok {
		if r.Hash != req.IntentHash {
			return protocol.Receipt{}, ErrIdentityConflict
		}
		if r.Status != "CLAIMED" {
			return d.receipt(r), nil
		}
		if d.preEffect[r.ID] {
			// in flight, plant write not started: the stop wins durably
			s := *r
			s.Durable = true
			return d.terminalLocked(&s, "STOPPED", "STOP_BEFORE_EFFECT")
		}
		// A claim without receipt from before a crash: the effect may or may
		// not have happened. Only an atomic device knows (and it recovered
		// such claims at start-up); others must not answer STOPPED.
		return d.receipt(r), nil
	}
	r := &record{ID: req.CommandID, Hash: req.IntentHash, Sequence: req.Sequence, AtMS: d.cfg.Now().UnixMilli(), Durable: true}
	return d.terminalLocked(r, "STOPPED", "STOP_TOMBSTONE")
}

// Barrier installs a durable cutoff (F) and returns terminal receipts for
// every identity at or below it.
func (d *Device) Barrier(req protocol.BarrierRequest) (protocol.BarrierReceipt, error) {
	if !d.cfg.Fence {
		return protocol.BarrierReceipt{}, ErrUnsupported
	}
	if req.DomainID != d.cfg.DomainID || req.Generation != d.cfg.Generation || req.Token == "" {
		return protocol.BarrierReceipt{}, fmt.Errorf("invalid barrier request")
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if !d.online {
		return protocol.BarrierReceipt{}, ErrOffline
	}
	prev, seen := d.tokens[req.Token]
	if seen && prev != req.CutoffSequence {
		return protocol.BarrierReceipt{}, ErrIdentityConflict
	}
	if !seen {
		cut := req.CutoffSequence
		if d.cutoff > cut {
			cut = d.cutoff
		}
		d.ownerV++
		if err := d.write(entry{Kind: "CUTOFF", Cutoff: cut, Token: req.Token, Req: req.CutoffSequence, Level: d.level, OwnerV: d.ownerV}); err != nil {
			return protocol.BarrierReceipt{}, err
		}
	}
	ids := make([]string, 0)
	for id, r := range d.recs {
		if r.Sequence <= req.CutoffSequence {
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	for _, id := range ids {
		r := d.recs[id]
		if r.Status == "CLAIMED" && (d.preEffect[id] || d.cfg.NegSealOrphans) {
			// NegSealOrphans (negative control): an orphan, whose effect did
			// happen, is sealed as STOPPED like a claim that never started.
			s := *r
			s.Durable = true
			reason := "SEALED_BY_FENCE"
			if !d.preEffect[id] {
				reason = "NEG_CONTROL_SEALED_ORPHAN"
			}
			if err := d.finish(&s, "STOPPED", reason, 0); err != nil {
				return protocol.BarrierReceipt{}, err
			}
		}
	}
	out := protocol.BarrierReceipt{SchemaVersion: 1, DomainID: d.cfg.DomainID, Generation: d.cfg.Generation, Token: req.Token,
		CutoffSequence: req.CutoffSequence, OwnerVersion: d.ownerV, State: []int64{d.level}, Reason: "DEVICE_CUTOFF_INSTALLED"}
	for _, id := range ids {
		out.Receipts = append(out.Receipts, d.receipt(d.recs[id]))
	}
	return out, nil
}

func (d *Device) State() (level int64, ownerV uint64, cutoff uint64, effects int) {
	d.mu.Lock()
	defer d.mu.Unlock()
	return d.level, d.ownerV, d.cutoff, d.effects
}

// Contract is the device's declared evidence contract; it is RUNTIME_VERIFIED
// by the tests in device_test.go (D01-D10) for exactly these settings.
func (d *Device) Contract() contract.EvidenceContract {
	c := contract.EvidenceContract{SchemaVersion: 1, ID: "device-" + d.cfg.DeviceID, System: "evicon-device emulator", Operation: "ADD (dosing pulse)",
		Source: contract.SourceRuntimeVerified, ClockSkewMS: d.cfg.ClockSkew.Milliseconds(),
		VerifiedTestIDs: []string{"D01", "D02", "D03", "D04", "D05", "D06", "D07", "D08", "D09", "D10"}}
	if d.cfg.DedupHorizon != 0 {
		c.Capabilities = append(c.Capabilities, contract.CapDedup)
		if d.cfg.DedupHorizon < 0 {
			c.DedupHorizonMS = contract.UnboundedHorizonMS
		} else {
			c.DedupHorizonMS = d.cfg.DedupHorizon.Milliseconds()
		}
	}
	if d.cfg.Query {
		c.Capabilities = append(c.Capabilities, contract.CapQueryApplied)
	}
	if d.cfg.Stop {
		c.Capabilities = append(c.Capabilities, contract.CapStop)
		c.Premises = append(c.Premises, contract.PremisePerIdentityStop)
	}
	if d.cfg.Fence {
		c.Capabilities = append(c.Capabilities, contract.CapFence)
	}
	if d.cfg.Atomic {
		c.Capabilities = append(c.Capabilities, contract.CapAtomic)
	}
	if d.cfg.EnforceExpiry && d.cfg.ExpiryHint > 0 {
		c.Capabilities = append(c.Capabilities, contract.CapExpiry)
		c.ExpiryMS = d.cfg.ExpiryHint.Milliseconds()
		c.Premises = append(c.Premises, contract.PremiseOwnerEnforcedExpiry, contract.PremiseBoundedClockSkew)
	}
	return c
}

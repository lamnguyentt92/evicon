// Package plant is the independent truth channel for IoT experiments (release
// 061, plan v2.1 section 7.5). Devices report every physical effect (e.g. a
// dosing pulse) to the plant through a local socket; the plant appends it to a
// durable JSONL log before acknowledging. Receipts never pass through the
// plant, so a crash between effect and receipt is a real software crash, and
// the harness/checker reads effects from the plant, never from devices.
package plant

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sort"
	"sync"
	"time"
)

type ApplyRequest struct {
	Op     string `json:"op"`
	Device string `json:"device"`
	Cmd    string `json:"cmd"`
	Value  int64  `json:"value"`
	// Once makes the plant itself idempotent per (device, cmd): a test-only
	// way to emulate an atomic effect+receipt (X) device.
	Once bool `json:"once,omitempty"`
}

type ApplyResult struct {
	OK        bool   `json:"ok"`
	Seq       uint64 `json:"seq,omitempty"`
	Effects   int    `json:"effects_for_cmd"`
	Total     int    `json:"total"`
	Duplicate bool   `json:"duplicate_suppressed,omitempty"`
	Error     string `json:"error,omitempty"`
}

type record struct {
	Seq    uint64 `json:"seq"`
	TimeNS int64  `json:"t_ns"`
	Device string `json:"device"`
	Cmd    string `json:"cmd"`
	Value  int64  `json:"value"`
}

type Plant struct {
	mu        sync.Mutex
	f         *os.File
	fsync     bool
	seq       uint64
	total     int
	perCmd    map[string]int
	perDevCmd map[string]int
	devValue  map[string]int64
	devCount  map[string]int
	firstNS   map[string]int64
	lastNS    map[string]int64
	maxLevel  map[string]int64
	limit     map[string]int64
}

func key(dev, cmd string) string { return dev + "\x00" + cmd }

// Open replays the log (if any) and opens it for appending.
func Open(path string, fsync bool) (*Plant, error) {
	p := &Plant{fsync: fsync, perCmd: map[string]int{}, perDevCmd: map[string]int{}, devValue: map[string]int64{},
		devCount: map[string]int{}, firstNS: map[string]int64{}, lastNS: map[string]int64{}, maxLevel: map[string]int64{}, limit: map[string]int64{}}
	if raw, err := os.Open(path); err == nil {
		sc := bufio.NewScanner(raw)
		sc.Buffer(make([]byte, 1<<20), 1<<20)
		line := 0
		for sc.Scan() {
			line++
			var r record
			if err := json.Unmarshal(sc.Bytes(), &r); err != nil {
				// A torn final line after a crash is tolerated; anything else is corruption.
				if !sc.Scan() {
					break
				}
				raw.Close()
				return nil, fmt.Errorf("plant log %s line %d corrupt: %v", path, line, err)
			}
			p.account(r)
		}
		raw.Close()
		if err := sc.Err(); err != nil {
			return nil, err
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return nil, err
	}
	p.f = f
	return p, nil
}

func (p *Plant) Close() error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.f == nil {
		return nil
	}
	err := p.f.Close()
	p.f = nil
	return err
}

func (p *Plant) account(r record) {
	if r.Seq > p.seq {
		p.seq = r.Seq
	}
	p.total++
	p.perCmd[r.Cmd]++
	p.perDevCmd[key(r.Device, r.Cmd)]++
	p.devValue[r.Device] += r.Value
	p.devCount[r.Device]++
	if _, ok := p.firstNS[r.Cmd]; !ok {
		p.firstNS[r.Cmd] = r.TimeNS
	}
	p.lastNS[r.Cmd] = r.TimeNS
	if p.devValue[r.Device] > p.maxLevel[r.Device] {
		p.maxLevel[r.Device] = p.devValue[r.Device]
	}
}

// SetLimit records a physical safety limit for a device; the summary reports
// any exceedance (the "bound violation" of the IoT dosing regime).
func (p *Plant) SetLimit(device string, limit int64) {
	p.mu.Lock()
	p.limit[device] = limit
	p.mu.Unlock()
}

func (p *Plant) Apply(r ApplyRequest) ApplyResult {
	if r.Op != "" && r.Op != "apply" {
		return ApplyResult{Error: "unsupported op " + r.Op}
	}
	if r.Device == "" || r.Cmd == "" {
		return ApplyResult{Error: "device and cmd required"}
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.f == nil {
		return ApplyResult{Error: "plant closed"}
	}
	k := key(r.Device, r.Cmd)
	if r.Once && p.perDevCmd[k] > 0 {
		return ApplyResult{OK: true, Effects: p.perCmd[r.Cmd], Total: p.total, Duplicate: true}
	}
	rec := record{Seq: p.seq + 1, TimeNS: time.Now().UnixNano(), Device: r.Device, Cmd: r.Cmd, Value: r.Value}
	b, _ := json.Marshal(rec)
	if _, err := p.f.Write(append(b, '\n')); err != nil {
		return ApplyResult{Error: err.Error()}
	}
	if p.fsync {
		if err := p.f.Sync(); err != nil {
			return ApplyResult{Error: err.Error()}
		}
	}
	p.account(rec)
	return ApplyResult{OK: true, Seq: rec.Seq, Effects: p.perCmd[r.Cmd], Total: p.total}
}

type Truth struct {
	Cmd       string `json:"cmd"`
	Device    string `json:"device,omitempty"`
	Effects   int    `json:"effects"`
	FirstNS   int64  `json:"first_effect_ns,omitempty"`
	LastNS    int64  `json:"last_effect_ns,omitempty"`
	PlantSeq  uint64 `json:"plant_seq"`
	TotalSeen int    `json:"total_effects"`
}

func (p *Plant) Truth(cmd, device string) Truth {
	p.mu.Lock()
	defer p.mu.Unlock()
	n := p.perCmd[cmd]
	if device != "" {
		n = p.perDevCmd[key(device, cmd)]
	}
	return Truth{Cmd: cmd, Device: device, Effects: n, FirstNS: p.firstNS[cmd], LastNS: p.lastNS[cmd], PlantSeq: p.seq, TotalSeen: p.total}
}

type DeviceSummary struct {
	Device     string `json:"device"`
	Effects    int    `json:"effects"`
	Level      int64  `json:"level"`
	MaxLevel   int64  `json:"max_level"`
	Limit      int64  `json:"limit,omitempty"`
	Violations bool   `json:"limit_exceeded"`
}

type Summary struct {
	Total      int             `json:"total_effects"`
	Seq        uint64          `json:"plant_seq"`
	Duplicates int             `json:"identities_with_duplicate_effects"`
	Devices    []DeviceSummary `json:"devices"`
}

func (p *Plant) Summary() Summary {
	p.mu.Lock()
	defer p.mu.Unlock()
	s := Summary{Total: p.total, Seq: p.seq}
	for _, n := range p.perDevCmd {
		if n > 1 {
			s.Duplicates++
		}
	}
	for d, n := range p.devCount {
		ds := DeviceSummary{Device: d, Effects: n, Level: p.devValue[d], MaxLevel: p.maxLevel[d], Limit: p.limit[d]}
		ds.Violations = ds.Limit > 0 && ds.MaxLevel > ds.Limit
		s.Devices = append(s.Devices, ds)
	}
	sort.Slice(s.Devices, func(i, j int) bool { return s.Devices[i].Device < s.Devices[j].Device })
	return s
}

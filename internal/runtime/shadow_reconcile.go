package runtime

// SHADOW_RECONCILE (release 086, PREREG_extra_086.md rules SH; reviewer comment M3 of 5 November 2026): a practice
// baseline that reproduces the documented desired/reported reconciliation loop of a device shadow. It is a policy of
// its own, off unless a campaign names it; no other policy changes.
//
// The documented rules it follows (quoted from the two pages the paper cites, as read on 5 November 2026):
//
//   AWS IoT Device Shadow (https://docs.aws.amazon.com/iot/latest/developerguide/iot-device-shadows.html):
//     "an app can request a change in a device's state by updating a shadow. AWS IoT publishes a message that
//      indicates the change to the device. The device receives this message, updates its state to match, and
//      publishes a message with its updated state." / "When the device reconnects, it receives the current state of
//      its shadows so that it can update its state to match that of its shadows, and then publish a message with its
//      updated state." / (using shadows in apps and services) "the app or service can send an HTTP POST request to the
//      URLs of the shadows it uses to update the desired state of the shadow. This request returns the change that
//      was accepted, but you might have to poll the shadow by making HTTP GET requests until the device has updated
//      the shadow with its new state." / (message order) "a device can safely discard any messages that contain a
//      version number older than the one it is tracking."
//   Azure IoT Hub device twins (https://learn.microsoft.com/en-us/azure/iot-hub/iot-hub-devguide-device-twins):
//     "Desired properties. Used along with reported properties to synchronize device configuration or conditions.
//      Back-end apps can set desired properties, and the device app can read them." / "The device app then reports
//      the updated configuration (or an error condition using the status property)." / "A back-end app tracks the
//      results of the configuration operation across many devices by querying device twins." / (device
//      reconnection flow) "a device that is connecting must retrieve the full desired properties document" ... "The
//      device app can ignore all notifications with $version less or equal than the version of the full retrieved
//      document."
//
// Neither page documents a timeout after which the back end gives up, nor a release: the desired state stays in the
// shadow (twin) until the device reports it. The loop here therefore:
//   - publishes each admitted command (the desired state) with its version: the command's identity and sequence; the
//     dispatcher sends it as for every policy (the campaign's retry guard applies to its own resends);
//   - on the reconciliation timeout ReleaseAfter after the command's creation, and every ReleaseAfter after that,
//     reads the owner's reported state for every unresolved command (the receipt read of the release baselines; on
//     the emulated owner with -shadow-reads the reported state is answered also while the device is offline, as a
//     shadow service answers for an offline device), oldest first, at most MaxReadsPerSweep per sweep;
//   - a reported APPLIED or STOPPED (the device's own report, the "error condition" being STOPPED) is recorded as
//     the owner's evidence;
//   - a command the reported state does not show (NOT_FOUND, or the read is not answered) is published again under
//     its own identity and version (the desired state stays and is delivered again; the owner deduplicates by
//     identity, as a device discards a version it has already applied), once per reconciliation, also after the
//     campaign's resend window (the shadow has no validity);
//   - an UNCERTAIN report (a claim without a receipt after an owner crash) is read again at the next timeout;
//   - nothing is ever committed STOPPED without the owner's report, and no query, stop or fence is sent on a guard
//     block (the documented loop has no resolver).
// Whether this can violate safety depends on the owner: a re-publish is a resend of the same identity, safe where the
// owner deduplicates every identity without a horizon (contract K2), unsafe where it does not (K0). The plan runs it
// on K2 only and preregisters absence there.

import (
	"errors"
	"sync"
	"time"

	"evicon/internal/journal"
	"evicon/internal/trace"
)

// Republisher holds the re-publish requests of the SHADOW_RECONCILE sweep and the time each command was last
// reconciled. The dispatcher's RetryAllowed consults it (RetryAllowedFunc).
type Republisher struct {
	mu    sync.Mutex
	want  map[string]bool
	last  map[string]time.Time
	total int
}

// NewRepublisher returns an empty Republisher.
func NewRepublisher() *Republisher {
	return &Republisher{want: map[string]bool{}, last: map[string]time.Time{}}
}

func repKey(domainID, commandID string) string { return domainID + "\x00" + commandID }

// Request asks for one more send of the command under its own identity.
func (r *Republisher) Request(domainID, commandID string) {
	r.mu.Lock()
	r.want[repKey(domainID, commandID)] = true
	r.total++
	r.mu.Unlock()
}

// Requested reports the number of re-publish requests so far.
func (r *Republisher) Requested() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.total
}

func (r *Republisher) take(domainID, commandID string) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	k := repKey(domainID, commandID)
	if r.want[k] {
		delete(r.want, k)
		return true
	}
	return false
}

// due reports whether the command's next reconciliation time (ReleaseAfter after its last one) has come.
func (r *Republisher) due(id string, now time.Time, every time.Duration) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	t, ok := r.last[id]
	return !ok || now.Sub(t) >= every
}

func (r *Republisher) reconciled(id string, t time.Time) {
	r.mu.Lock()
	r.last[id] = t
	r.mu.Unlock()
}

// RetryAllowedFunc wraps the dispatcher's retry guard base (nil: always): a requested re-publish is allowed once,
// whatever base says; every other resend is base's decision.
func (r *Republisher) RetryAllowedFunc(base func(domainID, commandID string, attempts int64) bool) func(domainID, commandID string, attempts int64) bool {
	return func(domainID, commandID string, attempts int64) bool {
		if r.take(domainID, commandID) {
			return true
		}
		return base == nil || base(domainID, commandID, attempts)
	}
}

// shadowReconcileOnce runs one sweep of SHADOW_RECONCILE and returns the number of commands it asked to publish again.
func (a *Authority) shadowReconcileOnce() (int, error) {
	rp := a.cfg.Republish
	if rp == nil {
		return 0, errors.New("SHADOW_RECONCILE without a Republisher")
	}
	a.mu.Lock()
	dcur, aged, err := a.agedUnresolved()
	a.mu.Unlock()
	if err != nil || len(aged) == 0 {
		return 0, err
	}
	now := time.Now()
	due := aged[:0]
	for _, c := range aged {
		if rp.due(c.CommandID, now, a.cfg.ReleaseAfter) {
			due = append(due, c)
		}
	}
	if len(due) > MaxReadsPerSweep {
		due = due[:MaxReadsPerSweep]
	}
	if len(due) == 0 {
		return 0, nil
	}
	reads := make([]ownerRead, len(due))
	var wg sync.WaitGroup
	sem := make(chan struct{}, 4)
	for i, c := range due {
		wg.Add(1)
		go func(i int, c journal.CommandRecord) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			reads[i] = a.readOwner(c, dcur.Dimension)
		}(i, c)
	}
	wg.Wait()
	a.mu.Lock()
	defer a.mu.Unlock()
	n := 0
	for _, r := range reads {
		rp.reconciled(r.cmd.CommandID, now)
		action := "WAIT"
		switch r.outcome {
		case "APPLIED", "STOPPED":
			action = "RECORDED"
			rev, er := a.cfg.Store.RecordEvidence(r.rc, r.reason, "SHADOW_REPORTED", true)
			if errors.Is(er, journal.ErrTerminalReversal) {
				a.noteEvidenceConflict(r.cmd.DomainID, r.cmd.CommandID, "SHADOW_REPORTED", r.cmd.Status, r.outcome, er.Error())
				action = "CONFLICT"
				break
			}
			if er != nil && !errors.Is(er, journal.ErrNotFound) {
				return n, er
			}
			if er == nil && a.cfg.Trace != nil {
				_ = a.cfg.Trace.Write(trace.Event{DomainID: r.cmd.DomainID, CommandID: r.cmd.CommandID, Event: "EVIDENCE_COMMITTED", Revision: rev,
					Outcome: r.outcome, Reason: "SHADOW_REPORTED:" + r.outcome})
			}
		case "NOT_FOUND", "UNREACHABLE":
			// the reported state does not show the desired version: publish it again (never release)
			action = "REPUBLISH"
			rp.Request(r.cmd.DomainID, r.cmd.CommandID)
			n++
		}
		if a.cfg.Trace != nil {
			_ = a.cfg.Trace.Write(trace.Event{DomainID: r.cmd.DomainID, CommandID: r.cmd.CommandID, Event: "SHADOW_RECONCILE_RESULT",
				Policy: string(a.cfg.Policy), Outcome: r.outcome, Reason: truncate("action="+action+";"+r.reason, 200)})
		}
	}
	if n > 0 && a.cfg.Dispatcher != nil {
		a.cfg.Dispatcher.Notify()
	}
	return n, nil
}

package outbox

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"evicon/internal/httpx"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"evicon/internal/fault"
	"evicon/internal/journal"
	"evicon/internal/protocol"
	"evicon/internal/trace"
)

// AttemptHeader (release 077) carries the dispatch attempt of a command request (1 = first send).
const AttemptHeader = "X-EviCon-Attempt"

type Dispatcher struct {
	Store        *journal.Store
	Client       *http.Client
	PollInterval time.Duration
	Trace        *trace.Writer
	Faults       *fault.Controller
	Wake         chan struct{}
	// RetryAllowed (release 061, optional): T4 guard. When it returns false
	// for a command that has already been attempted, the dispatcher does not
	// resend the same identity; the command stays ambiguous for backward
	// resolution. nil keeps the 060 behavior (always retry).
	RetryAllowed func(domainID, commandID string, attempts int64) bool
	// ExpiresAfter (release 061, optional): when > 0 every dispatch carries
	// expires_at_ms = command creation time + ExpiresAfter, so an owner with
	// capability T rejects late deliveries. Retries keep the same deadline.
	ExpiresAfter time.Duration
}

func New(store *journal.Store, poll time.Duration, tr *trace.Writer) *Dispatcher {
	if poll <= 0 {
		poll = 250 * time.Millisecond
	}
	return &Dispatcher{Store: store, Client: httpx.Client(2 * time.Second), PollInterval: poll, Trace: tr, Wake: make(chan struct{}, 1)}
}

func (d *Dispatcher) Notify() {
	select {
	case d.Wake <- struct{}{}:
	default:
	}
}

func (d *Dispatcher) Run(ctx context.Context) {
	t := time.NewTicker(d.PollInterval)
	defer t.Stop()
	// Recovery path: inspect durable outbox shortly after startup.
	_ = d.DispatchOnce(ctx)
	for {
		select {
		case <-ctx.Done():
			return
		case <-d.Wake:
			_ = d.DispatchOnce(ctx)
		case <-t.C:
			_ = d.DispatchOnce(ctx)
		}
	}
}

func (d *Dispatcher) DispatchOnce(ctx context.Context) error {
	items, err := d.Store.PendingOutbox(64)
	if err != nil {
		return err
	}
	for _, item := range items {
		if err := d.dispatchOne(ctx, item); err != nil {
			_ = d.Store.RecordDispatchFailure(item.DomainID, item.CommandID, err.Error())
		}
	}
	return nil
}

func (d *Dispatcher) dispatchOne(ctx context.Context, item journal.OutboxRecord) error {
	if d.RetryAllowed != nil && item.AttemptCount > 0 && !d.RetryAllowed(item.DomainID, item.CommandID, item.AttemptCount) {
		if d.Trace != nil {
			_ = d.Trace.Write(trace.Event{DomainID: item.DomainID, CommandID: item.CommandID, Event: "OUTBOX_RETRY_SUPPRESSED_T4"})
		}
		return nil
	}
	claimed, err := d.Store.ClaimDispatchAttempt(item.DomainID, item.CommandID)
	if err != nil {
		return err
	}
	if !claimed {
		// release 072: terminal evidence arrived after the outbox snapshot (query, stop,
		// fence); the command is never sent again
		if d.Trace != nil {
			_ = d.Trace.Write(trace.Event{DomainID: item.DomainID, CommandID: item.CommandID, Event: "OUTBOX_SKIPPED_TERMINAL"})
		}
		return nil
	}
	cmd, err := d.Store.GetCommand(item.DomainID, item.CommandID)
	if err != nil {
		return err
	}
	reqBody := protocol.CommandRequest{SchemaVersion: 1, DomainID: cmd.DomainID, Generation: cmd.Generation, CommandID: cmd.CommandID, Operation: cmd.Operation, Value: append([]int64(nil), cmd.Value...), Epoch: cmd.Epoch, Sequence: cmd.Sequence, IntentHash: cmd.IntentHash, TraceID: cmd.TraceID}
	if d.ExpiresAfter > 0 {
		created, err := d.Store.CommandCreatedTimes(cmd.DomainID)
		if err != nil {
			return err
		}
		if t0, ok := created[cmd.CommandID]; ok {
			reqBody.ExpiresAtMS = t0.Add(d.ExpiresAfter).UnixMilli()
		}
	}
	b, _ := json.Marshal(reqBody)
	url := strings.TrimRight(item.EndpointURL, "/") + "/v1/effects"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(b))
	if err != nil {
		return err
	}
	req.Header.Set("content-type", "application/json")
	// Release 077: the attempt number travels with the request (outside the body, so the intent hash
	// and every owner that ignores the header are unaffected); an owner that records it lets the
	// analysis tell an effect of the first send from an effect of a resend.
	attempt := item.AttemptCount + 1
	req.Header.Set(AttemptHeader, fmt.Sprint(attempt))
	if d.Trace != nil {
		_ = d.Trace.Write(trace.Event{DomainID: item.DomainID, CommandID: item.CommandID, Event: "OUTBOX_DISPATCH_ATTEMPT", Attempt: attempt})
	}
	resp, err := d.Client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode/100 != 2 {
		return fmt.Errorf("endpoint status %d: %s", resp.StatusCode, string(raw))
	}
	var rc protocol.Receipt
	if err = json.Unmarshal(raw, &rc); err != nil {
		return err
	}
	dom, err := d.Store.GetDomain(cmd.DomainID)
	if err != nil {
		return err
	}
	if err = protocol.ValidateReceipt(rc, cmd.DomainID, cmd.Generation, cmd.CommandID, cmd.IntentHash, dom.Dimension); err != nil {
		if d.Trace != nil {
			_ = d.Trace.Write(trace.Event{DomainID: item.DomainID, CommandID: item.CommandID, Event: "EVIDENCE_REJECTED", Reason: err.Error()})
		}
		return fmt.Errorf("invalid dispatch receipt: %w", err)
	}
	if d.Faults != nil {
		d.Faults.Hit("AFTER_ENDPOINT_REPLY_BEFORE_OBSERVATION", item.CommandID)
	}
	if _, err = d.Store.RecordEvidence(journal.ReceiptRecord{DomainID: rc.DomainID, CommandID: rc.CommandID, IntentHash: rc.IntentHash, Outcome: rc.Outcome, OwnerVersion: rc.OwnerVersion, ReceiptVersion: rc.ReceiptVersion, State: rc.State}, rc.Reason, "DISPATCH_REPLY", true); errors.Is(err, journal.ErrTerminalReversal) {
		// Release 068: the owner's reply contradicts the committed terminal outcome. The
		// committed outcome stays (terminal evidence is monotone), re-sending cannot resolve
		// the contradiction, so the item leaves the outbox and the conflict is recorded.
		if d.Trace != nil {
			_ = d.Trace.Write(trace.Event{DomainID: item.DomainID, CommandID: item.CommandID, Event: "EVIDENCE_CONFLICT", Outcome: rc.Outcome,
				Reason: "channel=DISPATCH_REPLY;committed=" + cmd.Status + ";owner=" + rc.Outcome + ";" + err.Error()})
		}
		return d.Store.MarkOutboxConflict(item.DomainID, item.CommandID, err.Error())
	} else if err != nil {
		return err
	}
	if d.Trace != nil {
		_ = d.Trace.Write(trace.Event{DomainID: item.DomainID, CommandID: item.CommandID, Event: "OUTBOX_RECEIPT_COMMITTED", Reason: rc.Outcome})
	}
	return nil
}

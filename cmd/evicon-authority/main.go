package main

import (
	"context"
	"encoding/json"
	"evicon/internal/httpx"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"evicon/internal/adapter"
	"evicon/internal/contract"
	"evicon/internal/domain"
	"evicon/internal/evidence"
	"evicon/internal/fault"
	"evicon/internal/journal"
	"evicon/internal/outbox"
	"evicon/internal/probe"
	"evicon/internal/protocol"
	"evicon/internal/resolve"
	eruntime "evicon/internal/runtime"
	"evicon/internal/trace"
)

func main() {
	listen := flag.String("listen", "127.0.0.1:18080", "authority API listen address")
	admin := flag.String("admin-listen", "", "optional test-only fault API listen address")
	db := flag.String("db", "./data/authority.db", "SQLite journal path")
	fromSnapshot := flag.Bool("belief-from-snapshot", false, "verification-only: replay runtime reducer from read-only DB, print JSON and exit")
	endpoint := flag.String("endpoint-url", "http://127.0.0.1:18181", "endpoint base URL")
	profile := flag.String("profile", "E3", "runtime profile: E1, E2, E3, LINUX_TC_E2_LIKE, or DEVICE (061 evicon-device; contract from the handshake or -evidence-contract-file)")
	domainID := flag.String("domain", "atlas-001", "domain ID")
	generation := flag.Uint64("generation", 1, "domain generation")
	epoch := flag.Uint64("epoch", 1, "installed epoch")
	anchor := flag.String("anchor", "0", "comma-separated anchor state")
	cmin := flag.String("component-min", "0", "component minima; empty disables")
	cmax := flag.String("component-max", "12", "component maxima; empty disables")
	summax := flag.String("sum-max", "", "optional sum max")
	contractHash := flag.String("contract-hash", "p3-policy-patch1-contract", "contract hash recorded in domain")
	poll := flag.Duration("dispatch-poll", 250*time.Millisecond, "durable outbox poll interval")
	maxCfg := flag.Int("max-configurations", 256, "online belief configuration cap")
	maxOutstanding := flag.Int("max-outstanding", 4, "tracked commands including candidate")
	policyName := flag.String("policy", "M0", "evidence policy: M0, M0-NO-PROBE, M1, M2, M3, M4, M5, M4P, M5V2, M5V2D1, EVICON (release 061), or RELEASE_ON_TIMEOUT, RELEASE_ON_READ (release 077: unsafe baselines), M1W (release 083: M1 with the window-full query trigger), RELEASE_ON_READ_ANSWERED (release 083: unsafe baseline, releases on an answered NOT_FOUND only)")
	// Release 061: EVICON evidence-contract resolution.
	evidenceContract := flag.String("evidence-contract", "", "EVICON runtime evidence contract alias: E1, E2, E2_STOP, E3, or DEVICE (taken from the device handshake) (default: -profile)")
	evidenceContractFile := flag.String("evidence-contract-file", "", "EVICON evidence contract JSON (RUNTIME_VERIFIED or MEASURED, e.g. from EviCon-Probe); capabilities must be advertised by the endpoint")
	expiresAfter := flag.Duration("expires-after", -1, "dispatch expires_at_ms = creation + this (owner-enforced T); negative = contract expiry_ms when the contract has T, else none")
	resolveMode := flag.String("resolve-mode", "SKI_RENTAL", "EVICON threshold: SKI_RENTAL, FIXED, IMMEDIATE, NEVER")
	resolveFixed := flag.Duration("resolve-fixed", 5*time.Second, "EVICON FIXED threshold")
	resolveQueryFirst := flag.Bool("resolve-query-first", true, "EVICON: query unqueried certificate targets before stop/fence")
	holdCost := flag.Float64("hold-cost", 1, "EVICON holding cost per reserved unit per second")
	eviconQueryCost := flag.Float64("evicon-query-cost", 0.01, "EVICON cost of one receipt query")
	stopCost := flag.Float64("stop-cost", 1, "EVICON cost of one per-identity stop")
	fenceCost := flag.Float64("fence-cost", 5, "EVICON cost of one owner fence")
	ambiguityAfter := flag.Duration("ambiguity-after", 2*time.Second, "EVICON: a command without terminal evidence this long after creation is ambiguous")
	certDepth := flag.Int("certificate-depth", 3, "EVICON continuation-certificate size bound")
	resolveSweep := flag.Duration("resolve-sweep", time.Second, "EVICON: background sweep interval over aged ambiguous reservations (0 disables)")
	resolveTrigger := flag.String("resolve-trigger", eruntime.TriggerBlockAndSweep, "EVICON: BLOCK_AND_SWEEP (resolve on a blocked candidate and in the sweep) or SWEEP_ONLY (periodic baseline)")
	retryGuard := flag.Bool("retry-guard", false, "release 061: suppress same-identity outbox retries past the T4 forward-retry deadline of the evidence contract")
	maxNetDelay := flag.Duration("max-network-delay", -1, "T4 assumed one-way delay bound; negative = unbounded")
	probeBudget := flag.Int("probe-budget", 2, "maximum receipt queries per logical candidate across retries")
	plannerDepth := flag.Int("planner-depth", 2, "M5 bounded-lookahead depth")
	plannerBudget := flag.Duration("planner-compute-budget", 5*time.Millisecond, "M5 planning compute budget per selection")
	queryConcurrency := flag.Int("query-concurrency", 2, "M1 maximum concurrent receipt queries")
	queryCostFile := flag.String("query-cost-file", "experiments/configs/query_costs.json", "frozen expected receipt-query cost file")
	outcomeModelFile := flag.String("outcome-model-file", "", "M4P/M5V2 frozen development-only outcome model JSON (required for experimental policies)")
	plannerCache := flag.Bool("planner-cache", true, "enable M5 cache scoped by revision/candidate/depth")
	skipCapabilityCheck := flag.Bool("skip-capability-check", false, "test-only: skip endpoint capability handshake")
	tracePath := flag.String("trace", "", "JSONL trace path")
	faultAck := flag.String("fault-ack", "", "fault acknowledgement JSONL path")
	runID := flag.String("run-id", "p3-local", "trace run ID")
	traceSeed := flag.Int64("trace-seed", 0, "nonnegative experiment seed recorded in the authority JSONL trace")
	ledgerName := flag.String("ledger-reconcile", "M1", "M0 reconciliation ranking: M1 (sequential ID), M2, M3, M4")
	resendUntil := flag.Duration("resend-until", 0, "release 077: with -retry-guard, resend a command under its own identity only until this long after its creation (0 = no validity cut, the rule of releases 072-076); to never resend after the intent's validity, the intent's validity minus the longest time a client may take to submit it")
	releaseAfter := flag.Duration("release-after", 10*time.Second, "release 077, policies RELEASE_ON_TIMEOUT and RELEASE_ON_READ only (unsafe baselines): a command unresolved this long after creation is committed STOPPED without owner evidence of non-occurrence (RELEASE_ON_READ first reads the owner's receipt)")
	releaseSettle := flag.Duration("release-settle", 0, "release 083, policy RELEASE_ON_READ_ANSWERED only: the one-way delay bound the baseline assumes; it re-sends a command only until release-after minus this after its creation and releases on an answered NOT_FOUND only when the read was sent at least this long after the command's last dispatch attempt (0 = release-after)")
	certificate := flag.String("certificate", eruntime.CertificateEnumerate, "release 083, EVICON: certificate search of a guard block: enumerate (bounded enumeration up to -certificate-depth, releases 061-082) or greedy (the greedy certificate of an additive domain, Section 3.6; other domains use the enumeration)")
	flag.Parse()
	if *traceSeed < 0 {
		log.Fatal("trace-seed must be nonnegative")
	}
	if *fromSnapshot {
		if err := printSnapshotBelief(*db, *domainID, *maxCfg); err != nil {
			log.Fatal(err)
		}
		return
	}
	p := strings.ToUpper(*profile)
	if p != "E1" && p != "E2" && p != "E3" && p != "LINUX_TC_E2_LIKE" && p != "DEVICE" {
		log.Fatalf("unsupported profile %q", *profile)
	}
	method, e := probe.ParseMethod(*policyName)
	if e != nil {
		log.Fatal(e)
	}
	costs, e := probe.LoadCostModel(*queryCostFile)
	if e != nil {
		log.Fatalf("query cost file: %v", e)
	}
	var outcomeModel probe.OutcomeModel
	if method == probe.M4P || method == probe.M5V2 || method == probe.M5V2D1 {
		outcomeModel, e = probe.LoadOutcomeModel(*outcomeModelFile)
		if e != nil {
			log.Fatalf("frozen outcome model: %v", e)
		}
		if outcomeModel.Spec.Profile != p {
			log.Fatal("outcome model profile mismatch")
		}
	}
	st := mustVec(*anchor)
	mins := optVec(*cmin)
	maxs := optVec(*cmax)
	if len(mins) > 0 && len(mins) != len(st) {
		log.Fatal("component-min dimension mismatch")
	}
	if len(maxs) > 0 && len(maxs) != len(st) {
		log.Fatal("component-max dimension mismatch")
	}
	var sm *int64
	if strings.TrimSpace(*summax) != "" {
		v, e := strconv.ParseInt(*summax, 10, 64)
		if e != nil {
			log.Fatal(e)
		}
		sm = &v
	}
	var advertised *protocol.EndpointCapabilities
	if !*skipCapabilityCheck {
		c, e := validateEndpoint(*endpoint, p, *domainID, *generation, *epoch, len(st), mins, maxs, sm)
		if e != nil {
			log.Fatalf("endpoint capability mismatch: %v", e)
		}
		advertised = &c
	}
	var ec contract.EvidenceContract
	if p == "DEVICE" || ((method == probe.EVICON || *retryGuard) && (*evidenceContractFile != "" || strings.EqualFold(*evidenceContract, "DEVICE"))) {
		ec, e = deviceOrFileContract(*evidenceContractFile, advertised)
		if e != nil {
			log.Fatal(e)
		}
		log.Printf("evidence contract %s source=%s level=%s capabilities=%v", ec.ID, ec.Source, ec.Level(), ec.Capabilities)
	} else if method == probe.EVICON || *retryGuard {
		alias := *evidenceContract
		if alias == "" {
			alias = p
		}
		if adapter.EndpointProfileFor(alias) != p {
			log.Fatalf("evidence contract %s runs on endpoint profile %s, not %s", alias, adapter.EndpointProfileFor(alias), p)
		}
		var ap contract.AdapterProfile
		ec, ap, e = adapter.RuntimeEvidenceContract(alias)
		if e != nil {
			log.Fatal(e)
		}
		if advertised != nil {
			if miss := adapter.MissingPremises(ap, advertised.Premises); len(miss) > 0 {
				log.Fatalf("endpoint does not advertise premises %v required by evidence contract %s (start evicon-endpoint with -per-identity-stop for E2_STOP)", miss, alias)
			}
		} else {
			log.Printf("WARNING: capability check skipped; evidence contract %s is trusted without handshake (test-only)", alias)
		}
		log.Printf("evidence contract %s level=%s capabilities=%v", ec.ID, ec.Level(), ec.Capabilities)
	}
	rp := resolve.Policy{Mode: resolve.ThresholdMode(strings.ToUpper(*resolveMode)), FixedMS: resolveFixed.Milliseconds(), QueryFirst: *resolveQueryFirst}
	if method == probe.EVICON {
		if e := rp.Validate(); e != nil {
			log.Fatal(e)
		}
	}
	store, e := journal.Open(*db)
	if e != nil {
		log.Fatal(e)
	}
	defer store.Close()
	fc := fault.New(*faultAck)
	tr := trace.New(*tracePath, "authority-"+strconv.Itoa(os.Getpid()), *runID, string(method), *traceSeed)
	disp := outbox.New(store, *poll, tr)
	disp.Faults = fc
	if *retryGuard {
		disp.RetryAllowed = eruntime.RetryAllowedUntilFunc(store, ec, *maxNetDelay, *resendUntil)
		if *resendUntil > 0 {
			log.Printf("T4 retry guard: same-identity resends stop %s after a command's creation (intent validity)", *resendUntil)
		}
	}
	if method == probe.ReleaseOnReadAnswered {
		settle := *releaseSettle
		if settle <= 0 {
			settle = *releaseAfter
		}
		disp.RetryAllowed = eruntime.RetryCutFunc(store, disp.RetryAllowed, *releaseAfter-settle)
		log.Printf("%s: same-identity resends stop %s after a command's creation (release-after %s - settle %s)", method, *releaseAfter-settle, *releaseAfter, settle)
	}
	switch {
	case *expiresAfter >= 0:
		disp.ExpiresAfter = *expiresAfter
	case ec.Has(contract.CapExpiry):
		disp.ExpiresAfter = time.Duration(ec.ExpiryMS) * time.Millisecond
	}
	if disp.ExpiresAfter > 0 {
		log.Printf("dispatch carries expires_at_ms = created + %s", disp.ExpiresAfter)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	auth, e := eruntime.NewAuthority(eruntime.AuthorityConfig{Store: store, Domain: journal.Domain{ID: *domainID, Generation: *generation, InstalledEpoch: *epoch, Revision: 0, Dimension: len(st), ComponentMin: mins, ComponentMax: maxs, SumMax: sm, Anchor: st, CutoffSequence: 0, ContractHash: *contractHash}, EndpointURL: *endpoint, Profile: p, Dispatcher: disp, Faults: fc, Trace: tr, MaxConfigurations: *maxCfg, MaxOutstanding: *maxOutstanding, Policy: method, LedgerReconcile: probe.Method(strings.ToUpper(*ledgerName)), QueryCosts: costs, OutcomeModel: outcomeModel, ProbeBudget: *probeBudget, PlannerDepth: *plannerDepth, PlannerComputeBudget: *plannerBudget, QueryConcurrency: *queryConcurrency, PlannerCache: *plannerCache,
		EvidenceContract: ec, ResolvePolicy: rp, ResolveCosts: resolve.Costs{HoldPerUnitPerSec: *holdCost, QueryCost: *eviconQueryCost, StopCost: *stopCost, FenceCost: *fenceCost},
		AmbiguityAfter: *ambiguityAfter, CertificateDepth: *certDepth, ResolveTrigger: strings.ToUpper(*resolveTrigger), ReleaseAfter: *releaseAfter,
		ReleaseSettle: *releaseSettle, Certificate: strings.ToLower(strings.TrimSpace(*certificate))})
	if e != nil {
		log.Fatal(e)
	}
	dispatchDone := make(chan struct{})
	go func() {
		defer close(dispatchDone)
		disp.Run(ctx)
	}()
	if method == probe.EVICON && *resolveSweep > 0 {
		go auth.RunSweep(ctx, *resolveSweep)
		log.Printf("EVICON sweep every %s (trigger %s)", *resolveSweep, strings.ToUpper(*resolveTrigger))
	}
	if probe.IsReleaseBaseline(method) {
		iv := *resolveSweep
		if iv <= 0 {
			iv = time.Second
		}
		go auth.RunReleaseSweep(ctx, iv)
		log.Printf("WARNING: %s baseline (unsafe by design): commands unresolved %s after creation are committed STOPPED without owner evidence of non-occurrence (sweep every %s)", method, *releaseAfter, iv)
		if method == probe.ReleaseOnReadAnswered {
			log.Printf("%s: only an answered NOT_FOUND releases a command; an unanswered or UNCERTAIN read is read again", method)
		}
	}
	srv := &http.Server{Addr: *listen, Handler: auth.Handler(), ReadHeaderTimeout: 2 * time.Second}
	if *admin != "" {
		go func() {
			as := &http.Server{Addr: *admin, Handler: fc.Handler(), ReadHeaderTimeout: 2 * time.Second}
			if e := as.ListenAndServe(); e != nil && e != http.ErrServerClosed {
				log.Printf("admin server: %v", e)
			}
		}()
	}
	go func() {
		log.Printf("evicon-authority %s policy=%s listening on %s; sqlite=%s cost_model=%s", p, method, *listen, store.SQLiteVersion(), costs.Hash)
		if e := srv.ListenAndServe(); e != nil && e != http.ErrServerClosed {
			log.Fatalf("server: %v", e)
		}
	}()
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig
	cancel()
	_ = srv.Close()
	// SIGTERM is a graceful shutdown path. Wait for an in-flight outbox
	// dispatch to observe cancellation and finish recording its attempt trace
	// before process exit closes the journal/trace resources. This keeps the
	// durable attempt counter and audit trace aligned on controlled shutdown.
	select {
	case <-dispatchDone:
	case <-time.After(3 * time.Second):
		log.Printf("dispatcher shutdown timeout")
	}
}

func printSnapshotBelief(path, domainID string, cap int) error {
	s, err := journal.OpenReadOnly(path)
	if err != nil {
		return err
	}
	defer s.Close()
	d, err := s.GetDomain(domainID)
	if err != nil {
		return err
	}
	commands, err := s.ListCommandsForBelief(d.ID, d.CutoffSequence)
	if err != nil {
		return err
	}
	b, stats, buildErr := evidence.BuildBeliefWithStats(d, commands, cap)
	out := map[string]any{"schema_version": 2, "status": "OK", "http_status": 200, "measurement_mode": "RUNTIME_REDUCER_REPLAY_AT_OBSERVATION_CUT",
		"domain_id": d.ID, "generation": d.Generation, "dimension": d.Dimension, "domain_revision": d.Revision, "cutoff_sequence": d.CutoffSequence,
		"enumeration_algorithm": stats.Algorithm, "enumeration_limit": stats.Limit, "explored_configurations": stats.ExploredConfigurations,
		"enumeration_complete": stats.Complete, "configuration_count": stats.ConfigurationCount, "commands": b.Registry, "configurations": b.Configurations}
	if buildErr != nil {
		out["status"], out["http_status"], out["error_kind"], out["error"] = "INCOMPLETE", 503, "BELIEF_BUILD_ERROR", buildErr.Error()
	}
	return json.NewEncoder(os.Stdout).Encode(out)
}
func sameVec(a, b []int64) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
func sameInt64Ptr(a, b *int64) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	return *a == *b
}
func validateEndpoint(base, profile, domainID string, generation, epoch uint64, dim int, mins, maxs []int64, sum *int64) (protocol.EndpointCapabilities, error) {
	var c protocol.EndpointCapabilities
	err := checkEndpoint(base, profile, domainID, generation, epoch, dim, mins, maxs, sum, &c)
	return c, err
}

func checkEndpoint(base, profile, domainID string, generation, epoch uint64, dim int, mins, maxs []int64, sum *int64, out *protocol.EndpointCapabilities) error {
	client := httpx.Client(2 * time.Second)
	resp, err := client.Get(strings.TrimRight(base, "/") + "/v1/capabilities")
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		return fmt.Errorf("capability status %d", resp.StatusCode)
	}
	var c protocol.EndpointCapabilities
	if err = json.NewDecoder(resp.Body).Decode(&c); err != nil {
		return err
	}
	if c.SchemaVersion != protocol.SchemaVersion {
		return fmt.Errorf("schema=%d", c.SchemaVersion)
	}
	if c.Profile != profile {
		return fmt.Errorf("profile=%s want %s", c.Profile, profile)
	}
	if c.DomainID != domainID || c.Generation != generation || c.InstalledEpoch != epoch {
		return fmt.Errorf("domain/generation/epoch mismatch")
	}
	if c.Dimension != dim {
		return fmt.Errorf("dimension=%d want %d", c.Dimension, dim)
	}
	// A DEVICE's own limit is optional defence in depth; the authority's bound
	// is the plant safety limit, so bounds are not required to match.
	if profile != "DEVICE" && (!sameVec(c.ComponentMin, mins) || !sameVec(c.ComponentMax, maxs) || !sameInt64Ptr(c.SumMax, sum)) {
		return fmt.Errorf("bound mismatch")
	}
	haveAdd, haveSet := false, false
	for _, op := range c.SupportedOperations {
		if op == domain.OpAdd {
			haveAdd = true
		}
		if op == domain.OpSet {
			haveSet = true
		}
	}
	if profile == "LINUX_TC_E2_LIKE" || profile == "DEVICE" {
		if !haveAdd {
			return fmt.Errorf("Linux tc endpoint must support ADD")
		}
	} else if !haveAdd || !haveSet {
		return fmt.Errorf("endpoint operation set incomplete")
	}
	if c.AdapterVersion == "" {
		return fmt.Errorf("adapter_version missing")
	}
	*out = c
	return nil
}

func mustVec(s string) []int64 {
	v := optVec(s)
	if len(v) == 0 {
		log.Fatal("state vector required")
	}
	return v
}
func optVec(s string) []int64 {
	if strings.TrimSpace(s) == "" {
		return nil
	}
	parts := strings.Split(s, ",")
	out := make([]int64, len(parts))
	for i, p := range parts {
		v, e := strconv.ParseInt(strings.TrimSpace(p), 10, 64)
		if e != nil {
			log.Fatalf("bad vector %q: %v", s, e)
		}
		out[i] = v
	}
	return out
}

// deviceOrFileContract returns the EVICON contract for a DEVICE endpoint or an
// explicit contract file. Only evidence-backed sources are accepted, and every
// capability must be advertised by the endpoint handshake (a stale file cannot
// grant capabilities the owner no longer offers).
func deviceOrFileContract(file string, advertised *protocol.EndpointCapabilities) (contract.EvidenceContract, error) {
	var c contract.EvidenceContract
	var raw []byte
	var err error
	switch {
	case file != "":
		raw, err = os.ReadFile(file)
		if err != nil {
			return c, err
		}
	case advertised != nil && len(advertised.EvidenceContract) > 0:
		raw = advertised.EvidenceContract
	default:
		return c, fmt.Errorf("no evidence contract: pass -evidence-contract-file or keep the capability handshake enabled for DEVICE endpoints")
	}
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&c); err != nil {
		return c, fmt.Errorf("evidence contract: %w", err)
	}
	if err := c.Validate(); err != nil {
		return c, err
	}
	if c.Source != contract.SourceRuntimeVerified && c.Source != contract.SourceMeasured {
		return c, fmt.Errorf("evidence contract %s has source %s; EVICON needs RUNTIME_VERIFIED or MEASURED", c.ID, c.Source)
	}
	if len(c.DeclaredUnverified) > 0 {
		log.Printf("contract %s: declared but unverified (not used): %v", c.ID, c.DeclaredUnverified)
	}
	if advertised == nil {
		log.Printf("WARNING: capability check skipped; contract %s trusted without handshake (test-only)", c.ID)
		return c, nil
	}
	if len(advertised.EvidenceContract) > 0 {
		var adv contract.EvidenceContract
		if err := json.Unmarshal(advertised.EvidenceContract, &adv); err != nil {
			return c, err
		}
		// The handshake does not carry a fault model; compare capabilities,
		// horizon, expiry and skew, and keep the file's fault model in the log.
		cmp := c
		cmp.FaultModel = ""
		if !adv.Includes(cmp) {
			return c, fmt.Errorf("endpoint-advertised contract %v does not include %s %v", adv.Capabilities, c.ID, c.Capabilities)
		}
	} else if file != "" && c.Source == contract.SourceRuntimeVerified {
		return c, fmt.Errorf("RUNTIME_VERIFIED contract file but the endpoint advertises no contract")
	}
	return c, nil
}

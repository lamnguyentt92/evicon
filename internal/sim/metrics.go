package sim

import (
	"encoding/csv"
	"fmt"
	"io"
	"math"
	"math/rand"
	"reflect"
	"sort"
	"strconv"
)

// Row is one (regime, episode, policy) simulation outcome. SIMULATION ONLY.
type Row struct {
	Regime                  string `json:"regime"`
	Policy                  string `json:"policy"`
	Episode                 int    `json:"episode"`
	Contract                string `json:"contract"`
	Level                   string `json:"level"`
	Offered                 int    `json:"offered"`
	CleanFulfilled          int    `json:"clean_fulfilled"`           // exactly one effect, before deadline and validity
	CleanFulfilledConfirmed int    `json:"clean_fulfilled_confirmed"` // ... and confirmed to the authority before the deadline
	FulfilledLate           int    `json:"fulfilled_late"`            // exactly one effect, but after deadline or validity
	Admitted                int    `json:"admitted"`
	Decisions               int    `json:"decisions"`
	BlockedDecisions        int    `json:"blocked_decisions"`
	DeferredDecisions       int    `json:"deferred_decisions"`
	DeferredOutstanding     int    `json:"deferred_outstanding"`
	DeferredBeliefCap       int    `json:"deferred_belief_cap"`
	AdmittedAfterBlock      int    `json:"admitted_after_block"`
	SelectorActivations     int    `json:"selector_activations"`
	Ambiguous               int    `json:"ambiguous_attempts"`
	Queries                 int    `json:"queries"`
	Stops                   int    `json:"stops"`
	Fences                  int    `json:"fences"`
	FenceRetries            int    `json:"fence_retries"`
	ValidityActions         int    `json:"validity_actions"`
	FencePauseUS            int64  `json:"fence_pause_us"`
	Retries                 int    `json:"retries"`
	Reissues                int    `json:"reissues"`
	Escalations             int    `json:"escalations"`
	ManualResolutions       int    `json:"manual_resolutions"`
	Cancellations           int    `json:"cancellations"`
	UnsafeReleases          int    `json:"unsafe_releases"`
	LostRequests            int    `json:"lost_requests"`
	LostReplies             int    `json:"lost_replies"`
	BrokerExpired           int    `json:"broker_expired"`
	OwnerCrashes            int    `json:"owner_crashes"`
	UnresolvedAtEnd         int    `json:"unresolved_at_end"`
	// stranded capacity-time (unit x s) of reservations that never took effect:
	StrandedResolvedUnitSec  float64 `json:"stranded_resolved_unit_sec"`  // admission -> terminal evidence
	StrandedCensoredUnitSec  float64 `json:"stranded_censored_unit_sec"`  // admission -> end, still unresolved (censored)
	StrandedAmbiguousUnitSec float64 `json:"stranded_ambiguous_unit_sec"` // part of both after ambiguity onset
	BoundViolations          int     `json:"bound_violations"`
	DuplicateEffects         int     `json:"duplicate_effects"`
	DuplicatedRequests       int     `json:"duplicated_requests"`
	LateEffects              int     `json:"late_effects"` // validity violations (effect after the validity window)
	FalseStopped             int     `json:"false_stopped"`
	SafetyViolations         int     `json:"safety_violations"` // bound + duplicate + false_stopped (late effects excluded)
	ModelInconsistency       int     `json:"model_inconsistency"`
	CertificateHits          int     `json:"certificate_hits"`
	CertificateErrors        int     `json:"certificate_errors"`
	SelectorErrors           int     `json:"selector_errors"`
	LateRepliesDropped       int     `json:"late_replies_dropped"` // network.late_replies = drop (release 066)
	FenceAborts              int     `json:"fence_aborts"`         // authority.fence_uncertain = abort (release 066)
}

func rowFields() []reflect.StructField {
	t := reflect.TypeOf(Row{})
	out := make([]reflect.StructField, t.NumField())
	for i := range out {
		out[i] = t.Field(i)
	}
	return out
}

func WriteCSV(w io.Writer, rows []Row) error {
	cw := csv.NewWriter(w)
	fs := rowFields()
	hdr := make([]string, len(fs))
	for i, f := range fs {
		hdr[i] = f.Tag.Get("json")
	}
	if err := cw.Write(hdr); err != nil {
		return err
	}
	for _, r := range rows {
		v := reflect.ValueOf(r)
		rec := make([]string, len(fs))
		for i := range fs {
			x := v.Field(i)
			switch x.Kind() {
			case reflect.String:
				rec[i] = x.String()
			case reflect.Int, reflect.Int64:
				rec[i] = strconv.FormatInt(x.Int(), 10)
			case reflect.Float64:
				rec[i] = strconv.FormatFloat(x.Float(), 'f', 6, 64)
			default:
				rec[i] = fmt.Sprint(x.Interface())
			}
		}
		if err := cw.Write(rec); err != nil {
			return err
		}
	}
	cw.Flush()
	return cw.Error()
}

// Summary aggregates rows per (regime, policy) and reports paired differences
// against a reference policy with a percentile bootstrap over episodes.
type Summary struct {
	Regime          string             `json:"regime"`
	Policy          string             `json:"policy"`
	Contract        string             `json:"contract"`
	Level           string             `json:"level"`
	Episodes        int                `json:"episodes"`
	Means           map[string]float64 `json:"means"`
	FulfillmentRate float64            `json:"clean_fulfillment_rate"`
	// GapToFullInfo = 1 - U/U_ref with U = clean useful work and ref = ORACLE, a
	// full-information REFERENCE policy (not an offline optimum). It is signed:
	// a negative value means the policy beat the reference heuristic.
	GapToFullInfo    *float64          `json:"gap_to_full_info_reference,omitempty"`
	Paired           map[string]Paired `json:"paired,omitempty"`
	TotalSafety      int               `json:"total_safety_violations"`
	EpisodesUnsafe   int               `json:"episodes_with_safety_violations"`
	TotalLateEffects int               `json:"total_late_effects"`
}

type Paired struct {
	Reference string  `json:"reference"`
	Metric    string  `json:"metric"`
	MeanDiff  float64 `json:"mean_diff"`
	CI95Low   float64 `json:"ci95_low"`
	CI95High  float64 `json:"ci95_high"`
	N         int     `json:"n"`
}

var summaryMetrics = []string{"clean_fulfilled", "clean_fulfilled_confirmed", "fulfilled_late", "stranded_resolved_unit_sec",
	"stranded_censored_unit_sec", "stranded_ambiguous_unit_sec", "queries", "stops", "fences", "fence_pause_us", "retries", "reissues",
	"escalations", "cancellations", "safety_violations", "late_effects", "false_stopped", "admitted_after_block",
	"selector_activations", "blocked_decisions", "unresolved_at_end"}

func metricValue(r Row, name string) float64 {
	v := reflect.ValueOf(r)
	for i, f := range rowFields() {
		if f.Tag.Get("json") == name {
			x := v.Field(i)
			switch x.Kind() {
			case reflect.Int, reflect.Int64:
				return float64(x.Int())
			case reflect.Float64:
				return x.Float()
			}
		}
	}
	return math.NaN()
}

func Summarize(rows []Row, references []string, bootstrap int, seed int64) []Summary {
	type key struct{ regime, policy string }
	groups := map[key][]Row{}
	order := []key{}
	for _, r := range rows {
		k := key{r.Regime, r.Policy}
		if _, ok := groups[k]; !ok {
			order = append(order, k)
		}
		groups[k] = append(groups[k], r)
	}
	sort.SliceStable(order, func(i, j int) bool {
		if order[i].regime != order[j].regime {
			return order[i].regime < order[j].regime
		}
		return order[i].policy < order[j].policy
	})
	byEp := func(regime, policy string) map[int]Row {
		m := map[int]Row{}
		for _, r := range groups[key{regime, policy}] {
			m[r.Episode] = r
		}
		return m
	}
	out := []Summary{}
	for _, k := range order {
		g := groups[k]
		s := Summary{Regime: k.regime, Policy: k.policy, Contract: g[0].Contract, Level: g[0].Level, Episodes: len(g), Means: map[string]float64{}, Paired: map[string]Paired{}}
		offered, fulfilled := 0.0, 0.0
		for _, r := range g {
			offered += float64(r.Offered)
			fulfilled += float64(r.CleanFulfilled)
			s.TotalSafety += r.SafetyViolations
			s.TotalLateEffects += r.LateEffects
			if r.SafetyViolations > 0 {
				s.EpisodesUnsafe++
			}
		}
		for _, m := range summaryMetrics {
			t := 0.0
			for _, r := range g {
				t += metricValue(r, m)
			}
			s.Means[m] = t / float64(len(g))
		}
		if offered > 0 {
			s.FulfillmentRate = fulfilled / offered
		}
		if or, ok := groups[key{k.regime, PolicyOracle}]; ok && k.policy != PolicyOracle {
			of := 0.0
			for _, r := range or {
				of += float64(r.CleanFulfilled)
			}
			if of > 0 {
				gap := 1 - fulfilled/of
				s.GapToFullInfo = &gap
			}
		}
		mine := byEp(k.regime, k.policy)
		for _, ref := range references {
			if ref == k.policy {
				continue
			}
			other := byEp(k.regime, ref)
			if len(other) == 0 {
				continue
			}
			for _, m := range []string{"clean_fulfilled", "stranded_resolved_unit_sec", "stranded_censored_unit_sec", "safety_violations", "late_effects"} {
				diffs := []float64{}
				eps := make([]int, 0, len(mine))
				for ep := range mine {
					eps = append(eps, ep)
				}
				sort.Ints(eps)
				for _, ep := range eps {
					if o, ok := other[ep]; ok {
						diffs = append(diffs, metricValue(mine[ep], m)-metricValue(o, m))
					}
				}
				if len(diffs) == 0 {
					continue
				}
				lo, hi := bootstrapCI(diffs, bootstrap, seed)
				s.Paired[ref+":"+m] = Paired{Reference: ref, Metric: m, MeanDiff: mean(diffs), CI95Low: lo, CI95High: hi, N: len(diffs)}
			}
		}
		out = append(out, s)
	}
	return out
}

func mean(x []float64) float64 {
	if len(x) == 0 {
		return math.NaN()
	}
	t := 0.0
	for _, v := range x {
		t += v
	}
	return t / float64(len(x))
}

func bootstrapCI(x []float64, n int, seed int64) (float64, float64) {
	if n <= 0 || len(x) < 2 {
		m := mean(x)
		return m, m
	}
	rnd := rand.New(rand.NewSource(seed))
	ms := make([]float64, n)
	for b := 0; b < n; b++ {
		t := 0.0
		for i := 0; i < len(x); i++ {
			t += x[rnd.Intn(len(x))]
		}
		ms[b] = t / float64(len(x))
	}
	sort.Float64s(ms)
	return ms[int(0.025*float64(n))], ms[int(math.Min(float64(n-1), 0.975*float64(n)))]
}

package probe

// OutcomeModel is an immutable, offline-fitted G1 *ranking* model. It cannot
// establish a terminal receipt, modify the belief, or authorize admission.
// The model deliberately excludes command ID, run ID, seeds and truth state.
import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"strings"

	"evicon/internal/journal"
)

type OutcomePrior struct {
	Applied   float64 `json:"applied"`
	Stopped   float64 `json:"stopped"`
	Uncertain float64 `json:"uncertain"`
	Timeout   float64 `json:"timeout"`
	LatencyUS int64   `json:"latency_us"`
	Samples   int     `json:"samples"`
}

type OutcomeFile struct {
	SchemaVersion    int                     `json:"schema_version"`
	Profile          string                  `json:"profile"`
	BucketThreshold  int64                   `json:"bucket_threshold"`
	DeadlineUS       int64                   `json:"deadline_us"`
	CostPenaltyPerUS float64                 `json:"cost_penalty_per_us"`
	MinimumSamples   int                     `json:"minimum_samples"`
	Rows             map[string]OutcomePrior `json:"rows"`
	// The following are metadata only, never inference features.
	TrainingSetSHA256 string `json:"training_set_sha256"`
	Split             string `json:"split"`
}

type OutcomeModel struct {
	Spec OutcomeFile
	Hash string
}

func LoadOutcomeModel(path string) (OutcomeModel, error) {
	if strings.TrimSpace(path) == "" {
		return OutcomeModel{}, fmt.Errorf("G1 outcome model path required; do not fit on final run")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return OutcomeModel{}, err
	}
	var v OutcomeFile
	if err = json.Unmarshal(data, &v); err != nil {
		return OutcomeModel{}, err
	}
	if v.SchemaVersion != 1 {
		return OutcomeModel{}, fmt.Errorf("outcome schema_version must be 1")
	}
	if v.Profile != "E2" && v.Profile != "E3" {
		return OutcomeModel{}, fmt.Errorf("G1 model requires E2 or E3 profile")
	}
	if v.BucketThreshold <= 0 || v.DeadlineUS <= 0 || v.MinimumSamples < 1 || len(v.Rows) == 0 || math.IsNaN(v.CostPenaltyPerUS) || math.IsInf(v.CostPenaltyPerUS, 0) || v.CostPenaltyPerUS < 0 {
		return OutcomeModel{}, fmt.Errorf("invalid G1 bucket/deadline/penalty/sample settings")
	}
	if v.Split != "development" {
		return OutcomeModel{}, fmt.Errorf("G1 model must identify development-only split")
	}
	if len(v.TrainingSetSHA256) != 64 {
		return OutcomeModel{}, fmt.Errorf("training_set_sha256 required to audit provenance")
	}
	for _, c := range v.TrainingSetSHA256 {
		if !strings.ContainsRune("0123456789abcdef", c) {
			return OutcomeModel{}, fmt.Errorf("training_set_sha256 must be lowercase hex")
		}
	}
	for k, x := range v.Rows {
		switch k {
		case "ADD:SMALL", "ADD:LARGE", "SET:SMALL", "SET:LARGE":
		default:
			return OutcomeModel{}, fmt.Errorf("unsupported observable bucket %s", k)
		}
		probs := []float64{x.Applied, x.Stopped, x.Uncertain, x.Timeout}
		sum := 0.0
		for _, a := range probs {
			if math.IsNaN(a) || math.IsInf(a, 0) || a < 0 || a > 1 {
				return OutcomeModel{}, fmt.Errorf("invalid probability in %s", k)
			}
			sum += a
		}
		if math.Abs(sum-1) > 1e-8 || x.LatencyUS < 1 || x.Samples < v.MinimumSamples {
			return OutcomeModel{}, fmt.Errorf("invalid probabilities/latency/samples in %s", k)
		}
	}
	h := sha256.Sum256(data)
	return OutcomeModel{Spec: v, Hash: hex.EncodeToString(h[:])}, nil
}

// Prior uses only observable operation and magnitude; no per-command lookup.
func (m OutcomeModel) Prior(c journal.CommandRecord) (OutcomePrior, bool) {
	size := obligationMagnitude(c)
	b := "SMALL"
	if size > m.Spec.BucketThreshold {
		b = "LARGE"
	}
	p, ok := m.Spec.Rows[string(c.Operation)+":"+b]
	return p, ok
}

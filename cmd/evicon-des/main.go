// evicon-des runs the EviCon v2 discrete-event simulator.
//
// Usage:
//
//	evicon-des -config experiments/sim/replay059.json -out results/sim/replay059
//
// Output (per config, expanded over its sweep):
//
//	episodes.csv  one row per (regime, episode, policy)
//	summary.json  per (regime, policy) means, PoA vs ORACLE, paired bootstrap CIs
//	manifest.json config hash, git-free source identity, seed, episodes
//
// Every value is a SIMULATION result (scope=SIMULATION_ONLY). It is never a
// measured result and must not be copied into the paper's measured tables.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"evicon/internal/buildinfo"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"evicon/internal/sim"
)

func main() {
	cfgPath := flag.String("config", "", "simulator config JSON")
	out := flag.String("out", "", "output directory")
	catalog := flag.String("catalog", "", "evidence contract catalog dir (default: <repo>/experiments/contracts)")
	episodes := flag.Int("episodes", 0, "override episodes")
	workers := flag.Int("workers", 0, "parallel workers (default NumCPU)")
	refs := flag.String("references", "B2_LEDGER,M1,ORACLE", "comma-separated reference policies for paired differences")
	bootstrap := flag.Int("bootstrap", 2000, "bootstrap resamples for paired CIs")
	only := flag.String("only", "", "run only sweep entries whose regime contains this substring")
	flag.Parse()
	if *cfgPath == "" || *out == "" {
		fmt.Fprintln(os.Stderr, "-config and -out are required")
		os.Exit(2)
	}
	raw, err := os.ReadFile(*cfgPath)
	if err != nil {
		fatal(err)
	}
	cfg, err := sim.ParseConfig(raw)
	if err != nil {
		fatal(err)
	}
	if *episodes > 0 {
		cfg.Episodes = *episodes
	}
	cat := *catalog
	if cat == "" {
		cat = sim.DefaultCatalogDir(repoRoot(*cfgPath))
	}
	cfgs, err := cfg.Expand()
	if err != nil {
		fatal(err)
	}
	if err := os.MkdirAll(*out, 0o755); err != nil {
		fatal(err)
	}
	start := time.Now()
	var all []sim.Row
	for _, c := range cfgs {
		if *only != "" && !strings.Contains(c.Regime, *only) {
			continue
		}
		if *episodes > 0 {
			c.Episodes = *episodes
		}
		t0 := time.Now()
		rows, err := sim.Run(c, cat, *workers)
		if err != nil {
			fatal(fmt.Errorf("%s: %w", c.Regime, err))
		}
		fmt.Fprintf(os.Stderr, "[evicon-des] %-40s episodes=%d policies=%d rows=%d %.1fs\n", c.Regime, c.Episodes, len(c.Policies), len(rows), time.Since(t0).Seconds())
		all = append(all, rows...)
	}
	f, err := os.Create(filepath.Join(*out, "episodes.csv"))
	if err != nil {
		fatal(err)
	}
	if err := sim.WriteCSV(f, all); err != nil {
		fatal(err)
	}
	if err := f.Close(); err != nil {
		fatal(err)
	}
	summ := sim.Summarize(all, strings.Split(*refs, ","), *bootstrap, cfg.Seed)
	writeJSON(filepath.Join(*out, "summary.json"), map[string]any{"scope": "SIMULATION_ONLY", "paper_result": false, "summaries": summ})
	h := sha256.Sum256(raw)
	writeJSON(filepath.Join(*out, "manifest.json"), map[string]any{
		"scope": "SIMULATION_ONLY", "paper_result": false, "tool": "evicon-des", "release": buildinfo.Release,
		"config": *cfgPath, "config_sha256": hex.EncodeToString(h[:]), "catalog": cat, "seed": cfg.Seed,
		"episodes": cfg.Episodes, "regimes": len(cfgs), "rows": len(all), "go": runtime.Version(),
		"elapsed_s": time.Since(start).Seconds(), "created_utc": time.Now().UTC().Format(time.RFC3339)})
	printTable(summ)
}

func printTable(s []sim.Summary) {
	fmt.Printf("%-34s %-16s %-14s %-5s %8s %8s %10s %10s %7s %6s %6s %5s %5s %7s\n", "regime", "policy", "contract", "level", "clean%", "conf%",
		"strand_res", "strand_cens", "queries", "stops", "fences", "safe!", "late", "gapRef%")
	for _, x := range s {
		gap := ""
		if x.GapToFullInfo != nil {
			gap = fmt.Sprintf("%.1f", 100**x.GapToFullInfo)
		}
		off := x.Means["clean_fulfilled"] / maxf(x.FulfillmentRate, 1e-12)
		conf := 0.0
		if off > 0 {
			conf = x.Means["clean_fulfilled_confirmed"] / off
		}
		fmt.Printf("%-34s %-16s %-14s %-5s %8.2f %8.2f %10.2f %10.2f %7.2f %6.2f %6.2f %5d %5d %7s\n", trunc(x.Regime, 34), x.Policy, trunc(x.Contract, 14), x.Level,
			100*x.FulfillmentRate, 100*conf, x.Means["stranded_resolved_unit_sec"], x.Means["stranded_censored_unit_sec"], x.Means["queries"],
			x.Means["stops"], x.Means["fences"], x.TotalSafety, x.TotalLateEffects, gap)
	}
}

func maxf(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

func trunc(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[len(s)-n:]
}

func repoRoot(cfgPath string) string {
	abs, err := filepath.Abs(cfgPath)
	if err != nil {
		return "."
	}
	d := filepath.Dir(abs)
	for i := 0; i < 6; i++ {
		if _, err := os.Stat(filepath.Join(d, "go.mod")); err == nil {
			return d
		}
		d = filepath.Dir(d)
	}
	wd, _ := os.Getwd()
	return wd
}

func writeJSON(path string, v any) {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		fatal(err)
	}
	if err := os.WriteFile(path, append(b, '\n'), 0o644); err != nil {
		fatal(err)
	}
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, "evicon-des:", err)
	os.Exit(1)
}

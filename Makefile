# EviCon - developer targets. Reviewers use evicon_review.py (ARTIFACT.md).
# Every target also writes its full output to results/logs/make-<target>-<time>.log;
# `make bundle` (= python3 evicon_review.py bundle) packs results/ into EVICON_<release>_result.zip.
SHELL := /bin/bash
PYTHON ?= python3
RELEASE := $(shell cat VERSION)
# the pinned toolchain from `make go-toolchain` (VM-C) wins over an older system Go
export PATH := $(CURDIR)/.tools/go/bin:$(PATH)
GO ?= go
export CGO_ENABLED := 1
export GOTOOLCHAIN := local
LOGGED := bash scripts/logged.sh

.PHONY: help go-toolchain build vet test go-test conformance-test firmware-host-test analysis-test verify tlc sim-quick sim-twin campaign-local bundle clean

help:
	@echo "targets: go-toolchain build vet test verify tlc sim-quick sim-twin campaign-local bundle clean"
	@echo "reviewers: python3 evicon_review.py plan   (then: python3 evicon_review.py all)"
	@echo "results for analysis: python3 evicon_review.py bundle  ->  EVICON_$(RELEASE)_result.zip"

go-toolchain:
	@$(LOGGED) make-go-toolchain $(PYTHON) scripts/ensure_go_toolchain.py

build:
	@mkdir -p bin
	@$(LOGGED) make-build $(GO) build -trimpath -o bin/ ./cmd/...

vet:
	@$(LOGGED) make-vet $(GO) vet ./...

go-test:
	@$(LOGGED) make-go-test $(GO) test -count=1 ./...

# --require-go: tests that need Go must run, not skip (exit 3 otherwise)
conformance-test:
	@$(LOGGED) make-conformance-test $(PYTHON) conformance/run_tests.py --require-go --json results/logs/conformance-tests.json

firmware-host-test:
	@$(LOGGED) make-firmware-host-test $(MAKE) -s -C firmware/zephyr-cutoff/tests/host clean test

# test_campaign runs one real local cell: it needs the binaries
analysis-test: build
	@$(LOGGED) make-analysis-test $(PYTHON) -m unittest evaluation.test_analysis evaluation.test_sim_to_real evaluation.test_campaign evaluation.test_prereg evaluation.test_reports evaluation.test_pairs_report evaluation.test_campaign_real evaluation.test_predict evaluation.test_campaign_zephyr evaluation.test_campaign_zephyr_ext evaluation.test_release075

test: go-test conformance-test firmware-host-test analysis-test

verify: build vet test
	@echo "EviCon $(RELEASE) development verification: PASS (development checks only; not a testbed measurement)" | tee -a results/logs/make-verify-summary.log

tlc:
	@$(LOGGED) make-tlc bash formal/run_tlc.sh results/formal

sim-quick: build
	@$(LOGGED) make-sim-quick bash -c 'set -e; \
	  ./bin/evicon-des -config experiments/sim/iot_dosing.json -episodes 5 -only sim-k2 -out results/sim/iot-k2; \
	  ./bin/evicon-des -config experiments/sim/cloud_quota.json -episodes 5 -only E1 -out results/sim/cloud-e1; \
	  $(PYTHON) evaluation/analyze.py --csv results/sim/iot-k2/episodes.csv --csv results/sim/cloud-e1/episodes.csv \
	    --reference B2_LEDGER --reference RES_IMMEDIATE --out results/analysis/sim-quick'

# simulator twin of experiment A (experiments/sim/iot_dosing_testbed.json), 200 episodes
sim-twin: build
	@$(LOGGED) make-sim-twin ./bin/evicon-des -config experiments/sim/iot_dosing_testbed.json -episodes 200 -out results/sim/expA-twin \
	  -references M1,B2_LEDGER -bootstrap 500

campaign-local: build
	@$(LOGGED) make-campaign-local $(PYTHON) evaluation/campaign.py --config evaluation/campaigns/local_smoke.json --out results/runtime

bundle:
	@$(PYTHON) evicon_review.py bundle

clean:
	rm -rf bin   # results/ (review runs, logs) and .isolated/ (Go archive cache) are kept
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	$(MAKE) -s -C firmware/zephyr-cutoff/tests/host clean

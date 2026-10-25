#!/usr/bin/env python3
"""Runs the EviCon-Probe test suite and reports what was skipped and why.

    python3 conformance/run_tests.py [--require-go] [--json <file>]

Exit codes: 0 all good; 1 a test failed or errored; 3 tests that need Go were
skipped although --require-go was given (a "PASS" with those tests silently
skipped would hide most of the end-to-end coverage).
"""
import argparse
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--require-go", action="store_true", help="fail when a test is skipped because Go is missing")
    ap.add_argument("--json", help="write a machine-readable summary here")
    a = ap.parse_args(argv)
    sys.path.insert(0, str(HERE))
    suite = unittest.defaultTestLoader.discover(str(HERE / "tests"), top_level_dir=str(HERE))
    result = unittest.TextTestRunner(verbosity=1, stream=sys.stdout).run(suite)
    skipped = [{"test": t.id(), "reason": r} for t, r in result.skipped]
    go_skips = [s for s in skipped if s["reason"].startswith("Go toolchain")]
    summary = {"tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
               "skipped": len(skipped), "skipped_detail": skipped, "go_skips": len(go_skips),
               "failed_tests": [t.id() for t, _ in result.failures + result.errors]}
    reasons = sorted({s["reason"] for s in skipped})
    print(f"EviCon-Probe tests: {result.testsRun} run, {len(result.failures)} failed, {len(result.errors)} errors, "
          f"{len(skipped)} skipped" + (f" (reasons: {'; '.join(reasons)})" if reasons else ""), flush=True)
    if a.json:
        Path(a.json).write_text(json.dumps(summary, indent=2) + "\n")
    if result.failures or result.errors:
        return 1
    if a.require_go and go_skips:
        print(f"FAIL: {len(go_skips)} test(s) need Go but were skipped; put Go on PATH or run `make go-toolchain`", flush=True)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

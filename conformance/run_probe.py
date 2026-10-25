#!/usr/bin/env python3
"""Entry point usable from any working directory:
python3 /root/EVICON_TOIT_062/conformance/run_probe.py run --config <cfg> --out <dir>"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evicon_probe.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

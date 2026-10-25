"""Runs a probe campaign for one (system, operation) and classifies it."""
from __future__ import annotations

import json
import os
import platform
import re
import socket
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

from . import adapters as registry
from .faults import FaultProxy, from_config
from .model import ProbeResult
from .probes import PROBES, Context, ProbeParams, run_probe
from .rubric import Rubric, classify, merge, to_contract, write_outputs

DEFAULT_ORDER = ["P4", "P6", "P2", "P1", "P8", "P3", "P9", "P9F", "P9T", "P7", "P5", "P5B", "P2N", "P5N", "P9N"]
CONFIG_KEYS = {"schema_version", "name", "adapter", "adapter_config", "faults", "probes", "params", "repetitions",
               "system_version", "references", "compare_with", "notes", "testbed", "fault_model", "expect"}


def load_config(path) -> dict:
    cfg = json.loads(Path(path).read_text())
    unknown = set(cfg) - CONFIG_KEYS
    if unknown:
        raise ValueError(f"{path}: unknown config keys {sorted(unknown)}")
    if cfg.get("schema_version") != 1:
        raise ValueError(f"{path}: schema_version must be 1")
    for k in ("name", "adapter", "adapter_config"):
        if k not in cfg:
            raise ValueError(f"{path}: missing {k}")
    for p in cfg.get("probes", []):
        if p not in PROBES:
            raise ValueError(f"{path}: unknown probe {p}")
    ProbeParams.from_dict(cfg.get("params", {}))
    return cfg


_ENV = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _substitute(obj, mapping: dict):
    """{proxy}-style placeholders from the run, ${ENV_VAR} from the environment
    (secrets such as passwords never live in config files)."""
    if isinstance(obj, str):
        for k, v in mapping.items():
            obj = obj.replace("{" + k + "}", v)

        def env(m):
            if m.group(1) not in os.environ:
                raise ValueError(f"environment variable {m.group(1)} required by the config is not set")
            return os.environ[m.group(1)]
        return _ENV.sub(env, obj)
    if isinstance(obj, dict):
        return {k: _substitute(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute(v, mapping) for v in obj]
    return obj


def run_campaign(cfg: dict, out_root, probes: Optional[list] = None, repetitions: Optional[int] = None,
                 log=print) -> tuple:
    faults = from_config(_substitute(cfg.get("faults") or {"kind": "none"}, {}))
    mapping = {}
    if isinstance(faults, FaultProxy):
        host, port = faults.address
        mapping["proxy"] = f"{host}:{port}"
        mapping["proxy_host"], mapping["proxy_port"] = str(host), str(port)
    acfg = _substitute(cfg["adapter_config"], mapping)
    cls = registry.load(cfg["adapter"])
    adapter = cls(acfg)
    params = ProbeParams.from_dict(cfg.get("params", {}))
    order = probes or cfg.get("probes") or DEFAULT_ORDER
    reps = repetitions or int(cfg.get("repetitions", 1))
    stamp = time.strftime("%Y%m%dT%H%M%S")
    out = Path(out_root) / cfg["name"] / stamp
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1, "scope": "MEASUREMENT", "name": cfg["name"], "started": stamp,
        "adapter": adapter.describe(), "faults": faults.describe(), "params": params.__dict__, "probes": order,
        "repetitions": reps, "system_version": cfg.get("system_version", ""), "testbed": cfg.get("testbed", {}),
        "host": {"hostname": socket.gethostname(), "python": sys.version.split()[0], "platform": platform.platform()},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    def _log(msg):  # console + <run>/probe.log
        log(msg)
        with open(out / "probe.log", "a") as lf:
            lf.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")

    def _error(msg, exc=None):  # <run>/errors.log, with traceback when there is one
        text = f"{time.strftime('%Y-%m-%d %H:%M:%S%z')} {msg}"
        if exc is not None:
            text += "\n" + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()
        with open(out / "errors.log", "a") as ef:
            ef.write(text + "\n\n")
    ctx = Context(adapter=adapter, faults=faults, params=params, log=_log)
    rubrics: list = []
    all_rows: list = []
    try:
        adapter.setup()
        with open(out / "results.jsonl", "w") as fh:
            for rep in range(reps):
                rows = []
                ctx.previous = {}
                for pid in order:
                    r: ProbeResult = run_probe(ctx, pid)
                    ctx.previous[pid] = r
                    d = r.to_dict()
                    d["repetition"] = rep
                    fh.write(json.dumps(d, sort_keys=True) + "\n")
                    fh.flush()
                    rows.append(d)
                    _log(f"[{cfg['name']}] rep={rep} {pid}: {_summary(d)}")
                    if d.get("error"):
                        _error(f"[{cfg['name']}] rep={rep} {pid}: {d['error']}")
                all_rows += rows
                rubrics.append(classify(rows, adapter.system, adapter.operation, adapter.declared, params.__dict__,
                                        adapter.implementation_status, cfg.get("fault_model", "")))
    except Exception as e:
        _error(f"[{cfg['name']}] aborted: {type(e).__name__}: {e}", e)
        raise
    finally:
        try:
            faults.clear()
        except Exception:
            pass
        adapter.teardown()
        if isinstance(faults, FaultProxy):
            faults.stop()
    rb = merge(rubrics)
    contract = to_contract(rb, list(cfg.get("references", [])) + list(adapter.references))
    if cfg.get("compare_with"):
        contract["notes"] = (contract.get("notes", "") + f"; compare_with={cfg['compare_with']}")[:2000]
    write_outputs(rb, contract, out)
    return rb, contract, out


def _summary(d: dict) -> str:
    if d.get("error"):
        return "ERROR " + d["error"]
    if not d.get("applicable", True):
        return "N/A " + d.get("skip_reason", "")
    parts = []
    if d.get("client"):
        parts.append(f"client={d['client']['status']}/{d['client']['outcome']}")
    for k in ("query", "stop", "fence"):
        if d.get(k):
            parts.append(f"{k}={d[k]['outcome']}")
    parts.append(f"effects={d.get('effects')}->{d.get('effects_final')}")
    for k in ("late_effect", "duplicate", "false_stopped"):
        if d.get(k):
            parts.append(k.upper())
    return " ".join(parts)


def reclassify(results_path, declared: dict, params: dict, fault_model: str = "") -> Rubric:
    rows = [json.loads(l) for l in Path(results_path).read_text().splitlines() if l.strip()]
    if not rows:
        raise ValueError("empty results")
    reps = sorted({r.get("repetition", 0) for r in rows})
    rubrics = []
    for rep in reps:
        rr = [r for r in rows if r.get("repetition", 0) == rep]
        rubrics.append(classify(rr, rr[0]["system"], rr[0]["operation"], declared, params, fault_model=fault_model))
    return merge(rubrics)

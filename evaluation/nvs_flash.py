#!/usr/bin/env python3
"""Flash writes of the EviCon firmware, counted from its flash file (release 086, PREREG_extra_086.md estimand E-COST;
reviewer comment M3(1)).

The cutoff firmware (firmware/zephyr-cutoff, unchanged) keeps its durable state with Zephyr's settings subsystem on
NVS: one settings_save_one per durable write of the cutoff (`evicon/cutoff`, evc_core_fence), the claim high-water mark
(`evicon/hw`, evc_core_command, once per EVC_HW_BLOCK = 64 sequences) and the tombstone table (`evicon/tomb`, stop
mode). NVS appends every write as a data record with an allocation table entry (ATE: id, offset, length, part, CRC-8)
and never overwrites in place until garbage collection, so the ATEs of a sector are the write history: for each
settings name (records with ids from 0x8001, the name counter at 0x8000) the value records carry id + 0x4000.

count(path) scans the native_sim flash file (2 MiB) for NVS sectors (4096 bytes, ATEs written downwards from the end,
each with a valid CRC-8/CCITT over its first 7 bytes, initial value 0xff) and returns, per settings name, the number of
value writes, plus the name-record and name-counter writes and a flag when the history may be incomplete (a sector
was garbage-collected: more than one sector holds ATEs, or a sector holds a close ATE). The campaign runner of
release 086 (campaign_zephyr_ext086.py) runs it on VM-E on every cell's flash files before the frozen runner deletes
them, and the report checks it against the writes the traces imply (cost086.py).

    python3 evaluation/nvs_flash.py <flash file or directory with fw*.bin> ...    prints JSON
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SECTOR = 4096
ATE = 8
NAMECNT_ID = 0x8000
NAME_ID_OFFSET = 0x4000


def crc8_ccitt(data: bytes, crc: int = 0xFF) -> int:
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _ates(sector: bytes):
    """Valid ATEs of a sector from its end downwards: (slot offset, id, data offset, length)."""
    out = []
    for off in range(SECTOR - ATE, -1, -ATE):
        e = sector[off:off + ATE]
        if e == b"\xff" * ATE:
            if off < SECTOR - 2 * ATE:
                break  # the first erased slot below the special entries: the end of the table
            continue
        if crc8_ccitt(e[:7]) != e[7]:
            if off >= SECTOR - 2 * ATE:
                continue
            break
        out.append((off, int.from_bytes(e[0:2], "little"), int.from_bytes(e[2:4], "little"), int.from_bytes(e[4:6], "little")))
    return out


def count(path) -> dict:
    data = Path(path).read_bytes()
    sectors = []
    for base in range(0, len(data) - SECTOR + 1, SECTOR):
        s = data[base:base + SECTOR]
        if s.count(0xFF) == SECTOR:
            continue
        ates = _ates(s)
        if ates:
            sectors.append((base, s, ates))
    names, values, name_writes, namecnt_writes, close = {}, {}, {}, 0, 0
    for base, s, ates in sectors:
        for off, ident, doff, ln in ates:
            if off == SECTOR - ATE:
                close += 1  # a close ATE: the sector was full and closed (garbage collection follows)
                continue
            if ident == 0xFFFF:
                continue  # the gc-done marker
            if ident == NAMECNT_ID:
                namecnt_writes += 1
            elif NAMECNT_ID < ident < NAMECNT_ID + NAME_ID_OFFSET:
                names[ident] = s[doff:doff + ln].decode(errors="replace")
                name_writes[ident] = name_writes.get(ident, 0) + 1
            elif ident > NAMECNT_ID + NAME_ID_OFFSET:
                values[ident - NAME_ID_OFFSET] = values.get(ident - NAME_ID_OFFSET, 0) + 1
    writes = {names.get(i, f"id{i:#x}"): n for i, n in sorted(values.items())}
    return {"file": Path(path).name, "sectors_used": len(sectors), "sector_offsets": [b for b, _, _ in sectors], "value_writes": writes,
            "name_writes": sum(name_writes.values()), "name_counter_writes": namecnt_writes,
            "complete": len(sectors) <= 1 and close == 0}


def main(argv=None):
    out = []
    for a in (sys.argv[1:] if argv is None else argv):
        p = Path(a)
        for f in (sorted(p.glob("fw*.bin")) if p.is_dir() else [p]):
            try:
                out.append(count(f))
            except OSError as e:
                out.append({"file": f.name, "error": str(e)})
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

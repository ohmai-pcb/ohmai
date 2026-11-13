# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""`skidl-netlist` check: an independent netlist built by SKiDL from expected_nets.csv must match KiCad's export.

Why: `netlist` compares KiCad's export with expected_nets.csv in ohmai's own code. This check builds a second
netlist from expected_nets.csv with SKiDL, which loads every part from the pinned KiCad symbol libraries (so a
pin number that the symbol does not have is an error), and then compares two netlists net-by-net as
partitions: every SKiDL net must be exactly one KiCad net (no split, no short, no extra pin unless the row is
open with "..."), and every NC pin must be alone. Optional: WARN when SKiDL is not installed.

SKiDL runs as a separate process (`ohmai/_skidl_runner.py`): it imports InSpice (GPL-3.0-or-later), which must
not be loaded into ohmai (Apache-2.0). Interpreter: $OHMAI_SKIDL_PYTHON, default this Python.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

from ohmai import kicadlibs
from ohmai.checks import FAIL, PASS, SKIP, WARN, Result, register
from ohmai.checks import netdiff as nd
from ohmai.checks.schematic import _need_cli, export_netlist
from ohmai.netlist import Netlist, base, load

RUNNER = Path(__file__).resolve().parents[1] / "_skidl_runner.py"


def skidl_python() -> str:
    return os.environ.get("OHMAI_SKIDL_PYTHON") or sys.executable


@lru_cache(maxsize=4)
def skidl_version(python: str) -> str | None:
    """SKiDL version in that interpreter, read from package metadata (SKiDL itself is not imported)."""
    try:
        r = subprocess.run([python, "-c", "import importlib.metadata as m; print(m.version('skidl'))"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def build_spec(kicad_nl: Netlist, rows, sym_dir: Path, out: Path) -> tuple[dict, list[str]]:
    parts, probs = [], []
    for ref, c in sorted(kicad_nl.comps.items()):
        lib, _, name = c["part"].partition(":")
        if not name:
            probs.append(f"{ref}: symbol {c['part']!r} has no library prefix")
            continue
        parts.append({"ref": ref, "lib": lib, "name": name, "value": c["value"], "footprint": c.get("footprint", "")})
    nets = []
    for name, nodes, is_open in rows:
        exact = [n for n in nodes if not n.endswith(".*")]
        refs = [n[:-2] for n in nodes if n.endswith(".*")]
        if name == "NC":
            nets += [{"name": f"NC:{n}", "nodes": [n], "refs": [], "open": False, "nc": True} for n in exact]
        else:
            nets.append({"name": name, "nodes": exact, "refs": refs, "open": is_open, "nc": False})
    return {"sym_dir": str(sym_dir), "out": str(out), "parts": parts, "nets": nets}, probs


def compare(sk: Netlist, ki: Netlist, nets: list[dict]) -> list[str]:
    """Partition compare: each expected (SKiDL) net = exactly one KiCad net."""
    errors: list[str] = []
    owner: dict[str, str] = {}
    for n in nets:
        sk_nodes = set(sk.nets.get(n["name"], set()))
        if not sk_nodes:
            if not n["nc"]:
                errors.append(f"{n['name']}: SKiDL net is empty (no pin of the row resolved)")
            continue
        ki_nets = {ki.where.get(x) for x in sk_nodes}
        missing = sorted(x for x in sk_nodes if x not in ki.where)
        if missing:
            errors.append(f"{n['name']}: pin(s) {', '.join(missing)} not in the KiCad netlist")
        ki_nets.discard(None)
        if len(ki_nets) > 1:
            parts = "; ".join(f"{base(k)}: {', '.join(sorted(ki.nets[k] & sk_nodes))}" for k in sorted(ki_nets))
            errors.append(f"split: expected net {n['name']} is {len(ki_nets)} KiCad nets ({parts})")
            continue
        if not ki_nets:
            continue
        k = ki_nets.pop()
        if k in owner:
            errors.append(f"short: expected nets {owner[k]} and {n['name']} are one KiCad net {base(k)}")
            continue
        owner[k] = n["name"]
        extra = sorted(ki.nets[k] - sk_nodes)
        if n["nc"] and extra:
            errors.append(f"nc: {n['name'][3:]} must be unconnected, KiCad net {base(k)} also has {', '.join(extra)}")
        elif extra and not n["open"]:
            errors.append(f"extra: KiCad net {base(k)} (expected {n['name']}) also has {', '.join(extra[:8])} "
                          "(add them to the row, or end the row with ...)")
    return errors


@register("skidl-netlist", "Independent cross-check: SKiDL builds a netlist from expected_nets.csv with the pinned "
                           "KiCad symbols; it must match the KiCad export net-by-net (optional: WARN without SKiDL)")
def skidl_netlist(project, opts: dict) -> Result:
    name = "skidl-netlist"
    py = opts.get("skidl_python") or skidl_python()
    ver = skidl_version(py)
    if not ver:
        return Result(name, WARN, "SKiDL not installed: independent netlist cross-check not run "
                                  "(optional: pip install skidl, or set OHMAI_SKIDL_PYTHON)")
    if not opts.get("netlist") and (r := _need_cli(name)) is not None:
        return r
    if not project.expected_nets.is_file():
        return Result(name, FAIL, f"{project.expected_nets.name} not found")
    tag = kicadlibs.pinned(project)
    sym = kicadlibs.dirs(tag)[0] if tag else None
    if not sym or not sym.is_dir():
        return Result(name, SKIP, f"pinned KiCad symbol libraries {tag} not installed (ohmai kicad-libs install)")
    try:
        ki = load(export_netlist(project, opts))
    except RuntimeError as e:
        return Result(name, FAIL, str(e))
    if not ki.comps:
        return Result(name, FAIL, "KiCad netlist has 0 components")
    rows, todo = nd.load_expected(project.expected_nets)
    with tempfile.TemporaryDirectory(prefix="ohmai-skidl-") as td:
        out = Path(td) / "skidl.net"
        spec, probs = build_spec(ki, rows, sym, out)
        (Path(td) / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        r = subprocess.run([py, str(RUNNER), str(Path(td) / "spec.json")], capture_output=True, text=True,
                           timeout=600, cwd=td)
        try:
            res = json.loads(r.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            return Result(name, FAIL, f"SKiDL run failed (exit {r.returncode})", [(r.stderr or r.stdout)[-1500:]])
        if not out.is_file():
            return Result(name, FAIL, "SKiDL wrote no netlist", res.get("errors", []))
        sk = load(out)
        if opts.get("keep_skidl_netlist", True):
            project.out.mkdir(parents=True, exist_ok=True)
            (project.out / "skidl.net").write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
    errors = probs + res.get("errors", []) + compare(sk, ki, spec["nets"])
    summary = (f"SKiDL {ver}: {len(sk.comps)} parts, {len(spec['nets'])} expected nets vs KiCad {len(ki.nets)} nets; "
               f"{todo} TODO rows skipped")
    if errors:
        return Result(name, FAIL, summary + f"; {len(errors)} error(s)", errors)
    return Result(name, WARN if todo else PASS, summary)

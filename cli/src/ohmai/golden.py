"""Golden projects: rebuild a reference project from its sources, diff its outputs with approved snapshots.

<tests>/golden/<name>/
    golden.yaml      what to build and compare (below)
    src/             the sources, overlaid on a fresh `ohmai new` project (base: template), or
                     `source: <path>` + `ref: <git ref>` for a full project repo (pico-k, once frozen)
    snapshots/       approved outputs (<artefact>.txt) + MANIFEST.yaml (toolchain used, approval log)

golden.yaml:
    name: ldo-led
    description: ...
    needs: [kicad-cli, kicad-libs]
    build: [kicad-libs-tables, schematic]       # rebuild steps (schematic = `ohmai schematic build`)
    artefacts: [netlist, erc, bom, symbols, checks]   # also drc, gerbers when the project has a layout
    checks: [erc, netlist, pin-map, ...]        # statuses + summaries recorded in checks.txt

Artefacts are normalised text (no dates, tool banners, absolute paths, coordinates of ERC markers) so a
diff means the design data changed. `ohmai golden --accept --reason "..."` writes new snapshots and logs
the reason, the changed artefacts and the toolchain in MANIFEST.yaml. Never accept to make a red run green
without reading the diff: a changed netlist after a tool upgrade is a design change.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from ohmai import __version__, kicad, kicadlibs
from ohmai.project import Project, find_project
from ohmai.resources import tests_dir

ARTEFACTS = ("netlist", "erc", "bom", "symbols", "checks", "drc", "gerbers")


@dataclass
class Golden:
    name: str
    dir: Path
    spec: dict

    @property
    def snapshots(self) -> Path:
        return self.dir / "snapshots"


@dataclass
class GoldenRun:
    golden: Golden
    outputs: dict[str, str] = field(default_factory=dict)
    diffs: dict[str, str] = field(default_factory=dict)
    skipped: str = ""
    error: str = ""
    toolchain: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.skipped:
            return "SKIP"
        if self.error:
            return "ERROR"
        return "DIFF" if self.diffs else "PASS"


def golden_root() -> Path:
    return Path(os.environ.get("OHMAI_GOLDEN_DIR") or tests_dir() / "golden")


def load_goldens(names: list[str] | None = None, root: Path | None = None) -> list[Golden]:
    root = root or golden_root()
    out = [Golden(d.name, d, yaml.safe_load((d / "golden.yaml").read_text(encoding="utf-8")) or {})
           for d in sorted(root.iterdir()) if (d / "golden.yaml").is_file()] if root.is_dir() else []
    if names:
        unknown = sorted(set(names) - {g.name for g in out})
        if unknown:
            raise SystemExit(f"ohmai golden: unknown golden project(s) {', '.join(unknown)} in {root}")
        out = [g for g in out if g.name in names]
    return out


# ---------------------------------------------------------------- build
def _copy_source(g: Golden, dest: Path) -> None:
    src = Path(os.path.expandvars(str(g.spec["source"]))).expanduser()
    ref = g.spec.get("ref")
    dest.mkdir(parents=True)
    if ref:
        arc = subprocess.run(["git", "-C", str(src), "archive", "--format=tar", str(ref)], capture_output=True)
        if arc.returncode != 0:
            raise RuntimeError(f"git archive {ref} in {src}: {arc.stderr.decode(errors='replace').strip()}")
        subprocess.run(["tar", "-x", "-C", str(dest)], input=arc.stdout, check=True)
    else:
        shutil.copytree(src, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", "out"))


def build(g: Golden, work: Path) -> Project:
    """Materialise the golden project in work/<name> and run its build steps."""
    from ohmai.newproject import create

    dest = work / g.name
    if dest.exists():
        shutil.rmtree(dest)
    if g.spec.get("source"):
        _copy_source(g, dest)
    else:
        create(dest, f"golden reference project {g.name}", g.spec.get("project_name", g.name), git=False)
        shutil.copytree(g.dir / "src", dest, dirs_exist_ok=True)
    p = find_project(dest)
    kicadlibs.activate(p)
    for step in g.spec.get("build") or []:
        if step == "kicad-libs-tables":
            kicadlibs.write_tables(p)
        elif step == "schematic":
            from ohmai.schgen import build as schbuild

            schbuild(p)
        else:
            raise RuntimeError(f"unknown build step {step!r}")
    return find_project(dest)


# ---------------------------------------------------------------- artefacts
def _netlist(p: Project) -> str:
    from ohmai.checks.schematic import export_netlist
    from ohmai.netlist import load

    nl = load(export_netlist(p, {}, force=True))
    lines = ["# components: ref | value | symbol | footprint | MPN | flags"]
    for ref, c in sorted(nl.comps.items(), key=lambda kv: _refkey(kv[0])):
        lines.append(f"{ref} | {c['value']} | {c['part']} | {c.get('footprint', '')} | {c['fields'].get('MPN', '')} | "
                     f"{','.join(sorted(c.get('flags', ())))}")
    lines.append("# nets: name: nodes (pin names from the symbol)")
    for name in sorted(nl.nets):
        nodes = sorted(nl.nets[name], key=_refkey)
        lines.append(f"{name}: " + " ".join(_node(nl, n) for n in nodes))
    return "\n".join(lines) + "\n"


def _node(nl, node: str) -> str:
    ref, pin = node.split(".", 1)
    pname = nl.pins_of(ref).get(pin, "")
    return f"{node}({pname})" if pname and pname != "~" else node


def _refkey(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def _erc(p: Project) -> str:
    out = p.out / "golden-erc.json"
    kicad.run(["sch", "erc", "--format", "json", "--severity-all", "-o", str(out), str(p.schematic)])
    data = json.loads(out.read_text(encoding="utf-8"))
    rows = []
    for s in data.get("sheets", []):
        for v in s.get("violations", []):
            items = "; ".join(sorted(i.get("description", "") for i in v.get("items", [])))
            rows.append(f"[{v.get('severity')}] {s.get('path', '?')} {v.get('type')}: {v.get('description', '')} "
                        f"<{items}>")
    return f"# ERC: {len(rows)} violation(s)\n" + "\n".join(sorted(rows)) + ("\n" if rows else "")


def _bom(p: Project) -> str:
    from ohmai.checks.schematic import export_netlist
    from ohmai.netlist import load

    nl = load(export_netlist(p, {}))
    groups: dict[tuple, list[str]] = {}
    for ref, c in nl.comps.items():
        if "exclude_from_bom" in c.get("flags", ()):
            continue
        key = (c["value"], c.get("footprint", ""), c["fields"].get("MPN", ""), "dnp" in c.get("flags", ()))
        groups.setdefault(key, []).append(ref)
    lines = ["refs,qty,value,footprint,mpn,dnp"]
    for (val, fp, mpn, dnp), refs in sorted(groups.items(), key=lambda kv: _refkey(sorted(kv[1], key=_refkey)[0])):
        refs = sorted(refs, key=_refkey)
        lines.append(f"{' '.join(refs)},{len(refs)},{val},{fp},{mpn},{'yes' if dnp else ''}")
    return "\n".join(lines) + "\n"


def _blocks(text: str, start: int):
    """Yield (name, block) of each top-level `(symbol "name" ...)` inside the s-expression at start."""
    depth, i, instr, cur = 0, start, False, None
    while i < len(text):
        ch = text[i]
        if instr:
            if ch == "\\":
                i += 1
            elif ch == '"':
                instr = False
        elif ch == '"':
            instr = True
        elif ch == "(":
            depth += 1
            if depth == 2 and text.startswith("(symbol ", i):
                cur = i
        elif ch == ")":
            if depth == 2 and cur is not None:
                blk = text[cur:i + 1]
                m = re.match(r'\(symbol "([^"]+)"', blk)
                yield (m.group(1) if m else "?"), blk
                cur = None
            depth -= 1
            if depth == 0:
                return
        i += 1


def _symbols(p: Project) -> str:
    from ohmai.checks.schematic import sheet_files

    seen: dict[str, str] = {}
    files, _ = sheet_files(p.schematic.resolve())
    for f in files:
        t = f.read_text(encoding="utf-8")
        k = t.find("(lib_symbols")
        if k < 0:
            continue
        for name, blk in _blocks(t, k):
            norm = re.sub(r"\s+", " ", blk)
            seen[name] = hashlib.sha256(norm.encode()).hexdigest()[:16]
    return "# embedded library symbols: lib_id sha256[:16] (a change = the library symbol changed)\n" + \
        "".join(f"{n} {h}\n" for n, h in sorted(seen.items()))


def _checks(p: Project, names: list[str]) -> str:
    from ohmai.checks import run

    lines = []
    for n in names:
        r = run(p, n, {})
        summary = r.summary.replace(str(p.root), "<project>")
        lines.append(f"{n}: {r.status} {summary}")
        lines += [f"    {d.replace(str(p.root), '<project>')}" for d in r.details[:20]]
    return "\n".join(lines) + "\n"


def _drc(p: Project) -> str:
    out = p.out / "golden-drc.json"
    kicad.run(["pcb", "drc", "--format", "json", "--severity-all", "--schematic-parity", "-o", str(out), str(p.pcb)])
    d = json.loads(out.read_text(encoding="utf-8"))
    rows = [f"[{v.get('severity')}] {k} {v.get('type')}: {v.get('description', '')}"
            for k in ("violations", "unconnected_items", "schematic_parity") for v in d.get(k, [])]
    return f"# DRC: {len(rows)} item(s)\n" + "\n".join(sorted(rows)) + ("\n" if rows else "")


_VOLATILE = re.compile(r"CreationDate|GenerationSoftware|^G04 Created by|^; #@! TF\.CreationDate|^; DRILL file.*date", re.I)


def _gerbers(p: Project) -> str:
    d = p.out / "golden-gerbers"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    kicad.run(["pcb", "export", "gerbers", "-o", str(d) + "/", str(p.pcb)])
    kicad.run(["pcb", "export", "drill", "-o", str(d) + "/", str(p.pcb)])
    lines = ["# fab files: name sha256[:16] of the content without date/tool lines"]
    for f in sorted(d.iterdir()):
        body = "\n".join(ln for ln in f.read_text(encoding="utf-8", errors="replace").splitlines()
                         if not _VOLATILE.search(ln))
        lines.append(f"{f.name} {hashlib.sha256(body.encode()).hexdigest()[:16]}")
    return "\n".join(lines) + "\n"


def artefacts(g: Golden, p: Project) -> dict[str, str]:
    out = {}
    for a in g.spec.get("artefacts") or ["netlist", "erc", "bom", "symbols", "checks"]:
        if a == "netlist":
            out[a] = _netlist(p)
        elif a == "erc":
            out[a] = _erc(p)
        elif a == "bom":
            out[a] = _bom(p)
        elif a == "symbols":
            out[a] = _symbols(p)
        elif a == "checks":
            out[a] = _checks(p, list(g.spec.get("checks") or []))
        elif a == "drc":
            out[a] = _drc(p)
        elif a == "gerbers":
            out[a] = _gerbers(p)
        else:
            raise RuntimeError(f"unknown artefact {a!r} (known: {', '.join(ARTEFACTS)})")
    return out


def toolchain_now(p: Project | None = None) -> dict:
    tag = os.environ.get(kicadlibs.TAG_ENV) or (kicadlibs.pinned(p) if p else None)
    return {"kicad-cli": kicad.version(), "kicad-libs": tag, "ohmai": __version__}


# ---------------------------------------------------------------- run / accept
def manifest(g: Golden) -> dict:
    f = g.snapshots / "MANIFEST.yaml"
    return (yaml.safe_load(f.read_text(encoding="utf-8")) or {}) if f.is_file() else {}


def run_golden(g: Golden, work: Path | None = None) -> GoldenRun:
    from ohmai.knownbad import missing_needs

    res = GoldenRun(g)
    miss = missing_needs(g.spec.get("needs") or [])
    if miss:
        res.skipped = "needs " + ", ".join(miss)
        return res
    cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="ohmai-golden-") as td:
        try:
            p = build(g, Path(work) if work else Path(td))
            os.chdir(p.root)
            res.outputs = artefacts(g, p)
            res.toolchain = toolchain_now(p)
        except (RuntimeError, SystemExit, OSError, ValueError, subprocess.SubprocessError) as e:
            res.error = f"{type(e).__name__}: {e}"
            return res
        finally:
            os.chdir(cwd)
    snap_tc = (manifest(g).get("toolchain") or {})
    if snap_tc and snap_tc != res.toolchain:
        res.notes.append(f"snapshots made with {snap_tc}, this run used {res.toolchain}")
    res.diffs = compare(g, res.outputs)
    return res


def compare(g: Golden, outputs: dict[str, str]) -> dict[str, str]:
    """Unified diff per artefact that differs from its approved snapshot (missing snapshot = differs)."""
    diffs = {}
    for name, text in outputs.items():
        f = g.snapshots / f"{name}.txt"
        old = f.read_text(encoding="utf-8") if f.is_file() else ""
        if old != text:
            diffs[name] = "".join(difflib.unified_diff(
                old.splitlines(True), text.splitlines(True), f"snapshots/{name}.txt (approved)",
                f"{name} (this run)")) or f"(no approved snapshot for {name})\n"
    return diffs


def accept(run: GoldenRun, reason: str, by: str = "") -> list[str]:
    """Write the run's outputs as the approved snapshots; log the approval. Returns changed artefacts."""
    if not reason.strip():
        raise SystemExit("ohmai golden --accept needs --reason (why the new outputs are correct)")
    g = run.golden
    g.snapshots.mkdir(parents=True, exist_ok=True)
    changed = sorted(run.diffs) or []
    for name, text in run.outputs.items():
        (g.snapshots / f"{name}.txt").write_text(text, encoding="utf-8")
    m = manifest(g)
    m["golden"] = g.name
    m["toolchain"] = dict(run.toolchain)
    m.setdefault("accepted", []).append({"date": date.today().isoformat(), "reason": reason.strip(),
                                         "by": by or os.environ.get("OHMAI_AUTHOR_NAME") or "unknown",
                                         "changed": changed, "toolchain": dict(run.toolchain)})
    (g.snapshots / "MANIFEST.yaml").write_text(
        "# Approved golden snapshots (ohmai golden --accept). Every entry: why the outputs changed.\n"
        + yaml.safe_dump(m, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")
    return changed

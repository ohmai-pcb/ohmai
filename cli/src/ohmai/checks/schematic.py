# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Schematic checks via kicad-cli: sheet-load, ERC, netlist diff, render."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from ohmai import kicad
from ohmai.checks import FAIL, PASS, SKIP, WARN, Result, register
from ohmai.checks import netdiff as nd
from ohmai.netlist import load as load_netlist
from ohmai.project import Project
from ohmai.sheets import from_opts

_SHEETFILE = re.compile(r'\(property\s+"Sheet ?[Ff]ile"\s+"([^"]+)"')


def _need_cli(name: str) -> Result | None:
    if not kicad.kicad_cli():
        return Result(name, SKIP, "kicad-cli not found (install KiCad 8/9 or set KICAD_CLI)")
    return None


def sheet_files(root_sch: Path) -> tuple[list[Path], list[str]]:
    """All sheets reachable from the root, plus missing references."""
    seen: list[Path] = []
    missing: list[str] = []
    todo = [root_sch]
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        if not f.is_file():
            missing.append(str(f))
            continue
        seen.append(f)
        for ref in _SHEETFILE.findall(f.read_text(encoding="utf-8", errors="replace")):
            todo.append((f.parent / ref).resolve())
    return seen, missing


def export_netlist(project: Project, opts: dict | None = None, force: bool = False) -> Path:
    """kicadsexpr netlist of the root schematic (cached in out/), or opts['netlist'] if given."""
    if opts and opts.get("netlist"):
        return Path(opts["netlist"])
    out = project.out / "netlist.net"
    if force or not out.exists() or out.stat().st_mtime < project.schematic.stat().st_mtime:
        r = kicad.run(["sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(out),
                       str(project.schematic)], log=project.out / "netlist.log")
        if kicad.load_failed(r) or not out.exists():
            raise RuntimeError("netlist export failed (see out/netlist.log)")
    return out


@register("kicad-libs", "The project pins an official KiCad library tag (ohmai.yaml kicad.libraries), it is installed, "
                        "and hardware/sym-lib-table + fp-lib-table point at it")
def kicad_libs(project: Project, opts: dict) -> Result:
    from ohmai import kicadlibs
    from ohmai import toolchain as tc
    from ohmai.checks.toolchain import lock_line

    st, summary, details = kicadlibs.status(project)
    if st == FAIL:
        return Result("kicad-libs", st, summary, details)
    lock = tc.load_lock(project)
    if lock is not None and "kicad-libs" in (lock.get("tools") or {}):
        want = lock["tools"]["kicad-libs"].get("version") if isinstance(lock["tools"]["kicad-libs"], dict) else None
        tag = kicadlibs.pinned(project)
        if str(want) != str(tag):
            return Result("kicad-libs", FAIL, f"ohmai.yaml kicad.libraries {tag} but {tc.LOCK_NAME} pins {want}; "
                          f"move the pin with `ohmai upgrade kicad-libs {tag}`", details)
        ls, msg = lock_line(project, "kicad-libs", tc.detect(project, "kicad-libs", opts))
        if ls != PASS:
            return Result("kicad-libs", ls, msg, details)
        summary += f"; {msg}"
    return Result("kicad-libs", st, summary, details)


from ohmai.versions import version_in_range  # noqa: E402,F401 (re-exported; moved to ohmai.versions)


@register("kicad-app", "kicad-cli (the KiCad application) is inside the version range pinned in ohmai.yaml kicad.app")
def kicad_app(project: Project, opts: dict) -> Result:
    spec = (project.config.get("kicad") or {}).get("app")
    have = opts.get("kicad_version") if opts.get("kicad_version") else None
    injected = (opts.get("toolchain") or {}).get("kicad-app", False)   # tests / known-bad inject detection
    if have is None and injected is not False:
        if injected is None:
            return Result("kicad-app", SKIP if not spec else FAIL, "kicad-cli not found (install KiCad or set KICAD_CLI)")
        have = injected.get("version")
    if have is None:
        if not kicad.kicad_cli():
            return Result("kicad-app", SKIP if not spec else FAIL,
                          "kicad-cli not found (install KiCad or set KICAD_CLI)")
        have = kicad.version()
    if not spec:
        return Result("kicad-app", WARN, f"kicad-cli {have}; no application version pinned "
                      "(set ohmai.yaml kicad.app, e.g. '>=9.0.2,<9.1')")
    if not have:
        return Result("kicad-app", FAIL, "kicad-cli did not report a version (`kicad-cli version`)")
    ok, err = version_in_range(have, spec)
    if err:
        return Result("kicad-app", FAIL, f"ohmai.yaml kicad.app {spec!r}: {err}")
    if not ok:
        return Result("kicad-app", FAIL, f"kicad-cli {have} is outside the pinned range {spec!r} (ohmai.yaml kicad.app); "
                      "install a KiCad in range or set KICAD_CLI to one")
    from ohmai import toolchain as tc
    from ohmai.checks.toolchain import lock_line

    over = opts.get("toolchain") or {}
    if "kicad-app" in over:
        found = over["kicad-app"]
    elif opts.get("kicad_version"):
        found = {"version": have}
    else:
        found = tc.detect(project, "kicad-app", opts)
    line = lock_line(project, "kicad-app", found)
    if line is not None and line[0] == FAIL:
        return Result("kicad-app", FAIL, f"kicad-cli {have} within {spec!r}, but {line[1]}")
    if line is not None:
        return Result("kicad-app", line[0], f"kicad-cli {have} within {spec!r}; {line[1]}")
    return Result("kicad-app", PASS, f"kicad-cli {have} within {spec!r} (no {tc.LOCK_NAME} entry: run `ohmai lock`)")


LOAD_RECORD = "load/result.json"


def load_record(project: Project) -> dict | None:
    """The last sheet-load result (out/load/result.json), or None when missing or older than a sheet file."""
    f = project.out / LOAD_RECORD
    if not f.is_file():
        return None
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        return None
    files, _ = sheet_files(project.schematic.resolve()) if project.schematic.is_file() else ([], [])
    if any(x.stat().st_mtime > f.stat().st_mtime for x in files):
        return None
    return rec


@register("sheet-load", "Every sheet loads on its own in kicad-cli (a broken sheet is silently dropped by ERC); "
                        "records loaded + symbol count per sheet in out/load/result.json (ERC is invalid without it)")
def sheet_load(project: Project, opts: dict) -> Result:
    from ohmai.sheets import from_opts, symbol_count

    if (r := _need_cli("sheet-load")) is not None:
        return r
    if not project.schematic.is_file():
        return Result("sheet-load", FAIL, f"root schematic {project.schematic} not found")
    files, missing = sheet_files(project.schematic.resolve())
    # also catch orphan sheets in the hardware folder that the root does not reference
    files += [p.resolve() for p in project.schematic.parent.glob("*.kicad_sch") if p.resolve() not in files]
    sh = from_opts(opts)
    if sh is not None:
        files, missing = [f for f in files if f == sh.file], [m for m in missing if Path(m) == sh.file]
    bad = [f"referenced sheet missing: {m}" for m in missing]
    logdir = project.out / "load"
    rec: dict = {"ok": True, "sheets": {}, "missing": missing}
    for f in files:
        r = kicad.run(["sch", "export", "netlist", "--format", "kicadsexpr",
                       "-o", str(logdir / f"{f.stem}.net"), str(f)], log=logdir / f"{f.stem}.log")
        n = symbol_count(f.read_text(encoding="utf-8", errors="replace"))
        loaded = not kicad.load_failed(r)
        rec["sheets"][f.name] = {"loaded": loaded, "symbols": n}
        if not loaded:
            bad.append(f"{f.name} does not load (out/load/{f.stem}.log)")
    total = sum(v["symbols"] for v in rec["sheets"].values())
    if files and total == 0:
        bad.append("0 placed symbols in the loaded sheet(s): nothing to check (an empty or unparsed sheet is not a pass)")
    rec["ok"] = not bad
    if sh is None:
        logdir.mkdir(parents=True, exist_ok=True)
        (project.out / LOAD_RECORD).write_text(json.dumps(rec, indent=2), encoding="utf-8")
    if bad:
        return Result("sheet-load", FAIL, f"{len(bad)} sheet problem(s)", bad)
    return Result("sheet-load", PASS, f"all {len(files)} sheet(s) load, {total} symbol(s)")


def erc_validity(project: Project, data: dict, opts: dict) -> list[str]:
    """Why an ERC report cannot count as a pass: a sheet did not load, 0 symbols, or ERC saw fewer sheets."""
    from ohmai.sheets import instances

    rec = load_record(project)
    if rec is None:
        r = sheet_load(project, {k: v for k, v in opts.items() if k != "sheet"})
        if r.status == SKIP:
            return []
        rec = load_record(project) or {"ok": r.status == PASS, "sheets": {}}
    why = []
    if not rec.get("ok"):
        failed = [k for k, v in rec.get("sheets", {}).items() if not v.get("loaded")] + list(rec.get("missing") or [])
        why.append("sheet-load failed (" + (", ".join(failed) or "see out/load/result.json") + "): ERC skips a sheet "
                   "that does not load, so its 0 violations would be a false pass")
    if rec.get("sheets") and sum(v.get("symbols", 0) for v in rec["sheets"].values()) == 0:
        why.append("0 placed symbols: ERC on an empty or unparsed schematic proves nothing")
    want = len(instances(project.schematic))
    got = len(data.get("sheets", []))
    if got < want:
        why.append(f"ERC report covers {got} sheet(s), the hierarchy has {want}: a sheet was dropped")
    return why


@register("erc", "kicad-cli ERC, all severities, JSON report in out/erc.json")
def erc(project: Project, opts: dict) -> Result:
    if (r := _need_cli("erc")) is not None:
        return r
    out = project.out / "erc.json"
    kicad.run(["sch", "erc", "--format", "json", "--severity-all", "-o", str(out), str(project.schematic)],
              log=project.out / "erc.log")
    try:
        data = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return Result("erc", FAIL, f"no ERC report ({e}); see out/erc.log")
    invalid = erc_validity(project, data, opts)
    if invalid:
        return Result("erc", FAIL, "ERC result invalid (load before ERC): " + invalid[0], invalid)
    sh = from_opts(opts)
    vs = [(s.get("path", "?"), v) for s in data.get("sheets", []) for v in s.get("violations", [])
          if sh is None or s.get("path") == sh.path]
    sev = Counter(v.get("severity", "?") for _, v in vs)
    details = [f"[{v.get('severity')}] {p} {v.get('type')}: {v.get('description', '')}" for p, v in vs[:50]]
    summary = (f"sheet {sh.name}: " if sh else "") + f"{len(vs)} violation(s)" + (": " + ", ".join(f"{n} {k}" for k, n in sev.most_common()) if vs else "")
    # Policy: errors fail; warnings fail too unless ERC_ALLOW_WARNINGS is set in ohmai.yaml (never silence ERC).
    allow_warn = bool(project.config.get("checks", {}).get("erc_allow_warnings", False))
    failing = sev.get("error", 0) + (0 if allow_warn else sev.get("warning", 0))
    return Result("erc", FAIL if failing else PASS, summary, details)


@register("netlist", "Netlist (kicadsexpr) vs expected_nets.csv; actual nets dumped to out/actual_nets.csv")
def netlist(project: Project, opts: dict) -> Result:
    if not opts.get("netlist") and (r := _need_cli("netlist")) is not None:
        return r
    if not project.expected_nets.is_file():
        return Result("netlist", FAIL, f"{project.expected_nets.name} not found")
    try:
        nl = load_netlist(export_netlist(project, opts))
    except RuntimeError as e:
        return Result("netlist", FAIL, str(e))
    nd.dump(nl, project.out / "actual_nets.csv")
    rows, todo = nd.load_expected(project.expected_nets)
    sh = from_opts(opts)
    refs = nl.refs_on(sh.path) if sh else None
    if sh is not None:
        rows = [r for r in rows if any(n.split(".")[0] in refs for n in r[1])]
    errors = nd.diff(nl, rows)
    cov = nd.coverage(nl, rows, refs)
    errors += cov["errors"]
    summary = ((f"sheet {sh.name}: {len(refs)} parts; " if sh else f"{len(nl.comps)} parts, {len(nl.nets)} nets; ")
               + f"{len(rows)} expected rows, {todo} TODO rows skipped; {cov['auto']} unlabeled net(s), "
               f"{cov['auto_missing']} not in {project.expected_nets.name}")
    if errors:
        return Result("netlist", FAIL, summary + f"; {len(errors)} error(s)", errors + cov["warns"])
    return Result("netlist", WARN if (todo or cov["warns"]) else PASS, summary, cov["warns"])


def _png_converter():
    if shutil.which("rsvg-convert"):
        return "rsvg"
    try:
        import cairosvg  # noqa: F401

        return "cairosvg"
    except ImportError:
        return None


@register("render", "SVG per sheet + PNG (rsvg-convert or cairosvg) in out/render/ for human and vision review")
def render(project: Project, opts: dict) -> Result:
    if (r := _need_cli("render")) is not None:
        return r
    svg_dir = project.out / "render" / "svg"
    png_dir = project.out / "render" / "png"
    for d in (svg_dir, png_dir):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
    r = kicad.run(["sch", "export", "svg", "-o", str(svg_dir), str(project.schematic)],
                  log=project.out / "render.log")
    svgs = sorted(svg_dir.glob("*.svg"))
    if r.returncode != 0 or not svgs:
        return Result("render", FAIL, "SVG export failed (see out/render.log)")
    conv = _png_converter()
    if conv is None:
        return Result("render", WARN, f"{len(svgs)} SVG written; no PNG converter (install librsvg2-bin or cairosvg)")
    width = str(project.config.get("checks", {}).get("png_width", 2400))
    for s in svgs:
        p = png_dir / (s.stem + ".png")
        if conv == "rsvg":
            subprocess.run(["rsvg-convert", "-w", width, "-b", "white", "-o", str(p), str(s)], check=False)
        else:
            import cairosvg

            cairosvg.svg2png(url=str(s), write_to=str(p), output_width=int(width), background_color="white")
    pngs = sorted(png_dir.glob("*.png"))
    sh = from_opts(opts)
    if sh is not None:
        mine = [p for p in pngs if (p.stem.endswith("-" + sh.name) if sh.path != "/" else p.stem == project.schematic.stem)]
        if not mine:
            return Result("render", FAIL, f"sheet {sh.name}: no PNG rendered", [p.name for p in pngs])
        return Result("render", PASS, f"sheet {sh.name}: {len(mine)} PNG; look at it",
                      [str(p.relative_to(project.root)) for p in mine])
    rel = [str(p.relative_to(project.root)) for p in pngs]
    if len(pngs) != len(svgs):
        return Result("render", FAIL, f"{len(pngs)}/{len(svgs)} PNG converted", rel)
    return Result("render", PASS, f"{len(pngs)} PNG in out/render/png/ ({conv}); look at every changed sheet", rel)

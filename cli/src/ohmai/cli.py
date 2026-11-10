# SPDX-License-Identifier: Apache-2.0
"""ohmai command line."""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import sys
from pathlib import Path

from ohmai import __version__

STATUS_ICON = {"PASS": "PASS", "FAIL": "FAIL", "WARN": "WARN", "SKIP": "SKIP", "TODO": "TODO"}


def cmd_new(a) -> int:
    from ohmai.newproject import create

    try:
        log = create(Path(a.path), a.idea, a.name, git=not a.no_git, anonymous=getattr(a, 'anonymous', False))
    except (FileExistsError, FileNotFoundError) as e:
        print(f"ohmai new: {e}", file=sys.stderr)
        return 1
    for line in log:
        print(f"  {line}")
    dest = Path(a.path).resolve()
    print(f"\nCreated {dest}\nNext: cd {shlex.quote(str(dest))} && ohmai status")
    print("Agent: read AGENTS.md, then .ohmai/phases/01-requirements.md")
    return 0


def cmd_adopt(a) -> int:
    from ohmai.adopt import adopt

    na = {}
    for item in a.na or []:
        k, sep, v = item.partition("=")
        if not sep or not v.strip():
            print(f"ohmai adopt: --na needs check=reason, got {item!r}", file=sys.stderr)
            return 2
        na[k.strip()] = v.strip()
    for line in adopt(Path(a.path), Path(a.schematic) if a.schematic else None, na,
                      Path(a.netlist) if a.netlist else None):
        print(f"  {line}")
    return 0


def _project():
    from ohmai.project import NotAProject, find_project

    try:
        return find_project()
    except NotAProject as e:
        print(f"ohmai: {e}", file=sys.stderr)
        sys.exit(2)


def cmd_status(a) -> int:
    from ohmai.status import load

    p = _project()
    if getattr(a, "set_phase", None) or getattr(a, "gate", None):
        from ohmai.status import set_front
        try:
            changes = set_front(p.root / "STATUS.md", a.set_phase, a.gate, a.decision)
        except ValueError as e:
            print(f"ohmai status: {e}", file=sys.stderr)
            return 2
        print("STATUS.md: " + ("; ".join(changes) if changes else "unchanged"))
        return 0
    st = load(p.root / "STATUS.md")
    if a.json:
        print(json.dumps(st.as_dict(), indent=2, default=str))
        return 1 if st.problems else 0
    ph = st.phase
    print(f"Project : {st.meta.get('project', '?')}  (OhmAI {st.meta.get('ohmai_version', '?')}, "
          f"CLI {__version__})")
    print(f"Phase   : {ph.num if ph else '?'} {ph.title if ph else ''}  gate={st.meta.get('gate', '?')}")
    if ph:
        print(f"Gate    : {ph.gate}")
        print(f"Checks  : ohmai check {ph.id}  ({', '.join(ph.checks)})")
    print(f"Decided : {len(st.decisions)} locked decision(s)")
    for d in st.decisions[-5:]:
        print(f"          {d.get('id', '')} {d.get('decision', '')}")
    print(f"Open    : {len(st.open_questions)} question(s)")
    for q in st.open_questions:
        print(f"          [ ] {q}")
    for prob in st.problems:
        print(f"PROBLEM : {prob}")
    return 1 if st.problems else 0


def _expand(names: list[str], project) -> list[str]:
    from ohmai.checks import HELP, REGISTRY
    from ohmai.phases import PHASES, resolve
    from ohmai.status import load

    out: list[str] = []
    for n in names:
        if n == "all":
            items = [c for ph in PHASES for c in ph.checks]
        elif n == "current":
            ph = load(project.root / "STATUS.md").phase
            if ph is None:
                raise SystemExit("ohmai check current: STATUS.md has no valid phase")
            items = list(ph.checks)
        elif (ph := resolve(n)) is not None:
            items = list(ph.checks)
        elif n in REGISTRY or n in HELP or n.startswith(("docs:", "review:")):
            items = [n]
        else:
            raise SystemExit(f"ohmai check: unknown phase or check '{n}' (see ohmai check --list)")
        for i in items:
            if i not in out:
                out.append(i)
    return out


def cmd_check(a) -> int:
    from ohmai.checks import FAIL, HELP, TODO, run
    from ohmai.phases import PHASES

    if a.list:
        print("Phases:")
        for ph in PHASES:
            print(f"  {ph.num:2} {ph.id:13} {', '.join(ph.checks)}")
        print("\nChecks:")
        for k, v in HELP.items():
            print(f"  {k:14} {v}")
        print("  docs:<file>    file exists, template marker removed, no TODO lines")
        print("  review:<phase> the review check for that phase's gate (see ohmai review start)")
        return 0
    p = _project()
    targets = a.targets or ["current"]
    opts = {"fab": a.fab, "against": a.against, "netlist": a.netlist}
    if getattr(a, "sheet", None):
        from ohmai.phases import SHEET_CHECKS, resolve
        from ohmai.sheets import resolve as resolve_sheet

        opts["sheet"] = resolve_sheet(p.schematic, a.sheet)
        new = []
        for t in (a.targets or ["schematic"]):
            ph = resolve(t)
            if ph is not None and ph.id != "schematic":
                raise SystemExit(f"ohmai check --sheet: only the schematic phase has a per-sheet gate (got {t})")
            new += list(SHEET_CHECKS) if ph is not None else [t]
        targets = new
    names = _expand(targets, p)
    # Review checks pulled in by a phase are the gate: reported separately so the work checks can pass and be
    # committed before the review exists. Named explicitly (review:<phase>) or with --gate they count.
    gate_names = {n for n in names if n.startswith("review:") and n not in targets and not a.gate}
    results = [run(p, n, opts) for n in names]
    work = [r for r in results if r.name not in gate_names]
    gate = [r for r in results if r.name in gate_names]
    if a.json:
        print(json.dumps([{**r.as_dict(), "gate": r.name in gate_names} for r in results], indent=2))
    else:
        for r in work:
            _print_result(r, a.max_details)
        if gate:
            print("Gate (not in RESULT; needed before the phase gate, `--gate` to include):")
            for r in gate:
                _print_result(r, a.max_details)
    failed = [r for r in work if r.status == FAIL or (a.strict and r.status == TODO)]
    _write_summary(p, work, failed, gate)
    if not a.json:
        warns = sum(r.status == "WARN" for r in work)
        wtxt = f", {warns} WARN" if warns else ""
        gtxt = "".join(f"; GATE {r.name} {r.status}" for r in gate)
        stxt = f"sheet {opts['sheet'].name}: " if opts.get("sheet") else ""
        print(f"RESULT: {stxt}{'FAIL' if failed else 'PASS'} ({len(work)} checks{wtxt}; summary in out/summary.md){gtxt}")
    from ohmai.attempts import record, retro

    sheet = opts["sheet"].file.relative_to(p.root).as_posix() if opts.get("sheet") else ""
    command = shlex.join(["ohmai", "check", *(a.targets or (["schematic"] if sheet else ["current"]))])
    if sheet:
        command += " --sheet " + shlex.quote(sheet)
    # Preserve verification options in the attempt evidence (including fixture netlists).
    for flag in ("strict", "gate", "json"):
        if getattr(a, flag, False):
            command += " --" + flag
    for flag in ("netlist", "fab", "against"):
        if getattr(a, flag, None):
            command += " --" + flag + " " + shlex.quote(str(getattr(a, flag)))
    try:
        state = record(p.out, failed, sheet, command)
    except ValueError as e:
        print(f"STOP: corrupt attempt history (exit 3): {e}", file=sys.stderr)
        return 3
    if len(state["attempts"]) >= 3:
        # Keep --json stdout a machine-readable result array.
        print(retro(state), file=sys.stderr if a.json else sys.stdout)
        return 3
    return 1 if failed else 0


def _print_result(r, max_details: int) -> None:
    print(f"{STATUS_ICON[r.status]:5} {r.name:14} {r.summary}")
    for d in r.details[:max_details]:
        print(f"        {d}")
    if len(r.details) > max_details:
        print(f"        ... {len(r.details) - max_details} more (use --json)")


def _write_summary(p, results, failed, gate=()) -> None:
    lines = [f"### OhmAI checks: {'FAIL' if failed else 'PASS'}", "", "| Check | Result | Summary |", "|---|---|---|"]
    lines += [f"| `{r.name}` | {r.status} | {r.summary.replace('|', '/')} |" for r in results]
    if gate:
        lines += ["", "Gate checks (not in the result above; required before the phase gate):", "",
                  "| Check | Result | Summary |", "|---|---|---|"]
        lines += [f"| `{r.name}` | {r.status} | {r.summary.replace('|', '/')} |" for r in gate]
    for r in [*results, *gate]:
        if r.details and (r.status in ("FAIL", "WARN") or any("SINGLE-SOURCE COMPONENTS" in d for d in r.details)):
            lines += ["", f"<details><summary>{r.name} ({len(r.details)})</summary>", "", "```"]
            lines += r.details[:100] + ["```", "</details>"]
    (p.out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    allr = [{**r.as_dict(), "gate": False} for r in results] + [{**r.as_dict(), "gate": True} for r in gate]
    (p.out / "summary.json").write_text(json.dumps(allr, indent=2), encoding="utf-8")


def cmd_kicad_libs(a) -> int:
    from ohmai import kicadlibs

    p = _project()
    if a.action in ("import", "fetch", "verify", "list"):
        return _cmd_lib_import(p, a)
    tag = kicadlibs.pinned(p)
    if a.action == "install":
        if not tag:
            raise SystemExit("ohmai kicad-libs: set kicad.libraries in ohmai.yaml first")
        for d in kicadlibs.install(tag):
            print(f"installed {d}")
        return 0
    if a.action == "tables":
        for t in kicadlibs.write_tables(p):
            print(f"wrote {t.relative_to(p.root)}")
        return 0
    if a.action == "models":
        from ohmai.models3d import sync_project_models

        stage = Path(a.stage_pcb) if getattr(a, "stage_pcb", None) else None
        manifest = sync_project_models(p, dummy_missing=not getattr(a, "no_dummy", False), stage_pcb=stage)
        if getattr(a, "json", False):
            print(json.dumps(manifest, indent=2))
            return 0
        print(
            f"3D models synced to {manifest['models_dir']} (tag {manifest['kicad_libraries_tag']}): "
            f"{manifest['total_official_files']} official ({manifest['total_official_bytes']:,} B), "
            f"{manifest['total_flagged_missing']} flagged missing "
            f"({'dummy STEP generated' if not getattr(a, 'no_dummy', False) else 'flagged only'})"
        )
        for item in manifest["fetched_official"]:
            print(f"  OK      {item['footprint']} ({item['count']}x: {', '.join(item['refs'])}) -> {item['model_rel']} ({item['size_bytes']:,} B)")
        for item in manifest["flagged_missing"]:
            dims = item["dimensions_mm"]
            print(
                f"  MISSING {item['footprint']} ({item['count']}x: {', '.join(item['refs'])}) "
                f"[{item['flag']}: {item['reason']}] -> "
                f"dummy {dims['dx']:.2f}x{dims['dy']:.2f}x{dims['dz']:.2f} mm ({item['dummy_model_rel']})"
            )
        if stage:
            print(f"staged PCB with injected dummy model refs -> {stage}")
        return 0
    st, summary, details = kicadlibs.status(p)
    print(f"{st:5} {summary}")
    for d in details:
        print(f"        {d}")
    return 0 if st != "FAIL" else 1


def _cmd_lib_import(p, a) -> int:
    from ohmai import libimport as li

    if a.action == "list":
        parts = li.load_manifest(p)["parts"]
        if not parts:
            print("no project-local parts (hardware/lib/manifest.yaml)")
        for mpn, e in parts.items():
            sym = (e.get("symbol") or {}).get("lib_id", "-")
            v = e.get("verification") or {}
            ver = f"{len(v.get('mismatches') or [])} mismatch(es)" if v else "no datasheet check"
            print(f"{e.get('status', '?'):16} {mpn:20} {sym:28} {', '.join(e.get('footprints') or []) or '-'}  [{ver}]")
        return 0
    if not a.mpn:
        raise SystemExit(f"ohmai kicad-libs {a.action}: MPN required")
    if a.action == "fetch":
        try:
            e = li.fetch(p, a.mpn, source=a.source_kind, lcsc=a.lcsc, url=a.url, vendor=a.vendor, base=a.base,
                         pins_count=a.pins, package=a.package, lib=a.lib, symbol=a.symbol, name=a.name,
                         footprint=a.footprint, datasheet=Path(a.datasheet) if a.datasheet else None,
                         pages=a.pages, replace=a.replace, note=a.note, pin_types=a.pin_types)
        except li.FetchBlocked as b:
            print(f"RETRY {a.mpn}: {b.msg}", file=sys.stderr)
            return li.EX_RETRY
        return _report_import(a.mpn, e)
    if a.action == "verify":
        if not a.datasheet:
            raise SystemExit("ohmai kicad-libs verify: --datasheet PDF required")
        errs = li.verify_part(p, a.mpn, Path(a.datasheet), a.pages)
        print(("FAIL " if errs else "PASS ") + f"{a.mpn}: {len(errs)} pin mismatch(es) vs {a.datasheet}")
        for e in errs:
            print(f"        {e}")
        return 1 if errs else 0
    if not a.source:
        raise SystemExit("ohmai kicad-libs import: --from <vetted .zip | dir | .kicad_sym | .kicad_mod> required "
                         f"(no file? `ohmai kicad-libs fetch {a.mpn}` downloads it)")
    e = li.import_part(p, a.mpn, Path(a.source), package=a.package, source_url=a.source_url, lib=a.lib,
                       symbol=a.symbol, name=a.name, footprint=a.footprint,
                       datasheet=Path(a.datasheet) if a.datasheet else None, pages=a.pages,
                       status=a.status, replace=a.replace, note=a.note, pin_types=a.pin_types)
    return _report_import(a.mpn, e)


def _report_import(mpn, e) -> int:
    print(f"{e['status']:9} {mpn}: symbol {(e.get('symbol') or {}).get('lib_id', '-')}, "
          f"footprints {', '.join(e.get('footprints') or []) or '-'} -> hardware/lib/ (manifest.yaml)")
    v = e.get("verification")
    if not v:
        print("        WARN no datasheet pin check (pass --datasheet, or put the PDF in datasheets/)")
    for m in (v or {}).get("mismatches") or []:
        print(f"        {m}")
    for fp in e.get("footprint_check") or []:
        print(f"        footprint {fp['file']}: {fp['pads']} pads, largest {fp['largest_pad_mm']} mm "
              "(compare with the datasheet land pattern)")
    for w in e.get("warnings") or []:
        print(f"        WARN {w}")
    print("next: ohmai check lib-refs")
    return 1 if (e.get("verification") or {}).get("mismatches") else 0


def cmd_schematic(a) -> int:
    from ohmai import kicadlibs
    from ohmai.schgen import build

    p = _project()
    kicadlibs.activate(p)
    for f in build(p, Path(a.layout) if a.layout else None):
        print(f"wrote {f.relative_to(p.root)}")
    return 0


def _repo_root() -> Path | None:
    """Project root if inside an OhmAI project, else the git work tree root (e.g. the OhmAI repo itself)."""
    from ohmai import trailers as tr
    from ohmai.project import NotAProject, find_project

    try:
        return find_project().root
    except NotAProject:
        r = tr._git(Path.cwd(), "rev-parse", "--show-toplevel")
        return Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None


def cmd_hooks(a) -> int:
    from ohmai import trailers as tr

    root = _repo_root()
    if root is None or not tr.is_repo(root):
        print("ohmai hooks: not a git repository", file=sys.stderr)
        return 1
    if a.action == "install":
        try:
            print(f"installed {tr.install_hook(root, force=a.force)} (rejects commits without an "
                  f"{tr.KEY} trailer)")
        except FileExistsError as e:
            print(f"ohmai hooks: {e}", file=sys.stderr)
            return 1
        return 0
    ok, msg = tr.hook_status(root)
    print(("PASS  " if ok else "FAIL  ") + msg)
    return 0 if ok else 1


def cmd_doctor(a) -> int:
    from ohmai import doctor

    items = doctor.run(Path.cwd())
    for it in items:
        print(f"{it.status:<5} {it.name:<14} {it.message}")
    worst = {s.status for s in items}
    if "FAIL" in worst or (a.strict and "WARN" in worst):
        return 1
    return 0


def cmd_setup(a) -> int:
    """Install this release's pinned tools with no prompts, then re-check them."""
    from ohmai import pins

    notices, errors = pins.install_all(download=not a.check)
    for line in notices:
        print(line)
    for err in errors:
        print(err, file=sys.stderr)
    items = pins.check_all(Path.cwd())
    for it in items:
        print(f"{it.status:<5} {it.name:<14} {it.message}")
    if errors or any(it.status == "FAIL" for it in items):
        return 1
    return 0


def cmd_commit(a) -> int:
    """git commit with the OhmAI-Author trailer from ohmai.yaml `author:` / OHMAI_AUTHOR_* appended."""
    import subprocess

    from ohmai import trailers as tr

    p = _project()
    msg = "\n\n".join(a.message)
    if not tr.message_trailers(msg):
        ident = tr.configured_identity(p.config)
        value = tr.format_trailer(ident)
        probs = tr.validate(value) if value else ["no identity configured"]
        if probs:
            print(f"ohmai commit: cannot build the {tr.KEY} trailer: {'; '.join(probs)}. Set ohmai.yaml "
                  f"`author:` ({', '.join(tr.FIELDS)}) or {', '.join(tr.ENV.values())}", file=sys.stderr)
            return 1
        msg = msg.rstrip() + f"\n\n{tr.KEY}: {value}\n"
    else:
        probs = tr.check_message(msg)
        if probs:
            print("ohmai commit: " + "; ".join(probs), file=sys.stderr)
            return 1
    cmd = ["git", "-C", str(p.root), "commit", "-F", "-", *a.git_args]
    if a.all:
        cmd.insert(4, "-a")
    return subprocess.run(cmd, input=msg, text=True).returncode


def cmd_release(a) -> int:
    from ohmai.release import release

    return release(_project(), a.rev, dry_run=a.dry_run, tag=a.tag, force=a.force)


def cmd_review(a) -> int:
    from ohmai.checks.review import check_review
    from ohmai.review import phase_id, start

    p = _project()
    if a.action == "start":
        path = start(p, a.phase)
        print(f"Wrote {path.relative_to(p.root)} (and resolutions.md if missing).")
        pk = p.root / "out" / "review-pack" / phase_id(a.phase)
        print(f"Review pack: {pk.relative_to(p.root)}/ (MANIFEST.md, BRIEF, resolutions, every files_hash input"
              + (", renders under visuals/" if phase_id(a.phase) in ("schematic", "layout", "fab") else "") + ").")
        print("Give the whole pack to the reviewer (a human must be handed these files, not a repo path).")
        print("Give the brief to an independent reviewer (highest feasible level: other vendor > other model >")
        print("other agent/instructions > human; never the author's own session). Commit the brief now.")
        return 0
    if a.action == "pack":
        from ohmai.review import pack
        out = pack(p, phase_id(a.phase))
        print(f"Wrote {out.relative_to(p.root)}/ (see MANIFEST.md).")
        return 0
    r = check_review(p, a.phase)
    print(f"{STATUS_ICON[r.status]:5} {r.name:14} {r.summary}")
    for d in r.details:
        print(f"        {d}")
    return 1 if r.status == "FAIL" else 0


def cmd_skill(a) -> int:
    from ohmai.resources import skill_dir

    src = skill_dir()
    if a.action == "path":
        print(src)
        return 0
    src = src.resolve()
    raw_dest = Path(a.dest).expanduser()
    if raw_dest.is_symlink():
        print("ohmai skill install: destination must not be a symlink", file=sys.stderr)
        return 1
    dest = raw_dest.resolve()
    if dest == src or src in dest.parents or dest in src.parents:
        print("ohmai skill install: destination overlaps the canonical skill", file=sys.stderr)
        return 1
    if dest.exists():
        if not a.force:
            print(f"ohmai skill install: {dest} exists (use --force to replace)", file=sys.stderr)
            return 1
        from datetime import datetime
        from time import time_ns

        backup = dest.with_name(f"{dest.name}.bak.{datetime.now():%Y%m%d-%H%M%S}.{time_ns()}")
        dest.rename(backup)
        print(f"Previous skill backed up to {backup}")
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("*.bak*", "__pycache__"))
    print(f"Installed OhmAI skill {__version__} to {dest}")
    return 0


def cmd_upgrade_guides(a) -> int:
    from ohmai.upgrade import upgrade_guides

    log = upgrade_guides(_project(), dry_run=a.dry_run, add_missing=a.add_missing)
    for line in log:
        print(f"  {line}")
    if a.dry_run:
        print("(dry run: nothing changed)")
    else:
        print("Review the guide diff (git diff .ohmai/) and commit it with the phase work.")
    return 0


def cmd_lock(a) -> int:
    from ohmai import toolchain as tc

    p = _project()
    if a.constraints:
        lock = tc.load_lock(p)
        if lock is None:
            print(f"ohmai lock: no {tc.LOCK_NAME}", file=sys.stderr)
            return 1
        for name, e in (lock.get("tools") or {}).items():
            if name.startswith(tc.PY_PREFIX) and isinstance(e, dict) and e.get("version"):
                print(f"{name[len(tc.PY_PREFIX):]}=={e['version']}")
        return 0
    data, changes, problems = tc.make_lock(p)
    exists = tc.lock_path(p).is_file()
    for name, e in data["tools"].items():
        v = e.get("version") if e["tier"] == 1 else f"{e.get('seen')} (range {e.get('range')})"
        print(f"  tier {e['tier']}  {name:22} {v if v is not None else '- (not used)'}")
    for prob in problems:
        print(f"BLOCK {prob}", file=sys.stderr)
    if changes:
        print(f"ohmai lock: installed tier-1 tools differ from {tc.LOCK_NAME}:", file=sys.stderr)
        for c in changes:
            print(f"  {c}", file=sys.stderr)
        print("Tier-1 pins move only through `ohmai upgrade <tool> <version>` (golden + known-bad suites first).",
              file=sys.stderr)
        return 1
    if problems:
        return 1
    if a.check:
        print(f"(--check: {tc.LOCK_NAME} {'up to date for tier 1' if exists else 'would be created'}; not written)")
        return 0
    path = tc.write_lock(p, data)
    print(f"{'updated' if exists else 'wrote'} {path.relative_to(p.root)}; commit it (ohmai commit -m 'toolchain lock' "
          f"-- {path.name})")
    return 0


def cmd_known_bad(a) -> int:
    import tempfile

    from ohmai import knownbad as kb

    with tempfile.TemporaryDirectory(prefix="ohmai-known-bad-") as td:
        outs = kb.run_all(Path(a.work) if a.work else Path(td), a.cases or None)
    if a.json:
        print(json.dumps([o.as_dict() for o in outs], indent=2))
    else:
        print("\n".join(kb.table(outs)))
        for name, why in kb.NOT_COVERED.items():
            print(f"| - | `{name}` | no planted bug: {why} | - | - | n/a |")
    bad = [o for o in outs if not o.caught and not o.skipped]
    skipped = [o for o in outs if o.skipped]
    if not a.json:
        print(f"RESULT: {'FAIL' if bad else 'PASS'} ({len(outs) - len(bad) - len(skipped)}/{len(outs)} caught, "
              f"{len(skipped)} skipped, {len(bad)} not caught or broken)")
    return 1 if bad else 0


def cmd_upgrade(a) -> int:
    from ohmai import toolupgrade as tu

    out = tu.upgrade(_project(), a.tool, a.version, explain=a.explain or "", path=a.path, install=a.install,
                     commit=not a.no_commit, dry_run=a.dry_run)
    print("\n".join(out.lines), file=sys.stdout if out.ok else sys.stderr)
    return 0 if out.ok else 1


def cmd_parts(a) -> int:
    from ohmai import partsearch as ps

    rows, meta = ps.search(a.terms, db=a.db, category=a.category, package=a.package, basic=a.basic,
                           min_stock=a.min_stock, limit=a.limit, qty=a.qty)
    if a.json:
        print(json.dumps({"meta": meta, "results": rows}, indent=2))
        return 0
    print(f"# {meta['note']}\n# db: {meta['db']} (file date {meta['db_mtime']}); {len(rows)} hit(s)")
    key = f"unit_price_qty{a.qty}"
    for r in rows:
        price = f"{r[key]:.4f}" if isinstance(r[key], (int, float)) else "-"
        print(f"{r['lcsc']:>9}  {r['mpn'][:28]:28} {r['manufacturer'][:18]:18} {r['package'][:14]:14} "
              f"{'basic' if r['basic'] else 'ext':5} stock {r['stock']:>8} @{a.qty}: {price} ({r['snapshot_date']})")
        print(f"           {r['category']}: {r['description'][:90]}\n           {r['url']}")
    return 0


def cmd_golden(a) -> int:
    from ohmai import golden as gd

    runs = [gd.run_golden(g) for g in gd.load_goldens(a.names or None)]
    if not runs:
        print(f"ohmai golden: no golden projects in {gd.golden_root()}", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps([{"golden": r.golden.name, "status": r.status, "diffs": r.diffs, "skipped": r.skipped,
                           "error": r.error, "notes": r.notes, "toolchain": r.toolchain} for r in runs], indent=2))
    for r in runs:
        if not a.json:
            extra = r.skipped or r.error or (", ".join(sorted(r.diffs)) + " differ" if r.diffs else "matches snapshots")
            print(f"{r.status:5} {r.golden.name:14} {extra}")
            for n in r.notes:
                print(f"        note: {n}")
            for name, d in sorted(r.diffs.items()):
                print(d.rstrip("\n") if a.diff else f"        {name}: {len(d.splitlines())} diff line(s) (--diff)")
        if a.accept and r.status in ("DIFF", "PASS") and r.outputs:
            changed = gd.accept(r, a.reason or "", a.by or "")
            print(f"        accepted ({', '.join(changed) or 'no changes'}): snapshots + MANIFEST.yaml written; "
                  "commit them on their own")
    bad = [r for r in runs if r.status == "ERROR" or (r.status == "DIFF" and not a.accept)]
    return 1 if bad else 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ohmai", description="OhmAI: idea to finished PCB in KiCad, with human gates.")
    ap.add_argument("--version", action="version", version=f"ohmai {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("new", help="create a project repo from the template")
    s.add_argument("path", help="new project directory")
    s.add_argument("--idea", required=True, help="the idea, in the owner's words (goes into STATUS.md)")
    s.add_argument("--name", help="project name (default: directory name)")
    s.add_argument("--anonymous", action="store_true",
                   help="enable anonymous mode in ohmai.yaml (role labels, repo-relative paths, YYYY-MM-DD dates)")
    s.add_argument("--no-git", action="store_true", help="do not git init / commit")
    s.set_defaults(fn=cmd_new)

    s = sub.add_parser("adopt", help="put an existing KiCad repo under OhmAI checks (ohmai.yaml with N/A reasons, "
                                     "expected_nets.csv baseline from the current netlist); never overwrites")
    s.add_argument("path", nargs="?", default=".", help="repo root (default: .)")
    s.add_argument("--schematic", help="root .kicad_sch, relative to the repo (default: the one next to the .kicad_pro)")
    s.add_argument("--na", action="append", metavar="CHECK=REASON", help="mark a check N/A with a reason (repeatable)")
    s.add_argument("--netlist", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_adopt)

    s = sub.add_parser("status", help="show phase, gate, decisions, open questions from STATUS.md")
    s.add_argument("--json", action="store_true")
    s.add_argument("--set-phase", metavar="N|ID", help="set the front-matter phase (1-12 or a phase id)")
    s.add_argument("--gate", choices=["open", "in_review", "approved"], help="set the front-matter gate")
    s.add_argument("--decision", metavar="D-xxx", help="with --gate approved: the logged owner decision")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("check", help="run the checks for a phase (or single checks)",
                       description="Targets: a phase (schematic, 7), a check (erc, netlist, ...), 'current' "
                                   "(phase in STATUS.md, default) or 'all'. Exit codes: 0 pass, 1 fail, "
                                   "3 stop after third identical failure signature or corrupt attempt history. "
                                   "History: out/check-attempts.json; "
                                   "PASS or a changed signature clears valid history; preserve and repair corrupt history. "
                                   "Stop outranks further redraw.")
    s.add_argument("targets", nargs="*")
    s.add_argument("--list", action="store_true", help="list phases and checks")
    s.add_argument("--json", action="store_true")
    s.add_argument("--strict", action="store_true", help="treat TODO (unimplemented) checks as failures")
    s.add_argument("--fab", help="bom-diff: the fab's actual-purchase CSV")
    s.add_argument("--against", help="bom-diff: git ref to diff bom.csv against (e.g. main)")
    s.add_argument("--netlist", help="use this kicadsexpr netlist instead of exporting one with kicad-cli")
    s.add_argument("--max-details", type=int, default=15)
    s.add_argument("--gate", action="store_true", help="count the phase's review gate check in RESULT")
    s.add_argument("--sheet", help="per-sheet gate: with `schematic`, run load, ERC, netlist, generator intent, wires, "
                                   "pin map, passives, readability and render for this sheet only (name, file or stem)")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("kicad-libs", help="pinned official KiCad libraries: install the tag, write project lib tables, or fetch project 3D models",
                       description="Uses kicad.libraries from ohmai.yaml; symbol/footprint libs go to $OHMAI_KICAD_LIBS "
                                   "(default ~/.cache/ohmai/kicad-libs); 3D .step models are fetched per project into out/3dmodels/.")
    s.add_argument("action", choices=["install", "tables", "status", "models", "import", "fetch", "verify", "list"],
                   help="import/fetch/verify/list: vetted project-local parts the pinned libraries lack "
                        "(hardware/lib/, manifest.yaml; see skill/phases/04-parts.md)")
    s.add_argument("mpn", nargs="?", help="import/fetch/verify: manufacturer part number")
    s.add_argument("--from", dest="source", help="import: vetted UL/manufacturer .zip, directory, .kicad_sym or .kicad_mod")
    s.add_argument("--source-url", help="import: where the vetted file was downloaded (required)")
    s.add_argument("--package", help="import/fetch: package code, e.g. RSN")
    s.add_argument("--lib", default="OhmAI_Project", help="import: project library name (default OhmAI_Project)")
    s.add_argument("--symbol", help="import: symbol to take when the source has several")
    s.add_argument("--name", help="import: name of the symbol in the project library (default: source name)")
    s.add_argument("--footprint", help="import: Footprint field for the symbol, e.g. Package_DFN_QFN:... (stock KiCad)")
    s.add_argument("--datasheet", help="import/verify: datasheet PDF; every symbol pin number+name must appear on one line")
    s.add_argument("--pages", help="import/verify: datasheet pin-table pages, e.g. 6-8")
    s.add_argument("--status", choices=["provisional"], help="import: mark a stopgap source provisional")
    s.add_argument("--replace", action="store_true", help="import: replace an existing symbol/footprint (backed up)")
    s.add_argument("--note", help="import: free-text provenance note")
    s.add_argument("--pin-types", dest="pin_types", action="store_const", const=True, default=False,
                   help="import/fetch: fill 'unspecified' pin types from the datasheet I/O column (needs --datasheet "
                        "or a PDF in datasheets/); logged in manifest.yaml")
    s.add_argument("--source", dest="source_kind", default="auto", choices=["auto", "easyeda", "url", "portal"],
                   help="fetch: auto (= --url if given, else LCSC/EasyEDA), easyeda, url, portal (UL/vendor probe; "
                        "walls exit 75 = retry another source)")
    s.add_argument("--lcsc", help="fetch: LCSC number (default: bom/bom.csv supplier_pn for the MPN)")
    s.add_argument("--url", help="fetch: direct manufacturer/mirror CAD file URL (.zip/.kicad_sym/.kicad_mod)")
    s.add_argument("--vendor", default="ul", choices=["ti", "ul", "analog", "st", "microchip", "nxp", "snapeda"],
                   help="fetch --source portal: which portal to probe")
    s.add_argument("--base", help="fetch --source portal: base part number (e.g. TPS63001)")
    s.add_argument("--pins", help="fetch --source portal: pin count")
    s.add_argument("--no-dummy", action="store_true", help="models: flag missing 3D models without generating dummy .step blocks")
    s.add_argument("--stage-pcb", help="models: write a temporary .kicad_pcb with (model ...) entries injected for footprints lacking one")
    s.add_argument("--json", action="store_true", help="models: print manifest JSON")
    s.set_defaults(fn=cmd_kicad_libs)

    s = sub.add_parser("lock", help="write toolchain.lock.yaml: exact tier-1 tool versions + hashes, tier-2 ranges",
                       description="First run creates the lock. Later runs refresh tier-2 entries and add new "
                                   "tools as absent; a changed tier-1 tool is refused (use `ohmai upgrade`).")
    s.add_argument("--check", action="store_true", help="report only, do not write")
    s.add_argument("--constraints", action="store_true",
                   help="print the locked Python packages as pip constraints (pkg==version) and exit")
    s.set_defaults(fn=cmd_lock)

    s = sub.add_parser("upgrade", help="move one tier-1 toolchain pin after the known-bad + golden suites pass",
                       description="Runs the known-bad and golden suites with the candidate selected, shows the diffs, "
                                   "and updates + commits the lock only when they pass or --explain covers the "
                                   "diffs. Tier-2 helpers: edit their range in the lock.")
    s.add_argument("tool", help="tier-1 tool name as in the lock (kicad-app, kicad-libs, freerouting, python:PyYAML, ...)")
    s.add_argument("version")
    s.add_argument("--path", help="candidate binary/dir/file (sets KICAD_CLI, FREEROUTING_JAR, OHMAI_IBOM_DIR or "
                                  "OHMAI_JLCPARTS_DB for the run)")
    s.add_argument("--install", action="store_true", help="kicad-libs: download the candidate tag first")
    s.add_argument("--explain", help="why the golden diffs (or missing suite coverage) are fine; logged")
    s.add_argument("--dry-run", action="store_true", help="run the suites, change nothing")
    s.add_argument("--no-commit", action="store_true", help="update the lock but do not commit")
    s.set_defaults(fn=cmd_upgrade)

    s = sub.add_parser("parts", help="parts helpers (phase 4): `parts search` in an optional local jlcparts DB")
    psub = s.add_subparsers(dest="parts_cmd", required=True)
    q = psub.add_parser("search", help="search a jlcparts cache.sqlite3 (OHMAI_JLCPARTS_DB); candidates only, "
                                       "copy live price/stock from the printed LCSC page")
    q.add_argument("terms", nargs="*", help="words that must all match (MPN, description, package, category, maker)")
    q.add_argument("--db", help="jlcparts cache.sqlite3 (default $OHMAI_JLCPARTS_DB)")
    q.add_argument("--category")
    q.add_argument("--package")
    q.add_argument("--basic", action="store_true", help="JLC basic parts only")
    q.add_argument("--min-stock", type=int, default=0)
    q.add_argument("--qty", type=int, default=1, help="quantity for the unit price column")
    q.add_argument("--limit", type=int, default=20)
    q.add_argument("--json", action="store_true")
    q.set_defaults(fn=cmd_parts)

    from ohmai.datasheets import add_parser as _datasheets_parser
    from ohmai.layout.cli import add_parser as _layout_parser
    from ohmai.passives import add_parser as _passives_parser
    from ohmai.passives import add_swap_parser as _parts_swap_parser
    from ohmai.tool_report import add_parser as _report_parser

    _parts_swap_parser(psub)
    _passives_parser(sub)
    _datasheets_parser(sub)
    _layout_parser(sub)
    _report_parser(sub)

    s = sub.add_parser("golden", help="rebuild golden reference projects and diff netlist/ERC/BOM/symbols/checks "
                                      "against approved snapshots",
                       description="Golden projects live in tests/golden/<name>/ (or $OHMAI_GOLDEN_DIR). A difference "
                                   "fails until it is approved with --accept --reason.")
    s.add_argument("names", nargs="*", help="golden projects (default: all)")
    s.add_argument("--diff", action="store_true", help="print the unified diffs")
    s.add_argument("--accept", action="store_true", help="write this run's outputs as the new approved snapshots")
    s.add_argument("--reason", help="why the new outputs are correct (required with --accept; logged)")
    s.add_argument("--by", help="who approves (default $OHMAI_AUTHOR_NAME)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_golden)

    s = sub.add_parser("known-bad", help="mutation suite: every check must FAIL on its planted bug, PASS on the twin",
                       description="Runs tests/fixtures/known-bad/<case>/ (see ohmai/knownbad.py). Cases whose "
                                   "tools are missing (kicad-cli, git, pinned libraries) are skipped.")
    s.add_argument("cases", nargs="*", help="case names (default: all)")
    s.add_argument("--json", action="store_true")
    s.add_argument("--work", help="keep the twin projects in this directory (default: a temp dir)")
    s.set_defaults(fn=cmd_known_bad)

    s = sub.add_parser("schematic", help="draw the hierarchical schematic from hardware/gen/layout/*.yaml "
                                         "(datasheet-figure style, pinned official symbols)")
    s.add_argument("action", choices=["build"])
    s.add_argument("--layout", help="layout directory (default hardware/gen/layout)")
    s.set_defaults(fn=cmd_schematic)

    s = sub.add_parser("hooks", help="git hooks: install the commit-msg hook that requires OhmAI-Author trailers")
    s.add_argument("action", choices=["install", "status"])
    s.add_argument("--force", action="store_true", help="replace a commit-msg hook that is not OhmAI's")
    s.set_defaults(fn=cmd_hooks)

    s = sub.add_parser("doctor", help="environment self-check: git repo, hooks, and this release's pinned tools",
                       description="Warns about set-up problems that silently weaken the workflow, e.g. a missing "
                                   "commit-msg hook (git does not version hooks, so a fresh clone has none). "
                                   "Compares kicad-cli, Freerouting, JDK and easyeda2kicad with pinned-tools.yaml; "
                                   "a mismatch is FAIL unless OHMAI_ALLOW_UNPINNED=1. Fix with `ohmai setup`.")
    s.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("setup", help="install this release's pinned tools (no prompts) and verify checksums",
                       description="Fetches the pinned Freerouting jar (sha256) and easyeda2kicad (PyPI hashes), "
                                   "and checks KiCad and JDK 25. Prints one line per tool. No questions. "
                                   "Does not touch a shared Freerouting config. Override checks with "
                                   "OHMAI_ALLOW_UNPINNED=1.")
    s.add_argument("--check", action="store_true", help="verify only; do not download or pip install")
    s.set_defaults(fn=cmd_setup)

    s = sub.add_parser("commit", help="git commit with the OhmAI-Author trailer appended "
                                      "(identity from ohmai.yaml `author:` or OHMAI_AUTHOR_* env)",
                       description="Extra git commit arguments go after `--`, e.g. "
                                   "`ohmai commit -m 'msg' -- path/a path/b`.")
    s.add_argument("-m", "--message", action="append", required=True, help="message paragraph (repeatable)")
    s.add_argument("-a", "--all", action="store_true", help="git commit -a")
    s.add_argument("git_args", nargs="*", help="passed to git commit (after --)")
    s.set_defaults(fn=cmd_commit)

    s = sub.add_parser("release", help="preflight + fab outputs for a revision (rev-A ...); never pushes")
    s.add_argument("rev")
    s.add_argument("--dry-run", action="store_true", help="preview outputs even if preflight blocks")
    s.add_argument("--tag", action="store_true", help="create the annotated git tag locally")
    s.add_argument("--force", action="store_true", help="ignore preflight blocks (owner override; log it)")
    s.set_defaults(fn=cmd_release)

    s = sub.add_parser("upgrade-guides", help="re-pin .ohmai/phases/ to this OhmAI version (local files only)")
    s.add_argument("--dry-run", action="store_true", help="list what would change")
    s.add_argument("--add-missing", action="store_true", help="also copy template docs/*.md the project lacks")
    s.set_defaults(fn=cmd_upgrade_guides)

    s = sub.add_parser("review", help="independent review gate: write the reviewer brief, or check reviews",
                       description="start: write reviews/<phase>/BRIEF.md (scope, sources, decisions, checklist, "
                                   "output format, content hash). check: same as `ohmai check review:<phase>`.")
    s.add_argument("action", choices=["start", "check", "pack"])
    s.add_argument("phase", help="phase id or number, e.g. constraints or 5")
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("skill", help="print or install the agent skill")
    s.add_argument("action", choices=["path", "install"])
    s.add_argument("--dest", default="~/.cursor/skills/ohmai", help="install target (default %(default)s)")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_skill)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    raise SystemExit(main())

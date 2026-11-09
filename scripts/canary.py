#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Toolchain canary: run the golden + known-bad suites against the newest KiCad / KiCad libraries.

Reports breakage early; never moves a pin. It writes no lock, no snapshot, no ohmai.yaml: candidates are
selected for the run only (KICAD_CLI, OHMAI_KICAD_LIBS_TAG), libraries are downloaded into the libs cache.
Moving a pin stays `ohmai upgrade <tool> <version>` in a project, with the owner's approval.

    scripts/canary.py                      # kicad-cli on PATH + newest stable libs of its major.minor
    scripts/canary.py --libs latest        # newest stable libs tag overall
    scripts/canary.py --libs 9.0.10 --kicad-cli /opt/kicad-9.0.6/bin/kicad-cli
    scripts/canary.py --report canary.md   # also write the markdown report ($GITHUB_STEP_SUMMARY is appended)

Exit: 0 = suites clean on the candidates, 1 = breakage (see report), 2 = setup problem.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

TAGS = "https://gitlab.com/api/v4/projects/kicad%2Flibraries%2Fkicad-symbols/repository/tags?per_page=100&page={page}"
STABLE = re.compile(r"^\d+(\.\d+)+$")
MAX_DIFF_LINES = 60


def vt(tag: str) -> tuple[int, ...]:
    return tuple(int(x) for x in tag.split("."))


def stable_tags(fetch=None) -> list[str]:
    """All stable (no -rc, no backport) kicad-symbols tags, newest first."""
    fetch = fetch or (lambda url: json.load(urllib.request.urlopen(url, timeout=60)))  # noqa: S310 (fixed https host)
    tags: list[str] = []
    for page in range(1, 6):
        rows = fetch(TAGS.format(page=page))
        if not rows:
            break
        tags += [r["name"] for r in rows if STABLE.match(r["name"])]
    return sorted(set(tags), key=vt, reverse=True)


def pick_libs(want: str, app_version: str | None, tags: list[str]) -> str:
    if want not in ("match-app", "latest"):
        return want
    if want == "latest":
        if not tags:
            raise SystemExit("canary: no stable library tags found")
        return tags[0]
    if not app_version:
        raise SystemExit("canary: --libs match-app needs kicad-cli (or pass --libs TAG)")
    series = tuple(int(x) for x in re.findall(r"\d+", app_version)[:2])
    same = [t for t in tags if vt(t)[:2] == series]
    if not same:
        raise SystemExit(f"canary: no stable library tag for KiCad {'.'.join(map(str, series))}")
    return same[0]


def pinned() -> dict:
    """What the project template and the golden snapshots are pinned to (for the report)."""
    from ohmai import golden as gd
    from ohmai.resources import template_dir

    tpl = (template_dir() / "ohmai.yaml").read_text(encoding="utf-8")
    m = re.search(r'^\s+libraries:\s*"?([^"\s#]+)', tpl, re.M)
    a = re.search(r'^\s+app:\s*"?([^"#\n]+?)"?\s*(#|$)', tpl, re.M)
    snaps = {g.name: (gd.manifest(g).get("toolchain") or {}) for g in gd.load_goldens()}
    return {"template kicad.libraries": m.group(1) if m else None, "template kicad.app": a.group(1) if a else None,
            **{f"golden {k} snapshot": f"kicad-cli {v.get('kicad-cli')}, libs {v.get('kicad-libs')}"
               for k, v in snaps.items()}}


def report(cand: dict, pins: dict, s, verdict: str) -> str:
    L = [f"# OhmAI toolchain canary: {verdict}", "", "| | version |", "|---|---|"]
    L += [f"| candidate {k} | {v} |" for k, v in cand.items()]
    L += [f"| pinned: {k} | {v} |" for k, v in pins.items()]
    L += ["", f"Suites: {s.summary()}", ""]
    for e in s.errors + s.golden_errors():
        L.append(f"- ERROR {e}")
    for m in s.misses():
        L.append(f"- known-bad NOT CAUGHT: {m}")
    for g, diffs in s.diffs().items():
        for art, d in sorted(diffs.items()):
            lines = d.rstrip().splitlines()
            more = f"\n... ({len(lines) - MAX_DIFF_LINES} more lines)" if len(lines) > MAX_DIFF_LINES else ""
            L += [f"### golden {g}: {art}", "```diff", "\n".join(lines[:MAX_DIFF_LINES]) + more, "```"]
    L += ["", "Pins were not moved. To adopt a candidate in a project: `ohmai upgrade kicad-app|kicad-libs <version>` "
          "(runs these suites again and needs the owner's approval for any diff)."]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--libs", default="match-app", help="match-app (default), latest, or a tag")
    ap.add_argument("--kicad-cli", help="candidate kicad-cli (default: KICAD_CLI or PATH)")
    ap.add_argument("--report", help="write the markdown report here")
    ap.add_argument("--no-install", action="store_true", help="do not download the libraries tag")
    a = ap.parse_args(argv)

    from ohmai import kicad, kicadlibs
    from ohmai.toolupgrade import run_suites

    env: dict[str, str] = {}
    if a.kicad_cli:
        env["KICAD_CLI"] = os.environ["KICAD_CLI"] = str(Path(a.kicad_cli).resolve())
    app = kicad.version()
    try:
        tags = [] if a.libs not in ("match-app", "latest") else stable_tags()
        tag = pick_libs(a.libs, app, tags)
    except (OSError, ValueError) as e:
        print(f"canary: cannot list library tags: {e}", file=sys.stderr)
        return 2
    if not a.no_install:
        try:
            kicadlibs.install(tag)
        except OSError as e:
            print(f"canary: cannot install libraries {tag}: {e}", file=sys.stderr)
            return 2
    env[kicadlibs.TAG_ENV] = tag
    cand = {"kicad-cli": app or "(not found)", "kicad-libs": tag,
            "newest stable libs tag": tags[0] if tags else "(not queried)"}
    s = run_suites(env)
    broken = bool(s.errors or s.golden_errors() or s.misses() or s.diffs())
    skipped = not s.ran("golden")
    verdict = "BREAKAGE" if broken else ("NOT RUN (golden skipped)" if skipped else "clean")
    text = report(cand, pinned(), s, verdict)
    print(text)
    if a.report:
        Path(a.report).write_text(text, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)
    return 1 if broken else (2 if skipped else 0)


if __name__ == "__main__":
    sys.exit(main())

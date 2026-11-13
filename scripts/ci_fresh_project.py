#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""CI: kicad-cli checks on a fresh `ohmai new` project (run inside it; needs kicad-cli).

The template's root sheet is empty on purpose. sheet-load and erc FAIL on 0 placed symbols by design
(an empty or unparsed sheet is not a pass), so this asserts the right thing instead of PASS:
  * every sheet loads in this kicad-cli (out/load/result.json: loaded, nothing missing),
  * sheet-load and erc FAIL only for the empty-sheet reason,
  * render PASSes.
A populated schematic (the ldo-led golden) runs sheet-load and erc to PASS in the next CI step.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

EMPTY = "0 placed symbols"


def check(*targets: str) -> tuple[int, list[dict]]:
    proc = subprocess.run(["ohmai", "check", *targets, "--json"], capture_output=True, text=True)
    sys.stderr.write(proc.stderr)
    try:
        return proc.returncode, json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.exit(f"ohmai check {' '.join(targets)}: no JSON (rc {proc.returncode}):\n{proc.stdout}")


def main() -> int:
    problems: list[str] = []
    _, results = check("sheet-load", "erc", "render")
    by_name = {r["name"]: r for r in results}
    for r in results:
        print(f"{r['status']:5} {r['name']:14} {r['summary']}")
        for d in r.get("details") or []:
            print(f"        {d}")

    load_json = Path("out/load/result.json")
    load = json.loads(load_json.read_text()) if load_json.is_file() else {}
    sheets = load.get("sheets") or {}
    if not sheets:
        problems.append(f"{load_json}: no sheets recorded")
    for name, s in sheets.items():
        if not s.get("loaded"):
            problems.append(f"{name}: kicad-cli did not load it")
        if s.get("symbols"):
            problems.append(f"{name}: {s['symbols']} symbol(s) in the fresh template (expected an empty sheet)")
    if load.get("missing"):
        problems.append(f"missing sheet(s): {load['missing']}")

    for name in ("sheet-load", "erc"):
        r = by_name.get(name)
        if r is None:
            problems.append(f"{name}: not run")
        elif r["status"] != "FAIL" or not any(EMPTY in d for d in r.get("details") or []):
            problems.append(f"{name}: expected FAIL for '{EMPTY}' only, got {r['status']}: {r.get('details')}")
        elif name == "sheet-load" and len(r.get("details") or []) != 1:
            problems.append(f"sheet-load: unexpected extra problem(s): {r['details']}")
    r = by_name.get("render")
    if r is None or r["status"] != "PASS":
        problems.append(f"render: expected PASS, got {r and r['status']}")

    for p in problems:
        print(f"ERROR {p}")
    print("fresh template: OK (sheets load; empty-sheet FAIL as designed; render PASS)" if not problems
          else f"fresh template: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

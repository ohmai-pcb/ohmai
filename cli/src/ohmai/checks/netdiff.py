# SPDX-License-Identifier: Apache-2.0
"""Compare a netlist with expected_nets.csv.

expected_nets.csv: one net per row, '#' lines are comments.
  net,ref.pin,ref.pin,...
  ref.pin   exact pin (U1.20)        ref.*  any pin of a non-polar part (C3.*)
  ...       open row: other nodes allowed
  NC        row name: every listed pin must be unconnected
  TODO      any cell containing TODO skips the row (counted)
Errors: missing, split, merged, extra, ambiguous, name, nc, empty, unlisted.
coverage(): every multi-node net KiCad auto-names (unlabeled, 'Net-(...)') must be matched by a row
(FAIL 'unlisted'); named multi-node nets without a row WARN. Unlabeled nets are never dropped.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

from ohmai.netlist import Netlist, base


def load_expected(path: Path):
    rows, todo = [], 0
    with open(path, newline="", encoding="utf-8") as fh:
        lines = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    for rec in csv.reader(lines):
        cells = [c.strip() for c in rec if c.strip()]
        if not cells or cells[0].lower() == "net":
            continue
        if any("TODO" in c for c in cells):
            todo += 1
            continue
        nodes = cells[1:]
        rows.append((cells[0], [n for n in nodes if n != "..."], "..." in nodes))
    return rows, todo


def diff(nl: Netlist, rows) -> list[str]:
    errors: list[str] = []
    if not nl.comps:
        return ["empty: netlist has 0 components (a sheet failed to load, or nothing is drawn yet)"]
    where, nets = nl.where, nl.nets
    ref_pins = defaultdict(set)
    for nd in where:
        ref_pins[nd.partition(".")[0]].add(nd)
    resolved: dict[str, set[str]] = {}
    for name, nodes, is_open in rows:
        if name == "NC":
            for nd in nodes:
                if nd not in where:
                    errors.append(f"nc: {nd} not in netlist (part missing?)")
                elif len(nets[where[nd]]) > 1:
                    others = sorted(nets[where[nd]] - {nd})
                    errors.append(f"nc: {nd} must be unconnected, is on {base(where[nd])} with {', '.join(others[:6])}")
            continue
        exact = [n for n in nodes if not n.endswith(".*")]
        wild = [n[:-2] for n in nodes if n.endswith(".*")]
        votes: Counter = Counter()
        for nd in exact:
            if nd in where:
                votes[where[nd]] += 1
        for ref in wild:
            for nd in ref_pins.get(ref, ()):
                votes[where[nd]] += 1
        if not votes:
            errors.append(f"missing: {name}: none of its nodes found ({', '.join(nodes[:6])})")
            continue
        top = votes.most_common(2)
        if len(top) > 1 and top[0][1] == top[1][1] and not any(nd in where for nd in exact):
            errors.append(f"ambiguous: {name}: fits {base(top[0][0])} and {base(top[1][0])}; add an exact ref.pin")
            continue
        actual = top[0][0]
        resolved.setdefault(actual, set()).add(name)
        anodes = nets[actual]
        for nd in exact:
            if nd not in where:
                errors.append(f"missing: {name}: {nd} not in netlist")
            elif where[nd] != actual:
                errors.append(f"split: {name}: {nd} is on {base(where[nd])}, rest on {base(actual)}")
        for ref in wild:
            if not any(nd in anodes for nd in ref_pins.get(ref, ())):
                errors.append(f"missing: {name}: no pin of {ref} on this net")
        if base(actual) != name and not base(actual).startswith("Net-("):
            errors.append(f"name: expected net {name} is named {base(actual)} in the schematic")
        # an unlabelled net (KiCad auto name "Net-(...)") wired inside one block is fine: datasheet-figure
        # style draws wires, not labels; the row still pins down its exact nodes
        if not is_open:
            extra = sorted(nd for nd in anodes if nd not in set(exact) and nd.split(".")[0] not in wild)
            if extra:
                errors.append(f"extra: {name}: unexpected nodes {', '.join(extra)}")
    for actual, names in resolved.items():
        if len(names) > 1:
            errors.append(f"merged: expected nets {', '.join(sorted(names))} are all on {base(actual)}")
    return errors


def dump(nl: Netlist, path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["net", "nodes..."])
        for name in sorted(nl.nets):
            w.writerow([base(name), *sorted(nl.nets[name])])


def is_auto(name: str) -> bool:
    b = base(name)
    return b.startswith(("Net-(", "unconnected-("))


def coverage(nl: Netlist, rows, refs: set[str] | None = None) -> dict:
    """Nets in the netlist that no expected row accounts for. refs: only nets touching these parts."""
    covered: set[str] = set()
    for name, nodes, _open in rows:
        if name == "NC":
            continue
        for n in nodes:
            if n.endswith(".*"):
                covered |= {nl.where[k] for k in nl.where if k.startswith(n[:-2] + ".")}
            elif n in nl.where:
                covered.add(nl.where[n])
    errors, warns, auto, auto_missing = [], [], 0, 0
    for name, nodes in sorted(nl.nets.items()):
        if len(nodes) < 2 or (refs is not None and not any(n.split(".")[0] in refs for n in nodes)):
            continue
        if is_auto(name):
            auto += 1
            if name not in covered:
                auto_missing += 1
                errors.append(f"unlisted: unlabeled net {base(name)} ({', '.join(sorted(nodes)[:6])}) has no row in "
                              "expected_nets.csv; add it (an unlabeled net is still a net)")
        elif name not in covered:
            warns.append(f"unlisted: net {base(name)} ({', '.join(sorted(nodes)[:6])}) has no expected_nets.csv row")
    return {"errors": errors, "warns": warns, "auto": auto, "auto_missing": auto_missing}

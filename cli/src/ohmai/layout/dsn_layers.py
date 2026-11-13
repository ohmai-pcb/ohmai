# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Specctra layer policy and dedicated-plane reservations (no native imports)."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from .. import sexp


def end_scope(text: str, start: int) -> int:
    depth = 0
    quoted = escaped = False
    for i in range(start, len(text)):
        c = text[i]
        if escaped:
            escaped = False
        elif c == "\\" and quoted:
            escaped = True
        elif c == '"':
            quoted = not quoted
        elif not quoted:
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    return i + 1
    raise ValueError('DSN: unterminated scope')


def section(text: str, name: str) -> tuple[int, int] | None:
    # Start AFTER parser/string_quote: the DSN header is not ordinary sexp.
    m = re.search(r'\(' + re.escape(name) + r'(?=\s|\))', text)
    return (m.start(), end_scope(text, m.start())) if m else None


def scopes(text: str, name: str):
    i = 0
    quoted = escaped = False
    while i < len(text):
        c = text[i]
        if escaped:
            escaped = False
        elif c == "\\" and quoted:
            escaped = True
        elif c == '"':
            quoted = not quoted
        elif c == '(' and not quoted:
            m = re.match(r'\(([^\s()]+)', text[i:])
            if m and m[1] == name:
                end = end_scope(text, i)
                yield i, end
                i = end
                continue
        i += 1


def board_layers(path: Path) -> dict:
    if not path.is_file():
        return {}
    text = path.read_text()
    span = section(text, 'layers')
    if not span:
        return {}
    tree = sexp.parse(text[slice(*span)])
    return {str(row[1]): (str(row[3]) if len(row) > 3 else str(row[1]), str(row[2]))
            for row in tree[1:] if isinstance(row, list) and len(row) > 2 and str(row[1]).endswith('.Cu')}


def policy(text: str, planes: dict, copper: list[str], signals: list[str] | None,
           inner: str | None, board: dict | None = None) -> dict:
    span = section(text, 'structure')
    structure = sexp.parse(text[slice(*span)]) if span else ['structure']
    rows = sexp.find(structure, 'layer')
    def index(row):
        prop = sexp.first(row, 'property') or []
        idx = sexp.first(prop, 'index')
        return int(idx[1]) if idx else rows.index(row)
    rows = sorted(rows, key=index)
    types = {str(row[1]): str((sexp.first(row, 'type') or ['', 'signal'])[1]) for row in rows}
    names = list(types) or list(copper)
    aliases = {n: n for n in names}
    for i, n in enumerate(names):
        canonical = 'F.Cu' if i == 0 else 'B.Cu' if i == len(names)-1 else f'In{i}.Cu'
        aliases.setdefault(canonical, n)
    for canonical, (alias, _) in (board or {}).items():
        if alias in names:
            aliases[canonical] = alias
        elif canonical in names:
            aliases[alias] = canonical
    def resolve(n):
        if n not in aliases:
            raise ValueError(f'DSN: signal layer {n} not found in board stackup')
        return aliases[n]
    for n in copper:
        resolve(n)
    if any(not isinstance(n, str) for n in planes.values()):
        raise ValueError('DSN: planes values must be layer names')
    reserved = {resolve(n) for n in planes.values()}
    reserved.update(n for n, typ in types.items() if typ == 'power')
    for canonical, (alias, typ) in (board or {}).items():
        if typ == 'power' and (canonical in aliases or alias in aliases):
            reserved.add(resolve(canonical if canonical in aliases else alias))
    # A plane statement also marks a dedicated inner plane even if an exporter
    # mislabeled it signal. Shared F/B pours remain refillable, not dedicated layers.
    outer = {aliases.get('F.Cu'), aliases.get('B.Cu')}
    for plane in sexp.find(structure, 'plane'):
        for shape in plane[2:]:
            if isinstance(shape, list) and len(shape) > 1 and str(shape[1]) not in outer:
                reserved.add(resolve(str(shape[1])))
    selected = [resolve(n) for n in signals] if signals is not None else [n for n in names if n not in reserved]
    inner = resolve(inner) if inner else None
    if inner and inner not in selected:
        selected.append(inner)
    selected = list(dict.fromkeys(selected))
    if not selected and names:
        raise ValueError('DSN: no routable signal layers; configure an explicit override')
    return dict(signal_layers=selected, non_signal_layers=[n for n in names if n not in selected],
                inner_layer=inner, types=types, aliases=aliases)


def reserve_planes(text: str, layers: dict) -> tuple[str, list[str]]:
    span = section(text, 'structure')
    if not span:
        return text, []
    a, b = span
    struct = text[a:b]
    tree = sexp.parse(struct)
    blocked = layers['non_signal_layers']
    covered = set()
    # Retain the original dedicated-plane areas. Freerouting 2.5.0 treats a
    # power-typed layer WITHOUT a plane as signal. wire_keepout is NOT a safe
    # substitute: a forced F/B control shows it also prevents through-vias.
    for x, y in reversed(list(scopes(struct, 'plane'))):
        node = sexp.parse(struct[x:y])
        shape = next((v for v in node[2:] if isinstance(v, list) and v[0] in ('polygon', 'rect', 'circle', 'path')), None)
        if shape is not None and str(shape[1]) in blocked:
            covered.add(str(shape[1]))
        else:
            # Raw shared-layer zone outlines are not actual filled copper: let
            # native refill carve them, then guard every net after additive import.
            struct = struct[:x] + struct[y:]
    boundaries = [v[1] for v in sexp.find(tree, 'boundary') if len(v) > 1 and isinstance(v[1], list)]
    additions = []
    synthetic = []
    for lay in blocked:
        if lay in covered:
            continue
        if not boundaries:
            raise ValueError(f'DSN: cannot reserve {lay}: missing board boundary / plane area')
        # DSN-only reservation, NOT physical copper to import. A pinless virtual
        # plane keeps an empty power layer inactive while allowing via antipads.
        net = '__ohmai_reserved_layer_' + lay
        for shape in boundaries:
            shape = [*shape]
            shape[1] = sexp.Atom(lay, quoted=False)
            if shape[0] == 'path':
                shape[0] = 'polygon'
            additions.append(sexp.dumps(['plane', net, shape]))
        synthetic.append(lay)
    struct = struct[:-1] + ''.join('\n    ' + v for v in additions) + '\n  )'
    log = [f'preserved dedicated-plane areas: {", ".join(blocked)}'] if blocked else []
    if synthetic:
        log.append('DSN-only plane reservations (no physical copper): ' + ', '.join(synthetic))
    return text[:a] + struct + text[b:], log


def ensure_plane_nets(text: str) -> str:
    """Keep pinless plane declarations valid after configured/scoped net removal."""
    struct = section(text, 'structure')
    network = section(text, 'network')
    if not struct or not network:
        return text
    planes = sexp.find(sexp.parse(text[slice(*struct)]), 'plane')
    nets = sexp.find(sexp.parse(text[slice(*network)]), 'net')
    existing = {str(n[1]) for n in nets}
    missing = list(dict.fromkeys(str(p[1]) for p in planes if str(p[1]) not in existing))
    pos = network[1]-1
    return text[:pos] + ''.join('\n    ' + sexp.dumps(['net', n]) for n in missing) + text[pos:]


def constrain_classes(text: str, layers: list[str], inner: str | None, limited_inner: bool,
                      inner_class: str) -> str:
    span = section(text, 'network')
    if not span:
        return text
    a, b = span
    network = text[a:b]
    for x, y in reversed(list(scopes(network, 'class'))):
        block = network[x:y]
        cls = str(sexp.parse(block)[1])
        allowed = [n for n in layers if not (limited_inner and n == inner and cls != inner_class)]
        if not allowed:
            raise ValueError(f'DSN: class {cls} has no signal layer')
        for u, v in reversed(list(scopes(block, 'use_layer'))):
            block = block[:u] + block[v:]
        names = ' '.join('"' + n.replace('"', '\\"') + '"' if re.search(r'[\s()"]', n) else n for n in allowed)
        circuit = section(block, 'circuit')
        if circuit:
            pos = circuit[1]-1
            block = block[:pos] + f'  (use_layer {names})\n      ' + block[pos:]
        else:
            block = block[:-1] + f'\n      (circuit (use_layer {names}))\n    )'
        network = network[:x] + block + network[y:]
    return text[:a] + network + text[b:]


def ses_non_signal_wires(text: str, signals: list[str]) -> dict[str, int]:
    tree = sexp.parse(text)
    bad = Counter()
    for item in sexp.walk(tree, 'wire'):
        typ = sexp.first(item, 'type')
        path = sexp.first(item, 'path')
        if typ and typ[1] in ('fix', 'protect'):
            continue
        if path and len(path) >= 7 and path[1] not in signals:
            bad[str(path[1])] += 1
    return dict(bad)

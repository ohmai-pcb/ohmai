# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""List board zones with the UUID and ``expect`` block that zone.edit/zone.delete need (no KiCad)."""
from __future__ import annotations

import re
from pathlib import Path

from ohmai import sexp


def _num(v):
    return round(float(v), 6)


def list_zones(board: Path, net: str | None = None) -> list[dict]:
    tree = sexp.parse(Path(board).read_text(encoding='utf-8'))
    nets = {str(n[1]): str(n[2]) for n in tree[1:] if isinstance(n, list) and n and n[0] == 'net' and len(n) > 2}
    out = []
    for z in tree[1:]:
        if not (isinstance(z, list) and z and z[0] == 'zone'):
            continue
        if sexp.first(z, 'keepout'):
            continue  # rule areas are not copper
        name_node = sexp.first(z, 'net_name')
        net_name = str(name_node[1]) if name_node else nets.get(str((sexp.first(z, 'net') or [0, ''])[1]), '')
        if net is not None and not re.fullmatch(net, net_name):
            continue
        layer = sexp.first(z, 'layer')
        layers = [str(layer[1])] if layer else [str(x) for x in (sexp.first(z, 'layers') or [None])[1:]]
        polys = sexp.find(z, 'polygon')
        outlines = [[[_num(p[1]), _num(p[2])] for p in (sexp.first(poly, 'pts') or [])[1:]
                     if isinstance(p, list) and p and p[0] == 'xy'] for poly in polys]
        uuid = sexp.first(z, 'uuid') or sexp.first(z, 'tstamp')
        prio = sexp.first(z, 'priority')
        zname = sexp.first(z, 'name')
        row = dict(uuid=str(uuid[1]) if uuid else None, net=net_name, layers=layers,
                   name=str(zname[1]) if zname else '', priority=int(prio[1]) if prio else 0,
                   outlines=len(outlines), points=sum(len(o) for o in outlines))
        editable = len(layers) == 1 and len(outlines) == 1 and row['uuid']
        row['editable'] = bool(editable)
        if editable:
            row['expect'] = dict(net=net_name, layer=layers[0], polygon=outlines[0])
        else:
            row['reason'] = 'zone.edit/delete v1 needs one copper layer, one outline and a UUID'
        out.append(row)
    return out

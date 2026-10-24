"""FICTIONAL test boards: write minimal .kicad_pcb text for the layout tests and known-bad cases.

The parts here are made up (a 16-pin QFN, two-pad passives, an 8-pin two-column flash, an edge strip); they do
not describe any real package. Loaded by ohmai.layout.placement; KiCad itself is not needed.
"""

from __future__ import annotations

import uuid as _uuid


def _u(seed: str) -> str:
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, "ohmai-fixture:" + seed))


def qfn(ref, x, y, nets=None, per_side=4, pitch=0.5, pad_w=0.25, pad_l=0.7, centre=1.45, ep=1.6, cy=2.1, rot=0):
    """Pins 1.. counter-clockwise from the top of the left side; EP = pin per_side * 4 + 1."""
    nets = nets or {}
    pads, n = [], 1
    span = (per_side - 1) * pitch / 2
    for side in ("L", "B", "R", "T"):
        for k in range(per_side):
            o = -span + k * pitch
            px, py, w, h = {"L": (-centre, o, pad_l, pad_w), "B": (o, centre, pad_w, pad_l),
                            "R": (centre, -o, pad_l, pad_w), "T": (-o, -centre, pad_w, pad_l)}[side]
            pads.append((str(n), px, py, w, h, nets.get(n, "")))
            n += 1
    pads.append((str(n), 0, 0, ep, ep, nets.get(n, "")))
    return {"ref": ref, "lib": "kb:QFN-16_fixture", "x": x, "y": y, "rot": rot, "pads": pads, "cy": (-cy, -cy, cy, cy)}


def two_pad(ref, x, y, net1="", net2="", rot=0, pitch=0.96, size=(0.5, 0.5), cy=(-0.95, -0.5, 0.95, 0.5), lib="kb:C_fixture"):
    return {"ref": ref, "lib": lib, "x": x, "y": y, "rot": rot,
            "pads": [("1", -pitch / 2, 0, *size, net1), ("2", pitch / 2, 0, *size, net2)], "cy": cy}


def flash(ref, x, y, nets=None, rot=0):
    """8 pins in two columns (1-4 left top to bottom, 5-8 right bottom to top), 0.5 pitch."""
    nets = nets or {}
    pads = [(str(i + 1), -1.0, -0.75 + 0.5 * i, 0.6, 0.3, nets.get(i + 1, "")) for i in range(4)]
    pads += [(str(i + 5), 1.0, 0.75 - 0.5 * i, 0.6, 0.3, nets.get(i + 5, "")) for i in range(4)]
    return {"ref": ref, "lib": "kb:FLASH-8_fixture", "x": x, "y": y, "rot": rot, "pads": pads, "cy": (-1.6, -1.2, 1.6, 1.2)}


def strip(ref, x, y, nets, pitch=1.0, vertical=True):
    pads = [(str(i + 1), 0, i * pitch, 1.2, 0.8, n) if vertical else (str(i + 1), i * pitch, 0, 0.8, 1.2, n)
            for i, n in enumerate(nets)]
    L = (len(nets) - 1) * pitch
    return {"ref": ref, "lib": "kb:EDGE_fixture", "x": x, "y": y, "rot": 0, "pads": pads,
            "cy": (-0.7, -0.5, 0.7, L + 0.5) if vertical else (-0.5, -0.7, L + 0.5, 0.7)}


def board(footprints, vias=(), tracks=(), edge=(100, 100, 120, 120)) -> str:
    nets = [""]
    for fp in footprints:
        for p in fp["pads"]:
            if p[5] and p[5] not in nets:
                nets.append(p[5])
    for v in vias:
        if v[2] not in nets:
            nets.append(v[2])
    for t in tracks:
        if t[0] not in nets:
            nets.append(t[0])
    num = {n: i for i, n in enumerate(nets)}
    out = ['(kicad_pcb\n\t(version 20241229)\n\t(generator "ohmai-fixture")\n\t(generator_version "9.0")',
           '\t(general\n\t\t(thickness 1.6)\n\t\t(legacy_teardrops no)\n\t)\n\t(paper "A4")',
           '\t(layers\n\t\t(0 "F.Cu" signal)\n\t\t(2 "B.Cu" signal)\n\t\t(25 "Edge.Cuts" user)\n'
           '\t\t(31 "F.CrtYd" user "F.Courtyard")\n\t\t(29 "B.CrtYd" user "B.Courtyard")\n\t)',
           '\t(setup\n\t\t(pad_to_mask_clearance 0)\n\t)']
    out += [f'\t(net {i} "{n}")' for i, n in enumerate(nets)]
    for fp in footprints:
        side = fp.get("layer", "F.Cu")
        s = side[0]
        rot = f" {fp['rot']}" if fp.get("rot") else ""
        lines = [f'\t(footprint "{fp["lib"]}"\n\t\t(layer "{side}")\n\t\t(uuid "{_u(fp["ref"])}")\n'
                 f'\t\t(at {fp["x"]} {fp["y"]}{rot})',
                 f'\t\t(property "Reference" "{fp["ref"]}"\n\t\t\t(at 0 -2 0)\n\t\t\t(layer "{s}.SilkS")\n'
                 f'\t\t\t(uuid "{_u(fp["ref"] + "-ref")}")\n\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 0.5 0.5)\n'
                 f'\t\t\t\t\t(thickness 0.08)\n\t\t\t\t)\n\t\t\t)\n\t\t)',
                 '\t\t(attr smd)']
        x0, y0, x1, y1 = fp["cy"]
        lines.append(f'\t\t(fp_rect\n\t\t\t(start {x0} {y0})\n\t\t\t(end {x1} {y1})\n\t\t\t(stroke\n\t\t\t\t(width 0.05)\n'
                     f'\t\t\t\t(type solid)\n\t\t\t)\n\t\t\t(fill no)\n\t\t\t(layer "{s}.CrtYd")\n'
                     f'\t\t\t(uuid "{_u(fp["ref"] + "-cy")}")\n\t\t)')
        for k, (n, px, py, w, h, net) in enumerate(fp["pads"]):
            nn = f'\n\t\t\t(net {num[net]} "{net}")' if net else ""
            prot = f" {fp['rot']}" if fp.get("rot") else ""
            lines.append(f'\t\t(pad "{n}" smd rect\n\t\t\t(at {px} {py}{prot})\n\t\t\t(size {w} {h})\n'
                         f'\t\t\t(layers "{s}.Cu" "{s}.Mask" "{s}.Paste"){nn}\n'
                         f'\t\t\t(uuid "{_u(fp["ref"] + "-p" + str(k))}")\n\t\t)')
        out.append("\n".join(lines) + "\n\t)")
    x0, y0, x1, y1 = edge
    out.append(f'\t(gr_rect\n\t\t(start {x0} {y0})\n\t\t(end {x1} {y1})\n\t\t(stroke\n\t\t\t(width 0.05)\n'
               f'\t\t\t(type default)\n\t\t)\n\t\t(fill no)\n\t\t(layer "Edge.Cuts")\n\t\t(uuid "{_u("edge")}")\n\t)')
    for i, (vx, vy, net) in enumerate(vias):
        out.append(f'\t(via\n\t\t(at {vx} {vy})\n\t\t(size 0.45)\n\t\t(drill 0.2)\n\t\t(layers "F.Cu" "B.Cu")\n'
                   f'\t\t(net {num[net]})\n\t\t(uuid "{_u(f"via{i}")}")\n\t)')
    for i, (net, a, b, layer) in enumerate(tracks):
        out.append(f'\t(segment\n\t\t(start {a[0]} {a[1]})\n\t\t(end {b[0]} {b[1]})\n\t\t(width 0.15)\n'
                   f'\t\t(layer "{layer}")\n\t\t(net {num[net]})\n\t\t(uuid "{_u(f"seg{i}")}")\n\t)')
    out.append("\t(embedded_fonts no)\n)\n")
    return "\n".join(out)

"""Specctra DSN edits between KiCad's export and Freerouting (`ohmai layout autoroute`; shapely, no KiCad).

KiCad writes the DSN (units um, y = -board y); these steps keep Freerouting inside the board rules where its own
model falls short. Every step is driven by the layout config (`autoroute.dsn`, `planes`, `signal_layers`,
`inner_signal_layer`); with an empty config only the plane and layer steps that the config asks for run.

 1 drop_pins        pins of a padstack (e.g. castellation half-pads centred on the outline) leave the DSN
 1 keepouts         extra keep-out rectangles (board mm), e.g. strips along castellated edges
 2 grow_cutouts     inner cut-out keepouts grown by the edge clearance (KiCad exports them with no margin)
 3 planes           plane nets are not autorouted: their nets go, their copper becomes keepouts
 4 power layers     copper layers that carry no signals become Specctra power layers
 5 fix              pre-placed signal copper (escape vias, stubs) is fixed; the router starts from it
 6 class_split      a net class split in two widths (thin where a wide track cannot leave fine-pitch pads)
 7 widen_padstacks  padstack copper grown so tracks keep a PTH hole rule Freerouting does not know
 9 inner layer      only the nets matching inner_nets may use the inner signal layer (use_layer for the rest)
"""

from __future__ import annotations

import re

VIA_NAME = r'"?Via\[[^\]]*\][^"\s)]*"?'


def _poly(coords) -> str:
    return " ".join(f"{x:.0f} {y:.0f}" for x, y in coords)


def _q(n: str) -> str:
    return n if re.fullmatch(r'[^\s()"]+', n) else f'"{n}"'


def subset_nets(s: str, pattern: str | None, exclude: str | None) -> str:
    """Staged routing: keep only the nets matching pattern (and not exclude) in the network section."""
    a = s.index("  (network")
    b = s.index("    (class", a)
    blocks = re.findall(r"(\n    \(net (\S+)\n.*?\n    \))", s[a:b], re.S)
    keep = [blk for blk, n in blocks if (not pattern or re.search(pattern, n.strip('"')))
            and not (exclude and re.search(exclude, n.strip('"')))]
    return s[:a] + "  (network" + "".join(keep) + "\n" + s[b:]


def normalize_wiring(s: str) -> tuple[str, int]:
    """Join degree-two same-net/width/layer wire chains before Freerouting import.

    Fixed split traces can be normalized by Freerouting despite their fixed state;
    2.2.4 may merge a trace whose search-tree entries have already been removed.
    Keep every vertex and branch: this changes representation, never copper geometry.
    Parse only wiring because Specctra's (string_quote ") is not a normal sexp.
    """
    from .. import sexp

    start = s.find("(wiring")
    if start < 0:
        return s, 0
    # KiCad puts wiring last, before the pcb closing parenthesis.
    end = s.rfind(")")
    tree = sexp.parse(s[start:end])
    groups = {}
    other = []
    for item in tree[1:]:
        path = sexp.first(item, "path") if item[0] == "wire" else None
        net = sexp.first(item, "net") if path else None
        if not path or not net or len(path) < 7:
            other.append(item)
            continue
        # Do not merge wires carrying extra attributes we cannot preserve.
        if any(isinstance(v, list) and v[0] not in {"path", "net", "type"} for v in item[1:]):
            other.append(item)
            continue
        pts = list(zip(path[3::2], path[4::2], strict=True))
        typ = sexp.first(item, "type")
        key = (str(net[1]), str(path[1]), float(path[2]), tuple(typ or []))
        groups.setdefault(key, []).append((item, pts))
    merged_count = 0
    wires = []
    for entries in groups.values():
        endpoints = {}
        for i, (_, pts) in enumerate(entries):
            for pt in (pts[0], pts[-1]):
                endpoints.setdefault(tuple(map(float, pt)), []).append(i)
        remaining = set(range(len(entries)))
        while remaining:
            i = min(remaining)
            remaining.remove(i)
            item, pts = entries[i]
            pts = list(pts)
            for reverse in (False, True):
                if reverse:
                    pts.reverse()
                while True:
                    neighbors = endpoints[tuple(map(float, pts[-1]))]
                    if len(neighbors) != 2:
                        break
                    nxt = next((j for j in neighbors if j in remaining), None)
                    if nxt is None:
                        break
                    tail = list(entries[nxt][1])
                    if tuple(map(float, tail[0])) != tuple(map(float, pts[-1])):
                        tail.reverse()
                    pts.extend(tail[1:])
                    remaining.remove(nxt)
                    merged_count += 1
            path = sexp.first(item, "path")
            path[:] = path[:3] + [v for pt in pts for v in pt]
            wires.append(item)
    if not merged_count:
        return s, 0
    return s[:start] + sexp.dumps(["wiring"] + wires + other) + "\n" + s[end:], merged_count


def patch(s: str, dsn_cfg: dict, planes: dict, copper: list[str], signal_layers: list[str],
          inner_layer: str | None = None, inner_nets: str | None = None, nets: str | None = None,
          exclude: str | None = None) -> tuple[str, list[str]]:
    from shapely.geometry import LineString, Polygon

    log: list[str] = []
    ko: list[str] = []
    # 1. dropped pins and their net references
    for d in dsn_cfg.get("drop_pins") or []:
        ps = re.escape(d["padstack"])
        ids = re.findall(rf"\n\s*\(pin {ps} ([\d@A-Za-z_.-]+) [-\d.]+ [-\d.]+\)", s)
        s = re.sub(rf"\n\s*\(pin {ps} [\d@A-Za-z_.-]+ [-\d.]+ [-\d.]+\)", "", s)
        for n in ids:
            s = re.sub(rf"(?<=[\s(]){re.escape(d['component'])}-{re.escape(n)}(?=[\s)])", "", s)
        log.append(f"dropped {len(ids)} {d['padstack']} pin(s) of {d['component']}")
    for x0, y0, x1, y1 in dsn_cfg.get("keepouts") or []:
        a, b, top, bot = x0 * 1000, x1 * 1000, -y0 * 1000, -y1 * 1000
        ko.append(f'(keepout "" (polygon signal 0  {_poly([(a, top), (b, top), (b, bot), (a, bot), (a, top)])}))')
    # 2. inner cut-outs
    grow_um = (dsn_cfg.get("grow_cutouts") or 0) * 1000
    if grow_um:
        def grow(m):
            n = [float(v) for v in m.group(1).split()]
            g = Polygon(list(zip(n[0::2], n[1::2], strict=False))).buffer(grow_um, quad_segs=4)
            return f'(keepout "" (polygon signal 0  {_poly(g.exterior.coords)}))'
        s = re.sub(r'\(keepout "" \(polygon signal 0\s+([-\d\s.]+)\)\)', grow, s)
    # 3. plane nets: copper -> keepouts, nets and plane statements out
    if planes:
        alt = "|".join(re.escape(_q(n)) for n in planes)
        margin = (dsn_cfg.get("plane_keepout_margin") or 0) * 1000

        def wire(m):
            g = LineString([(float(m.group(3)), float(m.group(4))), (float(m.group(5)), float(m.group(6)))]).buffer(
                float(m.group(2)) / 2 + margin, quad_segs=4)
            ko.append(f'(keepout "" (polygon {m.group(1)} 0  {_poly(g.exterior.coords)}))')
            return ""
        s = re.sub(rf"\n\s*\(wire \(path (\S+) (\d+)\s+(-?\d+) (-?\d+)\s+(-?\d+) (-?\d+)\)\(net (?:{alt})\)\(type \w+\)\)",
                   wire, s)
        vd = f"{(dsn_cfg.get('plane_via_keepout') or 0.5) * 1000:.0f}"

        def via(m):
            ko.append(f'(keepout "" (circle signal {vd} {m.group(1)} {m.group(2)}))')
            return ""
        s = re.sub(rf'\n\s*\(via {VIA_NAME}\s+(-?\d+) (-?\d+) \(net (?:{alt})\)\(type \w+\)\)', via, s)
        for n in planes:
            q = re.escape(_q(n))
            s = re.sub(rf"\n\s*\(net {q}\s*\n\s*\(pins[^)]*\)\s*\)", "", s)
            s = re.sub(rf"(\(class \S+[^\n]*?) {q}(?=[ \n])", r"\1", s)
        s = re.sub(r"\n\s*\(plane [^\n]*\n[^\n]*\)\)", "", s)
    # 4. power layers
    for lay in copper:
        if lay not in signal_layers:
            s, k = re.subn(rf"\(layer {re.escape(lay)}\s*\(type signal\)", f"(layer {lay}\n      (type power)", s)
            if k != 1:
                raise ValueError(f"DSN: signal layer {lay} not found once (found {k})")
    # 5. fixed pre-placed copper
    wi = s.find("(wiring")
    if wi >= 0:
        # KiCad wraps long names/paths and exports fractional um coordinates.
        # Matching individual one-line integer wires silently left copper movable.
        s = s[:wi] + re.sub(r"\(type\s+route\)", "(type fix)", s[wi:])
    if ko:
        k = s.find('\n    (via "')
        if k < 0:
            raise ValueError("DSN: no structure via line to insert the keepouts before")
        s = s[:k] + "".join("\n    " + x for x in ko) + s[k:]
    # 6. class split
    cs = dsn_cfg.get("class_split")
    if cs:
        m = re.search(rf"\(class {re.escape(cs['class'])} (.*?)\(circuit", s, re.S)
        if m:
            names = m.group(1).split()
            keep = set(cs.get("keep") or [])
            lo = [n for n in names if n.strip('"') not in keep]
            hin = [n for n in names if n.strip('"') in keep]
            block = s[m.start():s.index("\n    )", m.start()) + 6]
            new = re.sub(r"\(width \d+\)", f"(width {cs['width'] * 1000:.0f})",
                         block.replace(m.group(1), " ".join(lo) + "\n      "))
            new += "\n" + block.replace(f"(class {cs['class']}", f"(class {cs.get('name', cs['class'] + 'Hi')}").replace(
                m.group(1), " ".join(hin) + "\n      ")
            s = s.replace(block, new)
            log.append(f"class {cs['class']} split: {len(lo)} thin, {len(hin)} kept")
    # 7. widened padstacks
    for w in dsn_cfg.get("widen_padstacks") or []:
        def widen(m, w=w):
            return re.sub(rf"(\(path \S+ ){w['from']} ", rf"\g<1>{w['to']} ", m.group(1))
        s = re.sub(rf"(\(padstack {re.escape(w['padstack'])}.*?\(attach off\))", widen, s, flags=re.S)
    # 9. inner signal layer only for inner_nets
    if inner_layer and inner_layer in signal_layers:
        a = s.index("  (network")
        b = s.index("  (wiring", a)
        net_s = s[a:b]
        allowed: list[str] = []
        via_m = re.search(r'\(use_via ("[^"]+"|\S+)\)', net_s)
        via_name = via_m.group(1) if via_m else '""'
        if inner_nets:
            i0 = net_s.index("(class kicad_default")
            j0 = net_s.index("(circuit", i0)
            head = net_s[i0:j0]
            names = re.findall(r'"[^"]+"|[^\s()"]+', head[len("(class kicad_default"):])
            allowed = [n for n in names if re.search(inner_nets, n.strip('"'))]
            for n in allowed:
                head = re.sub(rf'\s{re.escape(n)}(?=[\s)])', "", head, count=1)
            net_s = net_s[:i0] + head + net_s[j0:]
        others = " ".join(lay for lay in signal_layers if lay != inner_layer)
        net_s = re.sub(r'(\(use_via (?:"[^"]+"|\S+)\)\n)', lambda m: m.group(1) + f"        (use_layer {others})\n", net_s)
        if allowed:
            ic = dsn_cfg.get("inner_class") or {}
            cls = (f"    (class {ic.get('name', 'InnerOk')} " + " ".join(allowed) +
                   f"\n      (circuit\n        (use_via {via_name})\n      )\n"
                   f"      (rule\n        (width {ic.get('width', 0.15) * 1000:.0f})\n"
                   f"        (clearance {ic.get('clearance', 0.1) * 1000:.0f})\n      )\n    )\n")
            k = net_s.index("    (class kicad_default")
            net_s = net_s[:k] + cls + net_s[k:]
        s = s[:a] + net_s + s[b:]
        log.append(f"{inner_layer} allowed for {len(allowed)} nets: {' '.join(allowed)}")
    if nets or exclude:
        s = subset_nets(s, nets, exclude)
    s, joined = normalize_wiring(s)
    if joined:
        log.append(f"coalesced {joined} fixed wire chain joins (geometry unchanged)")
    return s, log

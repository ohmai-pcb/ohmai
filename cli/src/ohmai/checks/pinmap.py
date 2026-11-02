"""Netlist vs per-part constraints: pin map, NC pins, one source per net, passives, pull-ups, I2C, EVB.

Binding: a constraints file binds to netlist parts by the symbol's MPN field (`match.mpn`), by
reference (`match.refs`) or by library symbol (`match.lib_symbol`). `match.value` is informational only:
value substrings bound unrelated parts (a test point named USB_CONN_DM matched a USB-C file).

Symbol maps: `parts/symbol-maps/<constraints stem>.yaml` maps library symbol pins to the datasheet pins of
the reviewed constraints file (names and numbers), so a vetted library symbol can be used without editing
the symbol or the reviewed constraints. Every check below works in datasheet pin numbers and translates
them to symbol pins through the map.
"""

from __future__ import annotations

import re
from collections import defaultdict

import yaml

from ohmai.checks import FAIL, PASS, SKIP, WARN, Result, register
from ohmai.checks.constraints import load_all
from ohmai.checks.docs import MARKER
from ohmai.netlist import Netlist, base, parse_value
from ohmai.project import Project

SOURCE_DIRS = {"regulator_out", "switch", "gate_drive", "output"}
GND_RE = re.compile(r"^(A|P|D)?GND|^VSS|^0V$|^EARTH$", re.I)
UNIT = {"F": 1, "mF": 1e-3, "uF": 1e-6, "nF": 1e-9, "pF": 1e-12, "ohm": 1, "kohm": 1e3, "Mohm": 1e6,
        "H": 1, "mH": 1e-3, "uH": 1e-6, "nH": 1e-9}
KIND_PREFIX = {"capacitor": "C", "resistor": "R", "inductor": "L", "diode": "D", "pullup": "R"}
TOL = 0.01          # rounding tolerance on min/max bounds
NOMINAL_TOL = 0.05  # resistor/inductor 'nominal' without min/max: accepted within ±5 %


def _norm(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


class PinMap:
    """Datasheet pin <-> symbol pin translation for one bound part."""

    def __init__(self, ref: str, c: dict, sym: dict[str, str], smap: dict | None):
        self.ref, self.c, self.sym, self.smap = ref, c, sym, smap or {}
        self.ds_pins = {str(p["number"]): p for p in c.get("pins") or [] if isinstance(p, dict) and "number" in p}
        self.explicit: dict[str, list[str]] = {}       # symbol pin -> datasheet pins
        for s, m in (self.smap.get("pins") or {}).items():
            ds = m.get("datasheet") if isinstance(m, dict) else m
            self.explicit[str(s)] = [str(x) for x in (ds if isinstance(ds, list) else [ds])]
        self.rev: dict[str, list[str]] = defaultdict(list)  # datasheet pin -> symbol pins
        for s in sym:
            for d in self.explicit.get(s, [s] if s in self.ds_pins else []):
                self.rev[d].append(s)

    def nodes(self, ds_num: str) -> list[str]:
        return [f"{self.ref}.{s}" for s in self.rev.get(str(ds_num), [])]

    def net(self, nl: Netlist, ds_num: str) -> str | None:
        for nd in self.nodes(ds_num):
            if nd in nl.where:
                return nl.where[nd]
        return None


def _load_map(project: Project, fname: str) -> dict | None:
    d = project.root / project.config.get("symbol_maps_dir", "parts/symbol-maps")
    for f in (d / f"{fname.rsplit('.', 1)[0]}.yaml", d / f"{fname.rsplit('.', 1)[0]}.yml"):
        if f.is_file():
            return yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return None


def bind(nl: Netlist, files: dict[str, dict]) -> tuple[list[tuple[str, str, dict]], list[str]]:
    bound, unmatched = [], []
    for fname, c in files.items():
        m = c.get("match") or {}
        refs = [
            r for r, comp in nl.comps.items()
            if (m.get("mpn") and _norm(comp["fields"].get("MPN", "")) == _norm(m["mpn"]))
            or (r in (m.get("refs") or []))
            or (m.get("lib_symbol") and comp["part"].split(":")[-1].upper() == str(m["lib_symbol"]).upper())
        ]
        if not refs:
            unmatched.append(f"{fname}: matches no component (binding uses the MPN field, refs or lib_symbol)")
        bound += [(r, fname, c) for r in refs]
    return bound, unmatched


def _context(project: Project, name: str, opts: dict):
    """(netlist, [(ref, filename, constraints, PinMap)], warnings) or a Result to return early."""
    from ohmai import kicad
    from ohmai.checks.schematic import export_netlist
    from ohmai.netlist import load

    if not opts.get("netlist") and not kicad.kicad_cli():
        return Result(name, SKIP, "kicad-cli not found")
    files, errors = load_all(project)
    if errors:
        return Result(name, FAIL, "constraints files do not load (run `ohmai check constraints`)", errors)
    try:
        nl = load(export_netlist(project, opts))
    except (RuntimeError, FileNotFoundError) as e:
        return Result(name, FAIL, str(e))
    pairs, unmatched = bind(nl, files)
    sh = (opts or {}).get("sheet")
    if sh is not None:
        on = nl.refs_on(sh.path)
        pairs, unmatched = [x for x in pairs if x[0] in on], []
        if not pairs:
            return Result(name, PASS, f"sheet {sh.name}: no constrained part on this sheet")
    if not pairs:
        return Result(name, SKIP, f"no netlist part matches a constraints file ({len(files)} file(s))", unmatched)
    bound = [(r, f, c, PinMap(r, c, nl.pins_of(r), _load_map(project, f))) for r, f, c in pairs]
    return nl, bound, unmatched


def _finish(name: str, n_parts: int, errors: list[str], warns: list[str]) -> Result:
    summary = f"{n_parts} constrained part(s); {len(errors)} error(s), {len(warns)} warning(s)"
    if errors:
        return Result(name, FAIL, summary, errors + warns)
    return Result(name, WARN if warns else PASS, summary, warns)


@register("pin-map", "Symbol pins match the datasheet table (directly or via parts/symbol-maps/); NC pins unconnected")
def pin_map(project: Project, opts: dict) -> Result:
    ctx = _context(project, "pin-map", opts)
    if isinstance(ctx, Result):
        return ctx
    nl, bound, warns = ctx
    errors = []
    for ref, fname, c, pm in bound:
        sym = pm.sym
        want = pm.smap.get("symbol")
        if want and nl.comps[ref]["part"] != want:
            errors.append(f"{ref}: symbol map for {fname} was written for {want}, part uses {nl.comps[ref]['part']}")
        for s, m in (pm.smap.get("pins") or {}).items():
            s = str(s)
            if s not in sym:
                errors.append(f"{ref}: symbol map pin {s} does not exist on {nl.comps[ref]['part']}")
                continue
            sname = m.get("symbol_name") if isinstance(m, dict) else None
            if sname is None:
                errors.append(f"{ref}: symbol map pin {s} needs symbol_name (the library pin name it was checked against)")
            elif _norm(sname) != _norm(sym[s]):
                errors.append(f"{ref}.{s}: symbol map says symbol pin is '{sname}', library has '{sym[s]}' (map stale)")
            for d in pm.explicit.get(s, []):
                if d not in pm.ds_pins:
                    errors.append(f"{ref}: symbol map pin {s} -> datasheet pin {d}, not in {fname}")
        for num, p in pm.ds_pins.items():
            names = {_norm(n) for n in [p.get("name", ""), *(p.get("aliases") or [])]}
            syms = pm.rev.get(num, [])
            if not syms:
                errors.append(f"{ref} ({c.get('part')}): no symbol pin for datasheet pin {num} ({p.get('name')})")
            for s in syms:
                if s not in pm.explicit and _norm(sym[s]) not in names:
                    errors.append(f"{ref}.{s}: symbol says '{sym[s]}', datasheet says '{p.get('name')}' "
                                  f"(p.{(p.get('cite') or {}).get('page', '?')}; add a symbol map if the library differs)")
            if p.get("direction") == "nc" or p.get("nc"):
                for nd in pm.nodes(num):
                    net = nl.where.get(nd)
                    if net and len(nl.nets[net]) > 1:
                        errors.append(f"{nd} ({p.get('name')}) must not connect, is on {base(net)}")
            if p.get("direction") == "unknown":
                errors.append(f"{ref}.{num} ({p.get('name')}): direction unknown in {fname}; read the datasheet")
        for s in sym:
            if s not in pm.explicit and s not in pm.ds_pins:
                errors.append(f"{ref}: symbol pin {s} ('{sym[s]}') not in {fname} and not in its symbol map")
    return _finish("pin-map", len(bound), errors, warns)


@register("single-source", "At most one regulator/switch/driver output per net (exceptions in parts/allow.yaml)")
def single_source(project: Project, opts: dict) -> Result:
    ctx = _context(project, "single-source", opts)
    if isinstance(ctx, Result):
        return ctx
    nl, bound, warns = ctx
    allow_file = project.root / "parts" / "allow.yaml"
    allow = (yaml.safe_load(allow_file.read_text(encoding="utf-8")) or {}).get("allow") or [] \
        if allow_file.is_file() else []
    allowed = {base(a.get("net", "")) for a in allow if a.get("reason") and a.get("source")}
    sources = defaultdict(set)
    for ref, _f, _c, pm in bound:
        for num, p in pm.ds_pins.items():
            net = pm.net(nl, num)
            if net and p.get("direction") in SOURCE_DIRS:
                sources[net].add(f"{ref}.{num}({p.get('name')})")
    errors = [f"{base(n)}: {len(s)} outputs tied: {', '.join(sorted(s))}"
              for n, s in sources.items() if len(s) > 1 and base(n) not in allowed]
    return _finish("single-source", len(bound), errors, warns)


def _two_pin_other(nl: Netlist, ref: str, net: str) -> list[str]:
    """Nets on the other pins of ref (ref has a pin on net)."""
    return sorted({nl.where[nd] for nd in nl.where if nd.startswith(ref + ".") and nl.where[nd] != net})


def _parts_between(nl: Netlist, net: str, prefix: str, other: str | None) -> list[str]:
    """Refs with prefix that have a pin on net (and, if other is given, a pin on other)."""
    refs = sorted({nd.split(".")[0] for nd in nl.nets.get(net, ()) if re.match(rf"^{prefix}\d", nd)})
    if other is None:
        return refs
    return [r for r in refs if other in _two_pin_other(nl, r, net)]


def _transparent(nl: Netlist, ref: str) -> bool:
    """Parts that are a wire electrically: 0 ohm links and normally-closed (bridged) solder jumpers."""
    comp = nl.comps.get(ref, {})
    if "Bridged" in comp.get("part", "") and "Jumper" in comp.get("part", ""):
        return True
    return bool(re.match(r"^R\d", ref)) and parse_value(comp.get("value", "")) == 0


def bounds(ext: dict, kind: str) -> tuple[float | None, float | None]:
    """(lo, hi) in ext units. 'nominal' alone is NOT a maximum: for capacitors it is a minimum, for
    resistors/inductors it is the design value (±NOMINAL_TOL)."""
    lo, hi, nom = ext.get("min"), ext.get("max"), ext.get("nominal")
    if lo is None and hi is None and nom is not None:
        if kind == "capacitor":
            return nom, None
        return nom * (1 - NOMINAL_TOL), nom * (1 + NOMINAL_TOL)
    return lo, hi


def _fits(v: float, lo, hi) -> bool:
    return (lo is None or v >= lo * (1 - TOL)) and (hi is None or v <= hi * (1 + TOL))


@register("passives", "Required external parts exist with values in range: rail decoupling counted per pin, "
                      "nominal-only values are not a maximum, series-connected parts looked up on the next net")
def passives(project: Project, opts: dict) -> Result:
    ctx = _context(project, "passives", opts)
    if isinstance(ctx, Result):
        return ctx
    nl, bound, warns = ctx
    errors: list[str] = []
    decoupling: dict[tuple, list[tuple[str, dict, float | None, float | None, float]]] = defaultdict(list)

    def resolve_to(pm: PinMap, to):
        if to and str(to).startswith("pin:"):
            return pm.net(nl, str(to)[4:])
        if to:
            hit = next((n for n in nl.nets if base(n) == to), None)
            if hit is None:   # a wired, unlabelled net (KiCad "Net-(...)"): the part's own pin of that name
                num = next((k for k, p in pm.ds_pins.items() if _norm(p.get("name", "")) == _norm(to)), None)
                hit = pm.net(nl, num) if num else None
            return hit
        return None

    def candidates(net, prefix, other, kind, src_net):
        refs = _parts_between(nl, net, prefix, other)
        if kind == "pullup":
            refs = [r for r in refs if any(not GND_RE.match(base(n)) and n != net and n != src_net
                                           for n in _two_pin_other(nl, r, net))]
        return refs

    for ref, _f, _c, pm in bound:
        for num, p in pm.ds_pins.items():
            net = pm.net(nl, num)
            exts = p.get("required_external") or []
            for ext in exts:
                kind = ext.get("kind")
                prefix = KIND_PREFIX.get(kind)
                where = f"{ref}.{num} ({p.get('name')}) {kind}, p.{(ext.get('cite') or {}).get('page', '?')}"
                if prefix is None:
                    warns.append(f"{where}: kind '{kind}' not machine-checked; review by hand")
                    continue
                if net is None:
                    errors.append(f"{where}: pin not in netlist")
                    continue
                to = ext.get("to")
                other = resolve_to(pm, to)
                if to and other is None:
                    errors.append(f"{where}: target '{to}' not found")
                    continue
                unit = ext.get("unit")
                scale = UNIT.get(unit or "", None)
                has_value = any(k in ext for k in ("min", "max", "nominal"))
                if has_value and scale is None:
                    warns.append(f"{where}: unit '{unit}' unknown; value not checked")
                    has_value = False
                lo, hi = bounds(ext, kind) if has_value else (None, None)
                if ext.get("total_on_net"):
                    refs = _parts_between(nl, net, prefix, other)
                    vals = [parse_value(nl.comps[r]["value"]) for r in refs]
                    if not refs or None in vals:
                        errors.append(f"{where}: no parseable {kind} on {base(net)}")
                        continue
                    total = sum(vals) / scale if kind == "capacitor" else min(vals) / scale
                    if has_value and not _fits(total, lo, hi):
                        errors.append(f"{where}: total {total:.3g} {unit} from {', '.join(refs)}; "
                                      f"allowed {lo}..{hi if hi is not None else '∞'} {unit}")
                    continue
                if kind == "capacitor" and other is not None and p.get("direction") == "supply_in" and has_value:
                    decoupling[(net, other)].append((where, ext, lo, hi, scale))
                    continue
                # at-least-one semantics; look through series parts this pin also requires, and through
                # transparent links (0 ohm, bridged jumpers), when nothing fits on the pin's own net
                series = set()
                for e2 in exts:
                    if e2 is not ext and KIND_PREFIX.get(e2.get("kind")) in ("R", "L") and e2.get("to") is None:
                        series |= set(_parts_between(nl, net, KIND_PREFIX[e2["kind"]], None))
                nets_to_try, seen = [(net, None)], {net}
                for n0 in [net]:
                    for r in sorted(nd.split(".")[0] for nd in nl.nets.get(n0, ())):
                        if (r in series or _transparent(nl, r)) and len(_two_pin_other(nl, r, n0)) == 1:
                            nn = _two_pin_other(nl, r, n0)[0]
                            if nn not in seen and not GND_RE.match(base(nn)):
                                seen.add(nn)
                                nets_to_try.append((nn, r))
                found, _via, vals = [], None, []
                for n1, through in nets_to_try:
                    refs = [r for r in candidates(n1, prefix, other, kind, net) if r != through]
                    if not refs:
                        continue
                    if has_value:
                        vv = [(r, parse_value(nl.comps[r]["value"])) for r in refs]
                        if any(v is None for _, v in vv):
                            warns.append(f"{where}: cannot parse value of {', '.join(refs)}")
                            found = refs
                            break
                        ok = [r for r, v in vv if _fits(v / scale, lo, hi)]
                        vals += [f"{r}={v / scale:.3g}" for r, v in vv]
                        if ok:
                            found, _via = ok, through
                            break
                    else:
                        found, _via = refs, through
                        break
                if not found:
                    rng = f"; allowed {lo}..{hi if hi is not None else '∞'} {unit}" if has_value else ""
                    seen_txt = f" (found {', '.join(vals)})" if vals else ""
                    errors.append(f"{where}: none found on {base(net)}" + (f" to {to}" if to else "") + rng + seen_txt)
    # rail decoupling: every supply pin that asks for a cap needs its own part on (net, target)
    for (net, other), reqs in decoupling.items():
        caps = {r: parse_value(nl.comps[r]["value"]) for r in _parts_between(nl, net, "C", other)}
        used: set[str] = set()
        for where, _ext, lo, hi, scale in sorted(reqs, key=lambda x: -(x[2] or 0) * x[4]):
            pick = next((r for r, v in sorted(caps.items(), key=lambda kv: kv[1] or 0)
                         if r not in used and v is not None and _fits(v / scale, lo, hi)), None)
            if pick is None:
                errors.append(f"{where}: no free capacitor in range on {base(net)} to {base(other)} "
                              f"({len(reqs)} supply pin(s) ask for one, {len(caps)} cap(s) on the rail)")
            else:
                used.add(pick)
    return _finish("passives", len(bound), errors, warns)


@register("pullups", "Open-drain and needs_pullup pins have a resistor to a non-ground net")
def pullups(project: Project, opts: dict) -> Result:
    ctx = _context(project, "pullups", opts)
    if isinstance(ctx, Result):
        return ctx
    nl, bound, warns = ctx
    errors = []
    for ref, _f, _c, pm in bound:
        for num, p in pm.ds_pins.items():
            if not (p.get("needs_pullup") or p.get("direction") == "open_drain"):
                continue
            net = pm.net(nl, num)
            if net is None or len(nl.nets[net]) < 2:
                continue  # unconnected optional pin: pin-map/netlist decide if that is allowed
            ok = any(not GND_RE.match(base(n)) for r in _parts_between(nl, net, "R", None)
                     for n in _two_pin_other(nl, r, net))
            if not ok:
                errors.append(f"{ref}.{num} ({p.get('name')}) on {base(net)}: no pull-up resistor")
    return _finish("pullups", len(bound), errors, warns)


@register("i2c-addr", "I2C addresses are unique per bus (parts declare i2c.address and i2c.sda_pin)")
def i2c_addr(project: Project, opts: dict) -> Result:
    ctx = _context(project, "i2c-addr", opts)
    if isinstance(ctx, Result):
        return ctx
    nl, bound, warns = ctx
    buses: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for ref, _f, c, pm in bound:
        i2c = c.get("i2c") or {}
        if not i2c.get("address"):
            continue
        net = pm.net(nl, str(i2c.get("sda_pin")))
        if net is None:
            warns.append(f"{ref}: SDA pin {i2c.get('sda_pin')} not connected")
            continue
        buses[net][str(i2c["address"]).lower()].append(ref)
    errors = [f"bus {base(n)}: address {a} used by {', '.join(r)}"
              for n, addrs in buses.items() for a, r in addrs.items() if len(r) > 1]
    return _finish("i2c-addr", len(bound), errors, warns)


@register("evb-compare", "Each power IC with evb_reference has a filled docs/evb-compare/<part>.md")
def evb_compare(project: Project, opts: dict) -> Result:
    from ohmai.checks.constraints import load_all as _load

    files, _ = _load(project)
    errors, n = [], 0
    for _fname, c in files.items():
        if not c.get("evb_reference"):
            continue
        n += 1
        doc = project.root / "docs" / "evb-compare" / f"{c.get('part')}.md"
        if not doc.is_file():
            errors.append(f"{c.get('part')}: {doc.relative_to(project.root)} missing (compare part by part)")
        elif MARKER in doc.read_text(encoding="utf-8"):
            errors.append(f"{c.get('part')}: {doc.relative_to(project.root)} not filled in")
    return _finish("evb-compare", n, errors, [])

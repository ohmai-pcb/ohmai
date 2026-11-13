# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Copper current/width sizing (IPC-2152 style screening; pure Python, no KiCad).

Methods (A in mil^2, I in A RMS, dT in degC):

* ``ipc2152`` (default): least-squares fit of the IPC-2152 generic chart (Fig. 5-2,
  0.070 in board, no planes, still air), ``A = (117.555*dT^-0.913 + 1.15) * I^(0.84*dT^-0.108 + 1.159)``.
  Its published check point is 10 A / 20 degC -> 513 mil^2 (chart: 500 mil^2). The chart is
  used for internal and external copper alike; IPC-2152 modifiers (board thickness,
  nearby planes, copper weight) are NOT applied, which keeps the result conservative.
* ``ipc2221``: legacy ``I = k * dT^0.44 * A^0.725`` (k = 0.048 external, 0.024 internal).

Parallel conductors in one corridor (pours on several layers, via barrels) are treated as one
conductor with the summed cross-section, which is how closely spaced parallel copper is sized.
This is a screening calculation, not a thermal simulation: confirm hot spots, necks, via
plating and the fabricator's real copper thickness separately.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

UM_PER_MIL = 25.4
UM_PER_OZ = 35.0          # nominal finished copper per oz/ft^2
RHO_CU = 1.72e-8          # ohm*m at 20 degC
ALPHA_CU = 0.00393        # 1/degC
METHODS = ('ipc2152', 'ipc2221')


def area_mil2(amps: float, rise: float, method: str = 'ipc2152', external: bool = True) -> float:
    """Required conductor cross-section for ``amps`` at a temperature rise of ``rise`` degC."""
    if amps < 0 or rise <= 0:
        raise ValueError('current must be >= 0 and temperature rise > 0')
    if amps == 0:
        return 0.0
    if method == 'ipc2152':
        return (117.555 * rise ** -0.913 + 1.15) * amps ** (0.84 * rise ** -0.108 + 1.159)
    if method == 'ipc2221':
        k = 0.048 if external else 0.024
        return (amps / (k * rise ** 0.44)) ** (1 / 0.725)
    raise ValueError(f'unknown method {method!r}; use one of {METHODS}')


def _bisect(f, lo: float, hi: float) -> float:
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(mid):
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def capacity(area: float, rise: float, method: str = 'ipc2152', external: bool = True) -> float:
    """Current (A) that ``area`` mil^2 carries at ``rise`` degC."""
    if area <= 0:
        return 0.0
    return _bisect(lambda i: area_mil2(i, rise, method, external) > area, 0.0, 10000.0)


def rise_for(amps: float, area: float, method: str = 'ipc2152', external: bool = True) -> float:
    """Temperature rise (degC) of ``area`` mil^2 at ``amps``; inf if above the fit's 1000 degC bound."""
    if amps <= 0:
        return 0.0
    if area <= 0 or area_mil2(amps, 1000.0, method, external) > area:
        return math.inf
    return _bisect(lambda t: area_mil2(amps, t, method, external) <= area, 1e-3, 1000.0)


def parse_thickness(text: str) -> float:
    """'1oz', '0.5oz', '35um', '0.035mm' or a bare number in um -> um."""
    m = re.fullmatch(r'\s*([0-9]*\.?[0-9]+)\s*(oz|um|mm)?\s*', str(text), re.I)
    if not m:
        raise ValueError(f'bad copper thickness {text!r} (use e.g. 1oz, 35um, 0.035mm)')
    value, unit = float(m[1]), (m[2] or 'um').lower()
    um = value * {'oz': UM_PER_OZ, 'um': 1.0, 'mm': 1000.0}[unit]
    if um <= 0:
        raise ValueError('copper thickness must be > 0')
    return um


def board_copper(path: Path) -> dict[str, float]:
    """Copper thickness (um) per copper layer from a .kicad_pcb stackup; {} when absent."""
    from ohmai import sexp
    tree = sexp.parse(Path(path).read_text(encoding='utf-8'))
    out = {}
    for stack in sexp.walk(tree, 'stackup'):
        for layer in stack[1:]:
            if not (isinstance(layer, list) and layer and layer[0] == 'layer'):
                continue
            kind = sexp.first(layer, 'type')
            thick = sexp.first(layer, 'thickness')
            if kind and str(kind[1]) == 'copper' and thick:
                out[str(layer[1])] = float(thick[1]) * 1000.0
    return out


def default_copper(layers: list[str]) -> dict[str, float]:
    """Common 4-layer default: 1 oz outer, 0.5 oz inner (assumption, override with --copper)."""
    return {n: UM_PER_OZ * (1.0 if n in ('F.Cu', 'B.Cu') else 0.5) for n in layers}


def via_area_mil2(drill_mm: float, plating_um: float) -> float:
    """Barrel cross-section of a plated hole drilled at ``drill_mm`` with wall ``plating_um``."""
    d, t = drill_mm * 1000.0, plating_um
    if d <= 2 * t:
        raise ValueError('plating thickness must be less than half the drill')
    return math.pi * t * (d - t) / UM_PER_MIL ** 2


def resistance(length_mm: float, area_mil2_value: float, temp_c: float = 20.0) -> float:
    area_m2 = area_mil2_value * (UM_PER_MIL * 1e-6) ** 2
    return RHO_CU * (1 + ALPHA_CU * (temp_c - 20.0)) * length_mm * 1e-3 / area_m2


def report(targets: list[dict], layers: list[str], copper_um: dict[str, float], method: str = 'ipc2152',
           widths_mm: dict[str, float] | None = None, length_mm: float | None = None,
           via: dict | None = None, ambient: float = 25.0) -> dict:
    """Size a conductor/corridor that shares current over ``layers``.

    targets: [{'name', 'amps', 'rise'}]; widths_mm: measured narrowest width per layer
    (e.g. a pour neck from ``ohmai layout pour``); via: {'drill', 'plating', 'count'?}.
    """
    if not layers:
        raise ValueError('at least one copper layer is required')
    missing = [n for n in layers if n not in copper_um]
    if missing:
        raise ValueError('no copper thickness for ' + ', '.join(missing))
    widths_mm = widths_mm or {}
    unknown = sorted(set(widths_mm) - set(layers))
    if unknown:
        raise ValueError('width given for a layer not in --layers: ' + ', '.join(unknown))
    external = {n: n in ('F.Cu', 'B.Cu') for n in layers}
    t_mil = {n: copper_um[n] / UM_PER_MIL for n in layers}
    all_external = all(external.values())
    out = dict(method=method, layers=layers, copper_um={n: round(copper_um[n], 2) for n in layers},
               assumptions=[
                   'IPC-2152 generic-chart fit, no plane/thickness modifiers (conservative screening)'
                   if method == 'ipc2152' else 'IPC-2221 legacy formula (k 0.048 external, 0.024 internal)',
                   'parallel layers share current in proportion to cross-section (summed area)',
                   'widths are the narrowest neck of each layer; necks on different layers must overlap '
                   'in position to be counted together'],
               targets=[])
    if via:
        out['via'] = dict(drill_mm=via['drill'], plating_um=via['plating'],
                          area_mil2=round(via_area_mil2(via['drill'], via['plating']), 3))
        out['assumptions'].append('via barrel sized as an internal conductor with the same method')
    for tgt in targets:
        amps, rise = float(tgt['amps']), float(tgt['rise'])
        need = area_mil2(amps, rise, method, all_external)
        row = dict(name=tgt['name'], amps=amps, rise_c=rise, required_area_mil2=round(need, 1),
                   width_alone_mm={n: round(area_mil2(amps, rise, method, external[n]) / t_mil[n] * UM_PER_MIL / 1000, 3)
                                   for n in layers},
                   corridor_width_mm=round(need / sum(t_mil.values()) * UM_PER_MIL / 1000, 3))
        if widths_mm:
            area = sum(widths_mm[n] * 1000 / UM_PER_MIL * t_mil[n] for n in widths_mm)
            cap = capacity(area, rise, method, all_external)
            dt = rise_for(amps, area, method, all_external)
            row.update(area_mil2=round(area, 1), capacity_a=round(cap, 2),
                       rise_at_target_c=None if math.isinf(dt) else round(dt, 1),
                       margin=round(cap / amps, 3) if amps else None, ok=cap >= amps)
            if length_mm:
                r = resistance(length_mm, area, ambient + (0 if math.isinf(dt) else dt))
                row.update(length_mm=length_mm, resistance_mohm=round(r * 1000, 3),
                           drop_mv=round(amps * r * 1000, 1), dissipation_w=round(amps * amps * r, 3))
        if via:
            per = capacity(out['via']['area_mil2'], rise, method, external=False)
            row.update(amps_per_via=round(per, 3), vias_needed=max(1, math.ceil(amps / per)) if per else None)
            if via.get('count'):
                n = int(via['count'])
                each = amps / n
                row.update(via_count=n, amps_each_via=round(each, 3),
                           via_rise_c=round(rise_for(each, out['via']['area_mil2'], method, False), 1),
                           vias_ok=n >= row['vias_needed'])
        out['targets'].append(row)
    checks = [r['ok'] for r in out['targets'] if 'ok' in r] + [r['vias_ok'] for r in out['targets'] if 'vias_ok' in r]
    out['ok'] = all(checks) if checks else None
    return out


def parse_target(text: str, default_rise: float) -> dict:
    """'NAME=AMPS[@RISE]' or 'AMPS[@RISE]', e.g. 'continuous=15', 'peak=20@30'."""
    name, _, value = text.rpartition('=')
    amps, _, rise = value.partition('@')
    try:
        return dict(name=name or f'{float(amps):g}A', amps=float(amps), rise=float(rise) if rise else default_rise)
    except ValueError as error:
        raise ValueError(f'bad --target {text!r} (use NAME=AMPS[@RISE_C])') from error


def parse_layer_values(items: list[str] | None, convert) -> dict:
    out = {}
    for item in items or []:
        layer, sep, value = item.partition('=')
        if not sep or not layer:
            raise ValueError(f'expected LAYER=VALUE, got {item!r}')
        out[layer] = convert(value)
    return out


def format_text(r: dict) -> str:
    lines = [f"method {r['method']}; copper " + ', '.join(f'{n} {t:g} um' for n, t in r['copper_um'].items())]
    if 'via' in r:
        v = r['via']
        lines.append(f"via drill {v['drill_mm']:g} mm, plating {v['plating_um']:g} um, barrel {v['area_mil2']:g} mil^2")
    for t in r['targets']:
        lines.append(f"\n{t['name']}: {t['amps']:g} A at +{t['rise_c']:g} C -> need {t['required_area_mil2']:g} mil^2")
        lines.append('  one layer alone: ' + ', '.join(f'{n} {w:g} mm' for n, w in t['width_alone_mm'].items()))
        lines.append(f"  corridor, same width on {'+'.join(r['layers'])}: {t['corridor_width_mm']:g} mm")
        if 'capacity_a' in t:
            dt = 'off-chart' if t['rise_at_target_c'] is None else f"+{t['rise_at_target_c']:g} C"
            lines.append(f"  given widths: {t['area_mil2']:g} mil^2 -> {t['capacity_a']:g} A at +{t['rise_c']:g} C; "
                         f"at {t['amps']:g} A: {dt}; {'OK' if t['ok'] else 'FAIL'}")
            if 'drop_mv' in t:
                lines.append(f"  {t['length_mm']:g} mm: {t['resistance_mohm']:g} mOhm, {t['drop_mv']:g} mV, "
                             f"{t['dissipation_w']:g} W")
        if 'amps_per_via' in t:
            lines.append(f"  vias: {t['amps_per_via']:g} A each -> at least {t['vias_needed']} per layer change")
            if 'via_count' in t:
                lines.append(f"  {t['via_count']} vias: {t['amps_each_via']:g} A each, +{t['via_rise_c']:g} C; "
                             f"{'OK' if t['vias_ok'] else 'FAIL'}")
    lines += ['', 'assumptions: ' + '; '.join(r['assumptions'])]
    if r['ok'] is not None:
        lines.append('RESULT: ' + ('OK' if r['ok'] else 'FAIL'))
    return '\n'.join(lines)


def pour_neck(path: Path) -> float:
    """Narrowest neck (mm) from an ``ohmai layout pour --json`` result file."""
    import json
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if 'neck' not in data:
        raise ValueError(f'{path}: not an ohmai layout pour --json result (no "neck")')
    if not data['neck'] and int(data.get('pieces') or 0) > 1:
        necks = [d['neck'] for d in data.get('pieces_detail') or [] if d.get('neck')]
        raise ValueError(f'{path}: the pour has {data["pieces"]} separate pieces on this layer, so its neck is 0; '
                         f'pass --width LAYER=MM with the corridor piece\'s neck from pieces_detail {necks}')
    return float(data['neck'])

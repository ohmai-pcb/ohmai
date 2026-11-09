"""Native candidate-only refill/DRC guard; KiCad runs exclusively as subprocesses."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

_REFILL = """
import pcbnew as p, sys
p.GetSettingsManager().LoadProject(sys.argv[3])
b = p.LoadBoard(sys.argv[1])
if not p.ZONE_FILLER(b).Fill(b.Zones()):
    raise RuntimeError('zone refill failed')
p.SaveBoard(sys.argv[2], b)
"""


def unconnected_by_net(report: dict) -> dict[str, int]:
    counts = Counter()
    for violation in report.get('unconnected_items', []):
        for item in violation.get('items', []):
            match = re.search(r'\[([^]]+)\]', item.get('description', ''))
            if match and not match[1].startswith('<'):
                counts[match[1]] += 1
                break
    return dict(counts)


def _signature(v):
    return (v.get('type'), v.get('description'),
            tuple(sorted(i.get('uuid', i.get('description', '')) for i in v.get('items', []))))


def metrics(report: dict) -> dict:
    violations = report.get('violations', [])
    nonzone = [v for v in violations if not any(
        'Zone [' in i.get('description', '') for i in v.get('items', []))]
    return dict(unconnected=unconnected_by_net(report),
                nonzone_clearance=sum(v.get('type') == 'clearance' for v in nonzone),
                safety=sum('SAFETY' in v.get('description', '') for v in nonzone),
                connection_width=sum(v.get('type') == 'connection_width' for v in violations),
                shorts=sum(v.get('type') == 'shorting_items' for v in violations),
                crossings=sum(v.get('type') == 'tracks_crossing' for v in violations))


def compare(before: dict, after: dict, target_nets: set[str], floor: float,
            original_uuids: set[str]) -> dict:
    """Never replace an existing violation with a new routed-copper violation."""
    b, a = unconnected_by_net(before), unconnected_by_net(after)
    worse = {n: [b.get(n, 0), count] for n, count in a.items()
             if n not in target_nets and count > b.get(n, 0)}
    target_worse = {n: [b.get(n, 0), a.get(n, 0)] for n in target_nets
                    if a.get(n, 0) > b.get(n, 0)}
    old = {_signature(v) for v in before.get('violations', [])}
    narrow, illegal = [], []
    for v in after.get('violations', []):
        if _signature(v) in old:
            continue
        kind = v.get('type')
        if kind == 'connection_width':
            actual = re.search(r'actual ([\d.]+) mm', v.get('description', ''))
            if actual is None or float(actual[1]) < floor - 1e-7:
                narrow.append(v)
        elif kind in {'shorting_items', 'tracks_crossing', 'clearance', 'hole_clearance', 'track_width',
                      'copper_edge_clearance', 'hole_near_hole', 'via_diameter',
                      'annular_width', 'drill_out_of_range'}:
            # Refill can change which pair of old items KiCad chooses to report.
            # New-copper IDs distinguish real regressions from that old-pair noise.
            new_item = any(i.get('uuid') and i['uuid'] not in original_uuids
                           for i in v.get('items', []))
            if new_item or kind in {'shorting_items', 'tracks_crossing'}:
                illegal.append(v)
    return dict(ok=not (worse or target_worse or narrow or illegal),
                worse_nets=worse, target_worse=target_worse,
                new_connection_width=narrow, new_copper_violations=illegal,
                before=metrics(before), after=metrics(after), width_floor=floor)


def _report(board: Path, work: Path, floor: float) -> dict:
    work.mkdir()
    raw = work / 'input.kicad_pcb'
    filled = work / 'filled.kicad_pcb'
    shutil.copy2(board, raw)
    for ext in ('.kicad_pro', '.kicad_dru'):
        source = board.with_suffix(ext)
        if source.exists():
            shutil.copy2(source, filled.with_suffix(ext))
    project = filled.with_suffix('.kicad_pro')
    if not project.is_file():
        raise RuntimeError('native handroute verification requires the project rules')
    data = json.loads(project.read_text())
    rules = data.setdefault('board', {}).setdefault('design_settings', {}).setdefault('rules', {})
    if float(rules.get('min_connection') or 0) < floor:
        shutil.copy2(project, str(project) + '.bak.' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        rules['min_connection'] = floor
        project.write_text(json.dumps(data))
    python = os.environ.get('OHMAI_KICAD_PYTHON', '/usr/bin/python3')
    subprocess.run([python, '-c', _REFILL, str(raw), str(filled), str(project)],
                   check=True, capture_output=True, text=True, timeout=60)
    output = work / 'drc.json'
    subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all',
                    '-o', str(output), str(filled)], check=True, capture_output=True, text=True, timeout=60)
    return json.loads(output.read_text())


def validate(board: Path, candidate: Path, target_nets: set[str], floor: float) -> dict:
    """Compare two independently refilled scratch boards; never touch the source."""
    if not shutil.which('kicad-cli'):
        raise RuntimeError('native handroute verification needs kicad-cli; no copper written')
    original_uuids = set(re.findall(r'\(uuid\s+"?([^"\s)]+)', board.read_text()))
    with tempfile.TemporaryDirectory(prefix='ohmai-handroute-guard-') as directory:
        work = Path(directory)
        before = _report(board, work / 'before', floor)
        after = _report(candidate, work / 'after', floor)
    return compare(before, after, target_nets, floor, original_uuids)

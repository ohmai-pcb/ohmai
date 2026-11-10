# SPDX-License-Identifier: Apache-2.0
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
# Loading a board can replace the active project with a dummy. Attach the
# real project AFTER loading, or Fill/SaveBoard silently use default rules.
b = p.LoadBoard(sys.argv[1])
manager = p.GetSettingsManager()
if not manager.LoadProject(sys.argv[3]):
    raise RuntimeError('project settings could not be loaded')
b.SetProject(manager.Prj())
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


def _backup(path: Path) -> Path:
    dest = path.with_name(path.name + datetime.now().strftime('.%Y-%m-%d_%H%M.bak'))
    if dest.exists():
        dest = Path(tempfile.mkdtemp(prefix='backups-', dir=path.parent)) / dest.name
    shutil.copy2(path, dest)
    return dest


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
        _backup(project)
        rules['min_connection'] = floor
        project.write_text(json.dumps(data))
    # SaveBoard may serialize attached project settings as well as copper.
    _backup(project)
    python = os.environ.get('OHMAI_KICAD_PYTHON', '/usr/bin/python3')
    attempts = []
    for attempt in range(1, 4):
        # Each attempt starts from immutable input/rules. A failed native process
        # may have partially serialized output; never reuse it as the next input.
        if filled.exists():
            _backup(filled)
            shutil.copy2(filled, work / f'failed-filled-{attempt - 1}.kicad_pcb')
            filled.unlink()
        backup_project = _backup(project)
        try:
            proc = subprocess.run([python, '-c', _REFILL, str(raw), str(filled), str(project)],
                                  check=True, capture_output=True, text=True, timeout=60)
            attempts.append(dict(attempt=attempt, returncode=proc.returncode,
                                 stdout=proc.stdout, stderr=proc.stderr))
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            code = getattr(error, 'returncode', None)
            attempts.append(dict(attempt=attempt, returncode=code,
                                 stdout=str(error.stdout or ''), stderr=str(error.stderr or '')))
            if (work / 'refill-attempts.json').exists():
                _backup(work / 'refill-attempts.json')
            (work / 'refill-attempts.json').write_text(json.dumps(attempts, indent=2) + '\n')
            if project.exists():
                _backup(project)
            shutil.copy2(backup_project, project)
            # Retry only native SIGSEGV, not bad rules, tool absence or timeout.
            if code not in (-11, 139) or attempt == 3:
                raise
        else:
            if (work / 'refill-attempts.json').exists():
                _backup(work / 'refill-attempts.json')
            (work / 'refill-attempts.json').write_text(json.dumps(attempts, indent=2) + '\n')
            break
    saved = json.loads(project.read_text()).get('board', {}).get('design_settings', {}).get('rules', {})
    if float(saved.get('min_connection') or 0) < floor:
        raise RuntimeError('native refill lost minimum connection settings; no copper written')
    output = work / 'drc.json'
    subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all',
                    '-o', str(output), str(filled)], check=True, capture_output=True, text=True, timeout=180)
    return json.loads(output.read_text())


def validate(board: Path, candidate: Path, target_nets: set[str], floor: float,
             evidence_dir: Path | None = None) -> dict:
    """Compare scratch refills; retain full before/after JSON even on rejection.

    Callers staging candidates in a TemporaryDirectory must supply a durable
    evidence_dir. Each invocation gets a unique directory, including failed DRC
    runs, so a rollback or a subsequent route cannot destroy the evidence.
    """
    if not shutil.which('kicad-cli'):
        raise RuntimeError('native handroute verification needs kicad-cli; no copper written')
    original_uuids = set(re.findall(r'\(uuid\s+"?([^"\s)]+)', board.read_text()))
    root = Path(evidence_dir) if evidence_dir is not None else candidate.parent / 'handroute-verification'
    root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=datetime.now().strftime('%Y%m%d-%H%M%S-'), dir=root))
    # Keep the proposed copper even if baseline refill crashes before after-DRC.
    for source, name in ((board, 'reference'), (candidate, 'proposal')):
        for ext in ('.kicad_pcb', '.kicad_pro', '.kicad_dru'):
            src = source.with_suffix(ext)
            if src.is_file():
                shutil.copy2(src, work / (name + ext))
    try:
        before = _report(board, work / 'before', floor)
        after = _report(candidate, work / 'after', floor)
    except Exception as error:
        (work / 'failure.json').write_text(json.dumps(dict(
            error=str(error), stdout=str(getattr(error, 'stdout', '') or ''),
            stderr=str(getattr(error, 'stderr', '') or '')), indent=2) + '\n')
        raise
    result = compare(before, after, target_nets, floor, original_uuids)
    result.update(evidence_dir=str(work), before_drc=str(work / 'before' / 'drc.json'),
                  after_drc=str(work / 'after' / 'drc.json'))
    (work / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    return result

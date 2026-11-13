# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Shared isolated refill: immutable, colocated rules; no post-load project swap."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from ohmai import sexp

# KiCad is GPL: execute this string ONLY in its own interpreter.
SCRIPT = '''
import faulthandler, json, os, sys
faulthandler.enable()
mode = sys.argv[4]
if mode != 'clean' and hasattr(os, 'sched_getaffinity'):
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
print(json.dumps({'stage': 'load', 'mode': mode,
                  'affinity': sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None}), flush=True)
import pcbnew as p
# LoadBoard creates the DRC engine and caches netclasses. All three input files
# MUST share a stem before this call. Never SetProject on the loaded board.
b = p.LoadBoard(sys.argv[1])
if b is None:
    raise RuntimeError('board load failed')
if p.ToMM(b.GetDesignSettings().m_MinConn) < float(sys.argv[3]) - 1e-7:
    raise RuntimeError('native board did not load the minimum connection rule')
print(json.dumps({'stage': 'loaded', 'version': p.GetBuildVersion()}), flush=True)
if mode == 'single-cpu-rebuild':
    b.SynchronizeNetsAndNetClasses(True)
    b.BuildConnectivity()
zones = b.Zones()
filler = p.ZONE_FILLER(b)
print(json.dumps({'stage': 'fill'}), flush=True)
if not filler.Fill(zones):
    raise RuntimeError('zone refill failed')
b.BuildConnectivity()
print(json.dumps({'stage': 'filled'}), flush=True)
# Do not serialize project settings or switch project objects while saving.
if not p.SaveBoard(sys.argv[2], b, True):
    raise RuntimeError('filled board save failed')
print(json.dumps({'stage': 'saved'}), flush=True)
'''


def backup(path: Path) -> Path:
    dest = path.with_name(path.name + datetime.now().strftime('.%Y-%m-%d_%H%M.bak'))
    if dest.exists():
        dest = Path(tempfile.mkdtemp(prefix='backups-', dir=path.parent)) / dest.name
    shutil.copy2(path, dest)
    return dest


def _write(path: Path, data):
    if path.exists():
        backup(path)
    path.write_text(json.dumps(data, indent=2) + '\n')


def _cycle(points):
    if points and points[0] == points[-1]:
        points = points[:-1]
    if not points:
        return ()
    smallest = min(points)
    choices = []
    for seq in (points, list(reversed(points))):
        for index, point in enumerate(seq):
            if point == smallest:
                choices.append(tuple(seq[index:] + seq[:index]))
    return min(choices)


def filled_polygons(board: Path) -> dict:
    """Canonical actual fill vertices in integer IU; ignore ordering/start/winding."""
    result = {}
    tree = sexp.parse(board.read_text())
    nets = {n[1]: n[2] for n in sexp.find(tree, 'net') if len(n) > 2}
    for zone in sexp.find(tree, 'zone'):
        net = (sexp.first(zone, 'net_name') or ['', nets.get((sexp.first(zone, 'net') or ['', ''])[1], '')])[1]
        layers = (sexp.first(zone, 'layer') or sexp.first(zone, 'layers') or ['layers'])[1:]
        outlines = []
        for polygon in sexp.find(zone, 'polygon'):
            points = [(round(float(p[1]) * 1_000_000), round(float(p[2]) * 1_000_000))
                      for p in sexp.find(sexp.first(polygon, 'pts') or [], 'xy')]
            outlines.append(_cycle(points))
        # UUIDs may be generated afresh for anonymous input zones. Group by
        # actual zone geometry/net/layers; preserve duplicate-zone multiplicity.
        key = json.dumps([net, sorted(layers), sorted(outlines)])
        polygons = []
        for fill in sexp.find(zone, 'filled_polygon'):
            layer = (sexp.first(fill, 'layer') or ['', ''])[1]
            points = [(round(float(p[1]) * 1_000_000), round(float(p[2]) * 1_000_000))
                      for p in sexp.find(sexp.first(fill, 'pts') or [], 'xy')]
            polygons.append((layer, bool(sexp.first(fill, 'island')), _cycle(points)))
        result.setdefault(key, []).append(sorted(polygons))
    return {key: sorted(groups) for key, groups in result.items()}


def polygon_digest(board: Path) -> str:
    return hashlib.sha256(json.dumps(filled_polygons(board), sort_keys=True).encode()).hexdigest()


def _copy(source: Path, dest: Path):
    if dest.exists():
        if source.read_bytes() == dest.read_bytes():
            return
        backup(dest)
    shutil.copy2(source, dest)


def run(board: Path, destination: Path, evidence_dir: Path, floor: float = .127) -> dict:
    """Refill a scratch copy. Three fresh processes, progressively isolated fallbacks.

    Every attempt loads a complete, same-stem PCB/pro/dru triplet. No live input
    is modified; no crash output is reused. Destination receives only a verified
    successful fill. A failure retains all partial outputs and fails closed.
    """
    board, destination, evidence_dir = Path(board), Path(destination), Path(evidence_dir)
    if board.resolve() == destination.resolve():
        raise ValueError('refill destination must be a scratch copy, not the input board')
    if not board.with_suffix('.kicad_pro').is_file():
        raise RuntimeError('native refill requires the project rules alongside the input PCB')
    if destination.with_suffix('.kicad_dru').exists() and not board.with_suffix('.kicad_dru').exists():
        raise RuntimeError('destination has stale custom rules absent from input')
    evidence_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='refill-', dir=evidence_dir))
    snapshot = work / 'snapshot.kicad_pcb'
    for ext in ('.kicad_pcb', '.kicad_pro', '.kicad_dru'):
        src = board.with_suffix(ext)
        if src.exists():
            shutil.copy2(src, snapshot.with_suffix(ext))
    project = snapshot.with_suffix('.kicad_pro')
    data = json.loads(project.read_text())
    rules = data.setdefault('board', {}).setdefault('design_settings', {}).setdefault('rules', {})
    if float(rules.get('min_connection') or 0) < floor:
        backup(project)
        rules['min_connection'] = floor
        project.write_text(json.dumps(data))
    attempts = []
    log = evidence_dir / 'refill-attempts.json'
    python = os.environ.get('OHMAI_KICAD_PYTHON', '/usr/bin/python3')
    for index, mode in enumerate(('clean', 'single-cpu', 'single-cpu-rebuild'), 1):
        trial = work / f'attempt-{index}'
        trial.mkdir()
        raw, filled = trial / 'board.kicad_pcb', trial / 'filled.kicad_pcb'
        for ext in ('.kicad_pcb', '.kicad_pro', '.kicad_dru'):
            src = snapshot.with_suffix(ext)
            if src.exists():
                shutil.copy2(src, raw.with_suffix(ext))
        row = dict(attempt=index, mode=mode, directory=str(trial))
        try:
            proc = subprocess.run([python, '-c', SCRIPT, str(raw), str(filled), str(floor), mode],
                                  check=True, capture_output=True, text=True, timeout=60)
            row.update(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
            # SaveBoard(skipSettings=True) must not mutate its input project.
            if raw.with_suffix('.kicad_pro').read_bytes() != project.read_bytes():
                raise RuntimeError('native refill modified immutable project settings')
            if not filled.is_file():
                raise RuntimeError('native refill returned success without a filled PCB')
            row['filled_polygon_sha256'] = polygon_digest(filled)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            code = getattr(error, 'returncode', None)
            row.update(returncode=code, stdout=str(error.stdout or ''), stderr=str(error.stderr or ''))
            attempts.append(row)
            _write(log, attempts)
            if code not in (-11, 139) or index == 3:
                raise
        except Exception as error:
            row['error'] = str(error)
            attempts.append(row)
            _write(log, attempts)
            raise
        else:
            attempts.append(row)
            _write(log, attempts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            for ext in ('.kicad_pro', '.kicad_dru'):
                src = snapshot.with_suffix(ext)
                if src.exists():
                    _copy(src, destination.with_suffix(ext))
            _copy(filled, destination)
            result = dict(ok=True, attempts=attempts, evidence_dir=str(work),
                          filled_polygon_sha256=row['filled_polygon_sha256'])
            _write(evidence_dir / 'refill-result.json', result)
            return result
    raise RuntimeError('no native refill succeeded')

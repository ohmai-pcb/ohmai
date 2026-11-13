# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Versioned, stale-copper-aware layout transactions with durable native evidence."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import jsonschema

from ohmai import sexp
from ohmai.jsonout import last_json_object
from ohmai.layout import native_guard
from ohmai.layout.changes_worker import SCRIPT


class ChangeError(ValueError):
    pass


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def backup(path: Path) -> Path:
    """Never overwrite a previous backup, even twice within one minute."""
    name = path.name + datetime.now().strftime('.%Y-%m-%d_%H%M.bak')
    dest = path.with_name(name)
    if dest.exists():
        folder = Path(tempfile.mkdtemp(prefix='backups-', dir=path.parent))
        dest = folder / name
    shutil.copy2(path, dest)
    return dest


def _fingerprints(path: Path) -> dict:
    tree = sexp.parse(path.read_text())
    nets = {n[1]: n[2] for n in sexp.find(tree, 'net') if len(n) > 2}

    def canonical(node):
        if not isinstance(node, list):
            try:
                return float(node)
            except ValueError:
                return node
        if node and node[0] == 'net':
            return ['net', nets.get(node[1], node[1])]
        return [canonical(c) for c in node if not isinstance(c, list) or not c or c[0] not in
                ('filled_polygon', 'fill_segments', 'uuid', 'tstamp')]

    found = {}

    def record(node, frame=()):
        key = sexp.first(node, 'uuid') or sexp.first(node, 'tstamp')
        if key:
            found[key[1]] = hashlib.sha256(json.dumps([canonical(node), frame], sort_keys=True).encode()).hexdigest()

    for kind in ('via', 'segment', 'arc', 'zone'):
        for item in sexp.find(tree, kind):
            record(item)
    for fp in sexp.find(tree, 'footprint'):
        record(fp)
        frame = [canonical(sexp.first(fp, k) or []) for k in ('at', 'layer', 'locked')]
        for pad in sexp.find(fp, 'pad'):
            record(pad, frame)
    return found


def _check_zone_polygons(op: dict) -> None:
    """Refuse self-intersecting or zero-area zone outlines before any native work."""
    from shapely.geometry import Polygon
    from shapely.validation import explain_validity

    polys = []
    if op['op'] == 'zone.add':
        polys.append(('polygon', op['polygon']))
    elif op['op'] == 'zone.edit' and 'polygon' in op.get('set', {}):
        polys.append(('set.polygon', op['set']['polygon']))
    for where, pts in polys:
        poly = Polygon([tuple(q) for q in pts])
        if not poly.is_valid:
            raise ChangeError(f"operation {op['id']}: {where} is not a simple polygon "
                              f"({explain_validity(poly)}); list the vertices in outline order without crossings")
        if poly.area <= 1e-6:
            raise ChangeError(f"operation {op['id']}: {where} has zero area")


def load(path: Path, board: Path, clearance: float) -> dict:
    def reject_constant(value):
        raise ChangeError('non-finite JSON number: ' + value)

    data = json.loads(path.read_text(), parse_constant=reject_constant)
    schema = json.loads(Path(__file__).with_name('change-list.schema.json').read_text())
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(data))
    if errors:
        raise ChangeError('invalid change list: ' + '; '.join(e.message for e in errors))
    for op in data['operations']:
        _check_zone_polygons(op)
    ids = [op['id'] for op in data['operations']]
    if len(ids) != len(set(ids)):
        raise ChangeError('operation ids must be unique')
    base = (path.parent / data['base']['board_file']).resolve()
    if not base.is_file() or digest(base) != data['base']['sha256']:
        raise ChangeError('base board snapshot missing or SHA-256 mismatch')
    if base == board.resolve():
        raise ChangeError('base snapshot must be separate from the mutable board')
    data['base_path'] = str(base)
    data['base_fingerprints'] = _fingerprints(base)
    data['current_fingerprints'] = _fingerprints(board)
    data['new_copper'] = [key for key, value in data['current_fingerprints'].items()
                          if data['base_fingerprints'].get(key) != value]
    data['clearance'] = clearance
    return data


def _native(data: dict, board: Path, candidate: Path, work: Path) -> dict:
    if not board.with_suffix('.kicad_pro').is_file():
        raise ChangeError('native apply requires same-stem project rules before LoadBoard')
    request = work / 'request.json'
    request.write_text(json.dumps(data))
    proc = subprocess.run([os.environ.get('OHMAI_KICAD_PYTHON', '/usr/bin/python3'),
                           '-c', SCRIPT, str(request), str(board), str(candidate)],
                          text=True, capture_output=True, timeout=120)
    (work / 'native.stdout').write_text(proc.stdout)
    (work / 'native.stderr').write_text(proc.stderr)
    if not proc.stdout.strip():
        raise ChangeError(f'native apply failed (exit {proc.returncode}); see {work}')
    try:
        result = last_json_object(proc.stdout)
    except ValueError as error:
        raise ChangeError(f'invalid native response; see {work}') from error
    if proc.returncode and result.get('ok'):
        raise ChangeError(f'native apply failed (exit {proc.returncode}); see {work}')
    return result


def apply(cfg: dict, path: Path, dry_run: bool = False) -> dict:
    """Stage everything, refill both sides, guard, then atomically replace PCB only."""
    board = Path(cfg['board']).resolve()
    path = path.resolve()
    root = Path(cfg['work_dir']) / 'apply-changes'
    root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=datetime.now().strftime('%Y%m%d-%H%M%S-'), dir=root))
    result = dict(ok=False, applied=False, dry_run=dry_run, evidence_dir=str(work), conflicts=[])
    command = f'ohmai layout apply-changes {shlex.quote(str(path))}'
    (work / 'RESULTS.md').write_text(f'# Layout change transaction\n\nApply command: `{command}`\n\n'
                                   'Full validation (no board write): append ` --dry-run`.\n')
    try:
        sources = [board.with_suffix(ext) for ext in ('.kicad_pcb', '.kicad_pro', '.kicad_dru')]
        originals = {src: digest(src) if src.exists() else None for src in sources}
        floor = max(0.127, float(cfg.get('rules_resolved', {}).get('min_connection', 0)))
        rules = cfg.get('rules_resolved', {})
        data = load(path, board, float(rules.get('clearance', 0.15)))
        classes = rules.get('classes', {})
        tree = sexp.parse(board.read_text())
        data['net_clearances'] = {}
        for node in sexp.find(tree, 'net'):
            if len(node) < 3:
                continue
            net = node[2]
            names = [name for pattern, name in rules.get('patterns', []) if fnmatch.fnmatchcase(net, pattern)]
            data['net_clearances'][net] = max([float(classes.get(name, {}).get('clearance', data['clearance']))
                                               for name in names] or [data['clearance']])
        candidate = work / 'candidate.kicad_pcb'
        # Snapshot source too: evidence and a stable native input, never the live board.
        reference = work / 'reference.kicad_pcb'
        for ext in ('.kicad_pcb', '.kicad_pro', '.kicad_dru'):
            src = board.with_suffix(ext)
            if src.exists():
                shutil.copy2(src, reference.with_suffix(ext))
                if ext != '.kicad_pcb':
                    shutil.copy2(src, candidate.with_suffix(ext))
        result.update(_native(data, reference, candidate, work))
        result['evidence_dir'] = str(work)
        if not result['ok']:
            return result
        guard = native_guard.validate(reference, candidate, set(), floor, evidence_dir=work / 'guard')
        # Unlike the router's pair-identity noise tolerance, a change transaction
        # also refuses any aggregate regression, even involving existing UUIDs.
        before = json.loads(Path(guard['before_drc']).read_text())
        after = json.loads(Path(guard['after_drc']).read_text())
        b, a = native_guard.metrics(before), native_guard.metrics(after)
        for name, report in [('before', before), ('after', after)]:
            (b if name == 'before' else a)['hole_clearance'] = sum(
                v.get('type') in ('hole_clearance', 'hole_near_hole') for v in report.get('violations', []))
        regressions = {key: [b[key], a[key]] for key in b if key != 'unconnected' and a[key] > b[key]}
        data['new_copper'] = result.get('newer_copper', data['new_copper'])
        created = result.get('created_items', {})
        id_by_uuid = {key: op for op, keys in created.items() for key in keys}
        for violation in after.get('violations', []):
            keys = {item.get('uuid') for item in violation.get('items', [])}
            newer = sorted(keys & set(data['new_copper']))
            affected = sorted({id_by_uuid[key] for key in keys & id_by_uuid.keys()})
            if newer and affected:
                result['conflicts'].append(dict(ids=affected, copper_uuids=newer,
                                                reason='native DRC conflict with copper added/changed since base commit',
                                                violation=violation))
        result.update(ok=guard['ok'] and not regressions and not result['conflicts'], guard=guard, before=b, after=a,
                      aggregate_regressions=regressions)
        # Record after-DRC BEFORE deciding rollback, including every rejected net.
        (work / 'after-drc.json').write_text(json.dumps(after, indent=2) + '\n')
        (work / 'before-drc.json').write_text(json.dumps(before, indent=2) + '\n')
        if result['ok'] and not dry_run:
            if any((digest(src) if src.exists() else None) != sha for src, sha in originals.items()):
                raise ChangeError('board/project/rules changed during validation; no copper written')
            result['backup'] = str(backup(board))
            # Same-filesystem temporary file makes replacement atomic; saved PCB
            # is the *refilled and measured* candidate, not the unfilled proposal.
            fd, temp = tempfile.mkstemp(prefix='.apply-', suffix='.kicad_pcb', dir=board.parent)
            os.close(fd)
            try:
                shutil.copy2(Path(guard['after_drc']).with_name('filled.kicad_pcb'), temp)
                os.replace(temp, board)
            finally:
                Path(temp).unlink(missing_ok=True)
            result['applied'] = True
        return result
    except Exception as error:
        result.update(ok=False, error=str(error))
        return result
    finally:
        (work / 'result.json').write_text(json.dumps(result, indent=2) + '\n')

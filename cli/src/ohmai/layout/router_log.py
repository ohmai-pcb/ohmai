# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Append-only, project-local Freerouting evidence. Never promotes SES copper."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from .native_guard import metrics


def _version(command):
    try:
        p = subprocess.run(command, capture_output=True, text=True, timeout=3)
        return (p.stdout + p.stderr).strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def _jar_version(path):
    try:
        with zipfile.ZipFile(path) as jar:
            text = jar.read('META-INF/MANIFEST.MF').decode(errors='replace')
            match = re.search(r'(?m)^(?:Implementation-Version|Bundle-Version):\s*(.+)', text)
            if match:
                return match[1].strip()
    except (OSError, KeyError, zipfile.BadZipFile):
        pass
    match = re.search(r'(\d+\.\d+\.\d+)', str(path))
    return match[1] if match else None


def append_run(root: Path, cfg: dict, prepared: dict, run: dict, options: dict,
               error: str | None = None, prepare_only: bool = False) -> dict:
    """Log failed preparation, exceptions, prepare-only and completed sessions alike.

    Missing DRC measurements remain null, never zero or an implied clean board.
    Optional before/after JSONs must describe the staged import, not merely SES.
    """
    root = Path(root)
    log_dir = root / 'out' / 'routing'
    log_dir.mkdir(parents=True, exist_ok=True)
    identifier = datetime.now().astimezone().strftime('%Y%m%d-%H%M%S-%f') + '-' + uuid.uuid4().hex[:8]
    artifacts = log_dir / identifier
    artifacts.mkdir()
    evidence = {}
    for key in ('raw_dsn', 'patched_dsn', 'ses_out'):
        source = Path(prepared[key]) if prepared.get(key) else None
        if key == 'ses_out' and not run.get('ses_exists'):
            continue
        if source and source.is_file():
            target = artifacts / (key + source.suffix)
            shutil.copy2(source, target)
            evidence[key] = dict(path=str(target.relative_to(root)), sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    text = '\n'.join(str(run.get(k) or '') for k in ('stdout_tail', 'stderr_tail'))
    (artifacts / 'router-tails.txt').write_text(text, encoding='utf-8')
    signature = run.get('stack_signature') or '\n'.join(line.strip() for line in text.splitlines()
                          if 'Exception' in line or 'merge_entries_in_front' in line or 'combine_at_start' in line)
    if signature:
        evidence['stack_signature'] = signature
    measurements = {}
    measurement_errors = []
    for key in ('drc_before', 'drc_after'):
        name = options.get(key)
        if name:
            source = Path(name) if Path(name).is_absolute() else root / name
            try:
                report = json.loads(source.read_text())
                if not all(isinstance(report.get(k), list) for k in ('violations', 'unconnected_items')):
                    raise ValueError('not a full KiCad JSON DRC report')
                target = artifacts / (key + '.json')
                shutil.copy2(source, target)
                measurements[key] = metrics(report)
            except (OSError, ValueError) as exc:
                measurement_errors.append(f"{key}: {exc}")
                measurements[key] = None
        else:
            measurements[key] = None
    before, after = measurements.values()
    delta = None
    if before is not None and after is not None:
        delta = {k: after[k] - before[k] for k in ('nonzone_clearance', 'safety', 'connection_width', 'shorts', 'crossings')}
        delta['unconnected'] = sum(after['unconnected'].values()) - sum(before['unconnected'].values())
    worse_nets = {} if before is None or after is None else {
        n: [before['unconnected'].get(n, 0), count]
        for n, count in after['unconnected'].items() if count > before['unconnected'].get(n, 0)}
    gate = None if delta is None else all(delta[k] <= 0 for k in
        ('nonzone_clearance', 'safety', 'connection_width', 'shorts', 'crossings')) and not worse_nets
    board = Path(cfg['board']) if cfg.get('board') else None
    custom_rules = board is not None and board.with_suffix('.kicad_dru').is_file()
    crash = bool('freerouting_npe' in run.get('fail_reasons', []) or
                 re.search(r'(?:java\.lang\.\w*Exception|Exception in thread)', text))
    rule_gap = bool(custom_rules or (delta and any(delta[k] > 0 for k in ('nonzone_clearance', 'safety'))))
    java = run.get('java') or prepared.get('java') or 'java'
    jar = prepared.get('jar')
    record = dict(schema_version=1, id=identifier, date=datetime.now().astimezone().date().isoformat(),
                  board=str(board.relative_to(root) if board and board.is_relative_to(root) else board) if board else None,
                  board_sha256=hashlib.sha256(board.read_bytes()).hexdigest() if board and board.is_file() else None,
                  board_commit=_git_commit(root), freerouting_version=_jar_version(jar) if jar else None,
                  jdk_version=_version([java, '-version']),
                  nets=prepared.get('nets', options.get('nets')), exclude=prepared.get('exclude', options.get('exclude_nets')),
                  options={k: prepared.get(k, options.get(k)) for k in ('passes', 'opt_passes', 'timeout', 'threads', 'via_costs')},
                  prepare_only=prepare_only, ok=bool(run.get('ok')) and not error,
                  error=error, measurement_errors=measurement_errors, command=prepared.get('cmd'),
                  ignored_options=run.get('ignored_options', [line for line in text.splitlines() if 'Unknown settings property' in line]),
                  fail_reasons=run.get('fail_reasons', []), wires=run.get('ses_route_wires'), vias=run.get('ses_route_vias'),
                  **measurements, drc_delta=delta, drc_status='measured' if delta is not None else 'pending staged import/refill/DRC',
                  upstream_candidate=crash or rule_gap, crash=crash, rule_gap=rule_gap,
                  ignored_rule='custom KiCad .kicad_dru rules are not fully exported to DSN' if custom_rules else None,
                  promotion_allowed=False, drc_gate_passed=gate, worse_nets=worse_nets, artifacts=evidence,
                  minimal_repro='Use archived patched DSN with recorded options; reduce fixed-wire chains without changing width/layer/net/vertices.')
    # One lock covers both append-only views; old entries are never rewritten.
    with (log_dir / '.log.lock').open('a') as lock:
        if __import__('os').name == 'nt':
            import msvcrt

            lock.write('0')
            lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
        with (log_dir / 'freerouting.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, sort_keys=True) + '\n')
        with (log_dir / 'freerouting-log.md').open('a', encoding='utf-8') as stream:
            stream.write(f"- {record['date']} `{identifier}`: {'OK' if record['ok'] else 'NOT VERIFIED'}; "
                         f"wires/vias={record['wires']}/{record['vias']}; DRC={record['drc_status']}; "
                         f"delta={record['drc_delta']}; gate={record['drc_gate_passed']}; "
                         f"upstream_candidate={record['upstream_candidate']} ([artifacts]({identifier}/)).\n")
    return record


def _git_commit(root):
    try:
        return subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], check=True,
                              capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None

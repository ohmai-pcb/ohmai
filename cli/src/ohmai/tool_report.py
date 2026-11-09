"""Local close-out draft from routing logs; never files an upstream issue."""
from __future__ import annotations

import json
from pathlib import Path

from . import __version__
from .project import find_project


def render(root: Path) -> str:
    lines = ['<!-- ohmai:template docs/tool-issues.md -->', '# Tool issue report — review required', '',
             f'- OhmAI version: {__version__}',
             '- Complete inventory of every tool used, including tools with no observed issues.',
             '- Review the tool-issues template fields before removing the template marker.', '',
             '## Inventory', '', '| Tool | Version/evidence | Issues or explicit none observed |',
             '|---|---|---|', f'| OhmAI | {__version__} | Review required |',
             '| KiCad/kicad-cli/pcbnew | Record exact versions | Review required |',
             '| Library-import tool | Record used/not used and version | Review required |',
             '| JLC/LCSC helpers | Record used/not used and version | Review required |',
             '| Other project tools | Add all used tools/versions | Review required |', '']
    logs = root / 'out' / 'routing'
    for path in sorted(logs.glob('*.jsonl')):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            try:
                r = json.loads(line)
            except ValueError:
                lines.append(f'Unparsed evidence: `{path.relative_to(root)}:{number}` — inspect manually.')
                continue
            tool = r.get('tool', 'Freerouting')
            version = r.get('tool_version', r.get('freerouting_version')) or 'version unavailable'
            ident = r.get('id', f'line-{number}')
            failures = [f for f in r.get('fail_reasons', []) if f != 'prepare_only_not_routed']
            if not (r.get('upstream_candidate') or r.get('crash') or r.get('rule_gap') or r.get('error') or failures):
                lines += [f'## Tool run evidence — {tool} {version}',
                          f'- Run `{ident}` at `{path.relative_to(root)}:{number}`: no issue flagged by this run; review inventory completeness.', '']
                continue
            lines += [f'## {tool} — {ident}', '', f'- Tool/version: {tool} {version}; JDK {r.get("jdk_version") or "unrecorded"}',
                      f'- Severity: {"high" if r.get("crash") else "medium"} — review required.',
                      f'- Symptom: {r.get("error") or r.get("fail_reasons") or r.get("ignored_rule") or "No failure recorded; review limits."}',
                      f'- Evidence: `{path.relative_to(root)}:{number}`; `{json.dumps(r.get("artifacts", {}), sort_keys=True)}`',
                      f'- Minimal repro: {r.get("minimal_repro") or "Reduce retained artifacts and record commands."}',
                      '- Project impact: record affected outputs/nets and actual blocking or safety impact.',
                      '- Workaround actually used: record it, or explicitly none.',
                      f'- Fixed status/commit: {r.get("fix_commit") or "not established; review required"}.',
                      f'- Upstream-ready title: {tool} {version}: {"routing crash" if r.get("crash") else "routing/rule limitation"}',
                      '- Upstream-ready body:',
                      f'  - Environment/version: {tool} {version}; JDK {r.get("jdk_version") or "unrecorded"}.',
                      f'  - Reproduce using scoped nets `{r.get("nets")}`, excludes `{r.get("exclude")}`, options `{r.get("options")}` and retained DSN.',
                      f'  - Actual: wires/vias {r.get("wires")}/{r.get("vias")}; DRC delta `{r.get("drc_delta")}`.',
                      f'  - Stack: `{r.get("artifacts", {}).get("stack_signature", "not recorded")}`.',
                      '  - Expected: route without crashes and preserve native design rules/connectivity.',
                      '  - Complete reduction, impact, workaround and fix evidence before filing.',
                      '- Filing: draft only; owner approval required before sending or sharing artifacts.', '']
    lines += ['## Final checklist', '', '- [ ] Every used tool and every issue/limit reviewed.',
              '- [ ] Repros, impact, actual workaround, severity, fix commit and upstream title/body complete.',
              '- [ ] Updated for the final project revision; owner reviewed; no filing assumed.', '']
    return '\n'.join(lines)


def command(args) -> int:
    project = find_project(Path.cwd())
    text = render(project.root)
    if args.output:
        target = (project.root / args.output).resolve()
        if not target.is_relative_to(project.root.resolve()):
            raise ValueError('report output must remain inside the project')
        target.parent.mkdir(parents=True, exist_ok=True)
        # Never clobber a completed report or owner edits.
        with target.open('x', encoding='utf-8') as stream:
            stream.write(text)
        print(f'tool report draft: {target.relative_to(project.root)} (review required)')
    else:
        print(text)
    return 0


def add_parser(sub):
    parser = sub.add_parser('report', help='mandatory project close-out reports (local drafts only)')
    reports = parser.add_subparsers(dest='report_cmd', required=True)
    tools = reports.add_parser('tools', help='pre-fill the all-tool issue report from retained run logs')
    tools.add_argument('--output', help='new project-relative draft path; existing files are never overwritten')
    tools.set_defaults(fn=command)

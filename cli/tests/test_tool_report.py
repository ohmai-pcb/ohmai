# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
import json
from argparse import Namespace

import pytest

from ohmai import phases, release, tool_report
from ohmai.checks.docs import check_doc
from ohmai.project import Project


def test_tool_report_draft_and_mandatory_gates(tmp_path):
    path=tmp_path/'out/routing'
    path.mkdir(parents=True)
    (path/'freerouting.jsonl').write_text(json.dumps(dict(id='fictional',freerouting_version='2.2.4',
        crash=True, artifacts={'patched_dsn': {'path':'out/repro.dsn'}}, nets='SIG'))+'\n')
    text=tool_report.render(tmp_path)
    assert '2.2.4' in text and 'Upstream-ready title' in text and 'out/repro.dsn' in text
    assert 'KiCad/kicad-cli/pcbnew' in text and 'JLC/LCSC' in text and 'Library-import' in text
    p=Project(tmp_path,{})
    report=tmp_path/'docs/tool-issues.md'
    report.parent.mkdir()
    report.write_text(text)
    assert check_doc(p,'docs/tool-issues.md').status=='FAIL'
    report.write_text('<!-- ohmai:not-applicable -->')
    assert check_doc(p,'docs/tool-issues.md').status=='FAIL'
    report.write_text('# Completed tool inventory and issues\nNo issues observed in the fictional test.')
    assert check_doc(p,'docs/tool-issues.md').status=='PASS'
    for phase in ('fab','bringup'):
        assert 'docs:docs/tool-issues.md' in phases.resolve(phase).checks
    assert 'docs:docs/tool-issues.md' in release.PREFLIGHT


def test_report_helper_never_overwrites_owner_report(tmp_path,monkeypatch):
    monkeypatch.setattr(tool_report,'find_project',lambda root: Project(tmp_path,{}))
    args=Namespace(output='docs/draft.md')
    assert tool_report.command(args)==0
    with pytest.raises(FileExistsError):
        tool_report.command(args)


def test_report_draft_cannot_escape_project(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_report,'find_project',lambda root: Project(tmp_path,{}))
    with pytest.raises(ValueError, match='inside the project'):
        tool_report.command(Namespace(output='../outside.md'))


def test_successful_run_does_not_invent_an_issue(tmp_path):
    path = tmp_path/'out/routing'
    path.mkdir(parents=True)
    (path/'freerouting.jsonl').write_text(json.dumps(dict(id='clean',ok=True,freerouting_version='2.2.4'))+'\n')
    text = tool_report.render(tmp_path)
    assert 'no issue flagged' in text
    assert 'Upstream-ready title:' not in text

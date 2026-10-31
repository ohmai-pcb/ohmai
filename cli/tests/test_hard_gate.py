"""Schematic readability is a hard part of the review gate, and all sheets share one paper size."""

from __future__ import annotations

import ohmai.checks as checks
from ohmai.checks import FAIL, Result
from ohmai.checks import review as review_check
from ohmai.checks.readability import readability

from test_cli import _review_proj, _write_review, proj  # noqa: F401  (fixture)


def _with_readability(monkeypatch, status):
    real = checks.run

    def fake(p, name, opts):
        if name == "readability":
            return Result("readability", status, "3 problem(s)" if status == FAIL else "ok")
        return real(p, name, opts)

    monkeypatch.setattr(checks, "run", fake)
    monkeypatch.setitem(review_check.HARD_GATE_CHECKS, "constraints", ("readability",))


def test_review_gate_fails_on_readability_fail(proj, monkeypatch):
    from ohmai.review import start

    p = _review_proj(proj)
    start(p, "constraints")
    _write_review(p)
    _with_readability(monkeypatch, FAIL)
    r = review_check.check_review(p, "constraints")
    assert r.status == FAIL
    assert any("hard gate" in d and "not waivable" in d for d in r.details), r.details


def test_review_gate_passes_when_readability_passes(proj, monkeypatch):
    from ohmai.review import start

    p = _review_proj(proj)
    start(p, "constraints")
    _write_review(p)
    _with_readability(monkeypatch, "PASS")
    assert review_check.check_review(p, "constraints").status == "PASS"


def test_schematic_hard_gate_includes_readability():
    assert "readability" in review_check.HARD_GATE_CHECKS["schematic"]


def test_mixed_sheet_sizes_fail(tmp_path, monkeypatch):
    class P:
        pass

    hw = tmp_path / "hardware"
    hw.mkdir()
    root = hw / "top.kicad_sch"
    root.write_text('(kicad_sch (version 20250114) (paper "A4")\n (sheet (at 10 10) (size 20 20)\n'
                    '  (property "Sheetname" "a" (at 10 9 0)) (property "Sheetfile" "a.kicad_sch" (at 10 31 0))))\n')
    (hw / "a.kicad_sch").write_text('(kicad_sch (version 20250114) (paper "A3"))\n')
    p = P()
    p.schematic, p.config, p.out = root, {}, tmp_path / "out"
    r = readability(p, {})
    assert r.status == FAIL
    assert any("common paper size" in d for d in r.details), r.details


def test_review_start_writes_pack(proj):
    from ohmai.review import start

    p = _review_proj(proj)
    start(p, "constraints")
    pk = p.root / "out" / "review-pack" / "constraints"
    m = (pk / "MANIFEST.md").read_text()
    assert (pk / "BRIEF.md").is_file() and (pk / "resolutions.md").is_file()
    files = [x for x in pk.rglob("*") if x.is_file() and "files" in x.relative_to(pk).parts[:1]]
    assert files and all(f"files/{f.relative_to(pk / 'files').as_posix()}" in m for f in files)


def test_pack_works_for_every_gate(proj, monkeypatch):
    """Every reviewable phase gets a pack: MANIFEST, BRIEF, resolutions, files_hash inputs, sources."""
    import shutil as _sh
    from ohmai.review import DEFAULT_GATES, gates, pack, start

    monkeypatch.setattr(_sh, "which", lambda name: None)   # no renderers: packs must still be written
    p = _review_proj(proj)
    for ph, g in gates(p).items():
        for pat in list(g.scope) + list(g.sources):
            if "*" in pat:
                f = p.root / pat.replace("*.", "x.").replace("*", "x")
            else:
                f = p.root / pat
            if not f.exists():
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("{}\n" if f.suffix in (".yaml", ".yml") else "x\n")
    for ph in DEFAULT_GATES:
        if ph not in gates(p) or gates(p)[ph].required < 1:
            continue
        start(p, ph)
        pk = p.root / "out" / "review-pack" / ph
        m = (pk / "MANIFEST.md").read_text()
        assert (pk / "BRIEF.md").is_file() and "files/" in m, ph
        if gates(p)[ph].sources:
            assert "sources/" in m, ph
        out = pack(p, ph)
        assert out == pk

"""Toolchain lock: ohmai lock, check toolchain, kicad-app/kicad-libs against the lock, Python dep pins."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from ohmai import kicadlibs
from ohmai import toolchain as tc
from ohmai.checks import run
from ohmai.cli import main
from ohmai.project import find_project
from ohmai.versions import first_version

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def proj(tmp_path, monkeypatch):
    d = tmp_path / "demo"
    assert main(["new", str(d), "--idea", "x", "--no-git"]) == 0
    monkeypatch.chdir(d)
    libs = tmp_path / "libs"
    for sub in ("kicad-symbols-9.0.9.1", "kicad-footprints-9.0.9.1"):
        (libs / sub).mkdir(parents=True)
    (libs / "kicad-symbols-9.0.9.1" / "Device.kicad_sym").write_text("(kicad_symbol_lib)\n")
    (libs / "kicad-footprints-9.0.9.1" / "Resistor_SMD.pretty").mkdir()
    monkeypatch.setenv("OHMAI_KICAD_LIBS", str(libs))
    monkeypatch.setenv("OHMAI_CACHE", str(tmp_path / "cache"))
    p = find_project(d)
    kicadlibs.write_tables(p)
    return p


def fake(p, **over):
    """Detection results for every tool (no real tool is queried)."""
    out = {n: None for n in tc.tool_names()}
    out.update({
        "kicad-app": {"version": "9.0.2", "sha256": "a" * 64, "hash_scope": "machine"},
        "kicad-libs": {"version": "9.0.9.1", "sha256": "b" * 64, "hash_scope": "portable", "files": 2},
        "python": {"version": "3.12.1"}, "ohmai": {"version": "0.1.0"}, "git": {"version": "2.47.3"},
        "rsvg-convert": {"version": "2.60.0"},
    })
    for n in tc.tool_names():
        if n.startswith(tc.PY_PREFIX):
            out[n] = {"version": "1.0", "sha256": "c" * 64, "hash_scope": "machine"}
    out.update(over)
    return {"toolchain": out}


def locked(p, **over):
    data, changes, problems = tc.make_lock(p, fake(p, **over))
    assert not changes and not problems, (changes, problems)
    tc.write_lock(p, data)
    return find_project(p.root)


def test_no_lock_warns(proj):
    r = run(proj, "toolchain", fake(proj))
    assert r.status == "WARN" and "ohmai lock" in r.summary


def test_lock_roundtrip_pass(proj):
    p = locked(proj)
    lock = yaml.safe_load((p.root / tc.LOCK_NAME).read_text())
    assert lock["schema"] == tc.SCHEMA
    assert lock["tools"]["kicad-app"] == {"tier": 1, "version": "9.0.2", "sha256": "a" * 64, "hash_scope": "machine"}
    assert lock["tools"]["freerouting"] == {"tier": 1, "version": None}
    assert lock["tools"]["git"]["tier"] == 2 and lock["tools"]["git"]["range"] == ">=2.30"
    assert any(n.startswith("python:") for n in lock["tools"])
    r = run(p, "toolchain", fake(p))
    assert r.status == "PASS", r.details
    assert run(p, "kicad-app", fake(p, **{})).status == "PASS"


def test_tier1_mismatch_fails_everywhere(proj):
    p = locked(proj)
    opts = fake(p, **{"kicad-app": {"version": "9.0.3"}})
    r = run(p, "toolchain", opts)
    assert r.status == "FAIL" and any("9.0.3 != locked 9.0.2" in d for d in r.details)
    # same answer from the kicad-app check (one implementation), even though 9.0.3 is inside kicad.app
    r = run(p, "kicad-app", {**opts, "kicad_version": "9.0.3"})
    assert r.status == "FAIL" and "locked 9.0.2" in r.summary and "ohmai upgrade kicad-app 9.0.3" in r.summary
    pkg = next(n for n in tc.tool_names() if n.startswith("python:"))
    r = run(p, "toolchain", fake(p, **{pkg: {"version": "9.9"}}))
    assert r.status == "FAIL" and any(pkg in d for d in r.details)


def test_missing_and_hash(proj):
    p = locked(proj, kikit={"version": "1.7.2", "sha256": "d" * 64, "hash_scope": "machine"})
    assert run(p, "toolchain", fake(p, kikit=None)).status == "FAIL"           # locked tool not installed
    r = run(p, "toolchain", fake(p, kikit={"version": "1.7.2", "sha256": "e" * 64, "hash_scope": "machine"}))
    assert r.status == "WARN" and any("different build" in d for d in r.details)
    r = run(p, "toolchain", fake(p, **{"kicad-libs": {"version": "9.0.9.1", "sha256": "f" * 64,
                                                       "hash_scope": "portable"}}))
    assert r.status == "FAIL" and any("content hash differs" in d for d in r.details)
    # absent in the lock, installed now: ignored (not used by this design)
    assert run(p, "toolchain", fake(p, kikit={"version": "1.7.2", "sha256": "d" * 64}, kibot={"version": "1.8"})
               ).status == "PASS"


def test_tier2_range_warns(proj):
    p = locked(proj)
    r = run(p, "toolchain", fake(p, git={"version": "2.20.0"}))
    assert r.status == "WARN" and any("outside the helper range" in d for d in r.details)
    r = run(p, "toolchain", fake(p, git=None))
    assert r.status == "WARN"


def test_relock_refuses_tier1_change(proj):
    p = locked(proj)
    _, changes, _ = tc.make_lock(p, fake(p, **{"kicad-libs": {"version": "9.0.10", "sha256": "1" * 64}}))
    assert changes and "kicad-libs" in changes[0]
    _, changes, _ = tc.make_lock(p, fake(p, **{"kicad-libs": {"version": "9.0.9.1", "sha256": "2" * 64,
                                                               "hash_scope": "portable"}}))
    assert changes and "content hash changed" in changes[0]
    # tier-2 refresh keeps an owner-edited range
    lock = yaml.safe_load((p.root / tc.LOCK_NAME).read_text())
    lock["tools"]["git"]["range"] = ">=2.40"
    tc.write_lock(p, lock)
    data, changes, _ = tc.make_lock(p, fake(p, git={"version": "2.50.0"}))
    assert not changes and data["tools"]["git"] == {"tier": 2, "range": ">=2.40", "seen": "2.50.0"}


def test_lock_blocks_kicad_outside_policy_range(proj):
    _, _, problems = tc.make_lock(proj, fake(proj, **{"kicad-app": {"version": "9.1.0"}}))
    assert problems and "kicad.app" in problems[0]


def test_kicad_libs_check_reads_lock(proj):
    p = locked(proj)
    r = run(p, "kicad-libs", fake(p))
    assert r.status == "PASS", (r.summary, r.details)
    r = run(p, "kicad-libs", fake(p, **{"kicad-libs": {"version": "9.0.9.1", "sha256": "0" * 64,
                                                        "hash_scope": "portable"}}))
    assert r.status == "FAIL" and "content hash" in r.summary
    lock = yaml.safe_load((p.root / tc.LOCK_NAME).read_text())
    lock["tools"]["kicad-libs"]["version"] = "9.0.8"
    tc.write_lock(p, lock)
    r = run(p, "kicad-libs", fake(p))
    assert r.status == "FAIL" and "pins 9.0.8" in r.summary
    r = run(p, "toolchain", fake(p))
    assert r.status == "FAIL" and any("kicad.libraries is 9.0.9.1" in d for d in r.details)


def test_real_detection_smoke(proj):
    """Real detection of the cheap tools (no KiCad needed): Python deps come from the ohmai distribution."""
    deps = tc.python_deps()
    assert "PyYAML" in deps and "jsonschema" in deps
    assert tc.detect(proj, "python")["version"].startswith("3.")
    assert tc.tree_sha256([proj.root / "parts"]) == tc.tree_sha256([proj.root / "parts"])   # cached, stable


def test_first_version():
    assert first_version("kibot 1.8.2 - KiCad automation") == "1.8.2"
    assert first_version("git version 2.47.3") == "2.47.3"
    assert first_version("no digits") is None


def test_constraints_pin_every_runtime_dependency():
    text = (REPO / "cli" / "constraints.txt").read_text()
    pins = {m.group(1).lower(): m.group(2) for m in re.finditer(r"^([A-Za-z0-9._-]+)==(\S+)", text, re.M)}
    for dep in tc.python_deps():
        assert dep.lower() in pins, f"{dep} (ohmai runtime dependency) not pinned in cli/constraints.txt"
    lock = (REPO / "cli" / "requirements.lock").read_text()
    for name, ver in pins.items():
        assert re.search(rf"^{re.escape(name)}=={re.escape(ver)}\b", lock, re.M | re.I), f"{name} not in requirements.lock"
    assert "--hash=sha256:" in lock


def _jar(path: Path, impl: str) -> Path:
    import zipfile
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/MANIFEST.MF", f"Manifest-Version: 1.0\nImplementation-Version: {impl}\n")
    return path


def test_freerouting_unspecified_manifest_uses_file_name_and_records_java(proj, tmp_path, monkeypatch):
    jar = _jar(tmp_path / "freerouting-2.2.4.jar", "unspecified")      # as in the 2.1.0 ... 2.5.0 release jars
    java = tmp_path / "java"
    java.write_text('#!/bin/sh\necho \'openjdk version "25.0.4.1" 2026-08-18\' >&2\n')
    java.chmod(0o755)
    monkeypatch.setenv("FREEROUTING_JAR", str(jar))
    monkeypatch.setenv("FREEROUTING_JAVA", str(java))
    found = tc.detect_freerouting(proj)
    assert found["version"] == "2.2.4" and found["java"] == "25.0.4.1"
    e = tc.entry_for(proj, "freerouting", found)
    assert e["java"] == "25.0.4.1" and e["version"] == "2.2.4"
    assert tc.verify_entry("freerouting", e, found)[0] == "PASS"
    assert tc.verify_entry("freerouting", e, {**found, "java": "21.0.12.1"})[0] == "FAIL"
    assert tc.verify_entry("freerouting", e, {k: v for k, v in found.items() if k != "java"})[0] == "FAIL"


def test_freerouting_manifest_version_wins(proj, tmp_path, monkeypatch):
    monkeypatch.setenv("FREEROUTING_JAR", str(_jar(tmp_path / "fr.jar", "2.1.0")))
    monkeypatch.setenv("FREEROUTING_JAVA", str(tmp_path / "no-java"))
    found = tc.detect_freerouting(proj)
    assert found["version"] == "2.1.0" and "java" not in found

# SPDX-License-Identifier: Apache-2.0
"""Release tool pins: manifest, doctor mismatch messages, setup install/checksums, unpinned override."""

from __future__ import annotations

import zipfile
from pathlib import Path

from ohmai import pins
from ohmai.cli import main

REPO = Path(__file__).resolve().parents[2]


def _manifest() -> dict:
    return pins.load(REPO / "pinned-tools.yaml")


def test_shipped_manifest_pins_this_release():
    data = _manifest()
    assert data["schema"] == pins.SCHEMA
    tools = data["tools"]
    assert tools["freerouting"]["version"] == "2.5.0"
    assert tools["freerouting"]["url"].endswith("/v2.5.0/freerouting-2.5.0.jar")
    assert len(tools["freerouting"]["sha256"]) == 64
    assert tools["jdk"]["version"] == "25" and tools["jdk"]["match"] == "major"
    assert tools["kicad"]["version"] == "9.0.2"
    assert tools["kicad"]["range"] == "9.0.x"
    assert tools["easyeda2kicad"]["version"] == "1.0.1"
    assert pins.manifest_path().resolve() == (REPO / "pinned-tools.yaml").resolve()


def test_kicad_range_accepts_9_0_patches_not_9_1():
    pin = _manifest()["tools"]["kicad"]
    assert pins.version_matches("9.0.2", pin) and pins.version_matches("9.0.3", pin)
    assert not pins.version_matches("9.1.0", pin) and not pins.version_matches("8.0.9", pin)


def test_jdk_major_only():
    pin = _manifest()["tools"]["jdk"]
    assert pins.version_matches("25.0.4.1", pin) and pins.version_matches("25", pin)
    assert not pins.version_matches("21.0.12.1", pin) and not pins.version_matches("26.0.1", pin)


def _items(found, monkeypatch=None, cwd=None, **kw):
    return pins.check_all(cwd or Path.cwd(), found=found, **kw)


def test_mismatch_fails_with_expected_found_and_fix():
    found = {"kicad-cli": "8.0.9", "freerouting": "2.2.4", "jdk": "21.0.12", "easyeda2kicad": "0.8.0"}
    res = {r.name: r for r in _items(found)}
    assert res["kicad-cli"].status == "FAIL"
    assert "expected" in res["kicad-cli"].message and "9.0.2" in res["kicad-cli"].message
    assert "found 8.0.9" in res["kicad-cli"].message
    assert "ohmai setup" in res["kicad-cli"].message and "OHMAI_ALLOW_UNPINNED=1" in res["kicad-cli"].message
    assert res["freerouting"].status == "FAIL" and "found 2.2.4" in res["freerouting"].message
    assert res["jdk"].status == "FAIL" and "found 21.0.12" in res["jdk"].message
    assert res["easyeda2kicad"].status == "FAIL" and "found 0.8.0" in res["easyeda2kicad"].message


def test_missing_is_a_mismatch():
    found = {"kicad-cli": None, "freerouting": None, "jdk": None, "easyeda2kicad": None}
    res = _items(found)
    assert all(r.status == "FAIL" and "not found" in r.message and "ohmai setup" in r.message for r in res)


def test_allow_unpinned_env_downgrades_to_warning(monkeypatch):
    monkeypatch.setenv("OHMAI_ALLOW_UNPINNED", "1")
    found = {"kicad-cli": "9.1.0", "freerouting": "2.2.4", "jdk": "21.0.1", "easyeda2kicad": "0.8.0"}
    res = _items(found)
    assert all(r.status == "WARN" for r in res)
    assert all("OHMAI_ALLOW_UNPINNED=1" in r.message for r in res)


def test_allow_unpinned_project_config(tmp_path):
    found = {"kicad-cli": "9.1.0", "freerouting": "2.5.0", "jdk": "25.0.1", "easyeda2kicad": "1.0.1"}
    # no project -> FAIL
    assert next(r for r in _items(found, cwd=tmp_path) if r.name == "kicad-cli").status == "FAIL"
    (tmp_path / "ohmai.yaml").write_text("project: x\nhardware: {schematic: a, pcb: b}\n"
                                         "toolchain: {allow_unpinned: true}\n")
    (tmp_path / "STATUS.md").write_text("---\nphase: 1\ngate: open\n---\n")
    r = next(r for r in _items(found, cwd=tmp_path) if r.name == "kicad-cli")
    assert r.status == "WARN"


def test_matching_versions_pass():
    found = {"kicad-cli": "9.0.2", "freerouting": "2.5.0", "jdk": "25.0.4.1", "easyeda2kicad": "1.0.1"}
    res = _items(found)
    assert all(r.status == "PASS" for r in res), res


def test_freerouting_sha256_mismatch_fails(tmp_path):
    pin = dict(_manifest()["tools"]["freerouting"])
    res = pins.check_one("freerouting", pin, "2.5.0", digest="0" * 64)
    assert res.status == "FAIL" and "sha256 mismatch" in res.message
    res = pins.check_one("freerouting", pin, "2.5.0", digest=pin["sha256"])
    assert res.status == "PASS"


def _jar(path: Path, impl: str, payload: bytes = b"abc") -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/MANIFEST.MF", f"Manifest-Version: 1.0\nImplementation-Version: {impl}\n")
        z.writestr("payload.bin", payload)
    return path


def test_setup_fetches_jar_verifies_checksum_and_prints_notice(tmp_path, monkeypatch, capsys):
    pin = dict(_manifest()["tools"]["freerouting"])
    src = _jar(tmp_path / "upstream.jar", "unspecified")
    digest = pins.file_sha256(src)
    pin["url"] = src.resolve().as_uri()
    pin["sha256"] = digest
    pin["filename"] = "freerouting-2.5.0.jar"
    data = {"schema": pins.SCHEMA, "tools": {
        "freerouting": pin,
        "jdk": {"version": "25", "match": "major"},
        "kicad": {"version": "9.0.2", "range": "9.0.x"},
        "easyeda2kicad": {"version": "1.0.1", "pypi": "easyeda2kicad"},
    }}
    monkeypatch.setenv("OHMAI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(pins, "detect_kicad", lambda: ("9.0.2", "/usr/bin/kicad-cli"))
    monkeypatch.setattr(pins, "detect_jdk", lambda: ("25.0.4.1", str(tmp_path / "java")))
    monkeypatch.setattr(pins, "detect_easyeda", lambda: ("1.0.1", "easyeda2kicad"))
    monkeypatch.setattr(pins, "load", lambda path=None: data)
    # Avoid a real pip; easyeda already "installed".
    notices, errors = pins.install_all(data=data)
    assert not errors, errors
    dest = tmp_path / "cache/freerouting/freerouting-2.5.0.jar"
    assert dest.is_file() and pins.file_sha256(dest) == digest
    assert any(n.startswith("freerouting 2.5.0") and "sha256 ok" in n for n in notices)
    assert any(n.startswith("kicad 9.0.2") for n in notices)
    assert any(n.startswith("jdk 25.0.4.1") for n in notices)
    assert any("easyeda2kicad 1.0.1" in n for n in notices)

    # Wrong bytes at the destination are backed up, then replaced.
    dest.write_bytes(b"corrupt")
    notices, errors = pins.install_all(data=data)
    assert not errors
    assert pins.file_sha256(dest) == digest
    baks = list((tmp_path / "cache/freerouting").glob("freerouting-2.5.0.jar.*.bak"))
    assert len(baks) == 1 and baks[0].read_bytes() == b"corrupt"

    # A digest that does not match the downloaded file is rejected; dest stays.
    bad = dict(pin)
    bad["sha256"] = "a" * 64
    data["tools"]["freerouting"] = bad
    dest.unlink()
    notices, errors = pins.install_all(data=data)
    assert errors and "sha256 mismatch" in errors[0]
    assert not dest.is_file()


def test_setup_check_does_not_download(tmp_path, monkeypatch):
    data = _manifest()
    monkeypatch.setenv("OHMAI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(pins, "detect_kicad", lambda: (None, None))
    monkeypatch.setattr(pins, "detect_jdk", lambda: (None, None))
    monkeypatch.setattr(pins, "detect_easyeda", lambda: (None, None))
    monkeypatch.setattr(pins, "detect_freerouting", lambda pin=None: (None, None, None))
    notices, errors = pins.install_all(data=data, download=False)
    assert not list((tmp_path / "cache").rglob("*"))
    assert any("kicad:" in e for e in errors) and any("jdk:" in e for e in errors)


def test_doctor_includes_pin_results(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OHMAI_ALLOW_UNPINNED", raising=False)
    monkeypatch.setattr(pins, "check_all", lambda cwd=None, **k: [
        pins.PinResult("kicad-cli", "FAIL", "9.0.2 (range 9.0.x)", None,
                       "expected 9.0.2 (range 9.0.x), found not found. Fix: ohmai setup. "
                       "Or OHMAI_ALLOW_UNPINNED=1 to test this version"),
        pins.PinResult("freerouting", "PASS", "2.5.0", "2.5.0", "freerouting 2.5.0 matches pin 2.5.0"),
        pins.PinResult("jdk", "PASS", "25 (major)", "25.0.4.1", "jdk 25.0.4.1 matches pin 25 (major)"),
        pins.PinResult("easyeda2kicad", "PASS", "1.0.1", "1.0.1", "easyeda2kicad 1.0.1 matches pin 1.0.1"),
    ])
    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "FAIL  kicad-cli" in out and "expected 9.0.2" in out and "found not found" in out
    assert "Fix: ohmai setup" in out
    assert "PASS  freerouting" in out and "PASS  jdk" in out


def test_cli_setup_check_uses_install_all(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pins, "install_all", lambda **k: (["kicad 9.0.2  /usr/bin/kicad-cli"], []) if k.get("download") is False else (_ for _ in ()).throw(AssertionError("should not download")))
    monkeypatch.setattr(pins, "check_all", lambda cwd=None, **k: [
        pins.PinResult("kicad-cli", "PASS", "9.0.2", "9.0.2", "kicad-cli 9.0.2 matches pin 9.0.2"),
        pins.PinResult("freerouting", "PASS", "2.5.0", "2.5.0", "freerouting 2.5.0 matches pin 2.5.0"),
        pins.PinResult("jdk", "PASS", "25 (major)", "25.0.1", "jdk 25.0.1 matches pin 25 (major)"),
        pins.PinResult("easyeda2kicad", "PASS", "1.0.1", "1.0.1", "easyeda2kicad 1.0.1 matches pin 1.0.1"),
    ])
    assert main(["setup", "--check"]) == 0
    out = capsys.readouterr().out
    assert "kicad 9.0.2  /usr/bin/kicad-cli" in out
    assert "PASS  kicad-cli" in out

def _fake_java(path: Path, version: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\necho \'openjdk version "{version}" 2026-08-18\' >&2\n')
    path.chmod(0o755)
    return path


def _no_system_java(monkeypatch, tmp_path):
    monkeypatch.delenv("FREEROUTING_JAVA", raising=False)
    monkeypatch.delenv("OHMAI_FREEROUTING_JAVA", raising=False)
    monkeypatch.delenv("JAVA_HOME", raising=False)
    empty = tmp_path / "empty-bin"
    empty.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(empty))


def test_jdk_search_roots_include_known_locations():
    roots = pins.jdk_search_roots()
    assert Path("/workspace/.toolcache/jdk25") in roots
    assert any(r.as_posix().endswith("/.local/share/jdk25") for r in roots)
    assert any(r.name in {"jdk", "jdk25"} and "ohmai" in r.as_posix() for r in roots)


def test_discover_jdk_finds_local_share_and_toolcache_without_env(tmp_path, monkeypatch):
    _no_system_java(monkeypatch, tmp_path)
    local = _fake_java(tmp_path / "share/jdk25/java-25-openjdk-amd64/bin/java", "25.0.4.1")
    monkeypatch.setattr(pins, "jdk_search_roots", lambda: [tmp_path / "share/jdk25"])
    ver, exe = pins.discover_jdk(require_major="25")
    assert ver == "25.0.4.1" and Path(exe) == local.resolve()

    tool = _fake_java(tmp_path / "toolcache/jdk25/root/usr/lib/jvm/java-25-openjdk-amd64/bin/java", "25.0.1")
    monkeypatch.setattr(pins, "jdk_search_roots", lambda: [tmp_path / "toolcache/jdk25"])
    ver, exe = pins.discover_jdk(require_major="25")
    assert ver == "25.0.1" and Path(exe) == tool.resolve()


def test_discover_jdk_env_overrides_even_when_not_25(tmp_path, monkeypatch):
    _no_system_java(monkeypatch, tmp_path)
    _fake_java(tmp_path / "share/jdk25/bin/java", "25.0.4.1")
    j21 = _fake_java(tmp_path / "jdk21/bin/java", "21.0.12")
    monkeypatch.setattr(pins, "jdk_search_roots", lambda: [tmp_path / "share/jdk25"])
    monkeypatch.setenv("FREEROUTING_JAVA", str(j21))
    ver, exe = pins.discover_jdk(require_major="25")
    assert ver == "21.0.12" and Path(exe) == j21.resolve()


def test_discover_jdk_prefers_25_over_path_java_21(tmp_path, monkeypatch):
    monkeypatch.delenv("FREEROUTING_JAVA", raising=False)
    monkeypatch.delenv("OHMAI_FREEROUTING_JAVA", raising=False)
    monkeypatch.delenv("JAVA_HOME", raising=False)
    j21 = _fake_java(tmp_path / "bin/java", "21.0.12")
    j25 = _fake_java(tmp_path / "share/jdk25/bin/java", "25.0.4.1")
    monkeypatch.setenv("PATH", str(j21.parent))
    monkeypatch.setattr(pins, "jdk_search_roots", lambda: [tmp_path / "share/jdk25"])
    ver, exe = pins.discover_jdk(require_major="25")
    assert ver == "25.0.4.1" and Path(exe) == j25.resolve()


def test_user_data_path_is_ohmai_owned_per_version(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    path = pins.freerouting_user_data_path("2.5.0")
    assert path == tmp_path / "data/ohmai/freerouting/2.5.0/userdata"
    assert ".config/freerouting" not in path.as_posix()
    assert pins.freerouting_user_data_path().as_posix().endswith("/ohmai/freerouting/2.5.0/userdata")

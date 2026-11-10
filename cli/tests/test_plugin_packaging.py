# SPDX-License-Identifier: Apache-2.0
"""Host-independent packaging contracts; native host smoke tests remain separate."""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from ohmai import __version__, doctor, kicad
from ohmai.cli import main
from ohmai.resources import skill_dir

ROOT = Path(__file__).resolve().parents[2]


def _script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_canonical_skill_and_host_versions():
    assert (ROOT / "skill").is_symlink()
    assert skill_dir().resolve() == ROOT / "skills/ohmai"
    assert _script("validate_plugin").validate(ROOT, f"v{__version__}") == __version__
    with pytest.raises(ValueError, match="tag"):
        _script("validate_plugin").validate(ROOT, "v999.0.0")


def test_guidance_only_marketplace_payload(tmp_path):
    artifact = tmp_path / "marketplace"
    plugin = _script("package_plugin").build(ROOT, artifact)
    assert _script("validate_plugin").validate_marketplace(artifact) == __version__
    expected = {"plugin.json", ".grok-plugin/plugin.json", ".claude-plugin/plugin.json",
                ".cursor-plugin/plugin.json", "INSTALL.md", "LICENSE", "NOTICE"}
    actual = {str(p.relative_to(plugin)) for p in plugin.rglob("*") if p.is_file()}
    guidance = {str(p.relative_to(ROOT)) for p in (ROOT / "skills/ohmai").rglob("*.md")}
    assert actual == expected | guidance
    for src in (ROOT / "skills/ohmai").rglob("*.md"):
        assert (plugin / src.relative_to(ROOT)).read_bytes() == src.read_bytes()
    assert not any(p.is_symlink() for p in artifact.rglob("*"))
    # Rebuild cannot destroy a prior artifact or arbitrary non-git data.
    with pytest.raises(FileExistsError):
        _script("package_plugin").build(ROOT, artifact)


@pytest.mark.parametrize("bad", ["private.kicad_pcb", "freerouting.jar", "notes.md.bak.123"])
def test_payload_rejects_non_guidance_files(tmp_path, bad):
    source = tmp_path / "source"
    source.mkdir()
    package = _script("package_plugin")
    for name in package.FILES:
        dst = source / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, dst)
    shutil.copytree(ROOT / "skills", source / "skills")
    (source / "skills/ohmai" / bad).write_text("not distributable")
    with pytest.raises(ValueError, match="non-guidance"):
        package.build(source, tmp_path / "artifact")


def test_payload_rejects_skill_symlink(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    package = _script("package_plugin")
    for name in package.FILES:
        dst = source / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, dst)
    shutil.copytree(ROOT / "skills", source / "skills")
    (source / "skills/ohmai/external.md").symlink_to(tmp_path / "not-guidance")
    with pytest.raises(ValueError, match="symlink"):
        package.build(source, tmp_path / "artifact")


def test_host_manifest_drift_is_rejected(tmp_path):
    plugin = _script("package_plugin").build(ROOT, tmp_path / "artifact")
    path = plugin / ".grok-plugin/plugin.json"
    data = json.loads(path.read_text())
    data["version"] = "999.0.0"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="drift"):
        _script("validate_plugin").validate(plugin)


@pytest.mark.parametrize("found,version,status", [(None, None, "FAIL"), ("fake", None, "FAIL"),
                         ("fake", "8.0.9", "FAIL"), ("fake", "10.0.0", "FAIL"),
                         ("fake", "garbage", "FAIL"), ("fake", "9.0.2", "PASS"),
                         ("fake", "9.0.3", "PASS")])
def test_doctor_requires_working_kicad9(tmp_path, monkeypatch, found, version, status):
    from ohmai import pins
    monkeypatch.setattr(kicad, "kicad_cli", lambda: found)
    monkeypatch.setattr(kicad, "version", lambda: version)
    # Isolate the kicad-cli item from other pins on this machine.
    monkeypatch.setattr(pins, "detect_freerouting", lambda pin=None: ("2.5.0", None, None))
    monkeypatch.setattr(pins, "detect_jdk", lambda: ("25.0.4.1", "java"))
    monkeypatch.setattr(pins, "detect_easyeda", lambda: ("1.0.1", "easyeda2kicad"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OHMAI_ALLOW_UNPINNED", raising=False)
    item = next(item for item in doctor.run(tmp_path) if item.name == "kicad-cli")
    assert item.status == status
    if status == "FAIL":
        assert "expected" in item.message and "found" in item.message
        assert "ohmai setup" in item.message
        assert main(["doctor"]) == 1


def test_skill_force_keeps_timestamped_backup(tmp_path):
    dest = tmp_path / "installed"
    assert main(["skill", "install", "--dest", str(dest)]) == 0
    (dest / "owner-note.md").write_text("keep this")
    assert main(["skill", "install", "--dest", str(dest), "--force"]) == 0
    backups = list(tmp_path.glob("installed.bak.*"))
    assert len(backups) == 1
    assert (backups[0] / "owner-note.md").read_text() == "keep this"
    assert not (dest / "owner-note.md").exists()
    assert (dest / "SKILL.md").read_bytes() == (ROOT / "skills/ohmai/SKILL.md").read_bytes()


def test_skill_refuses_to_overwrite_canonical_source():
    assert main(["skill", "install", "--dest", str(ROOT / "skills/ohmai"), "--force"]) == 1


def test_skill_refuses_destination_symlink(tmp_path):
    dest = tmp_path / "link"
    dest.symlink_to(ROOT / "skills/ohmai", target_is_directory=True)
    assert main(["skill", "install", "--dest", str(dest), "--force"]) == 1


def test_project_agents_nudge_points_to_canonical_skill():
    text = (ROOT / "template/AGENTS.md").read_text()
    assert "ohmai skill path" in text
    assert "ohmai doctor" in text
    assert "Skill installation alone does not enable ERC/DRC" in text

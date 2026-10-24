"""`ohmai upgrade-guides`: re-pin `.ohmai/phases/` to the installed OhmAI version.

Phase guides are pinned per project so a project keeps the rules it started with. When the owner
wants the newer rules, this replaces the pinned copy, bumps `ohmai_version` in STATUS.md and, with
`add_missing`, copies template docs the project does not have yet (never overwrites).
"""

from __future__ import annotations

import filecmp
import re
import shutil
from datetime import date
from pathlib import Path

from ohmai import __version__
from ohmai.newproject import slugify
from ohmai.project import Project
from ohmai.resources import skill_dir, template_dir


def upgrade_guides(project: Project, dry_run: bool = False, add_missing: bool = False) -> list[str]:
    root = project.root
    src = skill_dir() / "phases"
    dst = root / ".ohmai" / "phases"
    log: list[str] = []
    for f in sorted(src.glob("*.md")):
        old = dst / f.name
        if not old.exists():
            log.append(f"add     .ohmai/phases/{f.name}")
        elif not filecmp.cmp(f, old, shallow=False):
            log.append(f"update  .ohmai/phases/{f.name}")
    if dst.is_dir():
        for old in sorted(dst.glob("*.md")):
            if not (src / old.name).exists():
                log.append(f"remove  .ohmai/phases/{old.name}")
    status = root / "STATUS.md"
    text = status.read_text(encoding="utf-8") if status.is_file() else ""
    new_text = re.sub(r'^(ohmai_version:\s*)"?[^"\n#]*"?', rf'\g<1>"{__version__}"', text, count=1, flags=re.M)
    if new_text != text:
        log.append(f"update  STATUS.md ohmai_version -> {__version__}")
    cfg_path = root / "ohmai.yaml"
    cfg_text = cfg_path.read_text(encoding="utf-8") if cfg_path.is_file() else ""
    new_cfg = re.sub(r'^(ohmai_version:\s*)"?[^"\n#]*"?', rf'\g<1>"{__version__}"', cfg_text, count=1, flags=re.M)
    if new_cfg != cfg_text:
        log.append(f"update  ohmai.yaml ohmai_version -> {__version__}")
    missing: list[tuple[Path, Path]] = []
    if add_missing:
        tpl = template_dir()
        for f in sorted((tpl / "docs").glob("*.md")):
            target = root / "docs" / f.name
            if not target.exists():
                missing.append((f, target))
                log.append(f"add     docs/{f.name} (from template)")
    if dry_run or not log:
        return log or [f"already pinned to OhmAI {__version__}"]
    if dst.is_dir():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    if new_text != text:
        status.write_text(new_text, encoding="utf-8")
    if new_cfg != cfg_text:
        cfg_path.write_text(new_cfg, encoding="utf-8")
    cfg = project.config.get("project") if isinstance(project.config, dict) else None
    name = str(cfg.get("name") if isinstance(cfg, dict) else cfg or root.name)
    subs = {"{{project_name}}": name, "{{project_slug}}": slugify(name), "{{ohmai_version}}": __version__,
            "{{date}}": date.today().isoformat()}
    for f, target in missing:
        t = f.read_text(encoding="utf-8")
        for k, v in subs.items():
            t = t.replace(k, v)
        target.write_text(t, encoding="utf-8")
    return log

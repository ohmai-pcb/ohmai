"""Thin wrapper around kicad-cli (headless KiCad 8/9)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def kicad_cli() -> str | None:
    return os.environ.get("KICAD_CLI") or shutil.which("kicad-cli")


def version() -> str | None:
    exe = kicad_cli()
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else None


def run(args: list[str], log: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    exe = kicad_cli()
    if not exe:
        raise FileNotFoundError("kicad-cli not found (install KiCad 8 or 9, or set KICAD_CLI)")
    r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout)
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(f"$ kicad-cli {' '.join(args)}\n{r.stdout}\n{r.stderr}", encoding="utf-8")
    return r


def load_failed(r: subprocess.CompletedProcess) -> bool:
    text = (r.stdout + r.stderr).lower()
    return r.returncode != 0 or "failed to load" in text

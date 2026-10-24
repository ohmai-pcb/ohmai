"""Phase 12 bring-up firmware / self-test code check (`bringup-code`).

Verifies that `test/firmware/` contains ready-to-run bring-up test code (no TODOs or template markers)
so no code has to be developed from scratch during bench testing, and verifies pin parity against
`hardware/gen/pinmap.yaml` (when present) so every non-power signal pin/GPIO is exercised or documented.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from ohmai.checks import FAIL, PASS, Result, register
from ohmai.checks.docs import MARKER
from ohmai.project import Project

CODE_EXTS = {".py", ".c", ".cpp", ".cc", ".h", ".hpp", ".ino", ".rs", ".sh"}
POWER_NETS = {"GND", "AGND", "PGND", "VREG_PGND", "3V3", "+3V3", "+3.3V", "5V", "+5V", "VBUS", "VSYS", "VDD", "VCC",
              "DVDD", "AVDD", "ADC_AVDD", "VREG_AVDD", "VREG_VIN", "VINA", "NC", "unconnected"}


def _non_power_signals(pinmap_path: Path) -> list[str]:
    try:
        data = yaml.safe_load(pinmap_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    signals: list[str] = []

    def _add(name: str) -> None:
        s = str(name or "").strip().lstrip("/")
        if not s or s in POWER_NETS or s.upper() in POWER_NETS:
            return
        if s not in signals:
            signals.append(s)

    if isinstance(data, dict):
        for k in ("pins", "signals", "gpios", "nets"):
            val = data.get(k)
            if isinstance(val, dict):
                for pk, pv in val.items():
                    if isinstance(pv, dict):
                        if str(pv.get("type", "")).lower() in ("power", "gnd", "ground", "nc"):
                            continue
                        _add(pv.get("signal") or pv.get("net") or pv.get("name") or pk)
                    elif isinstance(pv, str):
                        _add(pv)
                    else:
                        _add(pk)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        if str(item.get("type", "")).lower() in ("power", "gnd", "ground", "nc"):
                            continue
                        _add(item.get("signal") or item.get("net") or item.get("gpio") or item.get("name") or item.get("pin"))
                    elif isinstance(item, str):
                        _add(item)
    return signals


@register(
    "bringup-code",
    "test/firmware/ has ready-to-run bring-up self-test code (no TODO/template markers) and covers hardware/gen/pinmap.yaml signals",
)
def check_bringup_code(project: Project, opts: dict) -> Result:
    fw_dir = project.root / "test" / "firmware"
    if not fw_dir.is_dir():
        return Result("bringup-code", FAIL, "test/firmware/ directory missing (generate bring-up test code before bench testing)")

    code_files = sorted(p for p in fw_dir.rglob("*") if p.is_file() and p.suffix.lower() in CODE_EXTS)
    doc_files = sorted(p for p in fw_dir.rglob("*.md") if p.is_file())
    if not code_files:
        return Result(
            "bringup-code",
            FAIL,
            "no bring-up test script/firmware file found in test/firmware/ (.py, .c, .cpp, .ino, .rs, .sh)",
        )

    problems: list[str] = []
    combined_chunks: list[str] = []

    for f in [*code_files, *doc_files]:
        rel = f.relative_to(project.root).as_posix()
        txt = f.read_text(encoding="utf-8", errors="replace")
        combined_chunks.append(txt)
        if MARKER in txt:
            problems.append(f"{rel}: still has the template marker ({MARKER})")
        for lineno, ln in enumerate(txt.splitlines(), start=1):
            if re.search(r"\bTODO\b", ln):
                problems.append(f"{rel}:{lineno}: unresolved TODO: {ln.strip()}")

    combined = "\n".join(combined_chunks)
    pinmap = project.root / "hardware" / "gen" / "pinmap.yaml"
    checked_signals = 0
    if pinmap.is_file():
        sigs = _non_power_signals(pinmap)
        checked_signals = len(sigs)
        missing_sigs = [s for s in sigs if not re.search(rf"\b{re.escape(s)}\b", combined)]
        if missing_sigs:
            problems.append(
                f"hardware/gen/pinmap.yaml signal(s) not referenced or documented in test/firmware/: {', '.join(missing_sigs)}"
            )

    if problems:
        return Result("bringup-code", FAIL, f"{len(problems)} problem(s) in test/firmware/", problems)

    names = ", ".join(f.relative_to(project.root).as_posix() for f in code_files)
    sig_note = f", {checked_signals} pinmap signal(s) verified" if checked_signals else ""
    return Result("bringup-code", PASS, f"{len(code_files)} file(s) ({names}){sig_note}")

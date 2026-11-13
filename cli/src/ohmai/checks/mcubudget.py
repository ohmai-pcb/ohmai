# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""MCU GPIO / net pin budget (parts/mcu_pin_budget.yaml), checked from architecture onward.

A pin-count shortfall must fail at Phase 2/4, not when the MCU sheet is drawn in Phase 7.

Format:
    mcu: MCU-TSSOP20          # or `none` when the board has no MCU
    package: TSSOP-20
    total_pins: 20
    reserved:                   # pins NOT available as GPIO, by group (counts or lists of pin names)
      power: [VDD, VDDA]
      ground: [VSS]
      reset: [NRST]
      boot: [BOOT0]
      swd: [PA13, PA14]
      fixed_serial: [PA9, PA10]
    required_nets:              # every control, ADC, UI and debug net the design needs on a GPIO
      - {net: LED_R, kind: ui}
      - {net: VBAT_SENSE, kind: adc}
    min_spare: 1                # optional, default 0
    free_gpio: 11               # optional override; otherwise total_pins - reserved
"""

from __future__ import annotations

import re

import yaml

from ohmai.checks import FAIL, PASS, SKIP, WARN, Result, register
from ohmai.project import Project

NAME = "mcu-pin-budget"
MCU_RX = re.compile(r"\b(MCU|microcontroller|STM32\w*|ESP32\w*|RP2040|RP2350|ATmega\w*|ATtiny\w*|nRF5\w*|"
                    r"PIC1[0-8]\w*|MSP430\w*|CH32V\w*|GD32\w*|SAMD\w*)\b", re.I)
KINDS = {"control", "adc", "ui", "debug", "comm", "pwm", "io", "other"}


def _count(v) -> int:
    if isinstance(v, bool) or v is None:
        return 0
    if isinstance(v, int):
        return v
    if isinstance(v, (list, tuple)):
        return len(v)
    raise ValueError(f"reserved group must be a count or a list, got {v!r}")


def _mentions_mcu(project: Project) -> list[str]:
    hits = []
    for rel in ("docs/architecture.md", "parts/selection.md"):
        f = project.root / rel
        if f.exists():
            m = MCU_RX.search(f.read_text(encoding="utf-8", errors="replace"))
            if m:
                hits.append(f"{rel}: '{m.group(0)}'")
    bom = project.bom
    if bom.exists():
        m = MCU_RX.search(bom.read_text(encoding="utf-8", errors="replace"))
        if m:
            hits.append(f"{bom.name}: '{m.group(0)}'")
    return hits


def evaluate(data: dict) -> Result:
    if not isinstance(data, dict):
        return Result(NAME, FAIL, "mcu_pin_budget.yaml is not a mapping")
    mcu = str(data.get("mcu") or "").strip()
    if not mcu:
        return Result(NAME, FAIL, "mcu_pin_budget.yaml: 'mcu' missing (use `mcu: none` if there is no MCU)")
    if mcu.lower() == "none":
        return Result(NAME, PASS, "no MCU on this board (mcu: none)")
    errors, details = [], []
    reserved = data.get("reserved") or {}
    if not isinstance(reserved, dict):
        return Result(NAME, FAIL, "'reserved' must map group -> count or pin list")
    for g in ("power", "ground", "swd"):
        if g not in reserved:
            errors.append(f"reserved.{g} missing (list the pins, or 0 if truly none)")
    try:
        n_res = sum(_count(v) for v in reserved.values())
    except ValueError as e:
        return Result(NAME, FAIL, str(e))
    nets = data.get("required_nets")
    if not isinstance(nets, list) or not nets:
        errors.append("required_nets missing or empty: list every control, ADC, UI and debug net")
        nets = []
    names = []
    for i, n in enumerate(nets):
        if isinstance(n, str):
            names.append(n)
            continue
        if not isinstance(n, dict) or not n.get("net"):
            errors.append(f"required_nets[{i}]: needs a 'net' name")
            continue
        names.append(str(n["net"]))
        k = str(n.get("kind", "")).lower()
        if k and k not in KINDS:
            errors.append(f"required_nets[{i}] {n['net']}: unknown kind '{k}' (one of {sorted(KINDS)})")
    dup = sorted({x for x in names if names.count(x) > 1})
    if dup:
        errors.append(f"duplicate required nets: {', '.join(dup)}")
    if "free_gpio" in data and data["free_gpio"] is not None:
        free = int(data["free_gpio"])
        details.append(f"free_gpio (declared) = {free}")
    else:
        total = data.get("total_pins")
        if not isinstance(total, int) or total <= 0:
            errors.append("total_pins missing (or give free_gpio)")
            free = 0
        else:
            free = total - n_res
            details.append(f"free_gpio = total_pins {total} - reserved {n_res} = {free}")
    spare = int(data.get("min_spare", 0) or 0)
    need = len(set(names))
    details.append(f"required nets = {need}, min_spare = {spare}")
    if errors:
        return Result(NAME, FAIL, f"{mcu}: budget incomplete ({len(errors)} issue(s))", errors + details)
    if need + spare > free:
        return Result(NAME, FAIL, f"{mcu}: PIN SHORTFALL: need {need}+{spare} spare, only {free} free GPIO "
                      f"(pick a bigger package/MCU, add an expander, or cut nets: owner decision)", details)
    return Result(NAME, PASS, f"{mcu}: {need} nets on {free} free GPIO ({free - need} spare)", details)


@register(NAME, "MCU GPIO/net budget: required_nets vs free GPIO after power/ground/SWD/fixed serials")
def mcu_pin_budget(project: Project, opts: dict) -> Result:
    f = project.root / "parts" / "mcu_pin_budget.yaml"
    if not f.exists():
        hits = _mentions_mcu(project)
        if hits:
            return Result(NAME, WARN, "an MCU is mentioned but parts/mcu_pin_budget.yaml is missing "
                          "(write it before the Phase 2/4 gate)", hits)
        return Result(NAME, SKIP, "no MCU mentioned and no parts/mcu_pin_budget.yaml")
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        return Result(NAME, FAIL, f"mcu_pin_budget.yaml does not parse: {e}")
    return evaluate(data)

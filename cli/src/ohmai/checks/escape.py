"""escape-room: placement rules that keep a fine-pitch QFN routable (run before routing)."""

from __future__ import annotations

from pathlib import Path

from ohmai.checks import FAIL, PASS, SKIP, Result, register
from ohmai.project import Project


@register("escape-room", "Placement escape room around QFN parts (layout.yaml escape_room): toe gap, flash facing, "
                         "edge lanes, ring vias, inductor facing, ring fill, per-side capacity")
def escape_room(project: Project, opts: dict) -> Result:
    from ohmai.checks import placement as pchk
    from ohmai.layout import config as lcfg
    from ohmai.layout import escape, placement

    try:
        cfg = lcfg.load(project, opts.get("layout_config") and project.root / opts["layout_config"])
    except lcfg.ConfigError as e:
        return Result("escape-room", FAIL, f"layout config: {e}")
    loops, keepouts, checklist = pchk.collect_rules(project, cfg)
    has_extra = bool(loops or keepouts or checklist or cfg.get("pour"))
    if not cfg["escape_room"] and not has_extra:
        return Result("escape-room", SKIP, f"no escape_room section in {lcfg.config_path(project).name}")

    pcb = Path(cfg["board"])
    if not pcb.is_file():
        return Result("escape-room", FAIL, f"{pcb.name}: board not found")
    findings: list[tuple[str, str, str]] = []
    if cfg["escape_room"]:
        try:
            er = {**escape.DEFAULTS, **cfg["escape_room"]}
        except TypeError:
            return Result("escape-room", FAIL, "escape_room must be a mapping")
        unknown = sorted(set(cfg["escape_room"]) - set(escape.DEFAULTS))
        if unknown:
            return Result("escape-room", FAIL, f"escape_room: unknown key(s) {', '.join(unknown)}")
        board = placement.load(pcb)
        findings.extend(escape.Rules(board, er, cfg["rules_resolved"]).run())
    if has_extra:
        pres = pchk.placement_check(project, opts)
        for d in pres.details:
            st, _, rest = d.partition(" ")
            rule = rest.split("]")[0].lstrip("[") if rest.startswith("[") else "placement"
            txt = rest.split("] ", 1)[1] if "] " in rest else rest
            findings.append((rule, st, txt))
    bad = [f for f in findings if f[1] == "FAIL"]
    details = [f"{st} [{rule}] {text}" for rule, st, text in findings]
    rules = sorted({f[0] for f in bad})
    return Result("escape-room", FAIL if bad else PASS,
                  f"{len(bad)} finding(s) in rule(s) {', '.join(rules)}" if bad else
                  f"{len(findings)} rule result(s), none failing", details)

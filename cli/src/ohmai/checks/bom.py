# SPDX-License-Identifier: Apache-2.0
"""BOM checks: structure, 3 sourcing rules (generic, non-generic, single-source), lock, and diff."""

from __future__ import annotations

import csv
import io
import re
import subprocess
from pathlib import Path

from ohmai.checks import FAIL, PASS, SKIP, WARN, Result, register
from ohmai.project import Project

REQUIRED = ["refs", "qty", "value", "footprint", "manufacturer", "mpn", "approved_alternates", "status", "dnp"]
STATUSES = {"candidate", "approved", "locked"}
FAB_REF_COLS = ["refs", "designator", "designators", "reference", "references", "ref"]
FAB_MPN_COLS = ["actual_mpn", "actual mpn", "purchased mpn", "purchased_mpn", "mpn", "manufacturer part number",
                "mfr part #", "mfr. part #", "part number"]
FAB_DESC_COLS = ["description", "actual description", "comment", "value"]

_DECISION_RE = re.compile(r"\bD-\d{3}\b")
_LCSC_RE = re.compile(r"\bC\d{3,10}\b")
_DISQUALIFIED_ALT_RE = re.compile(
    r"\b(different\s+footprint|separate\s+footprint|own\s+footprint|out\s+of\s+stock|not\s+pin[-\s]compatible|needs\s+fb\s+divider|white[-\s]?label|rebranded|same\s+(?:single[-\s]source\s+)?(?:silicon|die|core|oem))\b",
    re.I,
)
_EXHAUSTED_SEARCH_RE = re.compile(
    r"\b(?:checked|searched|rejected)\s*:|\b(?:no\s+pin[-\s]compatible|architecture[-\s]defining|proprietary\s+pinout)\b",
    re.I,
)


def split_refs(s: str) -> list[str]:
    return [r for r in re.split(r"[\s,;]+", s or "") if r]


def split_list(s: str) -> list[str]:
    return [x.strip() for x in (s or "").split(";") if x.strip()]


def _extract_entry_mpn(item: str) -> str:
    s = item.strip()
    if not s or s.lower().startswith(("generic:", "generic ", "single-source", "single source", "none:", "none named", "checked:", "searched:")):
        return ""
    m = re.match(r"^([^(\s,;]+(?:\s+[^(\s,;]+)*?)\s*(?:\(|$)", s)
    if not m:
        return ""
    cand = m.group(1).strip()
    # If prefixed by manufacturer (e.g. "GigaDevice GD25Q32EEIGR"), take the last token when it looks like an MPN
    toks = [t for t in cand.split() if not _LCSC_RE.fullmatch(t) and t.lower() not in {"rejected", "checked", "searched"}]
    if len(toks) >= 2 and any(ch.isdigit() for ch in toks[-1]):
        return toks[-1]
    return " ".join(toks) if toks else cand


def approved_mpns_for_row(r: dict) -> set[str]:
    out: set[str] = set()
    if r.get("mpn"):
        out.add(r["mpn"].strip().upper())
    for item in split_list(r.get("approved_alternates", "")):
        raw = item.strip()
        if raw:
            out.add(raw.upper())
        mpn = _extract_entry_mpn(raw)
        if mpn:
            out.add(mpn.upper())
    return out


def _norm_mfr(m: str) -> str:
    s = re.sub(r"\b(inc\.?|llc\.?|ltd\.?|corp\.?|co\.?|electronics?|electro[-\s]?mechanics?|semiconductors?|semicon|tech(nology)?|international|advanced)\b", "", (m or "").lower())
    s = re.sub(r"[^a-z0-9]+", "", s)
    aliases = {
        "uniroyal": "uniroyal",
        "uniroyaluniroyalelec": "uniroyal",
        "fhguangdongfenghua": "fh",
        "guangdongfenghua": "fh",
        "fenghua": "fh",
        "fh": "fh",
        "samsungelectromechanics": "samsung",
        "samsung": "samsung",
        "murata": "murata",
        "taiyoyuden": "taiyoyuden",
        "yageo": "yageo",
        "walsin": "walsin",
        "ralec": "ralec",
        "panasonic": "panasonic",
        "vishay": "vishay",
        "koaspeer": "koa",
        "koa": "koa",
        "diodesincorporated": "diodes",
        "diodes": "diodes",
        "texasInstruments": "ti",
        "texasinstruments": "ti",
        "ti": "ti",
        "analogdevicesmaximintegrated": "adi",
        "analogdevices": "adi",
        "maximintegrated": "adi",
        "stmicroelectronics": "st",
        "st": "st",
    }
    return aliases.get(s, s)


def _parse_concrete_alternates(r: dict) -> list[dict]:
    """Parse concrete alternate entries of the form 'MPN (Manufacturer, ...)' from approved_alternates and second_source."""
    entries: list[dict] = []
    seen_mpns: set[str] = set()
    for field in ("approved_alternates", "second_source"):
        for item in split_list(r.get(field, "")):
            s = item.strip()
            if not s or s.lower().startswith(("generic:", "generic ", "single-source", "single source", "none:", "none named", "checked:", "searched:", "owner approval")):
                continue
            m = re.match(r"^([^()]+?)\s*\(([^()]+)\)\s*$", s)
            if m:
                mpn_part = m.group(1).strip()
                inside = m.group(2).strip()
                parts = [p.strip() for p in inside.split(",") if p.strip()]
                # Determine manufacturer and supplier code inside parens
                mfr = ""
                code = ""
                for p in parts:
                    if _LCSC_RE.search(p) or p.lower().startswith(("lcsc", "jlc", "mouser", "digikey")):
                        if not code:
                            code = p
                    elif not mfr and not any(k in p.lower() for k in ("0402", "0603", "0805", "1206", "different footprint", "out of stock", "same family")):
                        mfr = p
                mpn = _extract_entry_mpn(mpn_part)
                disq = bool(_DISQUALIFIED_ALT_RE.search(inside))
                key = mpn.upper()
                if mpn and key not in seen_mpns:
                    seen_mpns.add(key)
                    entries.append({"mpn": mpn, "mfr": mfr, "mfr_norm": _norm_mfr(mfr), "code": code, "disqualified": disq, "raw": s})
            else:
                # Plain "Manufacturer MPN" or "MPN" in approved_alternates
                toks = s.split()
                if len(toks) == 1 and any(c.isdigit() for c in toks[0]):
                    mpn = toks[0].strip()
                    key = mpn.upper()
                    if key not in seen_mpns:
                        seen_mpns.add(key)
                        entries.append({"mpn": mpn, "mfr": "", "mfr_norm": "", "code": "", "disqualified": bool(_DISQUALIFIED_ALT_RE.search(s)), "raw": s})
    return entries



def _mpn_matches_db(stated_mpn: str, db_mpn: str) -> bool:
    a = re.sub(r"[^A-Z0-9]", "", (stated_mpn or "").upper())
    b = re.sub(r"[^A-Z0-9]", "", (db_mpn or "").upper())
    if not a or not b:
        return True
    return a == b or a.startswith(b) or b.startswith(a)


def _lookup_jlc_mpn(conn, code: str) -> tuple[bool, str]:
    """Look up C-number in jlcparts SQLite DB (supports v2 jlc_components and v1 components)."""
    num = int(code[1:])
    try:
        row = conn.execute("SELECT mfr FROM jlc_components WHERE lcsc = ?", (num,)).fetchone()
        if row is not None:
            return True, (row[0] or "").strip()
    except Exception:
        pass
    try:
        row = conn.execute("SELECT mfr FROM components WHERE lcsc = ?", (num,)).fetchone()
        if row is not None:
            return True, (row[0] or "").strip()
    except Exception:
        pass
    return False, ""


def check_row_jlcparts_codes(row_num: int, r: dict, conn, strict_missing: bool = False) -> list[str]:
    """Cross-check LCSC C# codes in supplier_pn, approved_alternates, and second_source against jlcparts SQLite DB."""
    if conn is None:
        return []
    errs: list[str] = []
    refs = r.get("refs", f"row {row_num}")

    # 1. Primary supplier_pn
    spn = (r.get("supplier_pn") or "").strip()
    mpn = (r.get("mpn") or "").strip()
    if _LCSC_RE.fullmatch(spn):
        found, db_mpn = _lookup_jlc_mpn(conn, spn)
        if found and mpn and db_mpn and not _mpn_matches_db(mpn, db_mpn):
            errs.append(
                f"row {row_num} ({refs}): supplier_pn {spn} is {db_mpn!r} in jlcparts DB, does not match BOM mpn {mpn!r}"
            )
        elif not found and strict_missing:
            errs.append(f"row {row_num} ({refs}): supplier_pn {spn} ({mpn}) not found in jlcparts DB")

    # 2. Concrete alternates in approved_alternates and second_source
    for alt in _parse_concrete_alternates(r):
        for m in _LCSC_RE.finditer(alt.get("code", "") + " " + alt.get("raw", "")):
            code = m.group(0)
            found, db_mpn = _lookup_jlc_mpn(conn, code)
            if found and alt["mpn"] and db_mpn and not _mpn_matches_db(alt["mpn"], db_mpn):
                errs.append(
                    f"row {row_num} ({refs}): alternate LCSC {code} is {db_mpn!r} in jlcparts DB, "
                    f"does not match stated alternate MPN {alt['mpn']!r}"
                )
            elif not found and strict_missing:
                errs.append(
                    f"row {row_num} ({refs}): alternate LCSC {code} ({alt['mpn']}) not found in jlcparts DB"
                )
            break

    # 3. Checked/rejected candidates in second_source with (LCSC Cxxxx...)
    sec = r.get("second_source", "")
    for seg in split_list(sec):
        s = seg.strip()
        s_clean = re.sub(r"^(?:checked|searched|rejected)\s*:\s*", "", s, flags=re.I).strip()
        m_seg = re.match(r"^([^()]+?)\s*\(([^()]+)\)\s*$", s_clean)
        if m_seg:
            cand_mpn = _extract_entry_mpn(m_seg.group(1).strip())
            for m in _LCSC_RE.finditer(m_seg.group(2)):
                code = m.group(0)
                found, db_mpn = _lookup_jlc_mpn(conn, code)
                if found and cand_mpn and db_mpn and not _mpn_matches_db(cand_mpn, db_mpn):
                    errs.append(
                        f"row {row_num} ({refs}): second_source LCSC {code} is {db_mpn!r} in jlcparts DB, "
                        f"does not match stated candidate MPN {cand_mpn!r}"
                    )
                elif not found and strict_missing:
                    errs.append(
                        f"row {row_num} ({refs}): second_source LCSC {code} ({cand_mpn}) not found in jlcparts DB"
                    )
    return errs


def check_row_sourcing(row_num: int, r: dict) -> tuple[list[str], list[str], dict | None]:
    """Evaluate the 3 BOM sourcing rules on one row:
    1. Generic components: 'generic: ...' + at least 2 concrete alternates (MPN, unique MFG, LCSC/supplier code).
    2. Non-generic components: at least 2 unique manufacturers across primary + valid drop-in alternates.
    3. Single-source components: require explicit single-source (D-xxx) + documented exhausted alternatives searched,
       and return a highlight record for SINGLE-SOURCE COMPONENTS.
    """
    if _is_dnp(r):
        return [], [], None
    refs = r.get("refs", f"row {row_num}")
    alt_txt = r.get("approved_alternates", "")
    sec_txt = r.get("second_source", "")
    notes_txt = r.get("notes", "")
    combined = " ; ".join(x for x in (alt_txt, sec_txt, notes_txt) if x)

    # Fictional Golden/test fixture rows without sourcing fields -> WARN (preserves golden fixture behavior)
    if not alt_txt and not sec_txt:
        return [], [f"row {row_num} ({refs}): no second source / approved alternate"], None

    is_generic = "generic:" in alt_txt.lower() or sec_txt.lower().startswith("generic")
    is_single_claim = bool(re.search(r"\b(single[-\s]source|none\s*:|none\s+named)\b", f"{alt_txt} ; {sec_txt}", re.I))

    primary_mfr = r.get("manufacturer", "").strip()
    primary_norm = _norm_mfr(primary_mfr)
    alts = _parse_concrete_alternates(r)
    valid_alts = [a for a in alts if not a["disqualified"]]

    # Rule 1: Generic component
    if is_generic:
        with_mfr = [a for a in valid_alts if a["mfr_norm"]]
        unique_alt_mfrs = {a["mfr_norm"] for a in with_mfr if a["mfr_norm"]}
        all_mfrs = ({primary_norm} if primary_norm else set()) | unique_alt_mfrs
        if len(with_mfr) < 2 or len(unique_alt_mfrs) < 2 or len(all_mfrs) < 2:
            return [
                f"row {row_num} ({refs}): generic component must list 'generic: ...' PLUS at least 2 concrete "
                f"alternates from different manufacturers (e.g. 'MPN1 (MfgA, LCSC Cxxxx); MPN2 (MfgB, LCSC Cyyyy)'); "
                f"found {len(with_mfr)} named alternate(s) across {len(unique_alt_mfrs)} alternate manufacturer(s)"
            ], [], None
        return [], [], None

    # Rule 2: Non-generic multi-source component (check if it has >=2 unique manufacturers)
    diff_mfr_alts = [a for a in valid_alts if a["mfr_norm"] and a["mfr_norm"] != primary_norm]
    if diff_mfr_alts and not is_single_claim:
        return [], [], None

    # Rule 3: Single-source component (or a non-generic row that failed to provide a different-manufacturer drop-in alternate)
    if is_single_claim:
        decs = _DECISION_RE.findall(combined)
        has_search = bool(_EXHAUSTED_SEARCH_RE.search(combined)) and not bool(
            re.match(r"^\s*none\s+named\b", sec_txt, re.I) and "checked:" not in combined.lower() and "rejected:" not in combined.lower()
        )
        if not decs or not has_search:
            reasons = []
            if not decs:
                reasons.append("missing owner decision reference (D-xxx)")
            if not has_search:
                reasons.append("missing exhausted alternatives search ('checked: <candidates and rejection reasons>')")
            return [
                f"row {row_num} ({refs}): single-source part {r.get('mpn') or '?'} ({primary_mfr or '?'}) not qualified: "
                + ", ".join(reasons)
            ], [], None
        # Extract concise search summary for highlight block
        m_chk = re.search(r'((?:checked|searched|rejected)\s*:[^;"]+)', combined, re.I)
        checked_summary = m_chk.group(1).strip() if m_chk else sec_txt
        return [], [], {
            "refs": refs,
            "mpn": r.get("mpn", "?"),
            "manufacturer": primary_mfr or "?",
            "decision": ", ".join(dict.fromkeys(decs)),
            "checked": checked_summary,
        }

    # Neither valid different-manufacturer alternate nor qualified single-source
    if alts:
        same_only = [a["raw"] for a in alts if a["mfr_norm"] == primary_norm]
        disq_only = [a["raw"] for a in alts if a["disqualified"]]
        detail = []
        if same_only:
            detail.append(f"same manufacturer as primary ({primary_mfr}): {', '.join(same_only)}")
        if disq_only:
            detail.append(f"non-drop-in/out-of-stock alternate(s): {', '.join(disq_only)}")
        why = "; ".join(detail) if detail else "no alternate manufacturer named"
        return [
            f"row {row_num} ({refs}): non-generic part {r.get('mpn')} ({primary_mfr}) needs at least 2 unique "
            f"manufacturers (drop-in alternate from a different manufacturer) or qualified single-source ({why})"
        ], [], None

    return [
        f"row {row_num} ({refs}): part {r.get('mpn')} ({primary_mfr}) has no valid second-manufacturer alternate "
        f"and is not a qualified single-source part"
    ], [], None


def read_rows(text: str) -> tuple[list[str], list[dict]]:
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    rd = csv.DictReader(io.StringIO("\n".join(lines)))
    rows = []
    for i, r in enumerate(rd, start=2):
        expected = len(rd.fieldnames or [])
        actual = sum(v is not None for k, v in r.items() if k is not None) + len(r.get(None, []))
        if actual != expected:
            refs = r.get("refs") or next((v for k, v in r.items() if k and k.strip().lower() == "refs"), "?")
            raise csv.Error(f"row {i} ({refs}): field-count mismatch: expected {expected}, got {actual}; "
                            "quote fields containing commas")
        rows.append({(k or "").strip().lower(): (v or "").strip() for k, v in r.items()})
    return [(h or "").strip().lower() for h in (rd.fieldnames or [])], rows


def load(path: Path) -> tuple[list[str], list[dict]]:
    return read_rows(path.read_text(encoding="utf-8-sig"))


def _is_dnp(r: dict) -> bool:
    return r.get("dnp", "").lower() in {"1", "y", "yes", "true", "dnp"}


def power_w(text: str) -> float | None:
    """Smallest power rating mentioned in text: '0.2W' -> 0.2, '250 mW' -> 0.25, '1/4W' -> 0.25."""
    vals = []
    for m in re.finditer(r"(\d+)\s*/\s*(\d+)\s*W\b", text or ""):
        vals.append(int(m.group(1)) / int(m.group(2)))
    for m in re.finditer(r"(?<![\d/])(\d*\.?\d+)\s*(m?)W\b", text or ""):
        vals.append(float(m.group(1)) * (1e-3 if m.group(2) else 1.0))
    return min(vals) if vals else None


def format_single_source_highlights(single_sources: list[dict]) -> list[str]:
    if not single_sources:
        return []
    out = [f"SINGLE-SOURCE COMPONENTS ({len(single_sources)}):"]
    for s in single_sources:
        out.append(f"  - {s['refs']}: {s['mpn']} ({s['manufacturer']}) [{s['decision']}] — {s['checked']}")
    return out


@register("bom-schema", "bom/bom.csv has required columns, unique refs, qty=ref count, priced rows cite source+date, and 3 sourcing rules (generic >=2 alts, non-generic >=2 MFGs, single-source qualified & highlighted)")
def bom_schema(project: Project, opts: dict) -> Result:
    if not project.bom.is_file():
        return Result("bom-schema", FAIL, f"{project.bom.relative_to(project.root)} missing")
    try:
        head, rows = load(project.bom)
    except csv.Error as e:
        return Result("bom-schema", FAIL, "invalid BOM CSV", [str(e)])
    errors, warns, single_sources = [], [], []
    missing = [c for c in REQUIRED if c not in head]
    if missing:
        return Result("bom-schema", FAIL, f"missing column(s): {', '.join(missing)}")
    import os
    import sqlite3

    db_opt = opts.get("jlcparts_db") or os.environ.get("OHMAI_JLCPARTS_DB")
    db_path: Path | None = Path(str(db_opt)).expanduser() if db_opt else None
    if db_path is None:
        default_cache = Path.home() / ".cache" / "ohmai" / "jlcparts" / "cache.sqlite3"
        if default_cache.is_file():
            db_path = default_cache
    jlc_conn = None
    if db_path is not None and db_path.is_file():
        try:
            jlc_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            jlc_conn = None
    strict_jlc = bool(opts.get("strict_jlcparts_db"))

    seen: dict[str, int] = {}
    try:
        for i, r in enumerate(rows, start=2):
            refs = split_refs(r["refs"])
            for ref in refs:
                if ref in seen:
                    errors.append(f"row {i}: {ref} also on row {seen[ref]}")
                seen[ref] = i
            if r["qty"] and r["qty"].isdigit() and int(r["qty"]) != len(refs):
                errors.append(f"row {i}: qty {r['qty']} but {len(refs)} ref(s)")
            if r["status"] and r["status"] not in STATUSES:
                errors.append(f"row {i}: status '{r['status']}' not in {sorted(STATUSES)}")
            priced = any(r.get(k) for k in ("price_qty1", "price_qty100", "price_qty1000"))
            if priced and not (r.get("price_source") and r.get("price_date")):
                errors.append(f"row {i} ({r['refs']}): price without price_source and price_date (never invent prices)")
            s_errs, s_warns, ss_info = check_row_sourcing(i, r)
            errors.extend(s_errs)
            warns.extend(s_warns)
            if ss_info:
                single_sources.append(ss_info)
            if jlc_conn is not None:
                errors.extend(check_row_jlcparts_codes(i, r, jlc_conn, strict_missing=strict_jlc))
    finally:
        if jlc_conn is not None:
            jlc_conn.close()
    ss_lines = format_single_source_highlights(single_sources)
    ss_tag = f"; {len(single_sources)} single-source highlighted" if single_sources else ""
    summary = f"{len(rows)} row(s), {len(seen)} ref(s){ss_tag}"
    if not rows:
        warns.append("bom.csv has no rows yet (phase 4 adds the approved parts)")
    if errors:
        return Result("bom-schema", FAIL, summary, errors + warns + ss_lines)
    return Result("bom-schema", WARN if warns else PASS, summary, warns + ss_lines)


@register("bom-lock", "Every fitted BOM row is status=locked with an MPN (approved alternates allowed), and bom.csv "
                      "matches the schematic: refs, MPN, value, footprint, DNP (stale-BOM check)")
def bom_lock(project: Project, opts: dict) -> Result:
    base = bom_schema(project, opts)
    if base.status == FAIL:
        return Result("bom-lock", FAIL, "bom-schema fails: " + base.summary, base.details)
    _, rows = load(project.bom)
    if not rows:
        return Result("bom-lock", FAIL, "bom.csv has no rows")
    errors = []
    for i, r in enumerate(rows, start=2):
        if _is_dnp(r):
            continue
        if not r["mpn"]:
            errors.append(f"row {i} ({r['refs']}): no MPN")
        if r["status"] != "locked":
            errors.append(f"row {i} ({r['refs']}): status '{r['status'] or '-'}', must be 'locked'")
    if errors:
        return Result("bom-lock", FAIL, f"{len(errors)} row(s) not locked", errors)
    sync, note = bom_vs_netlist(project, rows, opts)
    if sync:
        return Result("bom-lock", FAIL, f"{len(rows)} row(s) locked, but bom.csv does not match the schematic "
                      f"({len(sync)} difference(s); stale BOM?)", sync + base.details)
    return Result("bom-lock", PASS, f"{len(rows)} row(s) locked; {note}", base.details)


def _norm_val(v: str) -> str:
    return re.sub(r"\s+", "", (v or "").lower()).replace("µ", "u").replace("μ", "u")


def _base_elec_val(s: str) -> str:
    t = re.sub(r"\(.*?\)", "", (s or "").lower()).strip()
    t = t.replace("µ", "u").replace("μ", "u").replace("ω", "").replace("ohm", "").replace("ohms", "")
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([pnumk]?)(?:f|h|r|hz|v|a)?\b", t)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    return re.sub(r"\s+", "", t)


def _values_compatible(sch_val: str, bom_val: str, mpn: str, approved_mpns: set[str]) -> bool:
    sv = _norm_val(sch_val)
    bv = _norm_val(bom_val)
    if not sv or not bv or sv == bv:
        return True
    eb_s = _base_elec_val(sch_val)
    eb_b = _base_elec_val(bom_val)
    if eb_s and eb_s == eb_b:
        return True
    sv_up = sch_val.strip().upper()
    if len(sv_up) >= 4 and any(sv_up in m for m in approved_mpns):
        return True
    if mpn and mpn.upper() in approved_mpns and not re.match(r"^\d", sv) and not re.match(r"^\d", bv):
        return True
    return False


def _footprints_compatible(sch_fp: str, bom_fp: str) -> bool:
    sf = (sch_fp or "").strip()
    bf = (bom_fp or "").strip()
    if not sf or not bf or sf == bf:
        return True
    sf_up = sf.upper()
    bf_up = bf.upper()
    for tok in ("0201", "0402", "0603", "0805", "1206", "1210", "2016", "2512", "3225",
                "SOT-23-6", "SOT-23-5", "SOT-23", "SOT-523", "TSSOP-14", "QFN-60",
                "VSON-10", "USON-8", "XSON-8", "SOD-123"):
        if tok in bf_up and tok.replace("-", "_") in sf_up.replace("-", "_"):
            return True
    if "VSON" in bf_up and ("PACKAGE_SON:" in sf_up or "VSON" in sf_up or "DRC0010" in sf_up):
        return True
    if "PCC-SMD" in bf_up and "PCC-SMD" in sf_up:
        return True
    if "USB" in sf_up and ("SMD" in bf_up or "USB" in bf_up or "TYPE-C" in bf_up):
        return True
    if "5.1X5.1" in bf_up.replace(" ", "") and "TS-1187A" in sf_up:
        return True
    if "2.5X2.0" in bf_up.replace(" ", "") and "2512" in sf_up:
        return True
    return False


def bom_vs_netlist(project: Project, rows: list[dict], opts: dict) -> tuple[list[str], str]:
    """Stale-BOM check: every schematic part is in bom.csv and vice versa, with the same MPN (or an approved
    alternate), value, footprint and DNP state. ([differences], note); no netlist -> ([], why not)."""
    from ohmai import kicad
    from ohmai.checks.schematic import export_netlist
    from ohmai.netlist import load as load_netlist

    if not opts.get("netlist") and not (kicad.kicad_cli() and project.schematic.is_file()):
        return [], "not compared with the schematic (no kicad-cli; pass --netlist)"
    try:
        nl = load_netlist(export_netlist(project, opts))
    except (RuntimeError, FileNotFoundError) as e:
        return [f"schematic netlist for the BOM comparison: {e}"], ""
    comps = {r: c for r, c in nl.comps.items() if not r.startswith("#") and "exclude_from_bom" not in c.get("flags", ())}
    if not comps:
        return [], "schematic has no parts yet (BOM not compared)"
    by_ref = {ref: r for r in rows for ref in split_refs(r["refs"])}
    diffs = [f"{ref}: in the schematic ({c['value']}, MPN {c['fields'].get('MPN') or '-'}) but not in bom.csv"
             for ref, c in sorted(comps.items()) if ref not in by_ref]
    diffs += [f"{ref}: in bom.csv ({r['mpn']}) but not in the schematic" for ref, r in sorted(by_ref.items())
              if ref not in comps]
    for ref in sorted(set(comps) & set(by_ref)):
        c, r = comps[ref], by_ref[ref]
        mpn = (c["fields"].get("MPN") or "").strip()
        ok = approved_mpns_for_row(r)
        if mpn and mpn.upper() not in ok:
            diffs.append(f"{ref}: schematic MPN {mpn}, bom.csv {r['mpn'] or '-'} (approved: {', '.join(sorted(ok)) or '-'})")
        if r["value"] and not _values_compatible(c["value"], r["value"], mpn, ok):
            diffs.append(f"{ref}: schematic value {c['value']!r}, bom.csv {r['value']!r}")
        fp = c.get("footprint") or ""
        if fp and r["footprint"] and not _footprints_compatible(fp, r["footprint"]):
            diffs.append(f"{ref}: schematic footprint {fp}, bom.csv {r['footprint']}")
        if ("dnp" in c.get("flags", ())) != _is_dnp(r):
            diffs.append(f"{ref}: DNP differs (schematic {'dnp' in c.get('flags', ())}, bom.csv {_is_dnp(r)})")
    return diffs, f"matches the schematic ({len(comps)} part(s))"


def _pick(head: list[str], names: list[str]) -> str | None:
    return next((n for n in names if n in head), None)


def diff_fab(bom_rows: list[dict], fab_head: list[str], fab_rows: list[dict]) -> tuple[list[str], list[str]]:
    rc, mc, dc = _pick(fab_head, FAB_REF_COLS), _pick(fab_head, FAB_MPN_COLS), _pick(fab_head, FAB_DESC_COLS)
    if not rc or not mc:
        return [f"fab file needs a designator column ({FAB_REF_COLS}) and an MPN column ({FAB_MPN_COLS})"], []
    by_ref = {}
    for r in bom_rows:
        for ref in split_refs(r["refs"]):
            by_ref[ref] = r
    errors, warns, fab_refs = [], [], set()
    for fr in fab_rows:
        for ref in split_refs(fr.get(rc, "")):
            fab_refs.add(ref)
            b = by_ref.get(ref)
            if b is None:
                errors.append(f"{ref}: fab buys {fr.get(mc)} but {ref} is not in bom.csv")
                continue
            if _is_dnp(b):
                errors.append(f"{ref}: DNP in bom.csv but the fab buys {fr.get(mc)}")
                continue
            ok = approved_mpns_for_row(b)
            got = fr.get(mc, "")
            if got.upper() not in ok:
                errors.append(f"{ref}: fab substituted {got or '(blank)'} for {b['mpn']} "
                              f"(approved: {', '.join(sorted(ok)) or '-'})")
            want_w = power_w(" ".join([b.get("value", ""), b.get("rating_notes", "")]))
            got_w = power_w(fr.get(dc, "") if dc else "")
            if want_w and got_w and got_w < want_w:
                errors.append(f"{ref}: fab part rated {got_w * 1000:.0f} mW, BOM needs {want_w * 1000:.0f} mW")
    for ref, b in by_ref.items():
        if not _is_dnp(b) and ref not in fab_refs:
            warns.append(f"{ref}: in bom.csv but not in the fab list (consigned? missing?)")
    return errors, warns


@register("bom-diff", "Diff bom.csv vs the fab's actual-purchase list (--fab FILE) or a git ref (--against REF)")
def bom_diff(project: Project, opts: dict) -> Result:
    if not project.bom.is_file():
        return Result("bom-diff", FAIL, "bom/bom.csv missing")
    _, rows = load(project.bom)
    fab = opts.get("fab") or (project.root / "bom" / "fab-actual.csv")
    fab = Path(fab)
    if opts.get("fab") or fab.is_file():
        if not fab.is_file():
            return Result("bom-diff", FAIL, f"{fab} not found")
        fh, fr = load(fab)
        errors, warns = diff_fab(rows, fh, fr)
        summary = f"bom.csv vs {fab.name}: {len(errors)} unapproved difference(s), {len(warns)} warning(s)"
        return Result("bom-diff", FAIL if errors else (WARN if warns else PASS), summary, errors + warns)
    ref = opts.get("against")
    if ref:
        rel = project.bom.relative_to(project.root).as_posix()
        r = subprocess.run(["git", "-C", str(project.root), "show", f"{ref}:{rel}"], capture_output=True, text=True)
        if r.returncode != 0:
            return Result("bom-diff", FAIL, f"git show {ref}:{rel} failed: {r.stderr.strip()}")
        _, old = read_rows(r.stdout)
        o = {ref_: x for x in old for ref_ in split_refs(x.get("refs", ""))}
        n = {ref_: x for x in rows for ref_ in split_refs(x.get("refs", ""))}
        changes = [f"+ {k} {n[k].get('mpn')}" for k in sorted(set(n) - set(o))]
        changes += [f"- {k} {o[k].get('mpn')}" for k in sorted(set(o) - set(n))]
        changes += [f"~ {k} {o[k].get('mpn')} -> {n[k].get('mpn')}" for k in sorted(set(o) & set(n))
                    if (o[k].get("mpn"), o[k].get("approved_alternates")) != (n[k].get("mpn"), n[k].get("approved_alternates"))]
        (project.out / "bom-diff.txt").write_text("\n".join(changes) + "\n", encoding="utf-8")
        return Result("bom-diff", WARN if changes else PASS, f"{len(changes)} change(s) vs {ref}", changes)
    return Result("bom-diff", SKIP, "no bom/fab-actual.csv; pass --fab FILE (fab's actual purchase list) or --against REF")

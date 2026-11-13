# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""`ohmai parts search`: optional parametric search in a local jlcparts database (phase 4 candidates).

jlcparts (github.com/yaqwsx/jlcparts, MIT) builds an offline SQLite snapshot of the JLCPCB/LCSC catalogue.
OhmAI only reads that file (OHMAI_JLCPARTS_DB or --db); it never downloads or updates it. Two schemas:

- `source-db-v2` (current jlcparts, jlcparts/sourceDb.py: `meta.format = source-db-v2`, table `jlc_components`
  with category / manufacturer inline, `library_type` base|expand, `preferred`, `present`, `fetched_at`, price
  as "1-99:0.0297,100-:0.0214"); parts with present = 0 (delisted) are left out.
- the older `cache.sqlite3` schema of jlcparts/partLib.py (tables components, categories, manufacturers;
  price ladder JSON).

A hit is a candidate, not data for the BOM: stock and price are as of the snapshot. Phase 4 needs live
price/stock with URL and date, so open the LCSC page printed for each hit and copy from there.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

LCSC_URL = "https://www.lcsc.com/product-detail/{lcsc}.html"
V2_FORMAT = "source-db-v2"
NEEDED_V2 = {"jlc_components": {"lcsc", "mfr", "manufacturer", "package", "description", "stock", "price",
                                "library_type", "category", "subcategory", "datasheet", "fetched_at", "present"}}
NEEDED = {"components": {"lcsc", "mfr", "package", "description", "stock", "price", "basic", "category_id",
                         "manufacturer_id", "datasheet", "last_update"},
          "categories": {"id", "category", "subcategory"}, "manufacturers": {"id", "name"}}


def db_path(arg: str | None = None) -> Path:
    p = arg or os.environ.get("OHMAI_JLCPARTS_DB")
    if not p:
        raise SystemExit("ohmai parts search: no jlcparts database. Set OHMAI_JLCPARTS_DB or pass --db "
                         "(build or download cache.sqlite3 as described in docs/TESTING.md / phase 4 guide)")
    path = Path(p).expanduser()
    if not path.is_file():
        raise SystemExit(f"ohmai parts search: {path} not found")
    return path


def db_format(conn: sqlite3.Connection) -> str:
    """'source-db-v2' (current jlcparts) or 'partlib' (older cache.sqlite3)."""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'format'").fetchone()
    except sqlite3.DatabaseError:
        row = None
    return str(row[0]) if row else "partlib"


def _schema_problems(conn: sqlite3.Connection, needed: dict | None = None) -> list[str]:
    probs = []
    for table, cols in (needed or NEEDED).items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608 (fixed names)
        if not have:
            probs.append(f"table {table} missing")
        elif cols - have:
            probs.append(f"{table}: columns {', '.join(sorted(cols - have))} missing")
    return probs


def unit_price(price_json: str, qty: int) -> float | None:
    """Price per unit at qty from the jlcparts price ladder [{qFrom, qTo, price}, ...]."""
    try:
        ladder = json.loads(price_json or "[]")
    except json.JSONDecodeError:
        return None
    best = None
    for step in ladder if isinstance(ladder, list) else []:
        lo, hi = step.get("qFrom") or 1, step.get("qTo")
        if qty >= lo and (hi is None or qty <= hi):
            best = step.get("price")
    return best


def unit_price_v2(price: str, qty: int) -> float | None:
    """Price per unit at qty from a source-db-v2 ladder "1-99:0.0297,100-299:0.0214,8000-:0.0128"."""
    best = None
    for step in (price or "").split(","):
        rng, _, val = step.strip().partition(":")
        lo, _, hi = rng.partition("-")
        try:
            lo_i, hi_i, v = int(lo or 1), (int(hi) if hi.strip() else None), float(val)
        except ValueError:
            continue
        if qty >= lo_i and (hi_i is None or qty <= hi_i):
            best = v
    return best


def _search_v2(conn, terms, category, package, basic, min_stock, limit, qty) -> list[dict]:
    where, args = ["c.present = 1", "c.stock >= ?"], [min_stock]
    for t in terms:
        where.append("(c.mfr LIKE ? OR c.description LIKE ? OR c.package LIKE ? OR c.subcategory LIKE ? "
                     "OR c.category LIKE ? OR c.manufacturer LIKE ? OR ('C' || c.lcsc) = ?)")
        args += [f"%{t}%"] * 6 + [t.upper()]
    if category:
        where.append("(c.category LIKE ? OR c.subcategory LIKE ?)")
        args += [f"%{category}%"] * 2
    if package:
        where.append("c.package LIKE ?")
        args.append(f"%{package}%")
    if basic:
        where.append("c.library_type = 'base'")
    sql = ("SELECT c.lcsc, c.mfr, c.manufacturer, c.package, c.library_type, c.preferred, c.stock, c.price, "
           "c.description, c.datasheet, c.fetched_at, c.category, c.subcategory FROM jlc_components c "
           f"WHERE {' AND '.join(where)} ORDER BY (c.library_type = 'base') DESC, c.preferred DESC, c.stock DESC "
           "LIMIT ?")  # noqa: S608 (placeholders only)
    rows = []
    for r in conn.execute(sql, [*args, limit]):
        lcsc = f"C{r['lcsc']}"
        upd = datetime.fromtimestamp(r["fetched_at"], tz=timezone.utc).date().isoformat() if r["fetched_at"] else None
        rows.append({"lcsc": lcsc, "mpn": r["mfr"], "manufacturer": r["manufacturer"], "package": r["package"],
                     "basic": r["library_type"] == "base", "preferred": bool(r["preferred"]), "stock": r["stock"],
                     f"unit_price_qty{qty}": unit_price_v2(r["price"], qty),
                     "category": f"{r['category']} / {r['subcategory']}", "description": r["description"],
                     "datasheet": r["datasheet"], "snapshot_date": upd, "url": LCSC_URL.format(lcsc=lcsc)})
    return rows


def search(terms: list[str], *, db: str | None = None, category: str | None = None, package: str | None = None,
           basic: bool = False, min_stock: int = 0, limit: int = 20, qty: int = 1) -> tuple[list[dict], dict]:
    path = db_path(db)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    fmt = db_format(conn)
    probs = _schema_problems(conn, NEEDED_V2 if fmt == V2_FORMAT else NEEDED)
    if probs:
        raise SystemExit(f"ohmai parts search: {path} is not a supported jlcparts database (format {fmt}: "
                         f"{'; '.join(probs)})")
    if fmt == V2_FORMAT:
        rows = _search_v2(conn, terms, category, package, basic, min_stock, limit, qty)
        conn.close()
        return rows, _meta(path, fmt)
    where, args = ["c.stock >= ?"], [min_stock]
    for t in terms:
        where.append("(c.mfr LIKE ? OR c.description LIKE ? OR c.package LIKE ? OR cat.subcategory LIKE ? "
                     "OR cat.category LIKE ? OR m.name LIKE ? OR ('C' || c.lcsc) = ?)")
        like = f"%{t}%"
        args += [like] * 6 + [t.upper()]
    if category:
        where.append("(cat.category LIKE ? OR cat.subcategory LIKE ?)")
        args += [f"%{category}%"] * 2
    if package:
        where.append("c.package LIKE ?")
        args.append(f"%{package}%")
    if basic:
        where.append("c.basic = 1")
    sql = ("SELECT c.lcsc, c.mfr, m.name AS manufacturer, c.package, c.basic, c.stock, c.price, c.description, "
           "c.datasheet, c.last_update, cat.category, cat.subcategory FROM components c "
           "JOIN categories cat ON cat.id = c.category_id JOIN manufacturers m ON m.id = c.manufacturer_id "
           f"WHERE {' AND '.join(where)} ORDER BY c.basic DESC, c.stock DESC LIMIT ?")  # noqa: S608 (placeholders only)
    rows = []
    for r in conn.execute(sql, [*args, limit]):
        lcsc = f"C{r['lcsc']}"
        upd = datetime.fromtimestamp(r["last_update"], tz=timezone.utc).date().isoformat() if r["last_update"] else None
        rows.append({"lcsc": lcsc, "mpn": r["mfr"], "manufacturer": r["manufacturer"], "package": r["package"],
                     "basic": bool(r["basic"]), "stock": r["stock"], f"unit_price_qty{qty}": unit_price(r["price"], qty),
                     "category": f"{r['category']} / {r['subcategory']}", "description": r["description"],
                     "datasheet": r["datasheet"], "snapshot_date": upd, "url": LCSC_URL.format(lcsc=lcsc)})
    conn.close()
    return rows, _meta(path, "partlib")


def _meta(path: Path, fmt: str) -> dict:
    st = path.stat()
    return {"db": str(path), "format": fmt, "db_mtime": datetime.fromtimestamp(st.st_mtime).date().isoformat(),
            "note": "jlcparts offline snapshot: stock/price as of snapshot_date; copy live values from the URL"}

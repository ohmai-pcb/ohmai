# SPDX-License-Identifier: Apache-2.0
"""ohmai parts search against a tiny FICTIONAL jlcparts-schema database."""

import json
import sqlite3

import pytest

from ohmai import partsearch as ps
from ohmai.cli import main


@pytest.fixture
def db(tmp_path):
    """FICTIONAL rows in the jlcparts cache.sqlite3 schema (partLib.py); not real catalogue data."""
    p = tmp_path / "cache.sqlite3"
    c = sqlite3.connect(p)
    c.executescript("""
        CREATE TABLE components (lcsc INTEGER PRIMARY KEY, category_id INTEGER, mfr TEXT, package TEXT, joints INTEGER,
            manufacturer_id INTEGER, basic INTEGER, preferred INTEGER DEFAULT 0, description TEXT, datasheet TEXT,
            stock INTEGER, price TEXT, last_update INTEGER, extra TEXT, flag INTEGER DEFAULT 0);
        CREATE TABLE categories (id INTEGER PRIMARY KEY, category TEXT, subcategory TEXT);
        CREATE TABLE manufacturers (id INTEGER PRIMARY KEY, name TEXT);
        INSERT INTO categories VALUES (1, 'Power Management ICs', 'Linear Voltage Regulators (LDO)'),
                                      (2, 'Resistors', 'Chip Resistor - Surface Mount');
        INSERT INTO manufacturers VALUES (1, 'FICTIONAL Semi'), (2, 'FICTIONAL Passives');
    """)
    rows = [(900001, 1, "FICT-LDO33-SOT235", "SOT-23-5", 5, 1, 0, "FICTIONAL 3.3V 600mA LDO", "", 1200,
             json.dumps([{"qFrom": 1, "qTo": 9, "price": 0.30}, {"qFrom": 10, "qTo": None, "price": 0.20}]), 1760000000),
            (900002, 1, "FICT-LDO33-SOT89", "SOT-89", 3, 1, 1, "FICTIONAL 3.3V 1A LDO", "", 50,
             json.dumps([{"qFrom": 1, "qTo": None, "price": 0.25}]), 1760000000),
            (900003, 2, "FICT-R-1K-0603", "0603", 2, 2, 1, "FICTIONAL 1kΩ 1% 0603", "", 500000, "[]", 1760000000)]
    c.executemany("INSERT INTO components (lcsc, category_id, mfr, package, joints, manufacturer_id, basic, description,"
                  " datasheet, stock, price, last_update) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    c.close()
    return p


def test_search_filters_and_prices(db):
    rows, meta = ps.search(["3.3V", "LDO"], db=str(db), qty=10)
    assert [r["lcsc"] for r in rows] == ["C900002", "C900001"], "basic parts first"
    assert rows[1]["unit_price_qty10"] == 0.20 and rows[0]["url"].endswith("/C900002.html")
    assert "snapshot" in meta["note"]
    assert [r["lcsc"] for r in ps.search(["LDO"], db=str(db), package="SOT-23")[0]] == ["C900001"]
    assert [r["lcsc"] for r in ps.search([], db=str(db), basic=True, min_stock=100)[0]] == ["C900003"]
    assert [r["lcsc"] for r in ps.search(["c900003"], db=str(db))[0]] == ["C900003"]


def test_cli_and_errors(db, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OHMAI_JLCPARTS_DB", str(db))
    assert main(["parts", "search", "resistor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["results"][0]["mpn"] == "FICT-R-1K-0603"
    bad = tmp_path / "other.sqlite3"
    sqlite3.connect(bad).execute("CREATE TABLE x (a)").connection.commit()
    with pytest.raises(SystemExit, match="not a supported jlcparts"):
        ps.search(["x"], db=str(bad))
    monkeypatch.delenv("OHMAI_JLCPARTS_DB")
    with pytest.raises(SystemExit, match="OHMAI_JLCPARTS_DB"):
        ps.search(["x"])


@pytest.fixture
def db_v2(tmp_path):
    """FICTIONAL rows in the current jlcparts source-db-v2 schema (jlcparts/sourceDb.py); not catalogue data."""
    p = tmp_path / "cache.sqlite3"
    c = sqlite3.connect(p)
    c.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL);
        INSERT INTO meta VALUES ('format', 'source-db-v2');
        CREATE TABLE jlc_components (lcsc INTEGER PRIMARY KEY NOT NULL, fetched_at INTEGER NOT NULL,
            present INTEGER NOT NULL, sync_seen INTEGER NOT NULL DEFAULT 0, category TEXT NOT NULL,
            subcategory TEXT NOT NULL, mfr TEXT NOT NULL, package TEXT NOT NULL, joints INTEGER NOT NULL,
            manufacturer TEXT NOT NULL, library_type TEXT NOT NULL, preferred INTEGER NOT NULL,
            last_on_stock INTEGER NOT NULL, description TEXT NOT NULL, datasheet TEXT NOT NULL, stock INTEGER NOT NULL,
            price TEXT NOT NULL, attributes TEXT NOT NULL, rohs INTEGER, eccn TEXT NOT NULL, assembly INTEGER,
            assembly_process TEXT, assembly_mode TEXT, website_component_id TEXT, attrition TEXT NOT NULL);
    """)
    cols = ("lcsc, fetched_at, present, category, subcategory, mfr, package, joints, manufacturer, library_type, "
            "preferred, last_on_stock, description, datasheet, stock, price, attributes, eccn, attrition")
    rows = [(900011, 1760000000, 1, "Power Management ICs", "Linear Voltage Regulators (LDO)", "FICT-LDO33-SOT235",
             "SOT-23-5", 5, "FICTIONAL Semi", "expand", 0, 0, "FICTIONAL 3.3V 600mA LDO", "", 1200,
             "1-9:0.30,10-:0.20", "{}", "", "{}"),
            (900012, 1760000000, 1, "Power Management ICs", "Linear Voltage Regulators (LDO)", "FICT-LDO33-SOT89",
             "SOT-89", 3, "FICTIONAL Semi", "expand", 1, 0, "FICTIONAL 3.3V 1A LDO", "", 50, "1-:0.25", "{}", "", "{}"),
            (900013, 1760000000, 1, "Resistors", "Chip Resistor - Surface Mount", "FICT-R-1K-0603", "0603", 2,
             "FICTIONAL Passives", "base", 0, 0, "FICTIONAL 1kΩ 1% 0603", "", 500000, "1-999:0.002,1000-:0.001", "{}",
             "", "{}"),
            (900014, 1760000000, 0, "Power Management ICs", "Linear Voltage Regulators (LDO)", "FICT-LDO33-GONE",
             "SOT-23-5", 5, "FICTIONAL Semi", "base", 0, 0, "FICTIONAL 3.3V LDO, delisted", "", 9999, "1-:0.10", "{}",
             "", "{}")]
    c.executemany(f"INSERT INTO jlc_components ({cols}) VALUES ({','.join('?' * 19)})", rows)
    c.commit()
    c.close()
    return p


def test_v2_schema(db_v2):
    rows, meta = ps.search(["3.3V", "LDO"], db=str(db_v2), qty=10)
    assert meta["format"] == "source-db-v2"
    assert [r["lcsc"] for r in rows] == ["C900012", "C900011"], "delisted part left out; preferred before plain"
    assert rows[1]["unit_price_qty10"] == 0.20 and rows[0]["unit_price_qty10"] == 0.25
    assert rows[0]["preferred"] and not rows[0]["basic"] and rows[0]["snapshot_date"] == "2025-10-09"
    assert [r["lcsc"] for r in ps.search([], db=str(db_v2), basic=True)[0]] == ["C900013"]
    assert ps.search(["c900013"], db=str(db_v2), qty=1000)[0][0]["unit_price_qty1000"] == 0.001
    assert [r["lcsc"] for r in ps.search(["LDO"], db=str(db_v2), package="SOT-23")[0]] == ["C900011"]


def test_v2_price_ladder_and_bad_schema(tmp_path):
    assert ps.unit_price_v2("1-99:0.0297,100-299:0.0214,8000-:0.0128", 150) == 0.0214
    assert ps.unit_price_v2("1-99:0.0297,8000-:0.0128", 9000) == 0.0128
    assert ps.unit_price_v2("garbage", 5) is None
    bad = tmp_path / "v2bad.sqlite3"
    c = sqlite3.connect(bad)
    c.executescript("CREATE TABLE meta (key TEXT, value TEXT); INSERT INTO meta VALUES ('format','source-db-v2');"
                    "CREATE TABLE jlc_components (lcsc INTEGER);")
    c.commit()
    c.close()
    with pytest.raises(SystemExit, match="format source-db-v2: jlc_components: columns"):
        ps.search(["x"], db=str(bad))

"""
storage/database.py — SQLite persistence layer.

Tables
------
  ppra_tenders      – live tender notices from PPIP portal
  ppra_contracts    – awarded contracts (OCDS compiled releases)
  knbs_releases     – KNBS statistical publication index
  oag_reports       – Auditor-General report index (metadata + local path)
  cob_reports       – Controller of Budget report index
  treasury_docs     – National Treasury budget documents index
  downloaded_files  – Deduplicated registry of every downloaded file
  scrape_log        – Per-run audit log (start, end, rows, status)
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import Any, Optional

from config import DB_PATH
from utils.logger import get_logger

log = get_logger("database")


# ─── Schema DDL ──────────────────────────────────────────────────────────────
_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── PPRA / PPIP ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ppra_contracts (
    ocid                TEXT PRIMARY KEY,
    tender_id           TEXT,
    tender_title        TEXT,
    procuring_entity    TEXT,
    county              TEXT,
    procurement_method  TEXT,
    tender_status       TEXT,
    tender_value        REAL,
    tender_currency     TEXT,
    award_date          TEXT,
    award_value         REAL,
    award_currency      TEXT,
    supplier_name       TEXT,
    supplier_id         TEXT,
    contract_start      TEXT,
    contract_end        TEXT,
    contract_value      REAL,
    contract_currency   TEXT,
    fiscal_year         TEXT,
    raw_json            TEXT,
    scraped_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ppra_tenders (
    tender_id           TEXT PRIMARY KEY,
    title               TEXT,
    procuring_entity    TEXT,
    category            TEXT,
    procurement_method  TEXT,
    status              TEXT,
    closing_date        TEXT,
    published_date      TEXT,
    url                 TEXT,
    scraped_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── KNBS ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS knbs_releases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    category        TEXT,
    release_type    TEXT,         -- 'statistical_abstract' | 'economic_survey' | 'monthly' | 'other'
    year            INTEGER,
    chapter         TEXT,
    file_url        TEXT UNIQUE,
    local_path      TEXT,
    scraped_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── OAG ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS oag_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    fiscal_year     TEXT,
    report_type     TEXT,        -- 'county_executive'|'county_assembly'|'national'|'hospital'|etc.
    file_url        TEXT UNIQUE,
    local_path      TEXT,
    scraped_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── COB ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cob_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    fiscal_year     TEXT,
    quarter         TEXT,        -- 'Q1'|'Q2'|'Q3'|'Q4'|'Annual'|'Half-Year'
    government_level TEXT,       -- 'national'|'county'
    file_url        TEXT UNIQUE,
    local_path      TEXT,
    scraped_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── TREASURY ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS treasury_docs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    doc_type        TEXT,        -- 'BPS'|'BROP'|'Budget Summary'|'Supplementary'|'Other'
    fiscal_year     TEXT,
    file_url        TEXT UNIQUE,
    local_path      TEXT,
    scraped_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── File registry ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS downloaded_files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,   -- 'ppra'|'knbs'|'oag'|'cob'|'treasury'
    url             TEXT UNIQUE NOT NULL,
    local_path      TEXT NOT NULL,
    file_size_kb    REAL,
    downloaded_at   TEXT NOT NULL DEFAULT (datetime('now')),
    status          TEXT NOT NULL DEFAULT 'ok'   -- 'ok'|'failed'|'skipped'
);

-- ── Scrape audit log ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scrape_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    rows_added  INTEGER DEFAULT 0,
    files_saved INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'running',   -- 'running'|'ok'|'partial'|'failed'
    notes       TEXT
);

-- ── NLP Report Findings ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS report_findings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type     TEXT,        -- 'oag' or 'cob'
    entity          TEXT,        -- mapped county name
    keyword         TEXT,
    context_snippet TEXT,
    page_num        INTEGER,
    amount_lost_kes REAL,
    responsible_officers TEXT,
    severity        TEXT,
    scraped_at      TEXT NOT NULL DEFAULT (datetime('now'))
);


-- ── Red Flags (anomaly detection results) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS red_flags (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    flag_type       TEXT NOT NULL,
    severity        TEXT,
    ocid            TEXT,
    description     TEXT,
    entity          TEXT,
    supplier        TEXT,
    fiscal_year     TEXT,
    award_kes       REAL,
    detected_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_ppra_entity  ON ppra_contracts(procuring_entity);
CREATE INDEX IF NOT EXISTS idx_ppra_county  ON ppra_contracts(county);
CREATE INDEX IF NOT EXISTS idx_ppra_fy      ON ppra_contracts(fiscal_year);
CREATE INDEX IF NOT EXISTS idx_ppra_method  ON ppra_contracts(procurement_method);
CREATE INDEX IF NOT EXISTS idx_oag_fy       ON oag_reports(fiscal_year);
CREATE INDEX IF NOT EXISTS idx_cob_fy       ON cob_reports(fiscal_year);
CREATE INDEX IF NOT EXISTS idx_knbs_year    ON knbs_releases(year);
CREATE INDEX IF NOT EXISTS idx_rf_entity    ON red_flags(entity);
CREATE INDEX IF NOT EXISTS idx_rf_flag_type ON red_flags(flag_type);
CREATE INDEX IF NOT EXISTS idx_rf_ocid      ON red_flags(ocid);
CREATE INDEX IF NOT EXISTS idx_rf_severity  ON red_flags(severity);
"""


# ─── Connection helper ────────────────────────────────────────────────────────
def get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    """Create all tables if they don't exist."""
    with get_conn(db_path) as conn:
        conn.executescript(_SCHEMA)
    log.info("Database initialised at %s", db_path)


# ─── Generic upsert ───────────────────────────────────────────────────────────
def upsert(
    table: str,
    rows: list[dict[str, Any]],
    conflict_col: str = "id",
    db_path: Path = DB_PATH,
) -> int:
    """
    INSERT OR REPLACE rows into ``table``.
    Returns number of rows affected.
    """
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ", ".join("?" * len(cols))
    col_names = ", ".join(cols)
    sql = (
        f"INSERT OR REPLACE INTO {table} ({col_names}) "
        f"VALUES ({placeholders})"
    )
    values = [tuple(r.get(c) for c in cols) for r in rows]
    with get_conn(db_path) as conn:
        conn.executemany(sql, values)
    return len(rows)


def insert_if_new(
    table: str,
    rows: list[dict[str, Any]],
    db_path: Path = DB_PATH,
) -> int:
    """INSERT OR IGNORE — only inserts rows that don't already exist."""
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ", ".join("?" * len(cols))
    col_names = ", ".join(cols)
    sql = (
        f"INSERT OR IGNORE INTO {table} ({col_names}) "
        f"VALUES ({placeholders})"
    )
    values = [tuple(r.get(c) for c in cols) for r in rows]
    with get_conn(db_path) as conn:
        cur = conn.executemany(sql, values)
        return cur.rowcount


# ─── Scrape log helpers ───────────────────────────────────────────────────────
def log_start(source: str, db_path: Path = DB_PATH) -> int:
    """Create a scrape_log entry and return its id."""
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO scrape_log (source, started_at, status) VALUES (?, ?, 'running')",
            (source, datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def log_finish(
    run_id: int,
    status: str = "ok",
    rows_added: int = 0,
    files_saved: int = 0,
    notes: str = "",
    db_path: Path = DB_PATH,
) -> None:
    """Update a scrape_log entry on completion."""
    with get_conn(db_path) as conn:
        conn.execute(
            """UPDATE scrape_log
               SET finished_at = ?, status = ?, rows_added = ?,
                   files_saved = ?, notes = ?
               WHERE id = ?""",
            (datetime.utcnow().isoformat(), status, rows_added,
             files_saved, notes, run_id),
        )


# ─── File registry helper ─────────────────────────────────────────────────────
def register_file(
    source: str,
    url: str,
    local_path: Path,
    status: str = "ok",
    db_path: Path = DB_PATH,
) -> None:
    size_kb = local_path.stat().st_size / 1024 if local_path.exists() else 0.0
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO downloaded_files
               (source, url, local_path, file_size_kb, status)
               VALUES (?, ?, ?, ?, ?)""",
            (source, url, str(local_path), round(size_kb, 2), status),
        )


# ─── Export helpers ───────────────────────────────────────────────────────────
def export_table_to_csv(
    table: str,
    dest: Path,
    db_path: Path = DB_PATH,
) -> Path:
    """Export a table to CSV using pandas."""
    import pandas as pd
    with get_conn(db_path) as conn:
        df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
    dest.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(dest, index=False, encoding="utf-8-sig")
    log.info("Exported %s → %s (%d rows)", table, dest.name, len(df))
    return dest


def export_all_tables(dest_dir: Path, db_path: Path = DB_PATH) -> list[Path]:
    """Export every table to CSV files in ``dest_dir``."""
    tables = [
        "ppra_contracts", "ppra_tenders",
        "knbs_releases",
        "oag_reports",
        "cob_reports",
        "treasury_docs",
        "downloaded_files",
        "scrape_log",
        "red_flags",
    ]
    paths = []
    for t in tables:
        try:
            p = export_table_to_csv(t, dest_dir / f"{t}.csv", db_path)
            paths.append(p)
        except Exception as exc:
            log.warning("Could not export %s: %s", t, exc)
    return paths

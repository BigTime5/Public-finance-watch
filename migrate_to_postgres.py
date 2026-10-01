import sqlite3
import psycopg2
import os
import logging
from config import DB_PATH
from psycopg2.extras import execute_values

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migrate")

# Manually load .env (same pattern as extract_nlp.py)
env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(env_path):
    with open(env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")

NEON_URL = os.environ.get("DATABASE_URL")

def migrate_table(sqlite_conn, pg_conn, table_name, columns):
    logger.info(f"Migrating table {table_name}...")
    
    # Read from SQLite
    sqlite_conn.row_factory = sqlite3.Row
    rows = sqlite_conn.execute(f"SELECT * FROM {table_name}").fetchall()
    
    if not rows:
        logger.info(f"Table {table_name} is empty. Skipping.")
        return

    # Prepare data for insertion
    insert_query = f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES %s"
    
    # We need to map the rows to tuples matching the column order
    values = []
    for row in rows:
        values.append(tuple(row[col] for col in columns))

    with pg_conn.cursor() as cursor:
        execute_values(cursor, insert_query, values)
    
    pg_conn.commit()
    logger.info(f"Migrated {len(rows)} rows to {table_name}.")

def create_pg_schema(pg_conn):
    logger.info("Creating schema in PostgreSQL...")
    schema = """
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
        scraped_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        scraped_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS knbs_releases (
        id              SERIAL PRIMARY KEY,
        title           TEXT NOT NULL,
        category        TEXT,
        release_type    TEXT,
        year            INTEGER,
        chapter         TEXT,
        file_url        TEXT UNIQUE,
        local_path      TEXT,
        scraped_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS oag_reports (
        id              SERIAL PRIMARY KEY,
        title           TEXT NOT NULL,
        fiscal_year     TEXT,
        report_type     TEXT,
        file_url        TEXT UNIQUE,
        local_path      TEXT,
        scraped_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS cob_reports (
        id              SERIAL PRIMARY KEY,
        title           TEXT NOT NULL,
        fiscal_year     TEXT,
        quarter         TEXT,
        government_level TEXT,
        file_url        TEXT UNIQUE,
        local_path      TEXT,
        scraped_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS treasury_docs (
        id              SERIAL PRIMARY KEY,
        title           TEXT NOT NULL,
        doc_type        TEXT,
        fiscal_year     TEXT,
        file_url        TEXT UNIQUE,
        local_path      TEXT,
        scraped_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS downloaded_files (
        id              SERIAL PRIMARY KEY,
        source          TEXT NOT NULL,
        url             TEXT UNIQUE NOT NULL,
        local_path      TEXT NOT NULL,
        file_size_kb    REAL,
        downloaded_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status          TEXT NOT NULL DEFAULT 'ok'
    );

    CREATE TABLE IF NOT EXISTS scrape_log (
        id          SERIAL PRIMARY KEY,
        source      TEXT NOT NULL,
        started_at  TEXT NOT NULL,
        finished_at TEXT,
        rows_added  INTEGER DEFAULT 0,
        files_saved INTEGER DEFAULT 0,
        status      TEXT DEFAULT 'running',
        notes       TEXT
    );

    CREATE TABLE IF NOT EXISTS report_findings (
        id              SERIAL PRIMARY KEY,
        report_type     TEXT,
        entity          TEXT,
        keyword         TEXT,
        context_snippet TEXT,
        page_num        INTEGER,
        amount_lost_kes REAL,
        responsible_officers TEXT,
        severity        TEXT,
        scraped_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS red_flags (
        id              SERIAL PRIMARY KEY,
        flag_type       TEXT NOT NULL,
        severity        TEXT,
        ocid            TEXT,
        description     TEXT,
        entity          TEXT,
        supplier        TEXT,
        fiscal_year     TEXT,
        award_kes       REAL,
        detected_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    
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
    with pg_conn.cursor() as cursor:
        cursor.execute(schema)
    pg_conn.commit()

def run_migration():
    if not NEON_URL:
        logger.error("DATABASE_URL is not set!")
        return

    try:
        sqlite_conn = sqlite3.connect(DB_PATH)
        pg_conn = psycopg2.connect(NEON_URL)

        create_pg_schema(pg_conn)

        tables_to_migrate = [
            ("ppra_contracts", ["ocid", "tender_id", "tender_title", "procuring_entity", "county", "procurement_method", "tender_status", "tender_value", "tender_currency", "award_date", "award_value", "award_currency", "supplier_name", "supplier_id", "contract_start", "contract_end", "contract_value", "contract_currency", "fiscal_year", "raw_json", "scraped_at"]),
            ("ppra_tenders", ["tender_id", "title", "procuring_entity", "category", "procurement_method", "status", "closing_date", "published_date", "url", "scraped_at"]),
            ("knbs_releases", ["id", "title", "category", "release_type", "year", "chapter", "file_url", "local_path", "scraped_at"]),
            ("oag_reports", ["id", "title", "fiscal_year", "report_type", "file_url", "local_path", "scraped_at"]),
            ("cob_reports", ["id", "title", "fiscal_year", "quarter", "government_level", "file_url", "local_path", "scraped_at"]),
            ("treasury_docs", ["id", "title", "doc_type", "fiscal_year", "file_url", "local_path", "scraped_at"]),
            ("downloaded_files", ["id", "source", "url", "local_path", "file_size_kb", "downloaded_at", "status"]),
            ("scrape_log", ["id", "source", "started_at", "finished_at", "rows_added", "files_saved", "status", "notes"]),
            ("report_findings", ["id", "report_type", "entity", "keyword", "context_snippet", "page_num", "amount_lost_kes", "responsible_officers", "severity", "scraped_at"]),
            ("red_flags", ["id", "flag_type", "severity", "ocid", "description", "entity", "supplier", "fiscal_year", "award_kes", "detected_at"])
        ]

        for table, cols in tables_to_migrate:
            migrate_table(sqlite_conn, pg_conn, table, cols)
            
        logger.info("Migration completed successfully!")
        
    except Exception as e:
        logger.error(f"Migration failed: {e}")
    finally:
        if 'sqlite_conn' in locals(): sqlite_conn.close()
        if 'pg_conn' in locals(): pg_conn.close()

if __name__ == "__main__":
    run_migration()

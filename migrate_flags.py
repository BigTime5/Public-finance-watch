"""
migrate_flags.py — One-time migration: CSV red flags → SQLite red_flags table
"""
import csv
import os
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

from config import DB_PATH, PROC_DIR
from storage.database import init_db, get_conn

FLAGS_CSV = PROC_DIR / "red_flags.csv"


def migrate():
    print(f"Database: {DB_PATH}")
    print(f"CSV:      {FLAGS_CSV}")

    if not FLAGS_CSV.exists():
        print("ERROR: red_flags.csv not found. Run the anomaly detection first.")
        sys.exit(1)

    # Ensure schema is up to date (creates red_flags table if missing)
    init_db()

    # Read CSV
    with open(FLAGS_CSV, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            clean = {}
            for k, v in row.items():
                clean[k.strip().lstrip("\ufeff")] = (v.strip() if v else "")
            rows.append(clean)

    print(f"Read {len(rows)} flags from CSV")

    # Insert into SQLite
    with get_conn() as conn:
        conn.execute("DELETE FROM red_flags")
        conn.executemany(
            """INSERT INTO red_flags
               (flag_type, severity, ocid, description, entity, supplier, fiscal_year, award_kes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    r.get("flag_type", ""),
                    r.get("severity", ""),
                    r.get("ocid", ""),
                    r.get("description", ""),
                    r.get("entity", ""),
                    r.get("supplier", ""),
                    r.get("fiscal_year", ""),
                    float(r.get("award_kes") or r.get("award_value") or 0),
                )
                for r in rows
            ],
        )

    # Verify
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM red_flags").fetchone()[0]
        print(f"[OK] Migrated {count} red flags to SQLite")


if __name__ == "__main__":
    migrate()

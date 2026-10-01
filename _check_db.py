"""Quick diagnostic script to verify DB schema + red_flags.csv structure"""
import sqlite3
import csv
import os

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "data", "kenya_intel.db")
FLAGS = os.path.join(BASE, "data", "processed", "red_flags.csv")

print("=" * 60)
print("DATABASE CHECK")
print("=" * 60)
conn = sqlite3.connect(DB)
cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cursor.fetchall()]
print(f"Tables: {tables}")

for table in tables:
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    col_names = [c[1] for c in cols]
    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"\n  {table}: {count} rows")
    print(f"    Columns: {col_names}")
    if count > 0:
        row = conn.execute(f"SELECT * FROM {table} LIMIT 1").fetchone()
        print(f"    Sample: {dict(zip(col_names, row))}")

conn.close()

print("\n" + "=" * 60)
print("RED FLAGS CSV CHECK")
print("=" * 60)
if os.path.exists(FLAGS):
    with open(FLAGS, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames
        print(f"Columns: {cols}")
        rows = list(reader)
        print(f"Total rows: {len(rows)}")
        if rows:
            print(f"Sample row: {dict(rows[0])}")
else:
    print("red_flags.csv NOT FOUND!")

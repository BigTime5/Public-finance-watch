"""
migrate_values.py — Backfill award_value, contract_value, supplier_name
into ppra_contracts from the multi-sheet PPRA OCDS Excel files.

The original parser only read the 'main' sheet which has no monetary data.
This script reads awards, awards_suppliers, and contracts sheets, joins
them on main_ocid = ocid, and UPDATEs the existing rows.
"""

import sqlite3
import glob
import os
import sys

import pandas as pd
import numpy as np

DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data", "kenya_intel.db"
)
RAW_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data", "raw", "ppra"
)


def read_sheet_safe(path, sheet_name):
    """Read an Excel sheet, returning empty DataFrame on error."""
    try:
        return pd.read_excel(path, sheet_name=sheet_name, dtype=str)
    except Exception as e:
        print(f"  Warning: could not read sheet '{sheet_name}': {e}")
        return pd.DataFrame()


def process_file(path, conn):
    """Process one PPRA OCDS Excel file and update the database."""
    basename = os.path.basename(path)
    print(f"\n{'='*60}")
    print(f"Processing: {basename}")
    print(f"{'='*60}")

    # --- Read all sheets ---
    print("  Reading 'main' sheet...")
    df_main = read_sheet_safe(path, "main")
    if df_main.empty:
        print("  SKIP: main sheet is empty")
        return 0

    print("  Reading 'awards' sheet...")
    df_awards = read_sheet_safe(path, "awards")

    print("  Reading 'awards_suppliers' sheet...")
    df_suppliers = read_sheet_safe(path, "awards_suppliers")

    print("  Reading 'contracts' sheet...")
    df_contracts = read_sheet_safe(path, "contracts")

    print(f"  Rows:  main={len(df_main)}  awards={len(df_awards)}  "
          f"suppliers={len(df_suppliers)}  contracts={len(df_contracts)}")

    # --- Build award lookup: main_ocid -> best award ---
    award_map = {}
    if not df_awards.empty and "main_ocid" in df_awards.columns:
        for _, row in df_awards.iterrows():
            ocid = str(row.get("main_ocid", "")).strip()
            if not ocid:
                continue
            try:
                val = float(row.get("value_amount", 0) or 0)
            except (ValueError, TypeError):
                val = 0.0

            if ocid not in award_map or val > award_map[ocid].get("award_value", 0):
                award_date = str(row.get("contractPeriod_startDate", "") or "").strip()
                if "T" in award_date:
                    award_date = award_date.split("T")[0]
                award_map[ocid] = {
                    "award_value": val,
                    "award_currency": str(row.get("value_currency", "KES") or "KES").strip(),
                    "award_date": award_date if award_date and award_date != "nan" else None,
                }

    print(f"  Award lookup: {len(award_map)} OCIDs with award values")

    # --- Build supplier lookup: main_ocid -> supplier name/id ---
    supplier_map = {}
    if not df_suppliers.empty and "main_ocid" in df_suppliers.columns:
        for _, row in df_suppliers.iterrows():
            ocid = str(row.get("main_ocid", "")).strip()
            if not ocid:
                continue
            name = str(row.get("name", "") or "").strip()
            sid = str(row.get("id", "") or "").strip()
            if name and name != "nan" and ocid not in supplier_map:
                supplier_map[ocid] = {
                    "supplier_name": name,
                    "supplier_id": sid if sid != "nan" else None,
                }

    print(f"  Supplier lookup: {len(supplier_map)} OCIDs with supplier names")

    # --- Build contract lookup: main_ocid -> contract value ---
    contract_map = {}
    if not df_contracts.empty and "main_ocid" in df_contracts.columns:
        for _, row in df_contracts.iterrows():
            ocid = str(row.get("main_ocid", "")).strip()
            if not ocid:
                continue
            try:
                val = float(row.get("value_amount", 0) or 0)
            except (ValueError, TypeError):
                val = 0.0

            if ocid not in contract_map or val > contract_map[ocid].get("contract_value", 0):
                start = str(row.get("period_startDate", "") or "").strip()
                end = str(row.get("period_endDate", "") or "").strip()
                if "T" in start:
                    start = start.split("T")[0]
                if "T" in end:
                    end = end.split("T")[0]
                contract_map[ocid] = {
                    "contract_value": val,
                    "contract_start": start if start and start != "nan" else None,
                    "contract_end": end if end and end != "nan" else None,
                }

    print(f"  Contract lookup: {len(contract_map)} OCIDs with contract values")

    # --- Now UPDATE the database ---
    cursor = conn.cursor()

    all_ocids = set()
    if "ocid" in df_main.columns:
        all_ocids = set(df_main["ocid"].dropna().astype(str).str.strip().unique())

    print(f"  Updating {len(all_ocids)} OCIDs in database...")

    batch = []
    for ocid in all_ocids:
        award = award_map.get(ocid, {})
        supplier = supplier_map.get(ocid, {})
        contract = contract_map.get(ocid, {})

        award_value = award.get("award_value")
        award_date = award.get("award_date")
        award_currency = award.get("award_currency")
        supplier_name = supplier.get("supplier_name")
        supplier_id = supplier.get("supplier_id")
        contract_value = contract.get("contract_value")
        contract_start = contract.get("contract_start")
        contract_end = contract.get("contract_end")

        if award_value or supplier_name or contract_value:
            batch.append((
                award_value, award_currency, award_date,
                supplier_name, supplier_id,
                contract_value, contract_start, contract_end,
                ocid,
            ))

    if batch:
        cursor.executemany("""
            UPDATE ppra_contracts SET
                award_value = COALESCE(?, award_value),
                award_currency = COALESCE(?, award_currency),
                award_date = COALESCE(?, award_date),
                supplier_name = COALESCE(?, supplier_name),
                supplier_id = COALESCE(?, supplier_id),
                contract_value = COALESCE(?, contract_value),
                contract_start = COALESCE(?, contract_start),
                contract_end = COALESCE(?, contract_end)
            WHERE ocid = ?
        """, batch)
        conn.commit()
        updated = cursor.rowcount
        print(f"  Updated {len(batch)} rows (DB reports {updated} changes)")
    else:
        print("  No data to update")

    return len(batch)


def main():
    files = sorted(glob.glob(os.path.join(RAW_DIR, "ppra_ocds_*.xlsx")))
    if not files:
        print(f"No PPRA Excel files found in {RAW_DIR}")
        sys.exit(1)

    print(f"Found {len(files)} PPRA Excel files")
    print(f"Database: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    total_updated = 0
    for f in files:
        total_updated += process_file(f, conn)

    # Verify
    cursor = conn.cursor()
    stats = cursor.execute("""
        SELECT
            COUNT(*) as total,
            COUNT(award_value) as with_award,
            COUNT(supplier_name) as with_supplier,
            SUM(CASE WHEN award_value > 0 THEN award_value ELSE 0 END) as total_spend,
            COUNT(DISTINCT supplier_name) as distinct_suppliers
        FROM ppra_contracts
    """).fetchone()

    print(f"\n{'='*60}")
    print(f"MIGRATION COMPLETE")
    print(f"{'='*60}")
    print(f"  Total contracts:      {stats[0]:,}")
    print(f"  With award_value:     {stats[1]:,}")
    print(f"  With supplier_name:   {stats[2]:,}")
    print(f"  Total spend (KES):    {stats[3]:,.0f}")
    print(f"  Distinct suppliers:   {stats[4]:,}")
    print(f"  Rows updated:         {total_updated:,}")

    conn.close()


if __name__ == "__main__":
    main()

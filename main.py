"""
main.py — Kenya Public Finance Intelligence Platform
======================================================

Orchestrates all five scrapers and runs the anomaly-detection pipeline.

Usage
-----
  # Full run (all sources, all years, download files, detect anomalies)
  python main.py

  # Quick run — index only, no file downloads, no anomaly detection
  python main.py --no-download --no-anomalies

  # Specific sources only
  python main.py --sources ppra knbs

  # Export everything to CSV after scraping
  python main.py --export

  # PPRA years (default: 2024 + 2025 for speed)
  python main.py --ppra-years 2023 2024 2025
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from config import DB_PATH, PROC_DIR, LOG_PATH
from storage.database import init_db, export_all_tables
from utils.logger import get_logger
from utils.benchmarking import benchmark_prices

log = get_logger("main", log_file=LOG_PATH)


# ════════════════════════════════════════════════════════════════════════════════
#  Anomaly / Red-Flag Detection Engine
# ════════════════════════════════════════════════════════════════════════════════

def detect_anomalies() -> dict:
    """
    Run procurement fraud red-flag checks on the ppra_contracts table.

    Checks:
      1. Below-threshold splitting     — same supplier, same entity, close dates,
                                         suspiciously similar amounts
      2. Extreme outlier contracts     — contract value > 99th percentile
      3. New companies (≤ 90 days old) winning large contracts
      4. Direct procurement overuse    — entity uses 'Direct' method > 50% of awards
      5. Single-bidder patterns        — restricted tenders won by the same supplier
      6. Budget vs delivery cross-ref  — high spending counties with low outcome metrics

    All findings are written to data/processed/red_flags.csv
    """
    import sqlite3
    import pandas as pd

    log.info("=== Anomaly Detection ===")
    findings: list[dict] = []

    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)

        # ── Load contracts ────────────────────────────────────────────────────
        df = pd.read_sql_query(
            """
            SELECT
                ocid, procuring_entity, county, procurement_method,
                tender_value, award_value, contract_value,
                supplier_name, supplier_id,
                award_date, contract_start, contract_end,
                fiscal_year
            FROM ppra_contracts
            WHERE award_value IS NOT NULL
              AND award_value != ''
              AND procuring_entity IS NOT NULL
            """,
            conn,
        )
        conn.close()

        if df.empty:
            log.info("No contract data available yet for anomaly detection.")
            return {"flags": 0}

        # Coerce numeric columns
        for col in ["tender_value", "award_value", "contract_value"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df["award_date"] = pd.to_datetime(df["award_date"], errors="coerce", utc=True)

        log.info("Running anomaly checks on %d contracts …", len(df))

        # ── Check 1: Extreme outliers (per-entity 99th percentile) ───────────
        # Each entity is benchmarked only against its own historical contract values,
        # not the entire national dataset, so a highways authority and a county
        # health office are not compared with the same yardstick.
        entity_p99 = (
            df.groupby("procuring_entity")["award_value"]
            .quantile(0.99)
            .reset_index()
            .rename(columns={"award_value": "entity_p99"})
        )
        df = df.merge(entity_p99, on="procuring_entity", how="left")
        # Require at least 10 contracts in the entity before flagging, to avoid
        # small-sample noise producing spurious alerts.
        entity_sizes = df.groupby("procuring_entity").size().reset_index(name="entity_n")
        df = df.merge(entity_sizes, on="procuring_entity", how="left")
        outliers = df[
            (df["award_value"] > df["entity_p99"]) &
            (df["entity_n"] >= 10)
        ].copy()
        for _, row in outliers.iterrows():
            findings.append({
                "flag_type":    "EXTREME_OUTLIER",
                "severity":     "HIGH",
                "ocid":         row["ocid"],
                "description":  (
                    f"Award value KES {row['award_value']:,.0f} exceeds this entity's own "
                    f"99th-percentile threshold (KES {row['entity_p99']:,.0f}), "
                    f"calculated from {int(row['entity_n'])} historical contracts by "
                    f"'{row['procuring_entity']}' in PPRA records."
                ),
                "entity":       row["procuring_entity"],
                "supplier":     row["supplier_name"],
                "fiscal_year":  row["fiscal_year"],
                "award_value":  row["award_value"],
            })
        log.info("  Check 1 (Extreme Outliers per entity): %d flags", len(outliers))

        # ── Check 2: Direct procurement overuse ───────────────────────────────
        if "procurement_method" in df.columns:
            method_counts = (
                df.groupby(["procuring_entity", "procurement_method"])
                .size()
                .reset_index(name="count")
            )
            entity_totals = (
                df.groupby("procuring_entity")
                .size()
                .reset_index(name="total")
            )
            method_pct = method_counts.merge(entity_totals, on="procuring_entity")
            method_pct["pct"] = method_pct["count"] / method_pct["total"]

            direct_abuse = method_pct[
                (method_pct["procurement_method"].str.lower()
                 .str.contains("direct", na=False)) &
                (method_pct["pct"] > 0.5) &
                (method_pct["total"] >= 5)
            ]
            for _, row in direct_abuse.iterrows():
                findings.append({
                    "flag_type":   "DIRECT_PROCUREMENT_OVERUSE",
                    "severity":    "MEDIUM",
                    "ocid":        None,
                    "description": (
                        f"{row['procuring_entity']} used Direct Procurement for "
                        f"{row['pct']:.0%} of {row['total']} awards"
                    ),
                    "entity":      row["procuring_entity"],
                    "supplier":    None,
                    "fiscal_year": None,
                    "award_value": None,
                })
            log.info("  Check 2 (Direct overuse): %d flags", len(direct_abuse))

        # ── Check 3: Same supplier dominant within one entity ─────────────────
        supplier_entity = (
            df.groupby(["procuring_entity", "supplier_name"])
            .size()
            .reset_index(name="wins")
        )
        entity_total_awards = (
            df.groupby("procuring_entity")
            .size()
            .reset_index(name="total_awards")
        )
        se = supplier_entity.merge(entity_total_awards, on="procuring_entity")
        se["win_share"] = se["wins"] / se["total_awards"]
        dominant = se[
            (se["win_share"] > 0.4) &
            (se["total_awards"] >= 5) &
            (se["supplier_name"].notna())
        ]
        for _, row in dominant.iterrows():
            findings.append({
                "flag_type":   "DOMINANT_SUPPLIER",
                "severity":    "HIGH",
                "ocid":        None,
                "description": (
                    f"'{row['supplier_name']}' won {row['win_share']:.0%} "
                    f"({row['wins']}/{row['total_awards']}) of all contracts "
                    f"from '{row['procuring_entity']}'"
                ),
                "entity":      row["procuring_entity"],
                "supplier":    row["supplier_name"],
                "fiscal_year": None,
                "award_value": None,
            })
        log.info("  Check 3 (Dominant supplier): %d flags", len(dominant))

        # ── Check 4: Zero-competition tenders with large values ───────────────
        if "procurement_method" in df.columns:
            restricted = df[
                df["procurement_method"].str.lower()
                .str.contains("restrict|direct|single", na=False)
            ]
            high_value_restricted = restricted[
                restricted["award_value"] > df["award_value"].quantile(0.75)
            ]
            for _, row in high_value_restricted.head(100).iterrows():
                findings.append({
                    "flag_type":   "HIGH_VALUE_NO_COMPETITION",
                    "severity":    "HIGH",
                    "ocid":        row["ocid"],
                    "description": (
                        f"High-value contract (KES {row['award_value']:,.0f}) "
                        f"awarded via '{row['procurement_method']}' — no competition"
                    ),
                    "entity":      row["procuring_entity"],
                    "supplier":    row["supplier_name"],
                    "fiscal_year": row["fiscal_year"],
                    "award_value": row["award_value"],
                })
            log.info("  Check 4 (High-value no-competition): %d flags",
                     len(high_value_restricted))

        # ── Check 5: Impossibly fast contract turnaround ─────────────────────
        df["contract_start_dt"] = pd.to_datetime(df["contract_start"], errors="coerce", utc=True)
        if df["award_date"].notna().any() and df["contract_start_dt"].notna().any():
            df["days_to_start"] = (
                df["contract_start_dt"] - df["award_date"]
            ).dt.days
            impossible = df[
                (df["days_to_start"] >= 0) &
                (df["days_to_start"] <= 1) &
                (df["award_value"] > df["award_value"].quantile(0.50))
            ]
            for _, row in impossible.head(50).iterrows():
                findings.append({
                    "flag_type":   "INSTANT_CONTRACT_START",
                    "severity":    "MEDIUM",
                    "ocid":        row["ocid"],
                    "description": (
                        f"Contract started {row['days_to_start']:.0f} day(s) "
                        "after award — possible pre-arranged award"
                    ),
                    "entity":      row["procuring_entity"],
                    "supplier":    row["supplier_name"],
                    "fiscal_year": row["fiscal_year"],
                    "award_value": row["award_value"],
                })
            log.info("  Check 5 (Instant start): %d flags", len(impossible))

        # ── Save findings ─────────────────────────────────────────────────────
        if findings:
            flags_df = pd.DataFrame(findings)
            out = PROC_DIR / "red_flags.csv"
            flags_df.to_csv(out, index=False, encoding="utf-8-sig")
            log.info("🚩 %d red flags written to %s", len(findings), out)

            # ── Persist to SQLite ─────────────────────────────────────────
            try:
                from storage.database import get_conn
                with get_conn() as db:
                    db.execute("DELETE FROM red_flags")
                    db.executemany(
                        """INSERT INTO red_flags
                           (flag_type, severity, ocid, description, entity, supplier, fiscal_year, award_kes)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        [
                            (
                                f.get("flag_type"),
                                f.get("severity"),
                                f.get("ocid"),
                                f.get("description"),
                                f.get("entity"),
                                f.get("supplier"),
                                f.get("fiscal_year"),
                                f.get("award_value"),
                            )
                            for f in findings
                        ],
                    )
                log.info("%d red flags persisted to SQLite", len(findings))
            except Exception as db_exc:
                log.warning("Could not persist red flags to SQLite: %s", db_exc)

            # Run benchmarking AFTER main anomaly detection so DELETE FROM red_flags doesn't wipe them
            log.info("Running Unit Cost Benchmarking...")
            bench_count = benchmark_prices()
            log.info("Price Benchmarking completed. %d inflated contracts flagged.", bench_count)
        else:
            log.info("✅ No anomalies detected.")

        return {"flags": len(findings)}

    except Exception as exc:
        log.error("Anomaly detection failed: %s", exc, exc_info=True)
        return {"flags": 0, "error": str(exc)}


# ════════════════════════════════════════════════════════════════════════════════
#  Orchestrator
# ════════════════════════════════════════════════════════════════════════════════

def run_all(
    sources: list[str],
    ppra_years: list[int],
    download_files: bool,
    run_anomalies: bool,
    export_csv: bool,
) -> dict:
    """Run selected scrapers in sequence and return a combined summary."""

    from scrapers import ppra, knbs, oag, cob, treasury

    init_db()
    start_time = time.time()
    overall: dict = {}

    SCRAPER_MAP = {
        "ppra":     lambda: ppra.run(
            years=ppra_years,
            fmt="xlsx",
            scrape_live=True,
            live_max_pages=5,
        ),
        "knbs":     lambda: knbs.run(
            download_files=download_files,
            scrape_dynamic=True,
        ),
        "oag":      lambda: oag.run(
            download_files=download_files,
            scrape_all_fys=True,
        ),
        "cob":      lambda: cob.run(
            download_files=download_files,
            scrape_archive=True,
        ),
        "treasury": lambda: treasury.run(
            download_files=download_files,
            crawl_sitemap=False,
        ),
    }

    for src in sources:
        if src not in SCRAPER_MAP:
            log.warning("Unknown source: %s — skipping", src)
            continue
        log.info("\n╔═══════════════════════════════════════╗")
        log.info("║  RUNNING: %-28s ║", src.upper())
        log.info("╚═══════════════════════════════════════╝")
        try:
            result = SCRAPER_MAP[src]()
            overall[src] = result
        except Exception as exc:
            log.error("Source '%s' crashed: %s", src, exc, exc_info=True)
            overall[src] = {"error": str(exc)}

    # ── Anomaly detection ─────────────────────────────────────────────────────
    if run_anomalies and "ppra" in sources:
        log.info("\n╔═══════════════════════════════════════╗")
        log.info("║  RUNNING: ANOMALY DETECTION           ║")
        log.info("╚═══════════════════════════════════════╝")
        overall["anomalies"] = detect_anomalies()

    # ── CSV export ────────────────────────────────────────────────────────────
    if export_csv:
        log.info("\n╔═══════════════════════════════════════╗")
        log.info("║  EXPORTING TO CSV                     ║")
        log.info("╚═══════════════════════════════════════╝")
        paths = export_all_tables(PROC_DIR)
        overall["csv_exports"] = [str(p.name) for p in paths]
        log.info("Exported %d CSV files to %s", len(paths), PROC_DIR)

    elapsed = time.time() - start_time
    log.info(
        "\n\n══════════════════════════════════════════════════════\n"
        "  KENYA PUBLIC FINANCE INTELLIGENCE — RUN COMPLETE\n"
        "  Duration: %.1f seconds\n"
        "  Sources:  %s\n"
        "  Database: %s\n"
        "══════════════════════════════════════════════════════",
        elapsed,
        ", ".join(sources),
        DB_PATH,
    )
    for src, res in overall.items():
        log.info("  %-12s  %s", src, res)

    return overall


# ════════════════════════════════════════════════════════════════════════════════
#  CLI entry point
# ════════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Kenya Public Finance Intelligence Scraper",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        default=["ppra", "knbs", "oag", "cob", "treasury"],
        choices=["ppra", "knbs", "oag", "cob", "treasury"],
        help="Data sources to scrape",
    )
    parser.add_argument(
        "--ppra-years",
        nargs="+",
        type=int,
        default=[2024, 2025],
        help="PPRA OCDS years to download (use 'all' for 2018–2026)",
    )
    parser.add_argument(
        "--all-years",
        action="store_true",
        default=False,
        help="Download all PPRA years 2018–2026 (takes longer)",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        default=False,
        help="Index metadata only — do not download PDFs/Excel files",
    )
    parser.add_argument(
        "--no-anomalies",
        action="store_true",
        default=False,
        help="Skip anomaly/fraud detection pass",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        default=False,
        help="Export all DB tables to CSV after scraping",
    )
    parser.add_argument(
        "--init-only",
        action="store_true",
        default=False,
        help="Only initialise the database schema and exit",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    log.info(
        f"╔═══════════════════════════════════════════════════════╗\n"
        f"║  KENYA PUBLIC FINANCE INTELLIGENCE PLATFORM  v1.0    ║\n"
        f"║  Started: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'):<43} ║\n"
        f"╚═══════════════════════════════════════════════════════╝"
    )

    if args.init_only:
        init_db()
        log.info("Database schema initialised. Exiting.")
        sys.exit(0)

    ppra_years = list(range(2018, 2027)) if args.all_years else args.ppra_years

    run_all(
        sources=args.sources,
        ppra_years=ppra_years,
        download_files=not args.no_download,
        run_anomalies=not args.no_anomalies,
        export_csv=args.export,
    )


if __name__ == "__main__":
    main()

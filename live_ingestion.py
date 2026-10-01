import argparse
import sys
import time
from datetime import datetime

from scrapers.ppra import scrape_live_contracts
from main import detect_anomalies
from utils.logger import get_logger
from storage.database import init_db

log = get_logger("live_ingestion")

def run_ingestion(max_pages: int = 5):
    """
    Run a single cycle of live ingestion and anomaly detection.
    """
    log.info("Starting live ingestion cycle...")
    
    # 1. Scrape live contracts from PPIP
    try:
        new_contracts_count = scrape_live_contracts(max_pages=max_pages)
        log.info(f"Successfully scraped and upserted {new_contracts_count} live contracts.")
    except Exception as e:
        log.error(f"Error during live contract scraping: {e}")
        return
        
    # 2. Run anomaly detection if new contracts were found
    # (Running it regardless is fine as well, it recalculates flags)
    try:
        log.info("Running anomaly detection on updated dataset...")
        results = detect_anomalies()
        log.info(f"Anomaly detection complete. Total flags: {results.get('flags', 0)}")
    except Exception as e:
        log.error(f"Error during anomaly detection: {e}")

def main():
    parser = argparse.ArgumentParser(description="Live Data Ingestion Pipeline for Kenya Public Finance Intelligence")
    parser.add_argument(
        "--once",
        action="store_true",
        default=False,
        help="Run the ingestion pipeline once and exit."
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        default=False,
        help="Run the ingestion pipeline continuously in the background."
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=24,
        help="Interval in hours between runs if in daemon mode (default: 24)."
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=5,
        help="Number of pages to scrape from PPIP portal per run (default: 5)."
    )
    
    args = parser.parse_args()
    
    # Ensure DB is initialized
    init_db()
    
    if args.once:
        log.info(f"Running single ingestion pass (max {args.pages} pages)...")
        run_ingestion(max_pages=args.pages)
        log.info("Pass complete.")
        sys.exit(0)
        
    if args.daemon:
        interval_seconds = args.interval * 3600
        log.info(f"Starting daemon mode. Interval: {args.interval} hours ({interval_seconds} seconds). Max pages per run: {args.pages}")
        
        while True:
            log.info(f"--- Daemon Run Triggered: {datetime.utcnow().isoformat()} ---")
            run_ingestion(max_pages=args.pages)
            log.info(f"Sleeping for {args.interval} hours...")
            time.sleep(interval_seconds)
            
    # Default to just showing help if no mode selected
    if not args.once and not args.daemon:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()

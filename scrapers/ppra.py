"""
scrapers/ppra.py — Kenya Public Procurement Data (PPRA / PPIP)

Two complementary approaches:
  1. Bulk OCDS download from the OCP Data Registry (data.open-contracting.org)
     — structured JSON/Excel with 252 000+ tenders and 107 000+ awards.
     Updated daily. Zero bot-detection risk. Best for historical analysis.

  2. Live tender scraping from tenders.go.ke (JS-rendered portal)
     — captures tenders not yet in the OCDS bulk export.
     Uses requests + BeautifulSoup (the portal renders server-side tables
     when accessed without JS; DynamicFetcher used as fallback).

Data stored in:  ppra_contracts, ppra_tenders  tables
Files saved to:  data/raw/ppra/
"""

import json
import gzip
import tarfile
import io
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

import requests
import pandas as pd

from config import (
    RAW_DIR, PPRA_YEARS, ppra_download_url, ppra_all_url,
    PPIP_TENDERS_URL, PPIP_CONTRACTS_URL, PPIP_ENTITIES_URL,
    REQUEST_TIMEOUT,
)
from storage.database import (
    init_db, upsert, insert_if_new,
    log_start, log_finish, register_file,
)
from utils.helpers import safe_get, download_file, extract_file_links, get_session
from utils.logger import get_logger

log = get_logger("scraper.ppra")
RAW_PPRA = RAW_DIR / "ppra"


# ════════════════════════════════════════════════════════════════════════════════
#  APPROACH 1 — OCP Data Registry bulk download
# ════════════════════════════════════════════════════════════════════════════════

def download_ocds_year(year: int, fmt: str = "xlsx") -> Optional[Path]:
    """
    Download the OCDS dataset for a single ``year`` in ``fmt`` format.
    Supported formats: 'xlsx', 'csv.tar.gz', 'jsonl.gz'
    Returns local Path on success.
    """
    url = ppra_download_url(year, fmt)
    filename = f"ppra_ocds_{year}.{fmt}"
    return download_file(url, RAW_PPRA, filename, skip_if_exists=True)


def download_all_ocds(years: Optional[list[int]] = None, fmt: str = "xlsx") -> list[Path]:
    """
    Download OCDS data for all (or specified) years.
    Returns list of successfully downloaded Paths.
    """
    years = years or PPRA_YEARS
    paths = []
    for yr in years:
        log.info("Downloading PPRA OCDS %s (%s)...", yr, fmt)
        p = download_ocds_year(yr, fmt)
        if p:
            paths.append(p)
    return paths


def _read_sheet(path: Path, sheet_name: str) -> pd.DataFrame:
    """Read a single sheet from an Excel file, returning empty DF on error."""
    try:
        return pd.read_excel(path, sheet_name=sheet_name, dtype=str)
    except Exception:
        return pd.DataFrame()


def parse_and_store_xlsx(path: Path) -> int:
    """
    Parse a downloaded PPRA OCDS Excel file and upsert records into
    the ppra_contracts table.

    The OCDS Excel has 5 sheets: main, parties, awards, awards_suppliers,
    contracts. We join them on main_ocid = ocid to build complete records.

    Returns number of rows upserted.
    """
    log.info("Parsing %s …", path.name)

    df_main = _read_sheet(path, "main")
    if df_main.empty:
        log.warning("Could not read main sheet from %s", path.name)
        return 0

    df_awards = _read_sheet(path, "awards")
    df_suppliers = _read_sheet(path, "awards_suppliers")
    df_contracts = _read_sheet(path, "contracts")

    log.info("  Sheets: main=%d  awards=%d  suppliers=%d  contracts=%d",
             len(df_main), len(df_awards), len(df_suppliers), len(df_contracts))

    # --- Build lookup dicts keyed by main_ocid ---

    # Awards: main_ocid -> {value_amount, value_currency, contractPeriod_startDate}
    award_map: dict = {}
    if not df_awards.empty and "main_ocid" in df_awards.columns:
        for _, r in df_awards.iterrows():
            ocid = str(r.get("main_ocid", "")).strip()
            if not ocid:
                continue
            try:
                val = float(r.get("value_amount", 0) or 0)
            except (ValueError, TypeError):
                val = 0.0
            if ocid not in award_map or val > award_map[ocid].get("v", 0):
                dt = str(r.get("contractPeriod_startDate", "") or "").strip()
                if "T" in dt:
                    dt = dt.split("T")[0]
                award_map[ocid] = {
                    "v": val,
                    "cur": str(r.get("value_currency", "KES") or "KES").strip(),
                    "dt": dt if dt and dt != "nan" else None,
                }

    # Suppliers: main_ocid -> {name, id}
    sup_map: dict = {}
    if not df_suppliers.empty and "main_ocid" in df_suppliers.columns:
        for _, r in df_suppliers.iterrows():
            ocid = str(r.get("main_ocid", "")).strip()
            name = str(r.get("name", "") or "").strip()
            sid = str(r.get("id", "") or "").strip()
            if ocid and name and name != "nan" and ocid not in sup_map:
                sup_map[ocid] = {"name": name, "id": sid if sid != "nan" else None}

    # Contracts: main_ocid -> {value_amount, period_startDate, period_endDate}
    con_map: dict = {}
    if not df_contracts.empty and "main_ocid" in df_contracts.columns:
        for _, r in df_contracts.iterrows():
            ocid = str(r.get("main_ocid", "")).strip()
            if not ocid:
                continue
            try:
                val = float(r.get("value_amount", 0) or 0)
            except (ValueError, TypeError):
                val = 0.0
            if ocid not in con_map or val > con_map[ocid].get("v", 0):
                s = str(r.get("period_startDate", "") or "").strip()
                e = str(r.get("period_endDate", "") or "").strip()
                if "T" in s: s = s.split("T")[0]
                if "T" in e: e = e.split("T")[0]
                con_map[ocid] = {
                    "v": val,
                    "s": s if s and s != "nan" else None,
                    "e": e if e and e != "nan" else None,
                }

    # --- Build rows by iterating over main sheet ---
    # Normalise main column names
    df_main.columns = [c.strip().lower().replace(".", "_").replace(" ", "_")
                       for c in df_main.columns]

    rows = []
    for _, row in df_main.iterrows():
        ocid_val = row.get("ocid")
        if pd.isna(ocid_val):
            continue
        ocid_str = str(ocid_val).strip()

        aw = award_map.get(ocid_str, {})
        sp = sup_map.get(ocid_str, {})
        cn = con_map.get(ocid_str, {})

        def _v(col):
            val = row.get(col)
            return None if pd.isna(val) else str(val).strip()

        award_date = aw.get("dt") or _v("date")
        # Infer fiscal year
        fiscal_year = None
        try:
            if award_date and len(award_date) >= 7:
                yr = int(award_date[:4])
                mo = int(award_date[5:7])
                fiscal_year = f"{yr}/{str(yr + 1)[-2:]}" if mo >= 7 \
                    else f"{yr - 1}/{str(yr)[-2:]}"
        except Exception:
            pass

        rec = {
            "ocid":               ocid_str,
            "tender_id":          _v("tender_id"),
            "tender_title":       _v("tender_title"),
            "procuring_entity":   _v("buyer_name"),
            "procurement_method": _v("tender_procurementmethod"),
            "tender_status":      _v("tender_status"),
            "tender_value":       None,  # not in main sheet
            "tender_currency":    None,
            "award_date":         award_date,
            "award_value":        aw.get("v"),
            "award_currency":     aw.get("cur"),
            "supplier_name":      sp.get("name"),
            "supplier_id":        sp.get("id"),
            "contract_start":     cn.get("s"),
            "contract_end":       cn.get("e"),
            "contract_value":     cn.get("v"),
            "contract_currency":  aw.get("cur"),
            "fiscal_year":        fiscal_year,
            "scraped_at":         datetime.utcnow().isoformat(),
        }
        rows.append(rec)

    count = upsert("ppra_contracts", rows, conflict_col="ocid")
    log.info("Upserted %d PPRA contracts from %s", count, path.name)
    return count


def ingest_all_xlsx(paths: list[Path]) -> int:
    """Parse and store all downloaded XLSX files. Returns total rows added."""
    total = 0
    for p in paths:
        if p and p.exists() and p.suffix == ".xlsx":
            total += parse_and_store_xlsx(p)
    return total


# ════════════════════════════════════════════════════════════════════════════════
#  APPROACH 2 — Live PPIP portal scraping
# ════════════════════════════════════════════════════════════════════════════════

def _build_ppip_api_url(page: int = 1, per_page: int = 100,
                         status: str = "all") -> str:
    """
    Construct a PPIP tenders-listing URL.
    The portal exposes a paginated table; we request JSON-like output
    via the 'Download Excel' endpoint which is a direct data export.
    """
    return (
        f"https://tenders.go.ke/tenders"
        f"?page={page}&per_page={per_page}"
        f"&status={status}"
    )


def scrape_live_tenders(max_pages: int = 10) -> int:
    """
    Attempt to scrape live tenders from tenders.go.ke.
    The portal is JS-rendered; this function uses requests + BeautifulSoup
    (works when the server returns server-side rendered HTML).
    Falls back gracefully with a warning if it cannot parse the page.

    Returns number of new tender rows inserted.
    """
    session = get_session()
    total_new = 0

    for page in range(1, max_pages + 1):
        url = _build_ppip_api_url(page=page)
        resp = safe_get(url, session=session)
        if resp is None:
            log.warning("Page %d returned no response — stopping pagination.", page)
            break

        html = resp.text

        # Try to parse the tenders table
        tenders = _parse_ppip_table(html, base_url="https://tenders.go.ke")
        if not tenders:
            # Portal likely returned an empty JS shell or last page reached
            log.info("No tenders parsed on page %d — pagination complete.", page)
            break

        new = insert_if_new("ppra_tenders", tenders)
        total_new += new
        log.info("PPIP page %d — %d tenders parsed, %d new", page, len(tenders), new)

    return total_new


def _parse_ppip_table(html: str, base_url: str) -> list[dict]:
    """
    Parse the tenders listing table from PPIP HTML.
    Returns a list of dicts matching the ppra_tenders schema.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    rows = []

    # The portal renders a <table> with class variations; look broadly
    tables = soup.find_all("table")
    if not tables:
        # JS-only page — can't parse without browser
        return []

    for table in tables:
        headers = [th.get_text(strip=True).lower().replace(" ", "_")
                   for th in table.find_all("th")]
        if not any(kw in headers for kw in
                   ["tender", "title", "entity", "procuring"]):
            continue

        for tr in table.find_all("tr")[1:]:   # skip header row
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) < 3:
                continue

            # Map columns by position (common PPIP layout):
            # 0: tender_id, 1: title, 2: entity, 3: category,
            # 4: method, 5: status, 6: closing_date
            rec = {
                "tender_id":          cells[0] if len(cells) > 0 else None,
                "title":              cells[1] if len(cells) > 1 else None,
                "procuring_entity":   cells[2] if len(cells) > 2 else None,
                "category":           cells[3] if len(cells) > 3 else None,
                "procurement_method": cells[4] if len(cells) > 4 else None,
                "status":             cells[5] if len(cells) > 5 else None,
                "closing_date":       cells[6] if len(cells) > 6 else None,
                "published_date":     cells[7] if len(cells) > 7 else None,
                "url":                base_url,
                "scraped_at":         datetime.utcnow().isoformat(),
            }
            # Require at minimum a tender_id to avoid junk rows
            if rec["tender_id"]:
                rows.append(rec)

    return rows

def _build_ppip_contracts_url(page: int = 1, per_page: int = 100) -> str:
    """Construct a PPIP contracts-listing URL."""
    return f"{PPIP_CONTRACTS_URL}?page={page}&per_page={per_page}"

def scrape_live_contracts(max_pages: int = 5) -> int:
    """
    Attempt to scrape live contracts from tenders.go.ke/contracts.
    Uses requests + BeautifulSoup to parse the server-side table.
    Returns number of new contract rows inserted/upserted.
    """
    session = get_session()
    total_upserted = 0
    
    for page in range(1, max_pages + 1):
        url = _build_ppip_contracts_url(page=page)
        resp = safe_get(url, session=session)
        if resp is None:
            log.warning("Contracts page %d returned no response.", page)
            break
            
        html = resp.text
        contracts = _parse_ppip_contracts_table(html, base_url="https://tenders.go.ke")
        if not contracts:
            log.info("No contracts parsed on page %d — pagination complete.", page)
            break
            
        # Insert or update contracts
        # the conflict col is 'ocid'
        count = upsert("ppra_contracts", contracts, conflict_col="ocid")
        total_upserted += count
        log.info("PPIP contracts page %d — %d parsed, %d upserted", page, len(contracts), count)
        
    return total_upserted

def _parse_ppip_contracts_table(html: str, base_url: str) -> list[dict]:
    """Parse the contracts listing table from PPIP HTML."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    rows = []
    
    tables = soup.find_all("table")
    if not tables:
        return []
        
    for table in tables:
        headers = [th.get_text(strip=True).lower().replace(" ", "_")
                   for th in table.find_all("th")]
        # Ensure it's a contracts table
        if not any(kw in headers for kw in ["supplier", "award", "amount"]):
            continue
            
        for tr in table.find_all("tr")[1:]:   # skip header row
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) < 6:
                continue
                
            # Common layout: 
            # 0: Reference No, 1: Tender Name, 2: Procuring Entity, 
            # 3: Award Date, 4: Supplier Name, 5: Contract Amount
            
            ref_no = cells[0] if len(cells) > 0 else ""
            if not ref_no:
                continue
                
            title = cells[1] if len(cells) > 1 else None
            entity = cells[2] if len(cells) > 2 else None
            award_date = cells[3] if len(cells) > 3 else None
            supplier = cells[4] if len(cells) > 4 else None
            
            # Parse amount
            amount_str = cells[5] if len(cells) > 5 else "0"
            amount_str = amount_str.replace("KES", "").replace(",", "").strip()
            try:
                award_value = float(amount_str)
            except ValueError:
                award_value = 0.0
                
            # Create a synthetic OCID for the database if one doesn't exist
            # OCP format: ocds-5whusi-{ref_no}
            safe_ref = ref_no.replace("/", "-").replace(" ", "-")
            ocid = f"ocds-5whusi-live-{safe_ref}"
            
            # Infer fiscal year
            fiscal_year = None
            if award_date:
                try:
                    # PPIP format is often DD-MM-YYYY or similar, let's try standardizing
                    # We'll just use a simple string fallback
                    from dateutil.parser import parse
                    dt = parse(award_date, fuzzy=True)
                    fmt_date = dt.strftime("%Y-%m-%d")
                    yr = dt.year
                    mo = dt.month
                    fiscal_year = f"{yr}/{str(yr + 1)[-2:]}" if mo >= 7 else f"{yr - 1}/{str(yr)[-2:]}"
                    award_date = fmt_date
                except Exception:
                    pass
            
            rec = {
                "ocid": ocid,
                "tender_id": ref_no,
                "tender_title": title,
                "procuring_entity": entity,
                "award_date": award_date,
                "supplier_name": supplier,
                "award_value": award_value,
                "award_currency": "KES",
                "fiscal_year": fiscal_year,
                "scraped_at": datetime.utcnow().isoformat()
            }
            rows.append(rec)
            
    return rows
def scrape_procuring_entities() -> list[dict]:
    """
    Scrape the list of registered procuring entities from PPIP.
    Returns list of entity dicts.
    """
    resp = safe_get(PPIP_ENTITIES_URL)
    if resp is None:
        return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "lxml")
    entities = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/ProcuringEntities/" in href or "entity" in href.lower():
            entities.append({
                "name": a.get_text(strip=True),
                "url": href,
            })
    return entities


# ════════════════════════════════════════════════════════════════════════════════
#  Main entry point
# ════════════════════════════════════════════════════════════════════════════════

def run(
    years: Optional[list[int]] = None,
    fmt: str = "xlsx",
    scrape_live: bool = True,
    live_max_pages: int = 5,
) -> dict:
    """
    Full PPRA scrape run.

    Args:
        years:           List of years to download. Defaults to all (2018–2026).
        fmt:             File format for bulk download ('xlsx' or 'csv.tar.gz').
        scrape_live:     Whether to also scrape the live PPIP portal.
        live_max_pages:  Max pages to scrape from the live portal.

    Returns:
        Summary dict with counts.
    """
    init_db()
    run_id = log_start("ppra")
    summary = {"files": 0, "contracts_upserted": 0, "live_tenders": 0}

    try:
        log.info("═══ PPRA: Bulk OCDS Download (%s) ═══", fmt)
        paths = download_all_ocds(years=years, fmt=fmt)
        summary["files"] = len(paths)

        # Register all downloaded files
        for p in paths:
            if p:
                register_file("ppra", ppra_download_url(
                    int(re.search(r'_(\d{4})\.', p.name).group(1)), fmt
                ), p)

        # Parse and ingest Excel files
        if fmt == "xlsx":
            summary["contracts_upserted"] = ingest_all_xlsx(paths)

        # Live portal scraping
        if scrape_live:
            log.info("═══ PPRA: Live PPIP Portal Scrape ═══")
            summary["live_tenders"] = scrape_live_tenders(live_max_pages)

        log_finish(run_id, status="ok",
                   rows_added=summary["contracts_upserted"] + summary["live_tenders"],
                   files_saved=summary["files"])

    except Exception as exc:
        log.error("PPRA run failed: %s", exc, exc_info=True)
        log_finish(run_id, status="failed", notes=str(exc))

    log.info("PPRA complete — %s", summary)
    return summary


if __name__ == "__main__":
    result = run(years=[2024, 2025], scrape_live=True)
    print(result)

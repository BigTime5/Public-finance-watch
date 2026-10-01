"""
scrapers/treasury.py — Kenya National Treasury

Scrapes and downloads:
  • Budget Policy Statements (BPS)
  • Budget Review and Outlook Papers (BROP)
  • Budget Summaries of Revenue & Expenditure
  • Supplementary Estimates
  • Any other PDF/Excel on treasury.go.ke budget pages

Site:    https://www.treasury.go.ke  (WordPress + Apache file serving)
Storage: treasury_docs table
Files:   data/raw/treasury/
"""

import re
import time
from pathlib import Path
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config import (
    RAW_DIR,
    TREASURY_BASE,
    TREASURY_BUDGET_PAGE,
    TREASURY_BPS_PAGE,
    TREASURY_BROP_PAGE,
    TREASURY_KNOWN_DOCS,
)
from storage.database import (
    init_db, insert_if_new, log_start, log_finish, register_file,
)
from utils.helpers import safe_get, download_file, extract_file_links, slugify
from utils.logger import get_logger

log = get_logger("scraper.treasury")
RAW_TREASURY = RAW_DIR / "treasury"


# ─── Budget pages to crawl ────────────────────────────────────────────────────
_TREASURY_PAGES = {
    "budget_summary":          TREASURY_BUDGET_PAGE,
    "budget_policy_statement": TREASURY_BPS_PAGE,
    "budget_review_outlook":   TREASURY_BROP_PAGE,
    "supplementary_estimates": f"{TREASURY_BASE}/supplementary-estimates",
    "medium_term_budget":      f"{TREASURY_BASE}/medium-term-budget",
    "county_allocation":       f"{TREASURY_BASE}/county-allocation-revenue-fund",
    "public_debt":             f"{TREASURY_BASE}/public-debt-management-reports",
    "fiscal_policy":           f"{TREASURY_BASE}/fiscal-policy",
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _infer_doc_type(title: str, url: str) -> str:
    tl = (title + url).lower()
    if "budget policy statement" in tl or "bps" in tl:
        return "BPS"
    if "budget review" in tl or "brop" in tl or "outlook" in tl:
        return "BROP"
    if "budget summary" in tl:
        return "Budget Summary"
    if "supplementary" in tl:
        return "Supplementary Estimates"
    if "appropriation" in tl:
        return "Appropriation Bill"
    if "medium term" in tl or "mtef" in tl:
        return "MTEF"
    if "public debt" in tl:
        return "Public Debt Report"
    if "county" in tl and ("allocation" in tl or "transfer" in tl):
        return "County Allocation"
    if "annual" in tl and "estimate" in tl:
        return "Annual Estimates"
    return "Other"


def _parse_fy(text: str, url: str) -> Optional[str]:
    """Extract fiscal year from text or URL."""
    combined = text + " " + url
    m = re.search(r'(20\d\d)[/\-_](20\d\d|\d\d)', combined)
    if m:
        start = m.group(1)
        end = m.group(2)
        if len(end) == 4:
            end = end[2:]
        return f"{start}/{end}"
    # Try single-year from URL path
    m2 = re.search(r'/(20\d\d)/', url)
    if m2:
        yr = int(m2.group(1))
        return f"{yr}/{str(yr + 1)[2:]}"
    return None


def _make_record(title: str, url: str) -> dict:
    return {
        "title":       title,
        "doc_type":    _infer_doc_type(title, url),
        "fiscal_year": _parse_fy(title, url),
        "file_url":    url,
        "scraped_at":  datetime.utcnow().isoformat(),
    }


# ─── Scraping functions ───────────────────────────────────────────────────────

def scrape_treasury_page(page_url: str) -> list[dict]:
    """
    Scrape a single Treasury page for downloadable documents.
    Returns list of treasury_docs-compatible dicts.
    """
    resp = safe_get(page_url)
    if resp is None:
        log.warning("Treasury page unreachable: %s", page_url)
        return []

    links = extract_file_links(
        resp.text, base_url=page_url,
        extensions=(".pdf", ".xlsx", ".xls", ".docx")
    )
    records = []
    for lnk in links:
        text = lnk["text"] or Path(lnk["url"]).stem.replace("-", " ")
        records.append(_make_record(text, lnk["url"]))

    log.info("Treasury %s — %d documents found",
             page_url[-60:], len(records))
    return records


def scrape_all_treasury_pages() -> list[dict]:
    """Scrape all known Treasury budget pages."""
    all_records: list[dict] = []
    for page_name, url in _TREASURY_PAGES.items():
        log.info("Scraping Treasury page: %s", page_name)
        records = scrape_treasury_page(url)
        all_records.extend(records)
        time.sleep(1.2)
    return all_records


def scrape_treasury_sitemap() -> list[dict]:
    """
    Try to discover additional pages via the Treasury sitemap or search.
    Crawls treasury.go.ke/wp-sitemap.xml if available.
    """
    records: list[dict] = []
    sitemap_url = f"{TREASURY_BASE}/wp-sitemap.xml"
    resp = safe_get(sitemap_url)
    if resp is None:
        return records

    soup = BeautifulSoup(resp.text, "xml")
    locs = [loc.get_text(strip=True) for loc in soup.find_all("loc")]
    budget_pages = [
        l for l in locs
        if any(kw in l.lower() for kw in
               ["budget", "bps", "brop", "estimates", "supplement",
                "finance", "revenue", "fiscal", "debt"])
    ][:30]  # cap at 30 to avoid excessive crawl

    for page_url in budget_pages:
        records.extend(scrape_treasury_page(page_url))
        time.sleep(0.8)

    return records


def seed_known_docs() -> list[dict]:
    """Return records for the confirmed known Treasury document URLs."""
    records = []
    for title, url in TREASURY_KNOWN_DOCS.items():
        records.append(_make_record(title, url))
    return records


# ─── Download engine ──────────────────────────────────────────────────────────

def download_treasury_doc(record: dict) -> Optional[Path]:
    url = record.get("file_url")
    if not url:
        return None

    fy = (record.get("fiscal_year") or "").replace("/", "-")
    dtype = slugify(record.get("doc_type") or "doc")
    ext = Path(url.split("?")[0]).suffix or ".pdf"
    title_slug = slugify(record.get("title") or "doc")[:40]
    filename = f"treasury_{fy}_{dtype}_{title_slug}{ext}"

    return download_file(url, RAW_TREASURY, filename, skip_if_exists=True)


# ─── Main entry point ─────────────────────────────────────────────────────────

def run(
    download_files: bool = True,
    crawl_sitemap: bool = False,
) -> dict:
    """
    Full Treasury scrape run.

    Args:
        download_files:  Whether to download the actual documents.
        crawl_sitemap:   Whether to crawl the WordPress sitemap for extra pages.

    Returns:
        Summary dict with counts.
    """
    init_db()
    run_id = log_start("treasury")
    summary = {"records_indexed": 0, "files_downloaded": 0}

    try:
        all_records: list[dict] = []

        # ── Step 1: Seed known confirmed documents ────────────────────────────
        log.info("═══ Treasury: Seeding known documents ═══")
        all_records.extend(seed_known_docs())

        # ── Step 2: Scrape all known pages ────────────────────────────────────
        log.info("═══ Treasury: Scraping budget pages ═══")
        all_records.extend(scrape_all_treasury_pages())

        # ── Step 3 (optional): Sitemap crawl ─────────────────────────────────
        if crawl_sitemap:
            log.info("═══ Treasury: Sitemap crawl ═══")
            all_records.extend(scrape_treasury_sitemap())

        # Deduplicate by URL
        seen: set[str] = set()
        unique: list[dict] = []
        for r in all_records:
            url = r.get("file_url", "")
            if url and url not in seen:
                seen.add(url)
                unique.append(r)

        # ── Step 4: Insert into DB ────────────────────────────────────────────
        new = insert_if_new("treasury_docs", unique)
        summary["records_indexed"] = new
        log.info("Treasury: %d new documents indexed", new)

        # ── Step 5: Download files ────────────────────────────────────────────
        if download_files:
            log.info("═══ Treasury: Downloading documents ═══")
            from storage.database import get_conn, DB_PATH
            with get_conn(DB_PATH) as conn:
                rows = conn.execute(
                    "SELECT * FROM treasury_docs WHERE local_path IS NULL"
                ).fetchall()

            log.info("Downloading %d Treasury documents …", len(rows))
            downloaded = 0
            for row in rows:
                rec = dict(row)
                p = download_treasury_doc(rec)
                if p:
                    with get_conn(DB_PATH) as conn:
                        conn.execute(
                            "UPDATE treasury_docs SET local_path = ? WHERE file_url = ?",
                            (str(p), rec["file_url"]),
                        )
                    register_file("treasury", rec["file_url"], p)
                    downloaded += 1

            summary["files_downloaded"] = downloaded
            log.info("Downloaded %d Treasury documents", downloaded)

        log_finish(run_id, status="ok",
                   rows_added=summary["records_indexed"],
                   files_saved=summary["files_downloaded"])

    except Exception as exc:
        log.error("Treasury run failed: %s", exc, exc_info=True)
        log_finish(run_id, status="failed", notes=str(exc))

    log.info("Treasury complete — %s", summary)
    return summary


if __name__ == "__main__":
    result = run(download_files=True, crawl_sitemap=False)
    print(result)

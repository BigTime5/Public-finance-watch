"""
scrapers/cob.py — Office of the Controller of Budget (COB) Kenya

Scrapes and downloads:
  • Annual County Government Budget Implementation Review Reports (CBIRR)
  • Quarterly CBIRR (Q1/Q2/Q3/Q4)
  • Annual National Government BIRR (NG-BIRR)
  • Financial Reports index page

Site:    https://cob.go.ke  (WordPress — some pages return 415 without right headers)
Storage: cob_reports table
Files:   data/raw/cob/

Note: cob.go.ke can be slow and occasionally returns 415 errors.
      We use a multi-strategy approach: try the reports page, then the
      WordPress post archive (year-based URLs), then direct known PDFs.
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
    COB_BASE,
    COB_REPORTS_PAGE,
    COB_COUNTY_PAGE,
    COB_NATIONAL_PAGE,
)
from storage.database import (
    init_db, insert_if_new, log_start, log_finish, register_file,
)
from utils.helpers import safe_get, download_file, slugify
from utils.logger import get_logger

log = get_logger("scraper.cob")
RAW_COB = RAW_DIR / "cob"


# ─── Known direct URLs (WordPress CDN pattern discovered from searches) ───────
# These are used as seeds and also as fallback if the main pages are unreachable.

_KNOWN_COB_REPORTS = [
    # Annual County BIRR
    {
        "title": "Annual County BIRR FY2024/25",
        "fiscal_year": "2024/25", "quarter": "Annual",
        "government_level": "county",
        "file_url": "https://cob.go.ke/wp-content/uploads/2025/09/"
                    "Annual-County-BIRR-FY2024-25.pdf",
    },
    {
        "title": "Annual County BIRR FY2023/24",
        "fiscal_year": "2023/24", "quarter": "Annual",
        "government_level": "county",
        "file_url": "https://cob.go.ke/wp-content/uploads/2024/09/"
                    "Annual-County-BIRR-FY2023-24.pdf",
    },
    {
        "title": "Annual County BIRR FY2022/23",
        "fiscal_year": "2022/23", "quarter": "Annual",
        "government_level": "county",
        "file_url": "https://cob.go.ke/wp-content/uploads/2023/09/"
                    "Annual-County-BIRR-FY2022-23.pdf",
    },
    # First-half and quarterly
    {
        "title": "County BIRR First Half FY2024/25",
        "fiscal_year": "2024/25", "quarter": "Half-Year",
        "government_level": "county",
        "file_url": "https://kiambu.go.ke/wp-content/uploads/2025/03/"
                    "CBIRR-2024-25-24.02.2025-DR-25225-b-17-1.pdf",
    },
    # Annual National Government BIRR
    {
        "title": "Annual National Govt BIRR FY2024/25",
        "fiscal_year": "2024/25", "quarter": "Annual",
        "government_level": "national",
        "file_url": "https://cob.go.ke/wp-content/uploads/2025/09/"
                    "Annual-NG-BIRR-FY2024-25.pdf",
    },
]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _parse_quarter(text: str) -> str:
    """Infer quarter label from text."""
    tl = text.lower()
    if "first quarter" in tl or "q1" in tl:
        return "Q1"
    if "second quarter" in tl or "q2" in tl:
        return "Q2"
    if "third quarter" in tl or "q3" in tl:
        return "Q3"
    if "fourth quarter" in tl or "q4" in tl:
        return "Q4"
    if "half" in tl:
        return "Half-Year"
    if "annual" in tl:
        return "Annual"
    return "Unknown"


def _parse_fy(text: str) -> Optional[str]:
    """Extract fiscal year from text, normalised to 'YYYY/YY'."""
    m = re.search(r'(20\d\d)[/\-](20\d\d|\d\d)', text)
    if m:
        start = m.group(1)
        end = m.group(2)
        if len(end) == 4:
            end = end[2:]
        return f"{start}/{end}"
    return None


def _parse_govt_level(text: str, url: str) -> str:
    tl = (text + url).lower()
    if "national" in tl:
        return "national"
    return "county"


def _make_record(title: str, url: str,
                 fiscal_year: Optional[str] = None) -> dict:
    return {
        "title":            title,
        "fiscal_year":      fiscal_year or _parse_fy(title),
        "quarter":          _parse_quarter(title),
        "government_level": _parse_govt_level(title, url),
        "file_url":         url,
        "scraped_at":       datetime.utcnow().isoformat(),
    }


# ─── Page scrapers ────────────────────────────────────────────────────────────

def _scrape_cob_page_safe(page_url: str) -> list[dict]:
    """
    Attempt to scrape a COB page.
    Returns list of cob_reports-compatible dicts.

    cob.go.ke sometimes returns 415 (Unsupported Media Type) when the
    Accept header includes application/xml.  We send a minimal header set.
    """
    import requests
    special_headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) "
            "Gecko/20100101 Firefox/124.0"
        ),
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    records: list[dict] = []
    try:
        resp = requests.get(page_url, headers=special_headers, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        log.warning("COB page fetch failed (%s): %s", page_url[-50:], exc)
        return records

    soup = BeautifulSoup(resp.text, "lxml")

    # ── Strategy A: <a href="*.pdf"> links ────────────────────────────────────
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not (".pdf" in href.lower() or ".xlsx" in href.lower()):
            continue
        abs_url = urljoin(COB_BASE, href)
        text = a.get_text(strip=True)
        if not text:
            text = Path(href).stem.replace("-", " ").replace("_", " ")
        records.append(_make_record(text, abs_url))

    # ── Strategy B: WordPress download buttons ────────────────────────────────
    for btn in soup.find_all(class_=re.compile(r'download|btn|button', re.I)):
        for a in btn.find_all("a", href=True):
            href = a["href"].strip()
            if ".pdf" in href.lower():
                abs_url = urljoin(COB_BASE, href)
                text = a.get_text(strip=True) or btn.get_text(strip=True)
                if not any(r["file_url"] == abs_url for r in records):
                    records.append(_make_record(text, abs_url))

    # Deduplicate
    seen: set[str] = set()
    unique = []
    for r in records:
        if r["file_url"] not in seen:
            seen.add(r["file_url"])
            unique.append(r)

    log.info("COB %s — %d reports found", page_url[-60:], len(unique))
    return unique


def scrape_cob_reports_page() -> list[dict]:
    """Scrape the main financial reports page."""
    return _scrape_cob_page_safe(COB_REPORTS_PAGE)


def scrape_cob_county_page() -> list[dict]:
    """Scrape consolidated county reports page."""
    return _scrape_cob_page_safe(COB_COUNTY_PAGE)


def scrape_cob_national_page() -> list[dict]:
    """Scrape national government BIRR page."""
    return _scrape_cob_page_safe(COB_NATIONAL_PAGE)


def scrape_cob_wordpress_archive(years: range = range(2018, 2027)) -> list[dict]:
    """
    Crawl WordPress year-archive pages (cob.go.ke/YYYY/) to discover
    report posts and extract their PDF download links.
    """
    all_records: list[dict] = []
    for yr in years:
        url = f"{COB_BASE}/{yr}/"
        records = _scrape_cob_page_safe(url)
        if records:
            all_records.extend(records)
        # Also try individual post pages linked from the archive
        try:
            import requests
            resp = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
                timeout=20,
            )
            if resp.ok:
                soup = BeautifulSoup(resp.text, "lxml")
                post_links = [
                    urljoin(COB_BASE, a["href"])
                    for a in soup.find_all("a", href=True)
                    if f"{COB_BASE}/{yr}/" in urljoin(COB_BASE, a["href"])
                    and "download" in a["href"].lower()
                ]
                for pl in post_links[:10]:
                    sub = _scrape_cob_page_safe(pl)
                    all_records.extend(sub)
                    time.sleep(0.8)
        except Exception:
            pass
        time.sleep(1.0)

    return all_records


def seed_known_reports() -> list[dict]:
    """Return records for hardcoded known COB report URLs."""
    records = []
    for r in _KNOWN_COB_REPORTS:
        record = {**r, "scraped_at": datetime.utcnow().isoformat()}
        records.append(record)
    return records


# ─── Download engine ──────────────────────────────────────────────────────────

def download_cob_report(record: dict) -> Optional[Path]:
    url = record.get("file_url")
    if not url:
        return None

    fy = (record.get("fiscal_year") or "").replace("/", "-")
    qt = slugify(record.get("quarter") or "report")
    level = slugify(record.get("government_level") or "govt")
    filename = f"cob_{level}_{fy}_{qt}.pdf"

    return download_file(url, RAW_COB, filename, skip_if_exists=True)


# ─── Main entry point ─────────────────────────────────────────────────────────

def run(
    download_files: bool = True,
    scrape_archive: bool = True,
) -> dict:
    """
    Full COB scrape run.

    Args:
        download_files:  Whether to download the actual PDF reports.
        scrape_archive:  Whether to crawl the WordPress year archives.

    Returns:
        Summary dict with counts.
    """
    init_db()
    run_id = log_start("cob")
    summary = {"records_indexed": 0, "files_downloaded": 0}

    try:
        all_records: list[dict] = []

        # ── Step 1: Seed known reports ────────────────────────────────────────
        log.info("═══ COB: Seeding known reports ═══")
        all_records.extend(seed_known_reports())

        # ── Step 2: Scrape main pages ─────────────────────────────────────────
        log.info("═══ COB: Scraping main pages ═══")
        all_records.extend(scrape_cob_reports_page())
        all_records.extend(scrape_cob_county_page())
        all_records.extend(scrape_cob_national_page())

        # ── Step 3: WordPress archive crawl ───────────────────────────────────
        if scrape_archive:
            log.info("═══ COB: WordPress year archive crawl ═══")
            archive_records = scrape_cob_wordpress_archive(range(2019, 2027))
            all_records.extend(archive_records)

        # Deduplicate
        seen: set[str] = set()
        unique: list[dict] = []
        for r in all_records:
            url = r.get("file_url", "")
            if url and url not in seen:
                seen.add(url)
                unique.append(r)

        # ── Step 4: Insert into DB ────────────────────────────────────────────
        new = insert_if_new("cob_reports", unique)
        summary["records_indexed"] = new
        log.info("COB: %d new reports indexed", new)

        # ── Step 5: Download PDFs ─────────────────────────────────────────────
        if download_files:
            log.info("═══ COB: Downloading PDF reports ═══")
            from storage.database import get_conn, DB_PATH
            with get_conn(DB_PATH) as conn:
                rows = conn.execute(
                    "SELECT * FROM cob_reports WHERE local_path IS NULL"
                ).fetchall()

            log.info("Downloading %d COB reports …", len(rows))
            downloaded = 0
            for row in rows:
                rec = dict(row)
                p = download_cob_report(rec)
                if p:
                    with get_conn(DB_PATH) as conn:
                        conn.execute(
                            "UPDATE cob_reports SET local_path = ? WHERE file_url = ?",
                            (str(p), rec["file_url"]),
                        )
                    register_file("cob", rec["file_url"], p)
                    downloaded += 1

            summary["files_downloaded"] = downloaded
            log.info("Downloaded %d COB reports", downloaded)

        log_finish(run_id, status="ok",
                   rows_added=summary["records_indexed"],
                   files_saved=summary["files_downloaded"])

    except Exception as exc:
        log.error("COB run failed: %s", exc, exc_info=True)
        log_finish(run_id, status="failed", notes=str(exc))

    log.info("COB complete — %s", summary)
    return summary


if __name__ == "__main__":
    result = run(download_files=True, scrape_archive=True)
    print(result)

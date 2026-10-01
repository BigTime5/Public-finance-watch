"""
scrapers/oag.py — Office of the Auditor-General Kenya

Scrapes and downloads:
  • County Executives & Assemblies audit reports (FY 2016/17 → 2024/25)
  • National Government audit reports
  • Specialised audit reports
  • Hospital, Water Companies, County Funds, Revenue Funds reports

Site:    https://www.oagkenya.go.ke  (WordPress — static HTML tables)
Storage: oag_reports table
Files:   data/raw/oag/
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
    OAG_BASE,
    OAG_COUNTY_EXEC_YEARS,
    OAG_REPORT_SECTIONS,
    OAG_KNOWN_PDFS,
)
from storage.database import (
    init_db, insert_if_new, log_start, log_finish, register_file,
)
from utils.helpers import safe_get, download_file, slugify
from utils.logger import get_logger

log = get_logger("scraper.oag")
RAW_OAG = RAW_DIR / "oag"


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _parse_fy_from_text(text: str) -> Optional[str]:
    """
    Extract a fiscal-year string like '2023/24' or '2023-2024' from text.
    Normalises to 'YYYY/YY' format.
    """
    # Pattern: 2023/24 or 2023/2024
    m = re.search(r'(20\d\d)[/\-](20\d\d|\d\d)', text)
    if m:
        start = m.group(1)
        end = m.group(2)
        if len(end) == 2:
            end = start[:2] + end
        return f"{start}/{end[2:]}"
    return None


def _infer_report_type(title: str, url: str, section: str = "") -> str:
    tl = (title + url + section).lower()
    if "assembly" in tl:
        return "county_assembly"
    if "executive" in tl or "county govern" in tl:
        return "county_executive"
    if "national government" in tl or "national govt" in tl:
        return "national"
    if "hospital" in tl or "level 4" in tl or "level 5" in tl:
        return "hospital"
    if "water" in tl:
        return "water_company"
    if "fund" in tl:
        return "county_fund"
    if "revenue" in tl:
        return "county_revenue"
    if "special" in tl:
        return "special_audit"
    if "perform" in tl:
        return "performance_audit"
    if "summary" in tl or "popular" in tl:
        return "summary"
    return "financial_audit"


# ─── Per-page scrapers ────────────────────────────────────────────────────────

def scrape_oag_page(
    page_url: str,
    fiscal_year: Optional[str] = None,
    report_section: str = "",
) -> list[dict]:
    """
    Fetch an OAG page and extract every PDF download link.

    The OAG WordPress site structures reports as:
      <table>
        <tr>
          <td>Report Title</td>
          <td><a href="...pdf">Download</a></td>
        </tr>
        ...
      </table>

    Returns list of oag_reports-compatible dicts.
    """
    resp = safe_get(page_url)
    if resp is None:
        log.warning("OAG page unreachable: %s", page_url)
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    records = []

    # ── Strategy A: parse <table> rows ────────────────────────────────────────
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue

            # Look for any cell containing a PDF link
            pdf_links = []
            for cell in cells:
                for a in cell.find_all("a", href=True):
                    href = a["href"].strip()
                    if href.lower().endswith(".pdf") or ".pdf" in href.lower():
                        pdf_links.append({
                            "url": urljoin(OAG_BASE, href),
                            "link_text": a.get_text(strip=True),
                        })

            if not pdf_links:
                continue

            # Extract title from the first non-link cell
            title_cell = cells[0].get_text(strip=True)
            title = title_cell or pdf_links[0]["link_text"]

            # Try to infer FY from the title if not provided
            fy = fiscal_year or _parse_fy_from_text(title)

            for pdf in pdf_links:
                records.append({
                    "title":       title or pdf["link_text"],
                    "fiscal_year": fy,
                    "report_type": _infer_report_type(
                        title, pdf["url"], report_section
                    ),
                    "file_url":    pdf["url"],
                    "scraped_at":  datetime.utcnow().isoformat(),
                })

    # ── Strategy B: pick up any stray PDF links outside tables ────────────────
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not (href.lower().endswith(".pdf") or ".pdf" in href.lower()):
            continue
        abs_url = urljoin(OAG_BASE, href)
        if any(r["file_url"] == abs_url for r in records):
            continue  # already captured

        text = a.get_text(strip=True)
        fy = fiscal_year or _parse_fy_from_text(text)
        records.append({
            "title":       text or Path(href).stem,
            "fiscal_year": fy,
            "report_type": _infer_report_type(text, abs_url, report_section),
            "file_url":    abs_url,
            "scraped_at":  datetime.utcnow().isoformat(),
        })

    # Deduplicate within this page by URL
    seen: set[str] = set()
    unique = []
    for r in records:
        if r["file_url"] not in seen:
            seen.add(r["file_url"])
            unique.append(r)

    log.info("OAG %s — %d reports found", page_url[-60:], len(unique))
    return unique


def scrape_all_county_years() -> list[dict]:
    """Scrape all FY pages for county executives & assemblies."""
    all_records: list[dict] = []
    for fy, url in OAG_COUNTY_EXEC_YEARS.items():
        records = scrape_oag_page(url, fiscal_year=fy,
                                  report_section="county_executive")
        all_records.extend(records)
        time.sleep(1.2)
    return all_records


def scrape_all_sections() -> list[dict]:
    """Scrape all other OAG report sections (national, hospitals, water, etc.)."""
    all_records: list[dict] = []
    for section_name, url in OAG_REPORT_SECTIONS.items():
        records = scrape_oag_page(url, report_section=section_name)

        # The section pages often link to per-FY sub-pages — follow them
        resp = safe_get(url)
        if resp:
            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                abs_href = urljoin(OAG_BASE, href)
                if (OAG_BASE in abs_href
                        and re.search(r'20\d\d', abs_href)
                        and abs_href != url):
                    sub_records = scrape_oag_page(
                        abs_href,
                        fiscal_year=_parse_fy_from_text(abs_href),
                        report_section=section_name,
                    )
                    records.extend(sub_records)
                    time.sleep(1.0)

        all_records.extend(records)
        time.sleep(1.2)

    return all_records


def seed_known_pdfs() -> list[dict]:
    """Return records for the hardcoded known PDF URLs (guaranteed to exist)."""
    records = []
    for title, url in OAG_KNOWN_PDFS.items():
        records.append({
            "title":       title,
            "fiscal_year": _parse_fy_from_text(title),
            "report_type": _infer_report_type(title, url),
            "file_url":    url,
            "scraped_at":  datetime.utcnow().isoformat(),
        })
    return records


# ─── Download engine ──────────────────────────────────────────────────────────

def download_oag_report(record: dict) -> Optional[Path]:
    """
    Download the PDF referenced in an oag_reports record.
    Returns local Path on success.
    """
    url = record.get("file_url")
    if not url:
        return None

    fy = (record.get("fiscal_year") or "").replace("/", "-")
    rtype = slugify(record.get("report_type") or "report")
    title_slug = slugify(record.get("title") or "doc")[:40]
    filename = f"oag_{fy}_{rtype}_{title_slug}.pdf"

    return download_file(url, RAW_OAG, filename, skip_if_exists=True)


# ─── Main entry point ─────────────────────────────────────────────────────────

def run(
    download_files: bool = True,
    scrape_all_fys: bool = True,
) -> dict:
    """
    Full OAG scrape run.

    Args:
        download_files:  Whether to download the actual PDF reports.
        scrape_all_fys:  Whether to scrape all FY pages (or just seed known).

    Returns:
        Summary dict with counts.
    """
    init_db()
    run_id = log_start("oag")
    summary = {"records_indexed": 0, "files_downloaded": 0}

    try:
        all_records: list[dict] = []

        # ── Step 1: Seed guaranteed known PDFs ───────────────────────────────
        log.info("═══ OAG: Seeding known reports ═══")
        known = seed_known_pdfs()
        all_records.extend(known)

        # ── Step 2: Scrape all county year pages ─────────────────────────────
        if scrape_all_fys:
            log.info("═══ OAG: Scraping all county FY pages ═══")
            county_records = scrape_all_county_years()
            all_records.extend(county_records)

            log.info("═══ OAG: Scraping other sections ═══")
            section_records = scrape_all_sections()
            all_records.extend(section_records)

        # Deduplicate across all sources by URL
        seen: set[str] = set()
        unique: list[dict] = []
        for r in all_records:
            url = r.get("file_url", "")
            if url and url not in seen:
                seen.add(url)
                unique.append(r)

        # ── Step 3: Insert into DB ────────────────────────────────────────────
        new = insert_if_new("oag_reports", unique)
        summary["records_indexed"] = new
        log.info("OAG: %d new reports indexed", new)

        # ── Step 4: Download PDFs ─────────────────────────────────────────────
        if download_files:
            log.info("═══ OAG: Downloading PDF reports ═══")
            from storage.database import get_conn, DB_PATH
            with get_conn(DB_PATH) as conn:
                rows = conn.execute(
                    "SELECT * FROM oag_reports WHERE local_path IS NULL"
                ).fetchall()

            log.info("Downloading %d OAG PDFs …", len(rows))
            downloaded = 0
            for row in rows:
                rec = dict(row)
                p = download_oag_report(rec)
                if p:
                    with get_conn(DB_PATH) as conn:
                        conn.execute(
                            "UPDATE oag_reports SET local_path = ? WHERE file_url = ?",
                            (str(p), rec["file_url"]),
                        )
                    register_file("oag", rec["file_url"], p)
                    downloaded += 1

            summary["files_downloaded"] = downloaded
            log.info("Downloaded %d OAG reports", downloaded)

        log_finish(run_id, status="ok",
                   rows_added=summary["records_indexed"],
                   files_saved=summary["files_downloaded"])

    except Exception as exc:
        log.error("OAG run failed: %s", exc, exc_info=True)
        log_finish(run_id, status="failed", notes=str(exc))

    log.info("OAG complete — %s", summary)
    return summary


if __name__ == "__main__":
    result = run(download_files=True, scrape_all_fys=True)
    print(result)

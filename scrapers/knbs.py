"""
scrapers/knbs.py — Kenya National Bureau of Statistics

Scrapes and downloads:
  • Statistical Abstracts (2024, 2025) — chapter-level Excel files
  • Economic Surveys (2024, 2025) — chapter-level Excel files
  • Statistical Releases index — any PDF/Excel on the releases page
  • PDF full-publication downloads
  • Dynamic page crawl to catch any new uploads not in the hardcoded lists

Storage:  knbs_releases table
Files:    data/raw/knbs/
"""

import re
import time
from pathlib import Path
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config import (
    RAW_DIR,
    KNBS_BASE,
    KNBS_ABSTRACTS_PAGE,
    KNBS_SURVEY_2025_PAGE,
    KNBS_SURVEY_2024_PAGE,
    KNBS_RELEASES_PAGE,
    KNBS_PUBLICATIONS_PAGE,
    KNBS_ABSTRACT_2025,
    KNBS_ABSTRACT_2024,
    KNBS_ECON_SURVEY_2025,
)
from storage.database import (
    init_db, insert_if_new, log_start, log_finish, register_file,
)
from utils.helpers import safe_get, download_file, extract_file_links, slugify
from utils.logger import get_logger

log = get_logger("scraper.knbs")
RAW_KNBS = RAW_DIR / "knbs"


# ─── Chapter registry  (hardcoded + page-scraped) ────────────────────────────

_KNOWN_CHAPTERS: list[dict] = []

for chapter, url in KNBS_ABSTRACT_2025.items():
    _KNOWN_CHAPTERS.append({
        "title": chapter,
        "category": "Statistical Abstract",
        "release_type": "statistical_abstract",
        "year": 2025,
        "chapter": chapter,
        "file_url": url,
    })

for chapter, url in KNBS_ABSTRACT_2024.items():
    _KNOWN_CHAPTERS.append({
        "title": chapter,
        "category": "Statistical Abstract",
        "release_type": "statistical_abstract",
        "year": 2024,
        "chapter": chapter,
        "file_url": url,
    })

for chapter, url in KNBS_ECON_SURVEY_2025.items():
    _KNOWN_CHAPTERS.append({
        "title": chapter,
        "category": "Economic Survey",
        "release_type": "economic_survey",
        "year": 2025,
        "chapter": chapter,
        "file_url": url,
    })


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _year_from_url(url: str) -> Optional[int]:
    """Try to extract a 4-digit year from a URL path component."""
    m = re.search(r'/(\d{4})/', url)
    if m:
        return int(m.group(1))
    m = re.search(r'(\d{4})', url)
    if m:
        yr = int(m.group(1))
        if 2010 <= yr <= 2030:
            return yr
    return None


def _infer_release_type(title: str, url: str) -> str:
    tl = (title + url).lower()
    if "abstract" in tl:
        return "statistical_abstract"
    if "economic survey" in tl:
        return "economic_survey"
    if "cpi" in tl or "inflation" in tl:
        return "cpi"
    if "gdp" in tl:
        return "gdp"
    if "labour" in tl or "employment" in tl:
        return "labour"
    if "trade" in tl:
        return "trade"
    return "other"


def _infer_category(title: str, url: str) -> str:
    tl = (title + url).lower()
    if "public finance" in tl:
        return "Public Finance"
    if "national accounts" in tl or "gdp" in tl:
        return "National Accounts"
    if "agriculture" in tl:
        return "Agriculture"
    if "energy" in tl:
        return "Energy"
    if "transport" in tl:
        return "Transport"
    if "manufacturing" in tl:
        return "Manufacturing"
    if "health" in tl:
        return "Health"
    if "education" in tl:
        return "Education"
    return "General"


# ─── Page scrapers ────────────────────────────────────────────────────────────

def scrape_page_for_files(
    page_url: str,
    release_type: str,
    year: Optional[int] = None,
) -> list[dict]:
    """
    GET ``page_url`` and extract all downloadable file links (PDF, XLSX, XLS).
    Returns list of knbs_releases-compatible dicts.
    """
    resp = safe_get(page_url)
    if resp is None:
        log.warning("Could not fetch KNBS page: %s", page_url)
        return []

    links = extract_file_links(resp.text, base_url=page_url,
                               extensions=(".pdf", ".xlsx", ".xls", ".csv"))
    records = []
    for link in links:
        inferred_year = year or _year_from_url(link["url"])
        records.append({
            "title":        link["text"] or Path(link["url"]).stem,
            "category":     _infer_category(link["text"], link["url"]),
            "release_type": _infer_release_type(link["text"], link["url"]),
            "year":         inferred_year,
            "chapter":      None,
            "file_url":     link["url"],
            "scraped_at":   datetime.utcnow().isoformat(),
        })
    log.info("Found %d files on %s", len(records), page_url)
    return records


def scrape_releases_page() -> list[dict]:
    """Scrape the Statistical Releases category page."""
    return scrape_page_for_files(KNBS_RELEASES_PAGE, "other")


def scrape_abstracts_page() -> list[dict]:
    """Scrape the Statistical Abstracts landing page."""
    return scrape_page_for_files(KNBS_ABSTRACTS_PAGE, "statistical_abstract")


def scrape_survey_page(year: int) -> list[dict]:
    """Scrape the Economic Survey page for a given year."""
    url = KNBS_SURVEY_2025_PAGE if year >= 2025 else KNBS_SURVEY_2024_PAGE
    return scrape_page_for_files(url, "economic_survey", year=year)


def scrape_publications_page() -> list[dict]:
    """
    Crawl the main publications page and follow links to sub-pages
    to collect any remaining files not captured elsewhere.
    """
    resp = safe_get(KNBS_PUBLICATIONS_PAGE)
    if resp is None:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    sub_pages: list[str] = []

    # Find internal navigation links that look like publication pages
    for a in soup.find_all("a", href=True):
        href = a["href"]
        abs_href = urljoin(KNBS_BASE, href)
        if (KNBS_BASE in abs_href and
                any(kw in abs_href.lower() for kw in
                    ["reports", "releases", "publications", "survey",
                     "abstract", "cpi", "gdp", "labour"])):
            sub_pages.append(abs_href)

    # Deduplicate
    sub_pages = list(dict.fromkeys(sub_pages))[:20]  # cap at 20 sub-pages

    all_records: list[dict] = []
    for sp in sub_pages:
        records = scrape_page_for_files(sp, "other")
        all_records.extend(records)
        time.sleep(1.0)

    return all_records


# ─── Download engine ──────────────────────────────────────────────────────────

def download_knbs_file(record: dict) -> Optional[Path]:
    """
    Download the file referenced by a knbs_releases record.
    Returns local Path on success.
    """
    url = record.get("file_url")
    if not url:
        return None

    # Build a descriptive filename
    year = record.get("year") or ""
    release = slugify(record.get("release_type", "release"))
    chapter = slugify(record.get("chapter") or record.get("title") or "doc")
    ext = Path(url.split("?")[0]).suffix or ".xlsx"
    filename = f"knbs_{year}_{release}_{chapter}{ext}"

    return download_file(url, RAW_KNBS, filename, skip_if_exists=True)


# ─── Main entry point ─────────────────────────────────────────────────────────

def run(
    download_files: bool = True,
    scrape_dynamic: bool = True,
) -> dict:
    """
    Full KNBS scrape run.

    Args:
        download_files:  Whether to download the actual Excel/PDF files.
        scrape_dynamic:  Whether to also crawl page links dynamically.

    Returns:
        Summary dict with counts.
    """
    init_db()
    run_id = log_start("knbs")
    summary = {"records_indexed": 0, "files_downloaded": 0}

    try:
        # ── Step 1: insert known hardcoded chapters ───────────────────────────
        log.info("═══ KNBS: Indexing known chapters (%d) ═══", len(_KNOWN_CHAPTERS))
        ts = datetime.utcnow().isoformat()
        chapters_with_ts = [{**r, "scraped_at": ts} for r in _KNOWN_CHAPTERS]
        new = insert_if_new("knbs_releases", chapters_with_ts)
        summary["records_indexed"] += new
        log.info("Indexed %d new chapter records", new)

        # ── Step 2: dynamic page scraping ─────────────────────────────────────
        if scrape_dynamic:
            log.info("═══ KNBS: Dynamic page scraping ═══")
            scraped: list[dict] = []

            scraped.extend(scrape_abstracts_page())
            scraped.extend(scrape_survey_page(2025))
            scraped.extend(scrape_survey_page(2024))
            scraped.extend(scrape_releases_page())
            scraped.extend(scrape_publications_page())

            # Deduplicate by URL
            seen: set[str] = set()
            unique: list[dict] = []
            for r in scraped:
                url = r.get("file_url", "")
                if url and url not in seen:
                    seen.add(url)
                    r["scraped_at"] = datetime.utcnow().isoformat()
                    unique.append(r)

            if unique:
                new2 = insert_if_new("knbs_releases", unique)
                summary["records_indexed"] += new2
                log.info("Dynamic scrape found %d new records", new2)

        # ── Step 3: download files ─────────────────────────────────────────────
        if download_files:
            log.info("═══ KNBS: Downloading files ═══")
            from storage.database import get_conn, DB_PATH
            with get_conn(DB_PATH) as conn:
                rows = conn.execute(
                    "SELECT * FROM knbs_releases WHERE local_path IS NULL"
                ).fetchall()

            log.info("Downloading %d un-fetched KNBS files …", len(rows))
            downloaded = 0
            for row in rows:
                rec = dict(row)
                p = download_knbs_file(rec)
                if p:
                    # Update local_path in DB
                    with get_conn(DB_PATH) as conn:
                        conn.execute(
                            "UPDATE knbs_releases SET local_path = ? WHERE file_url = ?",
                            (str(p), rec["file_url"]),
                        )
                    register_file("knbs", rec["file_url"], p)
                    downloaded += 1

            summary["files_downloaded"] = downloaded
            log.info("Downloaded %d KNBS files", downloaded)

        log_finish(run_id, status="ok",
                   rows_added=summary["records_indexed"],
                   files_saved=summary["files_downloaded"])

    except Exception as exc:
        log.error("KNBS run failed: %s", exc, exc_info=True)
        log_finish(run_id, status="failed", notes=str(exc))

    log.info("KNBS complete — %s", summary)
    return summary


if __name__ == "__main__":
    result = run(download_files=True, scrape_dynamic=True)
    print(result)

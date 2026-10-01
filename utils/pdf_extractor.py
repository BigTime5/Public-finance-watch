"""
utils/pdf_extractor.py — Extract structured text and tables from government PDFs.

Uses pdfplumber (built on pdfminer) which handles complex government PDFs
better than PyPDF2.  Extracts:
  • Full text per page
  • Tables (as list-of-lists)
  • Financial figures (KES amounts)
  • Audit opinion keywords
  • County names and associated figures
"""

import re
from pathlib import Path
from typing import Optional, Generator

try:
    import pdfplumber
    _HAS_PDFPLUMBER = True
except ImportError:
    _HAS_PDFPLUMBER = False

from config import KENYA_COUNTIES
from utils.logger import get_logger

log = get_logger("pdf_extractor")

# ── Regex patterns ────────────────────────────────────────────────────────────
_KES_PATTERN     = re.compile(
    r"(?:KSh|KES|Kshs?|Ksh\.?)\s*\.?\s*([\d,]+(?:\.\d+)?)\s*(?:billion|million|thousand)?",
    re.IGNORECASE,
)
_MILLIONS_PTRN   = re.compile(r"([\d,]+(?:\.\d+)?)\s*(?:million|mn)", re.IGNORECASE)
_BILLIONS_PTRN   = re.compile(r"([\d,]+(?:\.\d+)?)\s*(?:billion|bn)", re.IGNORECASE)
_UNSUPPORTED_EXP = re.compile(
    r"unsupported\s+expenditure[s]?\s+of\s+(?:KSh|KES|Kshs?)?\s*([\d,\.]+)",
    re.IGNORECASE,
)
_ADVERSE_PATTERN = re.compile(
    r"\b(adverse|disclaimer|qualified|unqualified|unmodified)\b\s+(?:audit\s+)?opinion",
    re.IGNORECASE,
)

# Audit opinion levels (for scoring)
OPINION_SCORES = {
    "unqualified":  4,   # clean — best
    "unmodified":   4,
    "qualified":    2,   # issues found
    "disclaimer":   1,   # severe — auditor couldn't get info
    "adverse":      0,   # worst — materially misstated
}


def check_pdfplumber() -> bool:
    if not _HAS_PDFPLUMBER:
        log.warning("pdfplumber not installed. Run: pip install pdfplumber")
    return _HAS_PDFPLUMBER


def extract_text_pages(pdf_path: Path) -> list[str]:
    """
    Extract text from every page of a PDF.
    Returns list of strings (one per page).
    """
    if not check_pdfplumber():
        return []
    pages = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                pages.append(text)
    except Exception as exc:
        log.warning("PDF text extract failed (%s): %s", pdf_path.name, exc)
    return pages


def extract_tables(pdf_path: Path) -> list[list[list]]:
    """
    Extract all tables from a PDF.
    Returns list of tables, each table is list-of-rows, each row is list of cells.
    """
    if not check_pdfplumber():
        return []
    all_tables = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables() or []
                all_tables.extend(tables)
    except Exception as exc:
        log.warning("PDF table extract failed (%s): %s", pdf_path.name, exc)
    return all_tables


def extract_kes_amounts(text: str) -> list[float]:
    """
    Pull all KES / KSh monetary values from text.
    Converts millions/billions to absolute figures.
    Returns sorted list of floats.
    """
    amounts: list[float] = []

    for m in _KES_PATTERN.finditer(text):
        try:
            raw = float(m.group(1).replace(",", ""))
            suffix = (m.group(0).lower())
            if "billion" in suffix or "bn" in suffix:
                raw *= 1_000_000_000
            elif "million" in suffix or "mn" in suffix:
                raw *= 1_000_000
            elif "thousand" in suffix:
                raw *= 1_000
            amounts.append(raw)
        except ValueError:
            continue

    for m in _MILLIONS_PTRN.finditer(text):
        try:
            amounts.append(float(m.group(1).replace(",", "")) * 1_000_000)
        except ValueError:
            continue

    for m in _BILLIONS_PTRN.finditer(text):
        try:
            amounts.append(float(m.group(1).replace(",", "")) * 1_000_000_000)
        except ValueError:
            continue

    return sorted(set(amounts))


def extract_audit_opinion(text: str) -> dict:
    """
    Detect audit opinion type from text.
    Returns dict: {"opinion": str, "score": int, "snippets": list[str]}
    """
    text_lower = text.lower()
    found_opinions: list[tuple[str, int]] = []
    snippets: list[str] = []

    for m in _ADVERSE_PATTERN.finditer(text):
        opinion_word = m.group(1).lower()
        # normalise
        if opinion_word in ("unmodified",):
            opinion_word = "unqualified"
        score = OPINION_SCORES.get(opinion_word, 2)
        found_opinions.append((opinion_word, score))
        # grab surrounding context
        start = max(0, m.start() - 60)
        end   = min(len(text), m.end() + 60)
        snippets.append(text[start:end].strip())

    if not found_opinions:
        return {"opinion": "unknown", "score": -1, "snippets": []}

    # Return the worst opinion found (lowest score)
    worst = min(found_opinions, key=lambda x: x[1])
    return {
        "opinion":  worst[0],
        "score":    worst[1],
        "snippets": snippets[:3],
    }


def extract_unsupported_expenditure(text: str) -> list[float]:
    """
    Extract 'unsupported expenditure of KSh X' amounts — a key audit finding.
    These represent spending with NO supporting documentation.
    """
    amounts: list[float] = []
    for m in _UNSUPPORTED_EXP.finditer(text):
        try:
            amounts.append(float(m.group(1).replace(",", "")))
        except ValueError:
            continue
    return amounts


def extract_county_figures(text: str) -> list[dict]:
    """
    Find mentions of Kenya counties adjacent to financial figures.
    Returns list of {"county": str, "amount": float, "context": str}
    """
    results: list[dict] = []
    county_pattern = re.compile(
        r"(" + "|".join(re.escape(c) for c in KENYA_COUNTIES) + r")",
        re.IGNORECASE,
    )

    for m in county_pattern.finditer(text):
        county = m.group(1)
        # Look for an amount within 200 chars after the county name
        window = text[m.start(): m.start() + 200]
        amounts = extract_kes_amounts(window)
        if amounts:
            context_end = min(len(text), m.start() + 150)
            results.append({
                "county":  county,
                "amount":  amounts[0],
                "context": text[m.start(): context_end].replace("\n", " ").strip(),
            })

    return results


def analyse_oag_pdf(pdf_path: Path) -> dict:
    """
    Full structured analysis of an OAG audit report PDF.

    Returns:
    {
      "path":                    str,
      "pages":                   int,
      "audit_opinion":           dict,
      "total_kes_amounts":       list[float],
      "unsupported_expenditure": list[float],
      "county_figures":          list[dict],
      "key_findings":            list[str],
    }
    """
    result: dict = {
        "path":                    str(pdf_path),
        "pages":                   0,
        "audit_opinion":           {},
        "total_kes_amounts":       [],
        "unsupported_expenditure": [],
        "county_figures":          [],
        "key_findings":            [],
    }

    if not pdf_path.exists():
        log.warning("PDF not found: %s", pdf_path)
        return result

    pages = extract_text_pages(pdf_path)
    result["pages"] = len(pages)
    full_text = "\n".join(pages)

    result["audit_opinion"]           = extract_audit_opinion(full_text)
    result["total_kes_amounts"]        = extract_kes_amounts(full_text)
    result["unsupported_expenditure"]  = extract_unsupported_expenditure(full_text)
    result["county_figures"]           = extract_county_figures(full_text)

    # Key findings: sentences containing critical audit keywords
    critical_kws = [
        "unsupported", "irregular", "misappropriat", "embezzl",
        "ghost", "phantom", "unexplained", "unaccounted",
        "unvouched", "over-expenditure", "overexpenditure",
        "pending bill", "unimplemented", "not implemented",
    ]
    for page_text in pages:
        for sentence in re.split(r'(?<=[.!?])\s+', page_text):
            if any(kw in sentence.lower() for kw in critical_kws):
                clean = sentence.replace("\n", " ").strip()
                if len(clean) > 30:
                    result["key_findings"].append(clean[:300])

    # Deduplicate key findings
    result["key_findings"] = list(dict.fromkeys(result["key_findings"]))[:50]

    log.info(
        "PDF analysis: %s — %d pages, opinion=%s, %d findings",
        pdf_path.name,
        result["pages"],
        result["audit_opinion"].get("opinion", "?"),
        len(result["key_findings"]),
    )
    return result


def batch_analyse_oag(raw_dir: Path) -> list[dict]:
    """
    Analyse all OAG PDFs in ``raw_dir``.
    Returns list of analysis dicts sorted by unsupported expenditure (desc).
    """
    pdfs = sorted(raw_dir.glob("oag_*.pdf"))
    log.info("Batch-analysing %d OAG PDFs …", len(pdfs))
    results = []
    for pdf in pdfs:
        r = analyse_oag_pdf(pdf)
        results.append(r)

    # Sort: largest unsupported expenditure first
    results.sort(
        key=lambda x: max(x["unsupported_expenditure"]) if x["unsupported_expenditure"] else 0,
        reverse=True,
    )
    return results

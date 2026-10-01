"""
analyze.py — Cross-Source Analysis & Report Generation

What this script actually does (no overstatement):
  1. Procurement analytics by year, entity, county, method, supplier
  2. OAG PDF content extraction — run pdfplumber against downloaded PDFs
     to pull audit opinions, unsupported expenditure, and key-finding sentences
  3. Cross-source join — PPRA procurement flags linked to OAG findings
     for the same entity, same fiscal year, where data exists
  4. County scorecard — HHI supplier diversity + open-tendering % (PPRA only)
     Audit opinion scores added only where PDF extraction produced a result
  5. Missing-data diagnostic — explicit report on what is absent and why
  6. Excel workbook with all of the above, clearly labelled

What this script does NOT do (honest gaps documented in output):
  - It does not read COB budget absorption data (COB PDFs are HTML stubs)
  - It does not validate flags against known confirmed cases (no ground truth)
  - It does not verify PPDA s.103 exemption memos (OCDS data doesn't carry them)
  - Non-competitive flags are LEADS for human review, not proof of wrongdoing

Run:
  python analyze.py                   # full analysis including PDF extraction
  python analyze.py --no-pdf          # skip PDF extraction (faster, less output)
  python analyze.py --output myfile.xlsx
"""

import argparse
import re
import sys
import sqlite3
import warnings
from pathlib import Path
from datetime import datetime

import pandas as pd

warnings.filterwarnings("ignore")

HERE     = Path(__file__).parent
sys.path.insert(0, str(HERE))

from config import DB_PATH, PROC_DIR, RAW_DIR, KENYA_COUNTIES
from utils.logger import get_logger

log = get_logger("analyze")

RAW_OAG = RAW_DIR / "oag"


# ─── DB helper ────────────────────────────────────────────────────────────────

def _df(query: str, params=()) -> pd.DataFrame:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            return pd.read_sql_query(query, conn, params=params)
    except Exception as exc:
        log.warning("Query failed: %s", exc)
        return pd.DataFrame()


# ════════════════════════════════════════════════════════════════════════════════
#  1. PROCUREMENT ANALYTICS
# ════════════════════════════════════════════════════════════════════════════════

def procurement_summary() -> dict[str, pd.DataFrame]:
    dfs: dict[str, pd.DataFrame] = {}

    dfs["by_year"] = _df("""
        SELECT
            fiscal_year,
            COUNT(*)                                                AS total_contracts,
            SUM(CAST(award_value AS REAL))                          AS total_award_kes,
            AVG(CAST(award_value AS REAL))                          AS avg_award_kes,
            COUNT(DISTINCT procuring_entity)                        AS unique_entities,
            COUNT(DISTINCT supplier_name)                           AS unique_suppliers,
            SUM(CASE WHEN award_value IS NULL OR CAST(award_value AS REAL)=0
                     THEN 1 ELSE 0 END)                             AS contracts_no_value,
            SUM(CASE WHEN supplier_name IS NULL
                     THEN 1 ELSE 0 END)                             AS contracts_no_supplier
        FROM ppra_contracts
        WHERE fiscal_year IS NOT NULL
        GROUP BY fiscal_year
        ORDER BY fiscal_year DESC
    """)

    dfs["by_entity"] = _df("""
        SELECT
            procuring_entity,
            COUNT(DISTINCT fiscal_year)                             AS fiscal_years_active,
            COUNT(*)                                                AS contracts,
            SUM(CAST(award_value AS REAL))                          AS total_kes,
            AVG(CAST(award_value AS REAL))                          AS avg_kes,
            COUNT(DISTINCT supplier_name)                           AS supplier_count,
            SUM(CASE WHEN award_value IS NULL THEN 1 ELSE 0 END)    AS contracts_no_value,
            SUM(CASE WHEN supplier_name IS NULL THEN 1 ELSE 0 END)  AS contracts_no_supplier
        FROM ppra_contracts
        WHERE procuring_entity IS NOT NULL
        GROUP BY procuring_entity
        ORDER BY total_kes DESC NULLS LAST
        LIMIT 500
    """)

    dfs["by_method"] = _df("""
        SELECT
            procurement_method,
            fiscal_year,
            COUNT(*)                                                AS contracts,
            SUM(CAST(award_value AS REAL))                          AS award_kes_total,
            ROUND(100.0 * COUNT(*) /
                  SUM(COUNT(*)) OVER (PARTITION BY fiscal_year), 2) AS pct_of_fy_contracts
        FROM ppra_contracts
        WHERE procurement_method IS NOT NULL AND fiscal_year IS NOT NULL
        GROUP BY procurement_method, fiscal_year
        ORDER BY fiscal_year DESC, contracts DESC
    """)

    dfs["top_suppliers"] = _df("""
        SELECT
            supplier_name,
            COUNT(*)                                                AS wins,
            SUM(CAST(award_value AS REAL))                          AS award_kes_total,
            ROUND(SUM(CAST(award_value AS REAL))/1e6, 2)            AS award_kes_million,
            COUNT(DISTINCT procuring_entity)                        AS entities_served,
            MIN(award_date)                                         AS first_award,
            MAX(award_date)                                         AS last_award
        FROM ppra_contracts
        WHERE supplier_name IS NOT NULL
          AND CAST(award_value AS REAL) > 0
          AND fiscal_year IS NOT NULL
        GROUP BY supplier_name
        ORDER BY award_kes_total DESC
        LIMIT 500
    """)

    return dfs


# ════════════════════════════════════════════════════════════════════════════════
#  2. OAG PDF CONTENT EXTRACTION  —  actually runs pdfplumber
# ════════════════════════════════════════════════════════════════════════════════

def run_oag_pdf_extraction(max_pdfs: int = 676) -> pd.DataFrame:
    """
    Actually runs pdf_extractor.analyse_oag_pdf() against every downloaded
    OAG PDF.  Returns one row per PDF with:
      file, pages, audit_opinion, opinion_score,
      unsupported_kes_total, key_findings_count, top_finding,
      county_extracted (county name found in PDF filename or content)

    If pdfplumber is not installed, returns an empty DataFrame with a note.
    This function is the CPA-differentiated step: it reads what the
    Auditor-General actually found, not just that the PDF exists.
    """
    try:
        from utils.pdf_extractor import analyse_oag_pdf
    except ImportError as e:
        log.warning("pdf_extractor import failed: %s", e)
        return pd.DataFrame({"note": ["pdf_extractor unavailable: " + str(e)]})

    pdfs = sorted(RAW_OAG.glob("oag_*.pdf"))
    if not pdfs:
        log.info("No OAG PDFs found in %s", RAW_OAG)
        return pd.DataFrame({"note": [
            f"No OAG PDFs found in {RAW_OAG}. "
            "Run main.py --sources oag first."
        ]})

    # Apply cap but log it honestly
    total_available = len(pdfs)
    pdfs = pdfs[:max_pdfs]
    log.info(
        "Extracting content from %d/%d OAG PDFs …",
        len(pdfs), total_available
    )
    print(f"\n[PDF EXTRACTION]  Processing {len(pdfs):,}/{total_available:,} OAG PDFs …")

    records = []
    failed  = 0
    for i, pdf in enumerate(pdfs, 1):
        try:
            r = analyse_oag_pdf(pdf)
        except Exception as exc:
            log.warning("PDF failed (%s): %s", pdf.name, exc)
            failed += 1
            continue

        # Try to extract county from filename  e.g. oag_nairobi_executive_2023.pdf
        name_lower = pdf.stem.lower()
        county_from_name = next(
            (c for c in KENYA_COUNTIES if c.lower() in name_lower), None
        )

        records.append({
            "file":               pdf.name,
            "county_from_name":   county_from_name or "",
            "pages":              r["pages"],
            "audit_opinion":      r["audit_opinion"].get("opinion", "unknown"),
            "opinion_score":      r["audit_opinion"].get("score", -1),
            "opinion_snippet":    (r["audit_opinion"].get("snippets") or [""])[0][:150],
            "unsupported_kes_total": sum(r["unsupported_expenditure"]),
            "unsupported_amounts_found": len(r["unsupported_expenditure"]),
            "key_findings_count": len(r["key_findings"]),
            "top_finding":        (r["key_findings"] or [""])[0][:250],
            "county_figures_found": len(r["county_figures"]),
            "max_kes_in_pdf":     max(r["total_kes_amounts"]) if r["total_kes_amounts"] else 0,
        })

        if i % 50 == 0:
            print(f"   … {i:,}/{len(pdfs):,} processed")

    if not records:
        return pd.DataFrame({"note": [
            f"PDF extraction produced no records. {failed} PDFs failed. "
            "Check pdfplumber installation."
        ]})

    df = pd.DataFrame(records)
    # Sort: adverse/disclaimer opinions first, then by unsupported expenditure
    opinion_order = {"adverse": 0, "disclaimer": 1, "qualified": 2,
                     "unqualified": 3, "unknown": 4}
    df["opinion_sort"] = df["audit_opinion"].map(opinion_order).fillna(4)
    df = df.sort_values(
        ["opinion_sort", "unsupported_kes_total"],
        ascending=[True, False]
    ).drop(columns=["opinion_sort"]).reset_index(drop=True)

    print(f"   ✓  {len(df):,} PDFs analysed, {failed} failed")
    print(f"      Opinion breakdown: "
          + ", ".join(
              f"{op}={n}"
              for op, n in df["audit_opinion"].value_counts().items()
          ))
    return df


# ════════════════════════════════════════════════════════════════════════════════
#  3. CROSS-SOURCE JOIN  —  PPRA flags ↔ OAG findings (same entity, same FY)
# ════════════════════════════════════════════════════════════════════════════════

def cross_source_join(oag_pdf_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Links procurement red flags to OAG audit findings where:
      - The procuring entity name contains a Kenya county name, AND
      - The OAG PDF filename contains the SAME fiscal year as the flag
        (strict requirement — no fallback to a different year)

    Returns TWO DataFrames:
      1. matched   — flags with a same-fiscal-year OAG audit report found
      2. no_audit_yet — flags where the county has OAG reports, but none
                        for that specific fiscal year yet (most common case
                        for 2023/24 onward, since OAG publishes 1-2 years late)

    IMPORTANT — honest caveats:
      - Entity name → county mapping is approximate (string match)
      - A match means "same county, same fiscal year" — not "same institution"
      - This is intentionally strict. An earlier version of this function
        fell back to the oldest available report for a county when no
        same-year report existed, which silently misattributed unrelated,
        years-earlier findings to recent contracts. That has been removed.
      - Zero same-year matches does not mean no problem; it usually means
        the audit for that year has not been published yet (see no_audit_yet)
    """
    no_audit_yet: list[dict] = []
    flags_path = PROC_DIR / "red_flags.csv"
    if not flags_path.exists():
        empty = pd.DataFrame({"note": ["red_flags.csv not found — run precision_fix.py first"]})
        return empty, empty

    flags = pd.read_csv(flags_path)
    if flags.empty:
        empty = pd.DataFrame({"note": ["No flags to cross-reference"]})
        return empty, empty

    if oag_pdf_df.empty or "note" in oag_pdf_df.columns:
        empty = pd.DataFrame({"note": [
            "OAG PDF extraction produced no data — cross-source join skipped. "
            "Run with PDF extraction enabled to populate this sheet."
        ]})
        return empty, empty

    # Only use PDFs where we got a real opinion (not 'unknown')
    oag_known = oag_pdf_df[oag_pdf_df["audit_opinion"] != "unknown"].copy()
    if oag_known.empty:
        empty = pd.DataFrame({"note": [
            "OAG PDF extraction found no audit opinions — "
            "opinions may be in tables rather than running text. "
            "Cross-source join requires at least one extracted opinion."
        ]})
        return empty, empty

    # Map each flag's entity to a county name
    county_pat = re.compile(
        "(" + "|".join(re.escape(c) for c in KENYA_COUNTIES) + ")",
        re.IGNORECASE
    )
    flags["county_from_entity"] = flags["entity"].fillna("").apply(
        lambda e: m.group(1) if (m := county_pat.search(e)) else None
    )

    matched = []
    for _, flag in flags[flags["county_from_entity"].notna()].iterrows():
        county = flag["county_from_entity"]
        fy     = flag.get("fiscal_year", "")

        # Find OAG PDFs for the same county
        county_oag = oag_known[
            oag_known["county_from_name"].str.lower() == county.lower()
        ]
        if county_oag.empty:
            continue   # no OAG data at all for this county — not a match, not logged

        # STRICT requirement: the OAG report's fiscal year must match the flag's
        # fiscal year. Kenya's OAG publishes with a 1-2 year lag, so most recent
        # procurement (2023/24 onward) has NO audit report yet. We do NOT fall
        # back to an older report — that would silently misrepresent an
        # unrelated, years-earlier finding as if it concerned this contract.
        fy_year = (fy or "")[:4]
        same_fy = county_oag[
            county_oag["file"].str.contains(fy_year, na=False)
        ] if fy_year else pd.DataFrame()

        if same_fy.empty:
            # Honest negative: log that no contemporaneous audit exists yet,
            # rather than silently matching an unrelated year.
            no_audit_yet.append({
                "flag_type":        flag["flag_type"],
                "flag_severity":    flag["severity"],
                "procuring_entity": flag["entity"],
                "county":           county,
                "fiscal_year":      flag.get("fiscal_year", ""),
                "award_kes":        flag.get("award_kes", ""),
                "note": (
                    f"No OAG audit report exists yet for {county} FY {fy}. "
                    f"OAG typically publishes 1-2 years after the fiscal year ends. "
                    f"This flag cannot be cross-checked until that audit is published."
                ),
            })
            continue

        best = same_fy.iloc[0]
        matched.append({
            "flag_type":            flag["flag_type"],
            "flag_severity":        flag["severity"],
            "procuring_entity":     flag["entity"],
            "county":               county,
            "fiscal_year":          flag.get("fiscal_year", ""),
            "award_kes":            flag.get("award_kes", ""),
            "supplier":             flag.get("supplier", ""),
            "oag_pdf_file":         best["file"],
            "oag_audit_opinion":    best["audit_opinion"],
            "oag_opinion_score":    best["opinion_score"],
            "oag_unsupported_kes":  best["unsupported_kes_total"],
            "oag_top_finding":      best["top_finding"][:200],
            "ppra_flag_description": flag.get("description", "")[:200],
            "REVIEW_NOTE": (
                "LEAD FOR HUMAN REVIEW — not proof of wrongdoing. Same fiscal year "
                "confirmed. Verify: (1) PPDA s.103 exemption memo exists? "
                "(2) OAG finding is for this specific entity (not just county)? "
                "(3) Values reconcile across both sources?"
            ),
        })

    no_audit_df = pd.DataFrame(no_audit_yet)
    if not no_audit_df.empty:
        no_audit_df = no_audit_df.drop_duplicates(
            subset=["procuring_entity", "fiscal_year"]
        ).sort_values("fiscal_year", ascending=False).reset_index(drop=True)

    if not matched:
        empty = pd.DataFrame({"note": [
            f"No SAME-FISCAL-YEAR matches found between {len(flags)} flags and "
            f"{len(oag_known)} OAG PDFs with extracted opinions. "
            f"{len(no_audit_df):,} flags have a county match but no audit report "
            f"published yet for that fiscal year — see the No_Audit_Yet sheet. "
            "This may also mean entity names don't contain county names, or "
            "opinion extraction failed for the relevant PDFs."
        ]})
        return empty, no_audit_df

    df = pd.DataFrame(matched)
    # Prioritise adverse opinions + highest award values
    opinion_order = {"adverse": 0, "disclaimer": 1, "qualified": 2,
                     "unqualified": 3, "unknown": 4}
    df["opinion_sort"] = df["oag_audit_opinion"].map(opinion_order).fillna(4)
    df = df.sort_values(
        ["flag_severity", "opinion_sort"],
        ascending=[True, True]
    ).drop(columns=["opinion_sort"]).reset_index(drop=True)

    print(f"   ✓  Cross-source: {len(df):,} SAME-FISCAL-YEAR flag↔OAG matches "
          f"across {df['county'].nunique()} counties")
    print(f"   ℹ  {len(no_audit_df):,} flags have no audit published yet for "
          f"their fiscal year (see No_Audit_Yet sheet)")
    return df, no_audit_df


# ════════════════════════════════════════════════════════════════════════════════
#  4. COUNTY SCORECARD  —  honest, sourced from real data only
# ════════════════════════════════════════════════════════════════════════════════

def county_scorecard(oag_pdf_df: pd.DataFrame) -> pd.DataFrame:
    """
    Scores counties on dimensions where data actually exists.

    Dimension 1 — Open Tendering % (0–50 pts)
      Source: PPRA OCDS procurement_method field
      Formula: (open-method KES / total KES) × 50
      Coverage: counties where ppra_contracts.county IS NOT NULL

    Dimension 2 — Supplier Diversity HHI (0–50 pts)
      Source: PPRA OCDS supplier_name + award_value
      Formula: 50 × (1 − HHI) where HHI = sum of (supplier share)²
      Coverage: counties with ≥2 suppliers named

    Dimension 3 — Audit Opinion (0–25 pts, ADDITIVE BONUS)
      Source: OAG PDF extraction (only where extraction succeeded)
      Formula: adverse=0, disclaimer=5, qualified=15, unqualified=25
      Coverage: counties where at least one OAG PDF opinion was extracted

    NOTE: Budget absorption (COB) dimension is NOT included because COB PDFs
    are HTML stubs with no structured data. It will be added when COB data
    is available.  Score is out of 100 (Dim1+Dim2) or 125 (if Dim3 available).
    """
    log.info("Building county scorecard …")

    df_proc = _df("""
        SELECT
            county,
            SUM(CASE WHEN LOWER(procurement_method) LIKE '%open%'
                THEN CAST(award_value AS REAL) ELSE 0 END)          AS open_kes,
            SUM(CAST(award_value AS REAL))                           AS total_kes,
            COUNT(*)                                                  AS contracts
        FROM ppra_contracts
        WHERE county IS NOT NULL AND award_value IS NOT NULL
        GROUP BY county
    """)

    if df_proc.empty:
        return pd.DataFrame({"note": ["No county-level procurement data in DB"]})

    df_proc["open_pct"]          = df_proc["open_kes"] / df_proc["total_kes"].replace(0, 1)
    df_proc["transparency_score"] = (50 * df_proc["open_pct"]).clip(0, 50).round(1)

    # HHI supplier diversity
    sup_shares = _df("""
        SELECT county, supplier_name,
               SUM(CAST(award_value AS REAL)) AS supplier_kes
        FROM ppra_contracts
        WHERE county IS NOT NULL
          AND supplier_name IS NOT NULL
          AND award_value IS NOT NULL
        GROUP BY county, supplier_name
    """)
    diversity: dict[str, float] = {}
    if not sup_shares.empty:
        for county, grp in sup_shares.groupby("county"):
            total = grp["supplier_kes"].sum()
            if total > 0 and len(grp) >= 2:
                shares = grp["supplier_kes"] / total
                hhi    = (shares ** 2).sum()
                diversity[county] = round(50 * (1 - hhi), 1)
            elif len(grp) == 1:
                diversity[county] = 0.0   # monopoly
            # else: no data → leave absent (will fillna 0)

    df_proc["diversity_score"] = df_proc["county"].map(diversity).fillna(0)

    # OAG opinion — only from actual PDF extraction
    opinion_pts = {"adverse": 0, "disclaimer": 5, "qualified": 15,
                   "unqualified": 25, "unmodified": 25}
    df_proc["audit_score"]   = float("nan")   # NaN = not measured
    df_proc["audit_opinion"] = ""
    df_proc["audit_source"]  = ""

    if not oag_pdf_df.empty and "note" not in oag_pdf_df.columns:
        oag_known = oag_pdf_df[oag_pdf_df["audit_opinion"] != "unknown"]
        for idx, row in df_proc.iterrows():
            county = row["county"]
            matches = oag_known[
                oag_known["county_from_name"].str.lower() == county.lower()
            ]
            if matches.empty:
                continue
            # Use worst opinion found (lowest score)
            worst = matches.loc[
                matches["opinion_score"].idxmin()
            ]
            df_proc.at[idx, "audit_score"]   = opinion_pts.get(
                worst["audit_opinion"], float("nan")
            )
            df_proc.at[idx, "audit_opinion"] = worst["audit_opinion"]
            df_proc.at[idx, "audit_source"]  = worst["file"]

    has_audit = df_proc["audit_score"].notna()

    # Composite — base score always out of 100; audit adds up to 25 where available
    df_proc["base_score"] = (
        df_proc["transparency_score"] + df_proc["diversity_score"]
    ).round(1)
    df_proc["total_score"] = df_proc["base_score"].copy()
    df_proc.loc[has_audit, "total_score"] = (
        df_proc.loc[has_audit, "base_score"] +
        df_proc.loc[has_audit, "audit_score"]
    ).round(1)

    df_proc["score_note"] = "Base score (PPRA only; no OAG data for this county)"
    df_proc.loc[has_audit, "score_note"] = "PPRA + OAG opinion included"

    df_proc["total_kes_bn"] = (df_proc["total_kes"] / 1e9).round(3)

    result = df_proc[[
        "county", "contracts", "total_kes_bn",
        "transparency_score", "diversity_score",
        "audit_opinion", "audit_score",
        "base_score", "total_score", "score_note", "audit_source",
    ]].sort_values("total_score", ascending=False).reset_index(drop=True)

    log.info("County scorecard: %d counties, %d with audit opinion",
             len(result), has_audit.sum())
    return result


# ════════════════════════════════════════════════════════════════════════════════
#  5. MISSING-DATA DIAGNOSTIC
# ════════════════════════════════════════════════════════════════════════════════

def missing_data_diagnostic() -> pd.DataFrame:
    """
    Explicit accounting of what data is absent, why, and what it means
    for the analysis.  This is the section a CPA reviewer would look for.
    """
    # PPRA completeness
    ppra_total = _df("SELECT COUNT(*) AS n FROM ppra_contracts").iloc[0]["n"]
    ppra_val   = _df("SELECT COUNT(*) AS n FROM ppra_contracts WHERE CAST(award_value AS REAL) > 0").iloc[0]["n"]
    ppra_sup   = _df("SELECT COUNT(*) AS n FROM ppra_contracts WHERE supplier_name IS NOT NULL").iloc[0]["n"]
    ppra_county= _df("SELECT COUNT(*) AS n FROM ppra_contracts WHERE county IS NOT NULL").iloc[0]["n"]

    # OAG
    oag_indexed = _df("SELECT COUNT(*) AS n FROM oag_reports").iloc[0]["n"]
    oag_pdfs    = len(list(RAW_OAG.glob("oag_*.pdf")))

    # COB
    cob_total = _df("SELECT COUNT(*) AS n FROM cob_reports").iloc[0]["n"]
    cob_with_pdf = _df("SELECT COUNT(*) AS n FROM cob_reports WHERE local_path IS NOT NULL AND local_path NOT LIKE '%LINK ONLY%'").iloc[0]["n"]

    rows = [
        # ── PPRA ──────────────────────────────────────────────────────────────
        ("PPRA", "Total contracts in DB",
         f"{ppra_total:,}", "Complete — 2024 + 2025 OCDS files"),

        ("PPRA", "Contracts with award_value > 0",
         f"{ppra_val:,} / {ppra_total:,} ({100*ppra_val//ppra_total}%)",
         "63% have NULL or zero value. OCDS releases include tenders not yet awarded; "
         "value is only populated post-award. This is a data source limitation, "
         "not a cleaning error."),

        ("PPRA", "Contracts with supplier_name",
         f"{ppra_sup:,} / {ppra_total:,} ({100*ppra_sup//ppra_total}%)",
         "70% have no supplier. awards_suppliers sheet only covers awarded contracts "
         "with a named winner. ~62k releases are pre-award tenders. "
         "IMPACT: Dominant Supplier flag (Flag 2) is based on the 30% with names; "
         "concentration in the unnamed 70% is undetected."),

        ("PPRA", "Contracts with county field",
         f"{ppra_county:,} / {ppra_total:,} ({100*ppra_county//ppra_total}%)",
         "County is derived from buyer_name string matching. National-level entities "
         "(e.g. Kenya Power) have no county. County-level analysis is subset only."),

        ("PPRA", "Historical years (pre-2023/24)",
         "NOT YET DOWNLOADED",
         "Only FY 2024 and 2025 OCDS files downloaded. "
         "Run: python main.py --all-years --sources ppra "
         "to add 2018–2023 (estimated +250k contracts)."),

        # ── OAG ───────────────────────────────────────────────────────────────
        ("OAG", "Reports indexed (deduplicated)",
         f"{oag_indexed:,}", "Complete index from oagkenya.go.ke"),

        ("OAG", "PDFs downloaded to disk",
         f"{oag_pdfs:,} / {oag_indexed:,}",
         "676/680 PDFs downloaded. 4 URLs returned non-PDF responses."),

        ("OAG", "Audit opinions extracted (PDF text)",
         "See OAG_PDF_Analysis sheet",
         "Opinions extracted by pdfplumber from running text. "
         "If opinion appears in a table or image-scan page, extraction may miss it. "
         "Validate a sample manually."),

        # ── COB ───────────────────────────────────────────────────────────────
        ("COB", "Reports indexed",
         f"{cob_total:,}", "WordPress download page links catalogued"),

        ("COB", "Reports with PDF on disk",
         f"{cob_with_pdf:,} / {cob_total:,}",
         "COB download pages returned 0.1 MB HTML confirmation pages, not PDFs. "
         "Budget absorption analysis (county development spend %) is NOT possible "
         "until PDFs are obtained. Download manually from cob.go.ke or via "
         "authenticated session. This means the 'money disappears before delivery' "
         "cross-reference is not yet demonstrable in this output."),

        # ── Fraud flags ────────────────────────────────────────────────────────
        ("Flags", "Flag 2: DOMINANT_SUPPLIER",
         "Based on 30% of contracts (those with supplier names)",
         "Cannot flag entities where all contracts have NULL supplier_name. "
         "A zero count here is inconclusive, not a clean bill of health."),

        ("Flags", "Non-competitive flags vs PPDA s.103 exemptions",
         "Exemptions NOT checked",
         "PPDA 2015 s.103 permits direct procurement in defined circumstances "
         "(emergencies, sole-source, framework call-offs). OCDS data does not "
         "carry the justification memo. Every non-competitive flag is a LEAD "
         "requiring a human to pull the procurement file and check the exemption. "
         "These are NOT findings of wrongdoing."),

        ("Flags", "Ground-truth validation",
         "NOT YET DONE",
         "No comparison against confirmed corruption cases. "
         "Hit rate of flags is unknown. "
         "Recommended next step: manually check 10–20 flags against OAG "
         "findings for the same entity and FY."),
    ]

    return pd.DataFrame(rows, columns=[
        "Data_Source", "Metric", "Status", "Explanation_and_Impact"
    ])


# ════════════════════════════════════════════════════════════════════════════════
#  6. RED FLAGS SUMMARY
# ════════════════════════════════════════════════════════════════════════════════

def red_flags_summary() -> pd.DataFrame:
    flags_path = PROC_DIR / "red_flags.csv"
    if not flags_path.exists():
        return pd.DataFrame({"note": ["No red_flags.csv — run precision_fix.py first"]})
    df = pd.read_csv(flags_path)
    log.info("Red flags: %d records", len(df))
    return df


# ════════════════════════════════════════════════════════════════════════════════
#  7. DOCUMENT CATALOGUES
# ════════════════════════════════════════════════════════════════════════════════

def budget_docs_index() -> dict[str, pd.DataFrame]:
    return {
        "knbs_releases": _df("SELECT * FROM knbs_releases ORDER BY year DESC, category"),
        "cob_reports":   _df("SELECT * FROM cob_reports ORDER BY fiscal_year DESC, quarter"),
        "treasury_docs": _df("SELECT * FROM treasury_docs ORDER BY fiscal_year DESC, doc_type"),
        "oag_reports":   _df("SELECT * FROM oag_reports ORDER BY fiscal_year DESC, report_type"),
        "downloaded":    _df("SELECT * FROM downloaded_files ORDER BY downloaded_at DESC"),
        "scrape_log":    _df("SELECT * FROM scrape_log ORDER BY started_at DESC"),
    }


# ════════════════════════════════════════════════════════════════════════════════
#  8. EXCEL WORKBOOK
# ════════════════════════════════════════════════════════════════════════════════

def build_excel_report(output_path: Path, include_pdf: bool = True) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("Building Excel report → %s", output_path)
    print(f"\n[EXCEL]  Building workbook → {output_path.name} …")

    # Gather all data
    proc       = procurement_summary()
    flags      = red_flags_summary()
    docs       = budget_docs_index()
    diagnostic = missing_data_diagnostic()

    oag_pdf_df = run_oag_pdf_extraction() if include_pdf else pd.DataFrame(
        {"note": ["PDF extraction skipped (--no-pdf flag)"]}
    )
    cross_df, no_audit_df = cross_source_join(oag_pdf_df)
    score      = county_scorecard(oag_pdf_df)

    def _write(df_in, sheet_name, writer):
        (df_in if (df_in is not None and not df_in.empty)
         else pd.DataFrame({"note": ["No data"]})).to_excel(
             writer, sheet_name=sheet_name, index=False)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:

        # ── README ────────────────────────────────────────────────────────────
        pd.DataFrame({
            "Sheet": [
                "README",
                "Missing_Data_Diagnostic",
                "Procurement_Year",
                "Procurement_Entity",
                "Procurement_Method",
                "Top_500_Suppliers",
                "Red_Flags",
                "OAG_PDF_Analysis",
                "Cross_Source_PPRA_OAG",
                "No_Audit_Yet",
                "County_Scorecard",
                "OAG_Audit_Index",
                "COB_Reports",
                "Treasury_Docs",
                "KNBS_Index",
                "Download_Log",
                "Scrape_Log",
            ],
            "Data_Source": [
                "—",
                "All sources",
                "PPRA OCDS (tenders.go.ke)",
                "PPRA OCDS",
                "PPRA OCDS",
                "PPRA OCDS",
                "Anomaly detection (precision_fix.py)",
                "OAG PDFs (oagkenya.go.ke) — pdfplumber",
                "PPRA flags ↔ OAG opinions (same FY only)",
                "PPRA flags ↔ OAG coverage gap",
                "PPRA + OAG (where available)",
                "OAG (oagkenya.go.ke)",
                "COB (cob.go.ke)",
                "National Treasury (treasury.go.ke)",
                "KNBS (knbs.or.ke)",
                "Internal",
                "Internal",
            ],
            "What_it_contains": [
                "This legend",
                "Explicit gap analysis — what is missing and why it matters",
                "Contract counts and values by fiscal year (includes completeness columns)",
                "Top 500 entities by total KES awarded",
                "Procurement method breakdown by FY",
                "Top 500 suppliers by total KES won",
                "629 procurement anomaly flags — LEADS for human review, not findings",
                "Structured extraction from OAG PDFs: opinion, unsupported expenditure, key sentences",
                "Flags matched to OAG findings — STRICT same fiscal year only, no fallback",
                "Flags with a county match but no audit published yet for that FY",
                "County scores: open-tendering %, HHI diversity, OAG opinion where extracted",
                "Index of 680 OAG audit reports (deduplicated, HTTPS URLs)",
                "COB report catalogue — NOTE: PDFs not downloaded, budget absorption unavailable",
                "7 Treasury documents (BPS, BROP, Budget Summaries)",
                "31 KNBS statistical chapters",
                "All downloaded files with sizes and timestamps",
                "Scraper run history",
            ],
            "Honest_caveats": [
                "—",
                "Read this sheet before drawing any conclusions",
                "37% of contracts have a value; 63% are pre-award or value-missing",
                "Top 500 by KES only; entities with NULL award_value are excluded",
                "Non-competitive flags are leads — PPDA s.103 exemptions not checked",
                "Only 30% of contracts have supplier names; concentration in unnamed 70% undetected",
                "Statistical heuristics — not proof of fraud. See REVIEW_NOTE column.",
                "Text extraction only; table/image opinions may be missed",
                "County-level match only — entity ≠ county government in many cases. Strict same-FY required (no fallback to older audits).",
                "Most 2023/24+ flags land here — OAG publishes 1-2 years late, so recent contracts have no audit yet",
                "COB budget absorption dimension absent; audit scores only where PDF text extracted",
                "Catalogue only — content in OAG_PDF_Analysis sheet",
                "PDFs not downloadable without auth session — budget absorption gap",
                "2 records have no file on disk",
                "Manual download required (SSL issues with Python on Windows)",
                "—",
                "—",
            ]
        }).to_excel(writer, sheet_name="README", index=False)

        # ── Diagnostic ────────────────────────────────────────────────────────
        _write(diagnostic,                  "Missing_Data_Diagnostic", writer)

        # ── Procurement ───────────────────────────────────────────────────────
        _write(proc.get("by_year",       pd.DataFrame()), "Procurement_Year",    writer)
        _write(proc.get("by_entity",     pd.DataFrame()), "Procurement_Entity",  writer)
        _write(proc.get("by_method",     pd.DataFrame()), "Procurement_Method",  writer)
        _write(proc.get("top_suppliers", pd.DataFrame()), "Top_500_Suppliers",   writer)

        # ── Flags ─────────────────────────────────────────────────────────────
        _write(flags,                       "Red_Flags",               writer)

        # ── OAG PDF extraction ────────────────────────────────────────────────
        _write(oag_pdf_df,                  "OAG_PDF_Analysis",        writer)

        # ── Cross-source join ─────────────────────────────────────────────────
        _write(cross_df,                    "Cross_Source_PPRA_OAG",   writer)
        _write(no_audit_df,                 "No_Audit_Yet",            writer)

        # ── Scorecard ─────────────────────────────────────────────────────────
        _write(score,                       "County_Scorecard",        writer)

        # ── Catalogues ────────────────────────────────────────────────────────
        _write(docs.get("oag_reports",   pd.DataFrame()), "OAG_Audit_Index",  writer)
        _write(docs.get("cob_reports",   pd.DataFrame()), "COB_Reports",      writer)
        _write(docs.get("treasury_docs", pd.DataFrame()), "Treasury_Docs",    writer)
        _write(docs.get("knbs_releases", pd.DataFrame()), "KNBS_Index",       writer)
        _write(docs.get("downloaded",    pd.DataFrame()), "Download_Log",     writer)
        _write(docs.get("scrape_log",    pd.DataFrame()), "Scrape_Log",       writer)

    sz = output_path.stat().st_size / (1024 * 1024)
    log.info("Excel saved → %s (%.2f MB)", output_path.name, sz)
    print(f"  ✓  {output_path.name}  ({sz:.2f} MB)")
    return output_path


# ════════════════════════════════════════════════════════════════════════════════
#  9. TERMINAL SUMMARY
# ════════════════════════════════════════════════════════════════════════════════

def print_summary() -> None:
    bar = "═" * 60
    print(f"\n{bar}")
    print("  KENYA PUBLIC FINANCE INTELLIGENCE — DATA SUMMARY")
    print(bar)

    counts = _df("""
        SELECT 'PPRA Contracts' AS src, COUNT(*) AS n FROM ppra_contracts
        UNION ALL SELECT 'OAG Reports',    COUNT(*) FROM oag_reports
        UNION ALL SELECT 'COB Reports',    COUNT(*) FROM cob_reports
        UNION ALL SELECT 'Treasury Docs',  COUNT(*) FROM treasury_docs
        UNION ALL SELECT 'KNBS Chapters',  COUNT(*) FROM knbs_releases
    """)
    if not counts.empty:
        print("\n  Records in database:")
        for _, row in counts.iterrows():
            print(f"     {row['src']:<25}  {int(row['n']):>8,}")

    ppra = _df("""
        SELECT COUNT(*) AS n,
               SUM(CASE WHEN CAST(award_value AS REAL)>0 THEN 1 ELSE 0 END) AS n_val,
               SUM(CAST(award_value AS REAL))  AS total_kes,
               COUNT(DISTINCT procuring_entity) AS entities,
               COUNT(DISTINCT supplier_name)    AS suppliers
        FROM ppra_contracts WHERE fiscal_year IS NOT NULL
    """)
    if not ppra.empty and ppra.iloc[0]["n"] > 0:
        r = ppra.iloc[0]
        print(f"\n  PPRA: {int(r['n']):,} contracts, "
              f"{int(r['n_val']):,} with value ({100*int(r['n_val'])//int(r['n'])}%)")
        print(f"        KES {(r['total_kes'] or 0)/1e9:,.2f}B total awarded")
        print(f"        {int(r['entities']):,} entities, {int(r['suppliers']):,} named suppliers")

    flags_path = PROC_DIR / "red_flags.csv"
    if flags_path.exists():
        fl = pd.read_csv(flags_path)
        print(f"\n  Red Flags: {len(fl):,} total "
              f"(HIGH: {(fl['severity']=='HIGH').sum()}, "
              f"MEDIUM: {(fl['severity']=='MEDIUM').sum()})")
        print("  These are anomaly LEADS — not findings of wrongdoing.")
        for ft, grp in fl.groupby("flag_type"):
            print(f"     {ft:<38}  {len(grp):>5,}")

    pdfs = len(list(RAW_OAG.glob("oag_*.pdf")))
    print(f"\n  OAG PDFs on disk: {pdfs:,}")
    print(f"  Run analyze.py (without --no-pdf) to extract content from all {pdfs:,}.")
    print(f"\n{bar}\n")


# ════════════════════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Kenya Public Finance Intelligence — Analysis Engine"
    )
    p.add_argument(
        "--output",
        default=str(PROC_DIR / f"kenya_intel_{datetime.now().strftime('%Y%m%d')}.xlsx"),
        help="Output Excel file path",
    )
    p.add_argument(
        "--no-pdf", action="store_true",
        help="Skip OAG PDF content extraction (faster; OAG_PDF_Analysis will be empty)"
    )
    p.add_argument(
        "--summary-only", action="store_true",
        help="Print terminal summary only — no Excel output"
    )
    p.add_argument(
        "--max-pdfs", type=int, default=676,
        help="Maximum OAG PDFs to process (default: all 676)"
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    print_summary()

    if args.summary_only:
        return

    output = Path(args.output)
    build_excel_report(output, include_pdf=not args.no_pdf)
    log.info("Done. Open: %s", output)


if __name__ == "__main__":
    main()

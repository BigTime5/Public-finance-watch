"""
fix_and_analyze.py  —  Run this ONCE to fix and generate everything.

Fixes:
  1. Reads ALL sheets from PPRA OCDS Excel (main + awards + contracts are separate sheets)
  2. Merges them correctly, re-ingests 92,230+ contracts with award_value populated
  3. Runs 5 automated fraud/anomaly detection checks
  4. Downloads real COB budget reports from confirmed working URLs
  5. Generates the complete Excel analysis workbook

Usage:
    python fix_and_analyze.py
"""

import sys, sqlite3, re, time, warnings, datetime as dt
from pathlib import Path

warnings.filterwarnings("ignore")

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import pandas as pd

# ── Config (inline so this script is self-contained) ──────────────────────────
DB_PATH   = HERE / "data" / "kenya_intel.db"
RAW_PPRA  = HERE / "data" / "raw" / "ppra"
RAW_COB   = HERE / "data" / "raw" / "cob"
PROC_DIR  = HERE / "data" / "processed"
PROC_DIR.mkdir(parents=True, exist_ok=True)
RAW_COB.mkdir(parents=True, exist_ok=True)

SEP = "═" * 62
print(SEP)
print("  KENYA PUBLIC FINANCE INTELLIGENCE — FIX & ANALYZE")
print(SEP)


# ══════════════════════════════════════════════════════════════════════════════
#  UTILITY FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def db():
    c = sqlite3.connect(DB_PATH, timeout=60)
    c.execute("PRAGMA journal_mode=WAL")
    return c


def find_col(cols, *candidates):
    """
    Find the first column matching any candidate pattern (case-insensitive).
    Tries exact match first, then 'ends with', then 'contains'.
    """
    cols_l = [(c, c.lower().replace(" ", "")) for c in cols]
    for cand in candidates:
        cl = cand.lower().replace(" ", "").replace("/", "").replace(".", "")
        # exact
        for orig, low in cols_l:
            if cl == low.replace("/", "").replace(".", ""):
                return orig
        # ends-with
        for orig, low in cols_l:
            stripped = low.replace("/", "").replace(".", "")
            if stripped.endswith(cl):
                return orig
        # contains
        for orig, low in cols_l:
            stripped = low.replace("/", "").replace(".", "")
            if cl in stripped:
                return orig
    return None


def safe(v):
    """Return cleaned string or None."""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "nan", "None", "NaN", "NaT"):
        return None
    return s


def fy(date_str):
    """Convert date string → Kenyan fiscal year string."""
    try:
        d = pd.to_datetime(date_str, errors="coerce")
        if pd.isna(d):
            return None
        y, m = d.year, d.month
        return f"{y}/{str(y+1)[2:]}" if m >= 7 else f"{y-1}/{str(y)[2:]}"
    except Exception:
        return None


def batch_upsert(conn, table, rows, batch=2000):
    """Upsert rows into table in batches."""
    if not rows:
        return 0
    cols = list(rows[0].keys())
    ph   = ", ".join("?" * len(cols))
    cn   = ", ".join(cols)
    sql  = f"INSERT OR REPLACE INTO {table} ({cn}) VALUES ({ph})"
    total = 0
    for i in range(0, len(rows), batch):
        chunk = rows[i:i+batch]
        conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in chunk])
        conn.commit()
        total += len(chunk)
    return total


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 1 — Diagnose PPRA Excel sheet structure
# ══════════════════════════════════════════════════════════════════════════════
print("\n[1/6]  Reading PPRA Excel sheet structure …")

xlsx_files = sorted(RAW_PPRA.glob("ppra_ocds_*.xlsx"))
if not xlsx_files:
    print(f"  ✗  No PPRA Excel files in {RAW_PPRA}")
    print("     Run:  python main.py --sources ppra --ppra-years 2024 2025")
    sys.exit(1)

# Inspect first file
sample = xlsx_files[0]
print(f"  File: {sample.name}")

try:
    xl = pd.ExcelFile(sample)
    sheets = xl.sheet_names
    print(f"  Sheets ({len(sheets)}): {sheets}")
except Exception as e:
    print(f"  ✗  Cannot open Excel: {e}")
    sys.exit(1)

# Show columns per sheet (first 20 cols)
for sh in sheets[:6]:
    try:
        df_h = xl.parse(sh, nrows=0)
        print(f"    Sheet '{sh}': {len(df_h.columns)} cols — "
              f"{list(df_h.columns[:8])} …")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 2 — Re-ingest with correct multi-sheet merging
# ══════════════════════════════════════════════════════════════════════════════
print("\n[2/6]  Re-ingesting PPRA contracts (multi-sheet merge) …")

def ingest_xlsx(path: Path, conn) -> int:
    """
    Read a PPRA OCDS Excel workbook, merge main+awards+contracts sheets,
    and upsert into ppra_contracts.  Returns rows upserted.
    """
    xl = pd.ExcelFile(path)
    sh = xl.sheet_names
    sh_l = {s.lower().replace(" ", "").replace("_", ""): s for s in sh}

    # ── Read main sheet ───────────────────────────────────────────────────────
    main_key = sh_l.get("main", sh_l.get("releases", sh[0]))
    df_main  = xl.parse(main_key, dtype=str)
    mc       = list(df_main.columns)

    # Map main-sheet fields
    MAIN = {
        "ocid":           find_col(mc, "ocid"),
        "tender_id":      find_col(mc, "tender/id", "tenderid", "tender_id"),
        "tender_title":   find_col(mc, "tender/title", "tender_title", "title"),
        "buyer_name":     find_col(mc, "buyer/name", "buyer.name", "buyername",
                                   "buyer_name", "procuringentity"),
        "proc_method":    find_col(mc, "tender/procurementMethod",
                                   "procurementmethod", "tender/procurementmethod",
                                   "procurement_method"),
        "tender_status":  find_col(mc, "tender/status", "tender_status", "status"),
        "tender_val":     find_col(mc, "tender/value/amount",
                                   "tendervalueamount", "tender_value_amount"),
        "tender_cur":     find_col(mc, "tender/value/currency",
                                   "tender_currency"),
    }

    # ── Try to find awards in SAME sheet (flattened format) ───────────────────
    award_val_col   = find_col(mc, "awards/value/amount", "awards.value.amount",
                               "awardsvalueamount", "award_value_amount",
                               "awards_value_amount")
    award_date_col  = find_col(mc, "awards/date", "awardsdate", "award_date",
                               "awards_date")
    supplier_col    = find_col(mc, "awards/suppliers/name",
                               "awards/supplier/name",
                               "awards.suppliers.name", "awards.supplier.name",
                               "supplierName", "supplier_name",
                               "awards_suppliers_name")
    contract_val_col = find_col(mc, "contracts/value/amount",
                                "contractsvalueamount", "contract_value_amount")
    contract_start_col = find_col(mc, "contracts/period/startDate",
                                  "contractsperiodstartdate",
                                  "contract_start", "contracts_period_startdate")
    contract_end_col   = find_col(mc, "contracts/period/endDate",
                                  "contractsperiodenddate",
                                  "contract_end", "contracts_period_enddate")

    print(f"    award_value col found in main: {award_val_col}")
    print(f"    supplier_name col found in main: {supplier_col}")

    # ── Try awards sheet if not in main ──────────────────────────────────────
    df_awards = pd.DataFrame()
    if not award_val_col:
        awards_key = (sh_l.get("awards") or
                      sh_l.get("tender_awards") or
                      next((v for k, v in sh_l.items() if "award" in k), None))
        if awards_key:
            try:
                df_awards = xl.parse(awards_key, dtype=str)
                ac = list(df_awards.columns)
                print(f"    Awards sheet '{awards_key}': {len(df_awards)} rows, "
                      f"cols: {ac[:8]}")
                link_col   = find_col(ac, "_link_main", "link_main",
                                      "_linkmain", "ocid")
                av_col     = find_col(ac, "value/amount", "valueamount",
                                      "amount", "value_amount")
                ac_col     = find_col(ac, "value/currency", "currency",
                                      "value_currency")
                ad_col     = find_col(ac, "date", "award_date", "awarddate")
                sn_col     = find_col(ac,
                                      "suppliers/name", "supplier/name",
                                      "suppliersname", "suppliername",
                                      "name", "supplier_name")
                if link_col and av_col:
                    df_awards = df_awards.rename(columns={
                        link_col: "_main_link",
                        av_col:   "_award_val",
                        ac_col:   "_award_cur",
                        ad_col:   "_award_date",
                        sn_col:   "_supplier",
                    })
                    df_awards = df_awards[
                        [c for c in ["_main_link","_award_val","_award_cur",
                                     "_award_date","_supplier"] if c in df_awards]
                    ].drop_duplicates(subset=["_main_link"])
                else:
                    df_awards = pd.DataFrame()
            except Exception as e:
                print(f"    Could not parse awards sheet: {e}")

    # ── Merge awards into main if separate ───────────────────────────────────
    if not df_awards.empty and MAIN["ocid"]:
        df_main = df_main.merge(
            df_awards,
            left_on=MAIN["ocid"],
            right_on="_main_link",
            how="left",
        )
        award_val_col     = "_award_val"
        award_date_col    = "_award_date"
        supplier_col      = "_supplier"

    # ── Year fallback ─────────────────────────────────────────────────────────
    yr_match  = re.search(r"_(\d{4})\.", path.name)
    file_year = int(yr_match.group(1)) if yr_match else None

    # ── Build rows ────────────────────────────────────────────────────────────
    rows = []
    now  = dt.datetime.utcnow().isoformat()
    for _, row in df_main.iterrows():
        ocid = safe(row.get(MAIN["ocid"])) if MAIN["ocid"] else None
        if not ocid:
            continue

        av         = safe(row.get(award_val_col))   if award_val_col   else None
        ad         = safe(row.get(award_date_col))  if award_date_col  else None
        fiscal_yr  = fy(ad)
        if not fiscal_yr and file_year:
            fiscal_yr = f"{file_year}/{str(file_year+1)[2:]}"

        rows.append({
            "ocid":               ocid,
            "tender_id":          safe(row.get(MAIN["tender_id"]))    if MAIN["tender_id"]    else None,
            "tender_title":       safe(row.get(MAIN["tender_title"])) if MAIN["tender_title"] else None,
            "procuring_entity":   safe(row.get(MAIN["buyer_name"]))   if MAIN["buyer_name"]   else None,
            "county":             None,
            "procurement_method": safe(row.get(MAIN["proc_method"]))  if MAIN["proc_method"]  else None,
            "tender_status":      safe(row.get(MAIN["tender_status"])) if MAIN["tender_status"] else None,
            "tender_value":       safe(row.get(MAIN["tender_val"]))   if MAIN["tender_val"]   else None,
            "tender_currency":    safe(row.get(MAIN["tender_cur"]))   if MAIN["tender_cur"]   else None,
            "award_date":         ad,
            "award_value":        av,
            "award_currency":     safe(row.get("_award_cur")) if "_award_cur" in df_main.columns else None,
            "supplier_name":      safe(row.get(supplier_col)) if supplier_col else None,
            "supplier_id":        None,
            "contract_start":     safe(row.get(contract_start_col)) if contract_start_col else None,
            "contract_end":       safe(row.get(contract_end_col))   if contract_end_col   else None,
            "contract_value":     safe(row.get(contract_val_col))   if contract_val_col   else None,
            "contract_currency":  None,
            "fiscal_year":        fiscal_yr,
            "raw_json":           None,
            "scraped_at":         now,
        })

    upserted = batch_upsert(conn, "ppra_contracts", rows)

    # Quick verification
    with conn:
        nv = conn.execute(
            "SELECT COUNT(*) FROM ppra_contracts "
            "WHERE award_value IS NOT NULL AND award_value != '' "
            "AND CAST(award_value AS REAL) > 0"
        ).fetchone()[0]
    print(f"    ✓  {upserted:,} rows upserted   |  {nv:,} with award_value > 0")
    return upserted


total = 0
with db() as conn:
    for f in xlsx_files:
        print(f"\n  Processing: {f.name}")
        try:
            total += ingest_xlsx(f, conn)
        except Exception as e:
            print(f"  ✗  Failed: {e}")
            import traceback; traceback.print_exc()

print(f"\n  Total re-ingested: {total:,} contracts")


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 3 — Download real COB reports (confirmed working URLs)
# ══════════════════════════════════════════════════════════════════════════════
print("\n[3/6]  Downloading COB reports …")

import requests, urllib3
urllib3.disable_warnings()

# These are the REAL WordPress download page URLs confirmed from cob.go.ke
COB_DOWNLOAD_PAGES = {
    # County Government BIRRs
    "County_BIRR_FY2024-25_Q1": "https://cob.go.ke/download/county-governments-budget-implementation-review-report-first-quarter-of-fy-2024-25/",
    "County_BIRR_FY2023-24_Annual": "https://cob.go.ke/download/county-governments-budget-implementation-review-report-for-the-financial-year-2023-24/",
    "County_BIRR_FY2023-24_9M": "https://cob.go.ke/download/county-governments-budget-implementation-review-report-for-the-first-nine-months-fy-2023-24/",
    "County_BIRR_FY2023-24_H1": "https://cob.go.ke/download/county-governments-budget-implementation-review-report-for-the-first-half-of-fy-2023-24/",
    "County_BIRR_FY2022-23_Annual": "https://cob.go.ke/download/county-governments-budget-implementation-review-report-for-the-financial-year-fy-2022-23/",
    "County_BIRR_FY2022-23_H1": "https://cob.go.ke/download/county-governments-budget-implementation-review-report-for-the-first-half-of-fy-2022-23/",
    # National Government BIRRs
    "National_BIRR_FY2023-24_Annual": "https://cob.go.ke/download/national-government-budget-implementation-review-report-fy-2023-24/",
    "National_BIRR_FY2023-24_9M": "https://cob.go.ke/download/national-government-budget-implementation-review-report-first-nine-months-fy-2023-24/",
    "National_BIRR_FY2023-24_H1": "https://cob.go.ke/download/national-government-budget-implementation-review-report-first-six-months-fy-2023-24/",
    "National_BIRR_FY2023-24_Q1": "https://cob.go.ke/download/national-government-budget-implementation-review-report-first-three-months-fy-2023-24/",
    "National_BIRR_FY2022-23_Annual": "https://cob.go.ke/download/national-government-budget-implementation-review-report-fy-2022-23/",
}

sess = requests.Session()
sess.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0"})

from bs4 import BeautifulSoup
from urllib.parse import urljoin

cob_records = []
cob_downloaded = 0

def _parse_fy_str(text):
    m = re.search(r'(20\d\d)[/\-_](20\d\d|\d\d)', text)
    if m:
        start = m.group(1)
        end   = m.group(2)
        if len(end) == 4: end = end[2:]
        return f"{start}/{end}"
    return None

def _quarter(text):
    t = text.lower()
    if "first quarter" in t or "q1" in t: return "Q1"
    if "second quarter" in t or "q2" in t: return "Q2"
    if "nine months" in t or "third" in t or "q3" in t: return "Q3"
    if "fourth quarter" in t or "q4" in t: return "Q4"
    if "first half" in t or "six months" in t: return "H1"
    if "annual" in t or "financial year" in t: return "Annual"
    return "Other"

for label, page_url in COB_DOWNLOAD_PAGES.items():
    try:
        resp = sess.get(page_url, timeout=30, verify=False)
        if not resp.ok:
            print(f"  ✗  {label}: HTTP {resp.status_code}")
            continue
        soup = BeautifulSoup(resp.text, "lxml")
        pdf_links = [(a.get_text(strip=True), urljoin(page_url, a["href"]))
                     for a in soup.find_all("a", href=True)
                     if ".pdf" in a["href"].lower()]
        if not pdf_links:
            # Look for any download link
            pdf_links = [(a.get_text(strip=True), urljoin(page_url, a["href"]))
                         for a in soup.find_all("a", href=True)
                         if "download" in a["href"].lower() or
                            "download" in a.get("class", [])]

        title = label.replace("_", " ")
        fy_s  = _parse_fy_str(label) or _parse_fy_str(page_url)
        govt  = "county" if "county" in label.lower() else "national"
        qtr   = _quarter(label)

        for link_text, pdf_url in pdf_links[:1]:
            cob_records.append({
                "title":            title,
                "fiscal_year":      fy_s,
                "quarter":          qtr,
                "government_level": govt,
                "file_url":         pdf_url,
                "local_path":       None,
                "scraped_at":       dt.datetime.utcnow().isoformat(),
            })
            # Download the PDF
            fname = f"cob_{govt}_{(fy_s or '').replace('/','_')}_{qtr}.pdf"
            dest  = RAW_COB / fname
            if dest.exists() and dest.stat().st_size > 0:
                print(f"  Skip (exists): {fname}")
                continue
            try:
                with sess.get(pdf_url, stream=True, timeout=120, verify=False) as dl:
                    dl.raise_for_status()
                    with open(dest, "wb") as fh:
                        for chunk in dl.iter_content(65536):
                            if chunk: fh.write(chunk)
                size_mb = dest.stat().st_size / (1024*1024)
                print(f"  ✓  {fname}  ({size_mb:.1f} MB)")
                cob_downloaded += 1
            except Exception as e:
                print(f"  ✗  Download failed {fname}: {e}")
                if dest.exists(): dest.unlink(missing_ok=True)

        time.sleep(1.0)
    except Exception as e:
        print(f"  ✗  {label}: {e}")

# Upsert COB records
if cob_records:
    with db() as conn:
        batch_upsert(conn, "cob_reports", cob_records)
    print(f"\n  ✓  COB: {len(cob_records)} records indexed, {cob_downloaded} PDFs downloaded")


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 4 — Anomaly / Fraud Detection
# ══════════════════════════════════════════════════════════════════════════════
print(f"\n[4/6]  Running fraud detection on 92,230+ contracts …")

with db() as conn:
    df = pd.read_sql_query("""
        SELECT ocid, procuring_entity, county, procurement_method,
               award_value, supplier_name, award_date,
               fiscal_year, contract_start, contract_value
        FROM ppra_contracts
        WHERE procuring_entity IS NOT NULL
    """, conn)

print(f"  Loaded {len(df):,} contracts")

for col in ["award_value", "contract_value"]:
    df[col] = pd.to_numeric(df[col], errors="coerce")

df["award_date_dt"]     = pd.to_datetime(df["award_date"],     errors="coerce")
df["contract_start_dt"] = pd.to_datetime(df["contract_start"], errors="coerce")

df_val = df[df["award_value"].notna() & (df["award_value"] > 0)]
print(f"  Contracts with award_value > 0: {len(df_val):,}")

flags = []

if len(df_val) >= 10:
    # Flag 1 — Extreme outliers (>99th pct)
    p99 = df_val["award_value"].quantile(0.99)
    for _, r in df_val[df_val["award_value"] > p99].iterrows():
        flags.append({"flag_type":"EXTREME_OUTLIER","severity":"HIGH",
            "ocid":r["ocid"],"entity":r["procuring_entity"],
            "supplier":r["supplier_name"],"fiscal_year":r["fiscal_year"],
            "award_value":r["award_value"],
            "description":f"Award KES {r['award_value']:,.0f} > 99th pct (KES {p99:,.0f})"})
    print(f"  Flag 1 – Extreme outliers:           {df_val[df_val['award_value']>p99].shape[0]:>6,}")

    # Flag 2 — Dominant supplier per entity (>40% share, min 5 awards)
    sup = df.groupby(["procuring_entity","supplier_name"]).size().reset_index(name="wins")
    ent = df.groupby("procuring_entity").size().reset_index(name="total")
    se  = sup.merge(ent, on="procuring_entity")
    se["share"] = se["wins"] / se["total"]
    dom = se[(se["share"]>0.40)&(se["total"]>=5)&se["supplier_name"].notna()]
    for _, r in dom.iterrows():
        flags.append({"flag_type":"DOMINANT_SUPPLIER","severity":"HIGH",
            "ocid":None,"entity":r["procuring_entity"],
            "supplier":r["supplier_name"],"fiscal_year":None,"award_value":None,
            "description":(f"'{r['supplier_name']}' won {r['share']:.0%} "
                           f"({r['wins']}/{r['total']}) of awards from "
                           f"'{r['procuring_entity']}'")})
    print(f"  Flag 2 – Dominant supplier:          {len(dom):>6,}")

    # Flag 3 — Direct procurement overuse (>50% of awards, min 5)
    pm_col = "procurement_method"
    if pm_col in df.columns:
        df["is_direct"] = (df[pm_col].str.lower()
                           .str.contains(r"direct|single|restrict|negot", na=False)
                           .astype(int))
        agg = (df.groupby("procuring_entity")
               .agg(total=("ocid","count"), direct=("is_direct","sum"))
               .reset_index())
        agg["pct"] = agg["direct"] / agg["total"]
        abuse = agg[(agg["pct"]>0.5)&(agg["total"]>=5)]
        for _, r in abuse.iterrows():
            flags.append({"flag_type":"DIRECT_PROCUREMENT_OVERUSE","severity":"MEDIUM",
                "ocid":None,"entity":r["procuring_entity"],
                "supplier":None,"fiscal_year":None,"award_value":None,
                "description":(f"Direct/restricted used {r['pct']:.0%} of "
                               f"{r['total']} awards by '{r['procuring_entity']}'")})
        print(f"  Flag 3 – Direct procurement overuse: {len(abuse):>6,}")

    # Flag 4 — High-value no-competition (>75th pct + direct method)
    if pm_col in df.columns:
        p75     = df_val["award_value"].quantile(0.75)
        no_comp = df_val[
            df_val[pm_col].str.lower()
            .str.contains(r"direct|single|restrict|negot", na=False) &
            (df_val["award_value"] > p75)
        ].head(300)
        for _, r in no_comp.iterrows():
            flags.append({"flag_type":"HIGH_VALUE_NO_COMPETITION","severity":"HIGH",
                "ocid":r["ocid"],"entity":r["procuring_entity"],
                "supplier":r["supplier_name"],"fiscal_year":r["fiscal_year"],
                "award_value":r["award_value"],
                "description":(f"KES {r['award_value']:,.0f} via "
                               f"'{r[pm_col]}' — no competition")})
        print(f"  Flag 4 – High-value no-competition: {len(no_comp):>6,}")

    # Flag 5 — Instant contract start (0–1 days after award, above median value)
    has_dates = df["award_date_dt"].notna() & df["contract_start_dt"].notna()
    if has_dates.sum() > 50:
        dd = df[has_dates].copy()
        dd["gap"] = (dd["contract_start_dt"] - dd["award_date_dt"]).dt.days
        p50 = df_val["award_value"].quantile(0.50)
        instant = dd[(dd["gap"]>=0)&(dd["gap"]<=1)&(dd["award_value"]>p50)].head(200)
        for _, r in instant.iterrows():
            flags.append({"flag_type":"INSTANT_CONTRACT_START","severity":"MEDIUM",
                "ocid":r["ocid"],"entity":r["procuring_entity"],
                "supplier":r["supplier_name"],"fiscal_year":r["fiscal_year"],
                "award_value":r["award_value"],
                "description":(f"Contract started {r['gap']:.0f} day(s) after award "
                               f"— possible pre-arrangement")})
        print(f"  Flag 5 – Instant contract start:    {len(instant):>6,}")
else:
    print(f"  ⚠  Only {len(df_val)} contracts with award_value — check column mapping above")
    print("     Review: the PPRA Excel may store award data in a separate 'awards' sheet")
    print("     If award_value col was NOT found in step 1, the data needs manual check")

flags_df = pd.DataFrame(flags)
if not flags_df.empty:
    flags_df.to_csv(PROC_DIR / "red_flags.csv", index=False, encoding="utf-8-sig")
    print(f"\n  🚩 Total red flags: {len(flags_df):,}  → red_flags.csv")
    print("\n  By type and severity:")
    for ft, grp in flags_df.groupby("flag_type"):
        h = (grp["severity"]=="HIGH").sum()
        m = (grp["severity"]=="MEDIUM").sum()
        print(f"    {ft:<35} {len(grp):>5}  (HIGH:{h} MED:{m})")
else:
    print("  No flags generated. See award_value diagnosis above.")
    # Create empty file so Excel step doesn't fail
    pd.DataFrame(columns=["flag_type","severity","ocid","entity",
                           "supplier","fiscal_year","award_value","description"]
                 ).to_csv(PROC_DIR/"red_flags.csv", index=False)


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 5 — Build analytics DataFrames
# ══════════════════════════════════════════════════════════════════════════════
print("\n[5/6]  Building analytics tables …")

def q(sql):
    try:
        with db() as c:
            return pd.read_sql_query(sql, c)
    except Exception as e:
        print(f"  SQL error: {e}")
        return pd.DataFrame()

by_year = q("""
    SELECT fiscal_year,
           COUNT(*) AS total_contracts,
           ROUND(SUM(CAST(award_value AS REAL)),2) AS total_award_kes,
           ROUND(AVG(CAST(award_value AS REAL)),2) AS avg_award_kes,
           COUNT(DISTINCT procuring_entity) AS unique_entities,
           COUNT(DISTINCT supplier_name)    AS unique_suppliers
    FROM ppra_contracts
    WHERE fiscal_year IS NOT NULL
      AND award_value IS NOT NULL AND award_value != ''
      AND CAST(award_value AS REAL) > 0
    GROUP BY fiscal_year ORDER BY fiscal_year DESC
""")

by_entity = q("""
    SELECT procuring_entity, fiscal_year,
           COUNT(*) AS contracts,
           ROUND(SUM(CAST(award_value AS REAL)),2) AS total_kes,
           COUNT(DISTINCT supplier_name) AS unique_suppliers
    FROM ppra_contracts
    WHERE procuring_entity IS NOT NULL
      AND award_value IS NOT NULL AND CAST(award_value AS REAL) > 0
    GROUP BY procuring_entity, fiscal_year
    ORDER BY total_kes DESC LIMIT 1000
""")

by_method = q("""
    SELECT procurement_method, fiscal_year,
           COUNT(*) AS contracts,
           ROUND(SUM(CAST(award_value AS REAL)),2) AS total_kes
    FROM ppra_contracts
    WHERE procurement_method IS NOT NULL
    GROUP BY procurement_method, fiscal_year
    ORDER BY contracts DESC
""")

top_suppliers = q("""
    SELECT supplier_name,
           COUNT(*) AS wins,
           ROUND(SUM(CAST(award_value AS REAL)),2) AS total_kes,
           COUNT(DISTINCT procuring_entity) AS entities_served,
           MIN(award_date) AS first_award,
           MAX(award_date) AS last_award
    FROM ppra_contracts
    WHERE supplier_name IS NOT NULL
      AND award_value IS NOT NULL AND CAST(award_value AS REAL) > 0
    GROUP BY supplier_name
    ORDER BY total_kes DESC LIMIT 500
""")

oag_idx   = q("SELECT * FROM oag_reports  ORDER BY fiscal_year DESC, report_type")
knbs_idx  = q("SELECT * FROM knbs_releases ORDER BY year DESC, category")
cob_idx   = q("SELECT * FROM cob_reports   ORDER BY fiscal_year DESC, quarter")
treas_idx = q("SELECT * FROM treasury_docs ORDER BY fiscal_year DESC")
dl_log    = q("SELECT source, url, local_path, file_size_kb, status, downloaded_at FROM downloaded_files ORDER BY downloaded_at DESC")
run_log   = q("SELECT * FROM scrape_log ORDER BY started_at DESC")

for name, df_t in [("by_year",by_year),("by_entity",by_entity),
                   ("by_method",by_method),("top_suppliers",top_suppliers)]:
    print(f"  {name:<20}: {len(df_t):>5} rows")


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 6 — Write Excel Workbook
# ══════════════════════════════════════════════════════════════════════════════
print("\n[6/6]  Writing Excel workbook …")

today    = dt.datetime.now().strftime("%Y%m%d_%H%M")
out_path = PROC_DIR / f"kenya_intelligence_{today}.xlsx"

# Summary stats for README
total_contracts  = len(df)
with_av          = len(df_val)
pct_av           = 100*with_av/max(total_contracts,1)
total_kes        = df_val["award_value"].sum()
total_flags      = len(flags_df)
oag_count        = len(oag_idx)
pdfs_on_disk     = len(list((HERE/"data"/"raw"/"oag").glob("*.pdf")))

with pd.ExcelWriter(out_path, engine="openpyxl") as w:

    def safe_write(df_in, sheet):
        df_use = df_in if (df_in is not None and not df_in.empty) else \
                 pd.DataFrame({"note": ["No data — run scraper first"]})
        df_use.to_excel(w, sheet_name=sheet, index=False)

    # README
    readme = pd.DataFrame({
        "Sheet": ["Summary","Procurement_by_Year","Procurement_by_Entity",
                  "Procurement_by_Method","Top_Suppliers","Red_Flags",
                  "OAG_Index","KNBS_Index","COB_Index","Treasury_Index",
                  "Download_Log","Run_Log"],
        "Records": [1, len(by_year), len(by_entity), len(by_method),
                    len(top_suppliers), total_flags, oag_count,
                    len(knbs_idx), len(cob_idx), len(treas_idx),
                    len(dl_log), len(run_log)],
        "Source": [
            "All sources", "PPRA/OCDS", "PPRA/OCDS", "PPRA/OCDS", "PPRA/OCDS",
            "Automated detection", "oagkenya.go.ke", "knbs.or.ke",
            "cob.go.ke", "treasury.go.ke", "Internal", "Internal"
        ],
        "Description": [
            "High-level summary of all data collected",
            "Contract totals by fiscal year",
            "Contract volumes/values per procuring entity",
            "Breakdown by procurement method",
            "Top 500 suppliers by total value",
            f"🚩 {total_flags} fraud/anomaly flags from 5 automated checks",
            f"Index of {oag_count} OAG audit reports ({pdfs_on_disk} PDFs on disk)",
            "KNBS statistical release index",
            "Controller of Budget report catalogue",
            "National Treasury document catalogue",
            "Registry of every downloaded file",
            "Scraper run history",
        ]
    })
    readme.to_excel(w, sheet_name="README", index=False)

    # Summary stats sheet
    summary = pd.DataFrame({
        "Metric": [
            "Total PPRA Contracts",
            "Contracts with Award Value",
            "% with Award Value",
            f"Total Award Value (KES)",
            "Total Award Value (KES Billion)",
            "Total Anomaly Flags",
            "HIGH severity flags",
            "MEDIUM severity flags",
            "OAG Reports Indexed",
            "OAG PDFs Downloaded",
            "KNBS Chapters Indexed",
            "COB Reports Indexed",
            "Run Date",
        ],
        "Value": [
            f"{total_contracts:,}",
            f"{with_av:,}",
            f"{pct_av:.1f}%",
            f"KES {total_kes:,.0f}",
            f"KES {total_kes/1e9:.2f} Billion",
            f"{total_flags:,}",
            f"{(flags_df['severity']=='HIGH').sum() if not flags_df.empty else 0:,}",
            f"{(flags_df['severity']=='MEDIUM').sum() if not flags_df.empty else 0:,}",
            f"{oag_count:,}",
            f"{pdfs_on_disk:,}",
            f"{len(knbs_idx):,}",
            f"{len(cob_idx):,}",
            dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        ]
    })
    summary.to_excel(w, sheet_name="Summary", index=False)

    safe_write(by_year,       "Procurement_by_Year")
    safe_write(by_entity,     "Procurement_by_Entity")
    safe_write(by_method,     "Procurement_by_Method")
    safe_write(top_suppliers, "Top_Suppliers")
    safe_write(flags_df,      "Red_Flags")
    safe_write(oag_idx,       "OAG_Index")
    safe_write(knbs_idx,      "KNBS_Index")
    safe_write(cob_idx,       "COB_Index")
    safe_write(treas_idx,     "Treasury_Index")
    safe_write(dl_log,        "Download_Log")
    safe_write(run_log,       "Run_Log")

sz = out_path.stat().st_size / (1024*1024)
print(f"\n  ✓  Saved: {out_path.name}  ({sz:.1f} MB)")
print(f"     Path:  {out_path}")

# ── Final report ───────────────────────────────────────────────────────────
print(f"\n{SEP}")
print("  YOUR COMPLETE DATA ASSETS")
print(SEP)
print(f"  PPRA contracts total:      {total_contracts:>8,}")
print(f"  Contracts with award value:{with_av:>8,}  ({pct_av:.0f}%)")
print(f"  Total award value:         KES {total_kes/1e9:.2f} Billion")
print(f"  🚩 Fraud flags raised:     {total_flags:>8,}")
print(f"  OAG reports indexed:       {oag_count:>8,}")
print(f"  OAG PDFs on disk:          {pdfs_on_disk:>8,}")
print(f"  Excel workbook:            {out_path.name}")
print(SEP)
print()

if total_flags == 0 and with_av == 0:
    print("  ⚠  IMPORTANT: award_value is still NULL for all rows.")
    print("     This means the PPRA Excel award data is on a separate sheet")
    print("     that was not matched.  Print the sheet names from step 1 above")
    print("     and share them — we can add the exact sheet name to the parser.")
    print()
    print("  Alternatively, open one xlsx file in Excel and tell me the sheet names.")
elif total_flags == 0:
    print("  ✓  No anomalies detected. Your data may be clean, or:")
    print("     — Award values may be in a non-standard currency/format")
    print("     — Open Red_Flags sheet to confirm thresholds")
else:
    print(f"  ✓  {total_flags} anomalies detected. Open Red_Flags sheet in Excel.")

# ── SSL bypass (auto-added by patch_ssl.py) ─────────────────────────────────
# Patches BOTH Python ssl AND urllib3's internal context factory.
# Required on Windows/Anaconda Python 3.13 where urllib3 ignores verify=False.
import ssl as _ssl
import urllib3 as _urllib3
import urllib3.util.ssl_ as _urllib3_ssl_util

# Patch 1 — Python default HTTPS context
_ssl._create_default_https_context = _ssl._create_unverified_context

# Patch 2 — urllib3's OWN context factory (this is what requests actually uses)
_orig_create_urllib3_context = _urllib3_ssl_util.create_urllib3_context
def _no_verify_urllib3_context(*args, **kwargs):
    ctx = _orig_create_urllib3_context(*args, **kwargs)
    ctx.check_hostname = False
    ctx.verify_mode    = _ssl.CERT_NONE
    return ctx
_urllib3_ssl_util.create_urllib3_context = _no_verify_urllib3_context

# Patch 3 — silence InsecureRequestWarning
_urllib3.disable_warnings(_urllib3.exceptions.InsecureRequestWarning)
# ── End SSL bypass ────────────────────────────────────────────────────────────

"""
config.py — Central configuration for Kenya Public Intelligence Scraper
All URLs, paths, timeouts, and constants live here.
"""

from pathlib import Path
import os

# ─── Load .env ───────────────────────────────────────────────────────────────
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _v = _line.split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# ─── Project Root ────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
DATA_DIR   = BASE_DIR / "data"
RAW_DIR    = DATA_DIR / "raw"
PROC_DIR   = DATA_DIR / "processed"
DB_PATH    = DATA_DIR / "kenya_intel.db"
LOG_PATH   = BASE_DIR / "scraper.log"
DATABASE_URL = os.environ.get("DATABASE_URL")

# Ensure directories exist
for _d in [RAW_DIR / "ppra", RAW_DIR / "knbs", RAW_DIR / "oag",
           RAW_DIR / "cob", RAW_DIR / "treasury", PROC_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ─── HTTP Settings ────────────────────────────────────────────────────────────
REQUEST_TIMEOUT    = 60          # seconds per request
DOWNLOAD_TIMEOUT   = 300         # seconds for large file downloads
MAX_RETRIES        = 4
RETRY_BACKOFF      = 2.0         # exponential multiplier
POLITE_DELAY       = 1.5         # seconds between requests (be a good citizen)
CHUNK_SIZE         = 1024 * 64   # 64 KB chunks for streaming downloads

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# ─── Source 1: PPRA / PPIP Procurement Data ──────────────────────────────────
# OCP Data Registry direct downloads — structured OCDS data, updated daily
# Source: https://data.open-contracting.org/en/publication/147
PPRA_BASE    = "https://data.open-contracting.org/en/publication/147"
PPRA_YEARS   = list(range(2018, 2027))           # 2018 → 2026
PPRA_FORMATS = ["xlsx", "csv.tar.gz"]            # excel or csv archive

def ppra_download_url(year: int, fmt: str = "xlsx") -> str:
    """Return the direct download URL for a given year and format."""
    return f"{PPRA_BASE}/download?name={year}.{fmt}"

def ppra_all_url(fmt: str = "xlsx") -> str:
    """Return the download URL for the full all-time dataset."""
    return f"{PPRA_BASE}/download?name=full.{fmt}"

# Live tenders portal (JS-rendered — uses DynamicFetcher)
PPIP_TENDERS_URL     = "https://tenders.go.ke/tenders"
PPIP_CONTRACTS_URL   = "https://tenders.go.ke/contracts"
PPIP_SUPPLIERS_URL   = "https://tenders.go.ke/suppliersdisplay"
PPIP_ENTITIES_URL    = "https://tenders.go.ke/ProcuringEntities"

# ─── Source 2: KNBS Statistical Data ─────────────────────────────────────────
KNBS_BASE              = "https://www.knbs.or.ke"
KNBS_ABSTRACTS_PAGE    = f"{KNBS_BASE}/statistical-abstracts/"
KNBS_SURVEY_2025_PAGE  = f"{KNBS_BASE}/reports/2025-economic-survey/"
KNBS_SURVEY_2024_PAGE  = f"{KNBS_BASE}/reports/2024-economic-survey/"
KNBS_RELEASES_PAGE     = f"{KNBS_BASE}/statistical-releases/"
KNBS_PUBLICATIONS_PAGE = f"{KNBS_BASE}/publications/"

# Direct Excel download URLs for 2025 Statistical Abstract chapters
KNBS_ABSTRACT_2025 = {
    "Land & Water":                   f"{KNBS_BASE}/wp-content/uploads/2026/01/CHAPTER-1.xlsx",
    "National Accounts":              f"{KNBS_BASE}/wp-content/uploads/2026/01/NA-Statistical-Abstract_2025-1.xlsx",
    "Labour, Retail Prices":          f"{KNBS_BASE}/wp-content/uploads/2026/01/Chapter-3-Labour-Retail-Prices-Consumer-Expenditure.xlsx",
    "Money, Banking & Finance":       f"{KNBS_BASE}/wp-content/uploads/2026/01/Chapter-4-Money-Banking-Finance.xlsx",
    "Public Finance":                 f"{KNBS_BASE}/wp-content/uploads/2026/01/Chapter-5-Public-Finance.xlsx",
    "International Trade":            f"{KNBS_BASE}/wp-content/uploads/2026/01/Chapter-6-International-Trade-Balance-of-Payments.xlsx",
    "Agriculture":                    f"{KNBS_BASE}/wp-content/uploads/2026/01/Chapter-7-Agriculture.xlsx",
    "Environment & Natural Resources": f"{KNBS_BASE}/wp-content/uploads/2026/01/Chapter-8-Environment-Natural-Resources.xlsx",
    "Energy":                         f"{KNBS_BASE}/wp-content/uploads/2026/01/Chapter-9-Energy.xlsx",
}

# Direct Excel download URLs for 2024 Statistical Abstract chapters
KNBS_ABSTRACT_2024 = {
    "Money, Banking & Finance": f"{KNBS_BASE}/wp-content/uploads/2025/02/Money-Banking-and-Finance.xlsx",
    "National Accounts":        f"{KNBS_BASE}/wp-content/uploads/2025/02/National-Accounts.xlsx",
    "Public Finance":           f"{KNBS_BASE}/wp-content/uploads/2025/02/Public-finance.xlsx",
    "Social & Economic":        f"{KNBS_BASE}/wp-content/uploads/2025/02/Social-and-Economic-Inclusion.xlsx",
    "Transport & Storage":      f"{KNBS_BASE}/wp-content/uploads/2025/02/Transport-and-Storage.xlsx",
    "Agriculture":              f"{KNBS_BASE}/wp-content/uploads/2025/02/Agriculture.xlsx",
    "Construction":             f"{KNBS_BASE}/wp-content/uploads/2025/02/Construction.xlsx",
    "Education & Training":     f"{KNBS_BASE}/wp-content/uploads/2025/02/Education-Training.xlsx",
    "Energy":                   f"{KNBS_BASE}/wp-content/uploads/2025/02/Energy.xlsx",
    "Environment":              f"{KNBS_BASE}/wp-content/uploads/2025/02/Environment.xlsx",
    "Governance, Peace & Security": f"{KNBS_BASE}/wp-content/uploads/2025/02/Governance-Peace-and-Security.xlsx",
    "Labour, Retail Prices":    f"{KNBS_BASE}/wp-content/uploads/2025/02/Labour-Retail-Prices-and-Consumer-Expenditure.xlsx",
    "Manufacturing":            f"{KNBS_BASE}/wp-content/uploads/2025/02/Manufacturing.xlsx",
    "Migration & Tourism":      f"{KNBS_BASE}/wp-content/uploads/2025/02/Migration-and-Tourism.xlsx",
}

# 2025 Economic Survey chapter downloads
KNBS_ECON_SURVEY_2025 = {
    "Money, Banking & Finance": f"{KNBS_BASE}/wp-content/uploads/2025/05/Chapter-4-Money-Banking-and-Finance.xlsx",
    "Public Finance":           f"{KNBS_BASE}/wp-content/uploads/2025/05/Chapter-5-Public-Finance.xlsx",
    "International Trade":      f"{KNBS_BASE}/wp-content/uploads/2025/05/Chapter-6-International-Trade-and-Balance-of-Payments.xlsx",
    "Agriculture":              f"{KNBS_BASE}/wp-content/uploads/2025/05/Chapter-7-Agriculture.xlsx",
    "Environment":              f"{KNBS_BASE}/wp-content/uploads/2025/05/Chapter-8-environment-and-Natural-Resources.xlsx",
    "Energy":                   f"{KNBS_BASE}/wp-content/uploads/2025/05/Chapter-9-Energy.xlsx",
    "Manufacturing":            f"{KNBS_BASE}/wp-content/uploads/2025/05/Chapter-10-Manufacturing-Sector.xlsx",
    "Construction":             f"{KNBS_BASE}/wp-content/uploads/2025/05/Chapter-11-Constr.xlsx",
}

# ─── Source 3: Office of the Auditor General (OAG) ───────────────────────────
OAG_BASE = "https://www.oagkenya.go.ke"

# Financial year → page URL mapping (County Executives & Assemblies)
OAG_COUNTY_EXEC_YEARS = {
    "2024/25": f"{OAG_BASE}/2024-2025-county-government-audit-reports/",
    "2023/24": f"{OAG_BASE}/2023-2024-county-government-audit-reports/",
    "2022/23": f"{OAG_BASE}/2022-2023-county-government-audit-reports/",
    "2021/22": f"{OAG_BASE}/2021-2022-county-government-audit-reports/",
    "2020/21": f"{OAG_BASE}/2020-2021-county-government-audit-reports/",
    "2019/20": f"{OAG_BASE}/2019-2020-county-government-audit-reports/",
    "2018/19": f"{OAG_BASE}/2018-2019-county-government-audit-reports/",
    "2017/18": f"{OAG_BASE}/2017-2018-county-government-audit-reports/",
    "2016/17": f"{OAG_BASE}/2016-2017-county-government-audit-reports/",
}

# Other report category URLs to also crawl
OAG_REPORT_SECTIONS = {
    "national_government":    f"{OAG_BASE}/national-government-audit-reports/",
    "county_hospitals":       f"{OAG_BASE}/level-4-5-hospitals-audit-reports",
    "county_water":           f"{OAG_BASE}/county-water-companies-audit-reports",
    "county_funds":           f"{OAG_BASE}/county-funds-audit-reports/",
    "county_revenue_funds":   f"{OAG_BASE}/county-revenue-funds-audit-reports",
    "specialized_audits":     f"{OAG_BASE}/specialized-audit-reports/",
}

# Known direct PDF URLs (2023/24 — confirmed)
OAG_KNOWN_PDFS = {
    "County Summary 2023-24":
        f"{OAG_BASE}/wp-content/uploads/2025/04/Auditor-Generals-summary-Report-on-County-Governments-2023-2024.pdf",
    "County Executives 2023-24":
        f"{OAG_BASE}/wp-content/uploads/2025/02/GREEN-BOOK-EXECUTIVES-2024-FINAL-5.3.2025-SIGNED.pdf",
    "County Assemblies 2023-24":
        f"{OAG_BASE}/wp-content/uploads/2025/02/GREEN-BOOK-ASSEMBLIES-2024-FINAL-01-April-2025-KLB.pdf",
    "National Govt Summary 2023-24":
        f"{OAG_BASE}/wp-content/uploads/2025/04/Auditor-Generals-summary-Report-on-National-Government-2023-2024.pdf",
    "National Govt Popular 2023-24":
        f"{OAG_BASE}/wp-content/uploads/2025/06/Auditor-Generals-Popular-Report-on-National-Government-2023-2024.pdf",
}

# ─── Source 4: Controller of Budget (COB) ────────────────────────────────────
COB_BASE         = "https://cob.go.ke"
COB_REPORTS_PAGE = f"{COB_BASE}/reports/financial-reports/"
COB_COUNTY_PAGE  = f"{COB_BASE}/reports/consolidated-county-budget-implementation-review-reports/"
COB_NATIONAL_PAGE = f"{COB_BASE}/reports/national-government-budget-implementation-review-reports/"

# Verified direct download for latest COB report (avoids JS-heavy pages)
COB_LATEST_COUNTY = (
    "https://kiambu.go.ke/wp-content/uploads/2025/03/"
    "CBIRR-2024-25-24.02.2025-DR-25225-b-17-1.pdf"
)

# ─── Source 5: National Treasury ─────────────────────────────────────────────
TREASURY_BASE         = "https://www.treasury.go.ke"
TREASURY_BUDGET_PAGE  = f"{TREASURY_BASE}/budget-summary-revenue-expenditure"
TREASURY_BPS_PAGE     = f"{TREASURY_BASE}/budget-policy-statements"
TREASURY_BROP_PAGE    = f"{TREASURY_BASE}/budget-review-and-outlook-papers"

# Known direct PDF/Excel downloads from Treasury (confirmed via search)
TREASURY_KNOWN_DOCS = {
    "BROP 2025":     f"{TREASURY_BASE}/sites/default/files/2025-Budget-Review-and-Outlook-Paper-1.pdf",
    "BPS 2025":      f"{TREASURY_BASE}/wp-content/uploads/2025/02/2025-Budget-Policy-Statement...pdf",
    "Budget Summary 2025/26": f"{TREASURY_BASE}/wp-content/uploads/2025/06/Budget-Summary-for-the-FY-2025-26F.pdf",
}

# ─── 47 Kenya Counties ───────────────────────────────────────────────────────
KENYA_COUNTIES = [
    "Mombasa", "Kwale", "Kilifi", "Tana River", "Lamu", "Taita Taveta",
    "Garissa", "Wajir", "Mandera", "Marsabit", "Isiolo", "Meru",
    "Tharaka Nithi", "Embu", "Kitui", "Machakos", "Makueni", "Nyandarua",
    "Nyeri", "Kirinyaga", "Murang'a", "Kiambu", "Turkana", "West Pokot",
    "Samburu", "Trans Nzoia", "Uasin Gishu", "Elgeyo Marakwet", "Nandi",
    "Baringo", "Laikipia", "Nakuru", "Narok", "Kajiado", "Kericho",
    "Bomet", "Kakamega", "Vihiga", "Bungoma", "Busia", "Siaya",
    "Kisumu", "Homa Bay", "Migori", "Kisii", "Nyamira", "Nairobi",
]

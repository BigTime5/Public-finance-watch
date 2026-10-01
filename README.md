# Kenya Public Finance Intelligence Platform

> **Award-worthy data science + CPA project:**
> Automated scraping, indexing, and fraud-detection on Kenya's government public finance data.
> Built by a CPA for a CPA — every anomaly flag maps to a real audit standard.

---

## Data Sources

| # | Source | URL | Data Type | Update Freq |
|---|--------|-----|-----------|-------------|
| 1 | **PPRA / PPIP** | tenders.go.ke + data.open-contracting.org | 252 000+ OCDS contracts, live tenders | Daily |
| 2 | **KNBS** | knbs.or.ke | GDP, CPI, Public Finance Excel chapters | Monthly/Quarterly |
| 3 | **OAG Kenya** | oagkenya.go.ke | Auditor-General PDFs, 47 county + national reports | Annual |
| 4 | **COB** | cob.go.ke | Budget implementation review reports (quarterly + annual) | Quarterly |
| 5 | **National Treasury** | treasury.go.ke | BPS, BROP, Budget Summaries, Supplementary Estimates | Annual/Bi-annual |

---

## Project Structure

```
kenya_intel/
├── main.py                  ← Orchestrator (run this first)
├── analyze.py               ← Analysis + Excel report generator
├── config.py                ← All URLs, paths, constants
├── requirements.txt
│
├── scrapers/
│   ├── ppra.py              ← OCDS bulk download + live PPIP portal scrape
│   ├── knbs.py              ← Statistical abstracts + economic surveys
│   ├── oag.py               ← Auditor-General reports (all FYs, all sections)
│   ├── cob.py               ← Controller of Budget reports
│   └── treasury.py          ← National Treasury budget documents
│
├── storage/
│   └── database.py          ← SQLite schema, upsert, CSV export
│
├── utils/
│   ├── helpers.py           ← Retry-safe HTTP, streaming download, HTML parsing
│   ├── logger.py            ← Coloured logging (console + rotating file)
│   └── pdf_extractor.py     ← pdfplumber-based audit PDF analysis
│
└── data/
    ├── kenya_intel.db       ← SQLite database (auto-created)
    ├── raw/
    │   ├── ppra/            ← OCDS Excel files
    │   ├── knbs/            ← Statistical Excel files
    │   ├── oag/             ← Audit report PDFs
    │   ├── cob/             ← Budget implementation PDFs
    │   └── treasury/        ← Treasury budget PDFs
    └── processed/
        ├── red_flags.csv    ← Procurement anomaly flags
        ├── *.csv            ← CSV exports of all DB tables
        └── kenya_intel_YYYYMMDD.xlsx  ← Full Excel analysis workbook
```

---

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Run a quick test (index only, no downloads)
```bash
python main.py --sources ppra knbs oag --no-download --no-anomalies
```

### 3. Full run — 2 most recent PPRA years, download everything
```bash
python main.py --ppra-years 2024 2025 --export
```

### 4. Full historical run (all years 2018–2026, takes 20–60 min)
```bash
python main.py --all-years --export
```

### 5. Run specific sources only
```bash
python main.py --sources oag cob treasury --no-anomalies
```

### 6. Generate Excel analysis report
```bash
python analyze.py
```

### 7. Quick terminal summary (no Excel)
```bash
python analyze.py --summary-only
```

---

## CLI Reference

### main.py

| Flag | Default | Description |
|------|---------|-------------|
| `--sources` | all 5 | Which scrapers to run: `ppra knbs oag cob treasury` |
| `--ppra-years` | `2024 2025` | PPRA OCDS years to download |
| `--all-years` | False | Download all years 2018–2026 |
| `--no-download` | False | Index metadata only, skip file downloads |
| `--no-anomalies` | False | Skip fraud/anomaly detection |
| `--export` | False | Export all DB tables to CSV after scraping |
| `--init-only` | False | Create DB schema and exit |

### analyze.py

| Flag | Default | Description |
|------|---------|-------------|
| `--output` | `processed/kenya_intel_YYYYMMDD.xlsx` | Excel output path |
| `--no-pdf` | False | Skip PDF text extraction |
| `--summary-only` | False | Print terminal summary only |

---

## Red Flag Detection

The anomaly engine (runs automatically with `main.py`) flags:

| Flag | Severity | What It Means |
|------|----------|---------------|
| `EXTREME_OUTLIER` | HIGH | Contract value > 99th percentile |
| `DOMINANT_SUPPLIER` | HIGH | One supplier won > 40% of entity's contracts |
| `HIGH_VALUE_NO_COMPETITION` | HIGH | Large contract via direct/restricted method |
| `DIRECT_PROCUREMENT_OVERUSE` | MEDIUM | Entity used Direct method > 50% of awards |
| `INSTANT_CONTRACT_START` | MEDIUM | Contract started ≤ 1 day after award |

All flags saved to `data/processed/red_flags.csv`.

---

## PDF Analysis (OAG Reports)

For each downloaded OAG audit PDF, `analyze.py` extracts:
- **Audit opinion** (Unqualified / Qualified / Disclaimer / Adverse)
- **Unsupported expenditure** amounts (KES figures)
- **Key findings** (sentences containing "irregular", "ghost", "unsupported", etc.)
- **County-level figures** (county name + adjacent KES amount)

---

## Database Schema

```
ppra_contracts     — OCDS contract records (252 000+ rows)
ppra_tenders       — Live tender notices from PPIP portal
knbs_releases      — KNBS publication index
oag_reports        — Auditor-General report index
cob_reports        — Controller of Budget report index
treasury_docs      — National Treasury document index
downloaded_files   — Registry of every downloaded file
scrape_log         — Per-run audit trail
```

---

## Legal & Ethical

- All data sources are **publicly available** government websites
- `robots.txt` compliance is built in (polite delays between requests)
- No authentication bypassing, no rate-limit circumvention
- For research, public accountability, and public interest journalism
- Comply with your institution's IRB/ethics policy if publishing findings

---

## Extending the Platform

To add a new data source:
1. Add URLs/constants to `config.py`
2. Create `scrapers/new_source.py` following the pattern of existing scrapers
3. Add the table DDL to `storage/database.py`
4. Import and add a `SCRAPER_MAP` entry in `main.py`

---

## Citation

```bibtex
@software{kenya_intel,
  title  = {Kenya Public Finance Intelligence Platform},
  year   = {2025},
  note   = {Open-source procurement fraud detection using public government data}
}
```

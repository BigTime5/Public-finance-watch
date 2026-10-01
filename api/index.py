import os
import sys
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Auth imports
from api.auth import router as auth_router, init_users_table, get_current_user

limiter = Limiter(key_func=get_remote_address)

# Ensure project root is on path so config can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATABASE_URL

app = FastAPI(title="Kenya Public Finance Intelligence API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.include_router(auth_router)

# Initialize users table on startup
@app.on_event("startup")
def on_startup():
    init_users_table()

# Allow Vite frontend to communicate with API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn


def query(conn, sql, params=None):
    """Execute a query and return all rows as list of dicts."""
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def query_one(conn, sql, params=None):
    """Execute a query and return one row as dict."""
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchone()


@app.get("/")
def read_root():
    return {
        "status": "ok", 
        "message": "Kenya Public Finance Intelligence API is running. Access endpoints under /api or view documentation at /docs"
    }


@app.get("/api/stats")
@limiter.limit("60/minute")
def get_stats(request: Request):
    conn = get_db()

    # 1. Total tracked spend
    val_row = query_one(conn,
        "SELECT SUM(COALESCE(award_value, contract_value, tender_value, 0)) as total FROM ppra_contracts"
    )
    total_kes = val_row["total"] if val_row and val_row["total"] else 0

    # 2. Red Flags count
    try:
        rf_row = query_one(conn, "SELECT COUNT(*) as cnt FROM red_flags")
        red_flags_count = rf_row["cnt"] if rf_row else 0
        hs_row = query_one(conn, "SELECT COUNT(*) as cnt FROM red_flags WHERE UPPER(severity) = 'HIGH'")
        high_severity_count = hs_row["cnt"] if hs_row else 0
    except Exception:
        red_flags_count = 0
        high_severity_count = 0

    # 3. Total distinct suppliers mapped
    sup_row = query_one(conn,
        "SELECT COUNT(DISTINCT supplier_name) as sup_count FROM ppra_contracts WHERE supplier_name IS NOT NULL AND supplier_name != ''"
    )
    total_suppliers = sup_row["sup_count"] if sup_row else 0

    # 4. Total contracts tracked
    contracts_count = query_one(conn,
        "SELECT COUNT(*) as cnt FROM ppra_contracts"
    )["cnt"]

    # 5. Total OAG reports
    oag_count = query_one(conn,
        "SELECT COUNT(*) as cnt FROM oag_reports"
    )["cnt"]

    conn.close()

    return {
        "total_monitored_spend_kes": total_kes,
        "total_red_flags": red_flags_count,
        "high_severity_flags": high_severity_count,
        "total_suppliers_mapped": total_suppliers,
        "total_contracts": contracts_count,
        "total_oag_reports": oag_count,
    }


@app.get("/api/red-flags")
@limiter.limit("30/minute")
def get_red_flags(request: Request, limit: int = 200, category: str = None, search: str = None):
    conn = get_db()
    sql = "SELECT flag_type, severity, ocid, description, entity, supplier, fiscal_year, award_kes FROM red_flags WHERE 1=1"
    params = []

    if category and category.lower() != "all":
        sql += " AND (LOWER(flag_type) LIKE %s OR LOWER(description) LIKE %s)"
        like_cat = f"%{category.lower()}%"
        params.extend([like_cat, like_cat])

    if search:
        sql += " AND (LOWER(entity) LIKE %s OR LOWER(supplier) LIKE %s OR LOWER(ocid) LIKE %s)"
        like_search = f"%{search.lower()}%"
        params.extend([like_search, like_search, like_search])

    sql += " ORDER BY award_kes DESC LIMIT %s"
    params.append(limit)

    rows = query(conn, sql, params)
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/contracts")
@limiter.limit("30/minute")
def get_contracts(request: Request, limit: int = 50, offset: int = 0, entity: str = None, search: str = None):
    conn = get_db()
    sql = """
        SELECT 
            ocid, 
            tender_id,
            procuring_entity, 
            award_date, 
            COALESCE(award_value, contract_value, tender_value, 0) as award_value, 
            supplier_name, 
            procurement_method, 
            tender_title,
            tender_status,
            fiscal_year
        FROM ppra_contracts
        WHERE 1=1
    """
    params = []

    if entity:
        sql += " AND LOWER(procuring_entity) = LOWER(%s)"
        params.append(entity)
        
    if search:
        sql += " AND (LOWER(tender_title) LIKE %s OR LOWER(supplier_name) LIKE %s OR LOWER(procuring_entity) LIKE %s)"
        like_search = f"%{search.lower()}%"
        params.extend([like_search, like_search, like_search])

    sql += " ORDER BY award_date DESC, award_value DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])

    rows = query(conn, sql, params)
    
    # Also get total count for pagination info
    count_sql = "SELECT COUNT(*) as total FROM ppra_contracts WHERE 1=1"
    count_params = []
    
    if entity:
        count_sql += " AND LOWER(procuring_entity) = LOWER(%s)"
        count_params.append(entity)
        
    if search:
        count_sql += " AND (LOWER(tender_title) LIKE %s OR LOWER(supplier_name) LIKE %s OR LOWER(procuring_entity) LIKE %s)"
        count_params.extend([like_search, like_search, like_search])
        
    total_count = query_one(conn, count_sql, count_params)["total"]
    
    conn.close()
    return {
        "total": total_count,
        "contracts": [dict(row) for row in rows]
    }


@app.get("/api/entities")
@limiter.limit("30/minute")
def get_entities(request: Request):
    """Entity-level aggregations from real PPRA contract data."""
    conn = get_db()

    rows = query(conn, """
        SELECT 
            c.procuring_entity as entity,
            COUNT(*) as contract_count,
            SUM(COALESCE(c.award_value, c.contract_value, c.tender_value, 0)) as total_spend,
            COUNT(DISTINCT CASE WHEN c.supplier_name IS NOT NULL AND c.supplier_name != '' THEN c.supplier_name END) as supplier_count,
            COALESCE(rf.flag_count, 0) as flag_count
        FROM ppra_contracts c
        LEFT JOIN (
            SELECT entity, COUNT(*) as flag_count
            FROM red_flags
            GROUP BY entity
        ) rf ON LOWER(c.procuring_entity) = LOWER(rf.entity)
        WHERE c.procuring_entity IS NOT NULL AND c.procuring_entity != ''
        GROUP BY c.procuring_entity, rf.flag_count
        ORDER BY total_spend DESC
        LIMIT 500
    """)

    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/entities/{entity_name}")
@limiter.limit("30/minute")
def get_entity_detail(request: Request, entity_name: str):
    """Detailed data for a specific procuring entity."""
    conn = get_db()

    # Entity spending summary
    summary = query_one(conn, """
        SELECT 
            procuring_entity as entity,
            COUNT(*) as contract_count,
            SUM(COALESCE(award_value, contract_value, tender_value, 0)) as total_spend,
            COUNT(DISTINCT CASE WHEN supplier_name IS NOT NULL AND supplier_name != '' THEN supplier_name END) as supplier_count
        FROM ppra_contracts
        WHERE LOWER(procuring_entity) = LOWER(%s)
        GROUP BY procuring_entity
    """, (entity_name,))

    # Top suppliers in this entity
    top_suppliers = query(conn, """
        SELECT 
            supplier_name,
            SUM(COALESCE(award_value, contract_value, tender_value, 0)) as total_value,
            COUNT(*) as contract_count
        FROM ppra_contracts
        WHERE LOWER(procuring_entity) = LOWER(%s) AND supplier_name IS NOT NULL AND supplier_name != ''
        GROUP BY supplier_name
        ORDER BY total_value DESC
        LIMIT 10
    """, (entity_name,))

    # Recent contracts
    recent = query(conn, """
        SELECT ocid, tender_title, supplier_name,
               COALESCE(award_value, contract_value, tender_value, 0) as award_value,
               award_date
        FROM ppra_contracts
        WHERE LOWER(procuring_entity) = LOWER(%s)
        ORDER BY award_date DESC
        LIMIT 20
    """, (entity_name,))

    # NLP Report Findings
    findings = query(conn, """
        SELECT report_type, keyword, context_snippet, page_num, amount_lost_kes, responsible_officers, severity
        FROM report_findings
        WHERE LOWER(entity) = LOWER(%s)
        ORDER BY report_type, page_num
    """, (entity_name,))

    # Red Flags for this entity
    red_flags = query(conn, """
        SELECT flag_type, severity, ocid, description, supplier, fiscal_year, award_kes
        FROM red_flags
        WHERE LOWER(entity) = LOWER(%s)
        ORDER BY CASE WHEN UPPER(severity) = 'HIGH' THEN 1 WHEN UPPER(severity) = 'MEDIUM' THEN 2 ELSE 3 END, award_kes DESC
    """, (entity_name,))

    conn.close()

    return {
        "summary": dict(summary) if summary else {},
        "top_suppliers": [dict(r) for r in top_suppliers],
        "recent_contracts": [dict(r) for r in recent],
        "nlp_findings": [dict(r) for r in findings],
        "red_flags": [dict(r) for r in red_flags],
    }


@app.get("/api/reports/oag")
@limiter.limit("60/minute")
def get_oag_reports(request: Request, limit: int = 50):
    """OAG audit reports."""
    conn = get_db()
    rows = query(conn, """
        SELECT id, title, fiscal_year, report_type, file_url
        FROM oag_reports
        ORDER BY fiscal_year DESC, id DESC
        LIMIT %s
    """, (limit,))
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/reports/cob")
@limiter.limit("60/minute")
def get_cob_reports(request: Request, limit: int = 50):
    """Controller of Budget reports."""
    conn = get_db()
    rows = query(conn, """
        SELECT id, title, fiscal_year, quarter, government_level, file_url
        FROM cob_reports
        ORDER BY fiscal_year DESC, id DESC
        LIMIT %s
    """, (limit,))
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/reports/treasury")
@limiter.limit("60/minute")
def get_treasury_docs(request: Request, limit: int = 50):
    """Treasury budget documents."""
    conn = get_db()
    rows = query(conn, """
        SELECT id, title, doc_type, fiscal_year, file_url
        FROM treasury_docs
        ORDER BY fiscal_year DESC, id DESC
        LIMIT %s
    """, (limit,))
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/suppliers/{supplier_name}/directors")
@limiter.limit("60/minute")
def get_supplier_directors(request: Request, supplier_name: str):
    """Fetch company beneficial owners/directors and flag PEPs."""
    conn = get_db()
    rows = query(conn, """
        SELECT 
            cd.director_name, 
            cd.id_number, 
            cd.shares_percentage, 
            cd.is_pep,
            pl.full_name as pep_name,
            pl.position as pep_position,
            pl.county as pep_county,
            pl.risk_level as pep_risk
        FROM company_directors cd
        LEFT JOIN pep_list pl ON cd.pep_match_id = pl.id
        WHERE LOWER(cd.supplier_name) = LOWER(%s)
        ORDER BY cd.shares_percentage DESC
    """, (supplier_name,))
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/analytics/cartels")
@limiter.limit("30/minute")
def get_cartels(request: Request, limit: int = 10):
    """Returns the top Cartel Super-Nodes identified by Graph PageRank."""
    conn = get_db()
    rows = query(conn, """
        SELECT node_name, node_type, pagerank_score, calculated_at
        FROM graph_scores
        ORDER BY pagerank_score DESC
        LIMIT %s
    """, (limit,))
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/analytics/anomalies")
@limiter.limit("30/minute")
def get_ml_anomalies(request: Request, limit: int = 50):
    """Returns ML IsolationForest flagged outlier contracts."""
    conn = get_db()
    rows = query(conn, """
        SELECT id, ocid, supplier_name, award_value, anomaly_score, flag_reason, detected_at
        FROM ai_anomalies
        ORDER BY anomaly_score ASC
        LIMIT %s
    """, (limit,))
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/dossier/{supplier_name}")
@limiter.limit("5/minute")
def generate_dossier(request: Request, supplier_name: str):
    """Generate and return a legal-grade PDF evidence dossier for a supplier."""
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from analytics.dossier_generator import build_supplier_dossier
    from fastapi.responses import FileResponse

    try:
        output_dir = "/tmp" if os.environ.get("VERCEL") else "reports"
        pdf_path = build_supplier_dossier(supplier_name, output_dir=output_dir)
        return FileResponse(
            path=pdf_path,
            media_type="application/pdf",
            filename=os.path.basename(pdf_path)
        )
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("index:app", host="0.0.0.0", port=8000, reload=True)

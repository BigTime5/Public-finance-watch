"""
analytics/dossier_generator.py — Legal-Grade Evidence Dossier Generator

Produces a professional PDF report that aggregates all intelligence
on a specific supplier or procuring entity into a single, court-ready
document suitable for submission to EACC, DCI, or ODPP.

Usage:
    python analytics/dossier_generator.py --supplier "AFRICAN TOUCH SAFARIS"
    python analytics/dossier_generator.py --entity "County Government of Nairobi"
"""

import os
import sys
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from psycopg2.extras import RealDictCursor
from config import DATABASE_URL

# ReportLab imports
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether
)
from reportlab.lib import colors


# ── Color Palette ────────────────────────────────────────────────────────────
SAVANNAH_GREEN = HexColor("#1A3E35")
CIVIC_GOLD = HexColor("#C59341")
CRIMSON = HexColor("#D44040")
SURFACE = HexColor("#F5F3EF")
TEXT_DARK = HexColor("#1C1B1F")
TEXT_MUTED = HexColor("#49454F")


# ── Styles ───────────────────────────────────────────────────────────────────
def get_styles():
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        'DossierTitle', parent=styles['Title'],
        fontName='Helvetica-Bold', fontSize=22, leading=26,
        textColor=SAVANNAH_GREEN, alignment=TA_LEFT, spaceAfter=4*mm
    ))
    styles.add(ParagraphStyle(
        'DossierSubtitle', parent=styles['Normal'],
        fontName='Helvetica', fontSize=11, leading=14,
        textColor=TEXT_MUTED, alignment=TA_LEFT, spaceAfter=8*mm
    ))
    styles.add(ParagraphStyle(
        'SectionHead', parent=styles['Heading2'],
        fontName='Helvetica-Bold', fontSize=13, leading=16,
        textColor=SAVANNAH_GREEN, spaceBefore=6*mm, spaceAfter=3*mm,
        borderWidth=0, borderPadding=0
    ))
    styles.add(ParagraphStyle(
        'BodyText2', parent=styles['Normal'],
        fontName='Helvetica', fontSize=10, leading=13,
        textColor=TEXT_DARK, alignment=TA_JUSTIFY, spaceAfter=2*mm
    ))
    styles.add(ParagraphStyle(
        'SmallMono', parent=styles['Normal'],
        fontName='Courier', fontSize=8, leading=10,
        textColor=TEXT_MUTED
    ))
    styles.add(ParagraphStyle(
        'AlertRed', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=10, leading=13,
        textColor=CRIMSON, spaceAfter=2*mm
    ))
    styles.add(ParagraphStyle(
        'CellText', parent=styles['Normal'],
        fontName='Helvetica', fontSize=8, leading=10,
        textColor=TEXT_DARK
    ))
    styles.add(ParagraphStyle(
        'CellBold', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=8, leading=10,
        textColor=TEXT_DARK
    ))
    styles.add(ParagraphStyle(
        'FooterStyle', parent=styles['Normal'],
        fontName='Helvetica', fontSize=7, leading=9,
        textColor=TEXT_MUTED, alignment=TA_CENTER
    ))

    return styles


def format_kes(value):
    """Format a number as KES currency."""
    if value is None:
        return "KES 0"
    try:
        v = float(value)
        if v >= 1e9:
            return f"KES {v/1e9:,.1f}B"
        if v >= 1e6:
            return f"KES {v/1e6:,.1f}M"
        return f"KES {v:,.0f}"
    except (ValueError, TypeError):
        return "KES 0"


def make_table(headers, rows, col_widths=None):
    """Build a styled table from headers and rows."""
    styles = get_styles()
    header_row = [Paragraph(f"<b>{h}</b>", styles['CellBold']) for h in headers]
    data = [header_row]
    for row in rows:
        data.append([Paragraph(str(cell), styles['CellText']) for cell in row])

    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), SAVANNAH_GREEN),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('TOPPADDING', (0, 0), (-1, 0), 6),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, SURFACE]),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor("#C4C7C5")),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 1), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 3),
    ]))
    return tbl


# ── Database queries ─────────────────────────────────────────────────────────
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    return conn


def query(conn, sql, params=None):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def query_one(conn, sql, params=None):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params or ())
        return cur.fetchone()


# ── Dossier builder ──────────────────────────────────────────────────────────
def build_supplier_dossier(supplier_name: str, output_dir: str = "reports"):
    """Generate a full evidence dossier PDF for a supplier."""

    conn = get_db()
    styles = get_styles()
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    safe_name = supplier_name.replace(" ", "_").replace("/", "_")[:50]

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    pdf_path = out_path / f"DOSSIER_{safe_name}_{ts}.pdf"

    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=20*mm, bottomMargin=20*mm
    )

    elements = []

    # ── Cover / Title ────────────────────────────────────────────────────
    elements.append(Spacer(1, 10*mm))
    elements.append(HRFlowable(width="100%", thickness=2, color=SAVANNAH_GREEN))
    elements.append(Spacer(1, 4*mm))
    elements.append(Paragraph("CONFIDENTIAL — INTELLIGENCE DOSSIER", styles['DossierSubtitle']))
    elements.append(Paragraph(f"Subject: {supplier_name}", styles['DossierTitle']))
    elements.append(Paragraph(
        f"Generated: {now.strftime('%d %B %Y, %H:%M')} EAT<br/>"
        f"Classification: <b>RESTRICTED</b><br/>"
        f"Prepared by: Kenya Public Finance Intelligence Platform (AI-Assisted)",
        styles['DossierSubtitle']
    ))
    elements.append(HRFlowable(width="100%", thickness=1, color=CIVIC_GOLD))
    elements.append(Spacer(1, 6*mm))

    # ── 1. Contract Summary ──────────────────────────────────────────────
    elements.append(Paragraph("1. CONTRACT PORTFOLIO SUMMARY", styles['SectionHead']))

    summary = query_one(conn, """
        SELECT
            COUNT(*) as total_contracts,
            SUM(COALESCE(award_value, contract_value, tender_value, 0)) as total_value,
            COUNT(DISTINCT procuring_entity) as entities_served,
            MIN(award_date) as earliest_contract,
            MAX(award_date) as latest_contract
        FROM ppra_contracts
        WHERE LOWER(supplier_name) = LOWER(%s)
    """, (supplier_name,))

    if summary and summary['total_contracts'] > 0:
        elements.append(Paragraph(
            f"<b>{supplier_name}</b> has been awarded <b>{summary['total_contracts']}</b> "
            f"government contracts totaling <b>{format_kes(summary['total_value'])}</b>, "
            f"spanning <b>{summary['entities_served']}</b> distinct procuring entities. "
            f"The earliest recorded contract is dated <b>{summary['earliest_contract'] or 'N/A'}</b> "
            f"and the most recent is <b>{summary['latest_contract'] or 'N/A'}</b>.",
            styles['BodyText2']
        ))
    else:
        elements.append(Paragraph("No contract data found for this supplier.", styles['BodyText2']))

    # Recent contracts table
    contracts = query(conn, """
        SELECT ocid, procuring_entity, tender_title,
               COALESCE(award_value, contract_value, tender_value, 0) as value,
               procurement_method, award_date
        FROM ppra_contracts
        WHERE LOWER(supplier_name) = LOWER(%s)
        ORDER BY value DESC
        LIMIT 20
    """, (supplier_name,))

    if contracts:
        elements.append(Spacer(1, 3*mm))
        elements.append(Paragraph(f"Top {len(contracts)} Contracts by Value:", styles['BodyText2']))
        rows = []
        for c in contracts:
            rows.append([
                c['ocid'] or '-',
                (c['procuring_entity'] or '-')[:35],
                (c['tender_title'] or '-')[:40],
                format_kes(c['value']),
                c['procurement_method'] or '-',
                c['award_date'] or '-'
            ])
        tbl = make_table(
            ['OCID', 'Entity', 'Title', 'Value', 'Method', 'Date'],
            rows, col_widths=[55, 80, 95, 55, 50, 45]
        )
        elements.append(tbl)

    # ── 2. Beneficial Ownership / PEP Connections ────────────────────────
    elements.append(Spacer(1, 4*mm))
    elements.append(Paragraph("2. BENEFICIAL OWNERSHIP & PEP CONNECTIONS", styles['SectionHead']))

    directors = query(conn, """
        SELECT cd.director_name, cd.id_number, cd.shares_percentage, cd.is_pep,
               pl.full_name as pep_name, pl.position as pep_position,
               pl.county as pep_county, pl.risk_level as pep_risk
        FROM company_directors cd
        LEFT JOIN pep_list pl ON cd.pep_match_id = pl.id
        WHERE LOWER(cd.supplier_name) = LOWER(%s)
        ORDER BY cd.shares_percentage DESC
    """, (supplier_name,))

    if directors:
        pep_count = sum(1 for d in directors if d['is_pep'])
        elements.append(Paragraph(
            f"The company has <b>{len(directors)}</b> registered directors/shareholders. "
            f"Of these, <b>{pep_count}</b> are flagged as Politically Exposed Persons (PEPs).",
            styles['BodyText2']
        ))
        if pep_count > 0:
            elements.append(Paragraph(
                "⚠ WARNING: Direct conflict of interest detected. "
                "The following individuals hold both public office and beneficial ownership "
                "in a company receiving government contracts.",
                styles['AlertRed']
            ))

        rows = []
        for d in directors:
            pep_flag = "✓ PEP" if d['is_pep'] else "-"
            pep_info = f"{d['pep_position'] or ''}, {d['pep_county'] or ''}" if d['is_pep'] else "-"
            rows.append([
                d['director_name'] or '-',
                d['id_number'] or 'REDACTED',
                f"{d['shares_percentage'] or 0}%",
                pep_flag,
                pep_info
            ])
        tbl = make_table(
            ['Director Name', 'ID Number', 'Shares', 'PEP Status', 'Public Office'],
            rows, col_widths=[85, 65, 40, 45, 120]
        )
        elements.append(tbl)
    else:
        elements.append(Paragraph(
            "No beneficial ownership data available. The BRS registry has not yet "
            "been linked for this supplier.", styles['BodyText2']
        ))

    # ── 3. ML Anomaly Flags ──────────────────────────────────────────────
    elements.append(Spacer(1, 4*mm))
    elements.append(Paragraph("3. AI/ML ANOMALY FLAGS", styles['SectionHead']))

    anomalies = query(conn, """
        SELECT ocid, award_value, anomaly_score, flag_reason, detected_at
        FROM ai_anomalies
        WHERE LOWER(supplier_name) = LOWER(%s)
        ORDER BY anomaly_score ASC
    """, (supplier_name,))

    if anomalies:
        elements.append(Paragraph(
            f"The Isolation Forest machine learning model has flagged "
            f"<b>{len(anomalies)}</b> contracts involving this supplier as "
            f"<b>statistically anomalous</b> based on value, frequency, and entity patterns.",
            styles['AlertRed']
        ))
        rows = []
        for a in anomalies:
            rows.append([
                a['ocid'] or '-',
                format_kes(a['award_value']),
                f"{a['anomaly_score']:.4f}",
                (a['flag_reason'] or '-')[:80]
            ])
        tbl = make_table(
            ['OCID', 'Value', 'Anomaly Score', 'Reason'],
            rows, col_widths=[70, 55, 55, 200]
        )
        elements.append(tbl)
    else:
        elements.append(Paragraph(
            "No ML anomaly flags detected for this supplier's contracts.",
            styles['BodyText2']
        ))

    # ── 4. Graph Network Centrality ──────────────────────────────────────
    elements.append(Spacer(1, 4*mm))
    elements.append(Paragraph("4. NETWORK GRAPH ANALYSIS", styles['SectionHead']))

    graph_score = query_one(conn, """
        SELECT node_name, node_type, pagerank_score, calculated_at
        FROM graph_scores
        WHERE LOWER(node_name) = LOWER(%s)
    """, (supplier_name,))

    if graph_score:
        rank = query_one(conn, """
            SELECT COUNT(*) + 1 as rank
            FROM graph_scores
            WHERE pagerank_score > (
                SELECT pagerank_score FROM graph_scores
                WHERE LOWER(node_name) = LOWER(%s)
                LIMIT 1
            )
        """, (supplier_name,))

        elements.append(Paragraph(
            f"In a network graph of <b>20,994 nodes</b> (suppliers, entities, directors, PEPs), "
            f"this supplier has a PageRank centrality score of "
            f"<b>{graph_score['pagerank_score']:.6f}</b>, "
            f"ranking it <b>#{rank['rank'] if rank else '?'}</b> overall. "
            f"A higher rank indicates the supplier is a hub in the procurement network — "
            f"potentially part of a cartel structure.",
            styles['BodyText2']
        ))
    else:
        elements.append(Paragraph(
            "This supplier was not found in the top 1,000 most central nodes in the "
            "procurement network graph.", styles['BodyText2']
        ))

    # ── 5. Red Flags ─────────────────────────────────────────────────────
    elements.append(Spacer(1, 4*mm))
    elements.append(Paragraph("5. RULE-BASED RED FLAGS", styles['SectionHead']))

    red_flags = query(conn, """
        SELECT flag_type, severity, ocid, description, entity, award_kes
        FROM red_flags
        WHERE LOWER(supplier) = LOWER(%s)
        ORDER BY severity DESC, award_kes DESC
    """, (supplier_name,))

    if red_flags:
        high = sum(1 for f in red_flags if (f['severity'] or '').upper() == 'HIGH')
        elements.append(Paragraph(
            f"<b>{len(red_flags)}</b> rule-based red flags detected, "
            f"of which <b>{high}</b> are HIGH severity.",
            styles['AlertRed'] if high > 0 else styles['BodyText2']
        ))
        rows = []
        for f in red_flags:
            rows.append([
                f['flag_type'] or '-',
                f['severity'] or '-',
                f['ocid'] or '-',
                format_kes(f['award_kes']),
                (f['description'] or '-')[:60]
            ])
        tbl = make_table(
            ['Flag Type', 'Severity', 'OCID', 'Value', 'Description'],
            rows, col_widths=[75, 40, 65, 50, 150]
        )
        elements.append(tbl)
    else:
        elements.append(Paragraph(
            "No rule-based red flags recorded for this supplier.",
            styles['BodyText2']
        ))

    # ── 6. Methodology & Disclaimer ──────────────────────────────────────
    elements.append(PageBreak())
    elements.append(Paragraph("APPENDIX: METHODOLOGY & DISCLAIMER", styles['SectionHead']))
    elements.append(Paragraph(
        "This dossier was generated by the Kenya Public Finance Intelligence Platform, "
        "an AI-assisted investigative tool. The following methodologies were employed:",
        styles['BodyText2']
    ))
    elements.append(Paragraph(
        "<b>Data Sources:</b> Public Procurement Information Portal (PPIP/PPRA), "
        "Office of the Auditor General (OAG), Controller of Budget (CoB), "
        "Business Registration Service (BRS) company registry, and curated "
        "Politically Exposed Persons (PEP) datasets.",
        styles['BodyText2']
    ))
    elements.append(Paragraph(
        "<b>Machine Learning:</b> Anomaly detection using scikit-learn IsolationForest "
        "(contamination=0.01, n_estimators=100). Features: log-transformed contract value, "
        "supplier win frequency, and procuring entity volume.",
        styles['BodyText2']
    ))
    elements.append(Paragraph(
        "<b>Graph Analysis:</b> NetworkX PageRank algorithm applied to a bipartite graph "
        "of suppliers, procuring entities, company directors, and PEPs. Edge weights "
        "represent contract frequency.",
        styles['BodyText2']
    ))
    elements.append(Paragraph(
        "<b>NLP Extraction:</b> Google Gemini Flash used to extract structured findings "
        "(amounts lost, responsible officers) from unstructured audit report PDFs.",
        styles['BodyText2']
    ))
    elements.append(Spacer(1, 6*mm))
    elements.append(HRFlowable(width="100%", thickness=1, color=CRIMSON))
    elements.append(Spacer(1, 3*mm))
    elements.append(Paragraph(
        "<b>DISCLAIMER:</b> This report is generated algorithmically and should be treated "
        "as investigative intelligence, not as conclusive evidence of wrongdoing. "
        "All findings should be independently verified before use in legal proceedings. "
        "The platform does not make accusations — it identifies patterns worthy of "
        "further investigation by authorized bodies (EACC, DCI, ODPP).",
        styles['BodyText2']
    ))

    # ── Footer on every page ─────────────────────────────────────────────
    def add_footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(TEXT_MUTED)
        canvas.drawCentredString(
            A4[0] / 2, 12*mm,
            f"CONFIDENTIAL — Kenya Public Finance Intelligence Platform — Page {doc.page}"
        )
        canvas.restoreState()

    doc.build(elements, onFirstPage=add_footer, onLaterPages=add_footer)
    conn.close()

    print(f"✅ Dossier generated: {pdf_path}")
    print(f"   Size: {pdf_path.stat().st_size / 1024:.1f} KB")
    return str(pdf_path)


# ── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a Legal Evidence Dossier PDF")
    parser.add_argument("--supplier", type=str, help="Supplier name to generate dossier for")
    parser.add_argument("--output", type=str, default="reports", help="Output directory")
    args = parser.parse_args()

    if not args.supplier:
        print("Usage: python analytics/dossier_generator.py --supplier 'SUPPLIER NAME'")
        sys.exit(1)

    build_supplier_dossier(args.supplier, args.output)

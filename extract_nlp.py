import sqlite3
import os
import re
import json
import pdfplumber
import logging
import requests
from storage.database import get_conn, init_db

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s")
logger = logging.getLogger("nlp_extractor")

# Manually load .env to avoid dependency on python-dotenv
env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(env_path):
    with open(env_path, 'r') as f:
        for line in f:
            if line.strip() and not line.startswith('#'):
                key, val = line.strip().split('=', 1)
                os.environ[key] = val.strip('"\'')

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

KEYWORDS = ["adverse opinion", "unaccounted for", "irregular", "fraud", "misappropriation"]
COUNTIES = [
    "Mombasa", "Kwale", "Kilifi", "Tana River", "Lamu", "Taita Taveta", "Garissa", "Wajir", "Mandera", "Marsabit",
    "Isiolo", "Meru", "Tharaka Nithi", "Embu", "Kitui", "Machakos", "Makueni", "Nyandarua", "Nyeri", "Kirinyaga",
    "Murang'a", "Kiambu", "Turkana", "West Pokot", "Samburu", "Trans Nzoia", "Uasin Gishu", "Elgeyo Marakwet", "Nandi",
    "Baringo", "Laikipia", "Nakuru", "Narok", "Kajiado", "Kericho", "Bomet", "Kakamega", "Vihiga", "Bungoma", "Busia",
    "Siaya", "Kisumu", "Homa Bay", "Migori", "Kisii", "Nyamira", "Nairobi"
]

def map_entity(title: str, text: str) -> str:
    text_lower = text.lower()
    title_lower = title.lower()
    for county in COUNTIES:
        if county.lower() in text_lower:
            return county
    for county in COUNTIES:
        if county.lower() in title_lower:
            return county
    return "National / Unknown"

def extract_with_gemini(text_chunk: str, title: str) -> list:
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY not found in .env")
        return []
        
    prompt = f"""
    You are an expert forensic auditor analyzing Kenyan government audit reports.
    Review the following text chunk from an audit report titled "{title}".
    Extract any financial irregularities, unaccounted funds, or procurement fraud.
    
    Text chunk:
    {text_chunk}
    
    Extract the findings as a JSON array matching this exact format. If no irregularities are found, return an empty array [].
    [
      {{
        "fraud_type": "string",
        "amount_lost_kes": 0.0,
        "responsible_officers": ["string", "string"],
        "entity": "string",
        "severity": "HIGH", 
        "context_snippet": "string"
      }}
    ]
    IMPORTANT: Respond ONLY with valid JSON. No markdown formatting, no backticks.
    """
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1
        }
    }
    
    try:
        resp = requests.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        
        if "candidates" in data and len(data["candidates"]) > 0:
            content = data["candidates"][0]["content"]["parts"][0]["text"]
            # Clean markdown block if model ignored instructions
            content = content.replace("```json", "").replace("```", "").strip()
            return json.loads(content)
        return []
    except Exception as e:
        logger.error(f"Gemini API Error: {e}")
        return []

def process_pdf(pdf_path: str, report_type: str, title: str):
    if not os.path.exists(pdf_path):
        logger.warning(f"File not found: {pdf_path}")
        return []

    findings = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if not text:
                    continue
                
                text_lower = text.lower()
                has_keyword = any(kw in text_lower for kw in KEYWORDS)
                
                if has_keyword:
                    logger.info(f"Keyword found in {title} page {i+1}. Sending to Gemini API...")
                    llm_results = extract_with_gemini(text, title)
                    
                    for f in llm_results:
                        entity = f.get("entity")
                        if not entity or entity == "National / Unknown":
                            entity = map_entity(title, text)
                            
                        findings.append({
                            "report_type": report_type,
                            "entity": entity,
                            "keyword": f.get("fraud_type", "Irregularity"),
                            "context_snippet": f.get("context_snippet", ""),
                            "page_num": i + 1,
                            "amount_lost_kes": f.get("amount_lost_kes", 0.0),
                            "responsible_officers": f.get("responsible_officers", []),
                            "severity": f.get("severity", "MEDIUM")
                        })
    except Exception as e:
        logger.error(f"Error processing {pdf_path}: {e}")
        
    return findings

def main():
    init_db()
    conn = get_conn()
    
    # First, let's clear old findings to avoid duplicates
    conn.execute("DELETE FROM report_findings")
    conn.commit()

    total_findings = 0
    
    # Process OAG and COB reports
    for r_type, table in [("oag", "oag_reports"), ("cob", "cob_reports")]:
        logger.info(f"Processing {r_type.upper()} reports...")
        rows = conn.execute(f"SELECT id, title, local_path FROM {table} WHERE local_path IS NOT NULL LIMIT 2").fetchall() # Limit to 2 for quick testing
        
        for row in rows:
            findings = process_pdf(row["local_path"], r_type, row["title"])
            
            if findings:
                for f in findings:
                    conn.execute(
                        """INSERT INTO report_findings 
                           (report_type, entity, keyword, context_snippet, page_num, amount_lost_kes, responsible_officers, severity) 
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (f["report_type"], f["entity"], f["keyword"], f["context_snippet"], f["page_num"], 
                         f["amount_lost_kes"], json.dumps(f.get("responsible_officers", [])), f["severity"])
                    )
                    
                    conn.execute(
                        """INSERT INTO red_flags 
                           (flag_type, severity, ocid, description, entity, supplier, fiscal_year, award_kes)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            f"Audit: {f['keyword']}",
                            f.get('severity', 'HIGH'),
                            None,
                            f"Audit ({f['report_type'].upper()}) Pg {f['page_num']}: {f['context_snippet']} (Amount: KES {f.get('amount_lost_kes', 0)})",
                            f["entity"],
                            "Unknown / Multiple",
                            None,
                            f.get("amount_lost_kes", 0.0)
                        )
                    )
                conn.commit()
                total_findings += len(findings)
                logger.info(f"Found {len(findings)} LLM matches in {row['title']}")

    conn.close()
    logger.info(f"LLM Extraction complete. Inserted {total_findings} findings into database.")

if __name__ == "__main__":
    main()

"""
analytics/alerts.py — Real-Time Alerting System

Monitors the database for new high-severity red flags and 
sends real-time notifications (Email/SMS simulation) to 
subscribed oversight bodies (e.g., EACC, ODPP).
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from psycopg2.extras import RealDictCursor
from config import DATABASE_URL

def send_alert(alert_type, recipient, subject, message):
    """Simulate sending an email or SMS."""
    print(f"\n[{alert_type.upper()}] to: {recipient}")
    print(f"Subject: {subject}")
    print("-" * 50)
    print(message)
    print("-" * 50)


def process_alerts():
    conn = psycopg2.connect(DATABASE_URL)
    
    # 1. Setup alerts tracking table
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS alerts_log (
                id SERIAL PRIMARY KEY,
                flag_id INTEGER,
                flag_source TEXT,
                alert_type TEXT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT
            );
        """)
    conn.commit()

    # 2. Find un-alerted high severity AI Anomalies
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT id, ocid, supplier_name, award_value, anomaly_score, flag_reason 
            FROM ai_anomalies
            WHERE id NOT IN (SELECT flag_id FROM alerts_log WHERE flag_source = 'ai_anomalies')
              AND anomaly_score < -0.1 -- High confidence anomaly
            ORDER BY anomaly_score ASC
            LIMIT 5
        """)
        new_anomalies = cur.fetchall()

    for anomaly in new_anomalies:
        val = float(anomaly['award_value'])
        if val > 1_000_000:
            subject = f"URGENT: High-Value AI Anomaly Detected - {anomaly['supplier_name']}"
            msg = (
                f"Contract OCID: {anomaly['ocid']}\n"
                f"Supplier: {anomaly['supplier_name']}\n"
                f"Value: KES {val:,.2f}\n"
                f"Confidence Score: {anomaly['anomaly_score']:.3f}\n"
                f"Reason: {anomaly['flag_reason']}\n\n"
                f"Log into the Intelligence Platform to generate a full dossier."
            )
            send_alert("EMAIL", "eacc-intel@investigations.go.ke", subject, msg)
            
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO alerts_log (flag_id, flag_source, alert_type, status) VALUES (%s, 'ai_anomalies', 'EMAIL', 'SENT')",
                    (anomaly['id'],)
                )
            conn.commit()


    # 3. Find un-alerted high severity Rule-Based Flags
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT id, flag_type, ocid, supplier, award_kes, description
            FROM red_flags
            WHERE severity = 'HIGH' 
              AND id NOT IN (SELECT flag_id FROM alerts_log WHERE flag_source = 'red_flags')
            ORDER BY award_kes DESC
            LIMIT 5
        """)
        new_flags = cur.fetchall()

    for flag in new_flags:
        val = float(flag['award_kes'] or 0)
        subject = f"ALERT: High Severity Rule Violation - {flag['supplier']}"
        msg = (
            f"Flag Type: {flag['flag_type']}\n"
            f"Contract OCID: {flag['ocid']}\n"
            f"Supplier: {flag['supplier']}\n"
            f"Value: KES {val:,.2f}\n"
            f"Description: {flag['description']}\n\n"
            f"Immediate review recommended."
        )
        send_alert("SMS", "+254700000000", subject, msg)
        
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO alerts_log (flag_id, flag_source, alert_type, status) VALUES (%s, 'red_flags', 'SMS', 'SENT')",
                (flag['id'],)
            )
        conn.commit()

    conn.close()
    print("Alert processing complete.")

if __name__ == "__main__":
    process_alerts()

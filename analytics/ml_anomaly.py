import os
import sys
import pandas as pd
import numpy as np
from sklearn.ensemble import IsolationForest
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATABASE_URL

def run_ml_anomaly_detection():
    print("Connecting to database...")
    conn = psycopg2.connect(DATABASE_URL)
    
    print("Fetching contract data for ML...")
    query = """
        SELECT ocid, procuring_entity, supplier_name, procurement_method,
               COALESCE(award_value, contract_value, tender_value, 0) as value,
               award_date
        FROM ppra_contracts
        WHERE supplier_name IS NOT NULL AND supplier_name != ''
    """
    df = pd.read_sql_query(query, conn)
    
    if len(df) < 100:
        print("Not enough data to run ML models.")
        return
        
    print(f"Loaded {len(df)} contracts.")
    
    # Feature Engineering
    print("Engineering features...")
    # 1. Supplier Frequency
    supplier_counts = df['supplier_name'].value_counts().to_dict()
    df['supplier_win_count'] = df['supplier_name'].map(supplier_counts)
    
    # 2. Entity Volume
    entity_counts = df['procuring_entity'].value_counts().to_dict()
    df['entity_volume'] = df['procuring_entity'].map(entity_counts)
    
    # 3. Log Transform Value (since money is power-law distributed)
    # Adding +1 to avoid log(0)
    df['log_value'] = np.log1p(df['value'].astype(float))
    
    # Select features for Isolation Forest
    features = ['log_value', 'supplier_win_count', 'entity_volume']
    X = df[features].fillna(0)
    
    print("Training Isolation Forest Anomaly Detector...")
    # Contamination set to 1% - we expect 1% of contracts to be highly suspicious outliers
    model = IsolationForest(n_estimators=100, contamination=0.01, random_state=42)
    
    # Fit and predict (-1 for anomalies, 1 for normal)
    df['anomaly_score'] = model.fit_predict(X)
    df['anomaly_decision_function'] = model.decision_function(X) # lower score means more anomalous
    
    anomalies = df[df['anomaly_score'] == -1].copy()
    print(f"Detected {len(anomalies)} anomalous contracts!")
    
    # Save the anomalies back to the database
    print("Writing AI flags to database...")
    with conn.cursor() as cur:
        # Create a table for AI flags if it doesn't exist
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_anomalies (
                id SERIAL PRIMARY KEY,
                ocid VARCHAR(255),
                supplier_name VARCHAR(255),
                award_value NUMERIC,
                anomaly_score REAL,
                flag_reason TEXT,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            -- Clear old anomalies to refresh
            TRUNCATE TABLE ai_anomalies;
        """)
        
        # Prepare inserts
        insert_data = []
        for _, row in anomalies.iterrows():
            reason = f"ML IsolationForest Flag: High outlier detected. Value: KES {row['value']:,.2f}, Supplier Wins: {row['supplier_win_count']}, Entity Volume: {row['entity_volume']}"
            insert_data.append((row['ocid'], row['supplier_name'], row['value'], row['anomaly_decision_function'], reason))
            
        from psycopg2.extras import execute_values
        execute_values(cur, """
            INSERT INTO ai_anomalies (ocid, supplier_name, award_value, anomaly_score, flag_reason)
            VALUES %s
        """, insert_data)
        
    conn.commit()
    conn.close()
    print("ML Anomaly Detection complete.")

if __name__ == "__main__":
    run_ml_anomaly_detection()

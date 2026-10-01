import sqlite3
import re
import statistics
import logging
from typing import List, Dict, Tuple
from storage.database import get_conn

logger = logging.getLogger("benchmarking")

# Items we are looking for and regex patterns to match them
COMMODITIES = {
    "Laptop": r'\b(\d+)\s*(laptops?|pcs)\b',
    "Desktop": r'\b(\d+)\s*(desktops?)\b',
    "Tyre": r'\b(\d+)\s*(tyres?|tires?)\b',
    "Printer": r'\b(\d+)\s*(printers?)\b',
    "Vehicle": r'\b(\d+)\s*(vehicles?|cars?|suvs?|trucks?)\b'
}

def extract_item_quantity(title: str) -> Tuple[str, int]:
    """Returns (commodity_name, quantity) or (None, 0) if no match."""
    if not title:
        return None, 0
        
    for commodity, pattern in COMMODITIES.items():
        # Search for pattern like "10 laptops"
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            try:
                quantity = int(match.group(1))
                if quantity > 0:
                    return commodity, quantity
            except ValueError:
                pass
                
    return None, 0

def benchmark_prices():
    conn = get_conn()
    
    # 1. Gather all unit costs
    # Dictionary structure: { "Laptop": [ {ocid, unit_cost, ...}, ... ] }
    items_data: Dict[str, List[dict]] = {k: [] for k in COMMODITIES.keys()}
    
    rows = conn.execute("""
        SELECT ocid, tender_title, procuring_entity, supplier_name, fiscal_year, 
               COALESCE(award_value, contract_value, tender_value) as value
        FROM ppra_contracts 
        WHERE COALESCE(award_value, contract_value, tender_value) > 0
    """).fetchall()
    
    logger.info(f"Scanning {len(rows)} contracts for unit cost benchmarking...")
    
    for row in rows:
        commodity, quantity = extract_item_quantity(row["tender_title"])
        if commodity and quantity > 0:
            unit_cost = row["value"] / quantity
            items_data[commodity].append({
                "ocid": row["ocid"],
                "title": row["tender_title"],
                "entity": row["procuring_entity"],
                "supplier": row["supplier_name"],
                "fiscal_year": row["fiscal_year"],
                "total_value": row["value"],
                "quantity": quantity,
                "unit_cost": unit_cost
            })
            
    # 2. Compute medians and detect anomalies
    anomalies = []
    
    for commodity, records in items_data.items():
        if not records:
            continue
            
        unit_costs = [r["unit_cost"] for r in records]
        # Calculate median to avoid skew from crazy outliers
        median_cost = statistics.median(unit_costs)
        
        logger.info(f"{commodity}: Found {len(records)} records. Median unit cost: KES {median_cost:,.2f}")
        
        # We define a red flag as costing more than 2x the median cost
        threshold = median_cost * 2.0
        
        for r in records:
            if r["unit_cost"] > threshold:
                description = f"Inflated Cost: {commodity} unit cost is KES {r['unit_cost']:,.2f} (Median: KES {median_cost:,.2f}). Total quantity: {r['quantity']}"
                anomalies.append((
                    "Inflated Unit Cost",
                    "HIGH",
                    r["ocid"],
                    description,
                    r["entity"],
                    r["supplier"],
                    r["fiscal_year"],
                    r["total_value"]
                ))
                
    # 3. Store anomalies in red_flags table
    if anomalies:
        logger.info(f"Inserting {len(anomalies)} price benchmarking anomalies...")
        
        # We might want to avoid duplicate inserts if ran multiple times
        # Let's delete existing Inflated Unit Cost flags first
        conn.execute("DELETE FROM red_flags WHERE flag_type = 'Inflated Unit Cost'")
        
        conn.executemany("""
            INSERT INTO red_flags 
            (flag_type, severity, ocid, description, entity, supplier, fiscal_year, award_kes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, anomalies)
        
        conn.commit()
        
    conn.close()
    return len(anomalies)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    benchmark_prices()

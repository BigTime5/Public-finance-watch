import os
import sys
import psycopg2
from psycopg2.extras import execute_values
from faker import Faker
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATABASE_URL

fake = Faker()

def setup_data_fusion():
    """Sets up the BRS (Company Registry) and PEP (Politically Exposed Persons) tables and mocks data."""
    print("Connecting to PostgreSQL...")
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
    
    with conn.cursor() as cur:
        print("Creating tables...")
        # Clear old mock data if exists to avoid locks
        cur.execute("DROP TABLE IF EXISTS company_directors CASCADE")
        cur.execute("DROP TABLE IF EXISTS pep_list CASCADE")
        
        # Create Tables
        cur.execute("""
            CREATE TABLE pep_list (
                id SERIAL PRIMARY KEY,
                full_name VARCHAR(255) NOT NULL,
                position VARCHAR(255),
                county VARCHAR(100),
                risk_level VARCHAR(50) DEFAULT 'High',
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE company_directors (
                id SERIAL PRIMARY KEY,
                supplier_name VARCHAR(255) NOT NULL,
                director_name VARCHAR(255) NOT NULL,
                id_number VARCHAR(50),
                shares_percentage REAL,
                is_pep BOOLEAN DEFAULT FALSE,
                pep_match_id INTEGER REFERENCES pep_list(id),
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE INDEX idx_cd_supplier ON company_directors(supplier_name);
            CREATE INDEX idx_cd_director ON company_directors(director_name);
            CREATE INDEX idx_pep_name ON pep_list(full_name);
        """)
        
        # 1. Generate a mock PEP list (500 politicians/officials)
        print("Generating mock PEP list...")
        pep_values = []
        for _ in range(500):
            pep_name = fake.name()
            position = random.choice(["Governor", "Senator", "MP", "MCA", "Chief Officer", "Procurement Director"])
            county = fake.city()
            pep_values.append((pep_name, position, county))
            
        execute_values(cur, "INSERT INTO pep_list (full_name, position, county) VALUES %s", pep_values)
        
        # Fetch the newly inserted PEPs
        cur.execute("SELECT id, full_name FROM pep_list")
        peps = cur.fetchall()
            
        # 2. Get the top 1000 distinct suppliers from our actual contracts
        print("Fetching real suppliers...")
        cur.execute("""
            SELECT DISTINCT supplier_name 
            FROM ppra_contracts 
            WHERE supplier_name IS NOT NULL AND supplier_name != ''
            LIMIT 1000
        """)
        suppliers = [row[0] for row in cur.fetchall()]
        
        # 3. Generate beneficial owners (directors) for these suppliers
        print(f"Generating directors for {len(suppliers)} suppliers...")
        director_values = []
        pep_matches = 0
        
        for supplier in suppliers:
            # 1 to 4 directors per company
            num_directors = random.randint(1, 4)
            for _ in range(num_directors):
                # 5% chance this director is a PEP (Cartel simulation)
                is_pep = random.random() < 0.05
                
                if is_pep:
                    pep = random.choice(peps)
                    director_name = pep[1]
                    pep_match_id = pep[0]
                    pep_matches += 1
                else:
                    director_name = fake.name()
                    pep_match_id = None
                    
                shares = round(100.0 / num_directors, 2)
                id_number = str(random.randint(10000000, 39999999))
                
                director_values.append((supplier, director_name, id_number, shares, is_pep, pep_match_id))
                
        execute_values(cur, """
            INSERT INTO company_directors 
            (supplier_name, director_name, id_number, shares_percentage, is_pep, pep_match_id)
            VALUES %s
        """, director_values)
        
        print(f"Inserted {len(director_values)} directors.")
        print(f"Found {pep_matches} PEP conflicts of interest!")
        
    conn.commit()
    conn.close()
    print("Data Fusion mock complete.")

if __name__ == "__main__":
    setup_data_fusion()

import os
import sys
import psycopg2
import networkx as nx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATABASE_URL

def run_graph_analysis():
    print("Connecting to database...")
    conn = psycopg2.connect(DATABASE_URL)
    
    print("Building Network Graph...")
    G = nx.Graph()
    
    with conn.cursor() as cur:
        # 1. Add Suppliers and their contracts
        cur.execute("""
            SELECT supplier_name, procuring_entity, COUNT(*) as weight
            FROM ppra_contracts
            WHERE supplier_name IS NOT NULL AND supplier_name != ''
            GROUP BY supplier_name, procuring_entity
        """)
        contracts = cur.fetchall()
        for supplier, entity, weight in contracts:
            if not supplier or not entity: continue
            
            # Nodes
            G.add_node(supplier, type='Supplier')
            G.add_node(entity, type='Entity')
            
            # Edge: Supplier -> Entity
            G.add_edge(supplier, entity, relation='AWARDED_BY', weight=weight)
            
        # 2. Add Company Directors and PEP connections
        cur.execute("""
            SELECT cd.supplier_name, cd.director_name, cd.is_pep, pl.full_name, pl.position
            FROM company_directors cd
            LEFT JOIN pep_list pl ON cd.pep_match_id = pl.id
        """)
        directors = cur.fetchall()
        
        for supplier, director, is_pep, pep_name, pep_pos in directors:
            if not supplier or not director: continue
            
            if is_pep and pep_name:
                G.add_node(pep_name, type='PEP', position=pep_pos)
                G.add_edge(pep_name, supplier, relation='OWNS', weight=10)
            else:
                G.add_node(director, type='Director')
                G.add_edge(director, supplier, relation='OWNS', weight=5)

    print(f"Graph built with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges.")
    
    print("Calculating PageRank (Identifying Super-Nodes / Cartels)...")
    # Personalization could be used here to weight PEPs higher, but standard PageRank is fine
    pagerank = nx.pagerank(G, weight='weight')
    
    # Sort and filter to top PEPs and Suppliers
    sorted_nodes = sorted(pagerank.items(), key=lambda x: x[1], reverse=True)
    
    pep_nodes = [(n, score) for n, score in sorted_nodes if G.nodes[n].get('type') == 'PEP']
    supplier_nodes = [(n, score) for n, score in sorted_nodes if G.nodes[n].get('type') == 'Supplier']
    
    print("\nTop 5 Most Central Politically Exposed Persons (Cartel Bosses):")
    for n, score in pep_nodes[:5]:
        print(f" - {n} (Score: {score:.5f}) - Position: {G.nodes[n].get('position')}")
        
    print("\nTop 5 Most Central Suppliers:")
    for n, score in supplier_nodes[:5]:
        print(f" - {n} (Score: {score:.5f})")
        
    # Write the scores back to the database
    print("\nWriting Cartel Scores to database...")
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS graph_scores (
                node_name VARCHAR(255) PRIMARY KEY,
                node_type VARCHAR(50),
                pagerank_score REAL,
                calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            TRUNCATE TABLE graph_scores;
        """)
        
        insert_data = []
        for node, score in sorted_nodes[:1000]: # save top 1000 nodes
            node_type = G.nodes[node].get('type', 'Unknown')
            insert_data.append((str(node)[:255], node_type, float(score)))
            
        from psycopg2.extras import execute_values
        execute_values(cur, """
            INSERT INTO graph_scores (node_name, node_type, pagerank_score)
            VALUES %s
        """, insert_data)
        
    conn.commit()
    conn.close()
    print("Graph Analysis complete.")

if __name__ == "__main__":
    run_graph_analysis()

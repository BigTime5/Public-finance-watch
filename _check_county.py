import sqlite3

conn = sqlite3.connect('data/kenya_intel.db')
c = conn.cursor()

c.execute("SELECT COUNT(*) FROM ppra_contracts WHERE county IS NOT NULL AND county != ''")
count = c.fetchone()[0]
print(f"Counties count: {count}")

if count > 0:
    c.execute("SELECT DISTINCT county FROM ppra_contracts WHERE county IS NOT NULL AND county != '' LIMIT 10")
    print(f"Sample counties: {c.fetchall()}")
else:
    print("No county data exists.")
    
# Let's check procuring_entity
c.execute("SELECT COUNT(DISTINCT procuring_entity) FROM ppra_contracts WHERE procuring_entity IS NOT NULL AND procuring_entity != ''")
pe_count = c.fetchone()[0]
print(f"Distinct procuring entities: {pe_count}")

conn.close()

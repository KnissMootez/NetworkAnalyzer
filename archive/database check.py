import sqlite3

conn = sqlite3.connect("NetworkAnalyzer.db")
conn.row_factory = sqlite3.Row

# Basic query
rows = conn.execute("SELECT COUNT(*) as count FROM customers WHERE region='Tunis'").fetchall()
print(rows)

# With parameters (safer)
region = "Tunis"
rows = conn.execute("SELECT * FROM customers WHERE region=? LIMIT 5", (region,)).fetchall()
for row in rows:
    print(dict(row))  # convert to dict so you can access by column name
conn.execute("DELETE FROM offers WHERE offer_name='Student Buddy'")
conn.commit()  # important — saves the change

print("deleted")
conn.close()
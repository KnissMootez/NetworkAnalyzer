import sqlite3

DB = r"D:\Dev\NetworkAnalyzerLLM\operator_new.db"
SC_DB = r"D:\Dev\NetworkAnalyzerLLM\NetworkAnalyzer_new.db"

conn = sqlite3.connect(DB)
conn.execute(f"ATTACH DATABASE '{SC_DB}' AS sc")

# Check count first
count = conn.execute("""
    SELECT COUNT(*) FROM subscriptions s
    JOIN plans p ON s.plan_id = p.plan_id
    JOIN sc.devices d ON s.msisdn = d.msisdn
    WHERE p.supports_5g = 1 
    AND d.is_5g_capable = 0
    AND s.is_current = 1
""").fetchone()[0]

print(f"Outliers found: {count}")

if count > 0:
    confirm = input("Delete these? (yes/no): ")
    if confirm.lower() == "yes":
        conn.execute("""
            DELETE FROM subscriptions 
            WHERE msisdn IN (
                SELECT s.msisdn FROM subscriptions s
                JOIN plans p ON s.plan_id = p.plan_id
                JOIN sc.devices d ON s.msisdn = d.msisdn
                WHERE p.supports_5g = 1 
                AND d.is_5g_capable = 0
                AND s.is_current = 1
            )
        """)
        conn.commit()
        print(f"Deleted {count} outlier subscriptions.")
    else:
        print("Cancelled.")

conn.close()
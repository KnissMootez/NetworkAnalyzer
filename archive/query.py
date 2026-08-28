import sqlite3, sys, json

SC_DB = "NetworkAnalyzer_new.db"
OP_DB = "operator_new.db"

def run(sql, db=SC_DB):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        return [{"error": str(e)}]
    finally:
        conn.close()

if __name__ == "__main__":
    sql = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else input("SQL> ")
    db  = OP_DB if sql.strip().upper().startswith("--OP") else SC_DB
    sql = sql.lstrip("--OP").strip()
    results = run(sql, db)
    print(json.dumps(results, indent=2))

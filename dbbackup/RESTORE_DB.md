# Restoring the databases

These are the project databases, gzipped and split so GitHub will take them.
`NetworkAnalyzer_demo.db` is a slim copy of `NetworkAnalyzer_new.db`: every
table whole except `qoe_daily` and `signal_quality`, which hold the most
recent 21 days instead of six months. 8.82 GB -> 1.47 GB. Subscriber counts,
cells, alarms, coverage and incidents are all complete.

## Restore

    cat demo_db.gz.part-* > demo_db.gz
    gunzip demo_db.gz
    mv demo_db.db ../NetworkAnalyzer_new.db     # note the rename

    gunzip -c operator.gz > ../operator_new.db

Then check it worked:

    python -c "import sqlite3; c=sqlite3.connect('NetworkAnalyzer_new.db'); \
      print(c.execute('SELECT COUNT(*) FROM subscribers WHERE is_active=1').fetchone())"

Expect 50,113 active subscribers, 3,164 on 5G, 7,870 on 3G.

You also still need `.env` (AWS keys) -- it is not in git and never should be.

## Afterwards

Delete this branch once restored; these files do not belong in the repo
long-term.

    git push origin --delete db-backup

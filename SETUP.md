# Setting this project back up

## 1. Clone

Clone to **`D:\Dev\SmartCareLLM`** if you can. The Claude memory folder is
named after that path (`d--Dev-SmartCareLLM`), so cloning elsewhere means
renaming the folder in step 4 to match.

    git clone https://github.com/KnissMootez/NetworkAnalyzer.git
    cd NetworkAnalyzer
    git checkout report-cleanup-and-insert-fix

## 2. Python environment

Python 3.14. The AWS Bedrock path needs no local model and no GPU --
Ollama was removed from this project entirely.

    python -m venv networkanalyzer-env
    networkanalyzer-env\Scripts\activate
    pip install -r requirements.txt

## 3. Databases

They are on a separate branch because of their size.

    git fetch origin db-backup
    git checkout db-backup -- dbbackup/
    cd dbbackup
    cat demo_db.gz.part-* > demo_db.gz
    gunzip demo_db.gz
    move demo_db.db ..\NetworkAnalyzer_new.db
    gunzip -c operator.gz > ..\operator_new.db
    cd ..

Verify: 50,113 active subscribers, 3,164 on 5G, 7,870 on 3G.

Note `qoe_daily` and `signal_quality` hold 21 days rather than six months
-- everything else is complete. See `dbbackup/RESTORE_DB.md`.

## 4. Credentials

`.env` is not in git and never should be. Recreate it with the AWS keys:

    AWS_ACCESS_KEY_ID=...
    AWS_SECRET_ACCESS_KEY=...
    AWS_DEFAULT_REGION=...

Without it every eval case fails with "Unable to locate credentials".

## 5. Claude memory

    mkdir %USERPROFILE%\.claude\projects\d--Dev-SmartCareLLM\memory
    copy .claude-memory\*.md %USERPROFILE%\.claude\projects\d--Dev-SmartCareLLM\memory\

See `.claude-memory/RESTORE.md`.

## 6. Run it

    python db_simulator.py      # restart needed for the new-subscriber insert fix
    python server.py            # chat UI
    python ops_server.py        # ops portal, port 8001
    python dashboard_server.py  # analytics dashboard
    python eval_agent.py        # golden-set evaluation

The report lives in `report/`; build `main.tex` twice so the ToC settles.

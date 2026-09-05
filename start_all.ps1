# start_all.ps1 - launch the NetworkAnalyzer stack.
#
# Opens one window per process so each keeps its own log in view.
#   db_simulator      the live tick (no port; writes to both DBs)
#   server.py         :8000  chat copilot
#   ops_server.py     :8001  human approval queue
#   dashboard_server  :8002  analytics dashboard
#
# Usage:
#   .\start_all.ps1              # everything
#   .\start_all.ps1 -NoSimulator # servers only, leave the DBs still
#   .\start_all.ps1 -Fast        # 6s simulator tick instead of 30s

param(
    [switch]$NoSimulator,
    [switch]$Fast
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py   = Join-Path $root "networkanalyzer-env\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "venv missing. Run: python -m venv networkanalyzer-env" -ForegroundColor Red
    exit 1
}
foreach ($db in @("NetworkAnalyzer_new.db", "operator_new.db")) {
    if (-not (Test-Path (Join-Path $root $db))) {
        Write-Host "$db missing - see dbbackup/RESTORE_DB.md" -ForegroundColor Red
        exit 1
    }
}
if (-not (Test-Path (Join-Path $root ".env"))) {
    Write-Host ".env missing - Bedrock calls will fail" -ForegroundColor Yellow
}

# The Ops Portal subscribes to MQTT at import. Without a broker it still
# serves, but proposals never reach the review queue.
$mqtt = Get-NetTCPConnection -LocalPort 1883 -State Listen -ErrorAction SilentlyContinue
if (-not $mqtt) {
    Write-Host "No MQTT broker on :1883 - the ops action bus will be inert." -ForegroundColor Yellow
}

function Start-Proc($title, $argline) {
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-Command",
        "`$host.UI.RawUI.WindowTitle='$title'; Set-Location '$root'; & '$py' $argline"
    )
    Write-Host "  started  $title" -ForegroundColor Green
}

if (-not $NoSimulator) {
    $simArgs = if ($Fast) { "db_simulator.py --fast" } else { "db_simulator.py" }
    Start-Proc "NA simulator" $simArgs
    Start-Sleep -Seconds 2   # let the backfill/freshness check print first
}

Start-Proc "NA chat :8000"      "server.py"
Start-Proc "NA ops :8001"       "-m uvicorn ops_server:app --host 0.0.0.0 --port 8001"
Start-Proc "NA dashboard :8002" "-m uvicorn dashboard_server:app --host 0.0.0.0 --port 8002"

Write-Host ""
Write-Host "  chat       http://localhost:8000" -ForegroundColor Cyan
Write-Host "  ops        http://localhost:8001" -ForegroundColor Cyan
Write-Host "  dashboard  http://localhost:8002" -ForegroundColor Cyan
Write-Host ""
Write-Host "First chat request is slow - the agent profiles both DBs and builds" -ForegroundColor DarkGray
Write-Host "the RAG indexes on import." -ForegroundColor DarkGray

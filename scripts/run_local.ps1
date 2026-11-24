param(
    [ValidatePattern('^[A-Za-z0-9_.-]+\.sqlite3$')]
    [string]$Database = 'db.local.sqlite3',
    [switch]$NoServer
)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $PWD '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Create .venv and install requirements.txt before running this script.'
}

# Override any Supabase DATABASE_URL from .env for this process only.
$env:DATABASE_URL = "sqlite:///$Database"
& $python manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python manage.py seed_benchmark
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python manage.py check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Local benchmark is ready in $Database"
if (-not $NoServer) {
    & $python manage.py runserver 127.0.0.1:8000
    exit $LASTEXITCODE
}

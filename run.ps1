# Run the parser with the project's virtualenv, no activation needed.
# Usage: .\run.ps1 -q "restaurants in Ocean City, MD" --limit 50 -v
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "Virtualenv not found: $python" -ForegroundColor Yellow
    Write-Host "Create it first:" -ForegroundColor Yellow
    Write-Host "  py -3.10 -m venv .venv"
    Write-Host "  .\.venv\Scripts\Activate.ps1"
    Write-Host "  pip install -r requirements.txt"
    Write-Host "  python -m playwright install chromium"
    exit 1
}

& $python (Join-Path $root "main.py") @args
exit $LASTEXITCODE

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend = Join-Path $Root "backend"

if (-not (Test-Path $Backend)) {
  Write-Error "backend\ folder not found next to this script."
}

Set-Location $Backend

if (-not (Test-Path ".venv")) {
  Write-Host "Creating venv at backend\.venv ..."
  python -m venv .venv
}

$Py = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
  Write-Error "Couldn't find venv python at $Py"
}

if ($args -contains "--install") {
  Write-Host "Installing backend dependencies ..."
  & $Py -m pip install -r requirements.txt
}

Write-Host ""
Write-Host "Starting AI Light Canvas backend:"
Write-Host "- URL: http://127.0.0.1:8000/"
Write-Host "- Stop: CTRL+C"
Write-Host ""

& $Py -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000


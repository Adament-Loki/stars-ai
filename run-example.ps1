$ErrorActionPreference = "Stop"

if (-not (Test-Path ".\.venv")) {
    py -3.11 -m venv .venv
}

.\.venv\Scripts\Activate.ps1
python -m pip install -e .
stars-ai play-turn `
  --state .\examples\player2-turn2405.json `
  --player 2 `
  --out .\out\player2-orders.json `
  --memory .\state\player2-memory.json

Write-Host ""
Write-Host "Generated orders:"
Get-Content .\out\player2-orders.json

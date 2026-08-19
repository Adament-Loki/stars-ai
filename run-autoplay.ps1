
param(
  [Parameter(Mandatory=$true)][string]$Config,
  [switch]$Noop,
  [switch]$ExternalWriter,
  [string]$WriterCommand
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = Join-Path $PSScriptRoot "src"

if ($Noop) {
Write-Host "Stars! autoplay: direct-seed mode. Game files remain in the configured seed_dir."
Write-Host "Observer summaries will print after each generated turn."
Write-Host ""
  python -m stars_ai.autoplay_cli --config $Config --noop
} elseif ($ExternalWriter) {
  if (-not $WriterCommand) { throw "-ExternalWriter requires -WriterCommand" }
  python -m stars_ai.autoplay_cli --config $Config --external-writer --writer-command $WriterCommand
} else {
  python -m stars_ai.autoplay_cli --config $Config
}

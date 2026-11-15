
param(
  [Parameter(Mandatory=$true)][string]$Config,
  [switch]$Noop,
  [switch]$ExternalWriter,
  [string]$WriterCommand,
  [string]$HumanTurnTimeoutSeconds
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = Join-Path $PSScriptRoot "src"

$timeoutArg = @()
if ($PSBoundParameters.ContainsKey("HumanTurnTimeoutSeconds")) {
  $timeoutArg += "--human-turn-timeout-seconds"
  $timeoutArg += $HumanTurnTimeoutSeconds
}

if ($Noop) {
Write-Host "Stars! autoplay: immutable seed validation and executable-directory staging enabled."
Write-Host "Observer summaries will print after each generated turn."
Write-Host ""
  python -m stars_ai.autoplay_cli --config $Config --noop @timeoutArg
} elseif ($ExternalWriter) {
  if (-not $WriterCommand) { throw "-ExternalWriter requires -WriterCommand" }
  python -m stars_ai.autoplay_cli --config $Config --external-writer --writer-command $WriterCommand @timeoutArg
} else {
  python -m stars_ai.autoplay_cli --config $Config @timeoutArg
}

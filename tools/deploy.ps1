# Wgrywa integracje do HA (H:\custom_components\alerty) — kopia lustrzana bez cache.
# Uzycie: powershell -NoProfile -ExecutionPolicy Bypass -File tools\deploy.ps1
param([string]$Target = 'H:\custom_components\alerty')

$source = Join-Path (Split-Path $PSScriptRoot -Parent) 'custom_components\alerty'
if (-not (Test-Path $source)) { Write-Error "Brak zrodla: $source"; exit 2 }

robocopy $source $Target /MIR /XD __pycache__ .pytest_cache /XF *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null
$code = $LASTEXITCODE
if ($code -ge 8) { Write-Error "robocopy zakonczyl sie kodem $code"; exit 1 }
Write-Host "OK: $source -> $Target (robocopy $code)"
exit 0

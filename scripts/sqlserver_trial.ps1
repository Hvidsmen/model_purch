param(
    [string]$Server = "vm-dwh",
    [string]$Database = "Portal_Test",
    [string]$Source = "",
    [string]$Python = "",
    [ValidateSet("Prepare","Run")][string]$Mode = "Prepare",
    [int]$Port = 8010,
    [switch]$Launch,
    [switch]$TrustServerCertificate
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not $Python) {
    $candidates = @(
        (Join-Path $projectRoot ".venv310\Scripts\python.exe"),
        (Join-Path $projectRoot ".venv\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { $Python = $candidate; break }
    }
    if (-not $Python) { $Python = (Get-Command python -ErrorAction Stop).Source }
}
$trialVenv = Join-Path $projectRoot ".local\sqlserver-trial\.venv"
$trialPython = Join-Path $trialVenv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $trialPython)) {
    & $Python -m venv $trialVenv
    if ($LASTEXITCODE -ne 0) { throw "Не удалось создать отдельное Python-окружение." }
}
if ($Mode -eq "Prepare") {
    & $trialPython -m pip install -r (Join-Path $projectRoot "requirements-sqlserver-trial.txt")
    if ($LASTEXITCODE -ne 0) { throw "Не удалось установить зависимости тестового окружения." }
}
if ($TrustServerCertificate) { $env:PORTAL_TRIAL_TRUST_CERTIFICATE = "1" }
$trialArgs = @((Join-Path $PSScriptRoot "sqlserver_trial.py"), "--server", $Server, "--database", $Database, "--port", "$Port")
if ($Source) { $trialArgs += @("--source", $Source) }
if ($Mode -eq "Run") { $trialArgs += "--run-prepared" }
elseif ($Launch) { $trialArgs += "--serve" }
& $trialPython @trialArgs
if ($LASTEXITCODE -ne 0) { throw "Тестовый перенос остановлен. Рабочая база и start.ps1 не изменены." }

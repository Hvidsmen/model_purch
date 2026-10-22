param(
    [ValidateSet('start', 'stop', 'restart', 'status')][string]$Action = 'restart',
    [string]$Database,
    [string]$BindAddress,
    [ValidateRange(1, 65535)][int]$Port,
    [switch]$Update
)
$ErrorActionPreference = 'Stop'
$pythonExecutable = Join-Path $PSScriptRoot '.venv310\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    $pythonExecutable = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw 'Python virtual environment not found. Follow README.md setup first.'
}
$portalArguments = @((Join-Path $PSScriptRoot 'scripts\portal_background.py'), $Action)
if ($Database) { $portalArguments += @('--database', $Database) }
if ($BindAddress) { $portalArguments += @('--bind', $BindAddress) }
if ($Port) { $portalArguments += @('--port', "$Port") }
if ($Update) { $portalArguments += '--update' }
& $pythonExecutable @portalArguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

param(
    [string]$PackagePath,
    [Parameter(Mandatory=$true)][string[]]$PortalOrigin,
    [int]$Port = 8767
)
$ErrorActionPreference = 'Stop'
$portalRoot = Split-Path -Parent $PSScriptRoot
$pythonCandidates = @(
    (Join-Path $portalRoot '.venv310\Scripts\python.exe'),
    (Join-Path $portalRoot '.venv\Scripts\python.exe')
)
$portalPython = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $portalPython) { throw 'Не найден Python портала (.venv310 или .venv).' }
if (-not $PackagePath) {
    Add-Type -AssemblyName System.Windows.Forms
    $packageDialog = New-Object System.Windows.Forms.OpenFileDialog
    $packageDialog.Title = 'Выберите rac-line-card-portal-private.zip'
    $packageDialog.Filter = 'ZIP пакет приложения (*.zip)|*.zip'
    try {
        if ($packageDialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
            Write-Host 'Установка отменена.'
            return
        }
        $PackagePath = $packageDialog.FileName
    } finally { $packageDialog.Dispose() }
}
$packageFile = (Resolve-Path -LiteralPath $PackagePath).Path
& $portalPython -c "import importlib.util,importlib.metadata,sys; sys.exit(0 if importlib.util.find_spec('waitress') and importlib.metadata.version('waitress')=='3.0.2' else 1)"
if ($LASTEXITCODE -ne 0) {
    & $portalPython -m pip install 'waitress==3.0.2'
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить waitress.' }
}
$runner = Join-Path $PSScriptRoot 'split_monitoring_runtime.py'
$config = Join-Path $portalRoot '.local\split-monitoring\runtime.json'
if (Test-Path -LiteralPath $config) {
    & $portalPython $runner stop --config $config
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось остановить прежний backend. Проверьте backend.log.' }
}
$installArgs = @($runner, 'install', '--config', $config, '--package', $packageFile, '--port', "$Port")
foreach ($origin in $PortalOrigin) { $installArgs += @('--portal-origin', $origin) }
& $portalPython @installArgs
if ($LASTEXITCODE -ne 0) { throw 'Пакет не установлен. Смотрите сообщение проверки архива.' }
Write-Host 'Готово. Откройте в портале «Мониторинг сплитов». Backend запустится автоматически.'

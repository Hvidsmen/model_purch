param(
    [string]$Database,
    [ValidateNotNullOrEmpty()][string]$BindAddress = '127.0.0.1',
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [string]$PreparedUpdate,
    [switch]$CheckDatabase
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonExecutable = Join-Path $PSScriptRoot '.venv310\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    $pythonExecutable = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw 'Не найден Python окружения. Выполните: py -3.10 -m venv .venv310; затем .\.venv310\Scripts\python.exe -m pip install -r requirements.txt'
}
# Existing terminal sessions do not refresh environment variables saved to Windows.
# Preserve explicit process overrides, otherwise read the saved user/machine value.
foreach ($connectionVariable in @('MS_SQL_CONN_STR', 'DWH_SQL_CONN_STR')) {
    $connectionValue = [Environment]::GetEnvironmentVariable($connectionVariable, 'Process')
    if ([string]::IsNullOrWhiteSpace($connectionValue)) {
        $connectionValue = [Environment]::GetEnvironmentVariable($connectionVariable, 'User')
        if ([string]::IsNullOrWhiteSpace($connectionValue)) {
            $connectionValue = [Environment]::GetEnvironmentVariable($connectionVariable, 'Machine')
        }
        if (-not [string]::IsNullOrWhiteSpace($connectionValue)) {
            [Environment]::SetEnvironmentVariable($connectionVariable, $connectionValue, 'Process')
        }
    }
}
Write-Host 'Подготовка портала. Этапы и время выполнения будут показаны ниже.'
$prepareArguments = @('-u', 'scripts/prepare_local.py')
if ($CheckDatabase) { $prepareArguments += '--check-database' }
if ($Database) { $prepareArguments += @('--database', $Database) }
if ($PreparedUpdate) {
    & $pythonExecutable -u scripts/portal_update_state.py --consume-prepared $PreparedUpdate --database $Database
    if ($LASTEXITCODE -ne 0) { throw 'Prepared update marker is invalid. Preparation was not bypassed.' }
    Write-Host 'Verified preparation from the independent update task.'
} else {
    & $pythonExecutable @prepareArguments
}
if ($LASTEXITCODE -ne 0) { throw 'Подготовка не завершена. Сервер не запущен.' }
$config = Get-Content -LiteralPath '.local/config.json' -Raw -Encoding UTF8 | ConvertFrom-Json
$env:DJANGO_DB_PATH = $config.database_path
Write-Host "Подготовка завершена. Запуск сервера ${BindAddress}:$Port"
& $pythonExecutable -u manage.py runserver "${BindAddress}:$Port" --settings=portal.settings_local --noreload --skip-checks
if ($LASTEXITCODE -ne 0) { throw 'Сервер остановился с ошибкой. Проверьте вывод, в том числе занятость порта.' }

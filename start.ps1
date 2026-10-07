param(
    [string]$Database,
    [ValidateRange(1, 65535)][int]$Port = 8000
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
$prepareArguments = @('scripts/prepare_local.py')
if ($Database) { $prepareArguments += @('--database', $Database) }
& $pythonExecutable @prepareArguments
if ($LASTEXITCODE -ne 0) { throw 'Подготовка не завершена. Сервер не запущен.' }
$config = Get-Content -LiteralPath '.local/config.json' -Raw -Encoding UTF8 | ConvertFrom-Json
$env:DJANGO_DB_PATH = $config.database_path
& $pythonExecutable manage.py runserver "127.0.0.1:$Port" --settings=portal.settings_local --noreload
if ($LASTEXITCODE -ne 0) { throw 'Сервер остановился с ошибкой. Проверьте вывод, в том числе занятость порта.' }

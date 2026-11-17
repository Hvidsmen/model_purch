param(
    [string]$CaCertFile,
    [switch]$HttpsOnly,
    [switch]$Restart,
    [string]$TaskName = 'PortalComp'
)
$ErrorActionPreference = 'Stop'
$portalRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $portalRoot
$pythonExecutable = Join-Path $portalRoot '.venv310\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) { $pythonExecutable = Join-Path $portalRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $pythonExecutable)) { throw 'Не найден Python окружения портала.' }
if (-not $HttpsOnly) {
    & $pythonExecutable -m pip install 'ldap3>=2.9.1,<3'
    if ($LASTEXITCODE -ne 0) { throw 'Не установлен ldap3. Настройки сайта не изменены.' }
}
$configureArguments = @('manage.py', 'configure_da_dev', '--settings=portal.settings_local')
if ($HttpsOnly) { $configureArguments += '--https-only' }
if ($CaCertFile) { $configureArguments += @('--ca-cert', $CaCertFile) }
& $pythonExecutable @configureArguments
if ($LASTEXITCODE -ne 0) { throw 'Настройка не завершена. Проверьте сообщение выше.' }
if ($Restart) {
    & (Join-Path $portalRoot 'portal-task.ps1') restart -TaskName $TaskName
    if ($LASTEXITCODE -ne 0) { throw 'Профиль записан, но перезапуск не завершён. Проверьте статус задачи.' }
} else { Write-Host 'Для применения перезапустите задачу портала: .\portal-task.ps1 restart' }
Write-Host 'Основной адрес: https://da-dev.daichi.ru'

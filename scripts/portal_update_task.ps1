param(
    [Parameter(Mandatory=$true)][ValidateSet('check', 'start-updater', 'stop-portal', 'start-portal')][string]$Action,
    [Parameter(Mandatory=$true)][string]$TaskName,
    [Parameter(Mandatory=$true)][string]$PortalTaskName
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv310\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = Join-Path $root '.venv\Scripts\python.exe' }
function Get-VerifiedTask([string]$name, [string]$worker) {
    $task = Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction Stop
    $expected = '-u "' + (Join-Path $root ('scripts\' + $worker)) + '"'
    if ($task.Actions.Count -ne 1 -or $task.Actions[0].Execute -ne $python -or $task.Actions[0].Arguments -ne $expected -or $task.Actions[0].WorkingDirectory -ne $root) {
        throw 'Task does not belong to this portal checkout.'
    }
    return $task
}
$portal = Get-VerifiedTask $PortalTaskName 'portal_task_worker.py'
$updater = Get-VerifiedTask $TaskName 'portal_update_worker.py'
if ($portal.Principal.UserId -ne $updater.Principal.UserId -or $portal.Principal.LogonType -ne $updater.Principal.LogonType) { throw 'Portal and updater must use the same Windows identity and logon type.' }
if ($Action -eq 'check') { exit 0 }
if ($Action -eq 'start-updater') { Start-ScheduledTask -InputObject $updater; exit 0 }
if ($Action -eq 'stop-portal') {
    # Stop the recorded PowerShell/Django tree while its wrapper is still alive.
    # PID plus process creation time prevents killing a reused PID.
    & $python -u (Join-Path $root 'scripts\portal_task_process.py') stop
    if ($LASTEXITCODE -ne 0) { throw 'Portal child processes did not stop safely.' }
    $portal = Get-VerifiedTask $PortalTaskName 'portal_task_worker.py'
    if ($portal.State -eq 'Running') { Stop-ScheduledTask -InputObject $portal }
    $deadline = (Get-Date).AddSeconds(30)
    do {
        $portal = Get-VerifiedTask $PortalTaskName 'portal_task_worker.py'
        if ($portal.State -ne 'Running') { exit 0 }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $deadline)
    throw 'Portal task did not stop.'
}
Start-ScheduledTask -InputObject $portal

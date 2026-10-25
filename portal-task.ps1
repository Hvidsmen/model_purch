param(
    [ValidateSet('install', 'start', 'stop', 'restart', 'status', 'enable-browser-updates')][string]$Action = 'status',
    [string]$TaskName = 'PortalComp',
    [string]$Database = '.\db.sqlite3',
    [ValidateNotNullOrEmpty()][string]$BindAddress = '0.0.0.0',
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [switch]$RunWhenLoggedOff,
    [switch]$Update
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($Update -and $Action -ne 'restart') { throw '-Update requires restart.' }
$pythonExecutable = Join-Path $PSScriptRoot '.venv310\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    $pythonExecutable = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $pythonExecutable)) { throw 'Python virtual environment not found.' }
$localDirectory = Join-Path $PSScriptRoot '.local'
$settingsPath = Join-Path $localDirectory 'task-settings.json'

function Get-PortalTask {
    $task = Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction SilentlyContinue
    if (-not $task) { throw "Task '$TaskName' not found. Run install first." }
    $workerPath = Join-Path $PSScriptRoot 'scripts\portal_task_worker.py'
    if ($task.Actions.Count -ne 1 -or $task.Actions[0].Execute -ne $pythonExecutable -or $task.Actions[0].Arguments -ne "-u `"$workerPath`"") {
        throw 'Task belongs to another application or checkout. Choose another -TaskName.'
    }
    return $task
}
function Stop-PortalTask {
    $task = Get-PortalTask
    if ($task.State -eq 'Running') { Stop-ScheduledTask -InputObject $task }
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-PortalTask).State -eq 'Running') {
        if ((Get-Date) -gt $deadline) { throw 'Task did not stop; restart cancelled.' }
        Start-Sleep -Milliseconds 300
    }
}

if ($Action -eq 'install') {
    if (Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction SilentlyContinue) {
        throw "Task '$TaskName' already exists. It has not been changed."
    }
    $databasePath = (Resolve-Path -LiteralPath $Database).Path
    New-Item -ItemType Directory -Path $localDirectory -Force | Out-Null
    $settings = @{ Database = $databasePath; BindAddress = $BindAddress; Port = $Port }
    $workerPath = Join-Path $PSScriptRoot 'scripts\portal_task_worker.py'
    $taskAction = New-ScheduledTaskAction -Execute $pythonExecutable -Argument "-u `"$workerPath`"" -WorkingDirectory $PSScriptRoot
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
    $taskSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    if ($RunWhenLoggedOff) {
        $credential = Get-Credential -UserName $identity -Message 'Windows account for running the portal (Task Scheduler stores the credential).'
        if (-not $credential -or $credential.UserName -ine $identity) { throw 'Use the current Windows account.' }
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $definition = New-ScheduledTask -Action $taskAction -Trigger $trigger -Settings $taskSettings
        Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -InputObject $definition -User $identity -Password ($credential.GetNetworkCredential().Password) | Out-Null
    } else {
        $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
        $definition = New-ScheduledTask -Action $taskAction -Trigger $trigger -Settings $taskSettings -Principal $principal
        Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -InputObject $definition | Out-Null
    }
    $settings | ConvertTo-Json | Set-Content -LiteralPath $settingsPath -Encoding UTF8
    Write-Host "Task '$TaskName' created. Start: .\portal-task.ps1 start"
    exit 0
}
$task = Get-PortalTask
if ($Action -eq 'enable-browser-updates') {
    # This is a separate Task Scheduler job, never a child of PortalComp.
    $updateTaskName = $TaskName + '-Updater'
    $workerPath = Join-Path $PSScriptRoot 'scripts\portal_update_worker.py'
    $updateAction = New-ScheduledTaskAction -Execute $pythonExecutable -Argument "-u `"$workerPath`"" -WorkingDirectory $PSScriptRoot
    $existing = Get-ScheduledTask -TaskName $updateTaskName -TaskPath '\' -ErrorAction SilentlyContinue
    if ($existing) {
        if ($existing.Actions.Count -ne 1 -or $existing.Actions[0].Execute -ne $pythonExecutable -or $existing.Actions[0].Arguments -ne $updateAction.Arguments -or $existing.Actions[0].WorkingDirectory -ne $PSScriptRoot -or $existing.Principal.UserId -ne $task.Principal.UserId -or $existing.Principal.LogonType -ne $task.Principal.LogonType) {
            throw 'Updater task belongs to another application. It has not been changed.'
        }
    } else {
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $portalIdentity = $task.Principal.UserId
        if ($portalIdentity -match '^S-1-') { $portalSid = ([System.Security.Principal.SecurityIdentifier]$portalIdentity).Value }
        else { $portalSid = ([System.Security.Principal.NTAccount]$portalIdentity).Translate([System.Security.Principal.SecurityIdentifier]).Value }
        $currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        if ($portalSid -ne $currentSid) { throw 'Enable browser updates using the Windows account that owns the portal task.' }
        $updateSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        $definition = New-ScheduledTask -Action $updateAction -Settings $updateSettings -Principal $task.Principal
        if ($task.Principal.LogonType -eq 'Password') {
            $credential = Get-Credential -UserName $identity -Message 'One-time credentials for the independent portal updater task.'
            if (-not $credential -or $credential.UserName -ine $identity) { throw 'Use the current Windows account.' }
            Register-ScheduledTask -TaskName $updateTaskName -TaskPath '\' -InputObject $definition -User $identity -Password ($credential.GetNetworkCredential().Password) | Out-Null
        } elseif ($task.Principal.LogonType -in @('Interactive', 'InteractiveOrPassword')) {
            Register-ScheduledTask -TaskName $updateTaskName -TaskPath '\' -InputObject $definition | Out-Null
        } else { throw 'This task logon type is not supported for browser updates.' }
    }
    @{ root=$PSScriptRoot; task_name=$updateTaskName; portal_task_name=$TaskName } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $localDirectory 'browser-updates.json') -Encoding UTF8
    Write-Host 'BROWSER UPDATES READY - open Administration / Portal updates.' -ForegroundColor Green
    exit 0
}
if ($Action -eq 'status') {
    $task | Format-List TaskName, State | Out-Host
    Get-ScheduledTaskInfo -InputObject $task | Format-List LastRunTime, LastTaskResult, NextRunTime | Out-Host
    Write-Host "Log: $localDirectory\portal-task.log"
    if (Test-Path -LiteralPath "$localDirectory\portal-task.log") {
        Get-Content -LiteralPath "$localDirectory\portal-task.log" -Encoding UTF8 -Tail 12 | Out-Host
    }
    exit 0
}
if ($Update) {
    $changes = & git status --porcelain
    if ($LASTEXITCODE -ne 0) { throw 'Git status failed; task not stopped.' }
    if ($changes) { throw 'Local Git changes found; task not stopped.' }
    & git pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw 'Git update failed; task not stopped.' }
}
if ($Action -in @('stop', 'restart')) { Stop-PortalTask }
if ($Action -in @('start', 'restart')) {
    if ($Update) {
        & $pythonExecutable -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; fix it and run start.' }
    }
    # Do not launch a second copy if an old manual/background server still owns the port.
    $settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse($settings.BindAddress), [int]$settings.Port)
    try { $listener.Start() } finally { $listener.Stop() }
    Start-ScheduledTask -InputObject (Get-PortalTask)
    & $pythonExecutable -u (Join-Path $PSScriptRoot 'scripts\portal_wait_ready.py') --address $settings.BindAddress --port $settings.Port --log "$localDirectory\portal-task.log"
    if ($LASTEXITCODE -ne 0) { throw 'Portal readiness was not confirmed. Check status and log; the task has not been stopped.' }
    Write-Host "PORTAL READY - HTTP OK. You can refresh the browser now." -ForegroundColor Green
}

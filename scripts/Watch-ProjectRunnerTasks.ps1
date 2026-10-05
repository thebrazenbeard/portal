[CmdletBinding()]
param(
    [string]$TasksRoot,
    [ValidateRange(1, 3600)]
    [int]$RefreshSeconds = 2,
    [switch]$Once,
    [switch]$IncludeCommand,
    [switch]$History
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-ProjectRunnerTasksRoot {
    param([string]$RequestedRoot)

    if (-not [string]::IsNullOrWhiteSpace($RequestedRoot)) {
        return [System.IO.Path]::GetFullPath($RequestedRoot)
    }
    if (-not [string]::IsNullOrWhiteSpace($env:PROJECT_RUNNER_TASKS_ROOT)) {
        return [System.IO.Path]::GetFullPath($env:PROJECT_RUNNER_TASKS_ROOT)
    }
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        return (Join-Path $env:LOCALAPPDATA "ProjectRunner\tasks")
    }
    return (Join-Path $HOME ".project-runner\tasks")
}

function Get-DescendantProcessIds {
    param(
        [int]$RootPid,
        [object[]]$ProcessRows
    )

    $seen = New-Object 'System.Collections.Generic.HashSet[int]'
    $queue = New-Object 'System.Collections.Generic.Queue[int]'
    $queue.Enqueue($RootPid)

    while ($queue.Count -gt 0) {
        $parent = $queue.Dequeue()
        foreach ($row in $ProcessRows) {
            if ([int]$row.ParentProcessId -eq $parent) {
                $child = [int]$row.ProcessId
                if ($seen.Add($child)) {
                    $queue.Enqueue($child)
                }
            }
        }
    }
    return @($seen)
}

function Test-HasAncestorNamed {
    param(
        [int]$ProcessId,
        [string]$Name,
        [hashtable]$ProcessByPid
    )

    $current = $ProcessId
    $visited = New-Object 'System.Collections.Generic.HashSet[int]'
    for ($depth = 0; $depth -lt 64; $depth++) {
        if (-not $ProcessByPid.ContainsKey($current)) { return $false }
        $row = $ProcessByPid[$current]
        $parent = [int]$row.ParentProcessId
        if ($parent -le 0 -or -not $visited.Add($parent)) { return $false }
        if (-not $ProcessByPid.ContainsKey($parent)) { return $false }
        $parentRow = $ProcessByPid[$parent]
        if ([string]$parentRow.Name -ieq $Name) { return $true }
        $current = $parent
    }
    return $false
}

function Test-RootProcessIdentity {
    param(
        [object]$Task,
        [System.Diagnostics.Process]$Process
    )

    if ($null -eq $Process) { return $false }
    if ([string]::IsNullOrWhiteSpace([string]$Task.process_started_at_utc)) {
        return $true
    }

    try {
        $expected = [DateTimeOffset]::Parse([string]$Task.process_started_at_utc).UtcDateTime
        $actual = $Process.StartTime.ToUniversalTime()
        return [Math]::Abs(($actual - $expected).TotalSeconds) -lt 1.0
    }
    catch {
        return $false
    }
}

function Get-TreeMetrics {
    param(
        [int]$RootPid,
        [object[]]$ProcessRows
    )

    $treePids = @($RootPid)
    $treePids += @(Get-DescendantProcessIds -RootPid $RootPid -ProcessRows $ProcessRows)
    $liveProcesses = @(
        $treePids |
            Sort-Object -Unique |
            ForEach-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue } |
            Where-Object { $null -ne $_ }
    )

    if ($liveProcesses.Count -gt 0) {
        $cpuSeconds = ($liveProcesses | Measure-Object -Property CPU -Sum).Sum
        $ramBytes = ($liveProcesses | Measure-Object -Property WorkingSet64 -Sum).Sum
    }
    else {
        $cpuSeconds = 0
        $ramBytes = 0
    }

    return [pscustomobject]@{
        Processes = $liveProcesses
        CPUSeconds = [double]$cpuSeconds
        RAMBytes = [double]$ramBytes
        ChildCount = [Math]::Max(0, $liveProcesses.Count - 1)
        Pids = @($treePids | Sort-Object -Unique)
    }
}

function Get-ProcessRuntimeText {
    param(
        [int]$RootPid,
        [DateTimeOffset]$Now
    )

    try {
        $process = Get-Process -Id $RootPid -ErrorAction Stop
        $runtime = $Now.UtcDateTime - $process.StartTime.ToUniversalTime()
        return "{0:00}:{1:00}:{2:00}" -f [Math]::Floor($runtime.TotalHours), $runtime.Minutes, $runtime.Seconds
    }
    catch {
        return "UNKNOWN"
    }
}

function Get-DiscoveredTaskLabel {
    param(
        [string]$Origin,
        [object]$ProcessRow
    )

    $commandLine = [string]$ProcessRow.CommandLine
    if ($Origin -eq "EXECUTOR" -and $commandLine -match '(?i)([A-Za-z0-9_.-]+\.py)\b') {
        return "executor:$([System.IO.Path]::GetFileName($Matches[1]))"
    }
    if ($Origin -eq "CODEX_BRIDGE") {
        return "codex:$([int]$ProcessRow.ProcessId)"
    }
    if ($Origin -eq "VERA_WORKER") {
        return "vera-mono:$([int]$ProcessRow.ProcessId)"
    }
    return "$($Origin.ToLowerInvariant()):$([string]$ProcessRow.Name):$([int]$ProcessRow.ProcessId)"
}

function Add-DiscoveredTaskRow {
    param(
        [System.Collections.ArrayList]$Rows,
        [System.Collections.Generic.HashSet[int]]$ClaimedPids,
        [object]$ProcessRow,
        [string]$Origin,
        [string]$Trust,
        [object[]]$ProcessRows,
        [DateTimeOffset]$Now,
        [bool]$ShowCommand
    )

    $rootPid = [int]$ProcessRow.ProcessId
    if ($ClaimedPids.Contains($rootPid)) { return }

    $metrics = Get-TreeMetrics -RootPid $rootPid -ProcessRows $ProcessRows
    foreach ($pidValue in $metrics.Pids) {
        [void]$ClaimedPids.Add([int]$pidValue)
    }

    $row = [ordered]@{
        Task = Get-DiscoveredTaskLabel -Origin $Origin -ProcessRow $ProcessRow
        PID = $rootPid
        State = "RUNNING"
        Origin = $Origin
        Trust = $Trust
        Runtime = Get-ProcessRuntimeText -RootPid $rootPid -Now $Now
        CPU_s = [Math]::Round($metrics.CPUSeconds, 1)
        RAM_MB = [Math]::Round(($metrics.RAMBytes / 1MB), 1)
        Children = $metrics.ChildCount
        Repo = ""
        Lane = ""
        Owner = "chatgpt-discovered"
    }
    if ($ShowCommand) {
        $row["Command"] = [string]$ProcessRow.CommandLine
    }
    [void]$Rows.Add([pscustomobject]$row)
}

$TasksRoot = Resolve-ProjectRunnerTasksRoot $TasksRoot
$activeRoot = Join-Path $TasksRoot "active"
$historyRoot = Join-Path $TasksRoot "history"

do {
    $now = [DateTimeOffset]::UtcNow
    $processRows = @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Select-Object ProcessId, ParentProcessId, Name, CreationDate, CommandLine
    )
    $processByPid = @{}
    foreach ($processRow in $processRows) {
        $processByPid[[int]$processRow.ProcessId] = $processRow
    }

    $rows = New-Object System.Collections.ArrayList
    $claimedPids = New-Object 'System.Collections.Generic.HashSet[int]'

    if (Test-Path $activeRoot) {
        foreach ($file in Get-ChildItem -Path $activeRoot -Filter "*.json" -File) {
            try {
                $task = Get-Content -Raw -Path $file.FullName | ConvertFrom-Json
                if ($task.schema -ne "PROJECT_RUNNER_LOCAL_TASK_V1") { continue }

                $rootProcess = Get-Process -Id ([int]$task.pid) -ErrorAction SilentlyContinue
                $identityMatches = Test-RootProcessIdentity -Task $task -Process $rootProcess
                if ($null -ne $rootProcess -and -not $identityMatches) {
                    $state = "PID_REUSED"
                    $metrics = [pscustomobject]@{ CPUSeconds = 0; RAMBytes = 0; ChildCount = 0; Pids = @() }
                }
                elseif ($null -eq $rootProcess) {
                    $state = "ORPHANED"
                    $metrics = [pscustomobject]@{ CPUSeconds = 0; RAMBytes = 0; ChildCount = 0; Pids = @() }
                }
                else {
                    $state = "RUNNING"
                    $metrics = Get-TreeMetrics -RootPid ([int]$task.pid) -ProcessRows $processRows
                    foreach ($pidValue in $metrics.Pids) {
                        [void]$claimedPids.Add([int]$pidValue)
                    }
                }

                try {
                    $started = [DateTimeOffset]::Parse([string]$task.started_at_utc)
                    $runtime = $now - $started
                    $runtimeText = "{0:00}:{1:00}:{2:00}" -f [Math]::Floor($runtime.TotalHours), $runtime.Minutes, $runtime.Seconds
                }
                catch {
                    $runtimeText = "UNKNOWN"
                }

                $row = [ordered]@{
                    Task = [string]$task.name
                    PID = [int]$task.pid
                    State = $state
                    Origin = "REGISTERED"
                    Trust = "EXACT"
                    Runtime = $runtimeText
                    CPU_s = [Math]::Round($metrics.CPUSeconds, 1)
                    RAM_MB = [Math]::Round(($metrics.RAMBytes / 1MB), 1)
                    Children = $metrics.ChildCount
                    Repo = [string]$task.repository
                    Lane = [string]$task.lane
                    Owner = [string]$task.owner
                }
                if ($IncludeCommand) {
                    $row["Command"] = [string]$task.command
                }
                [void]$rows.Add([pscustomobject]$row)
            }
            catch {
                [void]$rows.Add([pscustomobject]@{
                    Task = $file.BaseName
                    PID = ""
                    State = "INVALID_RECORD"
                    Origin = "REGISTERED"
                    Trust = "EXACT"
                    Runtime = ""
                    CPU_s = ""
                    RAM_MB = ""
                    Children = ""
                    Repo = ""
                    Lane = ""
                    Owner = ""
                })
            }
        }
    }

    $executorRoots = @(
        $processRows | Where-Object {
            [string]$_.Name -ieq "node.exe" -and
            [string]$_.CommandLine -match '(?i)\\AppData\\Local\\Executor\\DesktopCommanderMCP\\.*dist\\index\.js'
        }
    )
    foreach ($executorRoot in $executorRoots) {
        $directChildren = @(
            $processRows | Where-Object {
                [int]$_.ParentProcessId -eq [int]$executorRoot.ProcessId -and
                [string]$_.Name -ine "conhost.exe"
            }
        )
        foreach ($child in $directChildren) {
            $childTree = @([int]$child.ProcessId)
            $childTree += @(Get-DescendantProcessIds -RootPid ([int]$child.ProcessId) -ProcessRows $processRows)
            if ($childTree -contains $PID) { continue }
            if ([string]$child.CommandLine -match '(?i)Watch-ProjectRunnerTasks\.ps1') { continue }

            $params = @{
                Rows = $rows
                ClaimedPids = $claimedPids
                ProcessRow = $child
                Origin = "EXECUTOR"
                Trust = "EXACT"
                ProcessRows = $processRows
                Now = $now
                ShowCommand = [bool]$IncludeCommand
            }
            Add-DiscoveredTaskRow @params
        }
    }

    $codexWorkers = @($processRows | Where-Object { [string]$_.Name -ieq "codex.exe" })
    foreach ($worker in $codexWorkers) {
        if (Test-HasAncestorNamed -ProcessId ([int]$worker.ProcessId) -Name "tunnel-client.exe" -ProcessByPid $processByPid) {
            $params = @{
                Rows = $rows
                ClaimedPids = $claimedPids
                ProcessRow = $worker
                Origin = "CODEX_BRIDGE"
                Trust = "STRONG"
                ProcessRows = $processRows
                Now = $now
                ShowCommand = [bool]$IncludeCommand
            }
            Add-DiscoveredTaskRow @params
        }
    }

    $veraWorkers = @($processRows | Where-Object { [string]$_.Name -ieq "vera-mono.exe" })
    foreach ($worker in $veraWorkers) {
        $params = @{
            Rows = $rows
            ClaimedPids = $claimedPids
            ProcessRow = $worker
            Origin = "VERA_WORKER"
            Trust = "HEURISTIC"
            ProcessRows = $processRows
            Now = $now
            ShowCommand = [bool]$IncludeCommand
        }
        Add-DiscoveredTaskRow @params
    }

    if (-not $Once) { Clear-Host }
    Write-Host "Project Runner / ChatGPT Task Monitor  |  $TasksRoot"
    Write-Host "Updated: $($now.ToLocalTime().ToString('yyyy-MM-dd HH:mm:ss zzz'))"
    Write-Host "REGISTERED/EXACT = explicit Project Runner ownership; EXECUTOR/EXACT = live Executor child tree;"
    Write-Host "CODEX_BRIDGE/STRONG = Codex under tunnel bridge; VERA_WORKER/HEURISTIC = named worker discovery."
    Write-Host ""

    if ($rows.Count -eq 0) {
        Write-Host "No registered or discovered ChatGPT task trees are currently visible."
    }
    else {
        $sortedRows = @($rows | Sort-Object State, Origin, Task, PID)
        $sortedRows |
            Select-Object Task, PID, State, Origin, Trust, Runtime, CPU_s, RAM_MB, Children, Repo, Lane, Owner |
            Format-Table -AutoSize

        if ($IncludeCommand) {
            Write-Host ""
            Write-Host "Commands"
            foreach ($row in $sortedRows) {
                $commandProperty = $row.PSObject.Properties["Command"]
                if ($null -eq $commandProperty) { continue }
                $commandText = [string]$commandProperty.Value
                if ([string]::IsNullOrWhiteSpace($commandText)) { continue }
                Write-Host ("[{0}] {1}" -f $row.PID, $row.Task)
                Write-Host ("  {0}" -f $commandText)
            }
        }
    }

    if ($History) {
        Write-Host ""
        Write-Host "Task History"
        $historyRows = @()
        if (Test-Path $historyRoot) {
            foreach ($file in Get-ChildItem -Path $historyRoot -Filter "*.json" -File) {
                try {
                    $task = Get-Content -Raw -Path $file.FullName | ConvertFrom-Json
                    if ($task.schema -ne "PROJECT_RUNNER_LOCAL_TASK_V1") { continue }
                    $historyRows += [pscustomobject]@{
                        Task = [string]$task.name
                        State = [string]$task.state
                        Exit = $task.exit_code
                        Ended = [string]$task.ended_at_utc
                        Runtime_s = if ($null -eq $task.duration_seconds) { "" } else { [Math]::Round([double]$task.duration_seconds, 1) }
                        Repo = [string]$task.repository
                        Lane = [string]$task.lane
                        Reason = [string]$task.terminal_reason
                    }
                }
                catch {
                    $historyRows += [pscustomobject]@{
                        Task = $file.BaseName
                        State = "INVALID_RECORD"
                        Exit = ""
                        Ended = ""
                        Runtime_s = ""
                        Repo = ""
                        Lane = ""
                        Reason = ""
                    }
                }
            }
        }
        if ($historyRows.Count -eq 0) {
            Write-Host "No historical Project Runner task records."
        }
        else {
            $historyRows |
                Sort-Object Ended -Descending |
                Format-Table -AutoSize
        }
    }

    if ($Once) { break }
    Start-Sleep -Seconds $RefreshSeconds
} while ($true)

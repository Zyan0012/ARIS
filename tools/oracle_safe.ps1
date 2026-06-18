#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"

function Parse-Args {
    param([string[]]$InputArgs)
    $safeWaitSeconds = if ($env:ORACLE_SAFE_WAIT_SECONDS) { [int]$env:ORACLE_SAFE_WAIT_SECONDS } else { 1800 }
    $safePollSeconds = if ($env:ORACLE_SAFE_POLL_SECONDS) { [int]$env:ORACLE_SAFE_POLL_SECONDS } else { 30 }
    $defaultOracleBin = Join-Path $env:APPDATA "npm\oracle.cmd"
    if (-not (Test-Path -LiteralPath $defaultOracleBin)) {
        $defaultOracleBin = "oracle"
    }
    $safeOracleBin = if ($env:ORACLE_SAFE_ORACLE_BIN) { $env:ORACLE_SAFE_ORACLE_BIN } else { $defaultOracleBin }
    $oracleArgs = New-Object System.Collections.Generic.List[string]

    for ($i = 0; $i -lt $InputArgs.Count; $i++) {
        $token = $InputArgs[$i]
        if ($token -eq "--safe-wait-seconds" -and $i + 1 -lt $InputArgs.Count) {
            $i++
            $safeWaitSeconds = [int]$InputArgs[$i]
        } elseif ($token -like "--safe-wait-seconds=*") {
            $safeWaitSeconds = [int]($token.Split("=", 2)[1])
        } elseif ($token -eq "--safe-poll-seconds" -and $i + 1 -lt $InputArgs.Count) {
            $i++
            $safePollSeconds = [int]$InputArgs[$i]
        } elseif ($token -like "--safe-poll-seconds=*") {
            $safePollSeconds = [int]($token.Split("=", 2)[1])
        } elseif ($token -eq "--safe-oracle-bin" -and $i + 1 -lt $InputArgs.Count) {
            $i++
            $safeOracleBin = $InputArgs[$i]
        } elseif ($token -like "--safe-oracle-bin=*") {
            $safeOracleBin = $token.Split("=", 2)[1]
        } elseif ($token -eq "-h" -or $token -eq "--help") {
            Write-Output "Usage: oracle-safe [--safe-wait-seconds N] [--safe-poll-seconds N] [oracle args...]"
            Write-Output "Runs oracle. If browser mode reports busy, polls 'oracle session <slug>' instead of retrying."
            exit 0
        } else {
            $oracleArgs.Add($token) | Out-Null
        }
    }

    [pscustomobject]@{
        SafeWaitSeconds = $safeWaitSeconds
        SafePollSeconds = [Math]::Max(1, $safePollSeconds)
        SafeOracleBin = $safeOracleBin
        OracleArgs = [string[]]$oracleArgs
    }
}

function Get-Slug {
    param([string[]]$OracleArgs)
    for ($i = 0; $i -lt $OracleArgs.Count; $i++) {
        if ($OracleArgs[$i] -eq "--slug" -and $i + 1 -lt $OracleArgs.Count) {
            return $OracleArgs[$i + 1]
        }
        if ($OracleArgs[$i] -like "--slug=*") {
            return $OracleArgs[$i].Split("=", 2)[1]
        }
    }
    return ""
}

function Test-BusyOutput {
    param([string]$Text)
    $needles = @("ERROR: busy", "User error (browser-automation): busy", "failed: busy", '"error":"busy"')
    foreach ($needle in $needles) {
        if ($Text.Contains($needle)) {
            return $true
        }
    }
    return $false
}

function Read-SessionMeta {
    param([string]$Slug)
    $path = Join-Path $env:USERPROFILE ".oracle\sessions\$Slug\meta.json"
    if (-not (Test-Path -LiteralPath $path)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Test-ModelRunning {
    param($Meta)
    if ($null -eq $Meta -or $null -eq $Meta.models) {
        return $false
    }
    foreach ($model in $Meta.models) {
        if ($model.status -eq "running") {
            return $true
        }
    }
    return $false
}

function Test-SessionCompleted {
    param($Meta)
    if ($null -eq $Meta) {
        return $false
    }
    if ($Meta.status -eq "completed") {
        return $true
    }
    if ($null -eq $Meta.models) {
        return $false
    }
    foreach ($model in $Meta.models) {
        if ($model.status -eq "completed") {
            return $true
        }
    }
    return $false
}

function Invoke-OracleStreaming {
    param([string]$OracleBin, [string[]]$OracleArgs)
    $lines = New-Object System.Collections.Generic.List[string]
    & $OracleBin @OracleArgs 2>&1 | ForEach-Object {
        $line = $_.ToString()
        $lines.Add($line) | Out-Null
        [Console]::Out.WriteLine($line)
    }
    [pscustomobject]@{
        ExitCode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
        Output = ($lines -join "`n")
    }
}

function Invoke-OracleSession {
    param([string]$OracleBin, [string]$Slug)
    & $OracleBin "session" $Slug 2>&1 | ForEach-Object {
        [Console]::Out.WriteLine($_.ToString())
    }
    if ($null -eq $LASTEXITCODE) { return 0 }
    return $LASTEXITCODE
}

function Wait-OracleSession {
    param([string]$OracleBin, [string]$Slug, [int]$WaitSeconds, [int]$PollSeconds)
    $deadline = (Get-Date).AddSeconds([Math]::Max(0, $WaitSeconds))
    [Console]::Out.WriteLine("")
    [Console]::Out.WriteLine("[oracle-safe] Browser bridge is busy; polling existing session '$Slug' instead of retrying.")

    while ($true) {
        $meta = Read-SessionMeta -Slug $Slug
        if (Test-SessionCompleted -Meta $meta) {
            [Console]::Out.WriteLine("")
            [Console]::Out.WriteLine("[oracle-safe] Session completed; returning stored Oracle result.")
            [Console]::Out.WriteLine("")
            return Invoke-OracleSession -OracleBin $OracleBin -Slug $Slug
        }
        if ($null -ne $meta -and $meta.status -eq "error" -and -not (Test-ModelRunning -Meta $meta)) {
            [Console]::Out.WriteLine("")
            [Console]::Out.WriteLine("[oracle-safe] Session is an error and no model is still running.")
            [Console]::Out.WriteLine("")
            $code = Invoke-OracleSession -OracleBin $OracleBin -Slug $Slug
            if ($code -eq 0) { return 1 }
            return $code
        }
        if ((Get-Date) -ge $deadline) {
            [Console]::Out.WriteLine("")
            [Console]::Out.WriteLine("[oracle-safe] Timed out waiting for '$Slug' after ${WaitSeconds}s.")
            [Console]::Out.WriteLine("")
            $code = Invoke-OracleSession -OracleBin $OracleBin -Slug $Slug
            if ($code -eq 0) { return 124 }
            return $code
        }
        $status = if ($null -eq $meta) { "not-created-yet" } else { $meta.status }
        $running = Test-ModelRunning -Meta $meta
        [Console]::Out.WriteLine("[oracle-safe] waiting: session=$Slug status=$status model_running=$running")
        Start-Sleep -Seconds $PollSeconds
    }
}

$parsed = Parse-Args -InputArgs $args
$slug = Get-Slug -OracleArgs $parsed.OracleArgs
$mutex = [System.Threading.Mutex]::new($false, "Local\OracleSafeBrowserLock")
$deadline = (Get-Date).AddSeconds([Math]::Max(0, $parsed.SafeWaitSeconds))
$hasLock = $false

try {
    while (-not $hasLock) {
        $hasLock = $mutex.WaitOne([TimeSpan]::FromSeconds($parsed.SafePollSeconds))
        if (-not $hasLock) {
            if ((Get-Date) -ge $deadline) {
                [Console]::Out.WriteLine("[oracle-safe] Timed out waiting for oracle-safe mutex.")
                exit 124
            }
            [Console]::Out.WriteLine("[oracle-safe] another Oracle browser run is queued/running; waiting for lock.")
        }
    }

    $result = Invoke-OracleStreaming -OracleBin $parsed.SafeOracleBin -OracleArgs $parsed.OracleArgs
    if ($result.ExitCode -eq 0) {
        exit 0
    }
    if (-not (Test-BusyOutput -Text $result.Output)) {
        exit $result.ExitCode
    }
    if (-not $slug) {
        [Console]::Out.WriteLine("")
        [Console]::Out.WriteLine("[oracle-safe] Oracle reported busy, but no --slug was provided, so there is no session to poll.")
        exit $result.ExitCode
    }
    exit (Wait-OracleSession -OracleBin $parsed.SafeOracleBin -Slug $slug -WaitSeconds $parsed.SafeWaitSeconds -PollSeconds $parsed.SafePollSeconds)
} finally {
    if ($hasLock) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}

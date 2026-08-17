[CmdletBinding()]
param(
    [string]$Model = "qwen3:30b-instruct",
    [int]$Port = 8080,
    [int]$ContextTokens = 49152,
    [int]$PartitionClauses = 24,
    [int]$RepairAttempts = 2,
    [int]$MaximumRounds = 3
)

$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"
$root = Split-Path -Parent $PSScriptRoot
$bootstrap = Join-Path $root "work\bootstrap_semantic_authoring"
$taskDir = Join-Path $root "work\compact_semantic_tasks"
$runtime = Join-Path $root "work\local_semantic_authoring"
New-Item -ItemType Directory -Force -Path $runtime | Out-Null

$statePath = Join-Path $runtime "queue-state.json"
$serverScript = Join-Path $PSScriptRoot "start_local_semantic_server.ps1"

function Test-ModelServer {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
        return $health.status -eq "ok"
    } catch {
        return $false
    }
}

function Start-ModelServerIfNeeded {
    if (Test-ModelServer) {
        return $true
    }
    try {
        & $serverScript -Mode baseline -Model $Model -Port $Port `
            -ContextTokens $ContextTokens | Out-Null
    } catch {
        Write-Error "Could not restart the local model server: $($_.Exception.Message)"
        return $false
    }
    return Test-ModelServer
}

if (!(Start-ModelServerIfNeeded)) {
    throw "The local model server is unavailable."
}

$tasks = Get-ChildItem -LiteralPath $bootstrap -Filter "*.json" |
    Sort-Object Name
$passed = @{}
$pending = @($tasks)

for ($round = 1; $round -le $MaximumRounds -and $pending.Count -gt 0; $round++) {
    $next = @()
    foreach ($authoring in $pending) {
        $task = Join-Path $taskDir $authoring.Name
        $started = [DateTimeOffset]::UtcNow.ToString("o")
        if (!(Start-ModelServerIfNeeded)) {
            $next += $authoring
            continue
        }
        @{
            status = "running"
            round = $round
            current_task = $authoring.BaseName
            task_count = $tasks.Count
            completed = $passed.Count
            failed = $next.Count
            updated_at = $started
        } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8

        & python (Join-Path $PSScriptRoot "local_semantic_authoring_chunked.py") $task `
            --model $Model --endpoint "http://127.0.0.1:$Port" `
            --context-tokens $ContextTokens --partition-clauses $PartitionClauses `
            --repair-attempts $RepairAttempts
        if ($LASTEXITCODE -ne 0 -and !(Test-ModelServer)) {
            @{
                status = "recovering_server"
                round = $round
                current_task = $authoring.BaseName
                task_count = $tasks.Count
                completed = $passed.Count
                failed = $next.Count
                updated_at = [DateTimeOffset]::UtcNow.ToString("o")
            } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
            if (Start-ModelServerIfNeeded) {
                & python (Join-Path $PSScriptRoot "local_semantic_authoring_chunked.py") $task `
                    --model $Model --endpoint "http://127.0.0.1:$Port" `
                    --context-tokens $ContextTokens --partition-clauses $PartitionClauses `
                    --repair-attempts $RepairAttempts
            }
        }
        if ($LASTEXITCODE -eq 0) {
            $passed[$authoring.Name] = $true
        } else {
            $next += $authoring
        }
    }
    $pending = $next
}

@{
    status = $(if ($pending.Count -eq 0) { "complete" } else { "needs_repair" })
    current_task = $null
    task_count = $tasks.Count
    completed = $passed.Count
    failed = $pending.Count
    updated_at = [DateTimeOffset]::UtcNow.ToString("o")
} | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8

if ($pending.Count -gt 0) { exit 2 }

[CmdletBinding()]
param(
    [string]$Model = "qwen3:30b-instruct",
    [int]$Port = 8080,
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
$tasks = Get-ChildItem -LiteralPath $bootstrap -Filter "*.json" |
    Sort-Object Name
$passed = @{}
$pending = @($tasks)

for ($round = 1; $round -le $MaximumRounds -and $pending.Count -gt 0; $round++) {
    $next = @()
    foreach ($authoring in $pending) {
        $task = Join-Path $taskDir $authoring.Name
        $started = [DateTimeOffset]::UtcNow.ToString("o")
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
            --partition-clauses $PartitionClauses --repair-attempts $RepairAttempts
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

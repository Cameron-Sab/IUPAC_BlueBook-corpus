[CmdletBinding()]
param(
    [string]$Model = "qwen3:30b-instruct",
    [int]$Port = 8080,
    [int]$ContextTokens = 49152,
    [switch]$ReplaceServer
)

$ErrorActionPreference = "Stop"
$server = & "$PSScriptRoot\start_local_semantic_server.ps1" -Mode baseline -Model $Model `
    -Port $Port -ContextTokens $ContextTokens -Replace:$ReplaceServer | ConvertFrom-Json
$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root "work\local_semantic_supervisor"
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
$stdout = Join-Path $runtime "supervisor.out.log"
$stderr = Join-Path $runtime "supervisor.err.log"
$python = (Get-Command python -ErrorAction Stop).Source
$arguments = @(
    "scripts\run_local_semantic_supervisor.py",
    "--model", $Model,
    "--endpoint", "http://127.0.0.1:$Port",
    "--context-tokens", "$ContextTokens"
)
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root `
    -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Start-Sleep -Seconds 1
if ($process.HasExited) {
    Get-Content -LiteralPath $stderr -Tail 80
    throw "Local semantic supervisor exited with code $($process.ExitCode)."
}
@{
    model_server = $server
    supervisor_pid = $process.Id
    state = (Join-Path $runtime "state.json")
    stdout_log = $stdout
    stderr_log = $stderr
} | ConvertTo-Json -Depth 5

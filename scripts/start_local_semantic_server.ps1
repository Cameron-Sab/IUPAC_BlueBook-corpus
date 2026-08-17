[CmdletBinding()]
param(
    [ValidateSet("baseline", "ngram-simple", "ngram-mod")]
    [string]$Mode = "ngram-mod",
    [string]$Model = "qwen3:30b-instruct",
    [string]$ModelPath = "",
    [int]$Port = 8080,
    [int]$ContextTokens = 49152,
    [string]$Build = "b10453",
    [switch]$Replace
)

$ErrorActionPreference = "Stop"
$root = Join-Path $env:LOCALAPPDATA "IUPAC-BlueBook\llama.cpp\$Build"
$server = Get-ChildItem -LiteralPath (Join-Path $root "bin") -Filter "llama-server.exe" -Recurse |
    Select-Object -First 1 -ExpandProperty FullName
if (!$server) {
    throw "llama-server.exe is missing. Run scripts\install_llama_cpp_windows.ps1 first."
}

if ($ModelPath) {
    $resolvedModelPath = (Resolve-Path -LiteralPath $ModelPath).Path
} else {
    $from = & ollama show $Model --modelfile |
        Where-Object { $_ -match '^FROM\s+' } |
        Select-Object -First 1
    if (!$from) {
        throw "Could not resolve the GGUF blob for Ollama model $Model."
    }
    $resolvedModelPath = ($from -replace '^FROM\s+', '').Trim().Trim('"')
}
if (!(Test-Path -LiteralPath $resolvedModelPath)) {
    throw "Resolved model blob does not exist: $resolvedModelPath"
}

$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($listener) {
    $process = Get-Process -Id $listener.OwningProcess -ErrorAction Stop
    if (!$Replace) {
        throw "Port $Port is already owned by $($process.ProcessName) (PID $($process.Id))."
    }
    if ($process.ProcessName -ne "llama-server") {
        throw "Refusing to replace non-llama process $($process.ProcessName) on port $Port."
    }
    Stop-Process -Id $process.Id -Force
    $process.WaitForExit()
}

if (!$ModelPath) {
    & ollama stop $Model | Out-Null
}
$logs = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$stdout = Join-Path $logs "$Mode.out.log"
$stderr = Join-Path $logs "$Mode.err.log"
$arguments = @(
    "--model", ('"' + $resolvedModelPath + '"'),
    "--alias", ('"' + $Model + '"'),
    "--host", "127.0.0.1",
    "--port", "$Port",
    "--ctx-size", "$ContextTokens",
    "--n-gpu-layers", "all",
    "--flash-attn", "on",
    "--cache-type-k", "q8_0",
    "--cache-type-v", "q8_0",
    "--parallel", "1",
    "--metrics",
    "--jinja"
)
if ($Mode -eq "ngram-simple") {
    $arguments += @("--spec-type", "ngram-simple")
} elseif ($Mode -eq "ngram-mod") {
    $arguments += @(
        "--spec-type", "ngram-mod",
        "--spec-ngram-mod-n-min", "48",
        "--spec-ngram-mod-n-max", "64",
        "--spec-ngram-mod-n-match", "24"
    )
}

$process = Start-Process -FilePath $server -ArgumentList $arguments -WindowStyle Hidden `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
$deadline = (Get-Date).AddMinutes(3)
do {
    if ($process.HasExited) {
        Get-Content -LiteralPath $stderr -Tail 80
        throw "llama-server exited with code $($process.ExitCode)."
    }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        if ($health.status -eq "ok") {
            break
        }
    } catch {
        Start-Sleep -Seconds 2
    }
} while ((Get-Date) -lt $deadline)
if ((Get-Date) -ge $deadline) {
    Stop-Process -Id $process.Id -Force
    throw "llama-server did not become healthy within three minutes."
}

@{
    mode = $Mode
    pid = $process.Id
    endpoint = "http://127.0.0.1:$Port"
    model = $Model
    model_blob = $resolvedModelPath
    context_tokens = $ContextTokens
    stderr_log = $stderr
} | ConvertTo-Json

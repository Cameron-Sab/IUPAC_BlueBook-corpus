[CmdletBinding()]
param(
    [string]$Build = "b10453",
    [string]$InstallRoot = "$env:LOCALAPPDATA\IUPAC-BlueBook\llama.cpp"
)

$ErrorActionPreference = "Stop"

if ($Build -ne "b10453") {
    throw "This installer pins and verifies build b10453 only."
}

$artifacts = @(
    @{
        Name = "llama-b10453-bin-win-cuda-13.3-x64.zip"
        Sha256 = "92cb01d69bd52cf307914d7a7fc187d81434269db4fc9561eaa48f6ebdffef06"
    },
    @{
        Name = "cudart-llama-bin-win-cuda-13.3-x64.zip"
        Sha256 = "1462a050eb4c684921ba51dcc4cc488a036674c3e73e9945ee705b854808d03e"
    }
)

$root = Join-Path $InstallRoot $Build
$downloads = Join-Path $root "downloads"
$bin = Join-Path $root "bin"
New-Item -ItemType Directory -Force -Path $downloads, $bin | Out-Null

foreach ($artifact in $artifacts) {
    $archive = Join-Path $downloads $artifact.Name
    $url = "https://github.com/ggml-org/llama.cpp/releases/download/$Build/$($artifact.Name)"
    if (!(Test-Path -LiteralPath $archive)) {
        & curl.exe -L --fail --retry 3 -o $archive $url
        if ($LASTEXITCODE -ne 0) {
            throw "Download failed: $url"
        }
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
    if ($actual -ne $artifact.Sha256) {
        throw "SHA-256 mismatch for $($artifact.Name): $actual"
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $bin -Force
}

$server = Get-ChildItem -LiteralPath $bin -Filter "llama-server.exe" -Recurse |
    Select-Object -First 1 -ExpandProperty FullName
if (!$server) {
    throw "llama-server.exe was not found after extraction."
}

@{
    build = $Build
    executable = $server
    verified = $true
} | ConvertTo-Json

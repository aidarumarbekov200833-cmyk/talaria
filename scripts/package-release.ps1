[CmdletBinding()]
param(
    [string]$Output = "release/outly-deploy.zip",
    [switch]$ExcludeEnv
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$releaseDir = Join-Path $root "release"
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
$releaseFull = (Resolve-Path $releaseDir).Path

$stage = Join-Path $releaseFull ("stage-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $stage | Out-Null
$stageFull = (Resolve-Path $stage).Path
if (-not $stageFull.StartsWith($releaseFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe staging path: $stageFull"
}

try {
    $topFiles = @(
        ".env.example", ".gitignore", "Caddyfile",
        "docker-compose.yml", "docker-compose.prod.yml", "README.md", "SECURITY_REVIEW.md"
    )
    if (-not $ExcludeEnv) {
        $topFiles = @(".env") + $topFiles
    }
    foreach ($name in $topFiles) {
        $source = Join-Path $root $name
        if (Test-Path -LiteralPath $source) {
            Copy-Item -LiteralPath $source -Destination (Join-Path $stageFull $name) -Force
        }
    }

    foreach ($name in @(".github", "backend", "deploy", "frontend", "scripts", "tg-mini-client")) {
        $source = Join-Path $root $name
        if (Test-Path -LiteralPath $source) {
            Copy-Item -LiteralPath $source -Destination (Join-Path $stageFull $name) -Recurse -Force
        }
    }

    $unwanted = Get-ChildItem -LiteralPath $stageFull -Force -Recurse -Directory |
        Where-Object { $_.Name -in @("node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "sessions") } |
        Sort-Object FullName -Descending
    foreach ($item in $unwanted) {
        $resolved = $item.FullName
        if (-not $resolved.StartsWith($stageFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Unsafe cleanup path: $resolved"
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }

    $archivePath = if ([IO.Path]::IsPathRooted($Output)) { $Output } else { Join-Path $root $Output }
    $archiveParent = Split-Path -Parent $archivePath
    New-Item -ItemType Directory -Force -Path $archiveParent | Out-Null
    if (Test-Path -LiteralPath $archivePath) { Remove-Item -LiteralPath $archivePath -Force }

    $tar = Join-Path $env:WINDIR "System32\tar.exe"
    & $tar -a -cf $archivePath -C $stageFull .
    if ($LASTEXITCODE -ne 0) { throw "tar exited with code $LASTEXITCODE" }

    $entries = @(& $tar -tf $archivePath)
    $requiredEntries = @("./deploy/.env.production.example", "./docker-compose.prod.yml", "./scripts/preflight.sh", "./scripts/deploy-production.sh")
    if (-not $ExcludeEnv) {
        $requiredEntries = @("./.env") + $requiredEntries
    }
    foreach ($required in $requiredEntries) {
        if ($entries -notcontains $required) { throw "Required archive entry is missing: ${required}" }
    }
    if ($ExcludeEnv -and $entries -contains "./.env") {
        throw "Server-safe archive unexpectedly contains .env"
    }
    $forbidden = $entries | Where-Object {
        $_ -match '(^|/)(node_modules|sessions|backups|\.outly-rollback)(/|$)' -or
        $_ -match '(^|/)(__pycache__|\.pytest_cache|\.ruff_cache|\.mypy_cache)(/|$)'
    }
    if ($forbidden) { throw "Forbidden runtime data found in archive: $($forbidden -join ', ')" }

    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash
    Write-Output "Archive: $archivePath"
    Write-Output "SHA256: $hash"
    $envStatus = if ($ExcludeEnv) { ".env excluded" } else { ".env included" }
    Write-Output "Verified: $envStatus; sessions, backups, caches and node_modules excluded."
}
finally {
    if (Test-Path -LiteralPath $stageFull) {
        $resolvedStage = (Resolve-Path $stageFull).Path
        if ($resolvedStage.StartsWith($releaseFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedStage -Recurse -Force
        }
    }
}

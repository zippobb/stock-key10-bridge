[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$CommitSha,

    [string]$PackageRoot,

    [ValidatePattern('^/opt/')]
    [string]$RemoteStageRoot = '/opt/stock-automation/staging/stock-key10-candidate-assets'
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($PackageRoot)) {
    $workspaceRoot = Split-Path -Parent $PSScriptRoot
    $PackageRoot = Join-Path $workspaceRoot "offline/stock-key10-bridge-$CommitSha"
}

$PackageRoot = (Resolve-Path -LiteralPath $PackageRoot).Path
$manifestPath = Join-Path $PackageRoot 'manifest.json'
$verifierPath = Join-Path $PackageRoot 'verify_stage.py'

if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "STOP: manifest not found: $manifestPath"
}
if (-not (Test-Path -LiteralPath $verifierPath -PathType Leaf)) {
    throw "STOP: verify_stage.py not found"
}

try {
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    throw "STOP: manifest is not valid JSON"
}

if ($manifest.repository -ne 'zippobb/stock-key10-bridge') {
    throw "STOP: repository mismatch"
}
if ($manifest.ref -ne 'main') {
    throw "STOP: ref mismatch"
}
if ($manifest.commit_sha -ne $CommitSha) {
    throw "STOP: commit mismatch"
}

$expectedPaths = @(
    'PROJECT_STATE.md',
    'V2_REVIEW_WORKFLOW.md',
    'candidate_assets/README.md',
    'candidate_assets/candidate_sources_v2.py',
    'candidate_assets/test_candidate_sources_v2.py'
)
$entries = @($manifest.files)
if ($entries.Count -ne $expectedPaths.Count) {
    throw "STOP: unexpected manifest file count"
}

$records = @()
$seen = @{}
foreach ($entry in $entries) {
    $relative = [string]$entry.path
    if ($expectedPaths -notcontains $relative) {
        throw "STOP: unexpected manifest path: $relative"
    }
    if ($seen.ContainsKey($relative)) {
        throw "STOP: duplicate manifest path: $relative"
    }
    $seen[$relative] = $true
    if ($relative.Contains('..') -or [IO.Path]::IsPathRooted($relative)) {
        throw "STOP: unsafe manifest path"
    }
    $localPath = Join-Path $PackageRoot ($relative -replace '/', [IO.Path]::DirectorySeparatorChar)
    if (-not (Test-Path -LiteralPath $localPath -PathType Leaf)) {
        throw "STOP: package file missing: $relative"
    }
    $bytes = [IO.File]::ReadAllBytes($localPath)
    $actualSha = (Get-FileHash -LiteralPath $localPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($bytes.Length -ne [int64]$entry.bytes) {
        throw "STOP: size mismatch: $relative"
    }
    if ($actualSha -ne ([string]$entry.sha256).ToLowerInvariant()) {
        throw "STOP: SHA256 mismatch: $relative"
    }
    $records += [pscustomobject]@{
        Relative = $relative
        LocalPath = $localPath
        Bytes = $bytes.Length
        SHA256 = $actualSha
    }
}
if ($seen.Count -ne $expectedPaths.Count) {
    throw "STOP: manifest does not cover the expected file set"
}

$stage = "$RemoteStageRoot/$CommitSha"
$remotePreflight = "install -d -m 700 -- '$stage' '$stage/candidate_assets' && test ! -e '$stage/manifest.json' && test ! -e '$stage/verify_stage.py'"
& ssh stock-server $remotePreflight
if ($LASTEXITCODE -ne 0) {
    throw "STOP: remote stage already exists or could not be prepared: $stage"
}

foreach ($record in $records) {
    $remotePath = "$stage/$($record.Relative)"
    & ssh stock-server "test ! -e '$remotePath'"
    if ($LASTEXITCODE -ne 0) {
        throw "STOP: remote file already exists: $($record.Relative)"
    }
    & scp -- $record.LocalPath "stock-server:$remotePath"
    if ($LASTEXITCODE -ne 0) {
        throw "STOP: scp failed: $($record.Relative)"
    }
}

foreach ($toolFile in @($manifestPath, $verifierPath)) {
    $name = Split-Path -Leaf $toolFile
    & scp -- $toolFile "stock-server:$stage/$name"
    if ($LASTEXITCODE -ne 0) {
        throw "STOP: scp failed: $name"
    }
}

& ssh stock-server "python3 '$stage/verify_stage.py' '$stage/manifest.json' '$CommitSha'"
if ($LASTEXITCODE -ne 0) {
    throw "STOP: remote hash verification failed"
}

Write-Output "SYNC_PASSED"
Write-Output "repository=$($manifest.repository)"
Write-Output "ref=$($manifest.ref)"
Write-Output "commit_sha=$CommitSha"
Write-Output "remote_stage=$stage"
Write-Output "files=$($records.Count)"

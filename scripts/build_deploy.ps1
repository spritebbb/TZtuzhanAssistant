$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------
#  Build the one-click deployment package for Tuzhan Assistant.
#  Usage:  powershell -ExecutionPolicy Bypass -File scripts/build_deploy.ps1
#  Output: deploy\TZtuzhanAssistant-Deploy-v2.0.0\  +  .zip
#
#  All launcher/init files and docs live in scripts/deploy_assets/ and are
#  copied verbatim into the package root, so the build script stays ASCII.
# ---------------------------------------------------------------

$src = Split-Path -Parent $PSScriptRoot
$outRoot = Join-Path $src 'deploy'
$name = 'TZtuzhanAssistant-Deploy-v2.0.0'
$dir = Join-Path $outRoot $name
$zip = Join-Path $outRoot ($name + '.zip')

Write-Host "[deploy] source: $src"
Write-Host "[deploy] output: $dir"

if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force }
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
New-Item -ItemType Directory -Path $dir -Force | Out-Null

function Copy-Tree($rel) {
    $s = Join-Path $src $rel
    $d = Join-Path $dir $rel
    if (-not (Test-Path -LiteralPath $s)) {
        throw "source path missing: $s"
    }
    New-Item -ItemType Directory -Path $d -Force | Out-Null
    # 'data' is runtime data (db/logs/screenshots) and must never ship
    robocopy $s $d /E /XD __pycache__ .pytest_cache .git node_modules data /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed for $rel (exit $LASTEXITCODE)"
    }
    $global:LASTEXITCODE = 0
}

Write-Host "[deploy] copying backend..."
Copy-Tree 'backend'
Write-Host "[deploy] copying plugins..."
Copy-Tree 'plugins'
Write-Host "[deploy] copying skills..."
Copy-Tree 'skills'
Write-Host "[deploy] copying assets..."
Copy-Tree 'assets'

Write-Host "[deploy] copying prebuilt frontend..."
Copy-Tree 'frontend/dist'

# ---- persona (locate by pattern: file name contains non-ASCII) ----
$persona = Get-ChildItem -LiteralPath $src -File -Filter 'persona-*.md' | Select-Object -First 1
if (-not $persona) {
    throw 'persona markdown file not found'
}
Copy-Item -LiteralPath $persona.FullName -Destination $dir

# ---- runtime requirements (drop test-only deps) ----
$req = Join-Path $src 'requirements.txt'
$dstReq = Join-Path $dir 'requirements.txt'
Get-Content -LiteralPath $req -Encoding UTF8 |
    Where-Object { $_ -notmatch '^\s*(pytest|pytest-asyncio)' -and $_.Trim() -ne '' } |
    Set-Content -LiteralPath $dstReq -Encoding UTF8

# ---- deploy_assets: docs / env template / launcher bats / init script ----
Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'deploy_assets') -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $dir
}

# ---- zip ----
Write-Host "[deploy] creating zip..."
Compress-Archive -Path $dir -DestinationPath $zip -Force

$sizeMb = [math]::Round((Get-ChildItem -LiteralPath $dir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
$zipMb = [math]::Round((Get-Item -LiteralPath $zip).Length / 1MB, 1)

Write-Host ""
Write-Host "[deploy] done."
Write-Host "[deploy] folder: $dir  (${sizeMb} MB)"
Write-Host "[deploy] zip:    $zip  (${zipMb} MB)"

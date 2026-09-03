$ErrorActionPreference = 'Stop'

# Rebuild both deterministic one-click packages from the current source tree.
$src = Split-Path -Parent $PSScriptRoot
$outRoot = Join-Path $src 'deploy'
$resolvedSource = [IO.Path]::GetFullPath($src)
$resolvedOutput = [IO.Path]::GetFullPath($outRoot)
if (-not $resolvedOutput.StartsWith($resolvedSource + [IO.Path]::DirectorySeparatorChar)) {
    throw "unsafe deploy output path: $resolvedOutput"
}

$fullSuffix = ([string][char]0x5927) + ([char]0x676F) + ([char]0x7248)
$packages = @(
    @{ Name = 'TZtuzhanAssistant-Deploy-v2.0.0'; Full = $false },
    @{ Name = ('TZtuzhanAssistant-Deploy-Full-v2.0.0-' + $fullSuffix); Full = $true }
)

function Build-Package([string]$name, [bool]$full) {
    $dir = Join-Path $outRoot $name
    $zip = Join-Path $outRoot ($name + '.zip')
    $resolvedDir = [IO.Path]::GetFullPath($dir)
    $resolvedZip = [IO.Path]::GetFullPath($zip)
    if (-not $resolvedDir.StartsWith($resolvedOutput + [IO.Path]::DirectorySeparatorChar) -or
        -not $resolvedZip.StartsWith($resolvedOutput + [IO.Path]::DirectorySeparatorChar)) {
        throw "unsafe package path: $name"
    }

    Write-Host "[deploy] rebuilding: $name"
    if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force }
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    New-Item -ItemType Directory -Path $dir -Force | Out-Null

    foreach ($rel in @('backend', 'plugins', 'skills', 'assets', 'frontend/dist')) {
        $sourcePath = Join-Path $src $rel
        $destPath = Join-Path $dir $rel
        if (-not (Test-Path -LiteralPath $sourcePath)) { throw "source path missing: $sourcePath" }
        New-Item -ItemType Directory -Path $destPath -Force | Out-Null
        robocopy $sourcePath $destPath /E /XD __pycache__ .pytest_cache .git node_modules data /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy failed for $rel (exit $LASTEXITCODE)" }
        $global:LASTEXITCODE = 0
    }

    $persona = Get-ChildItem -LiteralPath $src -File -Filter 'persona-*.md' | Select-Object -First 1
    if (-not $persona) { throw 'persona markdown file not found' }
    Copy-Item -LiteralPath $persona.FullName -Destination $dir

    Get-Content -LiteralPath (Join-Path $src 'requirements.txt') -Encoding UTF8 |
        Where-Object { $_ -notmatch '^\s*(pytest|pytest-asyncio)' -and $_.Trim() -ne '' } |
        Set-Content -LiteralPath (Join-Path $dir 'requirements.txt') -Encoding UTF8

    Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'deploy_assets') -File -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $dir
    }

    if ($full) {
        $envPath = Join-Path $dir '.env.example'
        $envText = Get-Content -LiteralPath $envPath -Raw -Encoding UTF8
        $envText = $envText.Replace('MEMORY_EMBED_MODEL=BAAI/bge-small-zh-v1.5', 'MEMORY_EMBED_MODEL=BAAI/bge-m3')
        Set-Content -LiteralPath $envPath -Value $envText -Encoding UTF8
    }

    Compress-Archive -Path $dir -DestinationPath $zip -Force
    $sizeMb = [math]::Round((Get-ChildItem -LiteralPath $dir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
    $zipMb = [math]::Round((Get-Item -LiteralPath $zip).Length / 1MB, 1)
    Write-Host "[deploy] folder: $dir ($sizeMb MB)"
    Write-Host "[deploy] zip:    $zip ($zipMb MB)"
}

foreach ($package in $packages) {
    Build-Package -name $package.Name -full $package.Full
}

Write-Host '[deploy] both packages completed.'

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$delivery = Join-Path $workspace 'outputs'
$portable = Join-Path $delivery 'LCSC-OneClick-Windows-x64'
New-Item -ItemType Directory -Path $portable -Force | Out-Null
$exe = Join-Path $delivery 'LCSC3D.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'Run app/build.ps1 first' }
Copy-Item -LiteralPath $exe -Destination (Join-Path $portable 'LCSC-OneClick.exe') -Force
Copy-Item -LiteralPath (Join-Path $workspace 'README.md') -Destination (Join-Path $portable '使用说明.md') -Force
Copy-Item -LiteralPath (Join-Path $workspace 'LICENSE') -Destination $portable -Force
$licenseTarget = Join-Path $portable 'licenses'
New-Item -ItemType Directory -Path $licenseTarget -Force | Out-Null
Copy-Item -Path (Join-Path $workspace 'app/licenses/*') -Destination $licenseTarget -Recurse -Force
Compress-Archive -Path (Join-Path $portable '*') -DestinationPath (Join-Path $delivery 'LCSC-OneClick-Windows-x64.zip') -Force
git -C $workspace archive --format=zip --prefix=LCSC-OneClick/ -o (Join-Path $delivery 'LCSC-OneClick-Source.zip') HEAD
if ($LASTEXITCODE -ne 0) { throw 'Source archive failed' }
$sums = foreach ($name in @('LCSC-OneClick-Windows-x64.zip', 'LCSC-OneClick-Source.zip')) {
    (Get-FileHash -LiteralPath (Join-Path $delivery $name) -Algorithm SHA256).Hash.ToLowerInvariant() + '  ' + $name
}
Set-Content -LiteralPath (Join-Path $delivery 'SHA256SUMS.txt') -Value $sums -Encoding ascii
Write-Host "Portable and complete source packages: $delivery"

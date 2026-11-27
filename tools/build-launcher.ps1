$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $project 'dist'
$work = Join-Path $project 'build'
$assets = (Join-Path $project 'practice_tool\web_assets') + ';practice_tool/web_assets'
$entry = Join-Path $project 'practice_tool\launcher.py'
$options = @(
    '--noconfirm', '--clean', '--onefile', '--windowed', '--name', 'PracticeTool',
    '--distpath', $dist, '--workpath', $work, '--specpath', $work, '--add-data', $assets, $entry
)

Push-Location -LiteralPath $project
try {
    python -m PyInstaller @options
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}

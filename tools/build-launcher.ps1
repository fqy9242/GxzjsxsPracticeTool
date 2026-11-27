param([string]$OutputDirectory)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $project 'dist\launcher' }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$buildDirectory = Join-Path $project 'build\native-launcher'
$source = Join-Path $project 'launcher'
$service = Join-Path $project 'practice_tool'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (-not (Test-Path -LiteralPath $compiler)) { throw 'The .NET Framework C# compiler was not found.' }
[void][IO.Directory]::CreateDirectory($buildDirectory)
[void][IO.Directory]::CreateDirectory($OutputDirectory)

Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem, System.Drawing
$assetZip = Join-Path $buildDirectory 'service.zip'
$stream = [IO.File]::Open($assetZip, [IO.FileMode]::Create, [IO.FileAccess]::ReadWrite)
try {
    $archive = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Create, $true)
    try {
        Get-ChildItem -LiteralPath $service -File -Filter '*.py' | ForEach-Object {
            [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $_.FullName, ('practice_tool/' + $_.Name))
        }
        Get-ChildItem -LiteralPath (Join-Path $service 'web_assets') -File | ForEach-Object {
            [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $_.FullName, ('practice_tool/web_assets/' + $_.Name))
        }
    } finally { $archive.Dispose() }
} finally { $stream.Dispose() }

$iconFile = Join-Path $buildDirectory 'practice-tool.ico'
$bitmap = [Drawing.Bitmap]::new(48, 48)
$graphics = [Drawing.Graphics]::FromImage($bitmap)
$brush = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(36, 87, 213))
$font = [Drawing.Font]::new('Segoe UI', 27, [Drawing.FontStyle]::Bold, [Drawing.GraphicsUnit]::Pixel)
$format = [Drawing.StringFormat]::new()
$png = [IO.MemoryStream]::new()
try {
    $graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.TextRenderingHint = [Drawing.Text.TextRenderingHint]::AntiAlias
    $graphics.FillEllipse($brush, 1, 1, 46, 46)
    $format.Alignment = [Drawing.StringAlignment]::Center
    $format.LineAlignment = [Drawing.StringAlignment]::Center
    $graphics.DrawString('P', $font, [Drawing.Brushes]::White, [Drawing.RectangleF]::new(0, 0, 48, 48), $format)
    $bitmap.Save($png, [Drawing.Imaging.ImageFormat]::Png)
    $writer = [IO.BinaryWriter]::new([IO.File]::Create($iconFile))
    try {
        $writer.Write([byte[]]@(0, 0, 1, 0, 1, 0, 48, 48, 0, 0, 1, 0, 32, 0))
        $writer.Write([int]$png.Length)
        $writer.Write([int]22)
        $writer.Write($png.ToArray())
    } finally { $writer.Dispose() }
} finally {
    $png.Dispose()
    $format.Dispose()
    $font.Dispose()
    $brush.Dispose()
    $graphics.Dispose()
    $bitmap.Dispose()
}

$target = Join-Path $OutputDirectory 'PracticeTool.exe'
$options = @(
    '/nologo', '/target:winexe', '/optimize+', '/langversion:5',
    '/reference:System.dll', '/reference:System.Core.dll', '/reference:System.Drawing.dll',
    '/reference:System.Windows.Forms.dll', '/reference:System.Net.Http.dll',
    '/reference:System.IO.Compression.dll', '/reference:System.IO.Compression.FileSystem.dll',
    "/out:$target", "/resource:$assetZip,PracticeTool.service.zip", "/win32icon:$iconFile",
    ('/win32manifest:' + (Join-Path $source 'launcher.manifest'))
)
$files = Get-ChildItem -LiteralPath $source -File -Filter '*.cs' | Select-Object -ExpandProperty FullName
& $compiler @options @files
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Get-Item -LiteralPath $target | Select-Object FullName, Length

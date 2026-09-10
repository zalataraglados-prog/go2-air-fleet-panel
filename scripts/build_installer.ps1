[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$specPath = Join-Path $projectRoot 'packaging\windows\GO2AirFleetPanel.spec'
$installerScript = Join-Path $projectRoot 'packaging\windows\installer.iss'
$isccCandidates = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe'
)
$isccPath = $isccCandidates |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Project virtual environment not found: $pythonPath"
}
if (-not $isccPath) {
    throw 'Inno Setup 6 compiler (ISCC.exe) was not found.'
}

Push-Location $projectRoot
try {
    & $pythonPath -m PyInstaller --noconfirm --clean $specPath
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
    $bundleExe = Join-Path $projectRoot 'dist\GO2AirFleetPanel\GO2AirFleetPanel.exe'
    $wasmtimeDll = Join-Path $projectRoot (
        'dist\GO2AirFleetPanel\_internal\wasmtime\win32-x86_64\_wasmtime.dll'
    )
    if (-not (Test-Path -LiteralPath $wasmtimeDll -PathType Leaf)) {
        throw "Wasmtime runtime was not bundled: $wasmtimeDll"
    }
    $selfTest = Start-Process -FilePath $bundleExe -ArgumentList '--self-test' `
        -WorkingDirectory (Split-Path -Parent $bundleExe) -Wait -PassThru
    if ($selfTest.ExitCode -ne 0) {
        throw "Packaged runtime self-test failed with exit code $($selfTest.ExitCode)"
    }
    & $isccPath $installerScript
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

$version = '1.1.1'
$installerPath = Join-Path $projectRoot (
    "dist\installer\GO2-Air-Fleet-Panel-Setup-v$version.exe"
)
if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
    throw "Installer was not produced: $installerPath"
}
$hash = Get-FileHash -LiteralPath $installerPath -Algorithm SHA256
Write-Output "INSTALLER=$installerPath"
Write-Output "SHA256=$($hash.Hash.ToLowerInvariant())"

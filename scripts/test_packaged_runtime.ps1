[CmdletBinding()]
param(
    [string] $BundleRoot = ""
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($BundleRoot)) {
    $BundleRoot = Join-Path $projectRoot 'dist\GO2AirFleetPanel'
}
$resolvedBundle = (Resolve-Path -LiteralPath $BundleRoot).Path
$bundleExe = Join-Path $resolvedBundle 'GO2AirFleetPanel.exe'
$wasmtimeDll = Join-Path $resolvedBundle (
    '_internal\wasmtime\win32-x86_64\_wasmtime.dll'
)

if (-not (Test-Path -LiteralPath $bundleExe -PathType Leaf)) {
    throw "Packaged executable was not found: $bundleExe"
}
if (-not (Test-Path -LiteralPath $wasmtimeDll -PathType Leaf)) {
    throw "Wasmtime runtime was not bundled: $wasmtimeDll"
}

$selfTest = Start-Process -FilePath $bundleExe -ArgumentList '--self-test' `
    -WorkingDirectory $resolvedBundle -Wait -PassThru
if ($selfTest.ExitCode -ne 0) {
    throw "Packaged runtime self-test failed with exit code $($selfTest.ExitCode)"
}

[pscustomobject]@{
    executable = $bundleExe
    wasmtime_dll = $wasmtimeDll
    wasmtime_bytes = (Get-Item -LiteralPath $wasmtimeDll).Length
    self_test_exit_code = $selfTest.ExitCode
}

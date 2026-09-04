$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$panelScript = Join-Path $projectRoot 'scripts\panel.py'
$panelUrl = 'http://127.0.0.1:8765'

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "Project virtual environment not found: $pythonPath",
        'GO2 Air panel startup failed'
    ) | Out-Null
    exit 1
}

$listening = Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 8765 `
    -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    Start-Process -FilePath $pythonPath -ArgumentList @($panelScript) `
        -WorkingDirectory $projectRoot -WindowStyle Hidden

    $ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri $panelUrl -UseBasicParsing `
                -TimeoutSec 1
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        }
        catch {
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not $ready) {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            'The panel did not start within 10 seconds. Run diagnostics from the project directory.',
            'GO2 Air panel startup failed'
        ) | Out-Null
        exit 1
    }
}

Start-Process $panelUrl

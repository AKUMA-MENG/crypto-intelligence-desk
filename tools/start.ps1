[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$proxyPath = Join-Path $projectRoot 'proxy.py'
$indexPath = Join-Path $projectRoot 'index.html'
$bootstrapPath = Join-Path $PSScriptRoot 'bootstrap-python.ps1'

$port = 8899
if ($env:CID_PORT) {
    $parsedPort = 0
    if (-not [int]::TryParse($env:CID_PORT, [ref]$parsedPort) -or $parsedPort -lt 1024 -or $parsedPort -gt 65535) {
        Write-Error 'CID_PORT must be an integer from 1024 to 65535.'
        exit 2
    }
    $port = $parsedPort
}
$appUrl = "http://127.0.0.1:$port"

function Test-RunningApp {
    param([string]$Url)
    try {
        $reply = Invoke-RestMethod -Uri ($Url + '/ping') -TimeoutSec 2
        return $reply.app -eq 'crypto-intelligence-desk'
    } catch {
        return $false
    }
}

function Test-PythonCommand {
    param([string]$Executable, [string[]]$PrefixArgs)
    try {
        & $Executable @PrefixArgs -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

if (-not (Test-Path -LiteralPath $proxyPath) -or -not (Test-Path -LiteralPath $indexPath)) {
    Write-Error 'index.html or proxy.py is missing. Extract the complete release package first.'
    exit 1
}

if (Test-RunningApp -Url $appUrl) {
    Start-Process $appUrl
    exit 0
}

$pythonExe = $null
$pythonPrefix = @()
$pyLauncher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
if ($pyLauncher -and $pyLauncher.Source -notmatch '\\WindowsApps\\' -and (Test-PythonCommand -Executable $pyLauncher.Source -PrefixArgs @('-3'))) {
    $pythonExe = $pyLauncher.Source
    $pythonPrefix = @('-3')
}

if (-not $pythonExe) {
    $pythonCommand = Get-Command 'python.exe' -ErrorAction SilentlyContinue
    if ($pythonCommand -and $pythonCommand.Source -notmatch '\\WindowsApps\\' -and (Test-PythonCommand -Executable $pythonCommand.Source -PrefixArgs @())) {
        $pythonExe = $pythonCommand.Source
    }
}

if (-not $pythonExe) {
    Write-Host ''
    Write-Host 'First run: preparing the isolated portable runtime (about 11 MB)...'
    Write-Host 'It stays inside this application folder and does not require administrator access.'
    Write-Host ''
    & $bootstrapPath
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $pythonExe = Join-Path $projectRoot '.runtime\python.exe'
}

if (-not (Test-PythonCommand -Executable $pythonExe -PrefixArgs $pythonPrefix)) {
    Write-Error 'No working Python 3.8+ runtime is available.'
    exit 1
}

if ($env:CID_LAUNCHER_TEST -eq '1') {
    Write-Host 'Launcher preflight OK.'
    exit 0
}

$browserJob = Start-Job -ScriptBlock {
    param($Url)
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $reply = Invoke-RestMethod -Uri ($Url + '/ping') -TimeoutSec 1
            if ($reply.app -eq 'crypto-intelligence-desk') {
                Start-Process $Url
                return
            }
        } catch {}
        Start-Sleep -Milliseconds 300
    }
} -ArgumentList $appUrl

Write-Host ''
Write-Host "Crypto Intelligence Desk is starting at $appUrl"
Write-Host 'Keep this window open. Press Ctrl+C or close the window to stop.'
Write-Host ''

$exitCode = 0
try {
    & $pythonExe @pythonPrefix $proxyPath
    $exitCode = $LASTEXITCODE
} finally {
    Stop-Job -Job $browserJob -ErrorAction SilentlyContinue
    Remove-Job -Job $browserJob -ErrorAction SilentlyContinue
}
exit $exitCode

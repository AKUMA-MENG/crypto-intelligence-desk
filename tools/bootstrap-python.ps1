[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot '.runtime'
$pythonExe = Join-Path $runtimeDir 'python.exe'

if (Test-Path -LiteralPath $pythonExe) {
    & $pythonExe --version *> $null
    if ($LASTEXITCODE -eq 0) { exit 0 }
}

$version = '3.12.10'
$nativeArch = if ($env:PROCESSOR_ARCHITEW6432) {
    $env:PROCESSOR_ARCHITEW6432
} else {
    $env:PROCESSOR_ARCHITECTURE
}

switch ($nativeArch.ToUpperInvariant()) {
    'AMD64' {
        $archiveName = "python-$version-embeddable-amd64.zip"
        $expectedSha256 = '156c7eea90d58cd7e91a23f28a0056616b13e9f4cf4901b7b99b837b7848c6da'
    }
    'ARM64' {
        $archiveName = "python-$version-embeddable-arm64.zip"
        $expectedSha256 = 'be74794984f650e6d2251fb9315d92295f7545cf23529169544457a4076102f6'
    }
    'X86' {
        $archiveName = "python-$version-embeddable-win32.zip"
        $expectedSha256 = 'ecd6d9783faa928b3d84a341ce70cb92c32aa75e8cf5b82d70a9e34728f65f56'
    }
    default { throw "Unsupported Windows architecture: $nativeArch" }
}

$downloadUrl = "https://www.python.org/ftp/python/$version/$archiveName"
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("crypto-intel-runtime-" + [Guid]::NewGuid().ToString('N'))
$archivePath = Join-Path $tempRoot $archiveName
$unpackDir = Join-Path $tempRoot 'unpacked'

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    New-Item -ItemType Directory -Path $unpackDir -Force | Out-Null
    Write-Host "Downloading the official Python $version portable runtime..."
    Invoke-WebRequest -Uri $downloadUrl -OutFile $archivePath -UseBasicParsing

    $actualSha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualSha256 -ne $expectedSha256) {
        throw 'Runtime checksum verification failed. The downloaded file was not installed.'
    }

    Expand-Archive -LiteralPath $archivePath -DestinationPath $unpackDir -Force
    if (Test-Path -LiteralPath $runtimeDir) {
        [IO.Directory]::Delete($runtimeDir, $true)
    }
    [IO.Directory]::Move($unpackDir, $runtimeDir)

    if (-not (Test-Path -LiteralPath $pythonExe)) {
        throw 'Portable Python executable is missing after extraction.'
    }
    & $pythonExe --version
    if ($LASTEXITCODE -ne 0) { throw 'Portable Python failed its startup check.' }
    Write-Host 'Portable runtime is ready.'
} finally {
    if (Test-Path -LiteralPath $tempRoot) {
        [IO.Directory]::Delete($tempRoot, $true)
    }
}

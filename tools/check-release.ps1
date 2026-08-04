[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$failed = $false

$required = @(
    'index.html', 'proxy.py', 'start.bat', 'start.sh',
    'README.md', 'SECURITY.md', 'CONTRIBUTING.md',
    'THIRD_PARTY_NOTICES.md', 'LICENSE', 'VERSION', '.gitignore',
    'tools\bootstrap-python.ps1', 'tools\start.ps1', 'tools\check-release.ps1',
    'CHANGELOG.md'
)

foreach ($relative in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $relative))) {
        Write-Host "[FAIL] Missing required file: $relative" -ForegroundColor Red
        $failed = $true
    }
}

foreach ($relative in @('.runtime', '__pycache__')) {
    if (Test-Path -LiteralPath (Join-Path $projectRoot $relative)) {
        Write-Host "[FAIL] Local artifact must not be published: $relative" -ForegroundColor Red
        $failed = $true
    }
}

$rules = [ordered]@{
    'OpenAI/Anthropic-style API key' = 'sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}'
    'Google API key' = 'AIza[A-Za-z0-9_-]{20,}'
    'xAI API key' = 'xai-[A-Za-z0-9_-]{16,}'
    'AWS access key' = 'AKIA[0-9A-Z]{16}'
    'JWT token' = 'eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}'
    'Private key block' = '-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'
    'Personal Windows path' = '[A-Za-z]:\\Users\\[^\\\r\n]+'
    'Legacy private storage key' = 'localStorage\.(?:getItem|setItem)\([''"]cnt_(?:set|ev)[''"]'
}

$textExtensions = @('.html', '.py', '.bat', '.cmd', '.ps1', '.sh', '.md', '.txt', '.json', '.gitignore')
$files = Get-ChildItem -LiteralPath $projectRoot -Recurse -File | Where-Object {
    $textExtensions -contains $_.Extension.ToLowerInvariant() -or $_.Name -eq '.gitignore' -or $_.Name -eq 'VERSION'
}

foreach ($rule in $rules.GetEnumerator()) {
    $hitFiles = @()
    foreach ($file in $files) {
        $content = [IO.File]::ReadAllText($file.FullName)
        if ([regex]::IsMatch($content, $rule.Value)) {
            $hitFiles += $file.FullName.Substring($projectRoot.Length).TrimStart('\')
        }
    }
    if ($hitFiles.Count -gt 0) {
        Write-Host "[FAIL] $($rule.Key): $($hitFiles -join ', ')" -ForegroundColor Red
        $failed = $true
    }
}

if (-not ([IO.File]::ReadAllText((Join-Path $projectRoot 'index.html')).Contains('crypto_intel_oss_v1_settings'))) {
    Write-Host '[FAIL] Open-source storage namespace is missing.' -ForegroundColor Red
    $failed = $true
}

if ($failed) {
    Write-Host 'Release check failed. No suspected secret values were printed.' -ForegroundColor Red
    exit 1
}

Write-Host 'Release check passed: required files, local artifacts, storage namespace, paths and common secret patterns.' -ForegroundColor Green
exit 0

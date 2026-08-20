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
    'CHANGELOG.md',
    '.env.example',
    'requirements.txt',
    '使用说明.md',
    'background_worker.py',
    'crypto_desk\__init__.py',
    'crypto_desk\config.py',
    'crypto_desk\gemini.py',
    'crypto_desk\models.py',
    'crypto_desk\notifications\__init__.py',
    'crypto_desk\notifications\bark.py',
    'crypto_desk\policy.py',
    'crypto_desk\source_http.py',
    'crypto_desk\sources.py',
    'crypto_desk\state.py',
    'crypto_desk\transport.py',
    'crypto_desk\worker.py',
    'deploy\crypto-intelligence-desk-worker.service',
    'tests\__init__.py',
    'tests\helpers.py',
    'tests\fixtures\binance.json',
    'tests\fixtures\catcher.xml',
    'tests\fixtures\ctcn.xml',
    'tests\fixtures\odaily.xml',
    'tests\fixtures\panews.xml',
    'tests\fixtures\techflow.json',
    'tests\test_bark.py',
    'tests\test_config.py',
    'tests\test_gemini.py',
    'tests\test_policy.py',
    'tests\test_release_contract.py',
    'tests\test_sources.py',
    'tests\test_state.py',
    'tests\test_transport.py',
    'tests\test_worker.py'
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

$textExtensions = @('.html', '.py', '.bat', '.cmd', '.ps1', '.sh', '.md', '.txt', '.json', '.gitignore', '.service', '.example')
$files = Get-ChildItem -LiteralPath $projectRoot -Recurse -File | Where-Object {
    $_.Name -ne '.env' -and (
        $textExtensions -contains $_.Extension.ToLowerInvariant() -or
        $_.Name -eq '.gitignore' -or
        $_.Name -eq 'VERSION'
    )
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

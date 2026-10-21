# Self-authored offline fixtures only. No downloads, installers, or browser use.
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'app/bootstrap.ps1')
$script:checks = 0

function Assert-ReaderTest {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw "Test failed: $Message" }
    $script:checks++
}

function Assert-ReaderThrows {
    param([scriptblock]$Action, [string]$Pattern)
    $message = ''
    try { & $Action | Out-Null }
    catch { $message = $_.Exception.Message }
    Assert-ReaderTest ($message -match $Pattern) "Expected error matching: $Pattern; got: $message"
}

$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('fkg-bootstrap-test-' + [Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory((Join-Path $testRoot 'app')) | Out-Null
$configFile = Join-Path (Split-Path -Parent $PSScriptRoot) 'app/python-runtime.json'
Copy-Item -LiteralPath $configFile -Destination (Join-Path $testRoot 'app/python-runtime.json')
$config = Get-Content -LiteralPath $configFile -Raw -Encoding UTF8 | ConvertFrom-Json
$actualProbe = ${function:Invoke-PythonProbe}
$actualTest = ${function:Test-ReaderPython}
$actualFind = ${function:Find-ReaderPython}
try {
    Assert-ReaderTest ((Get-ReaderArchitecture 'AMD64') -eq 'amd64') 'x64 architecture'
    Assert-ReaderTest ((Get-ReaderArchitecture 'ARM64') -eq 'arm64') 'ARM64 architecture'
    Assert-ReaderTest ((Get-ReaderArchitecture 'x86') -eq 'win32') '32-bit architecture'
    Assert-ReaderThrows { Get-ReaderArchitecture 'OTHER' } 'Unsupported'
    foreach ($architecture in @('amd64', 'arm64', 'win32')) {
        $package = Get-ReaderRuntimePackage $config $architecture
        Assert-ReaderTest ($package.sha256 -match '^[a-f0-9]{64}$') "Pinned hash for $architecture"
        Assert-ReaderTest ($package.url.StartsWith('https://www.python.org/ftp/python/')) "Official HTTPS for $architecture"
    }
    $bad = Get-Content -LiteralPath $configFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $bad.packages.amd64.url = 'https://example.invalid/runtime.zip'
    Assert-ReaderThrows { Get-ReaderRuntimePackage $bad 'amd64' } 'Invalid official'

    $script:goodPython = Join-Path $testRoot 'example-python.exe'
    [IO.File]::WriteAllText($script:goodPython, 'Synthetic fixture, never executed.')
    function Invoke-PythonProbe {
        param([string]$Executable, [string]$Arguments)
        if ($Executable -eq $script:goodPython) { return $Executable }
        return $null
    }
    Assert-ReaderTest ((Test-ReaderPython $script:goodPython) -eq $script:goodPython) 'Valid interpreter accepted'
    Assert-ReaderTest (-not (Test-ReaderPython (Join-Path $testRoot 'missing.exe'))) 'Missing interpreter rejected'
    function Get-SystemPythonCandidates { $script:goodPython }
    Assert-ReaderTest ((Find-ReaderPython $testRoot $config 'amd64') -eq $script:goodPython) 'System interpreter reused'
    Set-Item -LiteralPath Function:Invoke-PythonProbe -Value $actualProbe

    $script:allowDownload = $false
    $script:promptCount = 0
    $script:downloadCount = 0
    function Request-ReaderDownload {
        param([string]$Url, [string]$Destination)
        $script:promptCount++
        return $script:allowDownload
    }
    function Save-ReaderRuntimeArchive {
        param([string]$Url, [string]$Path)
        $script:downloadCount++
        if ($script:failDownload) { throw 'Synthetic network failure' }
        Copy-Item -LiteralPath $script:fixtureZip -Destination $Path
    }
    Assert-ReaderThrows { Install-ReaderPython $testRoot $config 'amd64' } 'cancelled'
    Assert-ReaderTest ($script:downloadCount -eq 0) 'Declined setup never downloads'
    Assert-ReaderTest (-not (Test-Path -LiteralPath (Join-Path $testRoot 'runtime'))) 'Declined setup creates no runtime'

    function Find-ReaderPython { return $null }
    $before = $script:promptCount
    Assert-ReaderThrows { Invoke-ReaderBootstrap -Action Stop -Root $testRoot } 'No download was started'
    Assert-ReaderThrows { Invoke-ReaderBootstrap -Action Check -Root $testRoot } 'No download was started'
    Assert-ReaderTest ($script:promptCount -eq $before) 'Stop and Check never prompt for downloads'
    Set-Item -LiteralPath Function:Find-ReaderPython -Value $actualFind

    $fixture = Join-Path $testRoot 'fixture'
    [IO.Directory]::CreateDirectory($fixture) | Out-Null
    [IO.File]::WriteAllText((Join-Path $fixture 'python.exe'), 'Synthetic fixture, never executed.')
    [IO.File]::WriteAllText((Join-Path $fixture $config.pathFile), "python313.zip`r`n.`r`n")
    [IO.File]::WriteAllText((Join-Path $fixture 'LICENSE.txt'), 'Synthetic fixture license.')
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $script:fixtureZip = Join-Path $testRoot 'fixture.zip'
    [IO.Compression.ZipFile]::CreateFromDirectory($fixture, $script:fixtureZip)
    $hash = Get-ReaderFileSha256 $script:fixtureZip
    $extract = Join-Path $testRoot 'must-not-extract'
    Assert-ReaderThrows { Expand-ReaderRuntimeArchive $script:fixtureZip $extract ('0' * 64) } 'SHA-256'
    Assert-ReaderTest (-not (Test-Path -LiteralPath $extract)) 'Invalid hash rejected before extraction'

    $script:allowDownload = $true
    $script:failDownload = $true
    Assert-ReaderThrows { Install-ReaderPython $testRoot $config 'amd64' } 'network failure'
    $runtime = Join-Path $testRoot 'runtime'
    Assert-ReaderTest (@(Get-ChildItem -LiteralPath $runtime -Force | Where-Object Name -Like '.python-setup-*').Count -eq 0) 'Failed setup leaves no partial download'
    $script:failDownload = $false
    $config.packages.amd64.sha256 = $hash
    function Test-ReaderPython {
        param([string]$Executable)
        $pth = Join-Path (Split-Path -Parent $Executable) 'python313._pth'
        if ((Test-Path -LiteralPath $pth) -and ([IO.File]::ReadAllText($pth).Contains('../../app'))) { return $Executable }
        return $null
    }
    $python = Install-ReaderPython $testRoot $config 'amd64'
    Assert-ReaderTest (Test-Path -LiteralPath $python -PathType Leaf) 'Verified fixture installed atomically'
    Assert-ReaderTest (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $python) 'LICENSE.txt')) 'License retained'
    Assert-ReaderTest ([IO.File]::ReadAllText((Join-Path (Split-Path -Parent $python) $config.pathFile)).Contains('../../app')) 'Embedded app import path configured'
    Assert-ReaderTest (@(Get-ChildItem -LiteralPath $runtime -Force | Where-Object Name -Like '.python-setup-*').Count -eq 0) 'Successful setup cleans temporary files'
    $downloads = $script:downloadCount
    Assert-ReaderTest ((Find-ReaderPython $testRoot $config 'amd64') -eq $python) 'Prepared runtime reused without download'
    Assert-ReaderTest ($script:downloadCount -eq $downloads) 'Reuse causes no download'

    function Test-ReaderPython { return $null }
    Assert-ReaderThrows { Install-ReaderPython $testRoot $config 'amd64' } 'not overwritten'
    Assert-ReaderTest (Test-Path -LiteralPath $python) 'Unusable existing runtime preserved'
    $outside = Join-Path $testRoot 'keep.txt'
    [IO.File]::WriteAllText($outside, 'Keep this fixture.')
    Assert-ReaderThrows { Remove-ReaderSetupTemp $runtime $outside } 'Refusing to clean'
    Assert-ReaderTest (Test-Path -LiteralPath $outside) 'Cleanup cannot escape setup directory'
    Write-Host "Offline bootstrap checks passed: $script:checks"
}
finally {
    # This directory was uniquely created above; do not target any caller data.
    $absolute = [IO.Path]::GetFullPath($testRoot)
    if ([IO.Path]::GetDirectoryName($absolute) -eq [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') -and [IO.Path]::GetFileName($absolute) -match '^fkg-bootstrap-test-[a-f0-9]{32}$') {
        Remove-Item -LiteralPath $absolute -Recurse -Force
    }
}

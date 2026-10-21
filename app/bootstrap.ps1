# Windows PowerShell 5.1+. No system installation, PATH edits, or game downloads.
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Get-ReaderArchitecture {
    param([string]$Architecture = $(if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }))
    switch ($Architecture.ToUpperInvariant()) {
        'AMD64' { return 'amd64' }
        'ARM64' { return 'arm64' }
        'X86' { return 'win32' }
        default { throw "Unsupported Windows architecture: $Architecture. Install Python 3.10+ manually." }
    }
}

function Invoke-PythonProbe {
    param([string]$Executable, [string]$Arguments)
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo.FileName = $Executable
    $process.StartInfo.Arguments = $Arguments
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    try {
        if (-not $process.Start()) { return $null }
        if (-not $process.WaitForExit(10000)) {
            $process.Kill()
            return $null
        }
        if ($process.ExitCode -ne 0) { return $null }
        return $process.StandardOutput.ReadToEnd().Trim()
    }
    catch { return $null }
    finally { $process.Dispose() }
}

function Test-ReaderPython {
    param([string]$Executable)
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $null }
    # Import the modules needed by the reader, not just a Python version string.
    $code = "import sys,ssl,sqlite3,zlib,json,http.server,ctypes; assert sys.version_info >= (3,10); print(sys.executable)"
    $result = Invoke-PythonProbe $Executable ('-I -S -B -c "' + $code + '"')
    if ($result -and (Test-Path -LiteralPath $result -PathType Leaf)) { return $result }
    return $null
}

function Get-SystemPythonCandidates {
    # Avoid Windows Store execution aliases, which can open a store or installer.
    foreach ($name in @('python.exe', 'python3.exe')) {
        foreach ($command in @(Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue)) {
            if ($command.Source -notmatch '(?i)\\Microsoft\\WindowsApps\\') { $command.Source }
        }
    }
    foreach ($registry in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore', 'HKLM:\Software\WOW6432Node\Python\PythonCore')) {
        foreach ($key in @(Get-ChildItem -LiteralPath $registry -ErrorAction SilentlyContinue)) {
            $install = Get-Item -LiteralPath ($key.PSPath + '\InstallPath') -ErrorAction SilentlyContinue
            if ($install) {
                $exe = $install.GetValue('ExecutablePath', '')
                if ($exe) { $exe }
                $directory = $install.GetValue('', '')
                if ($directory) { Join-Path $directory 'python.exe' }
            }
        }
    }
    # Listing registered interpreters does not ask the launcher to install any.
    foreach ($command in @(Get-Command 'py.exe' -CommandType Application -All -ErrorAction SilentlyContinue)) {
        $listing = Invoke-PythonProbe $command.Source '-0p'
        foreach ($line in @($listing -split "`n")) {
            if ($line -match '(?i)([a-z]:\\.+?python(?:[0-9.]*)?\.exe)\s*$') { $Matches[1] }
        }
    }
}

function Find-ReaderPython {
    param([string]$Root, $Config, [string]$Architecture)
    $candidates = @(
        (Join-Path $Root "runtime/python-$($Config.version)-$Architecture/python.exe"),
        (Join-Path $Root 'runtime/python/python.exe')
    )
    foreach ($candidate in $candidates) {
        $usable = Test-ReaderPython $candidate
        if ($usable) { return $usable }
    }
    foreach ($candidate in @(Get-SystemPythonCandidates | Select-Object -Unique)) {
        $usable = Test-ReaderPython $candidate
        if ($usable) { return $usable }
    }
    return $null
}

function Get-ReaderRuntimePackage {
    param($Config, [string]$Architecture)
    if ($Config.version -notmatch '^3\.[0-9]+\.[0-9]+$' -or $Config.pathFile -notmatch '^python[0-9]+\._pth$') {
        throw 'Invalid Python runtime metadata.'
    }
    $package = $Config.packages.$Architecture
    $expected = "https://www.python.org/ftp/python/$($Config.version)/python-$($Config.version)-embed-$Architecture.zip"
    if ($package.url -cne $expected -or $package.sha256 -notmatch '^[a-f0-9]{64}$') {
        throw 'Invalid official Python URL or SHA-256 in runtime metadata.'
    }
    return $package
}

function Request-ReaderDownload {
    param([string]$Url, [string]$Destination)
    Write-Host 'Python 3.10+ was not found. A private Python runtime can be prepared for this reader.'
    Write-Host "Download: $Url"
    Write-Host "Save under: $Destination"
    Write-Host 'About 11 MB. No administrator rights, system installation, or PATH changes.'
    Write-Host 'The downloaded Python license is kept with the runtime. No game data is downloaded.'
    $answer = Read-Host 'Download and prepare Python now? [y/N]'
    return $answer.Trim() -match '^(?i)y(es)?$'
}

function Save-ReaderRuntimeArchive {
    param([string]$Url, [string]$Path)
    $ProgressPreference = 'SilentlyContinue'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Path -TimeoutSec 180 -MaximumRedirection 0
}

function Get-ReaderFileSha256 {
    param([string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return -join ($algorithm.ComputeHash($stream) | ForEach-Object { $_.ToString('x2') }) }
    finally { $algorithm.Dispose(); $stream.Dispose() }
}

function Expand-ReaderRuntimeArchive {
    param([string]$Archive, [string]$Destination, [string]$Sha256)
    $actual = Get-ReaderFileSha256 $Archive
    if ($actual -cne $Sha256) { throw 'Python download failed SHA-256 verification. It will not be extracted or executed.' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($Archive, $Destination)
}

function Remove-ReaderSetupTemp {
    param([string]$Runtime, [string]$Path)
    $absolute = [IO.Path]::GetFullPath($Path)
    $parent = [IO.Path]::GetDirectoryName($absolute)
    if ($parent -ne [IO.Path]::GetFullPath($Runtime) -or [IO.Path]::GetFileName($absolute) -notmatch '^\.python-setup-[a-f0-9]{32}(\.zip)?$') {
        throw 'Refusing to clean a path outside this setup operation.'
    }
    if (Test-Path -LiteralPath $absolute) {
        $item = Get-Item -LiteralPath $absolute -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing to clean a linked setup directory.' }
        Remove-Item -LiteralPath $absolute -Recurse -Force
    }
}

function Install-ReaderPython {
    param([string]$Root, $Config, [string]$Architecture)
    $package = Get-ReaderRuntimePackage $Config $Architecture
    $runtime = Join-Path $Root 'runtime'
    $destination = Join-Path $runtime "python-$($Config.version)-$Architecture"
    if (-not (Request-ReaderDownload $package.url $destination)) { throw 'Python setup cancelled. No download was started.' }
    [IO.Directory]::CreateDirectory($runtime) | Out-Null
    $lock = $null
    $stage = Join-Path $runtime ('.python-setup-' + [Guid]::NewGuid().ToString('N'))
    $archive = $stage + '.zip'
    try {
        try { $lock = [IO.File]::Open((Join-Path $runtime '.bootstrap.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
        catch { throw 'Another setup may be running, or the folder is not writable. Close that setup or use a writable folder, then retry.' }
        if (Test-Path -LiteralPath $destination) {
            $usable = Test-ReaderPython (Join-Path $destination 'python.exe')
            if ($usable) { return $usable }
            throw "Existing runtime is unusable and was not overwritten: $destination. Move it aside or install Python 3.10+ manually."
        }
        Write-Host 'Downloading official Python runtime...'
        Save-ReaderRuntimeArchive $package.url $archive
        Write-Host 'Verifying SHA-256 and preparing local runtime...'
        Expand-ReaderRuntimeArchive $archive $stage $package.sha256
        $pathFile = Join-Path $stage $Config.pathFile
        if (-not (Test-Path -LiteralPath $pathFile -PathType Leaf) -or -not (Test-Path -LiteralPath (Join-Path $stage 'LICENSE.txt') -PathType Leaf)) {
            throw 'The verified archive does not contain the expected Python runtime files.'
        }
        # Embedded Python is isolated. Explicitly allow only this reader's app.
        [IO.File]::AppendAllText($pathFile, "`r`n../../app`r`n", [Text.Encoding]::ASCII)
        if (-not (Test-ReaderPython (Join-Path $stage 'python.exe'))) { throw 'The downloaded Python cannot run on this Windows installation.' }
        Move-Item -LiteralPath $stage -Destination $destination
        Write-Host "Python is ready: $destination"
        return (Join-Path $destination 'python.exe')
    }
    finally {
        try {
            Remove-ReaderSetupTemp $runtime $stage
            Remove-ReaderSetupTemp $runtime $archive
        }
        finally { if ($lock) { $lock.Dispose() } }
    }
}

function Invoke-ReaderBootstrap {
    param(
        [ValidateSet('Start', 'Stop', 'Check')][string]$Action,
        [string[]]$ReaderArguments = @(),
        [string]$Root = (Split-Path -Parent $PSScriptRoot)
    )
    $config = Get-Content -LiteralPath (Join-Path $Root 'app/python-runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $architecture = Get-ReaderArchitecture
    $python = Find-ReaderPython $Root $config $architecture
    if (-not $python) {
        if ($Action -ne 'Start') { throw 'Python 3.10+ is unavailable. Run Start Reader.cmd to prepare it, or install Python manually. No download was started.' }
        $python = Install-ReaderPython $Root $config $architecture
    }
    $target = Join-Path $Root 'app/launcher.py'
    $forward = @($ReaderArguments)
    if ($Action -eq 'Stop') { $forward = @('--stop') + $forward }
    if ($Action -eq 'Check') { $target = Join-Path $Root 'app/verify-release.py' }
    & $python -X utf8 -E -s -S -B $target @forward | Out-Host
    return $LASTEXITCODE
}

# Dot-sourcing loads helpers for offline tests, without running a reader.
if ($MyInvocation.InvocationName -ne '.') {
    try {
        if ($args.Count -lt 1) { throw 'Usage: bootstrap.ps1 Start|Stop|Check [reader arguments]' }
        $action = $args[0]
        $forward = @($args | Select-Object -Skip 1)
        exit (Invoke-ReaderBootstrap -Action $action -ReaderArguments $forward)
    }
    catch {
        [Console]::Error.WriteLine('Error: ' + $_.Exception.Message)
        [Console]::Error.WriteLine('If setup fails, install Python 3.10+ from https://www.python.org/downloads/ and retry. Do not disable system security settings.')
        exit 1
    }
}

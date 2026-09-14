param([string]$InstallHome = "$env:LOCALAPPDATA\CodexTokenSaver", [string]$CodexHome = $(if ($env:CODEX_HOME) {$env:CODEX_HOME} else {Join-Path $env:USERPROFILE '.codex'}), [switch]$NoPath)
$ErrorActionPreference = 'Stop'
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Windows x64 is required' }
$ctsPython = $null
foreach ($candidate in @('python','python3')) {
    $found = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($found) {
        $version = & $found.Source -c 'import sys; print(int((3,11)<=sys.version_info[:2]<(3,14)))' 2>$null
        if ($LASTEXITCODE -eq 0 -and $version -eq '1') {$ctsPython = $found.Source; break}
    }
}
if (-not $ctsPython) {
    $runtime = Join-Path $InstallHome 'runtime'
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    $archive = Join-Path $runtime 'uv.zip'
    Invoke-WebRequest 'https://github.com/astral-sh/uv/releases/download/0.8.22/uv-x86_64-pc-windows-msvc.zip' -OutFile $archive -UseBasicParsing
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne '5049375aa2a5162f132b2c1cb992e25d42d47d934cab8c174dbe6f60973dcc12') {throw 'uv checksum mismatch'}
    Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $runtime 'uv') -Force
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $runtime 'python'
    $env:UV_CACHE_DIR = Join-Path $runtime 'cache'
    $uv = Join-Path $runtime 'uv\uv.exe'
    & $uv python install --no-bin --no-registry 3.13.7
    if ($LASTEXITCODE) {throw 'Python provisioning failed'}
    $ctsPython = & $uv python find --managed-python 3.13.7
    if ($LASTEXITCODE) {throw 'Managed Python not found'}
}
$ctsArgs = @((Join-Path $PSScriptRoot 'scripts\bootstrap.py'), '--home', $InstallHome, '--codex-home', $CodexHome)
if ($NoPath) {$ctsArgs += '--no-path'}
& $ctsPython @ctsArgs
if ($LASTEXITCODE) {throw 'Installation failed; existing Codex backups are retained'}
$env:PATH = (Join-Path $InstallHome 'bin') + ';' + $env:PATH

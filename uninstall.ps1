param([string]$InstallHome = "$env:LOCALAPPDATA\CodexTokenSaver")
$ErrorActionPreference = 'Stop'
& (Join-Path $InstallHome 'venv\Scripts\python.exe') -m codex_token_saver --home $InstallHome uninstall
if ($LASTEXITCODE) {throw 'Uninstall failed; inspect retained configuration'}

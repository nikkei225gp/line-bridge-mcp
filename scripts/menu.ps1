param(
    [ValidateSet('Menu', 'Install', 'Login', 'Start', 'Status', 'Stop', 'Codex', 'OpenCode', 'Doctor')]
    [string]$Action = 'Menu',
    [string]$DataDir = (Join-Path $env:LOCALAPPDATA 'LineBridge\profiles\default'),
    [ValidateSet('android', 'ipad', 'windows', 'mac')][string]$Device = 'android'
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root '.venv\Scripts\python.exe'
$launcher = Join-Path $root 'bin\linebridge.py'

function Invoke-Bridge([string[]]$BridgeArguments) {
    if (-not (Test-Path -LiteralPath $python)) {
        throw 'Install first (option 1). See START-HERE.md.'
    }
    & $python -B $launcher --data-dir $DataDir @BridgeArguments
    if ($LASTEXITCODE -ne 0) { throw 'The operation failed. Keep only the error CODE when asking for help.' }
}

function Invoke-Action([string]$SelectedAction) {
    switch ($SelectedAction) {
        'Install' { & (Join-Path $PSScriptRoot 'install.ps1') -DataDir $DataDir }
        'Login' {
            if ($Action -eq 'Menu') {
                $selectedDevice = Read-Host 'Device: android (default), ipad, windows, mac'
                if ($selectedDevice) {
                    if ($selectedDevice -notin @('android','ipad','windows','mac')) { throw 'Unknown login device.' }
                    $Device = $selectedDevice
                }
            }
            Write-Host ('A local QR window will open. Device: ' + $Device)
            Write-Host 'An existing session is never replaced here. See START-HERE.md for explicit re-login.'
            Invoke-Bridge -BridgeArguments @('login', 'unified', '--device', $Device)
        }
        'Start' { Invoke-Bridge -BridgeArguments @('start') }
        'Status' { Invoke-Bridge -BridgeArguments @('status') }
        'Stop' { Invoke-Bridge -BridgeArguments @('stop') }
        'Codex' {
            Write-Host 'Add the following block to your Codex config. Preserve other settings.'
            Invoke-Bridge -BridgeArguments @('clients', '--format', 'codex')
        }
        'OpenCode' {
            Write-Host 'Merge the following server entry into your OpenCode V2 config. Preserve other settings.'
            Invoke-Bridge -BridgeArguments @('clients', '--format', 'opencode')
        }
        'Doctor' { Invoke-Bridge -BridgeArguments @('doctor') }
    }
}

if ($Action -ne 'Menu') {
    Invoke-Action $Action
    exit 0
}

Write-Host 'LineBridge 0.3.0 - Windows x64'
Write-Host 'Read START-HERE.md (Japanese) before login. Internet is needed for install.'
Write-Host 'Unofficial LINE client. Chat text you request may be sent to your AI provider.'
Write-Host 'Sending is ON after login. Android is default. Monitoring is OFF.'
while ($true) {
    Write-Host ''
    Write-Host '1 Install     2 QR login     3 Start     4 Status     5 Stop'
    Write-Host '6 Codex config     7 OpenCode V2 config     8 Doctor     0 Exit menu'
    Write-Host 'Exit menu does not stop the bridge. Use 5 to stop it.'
    $choice = Read-Host 'Select'
    if ($choice -eq '0') { break }
    $actions = @{ '1'='Install'; '2'='Login'; '3'='Start'; '4'='Status'; '5'='Stop'; '6'='Codex'; '7'='OpenCode'; '8'='Doctor' }
    if (-not $actions.ContainsKey($choice)) { Write-Host 'Select a number from 0 to 8.'; continue }
    try { Invoke-Action $actions[$choice] }
    catch { Write-Host $_.Exception.Message -ForegroundColor Red }
}

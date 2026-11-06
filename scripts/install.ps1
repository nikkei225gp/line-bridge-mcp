param(
    [string]$DataDir = (Join-Path $env:LOCALAPPDATA 'LineBridge\profiles\default')
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if (-not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess) {
    throw 'Windows x64 and 64-bit PowerShell are required.'
}
if (Test-Path -LiteralPath (Join-Path $DataDir 'config.json')) {
    throw 'This profile already exists. Use the menu to log in/start, or specify a NEW -DataDir. Existing profiles are not overwritten.'
}
Push-Location $root
try {
    $uvDirectory = Join-Path $root 'runtime\uv'
    $uvExecutable = Join-Path $uvDirectory 'uv.exe'
    $uvMarker = Join-Path $uvDirectory '.linebridge-source-sha256'
    $uvPin = (Get-Content -LiteralPath (Join-Path $root 'runtime-pins.json') -Raw | ConvertFrom-Json).windows_x64.uv
    if (Test-Path -LiteralPath $uvDirectory) {
        if (-not (Test-Path -LiteralPath $uvExecutable) -or -not (Test-Path -LiteralPath $uvMarker) -or
            (Get-Content -LiteralPath $uvMarker -Raw).Trim() -ne $uvPin.sha256) { throw 'Existing portable uv is not verified.' }
    } else {
        $runtimeDirectory = Join-Path $root 'runtime'
        New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
        $uvTemporary = Join-Path $runtimeDirectory ('uv-bootstrap-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $uvTemporary | Out-Null
        try {
            $uvArchive = Join-Path $uvTemporary 'uv.zip'
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -UseBasicParsing -Uri $uvPin.url -OutFile $uvArchive
            if ((Get-FileHash -LiteralPath $uvArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $uvPin.sha256) { throw 'Portable uv hash mismatch.' }
            $uvExtracted = Join-Path $uvTemporary 'extracted'
            Expand-Archive -LiteralPath $uvArchive -DestinationPath $uvExtracted
            $uvCandidate = @(Get-ChildItem -LiteralPath $uvExtracted -Recurse -Filter uv.exe -File)
            if ($uvCandidate.Count -ne 1) { throw 'Portable uv executable missing.' }
            New-Item -ItemType Directory -Path $uvDirectory | Out-Null
            Copy-Item -LiteralPath $uvCandidate[0].FullName -Destination $uvExecutable
            Set-Content -LiteralPath $uvMarker -Value $uvPin.sha256 -NoNewline -Encoding ASCII
        } finally {
            $uvResolved = [IO.Path]::GetFullPath($uvTemporary)
            $runtimeResolved = [IO.Path]::GetFullPath($runtimeDirectory).TrimEnd('\') + '\'
            if ($uvResolved.StartsWith($runtimeResolved, [StringComparison]::OrdinalIgnoreCase)) {
                Remove-Item -LiteralPath $uvResolved -Recurse -Force
            }
        }
    }
    $syncArguments = @('sync', '--frozen', '--no-dev', '--no-editable', '--python', '3.11.15')
    & $uvExecutable @syncArguments
    if ($LASTEXITCODE -ne 0) { throw 'Locked Python installation failed' }
    $python = Join-Path $root '.venv\Scripts\python.exe'
    & $python -B (Join-Path $root 'tools\bootstrap-runtime.py')
    if ($LASTEXITCODE -ne 0) { throw 'Pinned runtime installation failed' }
    & $python -B (Join-Path $root 'bin\linebridge.py') --data-dir $DataDir init
    if ($LASTEXITCODE -ne 0) { throw 'Profile initialization refused; existing profiles are not overwritten' }
    & $python -B (Join-Path $root 'bin\linebridge.py') --data-dir $DataDir runtime `
        --deno (Join-Path $root 'runtime\deno\deno.exe') `
        --linejs (Join-Path $root 'runtime\linejs') `
        --deno-cache (Join-Path $root 'runtime\deno-cache')
    if ($LASTEXITCODE -ne 0) { throw 'Runtime configuration failed' }
    Write-Host 'Installed. Android is default; sending is enabled after login. Monitoring is off.'
    Write-Host 'No login, message, client configuration or scheduled task was started.'
} finally {
    Pop-Location
}

param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
& (Join-Path $root '.venv\Scripts\python.exe') -B (Join-Path $root 'bin\linebridge.py') @Arguments
exit $LASTEXITCODE

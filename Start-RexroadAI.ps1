# Start the existing local model and Rexroad AI without changing Windows startup.
[CmdletBinding()]
param(
    [string]$ModelManager = "$env:USERPROFILE\.lmstudio\bin\lms.exe"
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Rexroad AI virtual environment was not found.' }
if (-not (Test-Path -LiteralPath $ModelManager)) { throw 'LM Studio model manager was not found.' }

$loadedModels = & $ModelManager ps 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the local models.' }
if ($loadedModels -notmatch 'qwen3-coder-30b-a3b-instruct') {
    & $ModelManager load qwen3-coder-30b-a3b-instruct --context-length 16384 --identifier qwen3-coder-30b-a3b-instruct -y
    if ($LASTEXITCODE -ne 0) { throw 'Could not load the configured local model.' }
}
& $ModelManager server start --port 1234 --bind 127.0.0.1
if ($LASTEXITCODE -ne 0) { throw 'Could not start the local model server.' }
$modelInventory = Invoke-RestMethod -Uri 'http://127.0.0.1:1234/v1/models' -TimeoutSec 10
if ('qwen3-coder-30b-a3b-instruct' -notin $modelInventory.data.id) {
    throw 'The configured model is not available through the local API.'
}

$listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if (-not $listener) {
    Start-Process -FilePath $pythonPath -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectRoot '.venv\runtime-repair.stdout.log') -RedirectStandardError (Join-Path $projectRoot '.venv\runtime-repair.stderr.log')
}
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2
        if ($health.service -ne 'rexroad-ai') { throw 'Port 8000 belongs to another service.' }
        if ($health.status -eq 'ok') {
            Write-Output 'Rexroad AI and its local model are ready: http://127.0.0.1:8000/chat'
            exit 0
        }
    } catch {
        if ($listener) { throw }
    }
    Start-Sleep -Seconds 1
}
throw 'Rexroad AI did not become ready. Check .venv\runtime-repair.stderr.log.'

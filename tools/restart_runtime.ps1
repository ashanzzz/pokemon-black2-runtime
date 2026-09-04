[CmdletBinding()]
param(
    [string]$HttpHost = "127.0.0.1",
    [int]$HttpPort = 8765,
    [string]$BridgeHost = "127.0.0.1",
    [int]$BridgePort = 8766
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$baseUri = "http://${HttpHost}:${HttpPort}"
$startedByFallback = $false

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Workspace Python was not found: $pythonPath"
}

Write-Host "Pokemon Black 2 Runtime restart helper" -ForegroundColor Cyan
Write-Host "Project: $projectRoot"
Write-Host "HTTP:   $baseUri"

# Prefer the running service's authenticated local restart API. This lets the
# current process record the request before it retires.
try {
    $control = Invoke-RestMethod -Uri "$baseUri/api/v1/runtime/control" -TimeoutSec 2
    if ($control.capabilities.restart_backend -and $control.restart_token) {
        $headers = @{ "X-Runtime-Restart-Token" = [string]$control.restart_token }
        Invoke-RestMethod -Method Post -Uri "$baseUri/api/v1/runtime/restart" -Headers $headers -TimeoutSec 3 | Out-Null
        Write-Host "Web restart accepted by runtime-control v$($control.version)." -ForegroundColor Green
    } else {
        throw "The running service does not advertise restart_backend."
    }
} catch {
    Write-Host "The old process has no web restart API; using one-time bootstrap restart." -ForegroundColor Yellow
    $health = $null
    try { $health = Invoke-RestMethod -Uri "$baseUri/health" -TimeoutSec 2 } catch {}
    $healthPort = $health.ports.http.port
    if (-not $health -or $health.status -ne "ok" -or $health.backend_http -ne "online" -or $healthPort -ne $HttpPort) {
        throw "Port $HttpPort is not the expected Pokemon Black 2 Runtime; nothing was stopped."
    }

    $listener = Get-NetTCPConnection -LocalAddress $HttpHost -LocalPort $HttpPort -State Listen -ErrorAction Stop | Select-Object -First 1
    $runtimeProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    if (-not $runtimeProcess -or $runtimeProcess.CommandLine -notmatch "run_runtime\.py|backend\.black2\.api\.app") {
        throw "PID $($listener.OwningProcess) does not look like this project's runtime; nothing was stopped."
    }

    $oldPid = [int]$listener.OwningProcess
    Write-Host "Stopping verified runtime PID $oldPid ..."
    Stop-Process -Id $oldPid -ErrorAction Stop
    $deadline = (Get-Date).AddSeconds(12)
    do {
        Start-Sleep -Milliseconds 250
        $remaining = Get-NetTCPConnection -LocalPort $HttpPort -State Listen -ErrorAction SilentlyContinue
    } while ($remaining -and (Get-Date) -lt $deadline)
    if ($remaining) { throw "Port $HttpPort did not close after stopping PID $oldPid." }

    $env:BLACK2_RESTART_PARENT_PID = [string]$oldPid
    $arguments = @(
        "run_runtime.py",
        "--host", $HttpHost,
        "--port", [string]$HttpPort,
        "--bridge-host", $BridgeHost,
        "--bridge-port", [string]$BridgePort
    )
    $newProcess = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
    Write-Host "Started replacement PID $($newProcess.Id)."
    $startedByFallback = $true
}

Write-Host "Waiting for the version registry and monitor page ..."
$ready = $false
$versionReport = $null
$deadline = (Get-Date).AddSeconds(35)
do {
    Start-Sleep -Milliseconds 750
    try {
        $versionReport = Invoke-RestMethod -Uri "$baseUri/api/v1/runtime/versions" -TimeoutSec 2
        $page = Invoke-WebRequest -Uri "$baseUri/runtime-monitor" -TimeoutSec 2
        $ready = ($page.StatusCode -eq 200 -and $versionReport.release)
    } catch { $ready = $false }
} while (-not $ready -and (Get-Date) -lt $deadline)

if (-not $ready) {
    throw "The replacement did not become ready within 35 seconds. Check logs/runtime_control.jsonl."
}

$mode = if ($startedByFallback) { "bootstrap" } else { "web-control" }
Write-Host "Runtime v$($versionReport.release) is ready ($mode)." -ForegroundColor Green
Write-Host "Monitor: $baseUri/runtime-monitor" -ForegroundColor Cyan

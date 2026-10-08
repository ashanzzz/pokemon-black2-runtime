# bicycle_status.ps1
try {
    $resp = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/v1/player/bicycle" -Method Get
    Write-Host "[实时] 自行车状态与环境能力:" -ForegroundColor Cyan
    $resp | ConvertTo-Json -Depth 3
} catch {
    Write-Host "[错误] 无法获取自行车状态:" -ForegroundColor Red
    $_.ErrorDetails.Message
}

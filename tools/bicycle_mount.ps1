# bicycle_mount.ps1
try {
    $resp = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/v1/player/bicycle/mount" -Method Post -ContentType "application/json"
    Write-Host "[成功] 骑上自行车响应:" -ForegroundColor Green
    $resp | ConvertTo-Json -Depth 3
} catch {
    Write-Host "[拒绝/提示] 骑上自行车未完成:" -ForegroundColor Yellow
    $_.ErrorDetails.Message
}

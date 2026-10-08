# tools/advance_dialogue.ps1
param (
    [string]$HostUrl = "http://127.0.0.1:8765",
    [int]$MaxSteps = 20,
    [string]$Button = "A",
    [int]$StepDelayMs = 250
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$body = @{
    max_steps = $MaxSteps
    button = $Button
    step_delay_ms = $StepDelayMs
    stop_on_choice = $true
} | ConvertTo-Json

try {
    $res = Invoke-RestMethod -Uri "$HostUrl/api/v1/dialogue/skip" -Method Post -Body $body -ContentType "application/json"
    if ($res.dialogue_was_active) {
        Write-Host "================== 文字打印机实时读取内容 ==================" -ForegroundColor Cyan
        Write-Host $res.captured_text -ForegroundColor White
        Write-Host "============================================================" -ForegroundColor Cyan
        Write-Host "`n[成功] 对话已全部跳过！共推进 $($res.steps_taken) 步，已完全回落至大地图自由探索状态。" -ForegroundColor Green
        Write-Host "当前状态: $($res.final_screen.screen_type) | 可移动: $($res.final_screen.can_move_player)" -ForegroundColor Gray
    } else {
        Write-Host "[状态] 当前无活跃对话，主角已处于【$($res.final_screen.screen_type)】自由探索态。" -ForegroundColor Yellow
    }
} catch {
    Write-Host "API 调用失败: $($_.Exception.Message)" -ForegroundColor Red
}

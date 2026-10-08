# tools/watch_radar.ps1 - Pokemon Black 2 Real-Time Radar HUD
param (
    [int]$Radius            = 0,
    [double]$IntervalSec    = 0,
    [int]$IntervalMs        = 100,
    [string]$HostUrl        = 'http://127.0.0.1:8765',
    [string]$Scope          = 'local',
    [string]$Mode           = 'coarse',
    [object]$ZoneId         = $null,
    [object]$X              = $null,
    [object]$Y              = $null,
    [object]$Z              = $null,
    [switch]$Full,
    [switch]$Detail,
    [switch]$Coarse,
    [switch]$RefreshPlayerRuntime,
    [int]$DiscoveryIntervalMs = 3000,
    [switch]$Once
)

if ($Detail) { $Mode = 'detail' }
if ($Coarse) { $Mode = 'coarse' }

if ($Radius -le 0) {
    if ($Mode -eq 'detail') { $Radius = 5 }   # 11x11 (直径 11)
    else                    { $Radius = 15 }  # 31x31 (直径 31)
}

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { $OutputEncoding            = [System.Text.Encoding]::UTF8 } catch {}

if ($IntervalSec -gt 0) { $IntervalMs = [int]($IntervalSec * 1000) }

# Adjust console width and height gracefully
$neededWidth = [Math]::Max(100, ($Radius * 2 + 1) * 3 + 12)
try {
    $raw = $Host.UI.RawUI
    if ($raw.BufferSize.Width -lt $neededWidth) {
        $raw.BufferSize = New-Object System.Management.Automation.Host.Size($neededWidth, [Math]::Max(300, $raw.BufferSize.Height))
        $raw.WindowSize = New-Object System.Management.Automation.Host.Size([Math]::Min($neededWidth, $raw.MaxWindowSize.Width), $raw.WindowSize.Height)
    }
} catch {}

try { [Console]::CursorVisible = $false } catch {}
Clear-Host

$lastDiscovery = [DateTime]::MinValue
$prevLineCount = 0
$lastFrameTotalMs = $null

while ($true) {
    $frameStartedTick = [System.Diagnostics.Stopwatch]::GetTimestamp()
    if ($RefreshPlayerRuntime -and ((Get-Date) - $lastDiscovery).TotalMilliseconds -ge $DiscoveryIntervalMs) {
        try { Invoke-RestMethod -Uri ($HostUrl + '/api/v1/player/runtime') -Method Get | Out-Null } catch {}
        $lastDiscovery = Get-Date
    }

    $body = @{ radius = $Radius; text_map = 'true'; scope = $Scope; mode = $Mode }
    if ($null -ne $ZoneId) { $body['zone_id'] = $ZoneId }
    if ($null -ne $X)      { $body['x'] = $X }
    if ($null -ne $Y)      { $body['y'] = $Y }
    if ($null -ne $Z)      { $body['z'] = $Z }
    $endpoint = $HostUrl + '/api/v1/navigation/radar/grid'

    $timeStr = Get-Date -Format 'HH:mm:ss.fff'
    $success = $false
    $rawMapText = ''
    $errMsg = ''
    $requestStartedTick = [System.Diagnostics.Stopwatch]::GetTimestamp()
    try {
        $rawMapText = Invoke-RestMethod -Uri $endpoint -Method Get -Body $body
        $success = $true
    } catch {
        $errMsg = $_.Exception.Message
    }
    $requestElapsedMs = (([System.Diagnostics.Stopwatch]::GetTimestamp() - $requestStartedTick) * 1000.0 / [System.Diagnostics.Stopwatch]::Frequency)
    $renderStartedTick = [System.Diagnostics.Stopwatch]::GetTimestamp()

    $bufWidth = $Host.UI.RawUI.BufferSize.Width
    $printWidth = [Math]::Max(10, $bufWidth - 1)

    $outLines = New-Object System.Collections.Generic.List[string]

    if ($success) {
        $sections = $rawMapText -split "`n`n"
        $titleSec  = if ($sections.Count -ge 1) { $sections[0].Trim() } else { '' }
        $gridSec   = if ($sections.Count -ge 2) { $sections[1].TrimEnd() } else { '' }

        $gateSec    = ''
        $trainerSec = ''
        $boulderSec = ''
        $warpSec    = ''
        $elevSec    = ''
        $shoreSec   = ''
        $legendSec  = ''
        foreach ($s in $sections) {
            if ($s -match '剧情封路与道闸') { $gateSec = $s.Trim() }
            elseif ($s -match '训练家对战视线警戒') { $trainerSec = $s.Trim() }
            elseif ($s -match '场景机关与怪力巨石状态') { $boulderSec = $s.Trim() }
            elseif ($s -match '门/出入口与传送目的地') { $warpSec = $s.Trim() }
            elseif ($s -match '高度跃迁与楼梯') { $elevSec = $s.Trim() }
            elseif ($s -match '水岸与冲浪') { $shoreSec = $s.Trim() }
            elseif ($s -match '图例说明') { $legendSec = $s.Trim() }
        }

        # 1. Header Card
        $outLines.Add(('=' * 28 + ' 宝可梦黑2 实时周围雷达 [HUD] [' + $timeStr + '] ' + '=' * 28))
        $outLines.Add($titleSec)
        $modeLabel = if ($Mode -eq 'detail') { '详细材质' } else { '简明导航' }
        $outLines.Add(('半径: ' + $Radius + ' (直径: ' + ($Radius * 2 + 1) + ') | 刷新: ' + $IntervalMs + 'ms | 模式: ' + $modeLabel + ' (' + $Mode + ') | 按 Ctrl+C 退出'))
        $outLines.Add(('-' * 76))

        # 2. Grid Map
        foreach ($gLine in ($gridSec -split "`n")) {
            $outLines.Add($gLine.TrimEnd("`r"))
        }

        # 3. Tactical Story Gate Card
        if ($gateSec) {
            $outLines.Add('')
            $outLines.Add('>>> [!] 剧情封路与道闸拦截分析 (Story Gate Intel) <<<')
            foreach ($gLine in ($gateSec -split "`n")) {
                $clean = $gLine.TrimEnd("`r")
                if ($clean -and -not ($clean -match '剧情封路与道闸')) {
                    $outLines.Add(('  ' + $clean))
                }
            }
        }

        # 3.5. Tactical Trainer Sight Card
        if ($trainerSec) {
            $outLines.Add('')
            $outLines.Add('>>> [T] 训练家对战视线警戒 (Active Trainer Sights) <<<')
            foreach ($tLine in ($trainerSec -split "`n")) {
                $clean = $tLine.TrimEnd("`r")
                if ($clean -and -not ($clean -match '训练家对战视线警戒')) {
                    $outLines.Add(('  ' + $clean))
                }
            }
        }

        # 3.8. Tactical Boulders & Field Mechanics Card
        if ($boulderSec) {
            $outLines.Add('')
            $outLines.Add('>>> [=] 场景机关与怪力巨石状态 (Boulders & Field Mechanics) <<<')
            foreach ($bLine in ($boulderSec -split "`n")) {
                $clean = $bLine.TrimEnd("`r")
                if ($clean -and -not ($clean -match '场景机关与怪力巨石状态')) {
                    $outLines.Add(('  ' + $clean))
                }
            }
        }

        # 4. Doors & Warps Card (Warp Trigger Intel)
        if ($warpSec) {
            $outLines.Add('')
            $outLines.Add('>>> [D] 传送门与出入口 (Doors & Warps Intel) <<<')
            foreach ($wLine in ($warpSec -split "`n")) {
                $clean = $wLine.TrimEnd("`r")
                if ($clean -and -not ($clean -match '门/出入口与传送目的地')) {
                    $outLines.Add(('  ' + $clean))
                }
            }
            $outLines.Add('  * 操作提示: 站上 [D] 门垫后朝出口方向迈出一步即可触发过图传送 (如室内门垫 19 迈向 20)')
        }

        # 5. Full Diagnostic lists or Standard Compact Legend
        if ($Full) {
            if ($elevSec) {
                $outLines.Add('')
                $outLines.Add($elevSec.TrimEnd("`r"))
            }
            if ($shoreSec) {
                $outLines.Add('')
                $outLines.Add($shoreSec.TrimEnd("`r"))
            }
            if ($legendSec) {
                $outLines.Add('')
                $outLines.Add($legendSec.TrimEnd("`r"))
            }
        } else {
            $outLines.Add('')
            if ($Mode -eq 'detail') {
                $outLines.Add('图例速查 [详细材质模式]: [P]=主角 [.]=水泥路 [,]=黄土路 ["]=草坪路 [*]=普通草 [X]=深色草 [B]=建筑 [T]=树木 [#]=围栏 [!]=道闸 [G]=巨石 [=]=填平 [D]=传送门')
            } else {
                $outLines.Add('图例速查 [简明导航模式]: [P]=主角 [.]=通路 [*]=草丛 [#]=障碍 [!]=道闸 [T]=训练家 [N]=居民 [G]=巨石 [=]=填平 [U]=坑洞 [D]=传送门')
            }
        }
    } else {
        $outLines.Add(('=' * 28 + ' 宝可梦黑2 实时周围雷达 [' + $timeStr + '] ' + '=' * 28))
        $outLines.Add(('雷达连接中 / 等待采样... (' + $errMsg + ')'))
    }

    $renderElapsedMs = (([System.Diagnostics.Stopwatch]::GetTimestamp() - $renderStartedTick) * 1000.0 / [System.Diagnostics.Stopwatch]::Frequency)
    $preWriteElapsedMs = (([System.Diagnostics.Stopwatch]::GetTimestamp() - $frameStartedTick) * 1000.0 / [System.Diagnostics.Stopwatch]::Frequency)
    $plannedWaitMs = [Math]::Max(0, $IntervalMs - [int]$preWriteElapsedMs)
    $lastFrameLabel = if ($null -eq $lastFrameTotalMs) { 'n/a' } else { ([Math]::Round($lastFrameTotalMs, 1)).ToString() + 'ms' }
    $metricLine = ('目标:' + $IntervalMs + 'ms | RPC:' + ([Math]::Round($requestElapsedMs, 1)) + 'ms | 组帧:' + ([Math]::Round($renderElapsedMs, 1)) + 'ms | 上帧总:' + $lastFrameLabel + ' | 预计等待:' + $plannedWaitMs + 'ms')
    if ($outLines.Count -ge 3) { $outLines[2] = $metricLine }

    # Pad shorter frames with blanks to overwrite previous frame residues
    $currCount = $outLines.Count
    while ($outLines.Count -lt $prevLineCount) {
        $outLines.Add('')
    }
    $prevLineCount = $currCount

    # Zero-Jitter Console Assembly:
    # 1. Clamp line width strictly to BufferWidth - 1 to prevent auto-wrapping
    for ($i = 0; $i -lt $outLines.Count; $i++) {
        $l = $outLines[$i]
        $outLines[$i] = if ($l.Length -gt $printWidth) { $l.Substring(0, $printWidth) } else { $l.PadRight($printWidth) }
    }

    # 2. Never emit a trailing newline on the bottom-most line! (prevents scroll-bounce)
    $frameText = $outLines -join "`n"

    try { [Console]::SetCursorPosition(0, 0) } catch { Clear-Host }
    [Console]::Write($frameText)
    $lastFrameTotalMs = (([System.Diagnostics.Stopwatch]::GetTimestamp() - $frameStartedTick) * 1000.0 / [System.Diagnostics.Stopwatch]::Frequency)

    if ($Once) { break }
    # IntervalMs is a target frame period, not an additional sleep after HTTP.
    # Subtract request/render time so network latency does not accumulate.
    $elapsedMs = (([System.Diagnostics.Stopwatch]::GetTimestamp() - $frameStartedTick) * 1000.0 / [System.Diagnostics.Stopwatch]::Frequency)
    $remainingMs = [Math]::Max(0, $IntervalMs - [int]$elapsedMs)
    if ($remainingMs -gt 0) { Start-Sleep -Milliseconds $remainingMs }
}

try { [Console]::CursorVisible = $true } catch {}

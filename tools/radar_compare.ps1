# tools/radar_compare.ps1 - Pokemon Black 2 Zero-Jitter Dual-Plane HUD
param (
    [int]$IntervalMs        = 100,
    [string]$HostUrl        = 'http://127.0.0.1:8765',
    [switch]$FullView,
    [switch]$Once
)

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { $OutputEncoding            = [System.Text.Encoding]::UTF8 } catch {}

# 1. Zero-Jitter Console Dimension Adaptation
$neededWidth = 110
try {
    $raw = $Host.UI.RawUI
    # Width adjustment
    if ($raw.BufferSize.Width -lt $neededWidth) {
        $raw.BufferSize = New-Object System.Management.Automation.Host.Size($neededWidth, $raw.BufferSize.Height)
        $raw.WindowSize = New-Object System.Management.Automation.Host.Size([Math]::Min($neededWidth, $raw.MaxWindowSize.Width), $raw.WindowSize.Height)
    }
    # Height adjustment: if FullView (31x31 + 11x11), ensure window is tall enough to prevent scroll-bounce
    $targetHeight = if ($FullView) { [Math]::Min($raw.MaxWindowSize.Height, 52) } else { [Math]::Min($raw.MaxWindowSize.Height, 32) }
    if ($raw.WindowSize.Height -lt $targetHeight) {
        $raw.WindowSize = New-Object System.Management.Automation.Host.Size($raw.WindowSize.Width, $targetHeight)
    }
} catch {}

try { [Console]::CursorVisible = $false } catch {}
Clear-Host

$prevLineCount = 0
$lastFrameTotalMs = $null

while ($true) {
    $frameStartedTick = [System.Diagnostics.Stopwatch]::GetTimestamp()
    $timeStr = Get-Date -Format 'HH:mm:ss.fff'

    $urlSlices = $HostUrl + '/api/v1/navigation/radar/slices?radius=5&mode=detail&text_map=true'
    $urlCoarse = if ($FullView) { $HostUrl + '/api/v1/navigation/radar/grid?radius=15&mode=coarse&text_map=true' } else { $null }

    $sText = ''
    $cText = ''
    $success = $false
    $errMsg = ''
    $requestStartedTick = [System.Diagnostics.Stopwatch]::GetTimestamp()

    try {
        $sText = Invoke-RestMethod -Uri $urlSlices -Method Get
        if ($FullView) {
            $cText = Invoke-RestMethod -Uri $urlCoarse -Method Get
        }
        $success = $true
    } catch {
        $errMsg = $_.Exception.Message
    }
    $requestElapsedMs = (([System.Diagnostics.Stopwatch]::GetTimestamp() - $requestStartedTick) * 1000.0 / [System.Diagnostics.Stopwatch]::Frequency)
    $renderStartedTick = [System.Diagnostics.Stopwatch]::GetTimestamp()

    $outLines = New-Object System.Collections.Generic.List[string]

    if ($success) {
        $sSec = $sText -split "`n`n"
        $cSec = if ($cText) { $cText -split "`n`n" } else { @() }

        $titleSec   = if ($sSec.Count -ge 1) { $sSec[0].Trim() } else { '' }
        $sideBySide = if ($sSec.Count -ge 2) { $sSec[1].TrimEnd() -split "`n" } else { @() }
        $cGridLines = if ($cSec.Count -ge 2) { $cSec[1].TrimEnd() -split "`n" } else { @() }

        # Header Card (2 lines)
        $outLines.Add(('=' * 22 + ' 宝可梦黑2 3D双主层智能对照实时雷达 [HUD] [' + $timeStr + '] ' + '=' * 22))
        $outLines.Add($titleSec)
        $outLines.Add(('刷新: ' + $IntervalMs + 'ms | 零抖动平滑HUD | 模式: 3D双主层左右并排对照 | 按 Ctrl+C 退出'))
        $outLines.Add(('-' * 95))

        # Full 31x31 view if requested
        if ($FullView -and $cGridLines.Count -gt 0) {
            $outLines.Add('>>> [1] 31x31 宏观大局全局图 <<<')
            foreach ($line in $cGridLines) {
                $outLines.Add($line.TrimEnd("`r"))
            }
            $outLines.Add(('-' * 95))
        }

        # Side-by-side 11x11 dual slices
        $outLines.Add('>>> 3D 双主通行层智能对照 (11x11 左右并排 · 滤除中间过渡斜坡) <<<')
        foreach ($line in $sideBySide) {
            $outLines.Add($line.TrimEnd("`r"))
        }

        # Compact Quick Legend (1 line)
        $outLines.Add('')
        $outLines.Add('图例速查: [P]=主角所在 [p]=楼梯跨层投影 [▲]=上行阶梯 [▼]=下行阶梯 [.]=平坦通路/同层踏面 [,]=黄土路 [*]=草丛 [#]=障碍')
    } else {
        $outLines.Add(('=' * 22 + ' 宝可梦黑2 实时周围雷达 [' + $timeStr + '] ' + '=' * 22))
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

    # Clamp line width strictly to BufferWidth - 1 to prevent auto-wrapping
    $bufWidth = $Host.UI.RawUI.BufferSize.Width
    $printWidth = [Math]::Max(10, $bufWidth - 1)
    for ($i = 0; $i -lt $outLines.Count; $i++) {
        $l = $outLines[$i]
        $outLines[$i] = if ($l.Length -gt $printWidth) { $l.Substring(0, $printWidth) } else { $l.PadRight($printWidth) }
    }

    # CRITICAL ZERO-JITTER RULE:
    # 1. Never emit a trailing newline on the bottom-most line!
    # 2. Reset cursor to top-left and overwrite in-place!
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

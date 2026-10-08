# tools/radar_detail.ps1 - Pokemon Black 2 Detailed Material Radar (11x11 @ 100ms)
param (
    [switch]$Full,
    [switch]$Once
)
$script = Join-Path $PSScriptRoot 'watch_radar.ps1'
$argsList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $script, '-Radius', '5', '-Mode', 'detail', '-IntervalMs', '100')
if ($Full) { $argsList += '-Full' }
if ($Once) { $argsList += '-Once' }
& powershell @argsList

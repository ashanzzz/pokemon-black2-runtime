# tools/radar_coarse.ps1 - Pokemon Black 2 Coarse Radar (31x31 @ 100ms)
param (
    [switch]$Full,
    [switch]$Once
)
$script = Join-Path $PSScriptRoot 'watch_radar.ps1'
$argsList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $script, '-Radius', '15', '-Mode', 'coarse', '-IntervalMs', '100')
if ($Full) { $argsList += '-Full' }
if ($Once) { $argsList += '-Once' }
& powershell @argsList

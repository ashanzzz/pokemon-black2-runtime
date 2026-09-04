<#
One-click launcher for a NEW BizHawk session.

BizHawk's documented --lua option loads the given script at process start.
This script intentionally does not terminate or alter an existing EmuHawk
process: doing so could discard unsaved game state.  For an already running
session, use Lua Console -> Reload script.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$RomPath,

    [string]$BizHawkPath = 'D:\BizHawk-Chinese-Win-x64\EmuHawk.exe'
)

$LauncherVersion = '1.0.0'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BridgePath = Join-Path $ProjectRoot 'bridge\bizhawk\black2_bridge.lua'

foreach ($target in @($BizHawkPath, $RomPath, $BridgePath)) {
    if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
        throw "[BizHawk launcher $LauncherVersion] File not found: $target"
    }
}

Write-Host "[BizHawk launcher $LauncherVersion] Starting EmuHawk with Bridge source: $BridgePath"
Start-Process -FilePath $BizHawkPath -ArgumentList @("--lua=$BridgePath", $RomPath) -WorkingDirectory (Split-Path -Parent $BizHawkPath)

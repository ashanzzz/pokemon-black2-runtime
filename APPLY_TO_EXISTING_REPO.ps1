param([Parameter(Mandatory=$true)][string]$RepoPath)
$ErrorActionPreference='Stop'
$Base='7bd1de29a8ead9fdbd5c8e565ae7ad9728f7cddc'
$Root=(Resolve-Path $RepoPath).Path
$Here=$PSScriptRoot
$Head=(git -C $Root rev-parse HEAD).Trim()
if($Head -ne $Base){ throw "Git HEAD mismatch. Expected $Base, got $Head. Nothing was copied." }
$files=@(
 'BLACK2_LAUNCHER.cmd',
 'START_BLACK2.cmd',
 'STOP_BLACK2.cmd',
 'CLOSE_EMUHAWK.cmd',
 'tools/black2_launcher.py',
 'backend/black2/api/runtime_routes.py',
 'backend/black2/runtime/versions.py',
 'backend/black2/decoders/dialogue_object_resolver.py',
 'backend/black2/decoders/dialogue_runtime_decoder.py',
 'backend/black2/state/engine.py',
 'backend/black2/world/runtime_actor_overlay.py',
 'frontend/world3d-runtime-fixed.js',
 'frontend/workbench.html',
 'tests/test_dialogue_runtime_locator_v10.py',
 'tests/test_runtime_actor_overlay_v10.py',
 'tests/test_launcher_lifecycle_v10.py',
 'reverse_engineering/reports/USER_EVIDENCE_20260905_DIALOGUE_WORLD_REGRESSION.json',
 'reverse_engineering/reports/TEST_REPORT_FIX_20260905.md',
 'reverse_engineering/reports/TEST_REPORT_RUNTIME_LIFECYCLE_V10_1_20260905.md'
)
foreach($f in $files){
 $src=Join-Path $Here $f
 if(-not (Test-Path $src)){ throw "Repair package is missing: $f" }
 $dst=Join-Path $Root $f
 New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
 Copy-Item -Force $src $dst
}
Write-Host 'Applied dialogue, World Lab, and Runtime lifecycle fixes.' -ForegroundColor Green
Write-Host 'Review with:'
Write-Host "  git -C `"$Root`" status --short"
Write-Host "  git -C `"$Root`" diff"

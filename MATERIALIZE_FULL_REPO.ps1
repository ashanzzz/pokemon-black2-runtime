param([Parameter(Mandatory=$true)][string]$Destination)
$ErrorActionPreference='Stop'
$Repo='https://github.com/ashanzzz/pokemon-black2-runtime.git'
$Base='7bd1de29a8ead9fdbd5c8e565ae7ad9728f7cddc'
if(Test-Path $Destination){ throw "Destination already exists: $Destination" }
git clone $Repo $Destination
git -C $Destination checkout $Base
& (Join-Path $PSScriptRoot 'APPLY_TO_EXISTING_REPO.ps1') -RepoPath $Destination
Write-Host "Full Git working copy created at $Destination" -ForegroundColor Green

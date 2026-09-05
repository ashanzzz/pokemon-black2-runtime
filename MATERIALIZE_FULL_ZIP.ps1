param(
  [string]$OutputZip = (Join-Path (Get-Location) 'pokemon-black2-runtime-v10.1-fixed-full.zip')
)
$ErrorActionPreference='Stop'
$stage = Join-Path ([System.IO.Path]::GetTempPath()) ('pokemon-black2-runtime-v10.1-' + [Guid]::NewGuid().ToString('N'))
try {
  & (Join-Path $PSScriptRoot 'MATERIALIZE_FULL_REPO.ps1') -Destination $stage
  # The source ZIP intentionally excludes Git metadata and generated/local runtime artifacts.
  $git = Join-Path $stage '.git'
  if(Test-Path $git){ Remove-Item -Recurse -Force $git }
  Get-ChildItem -Path $stage -Recurse -Directory -Force | Where-Object {
    $_.Name -in @('__pycache__','.pytest_cache','.venv','venv','node_modules')
  } | Sort-Object FullName -Descending | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
  Get-ChildItem -Path $stage -Recurse -File -Force | Where-Object {
    $_.Extension -in @('.pyc','.pyo') -or $_.Name -match '\.(log|bak)$'
  } | Remove-Item -Force -ErrorAction SilentlyContinue
  $runtime = Join-Path $stage 'runtime'
  if(Test-Path $runtime){ Remove-Item -Recurse -Force $runtime }
  if(Test-Path $OutputZip){ Remove-Item -Force $OutputZip }
  Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $OutputZip -CompressionLevel Optimal
  $hash=(Get-FileHash -Algorithm SHA256 $OutputZip).Hash.ToLowerInvariant()
  Write-Host "Full source ZIP: $OutputZip" -ForegroundColor Green
  Write-Host "SHA256: $hash" -ForegroundColor Green
}
finally {
  if(Test-Path $stage){ Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue }
}

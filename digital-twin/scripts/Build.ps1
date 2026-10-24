param(
    [Parameter(Mandatory=$true)][string]$MaxBatch,
    [string]$UnityEditor = 'E:\FumaiTools\Unity\Editors\6000.3.27f1\Editor\Unity.exe'
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not (Test-Path -LiteralPath $MaxBatch -PathType Leaf)) { throw '3dsmaxbatch.exe is missing; install and activate 3ds Max first.' }
if (-not (Test-Path -LiteralPath $UnityEditor -PathType Leaf)) { throw 'Unity Editor is missing; install the pinned editor plus WebGL module.' }
$artifacts = Join-Path $root 'artifacts'
New-Item -ItemType Directory -Path $artifacts -Force | Out-Null
$started = [DateTime]::UtcNow
& $MaxBatch (Join-Path $root '3dsmax/BuildEquipment.ms') -listenerLog (Join-Path $artifacts '3dsmax-listener.log') -v 5
if ($LASTEXITCODE -ne 0) { throw "3ds Max batch failed: $LASTEXITCODE" }
$pointer = Join-Path $artifacts 'max/latest.json'
if (-not (Test-Path -LiteralPath $pointer) -or (Get-Item -LiteralPath $pointer).LastWriteTimeUtc -lt $started) { throw 'No new native Max export receipt. Check the Max log and license.' }
& $UnityEditor -batchmode -quit -projectPath (Join-Path $root 'unity') -buildTarget WebGL -executeMethod FumaiBuild.WebGL -logFile (Join-Path $artifacts 'unity-build.log')
if ($LASTEXITCODE -ne 0) { throw "Unity WebGL build failed: $LASTEXITCODE. Check unity-build.log and editor license." }
if (-not (Select-String -LiteralPath (Join-Path $artifacts 'unity-build.log') -Pattern 'FUMAI_UNITY_WEBGL_OK' -Quiet)) { throw 'Unity did not report a successful native build.' }
& node (Join-Path $root 'scripts/VerifyBuild.mjs')
if ($LASTEXITCODE -ne 0) { throw 'WebGL output verification failed; do not deploy.' }

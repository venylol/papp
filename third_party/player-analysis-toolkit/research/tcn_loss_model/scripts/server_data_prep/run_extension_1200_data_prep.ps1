param(
    [Parameter(Mandatory = $true)][string]$ServerRoot,
    [string]$PythonExe = '',
    [string]$AttemptName = 'oq_bilateral_extension_1200_20260804_v2',
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$PackageRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$ServerRoot = (Resolve-Path -LiteralPath $ServerRoot).Path
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $python = Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $python) { throw 'python.exe not found; supply -PythonExe C:\path\to\python.exe' }
    $PythonExe = $python.Source
}
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path

$ExpectedGames = 1200
$ExpectedRows = 72940
$ExpectedPlacements = 71954
$ExpectedPasses = 986
$Workers = 12
$HashLevel = 25
$SourceDir = Join-Path $PackageRoot 'assets\source'
$PullDir = Join-Path $PackageRoot 'assets\pull'
$SourceCsv = Join-Path $SourceDir 'raw_nodes_with_pass.csv'
$Verifier = Join-Path $PackageRoot 'scripts\verify_package.py'
$SourceAuditor = Join-Path $PackageRoot 'scripts\audit_oq_extension_source.py'
$ReturnPackager = Join-Path $PackageRoot 'scripts\package_extension_return.py'
$Engine = Join-Path $ServerRoot 'assets\engine\Egaroucid_for_Console_7_8_1_AVX512_AMD.exe'
$Runner = Join-Path $ServerRoot 'app\scripts\portable_safe_recompute.py'
$Assembler = Join-Path $ServerRoot 'app\scripts\assemble_safe_hint_recompute.py'
$Materializer = Join-Path $ServerRoot 'app\scripts\materialize_oq_tcn_model_ready.py'
$FinalValidator = Join-Path $ServerRoot 'app\scripts\validate_server_model_ready.py'
$BaselineGames = Join-Path $ServerRoot 'data\handoff\games.csv'
$Snapshot = Join-Path $ServerRoot 'data\source_snapshot'
$Attempt = Join-Path $ServerRoot (Join-Path 'extension_attempts' $AttemptName)
$Hint1Dir = Join-Path $Attempt 'work\hint1'
$Hint6Dir = Join-Path $Attempt 'work\hint6'
$AssembledDir = Join-Path $Attempt 'results\assembled'
$AssembledCsv = Join-Path $AssembledDir 'raw_nodes_with_pass_safe_hints.csv'
$AssemblyManifest = Join-Path $AssembledDir 'assembly_manifest.json'
$ModelReadyDir = Join-Path $Attempt 'results\model_ready'
$ModelReady = Join-Path $ModelReadyDir 'model_ready_1200.npz'
$FinalValidation = Join-Path $ModelReadyDir 'server_final_validation.json'
$StatusPath = Join-Path $ServerRoot 'EXTENSION_1200_DELIVERY_STATUS.json'
$ReturnZip = Join-Path $ServerRoot 'returns\oq_bilateral_extension_1200_model_ready_20260804_v2.zip'

function Write-Status([string]$Status, [string]$Stage, [string]$Detail) {
    $value = [ordered]@{
        schema = 'oq-extension-1200-data-prep-status-v1'; status = $Status; stage = $Stage
        detail = $Detail; attempt = $Attempt; returnZip = $ReturnZip
        trainingStarted = $false; cudaUsed = $false; workers = $Workers
        hint1ThreadsPerConsole = 1; hint6ThreadsPerConsole = 16; hashLevel = $HashLevel
        updatedAt = (Get-Date).ToString('o')
    } | ConvertTo-Json -Depth 10
    [System.IO.File]::WriteAllText($StatusPath, $value + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
}

function Invoke-Python {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $PythonExe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python failed ($LASTEXITCODE): $($Arguments -join ' ')" }
}

try {
    Write-Status 'running' 'preflight' 'verifying the independent 1200-game package and the completed 10000-game handoff boundary'
    Invoke-Python $Verifier --bundle-root $PackageRoot --manifest (Join-Path $PackageRoot 'package_manifest.json')
    Invoke-Python '-c' 'import numpy,pandas,torch,sklearn,scipy'
    $incrementalStatusPath = Join-Path $ServerRoot 'INCREMENTAL_DELIVERY_STATUS.json'
    if (Test-Path -LiteralPath $incrementalStatusPath) {
        $oldStatus = Get-Content -LiteralPath $incrementalStatusPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($oldStatus.status -ne 'complete') { throw "The 10000-game job is not complete: status=$($oldStatus.status) stage=$($oldStatus.stage)" }
    }
    $engines = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'Egaroucid_for_Console_7_8_1_AVX512_AMD.exe' })
    if ($engines.Count -ne 0) { throw "The earlier job is still using Egaroucid: $($engines.ProcessId -join ',')" }
    foreach ($required in @($Engine,$Runner,$Assembler,$Materializer,$FinalValidator,$BaselineGames)) {
        if (-not (Test-Path -LiteralPath $required)) { throw "Required original-package asset missing: $required" }
    }
    New-Item -ItemType Directory -Path $Attempt -Force | Out-Null
    Invoke-Python $SourceAuditor --source-dir $SourceDir --pull-dir $PullDir --baseline-games $BaselineGames --expected-games $ExpectedGames --expected-rows $ExpectedRows --expected-placements $ExpectedPlacements --expected-passes $ExpectedPasses --output (Join-Path $Attempt 'source_audit.json')
    if ($PreflightOnly) {
        Write-Status 'preflight-complete' 'extension-source-ready' '1200-game source passed exact-index/pass-aware audit; no Console launched'
        return
    }

    Write-Status 'running' 'hint1' 'computing all 71954 placement nodes at level2, no book, 1 thread per Console, hash25'
    Invoke-Python $Runner run --source-csv $SourceCsv --stage hint1 --engine $Engine --output-dir $Hint1Dir --workers $Workers --hash-level $HashLevel --batch-size 256 --timeout 900 --max-attempts 2 --resume
    Invoke-Python $Runner audit --output-dir $Hint1Dir --expected $ExpectedPlacements

    Write-Status 'running' 'hint6' 'computing all 71954 placement nodes at level18/book, 12 Consoles x 16 threads, hash25'
    Invoke-Python $Runner run --source-csv $SourceCsv --stage hint6 --engine $Engine --output-dir $Hint6Dir --workers $Workers --hash-level $HashLevel --batch-size 128 --timeout 900 --max-attempts 2 --resume
    Invoke-Python $Runner audit --output-dir $Hint6Dir --expected $ExpectedPlacements
    $hint1Audit = Get-Content -LiteralPath (Join-Path $Hint1Dir 'audit.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $hint6Audit = Get-Content -LiteralPath (Join-Path $Hint6Dir 'audit.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $hint1Audit.ok -or -not $hint6Audit.ok -or [int64]$hint1Audit.rows -ne $ExpectedPlacements -or [int64]$hint6Audit.rows -ne $ExpectedPlacements) {
        throw 'full extension hint1/hint6 audit gate failed'
    }

    if (-not (Test-Path -LiteralPath $AssemblyManifest)) {
        if (Test-Path -LiteralPath $AssembledDir) { throw "Incomplete assembly directory already exists: $AssembledDir" }
        New-Item -ItemType Directory -Path $AssembledDir | Out-Null
        Write-Status 'running' 'assembly' 'joining audited hints only by exact game_id and original pass-inclusive move_index'
        Invoke-Python $Assembler --source-csv $SourceCsv --hint1-dir $Hint1Dir --hint6-dir $Hint6Dir --output-csv $AssembledCsv --output-manifest $AssemblyManifest --merge-index (Join-Path $AssembledDir 'merge_index.sqlite3') --expected-rows $ExpectedRows --expected-placements $ExpectedPlacements --expected-passes $ExpectedPasses --expected-games $ExpectedGames
    }
    $assembly = Get-Content -LiteralPath $AssemblyManifest -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($assembly.status -ne 'complete' -or [int64]$assembly.rows -ne $ExpectedRows -or [int64]$assembly.placements -ne $ExpectedPlacements -or [int64]$assembly.passes -ne $ExpectedPasses -or [int64]$assembly.games -ne $ExpectedGames) {
        throw 'assembled 1200-game extension shape gate failed'
    }

    if (-not (Test-Path -LiteralPath $ModelReadyDir)) {
        Write-Status 'running' 'materialization' 'building the independent 1200-game 362-feature/23-channel TCN bundle on CPU'
        Invoke-Python $Materializer --handoff-dir $SourceDir --raw-nodes $AssembledCsv --split-manifest (Join-Path $SourceDir 'split_manifest.csv') --output-dir $ModelReadyDir --base-checkpoint (Join-Path $Snapshot 'tcn_board_cnn_time_model_best.pt') --preprocessing (Join-Path $Snapshot 'preprocessing.json') --source-research (Join-Path $Snapshot 'official_research') --human-opening-book (Join-Path $Snapshot 'othelloquest_human_frequency_nodes_ply1_30_min5.runtime.json') --output-name 'model_ready_1200.npz' --workers 12
    }
    Write-Status 'running' 'final-data-contract' 'validating the independent extension against the TCN checkpoint contract; no training'
    Invoke-Python $FinalValidator --data $ModelReady --context (Join-Path $ModelReadyDir 'position_context_metadata.csv') --checkpoint (Join-Path $Snapshot 'tcn_board_cnn_time_model_best.pt') --preprocessing (Join-Path $Snapshot 'preprocessing.json') --materialization-manifest (Join-Path $ModelReadyDir 'materialization_manifest.json') --output $FinalValidation

    Write-Status 'running' 'return-package' 'packing only the completed 1200-game extension and its audited engine evidence'
    Invoke-Python $ReturnPackager --attempt-dir $Attempt --source-dir $SourceDir --output-zip $ReturnZip --expected-games $ExpectedGames --expected-rows $ExpectedRows --expected-placements $ExpectedPlacements --expected-passes $ExpectedPasses
    Write-Status 'complete' 'extension-return-ready' 'The separate 1200-game base-feature return ZIP is complete; Player profile features are intentionally deferred until the local 11200 merge; no training occurred'
}
catch {
    Write-Status 'failed' 'blocked' $_.Exception.ToString()
    throw
}

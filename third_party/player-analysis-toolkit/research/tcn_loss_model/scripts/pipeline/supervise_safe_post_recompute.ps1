param(
    [Parameter(Mandatory = $true)]
    [int]$RecomputeSupervisorPid,
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
Set-Location -LiteralPath $ProjectRoot

$RunRoot = Join-Path $ProjectRoot 'outputs\oq_safe_full_recompute_10000_20260804'
$RecomputeProgress = Join-Path $RunRoot 'supervisor_progress.json'
$PostProgress = Join-Path $RunRoot 'post_supervisor_progress.json'
$Assembler = Join-Path $ProjectRoot 'scripts\pipeline\assemble_safe_hint_recompute.py'
$Materializer = Join-Path $ProjectRoot 'scripts\data\materialize_oq_tcn_model_ready.py'
$AssemblerHash = (Get-FileHash -LiteralPath $Assembler -Algorithm SHA256).Hash.ToLowerInvariant()
$MaterializerHash = (Get-FileHash -LiteralPath $Materializer -Algorithm SHA256).Hash.ToLowerInvariant()
$Frozen = Join-Path $ProjectRoot 'data\oq_elo2000_5min_bilateral_10000_model_ready_20260803_final'
$Handoff = Join-Path $Frozen 'handoff'
$Snapshot = Join-Path $Frozen 'source_snapshot'
$AssembledDir = Join-Path $RunRoot 'assembled'
$AssembledCsv = Join-Path $AssembledDir 'raw_nodes_with_pass_safe_hints.csv'
$AssemblyManifest = Join-Path $AssembledDir 'assembly_manifest.json'
$MergeIndex = Join-Path $AssembledDir 'merge_index.sqlite3'
$ModelReadyDir = Join-Path $ProjectRoot 'outputs\oq_safe_model_ready_10000_20260804'

function Write-PostProgress {
    param([string]$Status, [string]$Stage, [string]$Detail)
    [ordered]@{
        schema = 'egaroucid-safe-post-supervisor-v1'
        status = $Status
        stage = $Stage
        detail = $Detail
        recomputeSupervisorPid = $RecomputeSupervisorPid
        assemblerSha256 = $AssemblerHash
        materializerSha256 = $MaterializerHash
        updatedAt = (Get-Date).ToString('o')
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $PostProgress -Encoding UTF8
}

try {
    Write-PostProgress -Status 'waiting' -Stage 'recompute' -Detail 'waiting for full hint1/hint6 audits'
    while (Get-Process -Id $RecomputeSupervisorPid -ErrorAction SilentlyContinue) {
        $detail = if (Test-Path -LiteralPath $RecomputeProgress) {
            $state = Get-Content -LiteralPath $RecomputeProgress -Raw -Encoding UTF8 | ConvertFrom-Json
            "recompute status=$($state.status) stage=$($state.stage) detail=$($state.detail)"
        } else {
            'recompute supervisor progress not written yet'
        }
        Write-PostProgress -Status 'waiting' -Stage 'recompute' -Detail $detail
        Start-Sleep -Seconds 60
    }
    if (-not (Test-Path -LiteralPath $RecomputeProgress)) {
        throw 'recompute supervisor exited without a progress file'
    }
    $recompute = Get-Content -LiteralPath $RecomputeProgress -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($recompute.status -ne 'complete') {
        throw "recompute supervisor did not complete: $($recompute.detail)"
    }
    foreach ($stage in @('hint1', 'hint6')) {
        $auditPath = Join-Path $RunRoot "$stage\audit.json"
        if (-not (Test-Path -LiteralPath $auditPath)) {
            throw "missing $stage full audit"
        }
        $audit = Get-Content -LiteralPath $auditPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $audit.ok -or [int64]$audit.rows -ne 599112) {
            throw "$stage full audit is not a passing 599112-row audit"
        }
    }
    if ((Get-FileHash -LiteralPath $Assembler -Algorithm SHA256).Hash.ToLowerInvariant() -ne $AssemblerHash) {
        throw 'assembler changed while waiting; refusing to run an unaudited version'
    }
    if ((Get-FileHash -LiteralPath $Materializer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $MaterializerHash) {
        throw 'materializer changed while waiting; refusing to run an unaudited version'
    }
    if (Test-Path -LiteralPath $AssembledDir) {
        throw 'assembled output directory already exists; refusing to overwrite or duplicate'
    }
    New-Item -ItemType Directory -Path $AssembledDir | Out-Null
    Write-PostProgress -Status 'running' -Stage 'assembly' -Detail 'joining by exact game_id/move_index'
    & 'C:\Python314\python.exe' $Assembler `
        --source-csv (Join-Path $Handoff 'raw_nodes_with_pass.csv') `
        --hint1-dir (Join-Path $RunRoot 'hint1') `
        --hint6-dir (Join-Path $RunRoot 'hint6') `
        --output-csv $AssembledCsv `
        --output-manifest $AssemblyManifest `
        --merge-index $MergeIndex
    if ($LASTEXITCODE -ne 0) {
        throw "assembly failed with exit code $LASTEXITCODE"
    }
    $assembly = Get-Content -LiteralPath $AssemblyManifest -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($assembly.status -ne 'complete' -or [int64]$assembly.placements -ne 599112) {
        throw 'assembly manifest did not pass the frozen shape contract'
    }
    if (Test-Path -LiteralPath $ModelReadyDir) {
        throw 'model-ready output directory already exists; refusing to overwrite or duplicate'
    }
    Write-PostProgress -Status 'running' -Stage 'materialization' -Detail 'building 362-feature/23-channel model-ready data from frozen snapshot'
    & 'C:\Python314\python.exe' $Materializer `
        --handoff-dir $Handoff `
        --raw-nodes $AssembledCsv `
        --split-manifest (Join-Path $Handoff 'split_manifest.csv') `
        --output-dir $ModelReadyDir `
        --base-checkpoint (Join-Path $Snapshot 'tcn_board_cnn_time_model_best.pt') `
        --preprocessing (Join-Path $Snapshot 'preprocessing.json') `
        --source-research (Join-Path $Snapshot 'official_research') `
        --human-opening-book (Join-Path $Snapshot 'othelloquest_human_frequency_nodes_ply1_30_min5.runtime.json') `
        --output-name 'model_ready_10000.npz' `
        --workers 12
    if ($LASTEXITCODE -ne 0) {
        throw "materialization failed with exit code $LASTEXITCODE"
    }
    $materialization = Get-Content -LiteralPath (Join-Path $ModelReadyDir 'materialization_manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $materialization.ok -or -not $materialization.validation.ok) {
        throw 'materialization manifest or formal model-ready validation is not passing'
    }
    Write-PostProgress -Status 'complete' -Stage 'data-contract' -Detail 'safe full assembly and formal model-ready validation completed; training remains gated'
}
catch {
    Write-PostProgress -Status 'failed' -Stage 'post-supervisor' -Detail $_.Exception.ToString()
    throw
}

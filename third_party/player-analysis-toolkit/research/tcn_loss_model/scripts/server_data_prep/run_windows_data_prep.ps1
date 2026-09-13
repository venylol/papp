param(
    [string]$PythonExe = '',
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$BundleRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $BundleRoot

$ExpectedPlacements = 599112
$Workers = 12
$ThreadsPerConsole = 16
$HashLevel = 25
$StatusPath = Join-Path $BundleRoot 'DELIVERY_STATUS.json'
$App = Join-Path $BundleRoot 'app'
$PortableRunner = Join-Path $App 'scripts\portable_safe_recompute.py'
$Relocator = Join-Path $App 'scripts\relocate_hint6_resume.py'
$Assembler = Join-Path $App 'scripts\assemble_safe_hint_recompute.py'
$Materializer = Join-Path $App 'scripts\materialize_oq_tcn_model_ready.py'
$FinalValidator = Join-Path $App 'scripts\validate_server_model_ready.py'
$Verifier = Join-Path $App 'scripts\verify_package.py'
$Engine = Join-Path $BundleRoot 'assets\engine\Egaroucid_for_Console_7_8_1_AVX512_AMD.exe'
$Handoff = Join-Path $BundleRoot 'data\handoff'
$SourceCsv = Join-Path $Handoff 'raw_nodes_with_pass.csv'
$Snapshot = Join-Path $BundleRoot 'data\source_snapshot'
$Hint1Dir = Join-Path $BundleRoot 'work\hint1'
$Hint6Evidence = Join-Path $BundleRoot 'evidence\hint6_partial_original'
$Hint6Dir = Join-Path $BundleRoot 'work\hint6'
$AssembledDir = Join-Path $BundleRoot 'results\assembled'
$AssembledCsv = Join-Path $AssembledDir 'raw_nodes_with_pass_safe_hints.csv'
$AssemblyManifest = Join-Path $AssembledDir 'assembly_manifest.json'
$ModelReadyDir = Join-Path $BundleRoot 'results\model_ready'
$ModelReady = Join-Path $ModelReadyDir 'model_ready_10000.npz'
$Context = Join-Path $ModelReadyDir 'position_context_metadata.csv'
$FinalValidation = Join-Path $ModelReadyDir 'server_final_validation.json'

function Write-JsonNoBom {
    param([string]$Path, [object]$Value)
    $json = $Value | ConvertTo-Json -Depth 12
    [System.IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
}

function Write-Status {
    param([string]$Status, [string]$Stage, [string]$Detail)
    Write-JsonNoBom -Path $StatusPath -Value ([ordered]@{
        schema = 'oq-tcn-windows-data-prep-status-v1'
        status = $Status
        stage = $Stage
        detail = $Detail
        workers = $Workers
        threadsPerConsole = $ThreadsPerConsole
        hashLevel = $HashLevel
        trainingStarted = $false
        cudaUsed = $false
        updatedAt = (Get-Date).ToString('o')
    })
}

function Invoke-Python {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $PythonExe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code ${LASTEXITCODE}: $($Arguments -join ' ')"
    }
}

try {
    if ([string]::IsNullOrWhiteSpace($PythonExe)) {
        $command = Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $command) {
            throw 'python.exe was not found; rerun with -PythonExe C:\path\to\python.exe'
        }
        $PythonExe = $command.Source
    }
    $PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
    Write-Status -Status 'running' -Stage 'preflight' -Detail 'verifying package, Python dependencies, hashes, and exclusive process scope'
    & $PythonExe --version
    if ($LASTEXITCODE -ne 0) { throw 'Python could not be started' }
    Invoke-Python '-c' 'import numpy,pandas,torch,sklearn,scipy; print(numpy.__version__, pandas.__version__, torch.__version__)'
    Invoke-Python $Verifier --bundle-root $BundleRoot --manifest (Join-Path $BundleRoot 'package_manifest.json')

    $otherEngines = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq 'Egaroucid_for_Console_7_8_1_AVX512_AMD.exe'
    })
    if ($otherEngines.Count -ne 0) {
        throw "refusing to start while another target Egaroucid process exists: $($otherEngines.ProcessId -join ',')"
    }

    Write-Status -Status 'running' -Stage 'hint1-audit' -Detail 'revalidating the supplied complete 599112-row hint1 stage'
    Invoke-Python $PortableRunner audit --output-dir $Hint1Dir --expected $ExpectedPlacements

    if (-not (Test-Path -LiteralPath $Hint6Dir)) {
        Write-Status -Status 'running' -Stage 'hint6-relocation' -Detail 'creating a path-relocated work copy from the audited 14080-row partial evidence'
        Invoke-Python $Relocator --evidence-dir $Hint6Evidence --output-dir $Hint6Dir --engine $Engine --source-csv $SourceCsv
        $relocation = Get-Content -LiteralPath (Join-Path $Hint6Dir 'relocation_manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        Invoke-Python $PortableRunner audit --output-dir $Hint6Dir --expected ([int64]$relocation.rows)
    }

    if ($PreflightOnly) {
        Write-Status -Status 'preflight-complete' -Stage 'hint6-ready-to-resume' -Detail 'package, dependencies, hint1, relocation, and partial hint6 audit passed; no engine search or training started'
        return
    }

    Write-Status -Status 'running' -Stage 'hint6-resume' -Detail 'resuming fixed 12 Consoles x 16 threads, level18/book/hash25'
    Invoke-Python $PortableRunner run --source-csv $SourceCsv --stage hint6 --engine $Engine --output-dir $Hint6Dir --workers $Workers --hash-level $HashLevel --batch-size 128 --timeout 900 --max-attempts 2 --resume

    Write-Status -Status 'running' -Stage 'hint6-full-audit' -Detail 'auditing all 599112 hint6 placements for board provenance, legal moves, completeness, and unique keys'
    Invoke-Python $PortableRunner audit --output-dir $Hint6Dir --expected $ExpectedPlacements

    $hint1Audit = Get-Content -LiteralPath (Join-Path $Hint1Dir 'audit.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $hint6Audit = Get-Content -LiteralPath (Join-Path $Hint6Dir 'audit.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $hint1Audit.ok -or -not $hint6Audit.ok -or [int64]$hint1Audit.rows -ne $ExpectedPlacements -or [int64]$hint6Audit.rows -ne $ExpectedPlacements) {
        throw 'full hint1/hint6 audit gate did not pass'
    }

    if (-not (Test-Path -LiteralPath $AssembledDir)) {
        New-Item -ItemType Directory -Path $AssembledDir | Out-Null
        Write-Status -Status 'running' -Stage 'assembly' -Detail 'joining frozen rows and audited hints by exact game_id/move_index'
        Invoke-Python $Assembler --source-csv $SourceCsv --hint1-dir $Hint1Dir --hint6-dir $Hint6Dir --output-csv $AssembledCsv --output-manifest $AssemblyManifest --merge-index (Join-Path $AssembledDir 'merge_index.sqlite3')
    }
    $assembly = Get-Content -LiteralPath $AssemblyManifest -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($assembly.status -ne 'complete' -or [int64]$assembly.rows -ne 609124 -or [int64]$assembly.placements -ne $ExpectedPlacements -or [int64]$assembly.passes -ne 10012 -or [int64]$assembly.games -ne 10000) {
        throw 'assembled data did not pass the frozen 10000-game shape contract'
    }

    if (-not (Test-Path -LiteralPath $ModelReadyDir)) {
        Write-Status -Status 'running' -Stage 'materialization' -Detail 'building 362 numeric features and 23 board channels; no training'
        Invoke-Python $Materializer --handoff-dir $Handoff --raw-nodes $AssembledCsv --split-manifest (Join-Path $Handoff 'split_manifest.csv') --output-dir $ModelReadyDir --base-checkpoint (Join-Path $Snapshot 'tcn_board_cnn_time_model_best.pt') --preprocessing (Join-Path $Snapshot 'preprocessing.json') --source-research (Join-Path $Snapshot 'official_research') --human-opening-book (Join-Path $Snapshot 'othelloquest_human_frequency_nodes_ply1_30_min5.runtime.json') --output-name 'model_ready_10000.npz' --workers 12
    }

    Write-Status -Status 'running' -Stage 'final-data-contract' -Detail 'strict CPU validation against checkpoint feature order and preprocessing identity'
    Invoke-Python $FinalValidator --data $ModelReady --context $Context --checkpoint (Join-Path $Snapshot 'tcn_board_cnn_time_model_best.pt') --preprocessing (Join-Path $Snapshot 'preprocessing.json') --materialization-manifest (Join-Path $ModelReadyDir 'materialization_manifest.json') --output $FinalValidation
    $validation = Get-Content -LiteralPath $FinalValidation -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $validation.ok -or [int]$validation.inputFeatures -ne 362 -or [int]$validation.boardChannels -ne 23) {
        throw 'final TCN data-ready validation failed'
    }
    Write-Status -Status 'complete' -Stage 'data-ready' -Detail 'full audited model-ready data is ready for TCN use; no model training was started'
}
catch {
    Write-Status -Status 'failed' -Stage 'blocked' -Detail $_.Exception.ToString()
    throw
}

param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,
    [switch]$Resume
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
Set-Location -LiteralPath $ProjectRoot

# USER-LOCKED CONFIGURATION. Do not change these constants unless the user
# explicitly requests a different configuration.
$Workers = 12
$ThreadsPerConsole = 16
$HashLevel = 25
$EngineLevel = 18
$HintCount = 6
$ExpectedPlacements = 599112

$Policy = Join-Path $ProjectRoot 'HINT6_PARALLELISM_USER_LOCK_20260804.md'
$Runner = Join-Path $ProjectRoot 'scripts\pipeline\safe_recompute_egaroucid_hints.py'
$Engine = Join-Path $ProjectRoot '..\server_handoffs\oq_egaroucid_windows_9950x_20260803_final\engine\Egaroucid_for_Console_7_8_1_AVX512_AMD.exe'
$SourceCsv = Join-Path $ProjectRoot 'data\oq_elo2000_5min_bilateral_10000_model_ready_20260803_final\handoff\raw_nodes_with_pass.csv'
$SampleManifest = Join-Path $ProjectRoot 'outputs\oq_safe_smoke_100_20260804\sample_manifest.json'
$RunRoot = Join-Path $ProjectRoot 'outputs\oq_safe_full_recompute_10000_hint6_w12_20260804'
$SmokeDir = Join-Path $RunRoot 'smoke_100_hint6_w12'
$Hint6Dir = Join-Path $RunRoot 'hint6'
$SupervisorProgress = Join-Path $RunRoot 'supervisor_progress.json'

function Write-JsonNoBom {
    param([string]$Path, [object]$Value)
    $text = $Value | ConvertTo-Json -Depth 10
    [System.IO.File]::WriteAllText($Path, $text + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
}

function Write-SupervisorProgress {
    param([string]$Status, [string]$Stage, [string]$Detail)
    Write-JsonNoBom -Path $SupervisorProgress -Value ([ordered]@{
        schema = 'egaroucid-safe-authorized-hint6-w12-supervisor-v1'
        status = $Status
        stage = $Stage
        detail = $Detail
        workers = $Workers
        threadsPerConsole = $ThreadsPerConsole
        hashLevel = $HashLevel
        engineLevel = $EngineLevel
        hintCount = $HintCount
        policy = $Policy
        policySha256 = (Get-FileHash -LiteralPath $Policy -Algorithm SHA256).Hash.ToLowerInvariant()
        updatedAt = (Get-Date).ToString('o')
    })
}

function Assert-ManifestContract {
    param([string]$Directory, [int]$ExpectedRows)
    $manifestPath = Join-Path $Directory 'run_manifest.json'
    $auditPath = Join-Path $Directory 'audit.json'
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        throw "missing run manifest: $manifestPath"
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([int]$manifest.workerCount -ne $Workers -or
        [int]$manifest.threads -ne $ThreadsPerConsole -or
        [int]$manifest.hashLevel -ne $HashLevel -or
        [int]$manifest.level -ne $EngineLevel -or
        [int]$manifest.count -ne $HintCount -or
        -not [bool]$manifest.use_book -or
        [bool]$manifest.quietFlag -or
        [bool]$manifest.noBoardFlag) {
        throw "run manifest violates the user-locked 12x16/hash25 contract: $manifestPath"
    }
    if (-not (Test-Path -LiteralPath $auditPath)) {
        throw "missing audit: $auditPath"
    }
    $audit = Get-Content -LiteralPath $auditPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not [bool]$audit.ok -or [int64]$audit.rows -ne $ExpectedRows -or
        [int64]$audit.boardMismatches -ne 0 -or
        [int64]$audit.legalityOrCompletenessErrors -ne 0) {
        throw "audit violates the board/legality contract: $auditPath"
    }
}

try {
    foreach ($required in @($Policy, $Runner, $Engine, $SourceCsv, $SampleManifest)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "required file is missing: $required"
        }
    }
    if (-not (Test-Path -LiteralPath $RunRoot)) {
        New-Item -ItemType Directory -Path $RunRoot | Out-Null
    }

    $existingEngines = @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.CommandLine -match 'Egaroucid_for_Console_7_8_1_AVX512_AMD.exe'
    })
    if ($existingEngines.Count -ne 0) {
        throw "refusing to launch while another target Egaroucid process exists: $($existingEngines.ProcessId -join ',')"
    }

    if (-not (Test-Path -LiteralPath $SmokeDir)) {
        Write-SupervisorProgress -Status 'running' -Stage 'smoke' -Detail 'running exact 12-worker native-board and legality smoke test'
        & 'C:\Python314\python.exe' $Runner run `
            --sample-manifest $SampleManifest `
            --stage hint6 `
            --engine $Engine `
            --output-dir $SmokeDir `
            --workers $Workers `
            --hash-level $HashLevel `
            --batch-size 100 `
            --timeout 900 `
            --max-attempts 2
        if ($LASTEXITCODE -ne 0) {
            throw "12-worker smoke run failed with exit code $LASTEXITCODE"
        }
        & 'C:\Python314\python.exe' $Runner audit --output-dir $SmokeDir --expected 100
        if ($LASTEXITCODE -ne 0) {
            throw "12-worker smoke audit failed with exit code $LASTEXITCODE"
        }
    }
    Assert-ManifestContract -Directory $SmokeDir -ExpectedRows 100

    if ((Test-Path -LiteralPath $Hint6Dir) -and -not $Resume) {
        throw 'formal twelve-worker output already exists; use this same script with -Resume after explicit recovery review'
    }
    Write-SupervisorProgress -Status 'running' -Stage 'hint6' -Detail 'running user-locked 12 Consoles x 16 threads, hash25'
    $runnerArgs = @(
        $Runner, 'run',
        '--source-csv', $SourceCsv,
        '--stage', 'hint6',
        '--engine', $Engine,
        '--output-dir', $Hint6Dir,
        '--workers', [string]$Workers,
        '--hash-level', [string]$HashLevel,
        '--batch-size', '128',
        '--timeout', '900',
        '--max-attempts', '2'
    )
    if ($Resume) {
        $runnerArgs += '--resume'
    }
    & 'C:\Python314\python.exe' @runnerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "formal twelve-worker hint6 run failed with exit code $LASTEXITCODE"
    }

    Write-SupervisorProgress -Status 'auditing' -Stage 'hint6' -Detail 'running full 599112-row audit'
    & 'C:\Python314\python.exe' $Runner audit --output-dir $Hint6Dir --expected $ExpectedPlacements
    if ($LASTEXITCODE -ne 0) {
        throw "formal twelve-worker hint6 audit failed with exit code $LASTEXITCODE"
    }
    Assert-ManifestContract -Directory $Hint6Dir -ExpectedRows $ExpectedPlacements
    Write-SupervisorProgress -Status 'complete' -Stage 'hint6' -Detail 'user-locked twelve-worker hint6 and full audit completed'
}
catch {
    if (Test-Path -LiteralPath $RunRoot) {
        Write-SupervisorProgress -Status 'failed' -Stage 'supervisor' -Detail $_.Exception.ToString()
    }
    throw
}

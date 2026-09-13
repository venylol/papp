param(
    [Parameter(Mandatory = $true)]
    [int]$Hint1Pid,
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
Set-Location -LiteralPath $ProjectRoot

$RunRoot = Join-Path $ProjectRoot 'outputs\oq_safe_full_recompute_10000_20260804'
$Hint1Dir = Join-Path $RunRoot 'hint1'
$Hint6Dir = Join-Path $RunRoot 'hint6'
$SupervisorProgress = Join-Path $RunRoot 'supervisor_progress.json'
$Runner = Join-Path $ProjectRoot 'scripts\pipeline\safe_recompute_egaroucid_hints.py'
$SourceCsv = Join-Path $ProjectRoot 'data\oq_elo2000_5min_bilateral_10000_model_ready_20260803_final\handoff\raw_nodes_with_pass.csv'
$Engine = Join-Path $ProjectRoot '..\server_handoffs\oq_egaroucid_windows_9950x_20260803_final\engine\Egaroucid_for_Console_7_8_1_AVX512_AMD.exe'
$ExpectedPlacements = 599112

function Write-SupervisorProgress {
    param(
        [string]$Status,
        [string]$Stage,
        [string]$Detail
    )
    $payload = [ordered]@{
        schema = 'egaroucid-safe-full-supervisor-v1'
        status = $Status
        stage = $Stage
        detail = $Detail
        hint1Pid = $Hint1Pid
        updatedAt = (Get-Date).ToString('o')
    }
    $payload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $SupervisorProgress -Encoding UTF8
}

function Read-StageStatus {
    param([string]$StageDirectory)
    $progress = Join-Path $StageDirectory 'progress.json'
    if (-not (Test-Path -LiteralPath $progress)) {
        return $null
    }
    return Get-Content -LiteralPath $progress -Raw -Encoding UTF8 | ConvertFrom-Json
}

try {
    Write-SupervisorProgress -Status 'waiting' -Stage 'hint1' -Detail 'waiting for the authorized hint1 process'
    while (Get-Process -Id $Hint1Pid -ErrorAction SilentlyContinue) {
        $hint1 = Read-StageStatus -StageDirectory $Hint1Dir
        $detail = if ($null -eq $hint1) { 'hint1 progress not written yet' } else { "committed=$($hint1.committedThisAttempt) status=$($hint1.status)" }
        Write-SupervisorProgress -Status 'waiting' -Stage 'hint1' -Detail $detail
        Start-Sleep -Seconds 60
    }

    $hint1 = Read-StageStatus -StageDirectory $Hint1Dir
    if ($null -eq $hint1 -or $hint1.status -ne 'complete' -or [int64]$hint1.totalCommitted -ne $ExpectedPlacements) {
        throw "hint1 process exited without complete $ExpectedPlacements-row progress"
    }

    Write-SupervisorProgress -Status 'auditing' -Stage 'hint1' -Detail 'running full committed-batch audit'
    & 'C:\Python314\python.exe' $Runner audit --output-dir $Hint1Dir --expected $ExpectedPlacements
    if ($LASTEXITCODE -ne 0) {
        throw "hint1 audit failed with exit code $LASTEXITCODE"
    }

    if (Test-Path -LiteralPath $Hint6Dir) {
        throw "hint6 output directory already exists; refusing to launch a duplicate"
    }
    Write-SupervisorProgress -Status 'running' -Stage 'hint6' -Detail 'launching one exclusive level18/book/16-thread worker'
    & 'C:\Python314\python.exe' $Runner run `
        --source-csv $SourceCsv `
        --stage hint6 `
        --engine $Engine `
        --output-dir $Hint6Dir `
        --workers 1 `
        --batch-size 128 `
        --timeout 900 `
        --max-attempts 2
    if ($LASTEXITCODE -ne 0) {
        throw "hint6 run failed with exit code $LASTEXITCODE"
    }

    Write-SupervisorProgress -Status 'auditing' -Stage 'hint6' -Detail 'running full committed-batch audit'
    & 'C:\Python314\python.exe' $Runner audit --output-dir $Hint6Dir --expected $ExpectedPlacements
    if ($LASTEXITCODE -ne 0) {
        throw "hint6 audit failed with exit code $LASTEXITCODE"
    }
    Write-SupervisorProgress -Status 'complete' -Stage 'hint6' -Detail 'both full stages and audits completed'
}
catch {
    Write-SupervisorProgress -Status 'failed' -Stage 'supervisor' -Detail $_.Exception.ToString()
    throw
}

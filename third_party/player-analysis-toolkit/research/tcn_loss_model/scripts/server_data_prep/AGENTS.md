# AGENTS.md — Windows 9950X TCN data-preparation handoff

## Authorized scope

Run this package from its extracted directory until the frozen 10,000-game data is
fully recomputed, assembled, and validated as TCN model-ready data. Stop after the
362-feature/23-channel data contract passes. This server has no CUDA and must not
train, fine-tune, personalize, predict with, or evaluate a formal TCN model.

## Required entry point

Use one entry point only:

```powershell
.\run_windows_data_prep.cmd
```

If `python.exe` is not discoverable, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_windows_data_prep.ps1 -PythonExe "C:\path\to\python.exe"
```

Do not launch a second copy. Do not run the old disabled server analyzer.

## Fixed computation contract

- Frozen source: exactly 10,000 games, 609,124 rows, 599,112 placements, 10,012 passes.
- No network game pulls and no replacement games.
- hint1 is already complete and must pass its 599,112-row audit.
- hint6 resumes from the supplied audited 14,080-row partial evidence.
- hint6 is locked to 12 workers, one Console per worker, level18 with book,
  16 threads per Console, hash25, batch size 128, timeout 900 seconds, max attempts 2.
- Never use `-q` or `-noboard`; native Console boards are mandatory.
- `setboard + hint + complete response read` remains one atomic transaction.
- Do not change worker count, thread count, hash, level, book, sample, or source.

## Mandatory gates

The entry point performs these gates in order and must stop on any failure:

1. Verify every packaged immutable file against `package_manifest.json`.
2. Re-audit all 599,112 supplied hint1 rows.
3. Create a relocated working copy of the original partial hint6 evidence. The
   original evidence remains untouched under `evidence\hint6_partial_original`.
4. Audit the relocated 14,080-row work copy before resuming.
5. Resume hint6 and then audit all 599,112 rows: unique keys, native board agreement,
   legal candidates, exact candidate count, and no duplicates.
6. Merge by exact `(game_id, move_index)` only and verify the frozen 10,000-game shape.
7. Materialize official 362 numeric features and 23 board-CNN channels.
8. Validate feature order, board-channel order, preprocessing identity, masks, labels,
   game-level splits, current-move hiding, and checkpoint compatibility on CPU.

Success is only `DELIVERY_STATUS.json` with `status: "complete"` and
`stage: "data-ready"`, plus `results\model_ready\server_final_validation.json`
with `ok: true`. A stale `progress.json` is not success.

## Preservation and recovery

- Treat all text as UTF-8.
- Never delete, clean, truncate, reset, or overwrite supplied evidence.
- Do not use `Remove-Item`, `del`, `rd`, `git clean`, or equivalent deletion.
- Committed JSONL batches are immutable. A crash may discard only an uncommitted batch.
- Re-run the same entry point to resume hint6. It reads committed keys and skips them.
- If assembly or materialization has created a partial output and stopped, preserve it,
  diagnose the error, and create a new independent attempt path rather than deleting it.
- Preserve full errors and all progress/manifest/audit files for the workstation agent.

## Dependencies

Python must import NumPy, pandas, PyTorch, SciPy, and scikit-learn. If missing, install
the CPU/data-preparation dependencies from `requirements-data-prep.txt`. CUDA is neither
needed nor authorized for this package.

"""Separate competition and background budgets for local Egaroucid launchers.

Competition execution is capped at two workers.  Background investigation may
use the floor of 50% of physical memory over the 1400 MiB per-worker budget.
Slot files are held with an OS lock so separate Python commands still share the
same physical-memory pool.
"""

from __future__ import annotations

import ctypes
import os
import time
from pathlib import Path
from typing import Any


EGAROUCID_WORKER_MEMORY_MB = 1400
EGAROUCID_MEMORY_FRACTION_PERCENT = 50
FORMAL_EGAROUCID_MAX_WORKERS = 2
_BYTES_PER_MIB = 1024 * 1024
_SLOT_WAIT_SECONDS = 600.0


class EgaroucidWorkerBudgetError(RuntimeError):
    """Raised when the requested Egaroucid worker budget cannot be granted."""


def _windows_physical_memory_bytes() -> int | None:
    if os.name != "nt":
        return None

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return int(status.ullTotalPhys)
    return None


def physical_memory_bytes() -> int:
    """Return total physical memory in bytes using UTF-8-independent OS APIs."""

    windows_value = _windows_physical_memory_bytes()
    if windows_value:
        return windows_value
    if hasattr(os, "sysconf"):
        try:
            return int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
        except (OSError, ValueError):
            pass
    raise EgaroucidWorkerBudgetError("unable to determine total physical memory")


def memory_based_worker_limit(total_memory_bytes: int | None = None) -> int:
    """Return floor(50% of physical memory / 1400 MiB)."""

    total = int(total_memory_bytes if total_memory_bytes is not None else physical_memory_bytes())
    if total <= 0:
        return 0
    allowed = total * EGAROUCID_MEMORY_FRACTION_PERCENT // 100
    return int(allowed // (EGAROUCID_WORKER_MEMORY_MB * _BYTES_PER_MIB))


def max_egaroucid_workers(total_memory_bytes: int | None = None) -> int:
    """Return the competition execution limit (at most two workers)."""

    return min(
        FORMAL_EGAROUCID_MAX_WORKERS,
        memory_based_worker_limit(total_memory_bytes),
    )


def max_background_egaroucid_workers(total_memory_bytes: int | None = None) -> int:
    """Return the background limit derived from the 50% memory budget."""

    return memory_based_worker_limit(total_memory_bytes)


def worker_budget_info(total_memory_bytes: int | None = None) -> dict[str, Any]:
    total = int(total_memory_bytes if total_memory_bytes is not None else physical_memory_bytes())
    return {
        "totalPhysicalMemoryBytes": total,
        "totalPhysicalMemoryMiB": round(total / _BYTES_PER_MIB, 2),
        "workerMemoryMiB": EGAROUCID_WORKER_MEMORY_MB,
        "memoryFraction": EGAROUCID_MEMORY_FRACTION_PERCENT / 100,
        "memoryBasedWorkerLimit": memory_based_worker_limit(total),
        "formalWorkerLimit": FORMAL_EGAROUCID_MAX_WORKERS,
        "competitionWorkerLimit": max_egaroucid_workers(total),
        "backgroundWorkerLimit": max_background_egaroucid_workers(total),
        # Kept for older manifests; it means the competition limit.
        "effectiveWorkerLimit": max_egaroucid_workers(total),
    }


def _validate_worker_count(
    requested: int | None,
    *,
    limit: int,
    label: str,
    budget_name: str,
) -> int:
    if limit < 1:
        info = worker_budget_info()
        raise EgaroucidWorkerBudgetError(
            f"{label} cannot start: {budget_name} budget permits 0 workers; "
            f"memory={info['totalPhysicalMemoryMiB']} MiB, "
            f"perWorker={EGAROUCID_WORKER_MEMORY_MB} MiB"
        )
    value = limit if requested is None else int(requested)
    if value < 1:
        raise ValueError(f"{label} workers must be positive")
    if value > limit:
        info = worker_budget_info()
        raise ValueError(
            f"{label} requested {value} workers, but the {budget_name} limit is {limit} "
            f"(competition={info['competitionWorkerLimit']}, "
            f"background={info['backgroundWorkerLimit']})"
        )
    return value


def validate_egaroucid_workers(requested: int | None, *, label: str = "Egaroucid") -> int:
    """Validate a competition worker count; never silently lower a request."""

    return _validate_worker_count(
        requested,
        limit=max_egaroucid_workers(),
        label=label,
        budget_name="competition",
    )


def validate_background_egaroucid_workers(
    requested: int | None, *, label: str = "background Egaroucid"
) -> int:
    """Validate a background worker count against the 50% memory budget."""

    return _validate_worker_count(
        requested,
        limit=max_background_egaroucid_workers(),
        label=label,
        budget_name="background 50% physical-memory",
    )


def _slot_directory() -> Path:
    # .../papp/third_party/player-analysis-toolkit/src/player_analysis_toolkit/*.py
    papp_root = Path(__file__).resolve().parents[4]
    return papp_root / "data" / "egaroucid-worker-slots"


class EgaroucidWorkerReservation:
    """Hold exclusive machine-wide slots for a group of engine processes."""

    def __init__(
        self, workers: int, *, label: str = "Egaroucid", background: bool = False
    ) -> None:
        validator = (
            validate_background_egaroucid_workers
            if background
            else validate_egaroucid_workers
        )
        self.workers = validator(workers, label=label)
        self.label = label
        self.slot_directory = _slot_directory()
        self._handles: list[Any] = []

    def _try_lock(self, handle: Any) -> bool:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return False
            return True
        import fcntl

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(self, handle: Any) -> None:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def acquire(self, timeout: float = _SLOT_WAIT_SECONDS) -> "EgaroucidWorkerReservation":
        self.slot_directory.mkdir(parents=True, exist_ok=True)
        capacity = max_background_egaroucid_workers()
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            candidates: list[Any] = []
            for slot_index in range(capacity):
                path = self.slot_directory / f"slot-{slot_index:02d}.lock"
                handle = path.open("a+b")
                if self._try_lock(handle):
                    candidates.append(handle)
                    if len(candidates) == self.workers:
                        self._handles = candidates
                        return self
                else:
                    handle.close()
            for handle in candidates:
                self._unlock(handle)
            if deadline is not None and time.monotonic() >= deadline:
                raise EgaroucidWorkerBudgetError(
                    f"timed out waiting for {self.workers} {self.label} worker slot(s); "
                    f"machine limit={capacity}"
                )
            time.sleep(0.1)

    def release(self) -> None:
        handles, self._handles = self._handles, []
        for handle in handles:
            self._unlock(handle)

    def __enter__(self) -> "EgaroucidWorkerReservation":
        return self.acquire()

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.release()


def reserve_egaroucid_workers(workers: int, *, label: str = "Egaroucid") -> EgaroucidWorkerReservation:
    return EgaroucidWorkerReservation(workers, label=label)


def reserve_background_egaroucid_workers(
    workers: int, *, label: str = "background Egaroucid"
) -> EgaroucidWorkerReservation:
    return EgaroucidWorkerReservation(workers, label=label, background=True)

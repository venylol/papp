#!/usr/bin/env python3
"""Run the frozen safe recompute implementation with a bundle-local command matrix."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


BUNDLE_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = Path(__file__).with_name("safe_recompute_egaroucid_hints.py")
COMMAND_MATRIX = BUNDLE_ROOT / "assets" / "protocol" / "console_command_matrix.md"


def load_runner():
    spec = importlib.util.spec_from_file_location("bundled_safe_recompute", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load safe recompute runner: {RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.COMMAND_MATRIX = COMMAND_MATRIX
    return module


if __name__ == "__main__":
    if not COMMAND_MATRIX.is_file():
        raise SystemExit(f"bundle command matrix is missing: {COMMAND_MATRIX}")
    raise SystemExit(load_runner().main())

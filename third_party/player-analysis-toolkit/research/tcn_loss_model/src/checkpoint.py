"""Strict transfer loading and compatibility reporting."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import torch

from .backbone import ModelConfig
from .feature_policy import INPUT_POLICY, assert_uniform_loss_history_policy
from .model import (
    OffbookProfileConditionedLossModel,
    ProfileConditionedLossModel,
    TimeConditionedLossModel,
)

EXPECTED_MODEL_NAME = "tcn_board_cnn_time_model"
EXPECTED_INPUT_DIM = 362
EXPECTED_BOARD_CHANNELS = 23
EXPECTED_LEGACY_WLD_MISSING_KEYS = frozenset({"wld_head.weight", "wld_head.bias"})
EXPECTED_OFFBOOK_MIGRATION_KEYS = frozenset({
    "offbook_severity_film.weight", "offbook_severity_film.bias",
})


def load_trained_state_with_wld_migration(model: torch.nn.Module, state: dict[str, Any]) -> dict[str, Any]:
    """Strictly load current state, or an old state missing only the new WLD head."""
    expected = set(model.state_dict())
    supplied = set(state)
    missing = expected - supplied
    unexpected = supplied - expected
    if unexpected or (missing and missing != EXPECTED_LEGACY_WLD_MISSING_KEYS):
        raise RuntimeError(
            "checkpoint state mismatch: "
            f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
        )
    merged = model.state_dict()
    merged.update(state)
    model.load_state_dict(merged, strict=True)
    return {
        "migratedLegacyCheckpoint": missing == EXPECTED_LEGACY_WLD_MISSING_KEYS,
        "missingKeys": sorted(missing),
        "unexpectedKeys": sorted(unexpected),
        "strictFinalLoad": True,
    }


def load_trained_state_with_offbook_migration(
    model: torch.nn.Module, state: dict[str, Any]
) -> dict[str, Any]:
    """Migrate a formal profile+WLD member by adding only the zero offbook branch."""

    expected = set(model.state_dict())
    supplied = set(state)
    missing = expected - supplied
    unexpected = supplied - expected
    if unexpected or missing not in (set(), set(EXPECTED_OFFBOOK_MIGRATION_KEYS)):
        raise RuntimeError(
            "offbook checkpoint state mismatch: "
            f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
        )
    merged = model.state_dict()
    merged.update(state)
    model.load_state_dict(merged, strict=True)
    if missing == EXPECTED_OFFBOOK_MIGRATION_KEYS:
        if not bool(torch.equal(model.offbook_severity_film.weight, torch.zeros_like(model.offbook_severity_film.weight))):
            raise AssertionError("new offbook migration branch must start at exact zero weights")
        if not bool(torch.equal(model.offbook_severity_film.bias, torch.zeros_like(model.offbook_severity_film.bias))):
            raise AssertionError("new offbook migration branch must start at exact zero bias")
    return {
        "migratedOffbookCheckpoint": missing == EXPECTED_OFFBOOK_MIGRATION_KEYS,
        "missingKeys": sorted(missing),
        "unexpectedKeys": sorted(unexpected),
        "strictFinalLoad": True,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_checkpoint_payload(path: Path) -> dict[str, Any]:
    payload = torch.load(path, map_location="cpu", weights_only=False)
    if not isinstance(payload, dict) or "model_state_dict" not in payload:
        raise ValueError("base checkpoint is not the expected dictionary payload")
    return payload


def verify_checkpoint(path: Path, preprocessing_path: Path | None = None) -> dict[str, Any]:
    checkpoint = load_checkpoint_payload(path)
    errors: list[str] = []
    if checkpoint.get("model_name") != EXPECTED_MODEL_NAME:
        errors.append(f"model_name={checkpoint.get('model_name')!r}")
    if int(checkpoint.get("input_dim", -1)) != EXPECTED_INPUT_DIM:
        errors.append(f"input_dim={checkpoint.get('input_dim')!r}")
    input_features = checkpoint.get("input_features")
    if not isinstance(input_features, list) or len(input_features) != EXPECTED_INPUT_DIM:
        errors.append("checkpoint input feature list is not exactly 362 entries")
    elif len(set(input_features)) != EXPECTED_INPUT_DIM:
        errors.append("checkpoint input feature names are not unique")
    else:
        try:
            assert_uniform_loss_history_policy(input_features)
        except ValueError as exc:
            errors.append(str(exc))
    board_encoding = checkpoint.get("board_encoding", {})
    if int(board_encoding.get("board_cnn_input_channels", -1)) != EXPECTED_BOARD_CHANNELS:
        errors.append(f"board_cnn_input_channels={board_encoding.get('board_cnn_input_channels')!r}")
    if len(board_encoding.get("cnn_channels", [])) != EXPECTED_BOARD_CHANNELS:
        errors.append("board CNN channel order list is not exactly 23 entries")
    preprocessing = checkpoint.get("preprocessing", {})
    if preprocessing.get("input_features") != input_features:
        errors.append("checkpoint preprocessing feature order differs from checkpoint input_features")
    if len(preprocessing.get("means", [])) != len(preprocessing.get("numeric_features", [])):
        errors.append("preprocessing mean vector length mismatch")
    if len(preprocessing.get("stds", [])) != len(preprocessing.get("numeric_features", [])):
        errors.append("preprocessing std vector length mismatch")
    if preprocessing_path:
        external = json.loads(preprocessing_path.read_text(encoding="utf-8"))
        for key in ("numeric_features", "missing_indicator_features", "input_features", "means", "stds", "board_encoding"):
            if external.get(key) != preprocessing.get(key):
                errors.append(f"external preprocessing mismatch: {key}")
    if errors:
        raise ValueError("checkpoint compatibility failed: " + "; ".join(errors))

    cfg = ModelConfig.from_checkpoint(checkpoint)
    model = TimeConditionedLossModel(cfg)
    model.backbone.load_state_dict(checkpoint["model_state_dict"], strict=True)
    return {
        "compatible": True,
        "checkpoint": str(path.resolve()),
        "checkpointSha256": sha256_file(path),
        "checkpointVersion": checkpoint.get("checkpoint_version"),
        "sourceEpoch": checkpoint.get("epoch"),
        "sourceBestEpoch": checkpoint.get("best_epoch"),
        "inputFeatures": len(input_features),
        "numericFeatures": len(preprocessing["numeric_features"]),
        "missingIndicators": len(preprocessing["missing_indicator_features"]),
        "boardCnnChannels": len(board_encoding["cnn_channels"]),
        "stateTensorCount": len(checkpoint["model_state_dict"]),
        "strictBackboneLoad": True,
        "inputPolicy": INPUT_POLICY,
        "lossHistoryInputFeatures": 0,
        "sourceMaxSequenceLength": checkpoint.get("max_seq_len"),
        "passSemanticWarning": (
            "source checkpoint reports max_seq_len above 60; original trainer did not filter actual_move='-'. "
            "New loss-model sequences must exclude pass decision rows and remap ply to global_placement_ply."
        ),
    }


def load_transferred_model(path: Path) -> tuple[TimeConditionedLossModel, dict[str, Any]]:
    checkpoint = load_checkpoint_payload(path)
    model = TimeConditionedLossModel(ModelConfig.from_checkpoint(checkpoint))
    model.backbone.load_state_dict(checkpoint["model_state_dict"], strict=True)
    return model, checkpoint


def load_transferred_profile_model(
    path: Path,
    profile_ablation: str = "full-31",
) -> tuple[ProfileConditionedLossModel, dict[str, Any]]:
    checkpoint = load_checkpoint_payload(path)
    model = ProfileConditionedLossModel(ModelConfig.from_checkpoint(checkpoint), profile_ablation)
    model.backbone.load_state_dict(checkpoint["model_state_dict"], strict=True)
    return model, checkpoint


def load_transferred_offbook_profile_model(
    path: Path,
    profile_ablation: str = "full-31",
) -> tuple[OffbookProfileConditionedLossModel, dict[str, Any]]:
    checkpoint = load_checkpoint_payload(path)
    model = OffbookProfileConditionedLossModel(ModelConfig.from_checkpoint(checkpoint), profile_ablation)
    model.backbone.load_state_dict(checkpoint["model_state_dict"], strict=True)
    return model, checkpoint

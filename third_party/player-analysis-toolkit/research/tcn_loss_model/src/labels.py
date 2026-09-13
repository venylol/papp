"""Auditable next-placement disc-loss labels and pass-safe placement ply."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .feature_policy import assert_uniform_loss_history_policy

REQUIRED_LABEL_COLUMNS = ("game_id", "move_index", "ply", "side_to_move", "actual_move", "hint6_1_score")
FORBIDDEN_MODEL_INPUT_COLUMNS = {
    "next_move_index", "next_source_ply", "next_side_to_move", "next_best_score",
    "raw_loss", "disc_loss", "actual_loss", "has_consecutive_child", "same_side_after_move",
    "label_zero", "label_ge4", "label_ge10", "severity_class",
    "label_loss_ge4", "label_loss_ge10",
    "current_score", "actual_move_score", "before_wld_rank", "after_wld_rank",
    "wld_class", "wld_loss", "wld_label_available",
}

SEVERITY_CLASS_NAMES = ("class_zero", "class_1_3", "class_4_9", "class_ge10")
WLD_CLASS_NAMES = ("class_no_wld_loss", "class_half_wld_loss", "class_full_wld_loss")
WLD_MIN_GLOBAL_PLACEMENT_PLY = 39


def score_to_wld_rank(values: pd.Series) -> pd.Series:
    """Map an Egaroucid side-to-move score to Loss=0, Draw=1, Win=2."""
    score = pd.to_numeric(values, errors="coerce")
    result = pd.Series(np.nan, index=score.index, dtype="float64")
    result.loc[score.lt(0)] = 0
    result.loc[score.eq(0)] = 1
    result.loc[score.gt(0)] = 2
    return result


def disc_loss_to_severity_class(values: pd.Series) -> pd.Series:
    loss = pd.to_numeric(values, errors="coerce")
    non_integer = loss.notna() & ~np.isclose(loss, np.round(loss))
    if bool(non_integer.any()):
        raise ValueError("disc_loss must be integer-valued for the four severity intervals")
    result = pd.Series(np.nan, index=loss.index, dtype="float64")
    result.loc[loss.eq(0)] = 0
    result.loc[loss.between(1, 3)] = 1
    result.loc[loss.between(4, 9)] = 2
    result.loc[loss.ge(10)] = 3
    invalid = loss.notna() & (loss < 0)
    if bool(invalid.any()):
        raise ValueError("disc_loss must be nonnegative before severity classification")
    return result


def generate_disc_loss_labels(frame: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in REQUIRED_LABEL_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"label input missing columns: {missing}")
    df = frame.copy()
    df["move_index"] = pd.to_numeric(df["move_index"], errors="raise")
    df["ply"] = pd.to_numeric(df["ply"], errors="raise")
    df["hint6_1_score"] = pd.to_numeric(df["hint6_1_score"], errors="coerce")
    df = df.sort_values(["game_id", "move_index"], kind="stable").reset_index(drop=True)
    df["source_ply_including_pass"] = df["ply"]
    df["is_pass_record"] = df["actual_move"].astype("string").str.strip().eq("-").fillna(False)
    df["global_placement_ply"] = (~df["is_pass_record"]).astype("int64").groupby(df["game_id"], observed=True).cumsum()

    # A pass is a source-row continuity record, not an evaluated decision. Link
    # each placement to the next actual placement while requiring every skipped
    # source row to be consecutive. Pass rows therefore need no engine score.
    next_placement_row = np.full(len(df), -1, dtype=np.int64)
    for positions in df.groupby("game_id", sort=False, observed=True).indices.values():
        positions = np.asarray(positions, dtype=np.int64)
        next_position = -1
        for position in positions[::-1]:
            next_placement_row[position] = next_position
            if not bool(df.at[position, "is_pass_record"]):
                next_position = int(position)

    has_next_placement = next_placement_row >= 0
    safe_next_row = np.where(has_next_placement, next_placement_row, 0)
    next_rows = df.iloc[safe_next_row]
    df["next_game_id"] = np.where(has_next_placement, next_rows["game_id"].to_numpy(), None)
    df["next_move_index"] = np.where(has_next_placement, next_rows["move_index"].to_numpy(), np.nan)
    df["next_source_ply"] = np.where(
        has_next_placement, next_rows["source_ply_including_pass"].to_numpy(), np.nan
    )
    df["next_side_to_move"] = np.where(has_next_placement, next_rows["side_to_move"].to_numpy(), None)
    df["next_best_score"] = np.where(has_next_placement, next_rows["hint6_1_score"].to_numpy(), np.nan)
    source_gap = df["next_move_index"] - df["move_index"]
    row_gap = next_placement_row - np.arange(len(df), dtype=np.int64)
    df["child_pass_count"] = np.where(has_next_placement, row_gap - 1, np.nan)
    df["has_consecutive_child"] = (
        has_next_placement
        & df["game_id"].eq(df["next_game_id"])
        & source_gap.eq(row_gap)
        & df["next_source_ply"].eq(df["source_ply_including_pass"] + source_gap)
    )
    df["same_side_after_move"] = df["side_to_move"].eq(df["next_side_to_move"]).fillna(False)
    complete = df["hint6_1_score"].notna() & df["next_best_score"].notna()
    eligible = (~df["is_pass_record"]) & df["has_consecutive_child"] & complete
    df["current_score"] = df["hint6_1_score"]
    df["actual_move_score"] = np.where(
        eligible,
        np.where(df["same_side_after_move"], df["next_best_score"], -df["next_best_score"]),
        np.nan,
    )
    df["raw_loss"] = np.where(
        eligible,
        np.where(df["same_side_after_move"], df["hint6_1_score"] - df["next_best_score"], df["hint6_1_score"] + df["next_best_score"]),
        np.nan,
    )
    df["disc_loss"] = df["raw_loss"].clip(lower=0)
    df["severity_class"] = disc_loss_to_severity_class(df["disc_loss"])
    df["label_zero"] = np.where(df["disc_loss"].notna(), (df["disc_loss"] == 0).astype("int8"), np.nan)
    df["label_ge4"] = np.where(df["disc_loss"].notna(), (df["disc_loss"] >= 4).astype("int8"), np.nan)
    df["label_ge10"] = np.where(df["disc_loss"].notna(), (df["disc_loss"] >= 10).astype("int8"), np.nan)
    df["label_available"] = (~df["is_pass_record"]) & df["disc_loss"].notna()
    df["before_wld_rank"] = score_to_wld_rank(df["current_score"])
    df["after_wld_rank"] = score_to_wld_rank(df["actual_move_score"])
    df["wld_class"] = (df["before_wld_rank"] - df["after_wld_rank"]).clip(lower=0)
    wld_eligible = eligible & df["global_placement_ply"].ge(WLD_MIN_GLOBAL_PLACEMENT_PLY)
    df.loc[~wld_eligible, "wld_class"] = np.nan
    df["wld_loss"] = df["wld_class"] / 2.0
    df["wld_label_available"] = wld_eligible & df["wld_class"].notna()
    df["child_continuity_ok"] = df["has_consecutive_child"]
    df["child_transition"] = np.where(
        ~df["has_consecutive_child"], "",
        np.where(df["same_side_after_move"], "same-side-after-pass", "normal-turn-change"),
    )
    return df


def decision_nodes(frame: pd.DataFrame) -> pd.DataFrame:
    """Return actual placements only and remap the legacy `ply` feature to 1..60."""
    if "is_pass_record" not in frame or "global_placement_ply" not in frame:
        raise ValueError("run generate_disc_loss_labels before decision_nodes")
    result = frame.loc[~frame["is_pass_record"]].copy()
    if result["global_placement_ply"].lt(1).any() or result["global_placement_ply"].gt(60).any():
        sample = result.loc[~result["global_placement_ply"].between(1, 60), ["game_id", "move_index", "global_placement_ply"]].head().to_dict("records")
        raise ValueError(f"actual placement ply outside 1..60: {sample}")
    result["ply"] = result["global_placement_ply"]
    return result


def assert_no_label_leakage(input_columns: list[str] | tuple[str, ...]) -> None:
    leaked = sorted(set(input_columns) & FORBIDDEN_MODEL_INPUT_COLUMNS)
    if leaked:
        raise ValueError(f"label/child columns cannot enter model input: {leaked}")
    assert_uniform_loss_history_policy(input_columns)

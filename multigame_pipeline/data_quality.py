"""
Data Quality Checks — Pre-Training Validation Gates
=====================================================
Runs schema, range, duplicate, class-balance, sample-size, and null-density
checks on a game DataFrame *before* any model training begins.

Usage:
    from multigame_pipeline.data_quality import run_data_quality_checks

    report = run_data_quality_checks(df, "gonogo")
    if not report.passed:
        print("Aborting: ", report.failures)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .preprocessor import (
    NUMERIC_FEATURES,
    CATEGORICAL_FEATURES,
    GAME_EXTRA_FEATURES,
    get_extra_features,
)

log = logging.getLogger(__name__)

# ── Configurable thresholds ───────────────────────────────────────────────────

RANGE_CHECKS: dict[str, tuple[float, float]] = {
    "Reaction_Time": (0.05, 30.0),
    "Time_Spent": (1.0, 3600.0),
    "Total_Actions": (0, 10_000),
    "Correct_Responses": (0, 10_000),
    "Incorrect_Responses": (0, 10_000),
    "Hint_Usage": (0, 500),
    "Touch_Interactions": (0, 50_000),
}

MIN_PARTICIPANTS = 20
MIN_SESSIONS = 100
CLASS_WARN_THRESHOLD = 0.10   # warn if any cluster < 10 %
CLASS_FAIL_THRESHOLD = 0.05   # fail if any cluster < 5 %
NULL_FAIL_THRESHOLD = 0.30    # fail if a numeric col has > 30 % nulls
FEATURE_SET_MIN_NON_NULL_SHARE = 1.0 - NULL_FAIL_THRESHOLD

GAME_REQUIRED_FEATURE_SETS: dict[str, list[tuple[str, ...]]] = {
    "gonogo": [(
        "hits", "misses", "false_alarms", "correct_rejections",
        "commission_error_rate", "omission_error_rate",
        "d_prime_approx", "rt_variability_ms",
    )],
    "memory": [(
        "pairs_found", "total_pairs", "total_attempts",
        "match_accuracy", "completion_time_s",
    )],
    "tracking": [(
        "hits", "misses", "hit_rate", "avg_rt_ms",
        "rt_std_ms", "avg_target_speed", "avg_distractor_count",
    )],
    "shapes": [
        (
            "levels_played", "levels_completed", "levels_failed",
            "redo_count", "total_level_attempts", "session_accuracy",
            "avg_time_per_level",
        ),
        (
            "levels_played", "levels_won", "total_moves",
            "session_accuracy", "avg_move_efficiency", "avg_time_per_level",
        ),
        ("moves_used", "optimal_moves", "move_efficiency", "time_s", "solved"),
    ],
    "puzzle": [
        (
            "levels_completed", "avg_mastery_index", "avg_planning_score",
            "avg_impulsivity_score", "avg_attention_score",
            "avg_frustration_score", "avg_time_score",
            "Hint_Usage",
            "correct_placements", "wrong_placements",
            "total_pieces", "placement_accuracy",
        ),
        (
            "levels_completed", "avg_mastery_index", "avg_planning_score",
            "avg_impulsivity_score", "avg_attention_score",
            "avg_frustration_score", "avg_time_score",
            "Hint_Usage",
        ),
        (
            "pieces_count", "mastery_index", "planning_score",
            "impulsivity_score", "attention_score",
            "frustration_score", "time_score",
            "Hint_Usage",
        ),
    ],
}


def _numeric_non_null_share(df: pd.DataFrame, col: str) -> float:
    if col not in df.columns:
        return 0.0
    series = pd.to_numeric(df[col], errors="coerce")
    return float(series.notna().mean())


def _feature_set_support(df: pd.DataFrame, cols: tuple[str, ...]) -> dict:
    missing = [col for col in cols if col not in df.columns]
    shares = {
        col: _numeric_non_null_share(df, col)
        for col in cols
        if col in df.columns
    }
    sparse = [
        col
        for col, share in shares.items()
        if share < FEATURE_SET_MIN_NON_NULL_SHARE
    ]
    return {
        "cols": cols,
        "missing": missing,
        "sparse": sparse,
        "min_share": min(shares.values()) if shares else 0.0,
        "mean_share": float(np.mean(list(shares.values()))) if shares else 0.0,
        "supported": not missing and not sparse,
    }


def _supported_feature_sets(df: pd.DataFrame, game_name: str) -> list[dict]:
    return [
        support
        for support in (
            _feature_set_support(df, cols)
            for cols in GAME_REQUIRED_FEATURE_SETS.get(game_name, [])
        )
        if support["supported"]
    ]


def _active_game_extra_features(df: pd.DataFrame, game_name: str) -> list[str]:
    """
    Return the game-specific features that are active for this dataframe.

    Some built-in games, especially shapes, have legacy and current telemetry
    schemas. A single old row can cause pandas to create sparse legacy columns
    for the whole dataframe; those columns should not fail a current-schema
    training run or enter the null-density gate as mandatory features.
    """
    extras = get_extra_features(game_name)
    feature_sets = GAME_REQUIRED_FEATURE_SETS.get(game_name)
    if not feature_sets:
        return extras

    supported = _supported_feature_sets(df, game_name)
    if not supported:
        return []

    best = max(
        supported,
        key=lambda item: (item["mean_share"], len(item["cols"])),
    )
    active = set(best["cols"])
    for col in extras:
        if col not in df.columns or col in active:
            continue
        if _numeric_non_null_share(df, col) >= FEATURE_SET_MIN_NON_NULL_SHARE:
            active.add(col)
    return [col for col in extras if col in active]


# ── Report dataclass ─────────────────────────────────────────────────────────

@dataclass
class DataQualityReport:
    """Immutable result of a data-quality run."""
    passed: bool = True
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def _warn(self, msg: str) -> None:
        self.warnings.append(msg)
        log.warning("[DQ-WARN] %s", msg)

    def _fail(self, msg: str) -> None:
        self.failures.append(msg)
        self.passed = False
        log.error("[DQ-FAIL] %s", msg)


# ── Individual checks ────────────────────────────────────────────────────────

def _check_schema(df: pd.DataFrame, game_name: str, report: DataQualityReport) -> None:
    """Verify required columns exist."""
    n_failures_before = len(report.failures)
    required = {"Participant_ID", "cluster", "Game_Session_ID"}
    required.update(NUMERIC_FEATURES)
    required.update(CATEGORICAL_FEATURES)

    missing = required - set(df.columns)
    if missing:
        report._fail(f"Missing columns: {sorted(missing)}")

    feature_sets = GAME_REQUIRED_FEATURE_SETS.get(game_name)
    if feature_sets:
        supported = _supported_feature_sets(df, game_name)
        if not supported:
            diagnostics = []
            for cols in feature_sets:
                support = _feature_set_support(df, cols)
                sparse = [
                    f"{col} ({_numeric_non_null_share(df, col):.1%} non-null)"
                    for col in support["sparse"]
                ]
                diagnostics.append({
                    "missing": support["missing"],
                    "sparse": sparse,
                })
            report._fail(
                "Missing game-specific feature columns: "
                f"requires one sufficiently populated feature set; {diagnostics}"
            )
        else:
            best = max(
                supported,
                key=lambda item: (item["mean_share"], len(item["cols"])),
            )
            report.stats["active_feature_set"] = list(best["cols"])
    else:
        dynamic_features = get_extra_features(game_name)
        missing_dynamic = sorted(set(dynamic_features) - set(df.columns))
        if missing_dynamic:
            report._fail(
                "Missing game-module feature_set columns: "
                f"{missing_dynamic}"
            )

    if len(report.failures) == n_failures_before:
        report.stats["schema_ok"] = True


def _check_ranges(df: pd.DataFrame, game_name: str, report: DataQualityReport) -> None:
    """Verify numeric columns fall within sane ranges."""
    for col, (lo, hi) in RANGE_CHECKS.items():
        if col not in df.columns:
            continue
        if game_name in {"shapes", "puzzle"} and col == "Reaction_Time":
            lo = 0.0
        series = pd.to_numeric(df[col], errors="coerce")
        n_below = int((series < lo).sum())
        n_above = int((series > hi).sum())
        n_violations = n_below + n_above
        if n_violations > 0:
            pct = round(100 * n_violations / len(df), 2)
            msg = f"{col}: {n_violations} values ({pct}%) outside [{lo}, {hi}]"
            if pct > 10:
                report._fail(msg)
            else:
                report._warn(msg)
    report.stats["range_checked"] = True


def _check_duplicates(df: pd.DataFrame, report: DataQualityReport) -> None:
    """Detect duplicate (Participant_ID, Game_Session_ID) pairs."""
    if "Game_Session_ID" not in df.columns:
        return
    dup_mask = df.duplicated(subset=["Participant_ID", "Game_Session_ID"], keep=False)
    n_dups = int(dup_mask.sum())
    report.stats["n_duplicate_rows"] = n_dups
    if n_dups > 0:
        report._warn(f"{n_dups} duplicate (Participant_ID, Game_Session_ID) rows")


def _check_class_balance(df: pd.DataFrame, report: DataQualityReport) -> None:
    """Alert if cluster distribution is dangerously skewed."""
    if "cluster" not in df.columns:
        report._fail("'cluster' column missing — cannot check class balance")
        return

    counts = df["cluster"].value_counts(normalize=True)
    report.stats["class_distribution"] = counts.to_dict()

    for label, share in counts.items():
        if share < CLASS_FAIL_THRESHOLD:
            report._fail(f"Cluster '{label}' has only {share:.1%} of samples (< {CLASS_FAIL_THRESHOLD:.0%} threshold)")
        elif share < CLASS_WARN_THRESHOLD:
            report._warn(f"Cluster '{label}' has only {share:.1%} of samples (< {CLASS_WARN_THRESHOLD:.0%} threshold)")


def _check_sample_size(df: pd.DataFrame, report: DataQualityReport) -> None:
    """Minimum participant and session counts."""
    n_sessions = len(df)
    n_participants = df["Participant_ID"].nunique() if "Participant_ID" in df.columns else 0

    report.stats["n_sessions"] = n_sessions
    report.stats["n_participants"] = n_participants

    if n_participants < MIN_PARTICIPANTS:
        report._fail(f"Only {n_participants} participants (need ≥ {MIN_PARTICIPANTS})")
    if n_sessions < MIN_SESSIONS:
        report._fail(f"Only {n_sessions} sessions (need ≥ {MIN_SESSIONS})")


def _check_null_density(df: pd.DataFrame, game_name: str, report: DataQualityReport) -> None:
    """Fail if any numeric feature column has > 30 % nulls."""
    active_extras = _active_game_extra_features(df, game_name)
    numeric_cols = list(NUMERIC_FEATURES) + active_extras
    active_extra_set = set(active_extras)

    for col in get_extra_features(game_name):
        if col in df.columns and col not in active_extra_set:
            non_null_share = _numeric_non_null_share(df, col)
            if 0 < non_null_share < FEATURE_SET_MIN_NON_NULL_SHARE:
                report._warn(
                    f"{col}: sparse inactive game feature ignored "
                    f"({non_null_share:.1%} non-null)"
                )

    for col in numeric_cols:
        if col not in df.columns:
            continue
        null_pct = float(df[col].isna().mean())
        if null_pct > NULL_FAIL_THRESHOLD:
            report._fail(f"{col}: {null_pct:.1%} null (> {NULL_FAIL_THRESHOLD:.0%} threshold)")
        elif null_pct > 0.1:
            report._warn(f"{col}: {null_pct:.1%} null")

    report.stats["null_check_done"] = True


# ── Public entry point ────────────────────────────────────────────────────────

def run_data_quality_checks(df: pd.DataFrame, game_name: str) -> DataQualityReport:
    """
    Run all pre-training data quality checks.

    Args:
        df:        Raw session-level DataFrame (as loaded by ``load_raw``).
        game_name: Game identifier (e.g. ``'gonogo'``).

    Returns:
        DataQualityReport with ``passed``, ``warnings``, ``failures``, ``stats``.
    """
    report = DataQualityReport()

    if df is None or len(df) == 0:
        report._fail("DataFrame is empty or None")
        return report

    _check_schema(df, game_name, report)
    _check_ranges(df, game_name, report)
    _check_duplicates(df, report)
    _check_class_balance(df, report)
    _check_sample_size(df, report)
    _check_null_density(df, game_name, report)

    return report

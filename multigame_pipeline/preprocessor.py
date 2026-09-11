"""
Multi-Game Preprocessor
========================
Loads per-game session data from PostgreSQL and encodes categoricals.
Parameterized by game_name to support all 5 games.
"""

import os
import joblib
import pandas as pd
import numpy as np
from sklearn.preprocessing import LabelEncoder, StandardScaler

DEFAULT_GAME_NAMES = ["gonogo", "memory", "tracking", "shapes", "puzzle"]


def get_pipeline_game_names() -> list[str]:
    """Return ML-enabled game modules, falling back to the five built-ins."""
    try:
        from mlops.db import list_ml_enabled_game_ids
        games = list_ml_enabled_game_ids()
        return games or list(DEFAULT_GAME_NAMES)
    except Exception:
        return list(DEFAULT_GAME_NAMES)


GAME_NAMES = get_pipeline_game_names()

OUTPUTS_DIR = os.path.join(os.path.dirname(__file__), "outputs")
os.makedirs(OUTPUTS_DIR, exist_ok=True)

CLUSTER_LABELS = [
    "Combined ADHD",
    "Hyperactive-Impulsive ADHD",
    "Inattentive ADHD",
    "Optimal / Neurotypical",
]

# V1 core numeric features (present in all game session records)
NUMERIC_FEATURES = [
    "Time_Spent",
    "Total_Actions",
    "Correct_Responses",
    "Incorrect_Responses",
    "Touch_Interactions",
    "Reaction_Time",
]

CATEGORICAL_FEATURES = [
    "Age_Group",
    "Cognitive_Level",
    "Game_Completion_Status",
    "Performance_Level",
]

# Game-specific extra numeric columns (auto-detected)
GAME_EXTRA_FEATURES = {
    "gonogo": [
        "hits", "misses", "false_alarms", "correct_rejections",
        "commission_error_rate", "omission_error_rate",
        "d_prime_approx", "rt_variability_ms",
        "pause_count", "retry_count", "rt_cv",
    ],
    "memory": [
        "pairs_found", "total_pairs", "total_attempts",
        "match_accuracy", "completion_time_s",
        "pause_count", "retry_count", "rt_cv",
    ],
    "tracking": [
        "hits", "misses", "hit_rate", "avg_rt_ms",
        "rt_std_ms", "avg_target_speed", "avg_distractor_count",
        "pause_count", "retry_count", "rt_cv",
    ],
    "shapes": [
        "levels_played", "levels_won",
        "levels_completed", "levels_failed", "redo_count",
        "total_level_attempts", "total_moves",
        "session_accuracy", "avg_move_efficiency",
        "avg_time_per_level", "avg_moves_per_level", "avg_rotations",
        "level", "moves_used", "optimal_moves",
        "move_efficiency", "time_s", "solved",
        "completed", "failed", "redo",
        "pause_count", "retry_count", "rt_cv",
    ],
    "puzzle": [
        "Hint_Usage",
        "levels_completed", "avg_mastery_index", "avg_planning_score",
        "avg_impulsivity_score", "avg_attention_score",
        "avg_frustration_score", "avg_time_score",
        "rounds_played", "rounds_passed",
        "correct_placements", "wrong_placements",
        "total_pieces", "pieces_per_round", "placement_accuracy",
        "avg_time_per_round", "distractor_count",
        "pieces_count", "mastery_index", "planning_score",
        "impulsivity_score", "attention_score",
        "frustration_score", "time_score",
        "pause_count", "retry_count", "rt_cv",
    ],
}


def get_extra_features(game_name: str) -> list:
    """Return declared game-specific numeric features."""
    if game_name in GAME_EXTRA_FEATURES:
        return list(GAME_EXTRA_FEATURES[game_name])
    try:
        from mlops.db import get_game_module
        module = get_game_module(game_name)
        feature_set = (module or {}).get("feature_set") or []
        if isinstance(feature_set, list):
            return [
                str(feature)
                for feature in feature_set
                if feature not in NUMERIC_FEATURES and feature not in CATEGORICAL_FEATURES
            ]
    except Exception:
        pass
    return []


def get_game_outputs(game_name: str) -> str:
    """Return (and create) the per-game outputs dir."""
    d = os.path.join(OUTPUTS_DIR, game_name)
    os.makedirs(d, exist_ok=True)
    return d


def get_all_numeric_features(game_name: str) -> list:
    """Return V1 core + game-specific numeric feature columns."""
    extras = get_extra_features(game_name)
    return NUMERIC_FEATURES + extras


def load_raw(game_name: str) -> pd.DataFrame:
    """
    Load game data from the centralised PostgreSQL database.

    CSV files are fixture inputs only and must be imported with
    ``tools/seed_fake_data.py`` before running the pipeline.
    """
    try:
        from mlops.db import load_game_df
        return load_game_df(game_name)
    except ImportError as e:
        raise RuntimeError(
            "mlops.db is required to load pipeline data from PostgreSQL. "
            "Run from the project root with the project dependencies installed."
        ) from e
    except Exception as e:
        raise RuntimeError(f"PostgreSQL load failed for '{game_name}': {e}") from e



def encode_dataframe(df: pd.DataFrame, game_name: str, save_encoders: bool = True):
    """Encode categoricals, return encoded DataFrame + encoder dict."""
    df = df.copy()
    encoders = {}

    for col in CATEGORICAL_FEATURES:
        if col in df.columns:
            le = LabelEncoder()
            df[f"{col}_enc"] = le.fit_transform(df[col].astype(str))
            encoders[col] = le

    # Encode target
    le_target = LabelEncoder()
    df["cluster_enc"] = le_target.fit_transform(df["cluster"])
    encoders["cluster"] = le_target

    if save_encoders:
        out_dir = get_game_outputs(game_name)
        enc_path = os.path.join(out_dir, "label_encoders.pkl")
        joblib.dump(encoders, enc_path)

    return df, encoders


def get_feature_columns(game_name: str, include_categorical_enc: bool = True) -> list:
    """Return the list of feature column names for modeling."""
    cols = list(get_all_numeric_features(game_name))
    if include_categorical_enc:
        cols += [f"{c}_enc" for c in CATEGORICAL_FEATURES]
    return cols


def load_and_encode(game_name: str):
    """Convenience: load + encode → (df_raw, df_encoded, encoders)."""
    df_raw = load_raw(game_name)
    df_enc, encoders = encode_dataframe(df_raw, game_name)
    return df_raw, df_enc, encoders

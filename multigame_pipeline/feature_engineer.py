"""
Multi-Game Feature Engineering
================================
Builds participant-level aggregates, rolling features, derived scores,
and 3D sequence tensors. Auto-includes game-specific extra features.
"""

import numpy as np
import pandas as pd
from .preprocessor import NUMERIC_FEATURES, CATEGORICAL_FEATURES, get_all_numeric_features


def _numeric_col(df: pd.DataFrame, name: str, default=0) -> pd.Series:
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce")
    if isinstance(default, str):
        return _numeric_col(df, default, 0)
    return pd.Series(default, index=df.index, dtype=float)


def add_derived_features(df: pd.DataFrame, game_name: str | None = None) -> pd.DataFrame:
    """Add behaviorally meaningful derived columns to a session-level dataframe."""
    df = df.copy()
    is_shapes = game_name == "shapes"
    is_puzzle = game_name == "puzzle"
    if is_shapes:
        attempts = _numeric_col(df, "total_level_attempts", "levels_played").replace(0, np.nan)
        completed = _numeric_col(df, "levels_completed", "levels_won")
        failed = _numeric_col(df, "levels_failed", "Incorrect_Responses")
        redo = _numeric_col(df, "redo_count", "retry_count")
        df["accuracy_rate"] = completed / attempts
        df["error_rate"] = failed / attempts
        df["impulsivity_score_derived"] = redo / attempts
        df["engagement_score"] = completed / (_numeric_col(df, "Time_Spent").replace(0, np.nan) + 1e-6)
    elif is_puzzle:
        actions = df["Total_Actions"].replace(0, np.nan)
        hints = _numeric_col(df, "Hint_Usage", 0)
        df["accuracy_rate"] = df["Correct_Responses"] / actions
        df["error_rate"] = df["Incorrect_Responses"] / actions
        df["impulsivity_score_derived"] = _numeric_col(df, "avg_impulsivity_score", "error_rate")
        df["engagement_score"] = (
            df["Correct_Responses"] - hints
        ) / (df["Time_Spent"].replace(0, np.nan) + 1e-6)
    else:
        df["accuracy_rate"] = df["Correct_Responses"] / (df["Total_Actions"].replace(0, np.nan))
        df["error_rate"] = df["Incorrect_Responses"] / (df["Total_Actions"].replace(0, np.nan))
        df["impulsivity_score_derived"] = df["Total_Actions"] / (df["Reaction_Time"].replace(0, np.nan))
        df["engagement_score"] = df["Correct_Responses"] / (df["Time_Spent"].replace(0, np.nan) + 1e-6)
    df["completion_flag"] = (df["Game_Completion_Status"] == "Completed").astype(int)

    # ── Pauses/Retries/Variability derived features ──────────────────────
    if "pause_count" in df.columns:
        # Pause rate: pauses per minute of play
        df["pause_rate"] = df["pause_count"] / (df["Time_Spent"].replace(0, np.nan) / 60)
    if is_shapes and ("redo_count" in df.columns or "retry_count" in df.columns):
        attempts = _numeric_col(df, "total_level_attempts", "levels_played").replace(0, np.nan)
        redo = _numeric_col(df, "redo_count", "retry_count")
        df["retry_rate"] = redo / attempts
    elif "retry_count" in df.columns:
        # Retry rate: retries per action (persistence indicator)
        df["retry_rate"] = df["retry_count"] / (df["Total_Actions"].replace(0, np.nan))
    if "rt_cv" in df.columns:
        # Shapes/Puzzle have no meaningful reaction time; keep this neutral there.
        df["rt_instability"] = 0 if (is_shapes or is_puzzle) else df["Reaction_Time"] * df["rt_cv"]
    if is_shapes and all(c in df.columns for c in ["pause_count", "error_rate"]):
        redo = _numeric_col(df, "redo_count", "retry_count")
        df["disengagement_score"] = (
            df["pause_count"] * 0.35 +
            redo * 0.45 +
            df["error_rate"].fillna(0) * 10 * 0.20
        )
    elif is_puzzle and all(c in df.columns for c in ["pause_count", "Hint_Usage", "error_rate"]):
        # Disengagement score: composite of pauses, hints, and errors
        df["disengagement_score"] = (
            df["pause_count"] * 0.4 +
            df["Hint_Usage"] * 0.3 +
            df["error_rate"].fillna(0) * 10 * 0.3
        )
    elif "pause_count" in df.columns and "error_rate" in df.columns:
        df["disengagement_score"] = (
            df["pause_count"] * 0.55 +
            df["error_rate"].fillna(0) * 10 * 0.45
        )

    derived = ["accuracy_rate", "error_rate", "impulsivity_score_derived", "engagement_score"]
    # Add variability-derived columns to fillna/clip
    for col in ["pause_rate", "retry_rate", "rt_instability", "disengagement_score"]:
        if col in df.columns:
            derived.append(col)
    df[derived] = df[derived].fillna(0).clip(-10, 10)
    return df


DERIVED_FEATURES = [
    "accuracy_rate",
    "error_rate",
    "impulsivity_score_derived",
    "engagement_score",
    "completion_flag",
    # Pauses / retries / variability
    "pause_rate",
    "retry_rate",
    "rt_instability",
    "disengagement_score",
]


def get_all_session_features(game_name: str) -> list:
    """Return all numeric session features (V1 core + game extras + derived)."""
    base = get_all_numeric_features(game_name)
    return base + DERIVED_FEATURES


def build_participant_aggregate(df: pd.DataFrame, game_name: str, n_sessions: int = None) -> pd.DataFrame:
    """
    Aggregate session data per participant.
    Returns one row per participant with mean, std, min, max of all numeric features.
    """
    df = add_derived_features(df, game_name)
    all_features = get_all_session_features(game_name)

    # Only include columns that actually exist in the dataframe
    all_features = [c for c in all_features if c in df.columns]

    if n_sessions:
        df = df.groupby("Participant_ID").head(n_sessions)

    agg_dict = {}
    for col in all_features:
        if col in df.columns and df[col].dtype in [np.float64, np.int64, np.float32, np.int32, float, int]:
            agg_dict[f"{col}_mean"] = (col, "mean")
            agg_dict[f"{col}_std"] = (col, "std")
            agg_dict[f"{col}_min"] = (col, "min")
            agg_dict[f"{col}_max"] = (col, "max")

    # Categorical: take the mode
    for col in ["Age_Group", "Cognitive_Level"]:
        if col in df.columns:
            agg_dict[col] = (col, "first")

    agg_dict["cluster"] = ("cluster", "first")

    agg_df = df.groupby("Participant_ID").agg(**agg_dict).reset_index()
    agg_df = agg_df.fillna(0)
    return agg_df


def get_aggregate_feature_cols(agg_df: pd.DataFrame) -> list:
    """Return only the numeric feature columns from an aggregated dataframe."""
    skip = {"Participant_ID", "Age_Group", "Cognitive_Level", "cluster"}
    return [c for c in agg_df.columns if c not in skip]


def build_sequence_tensor(df: pd.DataFrame, game_name: str, n_sessions: int = 50):
    """
    Build a 3D tensor (n_participants, n_sessions, n_features) for LSTM / sequence models.
    """
    df = add_derived_features(df, game_name)
    feature_cols = get_all_session_features(game_name)
    feature_cols = [c for c in feature_cols if c in df.columns]

    participants = df["Participant_ID"].unique()
    n_features = len(feature_cols)

    X_seq = np.zeros((len(participants), n_sessions, n_features), dtype=np.float32)
    y_seq = np.zeros(len(participants), dtype=np.int64)

    from sklearn.preprocessing import LabelEncoder
    le = LabelEncoder()
    df["cluster_int"] = le.fit_transform(df["cluster"])

    for i, pid in enumerate(participants):
        pdata = df[df["Participant_ID"] == pid].reset_index(drop=True)
        sess_count = min(len(pdata), n_sessions)
        X_seq[i, :sess_count, :] = pdata[feature_cols].values[:sess_count]
        y_seq[i] = pdata["cluster_int"].iloc[0]

    return X_seq, y_seq, list(participants), le

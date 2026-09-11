"""
Inference Adapter — NeuroGames MLOps
======================================
Bridges raw session-level data to the trained classifier model.

The classifier is trained on participant-level aggregates produced by
``build_participant_aggregate()`` from the feature_engineer module.
This adapter replicates that exact preprocessing pipeline so that drift
detection can run inference without touching the classifier's training code.

Usage::

    from mlops.inference import load_model_bundle, prepare_inference_data, predict

    result = predict("gonogo", session_df)
    if result:
        probas   = result["probabilities"]   # shape (n_participants, n_classes)
        preds    = result["predictions"]      # integer class indices
        labels   = result["labels"]           # human-readable cluster names
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# Locate project root relative to this file (mlops/ → project root)
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

# Make multigame_pipeline importable
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _bundle_path(game: str) -> str:
    """Return the expected path to the classifier_ensemble.pkl bundle."""
    return os.path.join(
        _ROOT, "multigame_pipeline", "outputs", game, "classifier_ensemble.pkl"
    )


def load_model_bundle(game: str) -> Optional[dict]:
    """
    Load the local ``classifier_ensemble.pkl`` bundle for a game.

    The bundle is a dict saved by ``classifier.py`` containing:
        - ``model``          : VotingClassifier ensemble
        - ``feature_cols``   : list of feature column names used for training
        - ``label_encoder``  : fitted LabelEncoder mapping int → cluster name
        - ``class_names``    : list of class name strings
        - ``calibrated_model``: CalibratedClassifierCV (optional, preferred)

    Returns:
        dict if bundle exists and loads cleanly, else None.
    """
    try:
        import joblib
    except ImportError:
        log.warning("joblib not available; cannot load model bundle")
        return None

    path = _bundle_path(game)
    if not os.path.exists(path):
        log.info("No model bundle at %s — skipping inference", path)
        return None

    try:
        bundle = joblib.load(path)
        log.debug("Loaded model bundle for %s from %s", game, path)
        return bundle
    except Exception as e:
        log.warning("Failed to load model bundle for %s: %s", game, e)
        return None


def prepare_inference_data(
    game: str, session_df: pd.DataFrame
) -> tuple[np.ndarray, list[str], pd.DataFrame]:
    """
    Transform raw session rows into the feature matrix the model expects.

    Replicates the classifier preprocessing pipeline:
        1. ``build_participant_aggregate()``  — aggregate sessions per participant
        2. Encode categorical columns (Age_Group, Cognitive_Level) with LabelEncoder
        3. ``get_aggregate_feature_cols()``   — select numeric feature columns

    Args:
        game:       Game name (used by feature_engineer for game-specific extras).
        session_df: Raw session-level DataFrame from ``load_game_df()``.

    Returns:
        (X, feature_cols, agg_df) where X is (n_participants, n_features) float32.
        Returns (empty array, [], empty DF) if session_df is empty.
    """
    if session_df is None or session_df.empty:
        return np.empty((0, 0), dtype=np.float32), [], pd.DataFrame()

    try:
        from multigame_pipeline.feature_engineer import (
            build_participant_aggregate,
            get_aggregate_feature_cols,
        )
        from sklearn.preprocessing import LabelEncoder
    except ImportError as e:
        log.warning("Feature engineering imports unavailable: %s", e)
        return np.empty((0, 0), dtype=np.float32), [], pd.DataFrame()

    try:
        agg_df = build_participant_aggregate(session_df, game, n_sessions=None)
    except Exception as e:
        log.warning("build_participant_aggregate failed for %s: %s", game, e)
        return np.empty((0, 0), dtype=np.float32), [], pd.DataFrame()

    # Encode categoricals exactly as classifier.py (lines 225-233)
    for col in ["Age_Group", "Cognitive_Level"]:
        if col in agg_df.columns:
            le_c = LabelEncoder()
            agg_df[f"{col}_enc"] = le_c.fit_transform(agg_df[col].astype(str))

    feature_cols = get_aggregate_feature_cols(agg_df)
    feature_cols = [
        c for c in feature_cols
        if c in agg_df.columns and agg_df[c].dtype != object and c != "y"
    ]

    if not feature_cols:
        return np.empty((0, 0), dtype=np.float32), [], agg_df

    X = agg_df[feature_cols].values.astype(np.float32)
    return X, feature_cols, agg_df


def predict(game: str, session_df: pd.DataFrame) -> Optional[dict]:
    """
    Full inference: raw sessions → predictions + class probabilities.

    Uses the calibrated model if available; falls back to the raw ensemble.
    Aligns the feature matrix to the exact column order seen during training,
    filling missing features with 0.

    Args:
        game:       Game name.
        session_df: Raw session-level DataFrame from ``load_game_df()``.

    Returns:
        dict with keys::

            predictions  — int array (n_participants,)
            probabilities — float array (n_participants, n_classes)
            labels       — str array of cluster names
            feature_cols — list[str] of training feature column names
            class_names  — list[str] of class names
            agg_df       — participant-level aggregated DataFrame

        Returns None if the model bundle is not found, data is too small,
        or feature alignment is too poor (< 50% column overlap).
    """
    bundle = load_model_bundle(game)
    if bundle is None:
        return None

    X, feature_cols, agg_df = prepare_inference_data(game, session_df)
    if X.shape[0] == 0:
        log.info("No participants to run inference on for %s", game)
        return None

    bundle_features: list[str] = bundle.get("feature_cols", [])
    if not bundle_features:
        log.warning("Model bundle for %s has no feature_cols", game)
        return None

    # Check feature overlap — require at least 50% match
    common = [c for c in bundle_features if c in feature_cols]
    if len(common) < len(bundle_features) * 0.5:
        log.warning(
            "Insufficient feature overlap for %s: %d/%d columns matched",
            game, len(common), len(bundle_features),
        )
        return None

    # Align columns to training order; zero-fill any missing columns
    X_aligned = np.zeros((X.shape[0], len(bundle_features)), dtype=np.float32)
    fc_index = {c: i for i, c in enumerate(feature_cols)}
    for i, col in enumerate(bundle_features):
        if col in fc_index:
            X_aligned[:, i] = X[:, fc_index[col]]

    model = bundle.get("calibrated_model") or bundle.get("model")
    if model is None:
        log.warning("Model bundle for %s contains no model", game)
        return None

    try:
        preds = model.predict(X_aligned)
        probas = model.predict_proba(X_aligned)
        labels = bundle["label_encoder"].inverse_transform(preds)
    except Exception as e:
        log.warning("Inference failed for %s: %s", game, e)
        return None

    return {
        "predictions": preds,
        "probabilities": probas,
        "labels": labels,
        "feature_cols": bundle_features,
        "class_names": bundle.get("class_names", []),
        "agg_df": agg_df,
    }

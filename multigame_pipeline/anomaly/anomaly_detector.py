"""
Module 4 - Per-Game Anomaly Detection for At-Risk Session Flagging
===================================================================
Hybrid IsolationForest + MLP Autoencoder with per-player historical baselines.

Detection strategy (two-tier):
  - WARM player  (>= min_baseline_sessions): fit IF and AE on their own first
    `n_normal_sessions` sessions; score all remaining sessions against that
    personal baseline.  Flags are relative to the player's own history.
  - COLD player  (<  min_baseline_sessions): fall back to a population model
    trained on all warm-player baseline data.  Flags are relative to the
    population of known-good sessions.

After detection, `run_anomaly_detection` returns the set of flagged
(Participant_ID, Game_Session_ID) pairs so the classifier can remove them
from training data.
"""

import os
import sys
import warnings
import joblib
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns

from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.neural_network import MLPRegressor

warnings.filterwarnings("ignore")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..", "..")
sys.path.insert(0, _ROOT)

from multigame_pipeline.preprocessor import load_raw, get_game_outputs
from multigame_pipeline.feature_engineer import add_derived_features, get_all_session_features


# ── Autoencoder ───────────────────────────────────────────────────────────────

class MLPAutoencoder:
    def __init__(self, n_features, hidden=(32, 16, 32)):
        self.scaler = StandardScaler()
        self.model = MLPRegressor(
            hidden_layer_sizes=hidden, activation="relu",
            max_iter=200, random_state=42, early_stopping=False,
        )
        self.n_features = n_features

    def fit(self, X):
        X_sc = self.scaler.fit_transform(X)
        self.model.fit(X_sc, X_sc)
        return self

    def reconstruction_error(self, X):
        X_sc = self.scaler.transform(X)
        X_rec = self.model.predict(X_sc)
        return np.mean((X_sc - X_rec) ** 2, axis=1)


# ── Per-player detection helpers ──────────────────────────────────────────────

def _score_player_with_personal_model(
    grp: pd.DataFrame,
    feature_cols: list,
    n_normal: int,
    if_contamination: float,
    ae_sigma: float,
) -> tuple:
    """
    Fit IF + AE on the player's first `n_normal` sessions and score all
    of their sessions.  The first sessions are the accepted baseline and
    should not be removed from training as anomalies.
    """
    grp = grp.sort_values("Game_Session_ID").reset_index(drop=True)
    X_baseline = grp.head(n_normal)[feature_cols].values.astype(np.float32)
    X_all = grp[feature_cols].values.astype(np.float32)

    # ── Isolation Forest ──
    scaler_if = StandardScaler()
    X_base_sc = scaler_if.fit_transform(X_baseline)
    X_all_sc = scaler_if.transform(X_all)
    iso = IsolationForest(n_estimators=100, contamination=if_contamination,
                          random_state=42, n_jobs=1)
    iso.fit(X_base_sc)
    if_scores = iso.decision_function(X_all_sc)
    if_threshold = np.percentile(if_scores, if_contamination * 100)

    # ── Autoencoder ──
    ae = MLPAutoencoder(n_features=len(feature_cols))
    ae.fit(X_baseline)
    ae_errors = ae.reconstruction_error(X_all)
    baseline_errors = ae.reconstruction_error(X_baseline)
    baseline_threshold = baseline_errors.mean() + ae_sigma * max(baseline_errors.std(), 1e-9)
    # Small personal baselines can reconstruct too narrowly and label nearly
    # every later session as anomalous.  Bound AE sensitivity to the same
    # expected tail size as IF so persistent traits do not become anomalies.
    tail_threshold = np.percentile(ae_errors, 100 - if_contamination * 100)
    ae_threshold = max(baseline_threshold, tail_threshold)
    ae_flags = (ae_errors > ae_threshold).astype(int)

    if_flags = (if_scores < if_threshold).astype(int)
    if_flags[:n_normal] = 0
    ae_flags[:n_normal] = 0
    return if_flags, ae_flags, if_scores, ae_errors


def _build_population_model(
    df_baseline: pd.DataFrame,
    feature_cols: list,
    if_contamination: float,
) -> tuple:
    """Build a population-level IF + AE from all warm-player baseline sessions."""
    X = df_baseline[feature_cols].values.astype(np.float32)
    scaler_if = StandardScaler()
    X_sc = scaler_if.fit_transform(X)
    iso = IsolationForest(n_estimators=200, contamination=if_contamination,
                          random_state=42, n_jobs=1)
    iso.fit(X_sc)

    ae = MLPAutoencoder(n_features=len(feature_cols))
    ae.fit(X)
    baseline_ae_errors = ae.reconstruction_error(X)
    sigma_threshold = baseline_ae_errors.mean() + 2.0 * max(baseline_ae_errors.std(), 1e-9)
    tail_threshold = np.percentile(baseline_ae_errors, 100 - if_contamination * 100)
    ae_threshold = max(sigma_threshold, tail_threshold)

    return iso, scaler_if, ae, ae_threshold


# ── Main entry point ──────────────────────────────────────────────────────────

def run_anomaly_detection(
    game_name: str,
    n_normal_sessions: int = 10,
    min_baseline_sessions: int = 5,
    if_contamination: float = 0.05,
    ae_sigma: float = 2.0,
    verbose: bool = True,
) -> dict:
    """
    Run per-player hybrid anomaly detection for one game.

    Returns
    -------
    dict with keys:
        game_name       : str
        flags_df        : DataFrame with one row per session + flag columns
        n_flagged       : int  — total anomalous sessions
        flagged_ids     : set of (Participant_ID, Game_Session_ID) tuples
        clean_session_ids : set of Game_Session_IDs that are CLEAN (use for training)
    """
    OUTPUTS = get_game_outputs(game_name)

    if verbose:
        print(f"\n  --- Anomaly Detection: {game_name.upper()} ---")

    df_raw = load_raw(game_name)
    df = add_derived_features(df_raw, game_name)
    feature_cols = get_all_session_features(game_name)
    feature_cols = [c for c in feature_cols if c in df.columns]
    df[feature_cols] = df[feature_cols].fillna(0)

    df_sorted = df.sort_values(["Participant_ID", "Game_Session_ID"]).reset_index(drop=True)

    # ── Partition players into warm / cold ────────────────────────────────────
    session_counts = df_sorted.groupby("Participant_ID")["Game_Session_ID"].count()
    warm_pids = session_counts[session_counts >= min_baseline_sessions].index
    cold_pids = session_counts[session_counts < min_baseline_sessions].index

    if verbose:
        print(f"    Players - warm: {len(warm_pids)} | cold: {len(cold_pids)}")

    # ── Build population model from warm-player baselines (fallback) ──────────
    df_warm_baseline = df_sorted[df_sorted["Participant_ID"].isin(warm_pids)]
    df_warm_baseline = (
        df_warm_baseline.groupby("Participant_ID")
        .head(n_normal_sessions)
        .reset_index(drop=True)
    )
    pop_iso, pop_scaler_if, pop_ae, pop_ae_threshold = _build_population_model(
        df_warm_baseline, feature_cols, if_contamination
    )
    pop_if_threshold = np.percentile(
        pop_iso.decision_function(pop_scaler_if.transform(
            df_warm_baseline[feature_cols].values.astype(np.float32)
        )),
        if_contamination * 100,
    )

    # ── Per-player scoring ────────────────────────────────────────────────────
    if_scores_out = np.zeros(len(df_sorted), dtype=np.float32)
    ae_errors_out = np.zeros(len(df_sorted), dtype=np.float32)
    if_flags_out = np.zeros(len(df_sorted), dtype=int)
    ae_flags_out = np.zeros(len(df_sorted), dtype=int)
    detection_mode = [""] * len(df_sorted)

    for pid, grp in df_sorted.groupby("Participant_ID"):
        idx = grp.index
        X_all = grp[feature_cols].values.astype(np.float32)

        if pid in warm_pids:
            # Personal model
            if_flags, ae_flags, if_sc, ae_err = _score_player_with_personal_model(
                grp, feature_cols, n_normal_sessions, if_contamination, ae_sigma
            )
            if_scores_out[idx] = if_sc
            ae_errors_out[idx] = ae_err
            if_flags_out[idx] = if_flags
            ae_flags_out[idx] = ae_flags
            for i in idx:
                detection_mode[i] = "personal"
        else:
            # Population fallback
            X_sc = pop_scaler_if.transform(X_all)
            if_sc = pop_iso.decision_function(X_sc)
            ae_err = pop_ae.reconstruction_error(X_all)
            if_scores_out[idx] = if_sc
            ae_errors_out[idx] = ae_err
            if_flags_out[idx] = (if_sc < pop_if_threshold).astype(int)
            ae_flags_out[idx] = (ae_err > pop_ae_threshold).astype(int)
            for i in idx:
                detection_mode[i] = "population"

    combined_flags = ((if_flags_out) | (ae_flags_out)).astype(int)

    df_sorted["if_score"] = if_scores_out
    df_sorted["ae_error"] = ae_errors_out
    df_sorted["if_flag"] = if_flags_out
    df_sorted["ae_flag"] = ae_flags_out
    df_sorted["anomaly_flag"] = combined_flags
    df_sorted["detection_mode"] = detection_mode

    n_personal_flagged = int(combined_flags[np.array(detection_mode) == "personal"].sum())
    n_pop_flagged = int(combined_flags[np.array(detection_mode) == "population"].sum())

    if verbose:
        print(f"    IF flagged: {if_flags_out.sum()} | AE flagged: {ae_flags_out.sum()} "
              f"| Combined: {combined_flags.sum()}")
        print(f"    Personal-model flags: {n_personal_flagged} | Population-model flags: {n_pop_flagged}")

    # ── Flagged session ID sets ───────────────────────────────────────────────
    flagged_mask = df_sorted["anomaly_flag"] == 1
    flagged_ids = set(
        zip(df_sorted.loc[flagged_mask, "Participant_ID"],
            df_sorted.loc[flagged_mask, "Game_Session_ID"])
    )
    clean_session_ids = set(df_sorted.loc[~flagged_mask, "Game_Session_ID"].tolist())

    # ── Persist flags ─────────────────────────────────────────────────────────
    cols_out = [
        "Participant_ID", "Game_Session_ID", "cluster",
        "if_score", "ae_error", "if_flag", "ae_flag", "anomaly_flag", "detection_mode",
    ]
    # keep only columns that exist
    cols_out = [c for c in cols_out if c in df_sorted.columns]
    flags_df = df_sorted[cols_out].reset_index(drop=True)

    # CSV (fast, always written)
    flags_csv = os.path.join(OUTPUTS, "anomaly_flags.csv")
    flags_df.to_csv(flags_csv, index=False)

    # PostgreSQL
    try:
        from mlops.db import insert_anomaly_flags
        n_written = insert_anomaly_flags(game_name, flags_df)
        if verbose:
            print(f"    [DB] Wrote {n_written} anomaly flags to PostgreSQL")
    except Exception as e:
        if verbose:
            print(f"    [DB] Warning: could not write to PostgreSQL: {e}")

    # ── Plots ─────────────────────────────────────────────────────────────────
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    axes[0].hist(if_scores_out, bins=60, color="#4C72B0", edgecolor="white", alpha=0.85)
    axes[0].set_title("Isolation Forest Scores (per-player)", fontsize=11, weight="bold")
    axes[1].hist(np.log1p(ae_errors_out), bins=60, color="#DD8452", edgecolor="white", alpha=0.85)
    axes[1].set_title("Autoencoder Error (log, per-player)", fontsize=11, weight="bold")
    plt.suptitle(f"{game_name.upper()} - Anomaly Score Distributions", fontsize=13, weight="bold")
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUTS, "anomaly_score_distribution.png"), dpi=150)
    plt.close()

    if "cluster" in df_sorted.columns:
        flag_by_cluster = (
            df_sorted.groupby("cluster")["anomaly_flag"]
            .agg(["sum", "count"])
            .assign(rate=lambda d: d["sum"] / d["count"])
            .reset_index()
        )
        fig, ax = plt.subplots(figsize=(8, 4))
        palette = sns.color_palette("Reds_r", len(flag_by_cluster))
        ax.bar(flag_by_cluster["cluster"], flag_by_cluster["rate"], color=palette, edgecolor="white")
        ax.set_ylabel("Flag Rate"); ax.set_ylim(0, 1)
        ax.set_title(f"{game_name.upper()} - Anomaly Rate by Cluster", fontsize=12, weight="bold")
        plt.xticks(rotation=20, ha="right"); plt.tight_layout()
        plt.savefig(os.path.join(OUTPUTS, "anomaly_by_cluster.png"), dpi=150)
        plt.close()

    # ── Save model bundle ─────────────────────────────────────────────────────
    joblib.dump({
        "pop_iso": pop_iso,
        "pop_scaler_if": pop_scaler_if,
        "pop_ae": pop_ae,
        "pop_ae_threshold": pop_ae_threshold,
        "pop_if_threshold": pop_if_threshold,
        "feature_cols": feature_cols,
        "n_normal_sessions": n_normal_sessions,
        "min_baseline_sessions": min_baseline_sessions,
    }, os.path.join(OUTPUTS, "anomaly_detector.pkl"))

    if verbose:
        print(f"    [OK] Saved to {OUTPUTS}/")

    return {
        "game_name": game_name,
        "flags_df": flags_df,
        "n_flagged": int(combined_flags.sum()),
        "flagged_ids": flagged_ids,
        "clean_session_ids": clean_session_ids,
    }


# ── Public helper — used by classifier ───────────────────────────────────────

def load_anomaly_session_sets(game_name: str) -> tuple[set, set] | None:
    """
    Read anomaly_flags.csv and return (clean_ids, scoped_ids).

    scoped_ids are all Game_Session_IDs present in the flags file. This lets
    downstream training keep newer sessions that were never evaluated by a
    previous anomaly run.

    Returns None if no flags file exists yet (anomaly detector hasn't run).
    """
    OUTPUTS = get_game_outputs(game_name)
    flags_csv = os.path.join(OUTPUTS, "anomaly_flags.csv")
    if not os.path.exists(flags_csv):
        return None
    try:
        df = pd.read_csv(flags_csv)
        if "anomaly_flag" not in df.columns or "Game_Session_ID" not in df.columns:
            return None
        scoped = set(df["Game_Session_ID"].astype(str).tolist())
        clean = set(df.loc[df["anomaly_flag"] == 0, "Game_Session_ID"].astype(str).tolist())
        return clean, scoped
    except Exception:
        return None


def load_clean_session_ids(game_name: str) -> set | None:
    """
    Read the already-computed anomaly_flags.csv for `game_name` and return the
    set of Game_Session_IDs that are clean (anomaly_flag == 0).

    Returns None if no flags file exists yet (anomaly detector hasn't run).
    """
    session_sets = load_anomaly_session_sets(game_name)
    if session_sets is None:
        return None
    clean_ids, _ = session_sets
    return clean_ids


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", default="gonogo")
    args = parser.parse_args()
    run_anomaly_detection(args.game)

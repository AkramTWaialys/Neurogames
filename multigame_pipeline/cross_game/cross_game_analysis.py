"""
Module 5 - Cross-Game Profile Analysis
========================================
After all per-game runs, this module:
  1. Loads per-game classifier predictions from PostgreSQL
  2. Computes profile consistency across games
  3. Builds accuracy comparison + consistency heatmap
  4. Persists composite profile results to PostgreSQL
"""

import os
import sys
import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns

warnings.filterwarnings("ignore")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..", "..")
sys.path.insert(0, _ROOT)

from multigame_pipeline.preprocessor import GAME_NAMES, OUTPUTS_DIR


def _cross_game_ids() -> list[str]:
    try:
        from mlops.db import list_cross_game_enabled_game_ids
        games = list_cross_game_enabled_game_ids()
        return games or list(GAME_NAMES)
    except Exception:
        return list(GAME_NAMES)


def run_cross_game_analysis(per_game_results: dict = None, verbose: bool = True) -> dict:
    """
    Cross-game analysis using per-game classifier predictions.
    
    per_game_results : dict of game_name -> classification result dict (optional)
    """
    CROSS_DIR = os.path.join(OUTPUTS_DIR, "cross_game")
    os.makedirs(CROSS_DIR, exist_ok=True)

    if verbose:
        print("\n" + "=" * 60)
        print("  CROSS-GAME ANALYSIS")
        print("=" * 60)

    # 1. Load per-game predictions from PostgreSQL
    from mlops.db import load_classification_predictions_df

    game_preds = {}
    available_games = []
    cross_games = _cross_game_ids()
    for game in cross_games:
        pred_df = load_classification_predictions_df(game)
        if len(pred_df) > 0:
            game_preds[game] = pred_df
            available_games.append(game)
            if verbose:
                print(f"    Loaded {game}: {len(game_preds[game])} participants")

    if len(available_games) < 2:
        if verbose:
            print("    Not enough games with predictions for cross-game analysis.")
        return {}

    # 2. Merge predictions across games
    merged = None
    for game in available_games:
        df = game_preds[game][["Participant_ID", "cluster", "predicted_cluster"]].copy()
        df = df.rename(columns={
            "predicted_cluster": f"pred_{game}",
        })
        if merged is None:
            merged = df[["Participant_ID", "cluster", f"pred_{game}"]]
        else:
            merged = merged.merge(df[["Participant_ID", f"pred_{game}"]], on="Participant_ID", how="inner")

    if verbose:
        print(f"\n    Merged: {len(merged)} participants across {len(available_games)} games")

    # 3. Profile consistency: how often all games agree
    pred_cols = [f"pred_{g}" for g in available_games]
    merged["all_agree"] = merged[pred_cols].nunique(axis=1) == 1
    merged["majority_pred"] = merged[pred_cols].mode(axis=1)[0]
    merged["agreement_ratio"] = merged[pred_cols].apply(
        lambda row: row.value_counts().iloc[0] / len(row), axis=1
    )

    consistency_rate = merged["all_agree"].mean()
    avg_agreement = merged["agreement_ratio"].mean()

    if verbose:
        print(f"    Full consistency (all games agree): {consistency_rate:.2%}")
        print(f"    Average agreement ratio: {avg_agreement:.2%}")

    # 4. Per-game accuracy (vs true cluster)
    game_accs = {}
    for game in available_games:
        acc = (merged[f"pred_{game}"] == merged["cluster"]).mean()
        game_accs[game] = acc
        if verbose:
            print(f"    {game:12s} accuracy: {acc:.4f}")

    # Majority vote accuracy
    majority_acc = (merged["majority_pred"] == merged["cluster"]).mean()
    game_accs["majority_vote"] = majority_acc
    if verbose:
        print(f"    {'majority_vote':12s} accuracy: {majority_acc:.4f}")

    # 5. Plot: Per-game accuracy comparison
    fig, ax = plt.subplots(figsize=(10, 5))
    names = list(game_accs.keys())
    vals = [game_accs[n] for n in names]
    colors = sns.color_palette("viridis", len(names))
    bars = ax.bar(names, vals, color=colors, edgecolor="white", linewidth=1.2)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Accuracy", fontsize=12)
    ax.set_title("ADHD Profile Classification Accuracy per Game", fontsize=14, weight="bold")
    for bar, v in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width()/2, v + 0.01, f"{v:.3f}",
                ha="center", fontsize=10, fontweight="bold")
    plt.xticks(fontsize=10)
    plt.tight_layout()
    acc_path = os.path.join(CROSS_DIR, "cross_game_accuracy.png")
    plt.savefig(acc_path, dpi=150)
    plt.close()

    # 6. Plot: Cross-game consistency heatmap
    n_games = len(available_games)
    agree_matrix = np.zeros((n_games, n_games))
    for i, g1 in enumerate(available_games):
        for j, g2 in enumerate(available_games):
            agree_matrix[i, j] = (merged[f"pred_{g1}"] == merged[f"pred_{g2}"]).mean()

    fig, ax = plt.subplots(figsize=(8, 6))
    sns.heatmap(agree_matrix, annot=True, fmt=".2f", cmap="YlGnBu",
                xticklabels=[g.upper() for g in available_games],
                yticklabels=[g.upper() for g in available_games], ax=ax)
    ax.set_title("Cross-Game Profile Agreement", fontsize=14, weight="bold")
    plt.tight_layout()
    heatmap_path = os.path.join(CROSS_DIR, "cross_game_consistency_heatmap.png")
    plt.savefig(heatmap_path, dpi=150)
    plt.close()

    # 7. Plot: Per-cluster consistency breakdown
    cluster_consistency = merged.groupby("cluster")["agreement_ratio"].mean()
    fig, ax = plt.subplots(figsize=(9, 5))
    colors_c = sns.color_palette("Set2", len(cluster_consistency))
    ax.bar(cluster_consistency.index, cluster_consistency.values, color=colors_c, edgecolor="white")
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Avg Agreement Ratio", fontsize=12)
    ax.set_title("Cross-Game Consistency by ADHD Profile", fontsize=14, weight="bold")
    for i, (idx, v) in enumerate(cluster_consistency.items()):
        ax.text(i, v + 0.01, f"{v:.2f}", ha="center", fontsize=10, fontweight="bold")
    plt.xticks(rotation=20, ha="right", fontsize=9)
    plt.tight_layout()
    cluster_path = os.path.join(CROSS_DIR, "cross_game_consistency_by_cluster.png")
    plt.savefig(cluster_path, dpi=150)
    plt.close()

    # 8. Persist composite predictions
    try:
        from mlops.db import insert_cross_game_results
        n_written = insert_cross_game_results(merged)
    except Exception as e:
        n_written = 0
        if verbose:
            print(f"    [DB-WARN] Could not persist cross-game results: {e}")

    if verbose:
        print(f"\n    [OK] Accuracy chart     -> {acc_path}")
        print(f"    [OK] Consistency heatmap -> {heatmap_path}")
        print(f"    [OK] Cluster breakdown  -> {cluster_path}")
        print(f"    [OK] PostgreSQL rows    -> {n_written}")

    # 9. Conners concordance validation (soft integration)
    concordance_summary = {}
    try:
        from multigame_pipeline.conners_concordance import run_conners_concordance
        concordance_summary = run_conners_concordance(verbose=verbose)
    except Exception as e:
        if verbose:
            print(f"    [WARN] Conners concordance skipped: {e}")

    result = {
        "consistency_rate": consistency_rate,
        "avg_agreement": avg_agreement,
        "game_accuracies": game_accs,
        "majority_vote_acc": majority_acc,
    }
    if concordance_summary:
        result["conners_concordance"] = concordance_summary

    return result


if __name__ == "__main__":
    run_cross_game_analysis()

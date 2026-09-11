"""
Module 6 — Conners CGI-10 Concordance Validation
==================================================
Post-classification check that compares the ML-predicted ADHD profile
(from cross-game analysis) against the independently collected Conners
CGI-10 questionnaire score.

The Conners score is NOT a training feature — it serves as an external
validator. This module runs after cross-game analysis and produces:
  - Per-participant concordance level (concordant / partial / discordant)
  - Weighted post-hoc confidence correction (flag + adjust)
  - Population-level concordance rate and Cohen's kappa
  - A concordance bar chart
  - Persisted results in the conners_concordance PostgreSQL table
"""

import os
import sys
import json
import warnings
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns

warnings.filterwarnings("ignore")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..")
sys.path.insert(0, _ROOT)

from multigame_pipeline.preprocessor import OUTPUTS_DIR

# ── ML profile -> severity tier mapping ────────────────────────────────────────

ML_PROFILE_TO_TIER = {
    "Optimal / Neurotypical": "typical",
    "Inattentive ADHD": "borderline",
    "Hyperactive-Impulsive ADHD": "elevated",
    "Combined ADHD": "significant",
    "Moderate Difficulty":    "borderline",
    "At-Risk / Clinical":    "elevated",
    "Severe / Disengaged":   "significant",
}


def _ml_tier(profile_name: str) -> str:
    """Map an ML profile prediction to a severity tier string."""
    return ML_PROFILE_TO_TIER.get(profile_name, "typical")


# ── Concordance classification ────────────────────────────────────────────────

def _classify_concordance(conners_tier: str, ml_tier: str) -> str:
    """
    Compare two severity tiers and return a concordance label.

    - concordant:  tiers match exactly
    - partial:     tiers are 1 step apart
    - discordant:  tiers are ≥2 steps apart
    """
    from mlops.utils.scoring import tier_distance
    d = tier_distance(conners_tier, ml_tier)
    if d == 0:
        return "concordant"
    if d == 1:
        return "partial"
    return "discordant"


# ── Weighted post-hoc confidence correction ────────────────────────────────

# Correction weights: how much the ML confidence is scaled by concordance.
# ML keeps most of the weight; Conners acts as a corrective factor.
CONCORDANCE_WEIGHTS = {
    "concordant": 1.10,   # boost: both signals agree -> more trustworthy
    "partial":    0.85,   # mild reduction: signals are close but not exact
    "discordant": 0.50,   # halved: strong disagreement -> low confidence
}


def correct_confidence(
    original_confidence: float,
    concordance: str,
    conners_tier: str,
    ml_tier: str,
) -> tuple[float, str]:
    """
    Apply weighted post-hoc correction to the ML confidence.

    Returns:
        (adjusted_confidence, correction_flag)

    The correction_flag is a human-readable string describing the
    adjustment reason, or None if no correction was needed.
    """
    weight = CONCORDANCE_WEIGHTS.get(concordance, 1.0)
    adjusted = round(min(1.0, original_confidence * weight), 4)

    if concordance == "concordant":
        flag = None  # no issue to flag
    elif concordance == "partial":
        flag = (
            f"Partial agreement: ML predicted '{ml_tier}' but Conners "
            f"indicates '{conners_tier}'. Confidence reduced by 15%."
        )
    else:  # discordant
        if ml_tier in ("elevated", "significant") and conners_tier == "typical":
            flag = (
                f"Potential false positive: ML predicted '{ml_tier}' "
                f"but Conners indicates '{conners_tier}'. "
                f"Confidence halved. Review individual sessions."
            )
        elif ml_tier == "typical" and conners_tier in ("elevated", "significant"):
            flag = (
                f"Potential false negative: ML predicted '{ml_tier}' "
                f"but Conners indicates '{conners_tier}'. "
                f"Consider clinical referral despite clean ML result."
            )
        else:
            flag = (
                f"Discordant: ML='{ml_tier}', Conners='{conners_tier}'. "
                f"Confidence halved."
            )

    return adjusted, flag


# ── Cohen's weighted kappa (quadratic) ────────────────────────────────────────

def _cohens_kappa_ordinal(tier_a: list[str], tier_b: list[str]) -> float:
    """
    Compute Cohen's kappa with quadratic weights for ordinal agreement.
    Returns kappa in [-1, 1].
    """
    from mlops.utils.scoring import CONNERS_TIERS
    k = len(CONNERS_TIERS)
    idx = {t: i for i, t in enumerate(CONNERS_TIERS)}

    # Build confusion matrix
    cm = np.zeros((k, k), dtype=float)
    for a, b in zip(tier_a, tier_b):
        i, j = idx.get(a, 0), idx.get(b, 0)
        cm[i, j] += 1.0

    n = cm.sum()
    if n == 0:
        return 0.0

    # Quadratic weights
    W = np.zeros((k, k), dtype=float)
    for i in range(k):
        for j in range(k):
            W[i, j] = ((i - j) ** 2) / ((k - 1) ** 2)

    # Expected matrix under marginal independence
    row_sums = cm.sum(axis=1)
    col_sums = cm.sum(axis=0)
    E = np.outer(row_sums, col_sums) / n

    # Kappa
    po = 1.0 - (W * cm).sum() / n
    pe = 1.0 - (W * E).sum() / n
    if pe == 0:
        return 1.0 if po == 1.0 else 0.0
    return round(float((po - pe) / (1.0 - pe)), 4)


# ── Main entry point ─────────────────────────────────────────────────────────

def run_conners_concordance(verbose: bool = True) -> dict:
    """
    Run the Conners concordance validation.

    1. Load cross-game ML predictions from PostgreSQL.
    2. Load Conners scores from user_profiles.
    3. Join on Participant_ID.
    4. Compute per-participant concordance.
    5. Compute population-level metrics.
    6. Generate concordance chart.
    7. Persist results to conners_concordance table.

    Returns a summary dict, or empty dict if insufficient data.
    """
    from mlops.db import (
        load_cross_game_results_df,
        load_conners_for_participants,
        insert_conners_concordance,
    )
    from mlops.utils.scoring import (
        normalize_to_tscore,
        get_conners_tier,
    )

    CROSS_DIR = os.path.join(OUTPUTS_DIR, "cross_game")
    os.makedirs(CROSS_DIR, exist_ok=True)

    if verbose:
        print("\n" + "-" * 60)
        print("  CONNERS CONCORDANCE VALIDATION")
        print("-" * 60)

    # 1. Load cross-game predictions
    cg_df = load_cross_game_results_df()
    if cg_df.empty:
        if verbose:
            print("    No cross-game results available — skipping.")
        return {}

    # 2. Load Conners scores
    conners_df = load_conners_for_participants()
    if conners_df.empty:
        if verbose:
            print("    No Conners scores available — skipping.")
        return {}

    # 3. Inner join
    merged = cg_df.merge(conners_df, on="Participant_ID", how="inner")
    if len(merged) == 0:
        if verbose:
            print("    No participants with both ML predictions and Conners scores.")
        return {}

    if verbose:
        print(f"    Matched {len(merged)} participants (ML + Conners)")

    # 4. Per-participant concordance
    records = []
    conners_tiers = []
    ml_tiers = []

    for _, row in merged.iterrows():
        raw_score = int(row["conners_score"])
        t_score = normalize_to_tscore(raw_score)
        c_tier = get_conners_tier(raw_score)
        m_tier = _ml_tier(str(row.get("majority_pred", "")))
        concordance = _classify_concordance(c_tier, m_tier)

        # Post-hoc confidence correction
        orig_conf = float(row.get("agreement_ratio", 0))
        adj_conf, flag = correct_confidence(orig_conf, concordance, c_tier, m_tier)

        conners_tiers.append(c_tier)
        ml_tiers.append(m_tier)

        detail = json.dumps({
            "conners_tscore": t_score,
            "conners_tier": c_tier,
            "ml_tier": m_tier,
            "majority_pred": str(row.get("majority_pred", "")),
            "agreement_ratio": float(row.get("agreement_ratio", 0)),
            "original_confidence": orig_conf,
            "adjusted_confidence": adj_conf,
            "correction_flag": flag,
        })

        records.append({
            "participant_id": row["Participant_ID"],
            "conners_score": raw_score,
            "conners_tscore": t_score,
            "conners_tier": c_tier,
            "ml_prediction": str(row.get("majority_pred", "")),
            "agreement_ratio": float(row.get("agreement_ratio", 0)),
            "concordance": concordance,
            "concordance_detail": detail,
            "original_confidence": orig_conf,
            "adjusted_confidence": adj_conf,
            "correction_flag": flag,
        })

    # 5. Population-level metrics
    n = len(records)
    n_concordant = sum(1 for r in records if r["concordance"] == "concordant")
    n_partial = sum(1 for r in records if r["concordance"] == "partial")
    n_discordant = sum(1 for r in records if r["concordance"] == "discordant")

    concordance_rate = round((n_concordant + n_partial) / max(n, 1), 4)
    exact_rate = round(n_concordant / max(n, 1), 4)
    kappa = _cohens_kappa_ordinal(conners_tiers, ml_tiers)

    if verbose:
        print(f"    Concordant:  {n_concordant} ({n_concordant/max(n,1):.1%})")
        print(f"    Partial:     {n_partial} ({n_partial/max(n,1):.1%})")
        print(f"    Discordant:  {n_discordant} ({n_discordant/max(n,1):.1%})")
        print(f"    Concordance rate (exact + partial): {concordance_rate:.2%}")
        print(f"    Cohen's kappa (ordinal):            {kappa:.4f}")

    # 6. Chart — concordance distribution
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # 6a. Bar chart: concordance breakdown
    labels = ["Concordant", "Partial", "Discordant"]
    values = [n_concordant, n_partial, n_discordant]
    colors = ["#06d6a0", "#ffd166", "#ef476f"]
    ax = axes[0]
    bars = ax.bar(labels, values, color=colors, edgecolor="white", linewidth=1.5)
    for bar, v in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 0.3,
                str(v), ha="center", fontsize=12, fontweight="bold")
    ax.set_ylabel("Participants", fontsize=11)
    ax.set_title("Conners–ML Concordance Distribution", fontsize=13, weight="bold")
    ax.set_ylim(0, max(values) * 1.25 if max(values) > 0 else 1)

    # 6b. Heatmap: Conners tier vs ML tier
    from mlops.utils.scoring import CONNERS_TIERS
    tier_labels = list(CONNERS_TIERS)
    matrix = np.zeros((len(tier_labels), len(tier_labels)), dtype=int)
    for ct, mt in zip(conners_tiers, ml_tiers):
        i = tier_labels.index(ct) if ct in tier_labels else 0
        j = tier_labels.index(mt) if mt in tier_labels else 0
        matrix[i, j] += 1

    ax2 = axes[1]
    display_labels = [t.capitalize() for t in tier_labels]
    sns.heatmap(matrix, annot=True, fmt="d", cmap="YlOrRd",
                xticklabels=display_labels, yticklabels=display_labels, ax=ax2)
    ax2.set_xlabel("ML Tier", fontsize=11)
    ax2.set_ylabel("Conners Tier", fontsize=11)
    ax2.set_title(f"Tier Agreement (κ = {kappa:.3f})", fontsize=13, weight="bold")

    plt.tight_layout()
    chart_path = os.path.join(CROSS_DIR, "conners_concordance.png")
    plt.savefig(chart_path, dpi=150)
    plt.close()

    if verbose:
        print(f"    [OK] Chart -> {chart_path}")

    # 7. Persist to PostgreSQL
    try:
        n_written = insert_conners_concordance(records)
        if verbose:
            print(f"    [OK] PostgreSQL rows -> {n_written}")
    except Exception as e:
        n_written = 0
        if verbose:
            print(f"    [DB-WARN] Could not persist concordance: {e}")

    summary = {
        "n_matched": n,
        "n_concordant": n_concordant,
        "n_partial": n_partial,
        "n_discordant": n_discordant,
        "concordance_rate": concordance_rate,
        "exact_match_rate": exact_rate,
        "cohens_kappa": kappa,
        "chart_path": chart_path,
    }
    return summary


if __name__ == "__main__":
    run_conners_concordance()

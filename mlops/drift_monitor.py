"""
MLOps Data Drift Monitor (Evidently AI)
=========================================
Detects statistical drift between baseline (training) data
and current (tester) data for each game.

Data is loaded exclusively from the centralised PostgreSQL database.

Usage:
    from mlops.drift_monitor import run_drift_report

    report = run_drift_report("gonogo", baseline_df, current_df)
    print(report["drift_detected"], report["drift_share"])
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from evidently.report import Report
    from evidently.metric_preset import DataDriftPreset
    HAS_EVIDENTLY = True
except ImportError:
    HAS_EVIDENTLY = False

import pandas as pd
import numpy as np

from mlops.config import GAME_NAMES, DRIFT_CONFIDENCE, get_drift_output_dir
from mlops.db import list_ml_enabled_game_ids, load_game_df


def _drift_game_ids() -> list[str]:
    """Return ML-enabled registered games, falling back to the built-ins."""
    try:
        games = list_ml_enabled_game_ids()
        return games or list(GAME_NAMES)
    except Exception:
        return list(GAME_NAMES)


def run_drift_report(game_name: str, baseline_df: pd.DataFrame,
                     current_df: pd.DataFrame,
                     numeric_features: list = None) -> dict:
    """
    Compute data drift between baseline and current data.

    Args:
        game_name:   name of the game
        baseline_df: the original training data (sessions with training_runs)
        current_df:  the new tester data (all sessions)
        numeric_features: list of numeric columns to check (auto-detected if None)

    Returns:
        dict with:
            drift_detected (bool): True if overall drift is detected
            drift_share (float): fraction of features that drifted
            n_drifted (int): number of drifted features
            n_total (int): total features checked
            per_feature (list): per-feature drift details
            report_path (str): path to saved HTML report
    """
    if not HAS_EVIDENTLY:
        return _fallback_drift(game_name, baseline_df, current_df, numeric_features)

    output_dir = get_drift_output_dir(game_name)

    # Auto-detect numeric features if not provided
    if numeric_features is None:
        numeric_features = _get_common_numeric_cols(baseline_df, current_df)

    # Subset to common columns only
    common_cols = [c for c in numeric_features if c in baseline_df.columns and c in current_df.columns]

    if not common_cols:
        return {
            "drift_detected": False,
            "drift_share": 0.0,
            "n_drifted": 0,
            "n_total": 0,
            "per_feature": [],
            "report_path": None,
            "error": "No common numeric columns found",
        }

    ref = baseline_df[common_cols].copy()
    cur = current_df[common_cols].copy()

    # Run Evidently report
    report = Report(metrics=[DataDriftPreset()])
    report.run(reference_data=ref, current_data=cur)

    # Save HTML report
    report_path = os.path.join(output_dir, "drift_report.html")
    report.save_html(report_path)

    # Extract results from the report
    report_json = report.as_dict()

    # Parse drift results
    drift_result = _parse_evidently_result(report_json)
    drift_result["report_path"] = report_path
    drift_result["game"] = game_name

    return drift_result


def _parse_evidently_result(report_json: dict) -> dict:
    """Parse Evidently report JSON into a clean result dict."""
    result = {
        "drift_detected": False,
        "drift_share": 0.0,
        "n_drifted": 0,
        "n_total": 0,
        "per_feature": [],
    }

    try:
        metrics = report_json.get("metrics", [])
        for metric in metrics:
            metric_result = metric.get("result", {})

            if "drift_share" in metric_result:
                result["drift_share"] = metric_result["drift_share"]
                result["drift_detected"] = metric_result.get("dataset_drift", False)
                result["n_drifted"] = metric_result.get("number_of_drifted_columns", 0)
                result["n_total"] = metric_result.get("number_of_columns", 0)

            if "drift_by_columns" in metric_result:
                for col_name, col_data in metric_result["drift_by_columns"].items():
                    result["per_feature"].append({
                        "feature": col_name,
                        "drifted": col_data.get("drift_detected", False),
                        "statistic": col_data.get("stattest_name", ""),
                        "p_value": col_data.get("drift_score", 1.0),
                    })
    except Exception:
        pass

    return result


def _fallback_drift(game_name: str, baseline_df: pd.DataFrame,
                    current_df: pd.DataFrame,
                    numeric_features: list = None) -> dict:
    """
    Fallback drift detection using KS-test without Evidently.
    Uses scipy.stats.ks_2samp for each numeric column.
    """
    from scipy import stats

    output_dir = get_drift_output_dir(game_name)

    if numeric_features is None:
        numeric_features = _get_common_numeric_cols(baseline_df, current_df)

    common_cols = [c for c in numeric_features if c in baseline_df.columns and c in current_df.columns]

    per_feature = []
    n_drifted = 0

    for col in common_cols:
        ref_vals = baseline_df[col].dropna().values
        cur_vals = current_df[col].dropna().values

        if len(ref_vals) < 5 or len(cur_vals) < 5:
            continue

        stat, p_value = stats.ks_2samp(ref_vals, cur_vals)
        drifted = p_value < (1 - DRIFT_CONFIDENCE)
        if drifted:
            n_drifted += 1

        per_feature.append({
            "feature": col,
            "drifted": drifted,
            "statistic": "ks_2samp",
            "p_value": float(p_value),
        })

    n_total = len(per_feature)
    drift_share = n_drifted / max(1, n_total)

    return {
        "drift_detected": drift_share > 0.5,
        "drift_share": drift_share,
        "n_drifted": n_drifted,
        "n_total": n_total,
        "per_feature": per_feature,
        "report_path": None,
        "game": game_name,
        "method": "ks_2samp (fallback — install evidently for full reports)",
    }


def _get_common_numeric_cols(df1: pd.DataFrame, df2: pd.DataFrame) -> list:
    """Auto-detect common numeric columns between two DataFrames."""
    skip = {"Participant_ID", "Game_Session_ID", "cluster", "_source_file"}
    numeric1 = set(df1.select_dtypes(include=[np.number]).columns) - skip
    numeric2 = set(df2.select_dtypes(include=[np.number]).columns) - skip
    return sorted(numeric1 & numeric2)


def _load_drift_windows(game: str):
    """Return (baseline_df, current_df) with correct window separation.

    baseline = sessions consumed by the last training run
    current  = untrained sessions only (new data since last training run)

    This prevents diluting drift by comparing baseline against a superset
    that contains itself.
    """
    baseline_df = load_game_df(game, trained_only=True)
    all_df = load_game_df(game, trained_only=False)

    if baseline_df.empty or all_df.empty:
        return baseline_df, pd.DataFrame()

    trained_ids = set(baseline_df["Game_Session_ID"])
    current_df = all_df[~all_df["Game_Session_ID"].isin(trained_ids)]
    return baseline_df, current_df


def check_all_games() -> list:
    """
    Check drift for all games using PostgreSQL as the data source.

    Data source: ``mlops.db.sessions`` table (the ingestion queue).
    - Baseline = sessions recorded in ``training_runs`` as completed
    - Current  = untrained sessions only (new data since last run)

    Returns a list of drift result dicts.
    """
    results = []
    for game in _drift_game_ids():
        baseline_df, current_df = _load_drift_windows(game)

        if baseline_df.empty:
            results.append({
                "game": game,
                "drift_detected": False,
                "drift_share": 0.0,
                "n_drifted": 0,
                "n_total": 0,
                "per_feature": [],
                "report_path": None,
                "status": "no_baseline_data",
            })
            continue

        if current_df.empty:
            results.append({
                "game": game,
                "drift_detected": False,
                "drift_share": 0.0,
                "n_drifted": 0,
                "n_total": 0,
                "per_feature": [],
                "report_path": None,
                "status": "no_current_data",
            })
            continue

        result = run_drift_report(game, baseline_df, current_df)
        results.append(result)

    return results



# ── Advanced Drift Detection ──────────────────────────────────────────────────


def run_prediction_drift(
    game: str,
    baseline_probas: "np.ndarray",
    current_probas: "np.ndarray",
    confidence: float = DRIFT_CONFIDENCE,
    min_samples: int = 30,
) -> dict:
    """
    Detect prediction drift via KS-test on per-class probability distributions.

    Args:
        game:             Game name.
        baseline_probas:  (n_baseline, n_classes) from inference.predict()
        current_probas:   (n_current,  n_classes) from inference.predict()
        confidence:       Statistical confidence level (default from config).
        min_samples:      Minimum samples required in each window.

    Returns:
        Dict with keys: drift_type, drift_detected, per_class, game.
    """
    import numpy as np
    from scipy import stats

    if (
        baseline_probas is None
        or current_probas is None
        or baseline_probas.shape[0] < min_samples
        or current_probas.shape[0] < min_samples
    ):
        return {
            "drift_type": "prediction",
            "drift_detected": False,
            "reason": "insufficient_samples",
            "min_required": min_samples,
            "game": game,
        }

    per_class = []
    n_drifted = 0
    alpha = 1.0 - confidence
    n_classes = baseline_probas.shape[1]

    for c in range(n_classes):
        stat, p = stats.ks_2samp(baseline_probas[:, c], current_probas[:, c])
        drifted = p < alpha
        if drifted:
            n_drifted += 1
        per_class.append({
            "class_idx": c,
            "drifted": drifted,
            "p_value": float(p),
            "ks_stat": float(stat),
        })

    return {
        "drift_type": "prediction",
        "drift_detected": n_drifted > 0,
        "n_drifted_classes": n_drifted,
        "n_total_classes": n_classes,
        "per_class": per_class,
        "game": game,
    }


def run_label_distribution_drift(
    game: str,
    baseline_labels: "pd.Series",
    current_labels: "pd.Series",
    confidence: float = DRIFT_CONFIDENCE,
    min_samples: int = 30,
) -> dict:
    """
    Detect label distribution drift via chi-squared contingency test.

    Args:
        game:             Game name.
        baseline_labels:  Series of predicted/actual cluster labels (baseline).
        current_labels:   Series of predicted/actual cluster labels (current).
        confidence:       Statistical confidence level.
        min_samples:      Minimum samples required in each window.

    Returns:
        Dict with keys: drift_type, drift_detected, chi2_stat, p_value, game.
    """
    import numpy as np
    from scipy import stats

    if (
        baseline_labels is None
        or current_labels is None
        or len(baseline_labels) < min_samples
        or len(current_labels) < min_samples
    ):
        return {
            "drift_type": "label_dist",
            "drift_detected": False,
            "reason": "insufficient_samples",
            "min_required": min_samples,
            "game": game,
        }

    all_labels = sorted(set(baseline_labels) | set(current_labels))
    baseline_counts = (
        pd.Series(baseline_labels).value_counts().reindex(all_labels, fill_value=0)
    )
    current_counts = (
        pd.Series(current_labels).value_counts().reindex(all_labels, fill_value=0)
    )

    contingency = np.array([baseline_counts.values, current_counts.values])
    try:
        chi2, p_value, dof, _ = stats.chi2_contingency(contingency)
    except ValueError as e:
        return {
            "drift_type": "label_dist",
            "drift_detected": False,
            "reason": f"chi2_failed: {e}",
            "game": game,
        }

    alpha = 1.0 - confidence
    return {
        "drift_type": "label_dist",
        "drift_detected": bool(p_value < alpha),
        "chi2_stat": float(chi2),
        "p_value": float(p_value),
        "dof": int(dof),
        "baseline_dist": baseline_counts.to_dict(),
        "current_dist": current_counts.to_dict(),
        "game": game,
    }


def run_concept_drift(
    game: str,
    baseline_preds: "np.ndarray",
    baseline_true: "np.ndarray",
    current_preds: "np.ndarray",
    current_true: "np.ndarray",
    min_samples: int = 50,
    acc_drop_threshold: float = 0.05,
    confidence: float = DRIFT_CONFIDENCE,
) -> dict:
    """
    Detect concept drift by comparing model accuracy across windows.

    Uses a two-gate approach:
      1. Accuracy drop must exceed ``acc_drop_threshold`` (practical significance).
      2. Proportions z-test must be statistically significant.

    Both gates must pass to flag concept drift — avoiding spurious alerts on noise.

    Args:
        game:               Game name.
        baseline_preds:     Model predictions on baseline participants.
        baseline_true:      Ground-truth labels for baseline participants.
        current_preds:      Model predictions on current (untrained) participants.
        current_true:       Ground-truth labels for current participants.
        min_samples:        Minimum participants required per window.
        acc_drop_threshold: Minimum accuracy drop (absolute) before running z-test.
        confidence:         Statistical confidence level.

    Returns:
        Dict with: drift_type, drift_detected, baseline_acc, current_acc, p_value.
    """
    import numpy as np

    base_result = {
        "drift_type": "concept",
        "game": game,
    }

    if (
        baseline_preds is None
        or baseline_true is None
        or current_preds is None
        or current_true is None
        or len(baseline_preds) < min_samples
        or len(current_preds) < min_samples
    ):
        return {
            **base_result,
            "drift_detected": False,
            "reason": "insufficient_samples",
            "min_required": min_samples,
        }

    baseline_acc = float((np.asarray(baseline_preds) == np.asarray(baseline_true)).mean())
    current_acc = float((np.asarray(current_preds) == np.asarray(current_true)).mean())
    acc_drop = baseline_acc - current_acc

    # Gate 1: practical significance
    if acc_drop < acc_drop_threshold:
        return {
            **base_result,
            "drift_detected": False,
            "reason": "drop_below_threshold",
            "baseline_acc": baseline_acc,
            "current_acc": current_acc,
            "acc_drop": float(acc_drop),
            "threshold": acc_drop_threshold,
        }

    # Gate 2: statistical significance (proportions z-test)
    try:
        from statsmodels.stats.proportion import proportions_ztest

        n_correct = np.array([
            (np.asarray(baseline_preds) == np.asarray(baseline_true)).sum(),
            (np.asarray(current_preds) == np.asarray(current_true)).sum(),
        ])
        n_total = np.array([len(baseline_preds), len(current_preds)])
        z_stat, p_value = proportions_ztest(n_correct, n_total, alternative="larger")
        alpha = 1.0 - confidence
        drift_detected = bool(p_value < alpha)
    except ImportError:
        # statsmodels not installed — use acc_drop alone as the signal
        z_stat, p_value = float("nan"), float("nan")
        drift_detected = True  # Gate 1 already passed, treat as drift

    return {
        **base_result,
        "drift_detected": drift_detected,
        "baseline_acc": baseline_acc,
        "current_acc": current_acc,
        "acc_drop": float(acc_drop),
        "z_stat": float(z_stat) if not isinstance(z_stat, float) or not (z_stat != z_stat) else None,
        "p_value": float(p_value) if not isinstance(p_value, float) or not (p_value != p_value) else None,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MLOps Drift Monitor")
    parser.add_argument("--game", default="all")
    args = parser.parse_args()

    if args.game == "all":
        results = check_all_games()
    else:
        baseline = load_game_df(args.game, trained_only=True)
        current = load_game_df(args.game, trained_only=False)
        if not baseline.empty and not current.empty:
            results = [run_drift_report(args.game, baseline, current)]
        else:
            print(f"  Not enough data for {args.game}")
            results = []

    for r in results:
        status = "DRIFT" if r.get("drift_detected") else "STABLE"
        print(f"  {r.get('game', '?'):12s} | {status} | drifted={r.get('n_drifted', 0)}/{r.get('n_total', 0)}")
        if r.get("report_path"):
            print(f"    Report: {r['report_path']}")

"""
Module 1 - Per-Game ADHD Profile Classification (Supervised Learning)
=====================================================================
Selects a supervised classifier family by train-only cross-validation
to predict ADHD profiles from aggregated per-participant session features.
Runs independently for each game.

Includes:
  - Pre-training data quality gates
  - Baseline comparisons (majority-class, stratified-random, logistic regression)
  - Post-hoc model calibration (isotonic regression)
"""

import os
import sys
import warnings
import joblib
import textwrap
import shutil
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
import seaborn as sns

from sklearn.base import clone
from sklearn.ensemble import (
    ExtraTreesClassifier,
    GradientBoostingClassifier,
    RandomForestClassifier,
    VotingClassifier,
)
from sklearn.neural_network import MLPClassifier
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.svm import SVC
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.model_selection import StratifiedKFold, cross_validate, train_test_split
from sklearn.metrics import (
    classification_report, confusion_matrix, accuracy_score,
    ConfusionMatrixDisplay, f1_score, brier_score_loss
)
from sklearn.pipeline import Pipeline

warnings.filterwarnings("ignore")

try:
    from xgboost import XGBClassifier
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..", "..")
sys.path.insert(0, _ROOT)

from multigame_pipeline.preprocessor import (
    load_raw,
    encode_dataframe,
    get_game_outputs,
    CLUSTER_LABELS,
)
from multigame_pipeline.feature_engineer import build_participant_aggregate, get_aggregate_feature_cols


CANDIDATE_MODEL_NAMES = [
    "Logistic Regression",
    "Extra Trees",
    "SVC RBF",
    "Gradient Boosting",
    "Soft Voting Ensemble",
]


def _build_rf():
    return Pipeline([
        ("scaler", StandardScaler()),
        ("clf", RandomForestClassifier(
            n_estimators=300, max_depth=12,
            min_samples_leaf=2, random_state=42, n_jobs=-1
        ))
    ])


def _build_xgb(n_classes: int):
    if not HAS_XGB:
        return None
    return Pipeline([
        ("scaler", StandardScaler()),
        ("clf", XGBClassifier(
            n_estimators=300, max_depth=6, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, use_label_encoder=False,
            eval_metric="mlogloss", num_class=n_classes,
            random_state=42, verbosity=0
        ))
    ])


def _build_mlp():
    return Pipeline([
        ("scaler", StandardScaler()),
        ("clf", MLPClassifier(
            hidden_layer_sizes=(256, 128, 64), activation="relu",
            max_iter=500, random_state=42, early_stopping=True,
            validation_fraction=0.1
        ))
    ])


def _build_logreg():
    return Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(
            max_iter=3000, C=0.3, class_weight="balanced",
            random_state=42, n_jobs=-1
        ))
    ])


def _build_extra_trees():
    return ExtraTreesClassifier(
        n_estimators=500, max_features="sqrt", min_samples_leaf=2,
        class_weight="balanced", random_state=42, n_jobs=-1
    )


def _build_svc():
    return Pipeline([
        ("scaler", StandardScaler()),
        ("clf", SVC(
            C=1.0, kernel="rbf", gamma="scale", class_weight="balanced",
            probability=True, random_state=42
        ))
    ])


def _build_gradient_boosting():
    return GradientBoostingClassifier(random_state=42)


def _build_ensemble(n_classes: int):
    estimators = [("rf", _build_rf()), ("mlp", _build_mlp())]
    if HAS_XGB:
        estimators.append(("xgb", _build_xgb(n_classes)))
    return VotingClassifier(estimators=estimators, voting="soft")


def _build_candidate_models(n_classes: int) -> dict:
    """Model families considered for the production classifier."""
    builders = {
        "Logistic Regression": _build_logreg,
        "Extra Trees": _build_extra_trees,
        "SVC RBF": _build_svc,
        "Gradient Boosting": _build_gradient_boosting,
        "Soft Voting Ensemble": lambda: _build_ensemble(n_classes),
    }
    return {name: builders[name]() for name in CANDIDATE_MODEL_NAMES}


def _select_model_by_cv(candidate_cv: dict, candidate_cv_std: dict) -> str:
    """
    Select a model using a one-standard-deviation rule.

    If a simpler model is within one CV standard deviation of the top mean,
    prefer it. This reduces variance on small participant-level datasets.
    """
    best_name = max(candidate_cv, key=candidate_cv.get)
    threshold = candidate_cv[best_name] - candidate_cv_std.get(best_name, 0.0)
    preference = [
        "Logistic Regression",
        "SVC RBF",
        "Extra Trees",
        "Gradient Boosting",
        "Soft Voting Ensemble",
    ]
    for name in preference:
        if name in candidate_cv and candidate_cv[name] >= threshold:
            return name
    return best_name


def _filter_labeled_training_rows(df: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """Keep only rows with supervised training labels."""
    if "cluster" not in df.columns:
        return df

    labeled = df[df["cluster"].isin(CLUSTER_LABELS)].copy()
    dropped = len(df) - len(labeled)
    if verbose and dropped:
        print(f"    [DQ] Dropped {dropped} unlabeled rows before supervised training")
    return labeled


def _filter_anomalous_sessions(df: pd.DataFrame, game_name: str,
                               verbose: bool = True) -> pd.DataFrame:
    """
    Remove sessions that the anomaly detector has flagged.

    Reads the pre-computed anomaly_flags.csv for this game.  If the file does
    not yet exist (anomaly step hasn't run) all rows are kept and a warning is
    printed so the user knows training didn't benefit from anomaly filtering.

    Only *hard-flagged* sessions (anomaly_flag == 1) are removed.  The player
    is NOT dropped entirely — only their individual bad sessions are excluded,
    so their remaining clean sessions still contribute to training.
    """
    try:
        from multigame_pipeline.anomaly.anomaly_detector import load_anomaly_session_sets
        session_sets = load_anomaly_session_sets(game_name)
    except ImportError:
        session_sets = None

    if session_sets is None:
        if verbose:
            print("    [Anomaly] No flags file found — training on all sessions (run anomaly detection first)")
        return df

    if "Game_Session_ID" not in df.columns:
        if verbose:
            print("    [Anomaly] Game_Session_ID not in dataframe — skipping anomaly filter")
        return df

    before = len(df)
    clean_ids, scoped_ids = session_sets
    session_ids = df["Game_Session_ID"].astype(str)
    scoped_mask = session_ids.isin(scoped_ids)

    if not scoped_mask.any():
        if verbose:
            print("    [Anomaly] Flags file has no overlap with current data - skipping anomaly filter")
        return df

    # Keep sessions not covered by the latest anomaly run, and covered sessions
    # that were explicitly marked clean.
    df_clean = df[(~scoped_mask) | session_ids.isin(clean_ids)].copy()
    removed = before - len(df_clean)
    if verbose:
        pct = removed / before * 100 if before else 0
        print(f"    [Anomaly] Removed {removed} flagged sessions ({pct:.1f}%) — "
              f"{len(df_clean)} clean sessions remain for training")
    return df_clean


# ── Phase 2: Baseline Comparisons ────────────────────────────────────────────

def _run_baselines(X_train, y_train, X_test, y_test, cv, verbose=True) -> dict:
    """
    Train three baselines and return their test accuracies and F1 scores.
    These prove (or disprove) that the selected ML model adds real value.
    """
    baselines = {
        "Baseline: Majority Class": DummyClassifier(strategy="most_frequent"),
        "Baseline: Stratified Random": DummyClassifier(strategy="stratified", random_state=42),
        "Baseline: Logistic Regression": Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=1000, random_state=42, n_jobs=-1))
        ]),
    }

    results = {}
    f1_scores = {}

    for name, model in baselines.items():
        model.fit(X_train, y_train)
        y_pred = model.predict(X_test)
        acc = accuracy_score(y_test, y_pred)
        f1 = f1_score(y_test, y_pred, average="macro", zero_division=0)
        results[name] = acc
        f1_scores[name] = f1
        if verbose:
            print(f"      {name:40s}  acc={acc:.4f}  f1={f1:.4f}")

    return results, f1_scores


# ── Phase 3: Calibration Helpers ─────────────────────────────────────────────

def _compute_ece(y_true, y_prob, n_bins=10) -> float:
    """Expected Calibration Error — mean absolute gap across bins."""
    bin_boundaries = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    for lo, hi in zip(bin_boundaries[:-1], bin_boundaries[1:]):
        mask = (y_prob >= lo) & (y_prob < hi)
        if mask.sum() == 0:
            continue
        avg_confidence = y_prob[mask].mean()
        avg_accuracy = y_true[mask].mean()
        ece += mask.sum() * abs(avg_accuracy - avg_confidence)
    return float(ece / len(y_true)) if len(y_true) > 0 else 0.0


def _unwrap_pipeline(model):
    """Return the final estimator inside a sklearn Pipeline, if present."""
    if isinstance(model, Pipeline):
        return model.steps[-1][1]
    return model


def _get_feature_importance(model, feature_cols: list[str]):
    """
    Return model importances aligned with feature_cols when the estimator
    exposes a native importance signal. Falls back to coefficients.
    """
    estimator = _unwrap_pipeline(model)

    if isinstance(estimator, VotingClassifier):
        for fitted in getattr(estimator, "estimators_", []):
            values = _get_feature_importance(fitted, feature_cols)
            if values is not None:
                return values
        return None

    if hasattr(estimator, "feature_importances_"):
        values = np.asarray(estimator.feature_importances_, dtype=float)
    elif hasattr(estimator, "coef_"):
        values = np.asarray(estimator.coef_, dtype=float)
        values = np.mean(np.abs(values), axis=0) if values.ndim > 1 else np.abs(values)
    else:
        return None

    if len(values) != len(feature_cols):
        return None
    return values


def _save_calibration_curve(y_test, y_prob, class_names, outputs_dir, game_name):
    """Generate and save reliability diagram (calibration curve)."""
    n_classes = len(class_names)
    fig, ax = plt.subplots(figsize=(8, 6))

    # Plot calibration curve for each class (one-vs-rest)
    colors = sns.color_palette("viridis", n_classes)
    for i, (cls_name, color) in enumerate(zip(class_names, colors)):
        y_binary = (y_test == i).astype(int)
        if y_binary.sum() == 0:
            continue
        prob_true, prob_pred = calibration_curve(
            y_binary, y_prob[:, i], n_bins=8, strategy="uniform"
        )
        ax.plot(prob_pred, prob_true, marker="o", label=cls_name, color=color, linewidth=2)

    ax.plot([0, 1], [0, 1], "k--", alpha=0.5, label="Perfectly calibrated")
    ax.set_xlabel("Mean Predicted Probability", fontsize=11)
    ax.set_ylabel("Fraction of Positives", fontsize=11)
    ax.set_title(f"{game_name.upper()} — Calibration Curve", fontsize=13, weight="bold")
    ax.legend(loc="lower right", fontsize=9)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    plt.tight_layout()

    path = os.path.join(outputs_dir, "calibration_curve.png")
    plt.savefig(path, dpi=150)
    plt.close()
    return path


def _save_model_comparison_artifacts(
    game_name: str,
    candidate_metrics: dict,
    baseline_metrics: dict,
    selected_model_name: str,
    selected_test_acc: float,
    selected_f1: float,
    outputs_dir: str,
) -> tuple[str, str, str]:
    """Save a metrics table and image comparing candidate families honestly."""
    rows = []
    for model_name, metrics in candidate_metrics.items():
        rows.append({
            "model": model_name,
            "role": "candidate",
            "selected": model_name == selected_model_name,
            "cv_accuracy_mean": metrics.get("cv_accuracy_mean"),
            "cv_accuracy_std": metrics.get("cv_accuracy_std"),
            "cv_f1_macro_mean": metrics.get("cv_f1_macro_mean"),
            "cv_f1_macro_std": metrics.get("cv_f1_macro_std"),
            "holdout_accuracy": selected_test_acc if model_name == selected_model_name else None,
            "holdout_f1_macro": selected_f1 if model_name == selected_model_name else None,
        })

    for model_name, metrics in baseline_metrics.items():
        rows.append({
            "model": model_name.replace("Baseline: ", ""),
            "role": "baseline",
            "selected": False,
            "cv_accuracy_mean": None,
            "cv_accuracy_std": None,
            "cv_f1_macro_mean": None,
            "cv_f1_macro_std": None,
            "holdout_accuracy": metrics.get("holdout_accuracy"),
            "holdout_f1_macro": metrics.get("holdout_f1_macro"),
        })

    comparison_df = pd.DataFrame(rows)
    csv_path = os.path.join(outputs_dir, "classification_model_comparison.csv")
    comparison_df.to_csv(csv_path, index=False)
    png_path = os.path.join(outputs_dir, "classification_model_comparison.png")
    svg_path = os.path.join(outputs_dir, "classification_model_comparison.svg")

    if not candidate_metrics or comparison_df.empty:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.text(0.5, 0.5, "No model metrics available", ha="center", va="center")
        ax.set_axis_off()
        fig.suptitle(f"{game_name.upper()} classification model comparison", fontsize=13, weight="bold")
        fig.tight_layout()
        fig.savefig(png_path, dpi=180)
        fig.savefig(svg_path)
        plt.close(fig)
        return png_path, csv_path, svg_path

    candidates = pd.DataFrame([
        {
            "model": name,
            "cv_accuracy_mean": metrics.get("cv_accuracy_mean", 0.0),
            "cv_accuracy_std": metrics.get("cv_accuracy_std", 0.0),
            "cv_f1_macro_mean": metrics.get("cv_f1_macro_mean", 0.0),
            "cv_f1_macro_std": metrics.get("cv_f1_macro_std", 0.0),
            "selected": name == selected_model_name,
        }
        for name, metrics in candidate_metrics.items()
    ]).sort_values("cv_accuracy_mean", ascending=True)

    holdout = comparison_df[comparison_df["holdout_accuracy"].notna()].copy()
    holdout = holdout.sort_values("holdout_accuracy", ascending=True)

    fig, axes = plt.subplots(1, 2, figsize=(15, max(5.5, len(candidates) * 0.55)))
    y_pos = np.arange(len(candidates))
    colors = ["#f59e0b" if selected else "#2563eb" for selected in candidates["selected"]]
    axes[0].barh(
        y_pos - 0.18,
        candidates["cv_accuracy_mean"],
        xerr=candidates["cv_accuracy_std"],
        height=0.34,
        color=colors,
        alpha=0.9,
        label="CV accuracy",
    )
    axes[0].barh(
        y_pos + 0.18,
        candidates["cv_f1_macro_mean"],
        xerr=candidates["cv_f1_macro_std"],
        height=0.34,
        color="#10b981",
        alpha=0.8,
        label="CV macro F1",
    )
    axes[0].set_yticks(y_pos)
    axes[0].set_yticklabels(candidates["model"])
    axes[0].set_xlim(0, 1.05)
    axes[0].set_xlabel("Train-only cross-validation score")
    axes[0].set_title("Candidate model families")
    axes[0].legend(loc="lower right")
    axes[0].grid(axis="x", alpha=0.2)
    for y, row in zip(y_pos, candidates.itertuples(index=False)):
        axes[0].text(
            min(row.cv_accuracy_mean + 0.025, 1.02),
            y - 0.18,
            f"{row.cv_accuracy_mean:.3f} +/- {row.cv_accuracy_std:.3f}",
            va="center",
            fontsize=8.2,
            color="#334155",
        )
        axes[0].text(
            min(row.cv_f1_macro_mean + 0.025, 1.02),
            y + 0.18,
            f"{row.cv_f1_macro_mean:.3f} +/- {row.cv_f1_macro_std:.3f}",
            va="center",
            fontsize=8.2,
            color="#334155",
        )

    holdout_y = np.arange(len(holdout))
    holdout_colors = [
        "#f59e0b" if row["selected"] else "#64748b"
        for _, row in holdout.iterrows()
    ]
    if holdout.empty:
        axes[1].text(0.5, 0.5, "No holdout metrics available", ha="center", va="center")
    else:
        axes[1].barh(
            holdout_y - 0.18,
            holdout["holdout_accuracy"],
            height=0.34,
            color=holdout_colors,
            alpha=0.9,
            label="Holdout accuracy",
        )
        axes[1].barh(
            holdout_y + 0.18,
            holdout["holdout_f1_macro"],
            height=0.34,
            color="#10b981",
            alpha=0.8,
            label="Holdout macro F1",
        )
        axes[1].set_yticks(holdout_y)
        axes[1].set_yticklabels(holdout["model"])
        axes[1].legend(loc="lower right")
        for y, row in zip(holdout_y, holdout.itertuples(index=False)):
            axes[1].text(
                min(float(row.holdout_accuracy) + 0.025, 1.02),
                y - 0.18,
                f"{float(row.holdout_accuracy):.3f}",
                va="center",
                fontsize=8.3,
                color="#334155",
            )
            axes[1].text(
                min(float(row.holdout_f1_macro) + 0.025, 1.02),
                y + 0.18,
                f"{float(row.holdout_f1_macro):.3f}",
                va="center",
                fontsize=8.3,
                color="#334155",
            )
    axes[1].set_xlim(0, 1.05)
    axes[1].set_xlabel("Holdout test score")
    axes[1].set_title("Selected model vs baselines")
    axes[1].grid(axis="x", alpha=0.2)

    fig.suptitle(
        f"{game_name.upper()} classification model comparison\n"
        f"Selected: {selected_model_name}",
        fontsize=14,
        weight="bold",
    )
    fig.tight_layout(rect=[0, 0, 1, 0.92])
    fig.savefig(png_path, dpi=180)
    fig.savefig(svg_path)
    plt.close(fig)
    return png_path, csv_path, svg_path


def _publish_presentation_artifact(game_name: str, source_png: str) -> str:
    """Publish the Go/No-Go real-run comparison image used by the deck."""
    if game_name != "gonogo" or not source_png or not os.path.exists(source_png):
        return ""

    target_dir = os.path.join(_ROOT, "report_assets", "figures")
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, "model-evaluation-artifact.png")
    shutil.copyfile(source_png, target)
    return target


def _separability_diagnostic(candidate_metrics: dict, selected_test_acc: float) -> dict:
    """Flag perfect metrics without changing them."""
    perfect_cv_models = [
        name
        for name, metrics in candidate_metrics.items()
        if float(metrics.get("cv_accuracy_mean") or 0.0) >= 0.9999
    ]
    perfect_holdout = float(selected_test_acc or 0.0) >= 0.9999
    suspicious = bool(perfect_cv_models or perfect_holdout)
    message = ""
    if suspicious:
        reasons = []
        if perfect_cv_models:
            reasons.append("perfect cross-validation accuracy")
        if perfect_holdout:
            reasons.append("perfect holdout accuracy")
        message = (
            "Suspicious separability detected: "
            + " and ".join(reasons)
            + ". Check synthetic overlap and leakage before using this run in a presentation."
        )
    return {
        "suspicious": suspicious,
        "perfect_cv_models": perfect_cv_models,
        "perfect_holdout_accuracy": perfect_holdout,
        "message": message,
    }


def _add_flow_card(ax, x, y, w, h, title, body="", face="#ffffff", edge="#cbd5e1",
                   title_color="#0f172a", body_color="#475569", lw=1.4,
                   title_size=12, body_size=8.8):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.012,rounding_size=0.025",
        linewidth=lw,
        edgecolor=edge,
        facecolor=face,
    )
    ax.add_patch(patch)
    ax.text(
        x + w * 0.06,
        y + h - min(0.04, h * 0.24),
        title,
        ha="left",
        va="top",
        fontsize=title_size,
        fontweight="bold",
        color=title_color,
    )
    if body:
        wrap_width = max(18, int(w * 95))
        wrapped = "\n".join(
            "\n".join(textwrap.wrap(part, width=wrap_width)) if part else ""
            for part in str(body).splitlines()
        )
        ax.text(
            x + w * 0.06,
            y + h - min(0.085, h * 0.52),
            wrapped,
            ha="left",
            va="top",
            fontsize=body_size,
            linespacing=1.25,
            color=body_color,
        )
    return patch


def _add_flow_arrow(ax, start, end, color="#64748b", lw=2.0):
    ax.add_patch(FancyArrowPatch(
        start,
        end,
        arrowstyle="-|>",
        mutation_scale=15,
        linewidth=lw,
        color=color,
        shrinkA=4,
        shrinkB=6,
    ))


def _save_model_selection_flow_artifact(
    game_name: str,
    candidate_metrics: dict,
    selected_model_name: str,
    selected_test_acc: float,
    selected_f1: float,
    outputs_dir: str,
) -> tuple[str, str]:
    """Save a diagram showing how one game's champion candidate is selected."""
    png_path = os.path.join(outputs_dir, "classification_model_selection_flow.png")
    svg_path = os.path.join(outputs_dir, "classification_model_selection_flow.svg")

    if not candidate_metrics:
        return "", ""

    rows = []
    for model_name, metrics in candidate_metrics.items():
        rows.append({
            "model": model_name,
            "cv_accuracy": float(metrics.get("cv_accuracy_mean") or 0.0),
            "cv_f1": float(metrics.get("cv_f1_macro_mean") or 0.0),
            "selected": model_name == selected_model_name,
        })
    rows = sorted(rows, key=lambda row: row["cv_accuracy"], reverse=True)

    plt.rcParams["svg.fonttype"] = "none"
    fig, ax = plt.subplots(figsize=(16, 9), dpi=180)
    fig.patch.set_facecolor("#f8fafc")
    ax.set_facecolor("#f8fafc")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    game_label = game_name.replace("_", " ").title()
    ax.text(
        0.05,
        0.94,
        f"{game_label} classification model selection",
        ha="left",
        va="center",
        fontsize=22,
        fontweight="bold",
        color="#0f172a",
    )
    ax.text(
        0.05,
        0.895,
        "One game module trains multiple candidate families, evaluates them, and sends the selected run to MLflow registration.",
        ha="left",
        va="center",
        fontsize=11.5,
        color="#475569",
    )

    _add_flow_card(
        ax, 0.05, 0.63, 0.18, 0.18,
        "Game telemetry",
        "Raw session events and scores for this game only.",
        face="#eef2ff",
        edge="#818cf8",
    )
    _add_flow_card(
        ax, 0.05, 0.39, 0.18, 0.17,
        "Feature table",
        "Clean participant-level features after quality gates, anomaly filtering, and aggregation.",
        edge="#94a3b8",
    )
    _add_flow_arrow(ax, (0.14, 0.63), (0.14, 0.56))

    _add_flow_card(
        ax, 0.30, 0.23, 0.34, 0.58,
        "Candidate model families",
        "",
        face="#f8fafc",
        edge="#64748b",
        title_size=13,
    )
    ax.text(
        0.325,
        0.715,
        "Same train split and CV folds for this game.",
        ha="left",
        va="center",
        fontsize=8.8,
        color="#64748b",
    )

    max_cards = min(len(rows), 6)
    card_h = min(0.078, 0.43 / max(max_cards, 1))
    gap = 0.012
    start_y = 0.63
    for idx, row in enumerate(rows[:max_cards]):
        y = start_y - idx * (card_h + gap)
        selected = row["selected"]
        face = "#fff7ed" if selected else "#ffffff"
        edge = "#f97316" if selected else "#cbd5e1"
        lw = 2.1 if selected else 1.2
        _add_flow_card(
            ax, 0.325, y, 0.29, card_h,
            row["model"],
            "",
            face=face,
            edge=edge,
            lw=lw,
            title_size=8.7,
        )
        ax.text(
            0.343,
            y + card_h * 0.42,
            f"CV acc {row['cv_accuracy']:.3f} | macro F1 {row['cv_f1']:.3f}",
            ha="left",
            va="center",
            fontsize=7.4,
            color="#475569",
        )
        bar_x = 0.343
        bar_y = y + card_h * 0.17
        bar_w = 0.16
        ax.add_patch(FancyBboxPatch(
            (bar_x, bar_y), bar_w, 0.011,
            boxstyle="round,pad=0,rounding_size=0.006",
            linewidth=0,
            facecolor="#e2e8f0",
        ))
        ax.add_patch(FancyBboxPatch(
            (bar_x, bar_y), bar_w * min(max(row["cv_accuracy"], 0), 1), 0.011,
            boxstyle="round,pad=0,rounding_size=0.006",
            linewidth=0,
            facecolor="#f97316" if selected else "#2563eb",
        ))
        if selected:
            ax.text(
                0.56,
                y + card_h * 0.22,
                "SELECTED",
                ha="center",
                va="center",
                fontsize=7.4,
                fontweight="bold",
                color="#9a3412",
            )

    _add_flow_arrow(ax, (0.23, 0.475), (0.30, 0.475))

    _add_flow_card(
        ax, 0.71, 0.59, 0.22, 0.18,
        "Evaluation gate",
        "CV ranks candidates by accuracy and macro F1. The selected model is checked on holdout data.",
        face="#ecfeff",
        edge="#06b6d4",
    )
    _add_flow_card(
        ax, 0.71, 0.35, 0.22, 0.16,
        "Selected classifier",
        f"{selected_model_name} holdout acc {selected_test_acc:.3f}, macro F1 {selected_f1:.3f}.",
        face="#fff7ed",
        edge="#f97316",
        lw=2.0,
    )
    _add_flow_card(
        ax, 0.71, 0.14, 0.22, 0.13,
        "MLflow registry",
        "Register as challenger; promote to @champion if it beats production.",
        face="#f0fdf4",
        edge="#22c55e",
    )
    _add_flow_arrow(ax, (0.64, 0.55), (0.71, 0.68))
    _add_flow_arrow(ax, (0.82, 0.59), (0.82, 0.51))
    _add_flow_arrow(ax, (0.82, 0.35), (0.82, 0.27))
    _add_flow_arrow(ax, (0.82, 0.14), (0.82, 0.095), color="#22c55e")
    ax.text(
        0.82,
        0.055,
        "Inference loads the game-specific champion",
        ha="center",
        va="center",
        fontsize=9.4,
        fontweight="bold",
        color="#166534",
    )
    ax.text(
        0.49,
        0.135,
        "The training DAG repeats this independent loop for every eligible game module.",
        ha="center",
        va="center",
        fontsize=10.2,
        fontweight="bold",
        color="#334155",
    )
    ax.scatter([0.365, 0.475, 0.595], [0.075] * 3, s=70, c=["#2563eb", "#f97316", "#22c55e"])
    ax.text(0.382, 0.075, "candidate score", va="center", fontsize=8.8, color="#475569")
    ax.text(0.492, 0.075, "selected best", va="center", fontsize=8.8, color="#475569")
    ax.text(0.612, 0.075, "registry champion", va="center", fontsize=8.8, color="#475569")

    fig.tight_layout()
    fig.savefig(png_path, dpi=180, bbox_inches="tight", facecolor=fig.get_facecolor())
    fig.savefig(svg_path, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return png_path, svg_path


def _save_classification_report_pack(
    game_name: str,
    selected_model_name: str,
    selected_test_acc: float,
    selected_f1: float,
    selected_cv_acc: float,
    selected_cv_std: float,
    selected_cv_f1: float,
    selected_cv_f1_std: float,
    baseline_lift_f1: float,
    candidate_metrics: dict,
    baseline_metrics: dict,
    class_counts: dict,
    counts: dict,
    calibration_metrics: dict,
    artifact_paths: dict,
    outputs_dir: str,
) -> tuple[str, str]:
    """Save a slide-friendly model card and a report manifest for one game."""
    png_path = os.path.join(outputs_dir, "classification_model_card.png")
    md_path = os.path.join(outputs_dir, "classification_report_manifest.md")

    rows = sorted(
        [
            (
                name,
                float(metrics.get("cv_accuracy_mean") or 0.0),
                float(metrics.get("cv_f1_macro_mean") or 0.0),
                name == selected_model_name,
            )
            for name, metrics in candidate_metrics.items()
        ],
        key=lambda row: row[1],
        reverse=True,
    )
    best_baseline = max(
        baseline_metrics.items(),
        key=lambda item: item[1].get("holdout_f1_macro") or 0,
    )[0].replace("Baseline: ", "") if baseline_metrics else "N/A"

    game_label = game_name.replace("_", " ").title()
    fig, ax = plt.subplots(figsize=(13.5, 7.5), dpi=180)
    fig.patch.set_facecolor("#f8fafc")
    ax.set_facecolor("#f8fafc")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ax.text(
        0.045,
        0.93,
        f"{game_label} classification model card",
        ha="left",
        va="center",
        fontsize=20,
        fontweight="bold",
        color="#0f172a",
    )
    ax.text(
        0.045,
        0.885,
        "Presentation-ready summary of the model selected by the per-game classification pipeline.",
        ha="left",
        va="center",
        fontsize=10.5,
        color="#475569",
    )

    _add_flow_card(
        ax, 0.045, 0.60, 0.30, 0.20,
        "Selected champion candidate",
        selected_model_name,
        face="#fff7ed",
        edge="#f97316",
        lw=2.0,
        title_size=12.5,
        body_size=15,
    )
    metric_cards = [
        ("Holdout accuracy", selected_test_acc, 0.39, 0.69, "#dbeafe", "#2563eb"),
        ("Holdout macro F1", selected_f1, 0.58, 0.69, "#dcfce7", "#16a34a"),
        ("CV accuracy", selected_cv_acc, 0.39, 0.52, "#e0f2fe", "#0284c7"),
        ("CV macro F1", selected_cv_f1, 0.58, 0.52, "#fef3c7", "#d97706"),
    ]
    for label, value, x, y, face, edge in metric_cards:
        _add_flow_card(
            ax, x, y, 0.16, 0.11,
            label,
            f"{value:.3f}",
            face=face,
            edge=edge,
            title_size=9.2,
            body_size=15,
        )
    _add_flow_card(
        ax, 0.77, 0.52, 0.18, 0.28,
        "Training evidence",
        (
            f"Participants: {counts.get('participant_aggregate_examples', 'N/A')}\n"
            f"Train/test: {counts.get('evaluation_train_examples', 'N/A')}/"
            f"{counts.get('evaluation_test_examples', 'N/A')}\n"
            f"Classes: {len(class_counts)}\n"
            f"Best baseline: {best_baseline}"
        ),
        face="#ffffff",
        edge="#94a3b8",
        title_size=11,
        body_size=9.2,
    )

    ax.text(0.045, 0.46, "Candidate ranking", fontsize=12, fontweight="bold", color="#0f172a")
    y = 0.405
    for name, cv_acc, cv_f1, selected in rows[:5]:
        color = "#f97316" if selected else "#2563eb"
        label = f"{name}  |  CV acc {cv_acc:.3f}  |  macro F1 {cv_f1:.3f}"
        ax.text(0.06, y, label, ha="left", va="center", fontsize=9.2, color="#334155")
        ax.add_patch(FancyBboxPatch(
            (0.06, y - 0.027),
            0.28,
            0.009,
            boxstyle="round,pad=0,rounding_size=0.005",
            linewidth=0,
            facecolor="#e2e8f0",
        ))
        ax.add_patch(FancyBboxPatch(
            (0.06, y - 0.027),
            0.28 * min(max(cv_acc, 0), 1),
            0.009,
            boxstyle="round,pad=0,rounding_size=0.005",
            linewidth=0,
            facecolor=color,
        ))
        if selected:
            ax.text(0.36, y - 0.014, "selected", fontsize=8.2, fontweight="bold", color="#9a3412")
        y -= 0.07

    _add_flow_card(
        ax, 0.49, 0.17, 0.20, 0.20,
        "Robustness metrics",
        (
            f"Baseline lift F1: {baseline_lift_f1:+.3f}\n"
            f"Brier score: {calibration_metrics.get('brier_score', 'N/A')}\n"
            f"ECE: {calibration_metrics.get('ece', 'N/A')}\n"
            f"CV std acc: {selected_cv_std:.3f}\n"
            f"CV std F1: {selected_cv_f1_std:.3f}"
        ),
        face="#f0fdf4",
        edge="#22c55e",
        title_size=11,
        body_size=9.2,
    )
    _add_flow_card(
        ax, 0.74, 0.17, 0.21, 0.20,
        "Report figures",
        (
            "Selection flow\n"
            "Candidate comparison\n"
            "Confusion matrix\n"
            "Feature importance\n"
            "Calibration curve"
        ),
        face="#eef2ff",
        edge="#818cf8",
        title_size=11,
        body_size=9.2,
    )
    ax.text(
        0.045,
        0.055,
        "Use with the MLflow run page and Airflow DAG graph to show experiment tracking, artifacts, and orchestration.",
        ha="left",
        va="center",
        fontsize=9,
        color="#64748b",
    )
    fig.tight_layout()
    fig.savefig(png_path, dpi=180, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)

    def rel_or_name(path):
        return os.path.basename(path) if path else "not generated"

    manifest = [
        f"# {game_label} Classification Report Pack",
        "",
        "## Selected model",
        f"- Selected candidate: {selected_model_name}",
        f"- Holdout accuracy: {selected_test_acc:.4f}",
        f"- Holdout macro F1: {selected_f1:.4f}",
        f"- CV accuracy: {selected_cv_acc:.4f} +/- {selected_cv_std:.4f}",
        f"- CV macro F1: {selected_cv_f1:.4f} +/- {selected_cv_f1_std:.4f}",
        f"- Baseline lift macro F1: {baseline_lift_f1:+.4f}",
        f"- Brier score: {calibration_metrics.get('brier_score', 'N/A')}",
        f"- ECE: {calibration_metrics.get('ece', 'N/A')}",
        "",
        "## Dataset evidence",
        f"- Raw sessions: {counts.get('raw_sessions', 'N/A')}",
        f"- Raw participants: {counts.get('raw_participants', 'N/A')}",
        f"- Labeled sessions: {counts.get('labeled_sessions', 'N/A')}",
        f"- Labeled participants: {counts.get('labeled_participants', 'N/A')}",
        f"- Participant aggregate examples: {counts.get('participant_aggregate_examples', 'N/A')}",
        f"- Evaluation train/test examples: {counts.get('evaluation_train_examples', 'N/A')}/"
        f"{counts.get('evaluation_test_examples', 'N/A')}",
        f"- Class counts: {class_counts}",
        "",
        "## Figures to include",
        f"- Model card: {rel_or_name(png_path)}",
        f"- Model selection flow: {rel_or_name(artifact_paths.get('selection_flow_png'))}",
        f"- Candidate comparison: {rel_or_name(artifact_paths.get('comparison_png'))}",
        f"- Candidate comparison SVG: {rel_or_name(artifact_paths.get('comparison_svg'))}",
        f"- Candidate metrics CSV: {rel_or_name(artifact_paths.get('comparison_csv'))}",
        f"- Confusion matrix: {rel_or_name(artifact_paths.get('confusion_matrix'))}",
        f"- Feature importance: {rel_or_name(artifact_paths.get('feature_importance'))}",
        f"- Calibration curve: {rel_or_name(artifact_paths.get('calibration_curve'))}",
        "",
        "## Suggested captions",
        "- Model card: Summary of the selected classifier family and evidence used by the MLOps pipeline.",
        "- Model selection flow: Per-game training loop from telemetry to MLflow champion promotion.",
        "- Candidate comparison: Cross-validation metrics used to select the best model family.",
        "- Confusion matrix: Error distribution across ADHD profile classes on the holdout split.",
        "- Feature importance: Behavioral game features contributing most to the selected classifier.",
        "",
        "## Presentation note",
        "Pair this pack with screenshots of the successful Airflow DAG run and the matching MLflow run artifacts.",
        "",
    ]
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(manifest))

    return png_path, md_path


def run_classification(game_name: str, verbose: bool = True) -> dict:
    """Full classification pipeline for a single game."""
    OUTPUTS = get_game_outputs(game_name)

    if verbose:
        print(f"\n  --- Classification: {game_name.upper()} ---")

    # 0. Load raw data
    df_loaded = load_raw(game_name)
    n_raw_sessions = len(df_loaded)
    n_raw_participants = (
        df_loaded["Participant_ID"].nunique()
        if "Participant_ID" in df_loaded.columns
        else 0
    )
    df_raw = _filter_labeled_training_rows(df_loaded, verbose=verbose)

    # Remove anomaly-flagged sessions before any training happens
    df_raw = _filter_anomalous_sessions(df_raw, game_name, verbose=verbose)

    n_labeled_sessions = len(df_raw)
    n_labeled_participants = (
        df_raw["Participant_ID"].nunique()
        if "Participant_ID" in df_raw.columns
        else 0
    )

    # ── Phase 1: Data Quality Checks ─────────────────────────────────────
    dq_report = None
    try:
        from multigame_pipeline.data_quality import run_data_quality_checks
        dq_report = run_data_quality_checks(df_raw, game_name)

        if verbose:
            status = "PASS" if dq_report.passed else "FAIL"
            print(f"    [DQ] Data Quality: {status}")
            for w in dq_report.warnings:
                print(f"         ⚠ {w}")
            for f in dq_report.failures:
                print(f"         ✗ {f}")

        if not dq_report.passed:
            return {
                "game_name": game_name,
                "status": "data_quality_failed",
                "dq_report": {
                    "passed": False,
                    "warnings": dq_report.warnings,
                    "failures": dq_report.failures,
                    "stats": dq_report.stats,
                },
            }
    except ImportError:
        if verbose:
            print("    [DQ] data_quality module not available — skipping")

    df_enc, encoders = encode_dataframe(df_raw, game_name)

    # 1. Aggregate
    agg_df = build_participant_aggregate(df_raw, game_name, n_sessions=None)
    class_counts = agg_df["cluster"].value_counts().to_dict()

    # 2. Encode target
    le = LabelEncoder()
    agg_df["y"] = le.fit_transform(agg_df["cluster"])
    class_names = le.classes_.tolist()
    n_classes = len(class_names)

    # 3. Feature matrix
    feature_cols = get_aggregate_feature_cols(agg_df)
    for col in ["Age_Group", "Cognitive_Level"]:
        if col in agg_df.columns:
            le_c = LabelEncoder()
            agg_df[f"{col}_enc"] = le_c.fit_transform(agg_df[col].astype(str))
            if f"{col}_enc" not in feature_cols:
                feature_cols.append(f"{col}_enc")

    feature_cols = [c for c in feature_cols
                    if c in agg_df.columns and agg_df[c].dtype != object and c != "y"]

    X = agg_df[feature_cols].values.astype(np.float32)
    y = agg_df["y"].values

    # Guard: skip games where any class has fewer than 2 members
    min_class_count = min(np.bincount(y))
    if min_class_count < 2:
        if verbose:
            print(f"    [SKIP] Smallest class has only {min_class_count} member(s) — "
                  f"need ≥2 for stratified split. Skipping {game_name}.")
        return {
            "game_name": game_name,
            "status": "insufficient_class_members",
            "detail": f"Smallest class has {min_class_count} member(s), need ≥2.",
            "class_counts": class_counts,
        }

    if verbose:
        print(
            "    Raw sessions: "
            f"{n_raw_sessions} | Raw participants: {n_raw_participants}"
        )
        print(
            "    Labeled sessions: "
            f"{n_labeled_sessions} | Labeled participants: {n_labeled_participants}"
        )
        print(
            "    Training examples after participant aggregation: "
            f"{len(X)} | Features: {len(feature_cols)} | Classes: {n_classes}"
        )
        print(f"    Class counts: {class_counts}")

    # 4. Train/test split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, stratify=y, random_state=42
    )
    if verbose:
        print(f"    Evaluation split: train={len(X_train)} | test={len(X_test)}")

    # 5. Cross-validation across candidate model families
    # Adapt n_splits to the smallest class in the training set
    min_train_class = min(np.bincount(y_train))
    n_splits = min(5, min_train_class)
    if n_splits < 2:
        n_splits = 2
    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
    results = {}
    candidate_models = _build_candidate_models(n_classes)
    candidate_cv = {}
    candidate_cv_std = {}
    candidate_f1 = {}
    candidate_f1_std = {}
    candidate_metrics = {}

    for name, model in candidate_models.items():
        cv_result = cross_validate(
            model,
            X_train,
            y_train,
            cv=cv,
            scoring={"accuracy": "accuracy", "f1_macro": "f1_macro"},
            n_jobs=-1,
            error_score="raise",
        )
        scores = cv_result["test_accuracy"]
        f1_scores_cv = cv_result["test_f1_macro"]
        for metric_name, values in {
            "accuracy": scores,
            "f1_macro": f1_scores_cv,
        }.items():
            if not np.all(np.isfinite(values)):
                raise RuntimeError(f"Non-finite {metric_name} CV values for {name}: {values}")
        candidate_cv[name] = float(scores.mean())
        candidate_cv_std[name] = float(scores.std())
        candidate_f1[name] = float(f1_scores_cv.mean())
        candidate_f1_std[name] = float(f1_scores_cv.std())
        results[name] = candidate_cv[name]
        candidate_metrics[name] = {
            "cv_accuracy_mean": round(candidate_cv[name], 4),
            "cv_accuracy_std": round(candidate_cv_std[name], 4),
            "cv_f1_macro_mean": round(candidate_f1[name], 4),
            "cv_f1_macro_std": round(candidate_f1_std[name], 4),
        }
        if verbose:
            print(
                f"      {name:20s}  acc={scores.mean():.4f} (+/-{scores.std():.4f})"
                f"  f1={f1_scores_cv.mean():.4f} (+/-{f1_scores_cv.std():.4f})"
            )

    # 6. Select the production family using train-only CV, then evaluate once.
    selected_model_name = _select_model_by_cv(candidate_cv, candidate_cv_std)
    selected_model = clone(candidate_models[selected_model_name])
    selected_model.fit(X_train, y_train)
    y_selected_pred = selected_model.predict(X_test)
    selected_test_acc = accuracy_score(y_test, y_selected_pred)
    results["Selected Model Test"] = selected_test_acc
    if verbose:
        print(
            f"      {'Selected':20s}  {selected_model_name} "
            f"test_acc={selected_test_acc:.4f}"
        )

    # ── Phase 2: Baseline Comparisons ────────────────────────────────────
    if verbose:
        print("\n    --- Baseline Comparisons ---")
    baseline_accs, baseline_f1s = _run_baselines(X_train, y_train, X_test, y_test, cv, verbose)
    results.update(baseline_accs)
    baseline_metrics = {
        name: {
            "holdout_accuracy": round(float(acc), 4),
            "holdout_f1_macro": round(float(baseline_f1s.get(name, 0.0)), 4),
        }
        for name, acc in baseline_accs.items()
    }

    # Compute selected-model F1 and baseline lift
    selected_f1 = f1_score(y_test, y_selected_pred, average="macro", zero_division=0)
    best_baseline_f1 = max(baseline_f1s.values()) if baseline_f1s else 0.0
    baseline_lift_f1 = round(selected_f1 - best_baseline_f1, 4)
    results["selected_f1_macro"] = round(selected_f1, 4)
    results["ensemble_f1_macro"] = round(selected_f1, 4)
    results["best_baseline_f1"] = round(best_baseline_f1, 4)
    results["baseline_lift_f1"] = baseline_lift_f1

    if verbose:
        print(f"\n      Selected F1 (macro): {selected_f1:.4f}")
        print(f"      Best Baseline F1:    {best_baseline_f1:.4f}")
        print(f"      Lift over baseline:  {baseline_lift_f1:+.4f}")

    separability_diagnostic = _separability_diagnostic(candidate_metrics, selected_test_acc)
    results["separability_suspicious"] = bool(separability_diagnostic["suspicious"])
    if verbose and separability_diagnostic["suspicious"]:
        print(f"      [WARN] {separability_diagnostic['message']}")

    comparison_png, comparison_csv, comparison_svg = _save_model_comparison_artifacts(
        game_name=game_name,
        candidate_metrics=candidate_metrics,
        baseline_metrics=baseline_metrics,
        selected_model_name=selected_model_name,
        selected_test_acc=selected_test_acc,
        selected_f1=selected_f1,
        outputs_dir=OUTPUTS,
    )
    flow_png, flow_svg = _save_model_selection_flow_artifact(
        game_name=game_name,
        candidate_metrics=candidate_metrics,
        selected_model_name=selected_model_name,
        selected_test_acc=selected_test_acc,
        selected_f1=selected_f1,
        outputs_dir=OUTPUTS,
    )
    results["classification_model_comparison_png"] = comparison_png
    results["classification_model_comparison_csv"] = comparison_csv
    results["classification_model_comparison_svg"] = comparison_svg
    results["classification_model_selection_flow_png"] = flow_png
    results["classification_model_selection_flow_svg"] = flow_svg
    results["presentation_model_evaluation_png"] = _publish_presentation_artifact(game_name, comparison_png)

    # ── Phase 3: Model Calibration ───────────────────────────────────────
    if verbose:
        print("\n    --- Model Calibration ---")

    calibration_path = ""
    try:
        calibrated_selected = CalibratedClassifierCV(selected_model, method="isotonic", cv=3)
        calibrated_selected.fit(X_train, y_train)
        y_cal_proba = calibrated_selected.predict_proba(X_test)

        # Brier score (one-vs-rest average)
        brier_scores = []
        for i in range(n_classes):
            y_binary = (y_test == i).astype(int)
            bs = brier_score_loss(y_binary, y_cal_proba[:, i])
            brier_scores.append(bs)
        avg_brier = float(np.mean(brier_scores))

        # ECE on the max-confidence prediction
        y_max_prob = y_cal_proba.max(axis=1)
        y_correct = (calibrated_selected.predict(X_test) == y_test).astype(int)
        ece = _compute_ece(y_correct, y_max_prob)

        results["brier_score"] = round(avg_brier, 4)
        results["ece"] = round(ece, 4)

        if verbose:
            print(f"      Brier Score (avg):   {avg_brier:.4f}")
            print(f"      ECE:                 {ece:.4f}")

        # Calibration curve
        calibration_path = _save_calibration_curve(y_test, y_cal_proba, class_names, OUTPUTS, game_name)
        if verbose:
            print(f"      [OK] Calibration curve saved")

        has_calibration = True
    except Exception as e:
        if verbose:
            print(f"      [WARN] Calibration failed: {e}")
        calibrated_selected = None
        has_calibration = False

    # 7. Evaluation
    y_pred = y_selected_pred
    if verbose:
        print(classification_report(y_test, y_pred, target_names=class_names))

    # 8. Confusion matrix
    cm = confusion_matrix(y_test, y_pred)
    fig, ax = plt.subplots(figsize=(8, 6))
    disp = ConfusionMatrixDisplay(confusion_matrix=cm, display_labels=class_names)
    disp.plot(ax=ax, cmap="Blues", colorbar=False)
    ax.set_title(f"{game_name.upper()} - Confusion Matrix", fontsize=13, weight="bold")
    plt.xticks(rotation=30, ha="right", fontsize=9)
    plt.yticks(fontsize=9)
    plt.tight_layout()
    cm_path = os.path.join(OUTPUTS, "classification_confusion_matrix.png")
    plt.savefig(cm_path, dpi=150)
    plt.close()

    # 9. Feature importance
    fi_path = ""
    importances = _get_feature_importance(selected_model, feature_cols)
    if importances is not None:
        idx = np.argsort(importances)[::-1][:20]
        top_features = [feature_cols[i] for i in idx]
        top_imp = importances[idx]

        fig, ax = plt.subplots(figsize=(9, 6))
        colors = sns.color_palette("viridis", len(top_features))
        ax.barh(top_features[::-1], top_imp[::-1], color=colors[::-1])
        ax.set_xlabel("Feature Importance", fontsize=11)
        ax.set_title(f"{game_name.upper()} - Top Features", fontsize=13, weight="bold")
        plt.tight_layout()
        fi_path = os.path.join(OUTPUTS, "classification_feature_importance.png")
        plt.savefig(fi_path, dpi=150)
        plt.close()
    elif verbose:
        print("      [WARN] Selected model does not expose feature importances")

    model_card_png, report_manifest_md = _save_classification_report_pack(
        game_name=game_name,
        selected_model_name=selected_model_name,
        selected_test_acc=selected_test_acc,
        selected_f1=selected_f1,
        selected_cv_acc=candidate_cv[selected_model_name],
        selected_cv_std=candidate_cv_std[selected_model_name],
        selected_cv_f1=candidate_f1[selected_model_name],
        selected_cv_f1_std=candidate_f1_std[selected_model_name],
        baseline_lift_f1=baseline_lift_f1,
        candidate_metrics=candidate_metrics,
        baseline_metrics=baseline_metrics,
        class_counts=class_counts,
        counts={
            "raw_sessions": n_raw_sessions,
            "raw_participants": n_raw_participants,
            "labeled_sessions": n_labeled_sessions,
            "labeled_participants": n_labeled_participants,
            "participant_aggregate_examples": len(X),
            "evaluation_train_examples": len(X_train),
            "evaluation_test_examples": len(X_test),
        },
        calibration_metrics={
            "brier_score": results.get("brier_score"),
            "ece": results.get("ece"),
        },
        artifact_paths={
            "selection_flow_png": flow_png,
            "comparison_png": comparison_png,
            "comparison_csv": comparison_csv,
            "comparison_svg": comparison_svg,
            "presentation_model_evaluation_png": results.get("presentation_model_evaluation_png"),
            "confusion_matrix": cm_path,
            "feature_importance": fi_path,
            "calibration_curve": calibration_path,
        },
        outputs_dir=OUTPUTS,
    )
    results["classification_model_card_png"] = model_card_png
    results["classification_report_manifest_md"] = report_manifest_md

    # 10. Refit the production model on all participant aggregates.
    # Evaluation metrics above remain holdout-based; the saved inference model
    # should use every labeled participant available at training time.
    production_model = clone(candidate_models[selected_model_name])
    production_model.fit(X, y)

    cal_full = None
    y_full_pred = production_model.predict(X)
    y_full_proba = production_model.predict_proba(X)
    if has_calibration:
        try:
            cal_full = CalibratedClassifierCV(production_model, method="isotonic", cv=3)
            cal_full.fit(X, y)
            y_full_pred = cal_full.predict(X)
            y_full_proba = cal_full.predict_proba(X)
        except Exception as e:
            if verbose:
                print(f"      [WARN] Full-data calibration failed: {e}")

    # 11. Save artifacts
    model_bundle = {
        "model": production_model,
        "feature_cols": feature_cols,
        "label_encoder": le,
        "class_names": class_names,
        "cv_results": {name: round(float(acc), 4) for name, acc in candidate_cv.items()},
        "candidate_metrics": candidate_metrics,
        "baseline_metrics": baseline_metrics,
        "separability_diagnostic": separability_diagnostic,
        "selected_model_name": selected_model_name,
        "selected_model_cv_acc": candidate_cv[selected_model_name],
        "selected_model_cv_std": candidate_cv_std[selected_model_name],
        "selected_model_cv_f1_macro": candidate_f1[selected_model_name],
        "selected_model_cv_f1_macro_std": candidate_f1_std[selected_model_name],
        # Backward-compatible metric key used by MLflow registry code.
        "ensemble_test_acc": selected_test_acc,
        "selected_f1_macro": round(selected_f1, 4),
        "game_name": game_name,
        "training_metadata": {
            "raw_sessions": n_raw_sessions,
            "raw_participants": n_raw_participants,
            "labeled_sessions": n_labeled_sessions,
            "labeled_participants": n_labeled_participants,
            "participant_aggregate_examples": len(X),
            "evaluation_train_examples": len(X_train),
            "evaluation_test_examples": len(X_test),
            "class_counts": class_counts,
            "saved_model_scope": "all_labeled_participant_aggregates",
        },
    }
    if cal_full is not None:
        model_bundle["calibrated_model"] = cal_full

    model_path = os.path.join(OUTPUTS, "classifier_ensemble.pkl")
    joblib.dump(model_bundle, model_path)

    agg_df["predicted_cluster"] = le.inverse_transform(y_full_pred)
    agg_df["confidence"] = [float(y_full_proba[i].max()) for i in range(len(y_full_pred))]

    # 12. Persist to PostgreSQL
    try:
        from datetime import datetime, timezone
        sys.path.insert(0, os.path.join(_ROOT))
        from mlops.db import insert_classification_results

        rows_to_insert = [
            {
                "game": game_name,
                "participant_id": row["Participant_ID"],
                "actual_cluster": row["cluster"],
                "predicted_cluster": row["predicted_cluster"],
                "confidence": round(row["confidence"], 4),
                "pipeline_run_id": None,
                "classified_at": datetime.now(timezone.utc).isoformat(),
            }
            for _, row in agg_df.iterrows()
        ]
        n_written = insert_classification_results(rows_to_insert)
        if verbose:
            print(f"    [DB] Wrote {n_written} classification results to PostgreSQL")
    except Exception as e:
        print(f"    [DB-WARN] Could not persist to PostgreSQL: {e}")

    if verbose:
        print(f"    [OK] Saved to {OUTPUTS}/")

    return {
        "game_name": game_name,
        "selected_model_name": selected_model_name,
        "selected_model_cv_acc": candidate_cv[selected_model_name],
        "selected_model_cv_std": candidate_cv_std[selected_model_name],
        "selected_model_cv_f1_macro": candidate_f1[selected_model_name],
        "selected_model_cv_f1_macro_std": candidate_f1_std[selected_model_name],
        # Backward-compatible metric key used by MLflow registry code.
        "ensemble_test_acc": selected_test_acc,
        "selected_f1_macro": round(selected_f1, 4),
        "cv_results": {name: round(float(acc), 4) for name, acc in candidate_cv.items()},
        "candidate_metrics": candidate_metrics,
        "baseline_metrics": baseline_metrics,
        "separability_diagnostic": separability_diagnostic,
        "classification_model_comparison_png": comparison_png,
        "classification_model_comparison_csv": comparison_csv,
        "classification_model_comparison_svg": comparison_svg,
        "classification_model_selection_flow_png": flow_png,
        "classification_model_selection_flow_svg": flow_svg,
        "presentation_model_evaluation_png": results.get("presentation_model_evaluation_png"),
        "classification_model_card_png": model_card_png,
        "classification_report_manifest_md": report_manifest_md,
        "class_names": class_names,
        "baseline_lift_f1": baseline_lift_f1,
        "brier_score": results.get("brier_score"),
        "ece": results.get("ece"),
        "raw_sessions": n_raw_sessions,
        "raw_participants": n_raw_participants,
        "labeled_sessions": n_labeled_sessions,
        "labeled_participants": n_labeled_participants,
        "participant_aggregate_examples": len(X),
        "evaluation_train_examples": len(X_train),
        "evaluation_test_examples": len(X_test),
        "class_counts": class_counts,
        "dq_report": {
            "passed": dq_report.passed,
            "warnings": dq_report.warnings,
            "failures": dq_report.failures,
        } if dq_report else None,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", default="gonogo")
    args = parser.parse_args()
    run_classification(args.game)

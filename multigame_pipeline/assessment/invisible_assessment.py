"""
Module 3 - Per-Game Invisible / Passive ADHD Assessment
========================================================
Bayesian session-by-session profile probability updater for each game.
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

from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report

warnings.filterwarnings("ignore")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..", "..")
sys.path.insert(0, _ROOT)

from multigame_pipeline.preprocessor import load_raw, get_game_outputs
from multigame_pipeline.feature_engineer import add_derived_features, get_all_session_features


class BayesianProfileUpdater:
    """Running posterior over ADHD profiles per participant."""

    def __init__(self, class_names):
        self.class_names = class_names
        n = len(class_names)
        self.posterior = np.ones(n) / n
        self.history = []

    def reset(self):
        n = len(self.class_names)
        self.posterior = np.ones(n) / n
        self.history = []

    def update(self, likelihood: np.ndarray):
        posterior = self.posterior * likelihood
        posterior = posterior / (posterior.sum() + 1e-12)
        self.posterior = posterior
        self.history.append(posterior.copy())

    def predicted_profile(self):
        return self.class_names[np.argmax(self.posterior)]


def _build_session_model(df, game_name):
    df = add_derived_features(df, game_name)
    feature_cols = get_all_session_features(game_name)
    feature_cols = [c for c in feature_cols if c in df.columns]

    le = LabelEncoder()
    y = le.fit_transform(df["cluster"])
    X = df[feature_cols].fillna(0).values.astype(np.float32)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    X_tr, X_te, y_tr, y_te = train_test_split(X_scaled, y, test_size=0.2, stratify=y, random_state=42)

    base_clf = RandomForestClassifier(n_estimators=200, max_depth=10, random_state=42, n_jobs=-1)
    calibrated = CalibratedClassifierCV(base_clf, method="sigmoid", cv=5)
    calibrated.fit(X_tr, y_tr)

    y_pred = calibrated.predict(X_te)
    test_acc = accuracy_score(y_te, y_pred)

    return calibrated, scaler, le, feature_cols, test_acc, y_te, y_pred


def _simulate_convergence(df, model, scaler, le, feature_cols, game_name, n_sample=8):
    df = add_derived_features(df, game_name)
    feature_cols = [c for c in feature_cols if c in df.columns]
    class_names = le.classes_.tolist()
    pids = df["Participant_ID"].unique()
    rng = np.random.default_rng(42)
    sample_pids = rng.choice(pids, size=min(n_sample, len(pids)), replace=False)

    convergence = {}
    for pid in sample_pids:
        pdata = df[df["Participant_ID"] == pid].reset_index(drop=True)
        X_p = scaler.transform(pdata[feature_cols].fillna(0).values.astype(np.float32))
        updater = BayesianProfileUpdater(class_names)
        for i in range(len(X_p)):
            proba = model.predict_proba(X_p[i:i+1])[0]
            updater.update(proba)
        convergence[pid] = {
            "history": np.array(updater.history),
            "true_label": pdata["cluster"].iloc[0],
            "class_names": class_names,
        }
    return convergence


def _plot_convergence(convergence, save_path, game_name):
    pids = list(convergence.keys())[:8]
    n_plots = len(pids)
    ncols = 2
    nrows = max(1, (n_plots + 1) // ncols)

    palette = sns.color_palette("tab10", 4)
    fig, axes = plt.subplots(nrows, ncols, figsize=(13, nrows * 3.5))
    if nrows == 1:
        axes = axes.reshape(1, -1)
    axes = axes.flatten()

    for i, pid in enumerate(pids):
        data = convergence[pid]
        history = data["history"]
        class_names = data["class_names"]
        ax = axes[i]
        for j, cls in enumerate(class_names):
            ax.plot(range(1, len(history)+1), history[:, j], label=cls, linewidth=1.8, color=palette[j])
        ax.axhline(0.5, color="grey", ls="--", alpha=0.5)
        ax.set_title(f"{pid}\n(True: {data['true_label']})", fontsize=8, weight="bold")
        ax.set_ylim(0, 1)
        ax.set_xlabel("Session", fontsize=7)
        ax.set_ylabel("P(profile)", fontsize=7)
        ax.tick_params(labelsize=7)

    axes[min(i, len(axes)-1)].legend(loc="upper right", fontsize=6)
    for j in range(i+1, len(axes)):
        axes[j].set_visible(False)

    fig.suptitle(f"{game_name.upper()} - Bayesian Posterior Convergence", fontsize=13, weight="bold", y=1.01)
    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close()


def _plot_early_accuracy(convergence, save_path, game_name):
    checkpoints = [5, 10, 20, 50]
    accs = {ck: [] for ck in checkpoints}
    for pid, data in convergence.items():
        history = data["history"]
        true_idx = data["class_names"].index(data["true_label"])
        for ck in checkpoints:
            idx = min(ck - 1, len(history) - 1)
            accs[ck].append(int(np.argmax(history[idx]) == true_idx))

    mean_accs = {ck: np.mean(v) for ck, v in accs.items()}

    fig, ax = plt.subplots(figsize=(7, 4))
    colors = sns.color_palette("Blues_d", len(checkpoints))
    bars = ax.bar([f"{ck}s" for ck in checkpoints], [mean_accs[ck] for ck in checkpoints], color=colors)
    for bar, ck in zip(bars, checkpoints):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.01,
                f"{mean_accs[ck]:.2f}", ha="center", fontsize=11, fontweight="bold")
    ax.set_ylim(0, 1.1); ax.set_ylabel("Accuracy")
    ax.set_title(f"{game_name.upper()} - Early Assessment Accuracy", fontsize=12, weight="bold")
    plt.tight_layout()
    plt.savefig(save_path, dpi=150); plt.close()


def run_assessment(game_name: str, verbose: bool = True) -> dict:
    OUTPUTS = get_game_outputs(game_name)

    if verbose:
        print(f"\n  --- Invisible Assessment: {game_name.upper()} ---")

    df_raw = load_raw(game_name)
    model, scaler, le, feature_cols, test_acc, y_te, y_pred = _build_session_model(df_raw, game_name)
    class_names = le.classes_.tolist()

    if verbose:
        print(f"    Session-level acc={test_acc:.4f}")
        print(classification_report(y_te, y_pred, target_names=class_names))

    convergence = _simulate_convergence(df_raw, model, scaler, le, feature_cols, game_name, n_sample=8)

    _plot_convergence(convergence, os.path.join(OUTPUTS, "assessment_convergence.png"), game_name)
    _plot_early_accuracy(convergence, os.path.join(OUTPUTS, "assessment_early_accuracy.png"), game_name)

    joblib.dump({
        "model": model, "scaler": scaler, "label_encoder": le,
        "feature_cols": feature_cols, "class_names": class_names,
    }, os.path.join(OUTPUTS, "invisible_assessment_model.pkl"))

    if verbose:
        print(f"    [OK] Saved to {OUTPUTS}/")

    return {"game_name": game_name, "session_test_acc": test_acc, "class_names": class_names}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", default="gonogo")
    args = parser.parse_args()
    run_assessment(args.game)

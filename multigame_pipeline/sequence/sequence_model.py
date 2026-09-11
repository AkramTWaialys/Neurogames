"""
Module 2 - Per-Game Temporal Sequence Modeling (Deep MLP / LSTM)
================================================================
Models the 50-session temporal sequence per participant for each game.
"""

import os
import sys
import warnings
import joblib
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_val_score
from sklearn.metrics import (
    classification_report, confusion_matrix, ConfusionMatrixDisplay, accuracy_score
)
from sklearn.pipeline import Pipeline

warnings.filterwarnings("ignore")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..", "..")
sys.path.insert(0, _ROOT)

from multigame_pipeline.preprocessor import load_raw, get_game_outputs
from multigame_pipeline.feature_engineer import build_sequence_tensor, get_all_session_features

try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


class _BiLSTM(nn.Module if HAS_TORCH else object):
    def __init__(self, input_size, hidden_size, n_layers, n_classes, dropout=0.3):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_size, hidden_size=hidden_size,
            num_layers=n_layers, batch_first=True,
            dropout=dropout if n_layers > 1 else 0, bidirectional=True,
        )
        self.classifier = nn.Sequential(
            nn.Linear(hidden_size * 2, 128), nn.ReLU(),
            nn.Dropout(dropout), nn.Linear(128, n_classes),
        )

    def forward(self, x):
        out, _ = self.lstm(x)
        return self.classifier(out[:, -1, :])


def _train_lstm(X_seq, y, class_names, verbose=True):
    n_samples, n_steps, n_features = X_seq.shape
    n_classes = len(class_names)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_seq.reshape(-1, n_features)).reshape(n_samples, n_steps, n_features)

    X_tr, X_te, y_tr, y_te = train_test_split(X_scaled, y, test_size=0.2, stratify=y, random_state=42)

    X_tr_t = torch.tensor(X_tr, dtype=torch.float32)
    y_tr_t = torch.tensor(y_tr, dtype=torch.long)
    X_te_t = torch.tensor(X_te, dtype=torch.float32)

    loader = DataLoader(TensorDataset(X_tr_t, y_tr_t), batch_size=32, shuffle=True)
    model = _BiLSTM(n_features, 128, 2, n_classes)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    criterion = nn.CrossEntropyLoss()
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=10, gamma=0.5)

    train_accs, epochs = [], 30
    for epoch in range(epochs):
        model.train()
        total, correct = 0, 0
        for xb, yb in loader:
            optimizer.zero_grad()
            logits = model(xb)
            loss = criterion(logits, yb)
            loss.backward()
            optimizer.step()
            correct += (logits.argmax(1) == yb).sum().item()
            total += len(yb)
        train_accs.append(correct / total)
        scheduler.step()

    model.eval()
    with torch.no_grad():
        y_pred = model(X_te_t).argmax(1).numpy()

    return model, scaler, y_pred, y_te, train_accs, accuracy_score(y_te, y_pred)


def _train_mlp_sequence(X_seq, y, class_names, verbose=True):
    n_samples, n_steps, n_features = X_seq.shape
    X_flat = X_seq.reshape(n_samples, n_steps * n_features).astype(np.float32)

    X_tr, X_te, y_tr, y_te = train_test_split(X_flat, y, test_size=0.2, stratify=y, random_state=42)

    pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", MLPClassifier(
            hidden_layer_sizes=(512, 256, 128, 64), activation="relu",
            max_iter=300, early_stopping=True, validation_fraction=0.1, random_state=42,
        ))
    ])

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_scores = cross_val_score(pipe, X_tr, y_tr, cv=cv, scoring="accuracy")
    if verbose:
        print(f"      5-Fold CV  acc={cv_scores.mean():.4f} (+/-{cv_scores.std():.4f})")

    pipe.fit(X_tr, y_tr)
    y_pred = pipe.predict(X_te)
    loss_curve = pipe.named_steps["clf"].loss_curve_

    return pipe, y_pred, y_te, loss_curve, accuracy_score(y_te, y_pred)


def run_sequence_model(game_name: str, verbose: bool = True) -> dict:
    OUTPUTS = get_game_outputs(game_name)

    if verbose:
        backend = "BiLSTM" if HAS_TORCH else "Deep MLP"
        print(f"\n  --- Sequence Model ({backend}): {game_name.upper()} ---")

    df_raw = load_raw(game_name)
    X_seq, y, pids, le = build_sequence_tensor(df_raw, game_name, n_sessions=50)
    class_names = le.classes_.tolist()
    n_samples, n_steps, n_features = X_seq.shape

    if verbose:
        print(f"    Tensor: ({n_samples}, {n_steps}, {n_features})")

    results = {}

    if HAS_TORCH:
        lstm_model, scaler, y_pred, y_te, train_curve, test_acc = _train_lstm(X_seq, y, class_names, verbose)
        results["BiLSTM"] = test_acc
        torch.save(lstm_model.state_dict(), os.path.join(OUTPUTS, "sequence_lstm.pt"))

        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(range(1, len(train_curve) + 1), train_curve, color="#4C72B0", linewidth=2)
        ax.set_xlabel("Epoch"); ax.set_ylabel("Training Accuracy")
        ax.set_title(f"{game_name.upper()} - LSTM Training Curve", fontsize=13, weight="bold")
        ax.grid(alpha=0.3); plt.tight_layout()
        plt.savefig(os.path.join(OUTPUTS, "sequence_lstm_curve.png"), dpi=150); plt.close()
    else:
        mlp_pipe, y_pred, y_te, loss_curve, test_acc = _train_mlp_sequence(X_seq, y, class_names, verbose)
        results["Deep MLP"] = test_acc
        joblib.dump(mlp_pipe, os.path.join(OUTPUTS, "sequence_model.pkl"))

        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(loss_curve, color="#4C72B0", linewidth=2)
        ax.set_xlabel("Iteration"); ax.set_ylabel("Loss")
        ax.set_title(f"{game_name.upper()} - MLP Loss Curve", fontsize=13, weight="bold")
        ax.grid(alpha=0.3); plt.tight_layout()
        plt.savefig(os.path.join(OUTPUTS, "sequence_loss_curve.png"), dpi=150); plt.close()

    if verbose:
        print(f"    Test Accuracy: {test_acc:.4f}")
        print(classification_report(y_te, y_pred, target_names=class_names))

    # Confusion matrix
    cm = confusion_matrix(y_te, y_pred)
    fig, ax = plt.subplots(figsize=(8, 6))
    ConfusionMatrixDisplay(confusion_matrix=cm, display_labels=class_names).plot(ax=ax, cmap="Blues", colorbar=False)
    ax.set_title(f"{game_name.upper()} - Sequence Confusion Matrix", fontsize=13, weight="bold")
    plt.xticks(rotation=30, ha="right", fontsize=9); plt.yticks(fontsize=9)
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUTS, "sequence_confusion_matrix.png"), dpi=150); plt.close()

    if verbose:
        print(f"    [OK] Saved to {OUTPUTS}/")

    return {"game_name": game_name, "test_acc": test_acc, "cv_results": results}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", default="gonogo")
    args = parser.parse_args()
    run_sequence_model(args.game)

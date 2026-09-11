"""
Multi-Game ADHD Analytics Dashboard (Plotly Dash)
==================================================
Run with:
    python multigame_pipeline/dashboard/dash_app.py
"""

import os
import sys
import warnings
import functools

import joblib
import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")

from dash import Dash, html, dcc, callback, Input, Output

warnings.filterwarnings("ignore")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── Constants ─────────────────────────────────────────────────────────────────
OUTPUTS = os.path.join(_HERE, "..", "outputs")
GAME_NAMES = ["gonogo", "memory", "tracking", "shapes", "puzzle"]
GAME_LABELS = {
    "gonogo": "Go/No-Go", "memory": "Memory Match",
    "tracking": "Visual Tracking", "shapes": "Shape Sorting", "puzzle": "Puzzle",
}
PROFILE_COLORS = {
    "Optimal / Neurotypical": "#4ade80", "Inattentive ADHD": "#fbbf24",
    "Hyperactive-Impulsive ADHD": "#f87171", "Combined ADHD": "#c084fc",
}
GAME_COLORS = {
    "gonogo": "#FF6B6B", "memory": "#4ECDC4",
    "tracking": "#45B7D1", "shapes": "#FFA07A", "puzzle": "#DDA0DD",
}

PAGE_OPTIONS = [
    {"label": "📊  Population Overview", "value": "population"},
    {"label": "🎮  Per-Game Explorer", "value": "per_game"},
    {"label": "🔍  Participant Explorer", "value": "participant"},
    {"label": "🧪  Invisible Assessment", "value": "assessment"},
    {"label": "⚠️  Anomaly Monitor", "value": "anomaly"},
    {"label": "🌐  Cross-Game Analysis", "value": "cross_game"},
    {"label": "📈  Model Comparison", "value": "comparison"},
    {"label": "📡  MLOps Monitor", "value": "mlops_monitor"},
]


# ── Data loaders (cached with lru_cache) ──────────────────────────────────────
@functools.lru_cache(maxsize=16)
def load_game_data(g):
    from multigame_pipeline.preprocessor import load_raw
    from multigame_pipeline.feature_engineer import add_derived_features
    return add_derived_features(load_raw(g), g)


def load_cross():
    from mlops.db import load_cross_game_results_df
    df = load_cross_game_results_df()
    return df if len(df) > 0 else None


@functools.lru_cache(maxsize=16)
def load_flags(g):
    from mlops.db import load_anomaly_flags_df
    df = load_anomaly_flags_df(g)
    return df if len(df) > 0 else None


@functools.lru_cache(maxsize=16)
def load_clf(g):
    p = os.path.join(OUTPUTS, g, "classifier_ensemble.pkl")
    return joblib.load(p) if os.path.exists(p) else None


@functools.lru_cache(maxsize=16)
def load_assess(g):
    p = os.path.join(OUTPUTS, g, "invisible_assessment_model.pkl")
    return joblib.load(p) if os.path.exists(p) else None


def mlops_runs():
    try:
        from mlops.tracking import get_experiment_runs
        return get_experiment_runs(), None
    except Exception as e:
        return [], str(e)


def mlops_registry():
    try:
        from mlops.registry import get_registry_summary
        return get_registry_summary(), None
    except Exception as e:
        return [], str(e)


def mlops_drift():
    try:
        from mlops.drift_monitor import check_all_games
        return check_all_games(), None
    except Exception as e:
        return [], str(e)


def mlops_incoming():
    try:
        from mlops.aggregator import count_all_incoming
        from mlops.config import ensure_dirs
        ensure_dirs()
        return count_all_incoming(), None
    except Exception as e:
        return {}, str(e)


class BayesianProfileUpdater:
    def __init__(self, cn):
        self.class_names = cn
        self.posterior = np.ones(len(cn)) / len(cn)
        self.history = []

    def update(self, lik):
        p = self.posterior * lik
        p /= (p.sum() + 1e-12)
        self.posterior = p
        self.history.append(p.copy())


# ── Helpers ───────────────────────────────────────────────────────────────────
def _slope(y):
    if len(y) < 2 or np.std(y) < 1e-9:
        return 0.0
    return np.polyfit(np.arange(len(y), dtype=float), y, 1)[0]


def _arrow(s, lb=False):
    if abs(s) < 1e-5:
        return "~", "#94a3b8", "Stable"
    if lb:
        return ("↓", "#4ade80", "Improving") if s < 0 else ("↑", "#f87171", "Worsening")
    return ("↑", "#4ade80", "Improving") if s > 0 else ("↓", "#f87171", "Worsening")


# ── Dash App ──────────────────────────────────────────────────────────────────
app = Dash(
    __name__,
    suppress_callback_exceptions=True,
    title="NeuroGames — Multi-Game ADHD Dashboard",
    update_title=None,
    assets_folder=os.path.join(_HERE, "assets"),
)


# ── Layout ────────────────────────────────────────────────────────────────────
app.layout = html.Div([
    dcc.Location(id="url", refresh=False),

    # ── Sidebar ───────────────────────────────────────────────────────────
    html.Div([
        # Brand
        html.Div([
            html.Div([
                html.Div("🧠", className="sidebar-logo-icon"),
                html.Div([
                    html.Div("NeuroGames", className="sidebar-title"),
                    html.Div("ADHD Analytics Dashboard", className="sidebar-subtitle"),
                ]),
            ], className="sidebar-logo"),
        ], className="sidebar-brand"),

        # Game selector
        html.Div([
            html.Span("Active Game", className="game-selector-label"),
            dcc.Dropdown(
                id="game-selector",
                options=[{"label": GAME_LABELS[g], "value": g} for g in GAME_NAMES],
                value="gonogo",
                clearable=False,
            ),
        ], className="sidebar-section"),

        html.Hr(className="sidebar-divider"),

        # Navigation
        html.Div([
            html.Div("Pages", className="nav-section-label"),
            html.Div(id="nav-links"),
        ], className="sidebar-section"),

        # Footer
        html.Div([
            html.Div([
                html.B("5"), html.Span(" games · "),
                html.B("400"), html.Span(" participants"), html.Br(),
                html.B("50"), html.Span(" sessions / game"), html.Br(),
                html.B("4"), html.Span(" ADHD profiles"),
            ], className="sidebar-stats"),
            html.Div("NeuroGames v2.0 · Dash", className="sidebar-version"),
        ], className="sidebar-footer"),
    ], className="sidebar"),

    # ── Main content ──────────────────────────────────────────────────────
    html.Div(id="page-content", className="main-content"),

], className="app-container")


# ── Navigation callback ──────────────────────────────────────────────────────
@callback(
    Output("nav-links", "children"),
    Input("url", "pathname"),
)
def update_nav(pathname):
    current = (pathname or "/").strip("/") or "population"
    links = []
    for opt in PAGE_OPTIONS:
        cls = "nav-link active" if opt["value"] == current else "nav-link"
        links.append(
            dcc.Link(opt["label"], href=f"/{opt['value']}", className=cls)
        )
    return links


# ── Page routing callback ────────────────────────────────────────────────────
@callback(
    Output("page-content", "children"),
    Input("url", "pathname"),
    Input("game-selector", "value"),
)
def render_page(pathname, game):
    from multigame_pipeline.dashboard.components import page_header

    page = (pathname or "/").strip("/") or "population"

    from multigame_pipeline.dashboard.pages import (
        population, per_game, participant, assessment,
        anomaly, cross_game, comparison, mlops_monitor,
    )

    page_map = {
        "population": population.layout,
        "per_game": per_game.layout,
        "participant": participant.layout,
        "assessment": assessment.layout,
        "anomaly": anomaly.layout,
        "cross_game": cross_game.layout,
        "comparison": comparison.layout,
        "mlops_monitor": mlops_monitor.layout,
    }

    layout_fn = page_map.get(page, population.layout)
    try:
        return layout_fn(game)
    except Exception as e:
        return html.Div([
            page_header("❌", "Error", f"Could not render page '{page}'"),
            html.Pre(str(e), style={"color": "#f87171", "padding": "16px"}),
        ])


# ── Entry point ──────────────────────────────────────────────────────────────
server = app.server  # Expose Flask server for testing

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=8050)

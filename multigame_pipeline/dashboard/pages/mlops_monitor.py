"""MLOps Monitor page."""

import pandas as pd
from dash import html, dcc

from multigame_pipeline.dashboard.dash_app import (
    mlops_runs, mlops_registry, mlops_drift, mlops_incoming,
    GAME_NAMES, GAME_LABELS,
)
from multigame_pipeline.dashboard.components import (
    page_header, metric_card, section_divider, info_box, about_box, data_table,
)


def layout(game):
    children = [
        page_header("📡", "MLOps Monitor",
                     "Live view of MLflow experiment runs, model registry, data drift status, and incoming data queue."),
        about_box([
            html.B("What: "), "Operational dashboard for the MLOps pipeline. ",
            html.B("Components: "), "MLflow experiment tracking, model registry, Evidently drift monitor, "
            "and incoming-data aggregator. ",
            html.B("How to read: "), "Champion = promoted model version. Drift = statistical shift vs training data.",
        ]),
    ]

    # ── Incoming Data Queue ─────────────────────────────────────────────────
    children.append(section_divider("Incoming Data Queue"))
    inc, inc_err = mlops_incoming()
    if inc_err:
        children.append(info_box(f"Could not read incoming queue: {inc_err}"))
    elif inc:
        cards = [metric_card(GAME_LABELS[g], inc.get(g, 0), "sessions") for g in GAME_NAMES]
        children.append(html.Div(cards, className="metrics-row"))
    else:
        children.append(info_box("No untrained sessions found in PostgreSQL."))


    # ── Model Registry ────────────────────────────────────────────────────
    children.append(section_divider("Registered Models"))
    reg, reg_err = mlops_registry()
    if reg_err:
        children.append(info_box(f"Registry not available: {reg_err}"))
    elif reg:
        reg_df = pd.DataFrame(reg)
        children.append(data_table(reg_df, "registry-table", precision=4))
    else:
        children.append(info_box("No registered models found."))

    children.append(section_divider("Recent MLflow Runs"))
    runs, runs_err = mlops_runs()
    if runs_err:
        children.append(info_box(f"MLflow runs not available: {runs_err}"))
    elif runs:
        runs_df = pd.DataFrame(runs)
        display_cols = [
            ("tags.mlflow.runName", "run_name"),
            ("tags.game", "game"),
            ("tags.module", "module"),
            ("params.selected_model_name", "selected_model"),
            ("metrics.ensemble_test_acc", "test_accuracy"),
            ("metrics.selected_f1_macro", "macro_f1"),
            ("metrics.selected_model_cv_acc", "cv_accuracy"),
            ("metrics.selected_model_cv_f1_macro", "cv_macro_f1"),
            ("metrics.baseline_lift_f1", "baseline_lift_f1"),
            ("run_id", "run_id"),
        ]
        available = [
            pd.Series(runs_df[source], name=label)
            for source, label in display_cols
            if source in runs_df.columns
        ]
        if available:
            display_df = pd.concat(available, axis=1).head(20)
            children.append(data_table(display_df, "mlflow-runs-table", precision=4))
        else:
            children.append(data_table(runs_df.head(20), "mlflow-runs-table", precision=4))
    else:
        children.append(info_box("No MLflow runs found."))

    # ── Data Drift Status ─────────────────────────────────────────────────
    children.append(section_divider("Data Drift Status"))
    drift, drift_err = mlops_drift()
    if drift_err:
        children.append(info_box(f"Drift monitor unavailable: {drift_err}"))
    elif drift:
        cards = []
        for r in drift:
            game_name = r.get("game", "?")
            detected = r.get("drift_detected", False)
            n_d, n_t = r.get("n_drifted", 0), r.get("n_total", 0)
            status_str = "🟥 DRIFT" if detected else "🟩 STABLE"
            cards.append(metric_card(GAME_LABELS.get(game_name, game_name), status_str, f"{n_d}/{n_t} features"))
        children.append(html.Div(cards, className="metrics-row"))
    else:
        children.append(info_box("No drift data available. Seed or ingest sessions, then run the drift pipeline."))

    # ── Pipeline Run Info ──────────────────────────────────────────────────
    children.append(section_divider("Run Pipeline"))
    children.append(html.P("Re-run the full multi-game pipeline (all 5 games, all 4 modules) "
                           "from the command line:",
                           style={"color": "#8b9dc3", "fontSize": "13px", "marginBottom": "8px"}))
    children.append(html.Code(
        "python multigame_pipeline/run_pipeline.py",
        style={"background": "#141418", "color": "#4ade80", "padding": "10px 18px",
               "borderRadius": "8px", "fontSize": "13px", "display": "block",
               "border": "1px solid rgba(40,40,47,0.7)", "fontFamily": "ui-monospace, 'Cascadia Code', monospace"},
    ))

    return html.Div(children)

"""Model Comparison page."""

import os
import numpy as np
import pandas as pd
from dash import html, dcc
import plotly.graph_objects as go
import plotly.express as px
import plotly.figure_factory as ff

from multigame_pipeline.dashboard.dash_app import (
    load_clf, GAME_NAMES, GAME_LABELS, GAME_COLORS, OUTPUTS,
)
from multigame_pipeline.dashboard.components import (
    page_header, metric_card, section_divider, info_box, about_box, data_table,
)
from multigame_pipeline.dashboard.theme import apply_theme


def _build_confusion_matrix(clf_artifact, game):
    """Recompute confusion matrix from the saved model, or return None."""
    try:
        from multigame_pipeline.preprocessor import load_and_encode
        from multigame_pipeline.feature_engineer import build_participant_aggregate, get_aggregate_feature_cols
        from sklearn.preprocessing import LabelEncoder
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import confusion_matrix

        model = clf_artifact["model"]
        feature_cols = clf_artifact["feature_cols"]
        le = clf_artifact["label_encoder"]
        class_names = clf_artifact["class_names"]

        df_raw, _, _ = load_and_encode(game)
        agg_df = build_participant_aggregate(df_raw, game, n_sessions=None)
        agg_df["y"] = le.transform(agg_df["cluster"])

        for col in ["Age_Group", "Cognitive_Level"]:
            if col in agg_df.columns:
                le_c = LabelEncoder()
                agg_df[f"{col}_enc"] = le_c.fit_transform(agg_df[col].astype(str))

        avail = [c for c in feature_cols if c in agg_df.columns]
        X = agg_df[avail].values.astype(np.float32)
        y = agg_df["y"].values

        _, X_test, _, y_test = train_test_split(X, y, test_size=0.2, stratify=y, random_state=42)
        y_pred = model.predict(X_test)
        cm = confusion_matrix(y_test, y_pred)
        return cm, class_names
    except Exception:
        return None, None


def _get_feature_importance(clf_artifact):
    """Extract RF feature importances from the saved ensemble."""
    try:
        model = clf_artifact["model"]
        feature_cols = clf_artifact["feature_cols"]
        # VotingClassifier: first estimator is RF pipeline
        rf_pipeline = model.estimators_[0]
        rf_model = rf_pipeline.named_steps["clf"]
        importances = rf_model.feature_importances_
        idx = np.argsort(importances)[::-1][:20]
        top_features = [feature_cols[i] for i in idx]
        top_imp = importances[idx]
        return top_features, top_imp
    except Exception:
        return None, None


def layout(game):
    children = [
        page_header("📈", "Model Comparison",
                     "Performance summary across all pipeline modules and all games."),
        about_box([
            html.B("What: "), "Side-by-side accuracy comparison for all games. ",
            html.B("ML module: "), "Classification ensemble (Random Forest + XGBoost + MLP). ",
            html.B("How to read: "), "Higher bars = better accuracy. "
            "Confusion matrices show per-class classification quality. "
            "Feature importance reveals the most discriminative behavioural features.",
        ]),
    ]

    # Collect results
    results = []
    clf_artifacts = {}
    for g in GAME_NAMES:
        clf = load_clf(g)
        if clf:
            results.append({"Game": GAME_LABELS[g], "Module": "Classification",
                             "Accuracy": clf.get("ensemble_test_acc", 0), "game_key": g})
            clf_artifacts[g] = clf

    if not results:
        children.append(html.Div("No model results found. Run the pipeline first.",
                                 style={"color": "#f87171", "padding": "16px"}))
        return html.Div(children)

    comp_df = pd.DataFrame(results)

    # ── Accuracy chart ────────────────────────────────────────────────────
    children.append(section_divider("Classification Accuracy by Game"))
    fig = px.bar(comp_df, x="Game", y="Accuracy", color="Game",
                 color_discrete_sequence=[GAME_COLORS[g] for g in GAME_NAMES],
                 height=350, text_auto=".3f")
    apply_theme(fig, yaxis=dict(range=[0, 1.1], title="Test Accuracy"), showlegend=False)
    fig.update_traces(textposition="outside")
    children.append(dcc.Graph(figure=fig))

    # Results table
    children.append(section_divider("Detailed Results"))
    display_df = comp_df[["Game", "Module", "Accuracy"]]
    children.append(data_table(display_df, "comparison-table", precision=4))

    # ── Per-Game Confusion Matrices & Feature Importance (native Plotly) ─
    children.append(section_divider("Per-Game Model Diagnostics"))

    for g in GAME_NAMES:
        if g not in clf_artifacts:
            continue

        clf_art = clf_artifacts[g]
        children.append(html.H4(f"🎮 {GAME_LABELS[g]}",
                                style={"color": "#e2e8f0", "marginTop": "24px", "marginBottom": "14px",
                                       "fontSize": "15px", "fontWeight": "600"}))

        charts_row = []

        # Confusion Matrix
        cm, class_names = _build_confusion_matrix(clf_art, g)
        if cm is not None:
            # Normalize for display
            cm_norm = cm.astype(float)
            row_sums = cm_norm.sum(axis=1, keepdims=True)
            row_sums[row_sums == 0] = 1
            cm_pct = cm_norm / row_sums

            short_names = [n.replace("Optimal / Neurotypical", "Neuro.").replace("Inattentive ADHD", "Inatt.")
                           .replace("Hyperactive-Impulsive ADHD", "Hyper.").replace("Combined ADHD", "Comb.")
                           for n in class_names]

            # Show counts with percentage in annotations
            annotations = [[f"{cm[i][j]}\n({cm_pct[i][j]:.0%})" for j in range(len(class_names))]
                           for i in range(len(class_names))]

            fig_cm = ff.create_annotated_heatmap(
                z=cm_pct.tolist(), x=short_names, y=short_names,
                annotation_text=annotations,
                colorscale="Blues", zmin=0, zmax=1, showscale=True,
            )
            fig_cm.update_layout(
                xaxis_title="Predicted", yaxis_title="True",
                yaxis=dict(autorange="reversed"),
            )
            apply_theme(fig_cm, height=320, margin=dict(l=80, r=20, t=10, b=60))
            charts_row.append(html.Div([
                dcc.Graph(figure=fig_cm),
                html.P("Confusion Matrix (normalised)", style={"color": "#64748b", "fontSize": "11px",
                                                                "textAlign": "center", "marginTop": "4px"}),
            ], style={"flex": "1", "minWidth": "0"}))

        # Feature Importance
        top_features, top_imp = _get_feature_importance(clf_art)
        if top_features is not None:
            clean_names = [f.replace("_", " ")[:25] for f in top_features]
            fig_fi = go.Figure(go.Bar(
                x=top_imp[::-1], y=clean_names[::-1], orientation="h",
                marker=dict(
                    color=top_imp[::-1],
                    colorscale="Viridis", showscale=False,
                ),
                hovertemplate="%{y}<br>Importance: %{x:.4f}<extra></extra>",
            ))
            apply_theme(fig_fi, height=320, margin=dict(l=160, r=20, t=10, b=40),
                        xaxis=dict(title="Feature Importance (Gini)"))
            charts_row.append(html.Div([
                dcc.Graph(figure=fig_fi),
                html.P("Top 20 Features (RF)", style={"color": "#64748b", "fontSize": "11px",
                                                       "textAlign": "center", "marginTop": "4px"}),
            ], style={"flex": "1", "minWidth": "0"}))

        if charts_row:
            children.append(html.Div(charts_row, style={
                "display": "flex", "gap": "16px", "flexWrap": "wrap",
            }))
        else:
            children.append(info_box(f"Could not generate diagnostics for {GAME_LABELS[g]}."))

        children.append(html.Hr(style={"borderColor": "rgba(40,40,47,0.7)", "margin": "20px 0"}))

    return html.Div(children)

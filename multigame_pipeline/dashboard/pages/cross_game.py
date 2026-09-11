"""Cross-Game Analysis page."""

import numpy as np
from dash import html, dcc
import plotly.graph_objects as go
import plotly.figure_factory as ff

from multigame_pipeline.dashboard.dash_app import (
    load_cross, GAME_NAMES, GAME_LABELS, GAME_COLORS, PROFILE_COLORS,
)
from multigame_pipeline.dashboard.components import (
    page_header, metric_card, section_divider, info_box, about_box, data_table,
)
from multigame_pipeline.dashboard.theme import apply_theme


def _load_per_game_predictions():
    """Load stored classifier predictions for each game."""
    from mlops.db import load_classification_predictions_df

    preds = {}
    for g in GAME_NAMES:
        df = load_classification_predictions_df(g)
        if len(df) > 0:
            preds[g] = df
    return preds


def layout(game):
    children = [
        page_header("🌐", "Cross-Game Analysis",
                     "Profile consistency, prediction agreement, and majority voting across all 5 games."),
        about_box([
            html.B("What: "), "Compares ADHD-profile predictions across all 5 games for each participant. ",
            html.B("ML module: "), "Cross-game ensembler (majority voting). ",
            html.B("How to read: "), "Full consistency = all 5 game classifiers agree on the profile. "
            "Higher majority-vote accuracy indicates robust cross-game signal.",
        ]),
    ]

    cross_df = load_cross()
    if cross_df is None:
        children.append(html.Div("Cross-game predictions not found. Run the full pipeline first.",
                                 style={"color": "#f87171", "padding": "16px"}))
        return html.Div(children)

    n_p = len(cross_df)
    consist = cross_df["all_agree"].mean() if "all_agree" in cross_df.columns else 0
    avg_agr = cross_df["agreement_ratio"].mean() if "agreement_ratio" in cross_df.columns else 0
    maj_acc = (cross_df["majority_pred"] == cross_df["cluster"]).mean() if "majority_pred" in cross_df.columns else 0

    children.append(html.Div([
        metric_card("Participants", n_p),
        metric_card("Full Consistency", f"{consist:.1%}", "All 5 games agree"),
        metric_card("Avg Agreement", f"{avg_agr:.1%}"),
        metric_card("Majority Vote Acc", f"{maj_acc:.1%}"),
    ], className="metrics-row"))

    # ── Per-Game Classification Accuracy (native Plotly) ──────────────────
    children.append(section_divider("Per-Game Classification Accuracy"))
    game_preds = _load_per_game_predictions()

    if game_preds:
        game_accs = {}
        for g in GAME_NAMES:
            if g in game_preds:
                gdf = game_preds[g]
                game_accs[g] = (gdf["predicted_cluster"] == gdf["cluster"]).mean()

        # Add majority vote
        if "majority_pred" in cross_df.columns:
            game_accs["majority_vote"] = float(maj_acc)

        names = [GAME_LABELS.get(g, g.title()) for g in game_accs]
        vals = list(game_accs.values())
        colors = [GAME_COLORS.get(g, "#6366f1") for g in game_accs]

        fig = go.Figure(go.Bar(
            x=names, y=vals, marker_color=colors,
            text=[f"{v:.3f}" for v in vals], textposition="outside",
            hovertemplate="%{x}<br>Accuracy: %{y:.4f}<extra></extra>",
        ))
        apply_theme(fig, height=350, showlegend=False,
                    yaxis=dict(range=[0, 1.1], title="Accuracy"))
        children.append(dcc.Graph(figure=fig))
    else:
        children.append(info_box("Per-game predictions not found. Run the pipeline first."))

    # ── Cross-Game Consistency Heatmap (native Plotly) ────────────────────
    children.append(section_divider("Cross-Game Profile Agreement Heatmap"))
    pred_cols = [c for c in cross_df.columns if c.startswith("pred_")]
    available_games = [c.replace("pred_", "") for c in pred_cols]

    if len(available_games) >= 2:
        n_g = len(available_games)
        agree_matrix = np.zeros((n_g, n_g))
        for i, g1 in enumerate(available_games):
            for j, g2 in enumerate(available_games):
                agree_matrix[i, j] = (cross_df[f"pred_{g1}"] == cross_df[f"pred_{g2}"]).mean()

        labels = [GAME_LABELS.get(g, g.upper()) for g in available_games]
        fig = ff.create_annotated_heatmap(
            z=np.round(agree_matrix, 3), x=labels, y=labels,
            colorscale="YlGnBu", zmin=0.3, zmax=1, showscale=True,
            hovertemplate="%{y} vs %{x}<br>Agreement: %{z:.3f}<extra></extra>",
        )
        apply_theme(fig, height=400, margin=dict(l=120, r=20, t=10, b=80))
        children.append(dcc.Graph(figure=fig))
    else:
        children.append(info_box("Not enough games for heatmap."))

    # ── Consistency by ADHD Profile (native Plotly) ──────────────────────
    children.append(section_divider("Consistency by ADHD Profile"))
    if "agreement_ratio" in cross_df.columns:
        cluster_consist = cross_df.groupby("cluster")["agreement_ratio"].mean().reset_index()
        cluster_consist = cluster_consist.sort_values("agreement_ratio", ascending=False)

        fig = go.Figure(go.Bar(
            x=cluster_consist["cluster"], y=cluster_consist["agreement_ratio"],
            marker_color=[PROFILE_COLORS.get(c, "#6366f1") for c in cluster_consist["cluster"]],
            text=[f"{v:.2f}" for v in cluster_consist["agreement_ratio"]],
            textposition="outside",
            hovertemplate="%{x}<br>Avg Agreement: %{y:.3f}<extra></extra>",
        ))
        apply_theme(fig, height=320, showlegend=False,
                    yaxis=dict(range=[0, 1.1], title="Avg Agreement Ratio"))
        children.append(dcc.Graph(figure=fig))
    else:
        children.append(info_box("Agreement ratio column not found."))

    # Composite data table
    children.append(section_divider("Composite Predictions Data"))
    children.append(data_table(cross_df, "cross-game-table", precision=3))

    return html.Div(children)

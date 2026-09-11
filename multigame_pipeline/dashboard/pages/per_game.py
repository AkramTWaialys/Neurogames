"""Per-Game Explorer page."""

import numpy as np
import pandas as pd
from dash import html, dcc
import plotly.graph_objects as go
import plotly.figure_factory as ff
from plotly.subplots import make_subplots

from multigame_pipeline.dashboard.dash_app import (
    load_game_data, GAME_LABELS, PROFILE_COLORS,
)
from multigame_pipeline.dashboard.components import (
    page_header, metric_card, section_divider, about_box, data_table,
)
from multigame_pipeline.dashboard.theme import apply_theme


def layout(game):
    children = [
        page_header("🎮", "Per-Game Explorer",
                     "Detailed statistics, game-specific metrics, and feature correlations for each game."),
        about_box([
            html.B("What: "), "Deep dive into one game's data. ",
            html.B("ML module: "), "Preprocessor + game-specific feature extraction. ",
            html.B("How to read: "), "Box plots show ADHD-profile distributions. "
            "The heatmap reveals feature correlations within the selected game.",
        ]),
    ]

    try:
        df = load_game_data(game)
    except Exception as e:
        children.append(html.Div(f"Could not load {game}: {e}", style={"color": "#f87171"}))
        return html.Div(children)

    n_sessions = len(df)
    n_pids = df["Participant_ID"].nunique()
    acc = df["Correct_Responses"].sum() / max(1, df["Total_Actions"].sum())

    children.append(html.Div([
        metric_card("Sessions", f"{n_sessions:,}"),
        metric_card("Participants", n_pids),
        metric_card("Accuracy", f"{acc * 100:.1f}%"),
        metric_card("Avg RT", f"{df['Reaction_Time'].mean():.2f}s"),
    ], className="metrics-row"))

    # Core feature box plots
    children.append(section_divider("Core Feature Distributions by Profile"))
    metrics = ["Reaction_Time", "Correct_Responses", "Time_Spent"]
    fig = make_subplots(rows=1, cols=3, subplot_titles=[m.replace("_", " ") for m in metrics])
    for i, m in enumerate(metrics, 1):
        for cls in df["cluster"].unique():
            fig.add_trace(go.Box(y=df[df["cluster"] == cls][m], name=cls,
                                 marker_color=PROFILE_COLORS.get(cls, "#fff"), showlegend=(i == 1)), row=1, col=i)
    apply_theme(fig, height=350, legend=dict(orientation="h", y=1.15))
    children.append(dcc.Graph(figure=fig))

    # Game-specific metrics
    from multigame_pipeline.preprocessor import GAME_EXTRA_FEATURES
    extras = GAME_EXTRA_FEATURES.get(game, [])
    avail_extras = [c for c in extras if c in df.columns and df[c].dtype != object]

    if avail_extras:
        children.append(section_divider(f"{GAME_LABELS[game]} — Game-Specific Metrics by Profile"))
        extra_means = df.groupby("cluster")[avail_extras].mean().reset_index()
        children.append(data_table(extra_means, "extras-table", precision=3))

        if len(avail_extras) >= 2:
            plot_extras = avail_extras[:4]
            fig = make_subplots(rows=1, cols=len(plot_extras),
                                subplot_titles=[e.replace("_", " ") for e in plot_extras])
            for i, col_name in enumerate(plot_extras, 1):
                for cls in df["cluster"].unique():
                    fig.add_trace(go.Box(y=df[df["cluster"] == cls][col_name], name=cls,
                                         marker_color=PROFILE_COLORS.get(cls, "#fff"), showlegend=(i == 1)),
                                 row=1, col=i)
            apply_theme(fig, height=350, legend=dict(orientation="h", y=1.15))
            children.append(dcc.Graph(figure=fig))

    # Correlation
    children.append(section_divider("Feature Correlation Matrix"))
    core = ["Time_Spent", "Total_Actions", "Correct_Responses", "Incorrect_Responses", "Reaction_Time"]
    all_corr = core + avail_extras
    all_corr = [c for c in all_corr if c in df.columns]
    corr = df[all_corr].corr(method="spearman")
    labels = [c.replace("_", " ")[:18] for c in corr.columns]
    fig = ff.create_annotated_heatmap(z=np.round(corr.values[::-1], 2), x=labels, y=labels[::-1],
                                      colorscale="RdBu_r", zmin=-1, zmax=1, showscale=True)
    apply_theme(fig, height=500, margin=dict(l=150, r=20, t=20, b=100))
    children.append(dcc.Graph(figure=fig))

    return html.Div(children)

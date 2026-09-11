"""Population Overview page."""

import numpy as np
import pandas as pd
from dash import html, dcc
import plotly.graph_objects as go
import plotly.express as px
import plotly.figure_factory as ff
from plotly.subplots import make_subplots

from multigame_pipeline.dashboard.dash_app import (
    load_game_data, GAME_NAMES, GAME_LABELS, GAME_COLORS, PROFILE_COLORS, OUTPUTS,
)
from multigame_pipeline.dashboard.components import (
    page_header, metric_card, section_divider, info_box, about_box, data_table,
)
from multigame_pipeline.dashboard.theme import apply_theme


def layout(game):
    children = [
        page_header("📊", "Population Overview",
                     "Global dataset statistics, profile distributions, and feature comparison across all 5 games."),
        about_box([
            html.B("What: "), "Aggregated statistics across all 5 games and 400 participants. ",
            html.B("ML module: "), "Data preprocessor + feature engineering. ",
            html.B("How to read: "), "Profile distributions show ADHD subtypes by game. "
            "Box plots reveal per-profile behavioural patterns across all games.",
        ]),
    ]

    # Load all games
    all_dfs = {}
    for g in GAME_NAMES:
        try:
            all_dfs[g] = load_game_data(g)
        except Exception as e:
            children.append(info_box(f"⚠️ Could not load {GAME_LABELS[g]}: {e}"))

    if not all_dfs:
        children.append(html.Div("No game data found. Run the pipeline first.",
                                 style={"color": "#f87171", "padding": "16px"}))
        return html.Div(children)

    total_sessions = sum(len(d) for d in all_dfs.values())
    total_pids = list(all_dfs.values())[0]["Participant_ID"].nunique()
    avg_acc = np.mean([(d["Correct_Responses"].sum() / max(1, d["Total_Actions"].sum())) for d in all_dfs.values()])
    avg_rt = np.mean([d["Reaction_Time"].mean() for d in all_dfs.values()])

    children.append(html.Div([
        metric_card("Games", len(all_dfs)),
        metric_card("Participants", f"{total_pids:,}", "Shared pool"),
        metric_card("Total Sessions", f"{total_sessions:,}", f"{total_sessions // len(all_dfs):,}/game"),
        metric_card("Avg Accuracy", f"{avg_acc * 100:.1f}%"),
        metric_card("Avg Reaction", f"{avg_rt:.2f}s"),
    ], className="metrics-row"))

    # Per-game performance bars
    children.append(section_divider("Per-Game Performance Comparison"))
    perf = []
    for g, d in all_dfs.items():
        perf.append({"Game": GAME_LABELS[g],
                     "Accuracy": d["Correct_Responses"].sum() / max(1, d["Total_Actions"].sum()),
                     "Avg RT": d["Reaction_Time"].mean()})
    perf_df = pd.DataFrame(perf)
    fig = make_subplots(rows=1, cols=2, subplot_titles=["Accuracy by Game", "Avg Reaction Time"])
    fig.add_trace(go.Bar(x=perf_df["Game"], y=perf_df["Accuracy"],
                         marker_color=[GAME_COLORS[g] for g in all_dfs],
                         text=[f"{a:.1%}" for a in perf_df["Accuracy"]], textposition="outside"), row=1, col=1)
    fig.add_trace(go.Bar(x=perf_df["Game"], y=perf_df["Avg RT"],
                         marker_color=[GAME_COLORS[g] for g in all_dfs],
                         text=[f"{r:.2f}s" for r in perf_df["Avg RT"]], textposition="outside"), row=1, col=2)
    apply_theme(fig, height=350, showlegend=False, margin=dict(l=40, r=20, t=40, b=40))
    fig.update_yaxes(range=[0, 1.1], row=1, col=1)
    children.append(dcc.Graph(figure=fig))

    # Profile distribution
    children.append(section_divider("Sessions by Profile & Game"))
    dist = []
    for g, d in all_dfs.items():
        for cl in d["cluster"].unique():
            dist.append({"Game": GAME_LABELS[g], "Profile": cl, "Sessions": len(d[d["cluster"] == cl])})
    fig = px.bar(pd.DataFrame(dist), x="Game", y="Sessions", color="Profile",
                 color_discrete_map=PROFILE_COLORS, barmode="group", height=320)
    apply_theme(fig, margin=dict(l=40, r=20, t=10, b=40))
    children.append(dcc.Graph(figure=fig))

    # Feature distributions
    children.append(section_divider("Feature Distributions by Profile (All Games Combined)"))
    combined = pd.concat(all_dfs.values(), ignore_index=True)
    metrics = ["Reaction_Time", "Correct_Responses", "Time_Spent"]
    fig = make_subplots(rows=1, cols=3, subplot_titles=[m.replace("_", " ") for m in metrics])
    for i, m in enumerate(metrics, 1):
        for cls in combined["cluster"].unique():
            fig.add_trace(go.Box(y=combined[combined["cluster"] == cls][m], name=cls,
                                 marker_color=PROFILE_COLORS.get(cls, "#fff"), showlegend=(i == 1)), row=1, col=i)
    apply_theme(fig, height=350, legend=dict(orientation="h", y=1.15))
    children.append(dcc.Graph(figure=fig))

    # Means table
    children.append(section_divider("Feature Means by Profile"))
    mean_cols = ["Time_Spent", "Total_Actions", "Correct_Responses", "Reaction_Time"]
    mean_df = combined.groupby("cluster")[mean_cols].mean().reset_index()
    children.append(data_table(mean_df, "pop-means-table"))

    # Correlation
    children.append(section_divider("Spearman Correlations (All Games)"))
    core_cols = ["Time_Spent", "Total_Actions", "Correct_Responses", "Incorrect_Responses",
                 "Touch_Interactions", "Reaction_Time"]
    avail = [c for c in core_cols if c in combined.columns]
    corr = combined[avail].corr(method="spearman")
    labels = [c.replace("_", " ")[:18] for c in corr.columns]
    fig = ff.create_annotated_heatmap(z=np.round(corr.values[::-1], 2), x=labels, y=labels[::-1],
                                      colorscale="RdBu_r", zmin=-1, zmax=1, showscale=True)
    apply_theme(fig, height=450, margin=dict(l=150, r=20, t=20, b=100))
    children.append(dcc.Graph(figure=fig))

    return html.Div(children)

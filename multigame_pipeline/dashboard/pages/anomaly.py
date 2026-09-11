"""Anomaly Monitor page."""

import numpy as np
from dash import html, dcc
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

from multigame_pipeline.dashboard.dash_app import (
    load_game_data, load_flags,
    GAME_COLORS, PROFILE_COLORS,
)
from multigame_pipeline.dashboard.components import (
    page_header, metric_card, section_divider, flag_alert, badge_html,
    info_box, about_box, data_table,
)
from multigame_pipeline.dashboard.theme import apply_theme


def layout(game):
    children = [
        page_header("⚠️", "Anomaly Monitor",
                     "Sessions flagged as at-risk by IsolationForest and Autoencoder detectors."),
        about_box([
            html.B("What: "), "Anomaly detection across game sessions. ",
            html.B("ML module: "), "IsolationForest + Autoencoder ensemble. ",
            html.B("How to read: "), "Combined flag = flagged by at least one detector. "
            "Higher AE error → more unusual session behaviour.",
        ]),
    ]

    flags_df = load_flags(game)
    if flags_df is None:
        children.append(html.Div(f"Anomaly flags not found for {game}. Run the pipeline first.",
                                 style={"color": "#f87171", "padding": "16px"}))
        return html.Div(children)

    total = len(flags_df)
    flagged = int(flags_df["anomaly_flag"].sum())
    if_flagged = int(flags_df["if_flag"].sum())
    ae_flagged = int(flags_df["ae_flag"].sum())

    children.append(html.Div([
        metric_card("Total Sessions", f"{total:,}"),
        metric_card("Combined Flags", f"{flagged:,}", f"{flagged / total * 100:.1f}%"),
        metric_card("IF Flags", f"{if_flagged:,}"),
        metric_card("AE Flags", f"{ae_flagged:,}"),
    ], className="metrics-row"))

    # Flag rate by cluster
    children.append(section_divider("Flag Rate by ADHD Profile"))
    rate_df = (flags_df.groupby("cluster")["anomaly_flag"].agg(["sum", "count"])
               .assign(rate=lambda d: d["sum"] / d["count"]).reset_index().sort_values("rate", ascending=False))
    fig = px.bar(rate_df, x="cluster", y="rate", color="cluster",
                 color_discrete_map=PROFILE_COLORS, height=300)
    apply_theme(fig, showlegend=False, yaxis=dict(tickformat=".0%", title="Flag Rate"), xaxis_title="")
    children.append(dcc.Graph(figure=fig))

    # ── Score Distribution Plots (native Plotly) ──────────────────────────
    children.append(section_divider("Anomaly Score Distributions"))

    if "if_score" in flags_df.columns and "ae_error" in flags_df.columns:
        fig = make_subplots(rows=1, cols=2,
                            subplot_titles=["Isolation Forest Scores", "Autoencoder Error (log)"],
                            horizontal_spacing=0.1)

        # IF score histogram
        if_scores = flags_df["if_score"].dropna()
        if_threshold = np.percentile(if_scores, 5)  # 5% contamination default
        fig.add_trace(go.Histogram(
            x=if_scores, nbinsx=60, name="IF Score",
            marker_color="#6366f1", opacity=0.85,
            hovertemplate="Score: %{x:.3f}<br>Count: %{y}<extra></extra>",
        ), row=1, col=1)
        fig.add_vline(x=if_threshold, line_dash="dash", line_color="#f87171",
                      annotation_text="Threshold", annotation_position="top right",
                      row=1, col=1)

        # AE error histogram (log scale)
        ae_errors_log = np.log1p(flags_df["ae_error"].dropna())
        fig.add_trace(go.Histogram(
            x=ae_errors_log, nbinsx=60, name="AE Error (log)",
            marker_color="#fbbf24", opacity=0.85,
            hovertemplate="log(1+error): %{x:.3f}<br>Count: %{y}<extra></extra>",
        ), row=1, col=2)

        apply_theme(fig, height=340, showlegend=False,
                    margin=dict(l=40, r=20, t=40, b=40))
        fig.update_xaxes(title_text="Decision Function Score", row=1, col=1)
        fig.update_xaxes(title_text="log(1 + reconstruction error)", row=1, col=2)
        fig.update_yaxes(title_text="Sessions", row=1, col=1)
        children.append(dcc.Graph(figure=fig))
    else:
        children.append(info_box("Score columns (if_score, ae_error) not found in anomaly flags."))

    # Participant anomaly explorer
    children.append(section_divider("Participant Anomaly Timeline"))
    try:
        df = load_game_data(game)
    except Exception:
        children.append(info_box("Could not load game data for participant timeline."))
        return html.Div(children)

    pids = sorted(df["Participant_ID"].unique())
    sel_pid = pids[0] if pids else None
    if not sel_pid:
        return html.Div(children)

    pflag = flags_df[flags_df["Participant_ID"] == sel_pid].reset_index(drop=True)
    pdata = df[df["Participant_ID"] == sel_pid].reset_index(drop=True)
    n_flags = int(pflag["anomaly_flag"].sum())

    children.append(html.Div([
        html.Span("Profile: "), badge_html(pdata["cluster"].iloc[0]),
        html.Span(f"  |  Flagged sessions: {n_flags}"),
    ], style={"marginBottom": "12px", "color": "#e2e8f0"}))

    if len(pflag) == len(pdata):
        colors_pt = ["#f87171" if f else GAME_COLORS[game] for f in pflag["anomaly_flag"]]
        symbols_pt = ["x" if f else "circle" for f in pflag["anomaly_flag"]]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=list(range(1, len(pdata) + 1)), y=pdata["Reaction_Time"],
                                 mode="lines+markers", name="RT",
                                 line=dict(color=GAME_COLORS[game], width=2),
                                 marker=dict(color=colors_pt, symbol=symbols_pt, size=8,
                                            line=dict(width=1, color="white"))))
        fig.add_trace(go.Scatter(x=list(range(1, len(pflag) + 1)), y=pflag["ae_error"],
                                 mode="lines", name="AE Error",
                                 line=dict(color="#fbbf24", width=1.5, dash="dot"), yaxis="y2"))
        apply_theme(fig, height=320, margin=dict(l=40, r=60, t=10, b=40),
                    yaxis=dict(title="Reaction Time"),
                    yaxis2=dict(title="AE Error", overlaying="y", side="right"),
                    xaxis=dict(title="Session #"), legend=dict(orientation="h", y=1.1))
        children.append(dcc.Graph(figure=fig))

    if n_flags:
        children.append(flag_alert(f"⚠️ {n_flags} sessions flagged as at-risk."))

    # Flagged sessions table
    if n_flags and len(pflag) == len(pdata):
        children.append(section_divider("Flagged Session Details"))
        merged = pdata.copy()
        merged["anomaly_flag"] = pflag["anomaly_flag"].values
        merged["ae_error"] = pflag["ae_error"].values
        detail_cols = ["Game_Session_ID", "Reaction_Time", "Total_Actions", "Correct_Responses", "ae_error"]
        if "Hint_Usage" in merged.columns:
            detail_cols.insert(-1, "Hint_Usage")
        flagged_rows = merged[merged["anomaly_flag"] == 1][detail_cols]
        children.append(data_table(flagged_rows, "anomaly-flagged-table", precision=4))

    return html.Div(children)

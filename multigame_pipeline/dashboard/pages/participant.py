"""Participant Explorer page."""

import numpy as np
from dash import html, dcc
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from multigame_pipeline.dashboard.dash_app import (
    load_game_data, load_flags, GAME_COLORS, PROFILE_COLORS,
    _slope, _arrow,
)
from multigame_pipeline.dashboard.components import (
    page_header, metric_card, section_divider, flag_alert, badge_html,
    about_box, data_table,
)
from multigame_pipeline.dashboard.theme import apply_theme


def layout(game):
    children = [
        page_header("🔍", "Participant Explorer",
                     "Individual session timelines, behavioural metrics, progress trends, and raw session data."),
        about_box([
            html.B("What: "), "All sessions for one participant in one game. ",
            html.B("ML module: "), "Anomaly detector flags sessions; trends use linear regression. ",
            html.B("How to read: "), "× markers on the timeline = anomaly-flagged sessions. "
            "The radar chart compares this participant's averages to the population mean.",
        ]),
    ]

    try:
        df = load_game_data(game)
    except Exception as e:
        children.append(html.Div(f"Could not load {game}: {e}", style={"color": "#f87171"}))
        return html.Div(children)

    pids = sorted(df["Participant_ID"].unique())
    selected_pid = pids[0] if pids else None

    if not selected_pid:
        children.append(html.Div("No participants found.", style={"color": "#f87171"}))
        return html.Div(children)

    pdata = df[df["Participant_ID"] == selected_pid].reset_index(drop=True)
    true_label = pdata["cluster"].iloc[0]

    age_str = f"{pdata['Age'].iloc[0]} ans" if "Age" in pdata.columns else pdata["Age_Group"].iloc[0]

    children.append(html.Div([
        html.Span("Profile: "), badge_html(true_label),
        html.Span(f"  |  Age: {age_str} ({pdata['Age_Group'].iloc[0]})  |  "
                   f"Cognitive: {pdata['Cognitive_Level'].iloc[0]}"),
    ], style={"marginBottom": "16px", "color": "#e2e8f0"}))

    # Flags
    flags_df = load_flags(game)
    n_flagged = 0
    if flags_df is not None:
        pflag = flags_df[flags_df["Participant_ID"] == selected_pid]
        if len(pflag) == len(pdata):
            pdata = pdata.copy()
            pdata["flagged"] = pflag["anomaly_flag"].values.astype(bool)
            n_flagged = int(pdata["flagged"].sum())
        else:
            pdata["flagged"] = False
    else:
        pdata["flagged"] = False

    # Metric cards
    acc_val = pdata["Correct_Responses"].sum() / max(1, pdata["Total_Actions"].sum())
    has_hints = "Hint_Usage" in pdata.columns
    cards = [
        metric_card("Sessions", len(pdata)),
        metric_card("Avg RT", f"{pdata['Reaction_Time'].mean():.2f}s"),
        metric_card("Accuracy", f"{acc_val * 100:.1f}%"),
    ]
    if has_hints:
        cards.append(metric_card("Avg Hints", f"{pdata['Hint_Usage'].mean():.1f}"))
    cards.append(metric_card("Flagged", n_flagged))
    children.append(html.Div(cards, className="metrics-row"))

    # Session timeline
    children.append(section_divider("Session Timeline"))
    sessions = list(range(1, len(pdata) + 1))
    colors_pt = ["#f87171" if f else GAME_COLORS[game] for f in pdata["flagged"]]
    symbols_pt = ["x" if f else "circle" for f in pdata["flagged"]]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=sessions, y=pdata["Reaction_Time"], mode="lines+markers",
                             name="Reaction Time", line=dict(color=GAME_COLORS[game], width=2),
                             marker=dict(color=colors_pt, symbol=symbols_pt, size=8)))
    if "accuracy_rate" in pdata.columns:
        fig.add_trace(go.Scatter(x=sessions, y=pdata["accuracy_rate"], mode="lines",
                                 name="Accuracy", line=dict(color="#4ade80", width=2, dash="dot"), yaxis="y2"))
    apply_theme(fig, height=340, margin=dict(l=40, r=40, t=10, b=40),
                legend=dict(orientation="h", y=1.1),
                yaxis=dict(title="RT (s)"),
                yaxis2=dict(title="Accuracy", overlaying="y", side="right", range=[0, 1]),
                xaxis=dict(title="Session #"))
    if n_flagged:
        fig.add_annotation(text="× = anomaly-flagged session", xref="paper", yref="paper",
                           x=0.01, y=0.95, showarrow=False, font=dict(color="#f87171", size=11))
    children.append(dcc.Graph(figure=fig))

    if n_flagged:
        children.append(flag_alert(f"⚠️ {n_flagged} sessions flagged as at-risk for this participant."))

    # Progress trends
    children.append(section_divider("Progress Trends"))
    n_sess = len(pdata)
    acc_s = (pdata["Correct_Responses"] / pdata["Total_Actions"].replace(0, np.nan)).fillna(0).values
    rt_s = pdata["Reaction_Time"].values
    comp_s = (pdata["Game_Completion_Status"] == "Completed").astype(float).values
    sessions_x = np.arange(1, n_sess + 1)

    trend_cards = []
    trend_items = [
        ("Reaction Time", _slope(rt_s), "s/sess", True),
        ("Accuracy", _slope(acc_s), "%/sess", False),
        ("Completion", _slope(comp_s), "%/sess", False),
    ]
    if has_hints:
        hint_s = pdata["Hint_Usage"].values.astype(float)
        trend_items.append(("Hint Usage", _slope(hint_s), "h/sess", True))

    for nm, sl, un, lb in trend_items:
        ar, clr, lbl = _arrow(sl, lb)
        ds = sl * 100 if "%" in un else sl
        trend_cards.append(html.Div([
            html.H3(nm),
            html.H2(f"{ar} {lbl}", style={"color": clr, "fontSize": "20px"}),
            html.P(f"{ds:+.4f} {un}"),
        ], className="metric-card"))

    children.append(html.Div(trend_cards, className="metrics-row"))

    # Rolling averages chart
    w = min(5, n_sess)
    def _roll(a, w):
        return np.convolve(a, np.ones(w) / w, mode="valid")

    roll_x = np.arange(w, n_sess + 1)
    if has_hints:
        fig = make_subplots(rows=2, cols=2, subplot_titles=["Reaction Time", "Accuracy", "Completion", "Hints"],
                            vertical_spacing=0.12, horizontal_spacing=0.08)
        chart_data = [(rt_s, "#4C72B0", 1, 1), (acc_s, "#4ade80", 1, 2),
                      (comp_s, "#fbbf24", 2, 1), (hint_s, "#c084fc", 2, 2)]
        chart_height = 440
    else:
        fig = make_subplots(rows=1, cols=3, subplot_titles=["Reaction Time", "Accuracy", "Completion"],
                            horizontal_spacing=0.08)
        chart_data = [(rt_s, "#4C72B0", 1, 1), (acc_s, "#4ade80", 1, 2),
                      (comp_s, "#fbbf24", 1, 3)]
        chart_height = 340
    for raw, color, r, c in chart_data:
        fig.add_trace(go.Scatter(x=list(sessions_x), y=list(raw), mode="markers",
                                 marker=dict(color=color, size=3, opacity=0.35), showlegend=False), row=r, col=c)
        fig.add_trace(go.Scatter(x=list(roll_x), y=list(_roll(raw, w)), mode="lines",
                                 line=dict(color=color, width=2.5), showlegend=False), row=r, col=c)
        trend_y = np.polyval(np.polyfit(sessions_x, raw, 1), sessions_x)
        fig.add_trace(go.Scatter(x=list(sessions_x), y=list(trend_y), mode="lines",
                                 line=dict(color="white", width=1, dash="dash"), showlegend=False), row=r, col=c)
    apply_theme(fig, height=chart_height, margin=dict(l=40, r=20, t=40, b=30))
    children.append(dcc.Graph(figure=fig))

    # Raw data
    children.append(section_divider("Raw Session Data"))
    show = ["Game_Session_ID", "Time_Spent", "Total_Actions", "Correct_Responses",
            "Reaction_Time", "Hint_Usage", "Game_Completion_Status", "Performance_Level"]
    show_avail = [c for c in show if c in pdata.columns]
    children.append(data_table(pdata[show_avail], "participant-raw-table"))

    # Radar chart
    children.append(section_divider("Profile Radar vs Population"))
    rc = ["Reaction_Time", "Correct_Responses", "Time_Spent", "Hint_Usage", "Total_Actions"]
    avail_r = [c for c in rc if c in df.columns]
    if len(avail_r) >= 3:
        pop_mean = df[avail_r].mean()
        pop_std = df[avail_r].std().replace(0, 1)
        z = ((pdata[avail_r].mean() - pop_mean) / pop_std).clip(-2, 2)
        r_vals = ((z + 2) / 4).values.tolist()
        theta = [c.replace("_", " ") for c in avail_r]
        fig = go.Figure()
        fig.add_trace(go.Scatterpolar(
            r=r_vals + [r_vals[0]], theta=theta + [theta[0]],
            fill="toself", name="Participant",
            line_color=GAME_COLORS[game], fillcolor=GAME_COLORS[game], opacity=0.45,
        ))
        fig.add_trace(go.Scatterpolar(
            r=[0.5] * (len(theta) + 1), theta=theta + [theta[0]],
            fill=None, name="Population Avg",
            line=dict(color="#8b9dc3", dash="dash"),
        ))
        apply_theme(fig, height=360, margin=dict(l=60, r=60, t=30, b=30),
                    polar=dict(radialaxis=dict(
                        visible=True, range=[0, 1],
                        tickvals=[0.25, 0.5, 0.75], ticktext=["Low", "Avg", "High"],
                    )),
                    legend=dict(orientation="h", y=-0.1))
        children.append(dcc.Graph(figure=fig))
        children.append(html.P("Radar shows participant z-score vs population, normalised 0–1. Centre = population average.",
                                style={"color": "#64748b", "fontSize": "12px", "marginTop": "4px"}))

    return html.Div(children)

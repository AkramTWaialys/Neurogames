"""Invisible / Passive ADHD Assessment page."""

import os
import numpy as np
from dash import html, dcc
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from multigame_pipeline.dashboard.dash_app import (
    load_game_data, load_assess, BayesianProfileUpdater,
    GAME_LABELS, OUTPUTS, PROFILE_COLORS,
)
from multigame_pipeline.dashboard.components import (
    page_header, metric_card, section_divider, badge_html, about_box,
)
from multigame_pipeline.dashboard.theme import apply_theme


def _run_convergence_sample(df, model, scaler, le, feature_cols, n_sample=4):
    """Run Bayesian convergence for a sample of participants, return histories.

    Batches predict_proba per participant for performance (single call per participant
    instead of per-session).
    """
    class_names = le.classes_.tolist()
    avail = [c for c in feature_cols if c in df.columns]
    pids = df["Participant_ID"].unique()
    rng = np.random.default_rng(42)
    sample_pids = rng.choice(pids, size=min(n_sample, len(pids)), replace=False)

    results = []
    for pid in sample_pids:
        pdata = df[df["Participant_ID"] == pid].reset_index(drop=True)
        X_p = pdata[avail].fillna(0).values.astype(np.float32)
        try:
            X_sc = scaler.transform(X_p)
        except Exception:
            from sklearn.preprocessing import StandardScaler as _SS
            X_sc = _SS().fit_transform(X_p)

        # Batch predict_proba for all sessions at once (much faster)
        try:
            all_proba = model.predict_proba(X_sc)
        except Exception:
            all_proba = np.ones((len(X_sc), len(class_names))) / len(class_names)

        updater = BayesianProfileUpdater(class_names)
        for i in range(len(all_proba)):
            updater.update(all_proba[i])

        results.append({
            "pid": str(pid),
            "history": np.array(updater.history),
            "true_label": pdata["cluster"].iloc[0],
            "class_names": class_names,
        })
    return results


def layout(game):
    children = [
        page_header("🧪", "Invisible / Passive ADHD Assessment",
                     "Bayesian posterior convergence — watch profile confidence grow session-by-session without explicit testing."),
        about_box([
            html.B("What: "), "Passive ADHD profile detection from gameplay behaviour alone. ",
            html.B("ML module: "), "Gradient Boosting classifier → Bayesian posterior updater. ",
            html.B("How to read: "), "Lines converge toward the true profile as more sessions are observed. "
            "Green label = correct prediction at that checkpoint.",
        ]),
    ]

    art = load_assess(game)
    if art is None:
        children.append(html.Div(f"Assessment model not found for {game}. Run the pipeline first.",
                                 style={"color": "#f87171", "padding": "16px"}))
        return html.Div(children)

    model, scaler, le = art["model"], art["scaler"], art["label_encoder"]
    feature_cols, class_names = art["feature_cols"], art["class_names"]

    try:
        df = load_game_data(game)
    except Exception as e:
        children.append(html.Div(f"Could not load data: {e}", style={"color": "#f87171"}))
        return html.Div(children)

    pids = sorted(df["Participant_ID"].unique())
    selected_pid = pids[0] if pids else None
    if not selected_pid:
        return html.Div(children + [html.P("No participants found.")])

    pdata = df[df["Participant_ID"] == selected_pid].reset_index(drop=True)
    true_label = pdata["cluster"].iloc[0]

    children.append(html.Div([
        html.Span("Profile: "), badge_html(true_label),
        html.Span(f"  |  Game: {GAME_LABELS[game]}  |  Sessions: {len(pdata)}"),
    ], style={"marginBottom": "16px", "color": "#e2e8f0"}))

    # Bayesian update for selected participant
    avail = [c for c in feature_cols if c in pdata.columns]
    X_p = pdata[avail].fillna(0).values.astype(np.float32)
    try:
        X_sc = scaler.transform(X_p)
    except Exception:
        from sklearn.preprocessing import StandardScaler as _SS
        X_sc = _SS().fit_transform(X_p)

    # Batch predict_proba for all sessions at once
    try:
        all_proba = model.predict_proba(X_sc)
    except Exception:
        all_proba = np.ones((len(X_sc), len(class_names))) / len(class_names)

    updater = BayesianProfileUpdater(class_names)
    for i in range(len(all_proba)):
        updater.update(all_proba[i])
    history = np.array(updater.history)

    # Convergence chart for selected participant
    children.append(section_divider("Posterior Probability Over Sessions"))
    fig = go.Figure()
    for j, cls in enumerate(class_names):
        c = list(PROFILE_COLORS.values())[j % len(PROFILE_COLORS)]
        fig.add_trace(go.Scatter(x=list(range(1, len(history) + 1)), y=history[:, j],
                                 mode="lines", name=cls, line=dict(color=c, width=2.5)))
    fig.add_hline(y=0.5, line_dash="dot", line_color="grey", opacity=0.5)
    apply_theme(fig, height=350, margin=dict(l=40, r=40, t=10, b=40),
                yaxis=dict(title="P(profile)", range=[0, 1]), xaxis=dict(title="Session #"),
                legend=dict(orientation="h", y=1.12))
    children.append(dcc.Graph(figure=fig))

    # Checkpoint cards
    children.append(section_divider("Prediction at Key Checkpoints"))
    checkpoints = [5, 10, 20, 50]
    ck_cards = []
    for ck in checkpoints:
        idx = min(ck - 1, len(history) - 1)
        pred = class_names[np.argmax(history[idx])]
        conf = float(np.max(history[idx]))
        ok = pred == true_label
        ck_cards.append(html.Div([
            html.H3(f"After {ck} sessions"),
            html.H2(pred, style={"fontSize": "14px", "color": "#4ade80" if ok else "#f87171"}),
            html.P(f"conf: {conf:.2f}"),
        ], className="metric-card"))
    children.append(html.Div(ck_cards, className="metrics-row"))

    # Final posterior
    children.append(section_divider("Final Posterior Distribution"))
    final = history[-1]
    fig = go.Figure(go.Bar(x=final, y=class_names, orientation="h",
                           marker_color=[PROFILE_COLORS.get(c, "#4C72B0") for c in class_names]))
    apply_theme(fig, height=200, margin=dict(l=10, r=10, t=10, b=10),
                xaxis=dict(range=[0, 1], title="Probability"))
    children.append(dcc.Graph(figure=fig))

    # ── Population-Level Convergence (8 participants, native Plotly) ──────
    children.append(section_divider("Population-Level Convergence (8 Sampled Participants)"))
    conv_data = _run_convergence_sample(df, model, scaler, le, feature_cols, n_sample=8)

    if conv_data:
        n_plots = len(conv_data)
        ncols = 2
        nrows = max(1, (n_plots + 1) // ncols)
        subtitles = [f"P{d['pid'][:8]}… (True: {d['true_label']})" for d in conv_data]
        fig = make_subplots(rows=nrows, cols=ncols, subplot_titles=subtitles,
                            vertical_spacing=0.08, horizontal_spacing=0.06)
        profile_colors = list(PROFILE_COLORS.values())
        for i, d in enumerate(conv_data):
            r, c = divmod(i, ncols)
            for j, cls in enumerate(d["class_names"]):
                fig.add_trace(go.Scatter(
                    x=list(range(1, len(d["history"]) + 1)), y=d["history"][:, j],
                    mode="lines", name=cls, line=dict(color=profile_colors[j % len(profile_colors)], width=1.8),
                    showlegend=(i == 0),
                    hovertemplate=f"{cls}<br>Session: %{{x}}<br>P: %{{y:.3f}}<extra></extra>",
                ), row=r + 1, col=c + 1)
            fig.add_hline(y=0.5, line_dash="dot", line_color="grey", opacity=0.3, row=r + 1, col=c + 1)
        apply_theme(fig, height=nrows * 220, margin=dict(l=30, r=20, t=30, b=20),
                    legend=dict(orientation="h", y=1.02))
        children.append(dcc.Graph(figure=fig))

    # ── Early Accuracy Chart (native Plotly) ─────────────────────────────
    children.append(section_divider("Early Assessment Accuracy"))
    checkpoints_pop = [5, 10, 20, 50]
    accs = {ck: [] for ck in checkpoints_pop}
    for d in conv_data:
        true_idx = d["class_names"].index(d["true_label"])
        for ck in checkpoints_pop:
            idx = min(ck - 1, len(d["history"]) - 1)
            accs[ck].append(int(np.argmax(d["history"][idx]) == true_idx))
    mean_accs = {ck: np.mean(v) if v else 0 for ck, v in accs.items()}

    x_labels = [f"After {ck} sessions" for ck in checkpoints_pop]
    y_vals = [mean_accs[ck] for ck in checkpoints_pop]
    fig = go.Figure(go.Bar(
        x=x_labels, y=y_vals,
        marker_color=["#818cf8", "#6366f1", "#4f46e5", "#4338ca"],
        text=[f"{v:.0%}" for v in y_vals], textposition="outside",
        hovertemplate="Checkpoint: %{x}<br>Accuracy: %{y:.1%}<extra></extra>",
    ))
    apply_theme(fig, height=300, showlegend=False,
                yaxis=dict(range=[0, 1.15], title="Accuracy", tickformat=".0%"),
                xaxis_title="")
    children.append(dcc.Graph(figure=fig))

    # Model Card
    children.append(section_divider("Model Card"))
    children.append(html.Div([
        html.Table([
            html.Tr([html.Td(html.B("Model type"), style={"padding": "4px 12px"}),
                      html.Td("Gradient Boosting Ensemble", style={"padding": "4px 12px"})]),
            html.Tr([html.Td(html.B("Training data"), style={"padding": "4px 12px"}),
                      html.Td("400 participants × 50 sessions / game", style={"padding": "4px 12px"})]),
            html.Tr([html.Td(html.B("Classes"), style={"padding": "4px 12px"}),
                      html.Td(", ".join(class_names), style={"padding": "4px 12px"})]),
            html.Tr([html.Td(html.B("Features"), style={"padding": "4px 12px"}),
                      html.Td(f"{len(feature_cols)} features", style={"padding": "4px 12px"})]),
            html.Tr([html.Td(html.B("Evaluation"), style={"padding": "4px 12px"}),
                      html.Td("Bayesian posterior, 5-fold CV", style={"padding": "4px 12px"})]),
            html.Tr([html.Td(html.B("Approach"), style={"padding": "4px 12px"}),
                      html.Td("Session-by-session likelihood → posterior update", style={"padding": "4px 12px"})]),
        ], style={"color": "#8b9dc3", "fontSize": "13px"}),
    ], className="about-box"))

    return html.Div(children)

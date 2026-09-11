"""
Multi-Game ADHD Analytics Dashboard (Streamlit)
=================================================
Run with:
    streamlit run multigame_pipeline/dashboard/app.py
"""

import os
import sys
import warnings
import joblib
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import streamlit as st

warnings.filterwarnings("ignore")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

OUTPUTS = os.path.join(_HERE, "..", "outputs")
GAME_NAMES = ["gonogo", "memory", "tracking", "shapes", "puzzle"]
GAME_LABELS = {
    "gonogo": "Go/No-Go", "memory": "Memory Match",
    "tracking": "Visual Tracking", "shapes": "Shape Sorting", "puzzle": "Puzzle",
}

st.set_page_config(
    page_title="NeuroGames Multi-Game Dashboard",
    page_icon="🧠", layout="wide", initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .main { background-color: #0e1117; }
    .page-header {
        background: linear-gradient(135deg, #1a1f36 0%, #252c48 100%);
        border: 1px solid #2d3250; border-radius: 14px;
        padding: 24px 30px; margin-bottom: 24px;
    }
    .page-header h1 { color: #e2e8f0; font-size: 26px; font-weight: 700; margin: 0 0 4px 0; }
    .page-header p  { color: #8b9dc3; font-size: 14px; margin: 0; }
    .metric-card {
        background: linear-gradient(135deg, #1e2130 0%, #252a3d 100%);
        border: 1px solid #2d3250; border-radius: 12px;
        padding: 18px 22px; margin: 6px 0; text-align: center;
        transition: border-color 0.2s ease;
    }
    .metric-card:hover { border-color: #4C72B0; }
    .metric-card h3 {
        color: #8b9dc3; font-size: 12px; font-weight: 600;
        text-transform: uppercase; letter-spacing: 1.2px; margin: 0 0 6px 0;
    }
    .metric-card h2 { color: #ffffff; font-size: 24px; font-weight: 700; margin: 0; }
    .metric-card p  { color: #64748b; font-size: 12px; margin: 4px 0 0 0; }
    .profile-badge {
        display: inline-block; padding: 5px 14px; border-radius: 20px;
        font-weight: 600; font-size: 13px; margin: 2px 4px;
    }
    .badge-optimal    { background:#1a4731; color:#4ade80; border:1px solid #4ade80; }
    .badge-inattentive { background:#3b2a14; color:#fbbf24; border:1px solid #fbbf24; }
    .badge-hyperactive { background:#3b1a1a; color:#f87171; border:1px solid #f87171; }
    .badge-combined   { background:#2a1a3b; color:#c084fc; border:1px solid #c084fc; }
    .section-line {
        font-size: 15px; font-weight: 700; color: #8b9dc3;
        text-transform: uppercase; letter-spacing: 1.2px;
        border-bottom: 2px solid #2d3250; padding-bottom: 8px;
        margin: 30px 0 16px 0;
    }
    .flag-alert {
        background: #3b1a1a; border: 1px solid #f87171;
        border-radius: 10px; padding: 12px 16px; color: #fca5a5;
        font-size: 13px; margin: 8px 0;
    }
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0e1117 0%, #141827 100%);
    }
</style>
""", unsafe_allow_html=True)

try:
    import plotly.graph_objects as go
    import plotly.express as px
    from plotly.subplots import make_subplots
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

# ── Helpers ───────────────────────────────────────────────────────────────────
PROFILE_COLORS = {
    "Optimal / Neurotypical": "#4ade80", "Inattentive ADHD": "#fbbf24",
    "Hyperactive-Impulsive ADHD": "#f87171", "Combined ADHD": "#c084fc",
}
GAME_COLORS = {
    "gonogo": "#FF6B6B", "memory": "#4ECDC4",
    "tracking": "#45B7D1", "shapes": "#FFA07A", "puzzle": "#DDA0DD",
}
PROFILE_BADGES = {
    "Optimal / Neurotypical": "badge-optimal", "Inattentive ADHD": "badge-inattentive",
    "Hyperactive-Impulsive ADHD": "badge-hyperactive", "Combined ADHD": "badge-combined",
}

def badge(label):
    cls = PROFILE_BADGES.get(label, "badge-optimal")
    return f'<span class="profile-badge {cls}">{label}</span>'

def _hdr(icon, title, subtitle):
    st.markdown(f"<div class='page-header'><h1>{icon} {title}</h1><p>{subtitle}</p></div>", unsafe_allow_html=True)

def _mc(label, value, sub=""):
    return f'<div class="metric-card"><h3>{label}</h3><h2>{value}</h2><p>{sub}</p></div>'

def _divider(text):
    st.markdown(f"<div class='section-line'>{text}</div>", unsafe_allow_html=True)


# ── Loaders ───────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_game_data(g):
    from multigame_pipeline.preprocessor import load_raw
    from multigame_pipeline.feature_engineer import add_derived_features
    return add_derived_features(load_raw(g), g)

@st.cache_data(show_spinner=False)
def load_cross():
    from mlops.db import load_cross_game_results_df
    df = load_cross_game_results_df()
    return df if len(df) > 0 else None

@st.cache_data(show_spinner=False)
def load_flags(g):
    from mlops.db import load_anomaly_flags_df
    df = load_anomaly_flags_df(g)
    return df if len(df) > 0 else None

@st.cache_resource(show_spinner=False)
def load_clf(g):
    p = os.path.join(OUTPUTS, g, "classifier_ensemble.pkl")
    return joblib.load(p) if os.path.exists(p) else None

@st.cache_resource(show_spinner=False)
def load_assess(g):
    p = os.path.join(OUTPUTS, g, "invisible_assessment_model.pkl")
    return joblib.load(p) if os.path.exists(p) else None


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


# ── Sidebar ───────────────────────────────────────────────────────────────────
st.sidebar.markdown(
    "<div style='font-size:20px;font-weight:700;color:#e2e8f0;'>🧠 NeuroGames</div>"
    "<div style='font-size:12px;color:#64748b;margin-bottom:12px;'>Multi-Game ADHD Dashboard</div>",
    unsafe_allow_html=True,
)
st.sidebar.markdown("---")

PAGES = {
    "📊  Population Overview":    "population",
    "🎮  Per-Game Explorer":      "per_game",
    "🔍  Participant Explorer":   "participant",
    "🧪  Invisible Assessment":   "assessment",
    "⚠️  Anomaly Monitor":        "anomaly",
    "🌐  Cross-Game Analysis":    "cross_game",
    "📈  Model Comparison":       "comparison",
}
page_label = st.sidebar.radio("Navigate to", list(PAGES.keys()), label_visibility="collapsed")
page = PAGES[page_label]

st.sidebar.markdown("---")
st.sidebar.markdown(
    "<div style='color:#64748b;font-size:11px;line-height:1.6;'>"
    "📂 <b>5 Games</b> · 400 participants<br>"
    "🎯 50 sessions per game<br>🏷️ 4 ADHD profiles<br>"
    "🔬 Gaussian Copula generated</div>",
    unsafe_allow_html=True,
)


# ==============================================================================
# PAGE 1 — Population Overview
# ==============================================================================
if page == "population":
    _hdr("📊", "Population Overview",
         "Global dataset statistics, profile distributions, and feature comparison across all 5 games.")

    all_dfs = {}
    for g in GAME_NAMES:
        try: all_dfs[g] = load_game_data(g)
        except Exception: pass

    if not all_dfs:
        st.error("No game data found. Run the pipeline first.")
        st.stop()

    total_sessions = sum(len(d) for d in all_dfs.values())
    total_pids = list(all_dfs.values())[0]["Participant_ID"].nunique()
    avg_acc = np.mean([(d["Correct_Responses"].sum() / max(1, d["Total_Actions"].sum())) for d in all_dfs.values()])
    avg_rt = np.mean([d["Reaction_Time"].mean() for d in all_dfs.values()])

    # Metric cards
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.markdown(_mc("Games", len(all_dfs)), unsafe_allow_html=True)
    c2.markdown(_mc("Participants", f"{total_pids:,}", "Shared pool"), unsafe_allow_html=True)
    c3.markdown(_mc("Total Sessions", f"{total_sessions:,}", f"{total_sessions//len(all_dfs):,}/game"), unsafe_allow_html=True)
    c4.markdown(_mc("Avg Accuracy", f"{avg_acc*100:.1f}%"), unsafe_allow_html=True)
    c5.markdown(_mc("Avg Reaction", f"{avg_rt:.2f}s"), unsafe_allow_html=True)

    # Per-game performance bars
    _divider("Per-Game Performance Comparison")
    if HAS_PLOTLY:
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
        fig.update_layout(template="plotly_dark", height=350, showlegend=False, margin=dict(l=40, r=20, t=40, b=40))
        fig.update_yaxes(range=[0, 1.1], row=1, col=1)
        st.plotly_chart(fig, use_container_width=True)

    # Profile distribution
    _divider("Sessions by Profile & Game")
    if HAS_PLOTLY:
        dist = []
        for g, d in all_dfs.items():
            for cl in d["cluster"].unique():
                dist.append({"Game": GAME_LABELS[g], "Profile": cl, "Sessions": len(d[d["cluster"] == cl])})
        fig = px.bar(pd.DataFrame(dist), x="Game", y="Sessions", color="Profile",
                     color_discrete_map=PROFILE_COLORS, barmode="group", template="plotly_dark", height=320)
        fig.update_layout(margin=dict(l=40, r=20, t=10, b=40))
        st.plotly_chart(fig, use_container_width=True)

    # Feature distributions
    _divider("Feature Distributions by Profile (All Games Combined)")
    combined = pd.concat(all_dfs.values(), ignore_index=True)
    if HAS_PLOTLY:
        metrics = ["Reaction_Time", "Correct_Responses", "Time_Spent"]
        fig = make_subplots(rows=1, cols=3, subplot_titles=[m.replace("_", " ") for m in metrics])
        for i, m in enumerate(metrics, 1):
            for cls in combined["cluster"].unique():
                fig.add_trace(go.Box(y=combined[combined["cluster"] == cls][m], name=cls,
                    marker_color=PROFILE_COLORS.get(cls, "#fff"), showlegend=(i == 1)), row=1, col=i)
        fig.update_layout(template="plotly_dark", height=350, legend=dict(orientation="h", y=1.15))
        st.plotly_chart(fig, use_container_width=True)

    # Means table
    _divider("Feature Means by Profile")
    mean_df = combined.groupby("cluster")[["Time_Spent", "Total_Actions", "Correct_Responses", "Reaction_Time"]].mean()
    st.dataframe(mean_df.style.format("{:.2f}").background_gradient(cmap="Blues", axis=0), use_container_width=True)

    # Correlation
    _divider("Spearman Correlations (All Games)")
    core_cols = ["Time_Spent", "Total_Actions", "Correct_Responses", "Incorrect_Responses", "Touch_Interactions", "Reaction_Time"]
    avail = [c for c in core_cols if c in combined.columns]
    corr = combined[avail].corr(method="spearman")
    if HAS_PLOTLY:
        import plotly.figure_factory as ff
        labels = [c.replace("_", " ")[:18] for c in corr.columns]
        fig = ff.create_annotated_heatmap(z=np.round(corr.values[::-1], 2), x=labels, y=labels[::-1],
                                          colorscale="RdBu_r", zmin=-1, zmax=1, showscale=True)
        fig.update_layout(template="plotly_dark", height=450, margin=dict(l=150, r=20, t=20, b=100))
        st.plotly_chart(fig, use_container_width=True)


# ==============================================================================
# PAGE 2 — Per-Game Explorer
# ==============================================================================
elif page == "per_game":
    _hdr("🎮", "Per-Game Explorer",
         "Detailed statistics, game-specific metrics, and feature correlations for each game.")

    game = st.selectbox("Select Game", GAME_NAMES, format_func=lambda g: GAME_LABELS[g])
    df = load_game_data(game)

    n_sessions = len(df)
    n_pids = df["Participant_ID"].nunique()
    acc = df["Correct_Responses"].sum() / max(1, df["Total_Actions"].sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(_mc("Sessions", f"{n_sessions:,}"), unsafe_allow_html=True)
    c2.markdown(_mc("Participants", n_pids), unsafe_allow_html=True)
    c3.markdown(_mc("Accuracy", f"{acc*100:.1f}%"), unsafe_allow_html=True)
    c4.markdown(_mc("Avg RT", f"{df['Reaction_Time'].mean():.2f}s"), unsafe_allow_html=True)

    # Core feature box plots
    _divider("Core Feature Distributions by Profile")
    if HAS_PLOTLY:
        metrics = ["Reaction_Time", "Correct_Responses", "Time_Spent"]
        fig = make_subplots(rows=1, cols=3, subplot_titles=[m.replace("_", " ") for m in metrics])
        for i, m in enumerate(metrics, 1):
            for cls in df["cluster"].unique():
                fig.add_trace(go.Box(y=df[df["cluster"] == cls][m], name=cls,
                    marker_color=PROFILE_COLORS.get(cls, "#fff"), showlegend=(i == 1)), row=1, col=i)
        fig.update_layout(template="plotly_dark", height=350, legend=dict(orientation="h", y=1.15))
        st.plotly_chart(fig, use_container_width=True)

    # Game-specific metrics
    from multigame_pipeline.preprocessor import GAME_EXTRA_FEATURES
    extras = GAME_EXTRA_FEATURES.get(game, [])
    avail_extras = [c for c in extras if c in df.columns and df[c].dtype != object]

    if avail_extras:
        _divider(f"{GAME_LABELS[game]} — Game-Specific Metrics by Profile")
        st.dataframe(df.groupby("cluster")[avail_extras].mean().style.format("{:.3f}").background_gradient(cmap="YlOrRd", axis=0),
                     use_container_width=True)

        if HAS_PLOTLY and len(avail_extras) >= 2:
            plot_extras = avail_extras[:4]
            fig = make_subplots(rows=1, cols=len(plot_extras),
                                subplot_titles=[e.replace("_", " ") for e in plot_extras])
            for i, col_name in enumerate(plot_extras, 1):
                for cls in df["cluster"].unique():
                    fig.add_trace(go.Box(y=df[df["cluster"] == cls][col_name], name=cls,
                        marker_color=PROFILE_COLORS.get(cls, "#fff"), showlegend=(i == 1)), row=1, col=i)
            fig.update_layout(template="plotly_dark", height=350, legend=dict(orientation="h", y=1.15))
            st.plotly_chart(fig, use_container_width=True)

    # Correlation
    _divider("Feature Correlation Matrix")
    core = ["Time_Spent", "Total_Actions", "Correct_Responses", "Incorrect_Responses", "Reaction_Time"]
    all_corr = core + avail_extras
    corr = df[all_corr].corr(method="spearman")
    if HAS_PLOTLY:
        import plotly.figure_factory as ff
        labels = [c.replace("_", " ")[:18] for c in corr.columns]
        fig = ff.create_annotated_heatmap(z=np.round(corr.values[::-1], 2), x=labels, y=labels[::-1],
                                          colorscale="RdBu_r", zmin=-1, zmax=1, showscale=True)
        fig.update_layout(template="plotly_dark", height=500, margin=dict(l=150, r=20, t=20, b=100))
        st.plotly_chart(fig, use_container_width=True)


# ==============================================================================
# PAGE 3 — Participant Explorer
# ==============================================================================
elif page == "participant":
    _hdr("🔍", "Participant Explorer",
         "Individual session timelines, behavioural metrics, progress trends, and raw session data.")

    col_g, col_p = st.columns(2)
    with col_g:
        game = st.selectbox("Game", GAME_NAMES, format_func=lambda g: GAME_LABELS[g], key="p_game")
    df = load_game_data(game)
    pids = sorted(df["Participant_ID"].unique())
    with col_p:
        selected_pid = st.selectbox("Participant", pids, key="p_pid")

    pdata = df[df["Participant_ID"] == selected_pid].reset_index(drop=True)
    true_label = pdata["cluster"].iloc[0]

    st.markdown(f"**Profile:** {badge(true_label)} &nbsp;|&nbsp; "
                f"**Age:** {pdata['Age_Group'].iloc[0]} &nbsp;|&nbsp; "
                f"**Cognitive:** {pdata['Cognitive_Level'].iloc[0]}",
                unsafe_allow_html=True)

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
    card_items = [
        ("Sessions", len(pdata)), ("Avg RT", f"{pdata['Reaction_Time'].mean():.2f}s"),
        ("Accuracy", f"{acc_val*100:.1f}%"),
    ]
    if has_hints:
        card_items.append(("Avg Hints", f"{pdata['Hint_Usage'].mean():.1f}"))
    card_items.append(("Flagged", n_flagged))
    for col, (lbl, val) in zip(st.columns(len(card_items)), card_items):
        col.markdown(_mc(lbl, val), unsafe_allow_html=True)

    # Session timeline
    _divider("Session Timeline")
    if HAS_PLOTLY:
        sessions = list(range(1, len(pdata)+1))
        colors_pt = ["#f87171" if f else GAME_COLORS[game] for f in pdata["flagged"]]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=sessions, y=pdata["Reaction_Time"], mode="lines+markers",
                                 name="Reaction Time", line=dict(color=GAME_COLORS[game], width=2),
                                 marker=dict(color=colors_pt, size=6)))
        if "accuracy_rate" in pdata.columns:
            fig.add_trace(go.Scatter(x=sessions, y=pdata["accuracy_rate"], mode="lines",
                                     name="Accuracy", line=dict(color="#4ade80", width=2, dash="dot"), yaxis="y2"))
        fig.update_layout(template="plotly_dark", height=340, margin=dict(l=40, r=40, t=10, b=40),
                          legend=dict(orientation="h", y=1.1),
                          yaxis=dict(title="RT (s)"), yaxis2=dict(title="Accuracy", overlaying="y", side="right", range=[0,1]),
                          xaxis=dict(title="Session #"))
        if n_flagged:
            fig.add_annotation(text="🔴 Red = anomaly-flagged", xref="paper", yref="paper",
                               x=0.01, y=0.95, showarrow=False, font=dict(color="#f87171", size=11))
        st.plotly_chart(fig, use_container_width=True)

    if n_flagged:
        st.markdown(f"<div class='flag-alert'>⚠️ {n_flagged} sessions flagged as at-risk for this participant.</div>",
                    unsafe_allow_html=True)

    # Progress trends
    _divider("Progress Trends")
    n_sess = len(pdata)
    acc_s = (pdata["Correct_Responses"] / pdata["Total_Actions"].replace(0, np.nan)).fillna(0).values
    rt_s = pdata["Reaction_Time"].values
    comp_s = (pdata["Game_Completion_Status"] == "Completed").astype(float).values
    sessions_x = np.arange(1, n_sess + 1)

    def _slope(y):
        if len(y) < 2 or np.std(y) < 1e-9: return 0.0
        return np.polyfit(np.arange(len(y), dtype=float), y, 1)[0]

    def _arrow(s, lb=False):
        if abs(s) < 1e-5: return "~", "#94a3b8", "Stable"
        if lb: return ("↓", "#4ade80", "Improving") if s < 0 else ("↑", "#f87171", "Worsening")
        return ("↑", "#4ade80", "Improving") if s > 0 else ("↓", "#f87171", "Worsening")

    trend_items = [
        ("Reaction Time", _slope(rt_s), "s/sess", True), ("Accuracy", _slope(acc_s), "%/sess", False),
        ("Completion", _slope(comp_s), "%/sess", False),
    ]
    if has_hints:
        hint_s = pdata["Hint_Usage"].values.astype(float)
        trend_items.append(("Hint Usage", _slope(hint_s), "h/sess", True))

    for col, (nm, sl, un, lb) in zip(st.columns(len(trend_items)), trend_items):
        ar, clr, lbl = _arrow(sl, lb)
        ds = sl * 100 if "%" in un else sl
        col.markdown(f"<div class='metric-card'><h3>{nm}</h3>"
                     f"<h2 style='color:{clr};font-size:20px;'>{ar} {lbl}</h2>"
                     f"<p>{ds:+.4f} {un}</p></div>", unsafe_allow_html=True)

    # Rolling averages chart
    if HAS_PLOTLY:
        w = min(5, n_sess)
        def _roll(a, w): return np.convolve(a, np.ones(w)/w, mode="valid")
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
        fig.update_layout(template="plotly_dark", height=chart_height, margin=dict(l=40, r=20, t=40, b=30))
        st.plotly_chart(fig, use_container_width=True)

    # Raw data
    _divider("Raw Session Data")
    show = ["Game_Session_ID", "Time_Spent", "Total_Actions", "Correct_Responses",
            "Reaction_Time", "Hint_Usage", "Game_Completion_Status", "Performance_Level"]
    show_avail = [c for c in show if c in pdata.columns]
    st.dataframe(pdata[show_avail].style.background_gradient(subset=["Reaction_Time"], cmap="RdYlGn_r"),
                 use_container_width=True, height=350)


# ==============================================================================
# PAGE 4 — Invisible Assessment
# ==============================================================================
elif page == "assessment":
    _hdr("🧪", "Invisible / Passive ADHD Assessment",
         "Bayesian posterior convergence — watch profile confidence grow session-by-session without explicit testing.")

    col_g, col_p = st.columns(2)
    with col_g:
        game = st.selectbox("Game", GAME_NAMES, format_func=lambda g: GAME_LABELS[g], key="a_game")
    art = load_assess(game)
    if art is None:
        st.error(f"Assessment model not found for {game}. Run the pipeline first.")
        st.stop()

    model, scaler, le = art["model"], art["scaler"], art["label_encoder"]
    feature_cols, class_names = art["feature_cols"], art["class_names"]

    df = load_game_data(game)
    pids = sorted(df["Participant_ID"].unique())
    with col_p:
        selected_pid = st.selectbox("Participant", pids, key="a_pid")

    pdata = df[df["Participant_ID"] == selected_pid].reset_index(drop=True)
    true_label = pdata["cluster"].iloc[0]
    st.markdown(f"**Profile:** {badge(true_label)} &nbsp;|&nbsp; **Game:** {GAME_LABELS[game]} &nbsp;|&nbsp; **Sessions:** {len(pdata)}",
                unsafe_allow_html=True)

    # Bayesian update
    avail = [c for c in feature_cols if c in pdata.columns]
    X_p = pdata[avail].fillna(0).values.astype(np.float32)
    try: X_sc = scaler.transform(X_p)
    except Exception:
        from sklearn.preprocessing import StandardScaler as _SS
        X_sc = _SS().fit_transform(X_p)

    updater = BayesianProfileUpdater(class_names)
    for i in range(len(X_sc)):
        try: proba = model.predict_proba(X_sc[i:i+1])[0]
        except Exception: proba = np.ones(len(class_names)) / len(class_names)
        updater.update(proba)
    history = np.array(updater.history)

    # Convergence chart
    _divider("Posterior Probability Over Sessions")
    if HAS_PLOTLY:
        fig = go.Figure()
        for j, cls in enumerate(class_names):
            c = list(PROFILE_COLORS.values())[j % len(PROFILE_COLORS)]
            fig.add_trace(go.Scatter(x=list(range(1, len(history)+1)), y=history[:, j],
                                     mode="lines", name=cls, line=dict(color=c, width=2.5)))
        fig.add_hline(y=0.5, line_dash="dot", line_color="grey", opacity=0.5)
        fig.update_layout(template="plotly_dark", height=350, margin=dict(l=40, r=40, t=10, b=40),
                          yaxis=dict(title="P(profile)", range=[0, 1]), xaxis=dict(title="Session #"),
                          legend=dict(orientation="h", y=1.12))
        st.plotly_chart(fig, use_container_width=True)

    # Checkpoint cards
    _divider("Prediction at Key Checkpoints")
    checkpoints = [5, 10, 20, 50]
    ck_cols = st.columns(len(checkpoints))
    for col, ck in zip(ck_cols, checkpoints):
        idx = min(ck - 1, len(history) - 1)
        pred = class_names[np.argmax(history[idx])]
        conf = float(np.max(history[idx]))
        ok = pred == true_label
        col.markdown(f"<div class='metric-card'><h3>After {ck} sessions</h3>"
                     f"<h2 style='font-size:14px;color:{'#4ade80' if ok else '#f87171'};'>{pred}</h2>"
                     f"<p>conf: {conf:.2f}</p></div>", unsafe_allow_html=True)

    # Final posterior
    _divider("Final Posterior Distribution")
    final = history[-1]
    if HAS_PLOTLY:
        fig = go.Figure(go.Bar(x=final, y=class_names, orientation="h",
                               marker_color=[PROFILE_COLORS.get(c, "#4C72B0") for c in class_names]))
        fig.update_layout(template="plotly_dark", height=200, margin=dict(l=10, r=10, t=10, b=10),
                          xaxis=dict(range=[0, 1], title="Probability"))
        st.plotly_chart(fig, use_container_width=True)

    # Population convergence images
    _divider("Population-Level Convergence")
    ic1, ic2 = st.columns(2)
    conv_img = os.path.join(OUTPUTS, game, "assessment_convergence.png")
    early_img = os.path.join(OUTPUTS, game, "assessment_early_accuracy.png")
    if os.path.exists(conv_img):
        ic1.image(conv_img, caption="Posterior Convergence (8 Samples)", use_container_width=True)
    if os.path.exists(early_img):
        ic2.image(early_img, caption="Accuracy vs Sessions Seen", use_container_width=True)


# ==============================================================================
# PAGE 5 — Anomaly Monitor
# ==============================================================================
elif page == "anomaly":
    _hdr("⚠️", "Anomaly Monitor",
         "Sessions flagged as at-risk by IsolationForest and Autoencoder detectors.")

    game = st.selectbox("Game", GAME_NAMES, format_func=lambda g: GAME_LABELS[g], key="an_game")
    flags_df = load_flags(game)
    if flags_df is None:
        st.error(f"Anomaly flags not found for {game}. Run the pipeline first.")
        st.stop()

    total = len(flags_df)
    flagged = int(flags_df["anomaly_flag"].sum())
    if_flagged = int(flags_df["if_flag"].sum())
    ae_flagged = int(flags_df["ae_flag"].sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(_mc("Total Sessions", f"{total:,}"), unsafe_allow_html=True)
    c2.markdown(_mc("Combined Flags", f"{flagged:,}", f"{flagged/total*100:.1f}%"), unsafe_allow_html=True)
    c3.markdown(_mc("IF Flags", f"{if_flagged:,}"), unsafe_allow_html=True)
    c4.markdown(_mc("AE Flags", f"{ae_flagged:,}"), unsafe_allow_html=True)

    # Flag rate by cluster
    _divider("Flag Rate by ADHD Profile")
    rate_df = (flags_df.groupby("cluster")["anomaly_flag"].agg(["sum", "count"])
               .assign(rate=lambda d: d["sum"] / d["count"]).reset_index().sort_values("rate", ascending=False))
    if HAS_PLOTLY:
        fig = px.bar(rate_df, x="cluster", y="rate", color="cluster",
                     color_discrete_map=PROFILE_COLORS, template="plotly_dark", height=300)
        fig.update_layout(showlegend=False, yaxis=dict(tickformat=".0%", title="Flag Rate"), xaxis_title="")
        st.plotly_chart(fig, use_container_width=True)

    # Participant anomaly explorer
    _divider("Participant Anomaly Timeline")
    df = load_game_data(game)
    pids = sorted(df["Participant_ID"].unique())
    sel_pid = st.selectbox("Select Participant", pids, key="an_pid")
    pflag = flags_df[flags_df["Participant_ID"] == sel_pid].reset_index(drop=True)
    pdata = df[df["Participant_ID"] == sel_pid].reset_index(drop=True)
    n_flags = int(pflag["anomaly_flag"].sum())
    st.markdown(f"**Profile:** {badge(pdata['cluster'].iloc[0])} &nbsp;|&nbsp; **Flagged sessions:** {n_flags}",
                unsafe_allow_html=True)

    if HAS_PLOTLY and len(pflag) == len(pdata):
        colors_pt = ["#f87171" if f else GAME_COLORS[game] for f in pflag["anomaly_flag"]]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=list(range(1, len(pdata)+1)), y=pdata["Reaction_Time"],
                                 mode="lines+markers", name="RT",
                                 line=dict(color=GAME_COLORS[game], width=2),
                                 marker=dict(color=colors_pt, size=7, line=dict(width=1, color="white"))))
        fig.add_trace(go.Scatter(x=list(range(1, len(pflag)+1)), y=pflag["ae_error"],
                                 mode="lines", name="AE Error",
                                 line=dict(color="#fbbf24", width=1.5, dash="dot"), yaxis="y2"))
        fig.update_layout(template="plotly_dark", height=320, margin=dict(l=40, r=60, t=10, b=40),
                          yaxis=dict(title="Reaction Time"), yaxis2=dict(title="AE Error", overlaying="y", side="right"),
                          xaxis=dict(title="Session #"), legend=dict(orientation="h", y=1.1))
        st.plotly_chart(fig, use_container_width=True)

    if n_flags:
        st.markdown(f"<div class='flag-alert'>⚠️ {n_flags} sessions flagged as at-risk.</div>", unsafe_allow_html=True)

    # Flagged sessions table
    if n_flags and len(pflag) == len(pdata):
        _divider("Flagged Session Details")
        merged = pdata.copy()
        merged["anomaly_flag"] = pflag["anomaly_flag"].values
        merged["ae_error"] = pflag["ae_error"].values
        detail_cols = ["Game_Session_ID", "Reaction_Time", "Total_Actions", "Correct_Responses", "ae_error"]
        if "Hint_Usage" in merged.columns:
            detail_cols.insert(-1, "Hint_Usage")
        flagged_rows = merged[merged["anomaly_flag"] == 1][detail_cols]
        st.dataframe(flagged_rows.style.format({"ae_error": "{:.4f}", "Reaction_Time": "{:.2f}"}), use_container_width=True)

    # Output plots
    _divider("Score Distribution Plots")
    ic1, ic2 = st.columns(2)
    dist_img = os.path.join(OUTPUTS, game, "anomaly_score_distribution.png")
    clust_img = os.path.join(OUTPUTS, game, "anomaly_by_cluster.png")
    if os.path.exists(dist_img):
        ic1.image(dist_img, caption="Anomaly Score Distribution", use_container_width=True)
    if os.path.exists(clust_img):
        ic2.image(clust_img, caption="Flag Rate by Cluster", use_container_width=True)


# ==============================================================================
# PAGE 6 — Cross-Game Analysis
# ==============================================================================
elif page == "cross_game":
    _hdr("🌐", "Cross-Game Analysis",
         "Profile consistency, prediction agreement, and majority voting across all 5 games.")

    cross_df = load_cross()
    if cross_df is None:
        st.error("Cross-game predictions not found. Run the full pipeline first.")
        st.stop()

    n_p = len(cross_df)
    consist = cross_df["all_agree"].mean() if "all_agree" in cross_df.columns else 0
    avg_agr = cross_df["agreement_ratio"].mean() if "agreement_ratio" in cross_df.columns else 0
    maj_acc = (cross_df["majority_pred"] == cross_df["cluster"]).mean() if "majority_pred" in cross_df.columns else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(_mc("Participants", n_p), unsafe_allow_html=True)
    c2.markdown(_mc("Full Consistency", f"{consist:.1%}", "All 5 games agree"), unsafe_allow_html=True)
    c3.markdown(_mc("Avg Agreement", f"{avg_agr:.1%}"), unsafe_allow_html=True)
    c4.markdown(_mc("Majority Vote Acc", f"{maj_acc:.1%}"), unsafe_allow_html=True)

    # Per-game accuracy
    _divider("Per-Game Classification Accuracy")
    acc_img = os.path.join(OUTPUTS, "cross_game", "cross_game_accuracy.png")
    if os.path.exists(acc_img):
        st.image(acc_img, use_container_width=True)

    # Consistency heatmap
    _divider("Cross-Game Profile Agreement Heatmap")
    hm_img = os.path.join(OUTPUTS, "cross_game", "cross_game_consistency_heatmap.png")
    if os.path.exists(hm_img):
        st.image(hm_img, use_container_width=True)

    # Consistency by cluster
    _divider("Consistency by ADHD Profile")
    clust_img = os.path.join(OUTPUTS, "cross_game", "cross_game_consistency_by_cluster.png")
    if os.path.exists(clust_img):
        st.image(clust_img, use_container_width=True)

    # Composite data table
    _divider("Composite Predictions Data")
    st.dataframe(cross_df, use_container_width=True, height=400)


# ==============================================================================
# PAGE 7 — Model Comparison
# ==============================================================================
elif page == "comparison":
    _hdr("📈", "Model Comparison",
         "Performance summary across all pipeline modules and all games, plus output gallery.")

    # Collect results
    results = []
    for g in GAME_NAMES:
        clf = load_clf(g)
        if clf:
            results.append({"Game": GAME_LABELS[g], "Module": "Classification",
                            "Accuracy": clf.get("ensemble_test_acc", 0), "game_key": g})

    if not results:
        st.error("No model results found. Run the pipeline first.")
        st.stop()

    comp_df = pd.DataFrame(results)

    # Accuracy chart
    _divider("Classification Accuracy by Game")
    if HAS_PLOTLY:
        fig = px.bar(comp_df, x="Game", y="Accuracy", color="Game",
                     color_discrete_sequence=[GAME_COLORS[g] for g in GAME_NAMES],
                     template="plotly_dark", height=350, text_auto=".3f")
        fig.update_layout(yaxis=dict(range=[0, 1.1], title="Test Accuracy"), showlegend=False)
        fig.update_traces(textposition="outside")
        st.plotly_chart(fig, use_container_width=True)

    # Results table
    _divider("Detailed Results")
    st.dataframe(comp_df[["Game", "Module", "Accuracy"]].style.format({"Accuracy": "{:.4f}"})
                 .background_gradient(subset=["Accuracy"], cmap="YlGn"), use_container_width=True)

    # Output gallery
    _divider("Per-Game Output Gallery")
    for g in GAME_NAMES:
        st.markdown(f"#### 🎮 {GAME_LABELS[g]}")
        imgs = [
            ("classification_confusion_matrix.png", "Confusion Matrix"),
            ("classification_feature_importance.png", "Feature Importance"),
            ("sequence_confusion_matrix.png", "Sequence Confusion Matrix"),
            ("assessment_convergence.png", "Assessment Convergence"),
            ("anomaly_score_distribution.png", "Anomaly Distribution"),
            ("anomaly_by_cluster.png", "Anomaly by Cluster"),
        ]
        cols = st.columns(3)
        for i, (fname, cap) in enumerate(imgs):
            fp = os.path.join(OUTPUTS, g, fname)
            if os.path.exists(fp):
                cols[i % 3].image(fp, caption=cap, use_container_width=True)
        st.markdown("---")

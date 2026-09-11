"""
Plotly chart theme — single source of truth for all dashboard charts.
"""

PLOTLY_TEMPLATE = "plotly_dark"

# Base layout overrides applied to every figure via apply_theme()
PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Inter, system-ui, sans-serif", color="#a1a1aa", size=12),
    xaxis=dict(
        gridcolor="rgba(31,31,40,0.5)",
        linecolor="#28282f",
        zerolinecolor="#28282f",
    ),
    yaxis=dict(
        gridcolor="rgba(31,31,40,0.5)",
        linecolor="#28282f",
        zerolinecolor="#28282f",
    ),
    margin=dict(l=40, r=20, t=36, b=40),
    legend=dict(
        bgcolor="rgba(0,0,0,0)",
        bordercolor="rgba(0,0,0,0)",
        font=dict(size=11),
    ),
    hoverlabel=dict(
        bgcolor="#1e1e25",
        bordercolor="#28282f",
        font=dict(family="Inter, system-ui, sans-serif", size=12, color="#f4f4f5"),
    ),
)


def apply_theme(fig, **overrides):
    """Apply the standard NeuroGames chart theme to a Plotly figure.

    Any keyword arguments are merged on top of the base PLOTLY_LAYOUT, so
    callers can override specific keys like ``height`` or ``margin``.
    """
    merged = {**PLOTLY_LAYOUT, **overrides}
    fig.update_layout(template=PLOTLY_TEMPLATE, **merged)
    return fig

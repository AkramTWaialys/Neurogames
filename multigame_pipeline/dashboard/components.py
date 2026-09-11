"""
Reusable Dash UI components — shared across all dashboard pages.
"""

from dash import html, dash_table as dt

# ── Profile badge mappings ────────────────────────────────────────────────────
PROFILE_BADGES = {
    "Optimal / Neurotypical": "badge-optimal",
    "Inattentive ADHD": "badge-inattentive",
    "Hyperactive-Impulsive ADHD": "badge-hyperactive",
    "Combined ADHD": "badge-combined",
}


# ── Component Factories ──────────────────────────────────────────────────────
def page_header(icon, title, subtitle):
    """Top-of-page header card with icon, title, and description."""
    return html.Div([
        html.H1(f"{icon}  {title}"),
        html.P(subtitle),
    ], className="page-header")


def metric_card(label, value, sub=""):
    """KPI metric card with label, large value, and optional sublabel."""
    children = [
        html.H3(label.upper()),
        html.H2(str(value)),
    ]
    if sub:
        children.append(html.P(sub))
    return html.Div(children, className="metric-card")


def section_divider(text):
    """Labeled horizontal separator between content sections."""
    return html.Div(text.upper(), className="section-line")


def badge_html(label):
    """Coloured profile badge chip."""
    cls = PROFILE_BADGES.get(label, "badge-optimal")
    return html.Span(label, className=f"profile-badge {cls}")


def info_box(text):
    """Neutral information callout."""
    return html.Div(text, className="info-box")


def flag_alert(text):
    """Red alert callout for anomaly flags / warnings."""
    return html.Div(text, className="flag-alert")


def about_box(children):
    """Page-level description box with accent left border."""
    return html.Div(children, className="about-box")


# ── Shared Data Table ─────────────────────────────────────────────────────────

# Consistent styling aligned with CSS design tokens
_TABLE_HEADER_STYLE = {
    "backgroundColor": "#1e1e25",
    "color": "#a1a1aa",
    "fontWeight": "600",
    "border": "1px solid rgba(40,40,47,0.7)",
    "fontSize": "10px",
    "textTransform": "uppercase",
    "letterSpacing": "1px",
    "fontFamily": "Inter, system-ui, sans-serif",
}

_TABLE_CELL_STYLE = {
    "backgroundColor": "#141418",
    "color": "#f4f4f5",
    "border": "1px solid rgba(40,40,47,0.7)",
    "padding": "10px 14px",
    "fontSize": "13px",
    "fontFamily": "Inter, system-ui, sans-serif",
}

_TABLE_CONDITIONAL = [
    {"if": {"row_index": "odd"}, "backgroundColor": "#0f0f12"},
]


def data_table(df, table_id, page_size=20, precision=2):
    """Build a styled Dash DataTable from a DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        Source data.
    table_id : str
        Unique DOM id.
    page_size : int
        Rows per page.
    precision : int
        Decimal places for float columns.
    """
    columns = []
    for c in df.columns:
        col = {"name": c, "id": c}
        if df[c].dtype in ("float64", "float32"):
            col["type"] = "numeric"
            col["format"] = {"specifier": f".{precision}f"}
        elif df[c].dtype != object:
            col["type"] = "numeric"
        else:
            col["type"] = "text"
        columns.append(col)

    return dt.DataTable(
        id=table_id,
        columns=columns,
        data=df.round(precision).to_dict("records"),
        style_table={"overflowX": "auto"},
        style_header=_TABLE_HEADER_STYLE,
        style_cell=_TABLE_CELL_STYLE,
        style_data_conditional=_TABLE_CONDITIONAL,
        page_size=page_size,
    )

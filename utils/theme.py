"""
EcoTrace AI - Shared theme constants

A deliberate, non-default palette for a sustainability/carbon-auditing
tool: deep forest/charcoal base with a warm amber "alert" accent for high
emissions and a clear moss-green accent for positive/low-carbon signals.
Avoids the generic "bright green eco app" cliché in favor of something
closer to an environmental-audit dashboard: serious, data-forward, calm.
"""

COLORS = {
    "bg_deep": "#15211C",       # near-black forest charcoal, app background
    "bg_panel": "#1E2E27",      # slightly lighter panel background
    "ink": "#EDF2EE",           # primary text on dark
    "ink_muted": "#9FB3A8",     # secondary text
    "moss": "#5C8A6E",          # primary brand / low-carbon positive
    "moss_bright": "#7FB894",   # hover/active state
    "amber": "#D98E3F",         # medium-risk / attention
    "rust": "#C2563B",          # high emissions / high risk
    "border": "#2E4339",
}

TRANSPORT_COLORS = {
    "air": COLORS["rust"],
    "road": COLORS["amber"],
    "sea": COLORS["moss"],
    "rail": COLORS["moss_bright"],
    "local": "#3F7A5C",
}

CUSTOM_CSS = f"""
<style>
    .stApp {{
        background-color: {COLORS['bg_deep']};
    }}
    [data-testid="stSidebar"] {{
        background-color: {COLORS['bg_panel']};
        border-right: 1px solid {COLORS['border']};
    }}
    h1, h2, h3 {{
        color: {COLORS['ink']} !important;
        font-family: 'Georgia', 'Iowan Old Style', serif;
        letter-spacing: -0.01em;
    }}
    p, li, span, label, div {{
        color: {COLORS['ink']};
    }}
    .ecotrace-metric-card {{
        background-color: {COLORS['bg_panel']};
        border: 1px solid {COLORS['border']};
        border-radius: 10px;
        padding: 18px 20px;
    }}
    .ecotrace-eyebrow {{
        color: {COLORS['moss_bright']};
        font-size: 0.78rem;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        font-weight: 600;
        margin-bottom: 4px;
    }}
    .ecotrace-badge-high {{
        background-color: {COLORS['rust']}33;
        color: {COLORS['rust']};
        padding: 2px 10px;
        border-radius: 999px;
        font-size: 0.8rem;
        font-weight: 600;
    }}
    .ecotrace-badge-medium {{
        background-color: {COLORS['amber']}33;
        color: {COLORS['amber']};
        padding: 2px 10px;
        border-radius: 999px;
        font-size: 0.8rem;
        font-weight: 600;
    }}
    .ecotrace-badge-low {{
        background-color: {COLORS['moss']}33;
        color: {COLORS['moss_bright']};
        padding: 2px 10px;
        border-radius: 999px;
        font-size: 0.8rem;
        font-weight: 600;
    }}
</style>
"""


def priority_badge_html(priority: str) -> str:
    p = (priority or "low").lower()
    cls = {"high": "ecotrace-badge-high", "medium": "ecotrace-badge-medium"}.get(p, "ecotrace-badge-low")
    return f'<span class="{cls}">{p.title()} priority</span>'

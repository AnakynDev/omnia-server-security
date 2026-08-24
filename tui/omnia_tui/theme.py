"""
Tema visual do Omnia — paleta própria, sem relação com outros projetos.
Slate escuro quase preto com destaque ciano/teal ("monitor de sala de
servidor"). Cores de status seguem o padrão pedido na spec: verde=normal,
amarelo=atenção, vermelho=crítico.
"""

BG = "#0a0e12"
PANEL_BG = "#111820"
PANEL_BG_ALT = "#0c1116"
ACCENT = "#2dd4c4"
ACCENT_DIM = "#1c6d64"
TEXT = "#d8e2e8"
TEXT_MUTED = "#66757f"
SUCCESS = "#4ade80"
WARNING = "#facc15"
ERROR = "#f87171"
NEUTRAL = "#8b98a3"

BASE_CSS = f"""
Screen {{
    background: {BG};
}}

.panel {{
    border: solid {ACCENT_DIM};
    background: {PANEL_BG};
    padding: 0 1;
}}

.panel-title {{
    color: {ACCENT};
    text-style: bold;
    height: 1;
}}

.muted {{
    color: {TEXT_MUTED};
}}

.updated-at {{
    color: {TEXT_MUTED};
    text-style: italic;
    height: 1;
}}

.empty-state {{
    color: {TEXT_MUTED};
    text-style: italic;
    width: 100%;
    content-align: center middle;
}}

.unavailable-state {{
    color: {TEXT_MUTED};
    width: 100%;
    content-align: center middle;
}}

.loading-state {{
    color: {ACCENT};
    width: 100%;
    content-align: center middle;
}}

.stat-card {{
    border: solid {ACCENT_DIM};
    background: {PANEL_BG};
    width: 1fr;
    height: 100%;
    align-horizontal: center;
    padding: 0 1;
    margin-right: 1;
}}

.stat-label {{
    color: {TEXT_MUTED};
    width: 100%;
    text-align: center;
    height: 1;
}}

.stat-value {{
    color: {ACCENT};
    text-style: bold;
    width: 100%;
    text-align: center;
    height: 1;
}}

.stat-value.warning {{ color: {WARNING}; }}
.stat-value.error {{ color: {ERROR}; }}
.stat-value.success {{ color: {SUCCESS}; }}

DataTable {{
    background: {PANEL_BG};
    height: 1fr;
}}

DataTable > .datatable--header {{
    background: {PANEL_BG_ALT};
    color: {ACCENT};
    text-style: bold;
}}

Button {{
    border: none;
    background: {ACCENT_DIM};
    color: {BG};
    text-style: bold;
    min-width: 10;
}}

Button:focus {{
    background: {ACCENT};
    border: none;
}}

Button.-danger {{
    background: {ERROR};
    color: {BG};
    border: none;
}}

Button.-danger:focus {{
    background: {ERROR};
    text-style: bold underline;
    border: none;
}}

Button:disabled {{
    background: {PANEL_BG_ALT};
    color: {TEXT_MUTED};
}}

Input {{
    border: solid {ACCENT_DIM};
    background: {BG};
    color: {TEXT};
}}

Input:focus {{
    border: solid {ACCENT};
}}

Select {{
    border: solid {ACCENT_DIM};
    background: {BG};
}}

Select > SelectCurrent {{
    border: none;
}}

Select:focus > SelectCurrent {{
    border: none;
}}

SelectOverlay {{
    border: solid {ACCENT};
    background: {PANEL_BG};
}}

Tabs {{
    background: {PANEL_BG_ALT};
}}

Tab {{
    color: {TEXT_MUTED};
}}

Tab.-active {{
    color: {ACCENT};
    text-style: bold;
}}

Underline > .underline--bar {{
    color: {ACCENT};
    background: {ACCENT_DIM};
}}

ProgressBar > .bar--indeterminate {{
    color: {ACCENT};
}}

ProgressBar > .bar--bar {{
    color: {ACCENT};
}}

Sparkline > .sparkline--max-color {{
    color: {ACCENT};
}}

Sparkline > .sparkline--min-color {{
    color: {ACCENT_DIM};
}}
"""


def status_color(status: str) -> str:
    return {
        "ok": SUCCESS,
        "active": SUCCESS,
        "success": SUCCESS,
        "connected": SUCCESS,
        "warning": WARNING,
        "attention": WARNING,
        "reconnecting": WARNING,
        "degraded": WARNING,
        "error": ERROR,
        "failed": ERROR,
        "failure": ERROR,
        "critical": ERROR,
        "disconnected": ERROR,
        "inactive": TEXT_MUTED,
        "unknown": TEXT_MUTED,
        "unavailable": TEXT_MUTED,
    }.get(status, TEXT_MUTED)

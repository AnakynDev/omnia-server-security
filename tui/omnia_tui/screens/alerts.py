"""Seção 5 — Alertas."""

from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from ..data import OmniaMockBackend
from ..theme import ERROR, WARNING, ACCENT, NEUTRAL

KIND_LABELS = {
    "login_bruteforce": "força bruta",
    "new_key": "chave nova",
    "disk_almost_full": "disco cheio",
    "service_failed": "serviço falhou",
    "reboot": "reinício",
}

KIND_COLORS = {
    "login_bruteforce": ERROR,
    "new_key": WARNING,
    "disk_almost_full": WARNING,
    "service_failed": ERROR,
    "reboot": ACCENT,
}


def _fmt_dt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m %H:%M:%S")


class AlertsPanel(Vertical):
    DEFAULT_CSS = """
    AlertsPanel {
        height: 1fr;
    }

    #alerts-table {
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("HISTÓRICO DE ALERTAS", classes="panel-title")
        yield DataTable(id="alerts-table", cursor_type="none", zebra_stripes=True)
        yield Static("", id="alerts-empty", classes="empty-state")

    def on_mount(self) -> None:
        table = self.query_one("#alerts-table", DataTable)
        table.add_column("Data/Hora", width=18)
        table.add_column("Tipo", width=16)
        table.add_column("Descrição", width=60)

    def refresh_data(self, backend: OmniaMockBackend) -> None:
        if not backend.ready:
            return

        table = self.query_one("#alerts-table", DataTable)
        empty = self.query_one("#alerts-empty", Static)
        table.clear()

        alerts = sorted(backend.alerts, key=lambda a: a.ts, reverse=True)
        if not alerts:
            table.display = False
            empty.display = True
            empty.update("Nenhum alerta registrado ainda.")
            return

        table.display = True
        empty.display = False
        for alert in alerts[:100]:
            color = KIND_COLORS.get(alert.kind, NEUTRAL)
            kind_text = Text(KIND_LABELS.get(alert.kind, alert.kind), style=color)
            table.add_row(_fmt_dt(alert.ts), kind_text, alert.description)

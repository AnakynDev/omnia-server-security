"""Seção 3 — Speed Test."""

from __future__ import annotations

from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, ProgressBar, Sparkline, Static

from ..data import OmniaMockBackend
from ..widgets import StatCard


def _fmt_dt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m %H:%M:%S")


class SpeedTestPanel(Vertical):
    DEFAULT_CSS = """
    SpeedTestPanel {
        height: 1fr;
    }

    #st-action-row {
        height: 5;
        margin-bottom: 1;
    }

    #st-run-btn {
        width: auto;
        margin-right: 2;
    }

    #st-status {
        width: 1fr;
        content-align: left middle;
    }

    #st-progress {
        margin-top: 1;
        display: none;
    }

    #st-result-row {
        height: 6;
        margin-bottom: 1;
    }

    #st-history-panel {
        height: 1fr;
    }

    #st-history-row {
        height: 1fr;
    }

    #st-history-table-container {
        width: 2fr;
        margin-right: 1;
        height: 100%;
    }

    #st-history-chart-container {
        width: 1fr;
        height: 100%;
    }

    #st-sparkline-down {
        height: 4;
    }

    #st-sparkline-up {
        height: 4;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel", id="st-action-row"):
            with Horizontal():
                yield Button("▶ Rodar teste agora", id="st-run-btn")
                yield Static("", id="st-status")
            yield ProgressBar(id="st-progress", show_percentage=False, show_eta=False)

        with Horizontal(classes="panel", id="st-result-row"):
            yield StatCard("DOWNLOAD", "-", "st-download")
            yield StatCard("UPLOAD", "-", "st-upload")
            yield StatCard("PING", "-", "st-ping")
            yield StatCard("SERVIDOR", "-", "st-server")
            yield StatCard("QUANDO", "-", "st-when")

        with Vertical(classes="panel", id="st-history-panel"):
            yield Static("HISTÓRICO DE TESTES", classes="panel-title")
            with Horizontal(id="st-history-row"):
                with Vertical(id="st-history-table-container"):
                    yield DataTable(id="st-history-table", cursor_type="none", zebra_stripes=True)
                    yield Static("", id="st-history-empty", classes="empty-state")
                with Vertical(id="st-history-chart-container"):
                    yield Static("Download (Mbps)", classes="muted")
                    yield Sparkline([0.0] * 20, id="st-sparkline-down")
                    yield Static("Upload (Mbps)", classes="muted")
                    yield Sparkline([0.0] * 20, id="st-sparkline-up")

    def on_mount(self) -> None:
        table = self.query_one("#st-history-table", DataTable)
        table.add_column("Data/Hora", width=16)
        table.add_column("Download", width=12)
        table.add_column("Upload", width=12)
        table.add_column("Ping", width=8)
        table.add_column("Servidor", width=22)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "st-run-btn":
            self.app.backend.start_speed_test()  # type: ignore[attr-defined]
            self.refresh_data(self.app.backend)  # type: ignore[attr-defined]

    def refresh_data(self, backend: OmniaMockBackend) -> None:
        if not backend.ready:
            return

        run_btn = self.query_one("#st-run-btn", Button)
        progress = self.query_one("#st-progress", ProgressBar)
        status = self.query_one("#st-status", Static)

        run_btn.disabled = backend.speedtest_running
        if backend.speedtest_running:
            progress.display = True
            status.update("Testando conexão... isso pode levar até 30 segundos.")
        else:
            progress.display = False
            if backend.speedtest_error:
                status.update(f"[b]Falha no teste:[/b] {backend.speedtest_error}")
            elif backend.speedtest_history:
                status.update("Pronto para rodar um novo teste.")
            else:
                status.update("Nenhum teste rodado ainda.")

        history = backend.speedtest_history
        if history:
            latest = history[-1]
            self.query_one("#st-download-value", Static).update(f"{latest.download_mbps:.0f} Mbps")
            self.query_one("#st-upload-value", Static).update(f"{latest.upload_mbps:.0f} Mbps")
            self.query_one("#st-ping-value", Static).update(f"{latest.ping_ms:.0f} ms")
            self.query_one("#st-server-value", Static).update(latest.server_name)
            self.query_one("#st-when-value", Static).update(_fmt_dt(latest.ts))
        else:
            for wid in ("st-download", "st-upload", "st-ping", "st-server", "st-when"):
                self.query_one(f"#{wid}-value", Static).update("-")

        table = self.query_one("#st-history-table", DataTable)
        empty = self.query_one("#st-history-empty", Static)
        table.clear()
        if history:
            table.display = True
            empty.display = False
            for result in reversed(history[-30:]):
                table.add_row(
                    _fmt_dt(result.ts),
                    f"{result.download_mbps:.0f} Mbps",
                    f"{result.upload_mbps:.0f} Mbps",
                    f"{result.ping_ms:.0f} ms",
                    result.server_name,
                )
            self.query_one("#st-sparkline-down", Sparkline).data = [r.download_mbps for r in history[-20:]]
            self.query_one("#st-sparkline-up", Sparkline).data = [r.upload_mbps for r in history[-20:]]
        else:
            table.display = False
            empty.display = True
            empty.update("Nenhum teste rodado ainda. Use \"Rodar teste agora\" para começar.")

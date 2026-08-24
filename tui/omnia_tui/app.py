"""
Omnia — painel de administração de servidor pessoal (TUI).

Uso único-operador: 5 seções (Segurança SSH, Sistema, Speed Test,
Ferramentas Extras, Alertas), navegáveis por aba/atalho numérico.

Conectado ao daemon real via socket Unix (ver backend.py). Para rodar contra
dados simulados em vez do daemon (ex: para demonstrar a UI sem um servidor
Linux real por perto), defina a variável de ambiente OMNIA_SIMULATE=1.
"""

from __future__ import annotations

import os
import time
from datetime import datetime

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Static, TabbedContent, TabPane

from .backend import OmniaBackend
from .data import OmniaMockBackend
from .screens.alerts import AlertsPanel
from .screens.speedtest import SpeedTestPanel
from .screens.ssh import SSHPanel
from .screens.system import SystemPanel
from .screens.tools import ToolsPanel
from .theme import BASE_CSS, status_color

TICK_SECONDS = 1.0
DEFAULT_SOCKET_PATH = "/run/omnia/omnia.sock"


class OmniaHeader(Static):
    """Cabeçalho customizado: hostname, relógio e status da conexão."""

    DEFAULT_CSS = """
    OmniaHeader {
        height: 1;
        background: $panel;
        color: $text;
        padding: 0 1;
    }
    """

    def render(self) -> str:
        return self._text if hasattr(self, "_text") else "Omnia"

    def set_content(self, hostname: str, conn_state: str, last_update_ago: str) -> None:
        now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        color = status_color(conn_state)
        labels = {"connected": "conectado", "reconnecting": "reconectando", "disconnected": "desconectado"}
        label = labels.get(conn_state, conn_state)
        self._text = (
            f"🖥  [b]{hostname}[/b]    {now}    "
            f"[{color}]●[/{color}] {label}    "
            f"[dim]atualizado há {last_update_ago}[/dim]"
        )
        self.update(self._text)


class OmniaApp(App):
    CSS = BASE_CSS + """
    TabbedContent {
        height: 1fr;
    }

    TabPane {
        height: 1fr;
        padding: 1;
    }

    #omnia-loading {
        height: 1fr;
        content-align: center middle;
    }
    """

    TITLE = "Omnia"

    BINDINGS = [
        Binding("q", "quit", "Sair"),
        Binding("ctrl+c", "quit", "Sair", show=False),
        Binding("1", "goto_ssh", "SSH"),
        Binding("2", "goto_system", "Sistema"),
        Binding("3", "goto_speedtest", "Speed Test"),
        Binding("4", "goto_tools", "Ferramentas"),
        Binding("5", "goto_alerts", "Alertas"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._simulate = os.environ.get("OMNIA_SIMULATE") == "1"
        if self._simulate:
            self.backend = OmniaMockBackend()
        else:
            socket_path = os.environ.get("OMNIA_SOCKET_PATH", DEFAULT_SOCKET_PATH)
            self.backend = OmniaBackend(socket_path)
        self._last_update_ts = time.time()

    def compose(self) -> ComposeResult:
        yield OmniaHeader(id="omnia-header")
        yield Static("Carregando Omnia...", id="omnia-loading", classes="loading-state")
        with TabbedContent(initial="ssh", id="omnia-tabs"):
            with TabPane("1 SSH", id="ssh"):
                yield SSHPanel()
            with TabPane("2 Sistema", id="system"):
                yield SystemPanel()
            with TabPane("3 Speed Test", id="speedtest"):
                yield SpeedTestPanel()
            with TabPane("4 Ferramentas", id="tools"):
                yield ToolsPanel()
            with TabPane("5 Alertas", id="alerts"):
                yield AlertsPanel()
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#omnia-tabs", TabbedContent).display = False
        if not self._simulate:
            self.run_worker(self.backend.run_forever(), exclusive=True, name="omnia-backend")
        self.set_interval(TICK_SECONDS, self._tick)

    def _tick(self) -> None:
        was_ready = self.backend.ready
        self.backend.tick()
        if self.backend.ready:
            self._last_update_ts = time.time()

        if self.backend.ready and not was_ready:
            self.query_one("#omnia-loading", Static).display = False
            self.query_one("#omnia-tabs", TabbedContent).display = True

        ago = f"{int(time.time() - self._last_update_ts)}s"
        self.query_one(OmniaHeader).set_content(self.backend.hostname, self.backend.connection_state, ago)

        self.query_one(SSHPanel).refresh_data(self.backend)
        self.query_one(SystemPanel).refresh_data(self.backend)
        self.query_one(SpeedTestPanel).refresh_data(self.backend)
        self.query_one(ToolsPanel).refresh_data(self.backend)
        self.query_one(AlertsPanel).refresh_data(self.backend)

    # -- navegação ----------------------------------------------------

    def _goto(self, tab_id: str) -> None:
        # Limpa o foco antes de trocar de aba: caso contrário, se um widget
        # da aba atual mantiver o foco (ex: botão recém-clicado), o
        # TabbedContent reverte a troca silenciosamente.
        self.set_focus(None)
        self.query_one(TabbedContent).active = tab_id

    def action_goto_ssh(self) -> None:
        self._goto("ssh")

    def action_goto_system(self) -> None:
        self._goto("system")

    def action_goto_speedtest(self) -> None:
        self._goto("speedtest")

    def action_goto_tools(self) -> None:
        self._goto("tools")

    def action_goto_alerts(self) -> None:
        self._goto("alerts")


def main() -> None:
    OmniaApp().run()


if __name__ == "__main__":
    main()

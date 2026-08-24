"""Seção 1 — Segurança SSH."""

from __future__ import annotations

import time
from datetime import datetime

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Static

from ..data import OmniaMockBackend, fmt_ago, fmt_uptime
from ..theme import ERROR, NEUTRAL, SUCCESS, WARNING

RESULT_LABELS = {
    "success": "sucesso",
    "failure": "falha",
    "invalid_user": "usuário inválido",
    "disconnected": "desconectado",
}

PAGE_SIZE = 20


def _fmt_dt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m %H:%M:%S")


def _result_text(result: str) -> Text:
    color = {"success": SUCCESS, "failure": ERROR, "invalid_user": ERROR}.get(result, NEUTRAL)
    return Text(RESULT_LABELS.get(result, result), style=color)


class SSHPanel(Vertical):
    DEFAULT_CSS = """
    SSHPanel {
        height: 1fr;
    }

    #ssh-active-panel {
        height: 8;
        margin-bottom: 1;
    }

    #ssh-history-panel {
        height: 1fr;
        margin-bottom: 1;
    }

    #ssh-history-toolbar {
        height: 3;
    }

    #ssh-history-filter {
        width: 40;
        margin-right: 1;
    }

    #ssh-history-loadmore {
        width: auto;
    }

    #ssh-history-count {
        width: 1fr;
        content-align: right middle;
        color: $text-muted;
    }

    #ssh-lower-row {
        height: 12;
    }

    #ssh-keys-panel {
        width: 1fr;
        margin-right: 1;
    }

    #ssh-blocked-panel {
        width: 1fr;
    }
    """

    visible_count: int = PAGE_SIZE

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel", id="ssh-active-panel"):
            yield Static("SESSÕES SSH ATIVAS AGORA", classes="panel-title")
            yield DataTable(id="ssh-active-table", cursor_type="none", zebra_stripes=True)
            yield Static("", id="ssh-active-empty", classes="empty-state")

        with Vertical(classes="panel", id="ssh-history-panel"):
            yield Static("LOGINS RECENTES", classes="panel-title")
            with Horizontal(id="ssh-history-toolbar"):
                yield Input(placeholder="Buscar por usuário ou IP...", id="ssh-history-filter")
                yield Button("Carregar mais", id="ssh-history-loadmore")
                yield Static("", id="ssh-history-count")
            yield DataTable(id="ssh-history-table", cursor_type="none", zebra_stripes=True)

        with Horizontal(id="ssh-lower-row"):
            with Vertical(classes="panel", id="ssh-keys-panel"):
                yield Static("CHAVES CADASTRADAS", classes="panel-title")
                yield DataTable(id="ssh-keys-table", cursor_type="none", zebra_stripes=True)

            with Vertical(classes="panel", id="ssh-blocked-panel"):
                yield Static("IPS BLOQUEADOS", classes="panel-title")
                yield DataTable(id="ssh-blocked-table", cursor_type="none", zebra_stripes=True)
                yield Static("", id="ssh-blocked-empty", classes="unavailable-state")

    def on_mount(self) -> None:
        active = self.query_one("#ssh-active-table", DataTable)
        active.add_column("Usuário", width=10)
        active.add_column("IP de origem", width=16)
        active.add_column("Terminal", width=8)
        active.add_column("Chave", width=16)
        active.add_column("Tipo", width=9)
        active.add_column("Conectado há", width=14)

        history = self.query_one("#ssh-history-table", DataTable)
        history.add_column("Data/Hora", width=16)
        history.add_column("Usuário", width=10)
        history.add_column("IP", width=16)
        history.add_column("Resultado", width=16)
        history.add_column("Chave/Detalhe", width=22)

        keys = self.query_one("#ssh-keys-table", DataTable)
        keys.add_column("Rótulo", width=16)
        keys.add_column("Tipo", width=9)
        keys.add_column("Fingerprint", width=16)
        keys.add_column("Último uso", width=14)

        blocked = self.query_one("#ssh-blocked-table", DataTable)
        blocked.add_column("IP", width=16)
        blocked.add_column("Regra/Serviço", width=14)
        blocked.add_column("Banido há", width=14)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ssh-history-loadmore":
            self.visible_count += PAGE_SIZE
            self.refresh_data(self.app.backend)  # type: ignore[attr-defined]

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "ssh-history-filter":
            self.visible_count = PAGE_SIZE
            self.refresh_data(self.app.backend)  # type: ignore[attr-defined]

    def refresh_data(self, backend: OmniaMockBackend) -> None:
        if not backend.ready:
            return

        # sessões ativas
        active_table = self.query_one("#ssh-active-table", DataTable)
        active_empty = self.query_one("#ssh-active-empty", Static)
        active_table.clear()
        if backend.ssh_active_sessions:
            active_table.display = True
            active_empty.display = False
            for s in backend.ssh_active_sessions:
                active_table.add_row(
                    s.user, s.ip, s.tty, s.key_label, s.key_type, fmt_uptime(time.time() - s.connected_since)
                )
        else:
            active_table.display = False
            active_empty.display = True
            active_empty.update("Nenhuma sessão SSH ativa no momento.")

        # histórico com filtro
        query = self.query_one("#ssh-history-filter", Input).value.strip().lower()
        events = backend.login_history
        if query:
            events = [e for e in events if query in e.user.lower() or query in e.ip.lower()]

        visible = events[: self.visible_count]
        history_table = self.query_one("#ssh-history-table", DataTable)
        history_table.clear()
        for i, event in enumerate(visible):
            flagged = False
            if event.result == "failure":
                same_ip_recent = [e for e in events[: i + 6] if e.ip == event.ip and e.result == "failure"]
                flagged = len(same_ip_recent) >= 3
            ip_text = Text(("⚠ " if flagged else "") + event.ip, style=ERROR if flagged else "")
            extra = event.key_label if event.result == "success" else event.detail
            history_table.add_row(_fmt_dt(event.ts), event.user, ip_text, _result_text(event.result), extra)

        count_widget = self.query_one("#ssh-history-count", Static)
        count_widget.update(f"mostrando {len(visible)} de {len(events)}")
        loadmore = self.query_one("#ssh-history-loadmore", Button)
        loadmore.disabled = self.visible_count >= len(events)

        # chaves
        keys_table = self.query_one("#ssh-keys-table", DataTable)
        keys_table.clear()
        for key in backend.ssh_keys:
            last_used = fmt_ago(key.last_used) if key.last_used else "nunca usada"
            keys_table.add_row(key.label, key.key_type, key.fingerprint, last_used)

        # ips bloqueados
        blocked_table = self.query_one("#ssh-blocked-table", DataTable)
        blocked_empty = self.query_one("#ssh-blocked-empty", Static)
        blocked_table.clear()
        if not backend.fail2ban_available:
            blocked_table.display = False
            blocked_empty.display = True
            blocked_empty.update("Recurso não disponível neste servidor (sem proteção contra força bruta instalada).")
        elif not backend.blocked_ips:
            blocked_table.display = False
            blocked_empty.display = True
            blocked_empty.remove_class("unavailable-state")
            blocked_empty.add_class("empty-state")
            blocked_empty.update("Nenhum IP bloqueado no momento.")
        else:
            blocked_table.display = True
            blocked_empty.display = False
            for b in backend.blocked_ips:
                blocked_table.add_row(b.ip, b.rule, fmt_ago(b.banned_at))

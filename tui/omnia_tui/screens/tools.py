"""Seção 4 — Ferramentas Extras."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Static
from rich.text import Text

from ..data import OmniaMockBackend
from ..theme import ERROR, SUCCESS, WARNING, status_color
from ..widgets import ConfirmDialog, StatCard


class ToolsPanel(Vertical):
    DEFAULT_CSS = """
    ToolsPanel {
        height: 1fr;
    }

    #tools-top-row {
        height: 12;
        margin-bottom: 1;
    }

    #tools-services-panel {
        width: 1fr;
        margin-right: 1;
    }

    #tools-containers-panel {
        width: 1fr;
    }

    #tools-mid-row {
        height: 9;
        margin-bottom: 1;
    }

    #tools-updates-panel {
        width: 1fr;
        margin-right: 1;
    }

    #tools-diskhealth-panel {
        width: 2fr;
    }

    #tools-danger-panel {
        height: 1fr;
        border: solid #f87171;
    }

    #tools-danger-panel .panel-title {
        color: #f87171;
    }

    #tools-danger-row {
        height: auto;
        margin-top: 1;
    }

    #tools-danger-row Button {
        margin-right: 2;
    }

    #tools-danger-note {
        margin-top: 1;
        color: $text-muted;
    }
    """

    selected_service: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="tools-top-row"):
            with Vertical(classes="panel", id="tools-services-panel"):
                yield Static("SERVIÇOS DO SISTEMA", classes="panel-title")
                yield DataTable(id="tools-services-table", cursor_type="row", zebra_stripes=True)

            with Vertical(classes="panel", id="tools-containers-panel"):
                yield Static("CONTÊINERES", classes="panel-title")
                yield DataTable(id="tools-containers-table", cursor_type="none", zebra_stripes=True)
                yield Static("", id="tools-containers-empty", classes="unavailable-state")

        with Horizontal(id="tools-mid-row"):
            yield StatCard("ATUALIZAÇÕES PENDENTES", "-", "updates")
            with Vertical(classes="panel", id="tools-diskhealth-panel"):
                yield Static("SAÚDE DOS DISCOS", classes="panel-title")
                yield DataTable(id="tools-diskhealth-table", cursor_type="none", zebra_stripes=True)

        with Vertical(classes="panel", id="tools-danger-panel"):
            yield Static("⚠ AÇÕES SENSÍVEIS", classes="panel-title")
            yield Static("", id="tools-danger-selected", classes="muted")
            with Horizontal(id="tools-danger-row"):
                yield Button("Reiniciar serviço selecionado", id="restart-service-btn", classes="-danger")
                yield Button("Reiniciar servidor", id="reboot-btn", classes="-danger")
            yield Static("", id="tools-danger-note")

    def on_mount(self) -> None:
        services = self.query_one("#tools-services-table", DataTable)
        services.add_column("Serviço", width=18, key="name")
        services.add_column("Status", width=12, key="status")

        containers = self.query_one("#tools-containers-table", DataTable)
        containers.add_column("Nome", width=16)
        containers.add_column("Imagem", width=20)
        containers.add_column("Status", width=10)
        containers.add_column("Estado", width=12)

        diskhealth = self.query_one("#tools-diskhealth-table", DataTable)
        diskhealth.add_column("Dispositivo", width=14)
        diskhealth.add_column("Status", width=14)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "tools-services-table" and event.row_key.value is not None:
            self.selected_service = str(event.row_key.value)
            self.query_one("#tools-danger-selected", Static).update(
                f"Serviço selecionado para reinício: [b]{self.selected_service}[/b]"
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        backend: OmniaMockBackend = self.app.backend  # type: ignore[attr-defined]

        if event.button.id == "restart-service-btn":
            if not self.selected_service:
                self.query_one("#tools-danger-note", Static).update(
                    "Selecione um serviço na tabela acima primeiro."
                )
                return
            name = self.selected_service
            self.app.push_screen(
                ConfirmDialog(
                    "Reiniciar serviço",
                    f'Isso vai reiniciar o serviço "{name}" agora. A operação é imediata '
                    "e pode causar uma breve indisponibilidade. Tem certeza?",
                    confirm_label="Reiniciar",
                    danger=True,
                ),
                lambda confirmed: self._on_restart_confirmed(confirmed, name),
            )

        elif event.button.id == "reboot-btn":
            self.app.push_screen(
                ConfirmDialog(
                    "Reiniciar servidor",
                    "Isso vai reiniciar o servidor inteiro agora, encerrando todas as "
                    "sessões e serviços por alguns instantes. Esta ação é irreversível. "
                    "Tem certeza?",
                    confirm_label="Reiniciar servidor",
                    danger=True,
                ),
                self._on_reboot_confirmed,
            )

    def _on_restart_confirmed(self, confirmed: bool, name: str) -> None:
        backend: OmniaMockBackend = self.app.backend  # type: ignore[attr-defined]
        if confirmed:
            backend.restart_service(name)
            self.query_one("#tools-danger-note", Static).update(f'Reiniciando "{name}"...')
            self.refresh_data(backend)

    def _on_reboot_confirmed(self, confirmed: bool) -> None:
        backend: OmniaMockBackend = self.app.backend  # type: ignore[attr-defined]
        if confirmed:
            backend.reboot_server()
            self.query_one("#tools-danger-note", Static).update("Servidor reiniciado.")
            self.refresh_data(backend)

    def refresh_data(self, backend: OmniaMockBackend) -> None:
        if not backend.ready:
            return

        services_table = self.query_one("#tools-services-table", DataTable)
        current_key = self.selected_service
        services_table.clear()
        for svc in backend.services:
            label = svc.status
            if svc.name == backend.restarting_service_name():
                label = "reiniciando..."
            status_text = Text(label, style=status_color("active" if label == "active" else ("warning" if label == "reiniciando..." else "error")))
            services_table.add_row(svc.name, status_text, key=svc.name)
        if current_key:
            try:
                services_table.move_cursor(row=services_table.get_row_index(current_key))
            except Exception:
                pass

        containers_table = self.query_one("#tools-containers-table", DataTable)
        containers_empty = self.query_one("#tools-containers-empty", Static)
        containers_table.clear()
        if not backend.containers_available:
            containers_table.display = False
            containers_empty.display = True
            containers_empty.update("Não instalado neste servidor.")
        elif not backend.containers:
            containers_table.display = False
            containers_empty.display = True
            containers_empty.remove_class("unavailable-state")
            containers_empty.add_class("empty-state")
            containers_empty.update("Nenhum contêiner em execução.")
        else:
            containers_table.display = True
            containers_empty.display = False
            for c in backend.containers:
                containers_table.add_row(c.name, c.image, c.status, c.state)

        updates_widget = self.query_one("#updates-value", Static)
        kind = "warning" if backend.pending_updates_count > 0 else "success"
        updates_widget.remove_class("warning", "success")
        updates_widget.add_class(kind)
        updates_widget.update(f"{backend.pending_updates_count} ({backend.pending_updates_manager})")

        diskhealth_table = self.query_one("#tools-diskhealth-table", DataTable)
        diskhealth_table.clear()
        for dh in backend.disk_health:
            diskhealth_table.add_row(dh.device, Text(dh.status, style=status_color(dh.status)))

        restart_btn = self.query_one("#restart-service-btn", Button)
        reboot_btn = self.query_one("#reboot-btn", Button)
        note = self.query_one("#tools-danger-note", Static)
        if not backend.dangerous_actions_enabled:
            restart_btn.disabled = True
            reboot_btn.disabled = True
            note.update("Ações sensíveis estão desabilitadas nas configurações do servidor.")

"""Widgets compartilhados entre telas do Omnia."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from .theme import ERROR, SUCCESS, WARNING


class StatCard(Vertical):
    """Card pequeno: rótulo em cima, valor grande embaixo."""

    def __init__(self, label: str, initial: str = "-", widget_id: str = "") -> None:
        super().__init__(classes="stat-card")
        self._label_text = label
        self._initial = initial
        self._value_id = f"{widget_id}-value"

    def compose(self) -> ComposeResult:
        yield Static(self._label_text, classes="stat-label")
        yield Static(self._initial, id=self._value_id, classes="stat-value")

    def update_value(self, value: str, kind: str = "") -> None:
        widget = self.query_one(f"#{self._value_id}", Static)
        widget.remove_class("warning", "error", "success")
        if kind:
            widget.add_class(kind)
        widget.update(value)


def pct_bar(pct: float, width: int = 20) -> str:
    """Barra de progresso em texto puro (blocos unicode)."""
    pct = max(0.0, min(100.0, pct))
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def pct_color(pct: float) -> str:
    if pct >= 90:
        return ERROR
    if pct >= 75:
        return WARNING
    return SUCCESS


class ConfirmDialog(ModalScreen[bool]):
    """Modal de confirmação genérico. Retorna True/False via dismiss()."""

    DEFAULT_CSS = """
    ConfirmDialog {
        align: center middle;
    }

    #confirm-box {
        width: 64;
        height: auto;
        border: solid $accent;
        background: $panel;
        padding: 1 2;
    }

    #confirm-title {
        text-style: bold;
        height: 1;
        margin-bottom: 1;
    }

    #confirm-message {
        width: 100%;
        padding-bottom: 1;
    }

    #confirm-buttons {
        width: 100%;
        height: auto;
        align-horizontal: right;
    }

    #confirm-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancelar")]

    def __init__(self, title: str, message: str, confirm_label: str = "Confirmar", danger: bool = False) -> None:
        super().__init__()
        self._title = title
        self._message = message
        self._confirm_label = confirm_label
        self._danger = danger

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(self._title, id="confirm-title")
            yield Static(self._message, id="confirm-message")
            with Horizontal(id="confirm-buttons"):
                yield Button("Cancelar", id="cancel-btn")
                yield Button(
                    self._confirm_label,
                    id="confirm-btn",
                    classes="-danger" if self._danger else "",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-btn")

    def action_cancel(self) -> None:
        self.dismiss(False)

"""Seção 2 — Estado do Sistema."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Sparkline, Static

from ..data import OmniaMockBackend, fmt_bytes, fmt_uptime
from ..theme import ERROR, SUCCESS, WARNING
from ..widgets import StatCard, pct_bar, pct_color


class SystemPanel(Vertical):
    DEFAULT_CSS = """
    SystemPanel {
        height: 1fr;
    }

    #sys-stats-row {
        height: 4;
        margin-bottom: 1;
    }

    #sys-cores-row {
        height: 8;
        margin-bottom: 1;
    }

    #sys-cores-panel {
        width: 2fr;
        margin-right: 1;
    }

    #sys-temps-panel {
        width: 1fr;
    }

    #sys-disks-panel {
        height: 6;
        margin-bottom: 1;
    }

    #sys-net-gpu-row {
        height: 8;
        margin-bottom: 1;
    }

    #sys-net-panel {
        width: 1fr;
        margin-right: 1;
    }

    #sys-gpu-panel {
        width: 1fr;
    }

    #sys-net-sparkline {
        height: 3;
    }

    #sys-processes-panel {
        height: 1fr;
    }

    #sys-processes-toolbar {
        height: 3;
    }

    #sys-processes-toolbar Button {
        margin-right: 1;
    }
    """

    sort_by: str = "cpu"

    def compose(self) -> ComposeResult:
        with Horizontal(id="sys-stats-row"):
            yield StatCard("CPU TOTAL", "-", "cpu-total")
            yield StatCard("MEMÓRIA", "-", "mem")
            yield StatCard("SWAP", "-", "swap")
            yield StatCard("UPTIME", "-", "uptime")

        with Horizontal(id="sys-cores-row"):
            with Vertical(classes="panel", id="sys-cores-panel"):
                yield Static("USO POR NÚCLEO", classes="panel-title")
                yield DataTable(id="sys-cores-table", cursor_type="none")

            with Vertical(classes="panel", id="sys-temps-panel"):
                yield Static("TEMPERATURA", classes="panel-title")
                yield DataTable(id="sys-temps-table", cursor_type="none")
                yield Static("", id="sys-temps-empty", classes="unavailable-state")

        with Vertical(classes="panel", id="sys-disks-panel"):
            yield Static("DISCOS", classes="panel-title")
            yield DataTable(id="sys-disks-table", cursor_type="none", zebra_stripes=True)

        with Horizontal(id="sys-net-gpu-row"):
            with Vertical(classes="panel", id="sys-net-panel"):
                yield Static("REDE", classes="panel-title")
                yield Static("", id="sys-net-speeds")
                yield Sparkline([0.0] * 40, id="sys-net-sparkline")

            with Vertical(classes="panel", id="sys-gpu-panel"):
                yield Static("GPU", classes="panel-title")
                yield DataTable(id="sys-gpu-table", cursor_type="none")
                yield Static("", id="sys-gpu-empty", classes="unavailable-state")

        with Vertical(classes="panel", id="sys-processes-panel"):
            yield Static("PROCESSOS", classes="panel-title")
            with Horizontal(id="sys-processes-toolbar"):
                yield Button("Ordenar por CPU", id="sort-cpu-btn")
                yield Button("Ordenar por memória", id="sort-mem-btn")
            yield DataTable(id="sys-processes-table", cursor_type="none", zebra_stripes=True)

    def on_mount(self) -> None:
        cores = self.query_one("#sys-cores-table", DataTable)
        cores.add_column("Núcleo", width=8)
        cores.add_column("Uso", width=32)

        temps = self.query_one("#sys-temps-table", DataTable)
        temps.add_column("Sensor", width=10)
        temps.add_column("Temp.", width=10)

        disks = self.query_one("#sys-disks-table", DataTable)
        disks.add_column("Ponto de montagem", width=16)
        disks.add_column("Dispositivo", width=12)
        disks.add_column("FS", width=6)
        disks.add_column("Usado/Total", width=18)
        disks.add_column("Uso", width=26)

        gpu = self.query_one("#sys-gpu-table", DataTable)
        gpu.add_column("Engine", width=14)
        gpu.add_column("Uso", width=22)

        procs = self.query_one("#sys-processes-table", DataTable)
        procs.add_column("PID", width=8)
        procs.add_column("Processo", width=20)
        procs.add_column("CPU %", width=10)
        procs.add_column("MEM %", width=10)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sort-cpu-btn":
            self.sort_by = "cpu"
        elif event.button.id == "sort-mem-btn":
            self.sort_by = "mem"
        self.refresh_data(self.app.backend)  # type: ignore[attr-defined]

    def refresh_data(self, backend: OmniaMockBackend) -> None:
        if not backend.ready:
            return

        cpu_total = backend.cpu_total_pct()
        self.query_one("#cpu-total-value", Static).update(f"{cpu_total:.0f}%")
        mem_pct = backend.mem_pct()
        self.query_one("#mem-value", Static).update(
            f"{fmt_bytes(backend.mem_used_mb)}/{fmt_bytes(backend.mem_total_mb)} ({mem_pct:.0f}%)"
        )
        self.query_one("#swap-value", Static).update(f"{backend.swap_pct:.0f}%")
        self.query_one("#uptime-value", Static).update(fmt_uptime(backend.uptime_seconds()))

        cores_table = self.query_one("#sys-cores-table", DataTable)
        cores_table.clear()
        for i, pct in enumerate(backend.cpu_per_core):
            cores_table.add_row(f"#{i}", f"{pct_bar(pct, 20)} {pct:.0f}%")

        temps_table = self.query_one("#sys-temps-table", DataTable)
        temps_empty = self.query_one("#sys-temps-empty", Static)
        temps_table.clear()
        if backend.temps_available and backend.temps:
            temps_table.display = True
            temps_empty.display = False
            for label, celsius in backend.temps.items():
                temps_table.add_row(label, f"{celsius:.0f}°C")
        else:
            temps_table.display = False
            temps_empty.display = True
            temps_empty.update("Não disponível neste servidor.")

        disks_table = self.query_one("#sys-disks-table", DataTable)
        disks_table.clear()
        for disk in backend.disks:
            disks_table.add_row(
                disk.mount, disk.device, disk.fstype,
                f"{disk.used_gb:.0f}/{disk.total_gb:.0f} GB",
                f"{pct_bar(disk.pct, 18)} {disk.pct:.0f}%",
            )

        self.query_one("#sys-net-speeds", Static).update(
            f"↓ {backend.net_download_kbps:.0f} KB/s     ↑ {backend.net_upload_kbps:.0f} KB/s"
        )
        self.query_one("#sys-net-sparkline", Sparkline).data = list(backend.net_history_down)

        gpu_table = self.query_one("#sys-gpu-table", DataTable)
        gpu_empty = self.query_one("#sys-gpu-empty", Static)
        gpu_table.clear()
        if backend.gpu_available and backend.gpu_engines:
            gpu_table.display = True
            gpu_empty.display = False
            for engine, pct in backend.gpu_engines.items():
                gpu_table.add_row(engine, f"{pct_bar(pct, 16)} {pct:.0f}%")
        else:
            gpu_table.display = False
            gpu_empty.display = True
            gpu_empty.update("Não disponível neste servidor.")

        procs_table = self.query_one("#sys-processes-table", DataTable)
        procs_table.clear()
        for proc in backend.sorted_processes(self.sort_by)[:12]:
            procs_table.add_row(str(proc.pid), proc.name, f"{proc.cpu_pct:.1f}", f"{proc.mem_pct:.1f}")

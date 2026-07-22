from __future__ import annotations

import calendar
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from tkinter import messagebox, ttk

from .desktop_stable import StableDesktopApp
from .homepicz_scheduler import ImportWindow, current_import_window
from .operational_inventory import (
    MAX_WORKERS,
    OperationalFolder,
    OperationalInventory,
    _scan_work,
    fetch_operational_works,
)


MONTH_NAMES = (
    "",
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
)
WEEKDAY_NAMES = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")


def scan_raw_inventory_for_date(settings, selected_date: date) -> OperationalInventory:
    """Gera o inventário RAW para um único dia, sem alterar a janela do restante do sistema."""
    started = time.perf_counter()
    root = Path(settings.homepicz_photos_root).expanduser().resolve()
    requested_window = ImportWindow(selected_date, selected_date)
    window, works = fetch_operational_works(settings, requested_window)
    folders: list[OperationalFolder] = []
    errors: list[str] = []

    workers = max(1, min(MAX_WORKERS, len(works)))
    if works:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="raw-calendar-inventory") as executor:
            futures = {executor.submit(_scan_work, root, work): work["id"] for work in works}
            for future in as_completed(futures):
                work_id = futures[future]
                try:
                    item = future.result()
                except Exception as exc:
                    errors.append(
                        f"ID {work_id}: falha inesperada na contagem: {type(exc).__name__}: {exc}"
                    )
                    continue
                folders.append(item)
                errors.extend(f"ID {item.work_id}: {error}" for error in item.errors)

    folders.sort(key=lambda item: (item.scheduled_at, item.work_id.lower()))
    return OperationalInventory(
        root=str(root),
        window=window,
        folders=tuple(folders),
        elapsed_seconds=time.perf_counter() - started,
        errors=tuple(errors[:200]),
    )


class RawCalendarDesktopApp(StableDesktopApp):
    """LRAutomatic com seletor de data isolado para a tabela do Gerenciador de RAW."""

    def __init__(self, config_path: str = "config.json") -> None:
        super().__init__(config_path)
        self.title("LRAutomatic V5.0")
        self._stabilize_job_monitor_widgets()
        self.raw_inventory_date = current_import_window(self.settings).start
        self.raw_calendar_button = ttk.Button(
            self.inventory_button.master,
            text=self._raw_calendar_button_text(),
            style="Secondary.TButton",
            command=self._open_raw_calendar,
        )
        self.raw_calendar_button.grid(row=0, column=3, padx=(6, 0))

    def _stabilize_job_monitor_widgets(self) -> None:
        """Evita repintura desnecessária e torna os detalhes realmente roláveis."""
        if hasattr(self, "jobs_tree"):
            original_tag_configure = self.jobs_tree.tag_configure
            applied_tag_styles: dict[str, dict[str, object]] = {}

            def stable_tag_configure(tag_name: str, **options):
                if applied_tag_styles.get(tag_name) == options:
                    return None
                applied_tag_styles[tag_name] = dict(options)
                return original_tag_configure(tag_name, **options)

            self.jobs_tree.tag_configure = stable_tag_configure

        if hasattr(self, "detail_text"):
            detail_parent = self.detail_text.master
            detail_parent.columnconfigure(0, weight=1)
            detail_parent.columnconfigure(1, weight=0)
            self.detail_scrollbar = ttk.Scrollbar(
                detail_parent,
                orient="vertical",
                command=self.detail_text.yview,
            )
            self.detail_scrollbar.grid(row=3, column=1, sticky="ns")
            self.detail_text.configure(yscrollcommand=self.detail_scrollbar.set)

    def _render(self, job) -> None:
        """Atualiza os detalhes sem jogar a leitura de volta para o início."""
        same_job = getattr(self, "selected_job_id", None) == job.job_id
        previous_view = self.detail_text.yview() if same_job and hasattr(self, "detail_text") else None
        super()._render(job)
        if previous_view:
            first_fraction = previous_view[0]
            self.after_idle(lambda value=first_fraction: self.detail_text.yview_moveto(value))

    def _raw_calendar_button_text(self) -> str:
        return f"📅 {self.raw_inventory_date:%d/%m/%Y}"

    def _open_raw_calendar(self) -> None:
        popup = tk.Toplevel(self)
        popup.title("Escolher dia do Gerenciador de RAW")
        popup.resizable(False, False)
        popup.transient(self)
        popup.grab_set()

        content = ttk.Frame(popup, padding=14)
        content.grid(row=0, column=0, sticky="nsew")
        shown_year = tk.IntVar(value=self.raw_inventory_date.year)
        shown_month = tk.IntVar(value=self.raw_inventory_date.month)
        month_title = tk.StringVar()
        days_frame = ttk.Frame(content)

        header = ttk.Frame(content)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.columnconfigure(1, weight=1)
        ttk.Button(header, text="◀", width=3, command=lambda: change_month(-1)).grid(row=0, column=0)
        ttk.Label(header, textvariable=month_title, style="Section.TLabel", anchor="center").grid(
            row=0, column=1, sticky="ew", padx=12
        )
        ttk.Button(header, text="▶", width=3, command=lambda: change_month(1)).grid(row=0, column=2)
        days_frame.grid(row=1, column=0)

        def select_day(day: int) -> None:
            self.raw_inventory_date = date(shown_year.get(), shown_month.get(), day)
            self.raw_calendar_button.configure(text=self._raw_calendar_button_text())
            popup.destroy()
            self.inventory_state.set(
                f"Data do Gerenciador de RAW: {self.raw_inventory_date:%d/%m/%Y}. Gerando tabela..."
            )
            self._refresh_inventory()

        def render_month() -> None:
            for widget in days_frame.winfo_children():
                widget.destroy()
            year = shown_year.get()
            month = shown_month.get()
            month_title.set(f"{MONTH_NAMES[month]} de {year}")
            for column, name in enumerate(WEEKDAY_NAMES):
                ttk.Label(days_frame, text=name, anchor="center", width=4).grid(
                    row=0, column=column, padx=2, pady=(0, 4)
                )
            for row, week in enumerate(calendar.monthcalendar(year, month), start=1):
                for column, day in enumerate(week):
                    if day == 0:
                        ttk.Label(days_frame, text="", width=4).grid(row=row, column=column, padx=2, pady=2)
                        continue
                    selected = date(year, month, day) == self.raw_inventory_date
                    style = "Primary.TButton" if selected else "Secondary.TButton"
                    ttk.Button(
                        days_frame,
                        text=str(day),
                        width=4,
                        style=style,
                        command=lambda value=day: select_day(value),
                    ).grid(row=row, column=column, padx=2, pady=2)

        def change_month(delta: int) -> None:
            year = shown_year.get()
            month = shown_month.get() + delta
            if month < 1:
                month = 12
                year -= 1
            elif month > 12:
                month = 1
                year += 1
            shown_year.set(year)
            shown_month.set(month)
            render_month()

        footer = ttk.Frame(content)
        footer.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)

        def choose_today() -> None:
            today = date.today()
            shown_year.set(today.year)
            shown_month.set(today.month)
            select_day(today.day)

        ttk.Button(footer, text="Hoje", style="Secondary.TButton", command=choose_today).grid(row=0, column=1)
        ttk.Button(footer, text="Cancelar", style="Secondary.TButton", command=popup.destroy).grid(
            row=0, column=2, padx=(6, 0)
        )
        render_month()

    def _refresh_inventory(self) -> None:
        if self.inventory_scanning:
            return
        if not self.settings.homepicz_appscript_url:
            messagebox.showwarning(
                "Gerenciador de RAW",
                "Configure a URL do Google Apps Script para consultar os IDs da data escolhida.",
            )
            return
        if not self.settings.homepicz_photos_root:
            messagebox.showwarning(
                "Gerenciador de RAW",
                "Configure a pasta Fotos do dia nas Configurações.",
            )
            return

        selected_date = self.raw_inventory_date
        self.inventory_scanning = True
        self.inventory_button.configure(state="disabled", text="Consultando...")
        self.raw_calendar_button.configure(state="disabled")
        self.inventory_state.set(
            f"Consultando a agenda de {selected_date:%d/%m/%Y} e contando os arquivos RAW..."
        )

        def worker() -> None:
            try:
                snapshot = scan_raw_inventory_for_date(self.settings, selected_date)
            except Exception as exc:
                self.after(0, lambda error=exc: self._raw_calendar_inventory_failed(error))
                return
            self.after(0, lambda result=snapshot: self._raw_calendar_inventory_done(result))

        threading.Thread(target=worker, daemon=True, name="RawCalendarInventoryScan").start()

    def _raw_calendar_inventory_done(self, snapshot: OperationalInventory) -> None:
        self.raw_calendar_button.configure(state="normal", text=self._raw_calendar_button_text())
        self._inventory_done(snapshot)

    def _raw_calendar_inventory_failed(self, error: Exception) -> None:
        self.raw_calendar_button.configure(state="normal", text=self._raw_calendar_button_text())
        self._inventory_failed(error)


def main() -> None:
    RawCalendarDesktopApp().mainloop()


if __name__ == "__main__":
    main()

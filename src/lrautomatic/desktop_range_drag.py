from __future__ import annotations

import calendar
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from tkinter import messagebox, ttk

from tkinterdnd2 import COPY, DND_FILES, REFUSE_DROP, TkinterDnD

from .desktop_raw_created import RawCreatedDesktopApp
from .homepicz_editor_features import (
    fetch_editor_metadata,
    filter_work_dicts,
    load_editor_preferences,
)
from .homepicz_scheduler import ImportWindow
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


def scan_raw_inventory_for_window(
    settings,
    requested_window: ImportWindow,
) -> tuple[OperationalInventory, dict[str, dict[str, str]]]:
    """Monta o inventário do Gerenciador para um dia ou período inclusivo."""
    started = time.perf_counter()
    root = Path(settings.homepicz_photos_root).expanduser().resolve()
    window, works = fetch_operational_works(settings, requested_window)
    metadata = fetch_editor_metadata(settings, requested_window)

    enriched: list[dict[str, str]] = []
    for work in works:
        item = dict(work)
        item.update(metadata.get(str(item.get("id") or ""), {}))
        enriched.append(item)
    works = filter_work_dicts(settings, enriched, "manager_scope")

    folders: list[OperationalFolder] = []
    errors: list[str] = []
    workers = max(1, min(MAX_WORKERS, len(works)))
    if works:
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="raw-range-inventory",
        ) as executor:
            futures = {executor.submit(_scan_work, root, work): work["id"] for work in works}
            for future in as_completed(futures):
                work_id = futures[future]
                try:
                    item = future.result()
                except Exception as exc:
                    errors.append(
                        f"ID {work_id}: falha inesperada na contagem: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    continue
                folders.append(item)
                errors.extend(f"ID {item.work_id}: {error}" for error in item.errors)

    folders.sort(key=lambda item: (item.scheduled_at, item.work_id.lower()))
    snapshot = OperationalInventory(
        root=str(root),
        window=window,
        folders=tuple(folders),
        elapsed_seconds=time.perf_counter() - started,
        errors=tuple(errors[:200]),
    )
    return snapshot, {
        folder.work_id: metadata.get(folder.work_id, {})
        for folder in folders
    }


class RangeDragDesktopApp(RawCreatedDesktopApp):
    """Gerenciador com consulta por período e arraste nativo para outros programas."""

    def __init__(self, config_path: str = "config.json") -> None:
        # A classe-pai usa métodos virtuais durante a própria inicialização.
        # Estes valores precisam existir antes do super().__init__().
        initial = date.today()
        self.raw_inventory_start_date = initial
        self.raw_inventory_end_date = initial
        self._native_drag_ready = False
        self._native_drag_error = ""

        super().__init__(config_path)

        original_date = getattr(self, "raw_inventory_date", initial)
        self.raw_inventory_start_date = original_date
        self.raw_inventory_end_date = original_date
        self.raw_calendar_button.configure(text=self._raw_calendar_button_text())
        self.title("LRAutomatic V5.4")
        self._initialize_native_drag()

    def _initialize_native_drag(self) -> None:
        try:
            TkinterDnD.require(self)
            self._native_drag_ready = True
        except Exception as exc:
            self._native_drag_ready = False
            self._native_drag_error = f"{type(exc).__name__}: {exc}"

    def _selected_raw_window(self) -> ImportWindow:
        start = self.raw_inventory_start_date
        end = self.raw_inventory_end_date
        if end < start:
            start, end = end, start
        return ImportWindow(start, end)

    def _selected_raw_window_label(self) -> str:
        window = self._selected_raw_window()
        if window.start == window.end:
            return window.start.strftime("%d/%m/%Y")
        return f"{window.start:%d/%m/%Y} até {window.end:%d/%m/%Y}"

    def _raw_calendar_button_text(self) -> str:
        start = getattr(
            self,
            "raw_inventory_start_date",
            getattr(self, "raw_inventory_date", date.today()),
        )
        end = getattr(self, "raw_inventory_end_date", start)
        if start == end:
            return f"📅 {start:%d/%m/%Y}"
        return f"📅 {start:%d/%m/%Y} → {end:%d/%m/%Y}"

    def _open_raw_calendar(self) -> None:
        popup = tk.Toplevel(self)
        popup.title("Escolher data ou período do Gerenciador de RAW")
        popup.resizable(False, False)
        popup.transient(self)
        popup.grab_set()

        content = ttk.Frame(popup, padding=16)
        content.grid(row=0, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)

        selected_start = self.raw_inventory_start_date
        selected_end = self.raw_inventory_end_date
        mode_var = tk.StringVar(value="day" if selected_start == selected_end else "range")
        target_var = tk.StringVar(value="start")
        selection_var = tk.StringVar()
        instruction_var = tk.StringVar()
        shown_year = tk.IntVar(value=selected_start.year)
        shown_month = tk.IntVar(value=selected_start.month)
        month_title = tk.StringVar()

        mode_box = ttk.LabelFrame(content, text="Consulta", padding=(12, 8))
        mode_box.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Radiobutton(
            mode_box,
            text="Dia isolado",
            variable=mode_var,
            value="day",
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            mode_box,
            text="Período de datas",
            variable=mode_var,
            value="range",
        ).grid(row=0, column=1, sticky="w", padx=(18, 0))

        target_box = ttk.Frame(mode_box)
        target_box.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        start_target = ttk.Radiobutton(
            target_box,
            text="Alterar início",
            variable=target_var,
            value="start",
        )
        start_target.grid(row=0, column=0, sticky="w")
        end_target = ttk.Radiobutton(
            target_box,
            text="Alterar fim",
            variable=target_var,
            value="end",
        )
        end_target.grid(row=0, column=1, sticky="w", padx=(18, 0))

        ttk.Label(
            content,
            textvariable=selection_var,
            style="Section.TLabel",
            anchor="center",
        ).grid(row=1, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(
            content,
            textvariable=instruction_var,
            style="Muted.TLabel",
            anchor="center",
        ).grid(row=2, column=0, sticky="ew", pady=(0, 10))

        header = ttk.Frame(content)
        header.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        header.columnconfigure(1, weight=1)
        ttk.Button(
            header,
            text="◀",
            width=3,
            command=lambda: change_month(-1),
        ).grid(row=0, column=0)
        ttk.Label(
            header,
            textvariable=month_title,
            style="Section.TLabel",
            anchor="center",
        ).grid(row=0, column=1, sticky="ew", padx=12)
        ttk.Button(
            header,
            text="▶",
            width=3,
            command=lambda: change_month(1),
        ).grid(row=0, column=2)

        days_frame = ttk.Frame(content)
        days_frame.grid(row=4, column=0)

        def update_mode_state() -> None:
            nonlocal selected_end
            is_range = mode_var.get() == "range"
            state = "normal" if is_range else "disabled"
            start_target.configure(state=state)
            end_target.configure(state=state)
            if not is_range:
                selected_end = selected_start
                target_var.set("start")
            update_labels()
            render_month()

        def update_labels() -> None:
            if mode_var.get() == "day":
                selection_var.set(f"Dia selecionado: {selected_start:%d/%m/%Y}")
                instruction_var.set("Clique em um dia e depois em Aplicar.")
            else:
                selection_var.set(
                    f"Início: {selected_start:%d/%m/%Y}   •   "
                    f"Fim: {selected_end:%d/%m/%Y}"
                )
                alvo = "início" if target_var.get() == "start" else "fim"
                instruction_var.set(f"Clique no calendário para alterar o {alvo} do período.")

        def select_day(day_number: int) -> None:
            nonlocal selected_start, selected_end
            picked = date(shown_year.get(), shown_month.get(), day_number)
            if mode_var.get() == "day":
                selected_start = picked
                selected_end = picked
            elif target_var.get() == "start":
                selected_start = picked
                if selected_end < selected_start:
                    selected_end = selected_start
                target_var.set("end")
            else:
                selected_end = picked
                if selected_end < selected_start:
                    selected_start, selected_end = selected_end, selected_start
            update_labels()
            render_month()

        def render_month() -> None:
            for widget in days_frame.winfo_children():
                widget.destroy()

            year = shown_year.get()
            month = shown_month.get()
            month_title.set(f"{MONTH_NAMES[month]} de {year}")

            for column, name in enumerate(WEEKDAY_NAMES):
                ttk.Label(
                    days_frame,
                    text=name,
                    anchor="center",
                    width=4,
                ).grid(row=0, column=column, padx=2, pady=(0, 4))

            for row, week in enumerate(calendar.monthcalendar(year, month), start=1):
                for column, day_number in enumerate(week):
                    if day_number == 0:
                        ttk.Label(days_frame, text="", width=4).grid(
                            row=row,
                            column=column,
                            padx=2,
                            pady=2,
                        )
                        continue
                    candidate = date(year, month, day_number)
                    in_selection = (
                        candidate == selected_start
                        if mode_var.get() == "day"
                        else selected_start <= candidate <= selected_end
                    )
                    ttk.Button(
                        days_frame,
                        text=str(day_number),
                        width=4,
                        style="Primary.TButton" if in_selection else "Secondary.TButton",
                        command=lambda value=day_number: select_day(value),
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

        def choose_today() -> None:
            nonlocal selected_start, selected_end
            today = date.today()
            selected_start = today
            selected_end = today
            shown_year.set(today.year)
            shown_month.set(today.month)
            update_labels()
            render_month()

        def apply_selection() -> None:
            nonlocal selected_end
            if mode_var.get() == "day":
                selected_end = selected_start
            if selected_end < selected_start:
                messagebox.showwarning(
                    "Gerenciador de RAW",
                    "A data final não pode ser anterior à data inicial.",
                    parent=popup,
                )
                return

            self.raw_inventory_start_date = selected_start
            self.raw_inventory_end_date = selected_end
            # Mantém compatibilidade com recursos antigos que ainda leem a data isolada.
            self.raw_inventory_date = selected_start
            self.raw_calendar_button.configure(text=self._raw_calendar_button_text())
            popup.destroy()
            self.inventory_state.set(
                f"Período do Gerenciador de RAW: {self._selected_raw_window_label()}. "
                "Gerando tabela..."
            )
            self._refresh_inventory()

        mode_var.trace_add("write", lambda *_args: update_mode_state())
        target_var.trace_add("write", lambda *_args: update_labels())

        footer = ttk.Frame(content)
        footer.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Button(
            footer,
            text="Hoje",
            style="Secondary.TButton",
            command=choose_today,
        ).grid(row=0, column=1)
        ttk.Button(
            footer,
            text="Aplicar",
            style="Primary.TButton",
            command=apply_selection,
        ).grid(row=0, column=2, padx=(6, 0))
        ttk.Button(
            footer,
            text="Cancelar",
            style="Secondary.TButton",
            command=popup.destroy,
        ).grid(row=0, column=3, padx=(6, 0))

        update_mode_state()

    def _refresh_inventory(self) -> None:
        if self.inventory_scanning:
            return
        if not self.settings.homepicz_appscript_url:
            messagebox.showwarning(
                "Gerenciador de RAW",
                "Configure a URL do Google Apps Script para consultar os IDs do período escolhido.",
            )
            return
        if not self.settings.homepicz_photos_root:
            messagebox.showwarning(
                "Gerenciador de RAW",
                "Configure a pasta Fotos do dia nas Configurações.",
            )
            return

        prefs = load_editor_preferences(self.settings)
        if prefs.get("manager_scope") == "mine" and not prefs.get("editor_name"):
            messagebox.showwarning(
                "Gerenciador de RAW",
                "Informe o editor de foto nas Configurações para exibir apenas seus trabalhos.",
            )
            return

        selected_window = self._selected_raw_window()
        label = self._selected_raw_window_label()
        self.inventory_scanning = True
        self.inventory_button.configure(state="disabled", text="Consultando...")
        self.raw_calendar_button.configure(state="disabled")
        self.inventory_state.set(
            f"Consultando a agenda de {label} e contando os arquivos RAW..."
        )

        def worker() -> None:
            try:
                snapshot, metadata = scan_raw_inventory_for_window(
                    self.settings,
                    selected_window,
                )
            except Exception as exc:
                self.after(
                    0,
                    lambda error=exc: self._raw_calendar_inventory_failed(error),
                )
                return
            self.after(
                0,
                lambda result=snapshot, details=metadata: self._raw_calendar_inventory_done(
                    result,
                    details,
                ),
            )

        threading.Thread(
            target=worker,
            daemon=True,
            name="RawRangeInventoryScan",
        ).start()

    @staticmethod
    def _find_treeview(widget: tk.Misc) -> ttk.Treeview | None:
        for child in widget.winfo_children():
            if isinstance(child, ttk.Treeview):
                return child
            found = RangeDragDesktopApp._find_treeview(child)
            if found is not None:
                return found
        return None

    def _show_inventory_details(self) -> None:
        previous_children = set(self.winfo_children())
        super()._show_inventory_details()

        new_popups = [
            child
            for child in self.winfo_children()
            if child not in previous_children and isinstance(child, tk.Toplevel)
        ]
        if not new_popups:
            return

        popup = new_popups[-1]
        tree = self._find_treeview(popup)
        if tree is None:
            return

        hint_var = tk.StringVar()
        ttk.Label(
            popup,
            textvariable=hint_var,
            style="Muted.TLabel",
            padding=(14, 0, 14, 10),
        ).grid(row=3, column=0, sticky="ew")

        if not self._native_drag_ready:
            detail = f" ({self._native_drag_error})" if self._native_drag_error else ""
            hint_var.set(
                "Arrastar pastas está indisponível. Execute instalar.bat para instalar "
                f"o suporte nativo de arraste.{detail}"
            )
            return

        snapshot = self.inventory_snapshot
        if snapshot is None:
            return
        folders_by_id = {folder.work_id: folder for folder in snapshot.folders}
        drag_state: dict[str, object] = {"selection": (), "pressed_row": ""}

        def remember_selection(event) -> None:
            drag_state["selection"] = tuple(tree.selection())
            drag_state["pressed_row"] = tree.identify_row(event.y)

        def drag_init(event):
            previous_selection = tuple(drag_state.get("selection") or ())
            pressed_row = str(drag_state.get("pressed_row") or "")
            current_selection = tuple(tree.selection())

            if pressed_row and pressed_row in previous_selection:
                selected = previous_selection
                tree.selection_set(selected)
            elif pressed_row:
                selected = (pressed_row,)
                tree.selection_set(pressed_row)
            else:
                selected = current_selection

            paths: list[str] = []
            for item_id in selected:
                folder = folders_by_id.get(str(item_id))
                if folder is None or not folder.folder_exists:
                    continue
                if folder.path not in paths:
                    paths.append(folder.path)

            if not paths:
                hint_var.set("Nenhuma pasta existente foi selecionada para arrastar.")
                return (REFUSE_DROP, DND_FILES, ())

            hint_var.set(
                f"Arrastando {len(paths)} pasta(s). Solte no Lightroom, Explorer "
                "ou em outro programa compatível."
            )
            return (COPY, DND_FILES, tuple(paths))

        def drag_end(_event) -> None:
            hint_var.set(
                "Selecione uma ou mais linhas e arraste. Ao soltar no Explorer, "
                "as pastas são copiadas; em programas compatíveis, elas são abertas/importadas."
            )

        tree.bind("<ButtonPress-1>", remember_selection, add="+")
        tree.drag_source_register(1, DND_FILES)
        tree.dnd_bind("<<DragInitCmd>>", drag_init)
        tree.dnd_bind("<<DragEndCmd>>", drag_end)
        drag_end(None)


def main() -> None:
    RangeDragDesktopApp().mainloop()


if __name__ == "__main__":
    main()

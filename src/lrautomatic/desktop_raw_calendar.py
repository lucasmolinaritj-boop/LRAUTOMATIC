from __future__ import annotations

import calendar
import os
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from tkinter import messagebox, ttk

from .desktop_stable import StableDesktopApp
from .homepicz_editor_features import (
    fetch_editor_metadata,
    filter_work_dicts,
    load_editor_preferences,
    save_editor_preferences,
)
from .homepicz_scheduler import ImportWindow, current_import_window
from .operational_inventory import (
    MAX_WORKERS,
    OperationalFolder,
    OperationalInventory,
    _scan_work,
    fetch_operational_works,
)

MONTH_NAMES = (
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
)
WEEKDAY_NAMES = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")
SCOPE_LABELS = {"mine": "Apenas pertencentes a você", "all": "Mostrar todos"}
AUTOMATION_SCOPE_LABELS = {
    "mine": "Importar apenas pertencentes a você",
    "all": "Importar todos",
}


def scan_raw_inventory_for_date(settings, selected_date: date) -> tuple[OperationalInventory, dict[str, dict[str, str]]]:
    started = time.perf_counter()
    root = Path(settings.homepicz_photos_root).expanduser().resolve()
    requested_window = ImportWindow(selected_date, selected_date)
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
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="raw-calendar-inventory") as executor:
            futures = {executor.submit(_scan_work, root, work): work["id"] for work in works}
            for future in as_completed(futures):
                work_id = futures[future]
                try:
                    item = future.result()
                except Exception as exc:
                    errors.append(f"ID {work_id}: falha inesperada na contagem: {type(exc).__name__}: {exc}")
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
    return snapshot, {folder.work_id: metadata.get(folder.work_id, {}) for folder in folders}


class RawCalendarDesktopApp(StableDesktopApp):
    def __init__(self, config_path: str = "config.json") -> None:
        super().__init__(config_path)
        self.title("LRAutomatic V5.2")
        self._stabilize_job_monitor_widgets()
        self.raw_inventory_date = current_import_window(self.settings).start
        self.raw_metadata: dict[str, dict[str, str]] = {}
        self.raw_calendar_button = ttk.Button(
            self.inventory_button.master,
            text=self._raw_calendar_button_text(),
            style="Secondary.TButton",
            command=self._open_raw_calendar,
        )
        self.raw_calendar_button.grid(row=0, column=3, padx=(6, 0))
        self._build_editor_preferences()

    def _build_editor_preferences(self) -> None:
        prefs = load_editor_preferences(self.settings)
        self.editor_name_var = tk.StringVar(value=prefs.get("editor_name", ""))
        self.manager_scope_var = tk.StringVar(value=SCOPE_LABELS.get(prefs.get("manager_scope", "all"), SCOPE_LABELS["all"]))
        self.automation_scope_var = tk.StringVar(value=AUTOMATION_SCOPE_LABELS.get(prefs.get("automation_scope", "all"), AUTOMATION_SCOPE_LABELS["all"]))
        box = ttk.LabelFrame(self.settings_content, text="Editor de foto e escopo", padding=16)
        box.grid(row=999, column=0, sticky="ew", pady=6)
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="Quem é o editor de foto deste computador").grid(row=0, column=0, sticky="w", padx=(0, 14), pady=7)
        ttk.Entry(box, textvariable=self.editor_name_var).grid(row=0, column=1, sticky="ew")
        ttk.Label(box, text="Gerenciador de RAW").grid(row=1, column=0, sticky="w", padx=(0, 14), pady=7)
        ttk.Combobox(box, textvariable=self.manager_scope_var, state="readonly", values=tuple(SCOPE_LABELS.values())).grid(row=1, column=1, sticky="ew")
        ttk.Label(box, text="Automação de importação").grid(row=2, column=0, sticky="w", padx=(0, 14), pady=7)
        ttk.Combobox(box, textvariable=self.automation_scope_var, state="readonly", values=tuple(AUTOMATION_SCOPE_LABELS.values())).grid(row=2, column=1, sticky="ew")
        ttk.Label(
            box,
            text="Coleções: Home Picz - DATA > Fotógrafos/Clientes > Nome > Horário > ID - Rua.",
            style="Muted.TLabel",
            wraplength=850,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 10))
        ttk.Button(box, text="SALVAR IDENTIDADE E ESCOPO", style="Primary.TButton", command=self._save_editor_preferences).grid(row=4, column=0, columnspan=2, sticky="ew")

    def _save_editor_preferences(self) -> None:
        reverse_manager = {label: key for key, label in SCOPE_LABELS.items()}
        reverse_automation = {label: key for key, label in AUTOMATION_SCOPE_LABELS.items()}
        editor = self.editor_name_var.get().strip()
        manager_scope = reverse_manager.get(self.manager_scope_var.get(), "all")
        automation_scope = reverse_automation.get(self.automation_scope_var.get(), "all")
        if (manager_scope == "mine" or automation_scope == "mine") and not editor:
            messagebox.showwarning("Editor de foto", "Informe o nome do editor antes de usar a opção 'apenas pertencentes a você'.")
            return
        save_editor_preferences(self.settings, {
            "editor_name": editor,
            "manager_scope": manager_scope,
            "automation_scope": automation_scope,
        })
        self.config_state.set("Identidade e escopos salvos")
        messagebox.showinfo("Configurações", "Editor e escopos atualizados. A automação aplicará a regra no próximo ciclo.")

    def _stabilize_job_monitor_widgets(self) -> None:
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
            self.detail_scrollbar = ttk.Scrollbar(detail_parent, orient="vertical", command=self.detail_text.yview)
            self.detail_scrollbar.grid(row=3, column=1, sticky="ns")
            self.detail_text.configure(yscrollcommand=self.detail_scrollbar.set)

    def _render(self, job) -> None:
        same_job = getattr(self, "selected_job_id", None) == job.job_id
        previous_view = self.detail_text.yview() if same_job and hasattr(self, "detail_text") else None
        super()._render(job)
        if previous_view:
            self.after_idle(lambda value=previous_view[0]: self.detail_text.yview_moveto(value))

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
        ttk.Label(header, textvariable=month_title, style="Section.TLabel", anchor="center").grid(row=0, column=1, sticky="ew", padx=12)
        ttk.Button(header, text="▶", width=3, command=lambda: change_month(1)).grid(row=0, column=2)
        days_frame.grid(row=1, column=0)

        def select_day(day: int) -> None:
            self.raw_inventory_date = date(shown_year.get(), shown_month.get(), day)
            self.raw_calendar_button.configure(text=self._raw_calendar_button_text())
            popup.destroy()
            self.inventory_state.set(f"Data do Gerenciador de RAW: {self.raw_inventory_date:%d/%m/%Y}. Gerando tabela...")
            self._refresh_inventory()

        def render_month() -> None:
            for widget in days_frame.winfo_children():
                widget.destroy()
            year, month = shown_year.get(), shown_month.get()
            month_title.set(f"{MONTH_NAMES[month]} de {year}")
            for column, name in enumerate(WEEKDAY_NAMES):
                ttk.Label(days_frame, text=name, anchor="center", width=4).grid(row=0, column=column, padx=2, pady=(0, 4))
            for row, week in enumerate(calendar.monthcalendar(year, month), start=1):
                for column, day in enumerate(week):
                    if day == 0:
                        ttk.Label(days_frame, text="", width=4).grid(row=row, column=column, padx=2, pady=2)
                        continue
                    selected = date(year, month, day) == self.raw_inventory_date
                    ttk.Button(days_frame, text=str(day), width=4, style="Primary.TButton" if selected else "Secondary.TButton", command=lambda value=day: select_day(value)).grid(row=row, column=column, padx=2, pady=2)

        def change_month(delta: int) -> None:
            year, month = shown_year.get(), shown_month.get() + delta
            if month < 1:
                month, year = 12, year - 1
            elif month > 12:
                month, year = 1, year + 1
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
        ttk.Button(footer, text="Cancelar", style="Secondary.TButton", command=popup.destroy).grid(row=0, column=2, padx=(6, 0))
        render_month()

    def _refresh_inventory(self) -> None:
        if self.inventory_scanning:
            return
        if not self.settings.homepicz_appscript_url:
            messagebox.showwarning("Gerenciador de RAW", "Configure a URL do Google Apps Script para consultar os IDs da data escolhida.")
            return
        if not self.settings.homepicz_photos_root:
            messagebox.showwarning("Gerenciador de RAW", "Configure a pasta Fotos do dia nas Configurações.")
            return
        prefs = load_editor_preferences(self.settings)
        if prefs.get("manager_scope") == "mine" and not prefs.get("editor_name"):
            messagebox.showwarning("Gerenciador de RAW", "Informe o editor de foto nas Configurações para exibir apenas seus trabalhos.")
            return
        selected_date = self.raw_inventory_date
        self.inventory_scanning = True
        self.inventory_button.configure(state="disabled", text="Consultando...")
        self.raw_calendar_button.configure(state="disabled")
        self.inventory_state.set(f"Consultando a agenda de {selected_date:%d/%m/%Y} e contando os arquivos RAW...")
        def worker() -> None:
            try:
                snapshot, metadata = scan_raw_inventory_for_date(self.settings, selected_date)
            except Exception as exc:
                self.after(0, lambda error=exc: self._raw_calendar_inventory_failed(error))
                return
            self.after(0, lambda result=snapshot, details=metadata: self._raw_calendar_inventory_done(result, details))
        threading.Thread(target=worker, daemon=True, name="RawCalendarInventoryScan").start()

    def _raw_calendar_inventory_done(self, snapshot: OperationalInventory, metadata: dict[str, dict[str, str]]) -> None:
        self.raw_metadata = metadata
        self.raw_calendar_button.configure(state="normal", text=self._raw_calendar_button_text())
        self._inventory_done(snapshot)

    def _raw_calendar_inventory_failed(self, error: Exception) -> None:
        self.raw_calendar_button.configure(state="normal", text=self._raw_calendar_button_text())
        self._inventory_failed(error)

    def _show_inventory_details(self) -> None:
        snapshot = self.inventory_snapshot
        if snapshot is None:
            messagebox.showinfo("Gerenciador de RAW", "Atualize as fotos antes de abrir a tabela.")
            return
        popup = tk.Toplevel(self)
        popup.title("Gerenciador de RAW — tabela completa")
        popup.geometry("1500x760")
        popup.minsize(1050, 560)
        popup.transient(self)
        popup.columnconfigure(0, weight=1)
        popup.rowconfigure(1, weight=1)
        controls = ttk.Frame(popup, padding=(14, 14, 14, 8))
        controls.grid(row=0, column=0, sticky="ew")
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Filtrar:").grid(row=0, column=0, padx=(0, 8))
        search_var = tk.StringVar()
        search_entry = ttk.Entry(controls, textvariable=search_var)
        search_entry.grid(row=0, column=1, sticky="ew")
        status_var = tk.StringVar(value="Todos")
        ttk.Combobox(controls, textvariable=status_var, state="readonly", values=("Todos", "OK", "Com alerta", "Sem RAW", "Pasta ausente"), width=18).grid(row=0, column=2, padx=(8, 0))

        table_frame = ttk.Frame(popup, padding=(14, 0, 14, 8))
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("id", "horario", "editor", "fotografo", "cliente", "rua", "cr2", "cr3", "dng", "total", "situacao")
        tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="extended")
        definitions = (
            ("id", "ID", 80), ("horario", "Horário", 90), ("editor", "Editor foto", 155),
            ("fotografo", "Fotógrafo", 145), ("cliente", "Cliente", 180), ("rua", "Rua", 290),
            ("cr2", "CR2", 65), ("cr3", "CR3", 65), ("dng", "DNG", 65),
            ("total", "Total", 70), ("situacao", "Situação", 220),
        )
        sort_state = {"column": "horario", "reverse": False}
        for key, title, width in definitions:
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=55, anchor="w", stretch=key in {"editor", "fotografo", "cliente", "rua", "situacao"})
        tree.grid(row=0, column=0, sticky="nsew")
        ybar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        xbar = ttk.Scrollbar(table_frame, orient="horizontal", command=tree.xview)
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        folders_by_id = {folder.work_id: folder for folder in snapshot.folders}

        def row_values(folder: OperationalFolder):
            meta = self.raw_metadata.get(folder.work_id, {})
            return (
                folder.work_id,
                meta.get("horario") or self._hour_from_datetime(folder.scheduled_at),
                meta.get("editorFoto") or "Editor de foto não informado",
                folder.photographer,
                meta.get("cliente") or "Cliente não informado",
                meta.get("rua") or "Rua não informada",
                folder.cr2, folder.cr3, folder.dng, folder.total, folder.warning,
            )

        def matches(folder: OperationalFolder) -> bool:
            values = row_values(folder)
            query = search_var.get().strip().casefold()
            if query and query not in " ".join(str(value) for value in values).casefold():
                return False
            status = status_var.get()
            if status == "OK" and folder.warning != "OK": return False
            if status == "Com alerta" and folder.warning == "OK": return False
            if status == "Sem RAW" and not (folder.folder_exists and folder.total == 0): return False
            if status == "Pasta ausente" and folder.folder_exists: return False
            return True

        def sort_key(folder: OperationalFolder, column: str):
            value = dict(zip(columns, row_values(folder)))[column]
            return value if isinstance(value, int) else str(value).casefold()

        result_var = tk.StringVar()
        def render() -> None:
            selected = set(tree.selection())
            tree.delete(*tree.get_children())
            folders = [folder for folder in snapshot.folders if matches(folder)]
            folders.sort(key=lambda folder: sort_key(folder, sort_state["column"]), reverse=sort_state["reverse"])
            for index, folder in enumerate(folders):
                tree.insert("", "end", iid=folder.work_id, values=row_values(folder), tags=("even" if index % 2 == 0 else "odd",))
            tree.tag_configure("even", background="#FFFFFF")
            tree.tag_configure("odd", background="#F8FAFC")
            for item_id in selected:
                if tree.exists(item_id): tree.selection_add(item_id)
            result_var.set(f"{len(folders)} trabalho(s) exibido(s) • {sum(folder.total for folder in folders)} RAW(s)")

        def sort_by(column: str) -> None:
            if sort_state["column"] == column:
                sort_state["reverse"] = not sort_state["reverse"]
            else:
                sort_state["column"] = column
                sort_state["reverse"] = False
            for key, title, _width in definitions:
                marker = " ▲" if key == column and not sort_state["reverse"] else " ▼" if key == column else ""
                tree.heading(key, text=title + marker)
            render()

        for key, _title, _width in definitions:
            tree.heading(key, command=lambda column=key: sort_by(column))
        search_var.trace_add("write", lambda *_args: render())
        status_var.trace_add("write", lambda *_args: render())
        footer = ttk.Frame(popup, padding=(14, 6, 14, 14))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=result_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")

        def selected_ids() -> list[str]:
            return [str(value) for value in tree.selection() if str(value) in folders_by_id]
        def open_selected() -> None:
            ids = selected_ids()
            if not ids:
                messagebox.showinfo("Gerenciador de RAW", "Selecione uma pasta.", parent=popup)
                return
            try:
                os.startfile(Path(folders_by_id[ids[0]].path))
            except Exception as exc:
                messagebox.showerror("Abrir pasta", str(exc), parent=popup)
        def cleanup_selected() -> None:
            ids = selected_ids()
            if not ids:
                messagebox.showinfo("Gerenciador de RAW", "Selecione uma ou mais pastas.", parent=popup)
                return
            self._open_raw_cleanup_confirmation(popup, snapshot, ids, f"{len(ids)} pasta(s) selecionada(s)")

        ttk.Button(footer, text="Abrir pasta", style="Secondary.TButton", command=open_selected).grid(row=0, column=1, padx=(8, 6))
        ttk.Button(footer, text="Excluir RAW selecionados", style="Danger.TButton", command=cleanup_selected).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(footer, text="Fechar", style="Secondary.TButton", command=popup.destroy).grid(row=0, column=3)
        render()
        search_entry.focus_set()

    @staticmethod
    def _hour_from_datetime(value: str) -> str:
        raw = str(value or "")
        if " " in raw and ":" in raw:
            return raw.split(" ", 1)[1][:5].replace(":", "h")
        return "—"


def main() -> None:
    RawCalendarDesktopApp().mainloop()


if __name__ == "__main__":
    main()

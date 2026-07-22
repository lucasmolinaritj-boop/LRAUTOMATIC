from __future__ import annotations

import os
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from .desktop_raw_calendar import RawCalendarDesktopApp
from .operational_inventory import OperationalFolder


class RawCreatedDesktopApp(RawCalendarDesktopApp):
    """Gerenciador RAW com coluna do último arquivo baseada na criação."""

    def __init__(self, config_path: str = "config.json") -> None:
        super().__init__(config_path)
        self.title("LRAutomatic V5.3")

    @staticmethod
    def _created_label(timestamp: float | None) -> str:
        if timestamp is None:
            return "—"
        try:
            return datetime.fromtimestamp(timestamp).strftime("%d/%m/%Y %H:%M")
        except (OSError, ValueError, OverflowError):
            return "—"

    def _show_inventory_details(self) -> None:
        snapshot = self.inventory_snapshot
        if snapshot is None:
            messagebox.showinfo("Gerenciador de RAW", "Atualize as fotos antes de abrir a tabela.")
            return

        popup = tk.Toplevel(self)
        popup.title("Gerenciador de RAW — tabela completa")
        popup.geometry("1580x760")
        popup.minsize(1100, 560)
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
        ttk.Combobox(
            controls,
            textvariable=status_var,
            state="readonly",
            values=("Todos", "OK", "Com alerta", "Sem RAW", "Pasta ausente"),
            width=18,
        ).grid(row=0, column=2, padx=(8, 0))

        table_frame = ttk.Frame(popup, padding=(14, 0, 14, 8))
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = (
            "id",
            "horario",
            "editor",
            "fotografo",
            "cliente",
            "rua",
            "cr2",
            "cr3",
            "dng",
            "total",
            "ultimo_raw",
            "situacao",
        )
        tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="extended")
        definitions = (
            ("id", "ID", 80),
            ("horario", "Horário", 90),
            ("editor", "Editor foto", 155),
            ("fotografo", "Fotógrafo", 145),
            ("cliente", "Cliente", 180),
            ("rua", "Rua", 270),
            ("cr2", "CR2", 60),
            ("cr3", "CR3", 60),
            ("dng", "DNG", 60),
            ("total", "Total", 65),
            ("ultimo_raw", "Último RAW (criado)", 155),
            ("situacao", "Situação", 210),
        )
        sort_state = {"column": "horario", "reverse": False}
        for key, title, width in definitions:
            tree.heading(key, text=title)
            tree.column(
                key,
                width=width,
                minwidth=55,
                anchor="w",
                stretch=key in {"editor", "fotografo", "cliente", "rua", "situacao"},
            )
        tree.grid(row=0, column=0, sticky="nsew")
        ybar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        xbar = ttk.Scrollbar(table_frame, orient="horizontal", command=tree.xview)
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        folders_by_id = {folder.work_id: folder for folder in snapshot.folders}

        def row_values(folder: OperationalFolder) -> tuple[object, ...]:
            meta = self.raw_metadata.get(folder.work_id, {})
            return (
                folder.work_id,
                meta.get("horario") or self._hour_from_datetime(folder.scheduled_at),
                meta.get("editorFoto") or "Editor de foto não informado",
                folder.photographer,
                meta.get("cliente") or "Cliente não informado",
                meta.get("rua") or "Rua não informada",
                folder.cr2,
                folder.cr3,
                folder.dng,
                folder.total,
                self._created_label(folder.latest_mtime),
                folder.warning,
            )

        def matches(folder: OperationalFolder) -> bool:
            values = row_values(folder)
            query = search_var.get().strip().casefold()
            if query and query not in " ".join(str(value) for value in values).casefold():
                return False
            status = status_var.get()
            if status == "OK" and folder.warning != "OK":
                return False
            if status == "Com alerta" and folder.warning == "OK":
                return False
            if status == "Sem RAW" and not (folder.folder_exists and folder.total == 0):
                return False
            if status == "Pasta ausente" and folder.folder_exists:
                return False
            return True

        def sort_key(folder: OperationalFolder, column: str):
            if column == "ultimo_raw":
                return folder.latest_mtime or 0
            value = dict(zip(columns, row_values(folder)))[column]
            return value if isinstance(value, int) else str(value).casefold()

        result_var = tk.StringVar()

        def render() -> None:
            selected = set(tree.selection())
            children = tree.get_children()
            if children:
                tree.delete(*children)
            folders = [folder for folder in snapshot.folders if matches(folder)]
            folders.sort(
                key=lambda folder: sort_key(folder, sort_state["column"]),
                reverse=sort_state["reverse"],
            )
            for index, folder in enumerate(folders):
                tag = "missing" if not folder.folder_exists else (
                    "warning" if folder.warning != "OK" else ("even" if index % 2 == 0 else "odd")
                )
                tree.insert("", "end", iid=folder.work_id, values=row_values(folder), tags=(tag,))
            tree.tag_configure("even", background="#FFFFFF")
            tree.tag_configure("odd", background="#F8FAFC")
            tree.tag_configure("warning", background="#FFF4D6")
            tree.tag_configure("missing", background="#FFF0F0")
            for item_id in selected:
                if tree.exists(item_id):
                    tree.selection_add(item_id)
            result_var.set(
                f"{len(folders)} trabalho(s) exibido(s) • "
                f"{sum(folder.total for folder in folders)} RAW(s)"
            )

        def sort_by(column: str) -> None:
            if sort_state["column"] == column:
                sort_state["reverse"] = not sort_state["reverse"]
            else:
                sort_state["column"] = column
                sort_state["reverse"] = False
            for key, title, _width in definitions:
                marker = (
                    " ▲"
                    if key == column and not sort_state["reverse"]
                    else " ▼"
                    if key == column
                    else ""
                )
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
                messagebox.showinfo(
                    "Gerenciador de RAW",
                    "Selecione uma ou mais pastas.",
                    parent=popup,
                )
                return
            self._open_raw_cleanup_confirmation(
                popup,
                snapshot,
                ids,
                f"{len(ids)} pasta(s) selecionada(s)",
            )

        ttk.Button(
            footer,
            text="Abrir pasta",
            style="Secondary.TButton",
            command=open_selected,
        ).grid(row=0, column=1, padx=(8, 6))
        ttk.Button(
            footer,
            text="Excluir RAW selecionados",
            style="Danger.TButton",
            command=cleanup_selected,
        ).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(
            footer,
            text="Fechar",
            style="Secondary.TButton",
            command=popup.destroy,
        ).grid(row=0, column=3)
        render()
        search_entry.focus_set()


def main() -> None:
    RawCreatedDesktopApp().mainloop()


if __name__ == "__main__":
    main()

from __future__ import annotations

import os
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from .desktop_raw_calendar import RawCalendarDesktopApp
from .operational_inventory import OperationalFolder


class RawCreatedDesktopApp(RawCalendarDesktopApp):
    """Gerenciador RAW com filtros completos, dia e limpeza por escopo."""

    def __init__(self, config_path: str = "config.json") -> None:
        super().__init__(config_path)
        self.title("LRAutomatic V5.6")

    @staticmethod
    def _created_label(timestamp: float | None) -> str:
        if timestamp is None:
            return "—"
        try:
            return datetime.fromtimestamp(timestamp).strftime("%d/%m/%Y %H:%M")
        except (OSError, ValueError, OverflowError):
            return "—"

    @staticmethod
    def _parse_any_schedule(value: object) -> datetime:
        raw = str(value or "").strip()
        if not raw:
            return datetime.min

        for fmt in (
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue

        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed
        except (TypeError, ValueError, OverflowError):
            return datetime.min

    def _show_inventory_details(self) -> None:
        snapshot = self.inventory_snapshot
        if snapshot is None:
            messagebox.showinfo("Gerenciador de RAW", "Atualize as fotos antes de abrir a tabela.")
            return

        popup = tk.Toplevel(self)
        popup.title("Gerenciador de RAW — tabela completa")
        popup.geometry("1740x860")
        popup.minsize(1220, 650)
        popup.transient(self)
        popup.columnconfigure(0, weight=1)
        popup.rowconfigure(1, weight=1)

        controls = ttk.LabelFrame(popup, text="Filtros e ordenação", padding=(14, 10))
        controls.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))
        for column in range(4):
            controls.columnconfigure(column, weight=1)

        day_var = tk.StringVar(value="Todos")
        photographer_var = tk.StringVar(value="Todos")
        editor_var = tk.StringVar(value="Todos")
        client_var = tk.StringVar(value="Todos")
        service_var = tk.StringVar(value="Todos")
        status_var = tk.StringVar(value="Todos")
        order_var = tk.StringVar(value="Dia")
        descending_var = tk.BooleanVar(value=False)
        search_var = tk.StringVar()

        def metadata(folder: OperationalFolder) -> dict[str, str]:
            return self.raw_metadata.get(folder.work_id, {})

        def scheduled_datetime(folder: OperationalFolder) -> datetime:
            meta = metadata(folder)
            return self._parse_any_schedule(meta.get("dataHora") or folder.scheduled_at)

        def day_name(folder: OperationalFolder) -> str:
            parsed = scheduled_datetime(folder)
            return parsed.strftime("%d/%m/%Y") if parsed != datetime.min else "Data não informada"

        def hour_name(folder: OperationalFolder) -> str:
            meta_hour = str(metadata(folder).get("horario") or "").strip()
            if meta_hour:
                return meta_hour
            parsed = scheduled_datetime(folder)
            return parsed.strftime("%Hh%M") if parsed != datetime.min else "—"

        def editor_name(folder: OperationalFolder) -> str:
            return metadata(folder).get("editorFoto") or "Editor de foto não informado"

        def client_name(folder: OperationalFolder) -> str:
            return metadata(folder).get("cliente") or "Cliente não informado"

        def service_name(folder: OperationalFolder) -> str:
            return folder.service or metadata(folder).get("servico") or "Serviço não informado"

        day_values = sorted(
            {day_name(folder) for folder in snapshot.folders},
            key=lambda value: (
                value == "Data não informada",
                self._parse_any_schedule(value),
            ),
        )
        days = ["Todos", *day_values]
        photographers = [
            "Todos",
            *sorted({folder.photographer for folder in snapshot.folders}, key=str.casefold),
        ]
        editors = [
            "Todos",
            *sorted({editor_name(folder) for folder in snapshot.folders}, key=str.casefold),
        ]
        clients = [
            "Todos",
            *sorted({client_name(folder) for folder in snapshot.folders}, key=str.casefold),
        ]
        services = [
            "Todos",
            *sorted({service_name(folder) for folder in snapshot.folders}, key=str.casefold),
        ]

        filter_combos: list[ttk.Combobox] = []

        def add_combo(
            row: int,
            column: int,
            label: str,
            variable: tk.StringVar,
            values: tuple[str, ...] | list[str],
            width: int,
        ) -> ttk.Combobox:
            ttk.Label(controls, text=label).grid(
                row=row,
                column=column,
                sticky="w",
                padx=(0, 8),
            )
            combo = ttk.Combobox(
                controls,
                textvariable=variable,
                values=values,
                state="readonly",
                width=width,
            )
            combo.grid(
                row=row + 1,
                column=column,
                sticky="ew",
                padx=(0, 8),
            )
            filter_combos.append(combo)
            return combo

        add_combo(0, 0, "Dia", day_var, days, 16)
        add_combo(0, 1, "Fotógrafo", photographer_var, photographers, 22)
        add_combo(0, 2, "Editor de foto", editor_var, editors, 23)
        add_combo(0, 3, "Cliente", client_var, clients, 25)
        add_combo(2, 0, "Serviço", service_var, services, 24)
        add_combo(
            2,
            1,
            "Situação",
            status_var,
            ("Todos", "OK", "Com alerta", "Com RAW", "Sem RAW", "Pasta ausente"),
            18,
        )
        order_combo = add_combo(
            2,
            2,
            "Ordenar por",
            order_var,
            (
                "Dia",
                "Horário",
                "ID",
                "Editor de foto",
                "Fotógrafo",
                "Cliente",
                "Serviço",
                "Rua",
                "Total RAW",
                "Último RAW",
                "Situação",
            ),
            18,
        )
        ttk.Checkbutton(
            controls,
            text="Decrescente",
            variable=descending_var,
        ).grid(row=3, column=3, sticky="w", padx=(4, 0))

        ttk.Label(
            controls,
            text="Pesquisar dia, ID, horário, editor, fotógrafo, cliente, serviço ou endereço",
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(10, 3))
        search_entry = ttk.Entry(controls, textvariable=search_var)
        search_entry.grid(row=5, column=0, columnspan=3, sticky="ew", padx=(0, 8))

        table_frame = ttk.Frame(popup, padding=(14, 0, 14, 8))
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = (
            "id",
            "dia",
            "horario",
            "editor",
            "fotografo",
            "cliente",
            "servico",
            "rua",
            "cr2",
            "cr3",
            "dng",
            "total",
            "ultimo_raw",
            "situacao",
        )
        tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
        )
        definitions = (
            ("id", "ID", 80),
            ("dia", "Dia", 105),
            ("horario", "Horário", 90),
            ("editor", "Editor foto", 155),
            ("fotografo", "Fotógrafo", 145),
            ("cliente", "Cliente", 180),
            ("servico", "Serviço", 180),
            ("rua", "Rua", 270),
            ("cr2", "CR2", 60),
            ("cr3", "CR3", 60),
            ("dng", "DNG", 60),
            ("total", "Total", 65),
            ("ultimo_raw", "Último RAW (criado)", 155),
            ("situacao", "Situação", 210),
        )
        sort_state: dict[str, object] = {"column": "dia", "reverse": False}
        for key, title, width in definitions:
            tree.heading(key, text=title)
            tree.column(
                key,
                width=width,
                minwidth=55,
                anchor="w",
                stretch=key
                in {
                    "editor",
                    "fotografo",
                    "cliente",
                    "servico",
                    "rua",
                    "situacao",
                },
            )
        tree.grid(row=0, column=0, sticky="nsew")
        ybar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        xbar = ttk.Scrollbar(table_frame, orient="horizontal", command=tree.xview)
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        folders_by_id = {folder.work_id: folder for folder in snapshot.folders}

        def row_values(folder: OperationalFolder) -> tuple[object, ...]:
            meta = metadata(folder)
            return (
                folder.work_id,
                day_name(folder),
                hour_name(folder),
                editor_name(folder),
                folder.photographer,
                client_name(folder),
                service_name(folder),
                meta.get("rua") or "Rua não informada",
                folder.cr2,
                folder.cr3,
                folder.dng,
                folder.total,
                self._created_label(folder.latest_mtime),
                folder.warning,
            )

        def matches(folder: OperationalFolder) -> bool:
            if day_var.get() != "Todos" and day_name(folder) != day_var.get():
                return False
            if photographer_var.get() != "Todos" and folder.photographer != photographer_var.get():
                return False
            if editor_var.get() != "Todos" and editor_name(folder) != editor_var.get():
                return False
            if client_var.get() != "Todos" and client_name(folder) != client_var.get():
                return False
            if service_var.get() != "Todos" and service_name(folder) != service_var.get():
                return False

            status = status_var.get()
            if status == "OK" and folder.warning != "OK":
                return False
            if status == "Com alerta" and folder.warning == "OK":
                return False
            if status == "Com RAW" and not (folder.folder_exists and folder.total > 0):
                return False
            if status == "Sem RAW" and not (folder.folder_exists and folder.total == 0):
                return False
            if status == "Pasta ausente" and folder.folder_exists:
                return False

            query = search_var.get().strip().casefold()
            if query and query not in " ".join(
                str(value) for value in row_values(folder)
            ).casefold():
                return False
            return True

        def sort_key(folder: OperationalFolder, column: str):
            if column == "id":
                return (
                    (0, int(folder.work_id))
                    if folder.work_id.isdigit()
                    else (1, folder.work_id.casefold())
                )
            if column in {"dia", "horario"}:
                return scheduled_datetime(folder)
            if column == "ultimo_raw":
                return folder.latest_mtime or 0
            value = dict(zip(columns, row_values(folder)))[column]
            return value if isinstance(value, int) else str(value).casefold()

        result_var = tk.StringVar()
        visible_folders: list[OperationalFolder] = []

        def render(*_args) -> None:
            nonlocal visible_folders
            selected = set(tree.selection())
            children = tree.get_children()
            if children:
                tree.delete(*children)
            visible_folders = [folder for folder in snapshot.folders if matches(folder)]
            visible_folders.sort(
                key=lambda folder: sort_key(folder, str(sort_state["column"])),
                reverse=bool(sort_state["reverse"]),
            )
            for index, folder in enumerate(visible_folders):
                tag = (
                    "missing"
                    if not folder.folder_exists
                    else (
                        "warning"
                        if folder.warning != "OK"
                        else ("even" if index % 2 == 0 else "odd")
                    )
                )
                tree.insert(
                    "",
                    "end",
                    iid=folder.work_id,
                    values=row_values(folder),
                    tags=(tag,),
                )
            tree.tag_configure("even", background="#FFFFFF")
            tree.tag_configure("odd", background="#F8FAFC")
            tree.tag_configure("warning", background="#FFF4D6")
            tree.tag_configure("missing", background="#FFF0F0")
            for item_id in selected:
                if tree.exists(item_id):
                    tree.selection_add(item_id)
            result_var.set(
                f"{len(visible_folders)} de {len(snapshot.folders)} trabalho(s) exibido(s) • "
                f"{sum(folder.total for folder in visible_folders)} RAW(s)"
            )

        column_to_order = {
            "id": "ID",
            "dia": "Dia",
            "horario": "Horário",
            "editor": "Editor de foto",
            "fotografo": "Fotógrafo",
            "cliente": "Cliente",
            "servico": "Serviço",
            "rua": "Rua",
            "total": "Total RAW",
            "ultimo_raw": "Último RAW",
            "situacao": "Situação",
        }
        order_to_column = {label: column for column, label in column_to_order.items()}

        def update_headings() -> None:
            current = str(sort_state["column"])
            reverse = bool(sort_state["reverse"])
            for key, title, _width in definitions:
                marker = " ▼" if key == current and reverse else " ▲" if key == current else ""
                tree.heading(key, text=title + marker)

        def sort_by(column: str) -> None:
            if sort_state["column"] == column:
                sort_state["reverse"] = not bool(sort_state["reverse"])
            else:
                sort_state["column"] = column
                sort_state["reverse"] = False
            order_var.set(column_to_order.get(column, "Dia"))
            descending_var.set(bool(sort_state["reverse"]))
            update_headings()
            render()

        def apply_order(*_args) -> None:
            sort_state["column"] = order_to_column.get(order_var.get(), "dia")
            sort_state["reverse"] = descending_var.get()
            update_headings()
            render()

        def clear_filters() -> None:
            day_var.set("Todos")
            photographer_var.set("Todos")
            editor_var.set("Todos")
            client_var.set("Todos")
            service_var.set("Todos")
            status_var.set("Todos")
            search_var.set("")
            order_var.set("Dia")
            descending_var.set(False)
            sort_state["column"] = "dia"
            sort_state["reverse"] = False
            update_headings()
            render()

        ttk.Button(
            controls,
            text="Limpar filtros",
            style="Secondary.TButton",
            command=clear_filters,
        ).grid(row=5, column=3, sticky="ew")

        for key, _title, _width in definitions:
            tree.heading(key, command=lambda column=key: sort_by(column))
        for combo in filter_combos:
            if combo is order_combo:
                combo.bind("<<ComboboxSelected>>", apply_order)
            else:
                combo.bind("<<ComboboxSelected>>", render)
        descending_var.trace_add("write", apply_order)
        search_var.trace_add("write", render)

        footer = ttk.Frame(popup, padding=(14, 6, 14, 14))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(
            footer,
            textvariable=result_var,
            style="Muted.TLabel",
        ).grid(row=0, column=0, sticky="w")

        def selected_ids() -> list[str]:
            return [
                str(value)
                for value in tree.selection()
                if str(value) in folders_by_id
            ]

        def open_selected() -> None:
            ids = selected_ids()
            if not ids:
                messagebox.showinfo(
                    "Gerenciador de RAW",
                    "Selecione uma pasta.",
                    parent=popup,
                )
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

        def open_cleanup_scope() -> None:
            filtered_ids = [folder.work_id for folder in visible_folders]
            filtered_raws = sum(folder.total for folder in visible_folders)
            all_raws = sum(folder.total for folder in snapshot.folders)

            chooser = tk.Toplevel(popup)
            chooser.title("Escolher alcance da exclusão")
            chooser.geometry("650x330")
            chooser.resizable(False, False)
            chooser.transient(popup)
            chooser.grab_set()
            chooser.columnconfigure(0, weight=1)

            content = ttk.Frame(chooser, padding=22)
            content.grid(row=0, column=0, sticky="nsew")
            content.columnconfigure(0, weight=1)

            ttk.Label(
                content,
                text="Quais pastas entram na limpeza?",
                style="Title.TLabel",
            ).grid(row=0, column=0, sticky="w")
            ttk.Label(
                content,
                text=(
                    "Depois desta escolha será aberta a tela padrão de verificação, "
                    "com contagem por extensão, duas confirmações e a palavra APAGAR."
                ),
                style="Muted.TLabel",
                wraplength=590,
                justify="left",
            ).grid(row=1, column=0, sticky="w", pady=(8, 18))

            def use_filtered() -> None:
                if not filtered_ids:
                    messagebox.showinfo(
                        "Limpeza de RAW",
                        "O filtro atual não possui nenhuma pasta.",
                        parent=chooser,
                    )
                    return
                chooser.destroy()
                self._open_raw_cleanup_confirmation(
                    popup,
                    snapshot,
                    filtered_ids,
                    (
                        f"filtro atual: {len(filtered_ids)} pasta(s), "
                        f"{filtered_raws} RAW(s)"
                    ),
                )

            def use_all() -> None:
                chooser.destroy()
                self._open_raw_cleanup_confirmation(
                    popup,
                    snapshot,
                    None,
                    (
                        f"todo o período: {len(snapshot.folders)} pasta(s), "
                        f"{all_raws} RAW(s)"
                    ),
                )

            ttk.Button(
                content,
                text=(
                    f"USAR FILTRO ATUAL — {len(filtered_ids)} pasta(s), "
                    f"{filtered_raws} RAW(s)"
                ),
                style="Danger.TButton",
                command=use_filtered,
            ).grid(row=2, column=0, sticky="ew", pady=(0, 10))
            ttk.Button(
                content,
                text=(
                    f"USAR TODO O PERÍODO — {len(snapshot.folders)} pasta(s), "
                    f"{all_raws} RAW(s)"
                ),
                style="Danger.TButton",
                command=use_all,
            ).grid(row=3, column=0, sticky="ew", pady=(0, 10))
            ttk.Button(
                content,
                text="Cancelar",
                style="Secondary.TButton",
                command=chooser.destroy,
            ).grid(row=4, column=0, sticky="ew")

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
            text="Excluir todos os RAWs...",
            style="Danger.TButton",
            command=open_cleanup_scope,
        ).grid(row=0, column=3, padx=(0, 6))
        ttk.Button(
            footer,
            text="Fechar",
            style="Secondary.TButton",
            command=popup.destroy,
        ).grid(row=0, column=4)

        update_headings()
        render()
        search_entry.focus_set()


def main() -> None:
    RawCreatedDesktopApp().mainloop()


if __name__ == "__main__":
    main()

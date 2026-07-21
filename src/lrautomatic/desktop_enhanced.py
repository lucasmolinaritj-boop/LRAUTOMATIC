from __future__ import annotations

import os
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from .desktop import DesktopApp
from .operational_inventory import (
    OperationalFolder,
    OperationalInventory,
    RawDeletionResult,
    delete_snapshot_raw_files,
    scan_operational_inventory,
)


class EnhancedDesktopApp(DesktopApp):
    """Desktop V4.6 com gerenciador operacional de arquivos RAW."""

    def __init__(self, config_path: str = "config.json") -> None:
        super().__init__(config_path)
        self.title("LRAutomatic V4.6")
        self.inventory_snapshot: OperationalInventory | None = None

    @staticmethod
    def _window_label(snapshot: OperationalInventory) -> str:
        start = snapshot.window.start.strftime("%d/%m/%Y")
        end = snapshot.window.end.strftime("%d/%m/%Y")
        return start if start == end else f"{start} a {end}"

    @staticmethod
    def _human_size(value: int) -> str:
        size = float(value)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024
        return f"{value} B"

    @staticmethod
    def _modified_label(timestamp: float | None) -> str:
        if timestamp is None:
            return "—"
        try:
            return datetime.fromtimestamp(timestamp).strftime("%d/%m/%Y %H:%M")
        except (OSError, ValueError, OverflowError):
            return "—"

    @staticmethod
    def _status_key(folder: OperationalFolder) -> str:
        if not folder.folder_exists:
            return "Pasta inexistente"
        if folder.total == 0:
            return "Sem RAW"
        return "Com RAW"

    @staticmethod
    def _parse_schedule(value: str) -> datetime:
        raw = str(value or "").strip()
        for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        return datetime.min

    def _refresh_inventory(self) -> None:
        if self.inventory_scanning:
            return
        if not self.settings.homepicz_appscript_url:
            messagebox.showwarning(
                "Fotos do dia",
                "Configure a URL do Google Apps Script para consultar os IDs do período operacional.",
            )
            return
        root = self.settings.homepicz_photos_root
        if not root:
            messagebox.showwarning("Fotos do dia", "Configure a pasta Fotos do dia nas Configurações.")
            return

        self.inventory_scanning = True
        self.inventory_button.configure(state="disabled", text="Consultando...")
        self.inventory_state.set("Consultando a agenda e contando somente as pastas do período operacional...")

        def worker() -> None:
            try:
                snapshot = scan_operational_inventory(self.settings)
            except Exception as exc:
                self.after(0, lambda error=exc: self._inventory_failed(error))
                return
            self.after(0, lambda result=snapshot: self._inventory_done(result))

        threading.Thread(target=worker, daemon=True, name="OperationalInventoryScan").start()

    def _inventory_done(self, snapshot: OperationalInventory) -> None:
        self.inventory_scanning = False
        self.inventory_snapshot = snapshot
        self.inventory_button.configure(state="normal", text="Atualizar fotos")
        self.inventory_vars["cr2"].set(str(snapshot.cr2))
        self.inventory_vars["cr3"].set(str(snapshot.cr3))
        self.inventory_vars["dng"].set(str(snapshot.dng))
        self.inventory_vars["total"].set(str(snapshot.total))

        notes = [
            f"Período {self._window_label(snapshot)}",
            f"{len(snapshot.folders)} trabalho(s)",
            f"{snapshot.empty_count} zerado(s)",
        ]
        if snapshot.missing_count:
            notes.append(f"{snapshot.missing_count} pasta(s) ausente(s)")
        if snapshot.errors:
            notes.append(f"{len(snapshot.errors)} erro(s) de leitura")
        notes.append(f"{snapshot.elapsed_seconds:.2f}s")
        self.inventory_state.set(" • ".join(notes))

    def _show_inventory_details(self) -> None:
        snapshot = self.inventory_snapshot
        if snapshot is None:
            messagebox.showinfo("Fotos do dia", "Clique em Atualizar fotos primeiro.")
            return

        window = tk.Toplevel(self)
        window.title("Gerenciador de RAW — período operacional")
        window.geometry("1380x780")
        window.minsize(1020, 590)
        window.transient(self)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(2, weight=1)

        header = ttk.Frame(window, padding=(18, 16, 18, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        title_var = tk.StringVar(value=f"{snapshot.total} RAW em {len(snapshot.folders)} trabalho(s)")
        subtitle_var = tk.StringVar(
            value=(
                f"Período operacional: {self._window_label(snapshot)}  •  "
                f"{snapshot.empty_count} zerada(s)  •  {snapshot.missing_count} ausente(s)"
            )
        )
        ttk.Label(header, textvariable=title_var, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=subtitle_var, style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(3, 0))
        ttk.Label(header, text=f"Leitura em {snapshot.elapsed_seconds:.2f}s", style="SuccessBadge.TLabel").grid(
            row=0, column=1, rowspan=2, padx=(12, 0)
        )

        filters = ttk.LabelFrame(window, text="Filtros e ordenação", padding=(14, 10))
        filters.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 10))
        filters.columnconfigure(7, weight=1)

        photographer_var = tk.StringVar(value="Todos")
        status_var = tk.StringVar(value="Todos")
        service_var = tk.StringVar(value="Todos")
        search_var = tk.StringVar()
        order_var = tk.StringVar(value="Agenda")
        descending_var = tk.BooleanVar(value=False)

        photographers = ["Todos", *sorted({item.photographer for item in snapshot.folders}, key=str.casefold)]
        services = ["Todos", *sorted({item.service for item in snapshot.folders if item.service}, key=str.casefold)]

        ttk.Label(filters, text="Fotógrafo").grid(row=0, column=0, sticky="w")
        photographer_combo = ttk.Combobox(filters, textvariable=photographer_var, values=photographers, state="readonly", width=22)
        photographer_combo.grid(row=1, column=0, padx=(0, 8), sticky="ew")
        ttk.Label(filters, text="Situação").grid(row=0, column=1, sticky="w")
        status_combo = ttk.Combobox(
            filters,
            textvariable=status_var,
            values=("Todos", "Com RAW", "Sem RAW", "Pasta inexistente"),
            state="readonly",
            width=18,
        )
        status_combo.grid(row=1, column=1, padx=(0, 8), sticky="ew")
        ttk.Label(filters, text="Serviço").grid(row=0, column=2, sticky="w")
        service_combo = ttk.Combobox(filters, textvariable=service_var, values=services, state="readonly", width=23)
        service_combo.grid(row=1, column=2, padx=(0, 8), sticky="ew")
        ttk.Label(filters, text="Ordenar por").grid(row=0, column=3, sticky="w")
        order_combo = ttk.Combobox(
            filters,
            textvariable=order_var,
            values=("Agenda", "ID", "Fotógrafo", "Serviço", "Total RAW", "Último RAW", "Situação"),
            state="readonly",
            width=16,
        )
        order_combo.grid(row=1, column=3, padx=(0, 8), sticky="ew")
        ttk.Checkbutton(filters, text="Decrescente", variable=descending_var).grid(row=1, column=4, padx=(0, 12))
        ttk.Label(filters, text="Pesquisar ID, fotógrafo ou serviço").grid(row=0, column=5, columnspan=3, sticky="w")
        search_entry = ttk.Entry(filters, textvariable=search_var)
        search_entry.grid(row=1, column=5, columnspan=3, sticky="ew")

        body = ttk.Frame(window, padding=(18, 0, 18, 8))
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        columns = ("id", "photographer", "service", "scheduled", "cr2", "cr3", "dng", "total", "modified", "state")
        tree = ttk.Treeview(body, columns=columns, show="headings", selectmode="extended")
        definitions = (
            ("id", "ID", 82, "center"),
            ("photographer", "Fotógrafo", 145, "w"),
            ("service", "Serviço", 175, "w"),
            ("scheduled", "Agenda", 145, "w"),
            ("cr2", "CR2", 55, "center"),
            ("cr3", "CR3", 55, "center"),
            ("dng", "DNG", 55, "center"),
            ("total", "Total", 60, "center"),
            ("modified", "Último RAW", 135, "w"),
            ("state", "Situação", 165, "w"),
        )
        for key, title, width, anchor in definitions:
            tree.heading(key, text=title)
            tree.column(key, width=width, anchor=anchor, stretch=key in {"photographer", "service", "state"})
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)
        tree.tag_configure("warning", background="#FFF4D6", foreground=self.WARNING)
        tree.tag_configure("missing", background="#FFF0F0", foreground=self.DANGER)

        visible_folders: list[OperationalFolder] = []

        def sort_value(folder: OperationalFolder):
            key = order_var.get()
            if key == "ID":
                return (0, int(folder.work_id)) if folder.work_id.isdigit() else (1, folder.work_id.casefold())
            if key == "Fotógrafo":
                return folder.photographer.casefold()
            if key == "Serviço":
                return folder.service.casefold()
            if key == "Total RAW":
                return folder.total
            if key == "Último RAW":
                return folder.latest_mtime or 0
            if key == "Situação":
                return self._status_key(folder)
            return self._parse_schedule(folder.scheduled_at)

        def matches(folder: OperationalFolder) -> bool:
            if photographer_var.get() != "Todos" and folder.photographer != photographer_var.get():
                return False
            if status_var.get() != "Todos" and self._status_key(folder) != status_var.get():
                return False
            if service_var.get() != "Todos" and folder.service != service_var.get():
                return False
            query = search_var.get().strip().casefold()
            if query:
                haystack = " ".join((folder.work_id, folder.photographer, folder.service, folder.scheduled_at, folder.status)).casefold()
                if query not in haystack:
                    return False
            return True

        def refresh_table(*_args) -> None:
            nonlocal visible_folders
            selected_ids = set(tree.selection())
            for item in tree.get_children():
                tree.delete(item)
            visible_folders = [folder for folder in snapshot.folders if matches(folder)]
            visible_folders.sort(key=sort_value, reverse=descending_var.get())
            for folder in visible_folders:
                tag = "missing" if not folder.folder_exists else ("warning" if folder.total == 0 else "")
                tree.insert(
                    "",
                    "end",
                    iid=folder.work_id,
                    values=(
                        folder.work_id,
                        folder.photographer,
                        folder.service or "—",
                        folder.scheduled_at or "—",
                        folder.cr2,
                        folder.cr3,
                        folder.dng,
                        folder.total,
                        self._modified_label(folder.latest_mtime),
                        folder.warning,
                    ),
                    tags=(tag,) if tag else (),
                )
            still_visible = [work_id for work_id in selected_ids if tree.exists(work_id)]
            if still_visible:
                tree.selection_set(still_visible)
            visible_total = sum(folder.total for folder in visible_folders)
            title_var.set(f"{visible_total} RAW em {len(visible_folders)} trabalho(s) visível(is)")
            subtitle_var.set(
                f"Período {self._window_label(snapshot)}  •  "
                f"{sum(folder.total == 0 for folder in visible_folders)} zerada(s)  •  "
                f"{sum(not folder.folder_exists for folder in visible_folders)} ausente(s)"
            )

        for widget in (photographer_combo, status_combo, service_combo, order_combo):
            widget.bind("<<ComboboxSelected>>", refresh_table)
        descending_var.trace_add("write", refresh_table)
        search_var.trace_add("write", refresh_table)

        def heading_sort(label: str) -> None:
            mapping = {
                "id": "ID",
                "photographer": "Fotógrafo",
                "service": "Serviço",
                "scheduled": "Agenda",
                "total": "Total RAW",
                "modified": "Último RAW",
                "state": "Situação",
            }
            target = mapping.get(label)
            if not target:
                return
            if order_var.get() == target:
                descending_var.set(not descending_var.get())
            else:
                order_var.set(target)
                descending_var.set(False)
                refresh_table()

        for key, *_rest in definitions:
            tree.heading(key, command=lambda column=key: heading_sort(column))

        footer = ttk.Frame(window, padding=(18, 8, 18, 16))
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(
            footer,
            text=(
                "Selecione uma ou várias linhas com Ctrl/Shift. A limpeza apaga somente .CR2, .CR3 e .DNG; "
                "JPG, XMP, vídeos e outros arquivos são preservados."
            ),
            style="Muted.TLabel",
            wraplength=650,
        ).grid(row=0, column=0, sticky="w")

        actions = ttk.Frame(footer)
        actions.grid(row=0, column=1, sticky="e", padx=(12, 0))

        def selected_ids() -> list[str]:
            return [str(value) for value in tree.selection()]

        def cleanup_selected() -> None:
            ids = selected_ids()
            if not ids:
                messagebox.showinfo("Limpeza de RAW", "Selecione ao menos uma pasta na tabela.", parent=window)
                return
            folders = snapshot.select(ids)
            label = f"{len(folders)} pasta(s) selecionada(s)"
            self._open_raw_cleanup_confirmation(window, snapshot, ids, label)

        def cleanup_photographer() -> None:
            photographer = photographer_var.get()
            if photographer == "Todos":
                messagebox.showinfo(
                    "Limpeza por fotógrafo",
                    "Escolha um fotógrafo no filtro antes de usar esta opção.",
                    parent=window,
                )
                return
            ids = [item.work_id for item in snapshot.folders if item.photographer == photographer]
            self._open_raw_cleanup_confirmation(window, snapshot, ids, f"fotógrafo {photographer}")

        ttk.Button(actions, text="LIMPAR PASTA(S) SELECIONADA(S)...", style="Danger.TButton", command=cleanup_selected).pack(side="left")
        ttk.Button(actions, text="LIMPAR POR FOTÓGRAFO...", style="Danger.TButton", command=cleanup_photographer).pack(side="left", padx=8)
        ttk.Button(
            actions,
            text="LIMPAR TODO O PERÍODO...",
            style="Danger.TButton",
            command=lambda: self._open_raw_cleanup_confirmation(window, snapshot, None, "todo o período operacional"),
        ).pack(side="left")

        def open_selected_folder(_event=None) -> None:
            ids = selected_ids()
            if len(ids) != 1:
                return
            folder = next((item for item in snapshot.folders if item.work_id == ids[0]), None)
            if folder and Path(folder.path).is_dir():
                os.startfile(folder.path)

        tree.bind("<Double-1>", open_selected_folder)
        refresh_table()
        search_entry.focus_set()

    def _open_raw_cleanup_confirmation(
        self,
        parent: tk.Misc,
        snapshot: OperationalInventory,
        work_ids: list[str] | None,
        target_label: str,
    ) -> None:
        folders = snapshot.select(work_ids)
        total = sum(folder.total for folder in folders)
        if total == 0:
            messagebox.showinfo("Limpeza de RAW", "Não há arquivos RAW no alvo escolhido.", parent=parent)
            return

        photographers = sorted({folder.photographer for folder in folders}, key=str.casefold)
        photographer_text = ", ".join(photographers[:5])
        if len(photographers) > 5:
            photographer_text += f" e mais {len(photographers) - 5}"

        dialog = tk.Toplevel(parent)
        dialog.title("Confirmar limpeza de RAW")
        dialog.geometry("700x510")
        dialog.resizable(False, False)
        dialog.transient(parent)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)

        content = ttk.Frame(dialog, padding=22)
        content.grid(row=0, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        ttk.Label(content, text="ATENÇÃO: exclusão permanente", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            content,
            text=(
                f"Alvo: {target_label}\n"
                f"Fotógrafo(s): {photographer_text}\n"
                f"Pastas: {len(folders)}\n"
                f"Arquivos RAW: {total}\n"
                f"Período: {self._window_label(snapshot)}\n\n"
                "A operação não envia os arquivos para a Lixeira e não apaga JPG, XMP ou vídeos."
            ),
            style="Muted.TLabel",
            wraplength=640,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(10, 18))

        first = tk.BooleanVar(value=False)
        second = tk.BooleanVar(value=False)
        typed = tk.StringVar()
        ttk.Checkbutton(
            content,
            text="Entendo que os arquivos RAW serão excluídos permanentemente.",
            variable=first,
        ).grid(row=2, column=0, sticky="w", pady=5)
        ttk.Checkbutton(
            content,
            text="Confirmei o fotógrafo, as pastas e o período operacional acima.",
            variable=second,
        ).grid(row=3, column=0, sticky="w", pady=5)
        ttk.Label(content, text="Digite APAGAR para liberar a exclusão:").grid(row=4, column=0, sticky="w", pady=(16, 4))
        ttk.Entry(content, textvariable=typed).grid(row=5, column=0, sticky="ew")

        actions = ttk.Frame(content)
        actions.grid(row=6, column=0, sticky="ew", pady=(24, 0))
        actions.columnconfigure(0, weight=1)
        ttk.Button(actions, text="Cancelar", style="Secondary.TButton", command=dialog.destroy).grid(row=0, column=1, padx=(0, 8))
        delete_button = ttk.Button(actions, text="APAGAR RAW", style="Danger.TButton", state="disabled")
        delete_button.grid(row=0, column=2)

        def update_state(*_args) -> None:
            enabled = first.get() and second.get() and typed.get().strip().upper() == "APAGAR"
            delete_button.configure(state="normal" if enabled else "disabled")

        first.trace_add("write", update_state)
        second.trace_add("write", update_state)
        typed.trace_add("write", update_state)
        delete_button.configure(
            command=lambda: self._start_raw_cleanup(dialog, parent, snapshot, work_ids, total, target_label)
        )

    def _start_raw_cleanup(
        self,
        dialog: tk.Toplevel,
        parent: tk.Misc,
        snapshot: OperationalInventory,
        work_ids: list[str] | None,
        total: int,
        target_label: str,
    ) -> None:
        confirmed = messagebox.askyesno(
            "Última confirmação",
            f"Apagar definitivamente {total} arquivos RAW de {target_label} agora?",
            icon="warning",
            parent=dialog,
        )
        if not confirmed:
            return
        dialog.destroy()
        self.inventory_scanning = True
        self.inventory_button.configure(state="disabled", text="Apagando...")
        self.inventory_state.set(f"Excluindo CR2, CR3 e DNG de {target_label}...")

        def worker() -> None:
            try:
                result = delete_snapshot_raw_files(snapshot, work_ids)
            except Exception as exc:
                self.after(0, lambda error=exc: self._raw_cleanup_failed(parent, error))
                return
            self.after(0, lambda value=result: self._raw_cleanup_done(parent, value))

        threading.Thread(target=worker, daemon=True, name="RawCleanup").start()

    def _raw_cleanup_failed(self, parent: tk.Misc, exc: Exception) -> None:
        self.inventory_scanning = False
        self.inventory_button.configure(state="normal", text="Atualizar fotos")
        self.inventory_state.set("Falha ao excluir arquivos RAW")
        messagebox.showerror("Falha na limpeza", str(exc), parent=parent)

    def _raw_cleanup_done(self, parent: tk.Misc, result: RawDeletionResult) -> None:
        self.inventory_scanning = False
        self.inventory_button.configure(state="normal", text="Atualizar fotos")
        details = [
            f"{item.work_id} — {item.photographer}: {item.deleted} apagado(s)"
            + (f", {item.failed} falha(s)" if item.failed else "")
            for item in result.folders[:20]
        ]
        if len(result.folders) > 20:
            details.append(f"... e mais {len(result.folders) - 20} pasta(s)")
        summary = (
            f"{result.deleted} arquivo(s) apagado(s).\n"
            f"Espaço liberado: {self._human_size(result.bytes_freed)}.\n"
            f"Falhas: {result.failed}.\n\n"
            + "\n".join(details)
        )
        if result.failed:
            messagebox.showwarning("Limpeza concluída com avisos", summary, parent=parent)
        else:
            messagebox.showinfo("Limpeza concluída", summary, parent=parent)
        try:
            parent.destroy()
        except tk.TclError:
            pass
        self.inventory_snapshot = None
        self._refresh_inventory()


def main() -> None:
    EnhancedDesktopApp().mainloop()


if __name__ == "__main__":
    main()

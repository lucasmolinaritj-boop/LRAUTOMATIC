from __future__ import annotations

import os
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from .desktop import DesktopApp
from .operational_inventory import (
    OperationalInventory,
    RawDeletionResult,
    delete_snapshot_raw_files,
    scan_operational_inventory,
)


class EnhancedDesktopApp(DesktopApp):
    """Desktop V4.5 com inventário limitado ao período operacional Home Picz."""

    def __init__(self, config_path: str = "config.json") -> None:
        super().__init__(config_path)
        self.title("LRAutomatic V4.5")
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
        window.title("Fotos do dia — período operacional")
        window.geometry("1120x680")
        window.minsize(880, 500)
        window.transient(self)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)

        header = ttk.Frame(window, padding=(18, 16, 18, 10))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text=f"{snapshot.total} RAW em {len(snapshot.folders)} trabalho(s)",
            style="Title.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text=(
                f"Período operacional: {self._window_label(snapshot)}  •  "
                f"{snapshot.empty_count} pasta(s) zerada(s)  •  "
                f"{snapshot.missing_count} ausente(s)"
            ),
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))
        ttk.Label(
            header,
            text=f"Leitura em {snapshot.elapsed_seconds:.2f}s",
            style="SuccessBadge.TLabel",
        ).grid(row=0, column=1, rowspan=2, padx=(12, 0))

        body = ttk.Frame(window, padding=(18, 0, 18, 8))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        columns = ("id", "photographer", "scheduled", "cr2", "cr3", "dng", "total", "modified", "state")
        tree = ttk.Treeview(body, columns=columns, show="headings")
        definitions = (
            ("id", "ID", 85, "center"),
            ("photographer", "Fotógrafo", 150, "w"),
            ("scheduled", "Agenda", 145, "w"),
            ("cr2", "CR2", 60, "center"),
            ("cr3", "CR3", 60, "center"),
            ("dng", "DNG", 60, "center"),
            ("total", "Total", 65, "center"),
            ("modified", "Último RAW", 135, "w"),
            ("state", "Situação", 170, "w"),
        )
        for key, title, width, anchor in definitions:
            tree.heading(key, text=title)
            tree.column(key, width=width, anchor=anchor, stretch=key in {"photographer", "state"})
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)
        tree.tag_configure("warning", background="#FFF4D6", foreground=self.WARNING)
        tree.tag_configure("missing", background="#FFF0F0", foreground=self.DANGER)

        for folder in snapshot.folders:
            tag = "missing" if not folder.folder_exists else ("warning" if folder.total == 0 else "")
            tree.insert(
                "",
                "end",
                values=(
                    folder.work_id,
                    folder.photographer,
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

        footer = ttk.Frame(window, padding=(18, 8, 18, 16))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        warning = (
            "A limpeza considera somente os IDs desta consulta e apaga apenas arquivos .CR2, .CR3 e .DNG. "
            "JPG, XMP, vídeos e demais arquivos são preservados."
        )
        ttk.Label(footer, text=warning, style="Muted.TLabel", wraplength=760).grid(row=0, column=0, sticky="w")
        ttk.Button(
            footer,
            text="ESVAZIAR ARQUIVOS RAW...",
            style="Danger.TButton",
            command=lambda: self._open_raw_cleanup_confirmation(window, snapshot),
        ).grid(row=0, column=1, padx=(12, 0))

    def _open_raw_cleanup_confirmation(self, parent: tk.Misc, snapshot: OperationalInventory) -> None:
        if snapshot.total == 0:
            messagebox.showinfo("Limpeza de RAW", "Não há arquivos RAW neste período.", parent=parent)
            return

        dialog = tk.Toplevel(parent)
        dialog.title("Confirmar limpeza de RAW")
        dialog.geometry("650x390")
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
                f"Serão apagados {snapshot.total} arquivos CR2/CR3/DNG de "
                f"{len(snapshot.folders)} trabalho(s) do período {self._window_label(snapshot)}.\n\n"
                "A operação não envia os arquivos para a Lixeira e não apaga JPG, XMP ou vídeos."
            ),
            style="Muted.TLabel",
            wraplength=590,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(10, 18))

        first = tk.BooleanVar(value=False)
        second = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            content,
            text="Entendo que os arquivos RAW serão excluídos permanentemente.",
            variable=first,
        ).grid(row=2, column=0, sticky="w", pady=5)
        ttk.Checkbutton(
            content,
            text="Confirmei que este é o período operacional correto.",
            variable=second,
        ).grid(row=3, column=0, sticky="w", pady=5)

        actions = ttk.Frame(content)
        actions.grid(row=4, column=0, sticky="ew", pady=(24, 0))
        actions.columnconfigure(0, weight=1)
        ttk.Button(actions, text="Cancelar", style="Secondary.TButton", command=dialog.destroy).grid(row=0, column=1, padx=(0, 8))
        delete_button = ttk.Button(actions, text="APAGAR RAW", style="Danger.TButton", state="disabled")
        delete_button.grid(row=0, column=2)

        def update_state(*_args) -> None:
            delete_button.configure(state="normal" if first.get() and second.get() else "disabled")

        first.trace_add("write", update_state)
        second.trace_add("write", update_state)
        delete_button.configure(command=lambda: self._start_raw_cleanup(dialog, parent, snapshot))

    def _start_raw_cleanup(self, dialog: tk.Toplevel, parent: tk.Misc, snapshot: OperationalInventory) -> None:
        confirmed = messagebox.askyesno(
            "Última confirmação",
            f"Apagar definitivamente {snapshot.total} arquivos RAW agora?",
            icon="warning",
            parent=dialog,
        )
        if not confirmed:
            return
        dialog.destroy()
        self.inventory_scanning = True
        self.inventory_button.configure(state="disabled", text="Apagando...")
        self.inventory_state.set("Excluindo somente CR2, CR3 e DNG das pastas confirmadas...")

        def worker() -> None:
            result = delete_snapshot_raw_files(snapshot)
            self.after(0, lambda value=result: self._raw_cleanup_done(parent, value))

        threading.Thread(target=worker, daemon=True, name="RawCleanup").start()

    def _raw_cleanup_done(self, parent: tk.Misc, result: RawDeletionResult) -> None:
        self.inventory_scanning = False
        self.inventory_button.configure(state="normal", text="Atualizar fotos")
        summary = (
            f"{result.deleted} arquivo(s) apagado(s).\n"
            f"Espaço liberado: {self._human_size(result.bytes_freed)}.\n"
            f"Falhas: {result.failed}."
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

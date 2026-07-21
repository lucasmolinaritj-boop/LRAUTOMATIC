from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .desktop_enhanced import EnhancedDesktopApp
from .operational_inventory import OperationalInventory, delete_snapshot_raw_files


class SelectiveCleanupDesktopApp(EnhancedDesktopApp):
    """Gerenciador de RAW com escolha explícita das extensões a excluir."""

    def __init__(self, config_path: str = "config.json") -> None:
        super().__init__(config_path)
        self.title("LRAutomatic V4.7")

    @staticmethod
    def _extension_counts(snapshot: OperationalInventory, work_ids: list[str] | None) -> dict[str, int]:
        folders = snapshot.select(work_ids)
        return {
            ".cr2": sum(folder.cr2 for folder in folders),
            ".cr3": sum(folder.cr3 for folder in folders),
            ".dng": sum(folder.dng for folder in folders),
        }

    def _open_raw_cleanup_confirmation(
        self,
        parent: tk.Misc,
        snapshot: OperationalInventory,
        work_ids: list[str] | None,
        target_label: str,
    ) -> None:
        folders = snapshot.select(work_ids)
        counts = self._extension_counts(snapshot, work_ids)
        available = {extension: count for extension, count in counts.items() if count > 0}
        if not available:
            messagebox.showinfo("Limpeza de RAW", "Não há arquivos RAW no alvo escolhido.", parent=parent)
            return

        photographers = sorted({folder.photographer for folder in folders}, key=str.casefold)
        photographer_text = ", ".join(photographers[:5])
        if len(photographers) > 5:
            photographer_text += f" e mais {len(photographers) - 5}"

        dialog = tk.Toplevel(parent)
        dialog.title("Confirmar limpeza seletiva de RAW")
        dialog.geometry("720x620")
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
                f"Período: {self._window_label(snapshot)}"
            ),
            style="Muted.TLabel",
            wraplength=660,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(10, 14))

        extension_box = ttk.LabelFrame(content, text="Extensões que serão apagadas", padding=(14, 10))
        extension_box.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        extension_vars: dict[str, tk.BooleanVar] = {}
        labels = {".cr2": "CR2", ".cr3": "CR3", ".dng": "DNG"}
        for column, extension in enumerate((".cr2", ".cr3", ".dng")):
            count = counts[extension]
            variable = tk.BooleanVar(value=count > 0)
            extension_vars[extension] = variable
            checkbox = ttk.Checkbutton(
                extension_box,
                text=f"{labels[extension]} ({count})",
                variable=variable,
                state="normal" if count > 0 else "disabled",
            )
            checkbox.grid(row=0, column=column, sticky="w", padx=(0, 28))

        selection_summary = tk.StringVar()
        ttk.Label(
            content,
            textvariable=selection_summary,
            style="WarningBadge.TLabel",
            wraplength=660,
            justify="left",
        ).grid(row=3, column=0, sticky="ew", pady=(0, 12))

        ttk.Label(
            content,
            text="A operação não envia os arquivos para a Lixeira. Extensões não marcadas, JPG, XMP, vídeos e outros arquivos serão preservados.",
            style="Muted.TLabel",
            wraplength=660,
            justify="left",
        ).grid(row=4, column=0, sticky="w", pady=(0, 14))

        first = tk.BooleanVar(value=False)
        second = tk.BooleanVar(value=False)
        typed = tk.StringVar()
        ttk.Checkbutton(
            content,
            text="Entendo que as extensões marcadas serão excluídas permanentemente.",
            variable=first,
        ).grid(row=5, column=0, sticky="w", pady=5)
        ttk.Checkbutton(
            content,
            text="Confirmei o fotógrafo, as pastas, o período e as extensões acima.",
            variable=second,
        ).grid(row=6, column=0, sticky="w", pady=5)
        ttk.Label(content, text="Digite APAGAR para liberar a exclusão:").grid(row=7, column=0, sticky="w", pady=(16, 4))
        ttk.Entry(content, textvariable=typed).grid(row=8, column=0, sticky="ew")

        actions = ttk.Frame(content)
        actions.grid(row=9, column=0, sticky="ew", pady=(24, 0))
        actions.columnconfigure(0, weight=1)
        ttk.Button(actions, text="Cancelar", style="Secondary.TButton", command=dialog.destroy).grid(row=0, column=1, padx=(0, 8))
        delete_button = ttk.Button(actions, text="APAGAR EXTENSÕES MARCADAS", style="Danger.TButton", state="disabled")
        delete_button.grid(row=0, column=2)

        def selected_extensions() -> tuple[str, ...]:
            return tuple(extension for extension, variable in extension_vars.items() if variable.get() and counts[extension] > 0)

        def update_state(*_args) -> None:
            selected = selected_extensions()
            total = sum(counts[extension] for extension in selected)
            names = ", ".join(labels[extension] for extension in selected) or "nenhuma extensão"
            selection_summary.set(f"Selecionado: {names}  •  {total} arquivo(s) serão apagados")
            enabled = bool(selected) and first.get() and second.get() and typed.get().strip().upper() == "APAGAR"
            delete_button.configure(state="normal" if enabled else "disabled")

        for variable in extension_vars.values():
            variable.trace_add("write", update_state)
        first.trace_add("write", update_state)
        second.trace_add("write", update_state)
        typed.trace_add("write", update_state)

        delete_button.configure(
            command=lambda: self._start_selective_raw_cleanup(
                dialog,
                parent,
                snapshot,
                work_ids,
                target_label,
                selected_extensions(),
                counts,
            )
        )
        update_state()

    def _start_selective_raw_cleanup(
        self,
        dialog: tk.Toplevel,
        parent: tk.Misc,
        snapshot: OperationalInventory,
        work_ids: list[str] | None,
        target_label: str,
        extensions: tuple[str, ...],
        counts: dict[str, int],
    ) -> None:
        if not extensions:
            messagebox.showwarning("Limpeza de RAW", "Selecione ao menos uma extensão.", parent=dialog)
            return
        total = sum(counts[extension] for extension in extensions)
        names = ", ".join(extension.lstrip(".").upper() for extension in extensions)
        confirmed = messagebox.askyesno(
            "Última confirmação",
            f"Apagar definitivamente {total} arquivo(s) {names} de {target_label} agora?",
            icon="warning",
            parent=dialog,
        )
        if not confirmed:
            return

        dialog.destroy()
        self.inventory_scanning = True
        self.inventory_button.configure(state="disabled", text="Apagando...")
        self.inventory_state.set(f"Excluindo somente {names} de {target_label}...")

        def worker() -> None:
            try:
                result = delete_snapshot_raw_files(snapshot, work_ids, extensions)
            except Exception as exc:
                self.after(0, lambda error=exc: self._raw_cleanup_failed(parent, error))
                return
            self.after(0, lambda value=result: self._raw_cleanup_done(parent, value))

        threading.Thread(target=worker, daemon=True, name="SelectiveRawCleanup").start()


def main() -> None:
    SelectiveCleanupDesktopApp().mainloop()


if __name__ == "__main__":
    main()

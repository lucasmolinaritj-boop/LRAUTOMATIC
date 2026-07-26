from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from tkinterdnd2 import COPY, DND_FILES, REFUSE_DROP

from .desktop_range_drag import RangeDragDesktopApp
from .models import ImportJobRequest, ImportSource
from .operational_inventory import OperationalFolder


class ReliableDragDesktopApp(RangeDragDesktopApp):
    """Arraste de pastas com lista Tcl explícita e importação direta de reserva."""

    def __init__(self, config_path: str = "config.json") -> None:
        super().__init__(config_path)
        self.title("LRAutomatic V5.7")

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
        snapshot = self.inventory_snapshot
        if tree is None or snapshot is None:
            return

        folders_by_id = {folder.work_id: folder for folder in snapshot.folders}
        status_var = tk.StringVar(
            value=(
                "Arraste as linhas para a Grade da Biblioteca. Se o Lightroom recusar, "
                "use Importar seleção no Lightroom."
            )
        )

        actions = ttk.LabelFrame(
            popup,
            text="Importação no Lightroom",
            padding=(14, 8),
        )
        actions.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 12))
        actions.columnconfigure(0, weight=1)
        ttk.Label(
            actions,
            textvariable=status_var,
            style="Muted.TLabel",
            wraplength=850,
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        def selected_folders() -> list[OperationalFolder]:
            result: list[OperationalFolder] = []
            for item_id in tree.selection():
                folder = folders_by_id.get(str(item_id))
                if folder is None or not folder.folder_exists:
                    continue
                if folder not in result:
                    result.append(folder)
            return result

        def reliable_drag_init(_event):
            folders = selected_folders()
            paths = [folder.path for folder in folders if Path(folder.path).is_dir()]
            if not paths:
                status_var.set("Selecione ao menos uma pasta existente para arrastar.")
                return (REFUSE_DROP, DND_FILES, ())

            # O tkdnd recebe uma lista Tcl. Montá-la explicitamente evita que espaços,
            # acentos e barras invertidas quebrem o CF_HDROP enviado ao Windows.
            tcl_file_list = self.tk.call("list", *paths)
            status_var.set(
                f"Arrastando {len(paths)} pasta(s). Solte sobre a Grade do módulo Biblioteca."
            )
            return (COPY, DND_FILES, tcl_file_list)

        def reliable_drag_end(event) -> None:
            action = str(getattr(event, "action", "") or "")
            if action.lower() in {"refuse_drop", "none", ""}:
                status_var.set(
                    "O destino não confirmou o drop. Confira se Lightroom e LRAutomatic "
                    "estão no mesmo nível de privilégio ou use a importação direta."
                )
            else:
                status_var.set(f"Arraste finalizado: {action}.")

        if self._native_drag_ready:
            tree.drag_source_register(1, DND_FILES)
            tree.dnd_bind("<<DragInitCmd>>", reliable_drag_init)
            tree.dnd_bind("<<DragEndCmd>>", reliable_drag_end)
        else:
            detail = f" Detalhe: {self._native_drag_error}" if self._native_drag_error else ""
            status_var.set(
                "Suporte nativo de arraste indisponível. Execute instalar.bat."
                + detail
            )

        def queue_selected_import() -> None:
            folders = selected_folders()
            if not folders:
                messagebox.showinfo(
                    "Importar no Lightroom",
                    "Selecione uma ou mais pastas existentes.",
                    parent=popup,
                )
                return

            sources: list[ImportSource] = []
            for folder in folders:
                metadata = self.raw_metadata.get(folder.work_id, {})
                street = str(metadata.get("rua") or "").strip()
                collection = f"{folder.work_id} - {street}" if street else folder.work_id
                sources.append(
                    ImportSource(
                        path=folder.path,
                        collection=collection,
                        recursive=bool(self.settings.homepicz_recursive),
                        work_id=folder.work_id,
                        photographer=folder.photographer,
                        client=str(metadata.get("cliente") or "").strip() or None,
                        service_name=folder.service or None,
                        scheduled_at=folder.scheduled_at or None,
                    )
                )

            request = ImportJobRequest(
                sources=sources,
                collection_set=None,
                recursive=bool(self.settings.homepicz_recursive),
                create_collections=False,
                organize_collections_by_photographer=False,
                organize_collections_by_client=False,
                build_standard_previews=bool(self.settings.homepicz_standard_previews),
                standard_preview_size=int(self.settings.homepicz_standard_preview_size),
                build_smart_previews=bool(self.settings.homepicz_smart_previews),
                allowed_extensions=self.settings.allowed_extensions,
                develop_preset_name=self.settings.homepicz_preset_name,
                duplicate_policy="skip",
            )
            try:
                job = self.store.create(request)
            except Exception as exc:
                messagebox.showerror(
                    "Importar no Lightroom",
                    str(exc),
                    parent=popup,
                )
                return

            self.selected_job_id = job.job_id
            self._refresh_jobs(True)
            status_var.set(
                f"{len(folders)} pasta(s) enviadas à fila do Lightroom: {job.job_id}."
            )
            messagebox.showinfo(
                "Importar no Lightroom",
                f"{len(folders)} pasta(s) enviadas à fila.\n\nJob: {job.job_id}",
                parent=popup,
            )

        ttk.Button(
            actions,
            text="IMPORTAR SELEÇÃO NO LIGHTROOM",
            style="Primary.TButton",
            command=queue_selected_import,
        ).grid(row=0, column=1, padx=(12, 0))


def main() -> None:
    ReliableDragDesktopApp().mainloop()


if __name__ == "__main__":
    main()

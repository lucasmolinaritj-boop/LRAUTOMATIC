from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from tkinterdnd2 import COPY, DND_FILES, REFUSE_DROP

from .desktop_range_drag import RangeDragDesktopApp
from .homepicz_scheduler import current_import_window
from .models import ImportJobRequest, ImportSource
from .operational_inventory import OperationalFolder, OperationalInventory

MANUAL_COLLECTION_PREFIX = "Home Picz - Manual - "
COLLECTION_ORGANIZATION_VERSION = 4


class ReliableDragDesktopApp(RangeDragDesktopApp):
    """Gerenciador integrado ao período operacional e à fila do Lightroom."""

    def __init__(self, config_path: str = "config.json") -> None:
        self._open_inventory_when_ready = False
        super().__init__(config_path)

        # A página inicial deve nascer com o mesmo período usado pela automação.
        # Aos sábados, domingos e segundas isso resulta em sexta + sábado.
        default_window = current_import_window(self.settings)
        self.raw_inventory_start_date = default_window.start
        self.raw_inventory_end_date = default_window.end
        self.raw_inventory_date = default_window.start
        self.raw_calendar_button.configure(text=self._raw_calendar_button_text())
        self.inventory_state.set(
            f"Período do Gerenciador de RAW: {self._selected_raw_window_label()}. "
            "Clique em Ver pastas para consultar."
        )
        self.title("LRAutomatic V6.1")

    @staticmethod
    def _find_inventory_footer(popup: tk.Toplevel) -> ttk.Frame | None:
        """Localiza o rodapé original da tabela para manter a ação sempre visível."""
        for child in popup.winfo_children():
            if not isinstance(child, ttk.Frame):
                continue
            try:
                info = child.grid_info()
            except tk.TclError:
                continue
            if str(info.get("row")) == "2":
                return child
        return None

    @staticmethod
    def _find_filter_box(popup: tk.Toplevel) -> ttk.LabelFrame | None:
        for child in popup.winfo_children():
            if not isinstance(child, ttk.LabelFrame):
                continue
            try:
                if str(child.cget("text")) == "Filtros e ordenação":
                    return child
            except tk.TclError:
                continue
        return None

    @classmethod
    def _promote_situation_filter(cls, popup: tk.Toplevel) -> None:
        """Coloca Situação na primeira linha para não ficar escondida."""
        controls = cls._find_filter_box(popup)
        if controls is None:
            return

        labels: dict[str, ttk.Label] = {}
        combos: list[ttk.Combobox] = []
        checkbuttons: list[ttk.Checkbutton] = []
        entries: list[ttk.Entry] = []
        buttons: list[ttk.Button] = []

        for child in controls.winfo_children():
            if isinstance(child, ttk.Label):
                try:
                    labels[str(child.cget("text"))] = child
                except tk.TclError:
                    pass
            elif isinstance(child, ttk.Combobox):
                combos.append(child)
            elif isinstance(child, ttk.Checkbutton):
                checkbuttons.append(child)
            elif isinstance(child, ttk.Entry):
                entries.append(child)
            elif isinstance(child, ttk.Button):
                buttons.append(child)

        combo_by_label: dict[str, ttk.Combobox] = {}
        for label_text, label_widget in labels.items():
            try:
                label_info = label_widget.grid_info()
                label_row = int(label_info.get("row", -1))
                label_column = int(label_info.get("column", -1))
            except (tk.TclError, TypeError, ValueError):
                continue
            for combo in combos:
                try:
                    combo_info = combo.grid_info()
                    combo_row = int(combo_info.get("row", -1))
                    combo_column = int(combo_info.get("column", -1))
                except (tk.TclError, TypeError, ValueError):
                    continue
                if combo_row == label_row + 1 and combo_column == label_column:
                    combo_by_label[label_text] = combo
                    break

        # Situação fica ao lado de Dia e continua combinável com todos os filtros.
        positions = {
            "Dia": (0, 0),
            "Situação": (0, 1),
            "Fotógrafo": (0, 2),
            "Editor de foto": (0, 3),
            "Cliente": (0, 4),
            "Serviço": (2, 0),
            "Ordenar por": (2, 1),
        }
        for column in range(5):
            controls.columnconfigure(column, weight=1)

        for label_text, (row, column) in positions.items():
            label_widget = labels.get(label_text)
            combo = combo_by_label.get(label_text)
            if label_widget is not None:
                label_widget.grid(
                    row=row,
                    column=column,
                    sticky="w",
                    padx=(0, 8),
                )
            if combo is not None:
                combo.grid(
                    row=row + 1,
                    column=column,
                    sticky="ew",
                    padx=(0, 8),
                )

        for checkbutton in checkbuttons:
            try:
                if str(checkbutton.cget("text")) == "Decrescente":
                    checkbutton.grid(row=3, column=2, sticky="w", padx=(4, 8))
            except tk.TclError:
                continue

        search_label = next(
            (widget for text, widget in labels.items() if text.startswith("Pesquisar ")),
            None,
        )
        if search_label is not None:
            search_label.grid(
                row=4,
                column=0,
                columnspan=5,
                sticky="w",
                pady=(10, 3),
            )
        if entries:
            entries[0].grid(
                row=5,
                column=0,
                columnspan=4,
                sticky="ew",
                padx=(0, 8),
            )
        for button in buttons:
            try:
                if str(button.cget("text")) == "Limpar filtros":
                    button.grid(row=5, column=4, sticky="ew")
            except tk.TclError:
                continue

    def _raw_calendar_inventory_done(
        self,
        snapshot: OperationalInventory,
        metadata: dict[str, dict[str, str]],
    ) -> None:
        super()._raw_calendar_inventory_done(snapshot, metadata)
        if self._open_inventory_when_ready:
            self._open_inventory_when_ready = False
            self.after_idle(self._show_inventory_details)

    def _raw_calendar_inventory_failed(self, error: Exception) -> None:
        self._open_inventory_when_ready = False
        super()._raw_calendar_inventory_failed(error)

    def _show_inventory_details(self) -> None:
        # Ver pastas vira uma ação única: consulta quando ainda não há inventário;
        # se a consulta já existe, apenas abre a tabela sem consultar novamente.
        if self.inventory_snapshot is None:
            self._open_inventory_when_ready = True
            if self.inventory_scanning:
                self.inventory_state.set(
                    "Consulta em andamento. O Gerenciador abrirá automaticamente ao concluir."
                )
            else:
                self.inventory_state.set(
                    f"Consultando {self._selected_raw_window_label()} para abrir o Gerenciador..."
                )
                self._refresh_inventory()
            return

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
        self._promote_situation_filter(popup)

        tree = self._find_treeview(popup)
        snapshot = self.inventory_snapshot
        if tree is None or snapshot is None:
            return

        folders_by_id = {folder.work_id: folder for folder in snapshot.folders}
        status_var = tk.StringVar(
            value=(
                "Selecione uma ou mais linhas. O botão abaixo cria a tarefa diretamente; "
                "o arraste continua disponível como alternativa."
            )
        )

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
                    "O Lightroom não confirmou o drop. Use o botão IMPORTAR SELEÇÃO NO LIGHTROOM."
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
                "Arraste nativo indisponível, mas a importação pelo botão funciona normalmente."
                + detail
            )

        def queue_selected_import() -> None:
            folders = selected_folders()
            if not folders:
                messagebox.showinfo(
                    "Importar no Lightroom",
                    "Selecione uma ou mais linhas da tabela antes de importar.",
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
                        expected_count=folder.total,
                        work_id=folder.work_id,
                        photographer=folder.photographer,
                        client=str(metadata.get("cliente") or "").strip() or None,
                        service_name=folder.service or None,
                        scheduled_at=folder.scheduled_at or None,
                    )
                )

            selected_window = self._selected_raw_window()
            manual_collection_set = f"{MANUAL_COLLECTION_PREFIX}{selected_window.label}"
            request = ImportJobRequest(
                sources=sources,
                collection_set=manual_collection_set,
                recursive=bool(self.settings.homepicz_recursive),
                create_collections=False,
                organize_collections_by_photographer=True,
                organize_collections_by_client=True,
                collection_organization_version=COLLECTION_ORGANIZATION_VERSION,
                build_standard_previews=bool(self.settings.homepicz_standard_previews),
                standard_preview_size=int(self.settings.homepicz_standard_preview_size),
                build_smart_previews=bool(self.settings.homepicz_smart_previews),
                allowed_extensions=self.settings.allowed_extensions,
                develop_preset_name=self.settings.homepicz_preset_name,
                duplicate_policy="skip",
            )
            try:
                job = self.store.create(request)
                job.collections_status = "requested"
                job.collections_organization_version = 0
                job.collections_run_once_token = job.job_id
                job.add_event(
                    "collections",
                    "Organização por dia solicitada",
                    (
                        "Após a importação, as fotos serão vinculadas em Home Picz - DIA > "
                        "Fotógrafos/Clientes > Nome > Horário > ID - Rua."
                    ),
                )
                self.store.save(job)
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
                f"{len(folders)} pasta(s) enviadas à fila; as coleções serão organizadas por dia."
            )
            messagebox.showinfo(
                "Importar no Lightroom",
                (
                    f"{len(folders)} pasta(s) enviadas à fila.\n\n"
                    "As coleções serão criadas conforme a data de cada trabalho.\n\n"
                    f"Job: {job.job_id}"
                ),
                parent=popup,
            )

        footer = self._find_inventory_footer(popup)
        if footer is None:
            footer = ttk.Frame(popup, padding=(14, 6, 14, 14))
            footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)

        ttk.Button(
            footer,
            text="IMPORTAR SELEÇÃO NO LIGHTROOM — GERAR TAREFA",
            style="Primary.TButton",
            command=queue_selected_import,
        ).grid(row=1, column=0, columnspan=5, sticky="ew", pady=(10, 4))
        ttk.Label(
            footer,
            textvariable=status_var,
            style="Muted.TLabel",
            wraplength=1100,
            justify="left",
        ).grid(row=2, column=0, columnspan=5, sticky="w", pady=(2, 0))

        try:
            popup.update_idletasks()
            required_height = popup.winfo_reqheight()
            current_width = max(popup.winfo_width(), 1280)
            screen_height = popup.winfo_screenheight()
            popup.geometry(f"{current_width}x{min(required_height + 20, screen_height - 80)}")
        except tk.TclError:
            pass


def main() -> None:
    ReliableDragDesktopApp().mainloop()


if __name__ == "__main__":
    main()

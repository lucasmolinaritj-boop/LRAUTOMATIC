from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .catalogs import create_catalog
from .config import (
    BOOL_SETTINGS,
    INT_SETTINGS,
    OPTIONAL_SETTINGS,
    PATH_SETTINGS,
    SETTING_GROUPS,
    SETTING_LABELS,
    Settings,
    generate_api_key,
    load_settings,
    save_settings,
    settings_from_dict,
)
from .diagnostics import create_diagnostic_zip
from .models import ImportJobRequest, ImportSource
from .store import JobStore


class ScrollableFrame(ttk.Frame):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.content = ttk.Frame(self.canvas, padding=(4, 4, 14, 14))
        self.window_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.content.bind("<Configure>", self._sync_scroll_region)
        self.canvas.bind("<Configure>", self._sync_width)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _sync_scroll_region(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _sync_width(self, event) -> None:
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _on_mousewheel(self, event) -> None:
        if self.winfo_ismapped():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")


class DesktopApp(tk.Tk):
    def __init__(self, config_path: str = "config.json") -> None:
        super().__init__()
        self.config_path = config_path
        self.settings = load_settings(config_path)
        self.store = JobStore(self.settings)
        self.title("LRAutomatic V4.1")
        self.geometry("1080x780")
        self.minsize(900, 650)
        self.option_add("*Font", ("Segoe UI", 10))

        self.catalog_name = tk.StringVar()
        self.collection_set = tk.StringVar()
        self.preset_name = tk.StringVar()
        self.smart_previews = tk.BooleanVar(value=True)
        self.recursive = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Pronto — Lightroom pode ficar aberto.")
        self.sources: list[Path] = []
        self.setting_vars: dict[str, tk.Variable] = {}
        self.setting_entries: dict[str, ttk.Entry] = {}
        self.path_summary_labels: list[ttk.Label] = []
        self._build()
        self._populate_settings_form()
        self._refresh_jobs()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, padding=(22, 18, 22, 10))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="LRAutomatic", font=("Segoe UI", 24, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Catálogo → importação → preset → Smart Previews", font=("Segoe UI", 11)).grid(row=1, column=0, sticky="w")
        ttk.Label(header, text="V4.1", font=("Segoe UI", 11, "bold")).grid(row=0, column=1, rowspan=2, sticky="e")

        notebook = ttk.Notebook(self)
        notebook.grid(row=1, column=0, sticky="nsew", padx=22, pady=(0, 12))

        pipeline = ttk.Frame(notebook, padding=18)
        catalog = ttk.Frame(notebook, padding=18)
        jobs = ttk.Frame(notebook, padding=18)
        settings_tab = ttk.Frame(notebook, padding=10)
        support = ttk.Frame(notebook, padding=18)
        notebook.add(pipeline, text="Pipeline de fotos")
        notebook.add(catalog, text="Novo catálogo")
        notebook.add(jobs, text="Fila e estado")
        notebook.add(settings_tab, text="Configurações")
        notebook.add(support, text="Diagnóstico")

        self._build_pipeline(pipeline)
        self._build_catalog(catalog)
        self._build_jobs(jobs)
        self._build_settings(settings_tab)
        self._build_support(support)

        footer = ttk.Frame(self, padding=(22, 8, 22, 16))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Separator(footer).grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(footer, textvariable=self.status).grid(row=1, column=0, sticky="w")

    def _build_pipeline(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        ttk.Label(parent, text="1. Pastas de origem", font=("Segoe UI", 13, "bold")).grid(row=0, column=0, sticky="w")
        buttons = ttk.Frame(parent)
        buttons.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        ttk.Button(buttons, text="+ Adicionar pasta", command=self._add_source).pack(side="left")
        ttk.Button(buttons, text="Remover selecionada", command=self._remove_source).pack(side="left", padx=8)
        ttk.Button(buttons, text="Limpar", command=self._clear_sources).pack(side="left")

        self.source_list = tk.Listbox(parent, height=9, activestyle="dotbox")
        self.source_list.grid(row=2, column=0, sticky="nsew")

        options = ttk.LabelFrame(parent, text="2. Processamento no Lightroom", padding=14)
        options.grid(row=3, column=0, sticky="ew", pady=14)
        options.columnconfigure(1, weight=1)
        ttk.Label(options, text="Conjunto de coleções").grid(row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Entry(options, textvariable=self.collection_set).grid(row=0, column=1, sticky="ew")
        ttk.Label(options, text="Preset de Revelação").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=(10, 0))
        ttk.Entry(options, textvariable=self.preset_name).grid(row=1, column=1, sticky="ew", pady=(10, 0))
        ttk.Label(options, text="Use o nome exato de um preset já instalado no Lightroom.").grid(row=2, column=1, sticky="w", pady=(2, 8))
        checks = ttk.Frame(options)
        checks.grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(checks, text="Criar Smart Previews oficiais", variable=self.smart_previews).pack(side="left")
        ttk.Checkbutton(checks, text="Incluir subpastas", variable=self.recursive).pack(side="left", padx=18)

        ttk.Button(parent, text="ENVIAR PARA O LIGHTROOM", command=self._queue_import).grid(row=4, column=0, sticky="ew", ipady=8)

    def _build_catalog(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        ttk.Label(parent, text="Criar catálogo gerenciado", font=("Segoe UI", 15, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(parent, text="O app copia seu catálogo-modelo vazio de forma atômica, valida a cópia e abre o novo catálogo no Lightroom.", wraplength=760).grid(row=1, column=0, sticky="w", pady=(4, 18))
        card = ttk.LabelFrame(parent, text="Novo trabalho", padding=18)
        card.grid(row=2, column=0, sticky="ew")
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text="Nome do catálogo").grid(row=0, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.catalog_name).grid(row=1, column=0, sticky="ew", pady=(5, 12))
        ttk.Button(card, text="Criar e abrir no Lightroom", command=self._create_catalog).grid(row=2, column=0, sticky="ew", ipady=6)

        self.paths_frame = ttk.LabelFrame(parent, text="Configuração atual", padding=14)
        self.paths_frame.grid(row=3, column=0, sticky="ew", pady=16)
        self._refresh_path_summary()

    def _build_jobs(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        top = ttk.Frame(parent)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(top, text="Tarefas recentes", font=("Segoe UI", 15, "bold")).pack(side="left")
        ttk.Button(top, text="Atualizar", command=self._refresh_jobs).pack(side="right")
        self.jobs_tree = ttk.Treeview(parent, columns=("status", "imported", "preset", "smart"), show="headings")
        for key, title, width in (("status", "Status", 110), ("imported", "Fotos", 80), ("preset", "Preset", 160), ("smart", "Smart Previews", 160)):
            self.jobs_tree.heading(key, text=title)
            self.jobs_tree.column(key, width=width, anchor="w")
        self.jobs_tree.grid(row=1, column=0, sticky="nsew")

    def _build_settings(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        header = ttk.Frame(parent, padding=(8, 4, 8, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Configurações completas", font=("Segoe UI", 15, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Edite aqui todas as variáveis persistentes do Python. O config.json será atualizado automaticamente e continuará compatível com CLI e serviço.", wraplength=760).grid(row=1, column=0, sticky="w", pady=(4, 0))

        scroll = ScrollableFrame(parent)
        scroll.grid(row=1, column=0, sticky="nsew")
        self.settings_content = scroll.content
        self.settings_content.columnconfigure(0, weight=1)

        actions = ttk.Frame(parent, padding=(8, 10, 8, 4))
        actions.grid(row=2, column=0, sticky="ew")
        ttk.Button(actions, text="Salvar configurações", command=self._save_settings_from_form).pack(side="left")
        ttk.Button(actions, text="Validar caminhos", command=self._validate_settings_paths).pack(side="left", padx=8)
        ttk.Button(actions, text="Recarregar arquivo", command=self._reload_settings).pack(side="left")
        ttk.Button(actions, text="Restaurar padrões", command=self._restore_defaults).pack(side="left", padx=8)
        ttk.Button(actions, text="Gerar chave forte", command=self._generate_api_key).pack(side="right")

        row = 0
        for group_name, setting_names in SETTING_GROUPS:
            group = ttk.LabelFrame(self.settings_content, text=group_name, padding=14)
            group.grid(row=row, column=0, sticky="ew", padx=4, pady=(6, 10))
            group.columnconfigure(1, weight=1)
            row += 1
            for field_row, name in enumerate(setting_names):
                ttk.Label(group, text=SETTING_LABELS[name]).grid(row=field_row, column=0, sticky="w", padx=(0, 12), pady=5)
                if name in BOOL_SETTINGS:
                    variable = tk.BooleanVar()
                    ttk.Checkbutton(group, variable=variable).grid(row=field_row, column=1, sticky="w", pady=5)
                elif name == "catalog_date_source":
                    variable = tk.StringVar()
                    combo = ttk.Combobox(group, textvariable=variable, state="readonly", values=("earliest_file", "today"))
                    combo.grid(row=field_row, column=1, sticky="ew", pady=5)
                else:
                    variable = tk.StringVar()
                    entry = ttk.Entry(group, textvariable=variable, show="•" if name == "api_key" else "")
                    entry.grid(row=field_row, column=1, sticky="ew", pady=5)
                    self.setting_entries[name] = entry
                    if name in PATH_SETTINGS:
                        ttk.Button(group, text="Procurar…", command=lambda n=name: self._browse_setting_path(n)).grid(row=field_row, column=2, padx=(8, 0), pady=5)
                self.setting_vars[name] = variable

        ttk.Label(self.settings_content, text="Alterações em host, porta, chave, pasta de dados ou automações em execução podem exigir reinício do serviço/agente para entrarem em vigor.", wraplength=800).grid(row=row, column=0, sticky="w", padx=8, pady=(0, 16))

    def _build_support(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        ttk.Label(parent, text="Suporte e diagnóstico", font=("Segoe UI", 15, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(parent, text="O pacote inclui jobs, logs do plugin, estado do loop, configuração sanitizada e informações do sistema — nunca inclui suas fotos ou chave da API.", wraplength=760).grid(row=1, column=0, sticky="w", pady=(4, 18))
        ttk.Button(parent, text="Gerar ZIP de diagnóstico", command=self._diagnostic).grid(row=2, column=0, sticky="ew", ipady=7)
        ttk.Button(parent, text="Abrir pasta de dados", command=self._open_data_dir).grid(row=3, column=0, sticky="ew", pady=10)

    def _populate_settings_form(self) -> None:
        values = self.settings.to_json_dict()
        for name, variable in self.setting_vars.items():
            value = values.get(name)
            if name in BOOL_SETTINGS:
                variable.set(bool(value))
            else:
                variable.set("" if value is None else str(value))

    def _settings_form_data(self) -> dict[str, object]:
        raw: dict[str, object] = {}
        for name, variable in self.setting_vars.items():
            value = variable.get()
            if name in BOOL_SETTINGS:
                raw[name] = bool(value)
            elif name in INT_SETTINGS:
                text = str(value).strip()
                if not text:
                    raise ValueError(f"{SETTING_LABELS[name]} não pode ficar vazio.")
                raw[name] = int(text)
            elif name in OPTIONAL_SETTINGS:
                raw[name] = str(value).strip() or None
            else:
                raw[name] = str(value).strip()
        return raw

    def _save_settings_from_form(self) -> None:
        try:
            new_settings = settings_from_dict(self._settings_form_data())
            path = save_settings(new_settings, self.config_path)
            self.settings = new_settings
            self.store = JobStore(self.settings)
            self._refresh_path_summary()
            self.status.set(f"Configurações salvas em {path}")
            messagebox.showinfo("LRAutomatic", f"Configurações salvas com segurança.\n\n{path}\n\nReinicie o serviço/agente caso tenha alterado parâmetros de execução.")
        except Exception as exc:
            messagebox.showerror("Configuração inválida", f"{type(exc).__name__}: {exc}")

    def _validate_settings_paths(self) -> None:
        try:
            candidate = settings_from_dict(self._settings_form_data())
            errors = candidate.validate(check_paths=True)
            if errors:
                messagebox.showwarning("Validação", "Foram encontrados problemas:\n\n- " + "\n- ".join(errors))
            else:
                messagebox.showinfo("Validação", "Todas as variáveis e caminhos configurados são válidos.")
        except Exception as exc:
            messagebox.showerror("Validação", f"{type(exc).__name__}: {exc}")

    def _reload_settings(self) -> None:
        try:
            self.settings = load_settings(self.config_path)
            self.store = JobStore(self.settings)
            self._populate_settings_form()
            self._refresh_path_summary()
            self.status.set("Configurações recarregadas do arquivo.")
        except Exception as exc:
            messagebox.showerror("LRAutomatic", f"Não foi possível recarregar: {exc}")

    def _restore_defaults(self) -> None:
        if not messagebox.askyesno("Restaurar padrões", "Preencher o formulário com os valores padrão? Nada será salvo até você clicar em Salvar configurações."):
            return
        self.settings = Settings(api_key=generate_api_key())
        self._populate_settings_form()
        self.status.set("Valores padrão carregados no formulário; ainda não foram salvos.")

    def _generate_api_key(self) -> None:
        self.setting_vars["api_key"].set(generate_api_key())
        self.status.set("Nova chave forte gerada. Clique em Salvar configurações.")

    def _browse_setting_path(self, name: str) -> None:
        if name in {"catalog_template", "lightroom_executable"}:
            filetypes = (("Catálogo Lightroom", "*.lrcat"), ("Todos os arquivos", "*.*")) if name == "catalog_template" else (("Executável", "*.exe"), ("Todos os arquivos", "*.*"))
            selected = filedialog.askopenfilename(title=SETTING_LABELS[name], filetypes=filetypes)
        else:
            selected = filedialog.askdirectory(title=SETTING_LABELS[name])
        if selected:
            self.setting_vars[name].set(selected)

    def _refresh_path_summary(self) -> None:
        if not hasattr(self, "paths_frame"):
            return
        for widget in self.paths_frame.winfo_children():
            widget.destroy()
        rows = (
            ("Modelo", self.settings.catalog_template or "não configurado"),
            ("Destino", self.settings.catalog_output_root or "não configurado"),
            ("Lightroom", self.settings.lightroom_executable or "não configurado"),
        )
        for index, (label, value) in enumerate(rows):
            ttk.Label(self.paths_frame, text=f"{label}: {value}", wraplength=820).grid(row=index, column=0, sticky="w", pady=2)

    def _run(self, label: str, action, done) -> None:
        self.status.set(label)

        def worker() -> None:
            try:
                result = action()
            except Exception as exc:
                self.after(0, lambda: (self.status.set("Falha"), messagebox.showerror("LRAutomatic", f"{type(exc).__name__}: {exc}")))
                return
            self.after(0, lambda: done(result))

        threading.Thread(target=worker, daemon=True).start()

    def _add_source(self) -> None:
        path = filedialog.askdirectory(title="Adicionar pasta de fotos")
        if path and Path(path) not in self.sources:
            self.sources.append(Path(path))
            self.source_list.insert("end", path)

    def _remove_source(self) -> None:
        selected = self.source_list.curselection()
        if selected:
            index = selected[0]
            self.source_list.delete(index)
            self.sources.pop(index)

    def _clear_sources(self) -> None:
        self.sources.clear()
        self.source_list.delete(0, "end")

    def _queue_import(self) -> None:
        if not self.sources:
            messagebox.showwarning("LRAutomatic", "Adicione ao menos uma pasta de fotos.")
            return
        request = ImportJobRequest(
            sources=[ImportSource(path=str(path), collection=path.name) for path in self.sources],
            collection_set=self.collection_set.get().strip() or None,
            recursive=self.recursive.get(),
            build_smart_previews=self.smart_previews.get(),
            develop_preset_name=self.preset_name.get().strip() or None,
        )
        job = self.store.create(request)
        self.status.set(f"Tarefa enviada: {job.job_id}")
        self._refresh_jobs()
        messagebox.showinfo("LRAutomatic", f"Tarefa enviada ao Lightroom.\n\n{job.job_id}")

    def _create_catalog(self) -> None:
        name = self.catalog_name.get().strip()
        if not name:
            messagebox.showwarning("LRAutomatic", "Informe o nome do catálogo.")
            return
        self._run(
            "Criando catálogo e abrindo o Lightroom...",
            lambda: create_catalog(self.settings, name, open_lightroom=True),
            lambda result: (self.status.set(f"Catálogo criado: {result.catalog_path}"), messagebox.showinfo("LRAutomatic", f"Catálogo criado com segurança:\n\n{result.catalog_path}")),
        )

    def _refresh_jobs(self) -> None:
        if not hasattr(self, "jobs_tree"):
            return
        for item in self.jobs_tree.get_children():
            self.jobs_tree.delete(item)
        for job in self.store.list()[:100]:
            preset = job.preset_status
            if job.preset_name_applied:
                preset = f"{job.preset_name_applied} ({job.preset_applied_count})"
            smart = job.smart_previews_status
            if job.smart_previews_created or job.smart_previews_existed:
                smart = f"{job.smart_previews_created} novas / {job.smart_previews_existed} existentes"
            self.jobs_tree.insert("", "end", values=(job.status, job.total_imported, preset, smart))

    def _diagnostic(self) -> None:
        output = filedialog.askdirectory(title="Onde salvar o ZIP de diagnóstico?")
        if output:
            self._run("Coletando diagnóstico...", lambda: create_diagnostic_zip(self.settings, self.config_path, Path(output)), lambda path: (self.status.set(f"Diagnóstico criado: {path}"), messagebox.showinfo("LRAutomatic", f"ZIP criado:\n\n{path}")))

    def _open_data_dir(self) -> None:
        os.startfile(self.settings.data_dir)


def main() -> None:
    DesktopApp().mainloop()


if __name__ == "__main__":
    main()

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .catalogs import create_catalog
from .config import load_settings
from .diagnostics import create_diagnostic_zip
from .models import ImportJobRequest, ImportSource
from .store import JobStore


class DesktopApp(tk.Tk):
    def __init__(self, config_path: str = "config.json") -> None:
        super().__init__()
        self.config_path = config_path
        self.settings = load_settings(config_path)
        self.store = JobStore(self.settings)
        self.title("LRAutomatic V4.0")
        self.geometry("980x700")
        self.minsize(860, 620)
        self.option_add("*Font", ("Segoe UI", 10))

        self.catalog_name = tk.StringVar()
        self.collection_set = tk.StringVar()
        self.preset_name = tk.StringVar()
        self.smart_previews = tk.BooleanVar(value=True)
        self.recursive = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Pronto — Lightroom pode ficar aberto.")
        self.sources: list[Path] = []
        self._build()
        self._refresh_jobs()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, padding=(22, 18, 22, 10))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="LRAutomatic", font=("Segoe UI", 24, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Catálogo → importação → preset → Smart Previews", font=("Segoe UI", 11)).grid(row=1, column=0, sticky="w")
        ttk.Label(header, text="V4.0", font=("Segoe UI", 11, "bold")).grid(row=0, column=1, rowspan=2, sticky="e")

        notebook = ttk.Notebook(self)
        notebook.grid(row=1, column=0, sticky="nsew", padx=22, pady=(0, 12))

        pipeline = ttk.Frame(notebook, padding=18)
        catalog = ttk.Frame(notebook, padding=18)
        jobs = ttk.Frame(notebook, padding=18)
        support = ttk.Frame(notebook, padding=18)
        notebook.add(pipeline, text="Pipeline de fotos")
        notebook.add(catalog, text="Novo catálogo")
        notebook.add(jobs, text="Fila e estado")
        notebook.add(support, text="Diagnóstico")

        self._build_pipeline(pipeline)
        self._build_catalog(catalog)
        self._build_jobs(jobs)
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

        paths = ttk.LabelFrame(parent, text="Configuração atual", padding=14)
        paths.grid(row=3, column=0, sticky="ew", pady=16)
        ttk.Label(paths, text=f"Modelo: {self.settings.catalog_template or 'não configurado'}", wraplength=760).grid(sticky="w")
        ttk.Label(paths, text=f"Destino: {self.settings.catalog_output_root or 'não configurado'}", wraplength=760).grid(sticky="w", pady=4)
        ttk.Label(paths, text=f"Lightroom: {self.settings.lightroom_executable or 'não configurado'}", wraplength=760).grid(sticky="w")

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

    def _build_support(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        ttk.Label(parent, text="Suporte e diagnóstico", font=("Segoe UI", 15, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(parent, text="O pacote inclui jobs, logs do plugin, estado do loop, configuração sanitizada e informações do sistema — nunca inclui suas fotos ou chave da API.", wraplength=760).grid(row=1, column=0, sticky="w", pady=(4, 18))
        ttk.Button(parent, text="Gerar ZIP de diagnóstico", command=self._diagnostic).grid(row=2, column=0, sticky="ew", ipady=7)
        ttk.Button(parent, text="Abrir pasta de dados", command=self._open_data_dir).grid(row=3, column=0, sticky="ew", pady=10)

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
        import os
        os.startfile(self.settings.data_dir)


def main() -> None:
    DesktopApp().mainloop()


if __name__ == "__main__":
    main()

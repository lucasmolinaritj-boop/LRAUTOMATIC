from __future__ import annotations

import os
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .automation_control import read_control, request_force_next, set_paused
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
from .photo_inventory import PhotoInventory, scan_photo_inventory
from .store import JobStore

STATUS_PT = {
    "queued": "Na fila",
    "running": "Em andamento",
    "completed": "Concluída",
    "partial": "Concluída parcialmente",
    "failed": "Falhou",
    "cancelled": "Cancelada",
}
TERMINAL = {"completed", "partial", "failed", "cancelled"}


class ScrollableFrame(ttk.Frame):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.content = ttk.Frame(self.canvas, padding=(2, 2, 12, 16))
        self.window_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.content.bind(
            "<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind(
            "<Configure>", lambda e: self.canvas.itemconfigure(self.window_id, width=e.width)
        )
        self.canvas.bind("<Enter>", lambda _e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda _e: self.canvas.unbind_all("<MouseWheel>"))

    def _wheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")


class DesktopApp(tk.Tk):
    BG = "#F4F6F8"
    SURFACE = "#FFFFFF"
    TEXT = "#17202A"
    MUTED = "#657180"
    BORDER = "#DCE2E8"
    ACCENT = "#246BFD"
    SUCCESS = "#16855B"
    WARNING = "#B7791F"
    DANGER = "#B42318"

    def __init__(self, config_path: str = "config.json") -> None:
        super().__init__()
        self.config_path = config_path
        self.settings = load_settings(config_path)
        self.store = JobStore(self.settings)
        self.title("LRAutomatic V4.4")
        self.geometry("1320x900")
        self.minsize(1080, 740)
        self.configure(bg=self.BG)
        self.option_add("*Font", ("Segoe UI", 10))

        self.catalog_name = tk.StringVar()
        self.collection_set = tk.StringVar()
        self.preset_name = tk.StringVar()
        self.smart_previews = tk.BooleanVar(value=True)
        self.recursive = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Pronto para receber uma nova tarefa.")
        self.source_count = tk.StringVar(value="Nenhuma pasta adicionada")
        self.config_state = tk.StringVar(value="Configuração carregada")
        self.monitor_state = tk.StringVar(value="Monitoramento automático ativo")
        self.history_filter = tk.StringVar(value="Todas")
        self.history_search = tk.StringVar()
        self.automation_state = tk.StringVar(value="Lendo estado da automação...")
        self.force_state = tk.StringVar(value="")
        self.inventory_state = tk.StringVar(value="Clique em Atualizar fotos")
        self.inventory_vars = {
            "cr2": tk.StringVar(value="—"),
            "cr3": tk.StringVar(value="—"),
            "dng": tk.StringVar(value="—"),
            "total": tk.StringVar(value="—"),
        }
        self.sources: list[Path] = []
        self.setting_vars = {}
        self.setting_entries = {}
        self.jobs_by_id = {}
        self.selected_job_id = None
        self.inventory_snapshot: PhotoInventory | None = None
        self.inventory_scanning = False

        self._styles()
        self._build()
        self._populate_settings_form()
        self._refresh_jobs(True)
        self._refresh_control_state()
        self.after(2500, self._auto_refresh)

    def _styles(self) -> None:
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        for name, bg in (
            ("App.TFrame", self.BG),
            ("Surface.TFrame", self.SURFACE),
            ("Card.TFrame", self.SURFACE),
        ):
            s.configure(name, background=bg)
        s.configure("Card.TFrame", relief="solid", borderwidth=1)
        s.configure(
            "Header.TLabel",
            background=self.BG,
            foreground=self.TEXT,
            font=("Segoe UI", 25, "bold"),
        )
        s.configure("Subtitle.TLabel", background=self.BG, foreground=self.MUTED)
        s.configure(
            "Title.TLabel",
            background=self.SURFACE,
            foreground=self.TEXT,
            font=("Segoe UI", 16, "bold"),
        )
        s.configure(
            "Section.TLabel",
            background=self.SURFACE,
            foreground=self.TEXT,
            font=("Segoe UI", 12, "bold"),
        )
        s.configure("Body.TLabel", background=self.SURFACE, foreground=self.TEXT)
        s.configure("Muted.TLabel", background=self.SURFACE, foreground=self.MUTED)
        s.configure(
            "Status.TLabel",
            background=self.SURFACE,
            foreground=self.MUTED,
            font=("Segoe UI", 9),
        )
        s.configure(
            "Badge.TLabel",
            background="#E8F0FF",
            foreground=self.ACCENT,
            font=("Segoe UI", 9, "bold"),
            padding=(9, 4),
        )
        s.configure(
            "SuccessBadge.TLabel",
            background="#E5F5EE",
            foreground=self.SUCCESS,
            font=("Segoe UI", 9, "bold"),
            padding=(9, 4),
        )
        s.configure(
            "WarningBadge.TLabel",
            background="#FFF4D6",
            foreground=self.WARNING,
            font=("Segoe UI", 9, "bold"),
            padding=(9, 4),
        )
        s.configure(
            "Metric.TLabel",
            background=self.SURFACE,
            foreground=self.TEXT,
            font=("Segoe UI", 20, "bold"),
        )
        s.configure(
            "MetricCaption.TLabel",
            background=self.SURFACE,
            foreground=self.MUTED,
            font=("Segoe UI", 9),
        )
        s.configure("TNotebook", background=self.BG, borderwidth=0)
        s.configure(
            "TNotebook.Tab",
            padding=(18, 11),
            background="#E9EDF2",
            foreground=self.MUTED,
            font=("Segoe UI", 10, "bold"),
            borderwidth=0,
        )
        s.map(
            "TNotebook.Tab",
            background=[("selected", self.SURFACE)],
            foreground=[("selected", self.ACCENT)],
        )
        s.configure(
            "Primary.TButton",
            background=self.ACCENT,
            foreground="#FFF",
            padding=(15, 10),
            font=("Segoe UI", 10, "bold"),
            borderwidth=0,
        )
        s.configure(
            "Secondary.TButton",
            background="#EDF1F5",
            foreground=self.TEXT,
            padding=(12, 8),
            borderwidth=0,
        )
        s.configure(
            "Danger.TButton",
            background="#FFF0F0",
            foreground=self.DANGER,
            padding=(12, 8),
            borderwidth=0,
        )
        s.configure(
            "Treeview",
            background="#FFF",
            fieldbackground="#FFF",
            foreground=self.TEXT,
            rowheight=34,
            borderwidth=0,
        )
        s.configure(
            "Treeview.Heading",
            background="#EEF2F6",
            foreground=self.TEXT,
            font=("Segoe UI", 9, "bold"),
            padding=8,
            relief="flat",
        )
        s.map(
            "Treeview",
            background=[("selected", "#DCE8FF")],
            foreground=[("selected", self.TEXT)],
        )
        s.configure(
            "TLabelframe",
            background=self.SURFACE,
            bordercolor=self.BORDER,
            relief="solid",
            borderwidth=1,
        )
        s.configure(
            "TLabelframe.Label",
            background=self.SURFACE,
            foreground=self.TEXT,
            font=("Segoe UI", 11, "bold"),
        )

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        h = ttk.Frame(self, style="App.TFrame", padding=(28, 22, 28, 14))
        h.grid(row=0, column=0, sticky="ew")
        h.columnconfigure(0, weight=1)
        ttk.Label(h, text="LRAutomatic", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            h,
            text="Central de catálogos, importação e automação do Lightroom Classic",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w")
        b = ttk.Frame(h, style="App.TFrame")
        b.grid(row=0, column=1, rowspan=2)
        ttk.Label(b, text="V4.4", style="Badge.TLabel").pack(side="left", padx=(0, 8))
        ttk.Label(b, text="MONITOR AO VIVO", style="SuccessBadge.TLabel").pack(side="left")

        shell = ttk.Frame(self, style="App.TFrame", padding=(28, 0, 28, 0))
        shell.grid(row=1, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)
        nb = ttk.Notebook(shell)
        nb.grid(row=0, column=0, sticky="nsew")
        for title, builder in (
            ("Importação", self._build_pipeline),
            ("Novo catálogo", self._build_catalog),
            ("Monitor e histórico", self._build_jobs),
            ("Configurações", self._build_settings),
            ("Diagnóstico", self._build_support),
        ):
            frame = ttk.Frame(nb, style="Surface.TFrame", padding=12 if title == "Configurações" else 22)
            nb.add(frame, text=title)
            builder(frame)

        foot = ttk.Frame(self, style="Surface.TFrame", padding=(28, 10, 28, 12))
        foot.grid(row=2, column=0, sticky="ew")
        foot.columnconfigure(1, weight=1)
        ttk.Label(foot, text="●", foreground=self.SUCCESS, background=self.SURFACE).grid(
            row=0, column=0, padx=(0, 7)
        )
        ttk.Label(foot, textvariable=self.status, style="Status.TLabel").grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(foot, textvariable=self.config_state, style="Status.TLabel").grid(row=0, column=2)

    def _heading(self, parent, title, subtitle) -> None:
        ttk.Label(parent, text=title, style="Title.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(parent, text=subtitle, style="Muted.TLabel", wraplength=900).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 18)
        )

    def _build_pipeline(self, parent) -> None:
        parent.columnconfigure(0, weight=3)
        parent.columnconfigure(1, weight=2)
        parent.rowconfigure(3, weight=1)
        self._heading(parent, "Enviar fotos ao Lightroom", "Monte uma tarefa e acompanhe tudo no monitor ao vivo.")
        source_box = ttk.LabelFrame(parent, text="Pastas de origem", padding=16)
        source_box.grid(row=2, column=0, rowspan=2, sticky="nsew", padx=(0, 12))
        source_box.columnconfigure(0, weight=1)
        source_box.rowconfigure(2, weight=1)
        bar = ttk.Frame(source_box, style="Surface.TFrame")
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Button(bar, text="Adicionar pasta", style="Primary.TButton", command=self._add_source).pack(side="left")
        ttk.Button(bar, text="Remover", style="Secondary.TButton", command=self._remove_source).pack(side="left", padx=8)
        ttk.Button(bar, text="Limpar", style="Danger.TButton", command=self._clear_sources).pack(side="left")
        ttk.Label(source_box, textvariable=self.source_count, style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(12, 8)
        )
        self.source_list = tk.Listbox(
            source_box,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=self.BORDER,
            selectbackground="#DCE8FF",
        )
        self.source_list.grid(row=2, column=0, sticky="nsew")

        options = ttk.LabelFrame(parent, text="Processamento", padding=16)
        options.grid(row=2, column=1, sticky="nsew")
        options.columnconfigure(0, weight=1)
        ttk.Label(options, text="Conjunto de coleções").grid(row=0, column=0, sticky="w")
        ttk.Entry(options, textvariable=self.collection_set).grid(row=1, column=0, sticky="ew", pady=(5, 12))
        ttk.Label(options, text="Preset de revelação").grid(row=2, column=0, sticky="w")
        ttk.Entry(options, textvariable=self.preset_name).grid(row=3, column=0, sticky="ew", pady=(5, 12))
        ttk.Checkbutton(options, text="Criar Smart Previews oficiais", variable=self.smart_previews).grid(row=4, column=0, sticky="w")
        ttk.Checkbutton(options, text="Incluir subpastas", variable=self.recursive).grid(row=5, column=0, sticky="w", pady=5)
        ttk.Button(
            parent,
            text="ENVIAR TAREFA AO LIGHTROOM",
            style="Primary.TButton",
            command=self._queue_import,
        ).grid(row=3, column=1, sticky="sew", pady=(12, 0), ipady=4)

    def _build_catalog(self, parent) -> None:
        parent.columnconfigure(0, weight=3)
        parent.columnconfigure(1, weight=2)
        self._heading(parent, "Criar catálogo gerenciado", "Crie e abra um catálogo com segurança.")
        card = ttk.LabelFrame(parent, text="Novo trabalho", padding=18)
        card.grid(row=2, column=0, sticky="new", padx=(0, 12))
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text="Nome do catálogo").grid(row=0, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.catalog_name).grid(row=1, column=0, sticky="ew", pady=(7, 14))
        ttk.Button(card, text="CRIAR E ABRIR NO LIGHTROOM", style="Primary.TButton", command=self._create_catalog).grid(row=2, column=0, sticky="ew")
        self.paths_frame = ttk.LabelFrame(parent, text="Configuração atual", padding=16)
        self.paths_frame.grid(row=2, column=1, sticky="new")
        self._refresh_path_summary()

    def _build_jobs(self, parent) -> None:
        parent.columnconfigure(0, weight=3)
        parent.columnconfigure(1, weight=2)
        parent.rowconfigure(5, weight=1)
        self._heading(parent, "Monitor e histórico", "Controle a automação, confira as fotos do dia e acompanhe as tarefas em tempo real.")

        cards = ttk.Frame(parent, style="Surface.TFrame")
        cards.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        self.metric_vars = {key: tk.StringVar(value="0") for key in ("active", "done", "photos", "failed")}
        for index, (key, label) in enumerate(
            (("active", "Em andamento"), ("done", "Concluídas"), ("photos", "Fotos importadas"), ("failed", "Com falha"))
        ):
            card = ttk.Frame(cards, style="Card.TFrame", padding=(16, 10))
            card.grid(row=0, column=index, sticky="ew", padx=4)
            cards.columnconfigure(index, weight=1)
            ttk.Label(card, textvariable=self.metric_vars[key], style="Metric.TLabel").pack(anchor="w")
            ttk.Label(card, text=label, style="MetricCaption.TLabel").pack(anchor="w")

        controls = ttk.LabelFrame(parent, text="Controle da automação", padding=(14, 10))
        controls.grid(row=3, column=0, sticky="ew", padx=(0, 12), pady=(0, 10))
        controls.columnconfigure(0, weight=1)
        ttk.Label(controls, textvariable=self.automation_state, style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(controls, textvariable=self.force_state, style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(3, 0))
        self.pause_button = ttk.Button(controls, text="PAUSAR AUTOMAÇÃO", style="Danger.TButton", command=self._toggle_automation)
        self.pause_button.grid(row=0, column=1, rowspan=2, padx=(10, 8))
        ttk.Button(controls, text="FORÇAR PRÓXIMO JOB", style="Primary.TButton", command=self._force_next_job).grid(row=0, column=2, rowspan=2)

        inventory = ttk.LabelFrame(parent, text="Fotos do dia", padding=(14, 10))
        inventory.grid(row=3, column=1, sticky="ew", pady=(0, 10))
        inventory.columnconfigure(0, weight=1)
        metrics = ttk.Frame(inventory, style="Surface.TFrame")
        metrics.grid(row=0, column=0, sticky="ew")
        for index, key in enumerate(("cr2", "cr3", "dng", "total")):
            box = ttk.Frame(metrics, style="Surface.TFrame")
            box.grid(row=0, column=index, sticky="ew", padx=(0, 12))
            metrics.columnconfigure(index, weight=1)
            ttk.Label(box, textvariable=self.inventory_vars[key], style="Section.TLabel").pack(anchor="w")
            ttk.Label(box, text=key.upper(), style="MetricCaption.TLabel").pack(anchor="w")
        inv_actions = ttk.Frame(inventory, style="Surface.TFrame")
        inv_actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        inv_actions.columnconfigure(0, weight=1)
        ttk.Label(inv_actions, textvariable=self.inventory_state, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.inventory_button = ttk.Button(inv_actions, text="Atualizar fotos", style="Secondary.TButton", command=self._refresh_inventory)
        self.inventory_button.grid(row=0, column=1, padx=(8, 6))
        ttk.Button(inv_actions, text="Ver pastas", style="Secondary.TButton", command=self._show_inventory_details).grid(row=0, column=2)

        filters = ttk.Frame(parent, style="Surface.TFrame")
        filters.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        filters.columnconfigure(1, weight=1)
        combo = ttk.Combobox(
            filters,
            textvariable=self.history_filter,
            state="readonly",
            values=("Todas", "Ativas", "Concluídas", "Com problema"),
            width=18,
        )
        combo.grid(row=0, column=0)
        combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_jobs(True))
        search = ttk.Entry(filters, textvariable=self.history_search)
        search.grid(row=0, column=1, sticky="ew", padx=8)
        search.bind("<KeyRelease>", lambda _e: self._refresh_jobs(True))
        ttk.Label(filters, textvariable=self.monitor_state, style="SuccessBadge.TLabel").grid(row=0, column=2, padx=(0, 8))
        ttk.Button(filters, text="Atualizar agora", style="Secondary.TButton", command=self._refresh_jobs).grid(row=0, column=3)

        left = ttk.Frame(parent, style="Surface.TFrame")
        left.grid(row=5, column=0, sticky="nsew", padx=(0, 12))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self.jobs_tree = ttk.Treeview(
            left,
            columns=("created", "status", "folders", "imported", "summary"),
            show="headings",
        )
        for key, title, width in (
            ("created", "Criada em", 125),
            ("status", "Status", 130),
            ("folders", "Pastas", 60),
            ("imported", "Fotos", 70),
            ("summary", "Resultado", 280),
        ):
            self.jobs_tree.heading(key, text=title)
            self.jobs_tree.column(key, width=width, anchor="w", stretch=key == "summary")
        self.jobs_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=self.jobs_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.jobs_tree.configure(yscrollcommand=scrollbar.set)
        self.jobs_tree.bind("<<TreeviewSelect>>", self._select_job)

        right = ttk.LabelFrame(parent, text="Detalhes da tarefa", padding=14)
        right.grid(row=5, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)
        self.detail_title = tk.StringVar(value="Selecione uma tarefa")
        self.detail_badge = tk.StringVar(value="—")
        self.detail_summary = tk.StringVar(value="Os detalhes aparecerão aqui.")
        ttk.Label(right, textvariable=self.detail_title, style="Section.TLabel", wraplength=390).grid(row=0, column=0, sticky="w")
        ttk.Label(right, textvariable=self.detail_badge, style="Badge.TLabel").grid(row=1, column=0, sticky="w", pady=(6, 10))
        ttk.Label(right, textvariable=self.detail_summary, style="Muted.TLabel", wraplength=390, justify="left").grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.detail_text = tk.Text(
            right,
            wrap="word",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=self.BORDER,
            bg="#FFF",
            fg=self.TEXT,
            padx=12,
            pady=10,
            state="disabled",
        )
        self.detail_text.grid(row=3, column=0, sticky="nsew")
        actions = ttk.Frame(right, style="Surface.TFrame")
        actions.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="Abrir pasta da tarefa", style="Secondary.TButton", command=self._open_selected_job).pack(side="left")
        self.cancel_button = ttk.Button(actions, text="Cancelar tarefa", style="Danger.TButton", command=self._cancel_selected_job)
        self.cancel_button.pack(side="right")

    def _build_settings(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        header = ttk.Frame(parent, style="Surface.TFrame", padding=10)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Configurações do sistema", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.config_state, style="SuccessBadge.TLabel").grid(row=0, column=1)
        scroll = ScrollableFrame(parent)
        scroll.grid(row=1, column=0, sticky="nsew")
        self.settings_content = scroll.content
        self.settings_content.columnconfigure(0, weight=1)
        row = 0
        for group, names in SETTING_GROUPS:
            box = ttk.LabelFrame(self.settings_content, text=group, padding=16)
            box.grid(row=row, column=0, sticky="ew", pady=6)
            box.columnconfigure(1, weight=1)
            row += 1
            for item_row, name in enumerate(names):
                ttk.Label(box, text=SETTING_LABELS[name]).grid(row=item_row, column=0, sticky="w", padx=(0, 14), pady=7)
                if name in BOOL_SETTINGS:
                    var = tk.BooleanVar()
                    ttk.Checkbutton(box, variable=var).grid(row=item_row, column=1, sticky="w")
                elif name == "catalog_date_source":
                    var = tk.StringVar()
                    ttk.Combobox(box, textvariable=var, state="readonly", values=("earliest_file", "today")).grid(row=item_row, column=1, sticky="ew")
                else:
                    var = tk.StringVar()
                    entry = ttk.Entry(box, textvariable=var, show="•" if name == "api_key" else "")
                    entry.grid(row=item_row, column=1, sticky="ew")
                    self.setting_entries[name] = entry
                    if name in PATH_SETTINGS:
                        ttk.Button(box, text="Procurar", style="Secondary.TButton", command=lambda n=name: self._browse_setting_path(n)).grid(row=item_row, column=2, padx=(8, 0))
                self.setting_vars[name] = var
        actions = ttk.Frame(parent, style="Surface.TFrame", padding=10)
        actions.grid(row=2, column=0, sticky="ew")
        ttk.Button(actions, text="SALVAR CONFIGURAÇÕES", style="Primary.TButton", command=self._save_settings_from_form).pack(side="left")
        ttk.Button(actions, text="Validar", style="Secondary.TButton", command=self._validate_settings_paths).pack(side="left", padx=8)
        ttk.Button(actions, text="Recarregar", style="Secondary.TButton", command=self._reload_settings).pack(side="left")
        ttk.Button(actions, text="Restaurar padrões", style="Danger.TButton", command=self._restore_defaults).pack(side="left", padx=8)
        ttk.Button(actions, text="Gerar chave forte", style="Secondary.TButton", command=self._generate_api_key).pack(side="right")

    def _build_support(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        self._heading(parent, "Diagnóstico e suporte", "Crie um pacote sanitizado para analisar problemas.")
        card = ttk.LabelFrame(parent, text="Ferramentas", padding=18)
        card.grid(row=2, column=0, sticky="ew")
        card.columnconfigure(0, weight=1)
        ttk.Button(card, text="GERAR ZIP DE DIAGNÓSTICO", style="Primary.TButton", command=self._diagnostic).grid(row=0, column=0, sticky="ew")
        ttk.Button(card, text="Abrir pasta de dados", style="Secondary.TButton", command=self._open_data_dir).grid(row=1, column=0, sticky="ew", pady=(10, 0))

    @staticmethod
    def _dt(value):
        if not value:
            return "—"
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            return value

    def _result(self, job):
        if str(job.status) == "failed":
            return job.error or "Falha no processamento"
        parts = [f"{job.total_imported} importada(s)"]
        if job.total_skipped:
            parts.append(f"{job.total_skipped} ignorada(s)")
        if job.total_failed:
            parts.append(f"{job.total_failed} falha(s)")
        return " • ".join(parts)

    def _matches(self, job):
        status = str(job.status)
        selected_filter = self.history_filter.get()
        if selected_filter == "Ativas" and status not in {"queued", "running"}:
            return False
        if selected_filter == "Concluídas" and status not in {"completed", "partial"}:
            return False
        if selected_filter == "Com problema" and status not in {"failed", "partial", "cancelled"}:
            return False
        query = self.history_search.get().strip().lower()
        haystack = " ".join(
            [
                job.job_id,
                job.request.collection_set or "",
                job.error or "",
                job.active_catalog_path or "",
                *(item.path for item in job.progress),
            ]
        ).lower()
        return not query or query in haystack

    def _timeline(self, job):
        if job.events:
            return [(event.at, event.title, event.detail or "") for event in job.events]
        result = [(job.created_at, "Tarefa criada", f"{len(job.progress)} pasta(s) adicionada(s) à fila.")]
        if str(job.status) != "queued":
            result.append((job.started_at or job.updated_at, "Processamento iniciado", job.active_catalog_path or "Lightroom assumiu a tarefa."))
        for source in job.progress:
            if source.discovered or source.imported or source.skipped or source.failed or str(source.status) != "queued":
                result.append((job.updated_at, source.collection or Path(source.path).name, f"{source.discovered} encontrada(s), {source.imported} importada(s), {source.skipped} ignorada(s), {source.failed} falha(s)."))
        if job.preset_status != "not_requested":
            result.append((job.updated_at, "Preset", f"{job.preset_status}; aplicado em {job.preset_applied_count} foto(s)."))
        if job.smart_previews_status != "not_requested":
            result.append((job.updated_at, "Smart Previews", f"{job.smart_previews_created} criado(s), {job.smart_previews_existed} existente(s), {job.smart_previews_failed} falha(s)."))
        if str(job.status) in TERMINAL:
            result.append((job.finished_at or job.updated_at, STATUS_PT.get(str(job.status), str(job.status)), self._result(job)))
        return result

    def _render(self, job) -> None:
        self.selected_job_id = job.job_id
        self.detail_title.set(job.request.collection_set or f"Tarefa {job.job_id[-8:]}")
        self.detail_badge.set(STATUS_PT.get(str(job.status), str(job.status)))
        self.detail_summary.set(
            f"Criada: {self._dt(job.created_at)}\nAtualizada: {self._dt(job.updated_at)}\nCatálogo: {job.active_catalog_path or 'ainda não informado'}"
        )
        lines = [
            "RESULTADO",
            f"  Fotos encontradas: {job.total_discovered}",
            f"  Fotos importadas: {job.total_imported}",
            f"  Fotos ignoradas: {job.total_skipped}",
            f"  Falhas: {job.total_failed}",
            "",
            "ETAPAS REALIZADAS",
        ]
        for at, title, detail in self._timeline(job):
            lines.extend([f"\n{self._dt(at)}  •  {title}", f"  {detail}" if detail else ""])
        lines.extend(["", "PASTAS PROCESSADAS"])
        for source in job.progress:
            lines.extend(
                [
                    f"\n• {source.collection or Path(source.path).name}",
                    f"  {source.path}",
                    f"  Status: {STATUS_PT.get(str(source.status), str(source.status))} | Encontradas {source.discovered} | Importadas {source.imported} | Ignoradas {source.skipped} | Falhas {source.failed}",
                ]
            )
        if job.error:
            lines.extend(["", "ERRO", f"  {job.error}"])
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", "\n".join(lines))
        self.detail_text.configure(state="disabled")
        self.cancel_button.configure(state="disabled" if str(job.status) in TERMINAL else "normal")

    def _select_job(self, _event=None) -> None:
        selection = self.jobs_tree.selection()
        if selection and selection[0] in self.jobs_by_id:
            self._render(self.jobs_by_id[selection[0]])

    def _refresh_jobs(self, silent=False) -> None:
        if not hasattr(self, "jobs_tree"):
            return
        selected = self.selected_job_id
        jobs = self.store.list()
        self.jobs_by_id = {job.job_id: job for job in jobs}
        for item in self.jobs_tree.get_children():
            self.jobs_tree.delete(item)
        visible = [job for job in jobs if self._matches(job)]
        for index, job in enumerate(visible):
            self.jobs_tree.insert(
                "",
                "end",
                iid=job.job_id,
                values=(
                    self._dt(job.created_at),
                    STATUS_PT.get(str(job.status), str(job.status)),
                    len(job.progress),
                    job.total_imported,
                    self._result(job),
                ),
                tags=(str(job.status), "even" if index % 2 == 0 else "odd"),
            )
        self.jobs_tree.tag_configure("even", background="#FFF")
        self.jobs_tree.tag_configure("odd", background="#F8FAFC")
        self.jobs_tree.tag_configure("failed", foreground=self.DANGER)
        self.jobs_tree.tag_configure("running", foreground=self.ACCENT)
        self.jobs_tree.tag_configure("completed", foreground=self.SUCCESS)
        self.jobs_tree.tag_configure("partial", foreground=self.WARNING)
        self.metric_vars["active"].set(sum(str(job.status) in {"queued", "running"} for job in jobs))
        self.metric_vars["done"].set(sum(str(job.status) in {"completed", "partial"} for job in jobs))
        self.metric_vars["photos"].set(sum(job.total_imported for job in jobs))
        self.metric_vars["failed"].set(sum(str(job.status) == "failed" for job in jobs))
        target = selected if selected in self.jobs_by_id and self.jobs_tree.exists(selected) else (visible[0].job_id if visible else None)
        if target:
            self.jobs_tree.selection_set(target)
            self.jobs_tree.see(target)
            self._render(self.jobs_by_id[target])
        if not silent:
            self.status.set(f"Histórico atualizado: {len(jobs)} tarefa(s).")

    def _refresh_control_state(self) -> None:
        control = read_control(self.settings)
        paused = bool(control["paused"])
        force_pending = bool(control["force_next_requested"])
        running = any(str(job.status) == "running" for job in self.store.list())
        if paused:
            self.automation_state.set("AUTOMAÇÃO PAUSADA" + (" — tarefa atual terminará normalmente" if running else ""))
            self.pause_button.configure(text="RETOMAR AUTOMAÇÃO", style="Primary.TButton")
        else:
            self.automation_state.set("AUTOMAÇÃO ATIVA")
            self.pause_button.configure(text="PAUSAR AUTOMAÇÃO", style="Danger.TButton")
        if force_pending:
            self.force_state.set("Próximo job solicitado; aguardando a tarefa atual terminar." if running else "Próximo job solicitado ao agente.")
        else:
            self.force_state.set(str(control.get("message") or ""))

    def _toggle_automation(self) -> None:
        control = read_control(self.settings)
        paused = not bool(control["paused"])
        set_paused(self.settings, paused)
        self._refresh_control_state()
        self.status.set("Automação pausada." if paused else "Automação retomada.")

    def _force_next_job(self) -> None:
        request_force_next(self.settings)
        self._refresh_control_state()
        self.status.set("Próximo job solicitado. O agente aguardará o job atual terminar.")

    def _refresh_inventory(self) -> None:
        if self.inventory_scanning:
            return
        root = self.settings.homepicz_photos_root
        if not root:
            messagebox.showwarning("Fotos do dia", "Configure a pasta Fotos do dia nas Configurações.")
            return
        self.inventory_scanning = True
        self.inventory_button.configure(state="disabled", text="Contando...")
        self.inventory_state.set("Contagem rápida em andamento, sem travar a tela...")

        def worker() -> None:
            try:
                snapshot = scan_photo_inventory(Path(root))
            except Exception as exc:
                self.after(0, lambda: self._inventory_failed(exc))
                return
            self.after(0, lambda: self._inventory_done(snapshot))

        threading.Thread(target=worker, daemon=True, name="PhotoInventoryScan").start()

    def _inventory_done(self, snapshot: PhotoInventory) -> None:
        self.inventory_scanning = False
        self.inventory_snapshot = snapshot
        self.inventory_button.configure(state="normal", text="Atualizar fotos")
        self.inventory_vars["cr2"].set(str(snapshot.cr2))
        self.inventory_vars["cr3"].set(str(snapshot.cr3))
        self.inventory_vars["dng"].set(str(snapshot.dng))
        self.inventory_vars["total"].set(str(snapshot.total))
        error_note = f" • {len(snapshot.errors)} erro(s) de leitura" if snapshot.errors else ""
        self.inventory_state.set(f"{len(snapshot.folders)} pasta(s) em {snapshot.elapsed_seconds:.2f}s{error_note}")

    def _inventory_failed(self, exc: Exception) -> None:
        self.inventory_scanning = False
        self.inventory_button.configure(state="normal", text="Atualizar fotos")
        self.inventory_state.set("Falha na contagem")
        messagebox.showerror("Fotos do dia", str(exc))

    def _show_inventory_details(self) -> None:
        snapshot = self.inventory_snapshot
        if snapshot is None:
            messagebox.showinfo("Fotos do dia", "Clique em Atualizar fotos primeiro.")
            return
        window = tk.Toplevel(self)
        window.title("Fotos do dia — arquivos por pasta")
        window.geometry("900x620")
        window.minsize(700, 450)
        window.transient(self)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)
        header = ttk.Frame(window, padding=(18, 16, 18, 10))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=f"{snapshot.total} arquivos RAW em {len(snapshot.folders)} pasta(s)", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text=snapshot.root, style="Muted.TLabel").grid(row=1, column=0, sticky="w")
        ttk.Label(header, text=f"Leitura concluída em {snapshot.elapsed_seconds:.2f}s", style="SuccessBadge.TLabel").grid(row=0, column=1, rowspan=2)

        body = ttk.Frame(window, padding=(18, 0, 18, 16))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        tree = ttk.Treeview(body, columns=("folder", "cr2", "cr3", "dng", "total"), show="headings")
        for key, title, width in (
            ("folder", "Pasta", 480),
            ("cr2", "CR2", 75),
            ("cr3", "CR3", 75),
            ("dng", "DNG", 75),
            ("total", "Total", 80),
        ):
            tree.heading(key, text=title)
            tree.column(key, width=width, anchor="w" if key == "folder" else "center", stretch=key == "folder")
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)
        for folder in snapshot.folders:
            tree.insert("", "end", values=(folder.name, folder.cr2, folder.cr3, folder.dng, folder.total))
        if snapshot.errors:
            ttk.Label(body, text=f"Aviso: {len(snapshot.errors)} item(ns) não puderam ser lidos.", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))

    def _auto_refresh(self) -> None:
        try:
            self._refresh_jobs(True)
            self._refresh_control_state()
            self.monitor_state.set("Atualizado automaticamente")
        except Exception as exc:
            self.monitor_state.set(f"Falha ao atualizar: {exc}")
        self.after(2500, self._auto_refresh)

    def _open_selected_job(self) -> None:
        if self.selected_job_id:
            os.startfile(self.settings.jobs_dir)

    def _cancel_selected_job(self) -> None:
        if self.selected_job_id and messagebox.askyesno("Cancelar tarefa", "Deseja cancelar esta tarefa?"):
            self.store.cancel(self.selected_job_id)
            self._refresh_jobs()

    def _populate_settings_form(self) -> None:
        values = self.settings.to_json_dict()
        for name, variable in self.setting_vars.items():
            variable.set(bool(values.get(name)) if name in BOOL_SETTINGS else ("" if values.get(name) is None else str(values.get(name))))

    def _settings_form_data(self):
        result = {}
        for name, variable in self.setting_vars.items():
            value = variable.get()
            if name in BOOL_SETTINGS:
                result[name] = bool(value)
            elif name in INT_SETTINGS:
                result[name] = int(str(value).strip())
            elif name in OPTIONAL_SETTINGS:
                result[name] = str(value).strip() or None
            else:
                result[name] = str(value).strip()
        return result

    def _save_settings_from_form(self) -> None:
        try:
            self.settings = settings_from_dict(self._settings_form_data())
            path = save_settings(self.settings, self.config_path)
            self.store = JobStore(self.settings)
            self._refresh_path_summary()
            self.config_state.set("Configuração salva")
            self._refresh_jobs(True)
            self._refresh_control_state()
            messagebox.showinfo("LRAutomatic", f"Configurações salvas.\n\n{path}")
        except Exception as exc:
            messagebox.showerror("Configuração inválida", str(exc))

    def _validate_settings_paths(self) -> None:
        try:
            errors = settings_from_dict(self._settings_form_data()).validate(check_paths=True)
            if errors:
                messagebox.showwarning("Validação", "- " + "\n- ".join(errors))
            else:
                messagebox.showinfo("Validação", "Tudo válido.")
        except Exception as exc:
            messagebox.showerror("Validação", str(exc))

    def _reload_settings(self) -> None:
        self.settings = load_settings(self.config_path)
        self.store = JobStore(self.settings)
        self._populate_settings_form()
        self._refresh_path_summary()
        self._refresh_jobs(True)
        self._refresh_control_state()

    def _restore_defaults(self) -> None:
        if messagebox.askyesno("Restaurar padrões", "Preencher valores padrão?"):
            self.settings = Settings(api_key=generate_api_key())
            self._populate_settings_form()
            self.config_state.set("Alterações não salvas")

    def _generate_api_key(self) -> None:
        self.setting_vars["api_key"].set(generate_api_key())
        self.config_state.set("Alterações não salvas")

    def _browse_setting_path(self, name) -> None:
        selected = (
            filedialog.askopenfilename(title=SETTING_LABELS[name])
            if name in {"catalog_template", "lightroom_executable"}
            else filedialog.askdirectory(title=SETTING_LABELS[name])
        )
        if selected:
            self.setting_vars[name].set(selected)

    def _refresh_path_summary(self) -> None:
        if not hasattr(self, "paths_frame"):
            return
        for widget in self.paths_frame.winfo_children():
            widget.destroy()
        for index, (label, value) in enumerate(
            (
                ("Catálogo-modelo", self.settings.catalog_template or "Não configurado"),
                ("Destino", self.settings.catalog_output_root or "Não configurado"),
                ("Lightroom", self.settings.lightroom_executable or "Não configurado"),
            )
        ):
            ttk.Label(self.paths_frame, text=label, style="Section.TLabel").grid(row=index * 2, column=0, sticky="w", pady=(8, 2))
            ttk.Label(self.paths_frame, text=str(value), style="Muted.TLabel", wraplength=390).grid(row=index * 2 + 1, column=0, sticky="w")

    def _run(self, label, action, done) -> None:
        self.status.set(label)

        def worker() -> None:
            try:
                result = action()
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("LRAutomatic", str(exc)))
                return
            self.after(0, lambda: done(result))

        threading.Thread(target=worker, daemon=True).start()

    def _update_source_count(self) -> None:
        count = len(self.sources)
        self.source_count.set("Nenhuma pasta adicionada" if not count else f"{count} pasta(s) adicionada(s)")

    def _add_source(self) -> None:
        selected = filedialog.askdirectory(title="Adicionar pasta de fotos")
        if selected and Path(selected) not in self.sources:
            self.sources.append(Path(selected))
            self.source_list.insert("end", selected)
            self._update_source_count()

    def _remove_source(self) -> None:
        selection = self.source_list.curselection()
        if selection:
            self.source_list.delete(selection[0])
            self.sources.pop(selection[0])
            self._update_source_count()

    def _clear_sources(self) -> None:
        self.sources.clear()
        self.source_list.delete(0, "end")
        self._update_source_count()

    def _queue_import(self) -> None:
        if not self.sources:
            messagebox.showwarning("LRAutomatic", "Adicione ao menos uma pasta.")
            return
        request = ImportJobRequest(
            sources=[ImportSource(path=str(path), collection=path.name) for path in self.sources],
            collection_set=self.collection_set.get().strip() or None,
            recursive=self.recursive.get(),
            build_smart_previews=self.smart_previews.get(),
            develop_preset_name=self.preset_name.get().strip() or None,
        )
        job = self.store.create(request)
        self.selected_job_id = job.job_id
        self._refresh_jobs(True)
        messagebox.showinfo("LRAutomatic", f"Tarefa enviada.\n\n{job.job_id}")

    def _create_catalog(self) -> None:
        name = self.catalog_name.get().strip()
        if not name:
            messagebox.showwarning("LRAutomatic", "Informe o nome.")
            return
        self._run(
            "Criando catálogo...",
            lambda: create_catalog(self.settings, name, open_lightroom=True),
            lambda result: messagebox.showinfo("LRAutomatic", f"Catálogo criado:\n\n{result.catalog_path}"),
        )

    def _diagnostic(self) -> None:
        output = filedialog.askdirectory(title="Onde salvar o ZIP?")
        if output:
            self._run(
                "Coletando diagnóstico...",
                lambda: create_diagnostic_zip(self.settings, self.config_path, Path(output)),
                lambda path: messagebox.showinfo("LRAutomatic", f"ZIP criado:\n\n{path}"),
            )

    def _open_data_dir(self) -> None:
        os.startfile(self.settings.data_dir)


def main() -> None:
    DesktopApp().mainloop()


if __name__ == "__main__":
    main()

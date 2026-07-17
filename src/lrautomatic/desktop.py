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
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.content = ttk.Frame(self.canvas, padding=(2, 2, 12, 16))
        self.window_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.content.bind("<Configure>", self._sync_scroll_region)
        self.canvas.bind("<Configure>", self._sync_width)
        self.canvas.bind("<Enter>", lambda _event: self.canvas.bind_all("<MouseWheel>", self._on_mousewheel))
        self.canvas.bind("<Leave>", lambda _event: self.canvas.unbind_all("<MouseWheel>"))

    def _sync_scroll_region(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _sync_width(self, event) -> None:
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _on_mousewheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")


class DesktopApp(tk.Tk):
    BG = "#F4F6F8"
    SURFACE = "#FFFFFF"
    TEXT = "#17202A"
    MUTED = "#657180"
    BORDER = "#DCE2E8"
    ACCENT = "#246BFD"
    ACCENT_HOVER = "#1757D8"
    SUCCESS = "#16855B"

    def __init__(self, config_path: str = "config.json") -> None:
        super().__init__()
        self.config_path = config_path
        self.settings = load_settings(config_path)
        self.store = JobStore(self.settings)
        self.title("LRAutomatic V4.2")
        self.geometry("1160x820")
        self.minsize(940, 680)
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
        self.sources: list[Path] = []
        self.setting_vars: dict[str, tk.Variable] = {}
        self.setting_entries: dict[str, ttk.Entry] = {}

        self._configure_styles()
        self._build()
        self._populate_settings_form()
        self._refresh_jobs()

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("App.TFrame", background=self.BG)
        style.configure("Surface.TFrame", background=self.SURFACE)
        style.configure("Card.TFrame", background=self.SURFACE, relief="solid", borderwidth=1)
        style.configure("Header.TLabel", background=self.BG, foreground=self.TEXT, font=("Segoe UI", 25, "bold"))
        style.configure("Subtitle.TLabel", background=self.BG, foreground=self.MUTED, font=("Segoe UI", 10))
        style.configure("Title.TLabel", background=self.SURFACE, foreground=self.TEXT, font=("Segoe UI", 16, "bold"))
        style.configure("Section.TLabel", background=self.SURFACE, foreground=self.TEXT, font=("Segoe UI", 12, "bold"))
        style.configure("Body.TLabel", background=self.SURFACE, foreground=self.TEXT)
        style.configure("Muted.TLabel", background=self.SURFACE, foreground=self.MUTED)
        style.configure("Status.TLabel", background=self.SURFACE, foreground=self.MUTED, font=("Segoe UI", 9))
        style.configure("Badge.TLabel", background="#E8F0FF", foreground=self.ACCENT, font=("Segoe UI", 9, "bold"), padding=(9, 4))
        style.configure("SuccessBadge.TLabel", background="#E5F5EE", foreground=self.SUCCESS, font=("Segoe UI", 9, "bold"), padding=(9, 4))

        style.configure("TNotebook", background=self.BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 11), background="#E9EDF2", foreground=self.MUTED, font=("Segoe UI", 10, "bold"), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", self.SURFACE), ("active", "#F2F5F8")], foreground=[("selected", self.ACCENT), ("active", self.TEXT)])

        style.configure("Primary.TButton", background=self.ACCENT, foreground="#FFFFFF", padding=(15, 10), font=("Segoe UI", 10, "bold"), borderwidth=0)
        style.map("Primary.TButton", background=[("active", self.ACCENT_HOVER), ("pressed", self.ACCENT_HOVER), ("disabled", "#AABCE6")])
        style.configure("Secondary.TButton", background="#EDF1F5", foreground=self.TEXT, padding=(12, 8), borderwidth=0)
        style.map("Secondary.TButton", background=[("active", "#E0E6EC"), ("pressed", "#D7DEE5")])
        style.configure("Danger.TButton", background="#FFF0F0", foreground="#B42318", padding=(12, 8), borderwidth=0)
        style.map("Danger.TButton", background=[("active", "#FFE1E1")])

        style.configure("TEntry", fieldbackground="#FFFFFF", foreground=self.TEXT, bordercolor=self.BORDER, lightcolor=self.BORDER, darkcolor=self.BORDER, padding=7)
        style.map("TEntry", bordercolor=[("focus", self.ACCENT)], lightcolor=[("focus", self.ACCENT)], darkcolor=[("focus", self.ACCENT)])
        style.configure("TCombobox", fieldbackground="#FFFFFF", foreground=self.TEXT, padding=6)
        style.configure("TCheckbutton", background=self.SURFACE, foreground=self.TEXT)
        style.map("TCheckbutton", background=[("active", self.SURFACE)])

        style.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF", foreground=self.TEXT, rowheight=32, borderwidth=0)
        style.configure("Treeview.Heading", background="#EEF2F6", foreground=self.TEXT, font=("Segoe UI", 9, "bold"), padding=8, relief="flat")
        style.map("Treeview", background=[("selected", "#DCE8FF")], foreground=[("selected", self.TEXT)])
        style.configure("TLabelframe", background=self.SURFACE, bordercolor=self.BORDER, relief="solid", borderwidth=1)
        style.configure("TLabelframe.Label", background=self.SURFACE, foreground=self.TEXT, font=("Segoe UI", 11, "bold"))

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, style="App.TFrame", padding=(28, 22, 28, 14))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="LRAutomatic", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Central de catálogos, importação e automação do Lightroom Classic", style="Subtitle.TLabel").grid(row=1, column=0, sticky="w", pady=(3, 0))
        badges = ttk.Frame(header, style="App.TFrame")
        badges.grid(row=0, column=1, rowspan=2, sticky="e")
        ttk.Label(badges, text="V4.2", style="Badge.TLabel").pack(side="left", padx=(0, 8))
        ttk.Label(badges, text="LOCAL", style="SuccessBadge.TLabel").pack(side="left")

        notebook_shell = ttk.Frame(self, style="App.TFrame", padding=(28, 0, 28, 0))
        notebook_shell.grid(row=1, column=0, sticky="nsew")
        notebook_shell.columnconfigure(0, weight=1)
        notebook_shell.rowconfigure(0, weight=1)
        notebook = ttk.Notebook(notebook_shell)
        notebook.grid(row=0, column=0, sticky="nsew")

        pipeline = ttk.Frame(notebook, style="Surface.TFrame", padding=22)
        catalog = ttk.Frame(notebook, style="Surface.TFrame", padding=22)
        jobs = ttk.Frame(notebook, style="Surface.TFrame", padding=22)
        settings_tab = ttk.Frame(notebook, style="Surface.TFrame", padding=12)
        support = ttk.Frame(notebook, style="Surface.TFrame", padding=22)
        notebook.add(pipeline, text="Importação")
        notebook.add(catalog, text="Novo catálogo")
        notebook.add(jobs, text="Fila")
        notebook.add(settings_tab, text="Configurações")
        notebook.add(support, text="Diagnóstico")

        self._build_pipeline(pipeline)
        self._build_catalog(catalog)
        self._build_jobs(jobs)
        self._build_settings(settings_tab)
        self._build_support(support)

        footer = ttk.Frame(self, style="Surface.TFrame", padding=(28, 10, 28, 12))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(1, weight=1)
        ttk.Label(footer, text="●", foreground=self.SUCCESS, background=self.SURFACE, font=("Segoe UI", 10, "bold")).grid(row=0, column=0, padx=(0, 7))
        ttk.Label(footer, textvariable=self.status, style="Status.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(footer, textvariable=self.config_state, style="Status.TLabel").grid(row=0, column=2, sticky="e")

    def _page_heading(self, parent: ttk.Frame, title: str, subtitle: str) -> None:
        ttk.Label(parent, text=title, style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(parent, text=subtitle, style="Muted.TLabel", wraplength=860).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 18))

    def _build_pipeline(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=3)
        parent.columnconfigure(1, weight=2)
        parent.rowconfigure(3, weight=1)
        self._page_heading(parent, "Enviar fotos ao Lightroom", "Monte uma tarefa com uma ou várias pastas e defina como o Lightroom deve processá-las.")

        source_card = ttk.LabelFrame(parent, text="Pastas de origem", padding=16)
        source_card.grid(row=2, column=0, rowspan=2, sticky="nsew", padx=(0, 12))
        source_card.columnconfigure(0, weight=1)
        source_card.rowconfigure(2, weight=1)
        toolbar = ttk.Frame(source_card, style="Surface.TFrame")
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Button(toolbar, text="Adicionar pasta", style="Primary.TButton", command=self._add_source).pack(side="left")
        ttk.Button(toolbar, text="Remover", style="Secondary.TButton", command=self._remove_source).pack(side="left", padx=8)
        ttk.Button(toolbar, text="Limpar", style="Danger.TButton", command=self._clear_sources).pack(side="left")
        ttk.Label(source_card, textvariable=self.source_count, style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(12, 8))
        list_shell = tk.Frame(source_card, bg=self.BORDER, padx=1, pady=1)
        list_shell.grid(row=2, column=0, sticky="nsew")
        self.source_list = tk.Listbox(list_shell, activestyle="none", borderwidth=0, highlightthickness=0, selectbackground="#DCE8FF", selectforeground=self.TEXT, bg="#FFFFFF", fg=self.TEXT, font=("Segoe UI", 10), relief="flat")
        self.source_list.pack(fill="both", expand=True)

        options = ttk.LabelFrame(parent, text="Processamento", padding=16)
        options.grid(row=2, column=1, sticky="nsew")
        options.columnconfigure(0, weight=1)
        ttk.Label(options, text="Conjunto de coleções", style="Body.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(options, textvariable=self.collection_set).grid(row=1, column=0, sticky="ew", pady=(5, 12))
        ttk.Label(options, text="Preset de revelação", style="Body.TLabel").grid(row=2, column=0, sticky="w")
        ttk.Entry(options, textvariable=self.preset_name).grid(row=3, column=0, sticky="ew", pady=(5, 4))
        ttk.Label(options, text="Digite exatamente o nome instalado no Lightroom.", style="Muted.TLabel").grid(row=4, column=0, sticky="w", pady=(0, 12))
        ttk.Checkbutton(options, text="Criar Smart Previews oficiais", variable=self.smart_previews).grid(row=5, column=0, sticky="w", pady=4)
        ttk.Checkbutton(options, text="Incluir fotos das subpastas", variable=self.recursive).grid(row=6, column=0, sticky="w", pady=4)

        action_card = ttk.Frame(parent, style="Surface.TFrame")
        action_card.grid(row=3, column=1, sticky="sew", pady=(12, 0))
        action_card.columnconfigure(0, weight=1)
        ttk.Button(action_card, text="ENVIAR TAREFA AO LIGHTROOM", style="Primary.TButton", command=self._queue_import).grid(row=0, column=0, sticky="ew", ipady=4)
        ttk.Label(action_card, text="A tarefa entra na fila persistente e pode ser retomada após reinicialização.", style="Muted.TLabel", wraplength=360).grid(row=1, column=0, sticky="w", pady=(9, 0))

    def _build_catalog(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=3)
        parent.columnconfigure(1, weight=2)
        self._page_heading(parent, "Criar catálogo gerenciado", "Crie um catálogo a partir do modelo oficial vazio e abra-o diretamente no Lightroom.")
        card = ttk.LabelFrame(parent, text="Novo trabalho", padding=18)
        card.grid(row=2, column=0, sticky="new", padx=(0, 12))
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text="Nome do catálogo", style="Body.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.catalog_name, font=("Segoe UI", 12)).grid(row=1, column=0, sticky="ew", pady=(7, 14), ipady=3)
        ttk.Button(card, text="CRIAR E ABRIR NO LIGHTROOM", style="Primary.TButton", command=self._create_catalog).grid(row=2, column=0, sticky="ew", ipady=4)
        ttk.Label(card, text="A cópia é criada de forma atômica antes de o Lightroom ser aberto.", style="Muted.TLabel", wraplength=520).grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.paths_frame = ttk.LabelFrame(parent, text="Configuração atual", padding=16)
        self.paths_frame.grid(row=2, column=1, sticky="new")
        self._refresh_path_summary()

    def _build_jobs(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)
        self._page_heading(parent, "Fila e histórico", "Acompanhe as tarefas recentes, quantidade de fotos, preset e geração de Smart Previews.")
        toolbar = ttk.Frame(parent, style="Surface.TFrame")
        toolbar.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(toolbar, text="Últimas 100 tarefas", style="Section.TLabel").pack(side="left")
        ttk.Button(toolbar, text="Atualizar", style="Secondary.TButton", command=self._refresh_jobs).pack(side="right")
        tree_shell = tk.Frame(parent, bg=self.BORDER, padx=1, pady=1)
        tree_shell.grid(row=3, column=0, sticky="nsew")
        tree_shell.columnconfigure(0, weight=1)
        tree_shell.rowconfigure(0, weight=1)
        self.jobs_tree = ttk.Treeview(tree_shell, columns=("status", "imported", "preset", "smart"), show="headings")
        for key, title, width in (("status", "Status", 130), ("imported", "Fotos", 90), ("preset", "Preset aplicado", 240), ("smart", "Smart Previews", 240)):
            self.jobs_tree.heading(key, text=title)
            self.jobs_tree.column(key, width=width, minwidth=70, anchor="w", stretch=True)
        scrollbar = ttk.Scrollbar(tree_shell, orient="vertical", command=self.jobs_tree.yview)
        self.jobs_tree.configure(yscrollcommand=scrollbar.set)
        self.jobs_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

    def _build_settings(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        header = ttk.Frame(parent, style="Surface.TFrame", padding=(10, 8, 10, 10))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Configurações do sistema", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Todas as variáveis persistentes do Python ficam disponíveis aqui. O config.json continua sincronizado com CLI, API e serviço.", style="Muted.TLabel", wraplength=850).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(header, textvariable=self.config_state, style="SuccessBadge.TLabel").grid(row=0, column=1, rowspan=2, sticky="e")
        scroll = ScrollableFrame(parent)
        scroll.grid(row=1, column=0, sticky="nsew", padx=(8, 0))
        self.settings_content = scroll.content
        self.settings_content.columnconfigure(0, weight=1)
        actions = ttk.Frame(parent, style="Surface.TFrame", padding=(10, 12, 10, 6))
        actions.grid(row=2, column=0, sticky="ew")
        ttk.Button(actions, text="SALVAR CONFIGURAÇÕES", style="Primary.TButton", command=self._save_settings_from_form).pack(side="left")
        ttk.Button(actions, text="Validar", style="Secondary.TButton", command=self._validate_settings_paths).pack(side="left", padx=8)
        ttk.Button(actions, text="Recarregar", style="Secondary.TButton", command=self._reload_settings).pack(side="left")
        ttk.Button(actions, text="Restaurar padrões", style="Danger.TButton", command=self._restore_defaults).pack(side="left", padx=8)
        ttk.Button(actions, text="Gerar chave forte", style="Secondary.TButton", command=self._generate_api_key).pack(side="right")

        row = 0
        for group_name, setting_names in SETTING_GROUPS:
            group = ttk.LabelFrame(self.settings_content, text=group_name, padding=16)
            group.grid(row=row, column=0, sticky="ew", padx=4, pady=(6, 10))
            group.columnconfigure(1, weight=1)
            row += 1
            for field_row, name in enumerate(setting_names):
                ttk.Label(group, text=SETTING_LABELS[name], style="Body.TLabel").grid(row=field_row, column=0, sticky="w", padx=(0, 14), pady=7)
                if name in BOOL_SETTINGS:
                    variable = tk.BooleanVar()
                    ttk.Checkbutton(group, variable=variable).grid(row=field_row, column=1, sticky="w", pady=7)
                elif name == "catalog_date_source":
                    variable = tk.StringVar()
                    combo = ttk.Combobox(group, textvariable=variable, state="readonly", values=("earliest_file", "today"))
                    combo.grid(row=field_row, column=1, sticky="ew", pady=7)
                else:
                    variable = tk.StringVar()
                    entry = ttk.Entry(group, textvariable=variable, show="•" if name == "api_key" else "")
                    entry.grid(row=field_row, column=1, sticky="ew", pady=7)
                    self.setting_entries[name] = entry
                    if name in PATH_SETTINGS:
                        ttk.Button(group, text="Procurar", style="Secondary.TButton", command=lambda n=name: self._browse_setting_path(n)).grid(row=field_row, column=2, padx=(8, 0), pady=7)
                self.setting_vars[name] = variable

        note = ttk.Frame(self.settings_content, style="Card.TFrame", padding=14)
        note.grid(row=row, column=0, sticky="ew", padx=4, pady=(0, 16))
        ttk.Label(note, text="Atenção", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(note, text="Mudanças em host, porta, chave, pasta de dados ou automações em execução podem exigir reinício do serviço e do agente.", style="Muted.TLabel", wraplength=820).grid(row=1, column=0, sticky="w", pady=(4, 0))

    def _build_support(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        self._page_heading(parent, "Diagnóstico e suporte", "Crie um pacote sanitizado para analisar problemas sem incluir fotos, catálogos ou a chave da API.")
        card = ttk.LabelFrame(parent, text="Ferramentas", padding=18)
        card.grid(row=2, column=0, sticky="ew")
        card.columnconfigure(0, weight=1)
        ttk.Button(card, text="GERAR ZIP DE DIAGNÓSTICO", style="Primary.TButton", command=self._diagnostic).grid(row=0, column=0, sticky="ew", ipady=4)
        ttk.Button(card, text="Abrir pasta de dados", style="Secondary.TButton", command=self._open_data_dir).grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(card, text="O ZIP reúne logs, tarefas, respostas, informações do sistema e configuração sanitizada.", style="Muted.TLabel", wraplength=760).grid(row=2, column=0, sticky="w", pady=(12, 0))

    def _populate_settings_form(self) -> None:
        values = self.settings.to_json_dict()
        for name, variable in self.setting_vars.items():
            value = values.get(name)
            variable.set(bool(value) if name in BOOL_SETTINGS else ("" if value is None else str(value)))

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
            self.config_state.set("Configuração salva")
            messagebox.showinfo("LRAutomatic", f"Configurações salvas com segurança.\n\n{path}\n\nReinicie o serviço/agente caso tenha alterado parâmetros de execução.")
        except Exception as exc:
            self.config_state.set("Configuração inválida")
            messagebox.showerror("Configuração inválida", f"{type(exc).__name__}: {exc}")

    def _validate_settings_paths(self) -> None:
        try:
            candidate = settings_from_dict(self._settings_form_data())
            errors = candidate.validate(check_paths=True)
            if errors:
                self.config_state.set("Revisão necessária")
                messagebox.showwarning("Validação", "Foram encontrados problemas:\n\n- " + "\n- ".join(errors))
            else:
                self.config_state.set("Tudo validado")
                messagebox.showinfo("Validação", "Todas as variáveis e caminhos configurados são válidos.")
        except Exception as exc:
            self.config_state.set("Configuração inválida")
            messagebox.showerror("Validação", f"{type(exc).__name__}: {exc}")

    def _reload_settings(self) -> None:
        try:
            self.settings = load_settings(self.config_path)
            self.store = JobStore(self.settings)
            self._populate_settings_form()
            self._refresh_path_summary()
            self.status.set("Configurações recarregadas do arquivo.")
            self.config_state.set("Configuração recarregada")
        except Exception as exc:
            messagebox.showerror("LRAutomatic", f"Não foi possível recarregar: {exc}")

    def _restore_defaults(self) -> None:
        if not messagebox.askyesno("Restaurar padrões", "Preencher o formulário com os valores padrão? Nada será salvo até você clicar em Salvar configurações."):
            return
        self.settings = Settings(api_key=generate_api_key())
        self._populate_settings_form()
        self.status.set("Valores padrão carregados no formulário; ainda não foram salvos.")
        self.config_state.set("Alterações não salvas")

    def _generate_api_key(self) -> None:
        self.setting_vars["api_key"].set(generate_api_key())
        self.status.set("Nova chave forte gerada. Clique em Salvar configurações.")
        self.config_state.set("Alterações não salvas")

    def _browse_setting_path(self, name: str) -> None:
        if name in {"catalog_template", "lightroom_executable"}:
            filetypes = (("Catálogo Lightroom", "*.lrcat"), ("Todos os arquivos", "*.*")) if name == "catalog_template" else (("Executável", "*.exe"), ("Todos os arquivos", "*.*"))
            selected = filedialog.askopenfilename(title=SETTING_LABELS[name], filetypes=filetypes)
        else:
            selected = filedialog.askdirectory(title=SETTING_LABELS[name])
        if selected:
            self.setting_vars[name].set(selected)
            self.config_state.set("Alterações não salvas")

    def _refresh_path_summary(self) -> None:
        if not hasattr(self, "paths_frame"):
            return
        for widget in self.paths_frame.winfo_children():
            widget.destroy()
        rows = (("Catálogo-modelo", self.settings.catalog_template or "Não configurado"), ("Destino", self.settings.catalog_output_root or "Não configurado"), ("Lightroom", self.settings.lightroom_executable or "Não configurado"))
        for index, (label, value) in enumerate(rows):
            ttk.Label(self.paths_frame, text=label, style="Section.TLabel").grid(row=index * 2, column=0, sticky="w", pady=(0 if index == 0 else 10, 2))
            ttk.Label(self.paths_frame, text=str(value), style="Muted.TLabel", wraplength=390).grid(row=index * 2 + 1, column=0, sticky="w")

    def _run(self, label: str, action, done) -> None:
        self.status.set(label)
        def worker() -> None:
            try:
                result = action()
            except Exception as exc:
                self.after(0, lambda: (self.status.set("Falha na operação"), messagebox.showerror("LRAutomatic", f"{type(exc).__name__}: {exc}")))
                return
            self.after(0, lambda: done(result))
        threading.Thread(target=worker, daemon=True).start()

    def _update_source_count(self) -> None:
        count = len(self.sources)
        self.source_count.set("Nenhuma pasta adicionada" if count == 0 else f"{count} pasta{'s' if count != 1 else ''} adicionada{'s' if count != 1 else ''}")

    def _add_source(self) -> None:
        path = filedialog.askdirectory(title="Adicionar pasta de fotos")
        if path and Path(path) not in self.sources:
            self.sources.append(Path(path))
            self.source_list.insert("end", path)
            self._update_source_count()

    def _remove_source(self) -> None:
        selected = self.source_list.curselection()
        if selected:
            index = selected[0]
            self.source_list.delete(index)
            self.sources.pop(index)
            self._update_source_count()

    def _clear_sources(self) -> None:
        self.sources.clear()
        self.source_list.delete(0, "end")
        self._update_source_count()

    def _queue_import(self) -> None:
        if not self.sources:
            messagebox.showwarning("LRAutomatic", "Adicione ao menos uma pasta de fotos.")
            return
        request = ImportJobRequest(sources=[ImportSource(path=str(path), collection=path.name) for path in self.sources], collection_set=self.collection_set.get().strip() or None, recursive=self.recursive.get(), build_smart_previews=self.smart_previews.get(), develop_preset_name=self.preset_name.get().strip() or None)
        job = self.store.create(request)
        self.status.set(f"Tarefa enviada: {job.job_id}")
        self._refresh_jobs()
        messagebox.showinfo("LRAutomatic", f"Tarefa enviada ao Lightroom.\n\n{job.job_id}")

    def _create_catalog(self) -> None:
        name = self.catalog_name.get().strip()
        if not name:
            messagebox.showwarning("LRAutomatic", "Informe o nome do catálogo.")
            return
        self._run("Criando catálogo e abrindo o Lightroom...", lambda: create_catalog(self.settings, name, open_lightroom=True), lambda result: (self.status.set(f"Catálogo criado: {result.catalog_path}"), messagebox.showinfo("LRAutomatic", f"Catálogo criado com segurança:\n\n{result.catalog_path}")))

    def _refresh_jobs(self) -> None:
        if not hasattr(self, "jobs_tree"):
            return
        for item in self.jobs_tree.get_children():
            self.jobs_tree.delete(item)
        for index, job in enumerate(self.store.list()[:100]):
            preset = f"{job.preset_name_applied} ({job.preset_applied_count})" if job.preset_name_applied else job.preset_status
            smart = f"{job.smart_previews_created} novas / {job.smart_previews_existed} existentes" if job.smart_previews_created or job.smart_previews_existed else job.smart_previews_status
            tag = "even" if index % 2 == 0 else "odd"
            self.jobs_tree.insert("", "end", values=(job.status, job.total_imported, preset, smart), tags=(tag,))
        self.jobs_tree.tag_configure("even", background="#FFFFFF")
        self.jobs_tree.tag_configure("odd", background="#F8FAFC")
        self.status.set("Fila atualizada.")

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

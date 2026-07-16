from __future__ import annotations

import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .catalogs import create_catalog
from .config import load_settings
from .diagnostics import create_diagnostic_zip


class DesktopApp(tk.Tk):
    def __init__(self, config_path: str = 'config.json') -> None:
        super().__init__()
        self.config_path = config_path
        self.title('LRAutomatic')
        self.geometry('720x430')
        self.minsize(640, 380)
        self.columnconfigure(0, weight=1)

        self.catalog_name = tk.StringVar()
        self.status = tk.StringVar(value='Pronto')
        self._build()

    def _build(self) -> None:
        header = ttk.Frame(self, padding=18)
        header.grid(sticky='nsew')
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text='LRAutomatic', font=('Segoe UI', 22, 'bold')).grid(row=0, column=0, sticky='w')
        ttk.Label(header, text='Catálogos, importação automática e diagnóstico do Lightroom Classic').grid(row=1, column=0, sticky='w', pady=(2, 18))

        card = ttk.LabelFrame(header, text='Criar catálogo pelo app', padding=14)
        card.grid(row=2, column=0, sticky='ew')
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text='Nome do catálogo').grid(row=0, column=0, sticky='w')
        ttk.Entry(card, textvariable=self.catalog_name).grid(row=1, column=0, sticky='ew', pady=(4, 10))
        ttk.Button(card, text='Criar e abrir no Lightroom', command=self._create_catalog).grid(row=2, column=0, sticky='w')

        tools = ttk.LabelFrame(header, text='Suporte e diagnóstico', padding=14)
        tools.grid(row=3, column=0, sticky='ew', pady=(16, 0))
        tools.columnconfigure(0, weight=1)
        ttk.Label(tools, text='Gera um ZIP sem sua chave da API, com logs, tarefas, configuração sanitizada e informações do sistema.').grid(row=0, column=0, sticky='w')
        ttk.Button(tools, text='Gerar ZIP de diagnóstico', command=self._diagnostic).grid(row=1, column=0, sticky='w', pady=(10, 0))

        ttk.Separator(header).grid(row=4, column=0, sticky='ew', pady=18)
        ttk.Label(header, textvariable=self.status).grid(row=5, column=0, sticky='w')

    def _run(self, label: str, action, done) -> None:
        self.status.set(label)
        def worker() -> None:
            try:
                result = action()
            except Exception as exc:
                self.after(0, lambda: (self.status.set('Falha'), messagebox.showerror('LRAutomatic', f'{type(exc).__name__}: {exc}')))
                return
            self.after(0, lambda: done(result))
        threading.Thread(target=worker, daemon=True).start()

    def _create_catalog(self) -> None:
        name = self.catalog_name.get().strip()
        if not name:
            messagebox.showwarning('LRAutomatic', 'Informe o nome do catálogo.')
            return
        self._run(
            'Criando catálogo e abrindo o Lightroom...',
            lambda: create_catalog(load_settings(self.config_path), name, open_lightroom=True),
            lambda result: (self.status.set(f'Catálogo criado: {result.catalog_path}'), messagebox.showinfo('LRAutomatic', f'Catálogo criado e enviado ao Lightroom:\n\n{result.catalog_path}')),
        )

    def _diagnostic(self) -> None:
        output = filedialog.askdirectory(title='Onde salvar o ZIP de diagnóstico?')
        if not output:
            return
        self._run(
            'Coletando diagnóstico...',
            lambda: create_diagnostic_zip(load_settings(self.config_path), self.config_path, Path(output)),
            lambda path: (self.status.set(f'Diagnóstico criado: {path}'), messagebox.showinfo('LRAutomatic', f'ZIP criado com sucesso:\n\n{path}')),
        )


def main() -> None:
    DesktopApp().mainloop()


if __name__ == '__main__':
    main()

from __future__ import annotations

import os
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .catalogs import create_catalog
from .config import BOOL_SETTINGS, INT_SETTINGS, OPTIONAL_SETTINGS, PATH_SETTINGS, SETTING_GROUPS, SETTING_LABELS, Settings, generate_api_key, load_settings, save_settings, settings_from_dict
from .diagnostics import create_diagnostic_zip
from .models import ImportJob, ImportJobRequest, ImportSource
from .store import JobStore

STATUS_PT = {'queued':'Na fila','running':'Em andamento','completed':'Concluída','partial':'Concluída parcialmente','failed':'Falhou','cancelled':'Cancelada'}
TERMINAL = {'completed','partial','failed','cancelled'}

class ScrollableFrame(ttk.Frame):
    def __init__(self,parent:tk.Misc)->None:
        super().__init__(parent)
        self.canvas=tk.Canvas(self,highlightthickness=0,borderwidth=0)
        self.scrollbar=ttk.Scrollbar(self,orient='vertical',command=self.canvas.yview)
        self.content=ttk.Frame(self.canvas,padding=(2,2,12,16))
        self.window_id=self.canvas.create_window((0,0),window=self.content,anchor='nw')
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0,column=0,sticky='nsew'); self.scrollbar.grid(row=0,column=1,sticky='ns')
        self.columnconfigure(0,weight=1); self.rowconfigure(0,weight=1)
        self.content.bind('<Configure>',lambda _e:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(self.window_id,width=e.width))
        self.canvas.bind('<Enter>',lambda _e:self.canvas.bind_all('<MouseWheel>',self._wheel))
        self.canvas.bind('<Leave>',lambda _e:self.canvas.unbind_all('<MouseWheel>'))
    def _wheel(self,event)->None:self.canvas.yview_scroll(int(-event.delta/120),'units')

class DesktopApp(tk.Tk):
    BG='#F4F6F8'; SURFACE='#FFFFFF'; TEXT='#17202A'; MUTED='#657180'; BORDER='#DCE2E8'; ACCENT='#246BFD'; SUCCESS='#16855B'; WARNING='#B7791F'; DANGER='#B42318'
    def __init__(self,config_path:str='config.json')->None:
        super().__init__(); self.config_path=config_path; self.settings=load_settings(config_path); self.store=JobStore(self.settings)
        self.title('LRAutomatic V4.3'); self.geometry('1240x850'); self.minsize(1020,700); self.configure(bg=self.BG); self.option_add('*Font',('Segoe UI',10))
        self.catalog_name=tk.StringVar(); self.collection_set=tk.StringVar(); self.preset_name=tk.StringVar(); self.smart_previews=tk.BooleanVar(value=True); self.recursive=tk.BooleanVar(value=False)
        self.status=tk.StringVar(value='Pronto para receber uma nova tarefa.'); self.source_count=tk.StringVar(value='Nenhuma pasta adicionada'); self.config_state=tk.StringVar(value='Configuração carregada')
        self.monitor_state=tk.StringVar(value='Monitoramento automático ativo'); self.history_filter=tk.StringVar(value='Todas'); self.history_search=tk.StringVar(); self.sources:list[Path]=[]
        self.setting_vars={}; self.setting_entries={}; self.jobs_by_id={}; self.selected_job_id=None
        self._styles(); self._build(); self._populate_settings_form(); self._refresh_jobs(True); self.after(2500,self._auto_refresh)

    def _styles(self)->None:
        s=ttk.Style(self)
        try:s.theme_use('clam')
        except tk.TclError:pass
        for name,bg in [('App.TFrame',self.BG),('Surface.TFrame',self.SURFACE),('Card.TFrame',self.SURFACE)]:s.configure(name,background=bg)
        s.configure('Card.TFrame',relief='solid',borderwidth=1); s.configure('Header.TLabel',background=self.BG,foreground=self.TEXT,font=('Segoe UI',25,'bold')); s.configure('Subtitle.TLabel',background=self.BG,foreground=self.MUTED)
        s.configure('Title.TLabel',background=self.SURFACE,foreground=self.TEXT,font=('Segoe UI',16,'bold')); s.configure('Section.TLabel',background=self.SURFACE,foreground=self.TEXT,font=('Segoe UI',12,'bold')); s.configure('Body.TLabel',background=self.SURFACE,foreground=self.TEXT); s.configure('Muted.TLabel',background=self.SURFACE,foreground=self.MUTED); s.configure('Status.TLabel',background=self.SURFACE,foreground=self.MUTED,font=('Segoe UI',9))
        s.configure('Badge.TLabel',background='#E8F0FF',foreground=self.ACCENT,font=('Segoe UI',9,'bold'),padding=(9,4)); s.configure('SuccessBadge.TLabel',background='#E5F5EE',foreground=self.SUCCESS,font=('Segoe UI',9,'bold'),padding=(9,4)); s.configure('Metric.TLabel',background=self.SURFACE,foreground=self.TEXT,font=('Segoe UI',20,'bold')); s.configure('MetricCaption.TLabel',background=self.SURFACE,foreground=self.MUTED,font=('Segoe UI',9))
        s.configure('TNotebook',background=self.BG,borderwidth=0); s.configure('TNotebook.Tab',padding=(18,11),background='#E9EDF2',foreground=self.MUTED,font=('Segoe UI',10,'bold'),borderwidth=0); s.map('TNotebook.Tab',background=[('selected',self.SURFACE)],foreground=[('selected',self.ACCENT)])
        s.configure('Primary.TButton',background=self.ACCENT,foreground='#FFF',padding=(15,10),font=('Segoe UI',10,'bold'),borderwidth=0); s.configure('Secondary.TButton',background='#EDF1F5',foreground=self.TEXT,padding=(12,8),borderwidth=0); s.configure('Danger.TButton',background='#FFF0F0',foreground=self.DANGER,padding=(12,8),borderwidth=0)
        s.configure('Treeview',background='#FFF',fieldbackground='#FFF',foreground=self.TEXT,rowheight=34,borderwidth=0); s.configure('Treeview.Heading',background='#EEF2F6',foreground=self.TEXT,font=('Segoe UI',9,'bold'),padding=8,relief='flat'); s.map('Treeview',background=[('selected','#DCE8FF')],foreground=[('selected',self.TEXT)])
        s.configure('TLabelframe',background=self.SURFACE,bordercolor=self.BORDER,relief='solid',borderwidth=1); s.configure('TLabelframe.Label',background=self.SURFACE,foreground=self.TEXT,font=('Segoe UI',11,'bold'))

    def _build(self)->None:
        self.columnconfigure(0,weight=1); self.rowconfigure(1,weight=1)
        h=ttk.Frame(self,style='App.TFrame',padding=(28,22,28,14)); h.grid(row=0,column=0,sticky='ew'); h.columnconfigure(0,weight=1); ttk.Label(h,text='LRAutomatic',style='Header.TLabel').grid(row=0,column=0,sticky='w'); ttk.Label(h,text='Central de catálogos, importação e automação do Lightroom Classic',style='Subtitle.TLabel').grid(row=1,column=0,sticky='w')
        b=ttk.Frame(h,style='App.TFrame'); b.grid(row=0,column=1,rowspan=2); ttk.Label(b,text='V4.3',style='Badge.TLabel').pack(side='left',padx=(0,8)); ttk.Label(b,text='MONITOR AO VIVO',style='SuccessBadge.TLabel').pack(side='left')
        shell=ttk.Frame(self,style='App.TFrame',padding=(28,0,28,0)); shell.grid(row=1,column=0,sticky='nsew'); shell.columnconfigure(0,weight=1); shell.rowconfigure(0,weight=1); nb=ttk.Notebook(shell); nb.grid(row=0,column=0,sticky='nsew')
        for title,builder in [('Importação',self._build_pipeline),('Novo catálogo',self._build_catalog),('Monitor e histórico',self._build_jobs),('Configurações',self._build_settings),('Diagnóstico',self._build_support)]:
            f=ttk.Frame(nb,style='Surface.TFrame',padding=12 if title=='Configurações' else 22); nb.add(f,text=title); builder(f)
        foot=ttk.Frame(self,style='Surface.TFrame',padding=(28,10,28,12)); foot.grid(row=2,column=0,sticky='ew'); foot.columnconfigure(1,weight=1); ttk.Label(foot,text='●',foreground=self.SUCCESS,background=self.SURFACE).grid(row=0,column=0,padx=(0,7)); ttk.Label(foot,textvariable=self.status,style='Status.TLabel').grid(row=0,column=1,sticky='w'); ttk.Label(foot,textvariable=self.config_state,style='Status.TLabel').grid(row=0,column=2)

    def _heading(self,p,t,st):ttk.Label(p,text=t,style='Title.TLabel').grid(row=0,column=0,columnspan=3,sticky='w');ttk.Label(p,text=st,style='Muted.TLabel',wraplength=900).grid(row=1,column=0,columnspan=3,sticky='w',pady=(4,18))
    def _build_pipeline(self,p):
        p.columnconfigure(0,weight=3);p.columnconfigure(1,weight=2);p.rowconfigure(3,weight=1);self._heading(p,'Enviar fotos ao Lightroom','Monte uma tarefa e acompanhe tudo no monitor ao vivo.')
        a=ttk.LabelFrame(p,text='Pastas de origem',padding=16);a.grid(row=2,column=0,rowspan=2,sticky='nsew',padx=(0,12));a.columnconfigure(0,weight=1);a.rowconfigure(2,weight=1);bar=ttk.Frame(a,style='Surface.TFrame');bar.grid(row=0,column=0,sticky='ew');ttk.Button(bar,text='Adicionar pasta',style='Primary.TButton',command=self._add_source).pack(side='left');ttk.Button(bar,text='Remover',style='Secondary.TButton',command=self._remove_source).pack(side='left',padx=8);ttk.Button(bar,text='Limpar',style='Danger.TButton',command=self._clear_sources).pack(side='left');ttk.Label(a,textvariable=self.source_count,style='Muted.TLabel').grid(row=1,column=0,sticky='w',pady=(12,8));self.source_list=tk.Listbox(a,borderwidth=0,highlightthickness=1,highlightbackground=self.BORDER,selectbackground='#DCE8FF');self.source_list.grid(row=2,column=0,sticky='nsew')
        o=ttk.LabelFrame(p,text='Processamento',padding=16);o.grid(row=2,column=1,sticky='nsew');o.columnconfigure(0,weight=1);ttk.Label(o,text='Conjunto de coleções').grid(row=0,column=0,sticky='w');ttk.Entry(o,textvariable=self.collection_set).grid(row=1,column=0,sticky='ew',pady=(5,12));ttk.Label(o,text='Preset de revelação').grid(row=2,column=0,sticky='w');ttk.Entry(o,textvariable=self.preset_name).grid(row=3,column=0,sticky='ew',pady=(5,12));ttk.Checkbutton(o,text='Criar Smart Previews oficiais',variable=self.smart_previews).grid(row=4,column=0,sticky='w');ttk.Checkbutton(o,text='Incluir subpastas',variable=self.recursive).grid(row=5,column=0,sticky='w',pady=5);ttk.Button(p,text='ENVIAR TAREFA AO LIGHTROOM',style='Primary.TButton',command=self._queue_import).grid(row=3,column=1,sticky='sew',pady=(12,0),ipady=4)
    def _build_catalog(self,p):
        p.columnconfigure(0,weight=3);p.columnconfigure(1,weight=2);self._heading(p,'Criar catálogo gerenciado','Crie e abra um catálogo com segurança.');c=ttk.LabelFrame(p,text='Novo trabalho',padding=18);c.grid(row=2,column=0,sticky='new',padx=(0,12));c.columnconfigure(0,weight=1);ttk.Label(c,text='Nome do catálogo').grid(row=0,column=0,sticky='w');ttk.Entry(c,textvariable=self.catalog_name).grid(row=1,column=0,sticky='ew',pady=(7,14));ttk.Button(c,text='CRIAR E ABRIR NO LIGHTROOM',style='Primary.TButton',command=self._create_catalog).grid(row=2,column=0,sticky='ew');self.paths_frame=ttk.LabelFrame(p,text='Configuração atual',padding=16);self.paths_frame.grid(row=2,column=1,sticky='new');self._refresh_path_summary()

    def _build_jobs(self,p):
        p.columnconfigure(0,weight=3);p.columnconfigure(1,weight=2);p.rowconfigure(4,weight=1);self._heading(p,'Monitor e histórico','Acompanhe tarefas em tempo real e consulte exatamente o que foi realizado.')
        cards=ttk.Frame(p,style='Surface.TFrame');cards.grid(row=2,column=0,columnspan=2,sticky='ew',pady=(0,12));self.metric_vars={k:tk.StringVar(value='0') for k in ('active','done','photos','failed')}
        for i,(k,l) in enumerate((('active','Em andamento'),('done','Concluídas'),('photos','Fotos importadas'),('failed','Com falha'))):
            c=ttk.Frame(cards,style='Card.TFrame',padding=(16,10));c.grid(row=0,column=i,sticky='ew',padx=4);cards.columnconfigure(i,weight=1);ttk.Label(c,textvariable=self.metric_vars[k],style='Metric.TLabel').pack(anchor='w');ttk.Label(c,text=l,style='MetricCaption.TLabel').pack(anchor='w')
        f=ttk.Frame(p,style='Surface.TFrame');f.grid(row=3,column=0,columnspan=2,sticky='ew',pady=(0,10));f.columnconfigure(1,weight=1);combo=ttk.Combobox(f,textvariable=self.history_filter,state='readonly',values=('Todas','Ativas','Concluídas','Com problema'),width=18);combo.grid(row=0,column=0);combo.bind('<<ComboboxSelected>>',lambda _e:self._refresh_jobs(True));search=ttk.Entry(f,textvariable=self.history_search);search.grid(row=0,column=1,sticky='ew',padx=8);search.bind('<KeyRelease>',lambda _e:self._refresh_jobs(True));ttk.Label(f,textvariable=self.monitor_state,style='SuccessBadge.TLabel').grid(row=0,column=2,padx=(0,8));ttk.Button(f,text='Atualizar agora',style='Secondary.TButton',command=self._refresh_jobs).grid(row=0,column=3)
        left=ttk.Frame(p,style='Surface.TFrame');left.grid(row=4,column=0,sticky='nsew',padx=(0,12));left.columnconfigure(0,weight=1);left.rowconfigure(0,weight=1);self.jobs_tree=ttk.Treeview(left,columns=('created','status','folders','imported','summary'),show='headings')
        for k,t,w in (('created','Criada em',125),('status','Status',130),('folders','Pastas',60),('imported','Fotos',70),('summary','Resultado',280)):self.jobs_tree.heading(k,text=t);self.jobs_tree.column(k,width=w,anchor='w',stretch=k=='summary')
        self.jobs_tree.grid(row=0,column=0,sticky='nsew');sb=ttk.Scrollbar(left,orient='vertical',command=self.jobs_tree.yview);sb.grid(row=0,column=1,sticky='ns');self.jobs_tree.configure(yscrollcommand=sb.set);self.jobs_tree.bind('<<TreeviewSelect>>',self._select_job)
        right=ttk.LabelFrame(p,text='Detalhes da tarefa',padding=14);right.grid(row=4,column=1,sticky='nsew');right.columnconfigure(0,weight=1);right.rowconfigure(3,weight=1);self.detail_title=tk.StringVar(value='Selecione uma tarefa');self.detail_badge=tk.StringVar(value='—');self.detail_summary=tk.StringVar(value='Os detalhes aparecerão aqui.');ttk.Label(right,textvariable=self.detail_title,style='Section.TLabel',wraplength=390).grid(row=0,column=0,sticky='w');ttk.Label(right,textvariable=self.detail_badge,style='Badge.TLabel').grid(row=1,column=0,sticky='w',pady=(6,10));ttk.Label(right,textvariable=self.detail_summary,style='Muted.TLabel',wraplength=390,justify='left').grid(row=2,column=0,sticky='ew',pady=(0,10));self.detail_text=tk.Text(right,wrap='word',borderwidth=0,highlightthickness=1,highlightbackground=self.BORDER,bg='#FFF',fg=self.TEXT,padx=12,pady=10,state='disabled');self.detail_text.grid(row=3,column=0,sticky='nsew');act=ttk.Frame(right,style='Surface.TFrame');act.grid(row=4,column=0,sticky='ew',pady=(10,0));ttk.Button(act,text='Abrir pasta da tarefa',style='Secondary.TButton',command=self._open_selected_job).pack(side='left');self.cancel_button=ttk.Button(act,text='Cancelar tarefa',style='Danger.TButton',command=self._cancel_selected_job);self.cancel_button.pack(side='right')

    def _build_settings(self,p):
        p.columnconfigure(0,weight=1);p.rowconfigure(1,weight=1);h=ttk.Frame(p,style='Surface.TFrame',padding=10);h.grid(row=0,column=0,sticky='ew');h.columnconfigure(0,weight=1);ttk.Label(h,text='Configurações do sistema',style='Title.TLabel').grid(row=0,column=0,sticky='w');ttk.Label(h,textvariable=self.config_state,style='SuccessBadge.TLabel').grid(row=0,column=1);scroll=ScrollableFrame(p);scroll.grid(row=1,column=0,sticky='nsew');self.settings_content=scroll.content;self.settings_content.columnconfigure(0,weight=1);row=0
        for group,names in SETTING_GROUPS:
            box=ttk.LabelFrame(self.settings_content,text=group,padding=16);box.grid(row=row,column=0,sticky='ew',pady=6);box.columnconfigure(1,weight=1);row+=1
            for r,name in enumerate(names):
                ttk.Label(box,text=SETTING_LABELS[name]).grid(row=r,column=0,sticky='w',padx=(0,14),pady=7)
                if name in BOOL_SETTINGS:var=tk.BooleanVar();ttk.Checkbutton(box,variable=var).grid(row=r,column=1,sticky='w')
                elif name=='catalog_date_source':var=tk.StringVar();ttk.Combobox(box,textvariable=var,state='readonly',values=('earliest_file','today')).grid(row=r,column=1,sticky='ew')
                else:
                    var=tk.StringVar();entry=ttk.Entry(box,textvariable=var,show='•' if name=='api_key' else '');entry.grid(row=r,column=1,sticky='ew');self.setting_entries[name]=entry
                    if name in PATH_SETTINGS:ttk.Button(box,text='Procurar',style='Secondary.TButton',command=lambda n=name:self._browse_setting_path(n)).grid(row=r,column=2,padx=(8,0))
                self.setting_vars[name]=var
        a=ttk.Frame(p,style='Surface.TFrame',padding=10);a.grid(row=2,column=0,sticky='ew');ttk.Button(a,text='SALVAR CONFIGURAÇÕES',style='Primary.TButton',command=self._save_settings_from_form).pack(side='left');ttk.Button(a,text='Validar',style='Secondary.TButton',command=self._validate_settings_paths).pack(side='left',padx=8);ttk.Button(a,text='Recarregar',style='Secondary.TButton',command=self._reload_settings).pack(side='left');ttk.Button(a,text='Restaurar padrões',style='Danger.TButton',command=self._restore_defaults).pack(side='left',padx=8);ttk.Button(a,text='Gerar chave forte',style='Secondary.TButton',command=self._generate_api_key).pack(side='right')
    def _build_support(self,p):
        p.columnconfigure(0,weight=1);self._heading(p,'Diagnóstico e suporte','Crie um pacote sanitizado para analisar problemas.');c=ttk.LabelFrame(p,text='Ferramentas',padding=18);c.grid(row=2,column=0,sticky='ew');c.columnconfigure(0,weight=1);ttk.Button(c,text='GERAR ZIP DE DIAGNÓSTICO',style='Primary.TButton',command=self._diagnostic).grid(row=0,column=0,sticky='ew');ttk.Button(c,text='Abrir pasta de dados',style='Secondary.TButton',command=self._open_data_dir).grid(row=1,column=0,sticky='ew',pady=(10,0))

    @staticmethod
    def _dt(v):
        if not v:return '—'
        try:return datetime.fromisoformat(v.replace('Z','+00:00')).astimezone().strftime('%d/%m/%Y %H:%M:%S')
        except:return v
    def _result(self,j):
        if str(j.status)=='failed':return j.error or 'Falha no processamento'
        x=[f'{j.total_imported} importada(s)']
        if j.total_skipped:x.append(f'{j.total_skipped} ignorada(s)')
        if j.total_failed:x.append(f'{j.total_failed} falha(s)')
        return ' • '.join(x)
    def _matches(self,j):
        st=str(j.status);f=self.history_filter.get()
        if f=='Ativas' and st not in {'queued','running'}:return False
        if f=='Concluídas' and st not in {'completed','partial'}:return False
        if f=='Com problema' and st not in {'failed','partial','cancelled'}:return False
        q=self.history_search.get().strip().lower();hay=' '.join([j.job_id,j.request.collection_set or '',j.error or '',j.active_catalog_path or '',*(x.path for x in j.progress)]).lower();return not q or q in hay
    def _timeline(self,j):
        if j.events:return [(e.at,e.title,e.detail or '') for e in j.events]
        out=[(j.created_at,'Tarefa criada',f'{len(j.progress)} pasta(s) adicionada(s) à fila.')]
        if str(j.status)!='queued':out.append((j.started_at or j.updated_at,'Processamento iniciado',j.active_catalog_path or 'Lightroom assumiu a tarefa.'))
        for s in j.progress:
            if s.discovered or s.imported or s.skipped or s.failed or str(s.status)!='queued':out.append((j.updated_at,s.collection or Path(s.path).name,f'{s.discovered} encontrada(s), {s.imported} importada(s), {s.skipped} ignorada(s), {s.failed} falha(s).'))
        if j.preset_status!='not_requested':out.append((j.updated_at,'Preset',f'{j.preset_status}; aplicado em {j.preset_applied_count} foto(s).'))
        if j.smart_previews_status!='not_requested':out.append((j.updated_at,'Smart Previews',f'{j.smart_previews_created} criado(s), {j.smart_previews_existed} existente(s), {j.smart_previews_failed} falha(s).'))
        if str(j.status) in TERMINAL:out.append((j.finished_at or j.updated_at,STATUS_PT.get(str(j.status),str(j.status)),self._result(j)))
        return out
    def _render(self,j):
        self.selected_job_id=j.job_id;self.detail_title.set(j.request.collection_set or f'Tarefa {j.job_id[-8:]}');self.detail_badge.set(STATUS_PT.get(str(j.status),str(j.status)));self.detail_summary.set(f'Criada: {self._dt(j.created_at)}\nAtualizada: {self._dt(j.updated_at)}\nCatálogo: {j.active_catalog_path or "ainda não informado"}')
        lines=['RESULTADO',f'  Fotos encontradas: {j.total_discovered}',f'  Fotos importadas: {j.total_imported}',f'  Fotos ignoradas: {j.total_skipped}',f'  Falhas: {j.total_failed}','','ETAPAS REALIZADAS']
        for at,title,detail in self._timeline(j):lines.extend([f'\n{self._dt(at)}  •  {title}',f'  {detail}' if detail else ''])
        lines.extend(['','PASTAS PROCESSADAS'])
        for s in j.progress:lines.extend([f'\n• {s.collection or Path(s.path).name}',f'  {s.path}',f'  Status: {STATUS_PT.get(str(s.status),str(s.status))} | Encontradas {s.discovered} | Importadas {s.imported} | Ignoradas {s.skipped} | Falhas {s.failed}'])
        if j.error:lines.extend(['','ERRO',f'  {j.error}'])
        self.detail_text.configure(state='normal');self.detail_text.delete('1.0','end');self.detail_text.insert('1.0','\n'.join(lines));self.detail_text.configure(state='disabled');self.cancel_button.configure(state='disabled' if str(j.status) in TERMINAL else 'normal')
    def _select_job(self,_e=None):
        s=self.jobs_tree.selection()
        if s and s[0] in self.jobs_by_id:self._render(self.jobs_by_id[s[0]])
    def _refresh_jobs(self,silent=False):
        if not hasattr(self,'jobs_tree'):return
        selected=self.selected_job_id;jobs=self.store.list();self.jobs_by_id={j.job_id:j for j in jobs}
        for i in self.jobs_tree.get_children():self.jobs_tree.delete(i)
        visible=[j for j in jobs if self._matches(j)]
        for n,j in enumerate(visible):self.jobs_tree.insert('', 'end',iid=j.job_id,values=(self._dt(j.created_at),STATUS_PT.get(str(j.status),str(j.status)),len(j.progress),j.total_imported,self._result(j)),tags=(str(j.status),'even' if n%2==0 else 'odd'))
        self.jobs_tree.tag_configure('even',background='#FFF');self.jobs_tree.tag_configure('odd',background='#F8FAFC');self.jobs_tree.tag_configure('failed',foreground=self.DANGER);self.jobs_tree.tag_configure('running',foreground=self.ACCENT);self.jobs_tree.tag_configure('completed',foreground=self.SUCCESS);self.jobs_tree.tag_configure('partial',foreground=self.WARNING)
        self.metric_vars['active'].set(sum(str(j.status) in {'queued','running'} for j in jobs));self.metric_vars['done'].set(sum(str(j.status) in {'completed','partial'} for j in jobs));self.metric_vars['photos'].set(sum(j.total_imported for j in jobs));self.metric_vars['failed'].set(sum(str(j.status)=='failed' for j in jobs))
        target=selected if selected in self.jobs_by_id and self.jobs_tree.exists(selected) else (visible[0].job_id if visible else None)
        if target:self.jobs_tree.selection_set(target);self.jobs_tree.see(target);self._render(self.jobs_by_id[target])
        if not silent:self.status.set(f'Histórico atualizado: {len(jobs)} tarefa(s).')
    def _auto_refresh(self):
        try:self._refresh_jobs(True);self.monitor_state.set('Atualizado automaticamente')
        except Exception as e:self.monitor_state.set(f'Falha ao atualizar: {e}')
        self.after(2500,self._auto_refresh)
    def _open_selected_job(self):
        if self.selected_job_id:os.startfile(self.settings.jobs_dir)
    def _cancel_selected_job(self):
        if self.selected_job_id and messagebox.askyesno('Cancelar tarefa','Deseja cancelar esta tarefa?'):self.store.cancel(self.selected_job_id);self._refresh_jobs()

    def _populate_settings_form(self):
        v=self.settings.to_json_dict()
        for n,x in self.setting_vars.items():x.set(bool(v.get(n)) if n in BOOL_SETTINGS else ('' if v.get(n) is None else str(v.get(n))))
    def _settings_form_data(self):
        r={}
        for n,v in self.setting_vars.items():
            x=v.get()
            if n in BOOL_SETTINGS:r[n]=bool(x)
            elif n in INT_SETTINGS:r[n]=int(str(x).strip())
            elif n in OPTIONAL_SETTINGS:r[n]=str(x).strip() or None
            else:r[n]=str(x).strip()
        return r
    def _save_settings_from_form(self):
        try:self.settings=settings_from_dict(self._settings_form_data());path=save_settings(self.settings,self.config_path);self.store=JobStore(self.settings);self._refresh_path_summary();self.config_state.set('Configuração salva');self._refresh_jobs(True);messagebox.showinfo('LRAutomatic',f'Configurações salvas.\n\n{path}')
        except Exception as e:messagebox.showerror('Configuração inválida',str(e))
    def _validate_settings_paths(self):
        try:
            e=settings_from_dict(self._settings_form_data()).validate(check_paths=True)
            if e:messagebox.showwarning('Validação','- '+'\n- '.join(e))
            else:messagebox.showinfo('Validação','Tudo válido.')
        except Exception as e:messagebox.showerror('Validação',str(e))
    def _reload_settings(self):self.settings=load_settings(self.config_path);self.store=JobStore(self.settings);self._populate_settings_form();self._refresh_path_summary();self._refresh_jobs(True)
    def _restore_defaults(self):
        if messagebox.askyesno('Restaurar padrões','Preencher valores padrão?'):self.settings=Settings(api_key=generate_api_key());self._populate_settings_form();self.config_state.set('Alterações não salvas')
    def _generate_api_key(self):self.setting_vars['api_key'].set(generate_api_key());self.config_state.set('Alterações não salvas')
    def _browse_setting_path(self,n):
        x=filedialog.askopenfilename(title=SETTING_LABELS[n]) if n in {'catalog_template','lightroom_executable'} else filedialog.askdirectory(title=SETTING_LABELS[n])
        if x:self.setting_vars[n].set(x)
    def _refresh_path_summary(self):
        if not hasattr(self,'paths_frame'):return
        for w in self.paths_frame.winfo_children():w.destroy()
        for i,(l,v) in enumerate((('Catálogo-modelo',self.settings.catalog_template or 'Não configurado'),('Destino',self.settings.catalog_output_root or 'Não configurado'),('Lightroom',self.settings.lightroom_executable or 'Não configurado'))):ttk.Label(self.paths_frame,text=l,style='Section.TLabel').grid(row=i*2,column=0,sticky='w',pady=(8,2));ttk.Label(self.paths_frame,text=str(v),style='Muted.TLabel',wraplength=390).grid(row=i*2+1,column=0,sticky='w')
    def _run(self,label,action,done):
        self.status.set(label)
        def worker():
            try:r=action()
            except Exception as e:self.after(0,lambda:messagebox.showerror('LRAutomatic',str(e)));return
            self.after(0,lambda:done(r))
        threading.Thread(target=worker,daemon=True).start()
    def _update_source_count(self):
        n=len(self.sources);self.source_count.set('Nenhuma pasta adicionada' if not n else f'{n} pasta(s) adicionada(s)')
    def _add_source(self):
        x=filedialog.askdirectory(title='Adicionar pasta de fotos')
        if x and Path(x) not in self.sources:self.sources.append(Path(x));self.source_list.insert('end',x);self._update_source_count()
    def _remove_source(self):
        s=self.source_list.curselection()
        if s:self.source_list.delete(s[0]);self.sources.pop(s[0]);self._update_source_count()
    def _clear_sources(self):self.sources.clear();self.source_list.delete(0,'end');self._update_source_count()
    def _queue_import(self):
        if not self.sources:messagebox.showwarning('LRAutomatic','Adicione ao menos uma pasta.');return
        q=ImportJobRequest(sources=[ImportSource(path=str(p),collection=p.name) for p in self.sources],collection_set=self.collection_set.get().strip() or None,recursive=self.recursive.get(),build_smart_previews=self.smart_previews.get(),develop_preset_name=self.preset_name.get().strip() or None);j=self.store.create(q);self.selected_job_id=j.job_id;self._refresh_jobs(True);messagebox.showinfo('LRAutomatic',f'Tarefa enviada.\n\n{j.job_id}')
    def _create_catalog(self):
        n=self.catalog_name.get().strip()
        if not n:messagebox.showwarning('LRAutomatic','Informe o nome.');return
        self._run('Criando catálogo...',lambda:create_catalog(self.settings,n,open_lightroom=True),lambda r:messagebox.showinfo('LRAutomatic',f'Catálogo criado:\n\n{r.catalog_path}'))
    def _diagnostic(self):
        o=filedialog.askdirectory(title='Onde salvar o ZIP?')
        if o:self._run('Coletando diagnóstico...',lambda:create_diagnostic_zip(self.settings,self.config_path,Path(o)),lambda p:messagebox.showinfo('LRAutomatic',f'ZIP criado:\n\n{p}'))
    def _open_data_dir(self):os.startfile(self.settings.data_dir)

def main():DesktopApp().mainloop()
if __name__=='__main__':main()

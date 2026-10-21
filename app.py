# -*- coding: utf-8 -*-
"""AudioText Studio — GUI для Windows 11, без консольного окна."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import time
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from backend import AUDIO, VIDEO, worker_main

APP = 'AudioText Studio'
VERSION = '1.1.2'
BG = '#0b1220'
PANEL = '#172337'
PANEL_DARK = '#101b2b'
BORDER = '#2a3a53'
TEXT = '#edf3fc'
MUTED = '#9eb0c7'
ACCENT = '#4ad2ba'
ACCENT_DARK = '#0d3a35'
BLUE = '#729cf8'
ERROR = '#ff9696'
DEVICE_LABELS = {'cpu': 'CPU — процессор', 'cuda': 'GPU — NVIDIA CUDA'}


def cfg_path() -> Path:
    base = os.environ.get('LOCALAPPDATA') or str(Path.home() / '.local' / 'share')
    return Path(base) / 'AudioTextStudio' / 'settings.json'


def read_config() -> dict:
    try:
        return json.loads(cfg_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_config(data: dict):
    try:
        target = cfg_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError:
        pass


def open_folder(path: str):
    folder = Path(path)
    if not folder.is_dir():
        raise FileNotFoundError('Папка результатов ещё не существует.')
    if sys.platform == 'win32':
        os.startfile(str(folder))  # type: ignore[attr-defined]
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', str(folder)])
    else:
        subprocess.Popen(['xdg-open', str(folder)])


class Studio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.config_data = read_config()
        self.title(APP + ' — аудио, видео, текст')
        self.geometry(f'940x{min(790, max(650, self.winfo_screenheight() - 95))}')
        self.minsize(810, 650)
        self.configure(bg=BG)
        self._setup_icon()
        self.protocol('WM_DELETE_WINDOW', self.on_close)

        self.file_var = tk.StringVar()
        self.output_var = tk.StringVar(value=self.config_data.get('output', ''))
        self.mode_var = tk.StringVar(value=self.config_data.get('mode', 'both'))
        self.model_var = tk.StringVar(value=self.config_data.get('model', 'small'))
        self.device_var = tk.StringVar(value=DEVICE_LABELS.get(self.config_data.get('device', 'cpu'), DEVICE_LABELS['cpu']))
        self.lang_var = tk.StringVar(value=self.config_data.get('language', 'ru'))
        self.format_var = tk.StringVar(value=self.config_data.get('audio_format', 'mp3'))
        self.srt_var = tk.BooleanVar(value=self.config_data.get('srt', True))
        self.vad_var = tk.BooleanVar(value=self.config_data.get('vad', True))
        self.status_var = tk.StringVar(value='Выбери файл, чтобы начать')
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_caption = tk.StringVar(value='0%')
        self.active = False
        self.process = None
        self.events = queue.Queue()
        self.job_file = None
        self.event_file = None
        self.last_output = ''
        self.last_error = None
        self.log_viewer_window = None
        self.log_viewer_text = None
        self.log_copy_status = tk.StringVar(value='')

        self._style()
        self._draw()
        self.after(90, self._poll_events)

    def _setup_icon(self):
        ico = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'assets' / 'app.ico'
        if ico.exists():
            try:
                self.iconbitmap(str(ico))
            except tk.TclError:
                pass

    def _style(self):
        s = ttk.Style(self)
        s.theme_use('clam')
        s.configure('Vertical.TScrollbar', background=PANEL, troughcolor=PANEL_DARK,
                    arrowcolor=ACCENT, bordercolor=BORDER, darkcolor=PANEL,
                    lightcolor=PANEL, gripcount=0, arrowsize=13)
        s.map('Vertical.TScrollbar', background=[('active', '#293d55')])
        s.configure('Studio.Horizontal.TProgressbar', troughcolor=PANEL_DARK, background=ACCENT,
                    bordercolor=PANEL_DARK, lightcolor=ACCENT, darkcolor=ACCENT, thickness=15)
        s.configure('Studio.TCombobox', foreground=TEXT, fieldbackground=PANEL_DARK,
                    background=PANEL_DARK, bordercolor=BORDER, arrowcolor=ACCENT,
                    lightcolor=PANEL_DARK, darkcolor=PANEL_DARK, padding=7)
        s.map('Studio.TCombobox', fieldbackground=[('readonly', PANEL_DARK)],
              selectforeground=[('readonly', TEXT)], selectbackground=[('readonly', PANEL_DARK)])
        self.option_add('*TCombobox*Listbox.background', PANEL_DARK)
        self.option_add('*TCombobox*Listbox.foreground', TEXT)
        self.option_add('*TCombobox*Listbox.selectBackground', ACCENT_DARK)

    def label(self, parent, text, size=11, color=TEXT, bold=False, **opts):
        return tk.Label(parent, text=text, bg=opts.pop('bg', PANEL), fg=color,
                        font=('Segoe UI', size, 'bold' if bold else 'normal'),
                        anchor=opts.pop('anchor', 'w'), **opts)

    def btn(self, parent, text, command, primary=False, **opts):
        return tk.Button(parent, text=text, command=command, cursor='hand2', relief='flat',
                         bd=0, padx=17, pady=11,
                         bg=ACCENT if primary else PANEL_DARK,
                         fg=BG if primary else TEXT,
                         activebackground='#8ce9d8' if primary else '#293b54',
                         activeforeground=BG if primary else TEXT,
                         font=('Segoe UI', 11, 'bold'), highlightthickness=0, **opts)

    def field(self, parent, var):
        return tk.Entry(parent, textvariable=var, relief='flat', bd=0,
                        bg=PANEL_DARK, fg=TEXT, insertbackground=TEXT,
                        readonlybackground=PANEL_DARK, font=('Segoe UI', 11),
                        highlightthickness=1, highlightcolor=ACCENT, highlightbackground=BORDER)

    def card(self, parent, **kwargs):
        return tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=BORDER, **kwargs)

    def _draw(self):
        # Закреплённая панель запуска/прогресса остаётся видимой даже на небольшом экране.
        fixed = tk.Frame(self, bg=BG, padx=30, pady=10)
        fixed.pack(side='bottom', fill='x')
        canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0)
        canvas.pack(side='left', fill='both', expand=True)
        scrollbar = ttk.Scrollbar(self, orient='vertical', command=canvas.yview)
        scrollbar.pack(side='right', fill='y')
        canvas.configure(yscrollcommand=scrollbar.set)
        base = tk.Frame(canvas, bg=BG, padx=30, pady=24)
        win = canvas.create_window((0, 0), window=base, anchor='nw')
        base.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(win, width=e.width))
        def wheel(e):
            # Logs have their own scrollbar; scrolling them must not scroll the whole form.
            if isinstance(e.widget, tk.Text):
                return
            canvas.yview_scroll(-int(e.delta / 120) if e.delta else 0, 'units')
        canvas.bind_all('<MouseWheel>', wheel)

        header = tk.Frame(base, bg=BG)
        header.pack(fill='x', pady=(0, 20))
        logo = tk.Frame(header, bg=ACCENT_DARK, width=54, height=54)
        logo.pack(side='left')
        logo.pack_propagate(False)
        self.label(logo, '▶', size=23, color=ACCENT, bold=True, bg=ACCENT_DARK,
                   anchor='center').pack(fill='both', expand=True)
        headings = tk.Frame(header, bg=BG)
        headings.pack(side='left', padx=15)
        self.label(headings, 'AudioText Studio', 23, TEXT, True, bg=BG).pack(anchor='w')
        self.label(headings, 'Видео → звук → текст и субтитры', 11, MUTED, bg=BG).pack(anchor='w')
        self.label(header, 'ЛОКАЛЬНО  •  БЕЗ ОБЛАКА', 10, ACCENT, True,
                   bg=BG).pack(side='right', anchor='n', pady=13)

        source = self.card(base)
        source.pack(fill='x', pady=(0, 15))
        inside = tk.Frame(source, bg=PANEL, padx=20, pady=17)
        inside.pack(fill='x')
        self.label(inside, '01   ИСХОДНЫЙ ФАЙЛ', 10, BLUE, True).pack(anchor='w', pady=(0, 9))
        line = tk.Frame(inside, bg=PANEL)
        line.pack(fill='x')
        self.source_entry = self.field(line, self.file_var)
        self.source_entry.pack(side='left', fill='x', expand=True, ipady=11)
        self.btn(line, 'Выбрать файл', self.choose_file).pack(side='left', padx=(10, 0))
        self.label(inside, 'MP4, MKV, MOV, AVI, WebM, MP3, WAV, M4A, FLAC и другие',
                   10, MUTED).pack(anchor='w', pady=(9, 0))

        mid = tk.Frame(base, bg=BG)
        mid.pack(fill='x', pady=(0, 15))
        mid.grid_columnconfigure(0, weight=3, uniform='mid')
        mid.grid_columnconfigure(1, weight=2, uniform='mid')
        modecard = self.card(mid)
        modecard.grid(row=0, column=0, sticky='nsew', padx=(0, 8))
        options = self.card(mid)
        options.grid(row=0, column=1, sticky='nsew', padx=(8, 0))

        m = tk.Frame(modecard, bg=PANEL, padx=20, pady=14)
        m.pack(fill='both', expand=True)
        self.label(m, '02   ЧТО СДЕЛАТЬ', 10, BLUE, True).pack(anchor='w', pady=(0, 9))
        modes = [('both', 'Аудио + текст + субтитры'),
                 ('transcribe', 'Только текст / субтитры'),
                 ('extract', 'Только аудиодорожку')]
        self.mode_buttons = []
        for value, name in modes:
            button = tk.Radiobutton(m, text=name, variable=self.mode_var, value=value,
                                    bg=PANEL, fg=TEXT, activebackground=PANEL,
                                    activeforeground=TEXT, selectcolor=PANEL_DARK,
                                    font=('Segoe UI', 11), padx=2, pady=5,
                                    highlightthickness=0, borderwidth=0,
                                    command=self._sync_mode, anchor='w')
            button.pack(anchor='w')
            self.mode_buttons.append(button)

        o = tk.Frame(options, bg=PANEL, padx=20, pady=14)
        o.pack(fill='both', expand=True)
        self.label(o, '03   ПАРАМЕТРЫ', 10, BLUE, True).pack(anchor='w', pady=(0, 9))
        r1 = tk.Frame(o, bg=PANEL)
        r1.pack(fill='x', pady=(0, 9))
        self.label(r1, 'Модель', 10, MUTED).pack(anchor='w')
        self.model_box = ttk.Combobox(r1, state='readonly', style='Studio.TCombobox',
                                       textvariable=self.model_var, values=['tiny', 'base', 'small', 'medium', 'large-v3'])
        self.model_box.pack(fill='x', pady=(3, 0))
        r2 = tk.Frame(o, bg=PANEL)
        r2.pack(fill='x', pady=(0, 8))
        self.label(r2, 'Язык', 10, MUTED).pack(anchor='w')
        self.lang_box = ttk.Combobox(r2, state='readonly', style='Studio.TCombobox',
                                      textvariable=self.lang_var, values=['ru', 'auto', 'en'])
        self.lang_box.pack(fill='x', pady=(3, 0))
        gpu_row = tk.Frame(o, bg=PANEL)
        gpu_row.pack(fill='x', pady=(0, 7))
        self.label(gpu_row, 'Где выполнять распознавание', 10, MUTED).pack(anchor='w')
        self.device_box = ttk.Combobox(gpu_row, state='readonly', style='Studio.TCombobox',
                                        textvariable=self.device_var, values=list(DEVICE_LABELS.values()))
        self.device_box.pack(fill='x', pady=(3, 0))
        self.label(o, 'CPU: без CUDA; GPU: CUDA 12 + cuDNN 9', 9, MUTED).pack(anchor='w')

        more = self.card(base)
        more.pack(fill='x', pady=(0, 15))
        minside = tk.Frame(more, bg=PANEL, padx=20, pady=14)
        minside.pack(fill='x')
        heading = tk.Frame(minside, bg=PANEL)
        heading.pack(fill='x')
        self.label(heading, '04   СОХРАНЕНИЕ', 10, BLUE, True).pack(side='left')
        self.label(heading, 'Пустое поле = рядом с исходным файлом', 9, MUTED).pack(side='right')
        destrow = tk.Frame(minside, bg=PANEL)
        destrow.pack(fill='x', pady=(11, 10))
        self.output_entry = self.field(destrow, self.output_var)
        self.output_entry.pack(side='left', fill='x', expand=True, ipady=9)
        self.btn(destrow, 'Обзор…', self.choose_output).pack(side='left', padx=(10, 0))

        bottom = tk.Frame(minside, bg=PANEL)
        bottom.pack(fill='x')
        self.chk_srt = tk.Checkbutton(bottom, text='Субтитры SRT', variable=self.srt_var,
                                       bg=PANEL, fg=TEXT, selectcolor=PANEL_DARK,
                                       activebackground=PANEL, activeforeground=TEXT,
                                       font=('Segoe UI', 10), highlightthickness=0, borderwidth=0)
        self.chk_srt.pack(side='left')
        self.chk_vad = tk.Checkbutton(bottom, text='Убирать тишину', variable=self.vad_var,
                                       bg=PANEL, fg=TEXT, selectcolor=PANEL_DARK,
                                       activebackground=PANEL, activeforeground=TEXT,
                                       font=('Segoe UI', 10), highlightthickness=0, borderwidth=0)
        self.chk_vad.pack(side='left', padx=(18, 0))
        self.label(bottom, 'Аудио:', 10, MUTED).pack(side='left', padx=(24, 8))
        self.format_box = ttk.Combobox(bottom, state='readonly', width=6,
                                        style='Studio.TCombobox', textvariable=self.format_var,
                                        values=['mp3', 'm4a', 'wav'])
        self.format_box.pack(side='left')

        statuscard = self.card(fixed)
        statuscard.pack(fill='x')
        sr = tk.Frame(statuscard, bg=PANEL, padx=20, pady=13)
        sr.pack(fill='x')
        status_row = tk.Frame(sr, bg=PANEL)
        status_row.pack(fill='x')
        self.label(status_row, 'ПРОГРЕСС', 10, BLUE, True).pack(side='left')
        self.label(status_row, '', 11).pack(side='left')
        self.label(status_row, '0%', 10, ACCENT, True, textvariable=self.progress_caption).pack(side='right')
        ttk.Progressbar(sr, variable=self.progress_var, maximum=100,
                        style='Studio.Horizontal.TProgressbar').pack(fill='x', pady=(9, 9))
        self.label(sr, '', 11, MUTED, textvariable=self.status_var).pack(anchor='w')
        actions = tk.Frame(sr, bg=PANEL)
        actions.pack(fill='x', pady=(11, 0))
        self.start_button = self.btn(actions, '▶  Начать обработку', self.start, primary=True)
        self.start_button.pack(side='left')
        self.stop_button = self.btn(actions, '■  Остановить', self.stop)
        self.stop_button.configure(state='disabled')
        self.stop_button.pack(side='left', padx=(10, 0))
        self.open_button = self.btn(actions, 'Открыть результаты ↗', self.open_results)
        self.open_button.pack(side='right')

        logs = tk.Frame(base, bg=BG)
        logs.pack(fill='both', expand=True)
        log_header = tk.Frame(logs, bg=BG)
        log_header.pack(fill='x', pady=(0, 7))
        self.label(log_header, 'ЖУРНАЛ ОБРАБОТКИ', 10, MUTED, True,
                   bg=BG).pack(side='left')
        self.label(log_header, '', 9, ACCENT, bg=BG,
                   textvariable=self.log_copy_status).pack(side='left', padx=(12, 0))

        def log_button(text, command):
            return tk.Button(log_header, text=text, command=command, cursor='hand2',
                             relief='flat', bd=0, padx=13, pady=6, bg=PANEL,
                             fg=TEXT, activebackground='#293b54', activeforeground=TEXT,
                             font=('Segoe UI', 9, 'bold'))

        log_button('Копировать журнал', self.copy_log).pack(side='right', padx=(8, 0))
        log_button('Открыть отдельно ↗', self.open_log_window).pack(side='right')

        log_body = tk.Frame(logs, bg=PANEL_DARK)
        log_body.pack(fill='x')
        log_scroll = ttk.Scrollbar(log_body, orient='vertical')
        log_scroll.pack(side='right', fill='y')
        self.log_area = self._make_log_text(log_body, 14, log_scroll)
        self.log_area.pack(side='left', fill='both', expand=True)
        log_scroll.configure(command=self.log_area.yview)
        self._append_log('Программа готова. Все файлы обрабатываются на вашем компьютере.')
        self._sync_mode()

    def _make_log_text(self, parent, height, scrollbar):
        widget = tk.Text(parent, height=height, relief='flat', wrap='word',
                         bg=PANEL_DARK, fg=MUTED, insertbackground=TEXT, bd=0,
                         selectbackground='#315582', selectforeground=TEXT,
                         font=('Consolas', 11), padx=14, pady=12,
                         yscrollcommand=scrollbar.set, state='disabled')
        widget.bind('<Control-c>', self._copy_selected_log)
        widget.bind('<Control-C>', self._copy_selected_log)
        widget.bind('<Control-a>', self._select_all_log)
        widget.bind('<Control-A>', self._select_all_log)
        return widget

    def _select_all_log(self, event):
        widget = event.widget
        widget.tag_add('sel', '1.0', 'end-1c')
        widget.mark_set('insert', '1.0')
        widget.see('1.0')
        return 'break'

    def _copy_selected_log(self, event):
        self._copy_log_from_widget(event.widget, selected=True)
        return 'break'

    def _copy_log_from_widget(self, widget, selected=False):
        try:
            content = widget.get('sel.first', 'sel.last') if selected else widget.get('1.0', 'end-1c')
        except tk.TclError:
            # Ctrl+C with no selection copies the whole journal.
            content = widget.get('1.0', 'end-1c')
        if not content:
            return
        self.clipboard_clear()
        self.clipboard_append(content)
        self.update_idletasks()
        self.log_copy_status.set('Скопировано')
        self.after(2500, lambda: self.log_copy_status.set(''))

    def copy_log(self):
        self._copy_log_from_widget(self.log_area)

    def open_log_window(self):
        if self.log_viewer_window is not None and self.log_viewer_window.winfo_exists():
            self.log_viewer_window.lift()
            self.log_viewer_window.focus_force()
            return
        window = tk.Toplevel(self)
        window.title('AudioText Studio — журнал обработки')
        window.geometry('1000x600')
        window.minsize(550, 320)
        window.configure(bg=BG)
        self.log_viewer_window = window
        header = tk.Frame(window, bg=BG, padx=16, pady=12)
        header.pack(fill='x')
        self.label(header, 'Журнал обработки · Ctrl+A / Ctrl+C', 12, TEXT, True,
                   bg=BG).pack(side='left')
        tk.Button(header, text='Копировать весь журнал',
                  command=lambda: self._copy_log_from_widget(self.log_viewer_text),
                  relief='flat', bd=0, padx=14, pady=8, bg=PANEL,
                  fg=TEXT, activebackground='#293b54', activeforeground=TEXT,
                  font=('Segoe UI', 10, 'bold')).pack(side='right')
        body = tk.Frame(window, bg=PANEL_DARK, padx=2, pady=2)
        body.pack(fill='both', expand=True, padx=16, pady=(0, 16))
        scroll = ttk.Scrollbar(body, orient='vertical')
        scroll.pack(side='right', fill='y')
        self.log_viewer_text = self._make_log_text(body, 20, scroll)
        self.log_viewer_text.pack(side='left', fill='both', expand=True)
        scroll.configure(command=self.log_viewer_text.yview)
        self._put_log_text(self.log_viewer_text, self.log_area.get('1.0', 'end-1c'))

        def close_viewer():
            self.log_viewer_text = None
            self.log_viewer_window = None
            window.destroy()

        window.protocol('WM_DELETE_WINDOW', close_viewer)

    def _put_log_text(self, widget, content):
        bottom = widget.yview()[1] >= 0.98
        widget.configure(state='normal')
        widget.insert('end', content)
        widget.configure(state='disabled')
        if bottom:
            widget.see('end')

    def _append_log(self, message):
        line = '• ' + message + '\n'
        self._put_log_text(self.log_area, line)
        if self.log_viewer_text is not None:
            self._put_log_text(self.log_viewer_text, line)

    def choose_file(self):
        path = filedialog.askopenfilename(parent=self, title='Выберите аудио или видео',
                                          filetypes=[('Аудио и видео', '*.mp4 *.mkv *.mov *.avi *.webm *.wmv *.m4v *.flv *.mpeg *.mpg *.3gp *.ts *.mts *.m2ts *.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus *.wma *.aiff *.aif *.amr'),
                                                     ('Все файлы', '*.*')])
        if path:
            self.file_var.set(path)
            if Path(path).suffix.lower() in AUDIO:
                self.mode_var.set('transcribe')
            self._sync_mode()

    def choose_output(self):
        target = filedialog.askdirectory(parent=self, title='Папка результатов')
        if target:
            self.output_var.set(target)

    def _sync_mode(self):
        audio_file = Path(self.file_var.get().strip(' "')).suffix.lower() in AUDIO
        mode = self.mode_var.get()
        if audio_file and mode != 'transcribe':
            self.mode_var.set('transcribe')
            mode = 'transcribe'
        self.mode_buttons[0].configure(state='disabled' if audio_file else 'normal')
        self.mode_buttons[2].configure(state='disabled' if audio_file else 'normal')
        self.format_box.configure(state='readonly' if mode in ('both', 'extract') else 'disabled')
        transcription = mode != 'extract'
        self.model_box.configure(state='readonly' if transcription else 'disabled')
        self.device_box.configure(state='readonly' if transcription else 'disabled')
        self.lang_box.configure(state='readonly' if transcription else 'disabled')
        self.chk_srt.configure(state='normal' if transcription else 'disabled')
        self.chk_vad.configure(state='normal' if transcription else 'disabled')

    def start(self):
        if self.active:
            return
        file = Path(self.file_var.get().strip().strip('"')).expanduser()
        if not file.is_file():
            messagebox.showwarning(APP, 'Сначала выбери существующий видео- или аудиофайл.', parent=self)
            return
        if file.suffix.lower() not in VIDEO | AUDIO:
            messagebox.showerror(APP, 'Этот формат пока не поддерживается.', parent=self)
            return
        self._sync_mode()
        settings = {'file': str(file.resolve()), 'output': self.output_var.get().strip(),
                    'mode': self.mode_var.get(), 'model': self.model_var.get(),
                    'device': next((key for key, label in DEVICE_LABELS.items() if label == self.device_var.get()), 'cpu'),
                    'language': self.lang_var.get(), 'audio_format': self.format_var.get(),
                    'srt': self.srt_var.get(), 'vad': self.vad_var.get()}
        save_config({k: v for k, v in settings.items() if k not in ('file',)})
        self.last_output = settings['output'] or str(file.parent / 'Результаты_распознавания')
        self.last_error = None
        self.progress_var.set(0)
        self.progress_caption.set('0%')
        self.status_var.set('Запуск обработки…')
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', prefix='ats_',
                                              encoding='utf-8', delete=False) as handle:
                json.dump(settings, handle, ensure_ascii=False)
                self.job_file = handle.name
            if getattr(sys, 'frozen', False):
                command = [sys.executable, '--worker', self.job_file]
            else:
                command = [sys.executable, str(Path(__file__).resolve()), '--worker', self.job_file]
            # --windowed в PyInstaller не предоставляет sys.stdout.
            # Применяем файловый IPC, а не stdout: события гарантированно
            # доставляются даже в установленной EXE-версии приложения.
            with tempfile.NamedTemporaryFile(mode='w', suffix='.events', prefix='ats_',
                                              encoding='utf-8', delete=False) as handle:
                self.event_file = handle.name
            env = os.environ.copy()
            env['ATS_EVENT_FILE'] = self.event_file
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            self.process = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, env=env,
                                            creationflags=flags)
        except OSError as exc:
            self._cleanup_job_file()
            messagebox.showerror(APP, f'Не удалось запустить обработку: {exc}', parent=self)
            return
        self.active = True
        self.start_button.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self._append_log('Обработка начата: ' + file.name)
        threading.Thread(target=self._read_worker, args=(self.process, self.event_file), daemon=True).start()

    def _read_worker(self, process, event_path):
        """Читает JSON-события без зависания GUI и корректно забирает хвост лога."""
        try:
            with open(event_path, 'r', encoding='utf-8') as stream:
                while True:
                    line = stream.readline()
                    if line:
                        try:
                            self.events.put(json.loads(line))
                        except json.JSONDecodeError:
                            self.events.put({'type': 'debug', 'message': line[:400]})
                        continue
                    if process.poll() is not None:
                        # Рабочий процесс завершился: после дочитывания
                        # всех событий в очередь можно помещать exit.
                        break
                    time.sleep(0.1)
            code = process.wait()
            self.events.put({'type': 'exit', 'code': code})
        except Exception as exc:
            self.events.put({'type': 'error', 'message': str(exc)})
            self.events.put({'type': 'exit', 'code': 1})

    def _poll_events(self):
        try:
            while True:
                data = self.events.get_nowait()
                kind = data.get('type')
                if kind == 'progress':
                    value = min(100, max(0, float(data.get('value', 0))))
                    self.progress_var.set(value)
                    self.progress_caption.set(f'{int(value)}%')
                elif kind == 'status':
                    self.status_var.set(data.get('message', ''))
                elif kind == 'log':
                    self._append_log(data.get('message', ''))
                elif kind == 'done':
                    self.last_output = data.get('output', self.last_output)
                    self.status_var.set('Готово! Результаты сохранены.')
                    self._append_log('✓ ' + data.get('message', 'Готово'))
                elif kind == 'error':
                    self.last_error = data.get('message', 'Ошибка без описания')
                    self.status_var.set('Ошибка обработки')
                    self._append_log('ОШИБКА: ' + self.last_error)
                elif kind == 'exit':
                    self.active = False
                    self.process = None
                    self.start_button.configure(state='normal')
                    self.stop_button.configure(state='disabled')
                    self._cleanup_job_file()
                    if data.get('code') and not self.last_error:
                        self.status_var.set('Обработка прервана или завершилась с ошибкой')
                        self._append_log('Процесс завершился с кодом ' + str(data['code']))
                    elif self.last_error:
                        messagebox.showerror('Ошибка обработки', self.last_error, parent=self)
                elif kind == 'debug':
                    # Подробности технических ошибок не теряются.
                    self._append_log(data.get('message', ''))
        except queue.Empty:
            pass
        self.after(90, self._poll_events)

    def _cleanup_job_file(self):
        for filename in (self.job_file, self.event_file):
            if filename:
                try:
                    Path(filename).unlink(missing_ok=True)
                except OSError:
                    pass
        self.job_file = None
        self.event_file = None

    def stop(self):
        if not self.active or not self.process:
            return
        if not messagebox.askyesno(APP, 'Остановить обработку? Уже записанный текст может остаться в папке результатов.', parent=self):
            return
        pid = self.process.pid
        try:
            if sys.platform == 'win32':
                subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                               creationflags=subprocess.CREATE_NO_WINDOW,
                               capture_output=True, timeout=10, check=False)
            else:
                self.process.terminate()
        except Exception as exc:
            self._append_log('Не удалось остановить процесс: ' + str(exc))
        self.status_var.set('Обработка остановлена пользователем')
        self._append_log('Обработка остановлена.')

    def open_results(self):
        if not self.last_output:
            source = Path(self.file_var.get().strip(' "'))
            if source.is_file():
                self.last_output = str(source.parent / 'Результаты_распознавания')
        try:
            open_folder(self.last_output)
        except (OSError, FileNotFoundError) as exc:
            messagebox.showinfo(APP, str(exc), parent=self)

    def on_close(self):
        if self.active:
            messagebox.showinfo(APP, 'Сначала останови обработку, чтобы закрыть программу.', parent=self)
            return
        self._cleanup_job_file()
        self.destroy()


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == '--worker':
        raise SystemExit(worker_main(sys.argv[2]))
    Studio().mainloop()


if __name__ == '__main__':
    main()
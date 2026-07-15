import json
import os
import subprocess
import sys
from typing import List, Optional

UI_HELPER = sys.executable.replace('python', 'python3') if sys.executable else 'python3'
HELPER_PATH = None
HERE = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
POSSIBLE = os.path.join(HERE, 'scripts', 'ui_file_dialog.py')
if os.path.exists(POSSIBLE):
    HELPER_PATH = POSSIBLE
else:
    HELPER_PATH = 'scripts/ui_file_dialog.py'


def _dialog_options(
    title: str = '',
    initialdir: Optional[str] = None,
    filetypes: Optional[List] = None,
    initialfile: Optional[str] = None,
    defaultextension: Optional[str] = None,
) -> dict:
    opts = {}
    if title:
        opts['title'] = title
    if initialdir:
        opts['initialdir'] = initialdir
    if filetypes:
        opts['filetypes'] = filetypes
    if initialfile:
        opts['initialfile'] = initialfile
    if defaultextension:
        opts['defaultextension'] = defaultextension
    return opts


def _run_direct(
    mode: str,
    title: str = '',
    initialdir: Optional[str] = None,
    filetypes: Optional[List] = None,
    initialfile: Optional[str] = None,
    defaultextension: Optional[str] = None,
):
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None

    own_root = None
    parent = None
    try:
        parent = tk._default_root
        if parent is not None and not parent.winfo_exists():
            parent = None
    except Exception:
        parent = None

    try:
        if parent is None:
            own_root = tk.Tk()
            own_root.withdraw()
            parent = own_root

        try:
            parent.update_idletasks()
            parent.lift()
            parent.focus_force()
        except Exception:
            pass

        opts = _dialog_options(
            title=title,
            initialdir=initialdir,
            filetypes=filetypes,
            initialfile=initialfile,
            defaultextension=defaultextension,
        )
        opts['parent'] = parent

        if mode == 'open':
            path = filedialog.askopenfilename(**opts)
            return {'path': path} if path else {'cancelled': True}
        if mode == 'open_multi':
            paths = filedialog.askopenfilenames(**opts)
            return {'paths': list(paths)} if paths else {'cancelled': True}
        if mode == 'save':
            path = filedialog.asksaveasfilename(**opts)
            return {'path': path} if path else {'cancelled': True}
        if mode == 'dir':
            path = filedialog.askdirectory(**opts)
            return {'path': path} if path else {'cancelled': True}
    except Exception:
        return None
    finally:
        if own_root is not None:
            try:
                own_root.destroy()
            except Exception:
                pass
    return None


def _run_helper(mode: str, title: str = '', initialdir: Optional[str] = None, filetypes: Optional[List] = None, initialfile: Optional[str] = None, defaultextension: Optional[str] = None, timeout: int = 15):
    if not HELPER_PATH:
        return None
    cmd = [sys.executable, HELPER_PATH, '--mode', mode]
    if title:
        cmd += ['--title', title]
    if initialdir:
        cmd += ['--initialdir', initialdir]
    if filetypes:
        cmd += ['--filetypes', json.dumps(filetypes)]
    if initialfile:
        cmd += ['--initialfile', initialfile]
    if defaultextension:
        cmd += ['--defaultextension', defaultextension]

    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False, text=True)
        if proc.returncode == 0 and proc.stdout:
            return json.loads(proc.stdout)
        else:
            return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def ask_open_file(title: str = 'Open file', initialdir: Optional[str] = None, filetypes: Optional[List] = None) -> Optional[str]:
    res = _run_direct('open', title=title, initialdir=initialdir, filetypes=filetypes)
    if not res:
        res = _run_helper('open', title=title, initialdir=initialdir, filetypes=filetypes)
    if res and 'path' in res:
        return res['path']
    return None


def ask_open_files(title: str = 'Open files', initialdir: Optional[str] = None, filetypes: Optional[List] = None) -> Optional[List[str]]:
    res = _run_direct('open_multi', title=title, initialdir=initialdir, filetypes=filetypes)
    if not res:
        res = _run_helper('open_multi', title=title, initialdir=initialdir, filetypes=filetypes)
    if res and 'paths' in res:
        return res['paths']
    return None


def ask_save_file(title: str = 'Save file', initialdir: Optional[str] = None, filetypes: Optional[List] = None, initialfile: Optional[str] = None, defaultextension: Optional[str] = None) -> Optional[str]:
    res = _run_direct('save', title=title, initialdir=initialdir, filetypes=filetypes, initialfile=initialfile, defaultextension=defaultextension)
    if not res:
        res = _run_helper('save', title=title, initialdir=initialdir, filetypes=filetypes, initialfile=initialfile, defaultextension=defaultextension)
    if res and 'path' in res:
        return res['path']
    return None


def ask_directory(title: str = 'Select folder', initialdir: Optional[str] = None) -> Optional[str]:
    res = _run_direct('dir', title=title, initialdir=initialdir)
    if not res:
        res = _run_helper('dir', title=title, initialdir=initialdir)
    if res and 'path' in res:
        return res['path']
    return None

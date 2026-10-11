"""Обход всех вкладок с поиском визуальных огрехов: снимки (вверху/середина/внизу), обрезанные надписи,
непереведённый текст (в английском режиме) и ошибки окна.

    SWEEP_LANG=en SWEEP_SIZE=1000x700 python tools/gui_sweep.py
Снимки - %TEMP%/minecraft-packs-shots/sweep_<lang>_<size>_<вкладка>_<n>.png, отчёт - sweep_<lang>_<size>.json рядом.
"""
import os, sys, re, json, tempfile, importlib.machinery, importlib.util, tkinter as tk, traceback
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winshot
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
os.makedirs(OUT, exist_ok=True)
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
sys.path.insert(0, LIB)
LANG = os.environ.get('SWEEP_LANG', 'ru')
SIZE = os.environ.get('SWEEP_SIZE', '1180x860')
TABS = os.environ.get('SWEEP_TABS', 'maps,packs,builder,skins,versions,servers,friend,launchers').split(',')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
tmp = tempfile.mkdtemp(prefix='portalis-sweep-')
ps.set_game_dir(os.path.join(tmp, '.minecraft'))
store = {'lang': LANG}
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))
orig = tk.Tk.mainloop
errors = []
CYR = re.compile('[А-Яа-яЁё]')
report = {'clipped': {}, 'untranslated': {}, 'errors': errors}
tag = '%s_%s' % (LANG, SIZE)


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


def texts(w):
    out = []
    try:
        t = w.cget('text')
        if t:
            out.append(str(t))
    except tk.TclError:
        pass
    if isinstance(w, tk.Canvas):
        for it in w.find_all():
            try:
                if w.type(it) == 'text':
                    out.append(w.itemcget(it, 'text'))
            except tk.TclError:
                pass
    return out


def visible(w, root):
    """Виден ли виджет в окне (не за краем прокрутки)."""
    try:
        if not w.winfo_ismapped():
            return False
        y, x = w.winfo_rooty() - root.winfo_rooty(), w.winfo_rootx() - root.winfo_rootx()
        return -5 < y < root.winfo_height() and -5 < x < root.winfo_width()
    except tk.TclError:
        return False


def inspect(root, where):
    for w in walk(root):
        if not visible(w, root):
            continue
        for t in texts(w):
            if LANG == 'en' and CYR.search(t):
                report['untranslated'].setdefault(t.strip()[:160], where)
        if isinstance(w, tk.Label):
            try:
                if not w.cget('image') and not int(w.cget('wraplength') or 0) and w.cget('text'):
                    rw, aw = w.winfo_reqwidth(), w.winfo_width()
                    if aw > 1 and rw - aw > 4:
                        report['clipped'].setdefault(str(w.cget('text'))[:100], '%s (%d из %d px)' % (where, aw, rw))
            except (tk.TclError, ValueError):
                pass


def patched(self, *a):
    self.geometry(SIZE + '+2200+30')
    hk = self._hooks
    steps = []
    at = lambda ms, fn: steps.append((ms, fn))
    shot = lambda name: winshot.shot(self, os.path.join(OUT, 'sweep_%s_%s.png' % (tag, name)))

    def view(tab, i, pos):
        hk['body'].yview_moveto(pos)
        self.update()
        inspect(self, '%s@%d%%' % (tab, pos * 100))
        shot('%s_%d' % (tab, i))
    for tab in TABS:
        at(2200 if tab == TABS[0] else 400, lambda tab=tab: hk['show'](tab, animated=False))
        for i, pos in enumerate((0.0, 0.3, 0.6, 1.0)):
            at(2600 if i == 0 else 500, lambda tab=tab, i=i, pos=pos: view(tab, i, pos))
    def tops():
        ov = hk['state'].get('login_ov')  # вход - отдельная заставка на всё окно
        return [w for w in self.winfo_children() if getattr(w, '_inpage', False) or isinstance(w, tk.Toplevel)
                or (ov is not None and w is ov and w.winfo_exists())]

    def dlg(name, opener):
        def open_():
            for w in tops():
                w.destroy()
            opener()
        at(400, open_)

        def look():
            ws = tops()
            if not ws:
                errors.append('окно «%s» не открылось' % name)
                return
            inspect(ws[-1], 'окно ' + name)
            shot('dlg_' + name)
        at(2500, look)
    if os.environ.get('SWEEP_DIALOGS', '1') == '1':
        dlg('map', lambda: hk['open_map'](ps.find_maps()[0]))
        dlg('pack', lambda: hk['open_pack'](ps.find_packs()[0]))
        dlg('server', lambda: hk['open_server'](ps.load_servers()[0]))
        dlg('style', lambda: hk['style_window']())
        dlg('login', lambda: hk['login_window']())
        at(400, lambda: [w.destroy() for w in tops()])
    at(400, lambda: self.destroy())

    def run(i=0):
        if i >= len(steps):
            return
        ms, fn = steps[i]

        def go():
            try:
                fn()
            except Exception:
                errors.append(traceback.format_exc()[-1500:])
            run(i + 1)
        self.after(ms, go)
    run()
    orig(self, *a)


tk.Tk.report_callback_exception = lambda self, *args: errors.append(''.join(traceback.format_exception(*args))[-1500:])
tk.Tk.mainloop = patched
ps.gui()
json.dump(report, open(os.path.join(OUT, 'sweep_%s.json' % tag), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('обрезано: %d, без перевода: %d, ошибок окна: %d' % (len(report['clipped']), len(report['untranslated']), len(errors)))
for k, v in list(report['clipped'].items())[:40]:
    print('  обрезано:', repr(k), '-', v)
for k, v in list(report['untranslated'].items())[:80]:
    print('  без перевода:', repr(k), '-', v)
for e in errors:
    print(e)

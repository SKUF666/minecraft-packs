"""«Место на диске»: подсчёт, разделы, «Убрать» у версии (в Корзину). python tools/gui_storage.py"""
import os, sys, tempfile, importlib.machinery, importlib.util, tkinter as tk, traceback
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winshot
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
sys.path.insert(0, LIB)
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
tmp = tempfile.mkdtemp(prefix='portalis-storage-')
ps.set_game_dir(os.path.join(tmp, '.minecraft'))
ps.SKINS_DIR = os.path.join(tmp, 'Скины')
store = {}
if os.environ.get('PORTALIS_LANG'):
    store['lang'] = os.environ['PORTALIS_LANG']
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))
orig = tk.Tk.mainloop
errors = []


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


def click(root, text):
    for x in walk(root):
        try:
            if str(x.cget('text')).strip().startswith(text) and x.winfo_ismapped():
                x.event_generate('<Enter>'); x.event_generate('<Button-1>')
                return True
        except tk.TclError:
            pass
    return False


def patched(self, *a):
    self.geometry('1180x860+2200+30')
    hk = self._hooks
    steps = []
    at = lambda ms, fn: steps.append((ms, fn))
    shot = lambda name: winshot.shot(self, os.path.join(OUT, 'storage_' + name + '.png'))
    def tops():
        return [w for w in self.winfo_children() if getattr(w, '_inpage', False)]

    def seed():  # тестовая папка игры: версия, мир, библиотеки
        mc = ps.MC
        for rel, n in (('versions/1.20.1/1.20.1.jar', 3 << 20), ('versions/26.1.2/26.1.2.jar', 5 << 20),
                       ('saves/Мой мир/level.dat', 1 << 20), ('libraries/a.jar', 2 << 20)):
            p = os.path.join(mc, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, 'wb').write(b'0' * n)
    at(1500, seed)
    at(300, lambda: hk['show']('versions', animated=False))
    at(800, lambda: print('кнопка на вкладке:', click(self, 'Место на диске')))
    def wait_done(n=0):
        txt = [w.cget('text') for w in walk(tops()[-1]) if isinstance(w, tk.Label)]
        if any(str(x).startswith('Считаю') for x in txt) and n < 60:
            self.after(500, lambda: wait_done(n + 1))
    at(500, wait_done)
    at(20000, lambda: (shot('page'), print('тексты:', [w.cget('text') for w in walk(tops()[-1]) if isinstance(w, tk.Label)
                                                       and str(w.cget('text')).strip()][:40])))
    at(300, lambda: [w.destroy() for w in tops()])
    at(300, lambda: self.destroy())

    def run(i=0):
        if i >= len(steps):
            return
        ms, fn = steps[i]

        def go():
            try:
                fn()
            except Exception:
                errors.append(traceback.format_exc())
            run(i + 1)
        self.after(ms, go)
    run()
    orig(self, *a)


tk.Tk.report_callback_exception = lambda self, *args: errors.append(''.join(traceback.format_exception(*args))[-1500:])
tk.Tk.mainloop = patched
ps.gui()
print('ошибок окна:', len(errors))
for e in errors:
    print(e)

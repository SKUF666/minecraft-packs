"""Прогон окна «как человек»: конструктор (добавить моды -> собрать) и скины (поиск по нику, мастер плаща).
Снимки - в %TEMP%/minecraft-packs-shots. python tools/gui_flows.py"""
import os, sys, importlib.machinery, importlib.util, tkinter as tk, traceback
from PIL import ImageGrab
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winshot
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
os.makedirs(OUT, exist_ok=True)
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
ps.save_settings = lambda d: None
orig = tk.Tk.mainloop
errors = []


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


def click(root, text, nth=0):
    hits = [w for w in walk(root) if isinstance(w, tk.Label) and str(w.cget('text')).strip().startswith(text)]
    if len(hits) > nth:
        hits[nth].event_generate('<Button-1>')
        return True
    return False


def grab(w, name):
    winshot.shot(w, os.path.join(OUT, name + '.png'))


def patched(self, *a):
    self.geometry('1080x760+2200+30')
    h = self._hooks
    steps = []

    def at(ms, fn):
        steps.append((ms, fn))

    at(1500, lambda: h['show']('builder'))
    at(6000, lambda: print('добавить 1:', click(self, 'Добавить', 0)))
    at(800, lambda: print('добавить 2:', click(self, 'Добавить', 0)))
    at(800, lambda: (grab(self, 'flow_builder_sel'), print('собрать:', click(self, 'Собрать сборку'))))
    at(12000, lambda: grab([w for w in self.winfo_children() if isinstance(w, tk.Toplevel)][-1], 'flow_builder_dialog'))
    at(300, lambda: [w.destroy() for w in self.winfo_children() if isinstance(w, tk.Toplevel)])
    at(500, lambda: h['show']('skins'))
    at(800, lambda: ([e.delete(0, 'end') or e.insert(0, 'Notch') for e in walk(self) if isinstance(e, tk.Entry)][:1],
                     print('найти:', click(self, 'Найти'))))
    at(7000, lambda: grab(self, 'flow_skins_found'))
    at(300, lambda: print('плащ:', click(self, 'Сделать плащ')))
    at(1500, lambda: (grab(self, 'flow_cape'), print('мастерская:', any(str(x.cget('text')) == 'Мастерская плащей'
                                                                       for x in walk(self) if isinstance(x, tk.Label)))))
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


def rep(self, *args):
    errors.append(''.join(traceback.format_exception(*args))[-1500:])
tk.Tk.report_callback_exception = rep
tk.Tk.mainloop = patched
ps.gui()
print('ошибок окна:', len(errors))
for e in errors:
    print(e)

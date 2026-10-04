"""Вкладка «Карты» как человек: наши карты со скриншотами, каталог из интернета, окно карты и просмотр.
Ничего не ставит. python tools/gui_webmaps.py"""
import os, sys, importlib.machinery, importlib.util, tkinter as tk, traceback
from PIL import ImageGrab
sys.stdout.reconfigure(encoding='utf-8')
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
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


def click(w, text):
    for x in walk(w):
        if isinstance(x, tk.Label) and str(x.cget('text')).strip().startswith(text):
            x.event_generate('<Button-1>')
            return True
    return False


def grab(w, name):
    w.attributes('-topmost', True)
    w.update()
    x, y = w.winfo_rootx(), w.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x + w.winfo_width(), y + w.winfo_height())).save(os.path.join(OUT, name + '.png'))


def patched(self, *a):
    self.geometry('1080x760+30+30')
    h = self._hooks
    steps = []

    def at(ms, fn):
        steps.append((ms, fn))

    def tops():
        return [w for w in self.winfo_children() if isinstance(w, tk.Toplevel)]
    at(1500, lambda: h['show']('maps'))
    at(7000, lambda: grab(self, 'wm_ours'))
    at(300, lambda: print('в интернет:', click(self, 'Из интернета')))
    at(8000, lambda: grab(self, 'wm_web'))
    at(300, lambda: h['body'].yview_moveto(1.0))
    at(7000, lambda: (grab(self, 'wm_web_more'), print('карточек:', sum(1 for x in walk(self) if isinstance(x, tk.Label) and str(x.cget('text')).startswith('Подробнее и')))))
    at(300, lambda: (h['body'].yview_moveto(0), print('подробнее:', click(self, 'Подробнее и скриншоты'))))
    at(8000, lambda: grab(tops()[-1], 'wm_detail'))
    at(300, lambda: (tops()[-1]._cv.yview_moveto(0.45), None))
    at(1500, lambda: grab(tops()[-1], 'wm_detail2'))
    at(300, lambda: [w.destroy() for w in tops()])
    at(300, lambda: h['show']('maps'))
    at(300, lambda: print('хоррор:', click(self, 'Хоррор')))
    at(7000, lambda: grab(self, 'wm_horror'))
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

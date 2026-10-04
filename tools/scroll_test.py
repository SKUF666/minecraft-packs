"""Быстрая прокрутка длинного списка (конструктор, 60 модов) со снимками экрана прямо во время прокрутки:
на них видно, успевает ли окно перерисовываться (раньше появлялись полосы). python tools/scroll_test.py"""
import os, sys, time, importlib.machinery, importlib.util, tkinter as tk, traceback
from PIL import ImageGrab, ImageStat
sys.stdout.reconfigure(encoding='utf-8')
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
os.makedirs(OUT, exist_ok=True)
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
ps.save_settings = lambda d: None
orig = tk.Tk.mainloop
errors = []
tab = sys.argv[1] if len(sys.argv) > 1 else 'builder'


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


def patched(self, *a):
    self.geometry('1080x760+30+30')
    h = self._hooks
    cv = [w for w in walk(self) if isinstance(w, tk.Canvas) and w.winfo_height() > 300]
    shots = []

    def grab(name):
        self.update_idletasks()
        x, y = self.winfo_rootx(), self.winfo_rooty()
        im = ImageGrab.grab(bbox=(x, y, x + self.winfo_width(), y + self.winfo_height()))
        im.save(os.path.join(OUT, name + '.png'))
        shots.append(name)

    def burst(n=0):
        c = [w for w in walk(self) if isinstance(w, tk.Canvas) and w.winfo_height() > 300][0]
        if n < 40:
            c.event_generate('<MouseWheel>', delta=-360, x=300, y=300)
            if n % 8 == 4:
                grab('scroll_%s_%02d' % (tab, n))
            self.after(25, lambda: burst(n + 1))
        else:
            self.after(900, lambda: (grab('scroll_%s_end' % tab), print('низ списка: %.2f' % c.yview()[1]),
                                     print('карточек:', len(state_results())), self.destroy()))

    def state_results():
        return [w for w in walk(self) if isinstance(w, tk.Label) and str(w.cget('text')) in ('Добавить', 'В сборке  ✓')]

    self.after(1200, lambda: h['show'](tab))
    self.after(9000, lambda: (self.attributes('-topmost', True), self.focus_force(), burst()))
    _ = cv
    orig(self, *a)


def rep(self, *args):
    errors.append(''.join(traceback.format_exception(*args))[-1500:])
tk.Tk.report_callback_exception = rep
tk.Tk.mainloop = patched
t0 = time.time()
ps.gui()
print('ошибок окна:', len(errors), '| %.0f с' % (time.time() - t0))
for e in errors:
    print(e)

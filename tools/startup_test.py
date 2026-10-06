"""Запуск окна: снимки каждые 40 мс, пока заставка сменяется окном. Считает долю чисто-белых пикселей
(белые прямоугольники = ещё не нарисованные части окна). python tools/startup_test.py"""
import os, sys, time, importlib.machinery, importlib.util, tkinter as tk
from PIL import ImageGrab
sys.stdout.reconfigure(encoding='utf-8')
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
os.makedirs(OUT, exist_ok=True)
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
ps.save_settings = lambda d: None
orig = tk.Tk.mainloop
res = []


def patched(self, *a):
    t0 = time.perf_counter()

    def shot(n=0):
        try:
            if self.winfo_viewable():
                self.update_idletasks()
                x, y, w, h = self.winfo_rootx(), self.winfo_rooty(), self.winfo_width(), self.winfo_height()
                im = ImageGrab.grab(bbox=(x, y, x + w, y + h)).convert('RGB').resize((w // 4, h // 4))
                white = sum(1 for p in im.getdata() if p[0] > 245 and p[1] > 245 and p[2] > 245) / float(im.width * im.height)
                res.append((time.perf_counter() - t0, white, float(self.attributes('-alpha'))))
                if white > 0.01:
                    im.save(os.path.join(OUT, 'startup_white_%02d.png' % n))
        except tk.TclError:
            pass
        if time.perf_counter() - t0 < 6.0:
            self.after(40, lambda: shot(n + 1))
        else:
            self.destroy()
    self.after(300, shot)
    orig(self, *a)


tk.Tk.mainloop = patched
ps.gui()
worst = max((w for _, w, al in res if al > 0.5), default=0)
print('снимков с окном: %d, больше всего белого при видимом окне: %.1f%%' % (len(res), worst * 100))
print(' '.join('%.2fс:%.0f%%/a%.1f' % (t, w * 100, al) for t, w, al in res)[:900])

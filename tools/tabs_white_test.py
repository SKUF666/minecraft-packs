"""Переключение вкладок: снимки сразу после переключения, доля белых пикселей (недорисованные части)."""
import os, sys, time, importlib.machinery, importlib.util, tkinter as tk
from PIL import ImageGrab
sys.stdout.reconfigure(encoding='utf-8')
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
ps.save_settings = lambda d: None
orig = tk.Tk.mainloop
res = {}


def patched(self, *a):
    self.geometry('1080x760+30+30')
    tabs = list(ps.TAB_ORDER) * 2

    def white():
        x, y, w, h = self.winfo_rootx(), self.winfo_rooty(), self.winfo_width(), self.winfo_height()
        im = ImageGrab.grab(bbox=(x, y, x + w, y + h)).convert('RGB').resize((w // 4, h // 4))
        return im, sum(1 for p in im.getdata() if min(p) > 245) / float(im.width * im.height)

    def go(i=0):
        if i >= len(tabs):
            self.destroy()
            return
        self.attributes('-topmost', True)
        self._hooks['show'](tabs[i])
        worst = [0]

        def snap(n):
            im, w = white()
            if w > worst[0]:
                worst[0] = w
                if w > 0.02:
                    im.save(os.path.join(OUT, 'tabwhite_%s.png' % tabs[i]))
            if n < 6:
                self.after(30, lambda: snap(n + 1))
            else:
                res[tabs[i]] = max(res.get(tabs[i], 0), worst[0])
                self.after(1200, lambda: go(i + 1))
        snap(0)
    self.after(4000, go)
    orig(self, *a)


tk.Tk.mainloop = patched
ps.gui()
print('белого сразу после переключения:', {k: '%.1f%%' % (v * 100) for k, v in res.items()})

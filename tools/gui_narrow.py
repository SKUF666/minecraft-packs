"""Узкое и низкое окно (1000x680): серверы с тремя кнопками, вход/регистрация целиком. python tools/gui_narrow.py"""
import os, sys, tempfile, importlib.machinery, importlib.util, tkinter as tk, traceback
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winshot
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
ps.set_game_dir(os.path.join(tempfile.mkdtemp(prefix='portalis-narrow-'), '.minecraft'))
store = {}
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))
orig = tk.Tk.mainloop
errors = []


def patched(self, *a):
    self.geometry('1000x680+2200+30')
    hk = self._hooks
    steps = []
    at = lambda ms, fn: steps.append((ms, fn))
    shot = lambda name: winshot.shot(self, os.path.join(OUT, 'narrow_' + name + '.png'))
    at(1800, lambda: hk['show']('servers', animated=False))
    at(1200, lambda: shot('servers'))
    at(200, lambda: hk['login_window']('up'))
    at(1500, lambda: shot('up'))
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

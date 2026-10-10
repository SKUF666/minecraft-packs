"""Мастерская скинов на вкладке «Скины» и палитра цветов внутри окна. python tools/gui_skinws.py"""
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
tmp = tempfile.mkdtemp(prefix='portalis-skinws-')
ps.set_game_dir(os.path.join(tmp, '.minecraft'))
ps.SKINS_DIR = os.path.join(tmp, 'Скины')
store = {}
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
    shot = lambda name: winshot.shot(self, os.path.join(OUT, 'skinws_' + name + '.png'))
    at(1800, lambda: (hk['state'].update(skinws_open=True), hk['show']('skins', animated=False)))
    at(1500, lambda: hk['body'].yview_moveto(0.1))
    at(800, lambda: shot('default'))
    at(200, lambda: print('случайный:', click(self, 'Случайный')))
    at(1200, lambda: shot('random'))
    at(200, lambda: print('кепка:', click(self, 'Кепка')))
    at(200, lambda: print('свой цвет:', click(self, 'Свой...')))
    at(900, lambda: shot('colorpick'))
    at(200, lambda: print('сохранить:', click(self, 'Сохранить скин'), os.listdir(ps.SKINS_DIR) if os.path.isdir(ps.SKINS_DIR) else []))
    at(1500, lambda: self.destroy())

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

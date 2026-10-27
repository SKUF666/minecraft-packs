"""Кнопки выбора (версия, сборка) и окошко выбора версии: снимки. python tools/gui_picker.py"""
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
ps.set_game_dir(os.path.join(tempfile.mkdtemp(prefix='portalis-pick-'), '.minecraft'))
store = {'lang': os.environ.get('PORTALIS_LANG', 'ru')}
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))
orig = tk.Tk.mainloop
errors = []


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


def patched(self, *a):
    self.geometry('1180x860+2200+30')
    hk = self._hooks
    shot = lambda n: winshot.shot(self, os.path.join(OUT, 'picker_' + n + '.png'))
    steps = []

    def pickers():
        return [w for w in walk(hk['body']) if type(w).__name__ == 'PickerButton' and w.winfo_ismapped()]

    def hover_first():
        p = pickers()[0]
        p.event_generate('<Enter>')
        self.update()
        print('кнопок выбора на вкладке:', len(pickers()))

    def click_first():
        p = pickers()[0]
        p._click()
        self.update()
    steps += [(1500, lambda: hk['show']('versions', animated=False)), (5000, hover_first), (300, lambda: shot('versions')),
              (200, click_first), (900, lambda: shot('versions_popup')),
              (300, lambda: hk['show']('builder', animated=False)), (3000, click_first), (900, lambda: shot('builder_popup')),
              (300, lambda: hk['show']('maps', animated=False)), (800, click_first), (900, lambda: shot('maps_popup')),
              (300, self.destroy)]

    def run(i=0):
        if i >= len(steps):
            return
        ms, fn = steps[i]

        def go():
            try:
                fn()
            except Exception:
                errors.append(traceback.format_exc()[-1200:])
            run(i + 1)
        self.after(ms, go)
    run()
    orig(self, *a)


tk.Tk.report_callback_exception = lambda self, *args: errors.append(''.join(traceback.format_exception(*args))[-1200:])
tk.Tk.mainloop = patched
ps.gui()
print('ошибок окна:', len(errors))
for e in errors:
    print(e)

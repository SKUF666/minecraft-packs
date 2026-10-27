"""Свечение карточки под мышью (два кадра пульсации) и выделение аватара/рамки в настройках профиля.
python tools/gui_glow.py"""
import os, sys, tempfile, importlib.machinery, importlib.util, tkinter as tk, traceback, time
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winshot
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
sys.path.insert(0, LIB)
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
ps.set_game_dir(os.path.join(tempfile.mkdtemp(prefix='portalis-glow-'), '.minecraft'))
store = {'lang': 'ru'}
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))
ME = {'id': 'u-me', 'nick': 'Гриша', 'login': 'grisha', 'avatar': 'preset:3', 'frame': None,
      'stats': {'minutes': 900, 'maps_n': 9, 'packs': 3, 'friends': 4}}
ps.Social.uid = property(lambda self: 'u-me')
ps.Social.logged_in = lambda self: True
ps.Social.ready = lambda self: True
orig = tk.Tk.mainloop
errors = []


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


def patched(self, *a):
    self.geometry('1180x860+2200+30')
    hk = self._hooks
    shot = lambda n: winshot.shot(self, os.path.join(OUT, 'glow_' + n + '.png'))
    steps = []

    def card():
        return next(w for w in walk(hk['body']) if isinstance(w, tk.Frame) and getattr(w, '_isl', None)
                    and str(w.cget('cursor')) == 'hand2' and w.winfo_ismapped())

    def hover():
        c = card()
        self.event_generate('<Motion>', warp=True, x=c.winfo_rootx() - self.winfo_rootx() + 60,
                            y=c.winfo_rooty() - self.winfo_rooty() + 60)
        c.event_generate('<Enter>')
        self.update()

    def avatars():
        pages = [w for w in self.winfo_children() if getattr(w, '_inpage', False)]
        pages[-1]._go('settings')
    steps += [(2000, hover), (300, lambda: shot('card_a')), (330, lambda: shot('card_b')), (100, hover),
              (500, lambda: hk['open_profile'](ME, True, 'overview')), (1500, avatars), (1500, lambda: shot('settings')),
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

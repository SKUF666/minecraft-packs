"""Внешний вид: шапка и картинки разделов во всю ширину (узкое и широкое окно), островки с лианами, поля,
мастерская плащей на вкладке «Скины». Снимки - %TEMP%/minecraft-packs-shots/look_*.png. python tools/gui_look.py"""
import os, sys, tempfile, importlib.machinery, importlib.util, tkinter as tk, traceback
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winshot
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
ps.set_game_dir(os.path.join(tempfile.mkdtemp(prefix='portalis-look-'), '.minecraft'))
store = {}
if os.environ.get('LOOK_STYLE'):
    store['style'] = os.environ['LOOK_STYLE']
    store['theme'] = ps.STYLES[store['style']]['theme'] or 'obsidian'
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))
orig = tk.Tk.mainloop
errors = []


def patched(self, *a):
    self.geometry('1180x800+2200+30')
    hk = self._hooks
    steps = []

    def at(ms, fn):
        steps.append((ms, fn))

    def shot(name):
        winshot.shot(self, os.path.join(OUT, 'look_' + os.environ.get('LOOK_STYLE', '') + name + '.png'))
    for tab in ('maps', 'packs', 'builder', 'friend', 'versions'):
        at(1800 if tab == 'maps' else 300, lambda tab=tab: hk['show'](tab, animated=False))
        at(2500 if tab == 'builder' else 1200, lambda tab=tab: shot(tab))
    def walk(w):
        yield w
        for c in w.winfo_children():
            yield from walk(c)

    def tops():
        return [w for w in self.winfo_children() if isinstance(w, tk.Toplevel)]
    at(300, lambda: hk['show']('builder', animated=False))

    def open_ver():
        for x in walk(self):
            if isinstance(x, tk.Label) and str(x.cget('text')).endswith('▾') and x.winfo_ismapped():
                x.event_generate('<Button-1>')
                return print('версии: открыто')
    at(1500, open_ver)
    at(1200, lambda: winshot.shot(tops()[-1], os.path.join(OUT, 'look_verpick.png')))
    at(300, lambda: [w.destroy() for w in tops()])
    at(300, lambda: hk['style_window']())
    at(2500, lambda: winshot.shot(tops()[-1], os.path.join(OUT, 'look_styles.png')))
    at(300, lambda: [w.destroy() for w in tops()])
    at(300, lambda: (hk['state'].update(cape_open=True), hk['show']('skins', animated=False)))
    at(1500, lambda: hk['body'].yview_moveto(0.12))
    at(800, lambda: shot('cape'))
    at(300, lambda: self.geometry('1900x800+2200+30'))
    at(300, lambda: hk['show']('friend', animated=False))
    at(1500, lambda: shot('wide_friend'))
    at(300, lambda: self.geometry('1000x700+2200+30'))
    at(1200, lambda: shot('narrow_friend'))
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

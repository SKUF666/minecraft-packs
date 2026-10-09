"""Экран входа на всё окно, профиль на всё окно, фильтры карт списком, кнопка «Добавить в игру» у серверов,
лаунчеры без лишнего «Указать путь». Снимки - %TEMP%/minecraft-packs-shots/login_*.png. python tools/gui_login.py"""
import os, sys, tempfile, importlib.machinery, importlib.util, tkinter as tk, traceback
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winshot
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
ps.set_game_dir(os.path.join(tempfile.mkdtemp(prefix='portalis-login-'), '.minecraft'))
store = {'favorites': {}}
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

    def shot(name, w=None):
        winshot.shot(w or self, os.path.join(OUT, 'login_' + name + '.png'))

    def walk(w):
        yield w
        for c in w.winfo_children():
            yield from walk(c)

    def tops():
        return [w for w in self.winfo_children() if isinstance(w, tk.Toplevel)]

    def click_text(t):
        for x in walk(self):
            try:
                if t in str(x.cget('text')) and x.winfo_ismapped():
                    x.event_generate('<Enter>'); x.event_generate('<Button-1>')
                    return print('нажато:', t)
            except tk.TclError:
                pass
        print('нет кнопки:', t)
    at(1800, lambda: hk['show']('maps', animated=False))
    at(1200, lambda: shot('maps'))
    at(200, lambda: click_text('Версия:'))
    at(900, lambda: shot('verfilter', tops()[-1]))
    at(200, lambda: [w.destroy() for w in tops()])
    at(200, lambda: click_text('Игроков:'))
    at(900, lambda: shot('players', tops()[-1]))
    at(200, lambda: [w.destroy() for w in tops()])
    at(200, lambda: click_text('♡'))
    at(300, lambda: print('избранное:', list(store.get('favorites', {}))))
    at(200, lambda: click_text('Избранное'))
    at(900, lambda: shot('fav'))
    at(200, lambda: hk['show']('servers', animated=False))
    at(1200, lambda: shot('servers'))
    at(200, lambda: hk['show']('launchers', animated=False))
    at(6000, lambda: shot('launchers'))
    at(200, lambda: hk['show']('friend', animated=False))
    at(1200, lambda: shot('friend'))
    at(200, lambda: hk['login_window']('in'))
    at(1500, lambda: shot('in'))
    at(200, lambda: hk['login_window']('up'))
    at(1500, lambda: shot('up'))
    at(200, lambda: hk['state']['login_ov'].destroy())
    at(300, lambda: hk['open_profile']({'nick': 'Тест', 'login': 'test', 'id': 'x1', 'about': 'Привет'}, True))
    at(2500, lambda: shot('profile', tops()[-1]))
    at(300, lambda: [w.destroy() for w in tops()])
    at(300, lambda: hk['show']('versions', animated=False))
    at(1500, lambda: shot('versions'))
    at(300, lambda: (hk['show']('builder', animated=False), hk['builder_set'](ptype='modpack')))
    at(9000, lambda: hk['body'].yview_moveto(0.18))
    at(1500, lambda: shot('modpacks'))
    at(300, lambda: hk['builder_set'](ptype='mod', src='cf'))
    at(6000, lambda: (hk['body'].yview_moveto(0.3), None))
    at(1500, lambda: shot('builder_cf'))
    at(300, lambda: (hk['state'].update(maps_src='web'), hk['show']('maps', animated=False)))
    at(25000, lambda: shot('webmaps'))
    at(300, lambda: hk['show']('skins', animated=False))
    at(20000, lambda: hk['body'].yview_moveto(0.55))
    at(4000, lambda: shot('skins'))
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

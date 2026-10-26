"""Сколько открываются окна внутри программы: карта, сборка, сервер, профиль, оформление, быстрый поиск.
Время = от вызова до готового кадра (с отрисовкой). python tools/perf_dialogs.py"""
import os, sys, time, tempfile, importlib.machinery, importlib.util, tkinter as tk, traceback
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
sys.path.insert(0, LIB)
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
ps.set_game_dir(os.path.join(tempfile.mkdtemp(prefix='portalis-dlg-'), '.minecraft'))
store = {'lang': 'ru'}
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))
ME = {'id': 'u-me', 'nick': 'Гриша', 'login': 'grisha', 'avatar': None, 'about': 'Привет', 'favorites': ['OneBlock'],
      'stats': {'minutes': 300, 'maps_n': 6, 'maps': ['OneBlock']}, 'skin': None}
ps.Social.wall = lambda self, uid, limit=50: []
orig = tk.Tk.mainloop
errors = []


def patched(self, *a):
    self.geometry(os.environ.get('DLG_GEO', '1180x860+2200+30'))
    hk = self._hooks
    maps = hk['find_maps']()
    packs = ps.find_packs()
    servers = ps.load_servers()

    def close_pages():
        for w in [w for w in self.winfo_children() if getattr(w, '_inpage', False)]:
            w.destroy()
        self.update()

    cases = [('карта', lambda: hk['open_map'](maps[0])), ('карта 2', lambda: hk['open_map'](maps[min(3, len(maps) - 1)])),
             ('сборка', lambda: hk['open_pack'](packs[0])), ('сервер', lambda: hk['open_server'](servers[0])),
             ('профиль', lambda: hk['open_profile'](ME, True, 'overview')), ('оформление', hk['style_window']),
             ('быстрый поиск', lambda: self.event_generate('<Control-k>'))]
    steps = []
    for name, fn in cases:
        def go(name=name, fn=fn):
            close_pages()
            t = time.perf_counter()
            prof = os.environ.get('DLG_PROFILE') == name
            if prof:
                import cProfile, pstats
                pr = cProfile.Profile(); pr.enable()
            fn()
            self.update()
            if prof:
                pr.disable()
                pstats.Stats(pr).sort_stats('cumulative').print_stats(40)
            print('%-14s %5.0f мс' % (name, (time.perf_counter() - t) * 1000))
        steps.append((1200, go))
    steps.append((300, self.destroy))

    def run(i=0):
        if i >= len(steps):
            return
        ms, fn = steps[i]

        def go():
            try:
                fn()
            except Exception:
                errors.append(traceback.format_exc()[-1000:])
            run(i + 1)
        self.after(ms, go)
    run()
    orig(self, *a)


tk.Tk.report_callback_exception = lambda self, *args: errors.append(''.join(traceback.format_exception(*args))[-1000:])
tk.Tk.mainloop = patched
ps.gui()
print('ошибок окна:', len(errors))
for e in errors:
    print(e)

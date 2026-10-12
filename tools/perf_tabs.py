"""Скорость: запуск окна, сборка каждой вкладки (холодная и повторная), загрузка процессора в простое.
python tools/perf_tabs.py"""
import os, sys, time, tempfile, importlib.machinery, importlib.util, tkinter as tk, traceback
sys.stdout.reconfigure(encoding='utf-8')
T0 = time.perf_counter()
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
sys.path.insert(0, LIB)
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
T_IMPORT = time.perf_counter() - T0
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
ps.set_game_dir(os.path.join(tempfile.mkdtemp(prefix='portalis-perf-'), '.minecraft'))
store = {'lang': os.environ.get('PORTALIS_LANG', 'ru')}
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))
orig = tk.Tk.mainloop
errors, res = [], {}
TABS = ['maps', 'packs', 'builder', 'skins', 'versions', 'servers', 'friend', 'launchers']


def cpu():
    t = os.times()
    return t.user + t.system


def patched(self, *a):
    res['window'] = time.perf_counter() - T0
    hk = self._hooks
    steps = []
    at = lambda ms, fn: steps.append((ms, fn))

    def timed(tab, n):
        t = time.perf_counter()
        c = cpu()
        if n == 1 and os.environ.get('PROFILE') == tab:
            import cProfile, pstats
            pr = cProfile.Profile(); pr.enable()
            hk['show'](tab, animated=False); self.update()
            pr.disable()
            pstats.Stats(pr).sort_stats('cumulative').print_stats(45)
            pstats.Stats(pr).sort_stats('tottime').print_stats(25)
        else:
            hk['show'](tab, animated=False)
            self.update()
        res.setdefault(tab, []).append((time.perf_counter() - t) * 1000)
        res.setdefault(tab + '_cpu', []).append((cpu() - c) * 1000)
    for n in range(2):
        for tab in TABS:
            at(2500 if (n == 0 and tab == 'maps') else 700, lambda tab=tab, n=n: timed(tab, n))
    at(300, lambda: hk['show']('maps', animated=False))
    at(3000, lambda: res.update(idle0=(cpu(), time.perf_counter())))
    at(20000, lambda: res.update(idle1=(cpu(), time.perf_counter())))
    at(100, lambda: self.destroy())

    def run(i=0):
        if i >= len(steps):
            return
        ms, fn = steps[i]

        def go():
            try:
                fn()
            except Exception:
                errors.append(traceback.format_exc()[-800:])
            run(i + 1)
        self.after(ms, go)
    run()
    orig(self, *a)


tk.Tk.report_callback_exception = lambda self, *args: errors.append(''.join(traceback.format_exception(*args))[-800:])
tk.Tk.mainloop = patched
ps.gui()
print('импорт модуля: %.2f с, окно готово через %.2f с' % (T_IMPORT, res.get('window', -1)))
for tab in TABS:
    v = res.get(tab, [])
    c = res.get(tab + '_cpu', [])
    print('  %-10s первый раз %5.0f мс (процессор %4.0f), повторно %5.0f мс (процессор %4.0f)' % (
        tab, v[0] if v else -1, c[0] if c else -1, v[1] if len(v) > 1 else -1, c[1] if len(c) > 1 else -1))
if 'idle1' in res:
    (c0, t0), (c1, t1) = res['idle0'], res['idle1']
    print('простой: процессор %.1f%% одного ядра' % (100 * (c1 - c0) / (t1 - t0)))
print('ошибок окна:', len(errors))
for e in errors:
    print(e)

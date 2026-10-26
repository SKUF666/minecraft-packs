"""Где дёргается: на каждой вкладке - сборка, прокрутка колесом, наведение на карточки, смена размера окна.
Счётчик кадров (after каждые 8 мс) ловит подвисания главного потока: максимум, 95-й процентиль, число кадров
дольше 50 мс. JANK_PROFILE=1 - ещё и cProfile главного потока (что съедает время).

    python tools/jank.py          (JANK_TABS=maps,packs,...  JANK_LOGIN=1 - «вошёл в аккаунт»)
"""
import os, sys, time, tempfile, collections, traceback, importlib.machinery, importlib.util, tkinter as tk
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.environ.get('JANK_LIB') or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
sys.path.insert(0, LIB)
TABS = os.environ.get('JANK_TABS', 'maps,packs,builder,skins,versions,servers,friend,launchers,profile').split(',')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
ps.set_game_dir(os.path.join(tempfile.mkdtemp(prefix='portalis-jank-'), '.minecraft'))
store = {'lang': 'ru'}
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))
ME = {'id': 'u-me', 'nick': 'Гриша', 'login': 'grisha', 'avatar': None, 'about': 'Привет, играю в карты', 'favorites': ['OneBlock'],
      'stats': {'minutes': 300, 'maps_n': 6, 'packs': 2, 'friends': 3, 'maps': ['OneBlock', 'SkyBlock']}, 'skin': 'Notch'}
if os.environ.get('JANK_LOGIN'):
    def _now():
        return time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime())
    FR = [{'id': 'u-%d' % i, 'nick': 'Друг %d' % i, 'login': 'fr%d' % i, 'avatar': None} for i in range(6)]
    S = ps.Social
    S.ready = lambda self: True
    S.logged_in = lambda self: True
    S.uid = property(lambda self: 'u-me')
    S.me = lambda self: dict(ME, last_seen=_now())
    S.profile = lambda self, uid: dict(ME, last_seen=_now())
    S.friends = lambda self: [{'profile': dict(p, last_seen=_now()), 'state': 'friend'} for p in FR]
    S.feed = lambda self, *a, **k: []
    S.parties = lambda self: ([], [])
    S.public_parties = lambda self: []
    S.messages = lambda self, *a, **k: []
    S.set_status = S.update_profile = lambda self, *a, **k: None
    S.inbox = lambda self, *a, **k: []
    S.wall = lambda self, uid, limit=50: []
orig = tk.Tk.mainloop
errors = []
PROF = os.environ.get('JANK_PROFILE')
import socket, threading
_connect = socket.socket.connect
net_main = collections.Counter()


def _guard_connect(self, addr, *a):
    """Сеть из главного потока = окно замирает на время запроса: запоминаем, откуда."""
    if threading.current_thread() is threading.main_thread():
        st = ' < '.join('%s:%d' % (f.name, f.lineno) for f in traceback.extract_stack()[:-1]
                        if f.filename.endswith('PackSwitcher.pyw'))[-300:]
        net_main[(str(addr)[:60], st)] += 1
    return _connect(self, addr, *a)


socket.socket.connect = _guard_connect


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


class Meter:
    def __init__(self, root):
        self.root, self.on, self.ts = root, False, []
        self.tick()

    def tick(self):
        if self.on:
            self.ts.append(time.perf_counter())
            cv = getattr(self, 'cv', None)
            if cv is not None:
                try:
                    self.ys.append((time.perf_counter(), cv.yview()[0] * (cv.bbox('all') or (0, 0, 0, 0))[3]))
                except tk.TclError:
                    pass
        self.root.after(8, self.tick)

    def motion(self):
        """Ровность прокрутки: моменты, когда страница сдвинулась, и скорость между ними."""
        ys = getattr(self, 'ys', [])
        moves = [(t, y) for (t, y), (_t0, y0) in zip(ys[1:], ys) if abs(y - y0) > 0.5]
        if len(moves) < 3:
            return ''
        gaps = [(b[0] - a[0]) * 1000 for a, b in zip(moves, moves[1:]) if (b[0] - a[0]) < 0.4]
        if not gaps:
            return ''
        steps = [abs(b[1] - a[1]) for a, b in zip(moves, moves[1:])]
        import statistics
        return '  | движение: сдвигов %d, между ними сред %.0f мс, макс %.0f мс, шаг сред %.0f px, макс %.0f px' % (
            len(moves), statistics.mean(gaps), max(gaps), statistics.mean(steps), max(steps))

    def start(self):
        self.ts, self.on, self.ys = [time.perf_counter()], True, []
        if PROF:
            import cProfile
            self.pr = cProfile.Profile()
            self.pr.enable()

    def stop(self):
        self.on = False
        if PROF:
            self.pr.disable()
        d = sorted((b - a) * 1000 for a, b in zip(self.ts, self.ts[1:]))
        if not d:
            return 'нет кадров'
        return 'кадров %3d, макс %5.0f мс, p95 %4.0f мс, >50мс: %d' % (len(d), d[-1], d[int(len(d) * .95)],
                                                                       sum(x > 50 for x in d)) + self.motion()

    def top(self, n=12):
        if not PROF:
            return
        import pstats, io
        s = io.StringIO()
        pstats.Stats(self.pr, stream=s).sort_stats(os.environ.get('JANK_SORT', 'tottime')).print_stats(n * 3)
        out = [l for l in s.getvalue().splitlines() if l.strip() and ('PackSwitcher' in l or '{' in l or 'tkinter' in l
                                                                      or 'PIL' in l)][:n]
        for l in out:
            print('        ' + l.strip()[:150])
        if os.environ.get('JANK_CALLERS'):
            s = io.StringIO()
            st = pstats.Stats(self.pr, stream=s)
            st.sort_stats('cumulative').print_callers(os.environ['JANK_CALLERS'])
            print(chr(10).join(l[:220] for l in s.getvalue().splitlines()
                               if 'PackSwitcher' in l or 'ssl' in l or 'urllib' in l or 'http' in l)[:4000])


def patched(self, *a):
    self.geometry(os.environ.get('JANK_GEO', '1180x860+2200+30'))
    hk = self._hooks
    m = Meter(self)
    steps = []

    def page_root():
        pages = [w for w in self.winfo_children() if getattr(w, '_inpage', False)]
        return pages[-1] if pages else self

    def scroll_target():
        r = page_root()
        return getattr(r, '_cv', None) or hk['body']

    def wheel_burst(n=12, gap=90):
        cv = scroll_target()
        x, y = cv.winfo_rootx() + 200, cv.winfo_rooty() + 200
        for i in range(n):
            self.after(i * gap, lambda: cv.event_generate('<MouseWheel>', delta=-120, x=200, y=200, rootx=x, rooty=y))
        for i in range(n // 2 if n < 30 else 0):
            self.after(n * gap + 400 + i * gap, lambda: cv.event_generate('<MouseWheel>', delta=120, x=200, y=200,
                                                                            rootx=x, rooty=y))

    def hover_burst():
        r = page_root()
        cards = [w for w in walk(r) if isinstance(w, tk.Frame) and str(w.cget('cursor')) == 'hand2' and w.winfo_ismapped()]
        cards += [w for w in walk(r) if isinstance(w, tk.Label) and str(w.cget('cursor')) == 'hand2' and w.winfo_ismapped()]
        cards = cards[:30]
        for i, c in enumerate(cards):
            self.after(i * 40, lambda c=c: (c.event_generate('<Enter>'), c.event_generate('<Leave>')))
        return len(cards)

    def resize_burst():
        for i, w in enumerate([1100, 1020, 960, 1020, 1100, 1180]):
            self.after(i * 40, lambda w=w: self.geometry('%dx860' % w))  # тянут за край: шаги чаще, чем раз в 90 мс

    def phase(tab, name, action, ms):
        def go():
            m.cv = scroll_target()
            m.start()
            info = action()
            res[(tab, name)] = info

        def end():
            print('   %-8s %s%s' % (name, m.stop(), ('  (%s)' % res[(tab, name)]) if res.get((tab, name)) is not None else ''))
            m.top()
        steps.append((300, go))
        steps.append((ms, end))
    res = {}
    for tab in TABS:
        def open_tab(tab=tab):
            for w in [w for w in self.winfo_children() if getattr(w, '_inpage', False)]:
                w.destroy()
            t = time.perf_counter()
            if tab == 'profile':
                hk['open_profile'](ME, True, 'overview')
            elif tab == 'webmaps':
                hk['state']['maps_src'] = 'web'
                hk['show']('maps', animated=False)
            else:
                hk['show'](tab, animated=False)
            self.update()
            n = sum(1 for _ in walk(page_root() if tab == 'profile' else hk['body']))
            print('%s: сборка %.0f мс, виджетов %d' % (tab, (time.perf_counter() - t) * 1000, n))
        steps.append((1500, open_tab))
        steps.append((2500, lambda: None))  # догрузка картинок
        phase(tab, 'покой', lambda: None, 2000)
        phase(tab, 'колесо', wheel_burst, 2800)
        if os.environ.get('JANK_LONG'):
            phase(tab, 'долго', lambda: wheel_burst(60, 70), 9000)
        phase(tab, 'наведение', hover_burst, 1800)
        phase(tab, 'размер', resize_burst, 1500)
    steps.append((300, self.destroy))

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
print('сеть из главного потока (окно замирает):', sum(net_main.values()) or 'нет')
for (addr, st), n in net_main.most_common(15):
    print('  %3d  %s  %s' % (n, addr, st))
print('ошибок окна:', len(errors))
for e in errors:
    print(e)

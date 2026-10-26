"""Что перерисовывается само, пока пользователь ничего не делает: на каждой вкладке и в профиле окно стоит
CHURN_SEC секунд, считаются новые/удалённые виджеты, смены картинок и событий <Configure>, и кто их вызвал.

    python tools/idle_churn.py            (CHURN_SEC=8, CHURN_TABS=maps,packs,...,profile)
"""
import os, sys, time, tempfile, collections, traceback, importlib.machinery, importlib.util, tkinter as tk
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
sys.path.insert(0, LIB)
SEC = float(os.environ.get('CHURN_SEC', '8'))
TABS = os.environ.get('CHURN_TABS', 'maps,packs,builder,skins,versions,servers,friend,launchers,profile,wall').split(',')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
ps.set_game_dir(os.path.join(tempfile.mkdtemp(prefix='portalis-churn-'), '.minecraft'))
store = {'lang': 'ru'}
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))
ME = {'id': 'u-me', 'nick': 'Гриша', 'login': 'grisha', 'avatar': None, 'about': 'Привет', 'favorites': ['OneBlock'],
      'stats': {'minutes': 300, 'maps_n': 4, 'maps': ['OneBlock', 'SkyBlock']}, 'skin': None}
ps.Social.wall = lambda self, uid, limit=50: [
    {'id': 1, 'author': 'u-me', 'author_profile': ME, 'body': 'Тест', 'created_at': '2026-10-26T10:00:00Z',
     'reply_to': None, 'reactions': {}}]

if os.environ.get('CHURN_LOGIN'):  # «вошёл в аккаунт»: сервер подменён, «был в сети» друзей меняется каждый запрос
    def _now():
        return time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime())
    FR = [{'id': 'u-%d' % i, 'nick': 'Друг %d' % i, 'login': 'fr%d' % i, 'avatar': None, 'status': 'online'} for i in range(4)]

    def _prof(p):
        return dict(p, last_seen=_now(), status_at=_now())
    S = ps.Social
    S.ready = lambda self: True
    S.logged_in = lambda self: True
    S.uid = property(lambda self: 'u-me')
    S.me = lambda self: _prof(ME)
    S.profile = lambda self, uid: _prof(ME)
    S.friends = lambda self: [{'profile': _prof(p), 'state': 'friend'} for p in FR]
    S.feed = lambda self, *a, **k: [{'id': 1, 'user_id': 'u-1', 'kind': 'level', 'text': 'достиг 5 уровня', 'created_at': '2026-10-26T10:00:00Z', 'profile': _prof(FR[1]), 'likes': []}]
    S.parties = lambda self: ([{'id': 'p1', 'name': 'Вечер', 'owner': 'u-me', 'lobby': {}, 'public': False, 'updated_at': _now()}], [])
    S.public_parties = lambda self: []
    S.members = lambda self, pid: [{'id': 'u-me', 'nick': 'Гриша', 'login': 'grisha', 'ready': False, 'last_seen': _now()}] + [dict(_prof(FR[0]), ready=True)]
    S.proposals = lambda self, pid: []
    S.messages = lambda self, pid=None, after=0, **k: []
    S.set_status = lambda self, *a, **k: None
    S.update_profile = lambda self, **k: None
    S.inbox = lambda self, *a, **k: []
    S.reactions = lambda self, *a, **k: {}
    S.top_played = lambda self, *a, **k: []
    S.top_rated = lambda self, *a, **k: []

counts = collections.Counter()
where = collections.Counter()
on = {'v': False}


def who():
    ch = [('%s:%d' % (fr.name, fr.lineno)) for fr in traceback.extract_stack()[:-2] if fr.filename.endswith('PackSwitcher.pyw')]
    return ' < '.join(reversed(ch[-5:])) or '?'


t0 = {'v': 0}
secs = collections.Counter()


def note(kind):
    if on['v']:
        counts[kind] += 1
        where[(kind, who())] += 1
        secs[int(time.time() - t0['v'])] += 1


_init = tk.BaseWidget.__init__
_destroy = tk.BaseWidget.destroy
_conf = tk.Misc.configure


def init(self, *a, **k):
    note('создан')
    return _init(self, *a, **k)


def destroy(self):
    note('удалён')
    return _destroy(self)


def conf(self, cnf=None, **kw):
    if on['v'] and ('image' in kw or (isinstance(cnf, dict) and 'image' in cnf)):
        note('картинка')
    if on['v'] and ('height' in kw or 'width' in kw):
        note('размер')
    return _conf(self, cnf, **kw)


tk.BaseWidget.__init__ = init
tk.BaseWidget.destroy = destroy
tk.Misc.configure = conf
tk.Misc.config = conf
orig = tk.Tk.mainloop
errors = []


def patched(self, *a):
    self.geometry('1180x860+2200+30')
    hk = self._hooks
    steps = []
    res = {}

    def measure(name):
        def start():
            counts.clear(); where.clear(); secs.clear(); on['v'] = True; t0['v'] = time.time()
            res[name] = time.time()

        def stop():
            on['v'] = False
            print('%-10s %s  по секундам: %s' % (name, dict(counts) or 'тихо', dict(sorted(secs.items()))))
            for (k, w), n in where.most_common(4):
                print('     %4d %-9s %s' % (n, k, w))
        return start, stop

    for tab in TABS:
        if tab == 'profile':
            steps.append((1500, lambda: hk['open_profile'](ME, True, 'overview')))
        elif tab == 'wall':
            steps.append((500, lambda: [w for w in self.winfo_children() if getattr(w, '_inpage', False)][-1]._go('wall')))
        else:
            steps.append((1500, lambda tab=tab: hk['show'](tab)))
        s0, s1 = measure(tab)
        steps.append((2500, s0))  # дать вкладке догрузиться
        steps.append((int(SEC * 1000), s1))
    steps.append((200, self.destroy))

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

"""Пати: краткая карточка на вкладке «Друзья» и страница пати (во что играем, готовность, игра, предложения,
участники, чат) - на подставном сервере, снимки. python tools/gui_party.py"""
import os, sys, time, tempfile, importlib.machinery, importlib.util, tkinter as tk, traceback
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winshot
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
sys.path.insert(0, LIB)
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
ps.set_game_dir(os.path.join(tempfile.mkdtemp(prefix='portalis-party-'), '.minecraft'))
store = {'lang': os.environ.get('PORTALIS_LANG', 'ru')}
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime())


ME = {'id': 'u-me', 'nick': 'Гриша', 'login': 'grisha', 'avatar': 'preset:3'}
FR = [{'id': 'u-%d' % i, 'nick': n, 'login': 'f%d' % i, 'avatar': 'preset:%d' % (i + 4)} for i, n in
      enumerate(['Олег', 'Маша', 'Дима'])]
calls = []
S = ps.Social
S.ready = lambda self: True
S.logged_in = lambda self: True
S.uid = property(lambda self: 'u-me')
S.me = lambda self: dict(ME, last_seen=now())
S.friends = lambda self: [{'profile': dict(p, last_seen=now()), 'state': 'friend'} for p in FR]
S.feed = lambda self, *a, **k: []
PARTY = {'id': 'p1', 'name': 'Вечер выживания', 'owner': 'u-me', 'plan': 'строим базу на острове', 'is_public': True,
         'max_players': 8, 'version': '1.21.8', 'launcher': 'TLauncher',
         'lobby': {'kind': 'map', 'map': 'oneblock', 'title': 'OneBlock', 'version': '26.1.2', 'pack': None},
         'game_code': None, 'game_at': None}
S.parties = lambda self: ([dict(PARTY, updated_at=now())], [{'id': 'p2', 'name': 'Паркур с Димой'}])
S.public_parties = lambda self: []
S.members = lambda self, pid: [dict(ME, ready=True, last_seen=now()), dict(FR[0], ready=True, last_seen=now()),
                               dict(FR[1], ready=False, last_seen='2026-01-01T00:00:00+00:00')]
S.proposals = lambda self, pid: [
    {'id': 11, 'kind': 'map', 'title': 'SkyBlock', 'author': 'u-0', 'votes': ['u-0', 'u-me'], 'payload': {'map': 'skyblock'}},
    {'id': 12, 'kind': 'server', 'title': 'Hypixel', 'author': 'u-1', 'votes': ['u-1'], 'payload': {'ip': 'mc.hypixel.net'}},
    {'id': 13, 'kind': 'text', 'title': 'строим замок', 'author': 'u-me', 'votes': [], 'payload': {}}]
S.messages = lambda self, pid=None, after=0, **k: [] if after else [
    {'id': 1, 'from_user': 'u-0', 'body': 'Всем привет! Кто готов?', 'created_at': now()},
    {'id': 2, 'from_user': 'u-me', 'body': 'Я готов, запускаю OneBlock', 'created_at': now()}]
S.set_status = S.update_profile = lambda self, *a, **k: None
S.inbox = lambda self, *a, **k: []
S.reactions = lambda self, *a, **k: {}
S.follow_stats = lambda self, u: None
for name in ('kick', 'make_owner', 'update_party', 'set_ready', 'vote', 'merge_lobby', 'send', 'invite'):
    setattr(S, name, (lambda n: lambda self, *a, **k: calls.append(n))(name))
orig = tk.Tk.mainloop
errors = []


def patched(self, *a):
    self.geometry('1180x860+2200+30')
    hk = self._hooks
    shot = lambda n: winshot.shot(self, os.path.join(OUT, 'party_' + n + '.png'))
    steps = [(1500, lambda: hk['show']('friend', animated=False)), (4500, lambda: hk['body'].yview_moveto(0.33)),
             (600, lambda: shot('tab')), (300, hk['party_page']), (1500, lambda: shot('page')),
             (300, lambda: [w for w in self.winfo_children() if getattr(w, '_inpage', False)][-1]._cv.yview_moveto(1.0)),
             (600, lambda: shot('page_bottom')), (300, self.destroy)]

    def run(i=0):
        if i >= len(steps):
            return
        ms, fn = steps[i]

        def go():
            try:
                fn()
            except Exception:
                errors.append(traceback.format_exc()[-1500:])
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

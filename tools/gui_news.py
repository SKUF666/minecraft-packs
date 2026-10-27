"""Официальные новости в ленте: записи Portalis (news.json из репозитория - здесь берётся локальный) и снапшоты/релизы
Minecraft из ленты Mojang (настоящая сеть); фильтр «Новости», страница «Все изменения» с переводом и оригиналом.
Сервер Portalis не нужен - свой профиль и записи друзей подставляются. python tools/gui_news.py"""
import os, sys, json, tempfile, importlib.machinery, importlib.util, tkinter as tk, traceback, time
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winshot
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO, 'library')
sys.path.insert(0, LIB)
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
ps.set_game_dir(os.path.join(tempfile.mkdtemp(prefix='portalis-news-'), '.minecraft'))
store = {'lang': os.environ.get('PORTALIS_LANG', 'ru'), 'news_seen': 'mc:26-4-snapshot-5'}  # сообщение о новых записях
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))
_real_get = ps._get


def _get(url, *a, **k):  # news.json - из рабочей копии (на GitHub он появится после пуша)
    if url.startswith(ps.NEWS_URL):
        with open(os.path.join(REPO, 'news.json'), 'rb') as f:
            return f.read()
    return _real_get(url, *a, **k)
ps._get = _get
now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
ME = {'id': 'u-me', 'nick': 'Гриша', 'login': 'grisha', 'avatar': None, 'stats': {'minutes': 754}}
FR = {'id': 'u-fr', 'nick': 'Друг Олег', 'login': 'oleg', 'avatar': None, 'status': 'online'}
S = ps.Social
S.uid = property(lambda self: 'u-me')
S.logged_in = lambda self: True
S.ready = lambda self: True
S.follow_stats = lambda self, u: {'followers': 3, 'following': 1, 'me': False}
S.following = lambda self: ['u-fr']
S.post_feed = lambda self, ids, limit=40: [
    {'id': 9, 'owner': 'u-fr', 'author': 'u-fr', 'author_profile': FR, 'body': 'Построил базу в OneBlock!',
     'created_at': now, 'reply_to': None, 'reactions': {'fire': ['u-me']}}]
S.feed = lambda self, limit=30: [{'id': 1, 'user_id': 'u-fr', 'kind': 'level', 'text': 'достиг 7 уровня',
                                  'created_at': '2026-10-21T10:00:00Z', 'profile': FR, 'likes': []}]
ps.load_lang(store['lang'])
t0 = time.time()
news = ps.official_news()
print('новостей: %d за %.1f с' % (len(news), time.time() - t0))
for n in news:
    print('  ', n['src'], n['kind'], n['created_at'][:10], n['title'], '| пунктов', len(n['lines']), '| картинка', bool(n['image']))
errors = []
orig = tk.Tk.mainloop


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


def patched(self, *a):
    self.geometry(os.environ.get('PROF_SIZE', '1180x860') + '+2200+30')
    hk = self._hooks
    steps = []
    at = lambda ms, fn: steps.append((ms, fn))

    def page():
        return [w for w in self.winfo_children() if getattr(w, '_inpage', False)][-1]
    shot = lambda name: winshot.shot(self, os.path.join(OUT, 'news_' + name + '.png'))

    def click_text(text):
        """Нажать видимую кнопку-фишку с таким текстом (PButton рисует текст картинкой - ищем по ._o)."""
        for w in walk(page()):
            if (getattr(w, '_o', None) or {}).get('text') in (text, ps.tr(text)) and w.winfo_ismapped():
                w._cmd()
                return True
        errors.append('не нашёл кнопку %s' % text)
    at(1800, lambda: (hk['state'].setdefault('soc', {'loaded': False, 'loading': False, 'me': None, 'friends': [], 'parties': [], 'invites': [], 'party': None, 'members': [], 'msgs': [], 'last': 0, 'err': None, 'mode': 'in', 'sel': None}).update(me=ME),
                      hk['open_profile'](ME, True, 'feed')))
    at(3500, lambda: shot('feed'))
    at(300, lambda: page()._cv.yview_moveto(0.25))
    at(700, lambda: shot('feed_mid'))
    at(300, lambda: page()._cv.yview_moveto(0))
    at(300, lambda: click_text('Новости'))
    at(1500, lambda: shot('only_news'))
    at(300, lambda: click_text('Друзья и подписки'))
    at(1200, lambda: shot('only_people'))
    at(300, lambda: hk['mc_notes_page'](next(n for n in news if n['src'] == 'minecraft')))
    at(6000, lambda: shot('notes'))
    at(10, lambda: toasts.extend(str(w.cget('text')) for w in walk(self) if isinstance(w, tk.Label)
                                 and ': ' in str(w.cget('text')) and str(w.cget('bg')) == ps.CARD_HI))
    at(300, lambda: page()._cv.yview_moveto(0.3))
    at(600, lambda: shot('notes_mid'))
    at(300, lambda: page()._cv.yview_moveto(0))
    at(300, lambda: ps.LANG == 'en' or click_text('Оригинал'))  # на английском заметки и так в оригинале
    at(1200, lambda: shot('notes_orig'))
    at(300, lambda: self.destroy())

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
toasts = []
ps.gui()
print('сообщение о новостях: просмотрено ->', store.get('news_seen'), '| показано:', toasts)
print('ошибок окна:', len(errors))
for e in errors:
    print(e)

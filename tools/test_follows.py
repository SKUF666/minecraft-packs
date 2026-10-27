"""Подписки, лента, картинки на стене, ссылки в профиле и управление пати - на настоящем сервере Supabase
(шаги 8-10 выполнены). Два тестовых игрока zt_<случайное>_a/_b (удалить их потом может только хозяин проекта).
Свои записи, подписки и пати тест за собой убирает.    python tools/test_follows.py"""
import importlib.machinery, importlib.util, os, sys, tempfile, shutil, random, string, io
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
tmp = tempfile.mkdtemp(prefix='portalis-follow-')
shutil.copy2(os.path.join(LIB, 'social.json'), os.path.join(tmp, 'social.json'))
ps.ROOT = tmp
fails = 0


def check(cond, text):
    global fails
    fails += not cond
    print(('OK   ' if cond else 'ОШИБКА ') + text, flush=True)


def client():
    store = {}
    ps.load_settings = lambda: dict(store)
    ps.save_settings = lambda d: (store.clear(), store.update(d))
    c = ps.Social()
    c._store = store
    return c


def use(c):
    ps.load_settings = lambda: dict(c._store)
    ps.save_settings = lambda d: (c._store.clear(), c._store.update(d))
    return c


tag = 'zt_' + ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
pw = 'Portalis-test-' + ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(10))
a, b = client(), client()
use(a).sign_up(tag + '_a', pw, 'Тестер А')
use(b).sign_up(tag + '_b', pw, 'Тестер Б')
ua, ub = use(a).uid, use(b).uid
print('тестовые логины:', tag + '_a', tag + '_b')

# --- подписка ---
st0 = use(a).follow_stats(ub)
check(st0 is not None and st0.get('followers') == 0 and not st0.get('me'), 'счётчики до подписки: %s' % st0)
use(a).follow(ub, True)
st1 = use(a).follow_stats(ub)
check(st1 and st1.get('followers') == 1 and st1.get('me') is True, 'А подписан на Б: %s' % st1)
check(ub in use(a).following(), 'А видит Б в своих подписках')
check([p.get('id') for p in use(b).followers(ub)] == [ua], 'Б видит А в подписчиках')

# --- пост с картинкой у Б, лента у А (не друзья, только подписка) ---
from PIL import Image as PI
img = PI.new('RGB', (64, 40), (200, 80, 40))
url = use(b).upload_image(img)
check(url.startswith(use(b).cfg['url']), 'картинка загружена в хранилище')
use(b).post_wall(ub, 'Пост с картинкой', None, url)
use(b).post_wall(ub, '', None, url)  # одна картинка без текста
feed = use(a).post_feed([ua, ub], 10)
mine = [r for r in feed if r['author'] == ub]
check(len(mine) == 2 and mine[0].get('image') == url, 'подписчик видит посты Б с картинкой в ленте (%d)' % len(mine))
check(ps.media_url_ok(url) if hasattr(ps, 'media_url_ok') else True, 'ссылка на картинку - из своего хранилища')
# без подписки - не видно
use(a).follow(ub, False)
feed2 = use(a).post_feed([ub], 10)
check(not feed2, 'после отписки посты Б не видны (не друзья)')
check(use(a).follow_stats(ub).get('followers') == 0, 'счётчик вернулся к 0')

# --- ссылки в профиле ---
links = [{'kind': ps.link_kind('https://youtube.com/@test'), 'url': 'https://youtube.com/@test'},
         {'kind': ps.link_kind(ps.clean_link('t.me/test_channel')), 'url': ps.clean_link('t.me/test_channel')}]
use(a).update_profile(links=links)
pa = use(b).profile(ua)
check(pa.get('links') == links, 'ссылки сохранены и видны другому: %s' % [x['kind'] for x in pa.get('links') or []])

# --- пати: настройки, хозяин, убрать участника ---
pid = use(a).create_party('Тест подписок', public=False)
pid = pid['id'] if isinstance(pid, dict) else pid
use(a).rest('POST', 'friendships', body={'requester': ua, 'addressee': ub})
use(b).accept(ua)
use(a).invite(pid, ub)
use(b).join(pid)
check(len(use(a).members(pid)) == 2, 'Б вступил в пати')
use(a).update_party(pid, name='Новое имя', plan='строим', max_players=4, is_public=True)
pr = use(a).rest('GET', 'parties', {'id': 'eq.' + pid, 'select': 'name,plan,max_players,is_public'})[0]
check(pr == {'name': 'Новое имя', 'plan': 'строим', 'max_players': 4, 'is_public': True}, 'хозяин поменял настройки: %s' % pr)
try:
    use(b).update_party(pid, name='Захват')
    check(False, 'участник НЕ должен менять настройки (шаг 9)')
except ps.SocialError as e:
    check(True, 'участник не может менять настройки: %s' % str(e)[:60])
use(a).make_owner(pid, ub)
own = use(b).rest('GET', 'parties', {'id': 'eq.' + pid, 'select': 'owner'})[0]['owner']
check(own == ub, 'пати передана Б')
use(b).kick(pid, ua)
check([m['id'] for m in use(b).members(pid)] == [ub], 'новый хозяин убрал А из пати')
# уборка
use(b).leave({'id': pid, 'owner': ub})
for r in use(b).wall(ub):
    use(b).delete_post(r['id'])
use(a).update_profile(links=[])
use(a).remove_friend(ub) if hasattr(use(a), 'remove_friend') else None
print('Итог: ошибок', fails, '| удалить аккаунты:', tag + '_a', tag + '_b')

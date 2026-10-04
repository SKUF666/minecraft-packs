"""Аккаунты Portalis на настоящем сервере Supabase: два тестовых игрока (zt_<случайное>_a/_b) и посторонний _c.
Регистрация, вход, друзья, пати, чат, личные сообщения, приглашение в игру, правила доступа.
    python tools/test_social.py"""
import importlib.machinery, importlib.util, os, sys, tempfile, shutil, random, string
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
tmp = tempfile.mkdtemp(prefix='portalis-social-')
shutil.copy2(os.path.join(LIB, 'social.json'), os.path.join(tmp, 'social.json'))
ps.ROOT = tmp
fails = 0


def check(cond, text):
    global fails
    fails += not cond
    print(('OK   ' if cond else 'ОШИБКА ') + text, flush=True)


def client():
    """У каждого игрока свои настройки (как будто разные компьютеры)."""
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
a, b, x = client(), client(), client()
use(a).sign_up(tag + '_a', pw, 'Тестер А')
check(a.logged_in() and a.uid, 'регистрация А: %s' % tag)
use(b).sign_up(tag + '_b', pw, 'Тестер Б')
use(x).sign_up(tag + '_c', pw, 'Посторонний')
try:
    use(a).sign_up(tag + '_a', pw, 'Дубль')
    check(False, 'повторный логин должен быть занят')
except ps.SocialError as e:
    check('занят' in str(e), 'повторная регистрация того же логина: «%s»' % e)
try:
    ps.Social.sign_in(client(), tag + '_a', 'неверный-пароль')
    check(False, 'неверный пароль должен не пускать')
except ps.SocialError as e:
    check('Неверный' in str(e), 'неверный пароль: «%s»' % e)
fresh = client()
fresh.sign_in(tag + '_a', pw)
check(fresh.uid == a.uid, 'вход по логину и паролю')
me = use(a).me()
check(me and me['login'] == tag + '_a' and me['nick'] == 'Тестер А', 'профиль создан сам: %s / %s' % (me and me['login'], me and me['nick']))
p, st = use(a).add_friend(tag + '_b')
check(st == 'sent', 'заявка в друзья отправлена')
fb = use(b).friends()
check(fb and fb[0]['state'] == 'incoming', 'у Б входящая заявка')
p2, st2 = use(b).add_friend(tag + '_a')
check(st2 == 'accepted', 'встречная заявка = дружба принята')
check(use(a).friends()[0]['state'] == 'friend', 'у А друг Б')
use(b).set_status('online')
check(ps.is_online(use(a).friends()[0]['profile']), 'А видит, что Б в сети')
party = use(a).create_party('Тестовая пати')
use(a).invite(party['id'], b.uid)
mine, inv = use(b).parties()
check(inv and inv[0]['id'] == party['id'], 'Б видит приглашение в пати')
use(b).join(party['id'])
check([m['id'] for m in use(a).members(party['id'])].count(b.uid) == 1, 'Б в пати')
use(a).send('Привет из теста!', pid=party['id'])
use(b).send('И тебе привет', pid=party['id'])
msgs = use(b).messages(pid=party['id'])
check([m['body'] for m in msgs] == ['Привет из теста!', 'И тебе привет'], 'чат пати: %d сообщения по порядку' % len(msgs))
check(use(b).messages(pid=party['id'], after=msgs[0]['id'])[0]['body'] == 'И тебе привет', 'новые сообщения после последнего')
use(a).send('Лично тебе', to=b.uid)
check([m['body'] for m in use(b).messages(to=a.uid)] == ['Лично тебе'], 'личное сообщение другу')
use(a).share_game(party['id'], 'MC1-test', {'host': a.uid, 'pack': 'RPG Pack', 'address': '25.1.2.3:51234'})
pb = use(b).party(party['id'])
check(pb['game_code'] == 'MC1-test' and pb['game_info']['pack'] == 'RPG Pack', 'Б получил приглашение в игру от хозяина')
# правила доступа: посторонний ничего не видит и не может писать
check(use(x).messages(pid=party['id']) == [], 'посторонний не видит чат пати')
check(use(x).party(party['id']) is None, 'посторонний не видит пати')
try:
    use(x).send('взлом', pid=party['id'])
    check(False, 'посторонний не должен писать в пати')
except ps.SocialError as e:
    check(True, 'посторонний не может писать в пати: «%s»' % e)
try:
    use(x).send('спам', to=a.uid)
    check(False, 'не друг не должен писать лично')
except ps.SocialError:
    check(True, 'не друг не может писать лично')
try:
    use(x).rest('POST', 'party_members', body={'party_id': party['id'], 'user_id': x.uid})
    check(False, 'вступить без приглашения нельзя')
except ps.SocialError:
    check(True, 'вступить в пати без приглашения нельзя')
try:
    use(x).rest('PATCH', 'profiles', {'id': 'eq.' + a.uid}, {'nick': 'взлом'})
    check(use(a).me()['nick'] == 'Тестер А', 'чужой профиль не меняется')
except ps.SocialError:
    check(True, 'чужой профиль не меняется')
# профиль: аватар, статус, настроение, любимые игры, о себе
use(a).update_profile(avatar='preset:3', presence='dnd', mood='Строю базу', about='Люблю скайблок',
                      favorites=['OneBlock', 'SkyBlock'])
pa = use(b).profile(a.uid)
check(pa['avatar'] == 'preset:3' and pa['mood'] == 'Строю базу' and pa['favorites'] == ['OneBlock', 'SkyBlock']
      and pa['about'] == 'Люблю скайблок', 'Б видит профиль А: аватар, настроение, любимые игры, о себе')
use(a).set_status('online')
check(ps.status_text(use(b).profile(a.uid)) == 'не беспокоить', 'статус «не беспокоить» виден другу')
use(a).update_profile(presence='invisible')
use(a).set_status('playing', 'RPG Pack')
pa = use(b).profile(a.uid)
check(pa['status'] == 'offline' and pa['last_seen'] is None and pa['status_detail'] is None and not ps.is_online(pa),
      'невидимку друг видит «не в сети», без подробностей')
check(use(a).me()['presence'] == 'invisible', 'сам себя невидимка видит невидимкой')
try:
    use(b).rest('GET', 'profiles', {'id': 'eq.' + a.uid, 'select': 'status,last_seen'})
    check(False, 'статус в обход представления не должен читаться')
except ps.SocialError:
    check(True, 'настоящий статус невидимки в обход не прочитать')
use(a).update_profile(presence='auto')
av = ps.avatar_from_file(os.path.join(LIB, 'Оформление', 'avatars', 'av_05.png'))
use(a).update_profile(avatar=av)
check(use(b).profile(a.uid)['avatar'] == av and len(av) < 40000, 'своя картинка аватара сохраняется (%d символов)' % len(av))
# стена
use(a).post_wall(a.uid, 'Моя первая запись')
use(b).post_wall(a.uid, 'Привет со стены!')
wl = use(b).wall(a.uid)
check([w['body'] for w in wl] == ['Привет со стены!', 'Моя первая запись'] and wl[0]['author_profile']['nick'] == 'Тестер Б',
      'стена: записи хозяина и друга, новые сверху, с автором')
check(use(x).wall(a.uid) == [], 'посторонний не видит стену')
try:
    use(x).post_wall(a.uid, 'спам')
    check(False, 'посторонний не должен писать на стену')
except ps.SocialError:
    check(True, 'посторонний не может писать на стену')
use(a).delete_post(wl[0]['id'])
check([w['body'] for w in use(a).wall(a.uid)] == ['Моя первая запись'], 'хозяин удалил чужую запись со своей стены')
use(b).leave(party)
check(not use(b).parties()[0], 'Б вышел из пати')
use(a).leave(party)
check(not use(a).parties()[0], 'хозяин распустил пати')
use(a).remove_friend(b.uid)
check(use(a).friends() == [], 'дружба удалена')
for c in (a, b, x):
    use(c).set_status('offline')
    use(c).sign_out()
print('Итог: ошибок', fails, '| тестовые логины', tag + '_a/_b/_c')
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if fails else 0)

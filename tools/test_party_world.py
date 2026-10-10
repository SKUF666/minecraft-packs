"""Общий мир пати на настоящем сервере: хозяин кладёт мир в облако пати, второй скачивает и становится хозяином.
Логины zt_* удаляет владелец.  python tools/test_party_world.py"""
import importlib.machinery, importlib.util, os, sys, tempfile, shutil, random, string, time
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
sys.path.insert(0, LIB)
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
tmp = tempfile.mkdtemp(prefix='portalis-pw-')
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
pw = 'Portalis-test-' + ''.join(random.choice(string.ascii_letters) for _ in range(10))
a, b = client(), client()
use(a).sign_up(tag + '_a', pw, 'Хозяин')
use(b).sign_up(tag + '_b', pw, 'Гость')
for c_ in (a, b):
    use(c_).set_status('online')
pa = use(a).create_party('Мир пати', public=True, max_players=4)
use(b).join_public(pa['id'])
# хозяин: мир -> облако пати
ps.SAVES = os.path.join(tmp, 'saves_a')
wd = os.path.join(ps.SAVES, 'Наш мир')
os.makedirs(wd)
open(os.path.join(wd, 'level.dat'), 'wb').write(b'day 5')
data = ps.world_zip(wd)
use(a).upload('worlds', 'party/%s/shared_world.zip' % pa['id'], data, 'application/zip', upsert=True)
lob = use(a).merge_lobby(pa['id'], world_title='Наш мир', world_by='Хозяин', world_size=len(data))
check(lob.get('world_title') == 'Наш мир', 'хозяин сохранил мир в пати, лобби знает его')
# гость: скачать и стать хозяином
ps.SAVES = os.path.join(tmp, 'saves_b')
os.makedirs(os.path.join(ps.SAVES, 'Наш мир'))
open(os.path.join(ps.SAVES, 'Наш мир', 'level.dat'), 'wb').write(b'old')
dst = os.path.join(tmp, 'pw.zip')
use(b).download_file('worlds', 'party/%s/shared_world.zip' % pa['id'], dst)
kept = ps.world_rollback(dst, 'Наш мир')
check(open(os.path.join(ps.SAVES, 'Наш мир', 'level.dat'), 'rb').read() == b'day 5', 'гость получил мир пати')
check(kept and open(os.path.join(kept, 'level.dat'), 'rb').read() == b'old', 'его прежний мир остался рядом')
lob2 = use(b).merge_lobby(pa['id'], host=b.uid)
check(lob2.get('host') == b.uid and lob2.get('world_title') == 'Наш мир', 'гость стал хозяином, мир в лобби не потерялся')
check((use(a).party(pa['id']).get('lobby') or {}).get('host') == b.uid, 'бывший хозяин видит нового')
use(a).delete_file('worlds', 'party/%s/shared_world.zip' % pa['id'])
use(a).leave(pa)
print('тестовые логины: %s_a / _b | ошибок: %d' % (tag, fails))
sys.exit(1 if fails else 0)

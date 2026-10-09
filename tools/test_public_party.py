"""Открытые пати на настоящем сервере (нужен supabase/portalis_7_public.sql): хозяин открывает пати, посторонний
видит её в списке и входит без приглашения; места кончаются; закрытая пропадает из списка. Логины zt_* удаляет владелец.
    python tools/test_public_party.py"""
import importlib.machinery, importlib.util, os, sys, tempfile, shutil, random, string
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
tmp = tempfile.mkdtemp(prefix='portalis-pub-')
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
a, b, x = client(), client(), client()
for c_, s_ in ((a, '_a'), (b, '_b'), (x, '_x')):
    use(c_).sign_up(tag + s_, pw, 'Тестер' + s_)
    use(c_).set_status('online')
pa = use(a).create_party('Открытая проверка', public=True, plan='OneBlock', launcher='TLauncher', version='1.21.4',
                         max_players=2)
check(pa.get('is_public') is True and pa.get('plan') == 'OneBlock', 'хозяин создал открытую пати с планом')
lst = use(b).public_parties()
mine = [p for p in lst or [] if p['id'] == pa['id']]
check(bool(mine), 'посторонний видит её в списке открытых')
check(bool(mine) and mine[0]['members'] == 1 and mine[0]['max_players'] == 2 and mine[0]['launcher'] == 'TLauncher',
      'в списке: 1 из 2, лаунчер')
use(b).join_public(pa['id'])
check(any(m['id'] == b.uid for m in use(a).members(pa['id'])), 'посторонний вошёл без приглашения')
try:
    use(x).join_public(pa['id'])
    check(False, 'третьего в полную пати не пускает')
except ps.SocialError:
    check(True, 'третьего в полную пати не пускает')
use(a).set_party_public(pa['id'], False)
check(not [p for p in use(x).public_parties() or [] if p['id'] == pa['id']], 'закрытая пати пропала из списка')
use(a).leave(pa)
print('тестовые логины: %s_a / _b / _x | ошибок: %d' % (tag, fails))
sys.exit(1 if fails else 0)

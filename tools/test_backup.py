"""Резервные копии миров на настоящем сервере: копия, лишние удаляются, откат (нынешний мир остаётся рядом).
Логин zt_* удаляет владелец.  python tools/test_backup.py"""
import importlib.machinery, importlib.util, os, sys, tempfile, shutil, random, string, time
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
tmp = tempfile.mkdtemp(prefix='portalis-bak-')
shutil.copy2(os.path.join(LIB, 'social.json'), os.path.join(tmp, 'social.json'))
ps.ROOT = tmp
ps.SAVES = os.path.join(tmp, 'saves')
store = {}
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))
fails = 0


def check(cond, text):
    global fails
    fails += not cond
    print(('OK   ' if cond else 'ОШИБКА ') + text, flush=True)


w = 'Мой мир'
wd = os.path.join(ps.SAVES, w)
os.makedirs(os.path.join(wd, 'region'))
open(os.path.join(wd, 'level.dat'), 'wb').write(b'v1')
tag = 'zt_' + ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
c = ps.Social()
c.sign_up(tag + '_k', 'Portalis-test-' + ''.join(random.choice(string.ascii_letters) for _ in range(10)), 'Копии')
ps.backup_world(c, w, keep=2)
time.sleep(1.2)
open(os.path.join(wd, 'level.dat'), 'wb').write(b'v2')
ps.backup_world(c, w, keep=2)
time.sleep(1.2)
open(os.path.join(wd, 'level.dat'), 'wb').write(b'v3')
ps.backup_world(c, w, keep=2)
vers = ps.backup_list(c, w)
check(len(vers) == 2, 'хранятся только 2 последние копии (%d)' % len(vers))
check(vers[0]['when'] >= vers[1]['when'], 'новые сверху')
dst = os.path.join(tmp, 'b.zip')
c.download_file('worlds', ps.backup_prefix(c.uid, w) + vers[1]['name'], dst)
open(os.path.join(wd, 'level.dat'), 'wb').write(b'v4')
kept = ps.world_rollback(dst, w)
check(open(os.path.join(wd, 'level.dat'), 'rb').read() == b'v2', 'мир вернулся к копии v2')
check(kept and open(os.path.join(kept, 'level.dat'), 'rb').read() == b'v4', 'нынешний мир сохранён рядом: ' + os.path.basename(kept or ''))
for v in ps.backup_list(c, w):
    c.delete_file('worlds', ps.backup_prefix(c.uid, w) + v['name'])
check(not ps.backup_list(c, w), 'копии теста убраны из облака')
print('тестовый логин: %s_k | ошибок: %d' % (tag, fails))
sys.exit(1 if fails else 0)

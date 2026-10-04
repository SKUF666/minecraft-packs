"""Модпак Modrinth, проверка обновлений модов, сервер пати и облачные миры - на временной папке.
    python tools/test_phase3.py"""
import importlib.machinery, importlib.util, os, sys, tempfile, time
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
tmp = tempfile.mkdtemp(prefix='portalis-p3-')
ps.ROOT = tmp
ps.MAPS_DIR = os.path.join(tmp, 'Карты')
ps.set_game_dir(os.path.join(tmp, '.minecraft'))
fails = 0


def check(cond, text):
    global fails
    fails += not cond
    print(('OK   ' if cond else 'ОШИБКА ') + text)


log = lambda m: print('   ', m)
# модпак
hits, total = ps.mr_search('', '1.21.1', 'fabric', 'modpack', None, 'downloads', 0, 5)
check(total > 50, 'модпаки для 1.21.1: %d, первые: %s' % (total, [h['title'] for h in hits[:3]]))
url, fn, ver = ps.mr_modpack_file('sop', '1.21.1')
check(fn.endswith('.mrpack'), 'Simply Optimized для 1.21.1: %s (%s)' % (fn, ver))
folder = ps.import_mrpack(url, None, log)
pk = next((p for p in ps.find_packs() if os.path.abspath(p['path']) == os.path.abspath(folder)), None)
check(pk and pk.get('count', 0) >= 5, 'модпак стал сборкой: %s, модов %s' % (pk and pk['name'], pk and pk.get('count')))
check(pk and os.path.isdir(os.path.join(folder, 'config')), 'настройки модпака (config) перенесены')
ups = ps.mod_updates(pk, log)
check(isinstance(ups, list), 'проверка обновлений модов: можно обновить %d %s' % (len(ups), ups[:3]))
# сервер пати
cfg = ps.server_prepare('1.21.1', None, None, log)
check(os.path.isfile(os.path.join(cfg['dir'], 'server.jar')) and cfg['java'], 'сервер 1.21.1 и Java скачаны, порт %d' % cfg['port'])
check(not ps.server_eula_ok(cfg['dir']), 'EULA не принята без игрока')
ps.server_accept_eula(cfg['dir'])
srv = ps.PartyServer(cfg, 1024)
t0 = time.time()
while not srv.ready and srv.alive() and time.time() - t0 < 180:
    time.sleep(1)
check(srv.ready, 'сервер запустился за %.0f с' % (time.time() - t0))
try:
    info = ps.ping_server('127.0.0.1', cfg['port'], 5)
    check(True, 'сервер отвечает: %s' % str(info)[:120])
except Exception as e:
    check(False, 'сервер не отвечает: %s' % e)
h = ps.host_setup(None, cfg['port'], '1.21.1')
cands, tl, pack = ps.parse_invite_full(h['code'])
check(tl == '1.21.1' and pack is None and cands, 'код приглашения на сервер: %s, версия %s' % (h['address'], tl))
srv.stop()
check(not srv.alive(), 'сервер выключен командой stop')
# облако миров (без сети: упаковать и восстановить)
data = ps.world_zip(os.path.join(cfg['dir'], 'world'))
check(len(data) > 1000, 'мир сервера упакован: %s' % ps.fmt_mb(len(data)))
zp = os.path.join(tmp, 'w.zip')
open(zp, 'wb').write(data)
os.makedirs(ps.SAVES, exist_ok=True)
a = ps.world_restore(zp, 'Мир пати', log)
b = ps.world_restore(zp, 'Мир пати', log)
check(os.path.isfile(os.path.join(a, 'level.dat')) and a != b, 'восстановлен дважды, второй рядом: %s' % os.path.basename(b))
cfg2 = ps.server_prepare('1.21.1', a, 'Мир пати', log)
olds = [x for x in os.listdir(cfg2['dir']) if x.startswith('world_old_')]
check(olds and os.path.isfile(os.path.join(cfg2['dir'], 'world', 'level.dat')), 'новый мир на сервере, прежний сохранён: %s' % olds)
print('ошибок:', fails, '| папка:', tmp)

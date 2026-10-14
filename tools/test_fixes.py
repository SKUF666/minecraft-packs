"""Проверки исправлений аудита 14.10.2026 (без сети и без настоящих данных пользователя).
python tools/test_fixes.py"""
import os, sys, io, json, zipfile, tempfile, importlib.machinery, importlib.util
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
sys.path.insert(0, LIB)
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
import portalis_voice
from PIL import Image
fails = []


def check(name, cond, info=''):
    print('%s  %s %s' % ('OK ' if cond else 'ОШИБКА', name, info))
    if not cond:
        fails.append(name)


tmp = tempfile.mkdtemp(prefix='portalis-fixes-')

# 1. пути из архивов
check('архив: обычный путь', ps.safe_rel('overrides/config/a.txt') == ['overrides', 'config', 'a.txt'])
for bad in ('D:/x/evil.dll', 'mods/D:evil.jar', '../x', 'a/../../b', '/abs', '\\\\server\\share\\x', 'C:\\Users\\x'):
    check('архив: опасный путь %r отсечён' % bad, ps.safe_rel(bad) is None)

# 2. откат мира: битая копия не трогает мир, опасное имя не принимается, хорошая копия встаёт, прежний мир рядом
ps.set_game_dir(os.path.join(tmp, '.minecraft'))
w = os.path.join(ps.SAVES, 'Мой мир')
os.makedirs(w)
open(os.path.join(w, 'level.dat'), 'wb').write(b'old')
bad_zip = os.path.join(tmp, 'bad.zip')
open(bad_zip, 'wb').write(b'PK\x03\x04 broken')
try:
    ps.world_rollback(bad_zip, 'Мой мир', log=lambda *a: None)
    check('откат: битая копия - ошибка', False)
except Exception:
    check('откат: битая копия - мир цел', open(os.path.join(w, 'level.dat'), 'rb').read() == b'old')
good_zip = os.path.join(tmp, 'good.zip')
with zipfile.ZipFile(good_zip, 'w') as z:
    z.writestr('level.dat', b'new')
    z.writestr('region/r.0.0.mca', b'r')
    z.writestr('../../evil.txt', b'x')
for evil in ('..\\..\\Startup', 'a/b', 'C:x', ''):
    try:
        ps.world_rollback(good_zip, evil, log=lambda *a: None)
        check('откат: имя %r отклонено' % evil, False)
    except RuntimeError:
        check('откат: имя %r отклонено' % evil, True)
kept = ps.world_rollback(good_zip, 'Мой мир', log=lambda *a: None)
check('откат: копия встала', open(os.path.join(w, 'level.dat'), 'rb').read() == b'new')
check('откат: прежний мир рядом', kept and open(os.path.join(kept, 'level.dat'), 'rb').read() == b'old')
check('откат: «../» из архива не вылез', not os.path.exists(os.path.join(tmp, 'evil.txt'))
      and not os.path.exists(os.path.join(ps.MC, 'evil.txt')))
check('откат: временных папок не осталось', not [x for x in os.listdir(ps.SAVES) if x.startswith('.portalis-')])

# 3. перетаскивание: плащ с элитрами - плащ, старый скин - скин
cape = Image.new('RGBA', (64, 32), (0, 0, 0, 0))
for x0, y0, x1, y1 in ((0, 0, 22, 17), (22, 0, 46, 22)):  # сам плащ и развёртка элитр, как у настоящих плащей
    for x in range(x0, x1):
        for y in range(y0, y1):
            cape.putpixel((x, y), (40, 60, 200, 255))
cp = os.path.join(tmp, 'migrator.png'); cape.save(cp)
check('плащ с элитрами распознан как плащ', ps.classify_drop(cp) == 'cape')
legacy = Image.new('RGBA', (64, 32), (0, 0, 0, 0))
for x in range(0, 64):
    for y in range(0, 32):
        legacy.putpixel((x, y), (200, 150, 100, 255))
lp_ = os.path.join(tmp, 'old.png'); legacy.save(lp_)
check('старый скин 64x32 распознан как скин', ps.classify_drop(lp_) == 'skin')

# 4. настройки: окно со старыми настройками не возвращает старый токен входа
ps.SETTINGS = os.path.join(tmp, '_settings.json')
ps.save_settings({'social': {'refresh_token': 'old'}, 'tab': 'maps'})
stale = ps.load_settings()
c = ps.Social.__new__(ps.Social)
c.session = {}
c._save({'access_token': 'a', 'refresh_token': 'NEW', 'expires_at': 1, 'user': {'id': 'u'}})
stale['tab'] = 'skins'
ps.save_settings(stale)
now = ps.load_settings()
check('настройки: свежий токен не затёрт', now.get('social', {}).get('refresh_token') == 'NEW', now.get('social'))
check('настройки: остальное сохранилось', now.get('tab') == 'skins')
check('настройки: нет временного файла', not os.path.exists(ps.SETTINGS + '.tmp'))
ps._SOCIAL_NOW[0] = None

# 5. NeoForge для двухчастных версий
vers = ['21.0.167', '21.1.77', '21.11.4', '26.1.0.3', '26.1.2.5']
ps._get = lambda url, *a, **k: json.dumps({'versions': vers}).encode()
check('NeoForge 1.21 -> 21.0.x', ps.loader_version('neoforge', '1.21') == '21.0.167')
check('NeoForge 1.21.1 -> 21.1.x', ps.loader_version('neoforge', '1.21.1') == '21.1.77')
check('NeoForge 26.1 -> 26.1.0.x', ps.loader_version('neoforge', '26.1') == '26.1.0.3')

# 6. обновления модов: бета новее релиза - не «обновление», которое всё равно не поставится
ps.mr_get = lambda path, params=None: [
    {'id': 'B', 'version_number': '1.1-beta', 'version_type': 'beta', 'date_published': '2026-10-02'},
    {'id': 'A', 'version_number': '1.0', 'version_type': 'release', 'date_published': '2026-09-01'}]
pack = {'constructor': {'gv': '1.21.1', 'loader': 'fabric'},
        'mr_files': {'mods/x.jar': 'https://cdn.modrinth.com/data/P1/versions/A/x.jar'}}
check('обновления модов: бету вместо релиза не предлагаем', ps.mod_updates(pack, log=lambda *a: None) == [])
ps.mr_get = lambda path, params=None: [
    {'id': 'C', 'version_number': '1.2', 'version_type': 'release', 'date_published': '2026-10-05'},
    {'id': 'A', 'version_number': '1.0', 'version_type': 'release', 'date_published': '2026-09-01'}]
check('обновления модов: новый релиз предлагаем', ps.mod_updates(pack, log=lambda *a: None) == [('x.jar', '1.0', '1.2')])

# 7. опись с GitHub старше своей - не откатываемся
root = os.path.join(tmp, 'lib')
os.makedirs(root)
json.dump({'version': '2026.10.14', 'items': [{'id': 'm1', 'kind': 'map', 'path': 'Карты/m1'}]},
          open(os.path.join(root, 'manifest.json'), 'w', encoding='utf-8'))
seen = {}
ps.plan_update = lambda remote, *a, **k: seen.setdefault('update', remote['version']) and {}
ps.plan_items = lambda remote, items, *a, **k: seen.setdefault('items', remote['version']) and {}
ps.plan_size = lambda *a: 0
ps.manifest_item = lambda man, i: (man.get('items') or [None])[0]
ps.plan_download({'version': '2026.10.13', 'items': []}, ['m1'], root=root, log=lambda *a: None)
check('старая опись: обновления (отката) нет', 'update' not in seen and seen.get('items') == '2026.10.14', seen)

# 8. список серверов: «host:25565» и «host» - один сервер
ps.add_server_entry('A', 'play.example.com:25565')
check('серверы: дубль с :25565 не добавляется', ps.add_server_entry('A', 'play.example.com') is False)

# 9. голос: «вошёл с другого компьютера» - без бесконечного переподключения
check('голос: код 4000 - остановка', portalis_voice.VoiceClient._close_code_error(None, (4000).to_bytes(2, 'big'))
      == portalis_voice.ERR_REPLACED)

print('Итог: ошибок %d' % len(fails))

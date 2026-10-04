"""Проверка работы с лаунчерами на временной папке (настоящую папку игры не трогает).
    python tools/test_launchers.py"""
import importlib.machinery, importlib.util, json, os, sys, tempfile, zipfile
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
tmp = tempfile.mkdtemp(prefix='kubo-test-')
ps.set_game_dir(os.path.join(tmp, '.minecraft'))
ps.save_settings = lambda d: None
fails = 0


def check(cond, text):
    global fails
    fails += not cond
    print(('OK   ' if cond else 'ОШИБКА ') + text)


# 1. Официальный лаунчер: профиль «kuboteka», свои профили не теряются.
os.makedirs(ps.MC)
json.dump({'profiles': {'mine': {'name': 'Мой', 'type': 'custom', 'lastVersionId': '1.21.8'}}, 'settings': {}, 'version': 3},
          open(os.path.join(ps.MC, 'launcher_profiles.json'), 'w', encoding='utf-8'))
ps.chosen_launcher = lambda: 'official'
ok, hint = ps.select_version('1.20.1', 'Побег от маньяка', lambda *a: None)
d = json.load(open(os.path.join(ps.MC, 'launcher_profiles.json'), encoding='utf-8'))
check(ok and d['profiles']['portalis']['lastVersionId'] == '1.20.1', 'профиль Portalis с версией 1.20.1')
check('mine' in d['profiles'], 'свой профиль пользователя сохранился')
check(d['profiles']['portalis']['icon'].startswith('data:image/png;base64,'), 'у профиля значок Portalis')
real_plan, real_apply = ps.plan_version, ps.apply_version
ps.plan_version = lambda gv, loader, lv=None, assets=True, log=None: {'gv': gv, 'loader': loader, 'lv': lv, 'id': '%s-forge-%s' % (gv, lv)}
ps.apply_version = lambda pl, log=None: pl['id']
ok, hint = ps.select_version('Forge 1.20.1', 'RPG Pack', lambda *a: None, '47.4.20')
d = json.load(open(os.path.join(ps.MC, 'launcher_profiles.json'), encoding='utf-8'))
check(d['profiles']['portalis']['lastVersionId'] == '1.20.1-forge-47.4.20', 'нет версии Forge - Portalis ставит её сам и выбирает: '
      + d['profiles']['portalis']['lastVersionId'])
ps.apply_version = lambda pl, log=None: (_ for _ in ()).throw(RuntimeError('нет интернета'))
ok, hint = ps.select_version('Forge 1.20.1', 'RPG Pack', lambda *a: None, '47.4.20')
check('Forge' in hint and 'установщик' in hint, 'не скачалось - подсказка поставить Forge')
ps.plan_version, ps.apply_version = real_plan, real_apply

# 2. Legacy Launcher: login.version в tl.properties, папка игры из minecraft.gamedir.
props = os.path.join(tmp, 'tl.properties')
open(props, 'w', encoding='utf-8').write('minecraft.gamedir=C\\:\\\\Games\\\\LL\\\\game\nlogin.version=1.12.2\n')
ps.LEGACY_PROPS[:] = [props]
ps.process_running = lambda *n: False
ps.chosen_launcher = lambda: 'legacy'
ps.load_settings = lambda: {}
check(ps.launcher_game_dir('legacy') == r'C:\Games\LL\game', 'папка игры Legacy Launcher из его настроек')
ok, hint = ps.select_version('1.21.8', 'Loot Box Time!', lambda *a: None)
check(ok and ps._props_read(props).get('login.version') == '1.21.8', 'версия выбрана в Legacy Launcher')
check(ps._props_read(props).get('minecraft.gamedir') == r'C:\Games\LL\game', 'папка игры в его настройках не испорчена')

# 3. Пакет .mrpack: формат, хеши, зависимости.
ps.set_game_dir(os.path.join(tmp, '.minecraft'))
maps = {m['id']: m for m in ps.find_maps()}
packs = ps.find_packs()
rpg = next(p for p in packs if p['name'].startswith('RPG'))
if not ps.missing_items(pack=rpg):
    out = ps.export_mrpack(None, rpg, lambda *a: None)
    z = zipfile.ZipFile(out)
    idx = json.loads(z.read('modrinth.index.json'))
    check(idx['dependencies'] == {'minecraft': '1.20.1', 'forge': '47.4.20'}, 'RPG Pack: Forge 47.4.20 на 1.20.1')
    check(all(len(f['hashes']['sha512']) == 128 and f['downloads'][0].startswith('https://cdn.modrinth.com/')
              for f in idx['files']), 'у всех модов по ссылкам есть sha512 и ссылка Modrinth')
    check(all(not f['path'].startswith(('/', '..')) and ':' not in f['path'] for f in idx['files']), 'пути внутри экземпляра')
    check(any(n.startswith('overrides/mods/twilightforest') for n in z.namelist()), 'мод с CurseForge лежит внутри пакета')
    check('overrides/servers.dat' in z.namelist(), 'серверы добавлены в пакет')
    z.close()
    os.remove(out)
m = maps['oneblock']
if not ps.missing_items(m):
    out = ps.export_mrpack(m, None, lambda *a: None)
    z = zipfile.ZipFile(out)
    idx = json.loads(z.read('modrinth.index.json'))
    check(idx['dependencies'] == {'minecraft': '26.1.2'}, 'карта без модов: только версия игры')
    check(any(n.startswith('overrides/saves/%s/level.dat' % m['save']) for n in z.namelist()), 'мир карты в saves')
    check(not any(n.endswith('session.lock') for n in z.namelist()), 'без session.lock')
    z.close()
    os.remove(out)
print('Итог: ошибок', fails)
sys.exit(1 if fails else 0)

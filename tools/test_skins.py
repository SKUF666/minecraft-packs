"""Проверка скинов на временной папке игры (настоящую не трогает).
    python tools/test_skins.py"""
import importlib.machinery, importlib.util, json, os, sys, tempfile, shutil
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
tmp = tempfile.mkdtemp(prefix='portalis-skins-')
ps.SKINS_DIR = os.path.join(tmp, 'Скины')
ps.set_game_dir(os.path.join(tmp, '.minecraft'))
settings = {}
ps.load_settings = lambda: dict(settings)
ps.save_settings = lambda d: settings.update(d)
fails = 0


def check(cond, text):
    global fails
    fails += not cond
    print(('OK   ' if cond else 'ОШИБКА ') + text)


found = ps.skin_lookup('Notch')
check(any(f['source'] == 'TLauncher' for f in found) and any('Mojang' in f['source'] for f in found),
      'Notch найден: ' + ', '.join(f['source'] for f in found))
check(ps.skin_lookup('zz_no_such_nick_123') == [], 'несуществующий ник: пусто, без ошибок')
fn = ps.save_my_skin('Notch (TLauncher)', found[0]['skin'], found[0]['slim'], found[0].get('cape'), 'TLauncher')
check(any(m['file'] == fn for m in ps.my_skins()), 'скин сохранён в «Мои скины»')
cape = ps.make_cape('#203080', '#ffd040', 'звёзды')
os.makedirs(os.path.join(ps.SKINS_DIR, 'Плащи'))
cp = os.path.join(ps.SKINS_DIR, 'Плащи', 'Звёздный.png')
open(cp, 'wb').write(cape)
from PIL import Image
import io
check(Image.open(io.BytesIO(cape)).size == (64, 32), 'плащ из мастера: 64x32')
# Надеть без интернета: файлы и настройка CustomSkinLoader
open(ps.MARKER if os.path.isdir(os.path.dirname(ps.MARKER)) else os.devnull, 'w').close()
os.makedirs(ps.MODS, exist_ok=True)
json.dump({'name': 'Удобства и шейдеры (21)', 'version_dir': 'Fabric 26.1.2'}, open(ps.MARKER, 'w', encoding='utf-8'))
logs = []
ps.install_offline_skin('Rafailka69', os.path.join(ps.SKINS_DIR, fn), cp, logs.append)
base = os.path.join(ps.MC, 'CustomSkinLoader')
check(os.path.isfile(os.path.join(base, 'LocalSkin', 'skins', 'Rafailka69.png')), 'скин лежит в LocalSkin/skins')
check(os.path.isfile(os.path.join(base, 'LocalSkin', 'capes', 'Rafailka69.png')), 'плащ лежит в LocalSkin/capes')
cfg = json.load(open(os.path.join(base, 'CustomSkinLoader.json'), encoding='utf-8'))
check(cfg['loadlist'][0]['name'] == 'LocalSkin', 'свои файлы первыми в списке загрузки')
check(os.path.isfile(os.path.join(ps.MODS, ps.CSL['name'])), 'мод CustomSkinLoader в mods: ' + '; '.join(logs[-2:]))
# Смена сборки: мод не уходит в «Несортированное» и возвращается
packs = ps.find_packs()
fab = next((p for p in packs if p['version_dir'] == 'Fabric 26.1.2' and not ps.missing_items(pack=p)), None)
if fab:
    ps.UNSORTED = os.path.join(tmp, '_Несортированное')
    ps.switch(fab, packs, False, lambda m: None)
    check(os.path.isfile(os.path.join(ps.MODS, ps.CSL['name'])), 'после включения сборки CustomSkinLoader на месте')
    check(not os.path.isdir(ps.UNSORTED), 'мод не уехал в «Несортированное»')
van = next((p for p in packs if p['version_dir'].startswith('Vanilla')), None)
logs = []
json.dump({'name': 'x', 'version_dir': 'Vanilla 26.1.2'}, open(ps.MARKER, 'w', encoding='utf-8'))
check(not ps.put_csl_in_mods(logs.append) and 'без модов' in ' '.join(logs), 'для ванильной сборки честно говорит, что нужен Fabric/Forge')
print('Итог: ошибок', fails)
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if fails else 0)

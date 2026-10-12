"""Живы ли сайты, откуда Portalis берёт карты, моды, скины и версии (тем же web_get, что программа: с запасным
путём через curl.exe Windows, если Cloudflare не пускает Питон). python tools/test_sites.py"""
import os, sys, time, urllib.parse, importlib.machinery, importlib.util
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
sys.path.insert(0, LIB)
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)

CHECKS = [  # (что, функция)
    ('minecraft-inside.ru: карты', lambda: len(ps.mi_list(1, None, None, 'popular', None)[0])),
    ('minecraftmaps.com: карты', lambda: len(ps.mm_list(1, None, None, 'popular', None)[0])),
    ('CurseForge: карты', lambda: len(ps.cf_maps(1, None, None, 'popular', None)[0]) if ps.cf_key() else 'нет ключа'),
    ('Modrinth: поиск модов', lambda: len(ps.web_get('https://api.modrinth.com/v2/search?limit=5', 20, 2))),
    ('Mojang: список версий', lambda: len(ps.web_get('https://piston-meta.mojang.com/mc/game/version_manifest_v2.json', 20, 2))),
    ('Fabric: версии', lambda: len(ps.web_get('https://meta.fabricmc.net/v2/versions/game', 20, 2))),
    ('Галерея скинов', lambda: len(ps.skin_gallery_page({'page': 1}, 'popular', None)[0])),
    ('Перевод описаний', lambda: ps.translate('Hello world', 'ru', cache_path=None)),
]
bad = 0
for name, fn in CHECKS:
    t = time.time()
    try:
        res = fn()
        ok = bool(res) and res != 0
    except Exception as e:
        res, ok = 'ошибка: %s' % str(e)[:80], False
    bad += not ok
    print('%s  %-28s %s  (%.1f с)' % ('OK ' if ok else 'ПЛОХО', name, res, time.time() - t))
print('через curl.exe Windows:', ', '.join(sorted(ps._CURL_HOSTS)) or 'никто')
print('Итог: не работает %d из %d' % (bad, len(CHECKS)))

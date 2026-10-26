"""Скачивание файлов карт: сам -> curl Windows -> сервер Portalis (map-proxy). Живые ссылки из выборки
tools/map_hosts.py (%TEMP%/map_hosts.json) - запусти её раньше. Проверяет и путь «напрямую не вышло -> через сервер»
(прямое скачивание и curl в этом случае подменяются ошибкой).

    python tools/test_map_fetch.py
"""
import os, sys, json, time, tempfile, importlib.machinery, importlib.util
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
sys.path.insert(0, LIB)
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
rows = json.load(open(os.path.join(os.environ.get('TEMP', '.'), 'map_hosts.json'), encoding='utf-8'))
tmp = tempfile.mkdtemp(prefix='portalis-mapfetch-')
bad = 0


def link(host, n=0):
    hits = [(res.split(' ', 1)[1], url) for src, url, h, res in rows if h == host and ' ' in res]
    return hits[n] if len(hits) > n else (None, None)


def check(name, url, ref, expect, relay_only=False):
    global bad
    if not url:
        print('SKIP', name)
        return
    dst = os.path.join(tmp, name + '.bin')
    logs, total = [], [0]
    f0, c0 = ps._fetch_to, ps.curl_fetch
    if relay_only:
        def no(*a, **k):
            raise RuntimeError('нарочно: напрямую нельзя')
        ps._fetch_to = ps.curl_fetch = no
    t = time.time()
    try:
        ok = ps.fetch_map_file(url, dst, ref, lambda n: total.__setitem__(0, total[0] + n), None, logs.append)
        res = 'archive' if ok else 'fail'
    except ps.FileGone:
        res = 'gone'
    finally:
        ps._fetch_to, ps.curl_fetch = f0, c0
    size = os.path.getsize(dst) if os.path.isfile(dst) else 0
    good = res == expect and (res != 'archive' or total[0] == size)
    bad += not good
    print('%s %-22s %-7s %6.1f МБ счётчик %6.1f МБ  %4.0f с  %s' % ('OK  ' if good else 'FAIL', name, res, size / 1048576,
                                                                 total[0] / 1048576, time.time() - t, ' | '.join(logs)[:200]))


check('minecraftmaps', *link('dl.minecraftmaps.com'), 'archive')
check('minecraft-inside', *link('minecraft-inside.ru'), 'archive')
check('mediafire', *link('mediafire.com', 1), 'archive')
check('yandex', *link('disk.yandex.ru'), 'archive')
check('yandex-dead', 'https://yadi.sk/d/-zFP6mwvSr-2ug', None, 'gone')
check('gdrive-amp', *link('drive.usercontent.google.com'), 'archive')
check('bauwks-http', 'http://www.bauwks.net/maps/Crack_in_the_World.zip', None, 'archive')
check('relay-gdrive', *link('drive.google.com'), 'archive', relay_only=True)
check('relay-mm', *link('dl.minecraftmaps.com', 3), 'archive', relay_only=True)
check('relay-mi-нельзя', *link('minecraft-inside.ru', 2), 'fail', relay_only=True)
print('Итог: ошибок', bad)

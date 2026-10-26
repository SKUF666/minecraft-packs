"""Где лежат файлы карт из интернета: выборка карт minecraft-inside и minecraftmaps (несколько страниц и разделов) ->
хостинги файлов, сколько из них программа качает сама (direct_link), а сколько - только через браузер.

    python tools/map_hosts.py [страниц=3]
"""
import os, sys, json, collections, urllib.parse, importlib.machinery, importlib.util
from concurrent.futures import ThreadPoolExecutor
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
sys.path.insert(0, LIB)
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
PAGES = int(sys.argv[1]) if len(sys.argv) > 1 else 3
items = []
for src, lister in (('mi', ps.mi_list), ('mm', ps.mm_list)):
    for sort in (None, 'visits'):
        for pg in range(1, PAGES + 1):
            try:
                got = lister(pg, None, None, sort, None)[0]
                items += [(src, x) for x in got]
            except Exception as e:
                print('список', src, sort, pg, 'ошибка:', str(e)[:100])
seen, uniq = set(), []
for src, x in items:
    if x['url'] not in seen:
        seen.add(x['url'])
        uniq.append((src, x))
print('карт в выборке:', len(uniq))


def one(sx):
    src, x = sx
    try:
        info = (ps.mi_page if src == 'mi' else ps.mm_page)(x['url'])
        dl, _rps = ps.mi_pick_download(info)
        if not dl:
            return src, x['url'], None, 'нет файла'
        host = urllib.parse.urlsplit(dl['url']).netloc.lower().replace('www.', '')
        return src, x['url'], host, ('сама' if ps.direct_link(dl['url']) else 'браузер') + ' ' + dl['url'][:110]
    except Exception as e:
        return src, x['url'], None, 'ошибка страницы: %s' % str(e)[:80]


hosts = collections.Counter()
how = collections.Counter()
rows = list(ThreadPoolExecutor(6).map(one, uniq))
for src, url, host, res in rows:
    hosts[(src, host)] += 1
    how[res.split(' ')[0]] += 1
print(dict(how))
for (src, h), n in hosts.most_common():
    print('%4d  %s  %s' % (n, src, h))
print('--- только через браузер / ошибки:')
for src, url, host, res in rows:
    if not res.startswith('сама'):
        print(' ', src, host, res[:160], '|', url)
json.dump(rows, open(os.path.join(os.environ.get('TEMP', '.'), 'map_hosts.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=0)

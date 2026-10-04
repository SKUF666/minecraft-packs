"""Карты из интернета: список, страница, установка во временную папку игры (настоящую не трогает).
    python tools/test_webmaps.py"""
import importlib.machinery, importlib.util, os, sys, tempfile, urllib.parse, time
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
tmp = tempfile.mkdtemp(prefix='portalis-webmaps-')
ps.ROOT = tmp
ps.set_game_dir(os.path.join(tmp, '.minecraft'))
assert ps.MC.startswith(tmp)
fails = 0


def check(cond, text):
    global fails
    fails += not cond
    print(('OK   ' if cond else 'ОШИБКА ') + text, flush=True)


lst, nxt = ps.mi_list(1)
check(len(lst) >= 8 and nxt and all(x['title'] and x['url'] for x in lst), 'каталог: %d карт на странице, есть следующая' % len(lst))
check(len(ps.mi_list(1, cat='horror')[0]) >= 8, 'раздел «Хоррор»')
check(len(ps.mi_list(1, version='1.20.1')[0]) >= 8, 'версия 1.20.1')
check(len(ps.mi_list(1, query='паркур')[0]) >= 3, 'поиск «паркур»')
want = {'minecraft-inside.ru': None, 'drive.google.com': None, 'disk.yandex.ru': None}
for page in range(1, 6):
    for x in ps.mi_list(page, sort='visits')[0]:
        info = ps.mi_page(x['url'])
        dl, _ = ps.mi_pick_download(info)
        if not dl or not ps.direct_link(dl['url']):
            continue
        host = urllib.parse.urlsplit(dl['url']).netloc.replace('www.', '')
        try:
            mb = float(dl['size'].split()[0].replace(',', '.')) * (1024 if 'ГБ' in dl['size'] else 1)
        except (ValueError, IndexError):
            mb = 50  # размер не указан
        if host in want and not want[host] and mb < 120:
            want[host] = info
    if all(want.values()):
        break
for host, info in want.items():
    if not info:
        print('нет подходящей карты с', host)
        continue
    t0 = time.time()
    try:
        gv, save = ps.install_web_map(info, log=lambda m: None)
        ok = os.path.isfile(os.path.join(ps.SAVES, save, 'level.dat'))
        check(ok, '%s: «%s» -> saves/%s, версия %s (%.0f с)' % (host, info['title'], save, gv, time.time() - t0))
    except Exception as e:
        check(False, '%s: «%s»: %s' % (host, info['title'], e))
check(len(ps.web_maps_installed()) >= 2, 'установленные карты запомнены')
print('Итог: ошибок', fails, '| папка', tmp)
sys.exit(1 if fails else 0)

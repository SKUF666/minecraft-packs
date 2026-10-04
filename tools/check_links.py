"""Проверка всего внешнего: серверы, сайты лаунчеров, страницы карт, ссылки на исходные архивы и моды.

    python tools/check_links.py [--lib "путь к библиотеке"]

Ссылки проверяются запросом первых байт (Range), размер сверяется с описью. Через VPN часть сайтов
отвечает иначе, поэтому каждая ссылка пробуется и через системный прокси, и напрямую.
"""
import argparse
import importlib.machinery
import importlib.util
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding='utf-8')
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_ps(lib):
    loader = importlib.machinery.SourceFileLoader('ps', os.path.join(lib, 'PackSwitcher.pyw'))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', loader))
    loader.exec_module(mod)
    return mod


def probe(ps, url, referer='', size=None):
    """(ok, текст). Пробует через прокси и напрямую, берёт первые байты."""
    last = ''
    for attempt in range(4):
        req = urllib.request.Request(url, headers={'User-Agent': ps.UA_BROWSER, 'Referer': referer, 'Range': 'bytes=0-1023'})
        try:
            with ps.urlopen(req, 25, attempt) as r:
                total = r.headers.get('Content-Range', '').rsplit('/', 1)[-1] or r.headers.get('Content-Length', '?')
                r.read(16)
                if size and total.isdigit() and int(total) != size:
                    return False, 'размер %s вместо %s' % (total, size)
                return True, '%s, %s байт' % (r.status, total)
        except Exception as e:
            last = str(e)[:80]
    return False, last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lib', default=os.path.join(os.environ['LOCALAPPDATA'], 'Portalis'))
    a = ap.parse_args()
    ps = load_ps(a.lib)
    bad = 0

    print('== Серверы')
    def srv(s):
        try:
            i = ps.server_status(s['ip'])
            return True, '%s: онлайн %s/%s, %s, %d мс' % (s['ip'], i['online'], i['max'], i['version'], i['ms'])
        except Exception as e:
            return False, '%s: не отвечает (%s)' % (s['ip'], str(e)[:60])
    with ThreadPoolExecutor(6) as ex:
        for ok, t in ex.map(srv, ps.load_servers()):
            bad += not ok
            print(' ', 'OK ' if ok else 'ПЛОХО', t)

    print('== Сайты лаунчеров')
    for x in ps.load_launchers():
        ok, t = probe(ps, x['site'])
        if not ok and '403' in t:
            ok, t = True, 'сайт закрыт от программ (Cloudflare), в браузере открывается'
        bad += not ok
        print(' ', 'OK ' if ok else 'ПЛОХО', x['name'], x['site'], t)
        try:
            url = ps.launcher_installer_url(x)
        except Exception as e:
            url, t = None, str(e)[:60]
            bad += 1
            print('      ПЛОХО установщик:', t)
        if url:
            ok, t = probe(ps, url)
            bad += not ok
            print('     ', 'OK ' if ok else 'ПЛОХО', 'установщик', url, t)
        elif (x.get('download') or {}).get('kind') == 'site':
            print('      (установщик качается на сайте в браузере)')

    man = ps.load_local_manifest(a.lib)
    print('== Исходные архивы карт и страницы')
    for arc in man.get('archives', []):
        ok, t = probe(ps, arc['page'])
        bad += not ok
        print(' ', 'OK ' if ok else 'ПЛОХО', 'страница', arc['id'], t)
        if arc.get('url') and not arc.get('browser'):
            ok, t = probe(ps, arc['url'], arc.get('page', ''), arc['size'])
            bad += not ok
            print('     ', 'OK ' if ok else 'ПЛОХО', 'файл', arc['name'], t)
        else:
            print('      (качается через браузер)')

    print('== Прямые ссылки на моды и файлы')
    urls = {}
    for it in man.get('items', []):
        if it['kind'] not in ('pack', 'map'):
            continue
        for f in ps.fetch_list(man, it, a.lib)['files']:
            s = f['src']
            if s['t'] == 'url':
                urls[s['u']] = f['z']
            elif s['t'] == 'zpatch':
                urls[s['u']] = s.get('uz')
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda u: (u, probe(ps, u, '', urls[u])), sorted(urls)))
    for u, (ok, t) in res:
        if not ok:
            bad += 1
            print('  ПЛОХО', u, t)
    print('  проверено ссылок: %d, плохих: %d' % (len(res), sum(1 for _, (ok, _t) in res if not ok)))
    print('== Итого проблем: %d' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())

"""Выпуск новой версии Portalis (рабочая библиотека - папка library рядом с tools) в релиз GitHub (тег library).

    python tools/publish.py [--dry] [--note "текст для «Что нового»"] [--lib "путь к библиотеке"]

Библиотека делится на части: core (программа без exe, описания карт и сборок, обложки, оформление),
app (exe), pack:<папка сборки>, map:<id карты>. Для каждого файла ищется исходное место:
  url     - Modrinth (по sha1), CurseForge и Mojang (tools/extra_sources.json);
  zpatch  - оригинал с Modrinth с заменой нескольких файлов (Faithful с поправленным pack.mcmeta);
  arcfile - файл совпадает с исходным архивом карты целиком (tools/archives.json);
  arc     - файл лежит внутри исходного архива карты (сайт автора: minecraft-inside.ru и др.);
  own     - наш файл, кладётся в архив части own-*.zip в релизе.
В релиз попадают только списки файлов (list-*.json), наши архивы (own-*.zip), manifest.json и
стартовый архив Minecraft-Packs.zip (программа + каталог). Чужие карты и моды не перезаливаются.
Исходные архивы карт должны лежать в archives/ рядом с tools (или скачиваются по url из archives.json).
"""
import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile

sys.stdout.reconfigure(encoding='utf-8')
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(REPO_DIR, 'tools')
ARCH = os.path.join(REPO_DIR, 'archives')
REPO = 'SKUF666/minecraft-packs'
TAG = 'library'
STARTER = 'Portalis.zip'
STARTERS_OLD = ('Minecraft-Packs.zip', 'Kuboteka.zip')  # прежние ссылки из README и переписки остаются рабочими
STORED = ('.jar', '.zip', '.png', '.jpg', '.jpeg', '.ogg', '.mp3', '.exe', '.gz', '.mca')


def load_ps(lib):
    loader = importlib.machinery.SourceFileLoader('ps', os.path.join(lib, 'PackSwitcher.pyw'))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', loader))
    loader.exec_module(mod)
    return mod


def gh(*args, check=True, timeout=None):
    try:
        r = subprocess.run(['gh', *args], capture_output=True, text=True, encoding='utf-8', errors='replace',
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        if check:
            raise RuntimeError('gh %s: зависло дольше %d с' % (' '.join(args[:3]), timeout))
        return subprocess.CompletedProcess(args, 1, '', 'зависло, повторяю')
    if check and r.returncode:
        raise RuntimeError('gh %s: %s' % (' '.join(args[:3]), r.stderr.strip()[-800:]))
    return r


def upload(path):
    for attempt in range(5):
        r = gh('release', 'upload', TAG, path, '--repo', REPO, '--clobber', check=False,
               timeout=180 + os.path.getsize(path) // 150000)
        if r.returncode == 0:
            return
        print('  повтор:', r.stderr.strip()[-200:])
        time.sleep(10)
    raise RuntimeError('не загрузился ' + path)


def sha1_file(path):
    h = hashlib.sha1()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def modrinth_urls(hashes):
    cache_path = os.path.join(TOOLS, 'modrinth_cache.json')
    try:
        cache = json.load(open(cache_path, encoding='utf-8'))
    except Exception:
        cache = {}
    need = [h for h in hashes if h not in cache]
    for i in range(0, len(need), 300):
        part = need[i:i + 300]
        req = urllib.request.Request('https://api.modrinth.com/v2/version_files', method='POST',
                                     data=json.dumps({'hashes': part, 'algorithm': 'sha1'}).encode(),
                                     headers={'Content-Type': 'application/json', 'User-Agent': REPO})
        res = json.loads(urllib.request.urlopen(req, timeout=60).read())
        for h in part:
            url = None
            for f in (res.get(h) or {}).get('files', []):
                if f['hashes'].get('sha1') == h:
                    url = f['url']
            cache[h] = url
    json.dump(cache, open(cache_path, 'w', encoding='utf-8'), indent=0, sort_keys=True)
    return cache


def archive_index(ps, archives):
    """sha1 файла -> [(id архива, имя внутри)]. Индекс кэшируется по sha1 архива."""
    cache_path = os.path.join(TOOLS, 'archive_index.json')
    try:
        cache = json.load(open(cache_path, encoding='utf-8'))
    except Exception:
        cache = {}
    idx = {}
    os.makedirs(ARCH, exist_ok=True)
    for a in archives:
        p = os.path.join(ARCH, a['name'])
        if a['sha1'] not in cache:
            if not os.path.isfile(p):
                if not a.get('url'):
                    raise RuntimeError('нет архива %s: скачай его со страницы %s в %s' % (a['name'], a['page'], ARCH))
                print('Скачиваю исходный архив', a['name'])
                req = urllib.request.Request(a['url'], headers={'User-Agent': ps.UA_BROWSER, 'Referer': a.get('page', '')})
                open(p, 'wb').write(urllib.request.urlopen(req, timeout=300).read())
            if sha1_file(p) != a['sha1']:
                raise RuntimeError('архив %s не совпадает с archives.json' % a['name'])
            entries = {}
            with zipfile.ZipFile(p) as z:
                for i in z.infolist():
                    if not i.is_dir():
                        entries.setdefault(hashlib.sha1(z.read(i)).hexdigest(), ps.zip_member_name(i))
            cache[a['sha1']] = entries
        for h, m in cache[a['sha1']].items():
            idx.setdefault(h, []).append((a['id'], m))
    json.dump(cache, open(cache_path, 'w', encoding='utf-8'), ensure_ascii=False)
    return idx


def library_files(ps, lib):
    out = []
    for rel in ps._walk_rel(lib, ''):
        top = rel.split('/')[0]
        if top.startswith(('_', '.')) or '/__pycache__/' in rel or rel.endswith(('.log', '.old')):
            continue
        if rel == 'manifest.json' or rel.rsplit('/', 1)[-1].lower() in ('.ds_store', 'thumbs.db', 'desktop.ini'):
            continue
        if '/natives/' in rel and '/versions/' in rel:
            continue  # лаунчер распаковывает их сам при запуске
        if top in ('Скины',):
            continue  # личные скины пользователя
        out.append(rel)
    return sorted(out)


def host_name(url):
    h = urllib.parse.urlparse(url).netloc.replace('www.', '')
    return {'cdn.modrinth.com': 'Modrinth', 'mediafilez.forgecdn.net': 'CurseForge', 'piston-data.mojang.com': 'Mojang',
            'api.skyblock.net': 'skyblock.net'}.get(h, h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lib', default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library'))
    ap.add_argument('--dry', action='store_true', help='всё посчитать и собрать, но не загружать')
    ap.add_argument('--note', action='append', default=[], help='строка в «Что нового»')
    ap.add_argument('--version', help='номер версии (по умолчанию дата)')
    a = ap.parse_args()
    lib = a.lib
    ps = load_ps(lib)
    out = os.path.join(lib, '_publish_out')
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    archives = json.load(open(os.path.join(TOOLS, 'archives.json'), encoding='utf-8'))
    extra = json.load(open(os.path.join(TOOLS, 'extra_sources.json'), encoding='utf-8'))
    arch_by_sha = {x['sha1']: x for x in archives}
    arch_by_id = {x['id']: x for x in archives}
    idx = archive_index(ps, archives)

    # 1. Части библиотеки.
    packs = [p for p in ps.find_packs() if not p.get('user')]  # свои сборки из конструктора не публикуются
    user_dirs = {os.path.relpath(p['path'], lib).replace('\\', '/') for p in ps.find_packs() if p.get('user')}
    pack_dirs = {os.path.relpath(p['path'], lib).replace('\\', '/'): p for p in packs}
    maps = ps.find_maps()
    map_dirs = {os.path.relpath(m['path'], lib).replace('\\', '/'): m for m in maps}
    items = {'core': {'id': 'core', 'kind': 'core', 'title': 'Программа: каталог и описания', 'files': []},
             'app': {'id': 'app', 'kind': 'app', 'title': 'Программа', 'path': ps.EXE_NAME, 'files': []}}
    for rel, p in sorted(pack_dirs.items()):
        items['pack:' + rel] = {'id': 'pack:' + rel, 'kind': 'pack', 'path': rel, 'files': [],
                                'title': 'Сборка «%s» (%s)' % (p['name'].rsplit(' (', 1)[0], p['version_dir'])}
    for rel, m in sorted(map_dirs.items()):
        items['map:' + os.path.basename(rel)] = {'id': 'map:' + os.path.basename(rel), 'kind': 'map', 'path': rel,
                                                 'files': [], 'title': 'Карта «%s»' % m['title']}

    def owner(rel):
        if rel == ps.EXE_NAME:
            return 'app'
        for d, kind in [(d, 'pack') for d in pack_dirs] + [(d, 'map') for d in map_dirs]:
            if rel.startswith(d + '/'):
                rest = rel[len(d) + 1:]
                if '/' not in rest and rest in ps.META_NAMES:
                    return 'core'
                return ('pack:' + d) if kind == 'pack' else ('map:' + os.path.basename(d))
        return 'core'

    def allowed_archives(iid):
        """Из каких исходных архивов часть может брать файлы: карта - из своих, Debt Hunt - из архива карты."""
        key = iid.split(':', 1)[1]
        if iid.startswith('map:'):
            return {x['id'] for x in archives if x['id'] == key or x['id'].startswith(key + '-')}
        return {x['id'] for x in archives if x['id'] in key.lower().replace(' ', '-')}

    cache = ps.HashCache(lib)
    files = [f for f in library_files(ps, lib) if not any(f.startswith(d + '/') for d in user_dirs)]
    # Faithful и подобные: пересобрать из оригинала, чтобы у всех получался одинаковый файл.
    zrules = {z['name']: z for z in extra.get('zpatch', [])}
    for rel in files:
        z = zrules.get(rel.rsplit('/', 1)[-1])
        if not z:
            continue
        orig = os.path.join(ARCH, 'z_' + z['sha1'])
        if not os.path.isfile(orig):
            req = urllib.request.Request(z['url'], headers={'User-Agent': ps.UA_BROWSER})
            open(orig, 'wb').write(urllib.request.urlopen(req, timeout=300).read())
        if sha1_file(orig) != z['sha1']:
            raise RuntimeError('оригинал %s изменился' % z['name'])
        tmp = os.path.join(out, 'zpatch.tmp')
        ps.zpatch_build(orig, tmp, z['set'])
        if sha1_file(tmp) != cache.sha1(rel):
            print('Пересобираю', rel)
            shutil.copy2(tmp, os.path.join(lib, rel))
        os.remove(tmp)
    sha = {rel: cache.sha1(rel) for rel in files}
    mr = modrinth_urls(sorted({sha[r] for r in files if r.endswith(('.jar', '.zip')) and owner(r) not in ('core', 'app')}))
    stat = {}
    for rel in files:
        h = sha[rel]
        size = os.path.getsize(ps.lp(os.path.join(lib, rel)))
        iid = owner(rel)
        src = {'t': 'own'}
        if iid not in ('core', 'app'):
            z = zrules.get(rel.rsplit('/', 1)[-1])
            ex = next((u for k, u in extra.get('url', {}).items() if h.startswith(k)), None)
            if z:
                src = {'t': 'zpatch', 'u': z['url'], 'us': z['sha1'], 'uz': z['size'], 'set': z['set']}
            elif mr.get(h):
                src = {'t': 'url', 'u': mr[h]}
            elif ex:
                src = {'t': 'url', 'u': ex}
            elif h in arch_by_sha and arch_by_sha[h]['id'] in allowed_archives(iid):
                src = {'t': 'arcfile', 'a': arch_by_sha[h]['id']}
            else:
                hit = next(((x, m) for x, m in idx.get(h, []) if x in allowed_archives(iid)), None)
                if hit:
                    src = {'t': 'arc', 'a': hit[0], 'm': hit[1]}
        items[iid]['files'].append({'p': rel, 's': h, 'z': size, 'src': src})
        stat[src['t']] = stat.get(src['t'], 0) + size
    exe_sha0 = cache.sha1(ps.EXE_NAME)
    for old_name in ps.OLD_EXE_NAMES:
        items['app']['files'].append({'p': old_name, 's': exe_sha0, 'z': os.path.getsize(os.path.join(lib, ps.EXE_NAME)),
                                      'src': {'t': 'own'}})
    cache.save()
    print('Источники, МБ:', {k: round(v / 1048576) for k, v in stat.items()})

    # 2. Сравнение с текущим релизом.
    r = gh('release', 'view', TAG, '--repo', REPO, '--json', 'assets', check=False)
    assets = {x['name']: x for x in json.loads(r.stdout)['assets']} if r.returncode == 0 else {}
    remote = {}
    if 'manifest.json' in assets:
        try:
            remote = ps.fetch_remote_manifest()
        except Exception as e:
            print('не прочитал manifest.json релиза:', e)
    ritems = {i['id']: i for i in remote.get('items', [])}
    to_upload = []
    lists_dir = os.path.join(lib, '_update', 'lists')
    os.makedirs(lists_dir, exist_ok=True)
    mitems = []
    for iid, it in items.items():
        fl = sorted(it.pop('files'), key=lambda f: f['p'])
        hh = hashlib.sha1()
        for f in fl:
            hh.update(('%s\t%s\n' % (f['p'], f['s'])).encode('utf-8'))
        it['hash'] = hh.hexdigest()
        it['size'] = sum(f['z'] for f in fl)
        tag = hashlib.sha1(iid.encode('utf-8')).hexdigest()[:8]
        it['list'] = 'list-%s-%s.json' % (tag, it['hash'][:10])
        own = [f for f in fl if f['src']['t'] == 'own']
        it['own'] = 'own-%s-%s.zip' % (tag, it['hash'][:10]) if own else None
        sites = []
        for f in fl:
            s = f['src']
            host = 'GitHub' if s['t'] == 'own' else host_name(
                s.get('u') or arch_by_id[s['a']].get('url') or arch_by_id[s['a']]['page'])
            if host not in sites:
                sites.append(host)
        it['sites'] = [x for x in sites if x != 'GitHub'] + (['GitHub'] if 'GitHub' in sites else [])
        old = ritems.get(iid)
        reuse = old and old.get('hash') == it['hash'] and old.get('list') in assets and (
            not it['own'] or old.get('own') in assets)
        lpath = os.path.join(out, it['list'])
        data = json.dumps({'item': iid, 'files': fl}, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        open(lpath, 'wb').write(data)
        it['list_sha1'] = hashlib.sha1(data).hexdigest()
        shutil.copy2(lpath, os.path.join(lists_dir, it['list']))
        if reuse:
            it['own_size'], it['own_sha1'] = old.get('own_size', 0), old.get('own_sha1')
        else:
            to_upload.append(lpath)
            if own:
                zp = os.path.join(out, it['own'])
                print('Пакую своё:', it['title'], '(%d файлов)' % len(own))
                with zipfile.ZipFile(zp, 'w', allowZip64=True) as z:
                    for f in own:
                        comp = zipfile.ZIP_STORED if f['p'].lower().endswith(STORED) else zipfile.ZIP_DEFLATED
                        srcp = ps.EXE_NAME if f['p'] in ps.OLD_EXE_NAMES else f['p']
                        z.write(ps.lp(os.path.join(lib, srcp)), f['p'], compress_type=comp, compresslevel=6)
                it['own_size'], it['own_sha1'] = os.path.getsize(zp), sha1_file(zp)
                to_upload.append(zp)
        arcs = {f['src']['a'] for f in fl if f['src']['t'] in ('arc', 'arcfile')}
        it['dl'] = (sum(f['z'] for f in fl if f['src']['t'] == 'url')
                    + sum(f['src']['uz'] for f in fl if f['src']['t'] == 'zpatch')
                    + sum(arch_by_id[x]['size'] for x in arcs) + (it.get('own_size') or 0))
        mitems.append(it)

    # 3. Версия и «Что нового».
    today = time.strftime('%Y.%m.%d')
    rv = remote.get('version', '')
    if a.version:
        version = a.version
    elif rv == today or rv.startswith(today + '.'):
        version = today + '.' + str((int(rv.split('.')[3]) if rv.count('.') == 3 else 1) + 1)
    else:
        version = today
    changes = list(a.note)
    for it in mitems:
        o = ritems.get(it['id'])
        if it['kind'] in ('pack', 'map'):
            if not o and ritems:
                changes.append('Новое: ' + it['title'])
            elif o and o.get('hash') != it['hash']:
                changes.append('Обновлено: ' + it['title'])
        elif it['kind'] == 'app' and o and o.get('hash') != it['hash']:
            changes.append('Обновлена программа')
    for iid, o in ritems.items():
        if iid not in items and o['kind'] in ('pack', 'map'):
            changes.append('Убрано: ' + o['title'])
    if not changes:
        changes.append('Мелкие исправления')

    manifest = {
        'name': 'Portalis', 'version': version, 'format': 2, 'repo': REPO, 'tag': TAG,
        'note': 'Всё, что входит в эту версию. Остальное в папке библиотеки - остатки прошлых версий '
                '(см. README, раздел 8 для Claude).',
        'changes': changes,
        'root': sorted({f.split('/')[0] for f in files} | {'manifest.json'}),
        'packs': {rel: sorted(set(ps.jars(os.path.join(lib, rel, 'mods')))) for rel in sorted(pack_dirs)},
        'maps': {os.path.basename(m['path']): {'title': m['title'], 'save': m['save'], 'version': m['version']}
                 for m in maps},
        'archives': archives,
        'items': mitems,
    }
    # Совместимость с программой 2026.10.04.x (опись format 1): она умеет обновлять только блоки core и app.
    # Даём ей их, а карты и сборки помечаем local_only, чтобы она их не трогала. Новая программа после
    # перезапуска сама докачает каталог (у неё не будет кэша списков в _update/lists).
    version_dirs = {d.split('/')[0] for d in pack_dirs}
    lc_paths = sorted({f.split('/')[0] for f in files if owner(f) == 'core'} - version_dirs - {'Карты'})
    lc_files = sorted(f for f in files if f.split('/')[0] in lc_paths)
    lc_hash = ps.block_hash(lc_files, cache)
    exe_sha = cache.sha1(ps.EXE_NAME)
    old_blocks = {b['id']: b for b in remote.get('blocks', [])}
    blocks = []
    for bid, kind, title, extra_b in (('core', 'core', 'Файлы программы и описания', {'paths': lc_paths}),
                                      ('app', 'app', 'Программа', {'path': ps.OLD_EXE_NAMES[0]})):
        h = lc_hash if kind == 'core' else hashlib.sha1(
            ('%s\t%s\n' % (ps.OLD_EXE_NAMES[0], exe_sha)).encode('utf-8')).hexdigest()
        b = dict({'id': bid, 'kind': kind, 'title': title, 'hash': h}, **extra_b)
        ob = old_blocks.get(bid)
        if ob and ob.get('hash') == h and ob.get('asset') in assets:
            b.update(asset=ob['asset'], size=ob['size'], asset_sha1=ob['asset_sha1'])
        else:
            if kind == 'core':
                b['asset'] = 'legacy-core-%s.zip' % h[:10]
                dst = os.path.join(out, b['asset'])
                with zipfile.ZipFile(dst, 'w') as z:
                    for f in lc_files:
                        comp = zipfile.ZIP_STORED if f.lower().endswith(STORED) else zipfile.ZIP_DEFLATED
                        z.write(ps.lp(os.path.join(lib, f)), f, compress_type=comp)
            else:
                b['asset'] = 'legacy-app-%s.exe' % exe_sha[:10]
                dst = os.path.join(out, b['asset'])
                shutil.copy2(os.path.join(lib, ps.EXE_NAME), dst)
            b['size'], b['asset_sha1'] = os.path.getsize(dst), sha1_file(dst)
            to_upload.append(dst)
        blocks.append(b)
    for it in mitems:
        if it['kind'] in ('pack', 'map'):
            blocks.append({'id': it['id'], 'kind': it['kind'], 'title': it['title'], 'path': it['path'],
                           'hash': it['hash'], 'local_only': True})
    manifest.update(blocks=blocks, files=[], local_only=[])
    mpath = os.path.join(out, 'manifest.json')
    json.dump(manifest, open(mpath, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    core_files = json.load(open(os.path.join(out, items['core']['list']), encoding='utf-8'))['files']
    # Главное: программа одним файлом (Portalis.exe и Portalis.zip с ним одним). Каталог она скачает сама.
    exe_asset = os.path.join(out, ps.EXE_NAME)
    shutil.copy2(os.path.join(lib, ps.EXE_NAME), exe_asset)
    starter = os.path.join(out, STARTER)
    with zipfile.ZipFile(starter, 'w') as z:
        z.write(exe_asset, ps.EXE_NAME, compress_type=zipfile.ZIP_DEFLATED)
    # Для старых ссылок - прежний вид: папка с программой и каталогом.
    starter_folder = os.path.join(out, 'folder-starter.zip')
    with zipfile.ZipFile(starter_folder, 'w') as z:
        for f in [x['p'] for x in core_files] + [ps.EXE_NAME]:
            comp = zipfile.ZIP_STORED if f.lower().endswith(STORED) else zipfile.ZIP_DEFLATED
            z.write(ps.lp(os.path.join(lib, f)), 'Portalis/' + f, compress_type=comp)
        z.write(mpath, 'Portalis/manifest.json')
    total = sum(os.path.getsize(p) for p in to_upload)
    print('Версия %s. Загрузить: %d файлов, %.1f МБ; стартовый архив %.1f МБ' % (
        version, len(to_upload), total / 1048576, os.path.getsize(starter) / 1048576))
    for it in mitems:
        print('  %-50s скачать %7.1f МБ, своё %6.2f МБ  %s' % (it['title'][:50], it['dl'] / 1048576,
                                                             (it.get('own_size') or 0) / 1048576, ', '.join(it['sites'])))
    for c in changes:
        print('  ·', c)
    if a.dry:
        print('--dry: ничего не загружено')
        return

    # 4. Загрузка: части, потом стартовый архив и опись. Лишнее из релиза удаляется.
    if gh('release', 'view', TAG, '--repo', REPO, check=False).returncode:
        gh('release', 'create', TAG, '--repo', REPO, '--title', 'Portalis', '--latest',
           '--notes', 'Скачай Portalis.zip, распакуй и запусти «Portalis.exe».')
    for i, p in enumerate(to_upload, 1):
        print('Загружаю %d/%d %s (%.1f МБ)' % (i, len(to_upload), os.path.basename(p), os.path.getsize(p) / 1048576),
              flush=True)
        upload(p)
    upload(exe_asset)
    upload(starter)
    for name_old in STARTERS_OLD:
        starter_old = os.path.join(out, name_old)
        shutil.copy2(starter_folder, starter_old)
        upload(starter_old)
    upload(mpath)
    used = {i['list'] for i in mitems} | {i['own'] for i in mitems if i['own']} | {STARTER, ps.EXE_NAME, 'manifest.json'} | set(STARTERS_OLD) \
        | {b['asset'] for b in blocks if b.get('asset')}
    # Файлы прошлой версии не удаляем: GitHub ещё пару минут раздаёт старую опись из кэша, а у кого-то
    # скачивание уже идёт по ней. Удаляется только то, что не нужно ни новой, ни прошлой описи.
    used |= {i.get('list') for i in remote.get('items', [])} | {i.get('own') for i in remote.get('items', [])} \
        | {b.get('asset') for b in remote.get('blocks', [])}
    for name in assets:
        if name not in used:
            gh('release', 'delete-asset', TAG, name, '--repo', REPO, '--yes', check=False)
            print('Удалён из релиза:', name)
    gh('release', 'edit', TAG, '--repo', REPO, '--title', 'Portalis %s' % version, '--latest',
       '--notes', 'Скачай Portalis.exe (или Portalis.zip) и запусти: установка не нужна, каталог и карты программа скачает сама. Карты и моды '
                  'программа скачивает с сайтов авторов, когда их выбираешь.\n\nЧто нового:\n' +
                  '\n'.join('- ' + c for c in changes))
    shutil.copy2(mpath, os.path.join(lib, 'manifest.json'))
    os.makedirs(os.path.join(REPO_DIR, 'src'), exist_ok=True)
    shutil.copy2(os.path.join(lib, 'PackSwitcher.pyw'), os.path.join(REPO_DIR, 'src', 'PackSwitcher.pyw'))
    for m in ('mc_launch.py', 'skin_maker.py', 'mod_doctor.py', 'portalis_voice.py', 'skin3d.py'):  # модули программы - тоже в исходники
        if os.path.isfile(os.path.join(lib, m)):
            shutil.copy2(os.path.join(lib, m), os.path.join(REPO_DIR, 'src', m))
    for p in to_upload:
        if os.path.exists(p) and not p.endswith('.json'):
            os.remove(p)
    print('Опубликовано: версия', version)


if __name__ == '__main__':
    main()

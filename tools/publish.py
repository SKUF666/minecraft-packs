"""Публикация библиотеки «Minecraft Packs» в релиз GitHub, откуда программа берёт обновления.

    python tools/publish.py [--dry] [--note "текст для «Что нового»"] [--lib "путь к библиотеке"]

Что делает:
  1. Делит библиотеку на блоки: core (файлы в корне), app (exe), по блоку на каждую сборку и карту.
  2. Моды, которые есть на Modrinth (по sha1), в блоки не кладёт: программа качает их прямо с Modrinth.
  3. Сравнивает хеши блоков с manifest.json текущего релиза и пакует/загружает только изменённые.
  4. Загружает новый manifest.json (последним), стартовый архив Minecraft-Packs.zip, удаляет лишние ассеты.
  5. Пишет manifest.json в библиотеку (теперь она считается «последней версией»), копирует исходник в src/.
Пути и файлы из _publish.json -> local_only никуда не загружаются (карта, которую автор запретил
перезаливать, OptiFine). У тех, у кого они уже есть, программа их не трогает.
"""
import argparse
import glob
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

sys.stdout.reconfigure(encoding='utf-8')
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = 'SKUF666/minecraft-packs'
TAG = 'library'
STARTER = 'Minecraft-Packs.zip'
STORED = ('.jar', '.zip', '.png', '.jpg', '.jpeg', '.ogg', '.mp3', '.exe', '.gz')


def load_ps(lib):
    loader = importlib.machinery.SourceFileLoader('ps', os.path.join(lib, 'PackSwitcher.pyw'))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', loader))
    loader.exec_module(mod)
    return mod


def gh(*args, check=True):
    r = subprocess.run(['gh', *args], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if check and r.returncode:
        raise RuntimeError('gh %s: %s' % (' '.join(args[:3]), r.stderr.strip()[-800:]))
    return r


def modrinth_urls(hashes, cache_path):
    try:
        cache = json.load(open(cache_path, encoding='utf-8'))
    except Exception:
        cache = {}
    need = [h for h in hashes if h not in cache]
    for i in range(0, len(need), 200):
        part = need[i:i + 200]
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


def sha1_file(path):
    h = hashlib.sha1()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lib', default=os.path.join(os.environ['USERPROFILE'], 'Desktop', 'Minecraft Packs'))
    ap.add_argument('--dry', action='store_true', help='всё посчитать и собрать, но не загружать')
    ap.add_argument('--note', action='append', default=[], help='строка в «Что нового»')
    ap.add_argument('--version', help='номер версии (по умолчанию дата)')
    a = ap.parse_args()
    lib = a.lib
    ps = load_ps(lib)
    cfg = json.load(open(os.path.join(lib, '_publish.json'), encoding='utf-8'))
    local_only = [p.replace('\\', '/') for p in cfg.get('local_only', [])]
    out = os.path.join(lib, '_publish_out')
    os.makedirs(out, exist_ok=True)
    cache = ps.HashCache(lib)

    def is_local_only(rel):
        return any(rel == p or rel.startswith(p + '/') for p in local_only)

    # 1. Блоки.
    packs = ps.find_packs()
    pack_dirs = {os.path.relpath(p['path'], lib).replace('\\', '/'): p for p in packs}
    version_dirs = {d.split('/')[0] for d in pack_dirs}
    maps = ps.find_maps()
    core_paths = sorted(f for f in os.listdir(lib)
                        if not f.startswith(('_', '.')) and f not in version_dirs and f not in (
                            'Карты', ps.EXE_NAME, 'manifest.json', 'switcher_cli.log', '__pycache__')
                        and not f.endswith('.old'))
    blocks = [{'id': 'core', 'kind': 'core', 'title': 'Файлы программы и описания', 'paths': core_paths},
              {'id': 'app', 'kind': 'app', 'title': 'Программа', 'path': ps.EXE_NAME}]
    for rel, p in sorted(pack_dirs.items()):
        blocks.append({'id': 'pack:' + rel, 'kind': 'pack', 'path': rel,
                       'title': 'Сборка «%s» (%s)' % (p['name'].rsplit(' (', 1)[0], p['version_dir'])})
    for m in maps:
        rel = os.path.relpath(m['path'], lib).replace('\\', '/')
        b = {'id': 'map:' + os.path.basename(rel), 'kind': 'map', 'path': rel, 'title': 'Карта «%s»' % m['title']}
        if is_local_only(rel):
            b['local_only'] = True
        blocks.append(b)

    # 2. Моды с Modrinth.
    jar_rels = []
    for rel in pack_dirs:
        for j in sorted(ps.jars(os.path.join(lib, rel, 'mods'))):
            r = rel + '/mods/' + j
            if not is_local_only(r):
                jar_rels.append(r)
    sha = {r: cache.sha1(r) for r in jar_rels}
    urls = modrinth_urls(sorted(set(sha.values())), os.path.join(REPO_DIR, 'tools', 'modrinth_cache.json'))
    files = [{'path': r, 'url': urls[sha[r]], 'sha1': sha[r], 'size': os.path.getsize(os.path.join(lib, r))}
             for r in jar_rels if urls.get(sha[r])]
    external = {f['path'] for f in files}
    skip = external | set(local_only)
    print('Модов всего %d, с Modrinth %d' % (len(jar_rels), len(files)))

    # 3. Хеши блоков и сравнение с релизом.
    r = gh('release', 'view', TAG, '--repo', REPO, '--json', 'assets', check=False)
    assets = {x['name']: x for x in json.loads(r.stdout)['assets']} if r.returncode == 0 else {}
    remote = {}
    if 'manifest.json' in assets:
        try:
            remote = ps.fetch_remote_manifest()
        except Exception as e:
            print('не прочитал manifest.json релиза:', e)
    rblocks = {b['id']: b for b in remote.get('blocks', [])}
    upload = []
    for b in blocks:
        fl = ps.block_files(b, lib, skip)
        if b['kind'] == 'pack':
            b['jars'] = sorted(f.rsplit('/', 1)[-1] for f in fl if f.startswith(b['path'] + '/mods/') and f.endswith('.jar'))
        b['hash'] = ps.block_hash(fl, cache)
        if b.get('local_only'):
            continue
        old = rblocks.get(b['id'])
        if old and old.get('hash') == b['hash'] and old.get('asset') in assets:
            b.update(asset=old['asset'], size=old['size'], asset_sha1=old['asset_sha1'])
            continue
        tag = hashlib.sha1(b['id'].encode()).hexdigest()[:8]
        if b['kind'] == 'app':
            b['asset'] = 'app-%s.exe' % b['hash'][:10]
            dst = os.path.join(out, b['asset'])
            shutil.copy2(os.path.join(lib, b['path']), dst)
        else:
            b['asset'] = '%s-%s-%s.zip' % (b['kind'], tag, b['hash'][:10])
            dst = os.path.join(out, b['asset'])
            if not os.path.isfile(dst):
                print('Пакую', b['title'], '(%d файлов)' % len(fl))
                with zipfile.ZipFile(dst + '.tmp', 'w', allowZip64=True) as z:
                    for f in fl:
                        comp = zipfile.ZIP_STORED if f.lower().endswith(STORED) else zipfile.ZIP_DEFLATED
                        z.write(ps.lp(os.path.join(lib, f)), f, compress_type=comp, compresslevel=6)
                os.replace(dst + '.tmp', dst)
        b['size'] = os.path.getsize(dst)
        b['asset_sha1'] = sha1_file(dst)
        upload.append(dst)
    cache.save()

    # 4. Версия и «Что нового».
    today = time.strftime('%Y.%m.%d')
    rv = remote.get('version', '')
    if a.version:
        version = a.version
    elif rv == today or rv.startswith(today + '.'):
        version = today + '.' + str((int(rv.split('.')[3]) if rv.count('.') == 3 else 1) + 1)
    else:
        version = today
    old_ids = set(rblocks)
    changes = list(a.note)
    for b in blocks:
        if b['id'] not in old_ids and remote and b['kind'] in ('pack', 'map'):
            changes.append('Новое: ' + b['title'])
    for i in old_ids - {b['id'] for b in blocks}:
        if i.startswith(('pack:', 'map:')):
            changes.append('Убрано: ' + rblocks[i]['title'])
    for b in blocks:
        o = rblocks.get(b['id'])
        if o and o.get('hash') != b['hash'] and b['kind'] in ('pack', 'map', 'app'):
            changes.append('Обновлено: ' + b['title'])
    if remote and {f['path']: f['sha1'] for f in remote.get('files', [])} != {f['path']: f['sha1'] for f in files}:
        changes.append('Обновлены моды в сборках')
    if not changes:
        changes.append('Первая версия с обновлениями через GitHub' if not remote else 'Мелкие исправления')

    manifest = {
        'name': 'Minecraft Packs',
        'version': version,
        'note': 'Всё, что входит в эту версию. Остальное в папке библиотеки - остатки прошлых версий '
                '(см. README, раздел 8 для Claude).',
        'repo': REPO, 'tag': TAG,
        'changes': changes,
        'root': sorted(set(core_paths) | version_dirs | {'Карты', ps.EXE_NAME, 'manifest.json'}),
        'packs': {rel: sorted(ps.jars(os.path.join(lib, rel, 'mods'))) for rel in sorted(pack_dirs)},
        'maps': {os.path.basename(m['path']): {'title': m['title'], 'save': m['save'], 'version': m['version']}
                 for m in maps},
        'local_only': local_only,
        'blocks': blocks,
        'files': files,
    }
    mpath = os.path.join(out, 'manifest.json')
    json.dump(manifest, open(mpath, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    # Стартовый архив: программа и файлы корня, остальное она скачает сама.
    starter = os.path.join(out, STARTER)
    core_files = ps.block_files(blocks[0], lib, skip) + [ps.EXE_NAME]
    with zipfile.ZipFile(starter, 'w') as z:
        for f in core_files:
            comp = zipfile.ZIP_STORED if f.lower().endswith(STORED) else zipfile.ZIP_DEFLATED
            z.write(ps.lp(os.path.join(lib, f)), 'Minecraft Packs/' + f, compress_type=comp)
    total = sum(os.path.getsize(p) for p in upload)
    print('Версия %s. Загрузить: %d файлов, %.0f МБ' % (version, len(upload), total / 1048576))
    for c in changes:
        print('  ·', c)
    if a.dry:
        print('--dry: ничего не загружено')
        return

    # 5. Загрузка: сначала блоки, потом стартовый архив и опись.
    if gh('release', 'view', TAG, '--repo', REPO, check=False).returncode:
        gh('release', 'create', TAG, '--repo', REPO, '--title', 'Minecraft Packs',
           '--notes', 'Библиотека карт и сборок. Скачай Minecraft-Packs.zip, распакуй и запусти '
                      '«Выбор карты и сборки.exe»: программа сама скачает карты и сборки и будет обновляться.',
           '--latest')
    for i, p in enumerate(upload, 1):
        print('Загружаю %d/%d %s (%.0f МБ)' % (i, len(upload), os.path.basename(p), os.path.getsize(p) / 1048576),
              flush=True)
        for attempt in range(3):
            r = gh('release', 'upload', TAG, p, '--repo', REPO, '--clobber', check=False)
            if r.returncode == 0:
                break
            print('  повтор:', r.stderr.strip()[-200:])
            time.sleep(10)
        else:
            raise RuntimeError('не загрузился ' + p)
    gh('release', 'upload', TAG, starter, '--repo', REPO, '--clobber')
    gh('release', 'upload', TAG, mpath, '--repo', REPO, '--clobber')
    used = {b.get('asset') for b in blocks} | {STARTER, 'manifest.json'}
    for name in assets:
        if name not in used:
            gh('release', 'delete-asset', TAG, name, '--repo', REPO, '--yes', check=False)
            print('Удалён старый ассет', name)
    gh('release', 'edit', TAG, '--repo', REPO, '--title', 'Minecraft Packs %s' % version, '--latest')
    shutil.copy2(mpath, os.path.join(lib, 'manifest.json'))
    for p in upload:
        os.remove(p)
    os.makedirs(os.path.join(REPO_DIR, 'src'), exist_ok=True)
    shutil.copy2(os.path.join(lib, 'PackSwitcher.pyw'), os.path.join(REPO_DIR, 'src', 'PackSwitcher.pyw'))
    print('Опубликовано: версия', version)


if __name__ == '__main__':
    main()

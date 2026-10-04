"""Проверка, что скачанная Portalis версия запускается: собирает команду запуска из описания версии
(как это делает лаунчер), запускает игру офлайн с ником Tester, ждёт в журнале «Sound engine started»
(игра дошла до меню) и закрывает её. Только для проверки, в программу не входит.

    python tools/launch_test.py <папка .minecraft> <id версии> [секунд]"""
import importlib.machinery, importlib.util, json, os, re, subprocess, sys, tempfile, time, zipfile, uuid
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)


def load_chain(mc, vid):
    chain = []
    while vid:
        with open(os.path.join(mc, 'versions', vid, vid + '.json'), encoding='utf-8') as fh:
            info = json.load(fh)
        chain.append((vid, info))
        vid = info.get('inheritsFrom')
    return chain


def args_of(items, sub):
    out = []
    for a in items:
        if isinstance(a, str):
            out.append(a)
        elif ps._rules_ok(a.get('rules')):
            v = a.get('value')
            out += v if isinstance(v, list) else [v]
    return [re.sub(r'\$\{(\w+)\}', lambda m: sub.get(m.group(1), m.group(0)), x) for x in out]


def main():
    mc, vid = sys.argv[1], sys.argv[2]
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 300
    ps.set_game_dir(mc)
    chain = load_chain(mc, vid)
    top, base_id, base = chain[0][1], chain[-1][0], chain[-1][1]
    natives = tempfile.mkdtemp(prefix='natives-')
    cp, seen = [], set()
    for _id, info in chain:  # сначала библиотеки загрузчика, потом игры
        for lib in info.get('libraries', []):
            if not ps._rules_ok(lib.get('rules')):
                continue
            parts = lib['name'].split(':')
            key = ':'.join(parts[:2] + parts[3:])
            dl = lib.get('downloads') or {}
            art = dl.get('artifact')
            rel = (art or {}).get('path') or ps._maven_path(lib['name'])
            path = ps._lib_path(rel)
            nat = (lib.get('natives') or {}).get('windows')
            if nat:
                c = (dl.get('classifiers') or {}).get(nat.replace('${arch}', '64'))
                if c:
                    with zipfile.ZipFile(ps._lib_path(c['path'])) as z:
                        for n in z.namelist():
                            if not n.startswith('META-INF') and not n.endswith('/'):
                                z.extract(n, natives)
            if (art is not None or not dl) and key not in seen and os.path.isfile(path):
                seen.add(key)
                cp.append(path)
    own = os.path.join(mc, 'versions', vid, (top.get('jar') or vid) + '.jar')  # Forge кладёт свою заглушку
    if not os.path.isfile(own):  # так делает официальный лаунчер: клиент под именем дочерней версии
        import shutil
        shutil.copy2(os.path.join(mc, 'versions', base_id, base_id + '.jar'), own)
    cp.append(own)
    ai = base.get('assetIndex', {})
    sub = {'auth_player_name': 'Tester', 'version_name': vid, 'game_directory': mc,
           'assets_root': os.path.join(mc, 'assets'), 'game_assets': os.path.join(mc, 'assets'),
           'assets_index_name': ai.get('id', base.get('assets', '')), 'auth_uuid': uuid.uuid4().hex,
           'auth_access_token': '0', 'auth_session': '0', 'clientid': '0', 'auth_xuid': '0', 'user_type': 'legacy',
           'version_type': 'release', 'natives_directory': natives, 'launcher_name': 'portalis-test',
           'launcher_version': '1', 'classpath': os.pathsep.join(cp), 'classpath_separator': os.pathsep,
           'library_directory': os.path.join(mc, 'libraries'), 'user_properties': '{}'}
    jvm, game = [], []
    for _id, info in reversed(chain):  # аргументы игры, потом загрузчика
        a = info.get('arguments') or {}
        jvm += args_of(a.get('jvm', []), sub)
        game += args_of(a.get('game', []), sub)
    if not any('-cp' == x for x in jvm):
        jvm += ['-Djava.library.path=' + natives, '-cp', sub['classpath']]
    if not game:
        game = args_of((top.get('minecraftArguments') or base.get('minecraftArguments', '')).split(), sub)
    comp = (base.get('javaVersion') or {}).get('component') or 'jre-legacy'
    java = ps.find_game_java(comp, console=True)
    cmd = [java, '-Xmx2G'] + jvm + [top.get('mainClass') or base['mainClass']] + game
    log = os.path.join(tempfile.gettempdir(), 'launch_test_%s.log' % re.sub(r'\W', '_', vid))
    print('Java:', java, '| библиотек:', len(cp), '| журнал:', log, flush=True)
    t0 = time.time()
    with open(log, 'w', encoding='utf-8', errors='ignore') as out:
        p = subprocess.Popen(cmd, cwd=mc, stdout=out, stderr=subprocess.STDOUT)
        ok = False
        while time.time() - t0 < limit:
            time.sleep(2)
            with open(log, encoding='utf-8', errors='ignore') as fh:
                text = fh.read()
            if 'Sound engine started' in text:
                ok = True
                break
            if p.poll() is not None:
                break
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(p.pid)], capture_output=True)
    print(('ЗАПУСТИЛАСЬ до меню за %.0f с' % (time.time() - t0)) if ok else 'НЕ ЗАПУСТИЛАСЬ (код %s)' % p.poll())
    if not ok:
        print(''.join(open(log, encoding='utf-8', errors='ignore').readlines()[-25:]))
    sys.exit(0 if ok else 1)


main()

"""Проверка «Версий без лаунчера» на временной папке игры (настоящую не трогает).
    python tools/test_versions.py            - только планы (что и сколько качать)
    python tools/test_versions.py --install  - полная установка Forge 1.20.1, Fabric 26.1.2, NeoForge 1.21.1, Forge 1.12.2"""
import importlib.machinery, importlib.util, json, os, sys, tempfile, time
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
full = '--install' in sys.argv
tmp = os.environ.get('PORTALIS_TEST_MC') or tempfile.mkdtemp(prefix='portalis-versions-')
ps.ROOT = tmp
ps.set_game_dir(os.path.join(tmp, '.minecraft'))
os.makedirs(ps.MC, exist_ok=True)
fails = 0


def check(cond, text):
    global fails
    fails += not cond
    print(('OK   ' if cond else 'ОШИБКА ') + text, flush=True)


vs = ps.mojang_versions()
check(len(vs) > 500 and vs[0]['id'], 'список Mojang: %d версий, свежая %s' % (len(vs), vs[0]['id']))
combos = [('1.20.1', 'forge'), ('26.1.2', 'fabric'), ('1.21.1', 'neoforge'), ('1.12.2', 'forge'), ('1.21.8', 'quilt'),
          ('1.8.9', 'vanilla')]
for gv, ld in combos:
    t0 = time.time()
    try:
        pl = ps.plan_version(gv, ld, log=lambda m: None)
        check(pl['tasks'] and pl['size'] > 0, '%s %s -> %s: %d файлов, %s, Java %s, звуков %d%s (%.0f с)' % (
            gv, ld, pl['id'], len(pl['tasks']), ps.fmt_mb(pl['size']), pl['java'], pl['assets'],
            ', установщик' if pl['installer'] else '', time.time() - t0))
    except Exception as e:
        check(False, '%s %s: %s' % (gv, ld, e))
if full:
    for gv, ld in combos[:4]:
        t0 = time.time()
        last = [0]

        def prog(d, tot):
            if time.time() - last[0] > 20:
                last[0] = time.time()
                print('   %s / %s' % (ps.fmt_mb(d), ps.fmt_mb(tot)), flush=True)
        try:
            pl = ps.plan_version(gv, ld, log=lambda m: None)
            vid = ps.apply_version(pl, log=lambda m: print('   ' + m, flush=True), progress=prog)
            j = os.path.join(ps.MC, 'versions', vid, vid + '.json')
            info = json.load(open(j, encoding='utf-8'))
            check(os.path.isfile(j), 'установлено %s за %.0f с' % (vid, time.time() - t0))
            again = ps.plan_version(gv, ld, log=lambda m: None)
            check(not again['tasks'] and not again['installer'], 'повторная проверка %s: докачивать нечего (%d)' % (vid, len(again['tasks'])))
            check(ps.find_game_java(pl['java']) is not None, 'Java %s на месте' % pl['java'])
            check(any(v['id'] == vid for v in ps.installed_versions()), 'видна в списке версий: ' + str(
                [(v['id'], v['kind']) for v in ps.installed_versions() if v['id'] == vid]))
            _ = info
        except Exception as e:
            check(False, '%s %s: %s' % (gv, ld, e))
print('Итог: ошибок', fails, '| папка', tmp)
sys.exit(1 if fails else 0)

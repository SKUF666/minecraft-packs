"""Обновление докачивает только изменившееся: копия каталога (часть core) из настоящего релиза во временной папке,
несколько файлов «устарели» (испорчены) и один пропал -> plan_items + apply_plan. Проверяет, что файлы
восстановлены и что скачано мало (кусками из архива), а не весь архив части.

    python tools/test_partial_update.py
"""
import os, sys, json, time, shutil, hashlib, tempfile, zipfile, importlib.machinery, importlib.util
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
sys.path.insert(0, LIB)
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
man = ps.fetch_remote_manifest()
root = tempfile.mkdtemp(prefix='portalis-upd-')
it = ps.manifest_item(man, 'core')
files = ps.fetch_list(man, it, root)['files']
own = [f for f in files if f['src']['t'] == 'own']
print('версия', man['version'], '| файлов в части «%s»: %d, архив %.1f МБ' % (it['title'], len(own), it['own_size'] / 1048576))
# 1. «установленная» копия: всё из архива части
zp = os.path.join(root, '_full.zip')
ps._fetch_to(ps.asset_url(man, it['own']), zp, None, it['own_sha1'])
with zipfile.ZipFile(zp) as z:
    for f in own:
        dst = os.path.join(root, f['p'])
        os.makedirs(os.path.dirname(ps.lp(dst)), exist_ok=True)
        with open(ps.lp(dst), 'wb') as fh:
            fh.write(z.read(f['p']))
os.remove(zp)
# 2. «устарело»: 3 файла изменены, 1 удалён
spoil = [f for f in own if f['p'].endswith('.json')][:3]
gone = [f for f in own if f['p'].endswith('.png')][:1]
for f in spoil:
    with open(ps.lp(os.path.join(root, f['p'])), 'ab') as fh:
        fh.write(b' ')
for f in gone:
    os.remove(ps.lp(os.path.join(root, f['p'])))
# 3. обновление
plan = ps.plan_items(man, [it], root, lambda *a: None)
need = [f['p'] for f in plan['need']]
est = ps.plan_size(man, plan)
got = [0]
t = time.time()
logs = []
ps.apply_plan(man, plan, root, logs.append, lambda done, total: got.__setitem__(0, done))
bad = [f['p'] for f in spoil + gone
       if not os.path.isfile(ps.lp(os.path.join(root, f['p'])))
       or hashlib.sha1(open(ps.lp(os.path.join(root, f['p'])), 'rb').read()).hexdigest() != f['s']]
print('нужно файлов: %d (%s...)' % (len(need), ', '.join(n[-30:] for n in need[:2])))
print('оценка %.2f МБ, скачано %.2f МБ (архив целиком был бы %.1f МБ), %.0f с' % (est / 1048576, got[0] / 1048576,
                                                                                   it['own_size'] / 1048576, time.time() - t))
print('журнал:', ' | '.join(l for l in logs if 'Кусками' in l or 'Скачиваю' in l)[:300])
ok = not bad and len(need) == len(spoil) + len(gone) and got[0] < it['own_size'] * 0.2
print('Итог:', 'OK' if ok else 'ОШИБКА %s' % bad)
shutil.rmtree(root, ignore_errors=True)

"""Проверка конструктора сборок на временной папке: подбор зависимостей и сохранение сборки.
    python tools/test_builder.py"""
import importlib.machinery, importlib.util, json, os, sys, tempfile
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
tmp = tempfile.mkdtemp(prefix='portalis-builder-')
ps.ROOT = tmp
ps.MAPS_DIR = os.path.join(tmp, 'Карты')
ps.set_game_dir(os.path.join(tmp, '.minecraft'))
fails = 0


def check(cond, text):
    global fails
    fails += not cond
    print(('OK   ' if cond else 'ОШИБКА ') + text)


hits, total = ps.mr_search('', '26.1.2', 'fabric', 'mod', 'optimization', 'downloads', 0, 5)
check(total > 20 and hits, 'поиск оптимизации для Fabric 26.1.2: %d найдено, первые: %s' % (total, [h['title'] for h in hits[:3]]))
sel = [{'project_id': 'mOgUt4GM', 'title': 'Mod Menu', 'type': 'mod'},
       {'project_id': 'AANobbMI', 'title': 'Sodium', 'type': 'mod'}]
sh, _t = ps.mr_search('complementary', '26.1.2', 'fabric', 'shader', None, 'downloads', 0, 1)
if sh:
    sel.append({'project_id': sh[0]['project_id'], 'title': sh[0]['title'], 'type': 'shader'})
files, problems = ps.resolve_build(sel, '26.1.2', 'fabric', log=lambda m: None)
titles = {f['title'] for f in files}
print('   состав:', sorted(titles), '| проблемы:', problems)
check('Fabric API' in titles, 'Fabric API добавлен сам (нужен Mod Menu)')
check(any('Iris' in t for t in titles) or not sh, 'Iris добавлен сам для шейдера')
check(all(f['sha1'] and f['url'].startswith('https://cdn.modrinth.com/') for f in files), 'у всех файлов есть ссылка и sha1')
f2, p2 = ps.resolve_build([{'project_id': 'LNytGWDc', 'title': 'Create', 'type': 'mod'},
                           {'project_id': 'u6dRKJwZ', 'title': 'JEI', 'type': 'mod'}], '1.20.1', 'forge', log=lambda m: None)
print('   Forge 1.20.1:', sorted(f['title'] for f in f2), p2)
check(len(f2) >= 2 and not any('нет для' in x for x in p2), 'Create и JEI есть для Forge 1.20.1')
f3, p3 = ps.resolve_build([{'project_id': 'AANobbMI', 'title': 'Sodium', 'type': 'mod'}], '1.12.2', 'forge', log=lambda m: None)
check(not f3 and p3, 'Sodium для Forge 1.12.2 честно помечен как «нет такой версии»')
check(ps.loader_version('fabric', '26.1.2').startswith('0.'), 'версия Fabric для 26.1.2: ' + ps.loader_version('fabric', '26.1.2'))
check(ps.loader_version('forge', '1.20.1').startswith('47.'), 'версия Forge для 1.20.1: ' + ps.loader_version('forge', '1.20.1'))
check(ps.loader_version('neoforge', '1.21.1').startswith('21.1.'), 'версия NeoForge для 1.21.1: ' + ps.loader_version('neoforge', '1.21.1'))
# Сохранение небольшой сборки Fabric целиком
small = [f for f in files if f['type'] == 'mod']
folder = ps.save_build('Тест конструктора', '26.1.2', 'fabric', small, sel[:2], log=lambda m: None)
pj = json.load(open(os.path.join(folder, 'pack.json'), encoding='utf-8'))
check(os.path.isdir(os.path.join(folder, 'mods')) and len(os.listdir(os.path.join(folder, 'mods'))) == len(small),
      'сборка сохранена: %s, модов %d' % (os.path.basename(folder), len(small)))
check(pj['tl_version'].startswith('fabric-loader-') and os.path.isfile(os.path.join(folder, 'versions', pj['tl_version'],
                                                                                    pj['tl_version'] + '.json')),
      'профиль Fabric для лаунчера: ' + pj['tl_version'])
check(os.path.isfile(os.path.join(folder, 'cover.png')), 'обложка из значка мода')
packs = ps.find_packs()
p = next((x for x in packs if x['path'] == folder), None)
check(p and p.get('user') and p['count'] == len(small), 'сборка видна в списке сборок как «своя»')
ps.switch(p, packs, True, lambda m: None)
check(len([j for j in os.listdir(ps.MODS) if j.endswith('.jar')]) == len(small), 'сборка включается: моды в папке игры')
check(os.path.isdir(os.path.join(ps.MC, 'versions', pj['tl_version'])), 'профиль Fabric скопирован в versions')
out = ps.export_mrpack(None, p, lambda m: None)
import zipfile
idx = json.loads(zipfile.ZipFile(out).read('modrinth.index.json'))
check(len(idx['files']) == len(small) and idx['dependencies'].get('fabric-loader'), 'пакет .mrpack: моды ссылками, загрузчик указан')
print('Итог: ошибок', fails, '| папка', tmp)
sys.exit(1 if fails else 0)

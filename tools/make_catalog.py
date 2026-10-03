import os, sys, json, glob
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.environ['USERPROFILE'], 'Desktop', 'Minecraft Packs')
lines = []
total = 0
for pj in sorted(glob.glob(os.path.join(LIB, '*', '*', 'pack.json'))):
    d = os.path.dirname(pj)
    p = json.load(open(pj, encoding='utf-8'))
    try:
        data = json.load(open(os.path.join(d, 'mods.json'), encoding='utf-8'))
    except Exception:
        continue
    mods = data['mods']
    if not mods:
        continue
    total += len(mods)
    lines += ['', '=' * 72, '%s  [%s, %d модов]' % (p['name'].rsplit(' (', 1)[0], os.path.basename(os.path.dirname(d)), len(mods)), '=' * 72]
    for cat in data['order']:
        items = sorted([m for m in mods if m['cat'] == cat], key=lambda m: m['name'].lower())
        if not items:
            continue
        lines += ['', '  ' + cat]
        for m in items:
            lines.append('    %s %s  (%s МБ)' % (m['name'], m['version'], m['mb']))
            if m.get('ru') and not cat.startswith('Библиотеки'):
                lines.append('        ' + m['ru'])
head = ['КАТАЛОГ МОДОВ ПО СБОРКАМ', 'Название, версия и размер; под ним - что мод делает.',
        'Библиотеки ничего не добавляют сами, но нужны другим модам.']
open(os.path.join(LIB, 'Каталог модов.txt'), 'w', encoding='utf-8').write('\n'.join(head + lines) + '\n')
print('каталог: строк', len(head + lines), '| модов во всех сборках', total)

import json, glob, os, sys
sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.environ['USERPROFILE'], 'Desktop', 'Minecraft Packs')
os.chdir(LIB)
t = open(os.path.join(HERE, 'readme_tpl.txt'), encoding='utf-8').read()
order = {'26.1.2': 0, '1.21.10': 1, '1.21.8': 2, '1.21.1': 3}
maps = [json.load(open(p, encoding='utf-8')) for p in glob.glob('Карты/*/map.json')]
mt = []
for m in sorted(maps, key=lambda m: (order.get(m['version'], 9), m['title'])):
    mt.append('  %s  [%s]  %s. %s' % (m['title'], m['version'], m['genre'], m['players']))
    mt.append('      ' + m['desc'])
pk = []
for p in sorted(glob.glob('*/*/pack.json')):
    d = json.load(open(p, encoding='utf-8'))
    pk.append('  %s  [%s]' % (d['name'], os.path.basename(os.path.dirname(os.path.dirname(p)))))
    pk.append('      ' + d.get('description', ''))
sv = ['  %-42s %-24s %-13s %s. %s' % (s['name'], s['ip'], s['versions'], s['lang'], s['note'])
      for s in json.load(open('servers.json', encoding='utf-8'))]
t = t.replace('{MAPS}', '\n'.join(mt)).replace('{PACKS}', '\n'.join(pk)).replace('{SERVERS}', '\n'.join(sv))
open('README.txt', 'w', encoding='utf-8').write(t)
print('README строк:', t.count('\n'))

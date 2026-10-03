import os, zipfile, time, sys
sys.stdout.reconfigure(encoding='utf-8')
root = os.path.join(os.environ['USERPROFILE'], 'Desktop', 'Minecraft Packs')
out = os.path.join(os.path.dirname(root), 'Minecraft Packs (для друга).zip')
LP = lambda p: '\\\\?\\' + os.path.abspath(p)
if os.path.exists(out):
    os.remove(out)
n = 0; t = time.time()
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
    for d, dirs, files in os.walk(LP(root)):
        rel = os.path.relpath(d, LP(root)); top = rel.split(os.sep)
        if top[0].startswith('_') or '__pycache__' in top:
            dirs[:] = []; continue
        for f in files:
            if f.endswith(('.log', '.old')) or (rel == '.' and f.startswith('_')):
                continue
            arc = os.path.join('Minecraft Packs', f) if rel == '.' else os.path.join('Minecraft Packs', rel, f)
            ct = zipfile.ZIP_STORED if f.lower().endswith(('.jar', '.zip', '.png', '.exe')) else zipfile.ZIP_DEFLATED
            z.write(os.path.join(d, f), arc, compress_type=ct); n += 1
print('архив: файлов', n, '| %.0f МБ' % (os.path.getsize(out) / 1048576), '| %.0fs' % (time.time() - t))
print('битых:', zipfile.ZipFile(out).testzip())

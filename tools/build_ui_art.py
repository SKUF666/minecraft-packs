"""Картинки ChatGPT (raw/) -> «Оформление» в библиотеке."""
import os, sys
from PIL import Image, ImageDraw
HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(os.path.dirname(HERE), 'art-src')
OUT = os.path.join(os.environ['LOCALAPPDATA'], 'Portalis', 'Оформление')
os.makedirs(OUT, exist_ok=True)
BG = (0x15, 0x16, 0x1a)
Y0 = int(sys.argv[1]) if len(sys.argv) > 1 else 330


def rounded(im, size, r):
    im = im.convert('RGBA').resize((size * 3, size * 3), Image.LANCZOS)
    m = Image.new('L', im.size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, im.size[0] - 1, im.size[1] - 1), r * 3, fill=255)
    a = Image.new('RGBA', im.size, (0, 0, 0, 0))
    a.paste(im, (0, 0), m)
    return a.resize((size, size), Image.LANCZOS)


# заставка
sp = Image.open(os.path.join(RAW, '00_splash.png')).convert('RGB').resize((660, 440), Image.LANCZOS)
sp.save(os.path.join(OUT, 'splash.png'))

# шапка: полоса 1400x150, низ растворяется в фоне окна
bn = Image.open(os.path.join(RAW, '01_banner.png')).convert('RGB')
bn = bn.resize((1400, int(bn.height * 1400 / bn.width)), Image.LANCZOS)
H = 150
band = bn.crop((0, Y0, 1400, Y0 + H))
px = band.load()
for y in range(H - 30, H):
    k = (y - (H - 30)) / 29.0
    for x in range(band.width):
        r, g, b = px[x, y]
        px[x, y] = tuple(int(c + (bg - c) * k) for c, bg in zip((r, g, b), BG))
band.save(os.path.join(OUT, 'banner.png'))

# значки вкладок: сетка 3x2 на прозрачном фоне
ic = Image.open(os.path.join(RAW, '02_icons.png')).convert('RGBA')
names = ['tab_maps', 'tab_packs', 'tab_servers', 'tab_friend', 'tab_launchers', 'tab_update']
cw, ch = ic.width // 3, ic.height // 2
for i, n in enumerate(names):
    cell = ic.crop(((i % 3) * cw, (i // 3) * ch, (i % 3 + 1) * cw, (i // 3 + 1) * ch))
    bbox = cell.getchannel('A').point(lambda a: 255 if a > 24 else 0).getbbox()
    cell = cell.crop(bbox)
    side = max(cell.size)
    sq = Image.new('RGBA', (side, side), (0, 0, 0, 0))
    sq.paste(cell, ((side - cell.width) // 2, (side - cell.height) // 2), cell)
    sq.resize((26, 26), Image.LANCZOS).save(os.path.join(OUT, n + '.png'))
    sq.resize((64, 64), Image.LANCZOS).save(os.path.join(OUT, n + '_64.png'))

rounded(Image.open(os.path.join(RAW, '03_ready.png')), 190, 22).save(os.path.join(OUT, 'ready.png'))
rounded(Image.open(os.path.join(RAW, '04_update.png')), 160, 20).save(os.path.join(OUT, 'update.png'))
rounded(Image.open(os.path.join(RAW, '05_empty.png')), 110, 16).save(os.path.join(OUT, 'empty.png'))
print('ok', sorted(os.listdir(OUT)))


# --- значки кнопок и иллюстрации 2026-10-05 ---
def slice_sheet(name, names, size=18):
    sh = Image.open(os.path.join(RAW, name)).convert('RGBA')
    cw, ch = sh.width // 4, sh.height // 2
    for i, n in enumerate(names):
        cell = sh.crop(((i % 4) * cw, (i // 4) * ch, (i % 4 + 1) * cw, (i // 4 + 1) * ch))
        bbox = cell.getchannel('A').point(lambda a: 255 if a > 24 else 0).getbbox()
        cell = cell.crop(bbox)
        side = max(cell.size)
        sq = Image.new('RGBA', (side, side), (0, 0, 0, 0))
        sq.paste(cell, ((side - cell.width) // 2, (side - cell.height) // 2), cell)
        sq.resize((size, size), Image.LANCZOS).save(os.path.join(OUT, 'ic_%s.png' % n))
        sq.resize((48, 48), Image.LANCZOS).save(os.path.join(OUT, 'ic_%s_48.png' % n))


slice_sheet('11_btn_a.png', ['play', 'folder', 'backup', 'copy', 'download', 'delete', 'search', 'settings'])
slice_sheet('12_btn_b.png', ['globe', 'check', 'cross', 'server', 'invite', 'compass', 'hourglass', 'bolt'])
fr = Image.open(os.path.join(RAW, '13_friends.png')).convert('RGB')
fr = fr.resize((1400, int(fr.height * 1400 / fr.width)), Image.LANCZOS)
fr.crop((0, 385, 1400, 385 + 200)).save(os.path.join(OUT, 'friends_wide.png'))
rounded(Image.open(os.path.join(RAW, '14_offline.png')), 110, 16).save(os.path.join(OUT, 'offline.png'))
icon = Image.open(os.path.join(RAW, '20_portal_icon.png')).convert('RGBA')  # Portalis
bb = icon.getchannel('A').point(lambda a: 255 if a > 20 else 0).getbbox()
icon = icon.crop(bb)
side = int(max(icon.size) * 1.04)
sq = Image.new('RGBA', (side, side), (0, 0, 0, 0))
sq.paste(icon, ((side - icon.width) // 2, (side - icon.height) // 2), icon)
for n, sz in (('appicon_72.png', 72), ('appicon_96.png', 96)):
    sq.resize((sz, sz), Image.LANCZOS).save(os.path.join(OUT, n))
sq.resize((256, 256), Image.LANCZOS).save(os.path.join(os.path.dirname(OUT), 'icon.png'))
print('значки и иллюстрации готовы')

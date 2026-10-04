"""Картинки ChatGPT (raw/) -> «Оформление» в библиотеке."""
import os, sys
from PIL import Image, ImageDraw
HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(os.path.dirname(HERE), 'art-src')
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library', 'Оформление')
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


# --- конструктор и скины (2026-10-04) ---
sh = Image.open(os.path.join(RAW, '30_tabs2.png')).convert('RGBA')
cw, ch = sh.width // 3, sh.height // 2
for i, n in enumerate(['tab_builder', 'tab_skins', 'ic_cape', 'ic_mannequin', 'ic_add', 'ic_palette']):
    cell = sh.crop(((i % 3) * cw, (i // 3) * ch, (i % 3 + 1) * cw, (i // 3 + 1) * ch))
    bbox = cell.getchannel('A').point(lambda a: 255 if a > 24 else 0).getbbox()
    cell = cell.crop(bbox)
    side = max(cell.size)
    sq = Image.new('RGBA', (side, side), (0, 0, 0, 0))
    sq.paste(cell, ((side - cell.width) // 2, (side - cell.height) // 2), cell)
    sq.resize((26 if n.startswith('tab_') else 18,) * 2, Image.LANCZOS).save(os.path.join(OUT, n + '.png'))
    sq.resize((48, 48), Image.LANCZOS).save(os.path.join(OUT, n + '_48.png'))
for src, dst, y0 in (('31_builder_hero.png', 'builder_wide.png', 245), ('32_skins_hero.png', 'skins_wide.png', 235)):
    im = Image.open(os.path.join(RAW, src)).convert('RGB')
    im = im.resize((1400, int(im.height * 1400 / im.width)), Image.LANCZOS)
    im.crop((0, y0, 1400, y0 + 200)).save(os.path.join(OUT, dst))
print('конструктор и скины: картинки готовы')


# Левая часть широких картинок затемняется плавно: на ней заголовок и подпись.
def darken_left(name, upto=0.62, strength=0.72):
    p = os.path.join(OUT, name)
    im = Image.open(p).convert('RGB')
    w, h = im.size
    mask = Image.new('L', (w, 1))
    for x in range(w):
        t = x / (w * upto)
        mask.putpixel((x, 0), int(255 * strength * max(0.0, 1 - t) ** 1.2))
    mask = mask.resize((w, h))
    dark = Image.new('RGB', (w, h), (12, 13, 17))
    Image.composite(dark, im, mask).save(p)


for n in ('friends_wide.png', 'builder_wide.png', 'skins_wide.png'):
    darken_left(n)
print('затемнение готово')


# Полупрозрачная тень слева для шапок вкладок: подпись читается при любой ширине окна.
sh = Image.new('RGBA', (760, 220), (0, 0, 0, 0))
for x in range(760):
    a = int(215 * max(0.0, 1 - x / 760) ** 1.15)
    for y in range(220):
        sh.putpixel((x, y), (10, 11, 15, a))
sh.save(os.path.join(OUT, 'shade_left.png'))
print('тень готова')


# --- вкладка «Версии» (2026-10-04): лист значков на прозрачном фоне и широкая картинка ---
VER_SHEET = '35_versions_icons_t.png' if os.path.isfile(os.path.join(RAW, '35_versions_icons_t.png')) else None
if VER_SHEET:
    sh = Image.open(os.path.join(RAW, VER_SHEET)).convert('RGBA')
    cw, ch = sh.width // 3, sh.height // 2
    for i, n in enumerate(['tab_versions', 'ic_versions', 'ic_java', 'ic_loader', 'ic_tag', 'ic_chest']):
        cell = sh.crop(((i % 3) * cw, (i // 3) * ch, (i % 3 + 1) * cw, (i // 3 + 1) * ch))
        cell = cell.crop(cell.getchannel('A').point(lambda a: 255 if a > 24 else 0).getbbox())
        side = max(cell.size)
        sq = Image.new('RGBA', (side, side), (0, 0, 0, 0))
        sq.paste(cell, ((side - cell.width) // 2, (side - cell.height) // 2), cell)
        sq.resize((26 if n.startswith('tab_') else 18,) * 2, Image.LANCZOS).save(os.path.join(OUT, n + '.png'))
        sq.resize((48, 48), Image.LANCZOS).save(os.path.join(OUT, n + '_48.png'))
im = Image.open(os.path.join(RAW, '34_versions_hero.png')).convert('RGB')
im = im.resize((1400, int(im.height * 1400 / im.width)), Image.LANCZOS)
im.crop((0, 250, 1400, 450)).save(os.path.join(OUT, 'versions_wide.png'))
darken_left('versions_wide.png')
print('вкладка «Версии»: картинки готовы')


# --- профиль (2026-10-04): 12 аватаров из листа 4x3 и фон шапки профиля ---
av = Image.open(os.path.join(RAW, '36_avatars.png')).convert('RGB')
cw, ch = av.width // 4, av.height // 3
os.makedirs(os.path.join(OUT, 'avatars'), exist_ok=True)
for i in range(12):
    cell = av.crop(((i % 4) * cw, (i // 4) * ch, (i % 4 + 1) * cw, (i // 4 + 1) * ch))
    side = int(min(cell.size) * 0.86)  # квадрат по центру, без белых промежутков и скруглённых углов
    l, t = (cell.width - side) // 2, (cell.height - side) // 2
    cell.crop((l, t, l + side, t + side)).resize((128, 128), Image.LANCZOS).save(
        os.path.join(OUT, 'avatars', 'av_%02d.png' % (i + 1)))
hb = Image.open(os.path.join(RAW, '34_versions_hero.png')).convert('RGB')
hb = hb.resize((900, int(hb.height * 900 / hb.width)), Image.LANCZOS).crop((120, 150, 900, 330))
hb.save(os.path.join(OUT, 'profile_banner.png'))
darken_left('profile_banner.png', upto=0.7, strength=0.6)
print('профиль: аватары и шапка готовы')


# --- значки аккаунта (2026-10-04): колокольчик, письмо, корона, связь, замок, сердце ---
sh = Image.open(os.path.join(RAW, '37_social_icons.png')).convert('RGBA')
cw, ch = sh.width // 3, sh.height // 2
for i, n in enumerate(['ic_bell', 'ic_mail', 'ic_crown', 'ic_signal', 'ic_lock', 'ic_heart']):
    cell = sh.crop(((i % 3) * cw, (i // 3) * ch, (i % 3 + 1) * cw, (i // 3 + 1) * ch))
    a = cell.getchannel('A').point(lambda v: 255 if v > 150 else 0)  # без размытого свечения вокруг
    cell = cell.crop(a.getbbox())
    cell.putalpha(Image.eval(cell.getchannel('A'), lambda v: 0 if v < 120 else min(255, v * 2)))
    side = max(cell.size)
    sq = Image.new('RGBA', (side, side), (0, 0, 0, 0))
    sq.paste(cell, ((side - cell.width) // 2, (side - cell.height) // 2), cell)
    sq.resize((18, 18), Image.LANCZOS).save(os.path.join(OUT, n + '.png'))
    sq.resize((48, 48), Image.LANCZOS).save(os.path.join(OUT, n + '_48.png'))
print('значки аккаунта готовы')


# --- достижения (2026-10-04): 12 медалей из листа 4x3, круглая маска убирает свечение ---
from PIL import ImageDraw
sh = Image.open(os.path.join(RAW, '38_achievements.png')).convert('RGBA')
cw, ch = sh.width // 4, sh.height // 3
os.makedirs(os.path.join(OUT, 'achievements'), exist_ok=True)
for i in range(12):
    cell = sh.crop(((i % 4) * cw, (i // 4) * ch, (i % 4 + 1) * cw, (i // 4 + 1) * ch))
    cell = cell.crop(cell.getchannel('A').point(lambda v: 255 if v > 200 else 0).getbbox())
    side = min(cell.size)
    l, t = (cell.width - side) // 2, (cell.height - side) // 2
    cell = cell.crop((l, t, l + side, t + side)).resize((256, 256), Image.LANCZOS)
    mask = Image.new('L', (256, 256), 0)
    ImageDraw.Draw(mask).ellipse((2, 2, 253, 253), fill=255)
    out = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
    out.paste(cell, (0, 0), mask)
    out.resize((64, 64), Image.LANCZOS).save(os.path.join(OUT, 'achievements', 'ach_%02d.png' % (i + 1)))
print('достижения готовы')

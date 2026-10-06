"""Рамки окон и шапки оформлений из картинок ChatGPT (art-src/40..54) -> Оформление/frames_ui/*.png + .json и
Оформление/banner_<стиль>.png. Середина рамки делается прозрачной (украшения у краёв остаются), размер подгоняется
так, чтобы толщина рамки была BORDER px. Программа собирает окно любого размера из 9 частей.
python tools/build_frames.py"""
import json, os, sys
from PIL import Image
sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ART = os.path.join(ROOT, 'library', 'Оформление')
OUT = os.path.join(ART, 'frames_ui')
os.makedirs(OUT, exist_ok=True)
# (имя рамки, файл, толщина рамки в px, доля угла)
FRAMES = [('panel', '40_panel_frame.png', 20, 0.32), ('card', '41_card_frame.png', 16, 0.46),
          ('card2', '42_forest_card2.png', 16, 0.46),
          ('nether_panel', '43_nether_panel.png', 20, 0.32), ('nether_card', '44_nether_card.png', 16, 0.46),
          ('end_panel', '46_end_panel.png', 20, 0.32), ('end_card', '47_end_card.png', 16, 0.46),
          ('heaven_panel', '49_heaven_panel.png', 20, 0.32), ('heaven_card', '50_heaven_card.png', 16, 0.46),
          ('ocean_panel', '52_ocean_panel.png', 20, 0.32), ('ocean_card', '53_ocean_card.png', 16, 0.46)]
BANNERS = [('banner_nether.png', '45_nether_banner.png'), ('banner_end.png', '48_end_banner.png'),
           ('banner_heaven.png', '51_heaven_banner.png'), ('banner_ocean.png', '54_ocean_banner.png')]


def dist(a, b):
    return sum(abs(a[i] - b[i]) for i in range(3))


def is_mid(p, ref):
    """Пиксель середины: цвет середины или её тень/виньетка (тёмный и почти серый)."""
    return p[3] > 150 and (dist(p, ref) < 34 or (sum(p[:3]) <= sum(ref[:3]) + 24 and max(p[:3]) - min(p[:3]) < 42))


def inset_of(im, ref):
    """Толщина рамки: от левого края по средним строкам до конца камня (тень внутри - уже середина)."""
    w, h = im.size

    def scan(test):
        best = []
        for y in (h // 2, int(h * 0.6), int(h * 0.4)):
            for x in range(w // 2):
                if test(im.getpixel((x, y))) and all(test(im.getpixel((x + d, y))) for d in range(2, 16)):
                    best.append(x)
                    break
        return min(best) if best else w // 20
    flat = scan(lambda p: p[3] > 200 and dist(p, ref) < 30)  # до ровной середины (с тенью)
    mid = scan(lambda p: is_mid(p, ref))  # до конца камня
    return mid if mid >= flat * 0.6 else flat


for name, fn, border, cfrac in FRAMES:
    path = os.path.join(ROOT, 'art-src', fn)
    if not os.path.isfile(path):
        print('нет', fn)
        continue
    im = Image.open(path).convert('RGBA')
    bbox = im.getchannel('A').point(lambda v: 255 if v > 30 else 0).getbbox()
    im = im.crop(bbox)
    w, h = im.size
    ref = im.getpixel((w // 2, int(h * 0.3)))
    if ref[3] < 128:  # середина прозрачная, вокруг - полупрозрачная тень: берём цвет тени
        for x in range(w // 2, 0, -1):
            p = im.getpixel((x, h // 2))
            if p[3] > 230:
                ref = p
                break
    ins = inset_of(im, ref)
    deep = int(min(w, h) * 0.24)  # глубже этого от края - только середина (там украшений нет)
    px = im.load()
    for y in range(ins, h - ins):
        for x in range(ins, w - ins):
            p = px[x, y]
            edge = min(x, y, w - 1 - x, h - 1 - y)
            if edge > deep or is_mid(p, ref) or (p[3] < 150 and edge > ins + 2):
                px[x, y] = (0, 0, 0, 0)
    k = border / float(ins)
    im = im.resize((max(8, int(w * k)), max(8, int(h * k))), Image.LANCZOS)
    corner = int(min(im.size) * cfrac)
    meta = {'inset': border, 'corner': corner, 'size': im.size}
    im.save(os.path.join(OUT, name + '.png'))
    json.dump(meta, open(os.path.join(OUT, name + '.json'), 'w'))
    print(name, 'исходник', (w, h), 'рамка', ins, '->', im.size, meta)

for out, fn in BANNERS:
    path = os.path.join(ROOT, 'art-src', fn)
    if not os.path.isfile(path):
        print('нет', fn)
        continue
    im = Image.open(path).convert('RGBA')
    w, h = im.size
    bh = int(w * 0.24)  # полоса по горизонту: шапка 150 px из 1400
    y0 = max(0, min(h - bh, int(h * 0.42) - bh // 2))
    band = im.crop((0, y0, w, y0 + bh)).resize((1400, int(1400 * bh / w)), Image.LANCZOS)
    band.save(os.path.join(ART, out))
    print(out, band.size)

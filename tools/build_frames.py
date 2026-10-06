"""Рамки окон из картинок ChatGPT (art-src/40_panel_frame.png, 41_card_frame.png) -> Оформление/frames_ui/*.png + .json.
Середина делается прозрачной (кроме листьев и лиан), размер подгоняется так, чтобы толщина камня была ~BORDER px.
Программа собирает окно любого размера из 9 частей: углы как есть, края повторяются, середина - ровный цвет.
python tools/build_frames.py"""
import json, os, sys
from PIL import Image
sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, 'library', 'Оформление', 'frames_ui')
os.makedirs(OUT, exist_ok=True)
SRC = [('panel', '40_panel_frame.png', 20, 0.32), ('card', '41_card_frame.png', 16, 0.46)]


def is_leafy(p):
    r, g, b, a = p
    return a > 40 and (g - max(r, b) > 14 or (r - b > 28 and r > 55 and g > 35))


def inset_of(im):
    """Толщина рамки: от левого края по средней строке до начала ровной середины."""
    w, h = im.size
    y = h // 2
    ref = im.getpixel((w // 2, int(h * 0.3)))
    for x in range(w // 2):
        p = im.getpixel((x, y))
        if p[3] > 200 and sum(abs(p[i] - ref[i]) for i in range(3)) < 24:
            ok = all(sum(abs(im.getpixel((x + d, y))[i] - ref[i]) for i in range(3)) < 24 for d in range(3, 15))
            if ok:
                return x, ref
    return w // 20, ref


for name, fn, border, cfrac in SRC:
    path = os.path.join(ROOT, 'art-src', fn)
    if not os.path.isfile(path):
        print('нет', fn)
        continue
    im = Image.open(path).convert('RGBA')
    # обрезать прозрачные поля
    bbox = im.getchannel('A').point(lambda v: 255 if v > 30 else 0).getbbox()
    im = im.crop(bbox)
    w, h = im.size
    ins, ref = inset_of(im)
    # середина: всё, что не листья, - прозрачное
    px = im.load()
    for y in range(ins, h - ins):
        for x in range(ins, w - ins):
            if not is_leafy(px[x, y]):
                px[x, y] = (0, 0, 0, 0)
    k = border / float(ins)
    im = im.resize((max(8, int(w * k)), max(8, int(h * k))), Image.LANCZOS)
    corner = int(min(im.size) * cfrac)
    meta = {'inset': border, 'corner': corner, 'size': im.size}
    im.save(os.path.join(OUT, name + '.png'))
    json.dump(meta, open(os.path.join(OUT, name + '.json'), 'w'))
    print(name, 'исходник', (w, h), 'рамка', ins, '->', im.size, meta)

"""Лист значков 58_btn_c (веб-ChatGPT, 14.10.2026): голос, общий мир, место на диске, перетаскивание, популярное,
обновление модов -> «Оформление/ic_<имя>.png» (18 px), _48 и _64. python tools/build_icons_c.py"""
import os
from PIL import Image
HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(os.path.dirname(HERE), 'art-src')
OUT = os.path.join(os.path.dirname(HERE), 'library', 'Оформление')
NAMES = ['mic', 'mic_off', 'headset', 'world_sync', 'disk', 'drop', 'chart', 'wrench']

sh = Image.open(os.path.join(RAW, '58_btn_c.png')).convert('RGBA')
solid_all = sh.getchannel('A').point(lambda a: 255 if a > 150 else 0)


def segments(profile, min_gap=12):
    """Отрезки, где есть непрозрачное, разделённые пустыми промежутками (значки не всегда влезают в сетку)."""
    out, start, gap = [], None, 0
    for i, v in enumerate(profile):
        if v:
            if start is None:
                start = i
            gap, last = 0, i
        elif start is not None:
            gap += 1
            if gap >= min_gap:
                out.append((start, last + 1))
                start = None
    if start is not None:
        out.append((start, last + 1))
    return out


cells = []
for y0, y1 in ((0, sh.height // 2), (sh.height // 2, sh.height)):
    band = solid_all.crop((0, y0, sh.width, y1))
    cols = [any(band.getpixel((x, y)) for y in range(0, band.height, 2)) for x in range(band.width)]
    segs = [sg for sg in segments(cols) if sg[1] - sg[0] > 60]
    assert len(segs) == 4, segs
    cells += [(x0, y0, x1, y1) for x0, x1 in segs]
for n, box in zip(NAMES, cells):
    cell = sh.crop(box)
    cell = cell.crop(cell.getchannel('A').point(lambda a: 255 if a > 150 else 0).getbbox())
    cell.putalpha(Image.eval(cell.getchannel('A'), lambda v: 0 if v < 110 else min(255, v * 2)))
    side = max(cell.size)
    sq = Image.new('RGBA', (side, side), (0, 0, 0, 0))
    sq.paste(cell, ((side - cell.width) // 2, (side - cell.height) // 2), cell)
    for size, suffix in ((18, ''), (24, '_24'), (48, '_48'), (64, '_64')):
        sq.resize((size, size), Image.LANCZOS).save(os.path.join(OUT, 'ic_%s%s.png' % (n, suffix)))
print('значки:', ', '.join(NAMES))

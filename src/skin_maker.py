"""Мастерская скинов Minecraft Java: процедурная сборка скина 64x64 из частей и цветов.

Только stdlib + Pillow. Совместим с Python 3.10+ (проверено на 3.14).

Публичное API:
    SKIN_PARTS            — варианты частей и палитры с русскими подписями
    default_spec()        — спецификация по умолчанию (dict)
    random_spec(seed)     — случайная, но гармоничная спецификация
    make_skin(spec)       — PIL.Image 64x64 RGBA в современном формате (с внешним слоем)
    render_preview(img)   — плоское превью «спереди + сзади» для быстрой проверки
    contact_sheet(items)  — сетка превью с подписями

Формат spec (все цвета — строки "#rrggbb"):
    model           "classic" | "slim"
    skin            цвет кожи
    hair            стиль причёски, hair_color — цвет волос (и бровей/бороды)
    eyes            стиль глаз, eye_color — цвет радужки
    face            рот/лицо
    top             верх, top_color — основной цвет, top_accent — второй цвет
                    (принт, галстук, табард, завязки; им же красится кепка)
    bottom          низ, bottom_color
    shoes           обувь, shoes_color
    accessory       аксессуар, accessory_color
Капюшон (hair="hood") красится цветом top_color, кепка — top_accent;
волосы под ними — hair_color.
"""
from __future__ import annotations

import colorsys
import hashlib
import random

from PIL import Image, ImageDraw, ImageFont

__all__ = [
    "SKIN_PARTS",
    "COLOR_KEYS",
    "default_spec",
    "random_spec",
    "make_skin",
    "render_preview",
    "contact_sheet",
    "is_slim",
]

# --------------------------------------------------------------------------------------
# Варианты и палитры
# --------------------------------------------------------------------------------------

SKIN_TONES = [
    ("#ffe3cf", "Фарфоровая"),
    ("#f7caa6", "Светлая"),
    ("#e9b089", "Персиковая"),
    ("#d69a6b", "Медовая"),
    ("#bd8156", "Смуглая"),
    ("#9a6240", "Карамельная"),
    ("#77482c", "Шоколадная"),
    ("#56331f", "Тёмная"),
    ("#8fd17a", "Зелёная (зомби)"),
    ("#8ab8f0", "Голубая (пришелец)"),
]
_NATURAL_SKIN = [c for c, _ in SKIN_TONES[:8]]

HAIR_COLORS = [
    ("#2a211f", "Чёрные"),
    ("#4a3022", "Тёмно-каштановые"),
    ("#704a2c", "Каштановые"),
    ("#9a6b3e", "Русые"),
    ("#c4612c", "Рыжие"),
    ("#e2bf6c", "Светлые"),
    ("#efe3c3", "Платиновые"),
    ("#9b9ba0", "Седые"),
    ("#f28cc2", "Розовые"),
    ("#5b8de8", "Синие"),
    ("#9b6ae0", "Фиолетовые"),
    ("#5fd2ae", "Мятные"),
    ("#cf3640", "Красные"),
]
_NATURAL_HAIR = [c for c, _ in HAIR_COLORS[:8]]
_FUN_HAIR = [c for c, _ in HAIR_COLORS[8:]]

EYE_COLORS = [
    ("#6b4226", "Карие"),
    ("#2e211b", "Тёмные"),
    ("#3b7bd6", "Голубые"),
    ("#3e9a4b", "Зелёные"),
    ("#7d8a96", "Серые"),
    ("#9a7330", "Ореховые"),
    ("#8b5cd6", "Фиолетовые"),
    ("#d23b3b", "Красные"),
]

CLOTHES_COLORS = [
    ("#d9433f", "Красный"),
    ("#f07a5a", "Коралловый"),
    ("#f29b38", "Оранжевый"),
    ("#f2d04a", "Жёлтый"),
    ("#8ccf4d", "Салатовый"),
    ("#3e9a54", "Зелёный"),
    ("#2fa3a0", "Бирюзовый"),
    ("#56b4e9", "Голубой"),
    ("#3567c8", "Синий"),
    ("#2b3557", "Тёмно-синий"),
    ("#7d4fc4", "Фиолетовый"),
    ("#ef87b5", "Розовый"),
    ("#f2efe8", "Белый"),
    ("#b9bcc4", "Светло-серый"),
    ("#6b6f78", "Серый"),
    ("#2d2d34", "Чёрный"),
    ("#7a5236", "Коричневый"),
    ("#d8c49a", "Бежевый"),
    ("#3b5a8f", "Джинсовый"),
    ("#e9b847", "Золотой"),
]

SKIN_PARTS: dict[str, list[tuple[str, str]]] = {
    "model": [("classic", "Обычные руки"), ("slim", "Тонкие руки")],
    "skin": SKIN_TONES,
    "hair": [
        ("short", "Короткие"),
        ("long", "Длинные"),
        ("ponytail", "Хвост"),
        ("bob", "Каре"),
        ("buzz", "Ёжик"),
        ("curly", "Кудри"),
        ("mohawk", "Ирокез"),
        ("bald", "Без волос"),
        ("cap", "Кепка"),
        ("hood", "Капюшон"),
    ],
    "hair_color": HAIR_COLORS,
    "eyes": [
        ("classic", "Обычные"),
        ("big", "Большие"),
        ("cute", "Милые"),
        ("sleepy", "Сонные"),
        ("wink", "Подмигивает"),
    ],
    "eye_color": EYE_COLORS,
    "face": [
        ("smile", "Улыбка"),
        ("neutral", "Спокойное"),
        ("grin", "Широкая улыбка"),
        ("blush", "Румянец"),
        ("freckles", "Веснушки"),
        ("mustache", "Усы"),
        ("beard", "Борода"),
    ],
    "top": [
        ("tshirt", "Футболка"),
        ("hoodie", "Худи"),
        ("jacket", "Куртка нараспашку"),
        ("tunic", "Рыцарская туника"),
        ("dress", "Платье"),
        ("suit", "Костюм"),
        ("sweater", "Свитер в полоску"),
    ],
    "bottom": [
        ("jeans", "Джинсы"),
        ("pants", "Брюки"),
        ("shorts", "Шорты"),
        ("skirt", "Юбка"),
    ],
    "shoes": [
        ("sneakers", "Кроссовки"),
        ("boots", "Ботинки"),
        ("sandals", "Сандалии"),
    ],
    "accessory": [
        ("none", "Без аксессуара"),
        ("glasses", "Очки"),
        ("sunglasses", "Тёмные очки"),
        ("headphones", "Наушники"),
        ("mask", "Маска"),
        ("scarf", "Шарф"),
        ("crown", "Корона"),
        ("cape", "Плащ с воротником"),
        ("backpack", "Рюкзак"),
        ("bow", "Бантик"),
    ],
    # общая палитра для одежды, обуви и аксессуаров
    "colors": CLOTHES_COLORS,
}

# какие ключи spec — цвета, и из какой палитры их предлагать в интерфейсе
COLOR_KEYS: dict[str, str] = {
    "skin": "skin",
    "hair_color": "hair_color",
    "eye_color": "eye_color",
    "top_color": "colors",
    "top_accent": "colors",
    "bottom_color": "colors",
    "shoes_color": "colors",
    "accessory_color": "colors",
}

_OPTION_KEYS = ("model", "hair", "eyes", "face", "top", "bottom", "shoes", "accessory")


def default_spec() -> dict:
    return {
        "model": "classic",
        "skin": "#e9b089",
        "hair": "short",
        "hair_color": "#704a2c",
        "eyes": "big",
        "eye_color": "#3b7bd6",
        "face": "smile",
        "top": "tshirt",
        "top_color": "#2fa3a0",
        "top_accent": "#f2efe8",
        "bottom": "jeans",
        "bottom_color": "#3b5a8f",
        "shoes": "sneakers",
        "shoes_color": "#d9433f",
        "accessory": "none",
        "accessory_color": "#2d2d34",
    }


# --------------------------------------------------------------------------------------
# Цвет
# --------------------------------------------------------------------------------------

RGB = tuple[int, int, int]


def _rgb(c) -> RGB:
    if isinstance(c, (tuple, list)):
        return (int(c[0]), int(c[1]), int(c[2]))
    s = str(c).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"Не цвет: {c!r}")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _hex(rgb: RGB) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in rgb)


def _cl(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def tone(rgb: RGB, k: float, warm: float = 1 / 6, cool: float = 2 / 3) -> RGB:
    """Осветлить (k>0, сдвиг оттенка к тёплому) или затемнить (k<0, к холодному)."""
    r, g, b = (v / 255 for v in rgb)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if k > 0:
        l = l + (1 - l) * k
        target = warm
    else:
        l = l * (1 + k)
        target = cool
        s = min(1.0, s * (1 - k * 0.25))
    if s > 0.06:
        dh = ((target - h + 0.5) % 1.0) - 0.5
        h = (h + dh * min(1.0, abs(k)) * 0.22) % 1.0
    r, g, b = colorsys.hls_to_rgb(h, _cl(l), _cl(s))
    return (round(r * 255), round(g * 255), round(b * 255))


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore


def _lum(c: RGB) -> float:
    return (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255


def _hls(h: float, l: float, s: float) -> str:
    r, g, b = colorsys.hls_to_rgb(h % 1.0, _cl(l), _cl(s))
    return _hex((round(r * 255), round(g * 255), round(b * 255)))


# --------------------------------------------------------------------------------------
# Раскладка UV
# --------------------------------------------------------------------------------------

FACES = ("top", "bottom", "right", "front", "left", "back")


class Box:
    """Коробка в развёртке Minecraft: (u, v) — левый верхний угол, w/h/d — размеры."""

    __slots__ = ("u", "v", "w", "h", "d")

    def __init__(self, u: int, v: int, w: int, h: int, d: int):
        self.u, self.v, self.w, self.h, self.d = u, v, w, h, d

    def face(self, name: str) -> tuple[int, int, int, int]:
        u, v, w, h, d = self.u, self.v, self.w, self.h, self.d
        if name == "top":
            return (u + d, v, w, d)
        if name == "bottom":
            return (u + d + w, v, w, d)
        if name == "right":  # правый бок персонажа; x=0 — у спины, x=d-1 — у лица
            return (u, v + d, d, h)
        if name == "front":  # x=0 — правая сторона персонажа (слева для зрителя)
            return (u + d, v + d, w, h)
        if name == "left":  # левый бок; x=0 — у лица, x=d-1 — у спины
            return (u + d + w, v + d, d, h)
        if name == "back":  # x=0 — левая сторона персонажа
            return (u + 2 * d + w, v + d, w, h)
        raise KeyError(name)

    @property
    def ring(self) -> int:
        return 2 * (self.w + self.d)

    def ring_face(self, s: int) -> tuple[str, int]:
        s %= self.ring
        d, w = self.d, self.w
        if s < d:
            return "right", s
        if s < d + w:
            return "front", s - d
        if s < 2 * d + w:
            return "left", s - d - w
        return "back", s - 2 * d - w

    def rects(self) -> list[tuple[int, int, int, int]]:
        return [self.face(f) for f in FACES]


PARTS = ("head", "body", "rarm", "larm", "rleg", "lleg")


def layout(model: str = "classic") -> dict[str, tuple[Box, Box]]:
    """Части скина: имя -> (базовый слой, внешний слой)."""
    aw = 3 if model == "slim" else 4
    return {
        "head": (Box(0, 0, 8, 8, 8), Box(32, 0, 8, 8, 8)),
        "body": (Box(16, 16, 8, 12, 4), Box(16, 32, 8, 12, 4)),
        "rarm": (Box(40, 16, aw, 12, 4), Box(40, 32, aw, 12, 4)),
        "larm": (Box(32, 48, aw, 12, 4), Box(48, 48, aw, 12, 4)),
        "rleg": (Box(0, 16, 4, 12, 4), Box(0, 32, 4, 12, 4)),
        "lleg": (Box(16, 48, 4, 12, 4), Box(0, 48, 4, 12, 4)),
    }


def _outer(part: str) -> str:
    return "right" if part[0] == "r" else "left"


def _inner(part: str) -> str:
    return "left" if part[0] == "r" else "right"


ARMS = ("rarm", "larm")
LEGS = ("rleg", "lleg")


# --------------------------------------------------------------------------------------
# Холст с затенением
# --------------------------------------------------------------------------------------


class _Canvas:
    def __init__(self, model: str, seed: int):
        self.img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        self.px = self.img.load()
        self.parts = layout(model)
        self.model = model
        rng = random.Random(seed)
        self.noise = [[rng.uniform(-1, 1) for _ in range(64)] for _ in range(64)]

    def box(self, part: str, over: bool = False) -> Box:
        return self.parts[part][1 if over else 0]

    def xy(self, part, face, x, y, over=False):
        fx, fy, fw, fh = self.box(part, over).face(face)
        if 0 <= x < fw and 0 <= y < fh:
            return fx + x, fy + y
        return None

    def light(self, part, face, x, y, over) -> float:
        fx, fy, fw, fh = self.box(part, over).face(face)
        if face == "top":
            k = 0.12
        elif face == "bottom":
            k = -0.22
        else:
            k = 0.08 - 0.2 * (y / max(1, fh - 1))
            k += {"front": 0.0, "right": -0.03, "left": -0.03, "back": -0.05}[face]
            if fw >= 6 and (x == 0 or x == fw - 1):
                k -= 0.04
        if over:
            k += 0.04
        return k

    def put(self, part, face, x, y, rgb, dk=0.0, over=False, tex=0.03, flat=False,
            warm=1 / 6, cool=2 / 3):
        p = self.xy(part, face, x, y, over)
        if p is None:
            return
        k = (0.0 if flat else self.light(part, face, x, y, over)) + dk
        k += tex * self.noise[p[1]][p[0]]
        self.px[p] = tone(rgb, k, warm, cool) + (255,)

    def raw(self, part, face, x, y, rgb, over=False):
        p = self.xy(part, face, x, y, over)
        if p is not None:
            self.px[p] = tuple(rgb[:3]) + (255,)

    def clear(self, part, face, x, y, over=True):
        p = self.xy(part, face, x, y, over)
        if p is not None:
            self.px[p] = (0, 0, 0, 0)

    def get(self, part, face, x, y, over=False):
        p = self.xy(part, face, x, y, over)
        return None if p is None else self.px[p]

    def rput(self, part, s, y, rgb, over=False, **kw):
        face, x = self.box(part, over).ring_face(s)
        self.put(part, face, x, y, rgb, over=over, **kw)

    def band(self, part, y0, y1, rgb, over=False, top=False, bottom=False, **kw):
        b = self.box(part, over)
        for s in range(b.ring):
            for y in range(max(0, y0), min(b.h, y1)):
                self.rput(part, s, y, rgb, over=over, **kw)
        if top:
            self.fill(part, "top", rgb, over=over, **kw)
        if bottom:
            self.fill(part, "bottom", rgb, over=over, **kw)

    def fill(self, part, face, rgb, over=False, **kw):
        _, _, fw, fh = self.box(part, over).face(face)
        for y in range(fh):
            for x in range(fw):
                self.put(part, face, x, y, rgb, over=over, **kw)

    def size(self, part, face, over=False):
        _, _, fw, fh = self.box(part, over).face(face)
        return fw, fh


# --------------------------------------------------------------------------------------
# Кожа и лицо
# --------------------------------------------------------------------------------------

SKIN_KW = dict(tex=0.012, warm=0.09, cool=0.98)


def _draw_skin(cv: _Canvas, skin: RGB):
    for part in PARTS:
        for face in FACES:
            cv.fill(part, face, skin, **SKIN_KW)
    # уши: светлый ободок и тёмная раковина
    for face in ("right", "left"):
        for (x, y, dk) in ((4, 3, 0.04), (4, 4, 0.0), (4, 5, 0.02), (3, 4, -0.14), (3, 3, -0.06),
                           (3, 5, -0.08)):
            xx = x if face == "right" else 7 - x
            cv.put("head", face, xx, y, skin, dk=dk, **SKIN_KW)
    # шея — тень
    cv.fill("head", "bottom", skin, dk=-0.08, **SKIN_KW)
    for arm in ARMS:
        cv.fill(arm, "bottom", skin, dk=-0.05, **SKIN_KW)


def _mouth_colors(skin: RGB) -> tuple[RGB, RGB]:
    lip = _mix(tone(skin, -0.42, cool=0.98), (150, 50, 60), 0.35)
    soft = _mix(tone(skin, -0.2, cool=0.98), (190, 90, 90), 0.2)
    return lip, soft


def _draw_eyes(cv: _Canvas, sp: dict, skin: RGB, hair: RGB):
    style = sp["eyes"]
    iris = _rgb(sp["eye_color"])
    white = (246, 246, 242)
    dark = tone(iris, -0.6)
    lite = tone(iris, 0.25)
    lash = tone(hair, -0.55) if _lum(hair) > 0.25 else (34, 26, 26)
    lash = _mix(lash, (40, 30, 30), 0.5)
    brow = _mix(tone(hair, -0.25), skin, 0.25)

    def P(x, y, c):
        cv.raw("head", "front", x, y, c)

    eyes = ((1, 2), (6, 5))  # (внешний, внутренний) столбец для правого и левого глаза
    for i, (o, n) in enumerate(eyes):
        st = style
        if style == "wink" and i == 1:
            st = "closed"
        if style == "wink" and i == 0:
            st = "big"
        if st == "classic":
            P(o, 4, white)
            P(n, 4, iris)
            P(o, 3, brow)
            P(n, 3, brow)
        elif st == "big":
            P(o, 3, white)
            P(o, 4, white)
            P(n, 3, lite)
            P(n, 4, dark)
        elif st == "cute":
            P(o, 3, dark)
            P(n, 3, white)
            P(o, 4, iris)
            P(n, 4, lite if _lum(iris) > 0.2 else tone(iris, 0.35))
        elif st == "sleepy":
            P(o, 4, lash)
            P(n, 4, lash)
            P(o, 3, tone(skin, -0.08, **{"cool": 0.98}))
            P(n, 3, tone(skin, -0.08, **{"cool": 0.98}))
        elif st == "closed":
            P(o, 4, lash)
            P(n, 4, lash)
            P(n, 3, lash)


def _draw_face(cv: _Canvas, sp: dict, skin: RGB, hair: RGB):
    face = sp["face"]
    lip, soft = _mouth_colors(skin)

    def P(x, y, c):
        cv.raw("head", "front", x, y, c)

    # еле заметный нос
    P(3, 5, tone(skin, -0.05, cool=0.98))
    P(4, 5, tone(skin, -0.09, cool=0.98))
    if face in ("smile", "blush", "freckles"):
        P(2, 5, soft)
        P(5, 5, soft)
        P(3, 6, lip)
        P(4, 6, lip)
        P(2, 6, soft)
        P(5, 6, soft)
    elif face == "neutral":
        P(3, 6, soft)
        P(4, 6, soft)
    elif face == "grin":
        teeth = (250, 250, 246)
        P(2, 6, lip)
        P(3, 6, teeth)
        P(4, 6, teeth)
        P(5, 6, lip)
        P(3, 7, tone(lip, -0.25))
        P(4, 7, tone(lip, -0.25))
    if face == "blush":
        pink = _mix(skin, (240, 110, 130), 0.45)
        for x in (0, 1, 6, 7):
            P(x, 5, pink)
    if face == "freckles":
        fr = _mix(tone(skin, -0.22, cool=0.02), (160, 90, 50), 0.35)
        for x, y in ((0, 5), (1, 6), (6, 6), (7, 5)):
            P(x, y, fr)
    if face in ("mustache", "beard"):
        hc = tone(hair, -0.05)
        if face == "beard":
            for y in (5, 6, 7):
                for x in range(8):
                    cv.put("head", "front", x, y, hc, dk=-0.04 * (y - 5), tex=0.05)
            # боковые бакенбарды и подбородок снизу
            for face_ in ("right", "left"):
                for xx in range(4):
                    x = 7 - xx if face_ == "right" else xx
                    for y in range(5 - (xx >= 2), 8):
                        cv.put("head", face_, x, y, hc, dk=-0.06, tex=0.05)
                    for y in range(3, 5):
                        if xx == 0:
                            cv.put("head", face_, x, y, hc, dk=-0.04, tex=0.05)
            for y in range(5, 8):
                for x in range(8):
                    cv.put("head", "bottom", x, y - 5, hc, dk=-0.12, tex=0.05)
            # рот в бороде
            P(3, 6, lip)
            P(4, 6, lip)
            P(2, 6, tone(hc, -0.15))
            P(5, 6, tone(hc, -0.15))
        else:
            P(3, 6, lip)
            P(4, 6, lip)
        for x in range(1, 7):
            cv.put("head", "front", x, 5, hc, dk=0.06 if x in (3, 4) else -0.02, tex=0.04)
        if face == "mustache":
            P(1, 6, tone(hc, -0.12))
            P(6, 6, tone(hc, -0.12))


# --------------------------------------------------------------------------------------
# Волосы
# --------------------------------------------------------------------------------------

# Длина волос по столбцам (сколько рядов сверху занято):
#   front — лицо, x=0..7 слева направо для зрителя;
#   side  — правый бок, x=0 (у спины) .. 7 (у лица); левый бок — зеркально;
#   back  — затылок.
_HAIR = {
    "short": dict(front=[3, 2, 2, 1, 2, 2, 2, 3], side=[6, 6, 6, 4, 3, 3, 3, 4],
                  back=[6, 6, 7, 6, 6, 7, 6, 6],
                  ofront=[1, 1, 1, 0, 1, 1, 1, 1], oside=[3, 3, 2, 2, 2, 1, 1, 1],
                  oback=[4, 5, 4, 5, 4, 5, 4, 5]),
    "long": dict(front=[8, 3, 2, 2, 1, 2, 3, 8], side=[8] * 8, back=[8] * 8,
                 ofront=[8, 1, 1, 1, 0, 1, 1, 8], oside=[8, 8, 8, 8, 8, 8, 8, 8],
                 oback=[8] * 8),
    "ponytail": dict(front=[3, 3, 2, 2, 2, 1, 1, 2], side=[7, 7, 6, 4, 3, 3, 3, 3],
                     back=[8] * 8,
                     ofront=[1, 1, 1, 1, 0, 0, 0, 1], oside=[2, 2, 1, 1, 1, 1, 1, 1],
                     oback=[3, 3, 3, 3, 3, 3, 3, 3]),
    "bob": dict(front=[7, 3, 3, 3, 3, 3, 3, 7], side=[7] * 8, back=[7] * 8,
                ofront=[7, 2, 1, 2, 1, 2, 2, 7], oside=[7] * 8, oback=[7] * 8),
    "buzz": dict(front=[2, 1, 1, 1, 1, 1, 1, 2], side=[5, 5, 4, 3, 2, 2, 2, 2],
                 back=[6, 6, 6, 6, 6, 6, 6, 6]),
    "curly": dict(front=[3, 2, 3, 2, 2, 3, 2, 3], side=[7, 7, 6, 6, 5, 4, 4, 4],
                  back=[7] * 8,
                  ofront=[3, 2, 2, 1, 2, 2, 2, 3], oside=[7, 7, 6, 6, 5, 4, 3, 3],
                  oback=[8, 7, 8, 7, 8, 7, 8, 7]),
    "mohawk": dict(front=[1, 1, 1, 2, 2, 1, 1, 1], side=[3, 3, 3, 3, 2, 2, 2, 2],
                   back=[5] * 8),
    "cap": dict(front=[3, 3, 3, 3, 3, 3, 3, 3], side=[6, 6, 5, 4, 4, 4, 4, 4],
                back=[6, 6, 6, 6, 6, 6, 6, 6]),
    "hood": dict(front=[3, 2, 2, 1, 2, 2, 2, 3], side=[6] * 8, back=[6] * 8),
}


def _side_len(prof, face, x):
    return prof[x] if face == "right" else prof[7 - x]


def _hair_k(s: int, y: int, L: int, top: bool = False) -> float:
    """Затенение пряди: светлее у макушки, тёмный кончик, чередование прядей."""
    k = 0.1 - 0.055 * y
    k += ((s * 5 + 3) % 4 - 1.5) * 0.025
    if y == L - 1 and L < 8:
        k -= 0.14
    if y == 1 and (s % 5) in (1, 2):
        k += 0.12  # блик
    return k


def _paint_hair_faces(cv, color, front, side, back, over=False, tex=0.05, stipple=None):
    head = "head"
    for face in ("front", "right", "left", "back"):
        for x in range(8):
            if face == "front":
                L = front[x]
            elif face == "back":
                L = back[x]
            else:
                L = _side_len(side, face, x)
            s = {"right": 0, "front": 8, "left": 16, "back": 24}[face] + x
            for y in range(L):
                c = color
                if stipple is not None and (x + y) % 2 == 1:
                    c = stipple
                cv.put(head, face, x, y, c, dk=_hair_k(s, y, L), over=over, flat=True, tex=tex)


def _paint_hair_top(cv, color, over=False, tex=0.05, stipple=None, cols=range(8)):
    for y in range(8):
        for x in cols:
            c = color
            if stipple is not None and (x + y) % 2 == 1:
                c = stipple
            k = 0.16 + ((x * 3 + y) % 4 - 1.5) * 0.025
            if y in (2, 5) and x in (2, 5):
                k -= 0.06
            cv.put("head", "top", x, y, c, dk=k, over=over, flat=True, tex=tex)


def _draw_hair(cv: _Canvas, sp: dict, skin: RGB):
    style = sp["hair"]
    hc = _rgb(sp["hair_color"])
    if style == "bald":
        # блик на макушке
        cv.put("head", "top", 3, 3, skin, dk=0.2, flat=True, tex=0)
        cv.put("head", "top", 4, 3, skin, dk=0.14, flat=True, tex=0)
        return
    p = _HAIR[style]
    if style in ("buzz", "mohawk"):
        stip = _mix(hc, skin, 0.45)
        base = _mix(hc, skin, 0.2)
        if style == "mohawk":  # выбритые виски: тень щетины, а не цвет ирокеза
            stip = tone(skin, -0.1, cool=0.98)
            base = _mix(tone(skin, -0.22, cool=0.98), (60, 45, 40), 0.25)
        _paint_hair_faces(cv, base, p["front"], p["side"], p["back"], stipple=stip, tex=0.03)
        _paint_hair_top(cv, base, stipple=stip, tex=0.03)
        if style == "mohawk":
            for x in (3, 4):
                for y in range(2):
                    cv.put("head", "front", x, y, hc, dk=_hair_k(8 + x, y, 2), flat=True)
                for y in range(6):
                    cv.put("head", "back", x, y, hc, dk=_hair_k(24 + x, y, 6), flat=True)
                for y in range(8):
                    cv.put("head", "top", x, y, hc, dk=0.14 - 0.03 * (x == 4), flat=True)
                    cv.put("head", "top", x, y, hc, dk=0.2 - 0.06 * (x == 4), flat=True,
                           over=True)
                cv.put("head", "front", x, 0, hc, dk=0.08, flat=True, over=True)
                for y in range(4):
                    cv.put("head", "back", x, y, hc, dk=0.06 - 0.05 * y, flat=True, over=True)
        return

    _paint_hair_faces(cv, hc, p["front"], p["side"], p["back"])
    _paint_hair_top(cv, hc)

    if style == "curly":
        _draw_curls(cv, hc, p)
    elif "ofront" in p:
        _paint_hair_faces(cv, tone(hc, 0.02), p["ofront"], p["oside"], p["oback"], over=True)
        _paint_hair_top(cv, hc, over=True)

    if style == "long":
        # волосы спадают на спину и плечи (внешний слой тела)
        for x, L in enumerate([3, 5, 6, 6, 6, 6, 5, 3]):
            for y in range(L):
                cv.put("body", "back", x, y, hc, dk=_hair_k(30 + x, y, L) - 0.03, over=True,
                       flat=True, tex=0.05)
        for face in ("right", "left"):
            for x in range(4):
                xx = x if face == "right" else 3 - x  # от спины к груди
                L = [5, 4, 2, 0][x]
                for y in range(L):
                    cv.put("body", face, xx, y, hc, dk=_hair_k(x, y, L) - 0.05, over=True,
                           flat=True, tex=0.05)
    elif style == "ponytail":
        tie = _rgb(sp["top_accent"])
        if _lum(tie) > 0.85 or abs(_lum(tie) - _lum(hc)) < 0.12:
            tie = (230, 70, 110)
        for x in (3, 4):
            cv.put("head", "back", x, 3, tie, dk=0.05 * (x == 3), flat=True, tex=0)
            cv.put("head", "back", x, 3, tie, dk=0.1 * (x == 3), flat=True, tex=0, over=True)
            for y in range(4, 8):
                cv.put("head", "back", x, y, hc, dk=0.06 - 0.03 * (y - 4) - 0.06 * (x == 4),
                       over=True, flat=True)
        for y, cols in enumerate([(3, 4), (3, 4), (3, 4), (3, 4), (3, 4), (4,)]):
            for x in cols:
                k = -0.02 - 0.04 * y - 0.06 * (x == 4)
                cv.put("body", "back", x, y, hc, dk=k, over=True, flat=True)
    elif style == "bob":
        # кончики, загнутые внутрь: тёмная нижняя кромка внешнего слоя
        for face in ("right", "left", "back"):
            for x in range(8):
                cv.put("head", face, x, 6, hc, dk=-0.2, over=True, flat=True)
    elif style == "cap":
        _draw_cap(cv, sp, hc)
    elif style == "hood":
        _draw_hood(cv, sp, hc)


def _draw_curls(cv, hc, p):
    """Кудри: кластеры 2x2 со светлым верхом и тёмным низом, неровный край."""
    def curl_k(s, y, L):
        cx, cy = (s + (y // 2) % 2) % 2, y % 2
        k = {(0, 0): 0.16, (1, 0): 0.05, (0, 1): -0.03, (1, 1): -0.17}[(cx, cy)]
        if y == L - 1:
            k -= 0.05
        return k

    for face in ("front", "right", "left", "back"):
        for x in range(8):
            if face == "front":
                L = p["ofront"][x]
            elif face == "back":
                L = p["oback"][x]
            else:
                L = _side_len(p["oside"], face, x)
            s = {"right": 0, "front": 8, "left": 16, "back": 24}[face] + x
            for y in range(L):
                cv.put("head", face, x, y, hc, dk=curl_k(s, y, L), over=True, flat=True,
                       tex=0.04)
    for y in range(8):
        for x in range(8):
            k = curl_k(x, y, 9) + 0.08
            cv.put("head", "top", x, y, hc, dk=k, over=True, flat=True, tex=0.04)
    # тёмные «провалы» между кудрями на базовом слое
    for face in ("right", "left", "back"):
        for x in range(8):
            for y in range(1, 6, 2):
                if (x + y) % 3 == 0 and cv.get("head", face, x, y) is not None:
                    cv.put("head", face, x, y, hc, dk=-0.2, flat=True, tex=0.03)


def _draw_cap(cv, sp, hc):
    c = _rgb(sp["top_accent"])
    if abs(_lum(c) - _lum(hc)) < 0.15:
        c = _rgb(sp["top_color"])
    if abs(_lum(c) - _lum(hc)) < 0.15:
        c = tone(c, -0.45 if _lum(hc) > 0.45 else 0.45)
    logo = (250, 250, 245) if _lum(c) < 0.6 else tone(c, -0.45)
    for over in (False, True):
        for face, rows in (("front", 2), ("right", 3), ("left", 3), ("back", 3)):
            for x in range(8):
                for y in range(rows):
                    cv.put("head", face, x, y, c, dk=0.06 - 0.05 * y + (0.03 if over else 0),
                           over=over, flat=True, tex=0.02)
        cv.fill("head", "top", c, over=over, flat=True, dk=0.14, tex=0.02)
        # пуговка на макушке и швы
        cv.put("head", "top", 3, 3, c, dk=-0.15, over=over, flat=True)
        cv.put("head", "top", 4, 4, c, dk=-0.15, over=over, flat=True)
    # козырёк: внешний слой, ряд 2 спереди + передняя кромка сверху
    for x in range(8):
        cv.put("head", "front", x, 2, c, dk=-0.28, over=True, flat=True, tex=0.02)
        cv.put("head", "top", x, 7, c, dk=0.22, over=True, flat=True, tex=0.02)
    for face in ("right", "left"):
        x = 7 if face == "right" else 0
        cv.put("head", face, x, 2, c, dk=-0.3, over=True, flat=True)
    # застёжка сзади
    for x in (3, 4):
        cv.put("head", "back", x, 2, hc, dk=-0.05, flat=True)
        cv.clear("head", "back", x, 2)
    # логотип
    for over in (False, True):
        cv.put("head", "front", 3, 0, logo, over=over, flat=True, tex=0)
        cv.put("head", "front", 4, 1, logo, over=over, flat=True, dk=-0.08, tex=0)


def _draw_hood(cv, sp, hc):
    c = _rgb(sp["top_color"])
    inner = tone(c, -0.45)
    for over in (False, True):
        dk0 = 0.04 if over else 0.0
        for face in ("right", "left", "back"):
            for x in range(8):
                for y in range(8):
                    cv.put("head", face, x, y, c, dk=dk0 + 0.05 - 0.035 * y, over=over, tex=0.03,
                           flat=True)
        cv.fill("head", "top", c, over=over, dk=0.12 + dk0, flat=True, tex=0.03)
        for x in range(8):
            cv.put("head", "front", x, 0, c, dk=0.06 + dk0, over=over, flat=True, tex=0.03)
        for y in range(1, 8):
            for x in (0, 7):
                if over:
                    cv.put("head", "front", x, y, c, dk=0.04 - 0.03 * y, over=True, flat=True)
                else:
                    cv.put("head", "front", x, y, inner, dk=-0.02 * y, flat=True)
    # шов посередине капюшона
    for y in range(8):
        cv.put("head", "top", 3, y, c, dk=-0.06, over=True, flat=True)
    for y in range(8):
        cv.put("head", "back", 3, y, c, dk=-0.08 - 0.03 * y, over=True, flat=True)
    # тень под кромкой капюшона
    for x in (1, 6):
        cv.put("head", "front", x, 1, hc, dk=-0.2, flat=True, tex=0.04)


# --------------------------------------------------------------------------------------
# Одежда
# --------------------------------------------------------------------------------------

CLOTH = dict(tex=0.03)


def _sleeves(cv, c, y1, cuff=None, cuff_dk=-0.14, top=True, **kw):
    for arm in ARMS:
        cv.band(arm, 0, y1, c, top=top, **kw)
        cv.band(arm, y1 - 1, y1, cuff or c, dk=cuff_dk if cuff is None else 0, **kw)


def _neck(cv, skin, cols=(3, 4)):
    for x in cols:
        cv.put("body", "front", x, 0, skin, dk=-0.06, **SKIN_KW)


def _top_tshirt(cv, c, acc, skin):
    cv.band("body", 0, 8, c, top=True, **CLOTH)
    cv.band("body", 7, 8, c, dk=-0.14, **CLOTH)
    _neck(cv, skin)
    cv.put("body", "front", 2, 0, c, dk=-0.1)
    cv.put("body", "front", 5, 0, c, dk=-0.1)
    _sleeves(cv, c, 4, **CLOTH)
    # подмышки и боковые швы
    for arm in ARMS:
        cv.put(arm, _inner(arm), 1, 3, c, dk=-0.25)
        cv.put(arm, _inner(arm), 2, 3, c, dk=-0.25)
    # принт-звёздочка
    if abs(_lum(acc) - _lum(c)) < 0.12:
        acc = (250, 250, 245) if _lum(c) < 0.6 else (40, 40, 48)
    for x, y, dk in ((4, 2, 0.05), (3, 3, 0.05), (4, 3, 0.1), (5, 3, -0.02), (4, 4, -0.06)):
        cv.put("body", "front", x, y, acc, dk=dk, flat=True, tex=0)


def _top_hoodie(cv, c, acc, skin, hood_up):
    cv.band("body", 0, 10, c, top=True, **CLOTH)
    for s in range(cv.box("body").ring):
        cv.rput("body", s, 9, c, dk=-0.12 - 0.06 * (s % 2))
    # карман-кенгуру
    for x in range(1, 7):
        cv.put("body", "front", x, 6, c, dk=-0.16 if x not in (1, 6) else -0.22)
        for y in (7, 8):
            cv.put("body", "front", x, y, c, dk=-0.05 if x not in (1, 6) else -0.18)
    # завязки
    sc = (245, 245, 240) if _lum(c) < 0.75 else (90, 90, 100)
    if abs(_lum(acc) - _lum(c)) > 0.2:
        sc = acc
    cv.put("body", "front", 3, 0, c, dk=-0.35)
    cv.put("body", "front", 4, 0, c, dk=-0.35)
    for x, y in ((2, 1), (2, 2), (5, 1), (5, 2), (5, 3)):
        cv.put("body", "front", x, y, sc, dk=0.05 - 0.05 * y, flat=True, tex=0)
    _sleeves(cv, c, 10, **CLOTH)
    if not hood_up:
        # капюшон лежит на спине
        for x in range(1, 7):
            for y in range(3):
                k = 0.02 - 0.06 * y - (0.08 if x in (1, 6) else 0)
                cv.put("body", "back", x, y, c, dk=k, over=True, flat=True)
        for x in (2, 3, 4, 5):
            cv.put("body", "back", x, 0, c, dk=-0.4, over=True, flat=True)
        for x in range(1, 7):
            cv.put("body", "top", x, 0, c, dk=0.05, over=True, flat=True)


def _top_jacket(cv, c, acc, skin):
    inner = acc if abs(_lum(acc) - _lum(c)) > 0.15 else (242, 240, 232)
    cv.band("body", 0, 9, c, top=True, **CLOTH)
    for y in range(9):
        for x in (3, 4):
            cv.put("body", "front", x, y, inner, **CLOTH)
        if y < 8:
            cv.put("body", "front", 2, y, c, dk=-0.2)
            cv.put("body", "front", 5, y, c, dk=-0.12)
    cv.band("body", 8, 9, c, dk=-0.14, **CLOTH)
    _neck(cv, skin)
    # воротник
    for x, y in ((1, 0), (2, 0), (5, 0), (6, 0), (2, 1), (5, 1)):
        cv.put("body", "front", x, y, c, dk=0.15)
    for face in ("right", "left", "back"):
        w, _ = cv.size("body", face, over=True)
        for x in range(w):
            cv.put("body", face, x, 0, c, dk=0.12, over=True)
    for x, y in ((1, 0), (6, 0), (2, 0), (5, 0)):
        cv.put("body", "front", x, y, c, dk=0.18, over=True)
    # карманы
    for x in (0, 1, 6, 7):
        cv.put("body", "front", x, 6, c, dk=-0.2)
    _sleeves(cv, c, 10, **CLOTH)
    for arm in ARMS:
        cv.band(arm, 4, 5, c, dk=-0.07)


def _top_tunic(cv, c, acc, skin):
    mail = (150, 156, 168)
    for y in range(10):
        for s in range(cv.box("body").ring):
            k = 0.05 if (s + y) % 2 == 0 else -0.1
            cv.rput("body", s, y, mail, dk=k, tex=0.04)
    cv.fill("body", "top", mail)
    for arm in ARMS:
        for y in range(7):
            for s in range(cv.box(arm).ring):
                cv.rput(arm, s, y, mail, dk=(0.05 if (s + y) % 2 == 0 else -0.1), tex=0.04)
        cv.fill(arm, "top", mail)
        cv.band(arm, 6, 7, mail, dk=-0.25)
        # кожаные перчатки
        cv.band(arm, 9, 12, (110, 74, 48), tex=0.04, bottom=True)
        cv.band(arm, 9, 10, (110, 74, 48), dk=0.12)
    trim = acc if abs(_lum(acc) - _lum(c)) > 0.15 else (233, 184, 71)
    for face in ("front", "back"):
        for y in range(1, 11):
            for x in range(1, 7):
                k = 0.0
                if x in (1, 6):
                    k = -0.12
                cv.put("body", face, x, y, c, dk=k, tex=0.03)
        for x in range(1, 7):
            cv.put("body", face, x, 10, trim, dk=-0.05)
            cv.put("body", face, x, 1, trim, dk=0.05)
    # герб
    for x, y in ((3, 3), (4, 3), (3, 4), (4, 4), (2, 4), (5, 4), (3, 5), (4, 5), (3, 6), (4, 6)):
        cv.put("body", "front", x, y, trim, dk=0.1 if y == 3 else 0.0, tex=0.02)
    # ремень
    belt = (92, 60, 38)
    cv.band("body", 7, 8, belt, tex=0.03)
    cv.put("body", "front", 3, 7, (233, 200, 90), flat=True, tex=0)
    cv.put("body", "front", 4, 7, (200, 165, 70), flat=True, tex=0)
    # наплечники
    plate = (190, 196, 206)
    for arm in ARMS:
        cv.band(arm, 0, 3, plate, over=True, top=True, tex=0.03)
        cv.band(arm, 2, 3, plate, over=True, dk=-0.3)
        cv.put(arm, _outer(arm), 1, 1, plate, dk=0.35, over=True, flat=True)


def _top_dress(cv, c, acc, skin):
    cv.band("body", 0, 12, c, top=True, **CLOTH)
    _neck(cv, skin, cols=(2, 3, 4, 5))
    cv.put("body", "front", 3, 1, skin, dk=-0.1, **SKIN_KW)
    cv.put("body", "front", 4, 1, skin, dk=-0.1, **SKIN_KW)
    sash = acc if abs(_lum(acc) - _lum(c)) > 0.12 else tone(c, -0.35)
    cv.band("body", 5, 6, sash, tex=0.02)
    # бантик сзади
    for x, y in ((2, 5), (5, 5), (3, 6), (4, 6)):
        cv.put("body", "back", x, y, sash, dk=0.08, over=True, flat=True)
    for x in (3, 4):
        cv.put("body", "back", x, 5, sash, dk=-0.12, over=True, flat=True)
    # складки юбки
    for y in range(6, 12):
        for s in range(cv.box("body").ring):
            if s % 2 == 0:
                cv.rput("body", s, y, c, dk=0.06 - 0.02 * (y - 6))
    for leg in LEGS:
        cv.band(leg, 0, 5, c, top=True, **CLOTH)
        for s in range(cv.box(leg).ring):
            for y in range(5):
                cv.rput(leg, s, y, c, dk=(0.05 if s % 2 == 0 else -0.06) - 0.03 * y)
        cv.band(leg, 4, 5, sash, dk=-0.05)
        # пышная юбка на внешнем слое
        for s in range(cv.box(leg, True).ring):
            for y in range(5):
                if y < 4 or s % 2 == 0:
                    cv.rput(leg, s, y, c, over=True, dk=(0.06 if s % 2 == 0 else -0.04) - 0.05 * y)
        cv.band(leg, 4, 5, sash, over=True, dk=-0.02)
    # рукава-фонарики
    _sleeves(cv, c, 3, **CLOTH)
    for arm in ARMS:
        cv.band(arm, 0, 3, c, over=True, top=True, dk=0.04)
        cv.band(arm, 2, 3, sash, over=True)


def _top_suit(cv, c, acc, skin):
    shirt = (244, 244, 240)
    tie = acc if abs(_lum(acc) - _lum(c)) > 0.12 and _lum(acc) < 0.9 else (205, 55, 60)
    cv.band("body", 0, 10, c, top=True, **CLOTH)
    for x in range(2, 6):
        cv.put("body", "front", x, 0, shirt, dk=0.02)
    for x in (2, 5):
        cv.put("body", "front", x, 1, shirt, dk=-0.02)
    for y in range(0, 6):
        for x in (3, 4):
            cv.put("body", "front", x, y, tie, dk=(0.08 if x == 3 else -0.04) - 0.03 * y,
                   tex=0.02)
    cv.put("body", "front", 3, 0, tie, dk=-0.1)
    cv.put("body", "front", 4, 0, tie, dk=-0.18)
    for x, y in ((2, 2), (2, 3), (5, 2), (5, 3), (2, 4), (5, 4)):
        cv.put("body", "front", x, y, c, dk=-0.22)
    for x, y in ((1, 1), (6, 1)):
        cv.put("body", "front", x, y, c, dk=0.12)
    for y in (6, 8):
        cv.put("body", "front", 3, y, c, dk=-0.4, flat=True)
    cv.put("body", "front", 6, 3, shirt, dk=-0.05, flat=True)  # платочек
    cv.band("body", 9, 10, c, dk=-0.14)
    _sleeves(cv, c, 10, **CLOTH)
    for arm in ARMS:
        cv.band(arm, 9, 10, shirt, dk=-0.04)
        cv.band(arm, 8, 9, c, dk=-0.12)


def _top_sweater(cv, c, acc, skin):
    stripe = acc if abs(_lum(acc) - _lum(c)) > 0.12 else tone(c, 0.5)
    parts = ("body",) + ARMS
    for part in parts:
        n = 10
        for y in range(n):
            for s in range(cv.box(part).ring):
                col = stripe if y in (3, 4, 7) else c
                k = 0.03 if (s + y) % 2 == 0 else -0.03
                cv.rput(part, s, y, col, dk=k)
        cv.fill(part, "top", c)
        cv.band(part, 9, 10, c, dk=-0.16)
        for s in range(0, cv.box(part).ring, 2):
            cv.rput(part, s, 9, c, dk=-0.24)
    cv.band("body", 0, 1, c, dk=-0.12)
    _neck(cv, skin)


# --------------------------------------------------------------------------------------
# Низ и обувь
# --------------------------------------------------------------------------------------


def _bottom(cv, sp, skin):
    kind = sp["bottom"]
    c = _rgb(sp["bottom_color"])
    belt = (84, 56, 38) if _lum(c) > 0.3 else (130, 100, 75)
    buckle = (210, 210, 200)
    if kind in ("jeans", "pants", "shorts"):
        tex = 0.07 if kind == "jeans" else 0.03
        cv.band("body", 8, 12, c, bottom=True, tex=tex)
        cv.band("body", 8, 9, belt, tex=0.03)
        cv.put("body", "front", 3, 8, buckle, flat=True, tex=0)
        cv.put("body", "front", 4, 8, tone(buckle, -0.2), flat=True, tex=0)
        cv.put("body", "front", 3, 9, c, dk=-0.22)
        cv.put("body", "front", 3, 10, c, dk=-0.18)
        if kind == "jeans":
            for x, y in ((1, 9), (6, 9), (0, 10), (7, 10)):
                cv.put("body", "front", x, y, c, dk=-0.2)
            for x, y in ((1, 9), (2, 9), (5, 9), (6, 9), (1, 10), (6, 10)):
                cv.put("body", "back", x, y, c, dk=-0.15)
        length = 12 if kind != "shorts" else 5
        for leg in LEGS:
            cv.band(leg, 0, length, c, top=True, bottom=True, tex=tex)
            if kind == "jeans":
                # шов по внешней стороне, светлые колени, подвёрнутые края
                for y in range(0, 10, 2):
                    cv.put(leg, _outer(leg), 2, y, c, dk=0.16, flat=True)
                for y in (5, 6):
                    for x in (1, 2):
                        cv.put(leg, "front", x, y, c, dk=0.08)
                cv.band(leg, 9, 10, c, dk=0.14)
            elif kind == "pants":
                xc = 1 if leg == "rleg" else 2
                for y in range(1, 11):
                    cv.put(leg, "front", xc, y, c, dk=0.1)
                cv.band(leg, 11, 12, c, dk=-0.16)
            else:
                cv.band(leg, 4, 5, c, dk=-0.12)
                cv.band(leg, 3, 4, c, dk=-0.04)
        # тень между ногами
        for y in range(1, length):
            cv.put("rleg", "left", 1, y, c, dk=-0.14)
            cv.put("lleg", "right", 2, y, c, dk=-0.14)
    elif kind == "skirt":
        cv.band("body", 8, 12, c, bottom=True, **CLOTH)
        cv.band("body", 8, 9, c, dk=-0.15)
        for y in range(9, 12):
            for s in range(0, cv.box("body").ring, 2):
                cv.rput("body", s, y, c, dk=0.07)
        for leg in LEGS:
            for y in range(4):
                for s in range(cv.box(leg).ring):
                    cv.rput(leg, s, y, c, dk=(0.06 if s % 2 == 0 else -0.06) - 0.03 * y)
            cv.fill(leg, "top", c)
            cv.band(leg, 3, 4, c, dk=-0.16)
            for s in range(cv.box(leg, True).ring):
                for y in range(4):
                    if y < 3 or s % 2 == 0:
                        cv.rput(leg, s, y, c, over=True,
                                dk=(0.08 if s % 2 == 0 else -0.04) - 0.06 * y)
        for s in range(0, cv.box("body", True).ring):
            for y in range(9, 12):
                cv.rput("body", s, y, c, over=True, dk=(0.08 if s % 2 == 0 else -0.04) - 0.04 * (y - 9))


def _shoes(cv, sp, skin):
    kind = sp["shoes"]
    c = _rgb(sp["shoes_color"])
    bare = sp["bottom"] in ("shorts", "skirt")
    if kind == "sneakers":
        sole = (236, 234, 228) if _lum(c) < 0.8 else (60, 60, 68)
        for leg in LEGS:
            if bare:
                cv.band(leg, 8, 10, (244, 244, 240), tex=0.02)
                cv.band(leg, 8, 9, (244, 244, 240), dk=-0.1, tex=0.02)
            cv.band(leg, 10, 11, c, tex=0.03, dk=0.04)
            cv.band(leg, 11, 12, sole, tex=0.02, flat=True)
            cv.fill(leg, "bottom", sole, dk=-0.25)
            # шнурки и носок
            cv.put(leg, "front", 1, 10, (250, 250, 248), flat=True, tex=0)
            cv.put(leg, "front", 2, 10, (225, 225, 222), flat=True, tex=0)
            cv.put(leg, _outer(leg), 1, 10, c, dk=0.25)  # «галочка»
            cv.put(leg, _outer(leg), 2, 10, c, dk=0.25)
    elif kind == "boots":
        for leg in LEGS:
            cv.band(leg, 7, 12, c, tex=0.04)
            cv.band(leg, 7, 8, c, dk=0.16)
            cv.band(leg, 11, 12, c, dk=-0.45)
            cv.fill(leg, "bottom", c, dk=-0.5)
            lace = tone(c, 0.45)
            for y in (8, 9, 10):
                cv.put(leg, "front", 1 + (y % 2), y, lace, flat=True, tex=0)
                cv.put(leg, "front", 2 - (y % 2), y, c, dk=-0.12)
    elif kind == "sandals":
        sole = tone(c, -0.35)
        for leg in LEGS:
            cv.band(leg, 11, 12, sole, tex=0.02)
            cv.fill(leg, "bottom", sole, dk=-0.1)
            for x in range(4):
                cv.put(leg, "front", x, 11, skin, dk=-0.05 if x % 2 else 0.02, **SKIN_KW)
                cv.put(leg, "front", x, 10, c, dk=0.05)
            for face in ("right", "left"):
                cv.put(leg, face, 3 if face == "right" else 0, 10, c)
                cv.put(leg, face, 2 if face == "right" else 1, 9, c)
            cv.put(leg, "back", 1, 10, c, dk=-0.05)
            cv.put(leg, "back", 2, 10, c, dk=-0.05)


# --------------------------------------------------------------------------------------
# Аксессуары
# --------------------------------------------------------------------------------------


def _acc(cv, sp, skin, hair):
    kind = sp["accessory"]
    c = _rgb(sp["accessory_color"])
    H = "head"
    if kind == "none":
        return
    if kind == "glasses":
        frame = c
        rim = [(1, 2), (2, 2), (5, 2), (6, 2), (0, 3), (3, 3), (4, 3), (7, 3),
               (0, 4), (3, 4), (4, 4), (7, 4), (1, 5), (2, 5), (5, 5), (6, 5)]
        for x, y in rim:
            cv.put(H, "front", x, y, frame, over=True, dk=0.05 - 0.04 * (y - 2), flat=True,
                   tex=0)
        for face in ("right", "left"):
            for i in range(5):
                x = 7 - i if face == "right" else i
                cv.put(H, face, x, 3, frame, over=True, dk=-0.1, flat=True, tex=0)
    elif kind == "sunglasses":
        lens = _mix((22, 22, 32), c, 0.15)
        for x in range(8):
            cv.put(H, "front", x, 3, c if x in (0, 3, 4, 7) else lens, over=True, dk=-0.05,
                   flat=True, tex=0)
        for x in (1, 2, 5, 6):
            cv.put(H, "front", x, 4, lens, over=True, dk=0.0, flat=True, tex=0)
        cv.put(H, "front", 1, 3, lens, over=True, dk=0.55, flat=True, tex=0)
        cv.put(H, "front", 5, 3, lens, over=True, dk=0.55, flat=True, tex=0)
        cv.put(H, "front", 2, 4, lens, over=True, dk=0.2, flat=True, tex=0)
        cv.put(H, "front", 6, 4, lens, over=True, dk=0.2, flat=True, tex=0)
        for face in ("right", "left"):
            for i in range(5):
                x = 7 - i if face == "right" else i
                cv.put(H, face, x, 3, c, over=True, dk=-0.15, flat=True, tex=0)
    elif kind == "headphones":
        for x in range(8):
            for y in (3, 4):
                cv.put(H, "top", x, y, c, over=True, dk=0.15 - 0.1 * (y == 4), flat=True)
        for face in ("right", "left"):
            for y in range(3):
                for x in (3, 4):
                    cv.put(H, face, x, y, c, over=True, dk=0.1 - 0.06 * (x == 4), flat=True)
            for y in range(3, 7):
                for x in range(2, 6):
                    k = 0.0
                    if y in (3, 6) or x in (2, 5):
                        k = -0.25
                    if (x, y) in ((3, 4), (4, 4)):
                        k = 0.18
                    cv.put(H, face, x, y, c, over=True, dk=k, flat=True)
    elif kind == "mask":
        for x in range(8):
            for y in (5, 6, 7):
                k = 0.08 - 0.06 * (y - 5)
                if y == 6:
                    k -= 0.05
                cv.put(H, "front", x, y, c, over=True, dk=k, flat=True, tex=0.02)
        for face in ("right", "left"):
            for i in range(4):
                x = 7 - i if face == "right" else i
                cv.put(H, face, x, 5, c, over=True, dk=-0.1, flat=True)
    elif kind == "scarf":
        light = tone(c, 0.35)
        b = cv.box("body", True)
        for s in range(b.ring):
            for y in (0, 1):
                col = light if (s // 2) % 2 == 0 and y == 1 else c
                cv.rput("body", s, y, col, over=True, dk=0.05 - 0.08 * y, flat=True)
        cv.fill("body", "top", c, over=True, dk=0.12, flat=True)
        for y in range(2, 7):
            for x in (5, 6):
                col = light if y % 2 == 0 else c
                k = -0.06 * (y - 2) + (0.04 if x == 5 else -0.06)
                cv.put("body", "front", x, y, col, over=True, dk=k, flat=True)
        cv.clear("body", "front", 5, 6)
        cv.put("body", "front", 6, 7, c, over=True, dk=-0.3, flat=True)
        for y in range(0, 2):
            for x in range(8):
                cv.put(H, "bottom", x, 7 - y, c, dk=-0.1)
    elif kind == "crown":
        gold = c if sp.get("accessory_color") else (233, 184, 71)
        gem = (220, 50, 70) if abs(gold[0] - 220) > 40 or gold[1] > 120 else (70, 160, 230)
        b = cv.box(H, True)
        for s in range(b.ring):
            cv.rput(H, s, 1, gold, over=True, dk=-0.05 + (0.1 if s % 2 else 0), flat=True)
            if s % 3 == 1:
                cv.rput(H, s, 0, gold, over=True, dk=0.2, flat=True)
        for x in range(8):
            for y in range(8):
                if x in (0, 7) or y in (0, 7):
                    cv.put(H, "top", x, y, gold, over=True, dk=0.12, flat=True)
        cv.put(H, "front", 3, 1, gem, over=True, flat=True, dk=0.15, tex=0)
        cv.put(H, "front", 4, 1, gem, over=True, flat=True, dk=-0.1, tex=0)
        for face in ("right", "left", "back"):
            cv.put(H, face, 3, 1, gem, over=True, flat=True, dk=0.0, tex=0)
    elif kind == "cape":
        lining = tone(c, -0.38)
        clasp = (236, 196, 80)
        for x in range(8):
            for y in range(12):
                k = 0.04 - 0.02 * y + (-0.08 if x in (0, 7) else 0) + (0.05 if x % 3 == 1 else 0)
                cv.put("body", "back", x, y, c, over=True, dk=k, flat=True, tex=0.02)
        for face in ("right", "left"):
            for x in range(4):
                for y in range(3):
                    if (face == "right" and x <= 2) or (face == "left" and x >= 1):
                        cv.put("body", face, x, y, c, over=True, dk=0.05 - 0.05 * y, flat=True)
            for y in range(3, 12):
                xx = 0 if face == "right" else 3
                cv.put("body", face, xx, y, lining, over=True, dk=-0.02 * y, flat=True)
        for x in (0, 1, 6, 7):
            cv.put("body", "front", x, 0, c, over=True, dk=0.1, flat=True)
            cv.put("body", "front", x, 1, lining, over=True, flat=True)
        cv.put("body", "front", 2, 0, clasp, over=True, flat=True, tex=0)
        cv.put("body", "front", 5, 0, clasp, over=True, flat=True, tex=0)
        cv.fill("body", "top", c, over=True, dk=0.1, flat=True)
        for arm in ARMS:
            cv.fill(arm, "top", c, over=True, dk=0.12, flat=True)
            b = cv.box(arm, True)
            for s in range(b.ring):
                cv.rput(arm, s, 0, c, over=True, dk=0.06, flat=True)
                face, _ = b.ring_face(s)
                if face in ("back", _outer(arm)):
                    cv.rput(arm, s, 1, c, over=True, dk=-0.04, flat=True)
                    cv.rput(arm, s, 2, c, over=True, dk=-0.22, flat=True)
                else:
                    cv.rput(arm, s, 1, c, over=True, dk=-0.2, flat=True)
    elif kind == "backpack":
        strap = tone(c, -0.3)
        for x in range(1, 7):
            for y in range(1, 10):
                k = 0.05 - 0.02 * y + (-0.12 if x in (1, 6) else 0)
                cv.put("body", "back", x, y, c, over=True, dk=k, flat=True, tex=0.03)
        for x in range(1, 7):
            cv.put("body", "back", x, 1, c, over=True, dk=0.12, flat=True)
            cv.put("body", "back", x, 4, c, over=True, dk=-0.3, flat=True)
        for x in range(2, 6):
            for y in (6, 7, 8):
                cv.put("body", "back", x, y, c, over=True, dk=0.08 - 0.06 * (y - 6), flat=True)
            cv.put("body", "back", x, 6, c, over=True, dk=-0.22, flat=True)
        cv.put("body", "back", 3, 4, (235, 225, 160), over=True, flat=True, tex=0)
        cv.put("body", "back", 4, 4, (200, 190, 130), over=True, flat=True, tex=0)
        for y in range(0, 8):
            for x in (1, 6):
                cv.put("body", "front", x, y, strap, over=True, dk=-0.02 * y, flat=True)
        for x in (1, 6):
            for y in range(4):
                cv.put("body", "top", x, y, strap, over=True, flat=True)
        for face in ("right", "left"):
            for y in range(1, 10):
                xx = 0 if face == "right" else 3
                cv.put("body", face, xx, y, c, over=True, dk=-0.15, flat=True)
    elif kind == "bow":
        shape = ["XX.XX", "XXOXX", "X...X"]
        for y, row in enumerate(shape):
            for i, ch in enumerate(row):
                if ch == ".":
                    continue
                k = 0.12 - 0.12 * y
                if ch == "O":
                    k = -0.22
                cv.put(H, "left", 1 + i, y, c, over=True, dk=k, flat=True, tex=0)
        # бант на макушке сбоку виден и спереди
        for x, y, k in ((5, 0, 0.14), (7, 0, 0.14), (5, 1, 0.0), (6, 1, -0.22), (7, 1, 0.0),
                        (6, 0, -0.05)):
            cv.put(H, "front", x, y, c, over=True, dk=k, flat=True, tex=0)
        for x in range(4, 8):
            for y in range(5, 8):
                cv.put(H, "top", x, y, c, over=True, dk=0.18 - 0.2 * (x == 6), flat=True)


# --------------------------------------------------------------------------------------
# Сборка
# --------------------------------------------------------------------------------------


def _normalize(spec: dict | None) -> dict:
    sp = default_spec()
    if spec:
        sp.update({k: v for k, v in spec.items() if v is not None})
    for key in _OPTION_KEYS:
        valid = [k for k, _ in SKIN_PARTS[key]]
        if sp[key] not in valid:
            raise ValueError(f"{key}={sp[key]!r}: допустимо {valid}")
    for key in COLOR_KEYS:
        sp[key] = _hex(_rgb(sp[key]))
    return sp


def _spec_seed(sp: dict) -> int:
    if "seed" in sp and isinstance(sp["seed"], int):
        return sp["seed"]
    h = hashlib.md5(repr(sorted((k, str(v)) for k, v in sp.items())).encode()).hexdigest()
    return int(h[:8], 16)


def make_skin(spec: dict | None = None) -> Image.Image:
    """Собрать скин 64x64 RGBA (современный формат, внешний слой)."""
    sp = _normalize(spec)
    cv = _Canvas(sp["model"], _spec_seed(sp))
    skin = _rgb(sp["skin"])
    hair = _rgb(sp["hair_color"])
    top = sp["top"]
    c, acc = _rgb(sp["top_color"]), _rgb(sp["top_accent"])

    _draw_skin(cv, skin)
    _bottom(cv, sp, skin)
    _shoes(cv, sp, skin)
    if top == "tshirt":
        _top_tshirt(cv, c, acc, skin)
    elif top == "hoodie":
        _top_hoodie(cv, c, acc, skin, hood_up=sp["hair"] == "hood")
    elif top == "jacket":
        _top_jacket(cv, c, acc, skin)
    elif top == "tunic":
        _top_tunic(cv, c, acc, skin)
    elif top == "dress":
        _top_dress(cv, c, acc, skin)
    elif top == "suit":
        _top_suit(cv, c, acc, skin)
    elif top == "sweater":
        _top_sweater(cv, c, acc, skin)
    _draw_eyes(cv, sp, skin, hair)
    _draw_face(cv, sp, skin, hair)
    _draw_hair(cv, sp, skin)
    _acc(cv, sp, skin, hair)
    return cv.img


# --------------------------------------------------------------------------------------
# Случайный гармоничный скин
# --------------------------------------------------------------------------------------

_NEUTRAL_BOTTOMS = ["#3b5a8f", "#2f4470", "#2d2d34", "#55596a", "#b59a6a", "#ebe7de",
                    "#2b3557", "#6b4a35", "#7a8796"]
_DENIM = ["#3b5a8f", "#2f4470", "#4a6fa8", "#5b7fb5", "#2d3550", "#6b8cc4"]


def random_spec(seed=None) -> dict:
    rng = random.Random(seed)
    sp = default_spec()
    pick = rng.choice
    sp["model"] = pick(["classic", "slim"])
    sp["skin"] = pick(_NATURAL_SKIN) if rng.random() > 0.05 else pick(
        [c for c, _ in SKIN_TONES[8:]])
    sp["hair"] = rng.choices([k for k, _ in SKIN_PARTS["hair"]],
                             [5, 5, 4, 3, 2, 3, 1, 1, 2, 2])[0]
    sp["hair_color"] = pick(_NATURAL_HAIR) if rng.random() > 0.2 else pick(_FUN_HAIR)
    sp["eyes"] = rng.choices(["classic", "big", "cute", "sleepy", "wink"], [3, 4, 3, 1, 1])[0]
    sp["eye_color"] = rng.choices([c for c, _ in EYE_COLORS], [4, 3, 3, 3, 2, 2, 1, 1])[0]
    sp["face"] = rng.choices([k for k, _ in SKIN_PARTS["face"]], [5, 2, 3, 3, 3, 1, 1])[0]
    if sp["face"] in ("beard", "mustache") and sp["model"] == "slim":
        sp["face"] = "smile"
    sp["top"] = pick([k for k, _ in SKIN_PARTS["top"]])
    sp["bottom"] = pick([k for k, _ in SKIN_PARTS["bottom"]])
    sp["shoes"] = rng.choices(["sneakers", "boots", "sandals"], [5, 3, 1])[0]
    sp["accessory"] = "none" if rng.random() < 0.35 else pick(
        [k for k, _ in SKIN_PARTS["accessory"][1:]])

    # цветовая схема
    h = rng.random()
    scheme = rng.choice(["neutral", "neutral", "analog", "complement", "mono", "pastel"])
    if scheme == "pastel":
        s, l = rng.uniform(0.45, 0.7), rng.uniform(0.7, 0.8)
    else:
        s, l = rng.uniform(0.45, 0.75), rng.uniform(0.42, 0.6)
    main = _hls(h, l, s)
    if scheme == "neutral":
        bottom = pick(_DENIM) if sp["bottom"] == "jeans" else pick(_NEUTRAL_BOTTOMS)
        accent = pick(["#f2efe8", _hls(h + 0.5, 0.6, 0.65), "#f2d04a", "#2d2d34"])
    elif scheme == "analog":
        bottom = _hls(h + rng.choice([-0.09, 0.09]), l - 0.2, s * 0.7)
        accent = _hls(h - 0.09, 0.78, 0.6)
    elif scheme == "complement":
        bottom = _hls(h + 0.5, max(0.22, l - 0.18), s * 0.55)
        accent = _hls(h + 0.5, 0.65, 0.7)
    elif scheme == "mono":
        bottom = _hex(tone(_rgb(main), -0.45))
        accent = _hex(tone(_rgb(main), 0.55))
    else:  # pastel
        bottom = _hls(h + 0.25, 0.68, 0.45)
        accent = "#ffffff"
    if sp["bottom"] == "jeans" and scheme != "mono" and rng.random() < 0.7:
        bottom = pick(_DENIM)
    sp["top_color"] = main
    sp["top_accent"] = accent
    sp["bottom_color"] = bottom
    sp["shoes_color"] = pick(["#f2efe8", "#2d2d34", "#7a5236", main, accent,
                              _hls(h + 0.5, 0.5, 0.6)])
    if sp["shoes"] == "boots" and rng.random() < 0.6:
        sp["shoes_color"] = pick(["#6b4a35", "#2d2d34", "#7a5236", "#8a6a4a"])
    acc = sp["accessory"]
    if acc == "crown":
        sp["accessory_color"] = pick(["#e9b847", "#f2d04a", "#d0d4dc"])
    elif acc in ("glasses", "sunglasses"):
        sp["accessory_color"] = pick(["#2d2d34", "#7a5236", main, "#d9433f"])
    else:
        sp["accessory_color"] = pick([accent, _hls(h + 0.5, 0.55, 0.65), _hls(h + 0.33, 0.55,
                                                                                0.6), "#f2efe8"])
    return sp


# --------------------------------------------------------------------------------------
# Превью
# --------------------------------------------------------------------------------------


def is_slim(img: Image.Image) -> bool:
    """Alex-модель: правый верхний пиксель задней грани правой руки прозрачен."""
    return img.convert("RGBA").getpixel((54, 20))[3] == 0


def _crop(img, box: Box, face: str) -> Image.Image:
    x, y, w, h = box.face(face)
    return img.crop((x, y, x + w, y + h))


def _paste_face(canvas, img, parts, part, face, pos):
    base, over = parts[part]
    for b in (base, over):
        canvas.alpha_composite(_crop(img, b, face), pos)


def render_preview(img: Image.Image, scale: int = 6, views=("front", "back"),
                   bg=(0, 0, 0, 0), gap: int = 4) -> Image.Image:
    """Плоское превью: лицевые грани, собранные в стоящую фигурку (и вид сзади/сбоку)."""
    img = img.convert("RGBA")
    parts = layout("slim" if is_slim(img) else "classic")
    aw = parts["rarm"][0].w
    W, H = aw * 2 + 8, 32
    figs = []
    for view in views:
        fig = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if view in ("front", "back"):
            f = view
            left_arm, right_arm = ("rarm", "larm") if f == "front" else ("larm", "rarm")
            left_leg, right_leg = ("rleg", "lleg") if f == "front" else ("lleg", "rleg")
            _paste_face(fig, img, parts, "body", f, (aw, 8))
            _paste_face(fig, img, parts, left_arm, f, (0, 8))
            _paste_face(fig, img, parts, right_arm, f, (aw + 8, 8))
            _paste_face(fig, img, parts, left_leg, f, (aw, 20))
            _paste_face(fig, img, parts, right_leg, f, (aw + 4, 20))
            _paste_face(fig, img, parts, "head", f, (aw, 0))
        else:  # "right" / "left" — вид сбоку
            f = view
            arm, leg = ("rarm", "rleg") if f == "right" else ("larm", "lleg")
            cx = (W - 4) // 2
            _paste_face(fig, img, parts, "body", f, (cx, 8))
            _paste_face(fig, img, parts, leg, f, (cx, 20))
            _paste_face(fig, img, parts, arm, f, (cx, 8))
            _paste_face(fig, img, parts, "head", f, ((W - 8) // 2, 0))
        figs.append(fig)
    out = Image.new("RGBA", ((W * len(figs) + gap * (len(figs) - 1)) * scale, H * scale), bg)
    for i, fig in enumerate(figs):
        big = fig.resize((W * scale, H * scale), Image.NEAREST)
        out.alpha_composite(big, (i * (W + gap) * scale, 0))
    return out


def _font(size: int):
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def contact_sheet(items, cols: int = 6, scale: int = 5, views=("front", "back"),
                  bg=(46, 52, 64)) -> Image.Image:
    """items: список (img, подпись). Возвращает сетку превью."""
    previews = [(render_preview(im, scale=scale, views=views), str(lbl)) for im, lbl in items]
    cw = max(p.width for p, _ in previews) + 24
    ch = max(p.height for p, _ in previews) + 44
    rows = (len(previews) + cols - 1) // cols
    sheet = Image.new("RGBA", (cw * cols, ch * rows), bg + (255,))
    draw = ImageDraw.Draw(sheet)
    font = _font(13)
    for i, (p, lbl) in enumerate(previews):
        x, y = (i % cols) * cw, (i // cols) * ch
        draw.rectangle((x + 4, y + 4, x + cw - 4, y + ch - 4), fill=(58, 66, 80))
        sheet.alpha_composite(p, (x + (cw - p.width) // 2, y + 10))
        draw.text((x + 10, y + ch - 30), lbl[:40], fill=(230, 230, 235), font=font)
    return sheet

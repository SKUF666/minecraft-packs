"""skin3d - Minecraft Java player model renderer (stdlib + Pillow only).

    from skin3d import render
    img = render(open("skin.png", "rb").read(), yaw=30, pitch=10)   # PIL RGBA

Geometry and UV layout follow Minecraft's own PlayerModel / ModelPart.Cube
(the same vertex/UV assignment per face, mirror handling, legacy 64x32
conversion, setNoAlpha / Notch transparency hack, cape and elytra layers).

Rendering: orthographic projection, each visible face is drawn with one
Image.transform(AFFINE, NEAREST) of its (pre-cropped, pre-shaded) texture
into the face's bounding box and alpha-composited onto a supersampled
canvas.  Parts are ordered by a separating-axis topological sort, inside a
part the order is: overlay inner (back) faces -> base -> overlay front faces.

Angles: yaw > 0 turns the model's front to the viewer's right (the model's
right side comes into view, it is on the viewer's left); pitch > 0 looks from
above.  For mouse dragging: yaw += dx * k, pitch -= dy * k (clamp pitch).

The scale does not depend on yaw/pitch, so the model does not "breathe"
while it is being rotated.  Use zoom= to change it.
"""
from __future__ import annotations

import io
import math
from collections import OrderedDict

from PIL import Image, ImageChops, ImageColor

__all__ = ["render", "SkinModel", "get_model", "normalize_skin", "detect_slim",
           "clear_cache"]

_FLIP_LR = Image.Transpose.FLIP_LEFT_RIGHT
_FLIP_TB = Image.Transpose.FLIP_TOP_BOTTOM
_AFFINE = Image.Transform.AFFINE
_NEAREST = Image.Resampling.NEAREST

# light direction in camera space (x right, y up, z towards viewer)
_L = (-0.38, 0.72, 0.58)
_ln = math.sqrt(sum(c * c for c in _L))
_L = tuple(c / _ln for c in _L)
_AMB, _DIF = 0.50, 0.55
_LEVELS = 64

# model centre used as rotation pivot (in "mine" space: y up, feet at 0)
_CENTER = (0.0, 16.0, 0.0)
# fixed fit extents (texels): half-width and full projected height bound
_FIT_W = 2 * 9.6
_FIT_H = 37.0


# --------------------------------------------------------------------------
# image loading / skin normalisation (mirrors Minecraft's SkinTextureDownloader)
# --------------------------------------------------------------------------
def _to_rgba(src):
    if src is None:
        return None
    if isinstance(src, Image.Image):
        im = src
    elif isinstance(src, (bytes, bytearray, memoryview)):
        im = Image.open(io.BytesIO(bytes(src)))
    else:  # path-like
        im = Image.open(src)
    im.load()
    return im if im.mode == "RGBA" else im.convert("RGBA")


def _copy_rect(img, k, x, y, dx, dy, w, h):
    reg = img.crop((x * k, y * k, (x + w) * k, (y + h) * k)).transpose(_FLIP_LR)
    img.paste(reg, ((x + dx) * k, (y + dy) * k))


def _set_no_alpha(img, k, x0, y0, x1, y1):
    box = (x0 * k, y0 * k, x1 * k, y1 * k)
    reg = img.crop(box)
    reg.putalpha(255)
    img.paste(reg, box[:2])


def _notch_hack(img, k, x0, y0, x1, y1):
    box = (x0 * k, y0 * k, x1 * k, y1 * k)
    reg = img.crop(box)
    if reg.getchannel("A").getextrema()[0] < 128:
        return
    reg.putalpha(0)
    img.paste(reg, box[:2])


def detect_slim(skin) -> bool:
    """Slim (Alex, 3px arms) if the unused arm column (54,20) is transparent."""
    im = _to_rgba(skin)
    w, h = im.size
    if h * 2 == w:          # legacy skins are always classic
        return False
    k = max(1, w // 64)
    return im.getpixel((54 * k, 20 * k))[3] == 0


def normalize_skin(skin):
    """Return (64k x 64k RGBA image, k, legacy) the way Minecraft processes it."""
    im = _to_rgba(skin)
    w, h = im.size
    if w < 64 or w % 64 or not (h == w or h * 2 == w):
        raise ValueError(f"unsupported skin size {w}x{h}")
    k = w // 64
    legacy = h * 2 == w
    if legacy:
        new = Image.new("RGBA", (w, w), (0, 0, 0, 0))
        new.paste(im, (0, 0))
        im = new
        for args in ((4, 16, 16, 32, 4, 4), (8, 16, 16, 32, 4, 4),
                     (0, 20, 24, 32, 4, 12), (4, 20, 16, 32, 4, 12),
                     (8, 20, 8, 32, 4, 12), (12, 20, 16, 32, 4, 12),
                     (44, 16, -8, 32, 4, 4), (48, 16, -8, 32, 4, 4),
                     (40, 20, 0, 32, 4, 12), (44, 20, -8, 32, 4, 12),
                     (48, 20, -16, 32, 4, 12), (52, 20, -8, 32, 4, 12)):
            _copy_rect(im, k, *args)
    else:
        im = im.copy()
    _set_no_alpha(im, k, 0, 0, 32, 16)
    if legacy:
        _notch_hack(im, k, 32, 0, 64, 32)
    _set_no_alpha(im, k, 0, 16, 64, 32)
    _set_no_alpha(im, k, 16, 48, 48, 64)
    return im, k, legacy


def _normalize_cape(cape):
    im = _to_rgba(cape)
    w, h = im.size
    if w * 2 != h * 4:  # odd sizes (e.g. 22x17): put on a 64x32 canvas
        new = Image.new("RGBA", (max(64, w), max(32, h)), (0, 0, 0, 0))
        new.paste(im, (0, 0))
        im = new
        w, h = im.size
        if w * 2 != h * 4:
            nw = max(w, h * 2)
            new = Image.new("RGBA", (nw, nw // 2), (0, 0, 0, 0))
            new.paste(im, (0, 0))
            im = new
    return im, im.size[0] / 64.0


_CUT_LUT = [0] * 26 + [255] * 230


def _cutout(im):
    """Minecraft draws skins/capes/elytra as alpha *cutout*: alpha < 0.1 is
    discarded, everything else is fully opaque."""
    r, g, b, a = im.split()
    return Image.merge("RGBA", (r, g, b, a.point(_CUT_LUT)))


def _default_elytra():
    im = Image.new("RGBA", (64, 32), (0, 0, 0, 0))
    px = im.load()
    for y in range(22):
        for x in range(22, 46):
            px[x, y] = (150, 150, 165, 255) if (x + y) % 5 else (120, 120, 135, 255)
    return im


# --------------------------------------------------------------------------
# small affine-matrix helpers (3x4 matrices as tuples of 3 row tuples)
# --------------------------------------------------------------------------
def _rx(a):
    c, s = math.cos(a), math.sin(a)
    return ((1, 0, 0, 0), (0, c, -s, 0), (0, s, c, 0))


def _ry(a):
    c, s = math.cos(a), math.sin(a)
    return ((c, 0, s, 0), (0, 1, 0, 0), (-s, 0, c, 0))


def _rz(a):
    c, s = math.cos(a), math.sin(a)
    return ((c, -s, 0, 0), (s, c, 0, 0), (0, 0, 1, 0))


def _tr(x, y, z):
    return ((1, 0, 0, x), (0, 1, 0, y), (0, 0, 1, z))


_ID = _tr(0, 0, 0)
# Minecraft model space (y down, front = -z, neck at y=0)  ->  "mine"
# (y up, front = +z, feet at y=0)
_MC2MINE = ((1, 0, 0, 0), (0, -1, 0, 24), (0, 0, -1, 0))


def _mm(a, b):
    out = []
    for r in a:
        out.append((r[0] * b[0][0] + r[1] * b[1][0] + r[2] * b[2][0],
                    r[0] * b[0][1] + r[1] * b[1][1] + r[2] * b[2][1],
                    r[0] * b[0][2] + r[1] * b[1][2] + r[2] * b[2][2],
                    r[0] * b[0][3] + r[1] * b[1][3] + r[2] * b[2][3] + r[3]))
    return tuple(out)


def _chain(*ms):
    m = ms[0]
    for n in ms[1:]:
        m = _mm(m, n)
    return m


def _ap(m, p):
    x, y, z = p
    return (m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3],
            m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3],
            m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3])


def _av(m, v):
    x, y, z = v
    return (m[0][0] * x + m[0][1] * y + m[0][2] * z,
            m[1][0] * x + m[1][1] * y + m[1][2] * z,
            m[2][0] * x + m[2][1] * y + m[2][2] * z)


def _pose(pivot=(0, 0, 0), xr=0.0, yr=0.0, zr=0.0):
    """ModelPart.translateAndRotate: T(pivot) * Rz * Ry * Rx."""
    m = _tr(*pivot)
    if zr:
        m = _mm(m, _rz(zr))
    if yr:
        m = _mm(m, _ry(yr))
    if xr:
        m = _mm(m, _rx(xr))
    return m


# --------------------------------------------------------------------------
# faces
# --------------------------------------------------------------------------
_CONST = {}


def _const(mode, size, level):
    key = (mode, size, level)
    im = _CONST.get(key)
    if im is None:
        v = min(255, int(255 * level / _LEVELS + 0.5))
        im = Image.new(mode, size, (v, v, v, 255))
        if len(_CONST) > 4096:
            _CONST.clear()
        _CONST[key] = im
    return im


class _Face:
    __slots__ = ("tex", "tw", "th", "p1", "eu", "ev", "n", "cache")

    def __init__(self, tex, p1, eu, ev, n):
        self.tex = tex
        self.tw, self.th = tex.size
        self.p1, self.eu, self.ev, self.n = p1, eu, ev, n
        self.cache = {}

    def shaded(self, level):
        im = self.cache.get(level)
        if im is None:
            tex = self.tex
            im = tex if level >= _LEVELS else ImageChops.multiply(
                tex, _const(tex.mode, tex.size, level))
            self.cache[level] = im
        return im


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _cube(tex, k, uv, box, g=0.0, mirror=False):
    """Faces of ModelPart.Cube, in the part's local (Minecraft) space.

    Returns (faces, corners)."""
    u, v = uv
    x, y, z, w, h, d = box
    x0, y0, z0 = x - g, y - g, z - g
    x1, y1, z1 = x + w + g, y + h + g, z + d + g
    if mirror:
        x0, x1 = x1, x0
    V0, V1, V2, V3 = (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)
    V4, V5, V6, V7 = (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)
    f4, f5, f6 = u, u + d, u + d + w
    f7, f8, f9 = u + d + w + w, u + d + w + d, u + d + w + d + w
    f10, f11, f12 = v, v + d, v + d + h
    polys = (((V5, V4, V0, V1), f5, f10, f6, f11),   # DOWN  (= top in mine)
             ((V2, V3, V7, V6), f6, f11, f7, f10),   # UP    (= bottom)
             ((V0, V4, V7, V3), f4, f11, f5, f12),   # WEST  (-x, model right)
             ((V1, V0, V3, V2), f5, f11, f6, f12),   # NORTH (front)
             ((V5, V1, V2, V6), f6, f11, f8, f12),   # EAST  (+x, model left)
             ((V4, V5, V6, V7), f8, f11, f9, f12))   # SOUTH (back)
    cx, cy, cz = (x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2
    faces = []
    tw_, th_ = tex.size
    for verts, u1, v1, u2, v2 in polys:
        ua, ub = sorted((u1, u2))
        va, vb = sorted((v1, v2))
        bx = (round(ua * k), round(va * k), round(ub * k), round(vb * k))
        if bx[2] <= bx[0] or bx[3] <= bx[1] or bx[2] > tw_ or bx[3] > th_:
            continue
        t = tex.crop(bx)
        if u2 < u1:
            t = t.transpose(_FLIP_LR)
        if v2 < v1:
            t = t.transpose(_FLIP_TB)
        if t.getchannel("A").getextrema()[1] == 0:
            continue  # fully transparent: never drawn
        p1 = verts[1]
        eu = _sub(verts[0], p1)
        ev = _sub(verts[2], p1)
        n = _cross(eu, ev)
        fc = tuple((verts[0][i] + verts[2][i]) / 2 for i in range(3))
        if (n[0] * (fc[0] - cx) + n[1] * (fc[1] - cy) + n[2] * (fc[2] - cz)) < 0:
            n = (-n[0], -n[1], -n[2])
        ln = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2) or 1.0
        faces.append(_Face(t, p1, eu, ev, (n[0] / ln, n[1] / ln, n[2] / ln)))
    corners = [(a, b, c) for a in (x0, x1) for b in (y0, y1) for c in (z0, z1)]
    return faces, corners


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------
_ORDER = ("cape", "wing_l", "wing_r", "right_leg", "left_leg", "body",
          "right_arm", "left_arm", "head")


def _player_spec(slim):
    aw = 3 if slim else 4
    ay = 2.5 if slim else 2.0
    return {
        "head": ((0, 0), (32, 0), (-4, -8, -4, 8, 8, 8), (0, 0, 0), 0.5),
        "body": ((16, 16), (16, 32), (-4, 0, -2, 8, 12, 4), (0, 0, 0), 0.25),
        "right_arm": ((40, 16), (40, 32), (-(aw - 1), -2, -2, aw, 12, 4), (-5, ay, 0), 0.25),
        "left_arm": ((32, 48), (48, 48), (-1, -2, -2, aw, 12, 4), (5, ay, 0), 0.25),
        "right_leg": ((0, 16), (0, 32), (-2, 0, -2, 4, 12, 4), (-2, 12, 0), 0.25),
        "left_leg": ((16, 48), (0, 48), (-2, 0, -2, 4, 12, 4), (2, 12, 0), 0.25),
    }


class _Part:
    __slots__ = ("name", "base", "over", "corners", "pivot", "solid_base")

    def __init__(self, name, base, over, corners, pivot, solid_base=True):
        self.name, self.base, self.over = name, base, over
        self.corners, self.pivot, self.solid_base = corners, pivot, solid_base


class SkinModel:
    """Parsed skin (+ optional cape) with all face textures cut and cached."""

    def __init__(self, skin, cape=None, slim=None, translucent=False):
        raw = _to_rgba(skin)
        self.slim = detect_slim(raw) if slim is None else bool(slim)
        self.skin, k, self.legacy = normalize_skin(raw)
        if not translucent:
            self.skin = _cutout(self.skin)
        self.parts = {}
        for name, (uv, ouv, box, pivot, g) in _player_spec(self.slim).items():
            base, corners = _cube(self.skin, k, uv, box)
            over, _ = _cube(self.skin, k, ouv, box, g)
            self.parts[name] = _Part(name, base, over, corners, pivot)
        self.cape = None
        self.has_cape = cape is not None
        ctex, ck = _normalize_cape(cape if cape is not None else _default_elytra())
        if not translucent:
            ctex = _cutout(ctex)
        if cape is not None:
            f, c = _cube(ctex, ck, (0, 0), (-5, 0, -1, 10, 16, 1))
            self.cape = _Part("cape", f, [], c, None)
        fl, cl = _cube(ctex, ck, (22, 0), (-10, 0, 0, 10, 20, 2), 1.0)
        fr, cr = _cube(ctex, ck, (22, 0), (0, 0, 0, 10, 20, 2), 1.0, mirror=True)
        self.wing_l = _Part("wing_l", [], fl, cl, None, solid_base=False)
        self.wing_r = _Part("wing_r", [], fr, cr, None, solid_base=False)
        self._pose_cache = {}
        # cutout textures have binary alpha -> keep them premultiplied and
        # paste them with a mask onto a premultiplied canvas (fastest path)
        self.premul = not translucent
        if self.premul:
            for p in list(self.parts.values()) + [self.cape, self.wing_l, self.wing_r]:
                if p is None:
                    continue
                for f in p.base + p.over:
                    f.tex = f.tex.convert("RGBa")

    # ---- pose: part -> (matrix local->mine, sort AABB in mine space) ----
    def _posed(self, pose, cape, elytra):
        key = (pose, cape, elytra)
        hit = self._pose_cache.get(key)
        if hit is not None:
            return hit
        sw = math.radians(28) if pose == "walk" else 0.0
        rot = {"right_arm": -sw, "left_arm": sw, "right_leg": sw, "left_leg": -sw}
        out = []
        for name in _ORDER:
            if name in self.parts:
                p = self.parts[name]
                m = _mm(_MC2MINE, _pose(p.pivot, xr=rot.get(name, 0.0)))
                rest = _mm(_MC2MINE, _pose(p.pivot))
                pts = [_ap(rest, c) for c in p.corners]   # rest pose for sorting
                out.append((p, m, _aabb(pts)))
            elif name == "cape" and cape and self.cape is not None and not elytra:
                m = _chain(_MC2MINE, _tr(0, 0, 2), _rx(math.radians(10)),
                           _ry(math.pi))
                out.append((self.cape, m, _aabb([_ap(m, c) for c in self.cape.corners])))
            elif name in ("wing_l", "wing_r") and elytra:
                a = 0.2617994
                ml = _chain(_tr(5, 0, 0), _rz(-a), _rx(a))
                # push the wings back so they do not intersect the body
                # (Minecraft hides the 1px intersection with the depth buffer)
                zmin = min(_ap(ml, c)[2] for c in self.wing_l.corners)
                push = max(2.0, 2.0 - zmin)
                if name == "wing_l":
                    mm = _chain(_MC2MINE, _tr(0, 0, push), ml)
                    part = self.wing_l
                else:
                    mm = _chain(_MC2MINE, _tr(0, 0, push), _tr(-5, 0, 0), _rz(a), _rx(a))
                    part = self.wing_r
                out.append((part, mm, _aabb([_ap(mm, c) for c in part.corners])))
        self._pose_cache[key] = out
        return out

    # ------------------------------------------------------------------
    def render(self, yaw=30, pitch=10, size=(260, 360), elytra=False,
               overlay=True, pose="stand", bg=None, cape=True, ss=2,
               zoom=1.0):
        W, H = int(size[0]), int(size[1])
        ss = max(1, int(ss))
        CW, CH = W * ss, H * ss
        premul = self.premul
        if bg is not None:
            bgc = ImageColor.getrgb(bg) if isinstance(bg, str) else tuple(bg)
            if len(bgc) == 3:
                bgc = bgc + (255,)
        if premul:
            if bg is None:
                canvas = Image.new("RGBa", (CW, CH), (0, 0, 0, 0))
            elif bgc[3] == 255:
                canvas = Image.new("RGB", (CW, CH), bgc[:3])
            else:  # translucent background colour
                canvas = Image.new("RGBA", (CW, CH), bgc).convert("RGBa")
        else:
            canvas = Image.new("RGBA", (CW, CH), bgc if bg is not None else (0, 0, 0, 0))
        scale = ss * zoom * min(W * 0.94 / _FIT_W, H * 0.95 / _FIT_H)
        cx, cy = CW / 2.0, CH / 2.0

        V = _mm(_rx(math.radians(pitch)), _ry(math.radians(yaw)))
        # mine -> camera (rotation about _CENTER)
        Vc = _mm(V, _tr(-_CENTER[0], -_CENTER[1], -_CENTER[2]))
        # camera -> screen pixels (x right, y down), keep depth in row 2
        S = ((scale, 0, 0, cx), (0, -scale, 0, cy), (0, 0, 1, 0))
        Mscr = _mm(S, Vc)
        camdir = V[2][:3]  # direction towards the viewer, in mine space

        posed = self._posed(pose, bool(cape), bool(elytra))
        # ---------- order parts ----------
        nodes = []
        for idx, (part, m, box) in enumerate(posed):
            lo, hi = box
            ctr = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)
            depth = _ap(Vc, ctr)[2]
            xs, ys = [], []
            for c in ((a, b, d) for a in (lo[0], hi[0]) for b in (lo[1], hi[1])
                      for d in (lo[2], hi[2])):
                q = _ap(Mscr, c)
                xs.append(q[0]); ys.append(q[1])
            nodes.append((idx, depth, (min(xs), min(ys), max(xs), max(ys)), box))
        order = _sort_parts(nodes, camdir)

        Wl = canvas.size
        for idx in order:
            part, m, _ = posed[idx]
            M = _mm(Mscr, m)          # local -> screen
            R = _mm(V, m)             # local -> camera (rotation part used)
            if part.solid_base:
                over = part.over if overlay else ()
                back = [f for f in over if _av(R, f.n)[2] < 0]
                front = [f for f in over if _av(R, f.n)[2] >= 0]
                for f in back:
                    _draw(canvas, f, M, R, Wl, True, premul)
                for f in part.base:
                    if _av(R, f.n)[2] > 1e-6:
                        _draw(canvas, f, M, R, Wl, False, premul)
                for f in front:
                    _draw(canvas, f, M, R, Wl, False, premul)
            else:  # elytra: double sided, no culling
                fs = part.over
                for f in fs:
                    if _av(R, f.n)[2] < 0:
                        _draw(canvas, f, M, R, Wl, True, premul)
                for f in fs:
                    if _av(R, f.n)[2] >= 0:
                        _draw(canvas, f, M, R, Wl, False, premul)
        if ss > 1:
            canvas = canvas.reduce(ss)       # premultiplied/RGB: plain box filter
        return canvas if canvas.mode == "RGBA" else canvas.convert("RGBA")


def _aabb(pts):
    return (tuple(min(p[i] for p in pts) for i in range(3)),
            tuple(max(p[i] for p in pts) for i in range(3)))


def _sort_parts(nodes, c):
    """Topological painter's order: separated boxes by separating axis,
    everything else by centre depth (farther first)."""
    n = len(nodes)
    succ = [[] for _ in range(n)]
    indeg = [0] * n
    eps = 1e-4
    for i in range(n):
        _, _, ri, (lo_i, hi_i) = nodes[i]
        for j in range(i + 1, n):
            _, _, rj, (lo_j, hi_j) = nodes[j]
            if ri[2] < rj[0] or rj[2] < ri[0] or ri[3] < rj[1] or rj[3] < ri[1]:
                continue  # no screen overlap
            first = None
            for ax in range(3):
                if abs(c[ax]) < 1e-6:
                    continue
                if hi_i[ax] <= lo_j[ax] + eps:     # i on negative side
                    first = (i, j) if c[ax] > 0 else (j, i)
                    break
                if hi_j[ax] <= lo_i[ax] + eps:
                    first = (j, i) if c[ax] > 0 else (i, j)
                    break
            if first:
                succ[first[0]].append(first[1])
                indeg[first[1]] += 1
    remaining = set(range(n))
    out = []
    while remaining:
        ready = [i for i in remaining if indeg[i] == 0]
        pool = ready or list(remaining)       # cycle: fall back to depth
        i = min(pool, key=lambda q: (nodes[q][1], q))
        remaining.discard(i)
        out.append(nodes[i][0])
        for j in succ[i]:
            indeg[j] -= 1
    return out


def _draw(canvas, f, M, R, size, inner=False, premul=True):
    n = _av(R, f.n)
    if inner:
        n = (-n[0], -n[1], -n[2])
    d = n[0] * _L[0] + n[1] * _L[1] + n[2] * _L[2]
    b = _AMB + _DIF * (d if d > 0 else 0.0)
    if inner:
        b *= 0.8
    level = min(_LEVELS, int(b * _LEVELS + 0.5))
    tex = f.shaded(level)

    p1 = _ap(M, f.p1)
    ux, uy = M[0][0] * f.eu[0] + M[0][1] * f.eu[1] + M[0][2] * f.eu[2], \
        M[1][0] * f.eu[0] + M[1][1] * f.eu[1] + M[1][2] * f.eu[2]
    vx, vy = M[0][0] * f.ev[0] + M[0][1] * f.ev[1] + M[0][2] * f.ev[2], \
        M[1][0] * f.ev[0] + M[1][1] * f.ev[1] + M[1][2] * f.ev[2]
    tw, th = f.tw, f.th
    ax, ay, bx, by = ux / tw, uy / tw, vx / th, vy / th
    det = ax * by - bx * ay
    if abs(det) < 1e-9:
        return
    px, py = p1[0], p1[1]
    xs = (px, px + ux, px + vx, px + ux + vx)
    ys = (py, py + uy, py + vy, py + uy + vy)
    x0 = max(0, int(math.floor(min(xs))))
    y0 = max(0, int(math.floor(min(ys))))
    x1 = min(size[0], int(math.ceil(max(xs))))
    y1 = min(size[1], int(math.ceil(max(ys))))
    if x1 <= x0 or y1 <= y0:
        return
    ia, ib = by / det, -bx / det
    id_, ie = -ay / det, ax / det
    ox, oy = x0 - px, y0 - py
    img = tex.transform((x1 - x0, y1 - y0), _AFFINE,
                        (ia, ib, ia * ox + ib * oy, id_, ie, id_ * ox + ie * oy),
                        _NEAREST)
    if premul:
        canvas.paste(img, (x0, y0), img)
    else:
        canvas.alpha_composite(img, (x0, y0))


# --------------------------------------------------------------------------
# cache + public entry point
# --------------------------------------------------------------------------
_CACHE: "OrderedDict[tuple, SkinModel]" = OrderedDict()
_CACHE_MAX = 16


def _key(src):
    if src is None:
        return None
    if isinstance(src, Image.Image):
        return ("img", id(src), src.size, src.mode, hash(src.tobytes()))
    if isinstance(src, (bytes, bytearray, memoryview)):
        return ("bytes", hash(bytes(src)))
    return ("path", str(src))


def get_model(skin, cape=None, slim=None, translucent=False) -> SkinModel:
    key = (_key(skin), _key(cape), slim, bool(translucent))
    m = _CACHE.get(key)
    if m is None:
        m = SkinModel(skin, cape, slim, translucent)
        _CACHE[key] = m
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    else:
        _CACHE.move_to_end(key)
    return m


def clear_cache():
    _CACHE.clear()


def render(skin, yaw=30, pitch=10, size=(260, 360), slim=None, cape=None,
           elytra=False, overlay=True, pose="stand", bg=None, ss=2, zoom=1.0,
           translucent=False):
    """Render a Minecraft player to a PIL RGBA image.

    skin   : PNG bytes, PIL image or path (64x64, 64x32 legacy, HD multiples)
    yaw    : degrees, rotation around the vertical axis (0 = facing viewer)
    pitch  : degrees, > 0 = seen from above
    slim   : None = auto-detect, True/False to force
    cape   : cape texture (64x32 layout) as bytes / PIL / path, or None
    elytra : draw elytra (from the cape texture) instead of the cape
    overlay: draw the second skin layer
    pose   : "stand" or "walk"
    bg     : None (transparent) or a colour accepted by PIL
    ss     : supersampling factor (2 = smooth edges, 1 = fastest)
    zoom   : extra scale factor (1.0 fits any yaw/pitch)
    translucent: False = Minecraft behaviour (semi-transparent overlay pixels
             are drawn opaque, alpha < 26 dropped); True = real alpha blending
    """
    m = get_model(skin, cape, slim, translucent)
    return m.render(yaw=yaw, pitch=pitch, size=size, elytra=elytra,
                    overlay=overlay, pose=pose, bg=bg, cape=True, ss=ss,
                    zoom=zoom)

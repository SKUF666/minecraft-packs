"""Доктор сборки модов Minecraft Java.

Находит проблемы в папке с модами (*.jar) ДО запуска игры: не тот загрузчик,
не та версия Minecraft, нет обязательных зависимостей, явные несовместимости,
дубликаты и известные в сообществе конфликты.

Только стандартная библиотека (zipfile, json, re, tomllib). Python 3.11+.

Публичный API:
    check_mods(mods_dir, mc_version, loader, loader_version=None, *, side="client")
        -> list[dict]  # {"level", "kind", "text", "mods", "fix"}
    read_mod(jar_path) -> dict
    server_unsafe(mods_dir) -> list[dict]
    KNOWN_CONFLICTS  # таблица известных конфликтов с источниками

loader: "fabric" | "quilt" | "forge" | "neoforge".
"""

from __future__ import annotations

import io
import json
import os
import re
import tomllib
import zipfile

__all__ = ["check_mods", "read_mod", "server_unsafe", "KNOWN_CONFLICTS",
           "fabric_match", "maven_match", "compare_versions"]

LEVEL_ORDER = {"error": 0, "warn": 1, "info": 2}
LOADER_TITLES = {
    "fabric": "Fabric", "quilt": "Quilt", "forge": "Forge", "neoforge": "NeoForge",
    "forge_legacy": "Forge до 1.13 (mcmod.info)", "optifine": "OptiFine",
}

# Человеческие имена частых зависимостей, чтобы подсказка «что скачать» была понятной.
KNOWN_NAMES = {
    "fabric-api": "Fabric API", "fabric": "Fabric API", "fabricloader": "Fabric Loader",
    "forge": "Forge", "neoforge": "NeoForge", "quilt_loader": "Quilt Loader", "minecraft": "Minecraft",
    "fabric-language-kotlin": "Fabric Language Kotlin", "cloth-config": "Cloth Config API",
    "cloth-config2": "Cloth Config API", "cloth_config": "Cloth Config API",
    "architectury": "Architectury API", "geckolib": "GeckoLib", "geckolib3": "GeckoLib",
    "curios": "Curios API", "trinkets": "Trinkets", "yet_another_config_lib_v3": "YetAnotherConfigLib (YACL)",
    "yacl": "YetAnotherConfigLib (YACL)", "modmenu": "Mod Menu", "sodium": "Sodium", "iris": "Iris",
    "embeddium": "Embeddium", "rubidium": "Rubidium", "oculus": "Oculus", "jei": "Just Enough Items (JEI)",
    "balm": "Balm", "kotlinforforge": "Kotlin for Forge", "citadel": "Citadel", "playeranimator": "playerAnimator",
    "player-animator": "playerAnimator", "puzzleslib": "Puzzles Lib", "bookshelf": "Bookshelf",
    "moonlight": "Moonlight Lib", "zeta": "Zeta", "placebo": "Placebo", "patchouli": "Patchouli",
    "iceberg": "Iceberg", "prism": "Prism", "owo": "oωo (owo-lib)", "owo-lib": "oωo (owo-lib)",
    "forgeconfigapiport": "Forge Config API Port", "fzzy_config": "Fzzy Config", "resourcefullib": "Resourceful Lib",
    "creativecore": "CreativeCore", "mclib": "McLib", "mixinbooter": "MixinBooter", "indium": "Indium",
    "lithium": "Lithium", "qsl": "Quilt Standard Libraries", "quilted_fabric_api": "Quilted Fabric API (QFAPI)",
    "connector": "Sinytra Connector", "fabric_api": "Forgified Fabric API", "ferritecore": "FerriteCore",
    "spruceui": "SpruceUI", "lambdynlights_api": "LambDynamicLights API", "yumi_mc_core": "Yumi Minecraft Core",
    "sophisticatedcore": "Sophisticated Core", "lionfishapi": "Lionfish API", "irons_lib": "Iron's Lib",
    "obscure_api": "Obscure API", "caelus": "Caelus API", "cerbons_api": "Cerbon's API",
    "yungsapi": "YUNG's API", "integrated_api": "Integrated API", "lithostitched": "Lithostitched",
    "octolib": "OctoLib", "libipn": "libIPN", "fabric-language-scala": "Fabric Language Scala",
}

# --------------------------------------------------------------------------------------
# Версии
# --------------------------------------------------------------------------------------

_WILDCARDS = {"x", "X", "*"}


def _parse_semver(s):
    """Fabric-подобный разбор: (компоненты, prerelease|None, позиция_звёздочки|None) или None.

    "1.20.x" -> ([1, 20], None, 2); "26.1-" -> ([26, 1], [], None); "1.21-rc.1" -> ([1, 21], ["rc", "1"], None)
    """
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    s = s.split("+", 1)[0]
    core, sep, pre = s.partition("-")
    pre_list = None if not sep else ([] if pre == "" else pre.split("."))
    comps, wild = [], None
    for part in core.split("."):
        if part in _WILDCARDS:
            if wild is None:
                wild = len(comps)
            continue
        if wild is not None or not part.isdigit():
            return None
        comps.append(int(part))
    if not comps and wild is None:
        return None
    return comps, pre_list, wild


def _cmp(a, b):
    return (a > b) - (a < b)


def _semver_cmp(a, b):
    ca, pa = a[0], a[1]
    cb, pb = b[0], b[1]
    for i in range(max(len(ca), len(cb))):
        c = _cmp(ca[i] if i < len(ca) else 0, cb[i] if i < len(cb) else 0)
        if c:
            return c
    if pa is None and pb is None:
        return 0
    if pa is None:
        return 1
    if pb is None:
        return -1
    for x, y in zip(pa, pb):
        xd, yd = x.isdigit(), y.isdigit()
        if xd and yd:
            c = _cmp(int(x), int(y))
        elif xd:
            c = -1
        elif yd:
            c = 1
        else:
            c = _cmp(x, y)
        if c:
            return c
    return _cmp(len(pa), len(pb))


def _normalize_mc_for_fabric(v):
    """Приводит версию Minecraft к виду, в котором её видит Fabric Loader."""
    v = str(v).strip()
    if re.fullmatch(r"\d\dw\d\d[a-z]", v):  # снапшоты вида 24w14a: без таблицы соответствий не сравнить
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)*)(?:-| )pre(?:-release )?(\d+)", v, re.I)
    if m:
        return f"{m.group(1)}-beta.{m.group(2)}"
    m = re.fullmatch(r"(\d+(?:\.\d+)*)(?:-| )(?:rc|release candidate )(\d+)", v, re.I)
    if m:
        return f"{m.group(1)}-rc.{m.group(2)}"
    m = re.fullmatch(r"(\d+(?:\.\d+)*)-snapshot-(\d+)", v, re.I)
    if m:
        return f"{m.group(1)}-snapshot.{m.group(2)}"
    return v


def _fabric_term(term, v):
    m = re.match(r"(>=|<=|>|<|=|~|\^)?\s*(.*)$", term)
    op, ver = m.group(1) or "=", m.group(2).strip()
    if ver in ("", "*", "x", "X"):
        return True
    pv = _parse_semver(ver)
    if pv is None:
        return None
    comps, pre, wild = pv
    if wild is not None:
        if not comps:
            return True
        lower = (comps, [])
        upper = (comps[:-1] + [comps[-1] + 1], [])
        if op in ("=", "~", "^"):
            return _semver_cmp(v, lower) >= 0 and _semver_cmp(v, upper) < 0
        if op == ">=":
            return _semver_cmp(v, lower) >= 0
        if op == ">":
            return _semver_cmp(v, upper) >= 0
        if op == "<":
            return _semver_cmp(v, lower) < 0
        if op == "<=":
            return _semver_cmp(v, upper) < 0
        return None
    c = _semver_cmp(v, (comps, pre))
    if op == "=":
        return c == 0
    if op == ">=":
        return c >= 0
    if op == ">":
        return c > 0
    if op == "<=":
        return c <= 0
    if op == "<":
        return c < 0
    if op == "~":
        upper = ([comps[0], (comps[1] if len(comps) > 1 else 0) + 1], [])
        return c >= 0 and _semver_cmp(v, upper) < 0
    if op == "^":
        return c >= 0 and _semver_cmp(v, ([comps[0] + 1], [])) < 0
    return None


def _tri_any(results):
    results = list(results)
    if any(r is True for r in results):
        return True
    if any(r is None for r in results):
        return None
    return False


def fabric_match(predicate, version, *, is_minecraft=False):
    """Проверка версии по предикату Fabric/Quilt.

    predicate: строка (пробел = И, "||" = ИЛИ) или список строк (ИЛИ).
    Возвращает True/False, либо None, если разобрать не удалось (тогда молчим).
    """
    if predicate is None:
        return True
    if isinstance(predicate, (list, tuple)):
        if not predicate:
            return True
        return _tri_any(fabric_match(p, version, is_minecraft=is_minecraft) for p in predicate)
    if isinstance(predicate, dict):  # Quilt: {"any": [...]} / {"all": [...]}
        if "any" in predicate:
            return fabric_match(list(predicate["any"]), version, is_minecraft=is_minecraft)
        if "all" in predicate:
            res = [fabric_match(p, version, is_minecraft=is_minecraft) for p in predicate["all"]]
            if any(r is False for r in res):
                return False
            return None if any(r is None for r in res) else True
        return None
    if version is None:
        return None
    vs = _normalize_mc_for_fabric(version) if is_minecraft else str(version)
    pv = _parse_semver(vs) if vs else None
    if pv is None or pv[2] is not None:
        return None
    v = (pv[0], pv[1])
    out = []
    for alt in str(predicate).split("||"):
        terms = alt.split()
        if not terms:
            out.append(True)
            continue
        res = [_fabric_term(t, v) for t in terms]
        if any(r is False for r in res):
            out.append(False)
        elif any(r is None for r in res):
            out.append(None)
        else:
            out.append(True)
    return _tri_any(out)


# Maven/Forge (упрощённый ComparableVersion)
_QUALIFIER_RANK = {"alpha": 0, "a": 0, "beta": 1, "b": 1, "milestone": 2, "m": 2, "rc": 3, "cr": 3,
                   "pre": 3, "snapshot": 4, "": 5, "ga": 5, "final": 5, "release": 5, "sp": 6}


def _maven_items(v):
    return [int(t) if t.isdigit() else t for t in re.findall(r"\d+|[a-z]+", str(v).lower())]


def _maven_item_cmp(x, y):
    if x is None and y is None:
        return 0
    if x is None:
        return -_maven_item_cmp(y, None)
    if isinstance(x, int):
        if y is None:
            return 1 if x > 0 else 0
        if isinstance(y, int):
            return _cmp(x, y)
        return 1
    rx = _QUALIFIER_RANK.get(x, 7)
    if y is None:
        return _cmp(rx, 5)
    if isinstance(y, int):
        return -1
    ry = _QUALIFIER_RANK.get(y, 7)
    if rx != ry:
        return _cmp(rx, ry)
    return _cmp(x, y) if rx == 7 else 0


def compare_versions(a, b):
    """Нестрогое сравнение двух произвольных версий (-1/0/1), как в Maven."""
    ia, ib = _maven_items(a), _maven_items(b)
    for i in range(max(len(ia), len(ib))):
        c = _maven_item_cmp(ia[i] if i < len(ia) else None, ib[i] if i < len(ib) else None)
        if c:
            return c
    return 0


def maven_match(spec, version):
    """Проверка версии по диапазону Maven/Forge: "[1.20,1.21)", "[1.20.1]", "(,1.19]", "[1,2),[3,)".

    Голая версия ("1.20.1") в Maven — мягкое требование и подходит под всё.
    """
    if spec is None:
        return True
    spec = str(spec).strip()
    if not spec or spec == "*":
        return True
    if spec[0] not in "[(":
        return True
    if version is None or not _maven_items(version):
        return None
    ranges = re.findall(r"([\[(])([^\])]*)([\])])", spec)
    if not ranges:
        return None
    for lo_b, body, hi_b in ranges:
        if "," not in body:
            if body.strip() and compare_versions(version, body.strip()) == 0:
                return True
            continue
        lo, hi = (p.strip() for p in body.split(",", 1))
        ok = True
        if lo:
            c = compare_versions(version, lo)
            ok = c > 0 or (c == 0 and lo_b == "[")
        if ok and hi:
            c = compare_versions(version, hi)
            ok = c < 0 or (c == 0 and hi_b == "]")
        if ok:
            return True
    return False


def _match(kind, rng, version, *, is_minecraft=False):
    if kind == "maven":
        return maven_match(rng, version)
    if kind == "legacy":
        return _legacy_mc_match(rng, version)
    return fabric_match(rng, version, is_minecraft=is_minecraft)


def _legacy_mc_match(mcversion, version):
    """mcmod.info: "mcversion": "1.12.2". Сверяем только старшие две цифры (1.12 == 1.12.2)."""
    a = re.match(r"(\d+)\.(\d+)", str(mcversion or ""))
    b = re.match(r"(\d+)\.(\d+)", str(version or ""))
    if not a or not b:
        return None
    return a.groups() == b.groups()


def _mc_before(mc, ref):
    return bool(mc) and compare_versions(mc, ref) < 0


def _pretty_range(kind, rng):
    if rng is None or rng == "" or rng == "*":
        return "любая версия"
    if isinstance(rng, (list, tuple)):
        return " или ".join(_pretty_range(kind, r) for r in rng)
    if isinstance(rng, dict):
        return json.dumps(rng, ensure_ascii=False)
    rng = str(rng).strip()
    if kind == "maven":
        parts = re.findall(r"([\[(])([^\])]*)([\])])", rng)
        if not parts:
            return rng
        out = []
        for lo_b, body, hi_b in parts:
            if "," not in body:
                out.append(body.strip())
                continue
            lo, hi = (x.strip() for x in body.split(",", 1))
            if lo and not hi:
                out.append(f"{lo} и новее" if lo_b == "[" else f"новее {lo}")
            elif hi and not lo:
                out.append(f"до {hi} включительно" if hi_b == "]" else f"старше {hi}")
            elif lo_b == "[" and hi_b == ")":
                out.append(f"от {lo} до {hi} (не включая {hi})")
            else:
                out.append(f"от {lo} до {hi}")
        return " или ".join(out)
    if kind == "fabric":
        terms = rng.split()
        if len(terms) == 1:
            t = terms[0]
            m = re.fullmatch(r"(>=|>|<=|<|~|\^|=)?(.+)", t)
            op, v = m.group(1) or "", m.group(2).rstrip("-")
            return {">=": f"{v} и новее", ">": f"новее {v}", "<=": f"до {v} включительно", "<": f"старше {v}",
                    "~": f"{v}.x", "^": f"{v} и новее (в пределах {v.split('.')[0]}.x)"}.get(op, v)
        m = re.fullmatch(r">=(\S+?)-?\s+<(\S+?)-?", rng)
        if m:
            return f"от {m.group(1)} до {m.group(2)} (не включая {m.group(2)})"
    return rng


# --------------------------------------------------------------------------------------
# Чтение метаданных
# --------------------------------------------------------------------------------------

def _load_json(raw):
    text = raw.decode("utf-8-sig", "replace")
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        pass
    # Нестрогий JSON: комментарии и висячие запятые встречаются в реальных модах.
    text = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', lambda m: m.group(1) or "", text, flags=re.S)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return json.loads(text, strict=False)


def _read_manifest(z, names):
    if "META-INF/MANIFEST.MF" not in names:
        return {}
    text = z.read("META-INF/MANIFEST.MF").decode("utf-8", "replace").replace("\r\n", "\n")
    text = text.replace("\n ", "")  # строки-продолжения
    out = {}
    for line in text.split("\n"):
        k, sep, v = line.partition(":")
        if sep and k.strip() and k.strip() not in out:
            out[k.strip()] = v.strip()
    return out


def _resolve_placeholder(value, manifest):
    if value is None:
        return None
    value = str(value).strip()
    if "${file.jarVersion}" in value:
        jv = manifest.get("Implementation-Version") or manifest.get("Specification-Version")
        return value.replace("${file.jarVersion}", jv) if jv else None
    if "${" in value or value == "":
        return None
    return value


def _dep(dep_id, rng, kind, side="both", reason=None):
    return {"id": str(dep_id).strip().lower(), "range": rng, "kind": kind, "side": side, "reason": reason}


def _entry(loader, mod_id, name, version, **kw):
    e = {"loader": loader, "id": str(mod_id).strip().lower(), "name": name or str(mod_id), "version": version,
         "mc": None, "mc_kind": None, "depends": [], "recommends": [], "optional": [], "breaks": [],
         "provides": [], "environment": "*", "toml_loader": None}
    e.update(kw)
    return e


def _fabric_dep_map(obj, kind="fabric"):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(_dep(k, v, kind))
    elif isinstance(obj, list):  # редкий нестандартный вид
        for k in obj:
            if isinstance(k, str):
                out.append(_dep(k, "*", kind))
    return out


def _parse_fabric(raw):
    d = _load_json(raw)
    if not isinstance(d, dict) or "id" not in d:
        return [], []
    deps = _fabric_dep_map(d.get("depends"))
    mc = next((x["range"] for x in deps if x["id"] == "minecraft"), None)
    e = _entry("fabric", d["id"], d.get("name"), d.get("version"),
               environment=str(d.get("environment", "*")).lower(),
               mc=mc, mc_kind="fabric" if mc is not None else None,
               depends=[x for x in deps if x["id"] not in ("minecraft",)],
               recommends=_fabric_dep_map(d.get("recommends")),
               provides=[str(p).lower() for p in d.get("provides", []) if isinstance(p, str)])
    for x in _fabric_dep_map(d.get("breaks")):
        x.update(level="error", why="breaks")
        e["breaks"].append(x)
    for x in _fabric_dep_map(d.get("conflicts")):
        x.update(level="warn", why="conflicts")
        e["breaks"].append(x)
    jars = [j.get("file") for j in d.get("jars", []) if isinstance(j, dict) and j.get("file")]
    return [e], jars


def _quilt_deps(lst):
    out = []
    if isinstance(lst, (str, dict)):
        lst = [lst]
    for item in lst or []:
        if isinstance(item, str):
            out.append((_dep(item.split(":")[-1], "*", "fabric"), False))
        elif isinstance(item, dict) and item.get("id"):
            d = _dep(str(item["id"]).split(":")[-1], item.get("versions", "*"), "fabric",
                     reason=item.get("reason"))
            out.append((d, bool(item.get("optional")) or bool(item.get("unless"))))
        # вложенные массивы = «любой из»: пропускаем, чтобы не было ложных тревог
    return out


def _parse_quilt(raw):
    d = _load_json(raw)
    ql = d.get("quilt_loader") if isinstance(d, dict) else None
    if not isinstance(ql, dict) or "id" not in ql:
        return [], []
    meta = ql.get("metadata") or {}
    deps = _quilt_deps(ql.get("depends"))
    mc = next((x["range"] for x, _ in deps if x["id"] == "minecraft"), None)
    env = str((d.get("minecraft") or {}).get("environment", "*")).lower()
    provides = []
    for p in ql.get("provides", []) or []:
        pid = p if isinstance(p, str) else (p.get("id") if isinstance(p, dict) else None)
        if pid:
            provides.append(str(pid).split(":")[-1].lower())
    e = _entry("quilt", ql["id"], meta.get("name"), ql.get("version"), environment=env,
               mc=mc, mc_kind="fabric" if mc is not None else None,
               depends=[x for x, opt in deps if not opt and x["id"] != "minecraft"],
               recommends=[x for x, opt in deps if opt and x["id"] != "minecraft"], provides=provides)
    for x, _ in _quilt_deps(ql.get("breaks")):
        x.update(level="error", why="breaks")
        e["breaks"].append(x)
    jars = [j if isinstance(j, str) else j.get("file") for j in ql.get("jars", []) or []]
    return [e], [j for j in jars if j]


# --- TOML: tomllib, а если файл кривой (NightConfig у Forge прощает многое) — простой разборщик

def _loose_toml(text):
    data = {}
    cur = data
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0

    def strip_comment(s):
        out, q = [], None
        for ch in s:
            if q:
                out.append(ch)
                if ch == q:
                    q = None
            elif ch in "\"'":
                q = ch
                out.append(ch)
            elif ch == "#":
                break
            else:
                out.append(ch)
        return "".join(out).strip()

    def parse_value(v):
        v = v.strip()
        if v[:1] in "\"'":
            q = v[0]
            end = v.find(q, 1)
            while q == '"' and end > 0 and v[end - 1] == "\\":
                end = v.find(q, end + 1)
            return v[1:end if end > 0 else None]
        if v.lower() in ("true", "false"):
            return v.lower() == "true"
        if v.startswith("["):
            return [parse_value(p) for p in re.findall(r'"[^"]*"|\'[^\']*\'|[^,\[\]\s]+', v[1:-1])]
        try:
            return int(v)
        except ValueError:
            return v

    def table_path(name):
        return [p.strip().strip("\"'") for p in re.findall(r'"[^"]*"|\'[^\']*\'|[^.]+', name)]

    while i < len(lines):
        line = lines[i]
        i += 1
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"\[\[\s*(.+?)\s*\]\]", s)
        if m:
            path = table_path(m.group(1))
            node = data
            for p in path[:-1]:
                node = node.setdefault(p, {})
                if isinstance(node, list):
                    node = node[-1]
            arr = node.setdefault(path[-1], [])
            if not isinstance(arr, list):
                arr = node[path[-1]] = [arr]
            cur = {}
            arr.append(cur)
            continue
        m = re.match(r"\[\s*(.+?)\s*\]", s)
        if m:
            cur = {}  # обычные таблицы ([mods."sodium:options"] и т. п.) нам не нужны
            continue
        k, sep, v = s.partition("=")
        if not sep:
            continue
        key = k.strip().strip("\"'")
        v = v.strip()
        for q in ("'''", '"""'):
            if v.startswith(q):
                body = v[3:]
                while q not in body and i < len(lines):
                    body += "\n" + lines[i]
                    i += 1
                cur[key] = body.split(q)[0]
                break
        else:
            cur[key] = parse_value(strip_comment(v))
    return data


def _load_toml(raw):
    text = raw.decode("utf-8-sig", "replace")
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return _loose_toml(text)


def _truthy(v):
    if isinstance(v, str):
        return v.strip().lower() == "true"
    return bool(v)


def _parse_toml(raw, manifest, file_kind):
    """file_kind: "neoforge" для neoforge.mods.toml, "forge" для mods.toml."""
    data = _load_toml(raw)
    mods = data.get("mods") or []
    if isinstance(mods, dict):
        mods = [mods]
    deps_root = data.get("dependencies") or {}
    if not isinstance(deps_root, dict):
        deps_root = {}
    req_ids, any_ids = set(), set()
    for lst in deps_root.values():
        for dep in (lst if isinstance(lst, list) else [lst]):
            if isinstance(dep, dict) and dep.get("modId"):
                did = str(dep["modId"]).lower()
                any_ids.add(did)
                typ = str(dep.get("type", "")).lower()
                if typ == "required" or (not typ and _truthy(dep.get("mandatory", False))):
                    req_ids.add(did)
    if file_kind == "neoforge":
        toml_loader = "neoforge"
    else:
        # NeoForge 1.20.2–1.20.4 ещё читал mods.toml; бывают и «универсальные» jar с обоими id
        pool = req_ids & {"forge", "neoforge"} or any_ids & {"forge", "neoforge"}
        toml_loader = next(iter(pool)) if len(pool) == 1 else None
    client_only = _truthy(data.get("clientSideOnly", False))
    entries = []
    for m in mods:
        if not isinstance(m, dict) or not m.get("modId"):
            continue
        mid = str(m["modId"])
        e = _entry("neoforge" if file_kind == "neoforge" else "forge", mid, m.get("displayName"),
                   _resolve_placeholder(m.get("version"), manifest), toml_loader=toml_loader,
                   environment="client" if client_only else "*",
                   provides=[str(p).lower() for p in (m.get("provides") or []) if isinstance(p, str)])
        dl = deps_root.get(mid, deps_root.get(mid.lower(), []))
        for dep in (dl if isinstance(dl, list) else [dl]):
            if not isinstance(dep, dict) or not dep.get("modId"):
                continue
            typ = str(dep.get("type", "")).lower()
            if not typ:
                if "mandatory" in dep:
                    typ = "required" if _truthy(dep["mandatory"]) else "optional"
                else:
                    typ = "required" if file_kind == "neoforge" else "optional"
            d = _dep(dep["modId"], dep.get("versionRange", ""), "maven",
                     side=str(dep.get("side", "BOTH")).lower(), reason=dep.get("reason"))
            if d["id"] == "minecraft":
                if typ in ("required", "optional"):
                    e["mc"], e["mc_kind"] = d["range"], "maven"
                continue
            if typ == "required":
                e["depends"].append(d)
            elif typ == "optional":
                e["optional"].append(d)
            elif typ in ("incompatible", "discouraged"):
                d.update(level="error" if typ == "incompatible" else "warn", why=typ)
                e["breaks"].append(d)
        entries.append(e)
    return entries


_LEGACY_MOD_DESC = b"Lnet/minecraftforge/fml/common/Mod;"
_LEGACY_DEP_RE = re.compile(rb"((?:required-after|required-before|required-client|required-server|required"
                            rb"|after|before)(?::[A-Za-z0-9_\-.*]+(?:@[\[(][^;\x00-\x1f]*?[\])])?;?)+)")


def _scan_legacy_deps(z, names, limit=6000):
    """Forge 1.12: настоящие зависимости живут в аннотации @Mod(dependencies="required-after:x@[1,)")."""
    deps = {}
    n = 0
    for name in names:
        if not name.endswith(".class"):
            continue
        n += 1
        if n > limit:
            break
        info = z.getinfo(name)
        if info.file_size > 400_000:
            continue
        data = z.read(name)
        if _LEGACY_MOD_DESC not in data:
            continue
        for m in re.finditer(rb"(?:required(?:-after|-before|-client|-server)?):([A-Za-z0-9_\-.]+)"
                             rb"(@[\[(][^;\x00-\x1f]*?[\])])?", data):
            mid = m.group(1).decode("ascii", "replace").lower()
            rng = (m.group(2) or b"").decode("ascii", "replace").lstrip("@")
            if mid not in ("*",):
                deps.setdefault(mid, rng)
    return [_dep(k, v, "maven") for k, v in deps.items()]


def _parse_mcmod(raw, z, names):
    d = _load_json(raw)
    if isinstance(d, dict):
        d = d.get("modList") or d.get("modlist") or []
    entries = []
    for m in d if isinstance(d, list) else []:
        if not isinstance(m, dict) or not m.get("modid"):
            continue
        mc = _resolve_placeholder(m.get("mcversion"), {})
        e = _entry("forge_legacy", m["modid"], m.get("name"), _resolve_placeholder(m.get("version"), {}),
                   mc=mc, mc_kind="legacy" if mc else None)
        if m.get("useDependencyInformation"):
            for r in m.get("requiredMods") or []:
                rid, _, rng = str(r).partition("@")
                e["depends"].append(_dep(rid, rng, "maven"))
        entries.append(e)
    if entries:
        scanned = _scan_legacy_deps(z, names)
        known = {x["id"] for x in entries[0]["depends"]}
        own = {e["id"] for e in entries}
        entries[0]["depends"] += [x for x in scanned if x["id"] not in known and x["id"] not in own]
    return entries


def _detect_optifine(names, filename):
    if not any(n.startswith(("net/optifine/", "optifine/")) for n in names):
        return None
    m = re.search(r"OptiFine[_-]([\d.]+)[_-](HD_U_\w+?)(?:\.jar)?$", filename, re.I)
    mc = m.group(1) if m else None
    return _entry("optifine", "optifine", "OptiFine", m.group(2) if m else None,
                  mc=mc, mc_kind="legacy" if mc else None, environment="client")


def _read_zip(z, filename, depth):
    names = z.namelist()
    nameset = set(names)
    manifest = _read_manifest(z, nameset)
    entries, nested, errors = [], [], []

    def guarded(label, fn):
        try:
            return fn()
        except Exception as exc:  # битые метаданные не должны ронять всю проверку
            errors.append(f"{label}: {exc}")
            return None

    if "fabric.mod.json" in nameset:
        r = guarded("fabric.mod.json", lambda: _parse_fabric(z.read("fabric.mod.json")))
        if r:
            entries += r[0]
            nested += r[1]
    if "quilt.mod.json" in nameset:
        r = guarded("quilt.mod.json", lambda: _parse_quilt(z.read("quilt.mod.json")))
        if r:
            entries += r[0]
            nested += r[1]
    if "META-INF/neoforge.mods.toml" in nameset:
        entries += guarded("neoforge.mods.toml", lambda: _parse_toml(
            z.read("META-INF/neoforge.mods.toml"), manifest, "neoforge")) or []
    if "META-INF/mods.toml" in nameset:
        entries += guarded("mods.toml", lambda: _parse_toml(z.read("META-INF/mods.toml"), manifest, "forge")) or []
    if "mcmod.info" in nameset:
        entries += guarded("mcmod.info", lambda: _parse_mcmod(z.read("mcmod.info"), z, names)) or []
    if "META-INF/jarjar/metadata.json" in nameset:
        meta = guarded("jarjar", lambda: _load_json(z.read("META-INF/jarjar/metadata.json")))
        for j in (meta or {}).get("jars", []) if isinstance(meta, dict) else []:
            if isinstance(j, dict) and j.get("path"):
                nested.append(j["path"])
    if not entries and depth == 0:
        of = _detect_optifine(names, filename)
        if of:
            entries.append(of)
        elif "mcmod.info" not in nameset and any(n.endswith(".class") for n in names[:4000]):
            # мод 1.12 без mcmod.info: ищем аннотацию @Mod прямо в классах
            deps = guarded("classes", lambda: _scan_legacy_deps(z, names))
            if deps is not None and any(_LEGACY_MOD_DESC in z.read(n) for n in names[:4000]
                                        if n.endswith(".class") and z.getinfo(n).file_size < 400_000):
                base = re.sub(r"[-_ ]?v?\d.*$", "", filename[:-4]) or filename[:-4]
                entries.append(_entry("forge_legacy", base.lower(), base, None, depends=deps))

    embedded = []
    if depth < 4:
        for p in dict.fromkeys(nested):
            if p not in nameset:
                continue
            try:
                with zipfile.ZipFile(io.BytesIO(z.read(p))) as sub:
                    info = _read_zip(sub, os.path.basename(p), depth + 1)
            except (zipfile.BadZipFile, OSError, ValueError):
                continue
            for e in info["entries"]:
                embedded.append({"file": os.path.basename(p), "id": e["id"], "version": e["version"],
                                 "loader": e["loader"], "provides": e["provides"], "toml_loader": e["toml_loader"]})
            embedded += info["embedded"]
    return {"entries": entries, "embedded": embedded, "errors": errors}


def read_mod(jar_path):
    """Читает метаданные одного jar.

    Возвращает dict: file, path, is_mod, id, name, version, loaders (список), mc (заявленный диапазон
    версий Minecraft), depends / breaks / provides (основной записи), embedded (jar-in-jar, плоский
    список), entries (все записи по всем загрузчикам), errors.
    loaders: "fabric", "quilt", "forge", "neoforge", "forge_legacy" (mcmod.info, Forge до 1.13), "optifine".
    """
    filename = os.path.basename(jar_path)
    out = {"file": filename, "path": jar_path, "is_mod": False, "id": None, "name": filename, "version": None,
           "loaders": [], "mc": None, "depends": [], "breaks": [], "provides": [], "embedded": [],
           "entries": [], "errors": []}
    try:
        with zipfile.ZipFile(jar_path) as z:
            info = _read_zip(z, filename, 0)
    except (zipfile.BadZipFile, OSError, ValueError, EOFError) as exc:
        out["errors"].append(f"bad_zip: {exc}")
        out["bad_zip"] = True
        return out
    out.update(entries=info["entries"], embedded=info["embedded"], errors=info["errors"])
    if not info["entries"]:
        # контейнер без своих метаданных (например, kotlinforforge-*-all.jar): моды лежат внутри
        if info["embedded"]:
            out["container"] = True
        return out
    e = info["entries"][0]
    out.update(is_mod=True, id=e["id"], name=e["name"], version=e["version"], mc=e["mc"],
               depends=e["depends"], breaks=e["breaks"], provides=e["provides"])
    out["loaders"] = list(dict.fromkeys(x["loader"] for x in info["entries"]))
    return out


# --------------------------------------------------------------------------------------
# Проверки
# --------------------------------------------------------------------------------------

def _p(level, kind, text, mods, fix=None):
    return {"level": level, "kind": kind, "text": text, "mods": list(dict.fromkeys(mods)), "fix": fix}


def _dep_title(dep_id):
    if dep_id in KNOWN_NAMES:
        return KNOWN_NAMES[dep_id]
    if dep_id.startswith("fabric-") and (dep_id.endswith(("-v0", "-v1", "-v2", "-v3")) or "-api" in dep_id):
        return f"Fabric API (модуль {dep_id})"
    if dep_id.startswith("quilted_fabric_") or dep_id.startswith("quilt_"):
        return f"Quilted Fabric API / QSL ({dep_id})"
    return dep_id


def _builtins(loader, mc, lv):
    if loader == "fabric":
        return {"minecraft": mc, "java": None, "fabricloader": lv, "mixinextras": None}
    if loader == "quilt":
        return {"minecraft": mc, "java": None, "quilt_loader": lv, "fabricloader": None, "mixinextras": None}
    if loader == "neoforge":
        b = {"minecraft": mc, "neoforge": lv, "fml": None, "javafml": None}
        if mc and compare_versions(mc, "1.20.1") == 0:
            b["forge"] = lv  # NeoForge 1.20.1 — форк Forge 47.1 и отвечает за modId "forge"
        return b
    if loader == "forge":
        if _mc_before(mc, "1.13"):
            return {"minecraft": mc, "forge": lv, "fml": None, "mcp": None, "forgemodloader": None}
        return {"minecraft": mc, "forge": lv, "fml": None, "javafml": None}
    return {"minecraft": mc}


_CONNECTOR_IDS = {"connector"}
_FFAPI_IDS = {"fabric_api", "forgified_fabric_api"}


def _usable(info, loader, mc, connector):
    """Какие записи jar реально прочитает выбранный загрузчик. -> (entries, via, reason)"""
    by = {}
    for e in info["entries"]:
        by.setdefault(e["loader"], []).append(e)
    if "optifine" in by:
        if loader in ("forge", "neoforge"):
            return by["optifine"], "native", None
        return [], None, "optifine_fabric"
    if loader == "fabric":
        return by.get("fabric", []), "native", None
    if loader == "quilt":
        if by.get("quilt"):
            return by["quilt"], "native", None
        return by.get("fabric", []), "native", None
    legacy_pack = _mc_before(mc, "1.13")
    if loader == "forge":
        if legacy_pack:
            return by.get("forge_legacy", []), "native", ("modern_forge" if by.get("forge") else None)
        ok = [e for e in by.get("forge", []) if e["toml_loader"] != "neoforge"]
        if ok:
            return ok, "native", None
        if by.get("forge_legacy"):
            return [], None, "legacy_forge"
        if connector and by.get("fabric"):
            return by["fabric"], "connector", None
        return [], None, None
    if loader == "neoforge":
        if by.get("neoforge"):
            return by["neoforge"], "native", None
        toml = by.get("forge", [])
        if toml and mc and compare_versions(mc, "1.20.5") < 0:
            if compare_versions(mc, "1.20.1") <= 0:
                return toml, "native", None  # NeoForge 1.20.1 грузит моды Forge
            ok = [e for e in toml if e["toml_loader"] != "forge"]
            if ok:
                return ok, "native", None
            return [], None, "forge_on_neoforge"
        if toml:
            return [], None, "forge_on_neoforge"
        if connector and by.get("fabric"):
            return by["fabric"], "connector", None
        if by.get("forge_legacy"):
            return [], None, "legacy_forge"
        return [], None, None
    return [], None, None


def _jar_files(mods_dir):
    try:
        names = sorted(os.listdir(mods_dir), key=str.lower)
    except OSError:
        return None
    return [n for n in names if n.lower().endswith(".jar") and os.path.isfile(os.path.join(mods_dir, n))]


def check_mods(mods_dir, mc_version, loader, loader_version=None, *, side="client"):
    """Проверяет папку mods. Возвращает список проблем, сначала ошибки, потом предупреждения и сведения."""
    loader = str(loader).strip().lower()
    mc = str(mc_version).strip() if mc_version else None
    files = _jar_files(mods_dir)
    if files is None:
        return [_p("error", "no_dir", f"Папка «{mods_dir}» не найдена или недоступна.", [],
                   "Проверьте путь к папке mods.")]
    problems = []
    infos = []
    for f in files:
        info = read_mod(os.path.join(mods_dir, f))
        infos.append(info)
        if info.get("bad_zip"):
            problems.append(_p("error", "bad_jar", f"Файл «{f}» повреждён: он не открывается как архив.", [f],
                               "Скачайте мод заново или удалите файл."))
        elif info["errors"] and not info["entries"]:
            problems.append(_p("warn", "bad_metadata",
                               f"В «{f}» не удалось прочитать описание мода, проверить его нельзя.", [f], None))

    all_ids = set()
    for info in infos:
        for e in info["entries"]:
            all_ids.add(e["id"])
        for em in info["embedded"]:
            all_ids.add(em["id"])
    connector = bool(all_ids & _CONNECTOR_IDS) and loader in ("forge", "neoforge")
    ffapi = bool(all_ids & _FFAPI_IDS)

    lt = LOADER_TITLES.get(loader, loader)
    used = []  # (info, entries, via)
    for info in infos:
        f = info["file"]
        if info.get("bad_zip"):
            continue
        if not info["entries"]:
            if info.get("container"):
                # контейнер jar-in-jar: оцениваем вложенные моды
                emb_loaders = {em["loader"] for em in info["embedded"]}
                fake = {"entries": [_entry(em["loader"], em["id"], em["id"], em["version"],
                                           provides=em["provides"], toml_loader=em["toml_loader"])
                                    for em in info["embedded"]]}
                ents, via, _ = _usable(fake, loader, mc, connector)
                if ents:
                    used.append((info, [], via))  # даёт только id (через embedded), без своих проверок
                    continue
                if emb_loaders & {"fabric", "quilt", "forge", "neoforge"}:
                    names = ", ".join(LOADER_TITLES.get(x, x) for x in sorted(emb_loaders))
                    problems.append(_p("error", "wrong_loader",
                                       f"«{f}» содержит моды для {names}, а сборка на {lt}.", [f],
                                       f"Удалите файл или скачайте версию для {lt}."))
                    continue
            if not info["errors"]:
                problems.append(_p("info", "not_a_mod",
                                   f"«{f}» — не мод: внутри нет описания ни для одного загрузчика. "
                                   "Скорее всего, это библиотека или лишний файл.", [f],
                                   "Если его не просил другой мод, файл можно убрать из папки mods."))
            continue
        ents, via, reason = _usable(info, loader, mc, connector)
        if not ents:
            name = info["name"]
            targets = ", ".join(LOADER_TITLES.get(x, x) for x in info["loaders"])
            if reason == "optifine_fabric":
                if "optifabric" in all_ids:
                    used.append((info, info["entries"], "optifabric"))
                    continue
                problems.append(_p("error", "wrong_loader",
                                   f"OptiFine («{f}») не работает на {lt} сам по себе.", [f],
                                   "Уберите OptiFine: на Fabric вместо него ставят Sodium + Iris."))
            elif reason == "legacy_forge":
                problems.append(_p("error", "wrong_mc",
                                   f"«{name}» сделан для старого Forge (Minecraft 1.12.2 и раньше), "
                                   f"а сборка на {mc}.", [f], f"Найдите версию мода для {lt} {mc} или уберите его."))
            elif reason == "modern_forge":
                problems.append(_p("error", "wrong_mc",
                                   f"«{name}» сделан для Forge 1.13 и новее, а сборка на {mc}.", [f],
                                   f"Найдите версию мода для Minecraft {mc} или уберите его."))
            elif reason == "forge_on_neoforge":
                problems.append(_p("error", "wrong_loader",
                                   f"«{name}» сделан для Forge. NeoForge {mc} моды Forge не грузит "
                                   "(совместимость была только на 1.20.1).", [f],
                                   "Скачайте версию мода для NeoForge или уберите его."))
            else:
                fix = f"Удалите его или скачайте версию для {lt}."
                if loader in ("forge", "neoforge") and "fabric" in info["loaders"]:
                    fix += " Запустить мод Fabric можно через Sinytra Connector, но работает это не со всеми модами."
                problems.append(_p("error", "wrong_loader", f"«{name}» сделан для {targets}, а сборка на {lt}.",
                                   [f], fix))
            continue
        used.append((info, ents, via))
        if via == "connector":
            problems.append(_p("info", "via_connector",
                               f"«{info['name']}» — мод для Fabric, его загрузит Sinytra Connector.", [info["file"]],
                               None if ffapi else "Для Connector нужен Forgified Fabric API — добавьте его."))

    # --- индекс присутствующих модов
    present = {}  # id -> [(version, file, how)]
    top_ids = {}  # id -> [(info, entry)]
    builtins = _builtins(loader, mc, loader_version)
    if connector:
        builtins.setdefault("fabricloader", None)  # Connector сам изображает Fabric Loader
        builtins.setdefault("java", None)
    for info, ents, via in used:
        for e in ents:
            present.setdefault(e["id"], []).append((e["version"], info["file"], "top"))
            top_ids.setdefault(e["id"], []).append((info, e))
            for pid in e["provides"]:
                present.setdefault(pid, []).append((e["version"], info["file"], "provides"))
        for em in info["embedded"]:
            fake = {"entries": [_entry(em["loader"], em["id"], em["id"], em["version"], toml_loader=em["toml_loader"])]}
            if _usable(fake, loader, mc, connector)[0] or via == "connector":
                present.setdefault(em["id"], []).append((em["version"], info["file"], "embedded"))
                for pid in em["provides"]:
                    present.setdefault(pid, []).append((em["version"], info["file"], "embedded"))

    def satisfied_by_ffapi(dep_id):
        return connector and ffapi and (dep_id.startswith("fabric-") or dep_id in ("fabric", "fabric-api"))

    # --- 2. версия Minecraft
    for info, ents, via in used:
        for e in ents:
            if e["mc"] is None or not mc:
                continue
            ok = _match(e["mc_kind"], e["mc"], mc, is_minecraft=True)
            if ok is False:
                problems.append(_p("error", "wrong_mc",
                                   f"«{e['name']}» рассчитан на Minecraft {_pretty_range(e['mc_kind'], e['mc'])}, "
                                   f"а сборка на {mc}.", [info["file"]],
                                   f"Скачайте версию «{e['name']}» для {mc} или уберите мод."))
                break

    # --- 3. зависимости
    for info, ents, via in used:
        f = info["file"]
        for e in ents:
            own = {e["id"], *e["provides"]}
            for dep, required in [(d, True) for d in e["depends"]] + [(d, False) for d in e["optional"]] \
                    + [(d, None) for d in e["recommends"]]:
                did = dep["id"]
                if did in own or did == "java":
                    continue
                if required and side == "client" and dep["side"] == "server":
                    continue
                if required and side == "server" and dep["side"] == "client":
                    continue
                if did in builtins:
                    ver = builtins[did]
                    if did == "minecraft" or ver is None:
                        continue
                    if _match(dep["kind"], dep["range"], ver) is False:
                        need = _pretty_range(dep["kind"], dep["range"])
                        fix = f"Обновите {lt} до подходящей версии или возьмите другую версию мода."
                        if loader == "neoforge" and did == "forge":
                            fix = ("NeoForge 1.20.1 соответствует Forge 47.1, новее не бывает. Этому моду нужен "
                                   "настоящий Forge — поставьте Forge или найдите версию мода постарше.")
                        problems.append(_p("error", "loader_version",
                                           f"«{e['name']}» требует {_dep_title(did)} версии {need}, а у вас {ver}.", [f], fix))
                    continue
                cands = present.get(did)
                if not cands:
                    if satisfied_by_ffapi(did):
                        continue
                    if required:
                        rng = dep["range"]
                        need = "" if rng in (None, "", "*") else f" (версии {_pretty_range(dep['kind'], rng)})"
                        problems.append(_p("error", "missing_dep",
                                           f"Моду «{e['name']}» нужен «{_dep_title(did)}»{need}, а его нет в папке.",
                                           [f], f"Скачайте «{_dep_title(did)}» для {lt} {mc} и положите в mods."))
                    elif required is None:
                        problems.append(_p("info", "recommends",
                                           f"«{e['name']}» советует поставить «{_dep_title(did)}».", [f], None))
                    continue
                res = [_match(dep["kind"], dep["range"], v) for v, _, _ in cands if v]
                if res and all(r is False for r in res):
                    v, df, _ = cands[0]
                    need = _pretty_range(dep["kind"], dep["range"])
                    level = "warn" if required is None else "error"
                    problems.append(_p(level, "dep_version",
                                       f"Моду «{e['name']}» нужен «{_dep_title(did)}» версии {need}, "
                                       f"а стоит {v} ({df}).", [f, df],
                                       f"Замените «{df}» на подходящую версию «{_dep_title(did)}»."))

    # --- 4. явные несовместимости из метаданных
    seen_pairs = set()
    titles = {"breaks": "несовместим с", "incompatible": "несовместим с",
              "conflicts": "конфликтует с", "discouraged": "плохо работает вместе с"}
    for info, ents, via in used:
        f = info["file"]
        for e in ents:
            for b in e["breaks"]:
                if b["id"] in (e["id"], *e["provides"]):
                    continue
                for v, bf, how in present.get(b["id"], []):
                    if bf == f:
                        continue
                    ok = True if b["range"] in (None, "", "*") else _match(b["kind"], b["range"], v) if v else None
                    if ok is not True:
                        continue
                    pair = frozenset((f, bf))
                    if pair in seen_pairs:
                        break
                    seen_pairs.add(pair)
                    other = top_ids.get(b["id"], [(None, {"name": b["id"]})])[0][1]["name"]
                    where = f" (встроен в «{bf}»)" if how == "embedded" else ""
                    ver = "" if b["range"] in (None, "", "*") else f" версий {_pretty_range(b['kind'], b['range'])}"
                    text = f"«{e['name']}» {titles.get(b['why'], 'несовместим с')} «{other}»{ver}{where} — так указано в самом моде."
                    if b.get("reason"):
                        text += f" Причина: {b['reason']}"
                    kind = "incompatible" if b["level"] == "error" else "conflict"
                    fix = (f"Оставьте что-то одно: уберите «{bf}» или «{f}»." if not ver else
                           f"Обновите «{other}» или уберите один из модов.")
                    problems.append(_p(b["level"], kind, text, [f, bf], fix))
                    break

    # --- 5. дубликаты
    for mid, lst in top_ids.items():
        files_ = list(dict.fromkeys(i["file"] for i, _ in lst))
        if len(files_) < 2:
            continue
        vers = {i["file"]: e["version"] for i, e in lst}
        name = lst[0][1]["name"]
        newest = files_[0]
        for fl in files_[1:]:
            if vers.get(fl) and (not vers.get(newest) or compare_versions(vers[fl], vers[newest]) > 0):
                newest = fl
        same = len({vers.get(x) for x in files_}) == 1
        listing = ", ".join(f"«{x}»" + (f" ({vers[x]})" if vers.get(x) else "") for x in files_)
        fix = ("Оставьте один файл, остальные удалите." if same or not any(vers.values())
               else f"Оставьте «{newest}» (он новее), остальные удалите.")
        text = f"Мод «{name}» лежит в папке несколько раз: {listing}."
        if any(i["entries"][0]["id"] != mid for i, _ in lst):
            text = (f"Несколько файлов объявляют один и тот же мод «{mid}»: {listing}. "
                    "Загрузчик откажется запускать игру.")
            fix = "Оставьте один из этих файлов."
        problems.append(_p("error", "duplicate", text, files_, fix))
        for i, fa in enumerate(files_):
            for fb in files_[i + 1:]:
                seen_pairs.add(frozenset((fa, fb)))

    # --- 6. известные конфликты
    nometa = [i for i in infos if not i["entries"] and not i.get("bad_zip") and not i.get("container")]
    problems += _known_conflicts(used, nometa, loader, mc, seen_pairs)

    problems.sort(key=lambda p: LEVEL_ORDER.get(p["level"], 9))
    return problems


def _kc_find(spec, used, nometa):
    """Все jar, подходящие под описание из таблицы: по id из метаданных (с условием на версию),
    а у jar без метаданных — по имени файла."""
    ids = {i.lower() for i in spec.get("ids", ())}
    want_ver = spec.get("version")
    found = []
    for info, ents, via in used:
        for e in ents:
            if e["id"] in ids and (not want_ver or fabric_match(want_ver, e["version"]) is True):
                found.append(info["file"])
                break
    pats = [p.lower() for p in spec.get("jars", ())]
    for info in nometa:
        if any(p in info["file"].lower() for p in pats):
            found.append(info["file"])
    return found


def _known_conflicts(used, nometa, loader, mc, seen_pairs):
    out = []
    for kc in KNOWN_CONFLICTS:
        if kc.get("loaders") and loader not in kc["loaders"]:
            continue
        if kc.get("mc") and mc and fabric_match(kc["mc"], mc, is_minecraft=True) is False:
            continue
        if "group" in kc:
            found, files = [], []
            for title, spec in kc["group"]:
                hit = [f for f in _kc_find(spec, used, nometa) if f not in files]
                if hit:
                    found.append(title)
                    files.append(hit[0])
            if len(found) >= kc.get("min", 2):
                names = ", ".join(f"«{t}»" for t in found)
                out.append(_p(kc["level"], "known_conflict", kc["text"].format(names=names), files, kc.get("fix")))
            continue
        fas = _kc_find(kc["a"], used, nometa)
        if not fas:
            continue
        if "requires_any" in kc:
            if not any(_kc_find(r, used, nometa) for r in kc["requires_any"]):
                out.append(_p(kc["level"], "known_conflict", kc["text"], [fas[0]], kc.get("fix")))
            continue
        fbs = _kc_find(kc["b"], used, nometa)
        pair = next(((fa, fb) for fa in fas for fb in fbs if fa != fb), None)
        if not pair or frozenset(pair) in seen_pairs:
            continue  # один и тот же jar или уже сказали по метаданным
        seen_pairs.add(frozenset(pair))
        out.append(_p(kc["level"], "known_conflict", kc["text"], list(pair), kc.get("fix")))
    return out


def server_unsafe(mods_dir):
    """Моды, которым не место на выделенном сервере (только для клиента), и цепочки зависимостей от них."""
    files = _jar_files(mods_dir) or []
    infos = [read_mod(os.path.join(mods_dir, f)) for f in files]
    client_only = {}
    out = []
    for info in infos:
        for e in info["entries"]:
            if e["environment"] == "client":
                client_only[e["id"]] = info
                if e["loader"] == "optifine":
                    text = f"OptiFine («{info['file']}») — клиентский мод, сервер с ним не запустится."
                elif e["loader"] in ("fabric", "quilt"):
                    text = (f"«{e['name']}» работает только в клиенте. Выделенный сервер его пропустит, "
                            "но лишний файл лучше убрать.")
                else:
                    text = (f"«{e['name']}» помечен как мод только для клиента: на выделенном сервере "
                            "он не нужен.")
                out.append(_p("info", "client_only", text, [info["file"]],
                              "В папку mods сервера его не кладите, у игроков оставьте."))
                break
    for info in infos:
        for e in info["entries"]:
            if e["environment"] == "client":
                continue
            for d in e["depends"]:
                if d["id"] in client_only and d["side"] != "client":
                    co = client_only[d["id"]]
                    out.append(_p("warn", "server_dep_on_client",
                                  f"«{e['name']}» нужен на сервере, но требует клиентский «{co['name']}» — "
                                  "сервер не запустится.", [info["file"], co["file"]],
                                  f"На сервере уберите и «{info['file']}», если он тоже только для игроков, "
                                  "или найдите серверную версию."))
    return out


# --------------------------------------------------------------------------------------
# Известные конфликты (не всегда записаны в метаданных). Заполняется ниже.
# Формат: {"a": {"ids": [...], "jars": [...]}, "b": {...}, "level", "loaders", "mc", "text", "fix", "source"}
#  или    {"a": {...}, "requires_any": [{...}, ...], ...}       — «A без B не работает»
#  или    {"group": [(название, {...}), ...], "min": 2, ...}   — «несколько модов делают одно и то же»
# --------------------------------------------------------------------------------------

_OPTIFINE = {"ids": ["optifine", "optifabric"], "jars": ["optifine", "optifabric"]}
_SODIUM_FORKS_FORGE = {"ids": ["embeddium", "rubidium", "magnesium", "chlorine"]}
_FABRIC_LIKE = ["fabric", "quilt"]
_FORGE_LIKE = ["forge", "neoforge"]

KNOWN_CONFLICTS = [
    # ---------------- OptiFine ----------------
    # Sodium сам объявляет breaks: optifabric = "*".
    # https://github.com/CaffeineMC/sodium/blob/dev/fabric/src/main/resources/fabric.mod.json
    {"a": _OPTIFINE, "b": {"ids": ["sodium"]}, "level": "error", "loaders": _FABRIC_LIKE + _FORGE_LIKE,
     "text": "OptiFine несовместим с Sodium: оба переписывают рендер, игра упадёт при запуске.",
     "fix": "Уберите OptiFine (и OptiFabric): Sodium с Iris дают то же самое.",
     "source": "https://modrinth.com/mod/sodium ; breaks в fabric.mod.json (sodium-fabric-0.9.2+mc26.1.2.jar)"},
    # Iris объявляет breaks: optifabric = "*".
    {"a": _OPTIFINE, "b": {"ids": ["iris"]}, "level": "error", "loaders": _FABRIC_LIKE + _FORGE_LIKE,
     "text": "OptiFine несовместим с Iris: это два разных мода для шейдеров, вместе они не запустятся.",
     "fix": "Оставьте Iris (шейдеры OptiFine он тоже грузит), OptiFine и OptiFabric уберите.",
     "source": "https://modrinth.com/mod/iris ; breaks в fabric.mod.json (iris-fabric-1.11.4+mc26.1.2.jar)"},
    # Порты Sodium на Forge: «OptiFine is not supported, and cannot be supported, in conjunction with this mod».
    {"a": _OPTIFINE, "b": _SODIUM_FORKS_FORGE, "level": "error", "loaders": _FORGE_LIKE,
     "text": "OptiFine несовместим с Embeddium/Rubidium (порты Sodium): оба лезут в рендер, игра упадёт.",
     "fix": "Уберите OptiFine. Для шейдеров вместо него — Oculus вместе с Embeddium.",
     "source": "https://www.modpackindex.com/mod/21967/chlorine"},
    # Oculus — форк Iris; Iris объявляет breaks optifabric.
    {"a": _OPTIFINE, "b": {"ids": ["oculus"]}, "level": "error", "loaders": _FORGE_LIKE,
     "text": "OptiFine несовместим с Oculus: оба отвечают за шейдеры и мешают друг другу.",
     "fix": "Оставьте Oculus (он читает шейдеры OptiFine), OptiFine уберите.",
     "source": "https://modrinth.com/mod/iris ; breaks в fabric.mod.json (iris-fabric-1.11.4+mc26.1.2.jar)"},
    # EMF и ETF объявляют breaks: optifabric = "*" (они сами заменяют CEM/случайные мобы OptiFine).
    {"a": _OPTIFINE, "b": {"ids": ["entity_model_features", "entity_texture_features"]}, "level": "error",
     "loaders": _FABRIC_LIKE,
     "text": "OptiFabric несовместим с Entity Model/Texture Features: они сами делают то, что умеет OptiFine.",
     "fix": "Уберите OptiFine и OptiFabric — модели и текстуры мобов из ресурспаков покажут EMF и ETF.",
     "source": "https://modrinth.com/mod/entitytexturefeatures ; breaks в fabric.mod.json (entity_texture_features-7.2.2, entity_model_features-3.3.6)"},
    # LambDynamicLights объявляет breaks: optifabric (у OptiFine своё динамическое освещение).
    {"a": _OPTIFINE, "b": {"ids": ["lambdynlights"]}, "level": "error", "loaders": _FABRIC_LIKE,
     "text": "OptiFabric несовместим с LambDynamicLights.",
     "fix": "Уберите OptiFine и OptiFabric либо LambDynamicLights.",
     "source": "https://modrinth.com/mod/lambdynamiclights ; breaks в fabric.mod.json и incompatible в neoforge.mods.toml (lambdynamiclights-4.11.2+26.1.2.jar)"},
    # Zoomify объявляет breaks: optifabric = "*" (оба вешают зум).
    {"a": _OPTIFINE, "b": {"ids": ["zoomify"]}, "level": "error", "loaders": _FABRIC_LIKE,
     "text": "OptiFabric несовместим с Zoomify: у OptiFine свой зум.",
     "fix": "Уберите Zoomify или OptiFine с OptiFabric.",
     "source": "https://modrinth.com/mod/zoomify ; breaks в fabric.mod.json (zoomify-2.16.1+26.1.jar)"},
    # «Optifine: DH partially works with forward rendered shaders».
    {"a": _OPTIFINE, "b": {"ids": ["distanthorizons"]}, "level": "warn", "loaders": None,
     "text": "Distant Horizons с OptiFine работает лишь частично: многие шейдеры дальние чанки не рисуют.",
     "fix": "Для дальней прорисовки с шейдерами лучше Sodium + Iris вместо OptiFine.",
     "source": "https://modrinth.com/mod/distanthorizons"},

    # ---------------- Рендер: Sodium и его форки ----------------
    {"a": {"ids": ["sodium"]}, "b": {"ids": ["embeddium"]}, "level": "error", "loaders": None,
     "text": "Sodium и Embeddium вместе не работают: Embeddium — форк Sodium, это два одинаковых движка рендера.",
     "fix": "Оставьте один: на Fabric — Sodium, на Forge/NeoForge — Embeddium или Sodium для NeoForge.",
     "source": "https://modrinth.com/mod/sodium ; breaks в fabric.mod.json (sodium-fabric-0.9.2+mc26.1.2.jar)"},
    {"a": {"ids": ["iris"]}, "b": {"ids": ["embeddium"]}, "level": "error", "loaders": None,
     "text": "Iris не работает с Embeddium: ему нужен оригинальный Sodium.",
     "fix": "Замените Embeddium на Sodium или Iris на Oculus (на Forge).",
     "source": "https://modrinth.com/mod/iris ; breaks в fabric.mod.json (iris-fabric-1.11.4+mc26.1.2.jar)"},
    # Embeddium в своём mods.toml объявляет второй мод с modId "rubidium" (замена Rubidium).
    {"a": {"ids": ["embeddium"]}, "b": {"ids": ["rubidium"]}, "level": "error", "loaders": _FORGE_LIKE,
     "text": "Embeddium уже заменяет Rubidium (он сам называется rubidium для других модов), вдвоём они не загрузятся.",
     "fix": "Уберите Rubidium, оставьте Embeddium.",
     "source": "https://modrinth.com/mod/embeddium ; второй [[mods]] modId=\"rubidium\" в META-INF/mods.toml (embeddium-0.3.31+mc1.20.1.jar)"},
    {"a": {"ids": ["rubidium"]}, "b": {"ids": ["magnesium", "chlorine"]}, "level": "error", "loaders": _FORGE_LIKE,
     "text": "Rubidium и Magnesium/Chlorine — разные порты Sodium на Forge, вместе они не работают.",
     "fix": "Оставьте один порт Sodium, лучше Embeddium.",
     "source": "https://www.modpackindex.com/mod/21967/chlorine"},
    {"a": {"ids": ["sodium"]}, "b": {"ids": ["canvas"]}, "level": "error", "loaders": None,
     "text": "Sodium и Canvas — два разных движка рендера, вместе не работают.",
     "fix": "Оставьте что-то одно.",
     "source": "https://modrinth.com/mod/sodium ; breaks в fabric.mod.json (sodium-fabric-0.9.2+mc26.1.2.jar)"},
    {"a": {"ids": ["iris"]}, "b": {"ids": ["canvas"]}, "level": "error", "loaders": None,
     "text": "Iris не работает вместе с Canvas.",
     "fix": "Оставьте Iris (с Sodium) или Canvas.",
     "source": "https://modrinth.com/mod/iris ; breaks в fabric.mod.json (iris-fabric-1.11.4+mc26.1.2.jar)"},
    {"a": {"ids": ["sodium"]}, "b": {"ids": ["vulkanmod"]}, "level": "error", "loaders": None,
     "text": "Sodium и VulkanMod вместе не работают: VulkanMod полностью заменяет рендер.",
     "fix": "Оставьте что-то одно.",
     "source": "https://modrinth.com/mod/sodium ; breaks в fabric.mod.json (sodium-fabric-0.9.2+mc26.1.2.jar)"},
    # Oculus при запуске показывает «Oculus failed to load! Oculus requires Rubidium ...» (assets/iris/lang/en_us.json,
    # ключ iris.sodium.failure.reason.notFound). Embeddium отвечает и за modId rubidium.
    {"a": {"ids": ["oculus"]}, "requires_any": [{"ids": ["embeddium", "rubidium"]}], "level": "error",
     "loaders": _FORGE_LIKE,
     "text": "Oculus не запустится без Embeddium или Rubidium — выдаст экран «Oculus failed to load».",
     "fix": "Добавьте Embeddium той же версии Minecraft.",
     "source": "https://modrinth.com/mod/oculus ; строка iris.sodium.failure.reason.notFound в assets/iris/lang/en_us.json (oculus-mc1.20.1-1.8.0.jar)"},
    # «Since Sodium 0.6.0, Indium is no longer required! ... Indium also isn't compatible with Sodium 0.6.0+».
    {"a": {"ids": ["indium"]}, "b": {"ids": ["sodium"], "version": ">=0.6.0-"}, "level": "error",
     "loaders": _FABRIC_LIKE,
     "text": "Indium не нужен и несовместим с Sodium 0.6 и новее: поддержка Fabric Rendering API уже встроена в Sodium.",
     "fix": "Удалите Indium.",
     "source": "https://modrinth.com/mod/indium"},
    # «Indium is no longer required, and will not work with Embeddium».
    {"a": {"ids": ["indium"]}, "b": {"ids": ["embeddium"]}, "level": "error", "loaders": None,
     "text": "Indium не работает с Embeddium: всё нужное уже встроено в Embeddium.",
     "fix": "Удалите Indium.",
     "source": "https://modrinth.com/mod/embeddium"},

    # ---------------- Свет и чанки ----------------
    # «Starlight cannot be installed with Phosphor, they are completely incompatible».
    {"a": {"ids": ["starlight"]}, "b": {"ids": ["phosphor"]}, "level": "error", "loaders": None,
     "text": "Starlight и Phosphor полностью несовместимы: оба переписывают движок освещения.",
     "fix": "Оставьте Starlight, Phosphor уберите.",
     "source": "https://modrinth.com/mod/starlight"},
    # Moonrise объявляет breaks: starlight, c2me, notenoughcrashes.
    {"a": {"ids": ["moonrise"]}, "b": {"ids": ["starlight"]}, "level": "error", "loaders": None,
     "text": "Moonrise уже содержит Starlight, отдельный Starlight с ним не загрузится.",
     "fix": "Удалите Starlight.",
     "source": "https://raw.githubusercontent.com/Tuinity/Moonrise/master/fabric/src/main/resources/fabric.mod.json"},
    {"a": {"ids": ["moonrise"]}, "b": {"ids": ["c2me"]}, "level": "error", "loaders": None,
     "text": "Moonrise несовместим с C2ME: Moonrise целиком заменяет систему чанков, на которой построен C2ME.",
     "fix": "Оставьте что-то одно.",
     "source": "https://modrinth.com/mod/moonrise-opt"},
    {"a": {"ids": ["moonrise"]}, "b": {"ids": ["notenoughcrashes"]}, "level": "error", "loaders": None,
     "text": "Moonrise несовместим с Not Enough Crashes — так указано в самом Moonrise.",
     "fix": "Уберите Not Enough Crashes.",
     "source": "https://raw.githubusercontent.com/Tuinity/Moonrise/master/fabric/src/main/resources/fabric.mod.json"},
    # ScalableLux — «a Fabric mod based on Starlight»: два движка света сразу.
    {"a": {"ids": ["scalablelux"], "jars": ["scalablelux"]}, "b": {"ids": ["starlight", "moonrise"]},
     "level": "warn", "loaders": None,
     "text": "ScalableLux сделан на основе Starlight, а Starlight (и Moonrise, где он встроен) тоже заменяет движок света.",
     "fix": "Оставьте один мод для освещения.",
     "source": "https://modrinth.com/mod/scalablelux"},

    # ---------------- Lithium и его форки ----------------
    # Radium — «Unofficial Fork of CaffeineMC's Lithium»; Canary — «hardfork» Lithium: одни и те же миксины.
    {"a": {"ids": ["lithium"]}, "b": {"ids": ["canary", "radium"]}, "level": "warn", "loaders": None,
     "text": "Canary и Radium — форки Lithium: они правят те же места игры, вдвоём с Lithium ставить их нельзя.",
     "fix": "Оставьте один: Lithium, если он есть для вашего загрузчика.",
     "source": "https://modrinth.com/mod/radium"},
    {"a": {"ids": ["canary"]}, "b": {"ids": ["radium"]}, "level": "warn", "loaders": None,
     "text": "Canary и Radium — два форка Lithium, вместе они дублируют друг друга.",
     "fix": "Оставьте один.",
     "source": "https://modrinth.com/mod/radium"},

    # ---------------- Динамический свет ----------------
    # LambDynamicLights объявляет breaks/incompatible: sodiumdynamiclights, ryoamiclights.
    {"a": {"ids": ["lambdynlights"]}, "b": {"ids": ["sodiumdynamiclights", "ryoamiclights"]}, "level": "error",
     "loaders": None,
     "text": "Два мода динамического освещения сразу: LambDynamicLights несовместим с Sodium Dynamic Lights и RyoamicLights.",
     "fix": "Оставьте один, лучше LambDynamicLights.",
     "source": "https://modrinth.com/mod/lambdynamiclights ; breaks в fabric.mod.json и incompatible в neoforge.mods.toml (lambdynamiclights-4.11.2+26.1.2.jar)"},

    # ---------------- Модели мобов ----------------
    # EMF объявляет breaks: cem = "*".
    {"a": {"ids": ["entity_model_features"]}, "b": {"ids": ["cem"]}, "level": "error", "loaders": None,
     "text": "Entity Model Features несовместим со старым модом CEM: оба грузят модели мобов из ресурспаков.",
     "fix": "Уберите CEM, EMF его полностью заменяет.",
     "source": "https://modrinth.com/mod/entity-model-features ; breaks в fabric.mod.json (entity_model_features-3.3.6-26.1-fabric.jar)"},

    # ---------------- Sinytra Connector ----------------
    # «Forgified Fabric API ... is not compatible with it [Fabric API]. Do not download the original Fabric API».
    {"a": {"ids": ["connector"]}, "b": {"ids": ["fabric-api"]}, "level": "error", "loaders": _FORGE_LIKE,
     "text": "С Sinytra Connector нельзя ставить обычный Fabric API: его заменяет Forgified Fabric API.",
     "fix": "Удалите Fabric API и поставьте Forgified Fabric API.",
     "source": "https://moddedmc.wiki/en/project/connector/latest/docs/introduction"},
    {"a": {"ids": ["connector"]}, "requires_any": [{"ids": ["fabric_api"]}], "level": "warn", "loaders": _FORGE_LIKE,
     "text": "Sinytra Connector почти всегда нужен вместе с Forgified Fabric API, а его в папке нет.",
     "fix": "Добавьте Forgified Fabric API той же версии Minecraft.",
     "source": "https://moddedmc.wiki/en/project/connector/latest/docs/introduction"},

    # ---------------- Дублирующие функции (не падает, но мешает) ----------------
    # Ok Zoomer и Zoomify по умолчанию вешают зум на одну клавишу C.
    {"a": {"ids": ["ok_zoomer", "okzoomer"]}, "b": {"ids": ["zoomify"]}, "level": "warn", "loaders": None,
     "text": "Ok Zoomer и Zoomify — два мода для зума, оба по умолчанию на клавише C.",
     "fix": "Оставьте один или переназначьте клавишу в настройках управления.",
     "source": "https://modrinth.com/mod/zoomify ; https://modrinth.com/mod/ok-zoomer"},
    {"group": [("Xaero's Minimap", {"ids": ["xaerominimap", "xaerominimapfair"]}),
               ("JourneyMap", {"ids": ["journeymap"]}),
               ("VoxelMap", {"ids": ["voxelmap"]}),
               ("FTB Chunks", {"ids": ["ftbchunks"]})],
     "min": 2, "level": "warn", "loaders": None,
     "text": "В сборке несколько миникарт: {names}. Они рисуют карту поверх друг друга и спорят за клавиши.",
     "fix": "Оставьте одну миникарту.",
     "source": "https://modrinth.com/mod/xaeros-minimap ; https://modrinth.com/mod/journeymap ; "
               "https://modrinth.com/mod/voxelmap-updated"},
    {"group": [("Inventory Profiles Next", {"ids": ["inventoryprofilesnext"]}),
               ("Mouse Wheelie", {"ids": ["mousewheelie"]}),
               ("Inventory Sorter", {"ids": ["inventorysorter"]})],
     "min": 2, "level": "warn", "loaders": None,
     "text": "Несколько модов сортировки инвентаря: {names}. Они реагируют на одни и те же клики и клавиши.",
     "fix": "Оставьте один мод сортировки.",
     "source": "https://modrinth.com/mod/inventory-profiles-next ; https://modrinth.com/mod/mouse-wheelie ; "
               "https://modrinth.com/mod/inventory-sorting"},

    # ---------------- Сведения ----------------
    # «The server will relay them unless the enforce-secure-profile option is set to true»; на 1.19.1–1.19.2
    # без подписи на таких серверах не зайти.
    {"a": {"ids": ["nochatreports"]}, "requires_any": [], "level": "info", "loaders": None,
     "text": "No Chat Reports: на серверах с enforce-secure-profile=true чат без подписи не пройдёт, "
             "а на 1.19.1–1.19.2 туда можно вообще не зайти.",
     "fix": "Если не пускает на сервер — включите подпись сообщений в настройках мода или уберите его.",
     "source": "https://modrinth.com/mod/no-chat-reports"},
]


# --------------------------------------------------------------------------------------
# Самопроверка на синтетических jar (только метаданные)
# --------------------------------------------------------------------------------------

def _fake_jar(folder, name, files):
    path = os.path.join(folder, name)
    with zipfile.ZipFile(path, "w") as z:
        for arc, content in files.items():
            if isinstance(content, (dict, list)):
                content = json.dumps(content)
            z.writestr(arc, content)
    return path


def _jar_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for arc, content in files.items():
            z.writestr(arc, json.dumps(content) if isinstance(content, (dict, list)) else content)
    return buf.getvalue()


def _fmj(mod_id, version="1.0.0", mc=None, **kw):
    d = {"schemaVersion": 1, "id": mod_id, "version": version, "name": kw.pop("name", mod_id)}
    deps = dict(kw.pop("depends", {}))
    if mc is not None:
        deps["minecraft"] = mc
    if deps:
        d["depends"] = deps
    d.update(kw)
    return {"fabric.mod.json": d}


def _toml(mod_id, version="1.0.0", mc="[1.20.1,1.21)", deps=(), neo=False, loader_dep=True, extra=""):
    lines = ['modLoader="javafml"', 'loaderVersion="[47,)"', "", "[[mods]]",
             f'modId="{mod_id}"', f'version="{version}"', f'displayName="{mod_id}"', extra, ""]
    all_deps = list(deps)
    if loader_dep:
        all_deps.append(("neoforge" if neo else "forge", "required", "[1,)"))
    if mc:
        all_deps.append(("minecraft", "required", mc))
    for did, typ, rng in all_deps:
        lines += [f"[[dependencies.{mod_id}]]", f'modId="{did}"']
        if neo or typ not in ("required", "optional"):
            lines.append(f'type="{typ}"')
        else:
            lines.append(f'mandatory={"true" if typ == "required" else "false"}')
        lines += [f'versionRange="{rng}"', 'ordering="NONE"', 'side="BOTH"', ""]
    return {("META-INF/neoforge.mods.toml" if neo else "META-INF/mods.toml"): "\n".join(lines)}


def _selftest(verbose=False):
    import tempfile

    # --- версии
    fm = fabric_match
    assert fm(">=1.20", "1.20.1", is_minecraft=True) is True
    assert fm("~1.20.1", "1.20.4", is_minecraft=True) is True
    assert fm("~1.20.1", "1.21", is_minecraft=True) is False
    assert fm("1.20.x", "1.20.6", is_minecraft=True) is True
    assert fm("1.20.x", "1.21", is_minecraft=True) is False
    assert fm("*", "26.1.2", is_minecraft=True) is True
    assert fm([">=1.19 <1.20", "1.20.1"], "1.20.1", is_minecraft=True) is True
    assert fm([">=1.19 <1.20", "1.20.2"], "1.20.1", is_minecraft=True) is False
    assert fm(">=1.20 <1.21", "1.21", is_minecraft=True) is False
    assert fm("~26.1-", "26.1.2", is_minecraft=True) is True
    assert fm("~26.1-", "26.2", is_minecraft=True) is False
    assert fm("~26.1", "26.1", is_minecraft=True) is True
    assert fm("26.1.x", "26.1.2", is_minecraft=True) is True
    assert fm(">=1.21", "26.1.2", is_minecraft=True) is True
    assert fm("<26", "26.1.2", is_minecraft=True) is False
    assert fm("1.20.1", "1.20.1", is_minecraft=True) is True
    assert fm("1.20", "1.20.0", is_minecraft=True) is True
    assert fm(">=1.21.2-alpha.24.33.a", "1.21.4", is_minecraft=True) is True
    assert fm(">=1.21-", "1.21-rc1", is_minecraft=True) is True
    assert fm("^0.9.0", "0.9.2+mc26.1.2") is True
    assert fm("0.9.x", "0.10.0") is False
    assert fm(">=1.2 || <1.0", "0.5") is True
    assert fm("~1.20", "24w14a", is_minecraft=True) is None
    mm = maven_match
    assert mm("[1.20,1.21)", "1.20.1") is True
    assert mm("[1.20,1.21)", "1.21") is False
    assert mm("[1.20.1]", "1.20.1") is True
    assert mm("[1.20.1]", "1.20.2") is False
    assert mm("(,1.19]", "1.20") is False
    assert mm("[1.19,1.20),[1.21,)", "26.1.2") is True
    assert mm("[26.1,26.1.2]", "26.1.2") is True
    assert mm("[26.1,26.1.2]", "26.2") is False
    assert mm("1.20.1", "1.12.2") is True  # «мягкое» требование Maven
    assert mm("[47,)", "47.3.0") is True and mm("[47.2,)", "47.1.3") is False
    assert compare_versions("1.20.1-2.1.0", "1.20.1-2.0.9") == 1
    assert compare_versions("1.0-SNAPSHOT", "1.0") == -1
    assert compare_versions("26.1.2", "1.21.11") == 1

    def has(probs, kind, *files, level=None):
        for p in probs:
            if p["kind"] == kind and all(f in p["mods"] for f in files) and (level is None or p["level"] == level):
                return p
        raise AssertionError(f"нет {kind} {files}: {[(p['kind'], p['mods'], p['text']) for p in probs]}")

    def none_of(probs, *bad):
        assert not [p for p in probs if p["kind"] in bad], [(p["kind"], p["text"]) for p in probs]

    def untouched(probs, *files):
        assert not [p for p in probs if set(p["mods"]) & set(files)], [(p["kind"], p["text"]) for p in probs]

    with tempfile.TemporaryDirectory() as root:
        def pack(name, jars):
            d = os.path.join(root, name)
            os.makedirs(d)
            for fname, files in jars.items():
                _fake_jar(d, fname, files)
            return d

        # 1. Не тот загрузчик
        d = pack("loader_fabric", {"forgemod.jar": _toml("forgemod"),
                                   "fabricmod.jar": _fmj("fabricmod", mc="~1.20"),
                                   "quiltonly.jar": {"quilt.mod.json": {"quilt_loader": {"id": "quiltonly",
                                                                                         "version": "1.0"}}}})
        pr = check_mods(d, "1.20.1", "fabric")
        has(pr, "wrong_loader", "forgemod.jar", level="error")
        has(pr, "wrong_loader", "quiltonly.jar")
        untouched(pr, "fabricmod.jar")
        pr = check_mods(d, "1.20.1", "quilt")
        untouched(pr, "fabricmod.jar", "quiltonly.jar")
        d = pack("loader_neo", {"forgemod.jar": _toml("forgemod", mc="[1.20,1.22)"),
                                "neomod.jar": _toml("neomod", mc="[1.21,1.22)", neo=True),
                                "fab.jar": _fmj("fab")})
        pr = check_mods(d, "1.21.1", "neoforge")
        has(pr, "wrong_loader", "forgemod.jar")
        has(pr, "wrong_loader", "fab.jar")
        untouched(pr, "neomod.jar")
        pr = check_mods(d, "1.20.1", "neoforge")
        assert not [p for p in pr if p["kind"] == "wrong_loader" and "forgemod.jar" in p["mods"]], pr
        pr = check_mods(d, "1.21.1", "forge")
        has(pr, "wrong_loader", "neomod.jar")
        # Sinytra Connector: мод Fabric на NeoForge допустим
        d = pack("connector", {"connector.jar": _toml("connector", mc="[1.21,1.22)", neo=True),
                               "ffapi.jar": _toml("fabric_api", mc="[1.21,1.22)", neo=True),
                               "fab.jar": _fmj("fab", depends={"fabric-api-base": "*", "fabricloader": ">=0.15"})})
        pr = check_mods(d, "1.21.1", "neoforge")
        none_of(pr, "wrong_loader", "missing_dep")
        has(pr, "via_connector", "fab.jar", level="info")

        # 2. Не та версия Minecraft (+ новая схема 26.x)
        d = pack("mc", {"old.jar": _fmj("old", mc="~1.20"), "ok.jar": _fmj("ok", mc="~26.1-"),
                        "range.jar": _fmj("range", mc=[">=1.19 <1.20", "26.1.x"]),
                        "future.jar": _fmj("future", mc=">=26.2")})
        pr = check_mods(d, "26.1.2", "fabric")
        has(pr, "wrong_mc", "old.jar")
        has(pr, "wrong_mc", "future.jar")
        untouched(pr, "ok.jar", "range.jar")
        d = pack("mc_forge", {"f119.jar": _toml("f119", mc="[1.19,1.20)"), "f120.jar": _toml("f120", mc="[1.20.1]"),
                              "legacy.jar": {"mcmod.info": [{"modid": "legacy", "name": "Legacy",
                                                             "mcversion": "1.12.2"}]}})
        pr = check_mods(d, "1.20.1", "forge")
        has(pr, "wrong_mc", "f119.jar")
        has(pr, "wrong_mc", "legacy.jar")
        untouched(pr, "f120.jar")
        pr = check_mods(d, "1.12.2", "forge")
        has(pr, "wrong_mc", "f120.jar")
        untouched(pr, "legacy.jar")

        # 3. Зависимости: обычная, модуль Fabric API через jar-in-jar, provides, версия, загрузчик
        api_inner = _jar_bytes(_fmj("fabric-api-base", "0.4.0"))
        lifecycle = _jar_bytes(_fmj("fabric-lifecycle-events-v1", "2.0.0"))
        d = pack("deps", {
            "needs_cloth.jar": _fmj("needs_cloth", depends={"cloth-config": ">=11"}),
            "needs_api.jar": _fmj("needs_api", depends={"fabric-api-base": "*", "fabric-lifecycle-events-v1": "*"}),
            "fabric-api.jar": {**_fmj("fabric-api", "0.92.0", jars=[{"file": "META-INF/jars/base.jar"},
                                                                    {"file": "META-INF/jars/life.jar"}]),
                               "META-INF/jars/base.jar": api_inner, "META-INF/jars/life.jar": lifecycle},
            "needs_indium.jar": _fmj("needs_indium", depends={"indium": "*"}),
            "sodium.jar": _fmj("sodium", "0.5.8", provides=["indium"]),
            "needs_new_sodium.jar": _fmj("needs_new_sodium", depends={"sodium": ">=0.6"}),
            "needs_loader.jar": _fmj("needs_loader", depends={"fabricloader": ">=0.16"}),
        })
        pr = check_mods(d, "1.20.1", "fabric", "0.15.11")
        p = has(pr, "missing_dep", "needs_cloth.jar", level="error")
        assert "Cloth Config" in p["text"], p
        has(pr, "dep_version", "needs_new_sodium.jar", "sodium.jar")
        has(pr, "loader_version", "needs_loader.jar")
        untouched(pr, "needs_api.jar", "needs_indium.jar")
        d = pack("deps_forge", {
            "a.jar": _toml("a", deps=[("curios", "required", "[5,)"), ("jei", "optional", "[15,)")]),
            "jei.jar": _toml("jei", "11.0.0"),
            "verjar.jar": {**_toml("verjar", "${file.jarVersion}"),
                           "META-INF/MANIFEST.MF": "Manifest-Version: 1.0\nImplementation-Version: 3.2.1\n"},
        })
        pr = check_mods(d, "1.20.1", "forge", "47.3.0")
        has(pr, "missing_dep", "a.jar")
        has(pr, "dep_version", "a.jar", "jei.jar")
        assert read_mod(os.path.join(d, "verjar.jar"))["version"] == "3.2.1"
        # Forge 1.12: зависимость из аннотации @Mod в байткоде
        cls = b"\xca\xfe\xba\xbe....Lnet/minecraftforge/fml/common/Mod;....required-after:mclib@[2.4,);after:jei"
        d = pack("deps_legacy", {"mappet.jar": {"mcmod.info": [{"modid": "mappet", "mcversion": "1.12.2"}],
                                                "a/Main.class": cls}})
        pr = check_mods(d, "1.12.2", "forge")
        has(pr, "missing_dep", "mappet.jar")
        assert "jei" not in " ".join(p["text"] for p in pr)

        # 4. Явные несовместимости
        d = pack("breaks", {"a.jar": _fmj("a", breaks={"b": "*"}), "b.jar": _fmj("b"),
                            "c.jar": _fmj("c", conflicts={"d": "<2"}), "d.jar": _fmj("d", "1.5"),
                            "e.jar": _fmj("e", breaks={"f": "<1"}), "f.jar": _fmj("f", "1.2")})
        pr = check_mods(d, "1.20.1", "fabric")
        has(pr, "incompatible", "a.jar", "b.jar", level="error")
        has(pr, "conflict", "c.jar", "d.jar", level="warn")
        untouched(pr, "f.jar")
        d = pack("breaks_neo", {"x.jar": _toml("x", mc=None, neo=True, deps=[("y", "incompatible", ""),
                                                                              ("z", "discouraged", "")]),
                                "y.jar": _toml("y", mc=None, neo=True), "z.jar": _toml("z", mc=None, neo=True)})
        pr = check_mods(d, "1.21.1", "neoforge")
        has(pr, "incompatible", "x.jar", "y.jar", level="error")
        has(pr, "conflict", "x.jar", "z.jar", level="warn")
        d = pack("breaks_quilt", {"q.jar": {"quilt.mod.json": {"quilt_loader": {
            "id": "q", "version": "1.0", "breaks": [{"id": "r", "versions": "*", "reason": "ломает рендер"}]}}},
            "r.jar": _fmj("r")})
        pr = check_mods(d, "1.20.1", "quilt")
        p = has(pr, "incompatible", "q.jar", "r.jar")
        assert "ломает рендер" in p["text"], p

        # 5. Дубликаты
        d = pack("dups", {"jei-1.jar": _fmj("jei", "15.2.0"), "jei-2.jar": _fmj("jei", "15.10.1"),
                          "same1.jar": _fmj("same", "1.0"), "same2.jar": _fmj("same", "1.0")})
        pr = check_mods(d, "1.20.1", "fabric")
        p = has(pr, "duplicate", "jei-1.jar", "jei-2.jar", level="error")
        assert "jei-2.jar" in p["fix"], p
        has(pr, "duplicate", "same1.jar", "same2.jar")

        # 6. Известные конфликты
        d = pack("kc_fabric", {"OptiFine_1.20.1_HD_U_I6.jar": {"net/optifine/Config.class": b"x"},
                               "optifabric.jar": _fmj("optifabric"), "sodium.jar": _fmj("sodium")})
        pr = check_mods(d, "1.20.1", "fabric")
        assert [p for p in pr if p["kind"] == "known_conflict" and "sodium.jar" in p["mods"]
                and set(p["mods"]) & {"OptiFine_1.20.1_HD_U_I6.jar", "optifabric.jar"}], pr
        d = pack("kc_forge", {"OptiFine_1.20.1_HD_U_I6.jar": {"net/optifine/Config.class": b"x"},
                              "embeddium.jar": _toml("embeddium"), "oculus.jar": _toml("oculus")})
        pr = check_mods(d, "1.20.1", "forge")
        has(pr, "known_conflict", "OptiFine_1.20.1_HD_U_I6.jar", "embeddium.jar", level="error")
        d = pack("kc_maps", {"xaero.jar": _fmj("xaerominimap"), "jm.jar": _fmj("journeymap")})
        has(check_mods(d, "1.20.1", "fabric"), "known_conflict", "xaero.jar", "jm.jar", level="warn")
        d = pack("kc_oculus", {"oculus.jar": _toml("oculus")})
        has(check_mods(d, "1.20.1", "forge"), "known_conflict", "oculus.jar")
        d = pack("kc_light", {"star.jar": _fmj("starlight"), "phos.jar": _fmj("phosphor")})
        has(check_mods(d, "1.19.2", "fabric"), "known_conflict", "star.jar", "phos.jar")
        d = pack("kc_lith", {"lithium.jar": _fmj("lithium"), "canary.jar": _fmj("canary")})
        has(check_mods(d, "1.20.1", "fabric"), "known_conflict", "lithium.jar", "canary.jar")
        d = pack("kc_indium", {"indium.jar": _fmj("indium", "1.0.30"), "sodium.jar": _fmj("sodium", "0.6.0+mc1.21")})
        has(check_mods(d, "1.21", "fabric"), "known_conflict", "indium.jar", "sodium.jar", level="error")
        d = pack("kc_indium_old", {"indium.jar": _fmj("indium", "1.0.30"), "sodium.jar": _fmj("sodium", "0.5.8")})
        untouched(check_mods(d, "1.20.1", "fabric"), "indium.jar")
        emb = _toml("embeddium")
        emb["META-INF/mods.toml"] += '\n[[mods]]\nmodId="rubidium"\nversion="0.7.1"\ndisplayName="Rubidium"\n'
        d = pack("kc_emb", {"embeddium.jar": emb, "rubidium.jar": _toml("rubidium"), "oculus.jar": _toml("oculus")})
        pr = check_mods(d, "1.20.1", "forge")
        has(pr, "duplicate", "embeddium.jar", "rubidium.jar")
        untouched([p for p in pr if p["kind"] == "known_conflict"], "oculus.jar")  # Embeddium считается за Rubidium
        assert len([p for p in pr if set(p["mods"]) == {"embeddium.jar", "rubidium.jar"}]) == 1, pr
        d = pack("kc_conn", {"connector.jar": _toml("connector", mc="[1.21,1.22)", neo=True),
                             "fabric-api.jar": _fmj("fabric-api", "0.100.0")})
        pr = check_mods(d, "1.21.1", "neoforge")
        has(pr, "known_conflict", "connector.jar", "fabric-api.jar", level="error")
        has(pr, "known_conflict", "connector.jar", level="warn")
        d = pack("kc_zoom", {"zoomify.jar": _fmj("zoomify"), "okz.jar": _fmj("ok_zoomer")})
        has(check_mods(d, "1.20.1", "fabric"), "known_conflict", "zoomify.jar", "okz.jar", level="warn")

        # Прочее: не мод, битый файл, OptiFine на Fabric без OptiFabric, контейнер jar-in-jar
        d = pack("misc", {"lib.jar": {"com/x/Lib.class": b"\xca\xfe\xba\xbe"},
                          "OptiFine_1.20.1_HD_U_I6.jar": {"net/optifine/Config.class": b"x"}})
        with open(os.path.join(d, "broken.jar"), "wb") as fh:
            fh.write(b"not a zip")
        pr = check_mods(d, "1.20.1", "fabric")
        has(pr, "not_a_mod", "lib.jar", level="info")
        has(pr, "bad_jar", "broken.jar", level="error")
        has(pr, "wrong_loader", "OptiFine_1.20.1_HD_U_I6.jar")
        inner = _jar_bytes(_toml("kfflang", "4.12.0"))
        d = pack("container", {"kff-all.jar": {"META-INF/jarjar/metadata.json": {"jars": [
            {"path": "META-INF/jarjar/kfflang.jar"}]}, "META-INF/jarjar/kfflang.jar": inner},
            "user.jar": _toml("user", deps=[("kfflang", "required", "[4,)")])})
        none_of(check_mods(d, "1.20.1", "forge"), "missing_dep", "not_a_mod", "wrong_loader")
        # Кривой mods.toml (дубликат ключа) читается запасным разборщиком
        bad = _toml("weird")
        bad["META-INF/mods.toml"] = bad["META-INF/mods.toml"].replace(
            'displayName="weird"', 'displayName="weird"\ndisplayName="twice"')
        d = pack("loose", {"weird.jar": bad})
        assert read_mod(os.path.join(d, "weird.jar"))["id"] == "weird"

        # 7. Сервер: клиентские моды
        d = pack("server", {"client.jar": _fmj("clientmod", environment="client"),
                            "both.jar": _fmj("bothmod", depends={"clientmod": "*"})})
        su = server_unsafe(d)
        has(su, "client_only", "client.jar", level="info")
        has(su, "server_dep_on_client", "both.jar", "client.jar")
    if verbose:
        print("mod_doctor: самопроверка пройдена")
    return True


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 4:
        for _pr in check_mods(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None):
            print(f"[{_pr['level']}] {_pr['text']}" + (f"\n    -> {_pr['fix']}" if _pr["fix"] else ""))
    else:
        _selftest(verbose=True)

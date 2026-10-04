# -*- coding: utf-8 -*-
"""Portalis: карты, сборки и серверы Minecraft.

Окно: двойной клик по «Portalis.exe» (или .bat, если установлен Python).
Командная строка (результат пишется в switcher_cli.log рядом с программой):
  --list                         список сборок
  --maps                         список карт
  --current                      активная сборка и версия в TLauncher
  --switch "<начало имени>"      включить сборку
  --play "<карта>" [--pack "<сборка>" | --nomods] [--fresh]
                                 установить карту и выбрать версию или сборку
  --servers                      добавить серверы из servers.json
"""
import os
import sys
import io
import json
import time
import shutil
import struct
import threading
import subprocess
import urllib.request
# Всё нужное грузим сразу: exe из PyInstaller подгружает модули из самого себя по ходу работы,
# и после самообновления (exe подменён новым) поздняя подгрузка читала бы уже чужой файл.
import zipfile
import hashlib
import random
import math
import ctypes
import ctypes.wintypes
import webbrowser
import concurrent.futures
import traceback
from PIL import Image as PILImage, ImageTk, ImageDraw  # значки Modrinth (webp) и превью скинов
try:
    import winreg
except ImportError:
    winreg = None
import urllib.parse
import urllib.error

# APP_DIR - где лежит сама программа. ROOT - где её данные: каталог, скачанные карты и моды, настройки.
# Если рядом с программой лежит библиотека (папка «Карты» или manifest.json) - работаем в ней, как раньше.
# Иначе программа - один файл (например, на рабочем столе), а данные в %LOCALAPPDATA%\Portalis.
APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))


def _pick_root():
    if os.environ.get("PORTALIS_DATA"):
        return os.environ["PORTALIS_DATA"]
    if any(os.path.exists(os.path.join(APP_DIR, n)) for n in ("manifest.json", "Карты", "Лаунчеры")):
        return APP_DIR
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Portalis")


ROOT = _pick_root()
SINGLE_FILE = os.path.abspath(ROOT) != os.path.abspath(APP_DIR)
os.makedirs(ROOT, exist_ok=True)
MC = os.path.join(os.environ["APPDATA"], ".minecraft")
MODS = os.path.join(MC, "mods")
SAVES = os.path.join(MC, "saves")
MARKER = os.path.join(MODS, ".active_pack.json")
TL_PROPS = os.path.join(os.environ["APPDATA"], ".tlauncher", "tlauncher-2.0.properties")
UNSORTED = os.path.join(ROOT, "_Несортированное")
MAPS_DIR = os.path.join(ROOT, "Карты")
SERVERS_JSON = os.path.join(ROOT, "servers.json")
SERVERS_DAT = os.path.join(MC, "servers.dat")
MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
NO_MODS = "Без модов"


# Вывод PowerShell по умолчанию в OEM-кодировке (cp866): кириллица в путях и названиях портилась.
PS_UTF8 = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"


def lp(path):
    """Путь с префиксом для длинных имён: в некоторых картах файлы лежат глубже 260 символов.
    Сетевой путь \\\\server\\share превращается в \\\\?\\UNC\\server\\share."""
    p = os.path.abspath(path)
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def copy_long(src, dst):
    n = 0
    for d, _dirs, files in os.walk(lp(src)):
        rel = os.path.relpath(d, lp(src))
        out_dir = os.path.join(lp(dst), rel) if rel != "." else lp(dst)
        os.makedirs(out_dir, exist_ok=True)
        for f in files:
            shutil.copy2(os.path.join(d, f), os.path.join(out_dir, f))
            n += 1
    return n


# ---------- servers.dat (формат NBT без сжатия) ----------

def _nbt_r(f, t):
    if t == 1: return struct.unpack(">b", f.read(1))[0]
    if t == 2: return struct.unpack(">h", f.read(2))[0]
    if t == 3: return struct.unpack(">i", f.read(4))[0]
    if t == 4: return struct.unpack(">q", f.read(8))[0]
    if t == 5: return struct.unpack(">f", f.read(4))[0]
    if t == 6: return struct.unpack(">d", f.read(8))[0]
    if t == 7:
        n = struct.unpack(">i", f.read(4))[0]
        return bytes(f.read(n))
    if t == 8:
        n = struct.unpack(">H", f.read(2))[0]
        return f.read(n).decode("utf-8", "surrogatepass")
    if t == 9:
        et = f.read(1)[0]
        n = struct.unpack(">i", f.read(4))[0]
        return ("list", et, [_nbt_r(f, et) for _ in range(n)])
    if t == 10:
        d = []
        while True:
            tt = f.read(1)[0]
            if tt == 0:
                return ("compound", d)
            n = struct.unpack(">H", f.read(2))[0]
            k = f.read(n).decode("utf-8", "surrogatepass")
            d.append((k, tt, _nbt_r(f, tt)))
    if t == 11:
        n = struct.unpack(">i", f.read(4))[0]
        return list(struct.unpack(">%di" % n, f.read(4 * n)))
    if t == 12:
        n = struct.unpack(">i", f.read(4))[0]
        return list(struct.unpack(">%dq" % n, f.read(8 * n)))
    raise ValueError("NBT tag %d" % t)


def _nbt_w(f, t, v):
    if t == 1: f.write(struct.pack(">b", v))
    elif t == 2: f.write(struct.pack(">h", v))
    elif t == 3: f.write(struct.pack(">i", v))
    elif t == 4: f.write(struct.pack(">q", v))
    elif t == 5: f.write(struct.pack(">f", v))
    elif t == 6: f.write(struct.pack(">d", v))
    elif t == 7:
        f.write(struct.pack(">i", len(v))); f.write(v)
    elif t == 8:
        b = v.encode("utf-8", "surrogatepass"); f.write(struct.pack(">H", len(b))); f.write(b)
    elif t == 9:
        _, et, items = v
        f.write(bytes([et if items else (et or 10)])); f.write(struct.pack(">i", len(items)))
        for it in items:
            _nbt_w(f, et, it)
    elif t == 10:
        for k, tt, vv in v[1]:
            f.write(bytes([tt])); b = k.encode("utf-8"); f.write(struct.pack(">H", len(b))); f.write(b)
            _nbt_w(f, tt, vv)
        f.write(b"\x00")
    elif t == 11:
        f.write(struct.pack(">i", len(v))); f.write(struct.pack(">%di" % len(v), *v))
    elif t == 12:
        f.write(struct.pack(">i", len(v))); f.write(struct.pack(">%dq" % len(v), *v))


def load_servers():
    try:
        with open(SERVERS_JSON, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return []


def merge_servers(log=print):
    """Добавляет серверы из servers.json в список «Сетевой игры». Существующие не трогает."""
    wanted = load_servers()
    if not wanted:
        return 0
    if os.path.isfile(SERVERS_DAT):
        with open(SERVERS_DAT, "rb") as fh:
            data = fh.read()
        f = io.BytesIO(data)
        f.read(1)
        n = struct.unpack(">H", f.read(2))[0]
        rname = f.read(n).decode()
        root = _nbt_r(f, 10)
    else:
        data, rname, root = b"", "", ("compound", [])
    lst = None
    for k, tt, v in root[1]:
        if k == "servers" and tt == 9:
            lst = v
    if lst is None:
        lst = ("list", 10, [])
        root[1].append(("servers", 9, lst))
    have = set()
    for s in lst[2]:
        for k, tt, v in s[1]:
            if k == "ip":
                have.add(v.lower().replace(":25565", ""))
    added = 0
    for s in wanted:
        ip = s["ip"].lower().replace(":25565", "")
        if ip in have:
            continue
        lst[2].append(("compound", [("ip", 8, s["ip"]), ("name", 8, s["name"]),
                                    ("acceptTextures", 1, 1), ("hideAddress", 1, 0)]))
        have.add(ip)
        added += 1
    if added:
        os.makedirs(MC, exist_ok=True)
        if data and not os.path.exists(SERVERS_DAT + ".switcher.bak"):
            with open(SERVERS_DAT + ".switcher.bak", "wb") as fh:
                fh.write(data)
        out = io.BytesIO()
        out.write(b"\x0a"); b = rname.encode(); out.write(struct.pack(">H", len(b))); out.write(b)
        _nbt_w(out, 10, root)
        with open(SERVERS_DAT, "wb") as fh:
            fh.write(out.getvalue())
    log("Серверов добавлено в список игры: %d" % added)
    return added


# ---------- процессы и TLauncher ----------

def _java_cmdlines():
    """Командные строки всех java-процессов. Нужны, чтобы отличить игру от самого TLauncher."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", PS_UTF8,
             "Get-CimInstance Win32_Process -Filter \"Name like 'java%'\" | ForEach-Object { $_.CommandLine }"],
            capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=20, creationflags=0x08000000).stdout
        return [l for l in out.splitlines() if l.strip()]
    except Exception:
        return []


def game_running():
    for c in _java_cmdlines():
        low = c.lower()
        if "tlauncher" in low and "--gamedir" not in low:
            continue
        if any(k in low for k in ("net.minecraft", "knotclient", "bootstraplauncher", "--gamedir", "--assetsdir")):
            return True
    return False


def tlauncher_running():
    for c in _java_cmdlines():
        low = c.lower()
        if "tlauncher" in low and "--gamedir" not in low:
            return True
    try:
        out = subprocess.run(["tasklist"], capture_output=True, text=True, encoding="utf-8", errors="ignore",
                             creationflags=0x08000000).stdout.lower()
        return "tlauncher.exe" in out
    except Exception:
        return False


def tlauncher_exe():
    for p in (os.path.join(MC, "TLauncher.exe"),
              os.path.join(os.environ.get("USERPROFILE", ""), "Desktop", "TLauncher.exe")):
        if os.path.isfile(p):
            return p
    return None


def open_tlauncher():
    p = tlauncher_exe()
    if p:
        os.startfile(p)
        return True
    return False


def get_tlauncher_version():
    try:
        with open(TL_PROPS, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if line.startswith("login.version.game="):
                    return line.split("=", 1)[1].strip().replace("\\", "")
    except Exception:
        pass
    return None


def set_tlauncher_version(version):
    """Выбирает версию в TLauncher. Срабатывает только при закрытом лаунчере, иначе он перезапишет выбор."""
    if not version or not os.path.isfile(TL_PROPS) or tlauncher_running():
        return False
    with open(TL_PROPS, encoding="utf-8", errors="ignore") as fh:
        lines = fh.read().splitlines()
    done = False
    for i, line in enumerate(lines):
        if line.startswith("login.version.game="):
            lines[i] = "login.version.game=" + version
            done = True
    if not done:
        lines.append("login.version.game=" + version)
    shutil.copy2(TL_PROPS, TL_PROPS + ".switcher.bak")
    with open(TL_PROPS, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return True


_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_NET = {"direct": False}


def urlopen(req, timeout, attempt=0):
    """Через системный прокси (VPN) или напрямую; при неудаче следующая попытка идёт другим путём,
    удачный путь запоминается. Через некоторые VPN GitHub отвечает 503 на скачивание из релизов."""
    direct = bool(urllib.request.getproxies()) and (_NET["direct"] != bool(attempt % 2))
    r = _DIRECT.open(req, timeout=timeout) if direct else urllib.request.urlopen(req, timeout=timeout)
    _NET["direct"] = direct
    return r


def _download(url, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MinecraftPacks/2.0"})
            return urlopen(req, 90, i).read()
        except Exception as e:
            last = e
            time.sleep(2)
    raise RuntimeError("не удалось скачать %s: %s" % (url, last))


def ensure_vanilla(version, log=print):
    """Ставит ванильную версию в .minecraft\\versions: описание и сам клиент с серверов Mojang.
    Библиотеки и звуки TLauncher докачает сам при первом запуске."""
    vdir = os.path.join(MC, "versions", version)
    vjson = os.path.join(vdir, version + ".json")
    vjar = os.path.join(vdir, version + ".jar")
    if os.path.isfile(vjson) and os.path.isfile(vjar):
        return True
    log("Скачиваю Minecraft %s с серверов Mojang..." % version)
    man = json.loads(_download(MANIFEST))
    entry = next((v for v in man["versions"] if v["id"] == version), None)
    if not entry:
        raise RuntimeError("версии %s нет в списке Mojang" % version)
    raw = _download(entry["url"])
    info = json.loads(raw)
    os.makedirs(vdir, exist_ok=True)
    if not os.path.isfile(vjar):
        jar = _download(info["downloads"]["client"]["url"])
        with open(vjar, "wb") as fh:
            fh.write(jar)
    with open(vjson, "wb") as fh:
        fh.write(raw)
    log("Minecraft %s установлен, остальное TLauncher докачает при запуске" % version)
    return True


# ---------- сборки ----------

def jars(d):
    if not os.path.isdir(d):
        return []
    return [f for f in os.listdir(d) if f.lower().endswith(".jar")]


def n_mods(n):
    """«1 мод», «3 мода», «21 мод», «129 модов»."""
    n = int(n)
    w = "мод" if n % 10 == 1 and n % 100 != 11 else ("мода" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "модов")
    return "%d %s" % (n, w)


def _listdir(d):
    try:
        return sorted(os.listdir(d))
    except OSError:
        return []


def find_packs():
    packs = []
    for vdir in _listdir(ROOT):
        vp = os.path.join(ROOT, vdir)
        if not os.path.isdir(vp) or vdir.startswith(("_", ".")) or vp == MAPS_DIR:
            continue
        for pdir in _listdir(vp):
            meta = os.path.join(vp, pdir, "pack.json")
            if not os.path.isfile(meta):
                continue
            try:
                with open(meta, encoding="utf-8") as fh:
                    m = json.load(fh)
            except Exception:
                continue
            if not isinstance(m, dict):
                continue
            m.setdefault("name", pdir)
            m.setdefault("loader", vdir.partition(" ")[0])
            m.setdefault("minecraft", vdir.partition(" ")[2])
            m["path"] = os.path.join(vp, pdir)
            m["version_dir"] = vdir
            m["count"] = len(jars(os.path.join(vp, pdir, "mods")))
            if not m["count"]:
                try:
                    with open(os.path.join(vp, pdir, "mods.json"), encoding="utf-8") as fh:
                        m["count"] = len(json.load(fh).get("mods", []))
                except Exception:
                    pass
            packs.append(m)
    return packs


def current():
    try:
        with open(MARKER, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def known_files(packs):
    known = set()
    for p in packs:
        d = os.path.join(p["path"], "mods")
        for f in jars(d):
            known.add((f, os.path.getsize(os.path.join(d, f))))
    return known


def missing_items(m=None, pack=None, packs=None):
    """Какие части (карта, сборка) ещё не скачаны: их id из описи."""
    man = load_local_manifest()
    ids = []
    if m is not None:
        it = manifest_item(man, map_item_id(m))
        if it and not item_downloaded(it):
            ids.append(it["id"])
        if not pack and m.get("requires_pack"):
            pack = next((p for p in (packs or find_packs()) if p["name"] == m.get("recommended")), None)
    if pack is not None:
        it = manifest_item(man, pack_item_id(pack))
        if it and not item_downloaded(it):
            ids.append(it["id"])
    return ids


def switch(pack, packs, copy_configs=True, log=print):
    if missing_items(pack=pack):
        raise RuntimeError("Сборка «%s» ещё не скачана." % pack["name"])
    os.makedirs(MODS, exist_ok=True)
    known = known_files(packs)

    # 1. Моды, которых нет ни в одной сборке, не удаляем, а уносим в «_Несортированное».
    stray = [f for f in jars(MODS) if (f, os.path.getsize(os.path.join(MODS, f))) not in known and f != CSL["name"]]
    if stray:
        dst = os.path.join(UNSORTED, time.strftime("%Y-%m-%d_%H-%M-%S"))
        os.makedirs(dst, exist_ok=True)
        for f in stray:
            shutil.move(os.path.join(MODS, f), os.path.join(dst, f))
        log("Неизвестных модов сохранено в «_Несортированное»: %d" % len(stray))

    # 2. Чистим mods и копируем моды сборки.
    for f in jars(MODS):
        os.remove(os.path.join(MODS, f))
    src = os.path.join(pack["path"], "mods")
    for f in jars(src):
        shutil.copy2(os.path.join(src, f), os.path.join(MODS, f))
    log("Скопировано модов: %d" % len(jars(src)))
    if pack.get("loader") in ("Fabric", "Forge", "NeoForge", "Quilt") and load_settings().get("offline_skins"):
        try:
            os.makedirs(os.path.dirname(MARKER), exist_ok=True)
            with open(MARKER, "w", encoding="utf-8") as fh:  # put_csl_in_mods смотрит на текущую сборку
                json.dump({"name": pack["name"], "version_dir": pack["version_dir"]}, fh, ensure_ascii=False)
            put_csl_in_mods(log)
        except Exception as e:
            log("Мод для своих скинов не добавлен: %s" % e)

    # 3. Конфиги поверх текущих. Недостающие паки ресурсов и шейдеров.
    if copy_configs:
        for sub in ("config", "defaultconfigs"):
            s = os.path.join(pack["path"], sub)
            if os.path.isdir(s):
                shutil.copytree(s, os.path.join(MC, sub), dirs_exist_ok=True)
                log("Конфиги обновлены: " + sub)
    for sub in ("resourcepacks", "shaderpacks"):
        s = os.path.join(pack["path"], sub)
        if not os.path.isdir(s):
            continue
        os.makedirs(os.path.join(MC, sub), exist_ok=True)
        added = 0
        for f in os.listdir(s):
            d = os.path.join(MC, sub, f)
            if os.path.isfile(os.path.join(s, f)) and not os.path.exists(d):
                shutil.copy2(os.path.join(s, f), d)
                added += 1
        if added:
            log("Добавлено в %s: %d" % (sub, added))

    # 3b. Профиль версии и миры из сборки: копируем только то, чего ещё нет.
    for sub in ("versions", "saves"):
        s_dir = os.path.join(pack["path"], sub)
        if not os.path.isdir(s_dir):
            continue
        os.makedirs(os.path.join(MC, sub), exist_ok=True)
        for item in os.listdir(s_dir):
            d = os.path.join(MC, sub, item)
            if os.path.isdir(os.path.join(s_dir, item)) and not os.path.exists(d):
                copy_long(os.path.join(s_dir, item), d)
                log("Добавлено в %s: %s" % (sub, item))

    # 3c. Серверы из servers.json.
    try:
        merge_servers(log)
    except Exception as e:
        log("Серверы не добавлены: %s" % e)

    # 4. Версия в «моём» лаунчере и отметка активной сборки.
    ok, hint = select_version(pack.get("tl_version", ""), pack["name"].rsplit(" (", 1)[0], log)
    if hint:
        log("Подсказка: " + hint)
    os.makedirs(MODS, exist_ok=True)
    with open(MARKER, "w", encoding="utf-8") as fh:
        json.dump({"name": pack["name"], "version_dir": pack["version_dir"],
                   "tl_version": pack.get("tl_version"),
                   "switched": time.strftime("%Y-%m-%d %H:%M:%S")}, fh, ensure_ascii=False, indent=1)
    return ok


def save_current_as(name, version_dir, tl_version, description=""):
    dst = os.path.join(ROOT, version_dir, name)
    os.makedirs(os.path.join(dst, "mods"), exist_ok=True)
    for f in jars(MODS):
        shutil.copy2(os.path.join(MODS, f), os.path.join(dst, "mods", f))
    loader, _, mcver = version_dir.partition(" ")
    with open(os.path.join(dst, "pack.json"), "w", encoding="utf-8") as fh:
        json.dump({"name": name, "loader": loader, "minecraft": mcver, "tl_version": tl_version,
                   "description": description, "is_pack": True}, fh, ensure_ascii=False, indent=1)
    return dst


LIBRARY_NOTE = ("Каталог карт и сборок ещё не загружен. Для первого запуска нужен интернет: Portalis сам скачает "
                "каталог с GitHub (около 3 МБ). Проверь подключение и нажми «Проверить обновления» внизу.")


def library_found():
    """Программа лежит в папке библиотеки: рядом есть карты или сборки."""
    return os.path.isdir(MAPS_DIR) or bool(find_packs())


# ---------- карты ----------

def find_maps():
    maps = []
    if not os.path.isdir(MAPS_DIR):
        return maps
    for d in _listdir(MAPS_DIR):
        meta = os.path.join(MAPS_DIR, d, "map.json")
        if not os.path.isfile(meta):
            continue
        try:
            with open(meta, encoding="utf-8") as fh:
                m = json.load(fh)
        except Exception:
            continue
        if not isinstance(m, dict) or not m.get("version"):
            continue  # без версии игры карту не поставить
        m.setdefault("id", d)
        m.setdefault("title", d)
        m.setdefault("save", m["title"])
        for k, v in (("genre", "Карта"), ("players", ""), ("desc", ""), ("recommended", None)):
            m.setdefault(k, v)
        m["path"] = os.path.join(MAPS_DIR, d)
        maps.append(m)
    def vkey(v):
        try:
            return tuple(-int(x) for x in v.split("."))
        except ValueError:
            return (0,)
    maps.sort(key=lambda m: (vkey(m["version"]), m["title"].lower()))
    return maps


def map_installed(m):
    return os.path.isdir(os.path.join(SAVES, m["save"]))


def packs_for_map(m, packs):
    """Сборки, с которыми можно играть на карте: той же версии игры, кроме «Чистой ваниллы»."""
    vers = m.get("compatible_versions") or [m["version"]]
    return [p for p in packs if p.get("minecraft") in vers and p.get("loader") != "Vanilla"]


def install_map(m, pack=None, fresh=False, packs=None, log=print):
    """Ставит мир в saves, включает сборку или чистую версию и выбирает её в TLauncher."""
    if missing_items(m, pack, packs):
        raise RuntimeError("Карта «%s» или её сборка ещё не скачана." % m["title"])
    os.makedirs(SAVES, exist_ok=True)
    dst = os.path.join(SAVES, m["save"])
    if os.path.isdir(dst) and fresh:
        backup = "%s (старое сохранение %s)" % (m["save"], time.strftime("%Y-%m-%d %H-%M-%S"))
        os.rename(dst, os.path.join(SAVES, backup))
        log("Старое сохранение переименовано в «%s»" % backup)
    if not os.path.isdir(dst):
        src = os.path.join(m["path"], "world")
        if not os.path.isfile(os.path.join(src, "level.dat")):
            raise RuntimeError("В папке карты нет мира (world\\level.dat). Скачай карту заново.")
        tmp = os.path.join(SAVES, ".portalis-tmp-" + m["save"])
        if os.path.isdir(lp(tmp)):
            shutil.rmtree(lp(tmp), ignore_errors=True)
        n = copy_long(src, tmp)
        os.replace(lp(tmp), lp(dst))  # мир появляется в игре только целиком
        log("Карта «%s» установлена, файлов: %d" % (m["title"], n))
    else:
        log("Карта «%s» уже установлена, продолжаешь своё сохранение" % m["title"])
    if m.get("usercache") and os.path.isfile(os.path.join(m["path"], m["usercache"])):
        n = merge_usercache(os.path.join(m["path"], m["usercache"]))
        log("Добавлено записей в usercache.json: %d (для голов персонажей)" % n)
    if not pack and m.get("requires_pack"):
        packs = packs or find_packs()
        pack = next((p for p in packs if p["name"] == m.get("recommended")), None)
        if not pack:
            raise RuntimeError("Для карты нужна сборка «%s», а её нет в папке." % m.get("recommended"))
    if pack:
        return switch(pack, packs or find_packs(), True, log)
    ensure_vanilla(m["version"], log)
    ok, hint = select_version(m["version"], m["title"], log)
    if hint:
        log("Подсказка: " + hint)
    return ok


# ---------- игра с другом через Hamachi / Radmin VPN ----------

import base64
import re
import socket

VPN_KINDS = (("Hamachi", "hamachi"), ("Radmin VPN", "radmin"), ("ZeroTier", "zerotier"))
LAN_KIND = "Одна Wi-Fi сеть"
NET_KINDS = ["Hamachi", "Radmin VPN", "ZeroTier", LAN_KIND]
FRIEND_SERVER = "Игра друга (Hamachi)"


def vpn_addresses():
    """Адреса виртуальных сетей: {'Hamachi': '25.x.x.x', 'Radmin VPN': '26.x.x.x'}."""
    found = {}
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", PS_UTF8,
             "Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object { $_.InterfaceAlias + '|' + $_.IPAddress }"],
            capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=20, creationflags=0x08000000).stdout
    except Exception:
        return found
    for line in out.splitlines():
        alias, _, ip = line.partition("|")
        ip = ip.strip()
        if not ip or ip.startswith("169.254."):
            continue
        for title, key in VPN_KINDS:
            if key in alias.lower():
                found[title] = ip
    return found


def vpn_gui(title):
    if title == "Hamachi":
        cands = []
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", PS_UTF8,
                                  "(Get-CimInstance Win32_Service -Filter \"Name='Hamachi2Svc'\").PathName"],
                                 capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=20,
                                 creationflags=0x08000000).stdout.strip().strip('"')
            exe = out.split(" -")[0].strip('"')
            if exe:
                d = os.path.dirname(exe)
                cands += [os.path.join(d, "hamachi-2-ui.exe"), os.path.join(os.path.dirname(d), "hamachi-2-ui.exe")]
        except Exception:
            pass
        cands += [r"C:\Program Files (x86)\LogMeIn Hamachi\hamachi-2-ui.exe",
                  r"C:\Program Files\LogMeIn Hamachi\hamachi-2-ui.exe"]
    elif title == "Radmin VPN":
        cands = [r"C:\Program Files (x86)\Radmin VPN\RvRvpnGui.exe", r"C:\Program Files\Radmin VPN\RvRvpnGui.exe"]
    elif title == "ZeroTier":
        cands = [r"C:\Program Files (x86)\ZeroTier\One\zerotier_desktop_ui.exe",
                 r"C:\Program Files\ZeroTier\One\zerotier_desktop_ui.exe"]
    else:
        cands = []
    return next((c for c in cands if os.path.isfile(c)), None)


def open_vpn(title):
    p = vpn_gui(title)
    if p:
        os.startfile(p)
        return True
    return False


def lan_port():
    """Порт, на котором открыт мир «Для сети». Игра пишет его в logs\\latest.log."""
    try:
        with open(os.path.join(MC, "logs", "latest.log"), encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except Exception:
        return None
    ports = re.findall(r"Started serving on (\d{2,5})", text)
    if not ports:
        ports = re.findall(r"\[CHAT\].*?(?:port|порт\w*)\D{0,6}(\d{4,5})", text, re.I)
    return int(ports[-1]) if ports else None


def _vi(n):
    out = b""
    n &= 0xFFFFFFFF
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def ping_server(host, port, timeout=6):
    """Запрос статуса, как в списке серверов игры. Возвращает версию и онлайн или бросает ошибку."""
    t0 = time.time()
    s = socket.create_connection((host, port), timeout=timeout)
    s.settimeout(timeout)
    hs = _vi(0) + _vi(767) + _vi(len(host)) + host.encode() + struct.pack(">H", port) + _vi(1)
    s.sendall(_vi(len(hs)) + hs + _vi(1) + b"\x00")

    def rvi():
        n = sh = 0
        while True:
            c = s.recv(1)
            if not c:
                raise EOFError("сервер закрыл соединение")
            b = c[0]
            n |= (b & 0x7F) << sh
            sh += 7
            if not b & 0x80:
                return n
    length = rvi()
    data = b""
    while len(data) < length:
        chunk = s.recv(length - len(data))
        if not chunk:
            break
        data += chunk
    s.close()
    i = 0
    while data[i] & 0x80:
        i += 1
    i += 1
    n = sh = 0
    while True:
        b = data[i]; i += 1
        n |= (b & 0x7F) << sh; sh += 7
        if not b & 0x80:
            break
    j = json.loads(data[i:i + n].decode("utf-8", "ignore"))
    motd = re.sub(r"\u00a7.", "", _motd_text(j.get("description", "")))
    return {"version": re.sub(r"\u00a7.", "", j.get("version", {}).get("name", "?")),
            "online": j.get("players", {}).get("online", 0),
            "max": j.get("players", {}).get("max", 0),
            "motd": " ".join(motd.split())[:200],
            "ms": int((time.time() - t0) * 1000)}


NET_SETUP = {
    "Hamachi": {
        "host": "В Hamachi нажми кнопку включения, потом «Сеть» -> «Создать сеть». Придумай имя и пароль, отправь их другу.",
        "guest": "В Hamachi нажми кнопку включения, потом «Сеть» -> «Подключиться к существующей сети» и введи имя и пароль от друга.",
        "setup": [
            "Отключи шифрование в Hamachi. «Система» -> «Параметры...» -> слева «Параметры» -> блок «Соединения с узлами». "
            "В строке «Шифрование» выбери «Отключено», в строке «Сжатие» тоже «Отключено». Нажми «ОК». "
            "Без этого игра часто не подключается или сильно лагает.",
            "Брандмауэр. Сеть Hamachi Windows считает общественной и режет входящие подключения. "
            "Либо выключи брандмауэр для общественной сети на время игры (кнопка «Открыть брандмауэр» -> "
            "«Включение и отключение брандмауэра Защитника Windows» -> в блоке «Параметры для общественной сети» "
            "выбери «Отключить»), либо разреши Java: кнопка «Разрешить Java» -> «Изменить параметры» -> найди "
            "Java(TM) Platform SE binary или OpenJDK Platform binary и поставь обе галочки, «Частная» и «Публичная».",
            "Если стоит антивирус со своим сетевым экраном (Kaspersky, ESET, Avast), разреши в нём сеть Hamachi "
            "или отключи экран на время игры.",
            "После игры включи брандмауэр обратно."],
    },
    "Radmin VPN": {
        "host": "В Radmin VPN: «Сеть» -> «Создать сеть». Придумай имя и пароль, отправь их другу.",
        "guest": "В Radmin VPN: «Сеть» -> «Присоединиться к сети» и введи имя и пароль от друга.",
        "setup": [
            "Шифрование в Radmin VPN трогать не нужно, оно не мешает игре.",
            "Брандмауэр. Сеть Radmin VPN Windows тоже считает общественной. Либо выключи брандмауэр для общественной "
            "сети на время игры (кнопка «Открыть брандмауэр» -> «Включение и отключение брандмауэра Защитника Windows»), "
            "либо разреши Java для частных и публичных сетей (кнопка «Разрешить Java»).",
            "Если стоит антивирус со своим сетевым экраном, разреши в нём сеть Radmin VPN.",
            "После игры включи брандмауэр обратно."],
    },
    "ZeroTier": {
        "host": "На my.zerotier.com создай сеть и отправь другу её ID из 16 символов. В ZeroTier: «Join New Network» -> этот ID.",
        "guest": "В ZeroTier: «Join New Network» и введи ID сети от друга. Друг должен отметить тебя галочкой Auth на my.zerotier.com.",
        "setup": [
            "ZeroTier ставится с zerotier.com. Шифрование в нём настраивать не нужно.",
            "На my.zerotier.com в списке Members отметь галочку Auth у обоих компьютеров, иначе адреса не появятся.",
            "Брандмауэр: выключи его для общественной сети на время игры или разреши Java для частных и публичных "
            "сетей (кнопки ниже).",
            "После игры включи брандмауэр обратно."],
    },
    LAN_KIND: {
        "host": "Оба компьютера должны быть подключены к одному роутеру, по Wi-Fi или кабелем. Hamachi не нужен.",
        "guest": "Подключись к тому же роутеру, что и друг. Игра друга часто сама появляется внизу «Сетевой игры», "
                 "но по коду приглашения надёжнее.",
        "setup": [
            "Никакие программы не нужны.",
            "Домашняя сеть в Windows должна быть «Частной». Разреши Java для частных сетей (кнопка «Разрешить Java»).",
            "Если друг не видит игру, на время игры выключи брандмауэр для частной сети."],
    },
}

_SKIP_ALIASES = ("hamachi", "radmin", "zerotier", "vethernet", "xray", "tap", "tun", "vpn", "wsl")


def lan_ip():
    """Адрес компьютера в домашней сети (у роутера), без VPN-адаптеров."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", PS_UTF8,
             "Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } | "
             "ForEach-Object { $_.InterfaceAlias + '|' + $_.IPv4Address.IPAddress }"],
            capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=25, creationflags=0x08000000).stdout
    except Exception:
        return None
    for line in out.splitlines():
        alias, _, ip = line.partition("|")
        if any(k in alias.lower() for k in _SKIP_ALIASES):
            continue
        ip = ip.strip().split()[0] if ip.strip() else ""
        if re.match(r"^(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)", ip):
            return ip
    return None


def net_address(kind):
    if kind == LAN_KIND:
        return lan_ip()
    return vpn_addresses().get(kind)


def network_checks(kind):
    """Что мешает игре с другом: сеть, её тип в Windows, брандмауэр, разрешение для Java. Только чтение."""
    res = {"kind": kind, "address": net_address(kind), "profiles": {}, "category": None, "java": []}
    ps_cmd = (
        "Get-NetFirewallProfile | ForEach-Object { 'P|' + $_.Name + '|' + $_.Enabled }; "
        "Get-NetConnectionProfile | ForEach-Object { 'C|' + $_.InterfaceAlias + '|' + $_.NetworkCategory }; "
        "Get-NetFirewallRule -Direction Inbound -Action Allow -Enabled True -ErrorAction SilentlyContinue | "
        "Where-Object { $_.DisplayName -match '^(java|javaw)$|Java\\(TM\\)|OpenJDK|Minecraft' } | "
        "ForEach-Object { 'R|' + $_.Profile + '|' + $_.DisplayName }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", PS_UTF8, ps_cmd], capture_output=True, text=True, encoding="utf-8",
                             errors="ignore", timeout=120, creationflags=0x08000000).stdout
    except Exception as e:
        res["error"] = str(e)
        return res
    key = {"Hamachi": "hamachi", "Radmin VPN": "radmin", "ZeroTier": "zerotier"}.get(kind)
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) < 3:
            continue
        if parts[0] == "P":
            res["profiles"][parts[1]] = parts[2].strip() == "True"
        elif parts[0] == "C":
            alias = parts[1].lower()
            if (key and key in alias) or (not key and not any(k in alias for k in _SKIP_ALIASES)):
                res["category"] = parts[2].strip()
        elif parts[0] == "R":
            res["java"].append(parts[1].strip())
    return res


def describe_checks(r):
    """Итог проверки простыми словами: список (текст, True — хорошо, False — плохо, None — проверь сам)."""
    if r.get("error"):
        return [("Не удалось прочитать настройки Windows: %s" % r["error"], False)]
    lines = [("%s: %s" % (r["kind"], ("адрес " + r["address"]) if r["address"] else "не включён или не установлен"),
              bool(r["address"]))]
    cat = r.get("category")
    if cat:
        ru = {"Public": "общественная", "Private": "частная", "DomainAuthenticated": "доменная"}.get(cat, cat)
        lines.append(("Windows считает эту сеть: %s" % ru, None if cat == "Public" else True))
    need = "Private" if (cat and cat != "Public") or (not cat and r["kind"] == LAN_KIND) else "Public"
    ru_need = "общественной" if need == "Public" else "частной"
    fw_on = r["profiles"].get(need)
    java_ok = any(("Any" in p or need in p) for p in r["java"])
    if fw_on is False:
        lines.append(("Брандмауэр для %s сети выключен: друг сможет подключиться" % ru_need, True))
    elif java_ok:
        lines.append(("Брандмауэр для %s сети включён, но Java в нём разрешена" % ru_need, True))
    else:
        lines.append(("Брандмауэр для %s сети включён, а Java в нём не разрешена: друг не подключится" % ru_need, False))
    if r["kind"] == "Hamachi":
        lines.append(("Шифрование Hamachi программа прочитать не может, проверь сам: «Система» -> «Параметры...» -> "
                      "«Параметры» -> «Шифрование: Отключено»", None))
    return lines


def open_firewall():
    subprocess.Popen(["control.exe", "firewall.cpl"], creationflags=0x08000000)


def open_firewall_apps():
    subprocess.Popen(["control.exe", "/name", "Microsoft.WindowsFirewall", "/page", "pageConfigureApps"],
                     creationflags=0x08000000)


def _motd_text(d):
    if isinstance(d, str):
        return d
    if isinstance(d, dict):
        return (d.get("text", "") or "") + "".join(_motd_text(x) for x in d.get("extra", []))
    if isinstance(d, list):
        return "".join(_motd_text(x) for x in d)
    return ""


def resolve_srv(host):
    """Адрес из DNS-записи _minecraft._tcp, если сервер сидит не на стандартном порту."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", PS_UTF8,
                              "Resolve-DnsName -Type SRV _minecraft._tcp.%s -ErrorAction Stop | "
                              "Where-Object { $_.Type -eq 'SRV' } | ForEach-Object { $_.NameTarget + '|' + $_.Port }" % host],
                             capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=20,
                             creationflags=0x08000000).stdout.strip().splitlines()
        if out:
            t, _, p = out[0].partition("|")
            return t.strip().rstrip("."), int(p)
    except Exception:
        pass
    return None


def server_status(address):
    """Онлайн, версия, сообщение сервера и пинг, как в списке серверов игры."""
    host, _, port = address.partition(":")
    port = int(port) if port else 25565
    try:
        return ping_server(host, port, 7)
    except Exception:
        srv = resolve_srv(host) if not _ else None
        if srv:
            return ping_server(srv[0], srv[1], 7)
        raise


def add_server_entry(name, ip):
    """Добавляет один сервер в конец списка «Сетевой игры», если его там нет."""
    data, rname, root = _read_servers_dat()
    lst = next((v for k, tt, v in root[1] if k == "servers" and tt == 9), None)
    if lst is None:
        lst = ("list", 10, [])
        root[1].append(("servers", 9, lst))
    if any(any(k == "ip" and v.lower().replace(":25565", "") == ip.lower() for k, _t, v in e[1]) for e in lst[2]):
        return False
    lst[2].append(("compound", [("ip", 8, ip), ("name", 8, name), ("acceptTextures", 1, 1), ("hideAddress", 1, 0)]))
    out = io.BytesIO(); out.write(b"\x0a"); b = rname.encode(); out.write(struct.pack(">H", len(b))); out.write(b)
    _nbt_w(out, 10, root)
    os.makedirs(MC, exist_ok=True)
    with open(SERVERS_DAT, "wb") as fh:
        fh.write(out.getvalue())
    return True


def host_setup(kind=None):
    """Собирает то, что нужно другу: адрес в выбранной сети, порт, версию и сборку."""
    if kind is None:
        vpn = vpn_addresses()
        kind = "Hamachi" if "Hamachi" in vpn else (next(iter(vpn)) if vpn else LAN_KIND)
    ip = net_address(kind)
    if not ip:
        if kind == LAN_KIND:
            raise RuntimeError("Не нашёл подключение к роутеру. Проверь Wi-Fi или кабель.")
        raise RuntimeError("%s не включён. Открой его, нажми кнопку включения и зайди в сеть." % kind)
    port = lan_port()
    if not port:
        raise RuntimeError("Мир ещё не открыт для сети. В игре: Esc -> «Открыть для сети» -> «Начать».")
    try:
        info = ping_server(ip, port)
    except Exception as e:
        raise RuntimeError("Нашёл порт %d, но игра на нём не отвечает (%s). Мир точно открыт для сети? "
                           "Проверь брандмауэр кнопкой «Проверить настройки»." % (port, e))
    tl = get_tlauncher_version()
    cur = current() or {}
    pack = cur.get("name") if cur.get("tl_version") == tl else None
    code = "MC1-" + base64.urlsafe_b64encode(json.dumps(
        {"a": "%s:%d" % (ip, port), "t": tl, "p": pack}, ensure_ascii=False).encode()).decode().rstrip("=")
    return {"vpn": kind, "address": "%s:%d" % (ip, port), "code": code, "tl": tl, "pack": pack, "info": info}


def parse_invite(text):
    text = text.strip()
    if text.startswith("MC1-"):
        raw = text[4:]
        raw += "=" * (-len(raw) % 4)
        try:
            j = json.loads(base64.urlsafe_b64decode(raw).decode())
            return j["a"], j.get("t"), j.get("p")
        except Exception:
            raise ValueError("Код приглашения повреждён. Попроси друга скопировать его ещё раз целиком.")
    m = re.match(r"^\s*([0-9.]+|[\w.-]+):(\d{2,5})\s*$", text)
    if m:
        return "%s:%s" % (m.group(1), m.group(2)), None, None
    raise ValueError("Не понял код. Вставь код приглашения от друга (начинается с MC1-) или адрес вида 25.1.2.3:51234.")


def _read_servers_dat():
    if not os.path.isfile(SERVERS_DAT):
        return b"", "", ("compound", [])
    with open(SERVERS_DAT, "rb") as fh:
        data = fh.read()
    f = io.BytesIO(data)
    f.read(1)
    n = struct.unpack(">H", f.read(2))[0]
    return data, f.read(n).decode(), _nbt_r(f, 10)


def set_friend_server(address, name=FRIEND_SERVER):
    """Ставит игру друга первой строкой в «Сетевой игре», старую запись с тем же именем заменяет."""
    data, rname, root = _read_servers_dat()
    lst = None
    for k, tt, v in root[1]:
        if k == "servers" and tt == 9:
            lst = v
    if lst is None:
        lst = ("list", 10, [])
        root[1].append(("servers", 9, lst))
    keep = [s for s in lst[2] if not any(k == "name" and v == name for k, _t, v in s[1])]
    entry = ("compound", [("ip", 8, address), ("name", 8, name), ("acceptTextures", 1, 1), ("hideAddress", 1, 0)])
    lst[2][:] = [entry] + keep
    os.makedirs(MC, exist_ok=True)
    if data and not os.path.exists(SERVERS_DAT + ".switcher.bak"):
        with open(SERVERS_DAT + ".switcher.bak", "wb") as fh:
            fh.write(data)
    out = io.BytesIO()
    out.write(b"\x0a"); b = rname.encode(); out.write(struct.pack(">H", len(b))); out.write(b)
    _nbt_w(out, 10, root)
    with open(SERVERS_DAT, "wb") as fh:
        fh.write(out.getvalue())


def join_friend(text, packs=None, log=print):
    address, tl, pack_name = parse_invite(text)
    packs = packs if packs is not None else find_packs()
    ok = True
    if pack_name:
        pack = next((p for p in packs if p["name"] == pack_name), None)
        if not pack:
            raise RuntimeError("У друга сборка «%s», а у тебя её нет. Обнови Portalis: кнопка «Проверить обновления»." % pack_name)
        cur = current() or {}
        if cur.get("name") != pack_name or get_tlauncher_version() != pack.get("tl_version"):
            ok = switch(pack, packs, True, log)
        else:
            log("Сборка «%s» уже включена" % pack_name)
    elif tl:
        if not tl.lower().startswith(("fabric", "forge")):
            ensure_vanilla(tl, log)
        ok, hint = select_version(tl, "игра друга", log)
        if hint:
            log("Подсказка: " + hint)
    set_friend_server(address)
    log("В «Сетевой игре» первой строкой добавлено: %s" % FRIEND_SERVER)
    host, _, port = address.rpartition(":")
    vpn = vpn_addresses()
    if not vpn:
        log("Внимание: не нашёл подключённую VPN-сеть (Hamachi, Radmin VPN или ZeroTier). Включи её и войди в сеть друга.")
    try:
        info = ping_server(host, int(port))
        log("Игра друга отвечает: версия %s, игроков %s из %s" % (info["version"], info["online"], info["max"]))
    except Exception as e:
        log("Игра друга пока не отвечает (%s). Проверь, что вы в одной сети и мир открыт для сети." % e)
    return ok


# ---------- лаунчеры, настройки, резервные копии ----------

SETTINGS = os.path.join(ROOT, "_settings.json")
LAUNCHERS_DIR = os.path.join(ROOT, "Лаунчеры")
_START_APPS = {"list": None}


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_settings(data):
    try:
        with open(SETTINGS, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
    except Exception:
        pass


def load_launchers():
    try:
        with open(os.path.join(LAUNCHERS_DIR, "launchers.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return []


def start_apps(refresh=False):
    """Программы из меню «Пуск»: (название, ярлык или AppID)."""
    if _START_APPS["list"] is not None and not refresh:
        return _START_APPS["list"]
    out = []
    try:
        raw = subprocess.run(["powershell", "-NoProfile", "-Command", PS_UTF8,
                              "Get-StartApps | ForEach-Object { $_.Name + '|' + $_.AppID }"],
                             capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=60,
                             creationflags=0x08000000).stdout
        for line in raw.splitlines():
            name, _, app = line.partition("|")
            if name.strip():
                out.append((name.strip(), app.strip()))
    except Exception:
        pass
    _START_APPS["list"] = out
    return out


LAUNCHER_PATHS = {
    # Где лаунчеры лежат после обычной установки (%VAR% раскрываются).
    "tlauncher": [r"%APPDATA%\.minecraft\TLauncher.exe", r"%USERPROFILE%\Desktop\TLauncher.exe"],
    "legacy": [r"%APPDATA%\.tlauncher\legacy\Minecraft\LL.exe"],
    "sklauncher": [r"%APPDATA%\sklauncher\SKlauncher.exe"],
    "official": [r"%ProgramFiles(x86)%\Minecraft Launcher\MinecraftLauncher.exe",
                 r"%ProgramFiles%\Minecraft Launcher\MinecraftLauncher.exe"],
    "prism": [r"%LOCALAPPDATA%\Programs\PrismLauncher\prismlauncher.exe"],
    "modrinth": [r"%LOCALAPPDATA%\Modrinth App\Modrinth App.exe", r"%LOCALAPPDATA%\Programs\Modrinth App\Modrinth App.exe"],
}
LEGACY_PROPS = [os.path.join(os.environ.get("APPDATA", ""), ".tlauncher", "legacy", "Minecraft", "tl.properties"),
                os.path.join(os.environ.get("APPDATA", ""), ".tlauncher", "legacy.properties")]
DEFAULT_MC = os.path.join(os.environ.get("APPDATA", ""), ".minecraft")
MRPACK_HOSTS = ("cdn.modrinth.com", "github.com", "raw.githubusercontent.com", "gitlab.com")


def launcher_info(lid=None):
    lid = lid or chosen_launcher()
    return next((x for x in load_launchers() if x["id"] == lid), {"id": lid, "name": lid, "mode": "tl"})


def launcher_mode(lid=None):
    """tl - TLauncher, legacy - Legacy Launcher, profiles - официальный и SKLauncher, mrpack - Prism и Modrinth App."""
    return launcher_info(lid).get("mode", "tl")


def _props_read(path):
    out = {}
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line or line.startswith(("#", "!")) or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                out[k.strip()] = v.replace("\\:", ":").replace("\\=", "=").replace("\\\\", "\\").strip()
    except OSError:
        pass
    return out


def _props_set(path, key, value):
    """Меняет один ключ в файле настроек Java (.properties), остальное не трогает. Резервная копия рядом."""
    esc = value.replace("\\", "\\\\").replace(":", "\\:").replace("=", "\\=")
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            lines = fh.read().splitlines()
    except OSError:
        lines = []
    done = False
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key:
            lines[i] = "%s=%s" % (key, esc)
            done = True
    if not done:
        lines.append("%s=%s" % (key, esc))
    if os.path.isfile(path) and not os.path.exists(path + ".switcher.bak"):
        shutil.copy2(path, path + ".switcher.bak")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def legacy_props():
    return next((p for p in LEGACY_PROPS if os.path.isfile(p)), None)


def launcher_game_dir(lid=None):
    """Папка игры выбранного лаунчера: своя (из настроек), у Legacy Launcher - из его tl.properties."""
    s = load_settings()
    if s.get("game_dir"):
        return s["game_dir"]
    if (lid or chosen_launcher()) == "legacy":
        p = legacy_props()
        if p:
            d = _props_read(p).get("minecraft.gamedir")
            if d and os.path.isabs(d):
                return d
    return DEFAULT_MC


def set_game_dir(path=None):
    """Переключает все пути программы на папку игры path (по умолчанию - папку выбранного лаунчера)."""
    global MC, MODS, SAVES, MARKER, SERVERS_DAT
    MC = path or launcher_game_dir()
    MODS = os.path.join(MC, "mods")
    SAVES = os.path.join(MC, "saves")
    MARKER = os.path.join(MODS, ".active_pack.json")
    SERVERS_DAT = os.path.join(MC, "servers.dat")
    return MC


def process_running(*names):
    try:
        out = subprocess.run(["tasklist"], capture_output=True, text=True, encoding="utf-8", errors="ignore",
                             creationflags=0x08000000).stdout.lower()
        return any(n.lower() in out for n in names)
    except Exception:
        return False


def _profiles_files():
    return [p for p in (os.path.join(MC, "launcher_profiles.json"),
                        os.path.join(MC, "launcher_profiles_microsoft_store.json")) if os.path.isfile(p)]


def set_launcher_profile(version, title):
    """Профиль «Portalis» в launcher_profiles.json (официальный лаунчер, SKLauncher): самый свежий lastUsed,
    поэтому он первый в списке. Возвращает True, если файл профилей нашёлся."""
    files = _profiles_files() or [os.path.join(MC, "launcher_profiles.json")]
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    icon = "Grass"
    try:
        with open(os.path.join(ROOT, "Оформление", "appicon_72.png"), "rb") as fh:
            icon = "data:image/png;base64," + base64.b64encode(fh.read()).decode()
    except OSError:
        pass
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            data = {"profiles": {}, "settings": {}, "version": 3}
        if os.path.isfile(path) and not os.path.exists(path + ".switcher.bak"):
            shutil.copy2(path, path + ".switcher.bak")
        prof = data.setdefault("profiles", {})
        old = prof.pop("kuboteka", None) or prof.get("portalis", {})  # «kuboteka» - профиль прежнего имени
        prof["portalis"] = {"name": "%s: %s" % (APP_NAME, title)[:60], "type": "custom",
                            "created": old.get("created", now), "lastUsed": now, "icon": icon, "lastVersionId": version}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    return True


def select_version(version, title="", log=print):
    """Выбирает версию в «моём» лаунчере. Возвращает (получилось, подсказка человеку)."""
    lid = chosen_launcher()
    mode = launcher_mode(lid)
    name = launcher_title(lid)
    vdir = os.path.join(MC, "versions", version)
    missing = not os.path.isfile(os.path.join(vdir, version + ".json"))
    if mode == "tl":
        ok = set_tlauncher_version(version)
        log("Версия в TLauncher: %s%s" % (version, "" if ok else " (TLauncher открыт, выбери версию в нём вручную)"))
        return ok, "" if ok else "TLauncher был открыт, поэтому версию «%s» выбери в нём сам." % version
    if mode == "legacy":
        p = legacy_props()
        if p and not process_running("LL.exe"):
            _props_set(p, "login.version", version)
            log("Версия в Legacy Launcher: %s" % version)
            return True, ""
        log("Версия для Legacy Launcher: %s" % version)
        return False, "В Legacy Launcher выбери версию «%s» (список внизу)." % version
    if mode == "profiles":
        set_launcher_profile(version, title or version)
        log("Профиль «%s» в %s: версия %s" % (APP_NAME, name, version))
        hint = "В %s выбери профиль «%s: %s» (он первый в списке) и нажми «Играть»." % (name, APP_NAME, title or version)
        if missing and not re.match(r"^\d+(\.\d+)+$", version):
            hint += (" Версии «%s» в папке игры нет: один раз поставь её через TLauncher или установщик "
                     "Forge/Fabric, иначе лаунчер её не найдёт." % version)
        return True, hint
    return False, "В %s выбери версию «%s»." % (name, version)


def launcher_exe(lid):
    """Путь к exe лаунчера: указанный вручную, из обычного места установки или из меню «Пуск»."""
    s = load_settings().get("launcher_paths", {})
    if s.get(lid) and os.path.isfile(s[lid]):
        return s[lid]
    for p in LAUNCHER_PATHS.get(lid, []):
        p = os.path.expandvars(p)
        if os.path.isfile(p):
            return p
    if lid == "prism":
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\PrismLauncher")
            p = os.path.join(winreg.QueryValueEx(k, "InstallDir")[0], "prismlauncher.exe")
            if os.path.isfile(p):
                return p
        except Exception:
            pass
    return None


def find_installed_launchers(launchers=None):
    launchers = launchers if launchers is not None else load_launchers()
    found = {}
    for x in launchers:
        exe = launcher_exe(x["id"])
        if exe:
            found[x["id"]] = exe
    apps = start_apps()
    for x in launchers:
        if x["id"] in found:
            continue
        for name, app in apps:
            low = name.lower()
            for kw in x.get("match", []):
                hit = low == kw[1:] if kw.startswith("=") else kw in low
                if hit and "media player" not in low:
                    found.setdefault(x["id"], app)
    return found


def run_target(target, args=()):
    if target.lower().endswith(".exe") and os.path.isfile(target):
        if args:
            subprocess.Popen([target] + list(args), cwd=os.path.dirname(target))
        else:
            os.startfile(target)
    else:
        subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + target])


def chosen_launcher():
    return load_settings().get("launcher", "tlauncher")


def launcher_title(lid):
    return next((x["name"] for x in load_launchers() if x["id"] == lid), "TLauncher")


def open_launcher():
    lid = chosen_launcher()
    exe = launcher_exe(lid)
    if lid == "tlauncher" and not exe:
        return open_tlauncher()
    target = exe or find_installed_launchers().get(lid)
    if target:
        run_target(target)
        return True
    return False


# --- пакет .mrpack для Prism Launcher и Modrinth App ---

def _servers_dat_bytes():
    lst = [("compound", [("ip", 8, s["ip"]), ("name", 8, s["name"]), ("acceptTextures", 1, 1), ("hideAddress", 1, 0)])
           for s in load_servers()]
    out = io.BytesIO()
    out.write(b"\x0a\x00\x00")
    _nbt_w(out, 10, ("compound", [("servers", 9, ("list", 10, lst))]))
    return out.getvalue()


def export_mrpack(m=None, pack=None, log=print):
    """Собирает .mrpack: моды с Modrinth - ссылками, остальное (конфиги, текстуры, мир карты) - внутри пакета.
    Prism Launcher и Modrinth App создают из него отдельный экземпляр с нужной версией и загрузчиком."""
    if m is not None and not pack and m.get("requires_pack"):
        pack = next((p for p in find_packs() if p["name"] == m.get("recommended")), None)
    if missing_items(m, pack):
        raise RuntimeError("Сначала нужно скачать карту или сборку.")
    man = load_local_manifest()
    urls = {}
    for iid in ([pack_item_id(pack)] if pack else []) + ([map_item_id(m)] if m else []):
        it = manifest_item(man, iid)
        if not it:
            continue
        try:
            for f in fetch_list(man, it)["files"]:
                if f["src"]["t"] == "url":
                    urls[f["p"]] = f["src"]["u"]
        except Exception:
            pass
    if pack and pack.get("mr_files"):
        base = os.path.relpath(pack["path"], ROOT).replace("\\", "/")
        for k, u in pack["mr_files"].items():
            urls[base + "/" + k] = u
    mc_ver = pack["minecraft"] if pack else m["version"]
    deps = {"minecraft": mc_ver}
    if pack and pack.get("loader") in ("Fabric", "Forge") and pack.get("loader_version"):
        deps["fabric-loader" if pack["loader"] == "Fabric" else "forge"] = pack["loader_version"]
    parts = [x for x in ((m or {}).get("title"), pack and pack["name"].rsplit(" (", 1)[0]) if x]
    title = " + ".join(dict.fromkeys(parts)) or mc_ver
    name = "%s - %s" % (APP_NAME, title)
    out_dir = os.path.join(ROOT, "_export")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, re.sub(r'[\\/:*?"<>|]', "_", title) + ".mrpack")
    files = []
    n_over = 0
    with zipfile.ZipFile(out + ".tmp", "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        if pack:
            base = os.path.relpath(pack["path"], ROOT).replace("\\", "/")
            for rel in _walk_rel(ROOT, base):
                sub = rel[len(base) + 1:]
                if "/" not in sub and sub in META_NAMES or sub.startswith(("versions/", "saves/")):
                    continue
                full = os.path.join(ROOT, rel)
                u = urls.get(rel)
                if u and urllib.parse.urlparse(u).netloc in MRPACK_HOSTS:
                    data = open(lp(full), "rb").read()
                    files.append({"path": sub, "hashes": {"sha1": hashlib.sha1(data).hexdigest(),
                                                          "sha512": hashlib.sha512(data).hexdigest()},
                                  "env": {"client": "required", "server": "required"},
                                  "downloads": [u], "fileSize": len(data)})
                else:
                    z.write(lp(full), "overrides/" + sub)
                    n_over += 1
        if m:
            wbase = os.path.relpath(os.path.join(m["path"], "world"), ROOT).replace("\\", "/")
            for rel in _walk_rel(ROOT, wbase):
                if rel.endswith("/session.lock"):
                    continue
                z.write(lp(os.path.join(ROOT, rel)), "overrides/saves/%s/%s" % (m["save"], rel[len(wbase) + 1:]))
                n_over += 1
            if m.get("usercache") and os.path.isfile(os.path.join(m["path"], m["usercache"])):
                z.write(os.path.join(m["path"], m["usercache"]), "overrides/usercache.json")
        if load_servers():
            z.writestr("overrides/servers.dat", _servers_dat_bytes())
        z.writestr("modrinth.index.json", json.dumps({
            "formatVersion": 1, "game": "minecraft", "versionId": load_local_manifest().get("version", "1"),
            "name": name, "summary": "Собрано в %s" % APP_NAME, "files": files, "dependencies": deps},
            ensure_ascii=False, indent=1))
    os.replace(out + ".tmp", out)
    log("Пакет для лаунчера собран: %s (модов по ссылкам %d, файлов внутри %d, %s)" % (
        os.path.basename(out), len(files), n_over, fmt_mb(os.path.getsize(out))))
    return out


def open_mrpack(path, lid=None):
    """Передаёт .mrpack лаунчеру: Prism - через --import, Modrinth App - путём в аргументе, иначе - как файл."""
    lid = lid or chosen_launcher()
    exe = launcher_exe(lid)
    if exe and lid == "prism":
        subprocess.Popen([exe, "--import", path], cwd=os.path.dirname(exe))
    elif exe and lid == "modrinth":
        subprocess.Popen([exe, path], cwd=os.path.dirname(exe))
    else:
        os.startfile(path)
    return True


# --- скачивание установщиков лаунчеров с официальных сайтов ---

def launcher_installer_url(x):
    """Прямая ссылка на установщик (только официальные адреса). None - качать в браузере с сайта."""
    d = x.get("download") or {}
    kind = d.get("kind")
    if kind == "url":
        return d["url"]
    if kind == "github":
        rel = json.loads(_get("https://api.github.com/repos/%s/releases/latest" % d["repo"]).decode("utf-8"))
        for a in rel.get("assets", []):
            if re.search(d["pattern"], a["name"]):
                return a["browser_download_url"]
    if kind == "modrinth":
        upd = json.loads(_get("https://launcher-files.modrinth.com/updates.json").decode("utf-8"))
        return upd["platforms"]["windows-x86_64"]["install_urls"][0]
    return None


def download_installer(x, log=print, progress=None, cancel=None):
    url = launcher_installer_url(x)
    if not url:
        raise RuntimeError("установщик %s скачивается только в браузере" % x["name"])
    name = urllib.parse.unquote(url.rsplit("/", 1)[-1].split("?")[0]) or (x["id"] + "-setup.exe")
    if not name.lower().endswith((".exe", ".msi")):
        name += ".exe"
    dst = os.path.join(downloads_dir(), name)
    log("Скачиваю %s с %s" % (name, urllib.parse.urlparse(url).netloc))
    total = [0]

    def tick(n):
        total[0] += n
        if progress:
            progress(total[0])
    _fetch_to(url, dst, tick, None, cancel, {"User-Agent": UA_BROWSER})
    log("Скачано: %s (%s)" % (dst, fmt_mb(os.path.getsize(dst))))
    return dst


def merge_usercache(src):
    """Добавляет записи из usercache.json карты в .minecraft\\usercache.json, свои записи не трогает."""
    dst = os.path.join(MC, "usercache.json")
    try:
        with open(src, encoding="utf-8") as fh:
            new = json.load(fh)
    except Exception:
        return 0
    try:
        with open(dst, encoding="utf-8") as fh:
            have = json.load(fh)
    except Exception:
        have = []
    names = {str(e.get("name", "")).lower() for e in have}
    added = [e for e in new if str(e.get("name", "")).lower() not in names]
    if added:
        os.makedirs(MC, exist_ok=True)
        with open(dst, "w", encoding="utf-8") as fh:
            json.dump(have + added, fh, ensure_ascii=False)
    return len(added)


def backups_dir():
    docs = os.path.join(os.environ.get("USERPROFILE", ""), "Documents")
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
        docs = os.path.expandvars(winreg.QueryValueEx(k, "Personal")[0])
    except Exception:
        pass
    return os.path.join(docs, "Minecraft - копии миров")


def backup_world(m, log=print):
    """Копия сохранения карты в zip в «Документы\\Minecraft - копии миров»."""
    src = os.path.join(SAVES, m["save"])
    if not os.path.isdir(src):
        raise RuntimeError("Карта ещё не установлена: копировать нечего.")
    out_dir = backups_dir()
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "%s %s.zip" % (m["save"], time.strftime("%Y-%m-%d %H-%M")))
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for d, _dirs, files in os.walk(lp(src)):
            for f in files:
                full = os.path.join(d, f)
                if f == "session.lock":
                    continue
                z.write(full, os.path.join(m["save"], os.path.relpath(full, lp(src))))
                n += 1
    log("Копия мира «%s» сохранена: %s (%d файлов, %.0f МБ)" % (m["title"], out, n, os.path.getsize(out) / 1048576))
    return out


def map_matches(m, players="all", version="all"):
    lo, hi = m.get("players_min", 1), m.get("players_max", 10)
    if players == "solo" and lo > 1:
        return False
    if players == "duo" and not (lo <= 2 <= hi):
        return False
    if players == "group" and hi < 3:
        return False
    v = m["version"]
    if version == "new" and not v.startswith("26."):
        return False
    if version == "1.21" and not v.startswith("1.21"):
        return False
    if version == "1.20" and not v.startswith("1.20"):
        return False
    if version == "old" and (v.startswith("26.") or v.startswith("1.2")):
        return False
    return True


# ---------- ярлыки ----------

APP_NAME = "Portalis"
APP_TITLE = APP_NAME
EXE_NAME = APP_NAME + ".exe"
# Прежние имена программы (до 04.10.2026 и «Portalis»): старые файлы и ярлыки переименовываются при запуске.
OLD_EXE_NAMES = ("Выбор карты и сборки.exe", "Куботека.exe")
OLD_APP_TITLES = ("Minecraft - карты и сборки", "Куботека")


def _shell_folder(name, default):
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
        return os.path.expandvars(winreg.QueryValueEx(k, name)[0])
    except Exception:
        return default


def shortcut_paths():
    home = os.environ.get("USERPROFILE", "")
    desk = _shell_folder("Desktop", os.path.join(home, "Desktop"))
    start = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")
    paths = [os.path.join(start, APP_TITLE + ".lnk")]
    if os.path.normcase(os.path.abspath(APP_DIR)) != os.path.normcase(os.path.abspath(desk)):
        paths.insert(0, os.path.join(desk, APP_TITLE + ".lnk"))
    return paths


def exe_path():
    """Файл программы, который обновлять и на который делать ярлыки."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.join(APP_DIR, EXE_NAME)


def app_target():
    """Что запускать ярлыком: сам exe, а без него — .pyw через pythonw."""
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    exe = os.path.join(ROOT, EXE_NAME)
    if os.path.isfile(exe):
        return exe, ""
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return (pyw if os.path.isfile(pyw) else sys.executable), '"%s"' % os.path.abspath(__file__)


def migrate_old_name(argv):
    """Запущен exe со старым именем: копируем себя в «Portalis.exe» и перезапускаемся под новым именем."""
    if not getattr(sys, "frozen", False) or os.path.basename(sys.executable) not in OLD_EXE_NAMES:
        return False
    new = os.path.join(APP_DIR, EXE_NAME)
    try:
        if not (os.path.isfile(new) and os.path.getsize(new) == os.path.getsize(sys.executable)):
            shutil.copy2(sys.executable, new)
        subprocess.Popen([new] + list(argv), cwd=ROOT)
        return True
    except Exception:
        return False


def tidy_old_name():
    """Убрать старый exe и старые ярлыки (их место займут новые)."""
    def work():
        for _ in range(20):
            left = 0
            for n in OLD_EXE_NAMES:
                p = os.path.join(APP_DIR, n)
                if os.path.exists(p) and os.path.abspath(p) != os.path.abspath(sys.executable):
                    try:
                        os.remove(p)
                    except OSError:
                        left += 1
            if not left:
                break
            time.sleep(1)
    threading.Thread(target=work, daemon=True).start()
    had = False
    home = os.environ.get("USERPROFILE", "")
    desk = _shell_folder("Desktop", os.path.join(home, "Desktop"))
    start = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")
    for t in OLD_APP_TITLES:
        for d in (desk, start):
            p = os.path.join(d, t + ".lnk")
            if os.path.isfile(p):
                had = True
                try:
                    os.remove(p)
                except OSError:
                    pass
    if had and not shortcuts_exist():
        def make():
            try:
                create_shortcuts()
            except Exception:
                pass
        threading.Thread(target=make, daemon=True).start()


def shortcuts_exist():
    return all(os.path.isfile(p) for p in shortcut_paths())


def create_shortcuts():
    """Ярлык на рабочем столе и в меню «Пуск». Возвращает список созданных путей."""
    target, args = app_target()
    icon = exe_path() if os.path.isfile(exe_path()) else target
    made = []
    for path in shortcut_paths():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{0}');$s.TargetPath='{1}';"
              "$s.Arguments='{2}';$s.WorkingDirectory='{3}';$s.IconLocation='{4},0';"
              "$s.Description='Карты, сборки и серверы Minecraft';$s.Save()").format(
            *(x.replace("'", "''") for x in (path, target, args, ROOT, icon)))
        subprocess.run(["powershell", "-NoProfile", "-Command", PS_UTF8, ps], capture_output=True, timeout=30,
                       creationflags=0x08000000)
        if os.path.isfile(path):
            made.append(path)
    return made


# ---------- каталог и загрузки ----------
#
# Описания карт и сборок (map.json, pack.json, mods.json, обложки) лежат рядом с программой всегда:
# это каталог. Содержимое карты или сборки скачивается, только когда её выбирают, и берётся из
# исходных мест: моды - с Modrinth и CurseForge, карты - с сайтов авторов (minecraft-inside.ru,
# skyblock.net, ijaminecraft.com, minecraftmaps.com), клиент Minecraft - с серверов Mojang.
# В релизе на GitHub лежат только наши файлы: программа, описания, конфиги сборок.
# manifest.json - опись версии: items (core, app, pack:<папка>, map:<id>) и archives (исходные архивы).
# У каждой части свой список файлов list-*.json: путь, sha1, размер и откуда взять (src):
#   own - из нашего архива части на GitHub, url - прямая ссылка, arc - файл внутри исходного архива,
#   arcfile - сам исходный архив, zpatch - скачать оригинал и заменить в нём несколько файлов.

REPO = "SKUF666/minecraft-packs"
REMOTE_MANIFEST = "https://github.com/%s/releases/latest/download/manifest.json" % REPO
LOCAL_MANIFEST = os.path.join(ROOT, "manifest.json")
UPD_DIR = os.path.join(ROOT, "_update")
_JUNK = ("__pycache__", "desktop.ini", "thumbs.db", ".ds_store", "switcher_cli.log")
UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/140.0 Safari/537.36")
META_NAMES = ("map.json", "pack.json", "mods.json", "cover.png", "cover_big.png")
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def vtuple(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


def load_local_manifest(root=ROOT):
    try:
        with open(os.path.join(root, "manifest.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


load_manifest_at = load_local_manifest


class AssetGone(RuntimeError):
    """Файла уже нет в релизе GitHub: пока качали, вышла новая версия (или GitHub ещё отдаёт старую опись)."""


def _get(url, timeout=30, tries=6, headers=None):
    """Небольшой файл целиком. GitHub иногда отвечает 503, поэтому несколько попыток разными путями."""
    last = None
    for i in range(tries):
        req = urllib.request.Request(url, headers=dict({"User-Agent": "MinecraftPacks/4.0"}, **(headers or {})))
        try:
            return urlopen(req, timeout, i).read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:
                raise AssetGone("на GitHub нет файла %s" % url.rsplit("/", 1)[-1].split("?")[0])
        except Exception as e:
            last = e
        time.sleep(1.0 + i)
    raise RuntimeError(str(last))


def fetch_remote_manifest(timeout=15, tries=6):
    """Опись последней версии с GitHub."""
    data = _get(REMOTE_MANIFEST + "?t=%d" % int(time.time() * 1000), timeout, tries, {"Cache-Control": "no-cache"})
    return json.loads(data.decode("utf-8"))


def asset_url(man, name):
    return "https://github.com/%s/releases/download/%s/%s" % (man.get("repo", REPO), man.get("tag", "library"),
                                                               urllib.parse.quote(name))


def _sha1_file(path):
    h = hashlib.sha1()
    with open(lp(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class HashCache:
    """sha1 файлов по (размер, время изменения), чтобы не пересчитывать гигабайт при каждой проверке."""

    def __init__(self, root=ROOT):
        self.root = root
        self.path = os.path.join(root, "_update", "hashcache.json")
        try:
            with open(self.path, encoding="utf-8") as fh:
                self.data = json.load(fh)
        except Exception:
            self.data = {}

    def sha1(self, rel):
        full = os.path.join(self.root, rel)
        st = os.stat(lp(full))
        key = rel.replace("\\", "/")
        hit = self.data.get(key)
        if hit and hit[0] == st.st_size and abs(hit[1] - st.st_mtime) < 0.01:
            return hit[2]
        h = _sha1_file(full)
        self.data[key] = [st.st_size, st.st_mtime, h]
        return h

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(self.data, fh)
            os.replace(self.path + ".tmp", self.path)
        except Exception:
            pass


def _walk_rel(root, base):
    """Файлы папки base (относительно root) в виде путей через «/»."""
    out = []
    top = os.path.join(root, base) if base else root
    if base and os.path.isfile(lp(top)):
        return [base.replace("\\", "/")]
    for d, dirs, files in os.walk(lp(top)):
        dirs[:] = [x for x in dirs if x.lower() not in _JUNK]
        for f in files:
            if f.lower() in _JUNK or f.lower().endswith(".pyc"):
                continue
            rel = os.path.relpath(os.path.join(d, f), lp(root))
            out.append(rel.replace("\\", "/"))
    return out


def block_hash(files, cache):
    """Хеш набора файлов: sha1 от строк «путь<TAB>sha1» по порядку путей."""
    h = hashlib.sha1()
    for f in sorted(files):
        h.update(("%s\t%s\n" % (f, cache.sha1(f))).encode("utf-8"))
    return h.hexdigest()


def zip_member_name(info):
    """Имя файла в zip. Старые архивы хранят русские имена в cp866 без пометки utf-8."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp866")
    except Exception:
        return info.filename


def zpatch_build(src, dst, setmap):
    """Пересобирает zip с заменой файлов. Без сжатия и с фиксированной датой, чтобы sha1 у всех совпадал."""
    with zipfile.ZipFile(lp(src)) as zin, zipfile.ZipFile(lp(dst), "w", zipfile.ZIP_STORED) as zout:
        for i in zin.infolist():
            if i.is_dir():
                continue
            data = setmap[i.filename].encode("utf-8") if i.filename in setmap else zin.read(i)
            zi = zipfile.ZipInfo(i.filename, date_time=ZIP_EPOCH)
            zi.compress_type = zipfile.ZIP_STORED
            zi.create_system = 0
            zi.external_attr = 0
            zout.writestr(zi, data)


# --- части библиотеки ---

def manifest_item(man, item_id):
    return next((i for i in man.get("items", []) if i["id"] == item_id), None)


def map_item_id(m):
    return "map:" + os.path.basename(m["path"])


def pack_item_id(p):
    return "pack:" + os.path.relpath(p["path"], ROOT).replace("\\", "/")


def item_content(item, root=ROOT):
    """Файлы содержимого карты или сборки: всё в её папке, кроме описаний (map.json, pack.json, обложки)."""
    base = item["path"]
    depth = base.count("/") + 1
    return sorted(f for f in _walk_rel(root, base)
                  if not (f.count("/") == depth and f.rsplit("/", 1)[1] in META_NAMES))


def item_downloaded(item, root=ROOT):
    if not item or item["kind"] in ("core", "app"):
        return True
    p = os.path.join(root, item["path"])
    try:
        return any(f not in META_NAMES for f in os.listdir(lp(p)))
    except OSError:
        return False


def downloaded_ids(man, root=ROOT):
    return {i["id"] for i in man.get("items", []) if i["kind"] in ("pack", "map") and item_downloaded(i, root)}


def fetch_list(man, item, root=ROOT):
    """Список файлов части (кэшируется в _update/lists)."""
    d = os.path.join(root, "_update", "lists")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, item["list"])
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    data = _get(asset_url(man, item["list"]))
    if item.get("list_sha1") and hashlib.sha1(data).hexdigest() != item["list_sha1"]:
        raise RuntimeError("список файлов «%s» скачался с ошибкой" % item["title"])
    lst = json.loads(data.decode("utf-8"))
    with open(p + ".tmp", "wb") as fh:
        fh.write(data)
    os.replace(p + ".tmp", p)
    return lst


PLAN_LOCK = threading.RLock()


def plan_items(man, items, root=ROOT, log=print):
    """Какие файлы скачать и какие лишние убрать, чтобы части items совпали с описью man."""
    with PLAN_LOCK:
        return _plan_items(man, items, root, log)


def _plan_items(man, items, root, log):
    cache = HashCache(root)
    need, extra = [], []
    for it in items:
        log("Проверяю: %s" % it["title"])
        lst = [f for f in fetch_list(man, it, root)["files"] if f["p"] not in OLD_EXE_NAMES]
        want = {f["p"] for f in lst}
        for f in lst:
            try:
                if f["p"] == EXE_NAME and os.path.abspath(root) == os.path.abspath(ROOT):
                    ok = os.path.isfile(exe_path()) and _sha1_file(exe_path()) == f["s"]
                else:
                    ok = os.path.isfile(lp(os.path.join(root, f["p"]))) and cache.sha1(f["p"]) == f["s"]
            except OSError:
                ok = False
            if not ok:
                need.append(dict(f, item=it["id"]))
        if it["kind"] in ("pack", "map"):
            extra += [rel for rel in item_content(it, root) if rel not in want]
    cache.save()
    return {"need": need, "extra": extra, "remove": [], "items": [i["id"] for i in items]}


def plan_size(man, plan):
    """Сколько примерно скачивать: исходные архивы целиком, прямые ссылки, наши архивы частей."""
    archives = {a["id"]: a for a in man.get("archives", [])}
    urls, arcs, own = {}, set(), set()
    for f in plan["need"]:
        s = f["src"]
        if s["t"] == "url":
            urls[f["s"]] = f["z"]
        elif s["t"] in ("arc", "arcfile"):
            arcs.add(s["a"])
        elif s["t"] == "zpatch":
            urls[s["us"]] = s.get("uz", f["z"])
        else:
            own.add(f["item"])
    return (sum(urls.values()) + sum(archives[a]["size"] for a in arcs if a in archives)
            + sum((manifest_item(man, i) or {}).get("own_size", 0) for i in own))


def plan_sources(man, plan):
    """Откуда будет скачиваться: для окна «Скачаю ...»."""
    archives = {a["id"]: a for a in man.get("archives", [])}
    hosts = []
    for f in plan["need"]:
        s = f["src"]
        if s["t"] in ("arc", "arcfile") and s["a"] in archives:
            u = archives[s["a"]].get("url") or archives[s["a"]].get("page", "")
        elif s["t"] in ("url", "zpatch"):
            u = s.get("u", "")
        else:
            u = "github.com"
        h = urllib.parse.urlparse(u).netloc or u
        h = {"cdn.modrinth.com": "Modrinth", "mediafilez.forgecdn.net": "CurseForge",
             "piston-data.mojang.com": "Mojang", "api.skyblock.net": "skyblock.net"}.get(h, h.replace("www.", ""))
        if h not in hosts:
            hosts.append(h)
    return hosts


def plan_update(remote, root=ROOT, log=print, extra_items=()):
    """Обновление: файлы программы, каталог и уже скачанные карты и сборки (+ extra_items)."""
    old = load_manifest_at(root)
    if not remote.get("items"):
        raise RuntimeError("опись на GitHub пустая или повреждена, обновление пропущено")
    have = downloaded_ids(remote, root) | set(extra_items)
    # Прошлая версия программы (до 2026.10.05) хранила части по-другому: считаем скачанным всё, что лежит.
    items = [i for i in remote["items"] if i["kind"] in ("core", "app") or i["id"] in have]
    plan = plan_items(remote, items, root, log)
    new_ids = {i["id"] for i in remote["items"]}
    new_dirs = {i["path"] for i in remote["items"] if i["kind"] in ("pack", "map")}
    # Карты и сборки, которых в новой версии нет: целиком в Корзину (только те, что были нашими).
    old_dirs = {i["path"] for i in old.get("items", []) if i["kind"] in ("pack", "map") and i["id"] not in new_ids}
    old_dirs |= {b["path"] for b in old.get("blocks", []) if b["kind"] in ("pack", "map")}
    old_dirs |= set(old.get("packs", {})) | {"Карты/" + k for k in old.get("maps", {})}
    remove = sorted(d for d in old_dirs - new_dirs if os.path.isdir(lp(os.path.join(root, d))))
    # Файлы каталога и корня, которые прошлая версия раскладывала, а новая нет.
    core = manifest_item(remote, "core")
    new_core = {f["p"] for f in fetch_list(remote, core, root)["files"]} if core else set()
    old_core = set()
    oc = manifest_item(old, "core")
    if oc:
        p = os.path.join(root, "_update", "lists", oc.get("list", "-"))
        try:
            with open(p, encoding="utf-8") as fh:
                old_core = {f["p"] for f in json.load(fh)["files"]}
        except Exception:
            pass
    tops_new = {p.split("/")[0] for p in new_core} | {d.split("/")[0] for d in new_dirs} | {"manifest.json", EXE_NAME}
    for name in old.get("root", []):
        # Из старой описи корня убираем только файлы (например «Переключатель сборок.exe»), не папки:
        # в папке версии может лежать своя сборка друга.
        if "/" not in name and name not in tops_new and not name.startswith("_") \
                and os.path.isfile(lp(os.path.join(root, name))):
            old_core.add(name)
    for p in sorted(old_core - new_core):
        if p in ("manifest.json", EXE_NAME) or any(p == d or p.startswith(d + "/") for d in new_dirs):
            continue
        if os.path.isfile(lp(os.path.join(root, p))):
            remove.append(p)
    plan["remove"] = sorted(set(remove))
    plan["version"] = remote.get("version")
    plan["changes"] = remote.get("changes", [])
    plan["size"] = plan_size(remote, plan)
    return plan


def plan_download(remote, item_ids, root=ROOT, log=print):
    """Скачать карты и сборки item_ids. Если вышла новая версия, заодно обновить всё остальное."""
    local = load_manifest_at(root)
    if local.get("version") != remote.get("version") or not local.get("items"):
        plan = plan_update(remote, root, log, item_ids)
        plan["with_update"] = True
        return plan
    items = [manifest_item(remote, i) for i in item_ids]
    plan = plan_items(remote, [i for i in items if i], root, log)
    plan["size"] = plan_size(remote, plan)
    plan["version"] = remote.get("version")
    return plan


def downloads_dir():
    return _shell_folder("{374DE290-123F-4565-9164-39C4925E467B}",
                         os.path.join(os.environ.get("USERPROFILE", ""), "Downloads"))


def find_archive_copy(a, root=ROOT):
    """Исходный архив, который уже есть: в кэше программы или в «Загрузках» (под любым именем)."""
    cands = [os.path.join(root, "_update", "archives", a["name"])]
    dl = downloads_dir()
    try:
        for f in os.listdir(dl):
            p = os.path.join(dl, f)
            if f == a["name"] or (f.lower().endswith(".zip") and os.path.getsize(p) == a["size"]):
                cands.append(p)
    except OSError:
        pass
    for p in cands:
        try:
            if os.path.isfile(p) and os.path.getsize(p) == a["size"] and _sha1_file(p) == a["sha1"]:
                return p
        except OSError:
            pass
    return None


def wait_archive_in_downloads(a, cancel=None, timeout=1800, manual=None):
    """Ждёт, пока человек скачает архив в браузере: файл нужного размера в «Загрузках» (или выбранный вручную)."""
    t0 = time.time()
    seen = set()
    while time.time() - t0 < timeout:
        if cancel and cancel.is_set():
            raise RuntimeError("отменено")
        if manual and manual.get("file"):
            p = manual.pop("file")
            if os.path.isfile(p) and _sha1_file(p) == a["sha1"]:
                return p
            manual["bad"] = p
        dl = downloads_dir()
        try:
            for f in os.listdir(dl):
                p = os.path.join(dl, f)
                if p in seen or f.endswith((".crdownload", ".part", ".tmp")):
                    continue
                if os.path.getsize(p) == a["size"]:
                    seen.add(p)
                    if _sha1_file(p) == a["sha1"]:
                        return p
        except OSError:
            pass
        time.sleep(2)
    raise RuntimeError("не дождался файла %s в «Загрузках»" % a["name"])


def _fetch_to(url, dst, progress=None, expect_sha1=None, cancel=None, headers=None):
    last = None
    os.makedirs(os.path.dirname(lp(dst)), exist_ok=True)
    for attempt in range(4):
        got = 0
        try:
            req = urllib.request.Request(url, headers=dict({"User-Agent": "MinecraftPacks/4.0"}, **(headers or {})))
            h = hashlib.sha1()
            with urlopen(req, 60, attempt) as r, open(lp(dst) + ".part", "wb") as out:
                while True:
                    if cancel and cancel.is_set():
                        raise RuntimeError("отменено")
                    chunk = r.read(1 << 18)
                    if not chunk:
                        break
                    out.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    if progress:
                        progress(len(chunk))
            if expect_sha1 and h.hexdigest() != expect_sha1:
                raise RuntimeError("файл скачался с ошибкой (не совпала контрольная сумма)")
            os.replace(lp(dst) + ".part", lp(dst))
            return
        except Exception as e:
            last = e
            try:
                os.remove(lp(dst) + ".part")
            except OSError:
                pass
            if progress and got:
                progress(-got)
            if cancel and cancel.is_set():
                raise
            if isinstance(e, urllib.error.HTTPError) and e.code == 404 and "/releases/download/" in url:
                raise AssetGone("на GitHub уже нет файла %s" % urllib.parse.unquote(url.rsplit("/", 1)[-1]))
            time.sleep(1 + attempt * 2)
    raise RuntimeError("не удалось скачать %s: %s" % (urllib.parse.unquote(url.rsplit("/", 1)[-1]), last))


def get_archive(man, a, root, tick, cancel, ask_browser, log):
    hit = find_archive_copy(a, root)
    if hit:
        log("Беру уже скачанный %s" % os.path.basename(hit))
        tick(a["size"])
        return hit
    dst = os.path.join(root, "_update", "archives", a["name"])
    err = None
    if a.get("url") and not a.get("browser"):
        site = urllib.parse.urlparse(a["url"]).netloc.replace("www.", "")
        log("Скачиваю с %s: %s (%s)" % (site, a["name"], fmt_mb(a["size"])))
        try:
            _fetch_to(a["url"], dst, tick, a["sha1"], cancel, {"User-Agent": UA_BROWSER, "Referer": a.get("page", "")})
            return dst
        except Exception as e:
            if cancel and cancel.is_set():
                raise
            err = e
    if ask_browser:
        p = ask_browser(a, err)
        tick(a["size"])
        return p
    raise RuntimeError("архив %s нужно скачать вручную со страницы %s (%s)" % (a["name"], a.get("page"), err or ""))


def recycle(path, tries=4, log=None):
    """В Корзину. Свежие файлы бывают заняты антивирусом, поэтому несколько попыток с паузой.
    На флешке и сетевом диске Корзины нет (Windows удалил бы насовсем) - тогда False, и файлы остаются в «_Старое»."""
    try:
        drive = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
        if ctypes.windll.kernel32.GetDriveTypeW(drive) != 3:  # 3 = DRIVE_FIXED
            return False
    except Exception:
        return False
    for i in range(tries):
        code = _recycle_once(path)
        if code == 0 and not os.path.exists(path):
            return True
        if log:
            log("Корзина пока не принимает старые файлы (код %s), пробую ещё раз" % code)
        time.sleep(1.5 * (i + 1))
    return False


def _recycle_once(path):
    try:
        wintypes = ctypes.wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                        ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
        op = SHFILEOPSTRUCTW(None, 3, os.path.abspath(path) + "\0\0", None, 0x0040 | 0x0010 | 0x0004 | 0x0400, False,
                             None, None)
        return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    except Exception as e:
        return str(e)


def _move(src, dst):
    os.makedirs(os.path.dirname(lp(dst)), exist_ok=True)
    os.replace(lp(src), lp(dst))


def _write(path, data):
    os.makedirs(os.path.dirname(lp(path)), exist_ok=True)
    with open(lp(path), "wb") as fh:
        fh.write(data)


def _lay_out(need, root, stage, old_dir):
    for f in need:
        if f["p"] == EXE_NAME:
            continue
        dst = os.path.join(root, f["p"])
        if os.path.exists(lp(dst)):
            _move(dst, os.path.join(old_dir, f["p"]))
        _move(os.path.join(stage, f["p"]), dst)


def apply_plan(man, plan, root=ROOT, log=print, progress=None, cancel=None, ask_browser=None):
    with PLAN_LOCK:
        try:
            return _apply_plan(man, plan, root, log, progress, cancel, ask_browser)
        except AssetGone:
            fresh = fresh_manifest_after(man, cancel)
            if not fresh:
                raise
        log("Пока качал, на GitHub вышла версия %s - докачиваю её" % fresh.get("version"))
        ids = [i for i in plan.get("items", []) if (manifest_item(man, i) or {}).get("kind") in ("pack", "map")]
        plan2 = plan_download(fresh, ids, root, lambda *a: None) if ids else plan_update(fresh, root, lambda *a: None)
        return _apply_plan(fresh, plan2, root, log, progress, cancel, ask_browser)


def fresh_manifest_after(man, cancel=None, wait=150):
    """Опись новее, чем man. GitHub раздаёт manifest.json через кэш, свежая опись появляется до пары минут."""
    t0 = time.time()
    while time.time() - t0 < wait:
        if cancel and cancel.is_set():
            return None
        try:
            fresh = fetch_remote_manifest()
            if fresh.get("version") != man.get("version") and fresh.get("items"):
                return fresh
        except Exception:
            pass
        time.sleep(10)
    return None


def _apply_plan(man, plan, root=ROOT, log=print, progress=None, cancel=None, ask_browser=None):
    """Скачивает нужные файлы из всех источников, проверяет sha1 и раскладывает. Старое - в Корзину.
    Возвращает {'restart': exe заменён, нужен перезапуск; 'trash': папка со старым, если Корзина не приняла}."""
    ThreadPoolExecutor = concurrent.futures.ThreadPoolExecutor
    upd = os.path.join(root, "_update")
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    stage = os.path.join(upd, "new_" + stamp)
    dl = os.path.join(upd, "dl")
    old_dir = os.path.join(upd, "old_" + stamp)
    total = max(1, plan_size(man, plan))
    done = [0]
    lock = threading.Lock()

    def tick(n):
        with lock:
            done[0] += n
            if progress:
                progress(max(0, done[0]), total)

    archives = {a["id"]: a for a in man.get("archives", [])}
    need = plan["need"]
    staged = {}

    def put(f, data):
        if hashlib.sha1(data).hexdigest() != f["s"]:
            raise RuntimeError("файл %s не совпал с описью (источник изменился?)" % f["p"])
        _write(os.path.join(stage, f["p"]), data)
        staged[f["p"]] = True

    try:
        # 1. Наши файлы: архив части с GitHub.
        own = {}
        for f in need:
            if f["src"]["t"] == "own":
                own.setdefault(f["item"], []).append(f)
        for iid, files in own.items():
            it = manifest_item(man, iid)
            log("Скачиваю с GitHub: %s" % it["title"])
            zp = os.path.join(dl, it["own"])
            if not (os.path.isfile(zp) and _sha1_file(zp) == it.get("own_sha1")):
                _fetch_to(asset_url(man, it["own"]), zp, tick, it.get("own_sha1"), cancel)
            else:
                tick(it.get("own_size", 0))
            with zipfile.ZipFile(lp(zp)) as z:
                for f in files:
                    put(f, z.read(f["p"]))
        # 2. Прямые ссылки (Modrinth, CurseForge, Mojang): каждый файл один раз, по 4 сразу.
        urls = {}
        for f in need:
            if f["src"]["t"] == "url":
                urls.setdefault(f["s"], []).append(f)
        if urls:
            log("Скачиваю моды и файлы с Modrinth, CurseForge и Mojang: %d" % len(urls))

            def one(fs):
                f = fs[0]
                p = os.path.join(dl, "f_" + f["s"])
                if not (os.path.isfile(p) and _sha1_file(p) == f["s"]):
                    _fetch_to(f["src"]["u"], p, tick, f["s"], cancel, {"User-Agent": UA_BROWSER})
                else:
                    tick(f["z"])
                return fs
            with ThreadPoolExecutor(4) as ex:
                for fs in ex.map(one, list(urls.values())):
                    with open(os.path.join(dl, "f_" + fs[0]["s"]), "rb") as fh:
                        data = fh.read()
                    for f in fs:
                        put(f, data)
        # 3. Исходные архивы карт с сайтов авторов.
        by_arc = {}
        for f in need:
            if f["src"]["t"] in ("arc", "arcfile"):
                by_arc.setdefault(f["src"]["a"], []).append(f)
        for aid, files in by_arc.items():
            a = archives[aid]
            path = get_archive(man, a, root, tick, cancel, ask_browser, log)
            log("Распаковываю %s" % a["name"])
            with zipfile.ZipFile(lp(path)) as z:
                names = {zip_member_name(i): i for i in z.infolist() if not i.is_dir()}
                for f in files:
                    if f["src"]["t"] == "arcfile":
                        with open(lp(path), "rb") as fh:
                            put(f, fh.read())
                    else:
                        info = names.get(f["src"]["m"])
                        if info is None:
                            raise RuntimeError("в архиве %s нет файла %s" % (a["name"], f["src"]["m"]))
                        put(f, z.read(info))
        # 4. Оригинал с заменой нескольких файлов (Faithful с поправленным pack.mcmeta).
        for f in need:
            s = f["src"]
            if s["t"] != "zpatch":
                continue
            log("Скачиваю %s" % f["p"].rsplit("/", 1)[-1])
            orig = os.path.join(dl, "z_" + s["us"])
            if not (os.path.isfile(orig) and _sha1_file(orig) == s["us"]):
                _fetch_to(s["u"], orig, tick, s["us"], cancel, {"User-Agent": UA_BROWSER})
            out = os.path.join(stage, f["p"])
            os.makedirs(os.path.dirname(lp(out)), exist_ok=True)
            zpatch_build(orig, out, s["set"])
            if _sha1_file(out) != f["s"]:
                raise RuntimeError("файл %s после правки не совпал с описью" % f["p"])
            staged[f["p"]] = True
        if cancel and cancel.is_set():
            raise RuntimeError("отменено")
        missing = [f["p"] for f in need if f["p"] not in staged]
        if missing:
            raise RuntimeError("не нашёл источник для %d файлов, например %s" % (len(missing), missing[0]))
    except Exception:
        shutil.rmtree(lp(stage), ignore_errors=True)
        raise

    # 5. Раскладка. Программу меняем последней. Если что-то сорвётся, прежние версии файлов
    # остаются в _update/old_<время>, и при следующем запуске уходят в Корзину или в «_Старое».
    restart = False
    app_files = [f for f in need if f["p"] == EXE_NAME]
    try:
        _lay_out(need, root, stage, old_dir)
    except Exception:
        shutil.rmtree(lp(stage), ignore_errors=True)
        raise
    for p in plan.get("extra", []) + plan.get("remove", []):
        if os.path.exists(lp(os.path.join(root, p))):
            log("Убираю старое: %s" % p)
            _move(os.path.join(root, p), os.path.join(old_dir, p))
            parent = os.path.dirname(os.path.join(root, p))
            try:
                while os.path.abspath(parent) != os.path.abspath(root) and not os.listdir(lp(parent)):
                    os.rmdir(lp(parent))
                    parent = os.path.dirname(parent)
            except OSError:
                pass
    if plan.get("with_update") or "core" in plan.get("items", []):
        with open(os.path.join(root, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(man, fh, ensure_ascii=False, indent=1)
    trash = None
    if os.path.isdir(old_dir):
        if not recycle(old_dir, log=log):
            trash = os.path.join(root, "_Старое после обновления " + stamp)
            os.replace(old_dir, trash)
    for f in app_files:
        target = exe_path() if os.path.abspath(root) == os.path.abspath(ROOT) else os.path.join(root, EXE_NAME)
        running = getattr(sys, "frozen", False) and os.path.abspath(sys.executable) == os.path.abspath(target)
        if os.path.exists(target + ".old"):
            try:
                os.remove(target + ".old")
            except OSError:
                pass
        if os.path.exists(target):
            os.replace(target, target + ".old")
        _move(os.path.join(stage, EXE_NAME), target)
        if running:
            restart = True
        else:
            try:
                os.remove(target + ".old")
            except OSError:
                pass
    shutil.rmtree(lp(stage), ignore_errors=True)
    shutil.rmtree(lp(dl), ignore_errors=True)
    shutil.rmtree(lp(os.path.join(upd, "archives")), ignore_errors=True)
    log("Готово: версия %s" % man.get("version"))
    return {"restart": restart, "trash": trash}


apply_update = apply_plan


def remove_item(item, root=ROOT, log=print):
    """Убрать скачанное содержимое карты или сборки (описание остаётся в каталоге)."""
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    old_dir = os.path.join(root, "_update", "old_" + stamp)
    files = item_content(item, root)
    for rel in files:
        _move(os.path.join(root, rel), os.path.join(old_dir, rel))
    base = os.path.join(root, item["path"])
    for d, dirs, fs in sorted(os.walk(lp(base)), key=lambda x: -len(x[0])):
        if d != lp(base) and not os.listdir(d):
            os.rmdir(d)
    if os.path.isdir(old_dir) and not recycle(old_dir, log=log):
        trash = os.path.join(root, "_Старое после обновления " + stamp)
        os.replace(old_dir, trash)
    log("Убрано файлов: %d" % len(files))
    return len(files)


def cleanup_after_update():
    """Хвосты прошлого обновления: старый exe, временные файлы, старые версии после прерванной раскладки."""
    for p in [exe_path() + ".old"] + [os.path.join(APP_DIR, n + ".old") for n in OLD_EXE_NAMES]:
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    try:
        for name in os.listdir(UPD_DIR):
            full = os.path.join(UPD_DIR, name)
            if name.startswith("new_") or name == "new":
                shutil.rmtree(lp(full), ignore_errors=True)
            elif name.startswith("old_") and os.path.isdir(full) and not recycle(full, tries=1):
                os.replace(full, os.path.join(ROOT, "_Старое после обновления " + name[4:]))
            elif name.startswith("dl") or name == "archives":
                for d, _dirs, fs in os.walk(full):
                    for f in fs:
                        if f.endswith(".part"):
                            try:
                                os.remove(os.path.join(d, f))
                            except OSError:
                                pass
    except OSError:
        pass


def lists_cached(man, root=ROOT):
    """Есть ли у нас список файлов каталога этой версии (его кладут обновление и выпуск версии)."""
    core = manifest_item(man, "core")
    return bool(core) and os.path.isfile(os.path.join(root, "_update", "lists", core["list"]))


def update_available(remote, local=None):
    local = local if local is not None else load_local_manifest()
    return vtuple(remote.get("version")) > vtuple(local.get("version", "0")) or not local.get("items")


def fmt_mb(n):
    mb = n / 1048576
    if mb >= 1024:
        return "%.1f ГБ" % (mb / 1024)
    return "%.0f МБ" % max(1, mb) if mb >= 1 else "меньше 1 МБ"

# ---------- конструктор сборок (Modrinth) ----------
#
# Поиск любых модов, шейдеров и текстур на Modrinth под выбранную версию игры и загрузчик.
# Обязательные зависимости подбираются сами (рекурсивно), несовместимые моды показываются заранее.
# Готовая сборка сохраняется в библиотеку как обычная: <Загрузчик> <версия>\<название> (N)\mods + pack.json.

MR_API = "https://api.modrinth.com/v2/"
MR_UA = {"User-Agent": "SKUF666/portalis (github.com/SKUF666/minecraft-packs)"}
LOADER_TITLES = {"fabric": "Fabric", "quilt": "Quilt", "forge": "Forge", "neoforge": "NeoForge"}
MR_CATEGORIES = [("optimization", "Оптимизация"), ("utility", "Удобства"), ("adventure", "Приключения"),
                 ("magic", "Магия"), ("technology", "Техника"), ("mobs", "Мобы"), ("worldgen", "Генерация мира"),
                 ("equipment", "Снаряжение"), ("decoration", "Декор"), ("food", "Еда"), ("storage", "Хранение"),
                 ("transportation", "Транспорт"), ("game-mechanics", "Механики"), ("library", "Библиотеки")]
MR_TYPES = [("mod", "Моды"), ("shader", "Шейдеры"), ("resourcepack", "Текстуры")]
TYPE_DIRS = {"mod": "mods", "shader": "shaderpacks", "resourcepack": "resourcepacks"}
# Шейдерам нужен загрузчик шейдеров: его конструктор добавляет сам.
SHADER_LOADERS = {"fabric": ["iris"], "quilt": ["iris"], "neoforge": ["iris"], "forge": ["oculus"]}


def mr_get(path, params=None):
    url = MR_API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    return json.loads(_get(url, 30, 4, MR_UA).decode("utf-8"))


def mr_game_versions():
    """Релизные версии игры, для которых на Modrinth есть моды (новые сверху)."""
    try:
        return [t["version"] for t in mr_get("tag/game_version") if t["version_type"] == "release"]
    except Exception:
        return ["26.2", "26.1.2", "1.21.8", "1.21.1", "1.20.1", "1.19.2", "1.18.2", "1.16.5", "1.12.2"]


def mr_search(query, gv, loader, ptype="mod", category=None, index="downloads", offset=0, limit=20):
    facets = [["project_type:%s" % ptype], ["versions:%s" % gv]]
    if ptype == "mod":
        facets.append(["categories:%s" % loader] + (["categories:fabric"] if loader == "quilt" else []))
    if category:
        facets.append(["categories:%s" % category])
    r = mr_get("search", {"query": query or "", "facets": json.dumps(facets), "index": index,
                          "offset": offset, "limit": limit})
    return r["hits"], r["total_hits"]


def _loaders_for(loader, ptype):
    if ptype == "mod":
        return [loader] + (["fabric"] if loader == "quilt" else [])
    if ptype == "shader":
        return None  # у шейдеров «загрузчик» - iris/optifine/canvas, фильтровать по нему не нужно
    return None


def pick_version(project_id, gv, loader, ptype="mod"):
    """Лучшая версия проекта для версии игры и загрузчика: сначала релизы, потом беты, свежие выше."""
    params = {"game_versions": json.dumps([gv])}
    lds = _loaders_for(loader, ptype)
    if lds:
        params["loaders"] = json.dumps(lds)
    vs = mr_get("project/%s/version" % project_id, params)
    if not vs:
        return None
    rank = {"release": 0, "beta": 1, "alpha": 2}
    vs.sort(key=lambda v: v.get("date_published", ""), reverse=True)
    vs.sort(key=lambda v: rank.get(v.get("version_type"), 3))
    return vs[0]


def resolve_build(selection, gv, loader, log=print):
    """selection: [{project_id, title, type}]. Возвращает (список файлов сборки, список проблем).
    Обязательные зависимости добавляются рекурсивно; несовместимые пары и моды без версии - в проблемы."""
    chosen, problems, incompat = {}, [], []
    queue = [(dict(p), None) for p in selection]
    if any(p.get("type") == "shader" for p in selection):
        for slug in SHADER_LOADERS.get(loader, []):
            queue.append(({"project_id": slug, "title": None, "type": "mod"}, "шейдеры"))
    seen_slugs = set()
    while queue:
        proj, reason = queue.pop(0)
        pid = proj["project_id"]
        if pid in chosen or pid in seen_slugs:
            continue
        seen_slugs.add(pid)
        log("Проверяю: %s" % (proj.get("title") or pid))
        try:
            v = pick_version(pid, gv, loader, proj.get("type", "mod"))
        except Exception as e:
            problems.append("«%s»: не удалось узнать версии (%s)" % (proj.get("title") or pid, str(e)[:60]))
            continue
        if not v:
            problems.append("«%s» нет для %s %s — пропущен" % (proj.get("title") or pid, LOADER_TITLES[loader], gv)
                            + ("" if not reason else " (нужен для «%s»)" % reason))
            continue
        real = v["project_id"]
        if real in chosen:
            continue
        f = next((x for x in v["files"] if x.get("primary")), v["files"][0])
        chosen[real] = {"project_id": real, "title": proj.get("title"), "type": proj.get("type", "mod"),
                        "version_id": v["id"], "version": v["version_number"], "file": f["filename"], "url": f["url"],
                        "sha1": f["hashes"]["sha1"], "sha512": f["hashes"].get("sha512"), "size": f["size"],
                        "reason": reason, "picked": reason is None}
        for d in v.get("dependencies", []):
            dpid = d.get("project_id")
            if not dpid and d.get("version_id"):
                try:
                    dpid = mr_get("version/%s" % d["version_id"])["project_id"]
                except Exception:
                    dpid = None
            if not dpid:
                continue
            if d["dependency_type"] == "required":
                queue.append(({"project_id": dpid, "title": None, "type": "mod"},
                              proj.get("title") or real))
            elif d["dependency_type"] == "incompatible":
                incompat.append((real, dpid))
    # Названия и категории (библиотеки отдельно).
    ids = list(chosen)
    for i in range(0, len(ids), 80):
        try:
            for pr in mr_get("projects", {"ids": json.dumps(ids[i:i + 80])}):
                c = chosen.get(pr["id"])
                if c:
                    c["title"] = c["title"] or pr["title"]
                    c["slug"] = pr.get("slug")
                    c["desc"] = pr.get("description", "")
                    c["icon"] = pr.get("icon_url")
                    c["categories"] = pr.get("categories", [])
                    c["client_side"], c["server_side"] = pr.get("client_side"), pr.get("server_side")
        except Exception:
            pass
    for c in chosen.values():
        if c.get("reason") and c["reason"] in chosen:
            c["reason"] = chosen[c["reason"]]["title"]
    for a, b in incompat:
        if b in chosen:
            problems.append("«%s» несовместим с «%s»: оставь что-то одно" % (chosen[a]["title"], chosen[b]["title"]))
    return list(chosen.values()), problems


def loader_version(loader, gv):
    """Свежая стабильная версия загрузчика для версии игры."""
    if loader == "fabric":
        lst = json.loads(_get("https://meta.fabricmc.net/v2/versions/loader/%s" % gv).decode("utf-8"))
        st = [x for x in lst if x["loader"].get("stable")] or lst
        return st[0]["loader"]["version"]
    if loader == "quilt":
        lst = json.loads(_get("https://meta.quiltmc.org/v3/versions/loader/%s" % gv).decode("utf-8"))
        return lst[0]["loader"]["version"]
    if loader == "forge":
        pr = json.loads(_get("https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json").decode("utf-8"))
        v = pr["promos"].get("%s-recommended" % gv) or pr["promos"].get("%s-latest" % gv)
        if not v:
            raise RuntimeError("для %s нет Forge" % gv)
        return v
    if loader == "neoforge":
        vs = json.loads(_get("https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge").decode("utf-8"))["versions"]
        parts = gv.split(".")
        pre = ".".join(parts[1:]) + "." if parts[0] == "1" else gv + "."  # 1.21.1 -> 21.1.x, 26.1.2 -> 26.1.2.x
        cand = [v for v in vs if v.startswith(pre)]
        stable = [v for v in cand if "beta" not in v and "alpha" not in v]
        if not (stable or cand):
            raise RuntimeError("для %s нет NeoForge" % gv)
        return (stable or cand)[-1]
    raise RuntimeError("неизвестный загрузчик " + loader)


def _safe_name(name):
    name = re.sub(r'[\\/:*?"<>|]', "", name).strip().rstrip(".")
    return name[:60] or "Моя сборка"


def _cover_from_icon(url, folder):
    """Обложка сборки из значка первого мода (Modrinth отдаёт webp - переводим в PNG)."""
    try:
        data = _get(url, 30, 3, MR_UA)
        im = PILImage.open(io.BytesIO(data)).convert("RGBA")
        for name, size in (("cover.png", 120), ("cover_big.png", 220)):
            bg = PILImage.new("RGBA", (size, size), (36, 38, 45, 255))
            ic = im.resize((size, size), PILImage.NEAREST if im.width <= 64 else PILImage.LANCZOS)
            bg.alpha_composite(ic)
            bg.save(os.path.join(folder, name))
        return True
    except Exception:
        return False


def save_build(name, gv, loader, files, selection, description="", replace=False, log=print, progress=None, cancel=None):
    """Скачивает файлы сборки с Modrinth (с проверкой sha1) и сохраняет её в библиотеку. Возвращает папку."""
    name = _safe_name(name)
    n_mods_count = sum(1 for f in files if f["type"] == "mod")
    vdir = "%s %s" % (LOADER_TITLES[loader], gv)
    folder = os.path.join(ROOT, vdir, "%s (%d)" % (name, n_mods_count))
    old = [p for p in find_packs() if p.get("name", "").rsplit(" (", 1)[0] == name and p["version_dir"] == vdir]
    if old and not replace:
        raise RuntimeError("Сборка «%s» для %s уже есть. Выбери другое название." % (name, vdir))
    log("Узнаю версию %s для %s" % (LOADER_TITLES[loader], gv))
    lv = loader_version(loader, gv)
    tmp = folder + ".tmp"
    shutil.rmtree(lp(tmp), ignore_errors=True)
    os.makedirs(tmp)
    total = max(1, sum(f["size"] for f in files))
    done = [0]

    def tick(n):
        done[0] += n
        if progress:
            progress(done[0], total)
    mr_files = {}
    for i, f in enumerate(files, 1):
        sub = TYPE_DIRS.get(f["type"], "mods")
        log("Скачиваю %d из %d: %s" % (i, len(files), f["title"] or f["file"]))
        _fetch_to(f["url"], os.path.join(tmp, sub, f["file"]), tick, f["sha1"], cancel, MR_UA)
        mr_files["%s/%s" % (sub, f["file"])] = f["url"]
    if loader in ("fabric", "quilt"):
        meta = ("https://meta.fabricmc.net/v2/versions/loader/%s/%s/profile/json" if loader == "fabric"
                else "https://meta.quiltmc.org/v3/versions/loader/%s/%s/profile/json") % (gv, lv)
        prof = json.loads(_get(meta).decode("utf-8"))
        tl = prof["id"]
        os.makedirs(os.path.join(tmp, "versions", tl), exist_ok=True)
        with open(os.path.join(tmp, "versions", tl, tl + ".json"), "w", encoding="utf-8") as fh:
            json.dump(prof, fh, indent=1)
    else:
        tl = "%s %s" % (LOADER_TITLES[loader], gv)  # так версии с Forge и NeoForge называет TLauncher
    picked = [f for f in files if f.get("picked")]
    icon = next((f.get("icon") for f in picked if f.get("icon")), None) or next((f.get("icon") for f in files if f.get("icon")), None)
    if not (icon and _cover_from_icon(icon, tmp)):
        try:
            shutil.copy2(os.path.join(ROOT, "Оформление", "tab_packs_64.png"), os.path.join(tmp, "cover.png"))
        except OSError:
            pass
    cats = dict(MR_CATEGORIES)
    mods = []
    for f in files:
        cat = "Библиотеки" if "library" in (f.get("categories") or []) or (f.get("reason") and not f.get("picked")) else \
            next((cats[c] for c in (f.get("categories") or []) if c in cats), "Моды")
        if f["type"] != "mod":
            cat = "Шейдеры" if f["type"] == "shader" else "Текстур-паки"
        ru = f.get("desc", "")
        if f.get("reason") and not f.get("picked"):
            ru = ("Нужен для «%s». " % f["reason"]) + ru
        mods.append({"name": f["title"] or f["file"], "version": f["version"], "file": f["file"], "cat": cat, "ru": ru,
                     "mb": round(f["size"] / 1048576, 1)})
    order = [c for _k, c in MR_CATEGORIES if c != "Библиотеки"] + ["Моды", "Шейдеры", "Текстур-паки", "Библиотеки"]
    with open(os.path.join(tmp, "mods.json"), "w", encoding="utf-8") as fh:
        json.dump({"order": [c for c in order if any(m["cat"] == c for m in mods)], "mods": mods}, fh,
                  ensure_ascii=False, indent=1)
    pj = {"name": "%s (%d)" % (name, n_mods_count), "loader": LOADER_TITLES[loader], "minecraft": gv, "tl_version": tl,
          "loader_version": lv, "description": description or "Своя сборка из конструктора: %s." % n_mods(n_mods_count),
          "is_pack": True, "user": True, "mr_files": mr_files,
          "constructor": {"loader": loader, "gv": gv, "projects": [{"project_id": f["project_id"], "title": f["title"],
                                                                    "type": f["type"], "icon": f.get("icon")} for f in picked]}}
    with open(os.path.join(tmp, "pack.json"), "w", encoding="utf-8") as fh:
        json.dump(pj, fh, ensure_ascii=False, indent=1)
    if loader in ("fabric", "quilt"):
        try:
            ensure_vanilla(gv, log)
        except Exception as e:
            log("Minecraft %s скачается лаунчером при запуске (%s)" % (gv, str(e)[:60]))
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    for p in old:  # прежняя версия этой сборки - в Корзину
        dst = os.path.join(ROOT, "_update", "old_" + stamp, os.path.basename(p["path"]))
        _move(p["path"], dst)
    os.replace(lp(tmp), lp(folder))
    if old:
        cleanup_after_update()
    log("Сборка «%s» сохранена: %s, %s" % (name, vdir, n_mods(n_mods_count)))
    return folder


def delete_user_pack(pack, log=print):
    """Своя сборка из конструктора - в Корзину (сборки из каталога так не удаляются)."""
    if not pack.get("user"):
        raise RuntimeError("Это сборка из каталога: её можно только «Удалить скачанное».")
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    _move(pack["path"], os.path.join(ROOT, "_update", "old_" + stamp, os.path.basename(pack["path"])))
    cleanup_after_update()
    log("Сборка «%s» убрана в Корзину" % pack["name"])


# ---------- скины и плащи ----------
#
# Скин по нику ищется сразу в трёх местах: TLauncher, Ely.by (им пользуется Legacy Launcher) и Mojang (лицензия).
# «Мои скины» - PNG в папке «Скины» (+ skins.json: модель руки и плащ). Стандартные скины берутся из файла игры,
# галерея - с MineSkin (скины, загруженные игроками). Носить свой скин и плащ без интернета помогает мод
# CustomSkinLoader: программа кладёт его в mods и раскладывает файлы в .minecraft\CustomSkinLoader\LocalSkin.

SKINS_DIR = os.path.join(ROOT, "Скины")
CSL = {"name": "CustomSkinLoader_Universal-15.0.1.jar",
       "url": "https://cdn.modrinth.com/data/idMHQ4n2/versions/OLaesh5y/CustomSkinLoader_Universal-15.0.1.jar",
       "sha1": "ab8dd841cafc3b4ecbf0250d3f5aee8a1fdb506a"}
SKIN_UA = {"User-Agent": "Portalis/1.0 (+https://github.com/SKUF666/minecraft-packs)"}


def _get_json_or_none(url):
    try:
        data = _get(url, 15, 2, SKIN_UA)
    except Exception:
        return None
    if not data.strip():
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:
        return None


def _first_cape_frame(png, cape_height=None):
    """Анимированный плащ TLauncher - вертикальная полоса кадров; берём первый кадр."""
    try:
        im = PILImage.open(io.BytesIO(png))
        if cape_height and im.height > cape_height:
            im = im.crop((0, 0, im.width, cape_height))
            out = io.BytesIO()
            im.save(out, "PNG")
            return out.getvalue()
    except Exception:
        pass
    return png


def skin_lookup(nick):
    """Скины и плащи игрока по нику: [{source, skin(bytes), cape(bytes|None), slim}]."""
    nick = nick.strip()
    out = []

    def tl():
        j = _get_json_or_none("https://auth.tlauncher.org/skin/v1/profile/texture/login/" + urllib.parse.quote(nick))
        if not j or "SKIN" not in j:
            return None
        def full(u):
            return u if u.startswith("http") else "https://auth.tlauncher.org/" + u.lstrip("/")
        skin = _get(full(j["SKIN"]["url"]), 20, 2, SKIN_UA)
        cape = None
        if j.get("CAPE"):
            try:
                cape = _first_cape_frame(_get(full(j["CAPE"]["url"]), 20, 2, SKIN_UA), j["CAPE"].get("capeHeight"))
            except Exception:
                cape = None
        return {"source": "TLauncher", "skin": skin, "cape": cape,
                "slim": (j["SKIN"].get("metadata") or {}).get("model") == "slim"}

    def ely():
        j = _get_json_or_none("https://skinsystem.ely.by/textures/" + urllib.parse.quote(nick))
        if not j or "SKIN" not in j or "textures.minecraft.net" in j["SKIN"]["url"]:
            return None  # Ely.by для незнакомых ников отдаёт скины Mojang - их покажет источник Mojang
        skin = _get(j["SKIN"]["url"], 20, 2, SKIN_UA)
        cape = _get(j["CAPE"]["url"], 20, 2, SKIN_UA) if j.get("CAPE") else None
        return {"source": "Ely.by", "skin": skin, "cape": cape,
                "slim": (j["SKIN"].get("metadata") or {}).get("model") == "slim"}

    def mojang():
        j = _get_json_or_none("https://api.mojang.com/users/profiles/minecraft/" + urllib.parse.quote(nick))
        if not j or "id" not in j:
            return None
        prof = _get_json_or_none("https://sessionserver.mojang.com/session/minecraft/profile/" + j["id"])
        if not prof:
            return None
        tex = json.loads(base64.b64decode(prof["properties"][0]["value"]).decode("utf-8"))["textures"]
        if "SKIN" not in tex:
            return None
        skin = _get(tex["SKIN"]["url"].replace("http://", "https://"), 20, 2, SKIN_UA)
        cape = _get(tex["CAPE"]["url"].replace("http://", "https://"), 20, 2, SKIN_UA) if tex.get("CAPE") else None
        return {"source": "Mojang (лицензия)", "skin": skin, "cape": cape,
                "slim": (tex["SKIN"].get("metadata") or {}).get("model") == "slim"}

    def safe(f):
        try:
            return f()
        except Exception:
            return None
    with concurrent.futures.ThreadPoolExecutor(3) as ex:
        out = [r for r in ex.map(safe, (tl, ely, mojang)) if r]
    return out


def skin_is_slim(im):
    """Тонкие руки (Alex): у них прозрачна полоска справа от руки на развёртке."""
    k = im.width // 64
    try:
        return im.height == im.width and im.getpixel((54 * k, 20 * k))[3] == 0
    except Exception:
        return False


def render_skin(png, slim=None, scale=6):
    """Превью скина спереди и сзади (как в игре, с вторым слоем). Возвращает PIL-картинку RGBA."""
    im = PILImage.open(io.BytesIO(png)).convert("RGBA")
    k = max(1, im.width // 64)
    legacy = im.height * 2 == im.width
    if slim is None:
        slim = skin_is_slim(im)
    aw = 3 if slim else 4

    def part(x, y, w, h, flip=False):
        p = im.crop((x * k, y * k, (x + w) * k, (y + h) * k))
        return p.transpose(PILImage.FLIP_LEFT_RIGHT) if flip else p

    def face(front):
        c = PILImage.new("RGBA", (16 * k, 32 * k), (0, 0, 0, 0))
        f = 0 if front else 1

        def put(img, x, y):
            c.alpha_composite(img, (x * k, y * k))
        # (передняя, задняя) развёртки
        head = [(8, 8), (24, 8)][f]
        hat = [(40, 8), (56, 8)][f]
        body = [(20, 20), (32, 20)][f]
        jacket = [(20, 36), (32, 36)][f]
        rarm = [(44, 20), (44 + aw + 4, 20)][f]
        rsleeve = [(44, 36), (44 + aw + 4, 36)][f]
        rleg = [(4, 20), (12, 20)][f]
        rpants = [(4, 36), (12, 36)][f]
        larm = [(36, 52), (36 + aw + 4, 52)][f]
        lsleeve = [(52, 52), (52 + aw + 4, 52)][f]
        lleg = [(20, 52), (28, 52)][f]
        lpants = [(4, 52), (12, 52)][f]
        # спереди правая рука игрока слева от зрителя, сзади - наоборот
        rx, lx = (4 - aw, 12) if front else (12, 4 - aw)
        rlx, llx = (4, 8) if front else (8, 4)
        put(part(*head, 8, 8), 4, 0)
        put(part(*body, 8, 12), 4, 8)
        put(part(*rarm, aw, 12), rx, 8)
        put(part(*rleg, 4, 12), rlx, 20)
        if legacy:
            put(part(*rarm, aw, 12, True), lx, 8)
            put(part(*rleg, 4, 12, True), llx, 20)
            hatimg = part(*hat, 8, 8)
            if hatimg.getextrema()[3][0] < 255:  # в старых скинах полностью непрозрачная «шапка» не рисуется
                put(hatimg, 4, 0)
        else:
            put(part(*larm, aw, 12), lx, 8)
            put(part(*lleg, 4, 12), llx, 20)
            for src, w, h, x, y in ((hat, 8, 8, 4, 0), (jacket, 8, 12, 4, 8), (rsleeve, aw, 12, rx, 8),
                                    (lsleeve, aw, 12, lx, 8), (rpants, 4, 12, rlx, 20), (lpants, 4, 12, llx, 20)):
                put(part(*src, w, h), x, y)
        return c
    a, b = face(True), face(False)
    out = PILImage.new("RGBA", (36 * k, 32 * k), (0, 0, 0, 0))
    out.alpha_composite(a, (0, 0))
    out.alpha_composite(b, (20 * k, 0))
    z = max(1, scale // k)
    return out.resize((out.width * z, out.height * z), PILImage.NEAREST)


def render_cape(png, scale=6):
    """Внешняя сторона плаща (как видно со спины)."""
    im = PILImage.open(io.BytesIO(png)).convert("RGBA")
    if im.width % 64 == 0 and im.height * 2 == im.width:
        k = im.width // 64
    else:  # HD-плащи TLauncher: только развёртка 22x17 в масштабе
        k = max(1, im.width // 22)
    c = im.crop((1 * k, 1 * k, 11 * k, 17 * k))
    z = max(1, scale // k)
    return c.resize((c.width * z, c.height * z), PILImage.NEAREST)


def make_cape(c1, c2, pattern="полосы", emblem=None):
    """Плащ 64x32 для CustomSkinLoader: два цвета и узор. Возвращает PNG (bytes)."""
    im = PILImage.new("RGBA", (64, 32), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    col1 = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5)) + (255,)
    col2 = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5)) + (255,)
    edge = tuple(max(0, v - 50) for v in col1[:3]) + (255,)
    # вся развёртка плаща (22x17) - основной цвет, края темнее
    d.rectangle((0, 0, 21, 16), fill=col1)
    for x0 in (1, 12):  # внешняя и внутренняя стороны 10x16
        for y in range(16):
            for x in range(10):
                c = col1
                if pattern == "полосы" and (y // 2) % 2:
                    c = col2
                elif pattern == "шахматка" and (x // 2 + y // 2) % 2:
                    c = col2
                elif pattern == "градиент":
                    t = y / 15.0
                    c = tuple(int(col1[i] + (col2[i] - col1[i]) * t) for i in range(3)) + (255,)
                elif pattern == "кайма" and (x in (0, 9) or y in (0, 15)):
                    c = col2
                elif pattern == "звёзды" and (x, y) in ((2, 3), (6, 5), (3, 9), (7, 11), (5, 14), (8, 2), (1, 13)):
                    c = col2
                im.putpixel((x0 + x, 1 + y), c)
    d.line((0, 1, 0, 16), fill=edge)
    d.line((11, 1, 11, 16), fill=edge)
    d.line((1, 0, 10, 0), fill=edge)
    if emblem:
        try:
            em = PILImage.open(emblem).convert("RGBA").resize((6, 6), PILImage.LANCZOS)
            im.alpha_composite(em, (3, 6))
        except Exception:
            pass
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def _versions_jars():
    vroot = os.path.join(MC, "versions")
    out = []
    for v in _listdir(vroot):
        j = os.path.join(vroot, v, v + ".jar")
        if os.path.isfile(j):
            out.append(j)
    return out


def default_skins():
    """Стандартные скины Minecraft из файла игры (Steve, Alex и ещё семь, в двух вариантах рук)."""
    for j in sorted(_versions_jars(), reverse=True):
        try:
            with zipfile.ZipFile(j) as z:
                names = [n for n in z.namelist() if re.match(r"assets/minecraft/textures/entity/player/(wide|slim)/\w+\.png$", n)]
                if names:
                    res = []
                    for n in sorted(names):
                        kind, file = n.rsplit("/", 2)[-2:]
                        res.append({"name": file[:-4].capitalize(), "slim": kind == "slim", "skin": z.read(n),
                                    "source": "стандартный"})
                    return res
        except Exception:
            continue
    return []


def mineskin_popular(after=None, size=24):
    """Популярные скины с MineSkin (их загружают игроки). Возвращает (список, курсор следующей страницы)."""
    q = {"size": size}
    if after:
        q["after"] = after
    j = json.loads(_get("https://api.mineskin.org/v2/skins?" + urllib.parse.urlencode(q), 20, 3, SKIN_UA).decode("utf-8"))
    items = [{"name": s.get("name") or "без названия", "texture": s.get("texture"), "uuid": s.get("uuid")}
             for s in j.get("skins", []) if s.get("texture")]
    nxt = (((j.get("pagination") or {}).get("next") or {}).get("after"))
    return items, nxt


def texture_png(texture_hash):
    return _get("https://textures.minecraft.net/texture/" + texture_hash, 20, 3, SKIN_UA)


def _skins_meta():
    try:
        with open(os.path.join(SKINS_DIR, "skins.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _skins_meta_save(meta):
    os.makedirs(SKINS_DIR, exist_ok=True)
    with open(os.path.join(SKINS_DIR, "skins.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)


def save_my_skin(name, skin, slim=None, cape=None, source=""):
    """Сохраняет скин (и плащ) в «Мои скины». Возвращает имя файла."""
    os.makedirs(SKINS_DIR, exist_ok=True)
    base = _safe_name(name) or "Скин"
    fn, i = base + ".png", 2
    while os.path.exists(os.path.join(SKINS_DIR, fn)):
        fn, i = "%s (%d).png" % (base, i), i + 1
    with open(os.path.join(SKINS_DIR, fn), "wb") as fh:
        fh.write(skin)
    meta = _skins_meta()
    if slim is None:
        slim = skin_is_slim(PILImage.open(io.BytesIO(skin)).convert("RGBA"))
    entry = {"slim": bool(slim), "source": source}
    if cape:
        cf = fn[:-4] + " - плащ.png"
        with open(os.path.join(SKINS_DIR, cf), "wb") as fh:
            fh.write(cape)
        entry["cape"] = cf
    meta[fn] = entry
    _skins_meta_save(meta)
    return fn


def my_skins():
    meta = _skins_meta()
    capes = {e.get("cape") for e in meta.values() if e.get("cape")}
    out = []
    for f in _listdir(SKINS_DIR):
        if f.lower().endswith(".png") and f not in capes and not f.startswith("_"):
            e = meta.get(f, {})
            out.append({"file": f, "name": f[:-4], "slim": e.get("slim"), "cape": e.get("cape"), "source": e.get("source", "")})
    return out


def delete_my_skin(fn):
    meta = _skins_meta()
    e = meta.pop(fn, {})
    for f in (fn, e.get("cape")):
        if f and os.path.isfile(os.path.join(SKINS_DIR, f)):
            if not recycle(os.path.join(SKINS_DIR, f), tries=1):
                os.remove(os.path.join(SKINS_DIR, f))
    _skins_meta_save(meta)


def csl_jar():
    """CustomSkinLoader (один файл для Fabric, Forge и NeoForge всех наших версий) - из кэша или с Modrinth."""
    p = os.path.join(ROOT, "_update", "extra", CSL["name"])
    if not (os.path.isfile(p) and _sha1_file(p) == CSL["sha1"]):
        _fetch_to(CSL["url"], p, None, CSL["sha1"], None, MR_UA)
    return p


def install_offline_skin(nick, skin_file, cape_file=None, log=print):
    """Скин (и плащ) для ника без интернета: файлы в CustomSkinLoader\\LocalSkin, локальные - первыми в списке."""
    base = os.path.join(MC, "CustomSkinLoader")
    for sub, src in (("skins", skin_file), ("capes", cape_file)):
        if src:
            os.makedirs(os.path.join(base, "LocalSkin", sub), exist_ok=True)
            shutil.copy2(src, os.path.join(base, "LocalSkin", sub, nick + ".png"))
    cfg_path = os.path.join(base, "CustomSkinLoader.json")
    try:
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:
        cfg = {"enableCape": True, "loadlist": []}
    local = {"name": "LocalSkin", "type": "Legacy", "checkPNG": False, "skin": "LocalSkin/skins/{USERNAME}.png",
             "model": "auto", "cape": "LocalSkin/capes/{USERNAME}.png", "elytra": "LocalSkin/elytras/{USERNAME}.png"}
    cfg["loadlist"] = [local] + [e for e in cfg.get("loadlist", []) if e.get("name") != "LocalSkin"]
    cfg["enableCape"] = True
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    s = load_settings()
    s["offline_skins"] = True
    s["skin_nick"] = nick
    save_settings(s)
    put_csl_in_mods(log)
    log("Скин для «%s» поставлен в папку CustomSkinLoader" % nick)


def put_csl_in_mods(log=print):
    """Если включены офлайн-скины и сейчас включена сборка с Fabric/Forge, кладём мод CustomSkinLoader в mods."""
    if not load_settings().get("offline_skins"):
        return False
    cur = current() or {}
    if cur.get("version_dir", "").split(" ")[0] not in ("Fabric", "Forge", "NeoForge", "Quilt"):
        log("Скин заработает, когда включишь сборку с модами (Fabric или Forge): без модов игра свои скины не грузит.")
        return False
    os.makedirs(MODS, exist_ok=True)
    if not os.path.isfile(os.path.join(MODS, CSL["name"])):
        shutil.copy2(csl_jar(), os.path.join(MODS, CSL["name"]))
        log("В сборку добавлен мод CustomSkinLoader (нужен для своих скинов без интернета)")
    return True


# ---------- командная строка ----------

def cli(argv):
    out = open(os.path.join(ROOT, "switcher_cli.log"), "a", encoding="utf-8")

    def log(*a):
        print(*a, file=out, flush=True)

    packs = find_packs()
    cmd = argv[0]
    if cmd == "--list":
        for p in packs:
            log("%s | %s | %s | %s" % (p["version_dir"], p["name"], n_mods(p["count"]), p.get("tl_version")))
    elif cmd == "--maps":
        for m in find_maps():
            log("%s | %s | %s | %s | установлена: %s" % (m["id"], m["title"], m["version"], m["genre"], map_installed(m)))
    elif cmd == "--current":
        log(current(), "| TLauncher:", get_tlauncher_version())
    elif cmd == "--servers":
        merge_servers(log)
    elif cmd == "--vpn":
        log(vpn_addresses(), "| роутер:", lan_ip(), "| порт открытой игры:", lan_port())
    elif cmd == "--check":
        for text, good in describe_checks(network_checks(argv[1] if len(argv) > 1 else "Hamachi")):
            log({True: "OK ", False: "ПЛОХО ", None: "ПРОВЕРЬ "}[good] + text)
    elif cmd == "--host":
        try:
            h = host_setup(argv[1] if len(argv) > 1 else None)
            log("Адрес: %s | код: %s | версия: %s | сборка: %s" % (h["address"], h["code"], h["tl"], h["pack"]))
        except Exception as e:
            log("Ошибка:", e); return 1
    elif cmd in ("--check-update", "--update", "--download", "--remove"):
        remote = fetch_remote_manifest()
        log("Версия у меня: %s, на GitHub: %s" % (load_local_manifest().get("version"), remote.get("version")))
        if cmd in ("--download", "--remove"):
            key = argv[1] if len(argv) > 1 else ""
            ids = [i["id"] for i in remote.get("items", []) if i["kind"] in ("pack", "map") and (
                key == "all" or i["id"] == key or i["id"].endswith(":" + key) or key.lower() in i["title"].lower())]
            if not ids:
                log("Не нашёл карту или сборку:", key); return 1
            if cmd == "--remove":
                for i in ids:
                    remove_item(manifest_item(remote, i), log=log)
                return 0
            plan = plan_download(remote, ids, log=lambda *a: None)
        else:
            plan = plan_update(remote, log=lambda *a: None)
        log("Скачать файлов: %d (%s), откуда: %s, убрать: %d" % (
            len(plan["need"]), fmt_mb(plan["size"]), ", ".join(plan_sources(remote, plan)) or "-",
            len(plan["extra"]) + len(plan["remove"])))
        for p in plan["extra"] + plan["remove"]:
            log("  убрать: " + p)
        if cmd != "--check-update" and (plan["need"] or plan["extra"] or plan["remove"] or plan.get("with_update")):
            def ask(a, err):
                log("Сайт не отдал %s (%s). Открываю страницу, скачай файл — программа подхватит его из «Загрузок»."
                    % (a["name"], err or "только через браузер"))
                webbrowser.open(a["page"])
                return wait_archive_in_downloads(a)
            res = apply_plan(remote, plan, log=log, ask_browser=ask)
            log("Готово:", res)
    elif cmd == "--shortcut":
        log("Ярлыки:", create_shortcuts())
    elif cmd == "--join" and len(argv) > 1:
        join_friend(argv[1], packs, log)
    elif cmd == "--switch" and len(argv) > 1:
        m = [p for p in packs if p["name"] == argv[1] or p["name"].startswith(argv[1])]
        if not m:
            log("Сборка не найдена:", argv[1]); return 1
        if game_running():
            log("Игра запущена. Закрой Minecraft."); return 2
        switch(m[0], packs, log=log)
        log("Готово:", m[0]["name"])
    elif cmd == "--play" and len(argv) > 1:
        key = argv[1].lower()
        mm = [m for m in find_maps() if m["id"] == key or m["title"].lower().startswith(key)]
        if not mm:
            log("Карта не найдена:", argv[1]); return 1
        m = mm[0]
        pack = None
        if "--pack" in argv:
            name = argv[argv.index("--pack") + 1]
            cand = [p for p in packs_for_map(m, packs) if p["name"].startswith(name)]
            if not cand:
                log("Сборка не подходит к карте или не найдена:", name); return 1
            pack = cand[0]
        elif "--nomods" not in argv and m.get("recommended"):
            pack = next((p for p in packs if p["name"] == m["recommended"]), None)
        if game_running():
            log("Игра запущена. Закрой Minecraft."); return 2
        ids = missing_items(m, pack, packs)
        if ids:
            remote = fetch_remote_manifest()
            plan = plan_download(remote, ids, log=lambda *a: None)
            log("Сначала скачаю: %s (%s)" % (", ".join(ids), fmt_mb(plan["size"])))

            def ask(a, err):
                log("Открываю страницу %s: скачай файл, программа подхватит его из «Загрузок»." % a["page"])
                webbrowser.open(a["page"])
                return wait_archive_in_downloads(a)
            apply_plan(remote, plan, log=log, ask_browser=ask)
            packs = find_packs()
            if pack:
                pack = next((p for p in packs if p["path"] == pack["path"]), pack)
        install_map(m, pack, "--fresh" in argv, packs, log)
        log("Готово:", m["title"])
    return 0


# ---------- окно ----------

BG = "#15161a"
PANEL = "#1d1f24"
CARD = "#24262d"
CARD_HI = "#2c2f37"
LINE = "#33363f"
TEXT = "#eceef3"
MUTED = "#9da2ae"
ACCENT = "#4caf50"
ACCENT_HI = "#5cc460"
BLUE = "#3b82c4"
GOLD = "#c9a227"
FONT = "Segoe UI"


ART = os.path.join(ROOT, "Оформление")
TAB_ORDER = ["maps", "packs", "builder", "skins", "servers", "friend", "launchers"]


def gui():
    import tkinter as tk
    from tkinter import messagebox, simpledialog, filedialog, colorchooser

    cleanup_after_update()
    tidy_old_name()
    win = tk.Tk()
    win.withdraw()
    win.title(APP_NAME + " — карты, сборки и серверы Minecraft")
    win.geometry("1080x760")
    win.minsize(1000, 660)
    win.configure(bg=BG)
    try:
        win.iconphoto(True, tk.PhotoImage(file=os.path.join(ROOT, "icon.png")))
    except Exception:
        pass
    images = []
    keep = []
    state = {"tab": "maps", "busy": False}
    settings = load_settings()

    def art(name, w=None, h=None):
        """Картинка из «Оформления» (или пустая заглушка, если файла нет)."""
        try:
            im = tk.PhotoImage(file=os.path.join(ART, name))
        except Exception:
            im = tk.PhotoImage(width=w or 1, height=h or 1)
        keep.append(im)
        return im

    # --- анимации ---
    anims = {}

    def animate(key, duration, step, done=None, ease=lambda t: 1 - (1 - t) ** 3):
        """step(k) получает 0..1 с замедлением к концу. Новая анимация с тем же ключом отменяет старую."""
        if key in anims:
            try:
                win.after_cancel(anims.pop(key))
            except Exception:
                pass
        t0 = time.perf_counter()

        def frame():
            t = min(1.0, (time.perf_counter() - t0) * 1000.0 / max(1, duration))
            try:
                step(ease(t))
            except tk.TclError:
                anims.pop(key, None)
                return
            if t < 1:
                anims[key] = win.after(14, frame)
            else:
                anims.pop(key, None)
                if done:
                    done()
        frame()

    def mix(c1, c2, k):
        a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
        return "#%02x%02x%02x" % tuple(int(x + (y - x) * k) for x, y in zip(a, b))

    def fade_color(w, opt, to, ms=150):
        try:
            cur = str(w.cget(opt))
        except tk.TclError:
            return
        if not (cur.startswith("#") and len(cur) == 7 and to.startswith("#")):
            w.configure(**{opt: to})
            return
        animate((str(w), opt), ms, lambda k: w.configure(**{opt: mix(cur, to, k)}))

    def pointer_inside(w):
        try:
            x, y = w.winfo_pointerxy()
            under = w.winfo_containing(x, y)
            return under is not None and str(under).startswith(str(w))
        except Exception:
            return False

    def fade_in_window(t, dy=18, ms=220):
        """Окно проявляется и чуть всплывает снизу."""
        try:
            t.attributes("-alpha", 0.0)
        except tk.TclError:
            return
        t.update_idletasks()
        geo = t.geometry()
        try:
            size, x, y = geo.split("+")[0], int(geo.split("+")[1]), int(geo.split("+")[2])
        except Exception:
            size = None

        def step(k):
            t.attributes("-alpha", k)
            if size:
                t.geometry("%s+%d+%d" % (size, x, y + int(dy * (1 - k))))
        animate(("win", str(t)), ms, step)

    # --- заставка ---
    splash = tk.Toplevel(win)
    splash.overrideredirect(True)
    splash.configure(bg=BG)
    try:
        splash.attributes("-alpha", 0.0)
        splash.attributes("-topmost", True)
    except tk.TclError:
        pass
    sp_img = art("splash.png", 640, 400)
    SW, SH = max(2, sp_img.width()), max(2, sp_img.height())
    if SW < 50:
        SW, SH = 640, 400
    scv = tk.Canvas(splash, width=SW, height=SH, bg=BG, highlightthickness=0, bd=0)
    scv.pack()
    scv.create_image(0, 0, image=sp_img, anchor="nw")
    for dx, dy, color in ((2, 2, "#000000"), (0, 0, "#ffffff")):
        scv.create_text(SW // 2 + dx, SH - 92 + dy, text="PORTALIS", font=(FONT, 30, "bold"), fill=color)
    scv.create_text(SW // 2 + 1, SH - 56 + 1, text="карты  ·  сборки  ·  серверы Minecraft", font=(FONT, 13), fill="#000000")
    scv.create_text(SW // 2, SH - 56, text="карты  ·  сборки  ·  серверы Minecraft", font=(FONT, 13), fill="#d7deea")
    scv.create_rectangle(SW // 2 - 120, SH - 26, SW // 2 + 120, SH - 22, fill="#2a2d35", width=0)
    sp_bar = scv.create_rectangle(SW // 2 - 120, SH - 26, SW // 2 - 120, SH - 22, fill=ACCENT_HI, width=0)
    splash.update_idletasks()
    splash.geometry("%dx%d+%d+%d" % (SW, SH, (splash.winfo_screenwidth() - SW) // 2,
                                     (splash.winfo_screenheight() - SH) // 2 - 40))
    splash_t0 = time.perf_counter()
    animate("splash_in", 260, lambda k: splash.attributes("-alpha", k))
    animate("splash_bar", 1100, lambda k: scv.coords(sp_bar, SW // 2 - 120, SH - 26, SW // 2 - 120 + 240 * k, SH - 22),
            ease=lambda t: t * t * (3 - 2 * t))
    splash.update()

    # --- шапка с картинкой ---
    HEAD_H = 150
    head = tk.Canvas(win, height=HEAD_H, bg=BG, highlightthickness=0, bd=0)
    head.pack(fill="x")
    banner = art("banner.png")
    BW = banner.width() if banner.width() > 10 else 0

    def px(img, x, y):
        try:
            v = img.get(x, y)
            if isinstance(v, str):
                v = tuple(int(c) for c in v.split())
            return "#%02x%02x%02x" % tuple(v[:3])
        except Exception:
            return BG
    # Слева от картинки (в широком окне) продолжаем небо цветами её левого края.
    for y in range(HEAD_H):
        head.create_line(0, y, 4000, y, fill=px(banner, 0, y) if BW else BG)
    banner_id = head.create_image(0, 0, image=banner, anchor="ne")
    for dx, dy, color in ((2, 2, "#000000"), (0, 0, "#ffffff")):
        head.create_text(108 + dx, 54 + dy, text="PORTALIS", font=(FONT, 26, "bold"), fill=color, anchor="w")
    head.create_text(111, 91, text="карты  ·  сборки  ·  серверы Minecraft", font=(FONT, 12), fill="#000000", anchor="w")
    head.create_text(110, 90, text="карты  ·  сборки  ·  серверы Minecraft", font=(FONT, 12), fill="#d7deea", anchor="w")
    head.create_image(22, HEAD_H // 2 - 4, image=art("appicon_72.png", 1, 1), anchor="w")
    rnd = random.Random(7)
    stars = []
    for _ in range(16):
        x, y = rnd.randint(240, 700), rnd.randint(5, 34)
        r = rnd.choice((1, 1, 1.5))
        stars.append((head.create_oval(x - r, y - r, x + r, y + r, fill="#ffffff", width=0), x, y,
                      rnd.random() * 6.28, 0.6 + rnd.random() * 1.4))

    def twinkle():
        """Звёзды над шапкой мерцают, растворяясь в цвете неба под ними."""
        if not head.winfo_exists():
            return
        t = time.perf_counter()
        off = head.winfo_width() - BW
        for sid, x, y, ph, sp in stars:
            under = px(banner, x - off, y) if BW and 0 <= x - off < BW else px(banner, 0, y) if BW else BG
            k = 0.5 + 0.5 * math.sin(t * sp + ph)
            head.itemconfigure(sid, fill=mix(under, "#ffffff", k * 0.9))
        win.after(70, twinkle)
    twinkle()

    now = tk.Frame(head, bg=PANEL, padx=14, pady=8)
    now_pack = tk.Label(now, font=(FONT, 10), fg=TEXT, bg=PANEL, anchor="e")
    now_pack.pack(anchor="e")
    now_ver = tk.Label(now, font=(FONT, 9), fg=MUTED, bg=PANEL, anchor="e")
    now_ver.pack(anchor="e")
    now_id = head.create_window(0, 18, window=now, anchor="ne")
    play_btn_box = tk.Frame(head, bg=PANEL)
    play_id = head.create_window(0, 0, window=play_btn_box, anchor="se")

    def place_head(e=None):
        w = head.winfo_width()
        head.coords(banner_id, w, 0)
        head.coords(now_id, w - 24, 16)
        head.coords(play_id, w - 24, HEAD_H - 14)
    head.bind("<Configure>", place_head)

    # --- вкладки с иконками и бегущей полоской ---
    tabs = tk.Frame(win, bg=BG)
    tabs.pack(fill="x", padx=24)
    tab_btns = {}
    tab_line = tk.Frame(tabs, bg=ACCENT, height=3)

    def tab_button(key, text, icon):
        b = tk.Label(tabs, text=" " + text, image=art(icon, 24, 24), compound="left", font=(FONT, 11, "bold"),
                     bg=BG, fg=MUTED, padx=9, pady=9, cursor="hand2")
        b.pack(side="left", padx=(0, 4), pady=(0, 3))
        b.bind("<Button-1>", lambda e: show(key))
        b.bind("<Enter>", lambda e: key != state["tab"] and fade_color(b, "fg", TEXT, 120))
        b.bind("<Leave>", lambda e: key != state["tab"] and fade_color(b, "fg", MUTED, 160))
        tab_btns[key] = b

    tab_button("maps", "Карты", "tab_maps.png")
    tab_button("packs", "Сборки", "tab_packs.png")
    tab_button("builder", "Конструктор", "tab_builder.png")
    tab_button("skins", "Скины", "tab_skins.png")
    tab_button("servers", "Серверы", "tab_servers.png")
    tab_button("friend", "С другом", "tab_friend.png")
    tab_button("launchers", "Лаунчеры", "tab_launchers.png")
    tk.Frame(win, height=1, bg=LINE).pack(fill="x", padx=24)

    def move_tab_line(key, instant=False):
        b = tab_btns[key]
        tabs.update_idletasks()
        x1, w1 = b.winfo_x(), b.winfo_width()
        y = b.winfo_y() + b.winfo_height()
        info = tab_line.place_info()
        x0, w0 = (int(info.get("x", x1)), int(info.get("width", w1))) if info else (x1, w1)
        if instant or not info:
            tab_line.place(x=x1, y=y, width=w1, height=3)
            return
        animate("tabline", 260, lambda k: tab_line.place(x=int(x0 + (x1 - x0) * k), y=y,
                                                          width=int(w0 + (w1 - w0) * k), height=3))

    # --- полоса «вышло обновление» (появляется при необходимости) ---
    upd_bar = tk.Frame(win, bg="#1d3524", highlightthickness=1, highlightbackground="#2f6b34")

    # --- прокручиваемая область ---
    body = tk.Frame(win, bg=BG)
    body.pack(fill="both", expand=True, padx=(24, 8), pady=12)
    canvas = tk.Canvas(body, bg=BG, highlightthickness=0, bd=0)
    sb = tk.Scrollbar(body, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    inner = tk.Frame(canvas, bg=BG)
    inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(inner_id, width=e.width))
    wheel = {}

    def on_wheel(e):
        """Плавная прокрутка колесом."""
        try:
            top = e.widget.winfo_toplevel()
        except Exception:
            return
        cv = getattr(top, "_cv", canvas)
        try:
            region = cv.bbox("all")
            total = (region[3] - region[1]) if region else 0
            view = cv.winfo_height()
        except tk.TclError:
            return
        if total <= view:
            return
        cur = cv.yview()[0] * total
        key = str(cv)
        target = wheel.get(key, cur) - e.delta / 120.0 * 110
        target = max(0.0, min(float(total - view), target))
        wheel[key] = target
        animate(("wheel", key), 230, lambda k: cv.yview_moveto((cur + (target - cur) * k) / total),
                done=lambda: wheel.pop(key, None))
    win.bind_all("<MouseWheel>", on_wheel)

    # --- подвал ---
    foot = tk.Frame(win, bg=PANEL)
    foot.pack(fill="x", side="bottom")
    busy_cv = tk.Canvas(foot, height=3, bg=PANEL, highlightthickness=0, bd=0)
    busy_cv.pack(fill="x", side="top")
    busy_seg = busy_cv.create_rectangle(-200, 0, -40, 3, fill=ACCENT_HI, width=0)
    foot_row = tk.Frame(foot, bg=PANEL)
    foot_row.pack(fill="x")
    auto_var = tk.BooleanVar(value=bool(settings.get("auto_launch", False)))

    def save_auto():
        s = load_settings(); s["auto_launch"] = bool(auto_var.get()); save_settings(s)
    tk.Checkbutton(foot_row, text="Сразу запускать лаунчер после выбора", variable=auto_var, command=save_auto,
                   font=(FONT, 10), bg=PANEL, fg=TEXT, selectcolor=CARD, activebackground=PANEL,
                   activeforeground=TEXT, highlightthickness=0, bd=0).pack(side="left", padx=18, pady=10)
    status = tk.Label(foot_row, text="", font=(FONT, 10), bg=PANEL, fg=MUTED, anchor="w")
    status.pack(side="left", fill="x", expand=True)

    def busy_anim(on):
        if not on:
            anims.pop("busy", None)
            busy_cv.coords(busy_seg, -200, 0, -40, 3)
            return

        def loop():
            if not state["busy"]:
                busy_cv.coords(busy_seg, -200, 0, -40, 3)
                return
            w = max(200, busy_cv.winfo_width())
            animate("busy", 1100, lambda k: busy_cv.coords(busy_seg, -180 + (w + 200) * k, 0, -180 + (w + 200) * k + 180, 3),
                    done=loop, ease=lambda t: t * t * (3 - 2 * t))
        loop()

    def press_flash(b, hot):
        try:
            b.configure(bg=mix(hot, "#ffffff", 0.28))
            fade_color(b, "bg", hot, 220)
        except tk.TclError:
            pass

    def with_icon(b, icon, text):
        """Значок слева от надписи кнопки (картинки ic_*.png из «Оформления»)."""
        im = art(icon, 1, 1) if icon else None
        if im is not None and im.width() > 2:
            b.configure(image=im, compound="left", text="  " + text)

    def small_button(parent, text, cmd, bg=CARD_HI, fg=TEXT, icon=None):
        b = tk.Label(parent, text=text, font=(FONT, 10), bg=bg, fg=fg, padx=12, pady=6, cursor="hand2")
        with_icon(b, icon, text)
        b.bind("<Button-1>", lambda e: (press_flash(b, LINE), cmd()))
        b.bind("<Enter>", lambda e: fade_color(b, "bg", LINE, 120))
        b.bind("<Leave>", lambda e: fade_color(b, "bg", bg, 180))
        return b

    def big_button(parent, text, cmd, color=ACCENT, hover=ACCENT_HI, icon=None):
        b = tk.Label(parent, text=text, font=(FONT, 11, "bold"), bg=color, fg="white",
                     padx=20, pady=7, cursor="hand2")
        if icon:
            with_icon(b, icon, text.lstrip("▶ ").strip())
        b.bind("<Button-1>", lambda e: (press_flash(b, hover), cmd()))
        b.bind("<Enter>", lambda e: fade_color(b, "bg", hover, 120))
        b.bind("<Leave>", lambda e: fade_color(b, "bg", color, 180))
        return b

    # --- всплывающие сообщения справа внизу ---
    toast_box = {}

    def toast(text, kind="ok", action=None, ms=3800):
        color = {"ok": ACCENT, "warn": GOLD, "err": "#e05a5a", "info": BLUE}.get(kind, ACCENT)
        old = toast_box.pop("w", None)
        if old is not None:
            try:
                old.destroy()
            except tk.TclError:
                pass
        f = tk.Frame(win, bg=CARD_HI, highlightthickness=1, highlightbackground=color)
        tk.Frame(f, bg=color, width=5).pack(side="left", fill="y")
        tk.Label(f, text=text, font=(FONT, 10), fg=TEXT, bg=CARD_HI, justify="left", anchor="w",
                 wraplength=330).pack(side="left", padx=(12, 10), pady=11)
        if action:
            def act():
                hide()
                action[1]()
            big_button(f, action[0], act).pack(side="right", padx=(0, 10), pady=8)
        toast_box["w"] = f
        win.update_idletasks()
        bottom = foot.winfo_height() + 14

        def place(k):
            f.place(relx=1.0, rely=1.0, x=int(-20 + (1 - k) * 440), y=-bottom, anchor="se")
        animate(("toast", str(f)), 320, place)
        f.lift()

        def hide():
            if not f.winfo_exists():
                return
            if pointer_inside(f):
                win.after(1200, hide)
                return
            animate(("toast", str(f)), 260, lambda k: f.place(relx=1.0, rely=1.0, x=int(-20 + k * 440), y=-bottom,
                                                              anchor="se"),
                    done=lambda: f.winfo_exists() and f.destroy(), ease=lambda t: t * t)
        win.after(ms, hide)

    def launch_now():
        lid = chosen_launcher()
        name = launcher_title(lid)
        if open_launcher():
            toast("Открываю %s. Нажми в нём «Играть» или «Войти в игру»." % name, "ok")
            return True
        toast("%s не нашёлся на этом компьютере. Во вкладке «Лаунчеры» можно скачать его "
              "или выбрать другой." % name, "warn", ("Лаунчеры", lambda: show("launchers")), 7000)
        return False

    def make_shortcuts():
        try:
            made = create_shortcuts()
        except Exception as e:
            made, err = [], str(e)
        else:
            err = ""
        if made:
            toast("Ярлык «%s» есть на рабочем столе и в меню «Пуск»." % APP_TITLE, "ok")
        else:
            toast("Не получилось создать ярлык. %s" % err, "err")
        refresh_foot()

    play_btn = big_button(play_btn_box, "", launch_now)
    play_btn.pack()
    foot_btns = tk.Frame(foot_row, bg=PANEL)
    foot_btns.pack(side="right", padx=(0, 10))
    small_button(foot_btns, "Папка игры", lambda: os.makedirs(MC, exist_ok=True) or os.startfile(MC), bg=PANEL, icon="ic_folder.png").pack(side="right", padx=4, pady=8)
    shortcut_btn = small_button(foot_btns, "", make_shortcuts, bg=PANEL)
    shortcut_btn.pack(side="right", padx=4, pady=8)
    small_button(foot_btns, "Папка Portalis", lambda: os.startfile(ROOT), bg=PANEL, icon="ic_backup.png").pack(
        side="right", padx=4, pady=8)
    small_button(foot_btns, "Проверить обновления", lambda: check_updates(manual=True), bg=PANEL, icon="tab_update.png").pack(
        side="right", padx=4, pady=8)

    def refresh_foot():
        shortcut_btn.configure(text="Ярлык создан ✓" if shortcuts_exist() else "Создать ярлык")
        with_icon(play_btn, "ic_play.png", ("Открыть " if launcher_mode() == "mrpack" else "Запустить ") + launcher_title(chosen_launcher()))

    def badge(parent, text, color=LINE, fg=TEXT):
        return tk.Label(parent, text=text, font=(FONT, 9, "bold"), bg=color, fg=fg, padx=8, pady=2)

    def image(path):
        try:
            im = tk.PhotoImage(file=path)
        except Exception:
            im = tk.PhotoImage(width=120, height=120)
        images.append(im)
        return im

    def card(parent, col, row):
        c = tk.Frame(parent, bg=CARD, padx=14, pady=14, highlightthickness=1, highlightbackground=CARD)
        c.grid(row=row, column=col, sticky="nsew", padx=(0, 14), pady=(0, 14))

        def leave(e):
            if not pointer_inside(c):
                fade_color(c, "highlightbackground", CARD, 220)
        c.bind("<Enter>", lambda e: fade_color(c, "highlightbackground", ACCENT, 160))
        c.bind("<Leave>", leave)
        return c

    def dropdown(parent, var, choices, bg=CARD):
        """Выпадающий список в стиле окна: подпись со стрелкой и тёмное меню."""
        def short(name):
            return name if name.startswith(NO_MODS) else name.rsplit(" (", 1)[0]
        disp = tk.StringVar(value=short(var.get()) + "   ▾")
        box = tk.Frame(parent, bg=bg)
        tk.Label(box, text="Сборка", font=(FONT, 8), fg=MUTED, bg=bg).pack(anchor="w")
        b = tk.Label(box, textvariable=disp, font=(FONT, 10), bg=CARD_HI, fg=TEXT, padx=10, pady=6,
                     anchor="w", width=30, cursor="hand2")
        b.pack(anchor="w")
        menu = tk.Menu(win, tearoff=0, bg=CARD_HI, fg=TEXT, activebackground=ACCENT, activeforeground="white",
                       font=(FONT, 10), bd=0, relief="flat")
        for ch in choices:
            menu.add_command(label="  " + short(ch) + "  ",
                             command=lambda ch=ch: (var.set(ch), disp.set(short(ch) + "   ▾")))
        b.bind("<Button-1>", lambda e: menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height()))
        b.bind("<Enter>", lambda e: b.configure(bg=LINE))
        b.bind("<Leave>", lambda e: b.configure(bg=CARD_HI))
        return box

    def option_menu(parent, var, choices):
        om = tk.OptionMenu(parent, var, *choices)
        om.configure(font=(FONT, 10), bg=CARD_HI, fg=TEXT, activebackground=LINE, activeforeground=TEXT,
                     highlightthickness=0, bd=0, relief="flat", anchor="w", width=24, indicatoron=True)
        om["menu"].configure(font=(FONT, 10), bg=CARD_HI, fg=TEXT, activebackground=ACCENT,
                             activeforeground="white", bd=0)
        return om

    def refresh_head():
        c = current()
        lid = chosen_launcher()
        if launcher_mode(lid) == "mrpack":
            now_pack.configure(text="Лаунчер: %s" % launcher_title(lid))
            now_ver.configure(text="карты и сборки уходят в него пакетами")
            return
        now_pack.configure(text="Моды: " + (c["name"] if c else "не выбраны через программу"))
        if lid == "tlauncher":
            now_ver.configure(text="Версия в TLauncher: %s" % (get_tlauncher_version() or "?"))
        else:
            now_ver.configure(text="Лаунчер: %s" % launcher_title(lid))

    def clear():
        for w in inner.winfo_children():
            w.destroy()
        images.clear()
        canvas.yview_moveto(0)
        inner.grid_columnconfigure(0, weight=1, uniform="c")
        inner.grid_columnconfigure(1, weight=1, uniform="c")

    def empty_note(row, text):
        f = tk.Frame(inner, bg=CARD, padx=20, pady=18)
        f.grid(row=row, column=0, columnspan=2, sticky="we", padx=(0, 14))
        tk.Label(f, image=art("empty.png", 1, 1), bg=CARD).pack(side="left", padx=(0, 18))
        tx = tk.Frame(f, bg=CARD)
        tx.pack(side="left", fill="both", expand=True)
        tk.Label(tx, text="Здесь пусто", font=(FONT, 13, "bold"), fg=GOLD, bg=CARD, anchor="w").pack(fill="x")
        tk.Label(tx, text=text, font=(FONT, 10), fg="#c3c7d1", bg=CARD, justify="left", anchor="w",
                 wraplength=700).pack(fill="x", pady=(6, 0))

    def search_box(parent, key, hint):
        """Поле поиска: фильтрует по мере ввода, Esc очищает. Ctrl+F ставит в него курсор."""
        box = tk.Frame(parent, bg=CARD_HI, padx=8, pady=2, highlightthickness=1, highlightbackground=CARD_HI)
        tk.Label(box, image=art("ic_search.png", 1, 1), bg=CARD_HI).pack(side="left")
        e = tk.Entry(box, font=(FONT, 10), bg=CARD_HI, fg=MUTED, insertbackground=TEXT, relief="flat", width=26,
                     highlightthickness=0, bd=0)
        e.pack(side="left", padx=(6, 0), ipady=4)
        q = state.get(key, "")
        e.insert(0, q or hint)
        if q:
            e.configure(fg=TEXT)

        def value():
            v = e.get()
            return "" if v == hint and e.cget("fg") == MUTED else v.strip()

        def focus_in(ev=None):
            fade_color(box, "highlightbackground", ACCENT, 150)
            if e.cget("fg") == MUTED:
                e.delete(0, "end")
                e.configure(fg=TEXT)

        def focus_out(ev=None):
            fade_color(box, "highlightbackground", CARD_HI, 200)
            if not e.get().strip():
                e.delete(0, "end")
                e.insert(0, hint)
                e.configure(fg=MUTED)

        def changed(ev=None):
            v = value()
            if v == state.get(key, ""):
                return
            state[key] = v
            if state.get("_sq"):
                win.after_cancel(state["_sq"])
            state["_sq"] = win.after(280, lambda: (state.update(focus_search=key), show(state["tab"], animated=False)))

        def clear(ev=None):
            e.delete(0, "end")
            changed()
        e.bind("<FocusIn>", focus_in)
        e.bind("<FocusOut>", focus_out)
        e.bind("<KeyRelease>", changed)
        e.bind("<Escape>", clear)
        state["search_entry"] = e
        if state.pop("focus_search", None) == key:
            e.focus_set()
            e.icursor("end")
        return box

    def toggle_chip(parent, text, key):
        on = bool(state.get(key))
        b = tk.Label(parent, text=("✓  " if on else "") + text, font=(FONT, 9, "bold"), bg=ACCENT if on else CARD_HI,
                     fg="white" if on else TEXT, padx=11, pady=5, cursor="hand2")
        b.bind("<Button-1>", lambda e: (state.update({key: not on}), show(state["tab"], animated=False)))
        b.bind("<Enter>", lambda e: on or fade_color(b, "bg", LINE, 120))
        b.bind("<Leave>", lambda e: on or fade_color(b, "bg", CARD_HI, 160))
        return b

    def matches_query(q, *fields):
        q = (q or "").lower()
        return not q or all(w in " ".join(str(f or "") for f in fields).lower() for w in q.split())

    def section(text, row, sub=""):
        f = tk.Frame(inner, bg=BG)
        f.grid(row=row, column=0, columnspan=2, sticky="we", padx=(0, 14), pady=(4, 10))
        tk.Label(f, text=text, font=(FONT, 14, "bold"), fg=TEXT, bg=BG).pack(side="left")
        if sub:
            tk.Label(f, text="   " + sub, font=(FONT, 10), fg=MUTED, bg=BG).pack(side="left", pady=(3, 0))
        return f

    # --- фоновые задачи, чтобы окно не зависало ---
    def run_task(title, fn, after=None):
        if state["busy"]:
            toast("Подожди: программа ещё занята предыдущим делом.", "warn")
            return
        state["busy"] = True
        status.configure(text=title + "...", fg=GOLD)
        win.configure(cursor="watch")
        busy_anim(True)
        logs, box = [], {}

        def worker():
            try:
                box["ok"] = fn(logs.append)
            except Exception as e:
                box["err"] = str(e)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        def poll():
            if t.is_alive():
                if logs:
                    status.configure(text=logs[-1])
                win.after(150, poll)
                return
            state["busy"] = False
            win.configure(cursor="")
            refresh_head()
            if "err" in box:
                status.configure(text="Ошибка: " + box["err"], fg="#e05a5a")
                messagebox.showerror("Не получилось", box["err"] + "\n\n" + "\n".join(logs))
                return
            status.configure(text="Готово", fg=ACCENT)
            if after:
                after(box.get("ok"), logs)
            show(state["tab"])
        poll()

    def finish(ok, logs):
        """Окно «Всё готово» с кнопкой запуска лаунчера."""
        lid = chosen_launcher()
        name = launcher_title(lid)
        mrpack = ok if isinstance(ok, str) and ok.endswith(".mrpack") else None
        ver = next((re.search(r"(?:Версия в [^:]+|версия): (\S+)", l).group(1) for l in reversed(logs)
                    if re.search(r"(?:Версия в [^:]+|версия): (\S+)", l)), None)
        hints = [l[len("Подсказка: "):] for l in logs if l.startswith("Подсказка: ")]
        opened = False
        if mrpack:
            hints = ["Пакет «%s» передаётся в %s: подтверди установку в его окне и запусти новый экземпляр."
                     % (os.path.basename(mrpack)[:-7], name)]
            if auto_var.get():
                opened = open_mrpack(mrpack, lid)
        elif auto_var.get() and ok:
            opened = open_launcher()

        t = tk.Toplevel(win)
        t.title("Готово")
        t.configure(bg=BG)
        t.transient(win)
        t.resizable(False, False)
        W = 560
        fr = tk.Frame(t, bg=BG, padx=26, pady=22)
        fr.pack(fill="both", expand=True)
        top = tk.Frame(fr, bg=BG)
        top.pack(fill="x")
        pic = art("ready.png", 1, 1)
        tk.Label(top, image=pic, bg=BG).pack(side="left", anchor="n")
        info = tk.Frame(top, bg=BG, padx=18)
        info.pack(side="left", fill="both", expand=True)
        tk.Label(info, text="Пакет готов!" if mrpack else "Всё готово!", font=(FONT, 22, "bold"), fg=TEXT, bg=BG,
                 anchor="w").pack(fill="x")
        what = next((l for l in logs if l.startswith(("Карта «", "Скопировано модов", "Пакет для лаунчера"))), "")
        tk.Label(info, text=(what + "\n" if what else "") + ("Версия: %s" % ver if ver else ""), font=(FONT, 10),
                 fg=MUTED, bg=BG, anchor="w", justify="left", wraplength=300).pack(fill="x", pady=(6, 0))
        for h in hints:
            tk.Label(info, text=h, font=(FONT, 10, "bold"), fg=GOLD, bg=BG, anchor="w", justify="left",
                     wraplength=300).pack(fill="x", pady=(8, 0))
        details = tk.Label(fr, text="\n".join(logs), font=(FONT, 9), fg="#8b909c", bg=PANEL, anchor="w",
                           justify="left", wraplength=W - 80, padx=12, pady=8)
        shown = {"v": False}

        def toggle():
            shown["v"] = not shown["v"]
            if shown["v"]:
                details.pack(fill="x", pady=(12, 0), before=row)
                more.configure(text="Скрыть подробности")
            else:
                details.pack_forget()
                more.configure(text="Подробнее о том, что сделано")
        row = tk.Frame(fr, bg=BG)
        row.pack(fill="x", pady=(18, 0))

        def go():
            t.destroy()
            if mrpack:
                try:
                    open_mrpack(mrpack, lid)
                    toast("Открываю пакет в %s." % name, "ok")
                except Exception as e:
                    toast("Не открылся пакет: %s. Он лежит в папке _export." % e, "err", ms=8000)
            else:
                launch_now()
        if opened:
            tk.Label(row, text="%s открывается..." % name, font=(FONT, 11, "bold"), fg=ACCENT_HI, bg=BG).pack(side="left")
            big_button(row, "Закрыть", t.destroy, CARD_HI, LINE).pack(side="right")
        else:
            big_button(row, ("▶  Открыть в %s" if mrpack else "▶  Запустить %s") % name, go, icon="ic_play.png").pack(side="right")
            small_button(row, "Закрыть", t.destroy, bg=BG).pack(side="right", padx=8)
        if mrpack:
            small_button(row, "Папка с пакетом", lambda: subprocess.Popen(["explorer.exe", "/select,", mrpack]),
                         bg=BG, icon="ic_folder.png").pack(side="right")
        more = small_button(row, "Подробнее о том, что сделано", toggle, bg=BG)
        more.pack(side="left")
        t.update_idletasks()
        w, h = max(W, t.winfo_reqwidth()), t.winfo_reqheight()
        t.geometry("%dx%d+%d+%d" % (w, h, win.winfo_rootx() + (win.winfo_width() - w) // 2,
                                    win.winfo_rooty() + (win.winfo_height() - h) // 3))
        t.bind("<Escape>", lambda e: t.destroy())
        t.bind("<Return>", lambda e: go() if not opened else t.destroy())
        t.focus_force()
        fade_in_window(t)

        def fix_size():
            if t.winfo_exists():
                t.update_idletasks()
                t.geometry("%dx%d" % (max(W, t.winfo_reqwidth()), t.winfo_reqheight()))
        details.bind("<Map>", lambda e: fix_size())
        details.bind("<Unmap>", lambda e: fix_size())

    def check_game():
        if game_running():
            messagebox.showwarning("Игра запущена", "Сначала закрой Minecraft, потом выбирай карту или сборку.")
            return False
        mode = launcher_mode()
        busy = (mode == "tl" and tlauncher_running()) or (mode == "legacy" and process_running("LL.exe")) or \
            (mode == "profiles" and process_running("MinecraftLauncher.exe", "SKlauncher"))
        if busy:
            name = launcher_title(chosen_launcher())
            return messagebox.askyesno(
                "%s открыт" % name,
                "%s сейчас открыт и при закрытии может перезаписать выбор версии.\n"
                "Лучше закрыть его и нажать ещё раз.\n\nПродолжить всё равно?" % name)
        return True

    # --- вкладка «Карты» ---
    # --- окна с подробным описанием ---
    def detail_window(title, width=800, height=700):
        t = tk.Toplevel(win)
        t.title(title)
        t.configure(bg=BG)
        x, y = win.winfo_rootx() + 60, win.winfo_rooty() + 40
        t.geometry("%dx%d+%d+%d" % (width, height, x, y))
        t.minsize(640, 480)
        t.transient(win)
        try:
            t.iconphoto(False, tk.PhotoImage(file=os.path.join(ROOT, "icon.png")))
        except Exception:
            pass
        cv = tk.Canvas(t, bg=BG, highlightthickness=0, bd=0)
        sb = tk.Scrollbar(t, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        body = tk.Frame(cv, bg=BG, padx=26, pady=22)
        bid = cv.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfigure(bid, width=e.width))
        t._cv = cv
        t._imgs = []
        t.bind("<Escape>", lambda e: t.destroy())
        fade_in_window(t)
        return t, body

    def detail_image(t, path, fallback=None, zoom=1):
        for p in (path, fallback):
            if p and os.path.isfile(p):
                try:
                    im = tk.PhotoImage(file=p)
                    if zoom > 1:
                        im = im.zoom(zoom)
                    t._imgs.append(im)
                    return im
                except Exception:
                    pass
        im = tk.PhotoImage(width=120, height=120)
        t._imgs.append(im)
        return im

    def detail_header(t, body, img, title, badges, sub=""):
        top = tk.Frame(body, bg=BG)
        top.pack(fill="x")
        tk.Label(top, image=img, bg=BG).pack(side="left", anchor="n")
        info = tk.Frame(top, bg=BG, padx=20)
        info.pack(side="left", fill="both", expand=True)
        tk.Label(info, text=title, font=(FONT, 21, "bold"), fg=TEXT, bg=BG, anchor="w", justify="left",
                 wraplength=440).pack(fill="x")
        bl = tk.Frame(info, bg=BG)
        bl.pack(fill="x", pady=(8, 10))
        for text, color, fg in badges:
            badge(bl, text, color, fg).pack(side="left", padx=(0, 6))
        if sub:
            tk.Label(info, text=sub, font=(FONT, 10), fg=MUTED, bg=BG, anchor="w", justify="left",
                     wraplength=440).pack(fill="x")
        return info

    def dsection(body, text):
        tk.Frame(body, height=1, bg=LINE).pack(fill="x", pady=(20, 0))
        tk.Label(body, text=text, font=(FONT, 13, "bold"), fg=TEXT, bg=BG, anchor="w").pack(fill="x", pady=(10, 6))

    def dpara(body, text, color="#c3c7d1", bullet=False, size=10, bold=False):
        tk.Label(body, text=("•  " if bullet else "") + text, font=(FONT, size, "bold" if bold else "normal"),
                 fg=color, bg=BG, justify="left", anchor="w", wraplength=700).pack(fill="x", pady=2)

    def dbuttons(body):
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=(20, 4))
        return row

    def folder_size(path):
        total = n = 0
        for d, _dirs, files in os.walk(lp(path)):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(d, f)); n += 1
                except OSError:
                    pass
        return total / 1048576, n

    def start_map(m, pick, packs):
        if not check_game():
            return
        pack = next((p for p in packs if p["name"] == pick), None)
        if launcher_mode() == "mrpack":
            def go_pack():
                ps2 = find_packs()
                pk = next((q for q in ps2 if pack and q["path"] == pack["path"]), None)
                run_task("Собираю пакет «%s» для %s" % (m["title"], launcher_title(chosen_launcher())),
                         lambda log: export_mrpack(m, pk, log), finish)
            ensure_items(missing_items(m, pack, packs), go_pack, "Скачать «%s»" % m["title"])
            return
        fresh = False
        if map_installed(m):
            ans = messagebox.askyesnocancel(
                m["title"], "Карта уже установлена.\n\nДа: продолжить своё сохранение\n"
                            "Нет: начать заново (старое сохранение останется копией)")
            if ans is None:
                return
            fresh = not ans
        pack = next((p for p in packs if p["name"] == pick), None)

        def go():
            ps2 = find_packs()
            pk = next((q for q in ps2 if pack and q["path"] == pack["path"]), None)
            run_task("Готовлю «%s»" % m["title"], lambda log: install_map(m, pk, fresh, ps2, log), finish)
        ensure_items(missing_items(m, pack, packs), go, "Скачать «%s»" % m["title"])

    def not_downloaded(item_id):
        """Часть из описи, если она ещё не скачана (иначе None)."""
        it = manifest_item(load_local_manifest(), item_id)
        return it if it and not item_downloaded(it) else None

    def dl_badge(parent, it, bg=CARD):
        if it:
            badge(parent, "не скачана · %s" % fmt_mb(it.get("dl", 0)), "#4a3f17", "#f3d27a").pack(side="left", padx=(0, 6))

    def remove_button(row, it, title, t=None):
        """«Удалить скачанное»: содержимое в Корзину, в каталоге карта или сборка остаётся."""
        if not it or not item_downloaded(it):
            return

        def go():
            if not messagebox.askyesno("Удалить скачанное", "Убрать скачанные файлы «%s» в Корзину?\n\n"
                                       "В списке она останется, скачать можно снова в любой момент. "
                                       "Миры в игре это не трогает." % title, parent=t or win):
                return
            if t:
                t.destroy()
            run_task("Убираю «%s»" % title, lambda log: remove_item(it, log=log),
                     lambda n, logs: toast("«%s» убрана, место освобождено." % title))
        small_button(row, "Удалить скачанное", go, bg=row["bg"], icon="ic_delete.png").pack(side="left", padx=(8, 0))

    def enable_pack(p):
        if launcher_mode() == "mrpack":
            def go_pack():
                pk = next((q for q in find_packs() if q["path"] == p["path"]), p)
                run_task("Собираю пакет «%s» для %s" % (pk["name"].rsplit(" (", 1)[0], launcher_title(chosen_launcher())),
                         lambda log: export_mrpack(None, pk, log), finish)
            ensure_items(missing_items(pack=p), go_pack, "Скачать «%s»" % p["name"].rsplit(" (", 1)[0])
            return

        def go():
            ps2 = find_packs()
            pk = next((q for q in ps2 if q["path"] == p["path"]), p)
            run_task("Включаю «%s»" % pk["name"], lambda log: switch(pk, ps2, True, log), finish)
        ensure_items(missing_items(pack=p), go, "Скачать «%s»" % p["name"].rsplit(" (", 1)[0])

    def open_map(m):
        packs = find_packs()
        t, body = detail_window(m["title"])
        img = detail_image(t, os.path.join(m["path"], "cover_big.png"), os.path.join(m["path"], "cover.png"))
        badges = [(m["version"], BLUE, "white"), (m["genre"], LINE, TEXT)]
        if m.get("resource_pack") == "встроен":
            badges.append(("ресурс-пак встроен", "#5a4c1c", "#f3d27a"))
        if map_installed(m):
            badges.append(("установлена", "#2f6b34", "white"))
        miss = not_downloaded(map_item_id(m))
        if miss:
            badges.append(("не скачана · %s" % fmt_mb(miss.get("dl", 0)), "#4a3f17", "#f3d27a"))
        sub = m["players"]
        if m.get("author"):
            sub += "\nАвтор: %s" % m["author"]
        if m.get("source"):
            sub += "\nОткуда: %s" % m["source"]
        detail_header(t, body, img, m["title"], badges, sub)
        dsection(body, "О карте")
        dpara(body, m["desc"])
        if m.get("how"):
            dsection(body, "Как играть")
            for line in m["how"]:
                dpara(body, line, bullet=True)
        if m.get("notes"):
            dsection(body, "Полезно знать")
            for line in m["notes"]:
                dpara(body, line, bullet=True)
        dsection(body, "Технически")
        opts = packs_for_map(m, packs)
        mb, nf = folder_size(os.path.join(m["path"], "world"))
        it = manifest_item(load_local_manifest(), map_item_id(m))
        if miss:
            dpara(body, "Карта ещё не скачана: %s, программа скачает её с %s при нажатии «Играть»." % (
                fmt_mb(miss.get("dl", 0)), ", ".join(miss.get("sites", [])) or "сайта автора"), bullet=True, color=GOLD)
        elif it and it.get("sites"):
            dpara(body, "Скачана с: %s." % ", ".join(it["sites"]), bullet=True)
        dpara(body, "Версия игры: %s. Программа сама скачает её и выберет в TLauncher." % m["version"], bullet=True)
        dpara(body, "Сборки, с которыми можно играть: %s." % (", ".join(p["name"].rsplit(" (", 1)[0] for p in opts)
                                                              if opts else "только без модов"), bullet=True)
        if nf:
            dpara(body, "Размер мира: %s МБ, файлов %d." % (("%.1f" if mb < 10 else "%.0f") % mb, nf), bullet=True)
        dpara(body, "Сохранение в игре: saves\\%s" % m["save"], bullet=True)
        row = dbuttons(body)
        choices = ([] if m.get("requires_pack") and opts else ["%s (чистая %s)" % (NO_MODS, m["version"])]) + \
            [p["name"] for p in opts]
        var = tk.StringVar(value=m.get("recommended") if m.get("recommended") in choices else choices[0])
        dropdown(row, var, choices, BG).pack(side="left")
        big_button(row, "Скачать и играть" if miss else "Играть",
                   lambda: (t.destroy(), start_map(m, var.get(), packs)),
                   icon="ic_download.png" if miss else "ic_play.png").pack(side="right")
        remove_button(row, it, m["title"], t)
        if map_installed(m):
            small_button(row, "Папка мира", lambda: os.startfile(os.path.join(SAVES, m["save"])), icon="ic_folder.png",
                         bg=BG).pack(side="right", padx=8)

            def do_backup():
                def after(path, logs):
                    if path and messagebox.askyesno("Копия готова", "\n".join(logs) + "\n\nОткрыть папку с копиями?",
                                                    parent=t if t.winfo_exists() else win):
                        os.startfile(os.path.dirname(path))
                run_task("Делаю копию мира «%s»" % m["title"], lambda log: backup_world(m, log), after)
            small_button(row, "Резервная копия", do_backup, bg=BG, icon="ic_backup.png").pack(side="right")
        auto_wrap(body)

    def open_pack(p):
        packs = find_packs()
        cur = current() or {}
        t, body = detail_window(p["name"].rsplit(" (", 1)[0])
        img = detail_image(t, os.path.join(p["path"], "cover_big.png"), os.path.join(p["path"], "cover.png"))
        active = cur.get("name") == p["name"] and cur.get("version_dir") == p["version_dir"]
        badges = [(p["version_dir"], BLUE, "white"), (n_mods(p["count"]) if p["count"] else "без модов", LINE, TEXT)]
        if active:
            badges.append(("включена", "#2f6b34", "white"))
        miss = not_downloaded(pack_item_id(p))
        if miss:
            badges.append(("не скачана · %s" % fmt_mb(miss.get("dl", 0)), "#4a3f17", "#f3d27a"))
        detail_header(t, body, img, p["name"].rsplit(" (", 1)[0], badges, p.get("description", ""))
        try:
            with open(os.path.join(p["path"], "mods.json"), encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            data = {"order": [], "mods": [{"name": j, "version": "", "cat": "Моды", "ru": ""} for j in
                                          jars(os.path.join(p["path"], "mods"))]}
        mods = data["mods"]
        order = data.get("order") or sorted({x["cat"] for x in mods})
        libs = [x for x in mods if x["cat"].startswith("Библиотеки")]
        for cat in order:
            items = [x for x in mods if x["cat"] == cat]
            if not items or cat.startswith("Библиотеки"):
                continue
            dsection(body, "%s  (%d)" % (cat, len(items)))
            for x in sorted(items, key=lambda x: x["name"].lower()):
                r = tk.Frame(body, bg=BG)
                r.pack(fill="x", pady=(3, 0))
                tk.Label(r, text=x["name"], font=(FONT, 10, "bold"), fg=TEXT, bg=BG).pack(side="left")
                if x.get("version"):
                    tk.Label(r, text="  " + str(x["version"])[:24], font=(FONT, 8), fg=MUTED, bg=BG).pack(side="left", pady=(2, 0))
                if x.get("ru"):
                    tk.Label(body, text=x["ru"], font=(FONT, 10), fg="#c3c7d1", bg=BG, justify="left", anchor="w",
                             wraplength=700).pack(fill="x", padx=(14, 0))
        if libs:
            dsection(body, "Библиотеки  (%d)" % len(libs))
            dpara(body, "Сами ничего не добавляют, но без них не запустятся другие моды: " +
                  ", ".join(sorted(x["name"] for x in libs)) + ".", color=MUTED)
        extras = []
        for sub, label in (("resourcepacks", "Текстур-паки"), ("shaderpacks", "Шейдеры")):
            d = os.path.join(p["path"], sub)
            if os.path.isdir(d):
                names = sorted(os.path.splitext(f)[0] for f in os.listdir(d) if f.lower().endswith(".zip"))
                if names:
                    extras.append((label, names))
        if extras:
            dsection(body, "Текстуры и шейдеры, которые ставятся со сборкой")
            for label, names in extras:
                dpara(body, "%s: %s." % (label, ", ".join(names)), bullet=True)
        fit = [m["title"] for m in find_maps() if any(q["name"] == p["name"] and q["version_dir"] == p["version_dir"]
                                                        for q in packs_for_map(m, packs))]
        dsection(body, "Технически")
        dpara(body, "Версия в TLauncher: %s." % p.get("tl_version"), bullet=True)
        if fit:
            dpara(body, "Подходит к картам: %s." % ", ".join(fit), bullet=True)
        mb, nf = folder_size(os.path.join(p["path"], "mods"))
        it = manifest_item(load_local_manifest(), pack_item_id(p))
        if miss:
            dpara(body, "Сборка ещё не скачана: %s. Моды программа скачает с %s при нажатии «Включить»." % (
                fmt_mb(miss.get("dl", 0)), ", ".join(miss.get("sites", [])) or "Modrinth"), bullet=True, color=GOLD)
        else:
            dpara(body, "Размер модов: %s МБ." % (("%.1f" if mb < 10 else "%.0f") % mb), bullet=True)
            if it and it.get("sites"):
                dpara(body, "Моды и файлы скачаны с: %s." % ", ".join(it["sites"]), bullet=True)
        row = dbuttons(body)

        def enable():
            if not check_game():
                return
            t.destroy()
            enable_pack(p)
        remove_button(row, it, p["name"].rsplit(" (", 1)[0], t)
        big_button(row, "Включена" if active else ("Скачать и включить" if miss else "Включить"), enable,
                   icon=None if active else ("ic_download.png" if miss else "ic_check.png"),
                   color=CARD_HI if active else ACCENT, hover=LINE if active else ACCENT_HI).pack(side="right")
        small_button(row, "Папка сборки", lambda: os.startfile(p["path"]), bg=BG, icon="ic_folder.png").pack(side="right", padx=8)
        auto_wrap(body)

    def open_server(s):
        t, body = detail_window(s["name"].partition(" - ")[0], 700, 620)
        icon = os.path.join(ROOT, s.get("icon", "")) if s.get("icon") else None
        img = detail_image(t, icon, None, zoom=2)
        name, _, modes = s["name"].partition(" - ")
        badges = [(x.strip(), LINE, TEXT) for x in modes.split(",") if x.strip()]
        badges.append((s.get("lang", ""), BLUE, "white"))
        detail_header(t, body, img, name, badges, "Адрес: %s\nВерсии: %s\n%s" % (
            s["ip"], s.get("versions", ""), (lambda n: n[:1].upper() + n[1:])(s.get("note", "") or "")))
        dsection(body, "Сейчас на сервере")
        live = tk.Label(body, text="Проверяю сервер...", font=(FONT, 11, "bold"), fg=GOLD, bg=BG, anchor="w",
                        justify="left", wraplength=600)
        live.pack(fill="x")
        motd = tk.Label(body, text="", font=(FONT, 10), fg="#c3c7d1", bg=BG, anchor="w", justify="left", wraplength=600)
        motd.pack(fill="x", pady=(4, 0))
        box = {}

        def work():
            try:
                box["info"] = server_status(s["ip"])
            except Exception as e:
                box["err"] = str(e) or type(e).__name__
        th = threading.Thread(target=work, daemon=True)
        th.start()

        def poll():
            if not t.winfo_exists():
                return
            if th.is_alive():
                t.after(200, poll); return
            if "info" in box:
                i = box["info"]
                live.configure(text="● Онлайн: %s из %s   ·   пинг %d мс   ·   %s" % (
                    i["online"], i["max"], i["ms"], i["version"]), fg=ACCENT_HI)
                if i.get("motd"):
                    motd.configure(text="Сообщение сервера: " + i["motd"])
            else:
                live.configure(text="● Сервер сейчас не отвечает (%s). Возможно, он перезапускается "
                                    "или его адрес изменился." % box.get("err"), fg="#e05a5a")
        poll()
        dsection(body, "Как зайти")
        for line in ("Включи «Чистую ваниллу» или «Только шейдеры и текстуры»: с большими сборками модов сервер может не пустить.",
                     "В игре открой «Сетевая игра» и выбери сервер в списке. Если его там нет, нажми «Добавить в игру».",
                     "При первом входе напиши в чат /register пароль пароль, потом при каждом входе /login пароль.",
                     "Пароль придумай отдельный. Администрация никогда не спрашивает его в чате."):
            dpara(body, line, bullet=True)
        row = dbuttons(body)

        def copy():
            t.clipboard_clear(); t.clipboard_append(s["ip"])
            toast("Адрес скопирован: " + s["ip"])

        def add():
            ok = add_server_entry(s["name"], s["ip"])
            messagebox.showinfo("Сервер", "Сервер добавлен в «Сетевую игру»." if ok else "Этот сервер уже есть в списке игры.",
                                parent=t)
        big_button(row, "Добавить в игру", add, icon="ic_server.png").pack(side="right")
        small_button(row, "Копировать адрес", copy, bg=BG, icon="ic_copy.png").pack(side="right", padx=8)
        auto_wrap(body)

    def clickable(widget, fn):
        """Клик по карточке открывает описание. Кнопки и выпадающие списки не трогаем."""
        stack = [widget]
        while stack:
            w = stack.pop()
            stack.extend(w.winfo_children())
            if isinstance(w, tk.Entry):
                continue
            if isinstance(w, tk.Label) and w.bind("<Button-1>"):
                continue
            w.bind("<Button-1>", lambda e: fn(), add="+")
            try:
                w.configure(cursor="hand2")
            except tk.TclError:
                pass

    def build_maps():
        packs = find_packs()
        maps = find_maps()
        head_f = section("Карты", 0, "выбери карту и сборку, остальное программа сделает сама")
        search_box(head_f, "q_maps", "Поиск карты").pack(side="right")
        if not maps:
            empty_note(2, LIBRARY_NOTE if not library_found() else "Карт пока нет. Добавь папку с картой в «Карты».")
        fbox = tk.Frame(inner, bg=BG)
        fbox.grid(row=1, column=0, columnspan=2, sticky="we", pady=(0, 12))
        flt = tk.Frame(fbox, bg=BG)
        flt.pack(fill="x")
        flt2 = tk.Frame(fbox, bg=BG)
        flt2.pack(fill="x", pady=(8, 0))

        def chip(parent, text, key, value):
            on = state.get(key, "all") == value
            b = tk.Label(parent, text=text, font=(FONT, 9, "bold"), bg=ACCENT if on else CARD_HI,
                         fg="white" if on else TEXT, padx=11, pady=5, cursor="hand2")
            b.pack(side="left", padx=(0, 5))
            b.bind("<Button-1>", lambda e: (state.update({key: value}), show("maps")))
        tk.Label(flt, text="Игроков:", font=(FONT, 10, "bold"), fg=TEXT, bg=BG).pack(side="left", padx=(0, 8))
        for text, v in (("Все", "all"), ("Один", "solo"), ("Вдвоём", "duo"), ("Компанией", "group")):
            chip(flt, text, "f_players", v)
        tk.Label(flt, text="   Версия:", font=(FONT, 10, "bold"), fg=TEXT, bg=BG).pack(side="left", padx=(0, 8))
        for text, v in (("Все", "all"), ("26.x", "new"), ("1.21", "1.21"), ("1.20", "1.20"), ("Старые", "old")):
            chip(flt, text, "f_version", v)
        total = len(maps)
        maps = [m for m in maps if map_matches(m, state.get("f_players", "all"), state.get("f_version", "all"))
                and matches_query(state.get("q_maps"), m["title"], m.get("genre"), m.get("desc"), m.get("players"),
                                  m.get("author"), m["version"])
                and (not state.get("f_dl_maps") or not not_downloaded(map_item_id(m)))]
        toggle_chip(flt2, "Только скачанные", "f_dl_maps").pack(side="left")
        tk.Label(flt2, text="   показано %d из %d" % (len(maps), total), font=(FONT, 9), fg=MUTED, bg=BG).pack(side="left")
        if total and not maps:
            empty_note(2, "Под этот фильтр карт нет. Сбрось фильтры или очисти поиск (Esc).")
        for i, m in enumerate(maps):
            c = card(inner, i % 2, 2 + i // 2)
            top = tk.Frame(c, bg=CARD)
            top.pack(fill="x")
            tk.Label(top, image=image(os.path.join(m["path"], "cover.png")), bg=CARD).pack(side="left", anchor="n")
            info = tk.Frame(top, bg=CARD, padx=14)
            info.pack(side="left", fill="both", expand=True)
            tk.Label(info, text=m["title"], font=(FONT, 15, "bold"), fg=TEXT, bg=CARD, anchor="w").pack(fill="x")
            bl = tk.Frame(info, bg=CARD)
            bl.pack(fill="x", pady=(4, 6))
            badge(bl, m["version"], BLUE, "white").pack(side="left", padx=(0, 6))
            badge(bl, m["genre"]).pack(side="left", padx=(0, 6))
            miss = not_downloaded(map_item_id(m))
            dl_badge(bl, miss)
            pl = tk.Frame(info, bg=CARD)
            pl.pack(fill="x")
            tk.Label(pl, text=m["players"], font=(FONT, 9), fg=MUTED, bg=CARD).pack(side="left")
            if map_installed(m):
                tk.Label(pl, text="   ● уже установлена", font=(FONT, 9, "bold"), fg=ACCENT_HI, bg=CARD).pack(side="left")
            tk.Label(c, text=m["desc"], font=(FONT, 10), fg="#c3c7d1", bg=CARD, justify="left",
                     anchor="w", wraplength=400).pack(fill="x", pady=(10, 10))
            row = tk.Frame(c, bg=CARD)
            row.pack(fill="x", side="bottom")
            opts = [p["name"] for p in packs_for_map(m, packs)]
            choices = ([] if m.get("requires_pack") and opts else ["%s (чистая %s)" % (NO_MODS, m["version"])]) + opts
            var = tk.StringVar(value=m["recommended"] if m.get("recommended") in opts else choices[0])
            dropdown(row, var, choices).pack(side="left")

            big_button(row, "Скачать и играть" if miss else "Играть",
                       lambda m=m, var=var, packs=packs: start_map(m, var.get(), packs),
                       icon="ic_download.png" if miss else "ic_play.png").pack(side="right")
            small_button(row, "Подробнее", lambda m=m: open_map(m), bg=CARD).pack(side="right", padx=8)
            clickable(c, lambda m=m: open_map(m))

    # --- вкладка «Сборки» ---
    def build_packs():
        packs = find_packs()
        cur = current() or {}
        head_f = section("Сборки", 0, "моды, текстуры и шейдеры для выбранной версии")

        def save_current():
            if not jars(MODS):
                messagebox.showinfo("Сохранить", "В папке mods нет модов."); return
            vdirs = sorted({p["version_dir"] for p in packs})
            v = simpledialog.askstring("Версия", "Папка версии (например: %s):" % ", ".join(vdirs), parent=win)
            if not v:
                return
            n = simpledialog.askstring("Название", "Название новой сборки:", parent=win)
            if not n:
                return
            tl = next((p.get("tl_version") for p in packs if p["version_dir"] == v), v)
            save_current_as(n, v, tl, "Сохранено из текущей папки mods")
            show("packs")
        small_button(head_f, "Сохранить текущие моды как сборку", save_current).pack(side="right")
        search_box(head_f, "q_packs", "Поиск сборки или мода").pack(side="right", padx=(0, 10))
        allp = len(packs)

        def mod_names(p):
            try:
                with open(os.path.join(p["path"], "mods.json"), encoding="utf-8") as fh:
                    return " ".join(x.get("name", "") + " " + x.get("ru", "") for x in json.load(fh).get("mods", []))
            except Exception:
                return ""
        packs = [p for p in packs if matches_query(state.get("q_packs"), p["name"], p.get("description"), p["version_dir"],
                                                    mod_names(p) if state.get("q_packs") else "")
                 and (not state.get("f_dl_packs") or not not_downloaded(pack_item_id(p)))]
        fl = tk.Frame(inner, bg=BG)
        fl.grid(row=1, column=0, columnspan=2, sticky="we", pady=(0, 12))
        toggle_chip(fl, "Только скачанные", "f_dl_packs").pack(side="left")
        tk.Label(fl, text="   показано %d из %d" % (len(packs), allp), font=(FONT, 9), fg=MUTED, bg=BG).pack(side="left")
        if allp and not packs:
            empty_note(2, "Под этот фильтр сборок нет. Очисти поиск (Esc) или сними «Только скачанные».")

        if not allp:
            empty_note(2, LIBRARY_NOTE)
        for i, p in enumerate(packs):
            c = card(inner, i % 2, 3 + i // 2)
            top = tk.Frame(c, bg=CARD)
            top.pack(fill="x")
            tk.Label(top, image=image(os.path.join(p["path"], "cover.png")), bg=CARD).pack(side="left", anchor="n")
            info = tk.Frame(top, bg=CARD, padx=14)
            info.pack(side="left", fill="both", expand=True)
            title = p["name"].rsplit(" (", 1)[0]
            tk.Label(info, text=title, font=(FONT, 15, "bold"), fg=TEXT, bg=CARD, anchor="w",
                     wraplength=260, justify="left").pack(fill="x")
            bl = tk.Frame(info, bg=CARD)
            bl.pack(fill="x", pady=(4, 6))
            badge(bl, p["version_dir"], BLUE, "white").pack(side="left", padx=(0, 6))
            badge(bl, n_mods(p["count"]) if p["count"] else "без модов").pack(side="left", padx=(0, 6))
            active = cur.get("name") == p["name"] and cur.get("version_dir") == p["version_dir"]
            if active:
                badge(bl, "включена", "#2f6b34", "white").pack(side="left", padx=(0, 6))
            if p.get("user"):
                badge(bl, "своя", "#3b2f6b", "white").pack(side="left")
            miss = not_downloaded(pack_item_id(p))
            dl_badge(bl, miss)
            tk.Label(c, text=p.get("description", ""), font=(FONT, 10), fg="#c3c7d1", bg=CARD,
                     justify="left", anchor="w", wraplength=400).pack(fill="x", pady=(10, 10))
            row = tk.Frame(c, bg=CARD)
            row.pack(fill="x", side="bottom")

            def enable(p=p):
                if not check_game():
                    return
                enable_pack(p)
            big_button(row, "Включена" if active else ("Скачать и включить" if miss else "Включить"), enable,
                   icon=None if active else ("ic_download.png" if miss else "ic_check.png"),
                       color=CARD_HI if active else ACCENT, hover=LINE if active else ACCENT_HI).pack(side="right")
            small_button(row, "Подробнее: моды", lambda p=p: open_pack(p), bg=CARD).pack(side="right", padx=8)
            if p.get("user"):
                def drop(p=p):
                    if messagebox.askyesno("Удалить сборку", "Убрать свою сборку «%s» в Корзину?" % p["name"]):
                        run_task("Убираю «%s»" % p["name"], lambda log: delete_user_pack(p, log),
                                 lambda ok, logs: toast("Сборка убрана в Корзину.", "ok"))
                small_button(row, "Изменить", lambda p=p: edit_user_pack(p), bg=CARD, icon="ic_settings.png").pack(side="left")
                small_button(row, "Удалить", drop, bg=CARD, icon="ic_delete.png").pack(side="left", padx=4)
            clickable(c, lambda p=p: open_pack(p))

    # --- вкладка «Серверы» ---
    def build_servers():
        head_f = section("Серверы", 0, "все пускают с TLauncher без лицензии")

        def add_all():
            logs = []
            try:
                merge_servers(logs.append)
            except Exception as e:
                messagebox.showerror("Серверы", str(e)); return
            messagebox.showinfo("Серверы", "\n".join(logs) + "\n\nОни появятся в «Сетевой игре».")
        big_button(head_f, "Добавить все в игру", add_all, icon="ic_server.png").pack(side="right")
        if not load_servers():
            empty_note(1, LIBRARY_NOTE if not library_found() else "Список серверов пуст: нет файла servers.json.")
            return
        box = tk.Frame(inner, bg=CARD, padx=6, pady=6)
        box.grid(row=1, column=0, columnspan=2, sticky="we", padx=(0, 14))
        srv_cache = state.setdefault("srv_status", {})
        srv_labels = {}

        def srv_text(st):
            if st is None:
                return "проверяю..."
            if st.get("err"):
                return "● не отвечает"
            return "● %s онлайн · %d мс" % (st["online"], st["ms"])

        def srv_color(st):
            return MUTED if st is None else ("#e05a5a" if st.get("err") else ACCENT_HI)

        if time.time() - state.get("srv_t", 0) > 120:
            state["srv_t"] = time.time()
            for k in list(srv_cache):
                srv_cache.pop(k)

            def ping_all(lst):
                def one(sv):
                    try:
                        srv_cache[sv["ip"]] = server_status(sv["ip"])
                    except Exception as e:
                        srv_cache[sv["ip"]] = {"err": str(e)}
                with concurrent.futures.ThreadPoolExecutor(6) as ex:
                    list(ex.map(one, lst))
            threading.Thread(target=ping_all, args=(load_servers(),), daemon=True).start()

        def refresh_status():
            alive = False
            for ip, lab in srv_labels.items():
                try:
                    if not lab.winfo_exists():
                        continue
                except tk.TclError:
                    continue
                alive = True
                st = srv_cache.get(ip)
                lab.configure(text=srv_text(st), fg=srv_color(st))
            if alive and any(srv_cache.get(ip) is None for ip in srv_labels):
                win.after(500, refresh_status)
        win.after(500, refresh_status)
        for i, s in enumerate(load_servers()):
            r = tk.Frame(box, bg=CARD if i % 2 else CARD_HI, padx=12, pady=8)
            r.pack(fill="x")
            name, _, modes = s["name"].partition(" - ")
            icon = os.path.join(ROOT, s.get("icon", ""))
            if s.get("icon") and os.path.isfile(icon):
                tk.Label(r, image=image(icon), bg=r["bg"]).pack(side="left", padx=(0, 12))
            nf = tk.Frame(r, bg=r["bg"])
            nf.pack(side="left")
            tk.Label(nf, text=name, font=(FONT, 11, "bold"), fg=TEXT, bg=r["bg"], width=15, anchor="w").pack(anchor="w")
            st = srv_cache.get(s["ip"])
            lab = tk.Label(nf, text=srv_text(st), font=(FONT, 8, "bold"), fg=srv_color(st), bg=r["bg"], anchor="w")
            lab.pack(anchor="w")
            srv_labels[s["ip"]] = lab
            tk.Label(r, text=modes, font=(FONT, 10), fg="#c3c7d1", bg=r["bg"], width=26, anchor="w").pack(side="left")
            tk.Label(r, text=s["ip"], font=("Consolas", 10), fg=ACCENT_HI, bg=r["bg"], width=23, anchor="w").pack(side="left")
            tk.Label(r, text="%s   %s" % (s.get("versions", ""), s.get("lang", "")), font=(FONT, 9),
                     fg=MUTED, bg=r["bg"], anchor="w").pack(side="left")

            def copy(ip=s["ip"]):
                win.clipboard_clear(); win.clipboard_append(ip)
                toast("Адрес скопирован: " + ip)
            small_button(r, "Копировать адрес", copy, bg=r["bg"], icon="ic_copy.png").pack(side="right")
            small_button(r, "Подробнее", lambda s=s: open_server(s), bg=r["bg"]).pack(side="right")
            clickable(r, lambda s=s: open_server(s))
        tk.Label(inner, text="Как играть: выбери «Без модов» или «Только шейдеры», зайди в «Сетевую игру», "
                             "при первом входе напиши /register пароль пароль, потом /login пароль.",
                 font=(FONT, 10), fg=MUTED, bg=BG, wraplength=900, justify="left", anchor="w"
                 ).grid(row=2, column=0, columnspan=2, sticky="we", pady=(12, 0))

    # --- вкладка «С другом» ---
    def net_info():
        """Адреса Hamachi/Radmin и роутера. PowerShell отвечает до 20 с, поэтому в фоне и с кэшем на минуту."""
        c = state.get("net_cache")
        if c and time.time() - c["t"] < 60:
            return c
        if not state.get("net_loading"):
            state["net_loading"] = True

            def work():
                res = {"vpn": vpn_addresses(), "lan": lan_ip(), "t": time.time()}
                state["net_cache"] = res
                state["net_loading"] = False
            threading.Thread(target=work, daemon=True).start()

            def poll():
                if state.get("net_loading"):
                    win.after(300, poll)
                elif state["tab"] == "friend":
                    show("friend", animated=False)
            win.after(300, poll)
        return c or {"vpn": {}, "lan": None, "t": 0, "loading": True}

    def build_friend():
        section("Игра с другом", 0, "свой сервер не нужен: один играет в свой мир, второй к нему подключается")
        hero = tk.Canvas(inner, height=150, bg=BG, highlightthickness=0, bd=0)
        hero.grid(row=1, column=0, columnspan=2, sticky="we", padx=(0, 14), pady=(0, 14))
        hero_img = art("friends_wide.png", 1, 1)
        hid = hero.create_image(0, -20, image=hero_img, anchor="ne")
        hero.create_image(0, 0, image=art("shade_left.png", 1, 1), anchor="nw")
        hero.create_text(22, 52, text="Играйте вместе", font=(FONT, 20, "bold"), fill="white", anchor="w")
        hero.create_text(22, 88, text="Hamachi, Radmin VPN, ZeroTier или одна Wi-Fi сеть: выбери ниже и следуй шагам",
                         font=(FONT, 10), fill="#d7deea", anchor="w")
        hero.bind("<Configure>", lambda e: hero.coords(hid, e.width, -20))
        nc = net_info()
        vpn = nc["vpn"]
        if state.get("net") not in NET_KINDS:
            state["net"] = "Hamachi" if "Hamachi" in vpn or not vpn else next(iter(vpn))
        kind = state["net"]
        info = NET_SETUP[kind]
        addr = nc["lan"] if kind == LAN_KIND else vpn.get(kind)
        sites = {"Hamachi": "vpn.net", "Radmin VPN": "radmin-vpn.com", "ZeroTier": "zerotier.com"}

        def copy(text, what):
            win.clipboard_clear(); win.clipboard_append(text)
            toast("%s скопирован" % what)

        def open_net():
            if not open_vpn(kind):
                messagebox.showinfo(kind, "%s не найден на этом компьютере. Скачай его с %s и установи." % (kind, sites.get(kind, "")))

        def field(parent, value="", readonly=False):
            e = tk.Entry(parent, font=("Consolas", 10), bg=CARD_HI, fg=TEXT, insertbackground=TEXT,
                         relief="flat", highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT)
            e.insert(0, value)
            if readonly:
                e.configure(state="readonly", readonlybackground=CARD_HI)
            return e

        def para(parent, text, n=None, color="#c3c7d1", wrap=410):
            tk.Label(parent, text=("%d.  %s" % (n, text)) if n else text, font=(FONT, 10), fg=color, bg=parent["bg"],
                     justify="left", anchor="w", wraplength=wrap).pack(fill="x", pady=2)

        def net_line(parent):
            if addr:
                tk.Label(parent, text="● %s: %s" % (kind, addr), font=(FONT, 10, "bold"), fg=ACCENT_HI,
                         bg=CARD).pack(anchor="w", pady=(6, 10))
            elif nc.get("loading"):
                tk.Label(parent, text="● Проверяю подключение...", font=(FONT, 10, "bold"), fg=GOLD,
                         bg=CARD).pack(anchor="w", pady=(6, 10))
            else:
                text = "● Нет подключения к роутеру" if kind == LAN_KIND else "● %s не включён" % kind
                tk.Label(parent, text=text, font=(FONT, 10, "bold"), fg="#e05a5a", bg=CARD).pack(anchor="w", pady=(6, 10))

        # выбор сети
        sel = tk.Frame(inner, bg=BG)
        sel.grid(row=2, column=0, columnspan=2, sticky="we", pady=(0, 12))
        tk.Label(sel, text="Через что играете:", font=(FONT, 11, "bold"), fg=TEXT, bg=BG).pack(side="left", padx=(0, 10))
        for k in NET_KINDS:
            on = k == kind
            text = k + ("  ●" if k in vpn else "")
            b = tk.Label(sel, text=text, font=(FONT, 10, "bold"), bg=ACCENT if on else CARD_HI,
                         fg="white" if on else TEXT, padx=14, pady=7, cursor="hand2")
            b.pack(side="left", padx=(0, 6))
            b.bind("<Button-1>", lambda e, k=k: (state.update(net=k), show("friend")))
        tk.Label(sel, text="● = включён на этом компьютере", font=(FONT, 9), fg=MUTED, bg=BG).pack(side="left", padx=8)

        # настройка
        setup = tk.Frame(inner, bg=CARD, padx=16, pady=14)
        setup.grid(row=3, column=0, columnspan=2, sticky="we", padx=(0, 14), pady=(0, 14))
        tk.Label(setup, text="Настройка перед первой игрой: делают оба", font=(FONT, 14, "bold"),
                 fg=TEXT, bg=CARD, anchor="w").pack(fill="x", pady=(0, 6))
        for i, line in enumerate(info["setup"], 1):
            para(setup, line, i, wrap=900)
        br = tk.Frame(setup, bg=CARD)
        br.pack(fill="x", pady=(10, 0))

        def check():
            run_task("Проверяю настройки Windows, до 20 секунд", lambda log: network_checks(kind),
                     lambda res, logs: state.update(checks=res))
        big_button(br, "Проверить настройки", check, icon="ic_check.png").pack(side="left")
        if kind != LAN_KIND:
            small_button(br, "Открыть %s" % kind, open_net).pack(side="left", padx=(8, 0))
        small_button(br, "Открыть брандмауэр", open_firewall).pack(side="left", padx=(8, 0))
        small_button(br, "Разрешить Java", open_firewall_apps).pack(side="left", padx=(8, 0))
        chk = state.get("checks")
        if chk and chk.get("kind") == kind:
            box = tk.Frame(setup, bg=PANEL, padx=12, pady=8)
            box.pack(fill="x", pady=(12, 0))
            for text, good in describe_checks(chk):
                mark, color = ("✓", ACCENT_HI) if good else (("✗", "#e05a5a") if good is False else ("!", GOLD))
                tk.Label(box, text="%s  %s" % (mark, text), font=(FONT, 10, "bold" if good is False else "normal"),
                         fg=color, bg=PANEL, justify="left", anchor="w", wraplength=900).pack(fill="x", pady=1)

        # хост
        host = card(inner, 0, 4)
        tk.Label(host, text="Я создаю игру", font=(FONT, 15, "bold"), fg=TEXT, bg=CARD, anchor="w").pack(fill="x")
        net_line(host)
        for i, line in enumerate([info["host"],
                                  "Во вкладке «Карты» выбери карту, нажми «Играть» и зайди в мир.",
                                  "В игре: Esc -> «Открыть для сети» -> «Начать». Если Windows спросит про Java, "
                                  "поставь обе галочки и нажми «Разрешить».",
                                  "Нажми «Найти мою игру» и отправь другу код приглашения."], 1):
            para(host, line, i)
        row = tk.Frame(host, bg=CARD)
        row.pack(fill="x", pady=(10, 0))

        def find_game():
            run_task("Ищу твою игру", lambda log: host_setup(kind), lambda res, logs: state.update(host=res))
        big_button(row, "Найти мою игру", find_game, icon="ic_compass.png").pack(side="left")
        h = state.get("host")
        if h:
            box = tk.Frame(host, bg=PANEL, padx=12, pady=10)
            box.pack(fill="x", pady=(12, 0))
            tk.Label(box, text="Адрес для друга (%s)" % h["vpn"], font=(FONT, 9), fg=MUTED, bg=PANEL).pack(anchor="w")
            ar = tk.Frame(box, bg=PANEL)
            ar.pack(fill="x")
            tk.Label(ar, text=h["address"], font=("Consolas", 15, "bold"), fg=ACCENT_HI, bg=PANEL).pack(side="left")
            small_button(ar, "Копировать", lambda: copy(h["address"], "Адрес"), bg=PANEL, icon="ic_copy.png").pack(side="right")
            tk.Label(box, text="Код приглашения: друг вставит его, и программа сама всё настроит",
                     font=(FONT, 9), fg=MUTED, bg=PANEL).pack(anchor="w", pady=(8, 2))
            cr = tk.Frame(box, bg=PANEL)
            cr.pack(fill="x")
            field(cr, h["code"], readonly=True).pack(side="left", fill="x", expand=True, ipady=4)
            small_button(cr, "Копировать код", lambda: copy(h["code"], "Код"), bg=PANEL, icon="ic_invite.png").pack(side="right", padx=(8, 0))
            tk.Label(box, text="Игра отвечает: %s, игроков %s из %s. Сборка: %s" % (
                h["info"]["version"], h["info"]["online"], h["info"]["max"], h["pack"] or "без модов, " + str(h["tl"])),
                font=(FONT, 9), fg=MUTED, bg=PANEL, wraplength=400, justify="left").pack(anchor="w", pady=(8, 0))

        # гость
        guest = card(inner, 1, 4)
        tk.Label(guest, text="Я подключаюсь", font=(FONT, 15, "bold"), fg=TEXT, bg=CARD, anchor="w").pack(fill="x")
        net_line(guest)
        for i, line in enumerate([info["guest"],
                                  "Вставь сюда код приглашения от друга и нажми «Подключиться».",
                                  "Программа включит ту же сборку и версию и поставит игру друга первой строкой "
                                  "в «Сетевой игре». Запусти игру и подключись."], 1):
            para(guest, line, i)
        tk.Label(guest, text="Код приглашения или адрес", font=(FONT, 9), fg=MUTED, bg=CARD).pack(anchor="w", pady=(10, 2))
        code_in = field(guest, state.get("join_text", ""))
        code_in.pack(fill="x", ipady=5)
        row2 = tk.Frame(guest, bg=CARD)
        row2.pack(fill="x", pady=(10, 0))

        def join():
            text = code_in.get().strip()
            if not text:
                messagebox.showinfo("Код", "Вставь код приглашения от друга."); return
            try:
                _addr, _tl, pack_name = parse_invite(text)
            except Exception as e:
                messagebox.showwarning("Код", str(e)); return
            if not check_game():
                return
            state["join_text"] = text
            pk = next((p for p in find_packs() if p["name"] == pack_name), None) if pack_name else None
            ids = missing_items(pack=pk) if pk else []
            if ids:  # у друга сборка, которую ещё не скачивали: сначала скачать
                ensure_items(ids, lambda: do_join(text), "Скачать «%s»" % pack_name.rsplit(" (", 1)[0])
                return
            do_join(text)

        def do_join(text):
            def after(ok, logs):
                state["join_logs"] = logs
                if auto_var.get() and not tlauncher_running():
                    launch_now()
                else:
                    toast("Готово: игра друга добавлена в «Сетевую игру».", "ok",
                          ("Запустить", launch_now), 9000)
            run_task("Подключаюсь к другу", lambda log: join_friend(text, None, log), after)
        big_button(row2, "Подключиться", join, icon="ic_invite.png").pack(side="left")
        for line in state.get("join_logs", []):
            color = ACCENT_HI if "отвечает:" in line else ("#e0a45a" if "не отвечает" in line or "Внимание" in line else "#c3c7d1")
            tk.Label(guest, text=line, font=(FONT, 9), fg=color, bg=CARD, wraplength=410,
                     justify="left", anchor="w").pack(fill="x", pady=(4, 0))

        tk.Label(inner, text="Важно: ники в TLauncher у вас должны быть разными, иначе второй не зайдёт. "
                             "Сеть выбирайте одну и ту же. Если друг не подключается, пусть тот, кто создал игру, первым делом "
                             "нажмёт «Проверить настройки».",
                 font=(FONT, 10), fg=MUTED, bg=BG, wraplength=900, justify="left", anchor="w"
                 ).grid(row=5, column=0, columnspan=2, sticky="we", pady=(4, 0))

    def auto_wrap(root):
        """Перенос текста по фактической ширине: растянутые по ширине подписи не обрезаются в узком окне."""
        stack = [root]
        while stack:
            w = stack.pop()
            stack.extend(w.winfo_children())
            if not isinstance(w, tk.Label):
                continue
            try:
                if not int(w.cget("wraplength") or 0):
                    continue
                mgr = w.winfo_manager()
                stretched = (mgr == "pack" and w.pack_info().get("fill") in ("x", "both")) or \
                            (mgr == "grid" and set("we") <= set(str(w.grid_info().get("sticky", ""))))
            except tk.TclError:
                continue
            if stretched:
                w.bind("<Configure>", lambda e, w=w: w.configure(wraplength=max(180, e.width - 6)), add="+")

    # --- вкладка «Лаунчеры» ---
    def build_launchers():
        launchers = load_launchers()
        section("Лаунчеры", 0, "чем запускать Minecraft: Portalis умеет работать с каждым из них")
        if not launchers:
            empty_note(1, LIBRARY_NOTE if not library_found() else "Нет файла Лаунчеры\\launchers.json.")
            return
        installed = state.get("installed_launchers")
        if installed is None:
            box = {}
            th = threading.Thread(target=lambda: box.update(found=find_installed_launchers(launchers)), daemon=True)
            th.start()

            def poll():
                if th.is_alive():
                    win.after(250, poll); return
                state["installed_launchers"] = box.get("found", {})
                if state["tab"] == "launchers":
                    show("launchers", animated=False)
            poll()
            installed = {}
        mine = chosen_launcher()
        mode = launcher_mode(mine)
        info = tk.Frame(inner, bg=PANEL, padx=16, pady=12)
        info.grid(row=1, column=0, columnspan=2, sticky="we", padx=(0, 14), pady=(0, 14))
        tk.Label(info, text="Мой лаунчер: %s" % launcher_title(mine), font=(FONT, 12, "bold"), fg=TEXT, bg=PANEL,
                 anchor="w").pack(fill="x")
        how = {"tl": "Portalis ставит карты и моды в папку игры и сам выбирает в нём версию.",
               "legacy": "Portalis ставит карты и моды в его папку игры и сам выбирает версию, если лаунчер закрыт.",
               "profiles": "Portalis ставит карты и моды в папку игры и создаёт в нём профиль «Portalis» с нужной версией.",
               "mrpack": "Portalis собирает пакет (сборка, мир карты, серверы) и передаёт его лаунчеру: "
                         "он сам создаст экземпляр с нужной версией."}.get(mode, "")
        tk.Label(info, text=how + ("" if state.get("installed_launchers") is not None else "   Ищу установленные лаунчеры..."),
                 font=(FONT, 10), fg=MUTED, bg=PANEL, anchor="w", justify="left", wraplength=900).pack(fill="x", pady=(4, 0))
        if mode != "mrpack":
            gr = tk.Frame(info, bg=PANEL)
            gr.pack(fill="x", pady=(8, 0))
            custom = bool(load_settings().get("game_dir"))
            tk.Label(gr, text="Папка игры: " + MC + ("  (своя)" if custom else ""), font=(FONT, 9), fg="#c3c7d1",
                     bg=PANEL, anchor="w").pack(side="left")

            def pick_dir():
                d = filedialog.askdirectory(parent=win, title="Папка игры (где лежат mods, saves, versions)", initialdir=MC)
                if d:
                    s = load_settings(); s["game_dir"] = os.path.normpath(d); save_settings(s)
                    set_game_dir()
                    toast("Папка игры: " + MC)
                    show("launchers", animated=False)

            def reset_dir():
                s = load_settings(); s.pop("game_dir", None); save_settings(s)
                set_game_dir()
                toast("Папка игры по умолчанию: " + MC)
                show("launchers", animated=False)
            if custom:
                small_button(gr, "Как у лаунчера", reset_dir, bg=PANEL).pack(side="right")
            small_button(gr, "Другая папка...", pick_dir, bg=PANEL, icon="ic_settings.png").pack(side="right", padx=4)
            small_button(gr, "Открыть", lambda: os.makedirs(MC, exist_ok=True) or os.startfile(MC), bg=PANEL,
                         icon="ic_folder.png").pack(side="right", padx=4)
        for i, x in enumerate(launchers):
            c = card(inner, i % 2, 2 + i // 2)
            top = tk.Frame(c, bg=CARD)
            top.pack(fill="x")
            tk.Label(top, image=image(os.path.join(ROOT, x["icon"])), bg=CARD).pack(side="left", anchor="n")
            head = tk.Frame(top, bg=CARD, padx=14)
            head.pack(side="left", fill="both", expand=True)
            tk.Label(head, text=x["name"], font=(FONT, 14, "bold"), fg=TEXT, bg=CARD, anchor="w", justify="left",
                     wraplength=300).pack(fill="x")
            bl = tk.Frame(head, bg=CARD)
            bl.pack(fill="x", pady=(4, 4))
            badge(bl, "нужна лицензия" if x["license"] else "без лицензии",
                  "#5a4c1c" if x["license"] else "#2f6b34", "#f3d27a" if x["license"] else "white").pack(side="left", padx=(0, 6))
            if x["id"] in installed:
                badge(bl, "установлен", BLUE, "white").pack(side="left", padx=(0, 6))
            if x["id"] == mine:
                badge(bl, "мой лаунчер", ACCENT, "white").pack(side="left")
            tk.Label(head, text=x["short"], font=(FONT, 9), fg=MUTED, bg=CARD, anchor="w", justify="left",
                     wraplength=300).pack(fill="x")
            for p in x["pros"]:
                tk.Label(c, text="+  " + p, font=(FONT, 10), fg=ACCENT_HI, bg=CARD, anchor="w", justify="left",
                         wraplength=400).pack(fill="x", pady=(1, 0))
            for p in x["cons"]:
                tk.Label(c, text="−  " + p, font=(FONT, 10), fg="#e0a45a", bg=CARD, anchor="w", justify="left",
                         wraplength=400).pack(fill="x", pady=(1, 0))
            tk.Label(c, text="С Portalis: " + x["compat_text"], font=(FONT, 9, "bold"), fg="#c3c7d1", bg=CARD,
                     anchor="w", justify="left", wraplength=400).pack(fill="x", pady=(8, 2))
            where = installed.get(x["id"])
            if where and os.path.isabs(where):
                tk.Label(c, text="Найден: " + where, font=(FONT, 8), fg=MUTED, bg=CARD, anchor="w", justify="left",
                         wraplength=400).pack(fill="x", pady=(0, 6))
            row = tk.Frame(c, bg=CARD)
            row.pack(fill="x", side="bottom", pady=(6, 0))

            def make_mine(x=x):
                s = load_settings(); s["launcher"] = x["id"]; save_settings(s)
                set_game_dir()
                toast("Теперь мой лаунчер: " + x["name"]); refresh_foot()
                show("launchers", animated=False)

            def site(x=x):
                webbrowser.open(x["site"])
                toast("Открываю официальный сайт: " + x["site"], "info")

            def install(x=x):
                def done(path, logs):
                    state["installed_launchers"] = None
                    if path:
                        os.startfile(path)
                        toast("Запускаю установщик %s. После установки вернись в Portalis: лаунчер появится "
                              "в списке сам." % x["name"], "ok", ms=9000)
                last = {"t": 0}

                def work(log):
                    def prog(d):
                        if time.time() - last["t"] > 0.4:
                            last["t"] = time.time()
                            log("Скачиваю установщик %s: %s" % (x["name"], fmt_mb(d)))
                    try:
                        return download_installer(x, log, prog)
                    except Exception as e:
                        log("Прямая ссылка не сработала (%s), открываю сайт" % e)
                        webbrowser.open(x["site"])
                        return None
                run_task("Скачиваю установщик %s" % x["name"], work, done)

            def set_path(x=x):
                f = filedialog.askopenfilename(parent=win, title="Где лежит %s?" % x["name"],
                                               filetypes=[("Программа", "*.exe"), ("Все файлы", "*.*")])
                if f:
                    s = load_settings(); s.setdefault("launcher_paths", {})[x["id"]] = os.path.normpath(f); save_settings(s)
                    state["installed_launchers"] = None
                    toast("Путь к %s сохранён." % x["name"])
                    show("launchers", animated=False)
            direct = (x.get("download") or {}).get("kind") in ("url", "github", "modrinth")
            if x["id"] in installed:
                big_button(row, "Запустить", lambda t=installed[x["id"]], n=x["name"]: (run_target(t), toast("Открываю " + n)),
                           color=CARD_HI, hover=LINE, icon="ic_play.png").pack(side="right")
            elif direct:
                big_button(row, "Скачать и установить", install, icon="ic_download.png").pack(side="right")
            else:
                big_button(row, "Скачать с сайта", site, color=CARD_HI, hover=LINE, icon="ic_globe.png").pack(side="right")
            small_button(row, "Сайт", site, bg=CARD, icon="ic_globe.png").pack(side="right", padx=4)
            if x["id"] != mine:
                small_button(row, "Сделать моим", make_mine, bg=CARD, icon="ic_check.png").pack(side="left")
            small_button(row, "Указать путь...", set_path, bg=CARD, icon="ic_settings.png").pack(side="left", padx=4)

    # --- картинки из сети и превью скинов: грузятся в фоне, окно не ждёт ---
    pic_cache = {}       # ключ -> PIL-картинка (None - не удалось)
    pic_wait = {}        # ключ -> [виджеты, ждущие картинку]
    pic_busy = set()
    pic_pool = concurrent.futures.ThreadPoolExecutor(6)

    def blank(w, h):
        """Пустая картинка: пока настоящая грузится, место под неё уже занято (размер в пикселях)."""
        im = tk.PhotoImage(width=w, height=h)
        images.append(im)
        return im

    def pil_photo(im):
        ph = ImageTk.PhotoImage(im)
        images.append(ph)
        return ph

    def want_pic(key, producer, widget):
        """producer() -> PIL-картинка; вызывается в фоне, результат кэшируется по key."""
        if key in pic_cache:
            if pic_cache[key] is not None:
                widget.configure(image=pil_photo(pic_cache[key]))
            return
        pic_wait.setdefault(key, []).append(widget)
        if key in pic_busy:
            return
        pic_busy.add(key)

        def job():
            try:
                pic_cache[key] = producer()
            except Exception:
                pic_cache[key] = None
        pic_pool.submit(job)
        if not state.get("pic_poll"):
            state["pic_poll"] = True
            win.after(150, pic_poll)

    def pic_poll():
        for key in [k for k in pic_wait if k in pic_cache]:
            for w in pic_wait.pop(key):
                try:
                    if w.winfo_exists() and pic_cache[key] is not None:
                        w.configure(image=pil_photo(pic_cache[key]))
                except tk.TclError:
                    pass
        if pic_wait:
            win.after(150, pic_poll)
        else:
            state["pic_poll"] = False

    def web_icon(url, size, key):
        """Значок проекта Modrinth (webp/png) -> PIL size x size, с кэшем на диске."""
        d = os.path.join(ROOT, "_update", "icons")
        p = os.path.join(d, "%s_%d.png" % (re.sub(r"\W", "_", key)[:60], size))
        if os.path.isfile(p):
            return PILImage.open(p).convert("RGBA")
        im = PILImage.open(io.BytesIO(_get(url, 20, 2, MR_UA))).convert("RGBA")
        im.thumbnail((size, size), PILImage.NEAREST if im.width <= size else PILImage.LANCZOS)
        out = PILImage.new("RGBA", (size, size), (0, 0, 0, 0))
        out.alpha_composite(im, ((size - im.width) // 2, (size - im.height) // 2))
        os.makedirs(d, exist_ok=True)
        out.save(p)
        return out

    def hero(row, img, title, sub):
        hc = tk.Canvas(inner, height=140, bg=BG, highlightthickness=0, bd=0)
        hc.grid(row=row, column=0, columnspan=2, sticky="we", padx=(0, 14), pady=(0, 14))
        hid = hc.create_image(0, -24, image=art(img, 1, 1), anchor="ne")
        hc.create_image(0, 0, image=art("shade_left.png", 1, 1), anchor="nw")
        for dx, dy, color in ((2, 2, "#000000"), (0, 0, "white")):
            hc.create_text(22 + dx, 48 + dy, text=title, font=(FONT, 19, "bold"), fill=color, anchor="w")
        hc.create_text(23, 84, text=sub, font=(FONT, 10), fill="#000000", anchor="w", width=520)
        hc.create_text(22, 83, text=sub, font=(FONT, 10), fill="#d7deea", anchor="w", width=520)
        hc.bind("<Configure>", lambda e: hc.coords(hid, e.width, -24))
        return hc

    def chip_row(parent, items, current, on_pick, bg=BG):
        for value, text in items:
            on = current == value
            b = tk.Label(parent, text=text, font=(FONT, 9, "bold"), bg=ACCENT if on else CARD_HI,
                         fg="white" if on else TEXT, padx=10, pady=5, cursor="hand2")
            b.pack(side="left", padx=(0, 5))
            b.bind("<Button-1>", lambda e, v=value: on_pick(v))
            if not on:
                b.bind("<Enter>", lambda e, b=b: fade_color(b, "bg", LINE, 120))
                b.bind("<Leave>", lambda e, b=b: fade_color(b, "bg", CARD_HI, 160))

    def fmt_count(n):
        n = int(n or 0)
        if n >= 1000000:
            return "%.1f млн" % (n / 1e6)
        if n >= 1000:
            return "%d тыс." % (n // 1000)
        return str(n)

    def plural(n, one, few, many):
        n = abs(int(n))
        if n % 10 == 1 and n % 100 != 11:
            return one
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return few
        return many

    # --- вкладка «Конструктор» ---
    def builder_state():
        cb = state.get("cb")
        if cb is None:
            cur = current() or {}
            gv = cur.get("version_dir", "Fabric 26.1.2").partition(" ")[2] or "26.1.2"
            cb = state["cb"] = {"gv": gv, "loader": "fabric", "ptype": "mod", "q": "", "cat": None, "index": "downloads",
                                "results": [], "total": 0, "sel": {}, "name": "Моя сборка", "qid": 0,
                                "versions": ["26.2", "26.1.2", "1.21.8", "1.21.1", "1.20.1", "1.19.2", "1.18.2", "1.16.5",
                                             "1.12.2"]}
            def load_versions():
                vs = mr_game_versions()
                if vs:
                    cb["versions"] = vs[:40]
            threading.Thread(target=load_versions, daemon=True).start()
            builder_query()
        return cb

    def builder_query(more=False):
        cb = state["cb"]
        cb["qid"] += 1
        qid = cb["qid"]
        cb["loading"] = True
        cb["error"] = None
        offset = len(cb["results"]) if more else 0
        if not more:
            cb["results"] = []

        def work():
            try:
                hits, total = mr_search(cb["q"], cb["gv"], cb["loader"], cb["ptype"], cb["cat"], cb["index"], offset, 20)
                if qid == cb["qid"]:
                    cb["results"] = (cb["results"] if more else []) + hits
                    cb["total"] = total
            except Exception as e:
                cb["error"] = str(e)[:120]
            cb["loading"] = False

            def redraw():
                if qid == cb["qid"] and state["tab"] == "builder":
                    state["keep_scroll"] = more
                    show("builder", animated=False)
            win.after(0, redraw)
        threading.Thread(target=work, daemon=True).start()

    def builder_set(**kw):
        cb = state["cb"]
        cb.update(kw)
        builder_query()
        show("builder", animated=False)

    def build_builder():
        cb = builder_state()
        section("Конструктор сборок", 0, "любые моды с Modrinth: нужные библиотеки Portalis добавит сам")
        n_sel = len(cb["sel"])
        hero(1, "builder_wide.png", "Собери свою сборку",
             "Найди моды, шейдеры и текстуры. Portalis проверит, что они подходят к версии игры, "
             "и сам добавит всё, без чего они не запустятся.")
        opt = tk.Frame(inner, bg=PANEL, padx=14, pady=10)
        opt.grid(row=2, column=0, columnspan=2, sticky="we", padx=(0, 14), pady=(0, 10))
        r1 = tk.Frame(opt, bg=PANEL)
        r1.pack(fill="x")
        tk.Label(r1, text="Версия игры:", font=(FONT, 10, "bold"), fg=TEXT, bg=PANEL).pack(side="left", padx=(0, 8))
        gvar = tk.StringVar(value=cb["gv"])
        gb = tk.Label(r1, text=cb["gv"] + "   ▾", font=(FONT, 10, "bold"), bg=CARD_HI, fg=TEXT, padx=12, pady=5,
                      cursor="hand2")
        gb.pack(side="left", padx=(0, 18))
        menu = tk.Menu(win, tearoff=0, bg=CARD_HI, fg=TEXT, activebackground=ACCENT, activeforeground="white",
                       font=(FONT, 10), bd=0)
        for v in cb["versions"]:
            menu.add_command(label="  " + v + "  ", command=lambda v=v: builder_set(gv=v))
        gb.bind("<Button-1>", lambda e: menu.tk_popup(gb.winfo_rootx(), gb.winfo_rooty() + gb.winfo_height()))
        tk.Label(r1, text="Загрузчик:", font=(FONT, 10, "bold"), fg=TEXT, bg=PANEL).pack(side="left", padx=(0, 8))
        chip_row(r1, [(k, v) for k, v in LOADER_TITLES.items()], cb["loader"], lambda v: builder_set(loader=v), PANEL)
        tk.Label(r1, text="   Что ищем:", font=(FONT, 10, "bold"), fg=TEXT, bg=PANEL).pack(side="left", padx=(0, 8))
        chip_row(r1, MR_TYPES, cb["ptype"], lambda v: builder_set(ptype=v, cat=None), PANEL)
        _ = gvar
        r2 = tk.Frame(opt, bg=PANEL)
        r2.pack(fill="x", pady=(10, 0))
        sbox = tk.Frame(r2, bg=CARD_HI, padx=8, pady=2)
        sbox.pack(side="left")
        tk.Label(sbox, image=art("ic_search.png", 1, 1), bg=CARD_HI).pack(side="left")
        e = tk.Entry(sbox, font=(FONT, 10), bg=CARD_HI, fg=TEXT, insertbackground=TEXT, relief="flat", width=34,
                     highlightthickness=0, bd=0)
        e.pack(side="left", padx=(6, 0), ipady=4)
        e.insert(0, cb["q"])
        state["search_entry"] = e

        def typed(ev=None):
            v = e.get().strip()
            if v == cb["q"]:
                return
            cb["q"] = v
            if state.get("_bq"):
                win.after_cancel(state["_bq"])
            state["_bq"] = win.after(450, lambda: (state.update(focus_builder=True), builder_query()))
        e.bind("<KeyRelease>", typed)
        e.bind("<Return>", lambda ev: builder_query())
        e.bind("<Escape>", lambda ev: (e.delete(0, "end"), typed()))
        if state.pop("focus_builder", None):
            e.focus_set()
            e.icursor("end")
        tk.Label(r2, text="   Сначала:", font=(FONT, 10, "bold"), fg=TEXT, bg=PANEL).pack(side="left", padx=(0, 8))
        chip_row(r2, [("downloads", "Популярные"), ("relevance", "Подходящие"), ("updated", "Свежие"),
                      ("newest", "Новые")], cb["index"], lambda v: builder_set(index=v), PANEL)
        if cb["ptype"] == "mod":
            r3 = tk.Frame(opt, bg=PANEL)
            r3.pack(fill="x", pady=(10, 0))
            r4 = tk.Frame(opt, bg=PANEL)
            r4.pack(fill="x", pady=(5, 0))
            half = (len(MR_CATEGORIES) + 1) // 2
            chip_row(r3, [(None, "Все")] + MR_CATEGORIES[:half - 1], cb["cat"], lambda v: builder_set(cat=v), PANEL)
            chip_row(r4, MR_CATEGORIES[half - 1:], cb["cat"], lambda v: builder_set(cat=v), PANEL)
        # «Моя сборка»
        sel = tk.Frame(inner, bg=CARD, padx=14, pady=12, highlightthickness=1, highlightbackground=ACCENT if n_sel else CARD)
        sel.grid(row=3, column=0, columnspan=2, sticky="we", padx=(0, 14), pady=(0, 14))
        top = tk.Frame(sel, bg=CARD)
        top.pack(fill="x")
        tk.Label(top, image=art("tab_builder.png", 1, 1), bg=CARD).pack(side="left")
        tk.Label(top, text="  Моя сборка:", font=(FONT, 12, "bold"), fg=TEXT, bg=CARD).pack(side="left")
        ne = tk.Entry(top, font=(FONT, 11), bg=CARD_HI, fg=TEXT, insertbackground=TEXT, relief="flat", width=24,
                      highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT)
        ne.insert(0, cb["name"])
        ne.pack(side="left", padx=(8, 10), ipady=3)
        ne.bind("<KeyRelease>", lambda ev: cb.update(name=ne.get()))
        tk.Label(top, text="%s %s · %s %d %s" % (LOADER_TITLES[cb["loader"]], cb["gv"], "выбрано" if n_sel != 1 else "выбран",
                                               n_sel, plural(n_sel, "проект", "проекта", "проектов")),
                 font=(FONT, 10), fg=MUTED, bg=CARD).pack(side="left")
        if n_sel:
            big_button(top, "Собрать сборку", lambda: builder_build(), icon="ic_add.png").pack(side="right")
            small_button(top, "Очистить", lambda: (cb["sel"].clear(), show("builder", animated=False)), bg=CARD,
                         icon="ic_delete.png").pack(side="right", padx=6)
            chips = tk.Frame(sel, bg=CARD)
            chips.pack(fill="x", pady=(10, 0))
            line = tk.Frame(chips, bg=CARD)
            line.pack(fill="x")
            width = 0
            for pid, p in list(cb["sel"].items()):
                t = "%s  ✕" % p["title"]
                w = 22 + len(t) * 7
                if width + w > 900:
                    line = tk.Frame(chips, bg=CARD)
                    line.pack(fill="x", pady=(5, 0))
                    width = 0
                width += w
                b = tk.Label(line, text=t, font=(FONT, 9, "bold"), bg=CARD_HI, fg=TEXT, padx=9, pady=4, cursor="hand2")
                b.pack(side="left", padx=(0, 5))
                b.bind("<Button-1>", lambda ev, pid=pid: (cb["sel"].pop(pid, None), show("builder", animated=False)))
                b.bind("<Enter>", lambda ev, b=b: fade_color(b, "bg", "#5a2b2b", 120))
                b.bind("<Leave>", lambda ev, b=b: fade_color(b, "bg", CARD_HI, 160))
        else:
            tk.Label(sel, text="Нажимай «Добавить» у модов ниже. Потом «Собрать сборку»: Portalis подберёт версии, "
                               "добавит обязательные библиотеки и сохранит сборку — её можно будет включить, как любую другую.",
                     font=(FONT, 10), fg=MUTED, bg=CARD, anchor="w", justify="left", wraplength=900).pack(fill="x", pady=(8, 0))
        # результаты
        if cb.get("error"):
            empty_note(4, "Modrinth не ответил: %s. Проверь интернет и нажми F5." % cb["error"])
            return
        if cb.get("loading") and not cb["results"]:
            note = tk.Label(inner, text="Ищу на Modrinth...", font=(FONT, 12, "bold"), fg=GOLD, bg=BG, anchor="w")
            note.grid(row=4, column=0, columnspan=2, sticky="we", pady=10)
            return
        if not cb["results"]:
            empty_note(4, "Ничего не нашлось для %s %s. Попробуй другое слово, категорию «Все» или другую версию игры."
                       % (LOADER_TITLES[cb["loader"]], cb["gv"]))
            return
        tk.Label(inner, text="Найдено: %s" % fmt_count(cb["total"]), font=(FONT, 9), fg=MUTED, bg=BG, anchor="w").grid(
            row=4, column=0, columnspan=2, sticky="we", pady=(0, 6))
        cats = dict(MR_CATEGORIES)
        for i, h in enumerate(cb["results"]):
            c = card(inner, i % 2, 5 + i // 2)
            top = tk.Frame(c, bg=CARD)
            top.pack(fill="x")
            ic = tk.Label(top, bg=CARD, width=48, height=48, image=art("ic_add_48.png", 1, 1))
            ic.pack(side="left", anchor="n")
            if h.get("icon_url"):
                want_pic("mr:" + h["project_id"], lambda u=h["icon_url"], k=h["project_id"]: web_icon(u, 48, k), ic)
            info = tk.Frame(top, bg=CARD, padx=12)
            info.pack(side="left", fill="both", expand=True)
            tk.Label(info, text=h["title"], font=(FONT, 13, "bold"), fg=TEXT, bg=CARD, anchor="w", justify="left",
                     wraplength=300).pack(fill="x")
            tk.Label(info, text="%s  ·  ↓ %s" % (h.get("author", ""), fmt_count(h.get("downloads"))), font=(FONT, 9),
                     fg=MUTED, bg=CARD, anchor="w").pack(fill="x")
            bl = tk.Frame(info, bg=CARD)
            bl.pack(fill="x", pady=(4, 0))
            for cat in [x for x in h.get("display_categories") or h.get("categories", []) if x in cats][:3]:
                badge(bl, cats[cat]).pack(side="left", padx=(0, 5))
            tk.Label(c, text=h.get("description", ""), font=(FONT, 9), fg="#c3c7d1", bg=CARD, anchor="w", justify="left",
                     wraplength=400).pack(fill="x", pady=(8, 8))
            row = tk.Frame(c, bg=CARD)
            row.pack(fill="x", side="bottom")
            inn = h["project_id"] in cb["sel"]

            def toggle(h=h):
                if h["project_id"] in cb["sel"]:
                    cb["sel"].pop(h["project_id"])
                else:
                    cb["sel"][h["project_id"]] = {"project_id": h["project_id"], "title": h["title"],
                                                  "type": cb["ptype"], "icon": h.get("icon_url")}
                show("builder", animated=False)
            if inn:
                big_button(row, "В сборке  ✓", toggle, color=CARD_HI, hover="#5a2b2b").pack(side="right")
            else:
                big_button(row, "Добавить", toggle, icon="ic_add.png").pack(side="right")
            small_button(row, "Страница", lambda h=h: webbrowser.open("https://modrinth.com/%s/%s" % (
                h.get("project_type", "mod"), h.get("slug") or h["project_id"])), bg=CARD, icon="ic_globe.png").pack(
                side="right", padx=6)
        last = 5 + (len(cb["results"]) + 1) // 2
        if len(cb["results"]) < cb["total"]:
            mb = tk.Frame(inner, bg=BG)
            mb.grid(row=last, column=0, columnspan=2, pady=(0, 14))
            if cb.get("loading"):
                tk.Label(mb, text="Загружаю ещё...", font=(FONT, 10, "bold"), fg=GOLD, bg=BG).pack()
            else:
                big_button(mb, "Показать ещё", lambda: builder_query(more=True), color=CARD_HI, hover=LINE).pack()

    def builder_build():
        cb = state["cb"]
        name = (cb.get("name") or "").strip() or "Моя сборка"
        selection = list(cb["sel"].values())
        if not selection:
            return
        t = tk.Toplevel(win)
        t.title("Сборка «%s»" % name)
        t.configure(bg=BG)
        t.transient(win)
        W, H = 700, 640
        t.geometry("%dx%d+%d+%d" % (W, H, win.winfo_rootx() + (win.winfo_width() - W) // 2, win.winfo_rooty() + 40))
        fr = tk.Frame(t, bg=BG, padx=24, pady=20)
        fr.pack(fill="both", expand=True)
        hd = tk.Frame(fr, bg=BG)
        hd.pack(fill="x")
        tk.Label(hd, image=art("tab_builder_48.png", 1, 1), bg=BG).pack(side="left")
        tk.Label(hd, text="  «%s»  ·  %s %s" % (name, LOADER_TITLES[cb["loader"]], cb["gv"]), font=(FONT, 16, "bold"),
                 fg=TEXT, bg=BG).pack(side="left")
        msg = tk.Label(fr, text="Подбираю версии и зависимости...", font=(FONT, 10, "bold"), fg=GOLD, bg=BG, anchor="w",
                       justify="left", wraplength=W - 60)
        msg.pack(fill="x", pady=(12, 6))
        bar = tk.Canvas(fr, height=10, bg=LINE, highlightthickness=0, bd=0)
        bar.pack(fill="x")
        fill = bar.create_rectangle(0, 0, 0, 10, fill=ACCENT, width=0)
        lst = tk.Text(fr, bg=PANEL, fg="#c3c7d1", font=(FONT, 10), relief="flat", wrap="word", height=18,
                      highlightthickness=0, padx=12, pady=10)
        lst.pack(fill="both", expand=True, pady=(12, 0))
        for tag, col in (("h", TEXT), ("dep", "#9fd3a5"), ("bad", "#e0a45a"), ("muted", MUTED)):
            lst.tag_configure(tag, foreground=col)
        lst.tag_configure("h", font=(FONT, 11, "bold"))
        row = tk.Frame(fr, bg=BG)
        row.pack(fill="x", pady=(12, 0))
        box = {"cancel": threading.Event(), "step": "", "done": 0, "total": 1}

        def buttons(*bs):
            for w in row.winfo_children():
                w.destroy()
            for kind, text, cmd in bs:
                (big_button(row, text, cmd) if kind == "big" else small_button(row, text, cmd, bg=BG)).pack(
                    side="right", padx=(8, 0))
        buttons(("small", "Отмена", lambda: (box["cancel"].set(), t.destroy())))

        def resolve():
            try:
                box["files"], box["problems"] = resolve_build(selection, cb["gv"], cb["loader"],
                                                              log=lambda m: box.__setitem__("step", m))
            except Exception as e:
                box["err"] = str(e)
        th = threading.Thread(target=resolve, daemon=True)
        th.start()

        def poll():
            if not t.winfo_exists():
                return
            if th.is_alive():
                msg.configure(text="Подбираю версии и зависимости...  " + box.get("step", ""))
                t.after(200, poll)
                return
            if "err" in box:
                msg.configure(text="Не получилось: " + box["err"], fg="#e05a5a")
                return
            files, problems = box["files"], box["problems"]
            lst.delete("1.0", "end")
            picked = [f for f in files if f.get("picked")]
            deps = [f for f in files if not f.get("picked")]
            size = sum(f["size"] for f in files)
            lst.insert("end", "Выбрано (%d)\n" % len(picked), "h")
            for f in picked:
                lst.insert("end", "  •  %s  " % f["title"])
                lst.insert("end", "%s\n" % f["version"], "muted")
            if deps:
                lst.insert("end", "\nДобавлено само, без них не запустится (%d)\n" % len(deps), "h")
                for f in deps:
                    lst.insert("end", "  +  %s  " % f["title"], "dep")
                    lst.insert("end", "нужен для «%s»\n" % f.get("reason"), "muted")
            if problems:
                lst.insert("end", "\nВнимание\n", "h")
                for p in problems:
                    lst.insert("end", "  !  %s\n" % p, "bad")
            lst.configure(state="disabled")
            if not files:
                msg.configure(text="Ни один мод не подходит к %s %s." % (LOADER_TITLES[cb["loader"]], cb["gv"]), fg="#e05a5a")
                return
            msg.configure(text="Готово к сборке: %d %s, %s. Скачаю с Modrinth и сохраню в «Сборки»." % (
                len(files), plural(len(files), "файл", "файла", "файлов"), fmt_mb(size)), fg=TEXT)
            buttons(("big", "Скачать и сохранить  (%s)" % fmt_mb(size), lambda: save(files)),
                    ("small", "Назад к поиску", t.destroy))

        def save(files, replace=False):
            vdir = "%s %s" % (LOADER_TITLES[cb["loader"]], cb["gv"])
            exists = [p for p in find_packs() if p.get("name", "").rsplit(" (", 1)[0] == _safe_name(name)
                      and p["version_dir"] == vdir]
            if exists and not replace:
                if not exists[0].get("user"):
                    messagebox.showwarning("Название занято", "Так называется сборка из каталога. Придумай другое название.",
                                           parent=t)
                    return
                if not messagebox.askyesno("Заменить?", "Сборка «%s» для %s уже есть. Заменить её новой?\n"
                                                        "Старая уйдёт в Корзину." % (name, vdir), parent=t):
                    return
                replace = True
            state["busy"] = True
            busy_anim(True)
            buttons(("small", "Отмена", lambda: box["cancel"].set()))

            def work():
                try:
                    box["folder"] = save_build(name, cb["gv"], cb["loader"], files, selection, replace=replace,
                                               log=lambda m: box.__setitem__("step", m),
                                               progress=lambda d, tot: box.update(done=d, total=tot), cancel=box["cancel"])
                except Exception as e:
                    box["err2"] = str(e)
            th2 = threading.Thread(target=work, daemon=True)
            th2.start()

            def poll2():
                if th2.is_alive():
                    if t.winfo_exists():
                        frac = min(1.0, box["done"] / float(max(1, box["total"])))
                        bar.coords(fill, 0, 0, bar.winfo_width() * frac, 10)
                        msg.configure(text="%s   %d%%" % (box.get("step", ""), frac * 100), fg=GOLD)
                    win.after(150, poll2)
                    return
                state["busy"] = False
                if not t.winfo_exists():
                    return
                if "err2" in box:
                    msg.configure(text=("Отменено." if "отменено" in box["err2"] else "Не получилось: " + box["err2"]),
                                  fg="#e05a5a")
                    buttons(("small", "Закрыть", t.destroy))
                    return
                bar.coords(fill, 0, 0, bar.winfo_width(), 10)
                folder = box["folder"]
                msg.configure(text="Готово! Сборка «%s» сохранена: %s." % (name, os.path.relpath(folder, ROOT)),
                              fg=ACCENT_HI)
                cb["sel"].clear()
                newp = next((p for p in find_packs() if os.path.abspath(p["path"]) == os.path.abspath(folder)), None)

                def enable_now():
                    t.destroy()
                    if newp:
                        enable_pack(newp)
                buttons(("big", "Включить сборку", enable_now), ("small", "К сборкам", lambda: (t.destroy(), show("packs"))))
                toast("Сборка «%s» готова." % name, "ok")
            poll2()
        poll()
        fade_in_window(t)

    def edit_user_pack(p):
        c = p.get("constructor") or {}
        cb = builder_state()
        cb.update(gv=c.get("gv", p.get("minecraft")), loader=c.get("loader", p.get("loader", "fabric").lower()),
                  ptype="mod", name=p["name"].rsplit(" (", 1)[0])
        cb["sel"] = {x["project_id"]: dict(x) for x in c.get("projects", [])}
        builder_query()
        show("builder")
        toast("Сборка «%s» открыта в конструкторе: добавь или убери моды и нажми «Собрать сборку»." % cb["name"], "info",
              ms=7000)

    # --- вкладка «Скины» ---
    def skin_photo_producer(png, slim=None, scale=5):
        return lambda: render_skin(png, slim, scale)

    def skins_state():
        sk = state.get("sk")
        if sk is None:
            sk = state["sk"] = {"nick": load_settings().get("skin_nick", ""), "found": None, "searching": False,
                                "gallery": [], "after": None, "gal_loading": False, "defaults": None}
        return sk

    def skins_lookup(nick):
        sk = skins_state()
        nick = nick.strip()
        if not re.match(r"^[A-Za-z0-9_]{2,16}$", nick):
            toast("Ник в Minecraft — от 2 до 16 латинских букв, цифр или «_».", "warn")
            return
        sk.update(nick=nick, searching=True, found=None)
        s = load_settings(); s["skin_nick"] = nick; save_settings(s)

        def work():
            try:
                sk["found"] = skin_lookup(nick)
            except Exception:
                sk["found"] = []
            sk["searching"] = False
            win.after(0, lambda: state["tab"] == "skins" and show("skins", animated=False))
        threading.Thread(target=work, daemon=True).start()
        show("skins", animated=False)

    def gallery_more():
        sk = skins_state()
        if sk["gal_loading"]:
            return
        sk["gal_loading"] = True

        def work():
            try:
                items, nxt = mineskin_popular(sk["after"], 24)
                sk["gallery"] += items
                sk["after"] = nxt
            except Exception as e:
                sk["gal_err"] = str(e)[:80]
            sk["gal_loading"] = False
            win.after(0, lambda: state["tab"] == "skins" and (state.update(keep_scroll=True), show("skins", animated=False)))
        threading.Thread(target=work, daemon=True).start()

    def wear_dialog(skin_path, cape_path=None, title=""):
        """Как надеть скин: без интернета у себя в игре или для всех - через сайт своего аккаунта."""
        t = tk.Toplevel(win)
        t.title("Надеть скин")
        t.configure(bg=BG)
        t.transient(win)
        W = 640
        fr = tk.Frame(t, bg=BG, padx=24, pady=20)
        fr.pack(fill="both", expand=True)
        top = tk.Frame(fr, bg=BG)
        top.pack(fill="x")
        prev = tk.Label(top, bg=BG)
        prev.pack(side="left", anchor="n")
        try:
            prev.configure(image=pil_photo(render_skin(open(skin_path, "rb").read(), None, 4)))
        except Exception:
            pass
        info = tk.Frame(top, bg=BG, padx=16)
        info.pack(side="left", fill="both", expand=True)
        tk.Label(info, text=title or "Надеть скин", font=(FONT, 17, "bold"), fg=TEXT, bg=BG, anchor="w").pack(fill="x")
        tk.Label(info, text="Без интернета скин увидишь ты (и друзья по сети с тем же модом). Чтобы скин видели все "
                            "на серверах, его загружают на сайт твоего аккаунта.", font=(FONT, 10), fg=MUTED, bg=BG,
                 anchor="w", justify="left", wraplength=400).pack(fill="x", pady=(6, 0))
        tk.Frame(fr, height=1, bg=LINE).pack(fill="x", pady=(16, 10))
        tk.Label(fr, text="1. Без интернета, в одиночной игре и по сети с другом", font=(FONT, 12, "bold"), fg=TEXT,
                 bg=BG, anchor="w").pack(fill="x")
        nr = tk.Frame(fr, bg=BG)
        nr.pack(fill="x", pady=(6, 0))
        tk.Label(nr, text="Твой ник в игре:", font=(FONT, 10), fg=TEXT, bg=BG).pack(side="left")
        ne = tk.Entry(nr, font=(FONT, 11), bg=CARD_HI, fg=TEXT, insertbackground=TEXT, relief="flat", width=18)
        ne.insert(0, load_settings().get("skin_nick", ""))
        ne.pack(side="left", padx=8, ipady=3)
        capes = [None] + sorted(f for f in _listdir(os.path.join(SKINS_DIR, "Плащи")) if f.lower().endswith(".png"))
        cvar = tk.StringVar(value=os.path.basename(cape_path) if cape_path else "без плаща")
        if cape_path and os.path.basename(cape_path) not in capes:
            capes.insert(1, os.path.basename(cape_path))
        om = option_menu(nr, cvar, ["без плаща"] + [c for c in capes if c])
        om.configure(width=18)
        om.pack(side="left", padx=(8, 0))

        def offline():
            nick = ne.get().strip()
            if not re.match(r"^[A-Za-z0-9_]{2,16}$", nick):
                messagebox.showwarning("Ник", "Ник — от 2 до 16 латинских букв, цифр или «_».", parent=t)
                return
            cp = None
            if cvar.get() != "без плаща":
                cp = cape_path if cape_path and os.path.basename(cape_path) == cvar.get() else \
                    os.path.join(SKINS_DIR, "Плащи", cvar.get())
            t.destroy()
            run_task("Надеваю скин", lambda log: install_offline_skin(nick, skin_path, cp, log),
                     lambda ok, logs: messagebox.showinfo("Скин", "\n".join(logs) + "\n\nВ игре скин появится после запуска "
                                                          "сборки с модами (Fabric или Forge). Мод CustomSkinLoader "
                                                          "Portalis добавляет в неё сам."))
        big_button(nr, "Надеть", offline, icon="ic_check.png").pack(side="right")
        tk.Label(fr, text="Работает со сборками на Fabric и Forge (мод CustomSkinLoader). В TLauncher при входе на "
                          "сервер может показываться скин из его аккаунта.", font=(FONT, 9), fg=MUTED, bg=BG, anchor="w",
                 justify="left", wraplength=W - 60).pack(fill="x", pady=(6, 0))
        tk.Frame(fr, height=1, bg=LINE).pack(fill="x", pady=(14, 10))
        tk.Label(fr, text="2. Для всех игроков: загрузить на сайт аккаунта", font=(FONT, 12, "bold"), fg=TEXT, bg=BG,
                 anchor="w").pack(fill="x")
        rr = tk.Frame(fr, bg=BG)
        rr.pack(fill="x", pady=(8, 0))

        def site(url, what):
            webbrowser.open(url)
            subprocess.Popen(["explorer.exe", "/select,", skin_path])
            toast("Открыл %s и папку со скином: войди в аккаунт и загрузи этот файл." % what, "info", ms=9000)
        small_button(rr, "TLauncher", lambda: site("https://tlauncher.org/ru/profile/", "сайт TLauncher"), bg=BG,
                     icon="ic_globe.png").pack(side="left")
        small_button(rr, "Ely.by (Legacy Launcher)", lambda: site("https://ely.by/skins/add", "Ely.by"), bg=BG,
                     icon="ic_globe.png").pack(side="left", padx=6)
        small_button(rr, "Лицензия (minecraft.net)", lambda: site("https://www.minecraft.net/msaprofile/mygames/editskin",
                                                                 "minecraft.net"), bg=BG, icon="ic_globe.png").pack(side="left")
        small_button(fr, "Закрыть", t.destroy, bg=BG).pack(anchor="e", pady=(16, 0))
        t.update_idletasks()
        t.geometry("%dx%d+%d+%d" % (W, t.winfo_reqheight(), win.winfo_rootx() + (win.winfo_width() - W) // 2,
                                    win.winfo_rooty() + 60))
        fade_in_window(t)

    def cape_maker():
        t = tk.Toplevel(win)
        t.title("Мастер плащей")
        t.configure(bg=BG)
        t.transient(win)
        fr = tk.Frame(t, bg=BG, padx=24, pady=20)
        fr.pack(fill="both", expand=True)
        st = {"c1": "#b03a2e", "c2": "#f4d03f", "pattern": "полосы"}
        tk.Label(fr, text="Мастер плащей", font=(FONT, 17, "bold"), fg=TEXT, bg=BG, anchor="w").pack(fill="x")
        body_ = tk.Frame(fr, bg=BG)
        body_.pack(fill="x", pady=(10, 0))
        prev = tk.Label(body_, bg=PANEL, padx=20, pady=14)
        prev.pack(side="left", anchor="n")
        ctl = tk.Frame(body_, bg=BG, padx=18)
        ctl.pack(side="left", fill="both", expand=True)

        def redraw():
            prev.configure(image=pil_photo(render_cape(make_cape(st["c1"], st["c2"], st["pattern"]), 12)))
            for k, b in sw.items():
                b.configure(bg=st[k])
            for w in pr.winfo_children():
                w.destroy()
            chip_row(pr, [(p, p.capitalize()) for p in ("полосы", "шахматка", "градиент", "кайма", "звёзды", "однотонный")],
                     st["pattern"], lambda v: (st.update(pattern=v), redraw()))

        def pick(k):
            c = colorchooser.askcolor(st[k], parent=t, title="Цвет плаща")
            if c and c[1]:
                st[k] = c[1]
                redraw()
        sw = {}
        for k, text in (("c1", "Основной цвет"), ("c2", "Второй цвет")):
            r = tk.Frame(ctl, bg=BG)
            r.pack(fill="x", pady=(0, 8))
            tk.Label(r, text=text, font=(FONT, 10), fg=TEXT, bg=BG, width=14, anchor="w").pack(side="left")
            sw[k] = tk.Label(r, text="      ", bg=st[k], cursor="hand2", relief="flat", padx=14, pady=6)
            sw[k].pack(side="left")
            sw[k].bind("<Button-1>", lambda e, k=k: pick(k))
        tk.Label(ctl, text="Узор", font=(FONT, 10), fg=TEXT, bg=BG, anchor="w").pack(fill="x", pady=(6, 4))
        pr = tk.Frame(ctl, bg=BG)
        pr.pack(fill="x")
        nr = tk.Frame(ctl, bg=BG)
        nr.pack(fill="x", pady=(14, 0))
        tk.Label(nr, text="Название", font=(FONT, 10), fg=TEXT, bg=BG, width=14, anchor="w").pack(side="left")
        ne = tk.Entry(nr, font=(FONT, 11), bg=CARD_HI, fg=TEXT, insertbackground=TEXT, relief="flat", width=20)
        ne.insert(0, "Мой плащ")
        ne.pack(side="left", ipady=3)

        def save():
            d = os.path.join(SKINS_DIR, "Плащи")
            os.makedirs(d, exist_ok=True)
            base = _safe_name(ne.get()) or "Плащ"
            fn, i = base + ".png", 2
            while os.path.exists(os.path.join(d, fn)):
                fn, i = "%s (%d).png" % (base, i), i + 1
            with open(os.path.join(d, fn), "wb") as fh:
                fh.write(make_cape(st["c1"], st["c2"], st["pattern"]))
            t.destroy()
            toast("Плащ «%s» сохранён. Выбери его, когда надеваешь скин." % fn[:-4], "ok")
            show("skins", animated=False)
        big_button(fr, "Сохранить плащ", save, icon="ic_cape.png").pack(anchor="e", pady=(16, 0))
        redraw()
        fade_in_window(t)

    def skin_card(parent, col, row, title, sub, png, slim, buttons_):
        c = card(parent, col, row)
        top = tk.Frame(c, bg=CARD)
        top.pack(fill="x")
        pv = tk.Label(top, bg=CARD, image=blank(180, 160))
        pv.pack(side="left")
        want_pic("skin:%s:%s" % (hashlib.sha1(png).hexdigest()[:16], slim), skin_photo_producer(png, slim, 5), pv)
        info = tk.Frame(top, bg=CARD, padx=12)
        info.pack(side="left", fill="both", expand=True)
        tk.Label(info, text=title, font=(FONT, 13, "bold"), fg=TEXT, bg=CARD, anchor="w", justify="left",
                 wraplength=220).pack(fill="x")
        tk.Label(info, text=sub, font=(FONT, 9), fg=MUTED, bg=CARD, anchor="w", justify="left", wraplength=220).pack(
            fill="x", pady=(2, 8))
        for kind, text, cmd, icon in buttons_:
            (big_button(info, text, cmd, icon=icon) if kind == "big" else small_button(info, text, cmd, bg=CARD, icon=icon)
             ).pack(anchor="w", pady=(0, 5))
        return c

    def build_skins():
        sk = skins_state()
        head_f = section("Скины и плащи", 0, "найди скин по нику, выбери из галереи или сделай свой плащ")
        small_button(head_f, "Папка скинов", lambda: (os.makedirs(SKINS_DIR, exist_ok=True), os.startfile(SKINS_DIR)),
                     icon="ic_folder.png").pack(side="right")
        hero(1, "skins_wide.png", "Твой персонаж",
             "Посмотри, какой скин у ника в TLauncher, Ely.by и Mojang, сохрани понравившиеся и надень — "
             "даже без интернета.")
        row = 2
        lk = tk.Frame(inner, bg=PANEL, padx=14, pady=12)
        lk.grid(row=row, column=0, columnspan=2, sticky="we", padx=(0, 14), pady=(0, 12))
        tk.Label(lk, text="Скин по нику:", font=(FONT, 11, "bold"), fg=TEXT, bg=PANEL).pack(side="left")
        ne = tk.Entry(lk, font=(FONT, 11), bg=CARD_HI, fg=TEXT, insertbackground=TEXT, relief="flat", width=22)
        ne.insert(0, sk["nick"])
        ne.pack(side="left", padx=10, ipady=4)
        ne.bind("<Return>", lambda e: skins_lookup(ne.get()))
        big_button(lk, "Найти", lambda: skins_lookup(ne.get()), icon="ic_search.png").pack(side="left")
        small_button(lk, "Добавить из файла", lambda: import_skin(), bg=PANEL, icon="ic_add.png").pack(side="right")
        small_button(lk, "Сделать плащ", cape_maker, bg=PANEL, icon="ic_cape.png").pack(side="right", padx=6)
        row += 1
        if sk["searching"]:
            tk.Label(inner, text="Ищу скин «%s» в TLauncher, Ely.by и Mojang..." % sk["nick"], font=(FONT, 11, "bold"),
                     fg=GOLD, bg=BG, anchor="w").grid(row=row, column=0, columnspan=2, sticky="we", pady=(0, 10))
            row += 1
        elif sk["found"] is not None:
            if not sk["found"]:
                empty_note(row, "У ника «%s» нет своего скина ни в TLauncher, ни на Ely.by, ни в Mojang." % sk["nick"])
                row += 1
            for i, r in enumerate(sk["found"]):
                def save_it(r=r):
                    fn = save_my_skin("%s (%s)" % (sk["nick"], r["source"].split(" ")[0]), r["skin"], r["slim"],
                                      r.get("cape"), r["source"])
                    toast("Сохранено в «Мои скины»: %s" % fn[:-4], "ok")
                    show("skins", animated=False)
                sub = "%s · %s%s" % (r["source"], "тонкие руки" if r["slim"] else "обычные руки",
                                     " · есть плащ" if r.get("cape") else "")
                skin_card(inner, i % 2, row + i // 2, sk["nick"], sub, r["skin"], r["slim"],
                          [("big", "Сохранить себе", save_it, "ic_backup.png")])
            row += (len(sk["found"]) + 1) // 2
        # Мои скины
        mine = my_skins()
        hf = section("Мои скины  (%d)" % len(mine), row)
        row += 1
        if not mine:
            tk.Label(inner, text="Пока пусто. Найди скин по нику, выбери стандартный или из галереи ниже — "
                                 "или добавь свой PNG кнопкой «Добавить из файла».", font=(FONT, 10), fg=MUTED, bg=BG,
                     anchor="w", justify="left", wraplength=900).grid(row=row, column=0, columnspan=2, sticky="we",
                                                                      pady=(0, 12))
            row += 1
        for i, m in enumerate(mine):
            path = os.path.join(SKINS_DIR, m["file"])
            try:
                png = open(path, "rb").read()
            except OSError:
                continue
            cape = os.path.join(SKINS_DIR, m["cape"]) if m.get("cape") else None

            def rm(m=m):
                if messagebox.askyesno("Удалить", "Убрать скин «%s» в Корзину?" % m["name"]):
                    delete_my_skin(m["file"])
                    show("skins", animated=False)
            skin_card(inner, i % 2, row + i // 2, m["name"],
                      ("тонкие руки" if m.get("slim") else "обычные руки") + (" · с плащом" if cape else "")
                      + (" · " + m["source"] if m.get("source") else ""), png, m.get("slim"),
                      [("big", "Надеть", lambda p=path, c=cape, n=m["name"]: wear_dialog(p, c, n), "ic_check.png"),
                       ("small", "Удалить", rm, "ic_delete.png")])
        row += (len(mine) + 1) // 2
        capes = sorted(f for f in _listdir(os.path.join(SKINS_DIR, "Плащи")) if f.lower().endswith(".png"))
        if capes:
            cf = section("Мои плащи  (%d)" % len(capes), row)
            row += 1
            line = tk.Frame(inner, bg=BG)
            line.grid(row=row, column=0, columnspan=2, sticky="we", pady=(0, 12))
            for f in capes:
                cell = tk.Frame(line, bg=CARD, padx=10, pady=8)
                cell.pack(side="left", padx=(0, 10))
                lb = tk.Label(cell, bg=CARD)
                lb.pack()
                want_pic("cape:" + f + str(os.path.getmtime(os.path.join(SKINS_DIR, "Плащи", f))),
                         lambda f=f: render_cape(open(os.path.join(SKINS_DIR, "Плащи", f), "rb").read(), 5), lb)
                tk.Label(cell, text=f[:-4][:16], font=(FONT, 9), fg=TEXT, bg=CARD).pack()
            row += 1
            _ = cf
        _ = hf
        # Стандартные скины
        if sk["defaults"] is None:
            sk["defaults"] = default_skins()
        if sk["defaults"]:
            section("Стандартные скины Minecraft", row, "из файлов игры")
            row += 1
            grid = tk.Frame(inner, bg=BG)
            grid.grid(row=row, column=0, columnspan=2, sticky="we", pady=(0, 12))
            for i, d in enumerate(sk["defaults"]):
                cell = tk.Frame(grid, bg=CARD, padx=6, pady=6, cursor="hand2")
                cell.grid(row=i // 7, column=i % 7, padx=(0, 8), pady=(0, 8))
                lb = tk.Label(cell, bg=CARD, image=blank(104, 96))
                lb.pack()
                want_pic("def:%s:%s" % (d["name"], d["slim"]), skin_photo_producer(d["skin"], d["slim"], 3), lb)
                tk.Label(cell, text="%s%s" % (d["name"], " ·т" if d["slim"] else ""), font=(FONT, 8), fg=TEXT, bg=CARD).pack()

                def take(d=d):
                    fn = save_my_skin(d["name"] + (" (тонкие)" if d["slim"] else ""), d["skin"], d["slim"], None, "стандартный")
                    toast("Сохранено в «Мои скины»: %s" % fn[:-4], "ok")
                    show("skins", animated=False)
                for w in (cell, lb) + tuple(cell.winfo_children()):
                    w.bind("<Button-1>", lambda e, take=take: take())
            row += 1
            tk.Label(inner, text="Нажми на скин, чтобы добавить его в «Мои скины».", font=(FONT, 9), fg=MUTED, bg=BG,
                     anchor="w").grid(row=row, column=0, columnspan=2, sticky="we", pady=(0, 12))
            row += 1
        # Галерея MineSkin
        section("Галерея скинов", row, "MineSkin: скины, которые загрузили игроки")
        row += 1
        if not sk["gallery"] and not sk["gal_loading"] and not sk.get("gal_err"):
            gallery_more()
        if sk.get("gal_err") and not sk["gallery"]:
            empty_note(row, "Галерея не загрузилась (%s). Проверь интернет и нажми F5." % sk["gal_err"])
            row += 1
        grid = tk.Frame(inner, bg=BG)
        grid.grid(row=row, column=0, columnspan=2, sticky="we", pady=(0, 10))
        for i, g in enumerate(sk["gallery"]):
            cell = tk.Frame(grid, bg=CARD, padx=6, pady=6, cursor="hand2")
            cell.grid(row=i // 7, column=i % 7, padx=(0, 8), pady=(0, 8))
            lb = tk.Label(cell, bg=CARD, image=blank(104, 96))
            lb.pack()
            want_pic("gal:" + g["texture"], lambda g=g: render_skin(texture_png(g["texture"]), None, 3), lb)
            nm = g["name"] if g["name"] != "без названия" else "скин"
            tk.Label(cell, text=nm[:13], font=(FONT, 8), fg=TEXT, bg=CARD).pack()

            def take_g(g=g, nm=nm):
                def work(log):
                    png = texture_png(g["texture"])
                    return save_my_skin(nm, png, None, None, "MineSkin")
                run_task("Сохраняю скин", work, lambda fn, logs: (toast("Сохранено в «Мои скины»: %s" % fn[:-4], "ok"),
                                                                  show("skins", animated=False)))
            for w in (cell, lb) + tuple(cell.winfo_children()):
                w.bind("<Button-1>", lambda e, f=take_g: f())
        row += 1
        mb = tk.Frame(inner, bg=BG)
        mb.grid(row=row, column=0, columnspan=2, pady=(0, 14))
        if sk["gal_loading"]:
            tk.Label(mb, text="Загружаю галерею...", font=(FONT, 10, "bold"), fg=GOLD, bg=BG).pack()
        elif sk.get("after"):
            big_button(mb, "Показать ещё", gallery_more, color=CARD_HI, hover=LINE).pack()

    def import_skin():
        f = filedialog.askopenfilename(parent=win, title="Скин Minecraft (PNG 64x64 или 64x32)",
                                       filetypes=[("Картинка PNG", "*.png")])
        if not f:
            return
        try:
            png = open(f, "rb").read()
            im = PILImage.open(io.BytesIO(png))
            if im.width % 64 or im.height not in (im.width, im.width // 2):
                raise ValueError("размер %dx%d, а нужен 64x64 или 64x32" % im.size)
        except Exception as e:
            messagebox.showwarning("Не скин", "Это не скин Minecraft: %s" % e)
            return
        fn = save_my_skin(os.path.splitext(os.path.basename(f))[0], png, None, None, "из файла")
        toast("Скин добавлен: %s" % fn[:-4], "ok")
        show("skins", animated=False)


    def show(key, animated=True):
        prev = state.get("tab")
        state["tab"] = key
        for k, b in tab_btns.items():
            if k == key:
                fade_color(b, "fg", TEXT, 150)
            else:
                b.configure(fg=MUTED)
        if win.winfo_viewable():
            move_tab_line(key)
        keep = state.pop("keep_scroll", False) and prev == key
        y = canvas.yview()[0] if keep else 0
        clear()
        {"maps": build_maps, "packs": build_packs, "servers": build_servers, "friend": build_friend,
         "launchers": build_launchers, "builder": build_builder, "skins": build_skins}[key]()
        if keep:
            canvas.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.yview_moveto(y)
        auto_wrap(inner)
        refresh_head()
        refresh_foot()
        # Содержимое въезжает сбоку: вправо или влево, смотря куда переключились.
        if animated and prev in TAB_ORDER and prev != key and win.winfo_viewable():
            d = 70 if TAB_ORDER.index(key) > TAB_ORDER.index(prev) else -70
            animate("slide", 260, lambda k: canvas.coords(inner_id, int(d * (1 - k)), 0))
        else:
            canvas.coords(inner_id, 0, 0)

    # --- обновления ---
    def show_update_bar(remote):
        for w in upd_bar.winfo_children():
            w.destroy()
        ubg = upd_bar["bg"]
        tk.Label(upd_bar, image=art("tab_update.png", 1, 1), bg=ubg).pack(side="left", padx=(12, 6), pady=8)
        tk.Label(upd_bar, text="Вышло обновление %s" % remote.get("version"), font=(FONT, 11, "bold"), fg="white",
                 bg=ubg).pack(side="left")
        ch = remote.get("changes") or []
        if ch:
            tk.Label(upd_bar, text="   " + ch[0] + ("  и ещё %d" % (len(ch) - 1) if len(ch) > 1 else ""),
                     font=(FONT, 10), fg="#b9d9bd", bg=ubg).pack(side="left")

        def later():
            upd_bar.pack_forget()
        small_button(upd_bar, "Позже", later, bg=ubg).pack(side="right", padx=(4, 12))
        big_button(upd_bar, "Обновить", lambda: open_update(remote), icon="ic_download.png").pack(side="right", pady=6)
        if not upd_bar.winfo_ismapped():
            upd_bar.pack(fill="x", padx=24, pady=(10, 0), before=body)

    def with_remote(fn, quiet=False):
        """Опись с GitHub в фоне, потом fn(remote) в окне. Без связи - сообщение."""
        box = {}

        def work():
            try:
                box["remote"] = fetch_remote_manifest()
            except Exception as e:
                box["err"] = str(e)
        th = threading.Thread(target=work, daemon=True)
        th.start()

        def poll():
            if th.is_alive():
                win.after(200, poll)
                return
            if "err" in box:
                if not quiet:
                    toast("Нет связи с GitHub (%s). Проверь интернет и попробуй ещё раз." % box["err"][:80], "err", ms=7000)
                return
            state["remote"] = box["remote"]
            fn(box["remote"])
        poll()

    def ensure_items(ids, then, title="Скачать"):
        """Если карта или сборка ещё не скачана - окно загрузки, а после неё then()."""
        if not ids:
            then()
            return
        toast("Узнаю, откуда скачивать...", "info", ms=2500)
        with_remote(lambda remote: open_update(remote, ids, then, title))

    def check_updates(manual=False):
        if manual:
            toast("Проверяю обновления на GitHub...", "info", ms=2500)

        def got(remote):
            if update_available(remote):
                show_update_bar(remote)
                if manual:
                    open_update(remote)
            elif not lists_cached(remote) and not state["busy"]:
                # Версия та же, но каталог мог прийти не целиком (программу обновила старая версия): сверим тихо.
                box = {}

                def work():
                    try:
                        box["plan"] = plan_update(remote, log=lambda m: None)
                    except Exception as e:
                        box["err"] = str(e)
                th = threading.Thread(target=work, daemon=True)
                th.start()

                def poll():
                    if th.is_alive():
                        win.after(300, poll)
                    elif box.get("plan") and (box["plan"]["need"] or box["plan"]["remove"]):
                        show_update_bar(remote)
                    elif manual:
                        toast("У тебя последняя версия: %s." % remote.get("version"), "ok")
                poll()
            elif manual:
                toast("У тебя последняя версия: %s." % remote.get("version"), "ok")
        with_remote(got, quiet=not manual)

    def open_update(remote, items=None, then=None, title=None, auto=False):
        """Окно загрузки: обновление (items=None) или скачивание карт и сборок items."""
        if state["busy"]:
            toast("Подожди, программа сейчас занята.", "warn")
            return
        if state.get("upd_win") is not None and state["upd_win"].winfo_exists():
            state["upd_win"].destroy()
        local = load_local_manifest()
        t = tk.Toplevel(win)
        state["upd_win"] = t
        t.title(title or "Обновление библиотеки")
        t.configure(bg=BG)
        t.transient(win)
        W, H = 680, 640
        t.geometry("%dx%d+%d+%d" % (W, H, win.winfo_rootx() + (win.winfo_width() - W) // 2, win.winfo_rooty() + 40))
        t.minsize(560, 500)
        fr = tk.Frame(t, bg=BG, padx=26, pady=22)
        fr.pack(fill="both", expand=True)
        top = tk.Frame(fr, bg=BG)
        top.pack(fill="x")
        tk.Label(top, image=art("update.png", 1, 1), bg=BG).pack(side="left", anchor="n")
        info = tk.Frame(top, bg=BG, padx=18)
        info.pack(side="left", fill="both", expand=True)
        names = [(manifest_item(remote, i) or {}).get("title", i) for i in (items or [])]
        tk.Label(info, text=title or "Обновление библиотеки", font=(FONT, 19, "bold"), fg=TEXT, bg=BG, anchor="w",
                 justify="left", wraplength=400).pack(fill="x")
        if items:
            line = ", ".join(names)
        else:
            line = "У тебя: %s   →   новая: %s" % (local.get("version", "?"), remote.get("version"))
        tk.Label(info, text=line, font=(FONT, 11), fg=ACCENT_HI, bg=BG, anchor="w", justify="left",
                 wraplength=400).pack(fill="x", pady=(6, 0))
        tk.Label(info, text=("Файлы берутся с сайтов авторов: карты — оттуда, где их выложили, моды — с Modrinth "
                             "и CurseForge. Если что-то уже есть в «Загрузках», программа возьмёт оттуда." if items else
                             "Программа скачает только то, что изменилось. Твои миры и настройки игры не трогаются."),
                 font=(FONT, 10), fg=MUTED, bg=BG, anchor="w", justify="left", wraplength=400).pack(fill="x", pady=(8, 0))
        ch = remote.get("changes") or []
        if ch and not items:
            tk.Label(fr, text="Что нового", font=(FONT, 13, "bold"), fg=TEXT, bg=BG, anchor="w").pack(fill="x", pady=(16, 4))
            for ln in ch[:8]:
                tk.Label(fr, text="•  " + ln, font=(FONT, 10), fg="#c3c7d1", bg=BG, anchor="w", justify="left",
                         wraplength=W - 80).pack(fill="x")
        msg = tk.Label(fr, text="Сверяю файлы...", font=(FONT, 10, "bold"), fg=GOLD, bg=BG, anchor="w",
                       justify="left", wraplength=W - 80)
        msg.pack(fill="x", pady=(16, 6))
        bar = tk.Canvas(fr, height=12, bg=LINE, highlightthickness=0, bd=0)
        bar.pack(fill="x")
        fill = bar.create_rectangle(0, 0, 0, 12, fill=ACCENT, width=0)
        shine = bar.create_rectangle(-60, 0, -20, 12, fill=ACCENT_HI, width=0)
        sub = tk.Label(fr, text="", font=(FONT, 9), fg=MUTED, bg=BG, anchor="w", justify="left", wraplength=W - 80)
        sub.pack(fill="x", pady=(6, 0))
        # Подсказка для сайтов, которые не отдают файл программе: человек скачивает в браузере.
        brow = tk.Frame(fr, bg=PANEL, padx=14, pady=10)
        row = tk.Frame(fr, bg=BG)
        row.pack(fill="x", side="bottom", pady=(14, 0))
        box = {"cancel": threading.Event(), "done": 0, "total": 1, "logs": [], "manual": {}}
        prog = {"shown": 0.0}

        def set_bar(frac):
            w = max(1, bar.winfo_width())
            start = prog["shown"]
            prog["shown"] = frac
            animate(("bar", str(bar)), 300, lambda k: bar.coords(fill, 0, 0, w * (start + (frac - start) * k), 12))

        def shimmer():
            if not t.winfo_exists() or box.get("finished"):
                if t.winfo_exists():
                    bar.coords(shine, -60, 0, -20, 12)
                return
            w = max(1, bar.winfo_width())
            animate(("shine", str(bar)), 1300, lambda k: bar.coords(shine, -60 + (w + 80) * k, 0, -20 + (w + 80) * k, 12),
                    done=shimmer, ease=lambda x: x)
        shimmer()

        def buttons(*btns):
            for w in row.winfo_children():
                w.destroy()
            for kind, text, cmd in btns:
                if kind == "big":
                    big_button(row, text, cmd).pack(side="right")
                else:
                    small_button(row, text, cmd, bg=BG).pack(side="right", padx=8)

        def close():
            if box.get("applying"):
                if messagebox.askyesno("Прервать?", "Загрузка ещё идёт. Прервать?\nПапка останется как была.", parent=t):
                    box["cancel"].set()
                return
            t.destroy()
            show(state["tab"], animated=False)
        t.protocol("WM_DELETE_WINDOW", close)
        buttons(("small", "Закрыть", close))

        def show_browser(a, err):
            for w in brow.winfo_children():
                w.destroy()
            site = urllib.parse.urlparse(a.get("page", "")).netloc.replace("www.", "")
            tk.Label(brow, text="Сайт %s не отдаёт файл программе%s. Я открыл страницу карты в браузере: "
                                "нажми там кнопку скачивания (Download). Программа сама найдёт файл «%s» в «Загрузках» "
                                "и продолжит." % (site, "" if a.get("browser") else " (%s)" % str(err)[:60], a["name"]),
                     font=(FONT, 10), fg=TEXT, bg=PANEL, anchor="w", justify="left", wraplength=W - 110).pack(fill="x")
            br = tk.Frame(brow, bg=PANEL)
            br.pack(fill="x", pady=(8, 0))
            small_button(br, "Открыть страницу ещё раз", lambda: webbrowser.open(a["page"]), bg=PANEL, icon="ic_globe.png").pack(side="left")

            def pick():
                f = filedialog.askopenfilename(parent=t, title="Где лежит %s?" % a["name"],
                                               filetypes=[("Архив", "*.zip"), ("Все файлы", "*.*")])
                if f:
                    box["manual"]["file"] = f
            small_button(br, "Указать файл вручную...", pick, bg=PANEL, icon="ic_folder.png").pack(side="left", padx=(8, 0))
            brow.pack(fill="x", pady=(12, 0), before=row)

        def ask_browser(a, err):
            seq = box.get("bseq", 0) + 1
            box["bseq"] = seq
            box["browser"] = (seq, a, err)
            webbrowser.open(a["page"])
            try:
                return wait_archive_in_downloads(a, box["cancel"], manual=box["manual"])
            finally:
                box["browser_done"] = seq

        def planning():
            try:
                box["plan"] = (plan_download(remote, items, log=lambda m: box.__setitem__("step", m)) if items else
                               plan_update(remote, log=lambda m: box.__setitem__("step", m)))
            except Exception as e:
                box["err"] = str(e)
        th = threading.Thread(target=planning, daemon=True)
        th.start()

        def poll_plan():
            if not t.winfo_exists():
                return
            if th.is_alive():
                sub.configure(text=box.get("step", ""))
                t.after(150, poll_plan)
                return
            box["finished"] = True
            if "err" in box:
                msg.configure(text="Не получилось: " + box["err"], fg="#e05a5a")
                return
            plan = box["plan"]
            if not plan["need"] and not plan["extra"] and not plan["remove"]:
                if not items or plan.get("with_update"):
                    apply_plan(remote, plan, log=lambda m: None)
                set_bar(1.0)
                upd_bar.pack_forget()
                if (items or auto) and then:
                    t.destroy()
                    then()
                    return
                msg.configure(text="Всё уже на месте: версия %s." % remote.get("version"), fg=ACCENT_HI)
                sub.configure(text="")
                return
            sites = plan_sources(remote, plan)
            text = "Скачаю %s (файлов: %d) с: %s" % (fmt_mb(plan["size"]), len(plan["need"]), ", ".join(sites) or "-")
            if plan.get("with_update") and items:
                text += ".\nЗаодно обновлю библиотеку до версии %s." % remote.get("version")
            msg.configure(text=text, fg=TEXT)
            rm = plan["extra"] + plan["remove"]
            sub.configure(text=("Уберу в Корзину старое: " + ", ".join(x.rsplit("/", 1)[-1] for x in rm[:8]) + (
                " и ещё %d" % (len(rm) - 8) if len(rm) > 8 else "")) if rm else "", fg="#e0a45a")
            label = ("Скачать  (%s)" if items else "Обновить  (%s)") % fmt_mb(plan["size"])
            buttons(("big", label, lambda: start(plan)), ("small", "Позже", close))
            if items or auto:
                start(plan)  # человек уже нажал «Играть» или «Включить» (или это первый запуск): сразу качаем

        def start(plan):
            if state["busy"]:
                toast("Подожди, программа сейчас занята.", "warn")
                return
            state["busy"] = True
            box.update(applying=True, finished=False, total=max(1, plan["size"]))
            busy_anim(True)
            shimmer()
            buttons(("small", "Отмена", close))
            msg.configure(text="Скачиваю...", fg=GOLD)

            def work():
                try:
                    box["res"] = apply_plan(remote, plan, log=lambda m: box["logs"].append(m),
                                            progress=lambda d, tot: box.update(done=d, total=tot),
                                            cancel=box["cancel"], ask_browser=ask_browser)
                except Exception as e:
                    box["err2"] = str(e)
            th2 = threading.Thread(target=work, daemon=True)
            th2.start()
            last = {"t": 0}

            def poll():
                if th2.is_alive():
                    if t.winfo_exists():
                        done_seq = box.pop("browser_done", None)
                        cur = box.get("browser")
                        if done_seq and box.get("browser_shown") == done_seq:
                            brow.pack_forget()
                            box["browser_shown"] = None
                        if cur and cur[0] != done_seq and box.get("browser_shown") != cur[0]:
                            box["browser_shown"] = cur[0]
                            show_browser(cur[1], cur[2])
                        if box["manual"].pop("bad", None):
                            toast("Это не тот файл: не совпала контрольная сумма.", "err")
                        if time.perf_counter() - last["t"] > 0.25:
                            last["t"] = time.perf_counter()
                            frac = min(1.0, box["done"] / float(box["total"]))
                            set_bar(frac * 0.97)
                            msg.configure(text="%s   %d%%   (%s из %s)" % (
                                box["logs"][-1] if box["logs"] else "Скачиваю", frac * 100,
                                fmt_mb(box["done"]), fmt_mb(box["total"])))
                    win.after(150, poll)
                    return
                state["busy"] = False
                box.update(applying=False, finished=True)
                if not t.winfo_exists():
                    return
                brow.pack_forget()
                if "err2" in box:
                    msg.configure(text=("Прервано. Папка осталась как была." if "отменено" in box["err2"]
                                        else "Не получилось: " + box["err2"]), fg="#e05a5a")
                    buttons(("big", "Ещё раз", lambda: (t.destroy(), open_update(remote, items, then, title))),
                            ("small", "Закрыть", close))
                    return
                set_bar(1.0)
                res = box["res"]
                if plan.get("with_update") or not items:
                    upd_bar.pack_forget()
                if res.get("restart"):
                    msg.configure(text="Готово! Программа тоже обновилась: перезапусти её.", fg=ACCENT_HI)

                    def restart():
                        subprocess.Popen([exe_path()], cwd=APP_DIR)
                        win.destroy()
                    buttons(("big", "Перезапустить программу", restart))
                    return
                if (items or auto) and then:
                    t.destroy()
                    toast("Скачано: %s." % ", ".join(names) if names else "Каталог загружен: выбирай карту или сборку.", "ok")
                    show(state["tab"], animated=False)
                    then()
                    return
                msg.configure(text="Готово! Версия %s." % remote.get("version"), fg=ACCENT_HI)
                sub.configure(text=("Старые файлы убраны в Корзину." if not res.get("trash") else
                                    "Старые файлы сложены в папку «%s», её можно удалить." % os.path.basename(res["trash"])),
                              fg=MUTED)
                buttons(("big", "Закрыть", close))
                toast("Библиотека обновлена до версии %s." % remote.get("version"), "ok")
            poll()
        poll_plan()
        t.bind("<Escape>", lambda e: close())
        fade_in_window(t)

    # --- запуск: заставка, потом окно ---
    win._hooks = {"finish": finish, "open_update": open_update, "toast": toast, "show": show, "body": canvas}
    # Окно открывается там же и таким же, каким его закрыли; горячие клавиши.
    g = settings.get("geometry", "")
    mg = re.match(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", g)
    if mg:
        gw, gh, gx, gy = (int(v) for v in mg.groups())
        if gw >= 1000 and gh >= 660 and -50 < gx < win.winfo_screenwidth() - 200 and -10 < gy < win.winfo_screenheight() - 200:
            win.geometry(g)

    def on_close():
        pic_pool.shutdown(wait=False, cancel_futures=True)
        s = load_settings()
        if win.state() == "normal":
            s["geometry"] = win.geometry()
        s["zoomed"] = win.state() == "zoomed"
        s["tab"] = state["tab"]
        save_settings(s)
        win.destroy()
    win.protocol("WM_DELETE_WINDOW", on_close)

    def focus_search(e=None):
        en = state.get("search_entry")
        if state["tab"] not in ("maps", "packs"):
            show("maps")
            win.after(350, focus_search)
            return
        try:
            en.focus_set()
            en.select_range(0, "end")
        except Exception:
            pass
    for key in ("<Control-f>", "<Control-F>", "<Control-Cyrillic_a>", "<Control-Cyrillic_A>"):
        win.bind_all(key, focus_search)
    for i, k in enumerate(TAB_ORDER, 1):
        win.bind_all("<Control-Key-%d>" % i, lambda e, k=k: show(k))
    win.bind_all("<F5>", lambda e: (state.update(installed_launchers=None, net_cache=None), show(state["tab"], animated=False)))
    show(settings.get("tab") if settings.get("tab") in TAB_ORDER else "maps", animated=False)

    def reveal():
        def gone():
            splash.destroy()
            win.deiconify()
            win.state("zoomed" if settings.get("zoomed") else "normal")  # первое окно бывает свёрнутым
            win.lift()
            try:
                win.focus_force()
            except tk.TclError:
                pass
            try:
                win.attributes("-alpha", 0.0)
            except tk.TclError:
                pass
            win.update_idletasks()
            move_tab_line(state["tab"], instant=True)
            place_head()
            animate("win_in", 280, lambda k: win.attributes("-alpha", k))
            win.after(500, after_start)
        animate("splash_out", 220, lambda k: splash.attributes("-alpha", 1 - k), done=gone)

    def after_start():
        if not library_found() or not load_local_manifest().get("items"):
            # Первый запуск (программа - один файл): каталог с GitHub, без лишних вопросов.
            with_remote(lambda r: open_update(r, None, lambda: show(state["tab"], animated=False),
                                              "Добро пожаловать в Portalis! Загружаю каталог", auto=True))
        else:
            win.after(1500, check_updates)
        if getattr(sys, "frozen", False) and not shortcuts_exist() and not settings.get("shortcut_offered"):
            s = load_settings()
            s["shortcut_offered"] = True
            save_settings(s)
            win.after(2500, lambda: toast("Сделать ярлык программы на рабочем столе и в меню «Пуск»?", "info",
                                          ("Создать ярлык", make_shortcuts), 12000))
    wait = max(0, int(1150 - (time.perf_counter() - splash_t0) * 1000))
    win.after(wait, reveal)
    win.mainloop()


if __name__ == "__main__":
    if migrate_old_name(sys.argv[1:]):
        sys.exit(0)
    set_game_dir()
    if len(sys.argv) > 1:
        try:
            cleanup_after_update()
            code = cli(sys.argv[1:])
        except Exception as e:
            try:
                with open(os.path.join(ROOT, "switcher_cli.log"), "a", encoding="utf-8") as fh:
                    fh.write("Ошибка: %s\n%s\n" % (e, traceback.format_exc()))
            except OSError:
                pass
            code = 1
        sys.exit(code)
    gui()

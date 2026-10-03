# -*- coding: utf-8 -*-
"""Minecraft: карты и сборки.

Окно: двойной клик по «Выбор карты и сборки.exe» (или .bat, если установлен Python).
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
import urllib.parse
import urllib.error

ROOT = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
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


def lp(path):
    """Путь с префиксом для длинных имён: в некоторых картах файлы лежат глубже 260 символов."""
    p = os.path.abspath(path)
    return p if p.startswith("\\\\?\\") else "\\\\?\\" + p


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
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like 'java%'\" | ForEach-Object { $_.CommandLine }"],
            capture_output=True, text=True, errors="ignore", timeout=20, creationflags=0x08000000).stdout
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
        out = subprocess.run(["tasklist"], capture_output=True, text=True, errors="ignore",
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


def find_packs():
    packs = []
    for vdir in sorted(os.listdir(ROOT)):
        vp = os.path.join(ROOT, vdir)
        if not os.path.isdir(vp) or vdir.startswith("_") or vp == MAPS_DIR:
            continue
        for pdir in sorted(os.listdir(vp)):
            meta = os.path.join(vp, pdir, "pack.json")
            if not os.path.isfile(meta):
                continue
            try:
                with open(meta, encoding="utf-8") as fh:
                    m = json.load(fh)
            except Exception:
                continue
            m["path"] = os.path.join(vp, pdir)
            m["version_dir"] = vdir
            m["count"] = len(jars(os.path.join(vp, pdir, "mods")))
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


def switch(pack, packs, copy_configs=True, log=print):
    os.makedirs(MODS, exist_ok=True)
    known = known_files(packs)

    # 1. Моды, которых нет ни в одной сборке, не удаляем, а уносим в «_Несортированное».
    stray = [f for f in jars(MODS) if (f, os.path.getsize(os.path.join(MODS, f))) not in known]
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

    # 4. Версия в TLauncher и отметка активной сборки.
    ok = set_tlauncher_version(pack.get("tl_version", ""))
    note = "" if ok else " (TLauncher открыт, выберите версию в нём вручную)"
    log("Версия в TLauncher: %s%s" % (pack.get("tl_version"), note))
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


LIBRARY_NOTE = ("Рядом с программой нет папок со сборками и картами. Похоже, она запущена прямо из архива или скопирована отдельно. Распакуй архив «Minecraft Packs» целиком и запусти «Выбор карты и сборки.exe» из распакованной папки.")


def library_found():
    """Программа лежит в папке библиотеки: рядом есть карты или сборки."""
    return os.path.isdir(MAPS_DIR) or bool(find_packs())


# ---------- карты ----------

def find_maps():
    maps = []
    if not os.path.isdir(MAPS_DIR):
        return maps
    for d in sorted(os.listdir(MAPS_DIR)):
        meta = os.path.join(MAPS_DIR, d, "map.json")
        if not os.path.isfile(meta):
            continue
        try:
            with open(meta, encoding="utf-8") as fh:
                m = json.load(fh)
        except Exception:
            continue
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
    os.makedirs(SAVES, exist_ok=True)
    dst = os.path.join(SAVES, m["save"])
    if os.path.isdir(dst) and fresh:
        backup = "%s (старое сохранение %s)" % (m["save"], time.strftime("%Y-%m-%d %H-%M"))
        os.rename(dst, os.path.join(SAVES, backup))
        log("Старое сохранение переименовано в «%s»" % backup)
    if not os.path.isdir(dst):
        n = copy_long(os.path.join(m["path"], "world"), dst)
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
    ok = set_tlauncher_version(m["version"])
    log("Версия в TLauncher: %s%s" % (m["version"], "" if ok else " (TLauncher открыт, выберите версию в нём вручную)"))
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
            ["powershell", "-NoProfile", "-Command",
             "Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object { $_.InterfaceAlias + '|' + $_.IPAddress }"],
            capture_output=True, text=True, errors="ignore", timeout=20, creationflags=0x08000000).stdout
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
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "(Get-CimInstance Win32_Service -Filter \"Name='Hamachi2Svc'\").PathName"],
                                 capture_output=True, text=True, errors="ignore", timeout=20,
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
            ["powershell", "-NoProfile", "-Command",
             "Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } | "
             "ForEach-Object { $_.InterfaceAlias + '|' + $_.IPv4Address.IPAddress }"],
            capture_output=True, text=True, errors="ignore", timeout=25, creationflags=0x08000000).stdout
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
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True,
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
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Resolve-DnsName -Type SRV _minecraft._tcp.%s -ErrorAction Stop | "
                              "Where-Object { $_.Type -eq 'SRV' } | ForEach-Object { $_.NameTarget + '|' + $_.Port }" % host],
                             capture_output=True, text=True, errors="ignore", timeout=20,
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
            raise RuntimeError("У друга сборка «%s», а у тебя её нет. Обнови папку Minecraft Packs." % pack_name)
        cur = current() or {}
        if cur.get("name") != pack_name or get_tlauncher_version() != pack.get("tl_version"):
            ok = switch(pack, packs, True, log)
        else:
            log("Сборка «%s» уже включена" % pack_name)
    elif tl:
        if not tl.lower().startswith(("fabric", "forge")):
            ensure_vanilla(tl, log)
        ok = set_tlauncher_version(tl)
        log("Версия в TLauncher: %s%s" % (tl, "" if ok else " (TLauncher открыт, выберите версию вручную)"))
    set_friend_server(address)
    log("В «Сетевой игре» первой строкой добавлено: %s" % FRIEND_SERVER)
    host, _, port = address.rpartition(":")
    vpn = vpn_addresses()
    if not vpn:
        log("Внимание: Hamachi не запущен. Включи его и войди в сеть друга.")
    try:
        info = ping_server(host, int(port))
        log("Игра друга отвечает: версия %s, игроков %s из %s" % (info["version"], info["online"], info["max"]))
    except Exception as e:
        log("Игра друга пока не отвечает (%s). Проверь, что вы в одной сети Hamachi и мир открыт для сети." % e)
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
        raw = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-StartApps | ForEach-Object { $_.Name + '|' + $_.AppID }"],
                             capture_output=True, text=True, errors="ignore", timeout=60,
                             creationflags=0x08000000).stdout
        for line in raw.splitlines():
            name, _, app = line.partition("|")
            if name.strip():
                out.append((name.strip(), app.strip()))
    except Exception:
        pass
    _START_APPS["list"] = out
    return out


def find_installed_launchers(launchers=None):
    launchers = launchers if launchers is not None else load_launchers()
    found = {}
    apps = start_apps()
    for x in launchers:
        if x["id"] == "tlauncher" and tlauncher_exe():
            found["tlauncher"] = tlauncher_exe()
            continue
        for name, app in apps:
            low = name.lower()
            for kw in x.get("match", []):
                hit = low == kw[1:] if kw.startswith("=") else kw in low
                if hit and "media player" not in low:
                    found.setdefault(x["id"], app)
    return found


def run_target(target):
    if target.lower().endswith(".exe") and os.path.isfile(target):
        os.startfile(target)
    else:
        subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + target])


def chosen_launcher():
    return load_settings().get("launcher", "tlauncher")


def launcher_title(lid):
    return next((x["name"] for x in load_launchers() if x["id"] == lid), "TLauncher")


def open_launcher():
    lid = chosen_launcher()
    if lid == "tlauncher":
        return open_tlauncher()
    target = find_installed_launchers().get(lid)
    if target:
        run_target(target)
        return True
    return False


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
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
        docs = os.path.expandvars(winreg.QueryValueEx(k, "Personal")[0])
    except Exception:
        pass
    return os.path.join(docs, "Minecraft - копии миров")


def backup_world(m, log=print):
    """Копия сохранения карты в zip в «Документы\\Minecraft - копии миров»."""
    import zipfile
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

APP_TITLE = "Minecraft - карты и сборки"
EXE_NAME = "Выбор карты и сборки.exe"


def _shell_folder(name, default):
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
        return os.path.expandvars(winreg.QueryValueEx(k, name)[0])
    except Exception:
        return default


def shortcut_paths():
    home = os.environ.get("USERPROFILE", "")
    desk = _shell_folder("Desktop", os.path.join(home, "Desktop"))
    start = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")
    return [os.path.join(desk, APP_TITLE + ".lnk"), os.path.join(start, APP_TITLE + ".lnk")]


def app_target():
    """Что запускать ярлыком: сам exe, а без него — .pyw через pythonw."""
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    exe = os.path.join(ROOT, EXE_NAME)
    if os.path.isfile(exe):
        return exe, ""
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return (pyw if os.path.isfile(pyw) else sys.executable), '"%s"' % os.path.abspath(__file__)


def shortcuts_exist():
    return all(os.path.isfile(p) for p in shortcut_paths())


def create_shortcuts():
    """Ярлык на рабочем столе и в меню «Пуск». Возвращает список созданных путей."""
    target, args = app_target()
    icon = os.path.join(ROOT, EXE_NAME) if os.path.isfile(os.path.join(ROOT, EXE_NAME)) else target
    made = []
    for path in shortcut_paths():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{0}');$s.TargetPath='{1}';"
              "$s.Arguments='{2}';$s.WorkingDirectory='{3}';$s.IconLocation='{4},0';"
              "$s.Description='Карты, сборки и серверы Minecraft';$s.Save()").format(
            *(x.replace("'", "''") for x in (path, target, args, ROOT, icon)))
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30,
                       creationflags=0x08000000)
        if os.path.isfile(path):
            made.append(path)
    return made


# ---------- обновления через GitHub ----------
#
# В релизе на GitHub лежат manifest.json (опись версии) и архивы «блоков»: core (файлы в корне),
# app (сама программа), по одному на каждую сборку и карту. Моды, которые есть на Modrinth,
# качаются прямо оттуда (manifest["files"]). Программа сравнивает хеши своих файлов с описью
# и скачивает только изменившиеся блоки. Старое уносит в Корзину.

REPO = "SKUF666/minecraft-packs"
REMOTE_MANIFEST = "https://github.com/%s/releases/latest/download/manifest.json" % REPO
LOCAL_MANIFEST = os.path.join(ROOT, "manifest.json")
UPD_DIR = os.path.join(ROOT, "_update")
_JUNK = ("__pycache__", "desktop.ini", "thumbs.db", ".ds_store", "switcher_cli.log")


def vtuple(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


def load_local_manifest():
    try:
        with open(LOCAL_MANIFEST, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def fetch_remote_manifest(timeout=15, tries=6):
    """Опись последней версии. GitHub иногда отвечает 503, поэтому несколько попыток."""
    last = None
    for i in range(tries):
        req = urllib.request.Request(REMOTE_MANIFEST + "?t=%d" % int(time.time() * 1000),
                                     headers={"User-Agent": "MinecraftPacks/3.0", "Cache-Control": "no-cache"})
        try:
            return json.loads(urlopen(req, timeout, i).read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:
                raise RuntimeError("на GitHub ещё нет ни одной версии")
        except Exception as e:
            last = e
        time.sleep(1.0 + i)
    raise RuntimeError(str(last))


def _sha1_file(path):
    import hashlib
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
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh)
        except Exception:
            pass


def _walk_rel(root, base):
    """Файлы папки base (относительно root) в виде путей через «/»."""
    out = []
    top = os.path.join(root, base)
    if os.path.isfile(lp(top)):
        return [base.replace("\\", "/")]
    for d, dirs, files in os.walk(lp(top)):
        dirs[:] = [x for x in dirs if x.lower() not in _JUNK]
        for f in files:
            if f.lower() in _JUNK or f.lower().endswith(".pyc"):
                continue
            rel = os.path.relpath(os.path.join(d, f), lp(root))
            out.append(rel.replace("\\", "/"))
    return out


def block_files(block, root=ROOT, skip=()):
    """Файлы блока без внешних (Modrinth) и личных (local_only)."""
    files = []
    for p in block.get("paths") or [block["path"]]:
        files += _walk_rel(root, p)
    return sorted(f for f in files if f not in skip)


def block_hash(files, cache):
    import hashlib
    h = hashlib.sha1()
    for f in files:
        h.update(("%s\t%s\n" % (f, cache.sha1(f))).encode("utf-8"))
    return h.hexdigest()


def _skip_set(man):
    return {f["path"] for f in man.get("files", [])} | set(man.get("local_only", []))


def plan_update(remote, root=ROOT, log=print):
    """Что скачать и что убрать, чтобы папка совпала с описью remote."""
    cache = HashCache(root)
    skip = _skip_set(remote)
    blocks, files = [], []
    local_only = set(remote.get("local_only", []))
    for b in remote.get("blocks", []):
        if b.get("local_only") or b.get("path") in local_only:
            continue
        log("Проверяю: %s" % b["title"])
        have = block_files(b, root, skip)
        if not have or block_hash(have, cache) != b["hash"]:
            blocks.append(b)
    for f in remote.get("files", []):
        full = os.path.join(root, f["path"])
        try:
            ok = os.path.isfile(lp(full)) and cache.sha1(f["path"]) == f["sha1"]
        except OSError:
            ok = False
        if not ok:
            files.append(f)
    cache.save()
    # Сборки и карты, которые были в прошлой версии, а в новой их нет.
    old = load_manifest_at(root)
    new_dirs = {b["path"] for b in remote.get("blocks", []) if b["kind"] in ("pack", "map")}
    old_dirs = set()
    for b in old.get("blocks", []):
        if b["kind"] in ("pack", "map"):
            old_dirs.add(b["path"])
    old_dirs |= set(old.get("packs", {}))
    old_dirs |= {"Карты/" + k for k in old.get("maps", {})}
    if not new_dirs:
        raise RuntimeError("опись на GitHub пустая или повреждена, обновление пропущено")
    remove = sorted(d for d in old_dirs - new_dirs - local_only if os.path.isdir(lp(os.path.join(root, d))))
    # Файлы и папки в корне, которые прошлая версия раскладывала, а новая уже нет.
    core_new = set()
    for b in remote.get("blocks", []):
        if b["kind"] in ("core", "app"):
            core_new |= set(b.get("paths") or [b["path"]])
    core_old = set(old.get("root", []))
    for b in old.get("blocks", []):
        if b["kind"] == "core":
            core_old |= set(b.get("paths", []))
    keep = {"manifest.json", EXE_NAME}
    remove += sorted(p for p in core_old - core_new - keep - new_dirs
                     if "/" not in p and not p.startswith("_") and os.path.exists(lp(os.path.join(root, p)))
                     and not any(p == d.split("/")[0] for d in new_dirs))
    # Лишние моды в сборках, которые сами не изменились (мод убрали из сборки).
    for b in remote.get("blocks", []):
        if b["kind"] != "pack" or "jars" not in b or b in blocks:
            continue
        pre = b["path"] + "/mods/"
        allowed = set(b["jars"]) | {f["path"][len(pre):] for f in remote.get("files", []) if f["path"].startswith(pre)}             | {x[len(pre):] for x in local_only if x.startswith(pre)}
        remove += [pre + j for j in jars(os.path.join(root, b["path"], "mods")) if j not in allowed]
    size = sum(b.get("size", 0) for b in blocks) + sum(f.get("size", 0) for f in {x["sha1"]: x for x in files}.values())
    return {"version": remote.get("version"), "blocks": blocks, "files": files, "remove": remove, "size": size,
            "changes": remote.get("changes", [])}


def load_manifest_at(root):
    try:
        with open(os.path.join(root, "manifest.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _fetch_to(url, dst, progress=None, expect_sha1=None, cancel=None):
    import hashlib
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MinecraftPacks/3.0"})
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
                    if progress:
                        progress(len(chunk))
            if expect_sha1 and h.hexdigest() != expect_sha1:
                raise RuntimeError("файл скачался с ошибкой (не совпала контрольная сумма)")
            os.replace(lp(dst) + ".part", lp(dst))
            return
        except Exception as e:
            last = e
            if cancel and cancel.is_set():
                raise
            time.sleep(1 + attempt * 2)
    raise RuntimeError("не удалось скачать %s: %s" % (url.rsplit("/", 1)[-1], last))


def recycle(path):
    """В Корзину. Возвращает True, если получилось."""
    try:
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                        ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
        op = SHFILEOPSTRUCTW(None, 3, os.path.abspath(path) + "\0\0", None, 0x0040 | 0x0010 | 0x0004 | 0x0400, False,
                             None, None)
        return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not os.path.exists(path)
    except Exception:
        return False


def _move(src, dst):
    os.makedirs(os.path.dirname(lp(dst)), exist_ok=True)
    os.replace(lp(src), lp(dst))


def apply_update(remote, plan, root=ROOT, log=print, progress=None, cancel=None):
    """Скачивает и раскладывает обновление. Возвращает {'restart': bool, 'trash': путь или None}."""
    import zipfile
    upd = os.path.join(root, "_update")
    dl = os.path.join(upd, "dl")
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    old_dir = os.path.join(upd, "old_" + stamp)
    os.makedirs(dl, exist_ok=True)
    tag = remote.get("tag", "library")
    base = "https://github.com/%s/releases/download/%s/" % (remote.get("repo", REPO), tag)
    total = max(1, plan["size"])
    done = [0]

    def tick(n):
        done[0] += n
        if progress:
            progress(done[0], total)

    # 1. Скачать всё заранее: если связь оборвётся, папка останется как была.
    for i, b in enumerate(plan["blocks"], 1):
        log("Скачиваю %d из %d: %s (%.0f МБ)" % (i, len(plan["blocks"]), b["title"], b.get("size", 0) / 1048576))
        dst = os.path.join(dl, b["asset"])
        if os.path.isfile(dst) and _sha1_file(dst) == b.get("asset_sha1"):
            tick(b.get("size", 0))
            continue
        _fetch_to(base + urllib.parse.quote(b["asset"]), dst, tick, b.get("asset_sha1"), cancel)
    files = plan["files"]
    if files:
        log("Скачиваю моды с Modrinth: %d" % len(files))
        from concurrent.futures import ThreadPoolExecutor
        lock = threading.Lock()

        def one(f):
            dst = os.path.join(dl, "f_" + f["sha1"])
            if not (os.path.isfile(dst) and _sha1_file(dst) == f["sha1"]):
                def t(n):
                    with lock:
                        tick(n)
                _fetch_to(f["url"], dst, t, f["sha1"], cancel)
            else:
                with lock:
                    tick(f.get("size", 0))
        uniq = list({f["sha1"]: f for f in files}.values())  # один и тот же мод бывает в нескольких сборках
        with ThreadPoolExecutor(4) as ex:
            for r in ex.map(one, uniq):
                pass
    if cancel and cancel.is_set():
        raise RuntimeError("отменено")

    # 2. Разложить. Старые версии файлов уходят в _update/old_<время>, потом в Корзину.
    restart = False
    skip = _skip_set(remote)
    for b in plan["blocks"]:
        log("Устанавливаю: %s" % b["title"])
        src = os.path.join(dl, b["asset"])
        if b["kind"] == "app":
            target = os.path.join(root, b["path"])
            running = getattr(sys, "frozen", False) and os.path.abspath(sys.executable) == os.path.abspath(target)
            if os.path.exists(target):
                if running:
                    os.replace(target, target + ".old")
                    restart = True
                else:
                    _move(target, os.path.join(old_dir, b["path"]))
            shutil.copy2(src, target)
            continue
        stage = os.path.join(upd, "new")
        if os.path.isdir(stage):
            shutil.rmtree(lp(stage), ignore_errors=True)
        with zipfile.ZipFile(src) as z:
            z.extractall(lp(stage))
        new_files = set(_walk_rel(stage, ""))  # пути относительно stage
        for p in (b.get("paths") or [b["path"]]):
            for rel in _walk_rel(root, p):
                if rel not in new_files and rel not in skip:
                    _move(os.path.join(root, rel), os.path.join(old_dir, rel))
        for rel in new_files:
            dst = os.path.join(root, rel)
            if os.path.exists(lp(dst)):
                _move(dst, os.path.join(old_dir, rel))
            _move(os.path.join(stage, rel), dst)
        shutil.rmtree(lp(stage), ignore_errors=True)
    for f in files:
        dst = os.path.join(root, f["path"])
        if os.path.exists(lp(dst)):
            _move(dst, os.path.join(old_dir, f["path"]))
        os.makedirs(os.path.dirname(lp(dst)), exist_ok=True)
        shutil.copy2(lp(os.path.join(dl, "f_" + f["sha1"])), lp(dst))
    for p in plan["remove"]:
        log("Убираю старое: %s" % p)
        _move(os.path.join(root, p), os.path.join(old_dir, p))
        parent = os.path.dirname(os.path.join(root, p))
        try:
            if os.path.abspath(parent) != os.path.abspath(root) and not os.listdir(parent):
                os.rmdir(parent)
        except OSError:
            pass
    with open(os.path.join(root, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(remote, fh, ensure_ascii=False, indent=1)
    shutil.rmtree(lp(dl), ignore_errors=True)
    trash = None
    if os.path.isdir(old_dir):
        if not recycle(old_dir):
            trash = os.path.join(root, "_Старое после обновления " + stamp)
            os.replace(old_dir, trash)
    log("Обновление %s установлено" % remote.get("version"))
    return {"restart": restart, "trash": trash}


def cleanup_after_update():
    """Хвосты прошлого обновления: старый exe и временные файлы."""
    try:
        if os.path.exists(os.path.join(ROOT, EXE_NAME + ".old")):
            os.remove(os.path.join(ROOT, EXE_NAME + ".old"))
    except OSError:
        pass
    if os.path.isdir(os.path.join(UPD_DIR, "new")):
        shutil.rmtree(lp(os.path.join(UPD_DIR, "new")), ignore_errors=True)


def update_available(remote, local=None):
    local = local if local is not None else load_local_manifest()
    if vtuple(remote.get("version")) > vtuple(local.get("version", "0")):
        return True
    # Версия та же, но чего-то не хватает (например, скачан только архив с программой).
    for b in remote.get("blocks", []):
        if b["kind"] in ("pack", "map") and not b.get("local_only") and not os.path.isdir(os.path.join(ROOT, b["path"])):
            return True
    return False


def fmt_mb(n):
    mb = n / 1048576
    if mb >= 1024:
        return "%.1f ГБ" % (mb / 1024)
    return "%.0f МБ" % max(1, mb) if mb >= 1 else "меньше 1 МБ"

# ---------- командная строка ----------

def cli(argv):
    out = open(os.path.join(ROOT, "switcher_cli.log"), "a", encoding="utf-8")

    def log(*a):
        print(*a, file=out, flush=True)

    packs = find_packs()
    cmd = argv[0]
    if cmd == "--list":
        for p in packs:
            log("%s | %s | %d модов | %s" % (p["version_dir"], p["name"], p["count"], p.get("tl_version")))
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
    elif cmd in ("--check-update", "--update"):
        remote = fetch_remote_manifest()
        log("Версия у меня: %s, на GitHub: %s" % (load_local_manifest().get("version"), remote.get("version")))
        plan = plan_update(remote, log=lambda *a: None)
        log("Скачать блоков: %d (%s), файлов с Modrinth: %d, убрать: %d, всего %s" % (
            len(plan["blocks"]), ", ".join(b["title"] for b in plan["blocks"]), len(plan["files"]),
            len(plan["remove"]), fmt_mb(plan["size"])))
        for p in plan["remove"]:
            log("  убрать: " + p)
        if cmd == "--update" and (plan["blocks"] or plan["files"] or plan["remove"]):
            res = apply_update(remote, plan, log=log)
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
            log("Игра запущена. Закройте Minecraft."); return 2
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
            log("Игра запущена. Закройте Minecraft."); return 2
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
TAB_ORDER = ["maps", "packs", "servers", "friend", "launchers"]


def gui():
    import tkinter as tk
    from tkinter import messagebox, simpledialog

    cleanup_after_update()
    win = tk.Tk()
    win.withdraw()
    win.title("Minecraft: карты и сборки")
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
        scv.create_text(SW // 2 + dx, SH - 92 + dy, text="MINECRAFT", font=(FONT, 30, "bold"), fill=color)
    scv.create_text(SW // 2 + 1, SH - 56 + 1, text="карты  ·  сборки  ·  серверы", font=(FONT, 13), fill="#000000")
    scv.create_text(SW // 2, SH - 56, text="карты  ·  сборки  ·  серверы", font=(FONT, 13), fill="#d7deea")
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
        head.create_text(28 + dx, 52 + dy, text="MINECRAFT", font=(FONT, 26, "bold"), fill=color, anchor="w")
    head.create_text(31, 89, text="карты  ·  сборки  ·  серверы", font=(FONT, 12), fill="#000000", anchor="w")
    head.create_text(30, 88, text="карты  ·  сборки  ·  серверы", font=(FONT, 12), fill="#d7deea", anchor="w")
    import random
    import math
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
                     bg=BG, fg=MUTED, padx=14, pady=9, cursor="hand2")
        b.pack(side="left", padx=(0, 4), pady=(0, 3))
        b.bind("<Button-1>", lambda e: show(key))
        b.bind("<Enter>", lambda e: key != state["tab"] and fade_color(b, "fg", TEXT, 120))
        b.bind("<Leave>", lambda e: key != state["tab"] and fade_color(b, "fg", MUTED, 160))
        tab_btns[key] = b

    tab_button("maps", "Карты", "tab_maps.png")
    tab_button("packs", "Сборки", "tab_packs.png")
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

    def small_button(parent, text, cmd, bg=CARD_HI, fg=TEXT):
        b = tk.Label(parent, text=text, font=(FONT, 10), bg=bg, fg=fg, padx=12, pady=6, cursor="hand2")
        b.bind("<Button-1>", lambda e: (press_flash(b, LINE), cmd()))
        b.bind("<Enter>", lambda e: fade_color(b, "bg", LINE, 120))
        b.bind("<Leave>", lambda e: fade_color(b, "bg", bg, 180))
        return b

    def big_button(parent, text, cmd, color=ACCENT, hover=ACCENT_HI):
        b = tk.Label(parent, text=text, font=(FONT, 11, "bold"), bg=color, fg="white",
                     padx=20, pady=7, cursor="hand2")
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
    small_button(foot_btns, "Папка игры", lambda: os.startfile(MC), bg=PANEL).pack(side="right", padx=4, pady=8)
    shortcut_btn = small_button(foot_btns, "", make_shortcuts, bg=PANEL)
    shortcut_btn.pack(side="right", padx=4, pady=8)
    small_button(foot_btns, "Проверить обновления", lambda: check_updates(manual=True), bg=PANEL).pack(
        side="right", padx=4, pady=8)

    def refresh_foot():
        shortcut_btn.configure(text="Ярлык создан ✓" if shortcuts_exist() else "Создать ярлык")
        play_btn.configure(text="▶  Запустить " + launcher_title(chosen_launcher()))

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
        now_pack.configure(text="Моды: " + (c["name"] if c else "не выбраны через программу"))
        now_ver.configure(text="Версия в TLauncher: %s" % (get_tlauncher_version() or "?"))

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

    def section(text, row, sub=""):
        f = tk.Frame(inner, bg=BG)
        f.grid(row=row, column=0, columnspan=2, sticky="we", pady=(4, 10))
        tk.Label(f, text=text, font=(FONT, 14, "bold"), fg=TEXT, bg=BG).pack(side="left")
        if sub:
            tk.Label(f, text="   " + sub, font=(FONT, 10), fg=MUTED, bg=BG).pack(side="left", pady=(3, 0))
        return f

    # --- фоновые задачи, чтобы окно не зависало ---
    def run_task(title, fn, after=None):
        if state["busy"]:
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
        ver = next((re.match(r"Версия в TLauncher: (.+?)(?: \(|$)", l).group(1) for l in reversed(logs)
                    if l.startswith("Версия в TLauncher:")), None)
        hints = []
        if lid == "tlauncher" and not ok:
            hints.append("TLauncher был открыт, поэтому версию «%s» выбери в нём сам (список версий внизу слева)." % ver)
        if lid != "tlauncher" and ver:
            hints.append("В %s выбери версию «%s»." % (name, ver))
        opened = False
        if auto_var.get() and not (lid == "tlauncher" and not ok):
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
        tk.Label(info, text="Всё готово!", font=(FONT, 22, "bold"), fg=TEXT, bg=BG, anchor="w").pack(fill="x")
        what = next((l for l in logs if l.startswith(("Карта «", "Скопировано модов"))), "")
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
                more.configure(text="Подробнее, что сделано")
        row = tk.Frame(fr, bg=BG)
        row.pack(fill="x", pady=(18, 0))

        def go():
            t.destroy()
            launch_now()
        if opened:
            tk.Label(row, text="%s открывается..." % name, font=(FONT, 11, "bold"), fg=ACCENT_HI, bg=BG).pack(side="left")
            big_button(row, "Закрыть", t.destroy, CARD_HI, LINE).pack(side="right")
        else:
            big_button(row, "▶  Запустить %s" % name, go).pack(side="right")
            small_button(row, "Закрыть", t.destroy, bg=BG).pack(side="right", padx=8)
        more = small_button(row, "Подробнее, что сделано", toggle, bg=BG)
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
            messagebox.showwarning("Игра запущена", "Сначала закройте Minecraft, потом выбирайте карту или сборку.")
            return False
        if tlauncher_running():
            return messagebox.askyesno(
                "TLauncher открыт",
                "TLauncher сейчас открыт и при закрытии перезапишет выбор версии.\n"
                "Лучше закрыть его и нажать ещё раз.\n\nПродолжить всё равно?")
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
        fresh = False
        if map_installed(m):
            ans = messagebox.askyesnocancel(
                m["title"], "Карта уже установлена.\n\nДа: продолжить своё сохранение\n"
                            "Нет: начать заново (старое сохранение останется копией)")
            if ans is None:
                return
            fresh = not ans
        pack = next((p for p in packs if p["name"] == pick), None)
        run_task("Готовлю «%s»" % m["title"], lambda log: install_map(m, pack, fresh, packs, log), finish)

    def open_map(m):
        packs = find_packs()
        t, body = detail_window(m["title"])
        img = detail_image(t, os.path.join(m["path"], "cover_big.png"), os.path.join(m["path"], "cover.png"))
        badges = [(m["version"], BLUE, "white"), (m["genre"], LINE, TEXT)]
        if m.get("resource_pack") == "встроен":
            badges.append(("ресурс-пак встроен", "#5a4c1c", "#f3d27a"))
        if map_installed(m):
            badges.append(("установлена", "#2f6b34", "white"))
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
        dpara(body, "Версия игры: %s. Программа сама скачает её и выберет в TLauncher." % m["version"], bullet=True)
        dpara(body, "Сборки, с которыми можно играть: %s." % (", ".join(p["name"].rsplit(" (", 1)[0] for p in opts)
                                                              if opts else "только без модов"), bullet=True)
        dpara(body, "Размер мира: %s МБ, файлов %d." % (("%.1f" if mb < 10 else "%.0f") % mb, nf), bullet=True)
        dpara(body, "Сохранение в игре: saves\\%s" % m["save"], bullet=True)
        row = dbuttons(body)
        choices = ([] if m.get("requires_pack") and opts else ["%s (чистая %s)" % (NO_MODS, m["version"])]) + \
            [p["name"] for p in opts]
        var = tk.StringVar(value=m.get("recommended") if m.get("recommended") in choices else choices[0])
        dropdown(row, var, choices, BG).pack(side="left")
        big_button(row, "Играть", lambda: start_map(m, var.get(), packs)).pack(side="right")
        if map_installed(m):
            small_button(row, "Папка мира", lambda: os.startfile(os.path.join(SAVES, m["save"])),
                         bg=BG).pack(side="right", padx=8)

            def do_backup():
                def after(path, logs):
                    if path and messagebox.askyesno("Копия готова", "\n".join(logs) + "\n\nОткрыть папку с копиями?", parent=t):
                        os.startfile(os.path.dirname(path))
                run_task("Делаю копию мира «%s»" % m["title"], lambda log: backup_world(m, log), after)
            small_button(row, "Резервная копия", do_backup, bg=BG).pack(side="right")
        auto_wrap(body)

    def open_pack(p):
        packs = find_packs()
        cur = current() or {}
        t, body = detail_window(p["name"].rsplit(" (", 1)[0])
        img = detail_image(t, os.path.join(p["path"], "cover_big.png"), os.path.join(p["path"], "cover.png"))
        active = cur.get("name") == p["name"] and cur.get("version_dir") == p["version_dir"]
        badges = [(p["version_dir"], BLUE, "white"), ("%d модов" % p["count"] if p["count"] else "без модов", LINE, TEXT)]
        if active:
            badges.append(("включена", "#2f6b34", "white"))
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
        dpara(body, "Размер модов: %s МБ." % (("%.1f" if mb < 10 else "%.0f") % mb), bullet=True)
        row = dbuttons(body)

        def enable():
            if not check_game():
                return
            run_task("Включаю «%s»" % p["name"], lambda log: switch(p, packs, True, log), finish)
        big_button(row, "Включена" if active else "Включить", enable,
                   color=CARD_HI if active else ACCENT, hover=LINE if active else ACCENT_HI).pack(side="right")
        small_button(row, "Папка сборки", lambda: os.startfile(p["path"]), bg=BG).pack(side="right", padx=8)
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
                live.configure(text="● Онлайн: %s игроков из %s   ·   пинг %d мс   ·   %s" % (
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
        big_button(row, "Добавить в игру", add).pack(side="right")
        small_button(row, "Копировать адрес", copy, bg=BG).pack(side="right", padx=8)
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
        section("Карты", 0, "выбери карту и с какой сборкой играть, остальное программа сделает сама")
        if not maps:
            empty_note(2, LIBRARY_NOTE if not library_found() else "Карт пока нет. Добавь папку с картой в «Карты».")
        flt = tk.Frame(inner, bg=BG)
        flt.grid(row=1, column=0, columnspan=2, sticky="we", pady=(0, 12))

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
        maps = [m for m in maps if map_matches(m, state.get("f_players", "all"), state.get("f_version", "all"))]
        tk.Label(flt, text="   показано %d из %d" % (len(maps), total), font=(FONT, 9), fg=MUTED, bg=BG).pack(side="left")
        if total and not maps:
            empty_note(2, "Под этот фильтр карт нет. Нажми «Все», чтобы сбросить.")
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

            def play(m=m, var=var, packs=packs):
                if not check_game():
                    return
                fresh = False
                if map_installed(m):
                    ans = messagebox.askyesnocancel(
                        m["title"], "Карта уже установлена.\n\nДа: продолжить своё сохранение\n"
                                    "Нет: начать заново (старое сохранение останется копией)")
                    if ans is None:
                        return
                    fresh = not ans
                pick = var.get()
                pack = next((p for p in packs if p["name"] == pick), None)
                run_task("Готовлю «%s»" % m["title"],
                         lambda log: install_map(m, pack, fresh, packs, log), finish)
            big_button(row, "Играть", play).pack(side="right")
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

        if not packs:
            empty_note(1, LIBRARY_NOTE)
        for i, p in enumerate(packs):
            c = card(inner, i % 2, 1 + i // 2)
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
            badge(bl, "%d модов" % p["count"] if p["count"] else "без модов").pack(side="left", padx=(0, 6))
            active = cur.get("name") == p["name"] and cur.get("version_dir") == p["version_dir"]
            if active:
                badge(bl, "включена", "#2f6b34", "white").pack(side="left")
            tk.Label(c, text=p.get("description", ""), font=(FONT, 10), fg="#c3c7d1", bg=CARD,
                     justify="left", anchor="w", wraplength=400).pack(fill="x", pady=(10, 10))
            row = tk.Frame(c, bg=CARD)
            row.pack(fill="x", side="bottom")

            def enable(p=p, packs=packs):
                if not check_game():
                    return
                run_task("Включаю «%s»" % p["name"], lambda log: switch(p, packs, True, log), finish)
            big_button(row, "Включена" if active else "Включить", enable,
                       color=CARD_HI if active else ACCENT, hover=LINE if active else ACCENT_HI).pack(side="right")
            small_button(row, "Подробнее: моды", lambda p=p: open_pack(p), bg=CARD).pack(side="right", padx=8)
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
        big_button(head_f, "Добавить все в игру", add_all).pack(side="right")
        if not load_servers():
            empty_note(1, LIBRARY_NOTE if not library_found() else "Список серверов пуст: нет файла servers.json.")
            return
        box = tk.Frame(inner, bg=CARD, padx=6, pady=6)
        box.grid(row=1, column=0, columnspan=2, sticky="we", padx=(0, 14))
        for i, s in enumerate(load_servers()):
            r = tk.Frame(box, bg=CARD if i % 2 else CARD_HI, padx=12, pady=8)
            r.pack(fill="x")
            name, _, modes = s["name"].partition(" - ")
            icon = os.path.join(ROOT, s.get("icon", ""))
            if s.get("icon") and os.path.isfile(icon):
                tk.Label(r, image=image(icon), bg=r["bg"]).pack(side="left", padx=(0, 12))
            tk.Label(r, text=name, font=(FONT, 11, "bold"), fg=TEXT, bg=r["bg"], width=15, anchor="w").pack(side="left")
            tk.Label(r, text=modes, font=(FONT, 10), fg="#c3c7d1", bg=r["bg"], width=26, anchor="w").pack(side="left")
            tk.Label(r, text=s["ip"], font=("Consolas", 10), fg=ACCENT_HI, bg=r["bg"], width=23, anchor="w").pack(side="left")
            tk.Label(r, text="%s   %s" % (s.get("versions", ""), s.get("lang", "")), font=(FONT, 9),
                     fg=MUTED, bg=r["bg"], anchor="w").pack(side="left")

            def copy(ip=s["ip"]):
                win.clipboard_clear(); win.clipboard_append(ip)
                toast("Адрес скопирован: " + ip)
            small_button(r, "Копировать адрес", copy, bg=r["bg"]).pack(side="right")
            small_button(r, "Подробнее", lambda s=s: open_server(s), bg=r["bg"]).pack(side="right")
            clickable(r, lambda s=s: open_server(s))
        tk.Label(inner, text="Как играть: выбери «Без модов» или «Только шейдеры», зайди в «Сетевую игру», "
                             "при первом входе напиши /register пароль пароль, потом /login пароль.",
                 font=(FONT, 10), fg=MUTED, bg=BG, wraplength=900, justify="left", anchor="w"
                 ).grid(row=2, column=0, columnspan=2, sticky="we", pady=(12, 0))

    # --- вкладка «С другом» ---
    def build_friend():
        section("Игра с другом", 0, "свой сервер не нужен: один играет в свой мир, второй к нему подключается")
        vpn = vpn_addresses()
        if state.get("net") not in NET_KINDS:
            state["net"] = "Hamachi" if "Hamachi" in vpn or not vpn else next(iter(vpn))
        kind = state["net"]
        info = NET_SETUP[kind]
        addr = lan_ip() if kind == LAN_KIND else vpn.get(kind)
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
            else:
                text = "● Нет подключения к роутеру" if kind == LAN_KIND else "● %s не включён" % kind
                tk.Label(parent, text=text, font=(FONT, 10, "bold"), fg="#e05a5a", bg=CARD).pack(anchor="w", pady=(6, 10))

        # выбор сети
        sel = tk.Frame(inner, bg=BG)
        sel.grid(row=1, column=0, columnspan=2, sticky="we", pady=(0, 12))
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
        setup.grid(row=2, column=0, columnspan=2, sticky="we", padx=(0, 14), pady=(0, 14))
        tk.Label(setup, text="Настройка перед первой игрой: делают оба", font=(FONT, 14, "bold"),
                 fg=TEXT, bg=CARD, anchor="w").pack(fill="x", pady=(0, 6))
        for i, line in enumerate(info["setup"], 1):
            para(setup, line, i, wrap=900)
        br = tk.Frame(setup, bg=CARD)
        br.pack(fill="x", pady=(10, 0))

        def check():
            run_task("Проверяю настройки Windows, до 20 секунд", lambda log: network_checks(kind),
                     lambda res, logs: state.update(checks=res))
        big_button(br, "Проверить настройки", check).pack(side="left")
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
        host = card(inner, 0, 3)
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
        big_button(row, "Найти мою игру", find_game).pack(side="left")
        h = state.get("host")
        if h:
            box = tk.Frame(host, bg=PANEL, padx=12, pady=10)
            box.pack(fill="x", pady=(12, 0))
            tk.Label(box, text="Адрес для друга (%s)" % h["vpn"], font=(FONT, 9), fg=MUTED, bg=PANEL).pack(anchor="w")
            ar = tk.Frame(box, bg=PANEL)
            ar.pack(fill="x")
            tk.Label(ar, text=h["address"], font=("Consolas", 15, "bold"), fg=ACCENT_HI, bg=PANEL).pack(side="left")
            small_button(ar, "Копировать", lambda: copy(h["address"], "Адрес"), bg=PANEL).pack(side="right")
            tk.Label(box, text="Код приглашения: друг вставит его, и программа сама всё настроит",
                     font=(FONT, 9), fg=MUTED, bg=PANEL).pack(anchor="w", pady=(8, 2))
            cr = tk.Frame(box, bg=PANEL)
            cr.pack(fill="x")
            field(cr, h["code"], readonly=True).pack(side="left", fill="x", expand=True, ipady=4)
            small_button(cr, "Копировать код", lambda: copy(h["code"], "Код"), bg=PANEL).pack(side="right", padx=(8, 0))
            tk.Label(box, text="Игра отвечает: %s, игроков %s из %s. Сборка: %s" % (
                h["info"]["version"], h["info"]["online"], h["info"]["max"], h["pack"] or "без модов, " + str(h["tl"])),
                font=(FONT, 9), fg=MUTED, bg=PANEL, wraplength=400, justify="left").pack(anchor="w", pady=(8, 0))

        # гость
        guest = card(inner, 1, 3)
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
                parse_invite(text)
            except Exception as e:
                messagebox.showwarning("Код", str(e)); return
            if not check_game():
                return
            state["join_text"] = text

            def after(ok, logs):
                state["join_logs"] = logs
                if auto_var.get() and not tlauncher_running():
                    launch_now()
                else:
                    toast("Готово: игра друга добавлена в «Сетевую игру».", "ok",
                          ("Запустить", launch_now), 9000)
            run_task("Подключаюсь к другу", lambda log: join_friend(text, None, log), after)
        big_button(row2, "Подключиться", join).pack(side="left")
        for line in state.get("join_logs", []):
            color = ACCENT_HI if "отвечает:" in line else ("#e0a45a" if "не отвечает" in line or "Внимание" in line else "#c3c7d1")
            tk.Label(guest, text=line, font=(FONT, 9), fg=color, bg=CARD, wraplength=410,
                     justify="left", anchor="w").pack(fill="x", pady=(4, 0))

        tk.Label(inner, text="Важно: ники в TLauncher у вас должны быть разными, иначе второй не зайдёт. "
                             "Сеть выбирайте одну и ту же. Если друг не подключается, у хоста первым делом "
                             "нажмите «Проверить настройки».",
                 font=(FONT, 10), fg=MUTED, bg=BG, wraplength=900, justify="left", anchor="w"
                 ).grid(row=4, column=0, columnspan=2, sticky="we", pady=(4, 0))

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
        section("Лаунчеры", 0, "чем запускать Minecraft: плюсы, минусы и ссылки на официальные сайты")
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
                    show("launchers")
            poll()
            installed = {}
        mine = chosen_launcher()
        info = tk.Frame(inner, bg=PANEL, padx=16, pady=12)
        info.grid(row=1, column=0, columnspan=2, sticky="we", padx=(0, 14), pady=(0, 14))
        tk.Label(info, text="Мой лаунчер: %s" % launcher_title(mine), font=(FONT, 12, "bold"), fg=TEXT, bg=PANEL,
                 anchor="w").pack(fill="x")
        tk.Label(info, text=("Программа открывает его после выбора карты или сборки. " +
                             ("Версию в нём она выбирает сама." if mine == "tlauncher" else
                              "Версию в нём выбери сам: программа подскажет, какую.")) +
                 ("" if state.get("installed_launchers") is not None else "  Ищу установленные лаунчеры..."),
                 font=(FONT, 10), fg=MUTED, bg=PANEL, anchor="w", justify="left", wraplength=900).pack(fill="x", pady=(4, 0))
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
            tk.Label(c, text="С нашей программой: " + x["compat_text"], font=(FONT, 9, "bold"), fg="#c3c7d1", bg=CARD,
                     anchor="w", justify="left", wraplength=400).pack(fill="x", pady=(8, 8))
            row = tk.Frame(c, bg=CARD)
            row.pack(fill="x", side="bottom")

            def make_mine(x=x):
                s = load_settings(); s["launcher"] = x["id"]; save_settings(s)
                toast("Теперь мой лаунчер: " + x["name"]); refresh_foot()
                show("launchers")

            def site(x=x):
                import webbrowser
                webbrowser.open(x["site"])
                toast("Открываю официальный сайт: " + x["site"], "info")
            big_button(row, "Скачать с сайта", site, color=CARD_HI, hover=LINE).pack(side="right")
            if x["id"] in installed:
                small_button(row, "▶ Запустить", lambda t=installed[x["id"]], n=x["name"]: (run_target(t), toast("Открываю " + n)), bg=CARD).pack(side="right", padx=6)
            if x["id"] != mine and x["compat"] != "manual":
                small_button(row, "Сделать моим", make_mine, bg=CARD).pack(side="left")

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
        clear()
        {"maps": build_maps, "packs": build_packs, "servers": build_servers, "friend": build_friend,
         "launchers": build_launchers}[key]()
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
        fresh = not load_local_manifest() or not library_found()
        title = ("Скачать библиотеку: карты, сборки и моды" if fresh else
                 "Вышло обновление %s" % remote.get("version"))
        tk.Label(upd_bar, text=title, font=(FONT, 11, "bold"), fg="white", bg=ubg).pack(side="left")
        ch = remote.get("changes") or []
        if ch and not fresh:
            tk.Label(upd_bar, text="   " + ch[0] + ("  и ещё %d" % (len(ch) - 1) if len(ch) > 1 else ""),
                     font=(FONT, 10), fg="#b9d9bd", bg=ubg).pack(side="left")

        def later():
            upd_bar.pack_forget()
        small_button(upd_bar, "Позже", later, bg=ubg).pack(side="right", padx=(4, 12))
        big_button(upd_bar, "Скачать" if fresh else "Обновить", lambda: open_update(remote)).pack(side="right", pady=6)
        if not upd_bar.winfo_ismapped():
            upd_bar.pack(fill="x", padx=24, pady=(10, 0), before=body)

    def check_updates(manual=False):
        box = {}

        def work():
            try:
                box["remote"] = fetch_remote_manifest()
            except Exception as e:
                box["err"] = str(e)
        th = threading.Thread(target=work, daemon=True)
        th.start()
        if manual:
            toast("Проверяю обновления на GitHub...", "info", ms=2500)

        def poll():
            if th.is_alive():
                win.after(200, poll)
                return
            if "err" in box:
                if manual:
                    toast("Не удалось проверить обновления: нет связи с GitHub (%s)." % box["err"][:80], "err", ms=6000)
                elif not library_found():
                    messagebox.showwarning("Не вижу файлов", LIBRARY_NOTE + "\n\nСейчас программа запущена из:\n" + ROOT)
                return
            remote = box["remote"]
            state["remote"] = remote
            if update_available(remote):
                show_update_bar(remote)
                if manual or not library_found():
                    open_update(remote)
            elif manual:
                toast("У тебя последняя версия библиотеки: %s." % remote.get("version"), "ok")
        poll()

    def open_update(remote):
        if state["busy"]:
            toast("Подожди, программа сейчас занята.", "warn")
            return
        if state.get("upd_win") is not None and state["upd_win"].winfo_exists():
            state["upd_win"].lift()
            return
        local = load_local_manifest()
        fresh = not local or not library_found()
        t = tk.Toplevel(win)
        state["upd_win"] = t
        t.title("Обновление библиотеки")
        t.configure(bg=BG)
        t.transient(win)
        W, H = 660, 620
        t.geometry("%dx%d+%d+%d" % (W, H, win.winfo_rootx() + (win.winfo_width() - W) // 2,
                                    win.winfo_rooty() + 50))
        t.minsize(560, 480)
        fr = tk.Frame(t, bg=BG, padx=26, pady=22)
        fr.pack(fill="both", expand=True)
        top = tk.Frame(fr, bg=BG)
        top.pack(fill="x")
        tk.Label(top, image=art("update.png", 1, 1), bg=BG).pack(side="left", anchor="n")
        info = tk.Frame(top, bg=BG, padx=18)
        info.pack(side="left", fill="both", expand=True)
        tk.Label(info, text="Скачать библиотеку" if fresh else "Обновление библиотеки", font=(FONT, 20, "bold"),
                 fg=TEXT, bg=BG, anchor="w").pack(fill="x")
        tk.Label(info, text=("Версия %s с GitHub" % remote.get("version")) if fresh else
                 "У тебя: %s   →   новая: %s" % (local.get("version", "?"), remote.get("version")),
                 font=(FONT, 11), fg=ACCENT_HI, bg=BG, anchor="w").pack(fill="x", pady=(6, 0))
        tk.Label(info, text="Программа скачает только то, что изменилось. Твои миры и настройки игры "
                            "не трогаются: обновляется только папка с картами и сборками.",
                 font=(FONT, 10), fg=MUTED, bg=BG, anchor="w", justify="left", wraplength=360).pack(fill="x", pady=(8, 0))
        ch = remote.get("changes") or []
        if ch:
            tk.Label(fr, text="Что нового", font=(FONT, 13, "bold"), fg=TEXT, bg=BG, anchor="w").pack(fill="x", pady=(16, 4))
            for line in ch[:8]:
                tk.Label(fr, text="•  " + line, font=(FONT, 10), fg="#c3c7d1", bg=BG, anchor="w", justify="left",
                         wraplength=W - 80).pack(fill="x")
        msg = tk.Label(fr, text="Сверяю файлы с новой версией...", font=(FONT, 10, "bold"), fg=GOLD, bg=BG, anchor="w",
                       justify="left", wraplength=W - 80)
        msg.pack(fill="x", pady=(16, 6))
        bar = tk.Canvas(fr, height=12, bg=LINE, highlightthickness=0, bd=0)
        bar.pack(fill="x")
        fill = bar.create_rectangle(0, 0, 0, 12, fill=ACCENT, width=0)
        shine = bar.create_rectangle(-60, 0, -20, 12, fill=ACCENT_HI, width=0)
        sub = tk.Label(fr, text="", font=(FONT, 9), fg=MUTED, bg=BG, anchor="w", justify="left", wraplength=W - 80)
        sub.pack(fill="x", pady=(6, 0))
        row = tk.Frame(fr, bg=BG)
        row.pack(fill="x", side="bottom", pady=(14, 0))
        box = {"cancel": threading.Event(), "done": 0, "total": 1, "logs": []}
        prog = {"shown": 0.0}

        def set_bar(frac):
            w = max(1, bar.winfo_width())
            start = prog["shown"]
            prog["shown"] = frac
            animate(("bar", str(bar)), 300, lambda k: bar.coords(fill, 0, 0, w * (start + (frac - start) * k), 12))

        def shimmer():
            if not t.winfo_exists() or box.get("finished"):
                bar.coords(shine, -60, 0, -20, 12)
                return
            w = max(1, bar.winfo_width())
            animate(("shine", str(bar)), 1300, lambda k: bar.coords(shine, -60 + (w + 80) * k, 0, -20 + (w + 80) * k, 12),
                    done=shimmer, ease=lambda x: x)
        shimmer()

        def buttons(*items):
            for w in row.winfo_children():
                w.destroy()
            for kind, text, cmd in items:
                if kind == "big":
                    big_button(row, text, cmd).pack(side="right")
                else:
                    small_button(row, text, cmd, bg=BG).pack(side="right", padx=8)

        def close():
            if box.get("applying"):
                if messagebox.askyesno("Прервать?", "Обновление ещё идёт. Прервать скачивание?\n"
                                                     "Папка останется как была.", parent=t):
                    box["cancel"].set()
                return
            t.destroy()
            show(state["tab"], animated=False)
        t.protocol("WM_DELETE_WINDOW", close)
        buttons(("small", "Закрыть", close))

        def planning():
            try:
                box["plan"] = plan_update(remote, log=lambda m: box.__setitem__("step", m))
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
                msg.configure(text="Не получилось сверить файлы: " + box["err"], fg="#e05a5a")
                return
            plan = box["plan"]
            if not plan["blocks"] and not plan["files"] and not plan["remove"]:
                apply_update(remote, plan, log=lambda m: None)
                set_bar(1.0)
                msg.configure(text="Всё уже на месте: у тебя версия %s." % remote.get("version"), fg=ACCENT_HI)
                sub.configure(text="")
                upd_bar.pack_forget()
                return
            names = [b["title"] for b in plan["blocks"]]
            text = "Скачаю %s: %s" % (fmt_mb(plan["size"]), ", ".join(names[:10]) + (
                " и ещё %d" % (len(names) - 10) if len(names) > 10 else ""))
            if plan["files"]:
                text += ("; " if names else "") + "моды с Modrinth: %d" % len(plan["files"])
            msg.configure(text=text, fg=TEXT)
            rm = plan["remove"]
            sub.configure(text=("Уберу в Корзину старое: " + ", ".join(rm[:8]) + (
                " и ещё %d" % (len(rm) - 8) if len(rm) > 8 else "")) if rm else "", fg="#e0a45a")
            label = ("Скачать  (%s)" if fresh else "Обновить  (%s)") % fmt_mb(plan["size"])
            buttons(("big", label, lambda: start(plan)), ("small", "Позже", close))

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
                    box["res"] = apply_update(remote, plan, log=lambda m: box["logs"].append(m),
                                              progress=lambda d, tot: box.update(done=d, total=tot), cancel=box["cancel"])
                except Exception as e:
                    box["err2"] = str(e)
            th2 = threading.Thread(target=work, daemon=True)
            th2.start()
            last = {"t": 0}

            def poll():
                if th2.is_alive():
                    if t.winfo_exists() and time.perf_counter() - last["t"] > 0.25:
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
                if "err2" in box:
                    msg.configure(text=("Обновление прервано. Папка осталась как была." if "отменено" in box["err2"]
                                        else "Не получилось: " + box["err2"]), fg="#e05a5a")
                    buttons(("big", "Ещё раз", lambda: (t.destroy(), open_update(remote))), ("small", "Закрыть", close))
                    return
                set_bar(1.0)
                res = box["res"]
                upd_bar.pack_forget()
                msg.configure(text="Готово! Установлена версия %s." % remote.get("version"), fg=ACCENT_HI)
                sub.configure(text=("Старые файлы убраны в Корзину." if not res.get("trash") else
                                    "Старые файлы сложены в папку «%s», её можно удалить." % os.path.basename(res["trash"])),
                              fg=MUTED)
                if res.get("restart"):
                    def restart():
                        subprocess.Popen([os.path.join(ROOT, EXE_NAME)], cwd=ROOT)
                        win.destroy()
                    tk.Label(fr, text="Программа тоже обновилась: перезапусти её.", font=(FONT, 10, "bold"),
                             fg=GOLD, bg=BG, anchor="w").pack(fill="x", pady=(8, 0))
                    buttons(("big", "Перезапустить программу", restart))
                else:
                    buttons(("big", "Закрыть", close))
                toast("Библиотека обновлена до версии %s." % remote.get("version"), "ok")
            poll()
        poll_plan()
        t.bind("<Escape>", lambda e: close())
        fade_in_window(t)

    # --- запуск: заставка, потом окно ---
    win._hooks = {"finish": finish, "open_update": open_update, "toast": toast, "show": show, "body": canvas}
    show("maps", animated=False)

    def reveal():
        def gone():
            splash.destroy()
            win.deiconify()
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
        if not library_found():
            check_updates()
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
    if len(sys.argv) > 1:
        sys.exit(cli(sys.argv[1:]))
    gui()

"""Direct (launcher-less) offline start of an installed Minecraft Java version on Windows.

Stdlib only. Reads the standard ``.minecraft`` layout:

    versions/<id>/<id>.json, versions/<id>/<id>.jar
    libraries/<maven path>
    assets/indexes/<index>.json, assets/objects/.., assets/log_configs/<file>

Understands vanilla jsons (old ``minecraftArguments`` and modern ``arguments``),
``inheritsFrom`` chains written by Fabric/Quilt/Forge/NeoForge installers, and the
flattened TLauncher-style jsons (``"values"`` instead of ``"value"``, top-level
``"artifact"`` in libraries, ``"rules": []``).

Public API:
    build_launch(...) -> (argv, cwd, env_extra)
    launch(...)       -> subprocess.Popen
    java_component(version_id, mc_dir) -> "java-runtime-delta" | ...
    java_major(version_id, mc_dir)     -> 21 | 17 | 8 | ...
    offline_uuid(nick)                 -> 32 hex chars
    find_javaw(component, mc_dir)      -> path or None (helper)
    MissingFilesError (subclass of RuntimeError, has .missing list)
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile

__all__ = [
    "build_launch", "launch", "java_component", "java_major", "offline_uuid",
    "find_javaw", "MissingFilesError", "LAUNCHER_NAME", "LAUNCHER_VERSION",
]

LAUNCHER_NAME = "Portalis"
LAUNCHER_VERSION = "1.0"
CP_SEP = ";" if os.name == "nt" else ":"
OS_NAME = {"win32": "windows", "darwin": "osx"}.get(sys.platform, "linux")

# Same defaults the official launcher writes into a new profile.
DEFAULT_GC_ARGS = [
    "-XX:+UnlockExperimentalVMOptions",
    "-XX:+UseG1GC",
    "-XX:G1NewSizePercent=20",
    "-XX:G1ReservePercent=20",
    "-XX:MaxGCPauseMillis=50",
    "-XX:G1HeapRegionSize=32M",
]


class MissingFilesError(RuntimeError):
    """Raised when files needed to start the game are absent. ``.missing`` holds all paths."""

    def __init__(self, version_id: str, missing: list[str]):
        self.version_id = version_id
        self.missing = list(missing)
        head = "\n  ".join(self.missing[:6])
        more = f"\n  ... and {len(self.missing) - 6} more" if len(self.missing) > 6 else ""
        super().__init__(
            f"Version '{version_id}' is not fully installed: {len(self.missing)} file(s) missing:\n  {head}{more}")


# --------------------------------------------------------------------------- basics

def offline_uuid(nick: str) -> str:
    """UUID v3 of "OfflinePlayer:<nick>" exactly like Java's UUID.nameUUIDFromBytes (no namespace)."""
    h = bytearray(hashlib.md5(("OfflinePlayer:" + nick).encode("utf-8")).digest())
    h[6] = (h[6] & 0x0F) | 0x30
    h[8] = (h[8] & 0x3F) | 0x80
    return h.hex()


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def _version_json_path(mc_dir: str, vid: str) -> str:
    return os.path.join(mc_dir, "versions", vid, vid + ".json")


def _load_chain(version_id: str, mc_dir: str) -> list[dict]:
    """Return [child, parent, grandparent, ...]."""
    chain, seen, vid = [], set(), version_id
    while vid:
        if vid in seen:
            raise RuntimeError(f"inheritsFrom loop at '{vid}'")
        seen.add(vid)
        p = _version_json_path(mc_dir, vid)
        if not os.path.isfile(p):  # the version itself or its parent (e.g. vanilla) json
            raise MissingFilesError(version_id, [p])
        j = _read_json(p)
        j.setdefault("id", vid)
        j["_dir_id"] = vid  # folder name; TLauncher ids may differ from folder in rare cases
        chain.append(j)
        vid = j.get("inheritsFrom")
    return chain


def _first(chain: list[dict], key: str, default=None):
    for j in chain:  # child first: child wins
        v = j.get(key)
        if v not in (None, "", {}, []):
            return v
    return default


def java_component(version_id: str, mc_dir: str) -> str:
    """Mojang runtime component name for this version (``jre-legacy`` when the json has none)."""
    jv = _first(_load_chain(version_id, mc_dir), "javaVersion") or {}
    return jv.get("component") or "jre-legacy"


def java_major(version_id: str, mc_dir: str) -> int:
    jv = _first(_load_chain(version_id, mc_dir), "javaVersion") or {}
    try:
        return int(float(jv.get("majorVersion") or 8))
    except (TypeError, ValueError):
        return 8


def find_javaw(component: str, mc_dir: str, extra_roots: list[str] | None = None) -> str | None:
    """Look for javaw.exe of a Mojang runtime component in the usual places."""
    roots = list(extra_roots or [])
    roots.append(os.path.join(mc_dir, "runtime"))
    la = os.environ.get("LOCALAPPDATA", "")
    if la:
        roots.append(os.path.join(la, "Portalis", "runtime"))
        pk = os.path.join(la, "Packages")
        if os.path.isdir(pk):
            for d in os.listdir(pk):
                if d.startswith("Microsoft.4297127D64EC6_"):
                    roots.append(os.path.join(pk, d, "LocalCache", "Local", "runtime"))
    exe = "javaw.exe" if os.name == "nt" else "java"
    for r in roots:
        for plat in ("windows-x64", "windows", "windows-x86", "windows-arm64"):
            cand = os.path.join(r, component, plat, component, "bin", exe)
            if os.path.isfile(cand):
                return cand
    return None


# --------------------------------------------------------------------------- rules

def _java_is_32bit(java: str) -> bool:
    p = java.replace("\\", "/").lower()
    return "windows-x86/" in p or "/x86/" in p


def _rule_matches(rule: dict, features: dict, arch32: bool) -> bool:
    os_r = rule.get("os")
    if os_r:
        name = os_r.get("name")
        if name and name != OS_NAME:
            return False
        arch = os_r.get("arch")
        if arch:
            if arch == "x86" and not arch32:
                return False
            if arch in ("x64", "x86_64", "amd64") and arch32:
                return False
            if arch in ("arm64", "aarch64"):
                return False
        ver = os_r.get("version")
        if ver:
            try:
                if not re.search(ver, platform.version()):
                    return False
            except re.error:
                return False
    feats = rule.get("features")
    if feats:
        for k, v in feats.items():
            if bool(features.get(k, False)) != bool(v):
                return False
    return True


def _allowed(rules, features: dict, arch32: bool) -> bool:
    if not rules:  # absent or [] (TLauncher writes "rules": [])
        return True
    allowed = False
    for r in rules:
        if _rule_matches(r, features, arch32):
            allowed = r.get("action", "allow") == "allow"
    return allowed


# --------------------------------------------------------------------------- libraries

def _maven_path(name: str, classifier: str | None = None) -> str:
    """group:artifact:version[:classifier][@ext] -> relative path."""
    ext = "jar"
    if "@" in name:
        name, ext = name.split("@", 1)
    parts = name.split(":")
    group, artifact, version = parts[0], parts[1], parts[2]
    if classifier is None and len(parts) > 3:
        classifier = parts[3]
    fname = f"{artifact}-{version}" + (f"-{classifier}" if classifier else "") + f".{ext}"
    return "/".join(group.split(".") + [artifact, version, fname])


def _lib_key(name: str) -> str:
    parts = name.split("@", 1)[0].split(":")
    key = parts[0] + ":" + (parts[1] if len(parts) > 1 else "")
    if len(parts) > 3:
        key += ":" + parts[3]
    return key


def _clean_rel(path: str) -> str:
    path = path.replace("\\", "/").lstrip("/")
    if path.startswith("libraries/"):  # TLauncher sometimes stores paths relative to .minecraft
        path = path[len("libraries/"):]
    return path


def _artifact_rel(lib: dict) -> str | None:
    """Relative path (inside libraries/) of the main artifact, or None if the lib is natives-only."""
    dl = lib.get("downloads")
    art = None
    if isinstance(dl, dict):
        art = dl.get("artifact")
        if art is None and "classifiers" in dl and lib.get("natives"):
            return None  # old natives-only entry (e.g. lwjgl-platform)
    if art is None:
        art = lib.get("artifact")  # TLauncher flattened format
    if isinstance(art, dict) and art.get("path"):
        return _clean_rel(art["path"])
    if lib.get("natives") and not art and isinstance(dl, dict):
        return None
    return _maven_path(lib["name"])


def _natives_rel(lib: dict, arch32: bool) -> str | None:
    nat = lib.get("natives")
    if not nat or OS_NAME not in nat:
        return None
    classifier = nat[OS_NAME].replace("${arch}", "32" if arch32 else "64")
    dl = lib.get("downloads") or {}
    c = (dl.get("classifiers") or {}).get(classifier) or (lib.get("classifiers") or {}).get(classifier)
    if isinstance(c, dict) and c.get("path"):
        return _clean_rel(c["path"])
    return _maven_path(lib["name"], classifier)


def _collect_libraries(chain: list[dict], features: dict, arch32: bool):
    """-> (classpath rel paths, [(natives rel path, exclude list)]) with child-first dedupe."""
    seen: set[str] = set()
    cp: list[str] = []
    natives: list[tuple[str, list[str]]] = []
    for j in chain:  # child first
        for lib in j.get("libraries") or []:
            name = lib.get("name")
            if not name or not _allowed(lib.get("rules"), features, arch32):
                continue
            key = _lib_key(name)
            has_nat = bool(lib.get("natives"))
            dkey = key + ("#natives" if has_nat else "")
            if dkey in seen:
                continue
            seen.add(dkey)
            rel = _artifact_rel(lib)
            if rel:
                cp.append(rel)
            if has_nat:
                nrel = _natives_rel(lib, arch32)
                if nrel:
                    natives.append((nrel, (lib.get("extract") or {}).get("exclude") or []))
    return cp, natives


def _extract_natives(lib_dir: str, natives: list[tuple[str, list[str]]], dest: str, log) -> None:
    os.makedirs(dest, exist_ok=True)
    for rel, exclude in natives:
        jar = os.path.join(lib_dir, rel)
        try:
            with zipfile.ZipFile(jar) as z:
                for info in z.infolist():
                    n = info.filename
                    if info.is_dir() or n.startswith("META-INF/") or any(n.startswith(e) for e in exclude):
                        continue
                    target = os.path.normpath(os.path.join(dest, n))
                    if not target.startswith(os.path.normpath(dest) + os.sep):
                        continue  # zip-slip guard
                    if os.path.isfile(target) and os.path.getsize(target) == info.file_size:
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    try:
                        with z.open(info) as src, open(target, "wb") as out:
                            shutil.copyfileobj(src, out)
                    except PermissionError:
                        log(f"[natives] {n} is locked (game already running?), kept the old copy")
        except (OSError, zipfile.BadZipFile) as e:
            log(f"[natives] cannot extract {jar}: {e}")


# --------------------------------------------------------------------------- arguments

_PH = re.compile(r"\$\{([A-Za-z0-9_]+)\}")


def _subst(s: str, vals: dict) -> str:
    return _PH.sub(lambda m: str(vals[m.group(1)]) if m.group(1) in vals else m.group(0), s)


def _expand_args(items, features: dict, arch32: bool) -> list[str]:
    out: list[str] = []
    for it in items or []:
        if isinstance(it, str):
            out.append(it)
            continue
        if not isinstance(it, dict) or not _allowed(it.get("rules"), features, arch32):
            continue
        v = it.get("value", it.get("values"))
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, list):
            out.extend(str(x) for x in v)
    return out


def _uses_feature(items, feature: str) -> bool:
    for it in items or []:
        if isinstance(it, dict):
            for r in it.get("rules") or []:
                if feature in (r.get("features") or {}):
                    return True
    return False


# --------------------------------------------------------------------------- assets

def _prepare_assets(mc_dir: str, game_dir: str, index_id: str, log, copy_legacy: bool) -> str:
    """Return the value for ${game_assets}; copy objects for virtual/resources indexes."""
    assets_root = os.path.join(mc_dir, "assets")
    idx_path = os.path.join(assets_root, "indexes", index_id + ".json")
    if not os.path.isfile(idx_path):
        log(f"[assets] index {idx_path} is missing; the game will start without sounds/languages")
        return assets_root
    try:
        idx = _read_json(idx_path)
    except (OSError, ValueError) as e:
        log(f"[assets] bad index {idx_path}: {e}")
        return assets_root
    if idx.get("map_to_resources"):
        target = os.path.join(game_dir, "resources")
    elif idx.get("virtual"):
        target = os.path.join(assets_root, "virtual", index_id)
    else:
        return assets_root
    if copy_legacy:
        copied = 0
        for rel, obj in (idx.get("objects") or {}).items():
            h = obj.get("hash", "")
            src = os.path.join(assets_root, "objects", h[:2], h)
            dst = os.path.join(target, *rel.split("/"))
            if not os.path.isfile(src):
                continue
            if os.path.isfile(dst) and os.path.getsize(dst) == obj.get("size", -1):
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            copied += 1
        if copied:
            log(f"[assets] copied {copied} legacy assets into {target}")
    return target


# --------------------------------------------------------------------------- main

def _client_jar(chain: list[dict], mc_dir: str) -> str:
    vdir = os.path.join(mc_dir, "versions")
    jar_id = _first(chain, "jar")
    if jar_id:
        return os.path.join(vdir, jar_id, jar_id + ".jar")
    for j in chain:  # own jar of the child if present (TLauncher/official copy), then parents
        d = j["_dir_id"]
        p = os.path.join(vdir, d, d + ".jar")
        if os.path.isfile(p):
            return p
    top = chain[-1]["_dir_id"]
    return os.path.join(vdir, top, top + ".jar")


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s) or "version"


def build_launch(version_id: str, mc_dir: str, nick: str, java: str, ram_mb: int = 4096,
                 width: int | None = None, height: int | None = None, server: str | None = None,
                 game_dir: str | None = None, log=print, *, natives_dir: str | None = None,
                 extra_jvm_args: list[str] | None = None, copy_legacy_assets: bool = True,
                 use_log_config: bool = True, check_files: bool = True):
    """Build the java command line for an installed version.

    Returns ``(argv, cwd, env_extra)``. Raises ``MissingFilesError`` (a RuntimeError) when the
    client jar, a library or a parent version json is absent.
    """
    mc_dir = os.path.abspath(mc_dir)
    game_dir = os.path.abspath(game_dir or mc_dir)
    os.makedirs(game_dir, exist_ok=True)
    chain = _load_chain(version_id, mc_dir)
    child = chain[0]
    arch32 = _java_is_32bit(java)
    lib_dir = os.path.join(mc_dir, "libraries")
    assets_root = os.path.join(mc_dir, "assets")

    # ---- merged fields (child wins / parent-then-child for arguments)
    main_class = _first(chain, "mainClass")
    if not main_class:
        raise RuntimeError(f"'{version_id}': no mainClass in the version json")
    legacy_args = _first(chain, "minecraftArguments")
    game_items, jvm_items = [], []
    for j in reversed(chain):  # parent first, then child
        a = j.get("arguments") or {}
        game_items += a.get("game") or []
        jvm_items += a.get("jvm") or []
    asset_index = _first(chain, "assetIndex") or {}
    index_id = asset_index.get("id") or _first(chain, "assets") or "legacy"
    version_type = _first(chain, "type") or "release"
    logging_cfg = ((_first(chain, "logging") or {}).get("client")) or {}

    # ---- features
    custom_res = bool(width and height)
    host, port = None, None
    if server:
        host, _, p = server.strip().rpartition(":") if ":" in server.strip() else (server.strip(), "", "")
        port = p if p.isdigit() else "25565"
        if not host:
            host = server.strip()
    quickplay_mp = bool(server) and _uses_feature(game_items, "is_quick_play_multiplayer")
    features = {
        "is_demo_user": False,
        "has_custom_resolution": custom_res,
        "has_quick_plays_support": False,
        "is_quick_play_singleplayer": False,
        "is_quick_play_multiplayer": quickplay_mp,
        "is_quick_play_realms": False,
    }

    # ---- libraries & classpath
    cp_rel, natives = _collect_libraries(chain, features, arch32)
    classpath: list[str] = []
    seen_cp: set[str] = set()
    for rel in cp_rel:
        p = os.path.normpath(os.path.join(lib_dir, rel))
        if p.lower() not in seen_cp:
            seen_cp.add(p.lower())
            classpath.append(p)

    client_jar = _client_jar(chain, mc_dir)
    client_jar_name = os.path.basename(client_jar)
    jar_version_name = child["id"]

    # Forge/NeoForge 1.17+ (BootstrapLauncher): the vanilla jar is listed in -DignoreList as
    # "${version_name}.jar" and must not end up in the module layer. If our client jar would not be
    # matched by that list (parent jar under its own name), leave it off the classpath: the
    # modular loader takes Minecraft from the patched/srg jars in libraries/.
    include_client = True
    raw_jvm = " ".join(_expand_args(jvm_items, features, arch32))
    m = re.search(r"-DignoreList=(\S+)", raw_jvm)
    if m:
        ign = [x for x in _subst(m.group(1), {"version_name": jar_version_name}).split(",") if x]
        if not any(client_jar_name.startswith(x) for x in ign):
            include_client = False
            log(f"[forge] modular loader: '{client_jar_name}' is not in ignoreList, kept off the classpath")
    if include_client and client_jar.lower() not in seen_cp:
        classpath.append(client_jar)

    # ---- natives dir (own folder so stale DLLs from other launchers never get loaded)
    if natives_dir is None:
        natives_dir = os.path.join(mc_dir, "bin", "portalis-natives", _safe_name(version_id))
    natives_dir = os.path.abspath(natives_dir)

    # ---- placeholder values
    uuid_hex = offline_uuid(nick)
    access_token = "0"
    game_assets = assets_root
    vals = {
        "auth_player_name": nick,
        "version_name": jar_version_name,
        "game_directory": game_dir,
        "assets_root": assets_root,
        "assets_index_name": index_id,
        "auth_uuid": uuid_hex,
        "auth_access_token": access_token,
        "auth_session": f"token:{access_token}:{uuid_hex}",
        "clientid": "0",
        "auth_xuid": "0",
        # Offline: "legacy" is the vanilla default for --userType and makes 1.19-1.20.x skip
        # Mojang services calls (profile keys) that would fail with a fake token. 1.20.5+/26.x
        # jsons do not pass --userType at all.
        "user_type": "legacy",
        "user_properties": "{}",
        "version_type": version_type,
        "natives_directory": natives_dir,
        "launcher_name": LAUNCHER_NAME,
        "launcher_version": LAUNCHER_VERSION,
        "classpath": CP_SEP.join(classpath),
        "classpath_separator": CP_SEP,
        "library_directory": lib_dir,
        "primary_jar": client_jar,
        "primary_jar_name": client_jar_name,
        "resolution_width": str(width or 854),
        "resolution_height": str(height or 480),
        "quickPlayPath": os.path.join(game_dir, "quickPlay", "log.json"),
        "quickPlaySingleplayer": "",
        "quickPlayMultiplayer": f"{host}:{port}" if server else "",
        "quickPlayRealms": "",
        "profile_name": nick,
    }

    # ---- files check
    if check_files:
        missing = []
        if include_client and not os.path.isfile(client_jar):
            missing.append(client_jar)
        missing += [p for p in classpath if p != client_jar and not os.path.isfile(p)]
        missing += [os.path.join(lib_dir, rel) for rel, _ in natives
                    if not os.path.isfile(os.path.join(lib_dir, rel))]
        # module path (-p / --module-path) entries of modern Forge/NeoForge
        jl = _expand_args(jvm_items, features, arch32)
        for i, a in enumerate(jl[:-1]):
            if a in ("-p", "--module-path"):
                for part in _subst(jl[i + 1], vals).split(CP_SEP):
                    part = os.path.normpath(part)
                    if part and not os.path.isfile(part) and part not in missing:
                        missing.append(part)
        if missing:
            raise MissingFilesError(version_id, missing)

    # ---- natives & assets
    os.makedirs(natives_dir, exist_ok=True)
    if natives:
        _extract_natives(lib_dir, natives, natives_dir, log)
    game_assets = _prepare_assets(mc_dir, game_dir, index_id, log, copy_legacy_assets)
    vals["game_assets"] = game_assets

    # ---- JVM args
    ram_mb = max(512, int(ram_mb))
    jvm: list[str] = [f"-Xmx{ram_mb}M", f"-Xms{min(ram_mb, 1024)}M"] + DEFAULT_GC_ARGS
    json_jvm = [_subst(a, vals) for a in _expand_args(jvm_items, features, arch32)]
    if not any("${classpath}" in a for a in _expand_args(jvm_items, features, arch32)):
        # legacy (minecraftArguments) versions: the launcher adds these itself
        if OS_NAME == "windows":
            jvm.append("-XX:HeapDumpPath=MojangTricksIntelDriversForPerformance_javaw.exe_minecraft.exe.heapdump")
        jvm += [f"-Djava.library.path={natives_dir}",
                f"-Dminecraft.launcher.brand={LAUNCHER_NAME}",
                f"-Dminecraft.launcher.version={LAUNCHER_VERSION}"]
        jvm += [a for a in json_jvm]
        jvm += ["-cp", vals["classpath"]]
    else:
        jvm += json_jvm
    if extra_jvm_args:
        jvm += list(extra_jvm_args)
    # make sure sub-folders the json points into natives_directory exist (26.x: /java /jna /lwjgl /netty)
    for a in jvm:
        if a.startswith("-D") and "=" in a:
            v = a.split("=", 1)[1]
            if v.startswith(natives_dir):
                os.makedirs(v, exist_ok=True)
    if use_log_config and logging_cfg.get("argument") and (logging_cfg.get("file") or {}).get("id"):
        xml = os.path.join(assets_root, "log_configs", logging_cfg["file"]["id"])
        if os.path.isfile(xml):
            jvm.append(logging_cfg["argument"].replace("${path}", xml))
        else:
            log(f"[logging] {xml} not found, starting without the log4j config")

    # ---- game args
    if legacy_args:
        game = [_subst(a, vals) for a in legacy_args.split()]
        game += [_subst(a, vals) for a in _expand_args(game_items, features, arch32)]
        if custom_res and "--width" not in game:
            game += ["--width", vals["resolution_width"], "--height", vals["resolution_height"]]
    else:
        game = [_subst(a, vals) for a in _expand_args(game_items, features, arch32)]
    if server and not quickplay_mp and "--server" not in game:
        game += ["--server", host, "--port", port]

    argv = [java] + jvm + [main_class] + game
    log(f"[launch] {version_id}: main={main_class}, classpath={len(classpath)} entries, "
        f"natives={'extracted ' + str(len(natives)) if natives else 'in jars'}, assets={index_id}")
    env_extra: dict[str, str] = {}
    return argv, game_dir, env_extra


def launch(version_id: str, mc_dir: str, nick: str, java: str, ram_mb: int = 4096,
           width: int | None = None, height: int | None = None, server: str | None = None,
           game_dir: str | None = None, log=print, **kw) -> subprocess.Popen:
    """Build the command and start the game. Output goes to <mc_dir>/logs/portalis-launch.log."""
    argv, cwd, env_extra = build_launch(version_id, mc_dir, nick, java, ram_mb, width, height,
                                        server, game_dir, log, **kw)
    if not os.path.isfile(java):
        raise RuntimeError(f"Java not found: {java}")
    log_dir = os.path.join(os.path.abspath(mc_dir), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "portalis-launch.log")
    env = dict(os.environ)
    for k in ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS", "CLASSPATH"):
        env.pop(k, None)  # a stray global option would break or slow the game
    env.update(env_extra)
    out = open(log_path, "w", encoding="utf-8", errors="replace")
    out.write("# " + subprocess.list2cmdline(argv) + "\n")
    out.flush()
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT, creationflags=flags, close_fds=True)
    finally:
        out.close()  # the child keeps its own handle
    proc.log_path = log_path  # type: ignore[attr-defined]
    log(f"[launch] pid {proc.pid}, log: {log_path}")
    return proc


if __name__ == "__main__":  # tiny CLI: python mc_launch.py <version> <nick> [--dry]
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("nick")
    ap.add_argument("--mc", default=os.path.join(os.environ.get("APPDATA", ""), ".minecraft"))
    ap.add_argument("--java")
    ap.add_argument("--ram", type=int, default=4096)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    j = a.java or find_javaw(java_component(a.version, a.mc), a.mc)
    if not j:
        sys.exit("javaw.exe not found for " + java_component(a.version, a.mc))
    if a.dry:
        print(subprocess.list2cmdline(build_launch(a.version, a.mc, a.nick, j, a.ram)[0]))
    else:
        launch(a.version, a.mc, a.nick, j, a.ram)

"""Проверка зависимостей модов во всех сборках библиотеки.

    python tools/check_deps.py [--lib "путь к библиотеке"] [--pack подстрока] [--offline] [-v]

Для каждой сборки (<Loader> <MC>\\<Имя>\\pack.json + mods\\*.jar):
  1. Опознаёт каждый jar на Modrinth по sha1 и сверяет версию MC и загрузчик сборки
     со списком, который указан у версии на Modrinth.
  2. Проверяет зависимости с Modrinth: required должны быть в сборке (по project_id),
     incompatible не должны.
  3. Читает метаданные самих jar (fabric.mod.json, META-INF/mods.toml, mcmod.info и @Mod
     в классах для 1.12.2), включая вложенные jar-in-jar, собирает набор mod id сборки и
     ищет обязательные зависимости, которые никто не даёт, неподходящие версии
     (Minecraft, загрузчик, другие моды), конфликты (breaks/incompatible) и дубли mod id.

Только stdlib. Код выхода 1, если найдены проблемы.
"""
import argparse
import hashlib
import io
import json
import re
import struct
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

DEFAULT_LIB = Path(r'C:\Users\Grisha\AppData\Local\Portalis')
API = 'https://api.modrinth.com/v2'
UA = 'SKUF666/minecraft-packs'

# Зависимости, которые даёт сама игра или загрузчик.
IGNORED_IDS = {'minecraft', 'java', 'fabricloader', 'fabric-loader', 'quilt_loader', 'forge', 'neoforge',
               'fml', 'mcp', 'javafml', 'lowcodefml'}
LOADER_IDS = {'fabricloader', 'fabric-loader', 'forge'}
FABRIC_API_MODULE = re.compile(r'^fabric(-[a-z0-9-]+)?-(v\d+|base)$|^fabric$')
MOD_ANNOTATION = b'Lnet/minecraftforge/fml/common/Mod;'


# ---------------------------------------------------------------- сеть

_openers = [urllib.request.build_opener(),                                  # системный прокси
            urllib.request.build_opener(urllib.request.ProxyHandler({}))]  # напрямую


def api(method, path, body=None, tries=6):
    """JSON-запрос к Modrinth с повторами (429/5xx/сетевые ошибки), по очереди через прокси и напрямую."""
    data = json.dumps(body).encode() if body is not None else None
    last = ''
    for attempt in range(tries):
        req = urllib.request.Request(API + path, data=data, method=method, headers={
            'User-Agent': UA, 'Accept': 'application/json', 'Content-Type': 'application/json'})
        wait = min(2 ** attempt, 20)
        try:
            with _openers[attempt % 2].open(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = 'HTTP %s' % e.code
            if e.code == 404:
                return None
            if e.code == 429:
                reset = e.headers.get('X-Ratelimit-Reset', '')
                wait = int(reset) + 1 if reset.isdigit() else wait
            elif e.code < 500:
                raise RuntimeError('Modrinth %s %s: %s' % (method, path[:60], last))
        except (urllib.error.URLError, OSError, ValueError) as e:
            last = str(e)[:100]
        time.sleep(wait)
    raise RuntimeError('Modrinth недоступен (%s %s): %s' % (method, path[:60], last))


def api_ids(endpoint, ids):
    """GET /projects или /versions по списку id, частями."""
    out = []
    ids = sorted(set(ids))
    for i in range(0, len(ids), 100):
        q = urllib.parse.quote(json.dumps(ids[i:i + 100]))
        out += api('GET', '/%s?ids=%s' % (endpoint, q)) or []
    return out


# ---------------------------------------------------------------- версии

def vkey(s):
    """Ключ сравнения версий: числа как числа, буквенные квалификаторы (alpha, rc, pre) младше чисел."""
    s = s.strip().lstrip('vV').split('+')[0]
    toks = re.findall(r'\d+|[A-Za-z]+', s)
    if not toks or not toks[0].isdigit():
        return None
    return [int(t) if t.isdigit() else t.lower() for t in toks]


def vcmp(a, b):
    a, b = list(a), list(b)
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else None
        y = b[i] if i < len(b) else None
        if x is None:
            x = 0 if isinstance(y, int) else ''   # «1.2» == «1.2.0», но «1.2» > «1.2-rc»
        if y is None:
            y = 0 if isinstance(x, int) else ''
        if x == y:
            continue
        if isinstance(x, int) and isinstance(y, int):
            return -1 if x < y else 1
        if isinstance(x, str) and isinstance(y, str):
            if x == '':
                return 1
            if y == '':
                return -1
            return -1 if x < y else 1
        return -1 if isinstance(x, str) else 1    # квалификатор младше числа
    return 0


def semver_match(ver, spec):
    """Диапазон в стиле fabric.mod.json. True / False / None (не удалось разобрать)."""
    if isinstance(spec, list):
        if not spec:
            return True
        res = [semver_match(ver, s) for s in spec]
        return True if True in res else (None if None in res else False)
    spec = str(spec).strip()
    if spec in ('', '*'):
        return True
    v = vkey(ver)
    if v is None:
        return None
    for part in spec.split():
        m = re.match(r'^(>=|<=|>|<|=|~|\^)?(.+)$', part)
        op, rest = m.group(1) or '=', m.group(2).rstrip('-')
        pieces = rest.split('.')
        if any(p in ('x', 'X', '*') for p in pieces):          # 1.20.x
            prefix = []
            for p in pieces:
                if p in ('x', 'X', '*'):
                    break
                if not p.isdigit():
                    return None
                prefix.append(int(p))
            if [t if isinstance(t, int) else -1 for t in (v + [0] * 5)[:len(prefix)]] != prefix:
                return False
            continue
        t = vkey(rest)
        if t is None:
            return None
        c = vcmp(v, t)
        num_v = [x for x in v if isinstance(x, int)] + [0, 0]
        num_t = [x for x in t if isinstance(x, int)] + [0, 0]
        ok = {'=': c == 0, '>=': c >= 0, '<=': c <= 0, '>': c > 0, '<': c < 0,
              '~': c >= 0 and num_v[:2] == num_t[:2],
              '^': c >= 0 and num_v[0] == num_t[0]}[op]
        if not ok:
            return False
    return True


def maven_match(ver, spec):
    """Диапазон в стиле Maven/Forge ([1.0,2.0), [47,), 1.0 = мягкая рекомендация)."""
    spec = (spec or '').strip()
    if not spec or spec == '*':
        return True
    if '${' in spec:
        return None
    if spec[0] not in '[(':
        return True
    v = vkey(ver)
    if v is None:
        return None
    for lo_b, body, hi_b in re.findall(r'([\[(])([^\[\]()]*)([\])])', spec):
        if ',' not in body:
            t = vkey(body)
            if t is None:
                return None
            if vcmp(v, t) == 0:
                return True
            continue
        lo, hi = body.split(',', 1)
        ok = True
        for bound, inclusive, sign in ((lo, lo_b == '[', 1), (hi, hi_b == ']', -1)):
            if not bound.strip():
                continue
            t = vkey(bound)
            if t is None:
                return None
            c = vcmp(v, t) * sign
            ok = ok and (c > 0 or (c == 0 and inclusive))
        if ok:
            return True
    return False


def fml12_deps(s):
    """Строка dependencies из @Mod (1.12.2): 'required-after:mclib@[2.0,);after:jei'."""
    out = []
    for part in (s or '').split(';'):
        part = part.strip()
        if ':' not in part:
            continue
        kind, rest = part.split(':', 1)
        modid, _, rng = rest.partition('@')
        if kind.strip().lower().startswith('required') and modid.strip() not in ('', '*'):
            out.append((modid.strip().lower(), rng.strip(), 'required'))
    return out


# ---------------------------------------------------------------- метаданные jar

class Mod:
    """Один мод внутри jar (их может быть несколько, плюс вложенные)."""
    def __init__(self, modid, version, scheme, nested):
        self.id = modid.lower()
        self.version = version or ''
        self.scheme = scheme            # semver | maven
        self.nested = nested            # пришёл из jar-in-jar
        self.provides = set()
        self.deps = []                  # (id, диапазон, required|breaks)


def _u2(d, p):
    return struct.unpack_from('>H', d, p)[0]


def _u4(d, p):
    return struct.unpack_from('>I', d, p)[0]


def class_mod_annotation(data):
    """Достаёт поля аннотации @Mod (modid, version, dependencies) из .class файла Forge 1.12.2."""
    if MOD_ANNOTATION not in data or data[:4] != b'\xca\xfe\xba\xbe':
        return None
    try:
        count, pos = _u2(data, 8), 10
        cp, i = [None] * count, 1
        while i < count:
            tag = data[pos]
            pos += 1
            if tag == 1:
                ln = _u2(data, pos)
                cp[i] = data[pos + 2:pos + 2 + ln].decode('utf-8', 'replace')
                pos += 2 + ln
            elif tag in (3, 4, 9, 10, 11, 12, 17, 18):
                pos += 4
            elif tag in (5, 6):
                pos += 8
                i += 1
            elif tag in (7, 8, 16, 19, 20):
                pos += 2
            elif tag == 15:
                pos += 3
            else:
                return None
            i += 1
        pos += 6
        pos += 2 + 2 * _u2(data, pos)
        for _ in range(2):                      # поля, методы
            n = _u2(data, pos)
            pos += 2
            for _ in range(n):
                ac = _u2(data, pos + 6)
                pos += 8
                for _ in range(ac):
                    pos += 6 + _u4(data, pos + 2)

        def element(p):
            tag = chr(data[p])
            p += 1
            if tag in 'BCDFIJSZsc':
                return (cp[_u2(data, p)] if tag == 's' else None), p + 2
            if tag == 'e':
                return None, p + 4
            if tag == '@':
                _, p = annotation(p)
                return None, p
            if tag == '[':
                n = _u2(data, p)
                p += 2
                for _ in range(n):
                    _, p = element(p)
                return None, p
            raise ValueError(tag)

        def annotation(p):
            typ, n = cp[_u2(data, p)], _u2(data, p + 2)
            p += 4
            vals = {}
            for _ in range(n):
                name = cp[_u2(data, p)]
                vals[name], p = element(p + 2)
            return (typ, vals), p

        ac = _u2(data, pos)
        pos += 2
        for _ in range(ac):
            name, ln = cp[_u2(data, pos)], _u4(data, pos + 2)
            if name == 'RuntimeVisibleAnnotations':
                p = pos + 6
                n = _u2(data, p)
                p += 2
                for _ in range(n):
                    (typ, vals), p = annotation(p)
                    if typ == MOD_ANNOTATION.decode():
                        return vals
            pos += 6 + ln
    except (IndexError, struct.error, ValueError, TypeError):
        return None
    return None


def read_jar(data, loader, legacy, nested=False, depth=0):
    """Список Mod из jar (байты), с рекурсией по jar-in-jar. Возвращает (mods, заметки).

    Читаются только метаданные, которые увидит загрузчик сборки: в мультилоадерных jar
    fabric.mod.json не нужен Forge и наоборот, а Forge до 1.13 знает только mcmod.info и @Mod.
    """
    mods, notes = [], []
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return mods, ['повреждённый jar']
    names = set(zf.namelist())
    inner = set()
    has_fabric = 'fabric.mod.json' in names
    has_toml = not legacy and ('META-INF/mods.toml' in names or 'META-INF/neoforge.mods.toml' in names)
    use_fabric = has_fabric and (loader == 'fabric' or not has_toml)
    use_toml = has_toml and (loader != 'fabric' or not has_fabric)

    if use_fabric:
        try:
            j = json.loads(zf.read('fabric.mod.json').decode('utf-8-sig', 'replace'), strict=False)
            m = Mod(j.get('id', '?'), str(j.get('version', '')), 'semver', nested)
            m.provides = {p.lower() for p in j.get('provides', []) if isinstance(p, str)}
            for kind, key in (('required', 'depends'), ('breaks', 'breaks')):
                for dep, rng in (j.get(key) or {}).items():
                    m.deps.append((dep.lower(), rng, kind))
            mods.append(m)
            inner |= {e['file'] for e in j.get('jars', []) if isinstance(e, dict) and e.get('file')}
        except (KeyError, ValueError) as e:
            notes.append('fabric.mod.json не читается: %s' % str(e)[:60])

    for toml_name in ('META-INF/mods.toml', 'META-INF/neoforge.mods.toml'):
        if not use_toml or toml_name not in names:
            continue
        raw = zf.read(toml_name).decode('utf-8-sig', 'replace')
        manifest_ver = ''
        if 'META-INF/MANIFEST.MF' in names:
            mf = re.search(r'^Implementation-Version:\s*(\S+)', zf.read('META-INF/MANIFEST.MF').decode('utf-8', 'replace'), re.M)
            manifest_ver = mf.group(1) if mf else ''
        try:
            t = tomllib.loads(raw)
        except tomllib.TOMLDecodeError as e:
            notes.append('%s не разбирается (%s), взяты только modId' % (toml_name, str(e)[:40]))
            for mid in re.findall(r'^\s*modId\s*=\s*"([^"]+)"', raw, re.M)[:1]:
                mods.append(Mod(mid, '', 'maven', nested))
            continue
        for entry in t.get('mods', []):
            ver = str(entry.get('version', ''))
            if '${' in ver:
                ver = manifest_ver
            mods.append(Mod(entry.get('modId', '?'), ver, 'maven', nested))
        by_id = {m.id: m for m in mods}
        for owner, deps in (t.get('dependencies') or {}).items():
            target = by_id.get(owner.lower()) or (mods[-1] if mods else None)
            if target is None or not isinstance(deps, list):
                continue
            for d in deps:
                if not isinstance(d, dict) or not d.get('modId'):
                    continue
                typ = str(d.get('type', '')).lower()
                if d.get('mandatory') is True or typ == 'required':
                    target.deps.append((d['modId'].lower(), str(d.get('versionRange', '')), 'required'))
                elif typ == 'incompatible':
                    target.deps.append((d['modId'].lower(), str(d.get('versionRange', '')), 'breaks'))
        if 'META-INF/jarjar/metadata.json' in names:
            try:
                meta = json.loads(zf.read('META-INF/jarjar/metadata.json'))
                inner |= {e['path'] for e in meta.get('jars', []) if e.get('path')}
            except (ValueError, KeyError):
                pass

    legacy_mode = not use_fabric and not use_toml
    info_deps = {}                                               # modid -> зависимости из mcmod.info
    if legacy_mode and 'mcmod.info' in names:                    # 1.12.2 и старше
        try:
            info = json.loads(zf.read('mcmod.info').decode('utf-8-sig', 'replace'), strict=False)
            lst = info.get('modList', []) if isinstance(info, dict) else info
            for e in lst:
                if isinstance(e, dict) and e.get('modid'):
                    ver = str(e.get('version', ''))
                    mods.append(Mod(e['modid'], '' if '${' in ver else ver, 'maven', nested))
                    # useDependencyInformation=true: FML берёт requiredMods отсюда, а не из @Mod
                    if str(e.get('useDependencyInformation', '')).lower() == 'true':
                        info_deps[e['modid'].lower()] = [
                            (r.split('@')[0].strip().lower(), r.partition('@')[2], 'required')
                            for r in e.get('requiredMods', []) if isinstance(r, str) and r.strip()]
        except ValueError:
            notes.append('mcmod.info не читается')

    if legacy_mode:
        # Forge 1.12.2: настоящий modid и зависимости лежат в аннотации @Mod.
        for n in names:
            if not n.endswith('.class'):
                continue
            vals = class_mod_annotation(zf.read(n))
            if not vals or not vals.get('modid'):
                continue
            mid = vals['modid'].lower()
            m = next((x for x in mods if x.id == mid), None)
            if m is None:
                m = Mod(mid, vals.get('version') or '', 'maven', nested)
                mods.append(m)
            elif vals.get('version') and '@' not in vals['version']:
                m.version = vals['version']
            if mid not in info_deps:
                m.deps += fml12_deps(vals.get('dependencies'))
        for m in mods:
            if m.id in info_deps and not m.deps:
                m.deps = info_deps[m.id]

    inner |= {n for n in names if n.endswith('.jar') and n.startswith(('META-INF/jars/', 'META-INF/jarjar/'))}
    if depth < 4:
        for n in sorted(inner):
            if n in names:
                sub, sub_notes = read_jar(zf.read(n), loader, legacy, True, depth + 1)
                mods += sub
    return mods, notes


# ---------------------------------------------------------------- сборки

class Jar:
    def __init__(self, path, loader, legacy):
        self.path = path
        self.name = path.name
        data = path.read_bytes()
        self.sha1 = hashlib.sha1(data).hexdigest()
        self.mods, self.notes = read_jar(data, loader, legacy)
        self.mr = None          # версия на Modrinth

    def label(self):
        return self.name


def find_packs(lib, flt):
    packs = []
    for pj in sorted(lib.glob('*/*/pack.json')):
        try:
            meta = json.loads(pj.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            continue
        if not meta.get('is_pack', True):
            continue
        rel = '%s / %s' % (pj.parent.parent.name, pj.parent.name)
        if flt and flt.lower() not in rel.lower():
            continue
        packs.append((rel, pj.parent, meta))
    return packs


def norm(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def check_pack(rel, folder, meta, online, verbose):
    loader = (meta.get('loader') or '').lower()
    mc = meta.get('minecraft', '')
    lver = meta.get('loader_version', '')
    legacy = bool(re.match(r'^1\.(\d|1[0-2])(\.|$)', mc))      # Forge до 1.13: mcmod.info + @Mod
    jars = [Jar(p, loader, legacy) for p in sorted((folder / 'mods').glob('*.jar'))]
    problems, warnings, notes = [], [], []

    head = '%s   [%s %s, загрузчик %s, %d jar]' % (rel, meta.get('loader'), mc, lver or '-', len(jars))
    if not jars:
        print('\n=== ' + head)
        print('  OK (модов нет)')
        return 0

    # --- набор mod id сборки
    provided = {}                       # id -> [(Mod, Jar)]
    for j in jars:
        for m in j.mods:
            for pid in {m.id} | m.provides:
                provided.setdefault(pid, []).append((m, j))
    has_fabric_api = 'fabric-api' in provided or 'fabric' in provided

    # дубли верхнего уровня (вложенные дубли загрузчик разрешает сам)
    top = {}
    for j in jars:
        for m in j.mods:
            if not m.nested:
                top.setdefault(m.id, set()).add(j.name)
    for mid, owners in sorted(top.items()):
        if len(owners) > 1:
            problems.append(('дубль', 'mod id "%s" есть в нескольких jar: %s' % (mid, ', '.join(sorted(owners)))))

    # --- Modrinth
    mr_present = {}                     # project_id -> jar
    if online:
        try:
            found = {}
            hashes = [j.sha1 for j in jars]
            for i in range(0, len(hashes), 100):
                found.update(api('POST', '/version_files', {'hashes': hashes[i:i + 100], 'algorithm': 'sha1'}) or {})
            for j in jars:
                j.mr = found.get(j.sha1)
                if j.mr:
                    mr_present[j.mr['project_id']] = j
        except RuntimeError as e:
            online = False
            problems.append(('сеть', str(e)))
    not_on_mr = [j.name for j in jars if not j.mr]

    if online:
        for j in jars:
            v = j.mr
            if not v:
                continue
            bad = []
            if mc and mc not in v.get('game_versions', []):
                gv = v.get('game_versions', [])
                bad.append('MC %s нет в списке (%s)' % (mc, ', '.join(gv[:6]) + (' ...' if len(gv) > 6 else '')))
            if loader and loader != 'vanilla' and loader not in v.get('loaders', []):
                bad.append('загрузчика %s нет (%s)' % (loader, ', '.join(v.get('loaders', []))))
            if bad:
                problems.append(('несовпадение', '%s: версия %s на Modrinth: %s' % (j.name, v.get('version_number'), '; '.join(bad))))

        # зависимости
        need_ver = {d['version_id'] for j in jars if j.mr for d in j.mr.get('dependencies', [])
                    if not d.get('project_id') and d.get('version_id')}
        ver2proj = {x['id']: x['project_id'] for x in api_ids('versions', need_ver)} if need_ver else {}
        missing, incompat = [], []
        for j in jars:
            for d in (j.mr or {}).get('dependencies', []):
                pid = d.get('project_id') or ver2proj.get(d.get('version_id'))
                kind = d.get('dependency_type')
                if not pid or pid == j.mr['project_id']:
                    continue
                if kind == 'required' and pid not in mr_present:
                    missing.append((j, pid))
                elif kind == 'incompatible' and pid in mr_present:
                    incompat.append((j, pid))
        projects = {}
        if missing or incompat:
            projects = {p['id']: p for p in api_ids('projects', {p for _, p in missing + incompat})}
        for j, pid in missing:
            p = projects.get(pid, {})
            title, slug = p.get('title', pid), p.get('slug', pid)
            # тот же мод мог прийти не с Modrinth или вложенным jar — ищем по mod id
            alias = next((mid for mid in provided if norm(mid) in (norm(slug), norm(title))), None)
            if alias:
                srcs = ', '.join(sorted({jj.name + (' (вложенный)' if m.nested else '') for m, jj in provided[alias]}))
                notes.append('%s: нужен «%s» (%s) — на Modrinth не найден, но mod id "%s" даёт %s' % (j.name, title, slug, alias, srcs))
            elif any(norm(d[0]) in (norm(slug), norm(title)) or (slug == 'fabric-api' and FABRIC_API_MODULE.match(d[0]))
                     for m in j.mods for d in m.deps if d[2] == 'required'):
                continue    # jar объявляет её сам — проверка по метаданным ниже скажет точнее
            elif any(m.deps for m in j.mods):
                # в самом jar зависимость не объявлена — загрузчик её не потребует
                warnings.append('%s: на Modrinth указан обязательным «%s» (%s), но в метаданных jar такой '
                                'зависимости нет — скорее всего устаревшая карточка Modrinth' % (j.name, title, slug))
            else:
                problems.append(('нет зависимости', '%s требует «%s» (modrinth: %s) [по Modrinth]' % (j.name, title, slug)))
        for j, pid in incompat:
            p = projects.get(pid, {})
            problems.append(('несовместимость', '%s несовместим с «%s» (%s), а он в сборке: %s'
                             % (j.name, p.get('title', pid), p.get('slug', pid), mr_present[pid].name)))

    # --- метаданные jar (для всех jar: это то, что реально проверит загрузчик)
    for j in jars:
        for m in j.mods:
            where = j.name + ('' if not m.nested else ' → вложенный %s' % m.id)
            match = semver_match if m.scheme == 'semver' else maven_match
            for dep, rng, kind in m.deps:
                if kind == 'required':
                    if dep == 'minecraft':
                        if mc and match(mc, rng) is False:
                            problems.append(('версия', '%s: нужен Minecraft %s, в сборке %s' % (where, rng, mc)))
                        continue
                    if dep in LOADER_IDS:
                        if lver and match(lver, rng) is False:
                            problems.append(('версия', '%s: нужен загрузчик %s %s, в сборке %s' % (where, dep, rng, lver)))
                        continue
                    if dep in IGNORED_IDS:
                        continue
                    if dep not in provided:
                        if has_fabric_api and FABRIC_API_MODULE.match(dep):
                            continue
                        problems.append(('нет зависимости', '%s требует mod id "%s"%s — его не даёт ни один jar [по метаданным]'
                                         % (where, dep, (' ' + str(rng)) if rng not in ('', '*', None) else '')))
                        continue
                    res = [match(pm.version, rng) for pm, _ in provided[dep]]
                    if res and all(r is False for r in res):
                        have = ', '.join(sorted({'%s %s' % (pm.id, pm.version) for pm, _ in provided[dep]}))
                        problems.append(('версия', '%s: нужен %s %s, в сборке %s' % (where, dep, rng, have)))
                elif kind == 'breaks' and dep in provided and dep not in IGNORED_IDS:
                    hits = [(pm, pj) for pm, pj in provided[dep] if pj is not j and match(pm.version, rng) is True]
                    if hits:
                        problems.append(('несовместимость', '%s объявляет конфликт с %s %s, а в сборке %s'
                                         % (where, dep, rng or '*', ', '.join(sorted({'%s (%s)' % (pj.name, pm.version) for pm, pj in hits})))))
        for n in j.notes:
            notes.append('%s: %s' % (j.name, n))
        if not j.mods:
            notes.append('%s: нет метаданных мода (библиотека/coremod) — зависимости не проверить' % j.name)

    # --- печать
    seen, uniq = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    print('\n=== ' + head)
    if online:
        print('  на Modrinth опознано: %d из %d' % (len(jars) - len(not_on_mr), len(jars)))
        if not_on_mr:
            print('  не на Modrinth: ' + ', '.join(not_on_mr))
    if uniq:
        print('  ПРОБЛЕМЫ (%d):' % len(uniq))
        for kind, text in sorted(uniq, key=lambda p: p[0]):
            print('   [%s] %s' % (kind, text))
    else:
        print('  OK')
    if warnings:
        print('  предупреждения (на код выхода не влияют):')
        for w in warnings:
            print('   ~ ' + w)
    if notes and (verbose or uniq):
        print('  заметки:')
        for n in notes:
            print('   - ' + n)
    elif notes:
        print('  заметок: %d (подробно: -v)' % len(notes))
    return len(uniq)


def main():
    ap = argparse.ArgumentParser(description='Проверка зависимостей модов в сборках')
    ap.add_argument('--lib', default=str(DEFAULT_LIB))
    ap.add_argument('--pack', default='', help='подстрока в «Loader MC / Имя»')
    ap.add_argument('--offline', action='store_true', help='без Modrinth, только метаданные jar')
    ap.add_argument('-v', '--verbose', action='store_true')
    a = ap.parse_args()
    packs = find_packs(Path(a.lib), a.pack)
    if not packs:
        print('Сборки не найдены в', a.lib)
        return 2
    total = 0
    summary = []
    for rel, folder, meta in packs:
        n = check_pack(rel, folder, meta, not a.offline, a.verbose)
        total += n
        summary.append((rel, n))
    print('\n=== ИТОГ')
    for rel, n in summary:
        print('  %-60s %s' % (rel, 'OK' if not n else 'проблем: %d' % n))
    return 1 if total else 0


if __name__ == '__main__':
    sys.exit(main())

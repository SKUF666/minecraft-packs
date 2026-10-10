"""Голосовой ретранслятор для пати Portalis.

Слушает обычный ws на 0.0.0.0:8765 (TLS снимает Caddy: wss://voice.arch-flow.ru/ws).

Протокол
--------
1. Клиент подключается к /ws и первым текстовым сообщением шлёт JSON
   {"token": "<Supabase access token>", "party": "<uuid пати>", "nick": "...",
    "muted": false, "loopback": false}
2. Сервер проверяет токен (GET /auth/v1/user) и членство в пати
   (GET /rest/v1/party_members), отвечает
   {"type":"welcome","slot":n,"party":...,"uid":...,"loopback":bool}
   и рассылает всем в комнате
   {"type":"roster","members":[{"slot","uid","nick","muted","speaking"}]}.
   При отказе: {"type":"error","code":...,"message":...} и закрытие с кодом
   4001 auth / 4003 not_member / 4009 full / 4400 bad_hello / 4502 auth_unavailable.
3. Бинарное сообщение клиента = один аудиокадр (до 2000 байт). Сервер шлёт его
   всем ОСТАЛЬНЫМ в комнате как bytes([slot]) + payload (в режиме loopback —
   только самому отправителю).
4. Текстовые сообщения после входа: {"type":"mute","muted":bool},
   {"type":"ping"} -> {"type":"pong"}.

Переменные окружения
--------------------
SUPABASE_URL, SUPABASE_ANON_KEY  — для проверки токенов
VOICE_HOST (0.0.0.0), VOICE_PORT (8765)
VOICE_FAKE_AUTH=1               — только для тестов: любой токен, uid = токен
VOICE_ALLOW_LOOPBACK (1)        — разрешить режим эха для проверки микрофона
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

# ------------------------------------------------------------------ настройки
HOST = os.environ.get("VOICE_HOST", "0.0.0.0")
PORT = int(os.environ.get("VOICE_PORT", "8765"))
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
FAKE_AUTH = os.environ.get("VOICE_FAKE_AUTH") == "1"
ALLOW_LOOPBACK = os.environ.get("VOICE_ALLOW_LOOPBACK", "1") == "1"

MAX_PER_ROOM = 12
MAX_SLOT = 250
MAX_FRAME = 2000            # байт полезной нагрузки
MAX_FPS = 60                # кадров в секунду с одного клиента
SEND_QUEUE = 50             # аудиокадров в очереди к одному клиенту
HELLO_TIMEOUT = 10.0        # сек на первое сообщение
IDLE_TIMEOUT = 75.0         # сек без единого сообщения от клиента
SPEAK_HOLD = 0.3            # сек тишины, после которых "говорит" гаснет
AUTH_CACHE_TTL = 300.0
HTTP_TIMEOUT = 6.0

log = logging.getLogger("voice")


# ------------------------------------------------------------------ проверка
class AuthError(Exception):
    def __init__(self, code: str, close_code: int, message: str):
        super().__init__(message)
        self.code, self.close_code, self.message = code, close_code, message


_auth_cache: dict[tuple[str, str], tuple[float, str]] = {}


def _get_json(url: str, token: str):
    req = urllib.request.Request(url, method="GET", headers={
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "portalis-voice/1",
    })
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read(65536) or b"null")


def _check_blocking(token: str, party: str) -> str:
    """Возвращает uid или бросает AuthError. Выполняется в пуле потоков."""
    try:
        user = _get_json(f"{SUPABASE_URL}/auth/v1/user", token)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AuthError("auth", 4001, "invalid token")
        raise AuthError("auth_unavailable", 4502, f"auth http {e.code}")
    except Exception as e:  # сеть, таймаут, мусор в ответе
        raise AuthError("auth_unavailable", 4502, f"auth error: {e!r}")
    uid = (user or {}).get("id") if isinstance(user, dict) else None
    if not uid:
        raise AuthError("auth", 4001, "no user id")
    q = urllib.parse.urlencode({
        "party_id": f"eq.{party}", "user_id": f"eq.{uid}", "select": "user_id",
    })
    try:
        rows = _get_json(f"{SUPABASE_URL}/rest/v1/party_members?{q}", token)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AuthError("auth", 4001, "invalid token")
        if e.code == 400:   # кривой uuid пати
            raise AuthError("not_member", 4003, "bad party id")
        raise AuthError("auth_unavailable", 4502, f"members http {e.code}")
    except Exception as e:
        raise AuthError("auth_unavailable", 4502, f"members error: {e!r}")
    if not isinstance(rows, list) or not rows:
        raise AuthError("not_member", 4003, "not a party member")
    return str(uid)


async def authenticate(token: str, party: str) -> str:
    if FAKE_AUTH:
        if not token:
            raise AuthError("auth", 4001, "empty token")
        if token.startswith("deny"):
            raise AuthError("not_member", 4003, "not a party member")
        return token
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise AuthError("auth_unavailable", 4502, "server misconfigured")
    key = (token, party)
    now = time.monotonic()
    hit = _auth_cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    uid = await asyncio.get_running_loop().run_in_executor(
        None, _check_blocking, token, party)
    if len(_auth_cache) > 2000:
        for k in [k for k, v in _auth_cache.items() if v[0] <= now] or list(_auth_cache)[:500]:
            _auth_cache.pop(k, None)
    _auth_cache[key] = (now + AUTH_CACHE_TTL, uid)
    return uid


# ------------------------------------------------------------------ комнаты
class Member:
    __slots__ = ("ws", "room", "slot", "uid", "nick", "muted", "speaking",
                 "last_audio", "audio", "control", "wake", "loopback",
                 "rate_t", "rate_n", "rx", "dropped_rate", "dropped_slow",
                 "sender")

    def __init__(self, ws: ServerConnection, room: "Room", slot: int, uid: str,
                 nick: str, muted: bool, loopback: bool):
        self.ws, self.room, self.slot = ws, room, slot
        self.uid, self.nick, self.muted = uid, nick, muted
        self.loopback = loopback
        self.speaking = False
        self.last_audio = 0.0
        self.audio: collections.deque[bytes] = collections.deque(maxlen=SEND_QUEUE)
        self.control: collections.deque[str] = collections.deque()
        self.wake = asyncio.Event()
        self.rate_t = 0.0
        self.rate_n = 0
        self.rx = 0
        self.dropped_rate = 0
        self.dropped_slow = 0
        self.sender: asyncio.Task | None = None

    def push_audio(self, data: bytes) -> None:
        if len(self.audio) == SEND_QUEUE:   # медленный клиент: старый кадр выбрасываем
            self.dropped_slow += 1
            stats["dropped_slow"] += 1
        self.audio.append(data)
        self.wake.set()

    def push_control(self, text: str) -> None:
        self.control.append(text)
        self.wake.set()

    async def sender_loop(self) -> None:
        try:
            while True:
                await self.wake.wait()
                self.wake.clear()
                while self.control or self.audio:
                    if self.control:
                        await self.ws.send(self.control.popleft())
                    else:
                        await self.ws.send(self.audio.popleft())
                        stats["tx"] += 1
        except ConnectionClosed:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("sender %s/%s", self.room.party[:8], self.slot)


class Room:
    def __init__(self, party: str):
        self.party = party
        self.members: dict[int, Member] = {}

    def free_slot(self) -> int | None:
        for s in range(1, MAX_SLOT + 1):
            if s not in self.members:
                return s
        return None

    def roster_msg(self) -> str:
        return json.dumps({"type": "roster", "members": [
            {"slot": m.slot, "uid": m.uid, "nick": m.nick,
             "muted": m.muted, "speaking": m.speaking}
            for m in sorted(self.members.values(), key=lambda m: m.slot)
        ]}, ensure_ascii=False)

    def broadcast_roster(self) -> None:
        msg = self.roster_msg()
        for m in self.members.values():
            m.push_control(msg)


rooms: dict[str, Room] = {}
stats = collections.Counter()


def clean_nick(nick) -> str:
    s = "".join(ch for ch in str(nick or "") if ch.isprintable()).strip()
    return s[:32] or "Игрок"


def valid_party(p) -> bool:
    return isinstance(p, str) and 1 <= len(p) <= 64 and all(
        c.isalnum() or c in "-_" for c in p)


# ------------------------------------------------------------------ HTTP
def process_request(connection: ServerConnection, request):
    path = request.path.split("?", 1)[0]
    if path == "/health":
        return connection.respond(HTTPStatus.OK, "ok\n")
    if path != "/ws":
        return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
    return None


# ------------------------------------------------------------------ обработчик
async def reject(ws: ServerConnection, err: AuthError) -> None:
    try:
        await ws.send(json.dumps({"type": "error", "code": err.code,
                                  "message": err.message}))
        await ws.close(err.close_code, err.code)
    except ConnectionClosed:
        pass


async def handler(ws: ServerConnection) -> None:
    peer = ws.remote_address[0] if ws.remote_address else "?"
    fwd = ws.request.headers.get("X-Forwarded-For") if ws.request else None
    if fwd:
        peer = fwd.split(",")[0].strip()
    # ---- приветствие
    try:
        raw = await asyncio.wait_for(ws.recv(), HELLO_TIMEOUT)
        hello = json.loads(raw) if isinstance(raw, str) else None
        if not isinstance(hello, dict):
            raise ValueError
        token = str(hello.get("token") or "")
        party = hello.get("party")
        if not token or not valid_party(party):
            raise ValueError
    except (asyncio.TimeoutError, ValueError, json.JSONDecodeError):
        await reject(ws, AuthError("bad_hello", 4400, "bad hello"))
        return
    except ConnectionClosed:
        return
    try:
        uid = await authenticate(token, party)
    except AuthError as e:
        log.info("reject %s party=%s: %s", peer, party[:8], e.message)
        await reject(ws, e)
        return

    room = rooms.get(party)
    if room is None:
        room = rooms[party] = Room(party)
    # тот же пользователь переподключился — старое соединение выкидываем
    for old in [m for m in room.members.values() if m.uid == uid]:
        log.info("replace %s slot=%s in %s", uid[:8], old.slot, party[:8])
        room.members.pop(old.slot, None)
        asyncio.ensure_future(old.ws.close(4000, "replaced"))
    if len(room.members) >= MAX_PER_ROOM:
        if not room.members:
            rooms.pop(party, None)
        await reject(ws, AuthError("full", 4009, "room is full"))
        return
    slot = room.free_slot()
    loopback = bool(hello.get("loopback")) and ALLOW_LOOPBACK
    me = Member(ws, room, slot, uid, clean_nick(hello.get("nick")),
                bool(hello.get("muted")), loopback)
    room.members[slot] = me
    me.push_control(json.dumps({"type": "welcome", "slot": slot, "party": party,
                                "uid": uid, "loopback": loopback}))
    me.sender = asyncio.create_task(me.sender_loop())
    room.broadcast_roster()
    log.info("join %s uid=%s nick=%r slot=%d party=%s (%d in room)%s",
             peer, uid[:8], me.nick, slot, party[:8], len(room.members),
             " loopback" if loopback else "")

    # ---- основной цикл
    close_reason = "closed"
    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                close_reason = "idle"
                await ws.close(4408, "idle")
                break
            if room.members.get(slot) is not me:   # нас заменили
                close_reason = "replaced"
                break
            if isinstance(msg, bytes):
                handle_audio(me, msg)
            else:
                handle_text(me, msg)
    except ConnectionClosed as e:
        close_reason = f"closed {e.rcvd.code if e.rcvd else ''}".strip()
    finally:
        if me.sender:
            me.sender.cancel()
        if room.members.get(slot) is me:
            del room.members[slot]
            room.broadcast_roster()
        if not room.members and rooms.get(party) is room:
            del rooms[party]
        log.info("leave uid=%s slot=%d party=%s (%s; rx=%d drop_rate=%d drop_slow=%d)",
                 uid[:8], slot, party[:8], close_reason, me.rx,
                 me.dropped_rate, me.dropped_slow)


def handle_audio(me: Member, payload: bytes) -> None:
    n = len(payload)
    if n == 0 or n > MAX_FRAME:
        stats["dropped_size"] += 1
        return
    now = time.monotonic()
    # ограничение частоты: окно в 1 секунду
    if now - me.rate_t >= 1.0:
        me.rate_t, me.rate_n = now, 0
    me.rate_n += 1
    if me.rate_n > MAX_FPS:
        me.dropped_rate += 1
        stats["dropped_rate"] += 1
        return
    if me.muted:
        return
    me.rx += 1
    stats["rx"] += 1
    me.last_audio = now
    if not me.speaking:
        me.speaking = True
        me.room.broadcast_roster()
    out = bytes((me.slot,)) + payload
    if me.loopback:
        me.push_audio(out)
        return
    for m in me.room.members.values():
        if m is not me:
            m.push_audio(out)


def handle_text(me: Member, text: str) -> None:
    try:
        msg = json.loads(text)
    except json.JSONDecodeError:
        return
    if not isinstance(msg, dict):
        return
    kind = msg.get("type")
    if kind == "ping":
        me.push_control('{"type":"pong"}')
    elif kind == "mute":
        muted = bool(msg.get("muted"))
        changed = muted != me.muted or (muted and me.speaking)
        me.muted = muted
        if muted:
            me.speaking = False
        if changed:
            me.room.broadcast_roster()


async def speaking_watch() -> None:
    """Гасит флаг "говорит" у тех, от кого давно не было звука."""
    while True:
        await asyncio.sleep(0.1)
        now = time.monotonic()
        for room in list(rooms.values()):
            changed = False
            for m in room.members.values():
                if m.speaking and now - m.last_audio > SPEAK_HOLD:
                    m.speaking = False
                    changed = True
            if changed:
                room.broadcast_roster()


async def stats_log() -> None:
    while True:
        await asyncio.sleep(60)
        people = sum(len(r.members) for r in rooms.values())
        if people or stats:
            log.info("stats rooms=%d people=%d %s", len(rooms), people,
                     " ".join(f"{k}={v}" for k, v in sorted(stats.items())))
        stats.clear()


async def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("websockets").setLevel(logging.WARNING)
    if FAKE_AUTH:
        log.warning("VOICE_FAKE_AUTH=1: проверка токенов ОТКЛЮЧЕНА (только для тестов)")
    elif not (SUPABASE_URL and SUPABASE_ANON_KEY):
        log.error("не заданы SUPABASE_URL / SUPABASE_ANON_KEY — все входы будут отклонены")

    stop = asyncio.get_running_loop().create_future()
    if sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(
                sig, lambda: stop.done() or stop.set_result(None))

    bg = [asyncio.create_task(speaking_watch()), asyncio.create_task(stats_log())]
    async with serve(
        handler, HOST, PORT,
        process_request=process_request,
        compression=None,           # μ-law всё равно не сжимается
        max_size=8192,              # приветствие с JWT + кадры до 2000 байт
        max_queue=64,
        ping_interval=15, ping_timeout=20,
        open_timeout=10, close_timeout=5,
        write_limit=32768,
    ) as server:
        log.info("voice relay on ws://%s:%d/ws (health: /health)", HOST, PORT)
        try:
            await stop
        finally:
            log.info("shutting down")
            server.close(close_connections=True)
            for t in bg:
                t.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

"""Голосовой чат пати для Portalis (клиент).

Только фоновые потоки, без asyncio. Зависимости: sounddevice, websocket-client.

    vc = VoiceClient("wss://voice.arch-flow.ru/ws", token, party_id, nick,
                     on_event=lambda kind, data: root.after(0, handle, kind, data))
    vc.start()
    vc.set_mic(False)        # выключить микрофон
    vc.set_ptt(True); vc.talking(True)   # рация: говорим, пока зажата клавиша
    vc.stop()

on_event(kind, data) вызывается из фоновых потоков:
    "connected"  {"slot": n, "uid": ..., "loopback": bool}
    "roster"     [ {"slot","uid","nick","muted","speaking"}, ... ]
    "speaking"   sorted list слотов, которые сейчас звучат (свой слот — когда шлём)
    "error"      "текст ошибки по-русски"
    "closed"     None (окончательно, после stop() или исчерпания попыток)

Аудио: 16 кГц моно int16, кадры по 20 мс (320 сэмплов), G.711 μ-law (320 байт).
"""

from __future__ import annotations

import collections
import json
import math
import queue
import threading
import time
from array import array

try:
    import websocket  # websocket-client
except ImportError:  # pragma: no cover
    websocket = None

RATE = 16000
FRAME = 320                 # сэмплов в кадре (20 мс)
FRAME_BYTES = FRAME * 2     # int16
FRAME_SEC = FRAME / RATE

JB_START = 3                # кадров в буфере до начала воспроизведения
JB_CAP = 10                 # максимум кадров в буфере на собеседника

VAD_THRESHOLD = 0.012       # RMS (0..1), выше — считаем речью
VAD_HANGOVER = 15           # кадров (300 мс) продолжаем слать после речи
VAD_PREROLL = 2             # кадров до начала речи, чтобы не съедать первый слог

MAX_RETRIES = 5
SPEAK_HOLD = 0.25            # сек: сколько после последнего кадра считаем, что человек говорит
PING_EVERY = 15.0

ERR_NO_MIC = "Нет микрофона"
ERR_NO_OUT = "Нет устройства для вывода звука"
ERR_SERVER = "Сервер голоса недоступен"
ERR_NOT_MEMBER = "Ты не в этой пати"
ERR_AUTH = "Не удалось войти в голосовой чат — перезайди в аккаунт"
ERR_FULL = "В голосовом чате уже 12 человек"
ERR_LIB = "Не установлен модуль websocket-client"
ERR_MIC_SILENT = ("Микрофон молчит — проверь, что он подключён, не выключен "
                  "и разрешён в «Параметры → Конфиденциальность → Микрофон»")
MIC_SILENT_RMS = 0.0003     # ниже этого 5 секунд подряд — микрофон, похоже, мёртв


# ===================================================================== μ-law
def _ulaw_encode_sample(s: int) -> int:
    BIAS, CLIP = 0x84, 32635
    sign = 0x80 if s < 0 else 0
    if s < 0:
        s = -s
    if s > CLIP:
        s = CLIP
    s += BIAS
    exp = 7
    mask = 0x4000
    while exp > 0 and not (s & mask):
        exp -= 1
        mask >>= 1
    mant = (s >> (exp + 3)) & 0x0F
    return ~(sign | (exp << 4) | mant) & 0xFF


def _ulaw_decode_byte(u: int) -> int:
    u = ~u & 0xFF
    sign = u & 0x80
    exp = (u >> 4) & 0x07
    mant = u & 0x0F
    s = (((mant << 3) + 0x84) << exp) - 0x84
    return -s if sign else s


# Таблица кодирования индексируется беззнаковым 16-битным значением сэмпла.
_ENC = bytes(_ulaw_encode_sample(u - 65536 if u >= 32768 else u) for u in range(65536))
_DEC = tuple(_ulaw_decode_byte(b) for b in range(256))


def ulaw_encode(pcm: bytes) -> bytes:
    """int16 LE PCM -> μ-law (байт на сэмпл)."""
    return bytes(map(_ENC.__getitem__, array("H", pcm)))


def ulaw_decode(data: bytes) -> array:
    """μ-law -> array('h')."""
    return array("h", map(_DEC.__getitem__, data))


def rms(samples: array) -> float:
    """RMS кадра int16 в долях полной шкалы (0..1)."""
    n = len(samples)
    if not n:
        return 0.0
    return math.sqrt(math.sumprod(samples, samples) / n) / 32768.0


# ===================================================================== буфер
class JitterBuffer:
    """Буфер на одного собеседника: копит start кадров, держит не больше cap."""

    def __init__(self, start: int = JB_START, cap: int = JB_CAP):
        self.start, self.cap = start, cap
        self.q: collections.deque = collections.deque()
        self.playing = False
        self.dropped = 0
        self.underruns = 0
        self.received = 0
        self.played = 0

    def push(self, frame) -> None:
        self.received += 1
        self.q.append(frame)
        while len(self.q) > self.cap:   # переполнение — выкидываем самые старые
            self.q.popleft()
            self.dropped += 1

    def pop(self):
        if not self.playing:
            if len(self.q) < self.start:
                return None
            self.playing = True
        if self.q:
            self.played += 1
            return self.q.popleft()
        self.playing = False            # кончились кадры — снова копим
        self.underruns += 1
        return None


# ===================================================================== устройства
def list_devices() -> dict:
    """{"input": [(index, name, hostapi)], "output": [...], "default": (in, out)}"""
    import sounddevice as sd
    apis = [a["name"] for a in sd.query_hostapis()]
    res = {"input": [], "output": [], "default": tuple(sd.default.device)}
    for i, d in enumerate(sd.query_devices()):
        api = apis[d["hostapi"]] if d["hostapi"] < len(apis) else "?"
        if d["max_input_channels"] > 0:
            res["input"].append((i, d["name"], api))
        if d["max_output_channels"] > 0:
            res["output"].append((i, d["name"], api))
    return res


# ===================================================================== клиент
class VoiceClient:
    def __init__(self, url, token, party, nick, on_event=None, *,
                 source=None, sink=None, input_device=None, output_device=None,
                 loopback=False, vad_threshold=VAD_THRESHOLD,
                 max_retries=MAX_RETRIES):
        """
        token     — строка или функция без аргументов, возвращающая свежий токен
                    (вызывается перед каждым подключением).
        source    — для тестов: итерируемое из кадров PCM int16 по 640 байт
                    (вместо микрофона, отдаются в темпе 20 мс).
        sink      — для тестов: sink(pcm_bytes) получает смикшированный звук
                    по 640 байт каждые 20 мс (вместо динамиков).
        loopback  — сервер возвращает твой же звук (проверка микрофона).
        """
        self.url, self._token, self.party, self.nick = url, token, party, nick
        self.on_event = on_event
        self.source, self.sink = source, sink
        self.input_device, self.output_device = input_device, output_device
        self.loopback = loopback
        self.vad_threshold = vad_threshold
        self.max_retries = max_retries

        self.roster: list = []
        self.speaking: dict = {}        # slot -> time.monotonic() последнего кадра
        self.level = 0.0                # RMS микрофона 0..1
        self.error: str | None = None
        self.my_slot: int | None = None
        self.connected = False
        self.mic_silent = False         # True, если первые 5 с с микрофона шла цифровая тишина

        self.mic_on = True
        self.volume = 1.0
        self.ptt = False
        self._talking = False
        self._sending = False           # шлём ли сейчас (VAD/рация)

        self._ws = None
        self._ws_lock = threading.Lock()
        self._stop = threading.Event()
        self._mic_q: queue.Queue = queue.Queue(maxsize=50)
        self._jb: dict[int, JitterBuffer] = {}
        self._jb_lock = threading.Lock()
        self._mix_carry = bytearray()
        self._threads: list[threading.Thread] = []
        self._in_stream = None
        self._out_stream = None
        self._speaking_state: list = []
        self._mic_carry = b""
        self._had_session = False
        self.stats = collections.Counter()

    # ------------------------------------------------------------ публичное
    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        self._open_audio()
        self._spawn(self._net_loop, "voice-net")
        self._spawn(self._send_loop, "voice-send")
        self._spawn(self._tick_loop, "voice-tick")
        if self.source is not None:
            self._spawn(self._source_loop, "voice-source")
        if self.sink is not None:
            self._spawn(self._sink_loop, "voice-sink")

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        self._close_ws()
        for s in (self._in_stream, self._out_stream):
            if s is not None:
                try:
                    s.stop()
                    s.close()
                except Exception:
                    pass
        self._in_stream = self._out_stream = None
        me = threading.current_thread()
        for t in self._threads:
            if t is not me:
                t.join(timeout=3)
        self._threads = []

    def set_mic(self, on: bool) -> None:
        self.mic_on = bool(on)
        if not on:
            self._sending = False
            self.level = 0.0
        self._send_text({"type": "mute", "muted": not self.mic_on})

    def set_volume(self, v: float) -> None:
        self.volume = max(0.0, min(2.0, float(v)))

    def set_ptt(self, on: bool) -> None:
        self.ptt = bool(on)
        if not on:
            self._talking = False

    def talking(self, held: bool) -> None:
        """Рация: True — клавиша зажата, False — отпущена."""
        self._talking = bool(held)

    # ------------------------------------------------------------ события
    def _emit(self, kind, data=None) -> None:
        if kind == "error":
            self.error = data
        cb = self.on_event
        if cb:
            try:
                cb(kind, data)
            except Exception:
                pass

    def _spawn(self, fn, name) -> None:
        t = threading.Thread(target=fn, name=name, daemon=True)
        self._threads.append(t)
        t.start()

    # ------------------------------------------------------------ звук
    def _open_audio(self) -> None:
        need_in = self.source is None
        need_out = self.sink is None
        if not (need_in or need_out):
            return
        try:
            import sounddevice as sd
        except Exception:
            if need_in:
                self._emit("error", ERR_NO_MIC)
            return
        if need_in:
            try:
                self._in_stream = sd.RawInputStream(
                    samplerate=RATE, blocksize=FRAME, channels=1, dtype="int16",
                    device=self.input_device, latency="low",
                    callback=self._in_cb)
                self._in_stream.start()
            except Exception:
                self._in_stream = None
                self._emit("error", ERR_NO_MIC)
        if need_out:
            try:
                self._out_stream = sd.RawOutputStream(
                    samplerate=RATE, blocksize=FRAME, channels=1, dtype="int16",
                    device=self.output_device, latency="low",
                    callback=self._out_cb)
                self._out_stream.start()
            except Exception:
                self._out_stream = None
                self._emit("error", ERR_NO_OUT)

    def _in_cb(self, indata, frames, time_info, status) -> None:
        data = bytes(indata)
        # блоки бывают не ровно по 320 — режем на кадры
        buf = self._mic_carry + data
        while len(buf) >= FRAME_BYTES:
            self._push_mic(buf[:FRAME_BYTES])
            buf = buf[FRAME_BYTES:]
        self._mic_carry = buf

    def _push_mic(self, frame: bytes) -> None:
        try:
            self._mic_q.put_nowait(frame)
        except queue.Full:   # сеть залипла — выкидываем самый старый
            try:
                self._mic_q.get_nowait()
            except queue.Empty:
                pass
            self.stats["mic_drop"] += 1
            try:
                self._mic_q.put_nowait(frame)
            except queue.Full:
                pass

    def _out_cb(self, outdata, frames, time_info, status) -> None:
        try:
            outdata[:] = self._pull(frames * 2)
        except Exception:
            outdata[:] = b"\x00" * len(outdata)

    def _pull(self, nbytes: int) -> bytes:
        buf = self._mix_carry
        while len(buf) < nbytes:
            buf += self._mix_frame()
        out = bytes(buf[:nbytes])
        del buf[:nbytes]
        return out

    def _mix_frame(self) -> bytes:
        """Один кадр 20 мс: по кадру из буфера каждого собеседника, сложить."""
        frames = []
        with self._jb_lock:
            for jb in self._jb.values():
                f = jb.pop()
                if f is not None:
                    frames.append(f)
        vol = self.volume
        if not frames or vol <= 0.0:
            return bytes(FRAME_BYTES)
        if len(frames) == 1:
            mixed = frames[0]
            if vol == 1.0:
                return mixed.tobytes()
            mixed = [int(x * vol) for x in mixed]
        else:
            mixed = map(sum, zip(*frames))
            if vol != 1.0:
                mixed = [int(x * vol) for x in mixed]
        out = array("h", [32767 if x > 32767 else -32768 if x < -32768 else x
                          for x in mixed])
        return out.tobytes()

    # ------------------------------------------------------------ тестовые источники
    def _source_loop(self) -> None:
        nxt = time.monotonic()
        for frame in self.source:
            if self._stop.is_set():
                return
            if frame is None:
                continue
            self._push_mic(bytes(frame))
            nxt += FRAME_SEC
            d = nxt - time.monotonic()
            if d > 0:
                time.sleep(d)
            elif d < -0.2:
                nxt = time.monotonic()

    def _sink_loop(self) -> None:
        nxt = time.monotonic()
        while not self._stop.is_set():
            self.sink(self._pull(FRAME_BYTES))
            nxt += FRAME_SEC
            d = nxt - time.monotonic()
            if d > 0:
                time.sleep(d)
            elif d < -0.2:
                nxt = time.monotonic()

    # ------------------------------------------------------------ отправка
    def _send_loop(self) -> None:
        hang = 0
        preroll: collections.deque = collections.deque(maxlen=VAD_PREROLL)
        probe_n, probe_max = 0, 0.0     # проверка «живой ли микрофон» на первых 5 с
        while not self._stop.is_set():
            try:
                frame = self._mic_q.get(timeout=0.2)
            except queue.Empty:
                continue
            samples = array("h", frame)
            lvl = rms(samples)
            if not self.mic_on:
                self.level = 0.0
                self._sending = False
                preroll.clear()
                continue
            self.level = lvl
            if self.source is None and probe_n < 250:
                probe_n += 1
                probe_max = max(probe_max, lvl)
                if probe_n == 250 and probe_max < MIC_SILENT_RMS:
                    self.mic_silent = True
                    self._emit("error", ERR_MIC_SILENT)
            if self.ptt:
                send = self._talking
                hang = 0
                preroll.clear()
                backlog = ()
            else:
                backlog = ()
                if lvl >= self.vad_threshold:
                    if hang == 0:
                        backlog = tuple(preroll)
                        preroll.clear()
                    hang = VAD_HANGOVER
                    send = True
                elif hang > 0:
                    hang -= 1
                    send = True
                else:
                    send = False
                if not send:
                    preroll.append(frame)
            self._sending = send
            if not send:
                self.stats["vad_skip"] += 1
                continue
            for f in (*backlog, frame):
                if self._send_bin(ulaw_encode(f)):
                    self.stats["sent"] += 1

    def _send_bin(self, data: bytes) -> bool:
        ws = self._ws
        if ws is None or not self.connected:
            return False
        try:
            ws.send_bytes(data)
            return True
        except Exception:
            return False

    def _send_text(self, obj) -> bool:
        ws = self._ws
        if ws is None or not self.connected:
            return False
        try:
            ws.send(json.dumps(obj))
            return True
        except Exception:
            return False

    # ------------------------------------------------------------ сеть
    def _token_now(self) -> str:
        t = self._token
        return t() if callable(t) else t

    def set_token(self, token) -> None:
        """Обновить токен (пригодится при следующем переподключении)."""
        self._token = token

    def _close_ws(self) -> None:
        with self._ws_lock:
            ws, self._ws = self._ws, None
        self.connected = False
        if ws is not None:
            try:
                ws.close(timeout=1)
            except Exception:
                pass

    def _net_loop(self) -> None:
        if websocket is None:
            self._emit("error", ERR_LIB)
            self._emit("closed")
            return
        fails = 0
        while not self._stop.is_set():
            fatal = self._session()
            if self._stop.is_set():
                break
            if fatal:
                self._emit("error", fatal)
                break
            if self._had_session:
                fails = 0
            fails += 1
            if fails > self.max_retries:
                self._emit("error", ERR_SERVER)
                break
            delay = min(8.0, 0.5 * 2 ** (fails - 1))
            if self._stop.wait(delay):
                break
        self._close_ws()
        self.connected = False
        self._emit("closed")

    def _session(self):
        """Одно подключение. Возвращает текст фатальной ошибки или None."""
        self._had_session = False
        try:
            token = self._token_now()
            ws = websocket.create_connection(
                self.url, timeout=8, enable_multithread=True,
                skip_utf8_validation=True)
        except Exception:
            return None
        with self._ws_lock:
            if self._stop.is_set():
                ws.close()
                return None
            self._ws = ws
        try:
            ws.send(json.dumps({
                "token": token, "party": self.party, "nick": self.nick,
                "muted": not self.mic_on, "loopback": self.loopback,
            }))
            ws.settimeout(1.0)
            last_ping = time.monotonic()
            while not self._stop.is_set():
                try:
                    op, data = ws.recv_data(control_frame=False)
                except websocket.WebSocketTimeoutException:
                    if self.connected and time.monotonic() - last_ping > PING_EVERY:
                        last_ping = time.monotonic()
                        self._send_text({"type": "ping"})
                    continue
                if op == websocket.ABNF.OPCODE_BINARY:
                    self._on_audio(data)
                elif op == websocket.ABNF.OPCODE_TEXT:
                    fatal = self._on_text(data.decode("utf-8", "replace"))
                    if fatal:
                        return fatal
                elif op == websocket.ABNF.OPCODE_CLOSE:
                    return self._close_code_error(data)
                if self.connected and time.monotonic() - last_ping > PING_EVERY:
                    last_ping = time.monotonic()
                    self._send_text({"type": "ping"})
        except websocket.WebSocketConnectionClosedException:
            return None
        except Exception:
            return None
        finally:
            was = self.connected
            self._close_ws()
            with self._jb_lock:
                self._jb.clear()
            self.speaking.clear()
            if was and not self._stop.is_set():
                self.roster = []
                self._emit("roster", [])
        return None

    def _close_code_error(self, data: bytes):
        code = int.from_bytes(data[:2], "big") if len(data) >= 2 else 0
        return {4001: ERR_AUTH, 4003: ERR_NOT_MEMBER, 4009: ERR_FULL}.get(code)

    def _on_text(self, text: str):
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            return None
        kind = msg.get("type")
        if kind == "roster":
            self.roster = msg.get("members") or []
            present = {m.get("slot") for m in self.roster}
            with self._jb_lock:
                for s in [s for s in self._jb if s not in present]:
                    del self._jb[s]
            for s in [s for s in self.speaking if s not in present]:
                self.speaking.pop(s, None)
            self._emit("roster", list(self.roster))
        elif kind == "welcome":
            self.my_slot = msg.get("slot")
            self.connected = True
            self._had_session = True
            self.error = None
            self._emit("connected", {"slot": self.my_slot, "uid": msg.get("uid"),
                                     "loopback": bool(msg.get("loopback"))})
        elif kind == "error":
            return {"auth": ERR_AUTH, "not_member": ERR_NOT_MEMBER,
                    "full": ERR_FULL}.get(msg.get("code"))
        return None

    def _on_audio(self, data: bytes) -> None:
        if len(data) < 2:
            return
        slot = data[0]
        pcm = ulaw_decode(data[1:])
        self.stats["recv"] += 1
        self.stats[f"recv_{slot}"] += 1
        self.speaking[slot] = time.monotonic()
        with self._jb_lock:
            jb = self._jb.get(slot)
            if jb is None:
                jb = self._jb[slot] = JitterBuffer()
            jb.push(pcm)

    # ------------------------------------------------------------ "говорит"
    def _tick_loop(self) -> None:
        while not self._stop.wait(0.1):
            now = time.monotonic()
            cur = sorted(s for s, t in list(self.speaking.items()) if now - t < SPEAK_HOLD)
            if self._sending and self.connected and self.my_slot is not None \
                    and not self.loopback and self.my_slot not in cur:
                cur = sorted(cur + [self.my_slot])
            if cur != self._speaking_state:
                self._speaking_state = cur
                self._emit("speaking", cur)

    # ------------------------------------------------------------ служебное
    def jitter_stats(self) -> dict:
        with self._jb_lock:
            return {s: {"received": jb.received, "played": jb.played,
                        "dropped": jb.dropped, "underruns": jb.underruns,
                        "queued": len(jb.q)} for s, jb in self._jb.items()}

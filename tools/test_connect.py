"""Подключение к игре друга без игры: поддельный «открытый для сети мир» (отвечает на запрос статуса, как
Minecraft) на всех сетях этого компьютера. Проверяет код приглашения с запасными адресами, выбор живого адреса,
старый код с одним адресом и подсказки, когда не отвечает никто. Папку игры подменяет на временную.
    python tools/test_connect.py"""
import importlib.machinery, importlib.util, json, os, socket, struct, sys, tempfile, threading, base64
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
tmp = tempfile.mkdtemp(prefix='portalis-connect-')
ps.set_game_dir(os.path.join(tmp, '.minecraft'))
os.makedirs(ps.MC)
fails = 0


def check(cond, text):
    global fails
    fails += not cond
    print(('OK   ' if cond else 'ОШИБКА ') + text, flush=True)


def vi(n):
    out = b''
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def fake_world(port):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('0.0.0.0', port))
    srv.listen(8)

    def handle(c):
        try:
            c.settimeout(5)
            c.recv(1024)
            body = json.dumps({'version': {'name': '1.20.1', 'protocol': 763}, 'players': {'max': 8, 'online': 1},
                               'description': {'text': 'Мир Тестера'}}).encode()
            pkt = vi(0) + vi(len(body)) + body
            c.sendall(vi(len(pkt)) + pkt)
            data = c.recv(1024)  # ping -> pong
            if data:
                c.sendall(data)
        except Exception:
            pass
        finally:
            c.close()

    def loop():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=handle, args=(c,), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()
    return srv


port = 51777
srv = fake_world(port)
ps.lan_port = lambda: port
nets = ps.vpn_addresses()
lan = ps.lan_ip()
print('сети этого компьютера:', nets, '| Wi-Fi:', lan)
h = ps.host_setup()
check(h['addresses'] and all(a.endswith(':%d' % port) for _k, a in h['addresses']),
      'хост: код с адресами %s' % ', '.join('%s %s' % tuple(x) for x in h['addresses']))
check(len(h['addresses']) == len(nets) + (1 if lan else 0), 'в коде все сети компьютера')
cands, tl, pack = ps.parse_invite_full(h['code'])
check(cands == h['addresses'], 'код читается обратно со всеми адресами')
check(ps.parse_invite(h['code'])[0] == h['address'], 'старые программы берут первый адрес из того же кода')
logs = []
addr, alive = ps.pick_address(cands, logs.append)
check(alive and addr in [a for _k, a in cands], 'гость выбрал живой адрес: %s' % addr)
# первый адрес мёртвый, второй живой - выбирается живой
dead = [['Hamachi', '25.0.0.1:%d' % port]] + [c for c in cands if c[0] != 'Hamachi']
logs = []
addr, alive = ps.pick_address(dead, logs.append, timeout=2)
check(alive and not addr.startswith('25.0.0.1'), 'мёртвый первый адрес пропущен, выбран %s' % addr)
# старый код с одним адресом
old = 'MC1-' + base64.urlsafe_b64encode(json.dumps({'a': '127.0.0.1:%d' % port, 't': None, 'p': None}).encode()).decode().rstrip('=')
c_old, _t, _p = ps.parse_invite_full(old)
check(c_old == [['?', '127.0.0.1:%d' % port]] and ps.pick_address(c_old, lambda m: None)[1], 'старый код с одним адресом работает')
# никто не отвечает: подсказки
srv.close()
logs = []
addr, alive = ps.pick_address([['Radmin VPN', '26.0.0.9:%d' % port], ['ZeroTier', '10.147.0.9:%d' % port]], logs.append, timeout=2)
print('   ' + '\n   '.join(logs))
check(not alive and any('Подсказка' in l for l in logs), 'никто не отвечает: есть подсказка, что делать')
# join_friend целиком: сервер снова поднят, сервер друга первой строкой в списке игры
srv = fake_world(port)
logs = []
ps.join_friend(h['code'], packs=[], log=logs.append)
data, _n, root = ps._read_servers_dat()
first = [s for s in root[1] if s[0] == 'servers'][0][2][2][0][1]
ip = [v for k, _t, v in first if k == 'ip'][0]
check(ip in [a for _k, a in cands] and any('Подключаю через' in l for l in logs), 'join_friend: в «Сетевой игре» первым стоит %s' % ip)
srv.close()
print('Итог: ошибок', fails)
sys.exit(1 if fails else 0)

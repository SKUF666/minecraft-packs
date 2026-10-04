"""
Генерация листа спрайтов через gpt-image (та же модель, что рисует в ChatGPT) на шлюзе cheapai.io.
Если указан --ref, картинка-образец уходит в /images/edits: так новые листы держат стиль первых,
нарисованных в самом ChatGPT.

  python tools/gen_art.py toys "<промпт>" --ref art-src/raw/coffee.png
  python tools/gen_art.py room_hall "<промпт>" --opaque --size 1536x1024

Ключ берётся из ../parfum-engine/.env (строка с sk-...). Результат — art-src/raw/<имя>.png.
"""
import argparse
import base64
import json
import re
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / 'art-src'
API = 'https://cheapai.io/v1'


def key() -> str:
    env = (ROOT.parent / 'parfum-engine' / '.env').read_text(encoding='utf-8')
    m = re.search(r'sk-[A-Za-z0-9_-]+', env)
    if not m:
        raise SystemExit('Не найден ключ cheapai в parfum-engine/.env')
    return m.group(0)


def multipart(fields: dict, files: list) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    for name, path in files:
        out += f'--{b}\r\nContent-Disposition: form-data; name="{name}"; filename="{Path(path).name}"\r\nContent-Type: image/png\r\n\r\n'.encode()
        out += Path(path).read_bytes() + b'\r\n'
    out += f'--{b}--\r\n'.encode()
    return bytes(out), f'multipart/form-data; boundary={b}'


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('name')
    ap.add_argument('prompt')
    ap.add_argument('--ref', action='append', default=[])
    ap.add_argument('--size', default='1536x1024')
    ap.add_argument('--opaque', action='store_true')
    ap.add_argument('--model', default='gpt-image-2.5')
    args = ap.parse_args()

    fields = {
        'model': args.model,
        'prompt': args.prompt,
        'size': args.size,
        'quality': 'high',
        'n': 1,
        'background': 'opaque' if args.opaque else 'transparent',
    }
    headers = {'Authorization': f'Bearer {key()}'}
    if args.ref:
        body, ctype = multipart(fields, [('image[]' if len(args.ref) > 1 else 'image', r) for r in args.ref])
        url = f'{API}/images/edits'
    else:
        body, ctype = json.dumps(fields).encode(), 'application/json'
        url = f'{API}/images/generations'
    headers['Content-Type'] = ctype
    t0 = time.time()
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method='POST')
            with urllib.request.urlopen(req, timeout=400) as r:
                d = json.loads(r.read().decode('utf-8'))
            break
        except Exception as e:  # шлюз иногда отвечает 5xx — повторяем
            msg = e.read().decode('utf-8', 'ignore')[:300] if hasattr(e, 'read') else str(e)
            print(f'попытка {attempt + 1}: {msg}')
            if attempt == 2:
                raise SystemExit(1)
            time.sleep(5)
    b64 = d['data'][0].get('b64_json')
    if not b64:
        raise SystemExit(f'нет картинки в ответе: {json.dumps(d)[:300]}')
    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / f'{args.name}.png'
    out.write_bytes(base64.b64decode(b64))
    print(f'{out.name}: {time.time() - t0:.0f} с')


if __name__ == '__main__':
    main()

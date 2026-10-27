"""Официальные записи Portalis в ленте программы. Лента читает news.json из корня репозитория на GitHub, поэтому
запись появляется у всех без выпуска новой версии (через 5-15 минут).

    python tools/news.py --title "Заголовок" --text "Текст" [--line "пункт"]... [--image картинка.png] [--link URL]
                         [--kind news|update] [--dry]

Обновления программы сюда добавляет выпуск (release.py) сам - из строк «Что нового». Снапшоты и релизы Minecraft
программа берёт прямо из ленты Mojang, их добавлять не нужно."""
import os, sys, json, time, subprocess, argparse
sys.stdout.reconfigure(encoding='utf-8')
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWS = os.path.join(REPO, 'news.json')
KEEP = 40  # столько последних записей хранится
SKIP = ('Обновлена программа', 'Мелкие исправления')  # в записи об обновлении - только то, что видно игроку


def load():
    try:
        with open(NEWS, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'posts': []}


def git(*args, check=True):
    r = subprocess.run(('git',) + args, cwd=REPO, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if check and r.returncode:
        raise SystemExit('git %s: %s%s' % (' '.join(args), r.stdout, r.stderr))
    return r


def save(d, msg, extra=(), push=True):
    d['posts'] = sorted(d['posts'], key=lambda p: p['at'], reverse=True)[:KEEP]
    with open(NEWS, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
        f.write('\n')
    if not push:
        return
    git('add', 'news.json', *extra)
    if git('diff', '--cached', '--quiet', check=False).returncode:
        git('commit', '-q', '-m', msg + '\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')
    for attempt in range(3):  # пуш иногда срывается по TLS - повторяем
        if git('push', '-q', 'origin', 'main', check=False).returncode == 0:
            return
        time.sleep(4 * (attempt + 1))
    raise SystemExit('не получилось отправить news.json на GitHub - повтори: git push origin main')


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def add_post(title, text='', lines=(), image=None, link=None, kind='news', push=True):
    d = load()
    pid = 'n%d' % int(time.time())
    post = {'id': pid, 'kind': kind, 'at': now(), 'title': title}
    if text:
        post['text'] = text
    if lines:
        post['lines'] = list(lines)
    if link:
        post['link'] = link
    extra = ()
    if image:
        from PIL import Image
        os.makedirs(os.path.join(REPO, 'news'), exist_ok=True)
        rel = 'news/%s.png' % pid
        im = Image.open(image)
        im.thumbnail((1200, 1200))
        im.save(os.path.join(REPO, rel), optimize=True)
        post['image'] = rel
        extra = (rel,)
    d['posts'].append(post)
    save(d, 'Новость в ленте: ' + title, extra, push)
    return post


def add_update(version, lines, push=True):
    """Запись «Обновление Portalis» после выпуска; без видимых игроку изменений - не пишем."""
    lines = [x for x in lines if x and x not in SKIP]
    if not lines:
        return None
    d = load()
    d['posts'] = [p for p in d['posts'] if p.get('id') != version]
    post = {'id': version, 'kind': 'update', 'version': version, 'at': now(), 'title': 'Обновление %s' % version,
            'lines': lines[:12]}
    d['posts'].append(post)
    save(d, 'Новость об обновлении %s' % version, push=push)
    return post


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--title', required=True)
    ap.add_argument('--text', default='')
    ap.add_argument('--line', action='append', default=[])
    ap.add_argument('--image')
    ap.add_argument('--link')
    ap.add_argument('--kind', default='news', choices=('news', 'update'))
    ap.add_argument('--dry', action='store_true', help='только записать news.json, без отправки на GitHub')
    a = ap.parse_args()
    if a.image and not os.path.isfile(a.image):
        raise SystemExit('нет картинки: %s' % a.image)
    p = add_post(a.title, a.text, a.line, a.image, a.link, a.kind, push=not a.dry)
    print('записано:', json.dumps(p, ensure_ascii=False))


if __name__ == '__main__':
    main()

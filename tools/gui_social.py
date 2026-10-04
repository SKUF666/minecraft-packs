"""Панель аккаунта на вкладке «С другом»: форма входа, затем вход тестовым игроком с другом, пати и чатом.
Создаёт на сервере тестовых игроков zt_<...>_g / _h. python tools/gui_social.py"""
import os, sys, random, string, importlib.machinery, importlib.util, tkinter as tk, traceback
from PIL import ImageGrab
sys.stdout.reconfigure(encoding='utf-8')
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
store = {}
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))
tag = 'zt_' + ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
pw = 'Portalis-gui-' + ''.join(random.choice(string.ascii_letters) for _ in range(8))
# друг Б заранее: свои настройки
other = {}
def as_other(fn):
    ps.load_settings, ps.save_settings = (lambda: dict(other)), (lambda d: (other.clear(), other.update(d)))
    try:
        return fn()
    finally:
        ps.load_settings, ps.save_settings = (lambda: dict(store)), (lambda d: (store.clear(), store.update(d)))
h = as_other(lambda: ps.Social())
as_other(lambda: h.sign_up(tag + '_h', pw, 'Друг Б'))
orig = tk.Tk.mainloop
errors = []


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


def grab(w, name):
    w.attributes('-topmost', True)
    w.update()
    x, y = w.winfo_rootx(), w.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x + w.winfo_width(), y + w.winfo_height())).save(os.path.join(OUT, name + '.png'))


def patched(self, *a):
    self.geometry('1080x760+30+30')
    hk = self._hooks
    steps = []

    def at(ms, fn):
        steps.append((ms, fn))

    def entries():
        return [e for e in walk(self) if isinstance(e, tk.Entry) and e.winfo_ismapped()]

    def click(text, exact=False):
        for x in walk(self):
            t_ = str(x.cget('text')).strip() if isinstance(x, tk.Label) else None
            if t_ is not None and (t_ == text if exact else t_.startswith(text)):
                x.event_generate('<Button-1>'); return True
        return False
    at(1500, lambda: hk['show']('friend'))
    at(1500, lambda: grab(self, 'soc_login'))
    at(300, lambda: print('регистрация:', click('Регистрация')))

    def fill():
        es = entries()
        vals = [tag + '_g', 'Тестер Г', pw]
        for e, v in zip(es[:3], vals):
            e.delete(0, 'end'); e.insert(0, v)
        print('создать:', click('Создать аккаунт'))
    at(800, fill)

    def befriend():
        g = ps.Social()
        print('вошёл:', g.logged_in())
        as_other(lambda: h.add_friend(tag + '_g'))
        g.add_friend(tag + '_h')
        pt = g.create_party('Вечерний OneBlock')
        g.invite(pt['id'], h.uid)
        as_other(lambda: (h.join(pt['id']), h.set_status('playing', 'Удобства и шейдеры'),
                          h.send('Заходи, я уже в OneBlock!', pid=pt['id'])))
        g.send('Иду, включаю сборку', pid=pt['id'])
        as_other(lambda: h.share_game(pt['id'], 'MC1-test', {'host': h.uid, 'pack': 'Удобства и шейдеры (21)'}))
        print('обновить:', click('Обновить'))
    at(6000, befriend)
    at(18000, lambda: grab(self, 'soc_main'))
    at(300, lambda: hk['body'].yview_moveto(0.22))
    at(800, lambda: (grab(self, 'soc_chat'), print('чат:', [t.get('1.0', 'end').strip() for t in walk(self) if isinstance(t, tk.Text)])))

    def profiles():
        g = ps.Social()
        g.update_profile(avatar='preset:1', mood='Ищу команду на вечер', about='Играю в OneBlock и RPG Pack.',
                         favorites=['OneBlock', 'RPG Pack', 'Business Battle'], banner='skins', color='#3d9be9',
                         skin='Notch', stats={'minutes': 420, 'maps': ['OneBlock', 'Business Battle', 'PILLARS 2'],
                                              'maps_n': 6, 'packs': 1, 'friends': 1, 'parties': 1, 'night': True})
        mine, _ = g.parties()
        if mine:
            g.set_lobby(mine[0]['id'], {'map': 'oneblock', 'title': 'OneBlock', 'version': '26.1.2',
                                        'pack': 'Удобства и шейдеры (21)'})
            as_other(lambda: h.set_ready(mine[0]['id'], True))
        as_other(lambda: (h.update_profile(avatar='preset:11', presence='dnd', mood='Строю замок',
                                            favorites=['SkyBlock']), h.post_wall(h.uid, 'Мой замок почти готов!')))
        as_other(lambda: h.post_wall(g.uid, 'Привет! Сыграем сегодня?'))
        print('обновить2:', click('Обновить'))
    at(500, profiles)
    at(5000, lambda: (grab(self, 'prof_head'), print('профиль:', click(self_name()))))
    at(5000, lambda: grab([w for w in self.winfo_children() if isinstance(w, tk.Toplevel)][-1], 'prof_mine'))
    at(300, lambda: [w for w in self.winfo_children() if isinstance(w, tk.Toplevel)][-1]._cv.yview_moveto(0.3))
    at(1500, lambda: grab([w for w in self.winfo_children() if isinstance(w, tk.Toplevel)][-1], 'prof_mine2'))
    at(300, lambda: [w.destroy() for w in self.winfo_children() if isinstance(w, tk.Toplevel)])
    at(500, lambda: print('друг:', click('Друг Б', True)))
    at(4000, lambda: grab([w for w in self.winfo_children() if isinstance(w, tk.Toplevel)][-1], 'prof_friend'))
    at(300, lambda: [w.destroy() for w in self.winfo_children() if isinstance(w, tk.Toplevel)])

    def self_name():
        return 'Тестер Г'

    def cleanup():
        g = ps.Social()
        try:
            mine, _ = g.parties()
            for p in mine:
                g.leave(p)
            g.remove_friend(h.uid)
        except Exception as e:
            print('уборка:', e)
        self.destroy()
    at(500, cleanup)

    def run(i=0):
        if i >= len(steps):
            return
        ms, fn = steps[i]

        def go():
            try:
                fn()
            except Exception:
                errors.append(traceback.format_exc())
            run(i + 1)
        self.after(ms, go)
    run()
    orig(self, *a)


def rep(self, *args):
    errors.append(''.join(traceback.format_exception(*args))[-1500:])
tk.Tk.report_callback_exception = rep
tk.Tk.mainloop = patched
ps.gui()
print('тестовые логины:', tag + '_g / _h', '| ошибок окна:', len(errors))
for e in errors:
    print(e)

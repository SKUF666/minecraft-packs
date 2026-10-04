"""Уведомления: игрок в окне сидит на вкладке «Карты», второй игрок шлёт заявку, личное сообщение,
приглашение в пати и в игру. Проверяет всплывающие сообщения и счётчик на вкладке «Друзья».
Создаёт на сервере тестовых игроков zt_<...>_n / _o. python tools/gui_notify.py"""
import os, sys, random, string, importlib.machinery, importlib.util, tkinter as tk, traceback
from PIL import ImageGrab
sys.stdout.reconfigure(encoding='utf-8')
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
store, other = {}, {}
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))


def as_other(fn):
    ps.load_settings, ps.save_settings = (lambda: dict(other)), (lambda d: (other.clear(), other.update(d)))
    try:
        return fn()
    finally:
        ps.load_settings, ps.save_settings = (lambda: dict(store)), (lambda d: (store.clear(), store.update(d)))


tag = 'zt_' + ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
pw = 'Portalis-ntf-' + ''.join(random.choice(string.ascii_letters) for _ in range(8))
g = ps.Social()
g.sign_up(tag + '_n', pw, 'Тестер Н')
o = as_other(lambda: ps.Social())
as_other(lambda: o.sign_up(tag + '_o', pw, 'Друг О'))
orig = tk.Tk.mainloop
errors, seen = [], []


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


def patched(self, *a):
    self.geometry('1080x760+30+30')
    hk = self._hooks
    steps = []

    def at(ms, fn):
        steps.append((ms, fn))

    def toasts():
        return [str(x.cget('text')) for x in walk(self) if isinstance(x, tk.Label) and x.winfo_ismapped()
                and any(k in str(x.cget('text')) for k in ('дружить', 'зовут в пати', 'зовёт в игру', ': Привет', 'Тебя ждут'))]

    def tab_text():
        return [str(x.cget('text')) for x in walk(self) if isinstance(x, tk.Label) and 'Друзья' in str(x.cget('text'))][:1]

    def watch(name):
        t = toasts()
        seen.extend(x for x in t if x not in seen)
        print(name, '| вкладка:', tab_text(), '| уведомления:', t)

    at(1500, lambda: hk['show']('maps'))
    at(4000, lambda: as_other(lambda: o.add_friend(tag + '_n')))
    for k in range(8):
        at(4000, lambda k=k: watch('заявка %d' % k))
    at(300, lambda: (g.add_friend(tag + '_o'), as_other(lambda: o.send('Привет, заходи вечером!', to=g.uid))))
    for k in range(8):
        at(4000, lambda k=k: watch('сообщение %d' % k))

    def party():
        pt = as_other(lambda: o.create_party('Вечерний сбор'))
        as_other(lambda: o.invite(pt['id'], g.uid))
        g.join(pt['id'])
        as_other(lambda: o.share_game(pt['id'], 'MC1-test', {'host': o.uid, 'pack': 'RPG Pack'}))
    at(300, party)
    for k in range(9):
        at(4000, lambda k=k: watch('игра %d' % k))
    at(300, lambda: (ImageGrab.grab(bbox=(self.winfo_rootx(), self.winfo_rooty(), self.winfo_rootx() + self.winfo_width(),
                                         self.winfo_rooty() + self.winfo_height())).save(os.path.join(OUT, 'notify.png'))))

    def cleanup():
        try:
            for p in as_other(lambda: o.parties()[0]):
                as_other(lambda: o.leave(p))
            g.remove_friend(o.uid)
        except Exception as e:
            print('уборка:', e)
        self.destroy()
    at(300, cleanup)

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
ok = {'заявка': any('дружить' in x for x in seen), 'сообщение': any('Привет, заходи' in x for x in seen),
      'игра': any('зовёт в игру' in x for x in seen)}
print('увидел уведомления:', ok, '| тестовые логины:', tag + '_n/_o', '| ошибок окна:', len(errors))
for e in errors:
    print(e)
sys.exit(0 if all(ok.values()) and not errors else 1)

"""Профиль: меню слева (наведение не должно «прыгать»), страницы «Обзор» и «Стена» - снимки.
Сервер не нужен: свой профиль и записи стены подставляются. python tools/gui_profile.py"""
import os, sys, tempfile, importlib.machinery, importlib.util, tkinter as tk, traceback, time
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winshot
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
sys.path.insert(0, LIB)
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
ps.set_game_dir(os.path.join(tempfile.mkdtemp(prefix='portalis-prof-'), '.minecraft'))
store = {'lang': os.environ.get('PORTALIS_LANG', 'ru')}
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))
ME = {'id': 'u-me', 'nick': 'Гриша', 'login': 'grisha', 'avatar': None, 'presence': 'auto', 'mood': 'строю базу в OneBlock',
      'about': 'Играю в карты на выживание и собираю сборки с шейдерами. Пиши - позову в пати!',
      'favorites': ['OneBlock', 'SkyBlock', 'Business Battle', 'RPG Pack'],
      'stats': {'minutes': 754, 'maps_n': 9, 'packs': 3, 'friends': 4, 'parties': 2, 'hosted': 1,
                'maps': ['OneBlock', 'SkyBlock', 'PC Building Simulator', 'Business Battle']},
      'skin': os.environ.get('PROF_SKIN', 'Notch'), 'color': None, 'banner': None}
now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
FR = {'id': 'u-fr', 'nick': 'Друг Олег', 'login': 'oleg', 'avatar': None, 'status': 'online'}
ps.Social.uid = property(lambda self: 'u-me')
ps.Social.logged_in = lambda self: True
ps.Social.ready = lambda self: True
ps.Social.wall = lambda self, uid, limit=50: [
    {'id': 3, 'author': 'u-fr', 'author_profile': FR, 'body': 'Го вечером в PC Building Simulator? Я собрал комп за 2 минуты :)',
     'created_at': now, 'reply_to': None, 'reactions': {'fire': ['u-me', 'u-x'], 'like': ['u-x']}},
    {'id': 4, 'author': 'u-me', 'author_profile': ME, 'body': 'Давай, в 19:00, зову в пати!', 'created_at': now,
     'reply_to': 3, 'reactions': {}},
    {'id': 1, 'author': 'u-me', 'author_profile': ME, 'body': 'Прошёл SkyBlock полностью - все 30 заданий 🎉',
     'created_at': now, 'reply_to': None, 'reactions': {'heart': ['u-fr']}}]
orig = tk.Tk.mainloop
errors = []


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


def patched(self, *a):
    self.geometry(os.environ.get('PROF_SIZE', '1180x860') + '+2200+30')
    hk = self._hooks
    steps = []
    at = lambda ms, fn: steps.append((ms, fn))

    def page():
        return [w for w in self.winfo_children() if getattr(w, '_inpage', False)][-1]
    shot = lambda name: winshot.shot(self, os.path.join(OUT, 'profile_' + name + '.png'))

    def menu_row(text):
        return next(w for w in walk(page()) if isinstance(w, tk.Label) and str(w.cget('cursor')) == 'hand2'
                    and str(w.cget('text')).strip() in (text, ps.tr(text)) and str(w.cget('image')))

    def geo(w):
        return (w.winfo_width(), w.winfo_height(), w.winfo_x(), w.winfo_y())
    side_n = {}

    def hover_check():
        """Строка меню - один виджет: наведение меняет только картинку, размеры и место не меняются."""
        lb = menu_row('Стена')
        side_n['n'] = len(lb.master.winfo_children())
        before, img0 = [geo(menu_row(x)) for x in ('Обзор', 'Стена', 'Достижения')], str(lb.cget('image'))
        lb.event_generate('<Enter>', x=5, y=5); self.update()
        img1 = str(lb.cget('image'))
        shot('hover')
        lb.event_generate('<Leave>', x=5, y=5); self.update()
        after = [geo(menu_row(x)) for x in ('Обзор', 'Стена', 'Достижения')]
        print('наведение: картинка сменилась', img0 != img1, '| вернулась', str(lb.cget('image')) == img0,
              '| строки на месте', before == after, after)

    def side_check():
        lb = menu_row('Стена')
        n = len(lb.master.winfo_children())
        print('боковая панель после смены страницы: виджетов', side_n['n'], '->', n,
              '| строки на месте', [geo(menu_row(x)) for x in ('Обзор', 'Стена')])
    at(1800, lambda: (hk['state'].setdefault('soc', {'loaded': False, 'loading': False, 'me': None, 'friends': [], 'parties': [], 'invites': [], 'party': None, 'members': [], 'msgs': [], 'last': 0, 'err': None, 'mode': 'in', 'sel': None}).update(me=ME),
                      hk['open_profile'](ME, True, 'overview')))
    at(4000, lambda: shot('overview'))
    at(200, hover_check)
    at(200, lambda: page()._cv.yview_moveto(1.0))
    at(500, lambda: shot('overview_bottom'))
    at(300, lambda: page()._go('ach'))
    at(1200, lambda: shot('ach'))
    at(300, lambda: page()._go('wall'))
    at(1800, lambda: shot('wall'))
    at(100, side_check)
    at(300, lambda: page()._cv.yview_moveto(1.0))
    at(600, lambda: shot('wall_bottom'))
    at(300, lambda: self.destroy())

    def run(i=0):
        if i >= len(steps):
            return
        ms, fn = steps[i]

        def go():
            try:
                fn()
            except Exception:
                errors.append(traceback.format_exc()[-1500:])
            run(i + 1)
        self.after(ms, go)
    run()
    orig(self, *a)


tk.Tk.report_callback_exception = lambda self, *args: errors.append(''.join(traceback.format_exception(*args))[-1500:])
tk.Tk.mainloop = patched
ps.gui()
print('ошибок окна:', len(errors))
for e in errors:
    print(e)

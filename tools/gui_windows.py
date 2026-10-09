"""Отдельные окна в стиле Portalis: сборка в конструкторе, версия, мастер плащей, окно карты, свой диалог,
тёмная рамка Windows. Снимки - в %TEMP%\\minecraft-packs-shots\\win_*.png. python tools/gui_windows.py"""
import os, sys, tempfile, importlib.machinery, importlib.util, tkinter as tk, traceback
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winshot
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
tmp = tempfile.mkdtemp(prefix='portalis-win-')
ps.set_game_dir(os.path.join(tmp, '.minecraft'))
store = {}
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))
orig = tk.Tk.mainloop
errors = []


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


def tops(self):
    return [w for w in self.winfo_children() if (isinstance(w, tk.Toplevel) or getattr(w, '_inpage', False))]


def patched(self, *a):
    self.geometry('1080x760+2200+30')
    hk = self._hooks
    steps = []

    def at(ms, fn):
        steps.append((ms, fn))

    def shot(w, name):
        winshot.shot(w, os.path.join(OUT, 'win_' + name + '.png'))

    def close_all():
        for w in tops(self):
            w.destroy()
    at(1500, lambda: shot(self, 'main'))

    def build():
        hk['show']('builder')
        cb = hk['builder_state']()
        cb.update(gv='1.21.1', loader='fabric', name='Проверка окон')
        cb['sel'] = {'mOgUt4GM': {'project_id': 'mOgUt4GM', 'title': 'Mod Menu', 'type': 'mod'},
                     'AANobbMI': {'project_id': 'AANobbMI', 'title': 'Sodium', 'type': 'mod'}}
        hk['builder_build']()
    at(800, build)
    at(9000, lambda: shot(tops(self)[-1], 'builder'))
    at(300, close_all)
    at(300, lambda: hk['version_dialog']('1.21.1', 'fabric'))
    at(6000, lambda: shot(tops(self)[-1], 'version'))
    at(300, close_all)
    at(300, lambda: hk['cape_maker']())
    at(1500, lambda: (shot(self, 'cape'), print('мастерская на вкладке:', hk['state'].get('cape_open'))))
    mp = hk['find_maps']()
    at(300, lambda: hk['open_map'](mp[0]))
    at(2500, lambda: shot(tops(self)[-1], 'map'))
    at(300, close_all)

    def ask():
        def grab_and_answer():
            d = tops(self)[-1]
            shot(d, 'dialog')
            for x in walk(d):
                if isinstance(x, tk.Label) and str(x.cget('text')).strip() == 'Да':
                    x.event_generate('<Button-1>')
                    break
        self.after(1200, grab_and_answer)
        r = hk['messagebox'].askyesno('Удалить облачную копию?', 'Копия мира «Мир пати» будет удалена из облака. '
                                      'Миры на компьютере не трогаются.')
        print('ответ диалога:', r)
    at(300, ask)

    def ask_str():
        def grab_and_answer():
            d = tops(self)[-1]
            shot(d, 'input')
            for x in walk(d):
                if isinstance(x, tk.Label) and str(x.cget('text')).strip() == 'ОК':
                    x.event_generate('<Button-1>')
                    break
        self.after(1200, grab_and_answer)
        print('ввод:', hk['messagebox'].askstring('Своя карта', 'Название карты:', initialvalue='Concha Drakona 69'))
    at(300, ask_str)
    at(800, lambda: self.destroy())

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
print('ошибок окна:', len(errors))
for e in errors:
    print(e)

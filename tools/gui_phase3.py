"""Этап 3 в окне: модпаки в конструкторе, сервер пати (настоящий запуск), облако миров, кнопки своей сборки.
Нужна папка от tools/test_phase3.py: python tools/gui_phase3.py <папка portalis-p3-...>"""
import os, sys, random, string, time, importlib.machinery, importlib.util, tkinter as tk, traceback
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winshot
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
tmp = sys.argv[1]
from tkinter import messagebox as MB
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
ps.ROOT = tmp
ps.MAPS_DIR = os.path.join(tmp, 'Карты')
ps.set_game_dir(os.path.join(tmp, '.minecraft'))
store = {}
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))
MB.askyesno = lambda *a, **k: True
MB.askokcancel = lambda *a, **k: True
MB.showinfo = lambda *a, **k: print('   сообщение:', (a[1] if len(a) > 1 else '')[:300].replace('\n', ' | '))
tag = 'zt_' + ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
pw = 'Portalis-p3-' + ''.join(random.choice(string.ascii_letters) for _ in range(8))
g = ps.Social()
g.sign_up(tag + '_g', pw, 'Хост П3')
orig = tk.Tk.mainloop
errors = []


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


def tops(self):
    return [w for w in self.winfo_children() if isinstance(w, tk.Toplevel)]


def labels(w):
    return [str(x.cget('text')) for x in walk(w) if isinstance(x, tk.Label) and str(x.cget('text')).strip()]


def patched(self, *a):
    self.geometry('1080x760+2200+30')
    hk = self._hooks
    steps = []

    def at(ms, fn):
        steps.append((ms, fn))

    def click(root, text):
        for x in walk(root):
            if isinstance(x, tk.Label) and str(x.cget('text')).strip().startswith(text):
                x.event_generate('<Button-1>'); return True
        return False
    at(1500, lambda: hk['show']('builder'))
    at(1500, lambda: hk['builder_set'](ptype='modpack'))
    at(6000, lambda: (winshot.shot(self, os.path.join(OUT, 'p3_modpacks.png')),
                      print('модпаки:', [t for t in labels(self) if 'Поставить модпак' in t][:2])))
    pk = [p for p in ps.find_packs() if p.get('user')]
    at(300, lambda: hk['open_pack'](pk[0]))
    at(2500, lambda: tops(self)[-1]._cv.yview_moveto(1.0))
    at(800, lambda: (winshot.shot(tops(self)[-1], os.path.join(OUT, 'p3_pack.png')),
                     print('кнопки сборки:', [t for t in labels(tops(self)[-1]) if 'обновлен' in t.lower() or 'пати' in t])))
    at(300, lambda: print('проверка обновлений:', click(tops(self)[-1], 'Проверить обновления')))
    at(15000, lambda: [w.destroy() for w in tops(self)])
    at(300, lambda: hk['party_server_window']())
    at(1500, lambda: winshot.shot(tops(self)[-1], os.path.join(OUT, 'p3_server_form.png')))
    at(300, lambda: print('запуск сервера:', click(tops(self)[-1], 'Запустить сервер')))
    at(45000, lambda: (winshot.shot(tops(self)[-1], os.path.join(OUT, 'p3_server_run.png')),
                       print('сервер:', [t for t in labels(tops(self)[-1]) if t.startswith('●') or 'Minecraft' in t])))

    def stop():
        srv = ps_state()['psrv']
        srv.stop()
        print('сервер выключен:', not srv.alive())
    at(300, stop)
    at(3000, lambda: [w.destroy() for w in tops(self)])
    at(300, lambda: hk['cloud_window']())
    at(4000, lambda: print('облако, отправить:', click(tops(self)[-1], 'В облако')))
    at(12000, lambda: (winshot.shot(tops(self)[-1], os.path.join(OUT, 'p3_cloud.png')),
                       print('облако:', [t for t in labels(tops(self)[-1]) if 'Мир' in t or 'МБ' in t][:8])))
    at(300, lambda: print('скачать:', click(tops(self)[-1], 'Скачать в игру')))
    at(8000, lambda: print('saves:', os.listdir(ps.SAVES)))

    def cleanup():
        try:
            for o in g.list_files('worlds', g.uid + '/'):
                g.delete_file('worlds', g.uid + '/' + o['name'])
        except Exception as e:
            print('уборка:', e)
        self.destroy()
    at(500, cleanup)

    def ps_state():
        st = hk['show'].__closure__
        for cell in st or []:
            v = cell.cell_contents
            if isinstance(v, dict) and 'tab' in v:
                return v
        raise RuntimeError('нет state')

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
print('тестовый логин:', tag + '_g', '| ошибок окна:', len(errors))
for e in errors:
    print(e)

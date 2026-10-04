"""Окно «Версии» как человек: уже скачанная версия («всё уже скачано») и скачивание новой с прогрессом.
Папка игры временная (PORTALIS_TEST_MC или новая). python tools/gui_versions.py"""
import os, sys, tempfile, importlib.machinery, importlib.util, tkinter as tk, traceback
from PIL import ImageGrab
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winshot
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
ps.save_settings = lambda d: None
mc = os.environ.get('PORTALIS_TEST_MC') or os.path.join(tempfile.mkdtemp(prefix='portalis-gui-'), '.minecraft')
os.makedirs(mc, exist_ok=True)
orig_set = ps.set_game_dir
ps.set_game_dir = lambda path=None: orig_set(mc)
orig_set(mc)
assert os.path.normcase(ps.MC) == os.path.normcase(mc), 'папка игры не подменилась'
orig = tk.Tk.mainloop
errors, seen = [], {}


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


def texts(w):
    return [str(x.cget('text')) for x in walk(w) if isinstance(x, tk.Label)]


def grab(w, name):
    winshot.shot(w, os.path.join(OUT, name + '.png'))


def patched(self, *a):
    self.geometry('1080x760+2200+30')
    h = self._hooks

    def top():
        return [w for w in self.winfo_children() if isinstance(w, tk.Toplevel)][-1]

    def click(w, text):
        for x in walk(w):
            if isinstance(x, tk.Label) and str(x.cget('text')).strip().startswith(text):
                x.event_generate('<Button-1>')
                return True
        return False

    def step1():
        h['show']('versions')
        h['version_dialog']('1.7.10', 'vanilla', None, False)
        self.after(6000, step2)

    def step2():
        t = top()
        seen['plan'] = [x for x in texts(t) if x.startswith('Скачаю')]
        grab(t, 'ver_plan')
        print('план:', seen['plan'], '| нажал:', click(t, 'Скачать'))
        self.after(2500, lambda: (grab(top(), 'ver_progress'), wait_done(0)))

    def wait_done(n):
        t = top()
        done = [x for x in texts(t) if x.startswith(('Готово!', 'Не получилось'))]
        if done or n > 120:
            print('итог:', done)
            grab(t, 'ver_done')
            t.destroy()
            self.after(300, step3)
            return
        self.after(1000, lambda: wait_done(n + 1))

    def step3():
        h['version_dialog']('1.7.10', 'vanilla', None, False)
        self.after(5000, lambda: (print('повторно:', [x for x in texts(top()) if x.startswith('Всё уже')]),
                                  top().destroy(), h['show']('versions'), self.after(1500, step4)))

    def step4():
        canvas = h['body']
        canvas.yview_moveto(1.0)
        self.after(600, lambda: (grab(self, 'ver_list'), print('карточки:', [x for x in texts(self) if x.startswith('●')]),
                                 self.destroy()))
    self.after(1500, step1)
    orig(self, *a)


def rep(self, *args):
    errors.append(''.join(traceback.format_exception(*args))[-1500:])
tk.Tk.report_callback_exception = rep
tk.Tk.mainloop = patched
ps.gui()
print('папка игры:', mc)
print('ошибок окна:', len(errors))
for e in errors:
    print(e)

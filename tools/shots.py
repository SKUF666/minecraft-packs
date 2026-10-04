"""Скриншоты окна: заставка, вкладки, «Готово», тост. python shots.py [W H TAG]"""
import os, sys, importlib.machinery, importlib.util, tkinter as tk, traceback
from PIL import ImageGrab
sys.stdout.reconfigure(encoding='utf-8')
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
os.makedirs(OUT, exist_ok=True)
LIB = os.environ.get('LIB') or os.path.join(os.environ['LOCALAPPDATA'], 'Portalis')
W, H = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (1080, 760)
TAG = sys.argv[3] if len(sys.argv) > 3 else ''
ONLY = os.environ.get('ONLY', '')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
if os.environ.get('NOUPDATE'):
    ps.fetch_remote_manifest = lambda timeout=15: (_ for _ in ()).throw(RuntimeError('offline test'))
ps.apply_update = lambda *a, **k: {'restart': False, 'trash': None}
ps.save_settings = lambda d: None
orig = tk.Tk.mainloop


def grab(w, name):
    w.update()
    x, y = w.winfo_rootx(), w.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x + w.winfo_width(), y + w.winfo_height())).save(os.path.join(OUT, '%s%s.png' % (TAG, name)))


def patched(self, *a):
    self.geometry('%dx%d+30+30' % (W, H))
    hooks = self._hooks
    seq = []
    if not ONLY:
        seq += [('splash', 450)]
    seq += [('maps', 2300), ('packs', 900), ('servers', 900), ('friend', 900), ('launchers', 3500),
            ('done', 900), ('toast', 900), ('update', 4000)]
    if ONLY:
        seq = [s for s in seq if s[0] in ONLY.split(',')]

    def run(i=0):
        if i >= len(seq):
            self.destroy(); return
        name, delay = seq[i]

        def act():
            try:
                if name == 'splash':
                    sp = [w for w in self.winfo_children() if isinstance(w, tk.Toplevel)]
                    if sp:
                        grab(sp[0], 'splash')
                elif name in ps.TAB_ORDER:
                    self.attributes('-topmost', True)
                    hooks['show'](name)
                    self.after(500, lambda: (grab(self, name), run(i + 1)))
                    return
                elif name == 'done':
                    hooks['finish'](True, ['Карта «OneBlock» установлена, файлов: 1520',
                                           'Скопировано модов: 21', 'Версия в TLauncher: Fabric 26.1.2'])
                    def snap():
                        top = [w for w in self.winfo_children() if isinstance(w, tk.Toplevel)][-1]
                        top.attributes('-topmost', True)
                        grab(top, 'done'); top.destroy(); run(i + 1)
                    self.after(700, snap)
                    return
                elif name == 'toast':
                    hooks['toast']('Адрес скопирован: mc.example.net', 'ok', ('Запустить', lambda: None))
                    self.after(700, lambda: (grab(self, 'toast'), run(i + 1)))
                    return
                elif name == 'update':
                    remote = ps.load_local_manifest()
                    remote = dict(remote, version='2099.01.01', changes=['Новая карта: Пример', 'Обновлена сборка RPG Pack'])
                    hooks['open_update'](remote)
                    def snap():
                        top = [w for w in self.winfo_children() if isinstance(w, tk.Toplevel)][-1]
                        top.attributes('-topmost', True)
                        grab(top, 'update'); run(i + 1)
                    self.after(3500, snap)
                    return
            except Exception:
                traceback.print_exc()
            run(i + 1)
        self.after(delay, act)
    self.after(10, run)
    orig(self, *a)


def rep(self, *args):
    print('TK ERROR:', ''.join(traceback.format_exception(*args))[-2500:])
tk.Tk.report_callback_exception = rep
tk.Tk.mainloop = patched
ps.gui()
print('ok')

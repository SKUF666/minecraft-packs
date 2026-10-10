"""Portalis как лаунчер: вкладка «Лаунчеры» с выбранным Portalis, кнопка «Играть» запускает игру сама
(запуск подменён - игра не открывается). python tools/gui_direct.py"""
import os, sys, tempfile, importlib.machinery, importlib.util, tkinter as tk, traceback
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winshot
OUT = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-shots')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
sys.path.insert(0, LIB)
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
ps.set_game_dir(os.path.join(os.environ['APPDATA'], '.minecraft'))
store = {'launcher': 'portalis', 'direct_nick': 'PortalisTest', 'direct_ram': 2048, 'direct_version': '26.2'}
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))
calls = []


class FakeProc:
    log_path = 'x'
    returncode = None

    def poll(self):
        return None


def fake_launch(version, mc, nick, java, ram, **kw):
    argv = ps.mc_launch.build_launch(version, mc, nick, java, ram, log=lambda *a: None)[0]
    calls.append((version, nick, ram, os.path.basename(java), len(argv)))
    return FakeProc()
ps.mc_launch.launch = fake_launch
ps.game_running = lambda: False
orig = tk.Tk.mainloop
errors = []


def patched(self, *a):
    self.geometry('1180x800+2200+30')
    hk = self._hooks
    steps = []
    at = lambda ms, fn: steps.append((ms, fn))
    shot = lambda name: winshot.shot(self, os.path.join(OUT, 'direct_' + name + '.png'))
    at(1800, lambda: hk['show']('launchers', animated=False))
    at(2500, lambda: shot('launchers'))
    at(300, lambda: print('запуск:', ps.open_launcher(), calls))
    at(1500, lambda: shot('after'))
    at(300, lambda: self.destroy())

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


tk.Tk.report_callback_exception = lambda self, *args: errors.append(''.join(traceback.format_exception(*args))[-1500:])
tk.Tk.mainloop = patched
ps.gui()
print('ошибок окна:', len(errors))
for e in errors:
    print(e)

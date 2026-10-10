"""Раздел «Проверка модов» в окне сборки. python tools/gui_doctor.py"""
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
ps.set_game_dir(os.path.join(tempfile.mkdtemp(prefix='portalis-doc-'), '.minecraft'))
store = {}
if os.environ.get('PORTALIS_LANG'):
    store['lang'] = os.environ['PORTALIS_LANG']
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))
orig = tk.Tk.mainloop
errors = []


def patched(self, *a):
    self.geometry('1180x800+2200+30')
    hk = self._hooks
    steps = []
    at = lambda ms, fn: steps.append((ms, fn))
    packs = [p for p in ps.find_packs() if 'RPG' in p['name']] or ps.find_packs()
    print('сборка:', packs[0]['name'], 'проблем:', len(ps.mod_doctor.check_mods(os.path.join(packs[0]['path'], 'mods'), '1.20.1', 'forge')) if 'RPG' in packs[0]['name'] else '-')
    at(1500, lambda: hk['open_pack'](packs[0]))
    at(6000, lambda: [w for w in self.winfo_children() if getattr(w, '_inpage', False)][-1]._cv.yview_moveto(0.0))
    at(1500, lambda: winshot.shot(self, os.path.join(OUT, 'doctor_%s.png' % os.environ.get('PORTALIS_LANG', 'ru'))))
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

"""Быстрый поиск (Ctrl+K): находит карты, сборки, серверы, вкладки и действия, Enter открывает. python tools/gui_qsearch.py"""
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
tmp = tempfile.mkdtemp(prefix='portalis-qsearch-')
ps.set_game_dir(os.path.join(tmp, '.minecraft'))
ps.SKINS_DIR = os.path.join(tmp, 'Скины')
store = {}
if os.environ.get('PORTALIS_LANG'):
    store['lang'] = os.environ['PORTALIS_LANG']
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))
orig = tk.Tk.mainloop
errors = []


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


def click(root, text):
    for x in walk(root):
        try:
            if str(x.cget('text')).strip().startswith(text) and x.winfo_ismapped():
                x.event_generate('<Enter>'); x.event_generate('<Button-1>')
                return True
        except tk.TclError:
            pass
    return False


def patched(self, *a):
    self.geometry('1180x860+2200+30')
    hk = self._hooks
    steps = []
    at = lambda ms, fn: steps.append((ms, fn))
    shot = lambda name: winshot.shot(self, os.path.join(OUT, 'qsearch_' + name + '.png'))
    def tops():
        return [w for w in self.winfo_children() if getattr(w, '_inpage', False)]

    def typ(text):
        en = [w for w in walk(tops()[-1]) if isinstance(w, tk.Entry)][0]
        en.delete(0, 'end'); en.insert(0, text)
        en.event_generate('<KeyRelease>', keysym='space')
        self.update()
        rows = [w.cget('text').strip() for w in walk(tops()[-1]) if isinstance(w, tk.Label) and
                str(w.cget('font')).find('bold') >= 0 and w.cget('text').startswith('  ')]
        print('«%s» ->' % text, rows[:5])
        return en

    def enter():
        en = [w for w in walk(tops()[-1]) if isinstance(w, tk.Entry)][0]
        en.event_generate('<Return>')

    at(1800, lambda: (hk['quick_search'](), print('Ctrl+K привязан:', bool(self.bind_all('<Control-k>')))))
    at(600, lambda: (print('открыт:', len(tops())), shot('empty')))
    at(300, lambda: typ('sky'))
    at(400, lambda: shot('sky'))
    at(200, enter)
    at(1500, lambda: print('после Enter окно:', [getattr(w, '_title', '') for w in tops()]))
    at(300, lambda: [w.destroy() for w in tops()])
    at(300, lambda: (hk['quick_search'](), print('Ctrl+K привязан:', bool(self.bind_all('<Control-k>')))))
    at(500, lambda: typ('оформ' if not os.environ.get('PORTALIS_LANG') else 'appear'))
    at(200, enter)
    at(1500, lambda: (print('после Enter окно:', [getattr(w, '_title', '') for w in tops()]), shot('style')))
    at(300, lambda: [w.destroy() for w in tops()])
    at(300, lambda: (hk['show']('servers', animated=False)))
    at(800, lambda: self.event_generate('<Control-f>', when='now') or (not tops() and hk['quick_search']()))
    at(500, lambda: typ('hypix bedwars'))
    at(200, lambda: typ('blocksmc'))
    at(200, lambda: typ('zzzzqq'))
    at(300, lambda: shot('nothing'))
    at(200, lambda: self.event_generate('<Escape>'))
    at(1000, lambda: self.destroy())

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

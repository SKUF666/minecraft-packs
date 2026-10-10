"""3D-просмотр скина: в мастерской, на карточках «Мои скины» (с плащом) и в «Надеть». python tools/gui_skin3d.py"""
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
tmp = tempfile.mkdtemp(prefix='portalis-skin3d-')
ps.set_game_dir(os.path.join(tmp, '.minecraft'))
ps.SKINS_DIR = os.path.join(tmp, 'Скины')
store = {}
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
    shot = lambda name: winshot.shot(self, os.path.join(OUT, 'skin3d_' + name + '.png'))
    def seed():
        import io
        from PIL import Image
        cape = Image.new('RGBA', (64, 32), (0, 0, 0, 0))
        for x in range(64):
            for y in range(32):
                cape.putpixel((x, y), (180, 30 + x * 3, 60 + y * 5, 255))
        b = io.BytesIO(); cape.save(b, 'PNG')
        import skin_maker
        sk = io.BytesIO(); skin_maker.make_skin(skin_maker.default_spec()).save(sk, 'PNG')
        ps.save_my_skin('С плащом', sk.getvalue(), False, b.getvalue(), 'тест')

    def drag(dx, dy):
        lbs = [x for x in walk(self) if isinstance(x, tk.Label) and str(x.cget('cursor')) == 'fleur' and x.winfo_ismapped()]
        lb = lbs[-1]
        x0, y0 = lb.winfo_rootx() + 60, lb.winfo_rooty() + 60
        lb.event_generate('<ButtonPress-1>', x=60, y=60, rootx=x0, rooty=y0)
        for i in range(1, 9):
            lb.event_generate('<B1-Motion>', x=60 + dx * i // 8, y=60 + dy * i // 8, rootx=x0 + dx * i // 8, rooty=y0 + dy * i // 8)
            self.update()
        lb.event_generate('<ButtonRelease-1>', x=60 + dx, y=60 + dy, rootx=x0 + dx, rooty=y0 + dy)
        print('3D-видов:', len(lbs))
    at(1500, seed)
    at(300, lambda: (hk['state'].update(skinws_open=True), hk['show']('skins', animated=False)))
    at(1500, lambda: hk['body'].yview_moveto(0.1))
    at(600, lambda: drag(140, -40))
    at(400, lambda: shot('workshop'))
    at(200, lambda: print('3D кнопка:', click(self, 'Покрутить в 3D')))
    at(900, lambda: drag(-200, 30))
    at(300, lambda: print('элитры:', click(self, 'Элитры')))
    at(600, lambda: shot('window_elytra'))
    at(200, lambda: print('шаг:', click(self, 'Шаг'), 'плащ:', click(self, 'Плащ')))
    at(600, lambda: shot('window_walk'))
    at(200, lambda: print('закрыть:', click(self, 'Закрыть')))
    at(600, lambda: print('надеть:', click(self, 'Надеть')))
    at(900, lambda: shot('wear'))
    at(1500, lambda: self.destroy())

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

"""Перетаскивание файлов в окно: распознавание (скин, старый скин, плащ, карта, модпак, мод, ресурс-пак) и настоящий
WM_DROPFILES, как от Проводника: скин из сообщения попадает в «Мои скины». python tools/gui_drop.py"""
import os, sys, io, json, zipfile, tempfile, ctypes, importlib.machinery, importlib.util, tkinter as tk, traceback
from ctypes import wintypes
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
sys.path.insert(0, LIB)
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
from PIL import Image
ps.fetch_remote_manifest = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline test'))
tmp = tempfile.mkdtemp(prefix='portalis-drop-')
ps.set_game_dir(os.path.join(tmp, '.minecraft'))
ps.SKINS_DIR = os.path.join(tmp, 'Скины')
store = {}
ps.load_settings = lambda: dict(store)
ps.save_settings = lambda d: (store.clear(), store.update(d))
fails = []


def check(name, got, want):
    ok = got == want
    print('%s  %-28s %s' % ('OK ' if ok else 'ОШИБКА', name, got))
    if not ok:
        fails.append(name)


def png(path, w, h, fill_box=None):
    im = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    for x in range(min(w, 32)):
        for y in range(min(h, 16)):
            im.putpixel((x, y), (200, 150, 100, 255))
    if fill_box:
        for x in range(fill_box[0], fill_box[2]):
            for y in range(fill_box[1], fill_box[3]):
                im.putpixel((x, y), (50, 90, 200, 255))
    im.save(path)
    return path


def zipf(path, names):
    with zipfile.ZipFile(path, 'w') as z:
        for n in names:
            z.writestr(n, '{}')
    return path


d = os.path.join(tmp, 'files')
os.makedirs(os.path.join(d, 'Мир', 'region'))
open(os.path.join(d, 'Мир', 'level.dat'), 'wb').write(b'x')
skin = png(os.path.join(d, 'Мой скин.png'), 64, 64)
check('скин 64x64', ps.classify_drop(skin), 'skin')
check('старый скин 64x32', ps.classify_drop(png(os.path.join(d, 'old.png'), 64, 32, (44, 20, 56, 32))), 'skin')
check('плащ 64x32', ps.classify_drop(png(os.path.join(d, 'c.png'), 64, 32)), 'cape')
check('плащ по имени', ps.classify_drop(png(os.path.join(d, 'my_cape.png'), 64, 64)), 'cape')
check('картинка не скин', ps.classify_drop(png(os.path.join(d, 'photo.png'), 300, 200)), None)
check('папка мира', ps.classify_drop(os.path.join(d, 'Мир')), 'map')
check('архив карты', ps.classify_drop(zipf(os.path.join(d, 'map.zip'), ['World/level.dat'])), 'map')
check('модпак .mrpack', ps.classify_drop(zipf(os.path.join(d, 'p.mrpack'), ['modrinth.index.json'])), 'mrpack')
check('модпак CurseForge', ps.classify_drop(zipf(os.path.join(d, 'cf.zip'), ['manifest.json', 'overrides/a'])), 'cfpack')
check('ресурс-пак', ps.classify_drop(zipf(os.path.join(d, 'rp.zip'), ['pack.mcmeta', 'assets/x'])), 'resourcepack')
check('мод .jar', ps.classify_drop(zipf(os.path.join(d, 'sodium.jar'), ['fabric.mod.json'])), 'mod')
check('непонятный файл', ps.classify_drop(zipf(os.path.join(d, 'x.txt'), [])), None)


def send_drop(hwnd, paths):
    """HDROP в памяти, как его собирает Проводник, и WM_DROPFILES окну."""
    files = ('\0'.join(paths) + '\0\0').encode('utf-16-le')
    head = 20  # DROPFILES: pFiles, pt.x, pt.y, fNC, fWide
    k32 = ctypes.windll.kernel32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    h = k32.GlobalAlloc(0x0042, head + len(files))
    p = k32.GlobalLock(h)
    ctypes.memmove(p, ctypes.c_buffer(b'\x14\0\0\0' + b'\0' * 12 + b'\x01\0\0\0', 20), 20)
    ctypes.memmove(p + head, files, len(files))
    k32.GlobalUnlock(ctypes.c_void_p(h))
    ctypes.windll.user32.PostMessageW(wintypes.HWND(hwnd), 0x0233, wintypes.WPARAM(h), 0)


orig = tk.Tk.mainloop
errors = []


def patched(self, *a):
    def go():
        user32 = ctypes.windll.user32
        user32.GetParent.restype = wintypes.HWND
        hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
        print('обработчик подключён:', 'drop_proc' in self._hooks['state'])
        send_drop(hwnd, [skin])
    self.after(3000, go)

    def done():
        got = [m['name'] for m in ps.my_skins()]
        check('скин через WM_DROPFILES', got, ['Мой скин'])
        self.destroy()
    self.after(5500, done)
    orig(self, *a)


tk.Tk.report_callback_exception = lambda self, *args: errors.append(''.join(traceback.format_exception(*args))[-1200:])
tk.Tk.mainloop = patched
ps.gui()
print('ошибок окна:', len(errors))
for e in errors:
    print(e)
print('Итог: ошибок %d' % (len(fails) + len(errors)))

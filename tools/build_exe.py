"""Собирает «Выбор карты и сборки.exe» из PackSwitcher.pyw и кладёт в библиотеку."""
import os, sys, shutil, subprocess, time
from PIL import Image
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.environ['USERPROFILE'], 'Desktop', 'Minecraft Packs')
B = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-build')
os.makedirs(B, exist_ok=True)
shutil.copy2(os.path.join(LIB, 'PackSwitcher.pyw'), os.path.join(B, 'PackSwitcher.pyw'))
Image.open(os.path.join(LIB, 'icon.png')).save(os.path.join(B, 'app.ico'),
                                               sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
r = subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onefile', '--windowed', '--icon', 'app.ico',
                    '--name', 'PackSwitcher', 'PackSwitcher.pyw'], cwd=B, capture_output=True, text=True)
if r.returncode:
    print(r.stdout[-2000:], r.stderr[-3000:])
    sys.exit(1)
src = os.path.join(B, 'dist', 'PackSwitcher.exe')
dst = os.path.join(LIB, 'Выбор карты и сборки.exe')
try:
    shutil.copy2(src, dst)
except PermissionError:
    old = dst + '.old'
    if os.path.exists(old):
        os.remove(old)
    os.replace(dst, old)
    shutil.copy2(src, dst)
    print('старый exe был открыт: переименован в .old')
print('exe', os.path.getsize(dst) // 1024, 'KB')

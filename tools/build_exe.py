"""Собирает «Portalis.exe» из PackSwitcher.pyw и кладёт в библиотеку (старое имя exe убирает)."""
import os, sys, shutil, subprocess, time
from PIL import Image
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.environ['USERPROFILE'], 'Desktop', 'Minecraft Packs')
B = os.path.join(os.environ.get('TEMP', '.'), 'minecraft-packs-build')
os.makedirs(B, exist_ok=True)
shutil.copy2(os.path.join(LIB, 'PackSwitcher.pyw'), os.path.join(B, 'PackSwitcher.pyw'))
Image.open(os.path.join(LIB, 'icon.png')).save(os.path.join(B, 'app.ico'),
                                               sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
# Сведения о файле (видны в «Свойствах» и в окне SmartScreen): название и версия по дате.
v = time.strftime('%Y.%m.%d').split('.')
ver = '(%d, %d, %d, 0)' % (int(v[0]), int(v[1]), int(v[2]))
open(os.path.join(B, 'version.txt'), 'w', encoding='utf-8').write('''VSVersionInfo(
  ffi=FixedFileInfo(filevers=%s, prodvers=%s, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('041904B0', [
    StringStruct('CompanyName', 'SKUF666'),
    StringStruct('FileDescription', 'Portalis: карты, сборки и серверы Minecraft'),
    StringStruct('FileVersion', '%s'),
    StringStruct('InternalName', 'Kuboteka'),
    StringStruct('OriginalFilename', 'Portalis.exe'),
    StringStruct('ProductName', 'Portalis'),
    StringStruct('ProductVersion', '%s')])]),
  VarFileInfo([VarStruct('Translation', [1049, 1200])])])
''' % (ver, ver, '.'.join(v), '.'.join(v)))
r = subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onefile', '--windowed', '--icon', 'app.ico',
                    '--version-file', 'version.txt', '--name', 'PackSwitcher', 'PackSwitcher.pyw'], cwd=B,
                   capture_output=True, text=True)
if r.returncode:
    print(r.stdout[-2000:], r.stderr[-3000:])
    sys.exit(1)
src = os.path.join(B, 'dist', 'PackSwitcher.exe')
dst = os.path.join(LIB, 'Portalis.exe')
for old in ('Выбор карты и сборки.exe', 'Куботека.exe'):
    try:
        os.remove(os.path.join(LIB, old))
    except OSError:
        pass
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

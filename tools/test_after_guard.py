"""Таймеры after() снимаются вместе с виджетом (защита от «missing 1 required positional argument: 'e'»).
python tools/test_after_guard.py"""
import os, sys, importlib.machinery, importlib.util, tkinter as tk
sys.stdout.reconfigure(encoding='utf-8')
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library')
L = importlib.machinery.SourceFileLoader('ps', os.path.join(LIB, 'PackSwitcher.pyw'))
ps = importlib.util.module_from_spec(importlib.util.spec_from_loader('ps', L)); L.exec_module(ps)
ps.install_after_guard()
root = tk.Tk()
root.withdraw()
fired, errors = [], []
root.report_callback_exception = lambda *a: errors.append(a[1])
fr = tk.Frame(root)
lb = tk.Label(fr)
ids = [lb.after(200, lambda: fired.append('лейбл')), fr.after_idle(lambda: fired.append('рамка'))]
keep = root.after(300, lambda: fired.append('окно'))
fr.destroy()
pending = root.tk.splitlist(root.tk.call('after', 'info'))
root.after(600, root.quit)
root.mainloop()
bad = [i for i in ids if i in pending]
print('таймеры удалённых виджетов сняты:', not bad, '| сработали:', fired, '| ошибок:', len(errors))
ok = not bad and fired == ['окно'] and not errors
print('Итог: ошибок %d' % (0 if ok else 1))

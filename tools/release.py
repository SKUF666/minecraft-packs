"""Выпуск Portalis через GitHub: исходники -> src/ и в репозиторий, сборка exe на GitHub Actions (там же подпись
SignPath, когда она подключена), exe из сборки -> library/Portalis.exe, затем обычная публикация (publish.py).
Так выпускается ровно тот exe, что собран из открытого исходника, а не на этом компьютере.

    python tools/release.py --note "Что нового" [--note "..."] [--local]   (--local - собрать exe здесь, как раньше)
"""
import os, sys, shutil, subprocess, time, json, tempfile
sys.stdout.reconfigure(encoding='utf-8')
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO, 'library')
MODULES = ('PackSwitcher.pyw', 'mc_launch.py', 'skin_maker.py', 'mod_doctor.py', 'portalis_voice.py', 'skin3d.py')


def run(*args, check=True, capture=False):
    r = subprocess.run(args, cwd=REPO, capture_output=capture, text=True, encoding='utf-8', errors='replace')
    if check and r.returncode:
        raise SystemExit('команда не прошла: %s\n%s%s' % (' '.join(args), r.stdout or '', r.stderr or ''))
    return r


def main():
    notes = []
    args = sys.argv[1:]
    local = '--local' in args
    i = 0
    while i < len(args):
        if args[i] == '--note' and i + 1 < len(args):
            notes += ['--note', args[i + 1]]
            i += 2
        else:
            i += 1
    if local:
        run(sys.executable, os.path.join(REPO, 'tools', 'build_exe.py'))
        return run(sys.executable, os.path.join(REPO, 'tools', 'publish.py'), *notes)
    # 1. исходники в репозиторий
    for m in MODULES:
        if os.path.isfile(os.path.join(LIB, m)):
            shutil.copy2(os.path.join(LIB, m), os.path.join(REPO, 'src', m))
    run('git', 'add', 'src')
    if run('git', 'diff', '--cached', '--quiet', check=False).returncode:
        run('git', 'commit', '-q', '-m', 'Исходники выпуска\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')
    run('git', 'push', '-q', 'origin', 'main')
    sha = run('git', 'rev-parse', 'HEAD', capture=True).stdout.strip()
    print('исходники на GitHub:', sha[:10])
    # 2. сборка на GitHub
    started = time.time()
    run('gh', 'workflow', 'run', 'build.yml', '--ref', 'main')
    run_id = None
    for _ in range(40):
        time.sleep(4)
        rows = json.loads(run('gh', 'run', 'list', '--workflow', 'build.yml', '--limit', '5', '--json',
                              'databaseId,headSha,createdAt,event', capture=True).stdout)
        hit = [r for r in rows if r['headSha'] == sha and r['event'] == 'workflow_dispatch']
        if hit:
            run_id = hit[0]['databaseId']
            break
    if not run_id:
        raise SystemExit('сборка на GitHub не запустилась')
    print('сборка: https://github.com/SKUF666/minecraft-packs/actions/runs/%s' % run_id)
    r = run('gh', 'run', 'watch', str(run_id), '--exit-status', check=False, capture=True)
    if r.returncode:
        raise SystemExit('сборка на GitHub не прошла - смотри ссылку выше')
    # 3. exe из сборки
    out = tempfile.mkdtemp(prefix='portalis-ci-')
    run('gh', 'run', 'download', str(run_id), '-n', 'Portalis', '-D', out)
    exe = os.path.join(out, 'Portalis.exe')
    if not os.path.isfile(exe) or os.path.getsize(exe) < 5 << 20:
        raise SystemExit('в сборке нет Portalis.exe')
    shutil.copy2(exe, os.path.join(LIB, 'Portalis.exe'))
    print('exe из сборки GitHub: %d КБ, %d с' % (os.path.getsize(exe) // 1024, time.time() - started))
    # 4. публикация
    run(sys.executable, os.path.join(REPO, 'tools', 'publish.py'), *notes)


if __name__ == '__main__':
    main()

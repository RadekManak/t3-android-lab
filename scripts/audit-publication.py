#!/usr/bin/env python3
"""Check publication content and all reachable Git history; requires gitleaks on PATH."""
from pathlib import Path
import io
import json
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def private(path):
    name = Path(path).name
    return (name.endswith('.local.json') or name.startswith('.env') or name.startswith('adbkey')
            or name == 'auth.json' or any(part in {'.state', '.scratch'} for part in Path(path).parts)
            or re.search(r'\.(?:apk|sqlite(?:-wal|-shm)?|log)$', name))


def local_identifiers():
    values = {str(Path.home())}
    sandbox = ROOT / 'config.local.json'
    if sandbox.exists():
        config = json.loads(sandbox.read_text())
        if config.get('serial'):
            values.add(config['serial'].rsplit(':', 1)[0])
    inventory = ROOT / 'devices.local.json'
    if inventory.exists():
        values.update(device['host'] for device in json.loads(inventory.read_text())['devices'].values())
    return [value.encode() for value in values if value]


def check_identifiers(content, location, identifiers):
    if any(value in content for value in identifiers):
        raise SystemExit('Local home path or configured device address in publication content: ' + location)


def main():
    if not shutil.which('gitleaks'):
        raise SystemExit('Install gitleaks and put it on PATH, then rerun scripts/audit-publication.py.')
    history = git('rev-list', '--objects', '--all').decode().splitlines()
    bad = [line.split(' ', 1)[1] for line in history if ' ' in line and private(line.split(' ', 1)[1])]
    files = [p.decode() for p in git('ls-files', '--cached', '--others', '--exclude-standard', '-z').split(b'\0') if p]
    bad.extend(p for p in files if private(p))
    if bad:
        raise SystemExit('Private paths in publishable content or history: ' + ', '.join(sorted(set(bad))))
    identifiers = local_identifiers()
    objects = '\n'.join(line.split(' ', 1)[0] for line in history).encode() + b'\n'
    stream = io.BytesIO(subprocess.check_output(['git', '-C', str(ROOT), 'cat-file', '--batch'], input=objects))
    while header := stream.readline():
        oid, kind, size = header.decode().split()
        content = stream.read(int(size))
        stream.read(1)
        if kind == 'blob':
            check_identifiers(content, oid, identifiers)
    for name in ['config.local.json', 'devices.local.json', '.state/logs/backend.log', '.scratch/report.json', '.env', 'auth.json', 'adbkey']:
        subprocess.run(['git', '-C', str(ROOT), 'check-ignore', '-q', '--no-index', name], check=True)
    subprocess.run(['gitleaks', 'git', str(ROOT), '--log-opts=--all', '--redact', '--no-banner'], check=True)
    with tempfile.TemporaryDirectory(prefix='android-lab-audit-') as temporary:
        snapshot = Path(temporary)
        for name in files:
            source = ROOT / name
            if source.is_symlink():
                raise SystemExit('Review symlink before publication: ' + name)
            if source.is_file():
                check_identifiers(source.read_bytes(), name, identifiers)
                target = snapshot / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        subprocess.run(['gitleaks', 'dir', str(snapshot), '--redact', '--no-banner'], check=True)
    print('PASS history and publication snapshot secret scans; private paths excluded')


if __name__ == '__main__':
    main()

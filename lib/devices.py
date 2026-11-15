import argparse
import getpass
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import warnings

import adb_status
import lab

CONFIG = lab.ROOT / 'devices.local.json'
SERVICE = 't3-android-devices'


def port(value):
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError('port must be between 1 and 65535')
    return number


def code_path(value):
    return Path('/dev/stdin') if value in {'-', '/dev/stdin'} else lab.expand(value)


def load():
    if not CONFIG.exists():
        return {'adb': None, 'devices': {}}
    return json.loads(CONFIG.read_text())


def get(config, name):
    if name not in config['devices']:
        registered = ', '.join(config['devices']) or 'none'
        raise lab.LabError(f'Unknown device {name!r}. Registered: {registered}.',
                           't3-android-lab device list', 't3-android-lab device add --help')
    return config['devices'][name]


def inventory(config):
    server, transports = adb_status.snapshot()
    adb = config['adb'] or lab.default_adb()
    found = shutil.which(os.path.expanduser(adb)) if adb else None
    return {**config, 'adb': os.path.abspath(found) if found else adb, 'adb_found': bool(found),
            'adb_source': 'saved' if config['adb'] else 'discovered' if adb else 'missing',
            'adb_server': server,
            'devices': {name: {**device, 'connection': adb_status.match(device, server, transports)}
                        for name, device in config['devices'].items()}}


def add(name, host, mode, connection_port=None, adb=None):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', name):
        raise RuntimeError('Use lowercase letters, numbers, and hyphens for a device name.')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', host):
        raise RuntimeError('Expected a Tailscale IPv4 address or DNS name, without a port.')
    config = load()
    device = {'host': host, 'port': connection_port, 'mode': mode}
    if name in config['devices'] and config['devices'][name] != device:
        raise RuntimeError('Device already exists with different settings. Remove it before replacing it.')
    if any(other != name and d['host'].lower() == host.lower() for other, d in config['devices'].items()):
        raise RuntimeError('This host is already registered. Use its existing device name.')
    config['devices'][name] = device
    if adb is not None:
        config['adb'] = lab.adb_executable(adb)
    elif not config['adb']:
        config['adb'] = lab.default_adb()
    lab.save_json(CONFIG, config)
    return {'added': name, **device, 'contacted': False}


def connect(config, name, discover=False, connection_port=None):
    device = get(config, name)
    retry = f't3-android-lab device connect {shlex.quote(name)}'
    adb = lab.adb_executable(config['adb'])
    saved_port = connection_port or device['port']
    serial = f'{device["host"]}:{saved_port}' if saved_port else None
    if not serial or not lab.adb_ready(adb, serial, attach=True):
        serial = None
        if discover:
            for candidate in lab.open_ports(device['host'], lab.ADB_PORTS):
                target = f'{device["host"]}:{candidate}'
                if lab.adb_ready(adb, target, attach=True):
                    serial = target
                    break
        if serial is None:
            raise lab.LabError(f'{name} is unavailable. Enable Tailscale and Wireless debugging. '
                               'You can also supply the connection port from the main Wireless debugging screen.',
                               retry, f'{retry} --port <connection-port>')
    device['port'] = int(serial.rsplit(':', 1)[1])
    config['adb'] = adb
    lab.save_json(CONFIG, config)
    return {'device': name, 'connected': serial, 'reverse_ports': []}


def pairing_code(code_file, prompt='Pairing code (hidden): '):
    if code_file is not None:
        code = code_file.read_text().strip()
    else:
        if not (sys.stdin.isatty() and sys.stderr.isatty()):
            raise lab.LabError('Pairing needs a code file when not running in a terminal.',
                               't3-android-lab device pair <name> --pairing-port <port> --code-file <path>',
                               't3-android-lab device pair <name> --pairing-port <port> --code-file -', exit_code=2)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                code = getpass.getpass(prompt).strip()
        except getpass.GetPassWarning:
            raise lab.LabError('Cannot hide terminal input. Use --code-file <path> or --code-file - instead.') from None
        except (EOFError, KeyboardInterrupt):
            raise lab.LabError('Pairing cancelled.') from None
    if not re.fullmatch(r'\d{6}', code):
        raise lab.LabError('The pairing code must contain exactly six digits.')
    return code


def pair(config, name, pairing_port, code_file=None):
    device = get(config, name)
    adb = lab.adb_executable(config['adb'])
    code = pairing_code(code_file, f'Pairing code for {name} ({device["host"]}:{pairing_port}, input hidden): ')
    with lab.operation():
        if load() != config:
            raise lab.LabError('Device configuration changed while entering the code. Run pairing again.')
        result = subprocess.run([adb, 'pair', f'{device["host"]}:{pairing_port}'],
                                input=code + '\n', capture_output=True, text=True, timeout=30)
        if result.returncode or 'Successfully paired' not in result.stdout:
            raise lab.LabError('ADB pairing failed. Check the pairing port and request a fresh code; keep its dialog open.',
                               't3-android-lab device pair --help')
        config['adb'] = adb
        lab.save_json(CONFIG, config)
    return {'paired': name, 'credential_printed': False}


def keep_alive(config):
    results = {}
    for name, device in config['devices'].items():
        if device['mode'] != 'managed' or not device['port']:
            continue
        try:
            results[name] = connect(config, name)
        except (RuntimeError, OSError, subprocess.SubprocessError) as error:
            results[name] = {'unavailable': str(error)}
    return {'devices': results}


def install():
    units = Path.home() / '.config/systemd/user'
    units.mkdir(parents=True, exist_ok=True)
    (units / f'{SERVICE}.service').write_text(f'''[Unit]
Description=Reconnect managed Android devices on saved ADB ports

[Service]
Type=oneshot
ExecStart={lab.unit_quote(lab.ROOT / 'bin/t3-android-lab')} device keep-alive
''')
    (units / f'{SERVICE}.timer').write_text('''[Unit]
Description=Maintain managed Android device connections

[Timer]
OnStartupSec=30s
OnUnitInactiveSec=30s
AccuracySec=5s

[Install]
WantedBy=timers.target
''')
    lab.systemctl('daemon-reload')
    lab.systemctl('enable', '--now', f'{SERVICE}.timer')
    return {'installed': f'{SERVICE}.timer', 'on_demand_excluded': True}


def parser(sub):
    root = lab.command(sub, 'device', 'Register, pair, and connect Android devices without sandbox configuration.',
                       't3-android-lab device list',
                       't3-android-lab device add lab-phone --host 100.x.x.x --mode managed',
                       't3-android-lab device add tablet --host 100.y.y.y',
                       't3-android-lab device pair tablet --pairing-port 38063 --code-file <path>',
                       't3-android-lab device connect lab-phone',
                       't3-android-lab device connect tablet --port 36000')
    commands = root.add_subparsers(dest='device_action', required=True, metavar='<action>', title='actions')
    def command(name, help, example, second=None):
        full = 't3-android-lab device ' + example
        return lab.command(commands, name, help, full,
                           't3-android-lab device ' + second if second else full + ' --json')
    command('list', 'List devices and their local ADB connection states without connecting to phones or starting ADB.', 'list')
    p = command('adb', 'Save the host ADB executable used for all registered devices.',
                'adb /path/to/platform-tools/adb')
    p.add_argument('path', metavar='<path>', help='Executable path; its symlink is preserved for SDK updates.')
    p = command('add', 'Register a device without contacting it. New devices default to on-demand.',
                'add tablet --host 100.64.0.2 --mode on-demand')
    p.add_argument('name', metavar='<name>', help='Local name using lowercase letters, numbers, and hyphens.')
    p.add_argument('--host', required=True, metavar='<tailscale-ip-or-dns>', help='Tailscale address or DNS name, without a port.')
    p.add_argument('--mode', choices=['managed', 'on-demand'], default='on-demand', help='Availability policy; default: on-demand.')
    p.add_argument('--port', type=port, metavar='<connection-port>', help='Current connection port from Wireless debugging; optional at registration.')
    p.add_argument('--adb', metavar='<path>', help='ADB executable shared by all inventory entries; default: discover on PATH or in an Android SDK.')
    p = command('remove', 'Remove local configuration; does not revoke Android pairing.', 'remove tablet')
    p.add_argument('name', metavar='<name>', help='Registered local device name. Repeating removal is safe.')
    for action in ['connect', 'disconnect', 'pair']:
        p = command(action, {'connect': 'Connect one named device; discover its ADB port if the saved port fails. Sets no app port forwards.',
                             'disconnect': 'Disconnect one named device from this ADB host.',
                             'pair': 'Pair this ADB host. Enter the code at a hidden terminal prompt, or supply --code-file.'}[action],
                    action + ' tablet' + {'pair': ' --pairing-port 38063',
                                         'connect': '', 'disconnect': ''}[action],
                    'pair tablet --pairing-port 38063 --code-file <path>' if action == 'pair' else None)
        p.add_argument('name', metavar='<name>', help='Registered local device name. See: t3-android-lab device list.')
        if action == 'connect':
            p.add_argument('--port', type=port, metavar='<connection-port>', help='Current connection port from Wireless debugging; default: saved port.')
            p.add_argument('--no-discover', dest='discover', action='store_false', help='Use only the saved port or --port; do not scan. Discovery is enabled by default for this named command.')
        if action == 'pair':
            p.add_argument('--pairing-port', type=port, required=True, metavar='<pairing-port>', help='Port in the open pairing-code dialog; different from the connection port.')
            p.add_argument('--code-file', type=code_path, metavar='<path>', help='File containing the six-digit code, or - for stdin. Required without a terminal; otherwise prompts with input hidden.')
    command('keep-alive', 'Reconnect managed devices only; never scan (timer). Always prints JSON.', 'keep-alive')
    command('install', 'Enable the managed-device reconnect timer; no Podman or T3 checkout required.', 'install')
    command('stop', 'Disable and stop the managed-device reconnect timer.', 'stop')


def dispatch(args):
    config = load()
    action = args.device_action
    if action == 'list':
        return inventory(config)
    if action == 'adb':
        config['adb'] = lab.adb_executable(args.path)
        lab.save_json(CONFIG, config)
        return {'adb': config['adb']}
    if action == 'add':
        return add(args.name, args.host, args.mode, args.port, args.adb)
    if action == 'remove':
        previous = config['devices'].pop(args.name, None)
        lab.save_json(CONFIG, config)
        return {'removed': args.name, **({'already_removed': True} if previous is None else {})}
    if action == 'connect':
        return connect(config, args.name, args.discover, args.port)
    if action == 'disconnect':
        device = get(config, args.name)
        if device['port']:
            lab.run(lab.adb_executable(config['adb']), 'disconnect', f'{device["host"]}:{device["port"]}', capture=True)
        return {'disconnected': args.name}
    if action == 'pair':
        return pair(config, args.name, args.pairing_port, args.code_file)
    if action == 'keep-alive':
        return keep_alive(config)
    if action == 'install':
        config['adb'] = lab.adb_executable(config['adb'])
        lab.save_json(CONFIG, config)
        return install()
    if action == 'stop':
        lab.systemctl('disable', '--now', f'{SERVICE}.timer')
        lab.systemctl('stop', f'{SERVICE}.service')
        return {'stopped': f'{SERVICE}.timer'}

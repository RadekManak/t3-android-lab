import argparse
import json
from pathlib import Path
import re
import subprocess

import lab

CONFIG = lab.ROOT / 'devices.local.json'
SERVICE = 't3-android-devices'


def port(value):
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError('port must be between 1 and 65535')
    return number


def load():
    if not CONFIG.exists():
        return {'adb': str(Path.home() / 'android-sdk/platform-tools/adb'), 'devices': {}}
    return json.loads(CONFIG.read_text())


def get(config, name):
    if name not in config['devices']:
        raise RuntimeError(f'Unknown device {name}. Run device list, or device add --help.')
    return config['devices'][name]


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
        config['adb'] = str(adb)
    lab.save_json(CONFIG, config)
    return {'added': name, **device, 'contacted': False}


def requested(device, explicit):
    if device['mode'] == 'on-demand' and not explicit:
        raise RuntimeError('On-demand device: use --requested only when the user explicitly requests this device.')


def connect(config, name, explicit=False, discover=False, connection_port=None):
    device = get(config, name)
    requested(device, explicit)
    adb = str(lab.expand(config['adb']))
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
            intent = ' --requested' if device['mode'] == 'on-demand' else ''
            raise RuntimeError(f'{name} is unavailable. Enable Tailscale and Wireless debugging, then use '
                               f'device connect {name} --port <connection-port>{intent}, or --discover to scan explicitly.')
    device['port'] = int(serial.rsplit(':', 1)[1])
    lab.save_json(CONFIG, config)
    return {'device': name, 'connected': serial, 'reverse_ports': []}


def pair(config, name, pairing_port, code_file, explicit=False):
    device = get(config, name)
    requested(device, explicit)
    code = code_file.read_text().strip()
    if not re.fullmatch(r'\d{6}', code):
        raise RuntimeError('The pairing code file must contain exactly six digits.')
    result = subprocess.run([str(lab.expand(config['adb'])), 'pair', f'{device["host"]}:{pairing_port}'],
                            input=code + '\n', capture_output=True, text=True, timeout=30)
    if result.returncode or 'Successfully paired' not in result.stdout:
        raise RuntimeError('ADB pairing failed. Check the pairing port and request a fresh code; keep its dialog open.')
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
    root = sub.add_parser('device', help='Onboard and connect Android devices without a T3 sandbox.')
    commands = root.add_subparsers(dest='device_action', required=True)
    def command(name, help, example):
        return commands.add_parser(name, help=help, description=help, epilog='Example: t3-android-lab device ' + example)
    command('list', 'List local configuration without contacting any device.', 'list')
    p = command('add', 'Register a device without contacting it. New devices default to on-demand.',
                'add tablet --host 100.64.0.2 --mode on-demand')
    p.add_argument('name')
    p.add_argument('--host', required=True)
    p.add_argument('--mode', choices=['managed', 'on-demand'], default='on-demand')
    p.add_argument('--port', type=port)
    p.add_argument('--adb', type=lab.expand, help='Path to this host\'s ADB executable.')
    p = command('remove', 'Remove local configuration; does not revoke Android pairing.', 'remove tablet')
    p.add_argument('name')
    for action in ['connect', 'disconnect', 'pair']:
        p = command(action, {'connect': 'Connect one named device without setting up app port forwards.',
                             'disconnect': 'Disconnect one named device from this ADB host.',
                             'pair': 'Pair this ADB host using a code file; never prints the code.'}[action],
                    action + ' tablet --requested' + {'pair': ' --pairing-port 38063 --code-file /path/to/secret',
                                                       'connect': ' --port 36000', 'disconnect': ''}[action])
        p.add_argument('name')
        p.add_argument('--requested', action='store_true', help='The user explicitly requested this on-demand device.')
        if action == 'connect':
            p.add_argument('--port', type=port, help='Current connection port from Wireless debugging.')
            p.add_argument('--discover', action='store_true', help='Explicitly scan the device for its changed ADB port.')
        if action == 'pair':
            p.add_argument('--pairing-port', type=port, required=True)
            p.add_argument('--code-file', type=lab.expand, required=True)
    command('keep-alive', 'Reconnect managed devices only; never scan. Used by the timer.', 'keep-alive')
    command('install', 'Enable the managed-device reconnect timer; no Podman or T3 checkout required.', 'install')
    command('stop', 'Disable and stop the managed-device reconnect timer.', 'stop')


def dispatch(args):
    config = load()
    action = args.device_action
    if action == 'list':
        return config
    if action == 'add':
        return add(args.name, args.host, args.mode, args.port, args.adb)
    if action == 'remove':
        config['devices'].pop(args.name, None)
        lab.save_json(CONFIG, config)
        return {'removed': args.name}
    if action == 'connect':
        return connect(config, args.name, args.requested, args.discover, args.port)
    if action == 'disconnect':
        device = get(config, args.name)
        requested(device, args.requested)
        if device['port']:
            lab.run(lab.expand(config['adb']), 'disconnect', f'{device["host"]}:{device["port"]}', capture=True)
        return {'disconnected': args.name}
    if action == 'pair':
        return pair(config, args.name, args.pairing_port, args.code_file, args.requested)
    if action == 'keep-alive':
        return keep_alive(config)
    if action == 'install':
        return install()
    if action == 'stop':
        lab.systemctl('disable', '--now', f'{SERVICE}.timer')
        lab.systemctl('stop', f'{SERVICE}.service')
        return {'stopped': f'{SERVICE}.timer'}

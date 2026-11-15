import argparse
import asyncio
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.state'
CONFIG = ROOT / 'config.local.json'
CONTAINER = 't3-a54-sandbox'
IMAGE = 'localhost/t3-a54-sandbox:local'
VOLUME = 't3-a54-sandbox-home'
SERVICE = 't3-android-lab'


def run(*args, cwd=None, capture=False, input=None, env=None, stdout=None):
    result = subprocess.run([str(a) for a in args], cwd=cwd, text=True,
                            capture_output=capture, input=input, env=env, stdout=stdout)
    if result.returncode:
        detail = result.stderr.strip() if capture else ''
        raise RuntimeError(f'Command failed ({result.returncode}): {args[0]}' + (f': {detail}' if detail else ''))
    return result.stdout.strip() if capture else None


# Kotlin daemons and ninja's nproc+2 clang jobs can exhaust small hosts during native builds.
# A capped scope is killed instead of freezing the host, and two cores keep ninja at three jobs.
NATIVE_BUILD_LIMITS = ('systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=5G', '-p', 'MemorySwapMax=1G',
                       'taskset', '-c', '0-1')


def native_build_env():
    return {**os.environ,
            'GRADLE_OPTS': r'-Dorg.gradle.jvmargs=-Xmx2g\ -XX:MaxMetaspaceSize=768m -Dorg.gradle.workers.max=2 -Dorg.gradle.parallel=false',
            'ORG_GRADLE_PROJECT_kotlin.compiler.execution.strategy': 'in-process'}


class LabError(RuntimeError):
    def __init__(self, message, *hints, exit_code=1):
        super().__init__(message)
        self.hints = hints
        self.exit_code = exit_code


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, examples=(), **kwargs):
        self.examples = examples
        epilog = 'Examples:\n' + '\n'.join('  ' + example for example in examples)
        if kwargs.get('epilog'):
            epilog += '\n\n' + kwargs['epilog']
        kwargs['epilog'] = epilog
        kwargs.setdefault('formatter_class', argparse.RawDescriptionHelpFormatter)
        super().__init__(*args, **kwargs)
        self.add_argument('--json', dest='output_format', action='store_const', const='json',
                          default=argparse.SUPPRESS, help='Print JSON (default when piped).')
        self.add_argument('--text', dest='output_format', action='store_const', const='text',
                          default=argparse.SUPPRESS, help='Print readable text (default on a terminal).')

    def error(self, message):
        self.print_usage(sys.stderr)
        example = '\nExample:\n  ' + self.examples[0] if self.examples else ''
        self.exit(2, f'Error: {message}{example}\nRun {self.prog} --help for details.\n')


def command(sub, name, help, *examples):
    return sub.add_parser(name, help=help, description=help, examples=examples)


def output(value, args):
    key = args.action + (' ' + args.device_action if args.action == 'device' else '')
    machine_only = key in {'keep-alive', 'device keep-alive'}
    output_format = getattr(args, 'output_format', None) or ('text' if sys.stdout.isatty() else 'json')
    if machine_only or output_format == 'json':
        print(json.dumps(value, ensure_ascii=False))
    else:
        from text import render
        print(render(key, value))


def expand(path):
    return Path(path).expanduser().resolve()


def default_adb():
    if found := shutil.which('adb'):
        return os.path.abspath(found)
    sdk_roots = [os.environ.get('ANDROID_HOME'), os.environ.get('ANDROID_SDK_ROOT'),
                 Path.home() / 'Android/Sdk', Path.home() / 'android-sdk']
    for root in sdk_roots:
        if root:
            candidate = Path(root).expanduser() / 'platform-tools/adb'
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return os.path.abspath(candidate)
    return None


def adb_executable(value):
    candidate = value or default_adb()
    found = shutil.which(str(Path(candidate).expanduser())) if candidate else None
    if found:
        return os.path.abspath(found)
    message = f'ADB executable is missing or not executable: {value}. ' if value else 'ADB was not found on PATH or in an Android SDK. '
    raise LabError(message + 'Install Android SDK Platform-Tools '
                   '(https://developer.android.com/tools/releases/platform-tools), then save its path.',
                   't3-android-lab device adb /path/to/platform-tools/adb')


def load_config():
    if not CONFIG.exists():
        raise LabError('Sandbox is not configured (config.local.json is missing). Device commands work without it.',
                       't3-android-lab configure --checkout ~/git/t3code --serial <tailscale-ip:connection-port>',
                       't3-android-lab device list')
    return json.loads(CONFIG.read_text())


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(json.dumps(value, indent=2) + '\n')
            handle.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def git_common(worktree):
    package = worktree / 'package.json'
    if not package.exists() or json.loads(package.read_text()).get('name') != '@t3tools/monorepo':
        raise RuntimeError(f'Not a T3 Code checkout: {worktree}')
    return expand(run('git', '-C', worktree, 'rev-parse', '--path-format=absolute', '--git-common-dir', capture=True))


def validate_worktree(worktree, config):
    if git_common(worktree) != git_common(expand(config['checkout'])):
        raise RuntimeError('This worktree is not part of the configured T3 Code repository.')


@contextmanager
def operation():
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / 'operation.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LabError('Another lab operation is running. Retry after it completes.', 't3-android-lab status')
        yield


def executable(name):
    candidate = shutil.which(name)
    if candidate:
        return candidate
    fallback = Path.home() / '.local/share/vite-plus/bin' / name
    if fallback.exists():
        return str(fallback)
    raise RuntimeError(f'Missing {name}; install it and retry.')


def systemctl(*args, capture=False):
    return run('systemctl', '--user', *args, capture=capture)


def unit_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def skill_install(directory):
    target = directory / 't3-android-lab'
    source = ROOT / 'skills/t3-android-lab'
    if target.is_symlink():
        if target.resolve() != source:
            raise RuntimeError(f'A different skill already owns {target}.')
    elif target.exists():
        raise RuntimeError(f'Refusing to overwrite {target}.')
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(source, target_is_directory=True)
    return {'skill': str(target), 'scope': 'user'}


def install(config):
    for name in ['podman', 'systemctl', 'git', 'python3']:
        executable(name)
    for name in ['egress', 'ingress', 'logs']:
        directory = STATE / name
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    units = Path.home() / '.config/systemd/user'
    quadlets = Path.home() / '.config/containers/systemd'
    units.mkdir(parents=True, exist_ok=True)
    quadlets.mkdir(parents=True, exist_ok=True)
    if subprocess.run(['podman', 'volume', 'exists', VOLUME], capture_output=True).returncode:
        run('podman', 'volume', 'create', VOLUME, capture=True)
    (quadlets / f'{SERVICE}-backend.container').write_text(f'''[Unit]
Description=Isolated T3 Code Android backend
Requires={SERVICE}-bridge.service
After={SERVICE}-bridge.service

[Container]
Image={IMAGE}
Pull=never
ContainerName={CONTAINER}
Network=none
UserNS=keep-id:uid=1000,gid=1000
User=1000:1000
DropCapability=all
NoNewPrivileges=true
ReadOnly=true
PidsLimit=256
PodmanArgs=--memory=3g --no-hosts
Tmpfs=/tmp:rw,nosuid,nodev,size=512m
Volume={VOLUME}:/home/node:U
Volume={STATE}/egress:/egress:ro,z
Volume={STATE}/ingress:/ingress:rw,z

[Service]
Restart=on-failure
RestartSec=5
StandardOutput=append:{STATE}/logs/backend.log
StandardError=inherit

[Install]
WantedBy=default.target
''')
    (units / f'{SERVICE}-bridge.service').write_text(f'''[Unit]
Description=Restricted OpenAI proxy and Android sandbox ingress

[Service]
ExecStart=/usr/bin/python3 {unit_quote(ROOT / 'lib/bridge.py')} --state-dir {unit_quote(STATE)} --port {config['backend_port']}
Restart=on-failure
RestartSec=3
MemoryMax=256M
TasksMax=96
StandardOutput=append:{STATE}/logs/bridge.log
StandardError=inherit

[Install]
WantedBy=default.target
''')
    (units / f'{SERVICE}-connect.service').write_text(f'''[Unit]
Description=Restore Android lab ADB connection and forwards

[Service]
Type=oneshot
ExecStart={unit_quote(ROOT / 'bin/t3-android-lab')} keep-alive
TimeoutStartSec=40
''')
    (units / f'{SERVICE}-connect.timer').write_text(f'''[Unit]
Description=Keep Android lab device connected

[Timer]
OnStartupSec=30s
OnUnitInactiveSec=30s
AccuracySec=5s

[Install]
WantedBy=timers.target
''')
    systemctl('daemon-reload')
    systemctl('enable', '--now', f'{SERVICE}-bridge.service', f'{SERVICE}-connect.timer')
    systemctl('restart', f'{SERVICE}-bridge.service')
    return {'installed': True, 'repository': str(ROOT), 'state': str(STATE)}


def adb_ready(adb, serial, attach=False):
    if attach:
        try:
            subprocess.run([adb, 'connect', serial], capture_output=True, timeout=15)
        except subprocess.TimeoutExpired:
            return False
    state = subprocess.run([adb, '-s', serial, 'get-state'], capture_output=True, text=True, timeout=5)
    return not state.returncode and state.stdout.strip() == 'device'


def open_ports(host, ports, timeout=1.5, concurrency=800):
    async def probe(port, gate, found):
        async with gate:
            try:
                _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
                writer.close()
                found.append(port)
            except (OSError, asyncio.TimeoutError):
                pass

    async def scan():
        gate, found = asyncio.Semaphore(concurrency), []
        await asyncio.gather(*(probe(port, gate, found) for port in ports))
        return sorted(found)

    return asyncio.run(scan())


# Wireless debugging picks a new port after each reboot or toggle, and mDNS does not cross Tailscale.
ADB_PORTS = range(30000, 61000)


def discover_serial(config):
    host = config['serial'].rsplit(':', 1)[0]
    # adbd often reopens its port a few seconds after a network change, so look twice.
    for attempt in range(2):
        if attempt:
            time.sleep(10)
        for port in open_ports(host, ADB_PORTS):
            serial = f'{host}:{port}'
            if adb_ready(config['adb'], serial, attach=True):
                save_json(CONFIG, {**load_config(), 'serial': serial})
                return serial
    return None


def connect(config, discover=True):
    adb, serial = config['adb'], config['serial']
    if not adb_ready(adb, serial) and not adb_ready(adb, serial, attach=True):
        serial = discover_serial(config) if discover else None
        if serial is None:
            raise LabError('Phone is not connected. Turn on Wireless debugging and keep it on Tailscale; '
                           + ('no port on the phone accepted ADB.' if discover else 'run connect to find its new port.'),
                           't3-android-lab connect')
        config['serial'] = serial
    forwards = run(adb, '-s', serial, 'reverse', '--list', capture=True).splitlines()
    for port in [config['metro_port'], config['backend_port']]:
        if not any(line.split()[-2:] == [f'tcp:{port}', f'tcp:{port}'] for line in forwards):
            run(adb, '-s', serial, 'reverse', f'tcp:{port}', f'tcp:{port}', capture=True)
    return {'connected': serial, 'reverse_ports': [config['metro_port'], config['backend_port']]}


def build(worktree, config, owner=None):
    check_owner(owner, worktree)
    validate_worktree(worktree, config)
    marker = worktree / '.t3/setup-running'
    if marker.exists():
        raise RuntimeError('T3 worktree setup is marked as running. Wait for setup completion before building.')
    context = STATE / 'image'
    context.mkdir(parents=True, exist_ok=True)
    codex = expand(config['codex_binary'])
    with codex.open('rb') as handle:
        if handle.read(4) != b'\x7fELF':
            raise RuntimeError('codex_binary must resolve to the Linux standalone Codex binary, not a shell or JavaScript wrapper.')
    if not (worktree / 'apps/server/node_modules/effect').exists():
        raise RuntimeError('Dependencies are missing. Run vp i --frozen-lockfile in the worktree first.')
    package = json.loads((worktree / 'package.json').read_text())
    node_version = package['engines']['node']
    if not re.fullmatch(r'\d+\.\d+\.\d+', node_version):
        raise RuntimeError('Expected an exact Node version in the T3 Code package.json engines.node field.')
    config_file = STATE / 'build.mjs'
    vite = (worktree / 'node_modules/vite-plus/dist/index.js').as_uri()
    pack_api = (worktree / 'node_modules/vite-plus/dist/pack.js').as_uri()
    config_file.write_text('import {loadConfigFromFile} from ' + json.dumps(vite) + ';\n'
        + 'import {build} from ' + json.dumps(pack_api) + ';\n'
        + 'const loaded = await loadConfigFromFile({command:"build",mode:"production"}, '
        + json.dumps(str(worktree / 'apps/server/vite.config.ts')) + ');\n'
        + 'const pack=loaded.config.pack;\nawait build({...pack,config:false,outDir:'
        + json.dumps(str(context / 'dist')) + ',sourcemap:false,define:Object.fromEntries(Object.entries(pack.define).map(([key,value])=>[key,key.includes("CHANNEL")?value:JSON.stringify("")]))});\n')
    log = STATE / 'logs/build.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('w') as handle:
        result = subprocess.run([executable('node'), str(config_file)], cwd=worktree / 'apps/server', stdout=handle, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'Backend bundle failed; see {log}.')
    selection = ('import {selectCliRuntimeExternalDependencies as select} from '
        + json.dumps((worktree / 'scripts/lib/cli-external-packages.ts').as_uri())
        + '; import fs from "node:fs"; const deps=JSON.parse(fs.readFileSync("package.json","utf8")).dependencies; console.log(JSON.stringify(select(deps)));')
    deps = json.loads(run(executable('node'), '--input-type=module', '-e', selection, cwd=worktree / 'apps/server', capture=True))
    pinned = {name: json.loads((worktree / 'apps/server/node_modules' / name / 'package.json').read_text())['version'] for name in deps}
    (context / 'package.json').write_text(json.dumps({'private': True, 'type': 'module', 'dependencies': pinned}, indent=2))
    shutil.copy2(codex, context / 'codex')
    for name in ['Containerfile', 'start.sh']:
        shutil.copy2(ROOT / 'container' / name, context / name)
    with log.open('a') as handle:
        result = subprocess.run(['podman', 'build', '--build-arg', 'NODE_VERSION=' + node_version, '--label', 't3.android-lab.worktree=' + str(worktree), '-t', IMAGE, str(context)], stdout=handle, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'Container image build failed; see {log}.')
    return {'image': IMAGE, 'worktree': str(worktree), 'log': str(log)}


RESERVATION_HOURS = 2
MAX_RESERVATION_HOURS = 12


def reservation():
    """The live reservation, or None when there is none or it has expired."""
    path = STATE / 'session.json'
    if not path.exists():
        return None
    session = json.loads(path.read_text())
    return session if session.get('expires_at', 0) > time.time() else None


def hours(value):
    value = float(value)
    if not 0 < value <= MAX_RESERVATION_HOURS:
        raise argparse.ArgumentTypeError(f'choose between 0 and {MAX_RESERVATION_HOURS} hours')
    return value


def reserved_by_other(session):
    until = time.strftime('%H:%M', time.localtime(session['expires_at']))
    return LabError(f'Phone is reserved by {session["owner"]} for {session["worktree"]} until {until}. '
                    'Ask that T3 thread to release it, or wait: t3-android-lab free prints when it is available.',
                    't3-android-lab status', 't3-android-lab free')


def claim(worktree, owner, duration=RESERVATION_HOURS):
    previous = reservation()
    if previous and previous['owner'] != owner:
        raise reserved_by_other(previous)
    if previous and previous['worktree'] != str(worktree):
        raise LabError('This owner already reserved another worktree. Release it before switching worktrees.',
                       f't3-android-lab release --owner {shlex.quote(owner)}')
    now = time.time()
    save_json(STATE / 'session.json', {'owner': owner, 'worktree': str(worktree), 'reserved_at': now,
                                       'expires_at': now + duration * 3600})


def extend(owner, duration):
    session = reservation()
    if session is None or session['owner'] != owner:
        raise LabError('Only a live reservation can be extended, by its owner. Run prepare to reserve again.',
                       f't3-android-lab prepare <worktree> --owner {shlex.quote(owner)} --hours {duration:g}')
    session['expires_at'] = time.time() + duration * 3600
    save_json(STATE / 'session.json', session)
    return describe(session)


def describe(session):
    return {**session, 'expires_in_minutes': round((session['expires_at'] - time.time()) / 60)} if session else None


def free():
    # A t3-watch check: silent while reserved, one line once the phone is available.
    return '' if reservation() else json.dumps({'free': True})


def check_owner(owner, worktree=None):
    session = reservation()
    if session and (session['owner'] != owner or (worktree is not None and session['worktree'] != str(worktree))):
        raise reserved_by_other(session)


def stop_metro():
    metro_active = subprocess.run(['systemctl', '--user', 'is-active', '--quiet', f'{SERVICE}-metro.service']).returncode == 0
    if metro_active:
        systemctl('stop', f'{SERVICE}-metro.service')


def stop(owner=None):
    check_owner(owner)
    for suffix in ['connect.timer', 'connect.service', 'metro.service', 'backend.service', 'bridge.service']:
        unit = f'{SERVICE}-{suffix}'
        loaded = systemctl('show', unit, '--property=LoadState', '--value', capture=True)
        if loaded != 'not-found':
            systemctl('stop', unit)
            if systemctl('show', unit, '--property=ActiveState', '--value', capture=True) == 'failed':
                systemctl('reset-failed', unit)
    (STATE / 'session.json').unlink(missing_ok=True)
    return {'stopped': True, 'data_preserved': True}


def register_demo_project():
    # The T3 CLI refuses to write state while a server owns it, so register in a one-off container.
    demo = subprocess.run(['podman','run','--rm','--network=none','--userns=keep-id:uid=1000,gid=1000','--user=1000:1000',
                           '--cap-drop=all','--security-opt=no-new-privileges','-v',f'{VOLUME}:/home/node:U',IMAGE,
                           'node','/opt/t3/dist/bin.mjs','project','add','--base-dir','/home/node/t3-state','--title','Sandbox Demo','/home/node/projects/demo'],
                          capture_output=True,text=True)
    message = demo.stdout + demo.stderr
    if demo.returncode and 'ProjectAlreadyExistsError:' not in message:
        raise RuntimeError('Could not register the synthetic demo project: ' + message.strip())


def prepare(worktree, owner, config, skip_native=False, duration=RESERVATION_HOURS):
    validate_worktree(worktree, config)
    claim(worktree, owner, duration)
    stop_metro()
    build(worktree, config, owner)
    systemctl('start', f'{SERVICE}-bridge.service')
    systemctl('stop', f'{SERVICE}-backend.service')
    register_demo_project()
    systemctl('start', f'{SERVICE}-backend.service')
    systemctl('start', f'{SERVICE}-connect.timer')
    connect(config)
    units = Path.home() / '.config/systemd/user'
    (units / f'{SERVICE}-metro.service').write_text(f'''[Unit]
Description=T3 Android Metro for reserved worktree

[Service]
WorkingDirectory={str(worktree / 'apps/mobile').replace('%', '%%')}
Environment=REACT_NATIVE_PACKAGER_HOSTNAME=127.0.0.1
Environment=PATH={Path(executable('vp')).parent}:{Path.home() / ".local/share/vite-plus/fallback-bin"}:/usr/local/bin:/usr/bin:/bin
ExecStart={unit_quote(executable('vp'))} run dev:client --max-workers 2 --port {config['metro_port']}
Restart=on-failure
RestartSec=5
StandardOutput=append:{STATE}/logs/metro.log
StandardError=inherit
''')
    systemctl('daemon-reload')
    systemctl('restart', f'{SERVICE}-metro.service')
    if not skip_native:
        run(*NATIVE_BUILD_LIMITS, executable('vp'), 'run', 'mobile:install', 'android', config['serial'], cwd=worktree,
            env=native_build_env(), stdout=sys.stderr)
    return {'owner': owner, 'worktree': str(worktree), 'metro': f'http://127.0.0.1:{config["metro_port"]}',
            'backend': f'http://127.0.0.1:{config["backend_port"]}', 'next': 'Use device_open and launch with its returned AgentDevice command. Run pair if this backend is not saved on the phone.'}


def auth_import(path):
    auth = json.loads(path.read_text())
    if not auth.get('tokens') or auth.get('OPENAI_API_KEY'):
        raise RuntimeError('Expected a ChatGPT Codex login with tokens and no API key.')
    selected = {key: auth[key] for key in ['auth_mode', 'last_refresh', 'tokens'] if key in auth}
    run('podman', 'exec', '-i', CONTAINER, 'sh', '-c', 'umask 077; mkdir -p "$CODEX_HOME"; cat > "$CODEX_HOME/auth.json"', input=json.dumps(selected), capture=True)
    return {'imported': 'Codex ChatGPT authentication only', 'destination': 'isolated container home volume'}


def pair(command, config, owner):
    check_owner(owner)
    url = f'http://127.0.0.1:{config["backend_port"]}'
    response = run('podman', 'exec', CONTAINER, 'node', '/opt/t3/dist/bin.mjs', 'auth', 'pairing', 'create', '--base-dir', '/home/node/t3-state', '--base-url', url, '--ttl', '15m', '--label', 'android-lab', capture=True)
    pairing = next((line[len('Pair URL: '):] for line in response.splitlines() if line.startswith('Pair URL: ')), None)
    if not pairing:
        raise RuntimeError('Backend did not return a pairing credential.')
    deep = 't3code-dev://connections/new?' + urllib.parse.urlencode({'pairingUrl': pairing, 'autoConnect': '1'})
    result = subprocess.run([str(command), 'open', 'com.t3tools.t3code.dev', deep], capture_output=True)
    if result.returncode:
        raise RuntimeError('AgentDevice could not open the pairing route. Inspect the Device panel before retrying.')
    return {'paired_route_opened': True, 'credential_printed': False}


def wait_ready(config):
    target = f'http://127.0.0.1:{config["metro_port"]}/status'
    for _ in range(60):
        try:
            with urllib.request.urlopen(target, timeout=2) as response:
                if response.read() == b'packager-status:running':
                    return {'metro_ready': target}
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError(f'Metro did not become ready; inspect {STATE / "logs/metro.log"}.')


PHONE_WOL = '/data/local/tmp/t3-wol'


def wake(config, mac):
    if not re.fullmatch(r'(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}', mac):
        raise RuntimeError('Expected a MAC address such as aa:bb:cc:dd:ee:ff.')
    serial = connect(config)['connected']
    adb = [config['adb'], '-s', serial]
    binary = STATE / 'wol-arm64'
    source = ROOT / 'tools/wol'
    if not binary.exists() or binary.stat().st_mtime < max(f.stat().st_mtime for f in source.iterdir()):
        run(executable('go'), 'build', '-trimpath', '-ldflags=-s -w', '-o', binary, '.', cwd=source,
            env={**os.environ, 'CGO_ENABLED': '0', 'GOOS': 'linux', 'GOARCH': 'arm64'})
    local = hashlib.sha256(binary.read_bytes()).hexdigest()
    remote = subprocess.run([*adb, 'shell', f'sha256sum {PHONE_WOL} 2>/dev/null'], capture_output=True, text=True).stdout.split()
    if not remote or remote[0] != local:
        run(*adb, 'push', binary, PHONE_WOL, capture=True)
        run(*adb, 'shell', f'chmod 755 {PHONE_WOL}', capture=True)
    # 255.255.255.255 may leave through the VPN, so also target the Wi-Fi subnet's own broadcast address.
    wifi = run(*adb, 'shell', 'ip -4 addr show wlan0', capture=True)
    broadcast = re.search(r'\bbrd (\S+)', wifi)
    if broadcast is None:
        raise RuntimeError('The phone is not on Wi-Fi, so it cannot reach a local network to wake.')
    sent = run(*adb, 'shell', PHONE_WOL, mac, broadcast.group(1), capture=True)
    return {'woke': mac, 'via': serial, 'broadcast': broadcast.group(1), 'result': sent}


def keep_alive(config):
    # The timer only maintains a reserved phone on its saved port; scanning costs megabytes, so agents ask for it.
    path = STATE / 'session.json'
    if not path.exists():
        return {'idle': True}
    if reservation() is None:
        expired = json.loads(path.read_text())
        stop_metro()
        path.unlink()
        return {'expired': expired['owner']}
    return connect(config, discover=False)


def status(config):
    return {'repository': str(ROOT), 'session': describe(reservation()),
            'serial': config['serial'], 'backend_port': config['backend_port'], 'metro_port': config['metro_port'],
            'container': run('podman', 'ps', '-a', '--filter', f'name=^{CONTAINER}$', '--format', '{{.State}}', capture=True) or 'absent',
            'services': {name: subprocess.run(['systemctl', '--user', 'is-active', f'{SERVICE}-{name}.service'],capture_output=True,text=True).stdout.strip() for name in ['backend','bridge','metro']}}


def make_parser():
    parser = Parser(prog='t3-android-lab', description='Remote Android debugging and an optional isolated T3 Code sandbox.',
                    examples=('t3-android-lab device list', 't3-android-lab device connect lab-phone',
                              't3-android-lab device connect tablet --port 36000',
                              't3-android-lab status', 't3-android-lab prepare . --owner <thread-id>',
                              't3-android-lab release --owner <thread-id>'),
                    epilog='Output:\n  Text on a terminal; one JSON object when piped or with --json.\n'
                           '  Agents and scripts that parse output should always pass --json.\n'
                           '  The last --json or --text flag wins, at any command level.\n'
                           '  free and timer commands keep their JSON output; verify prints PASS lines.\n'
                           '  Errors go to stderr. Exit codes: 0 success, 1 failure, 2 usage error.\n\n'
                           'Run t3-android-lab <command> --help for options and examples.')
    sub = parser.add_subparsers(dest='action', required=True, metavar='<command>', title='commands')
    import devices
    devices.parser(sub)
    command(sub, 'status', 'Show the sandbox reservation, configured phone, container, and services.',
            't3-android-lab status', 't3-android-lab status --json')
    command(sub, 'connect', 'Restore sandbox ADB forwards; scan if the saved connection port fails.',
            't3-android-lab connect', 't3-android-lab connect --json')
    p = command(sub, 'prepare', 'Reserve the phone and prepare a T3 worktree for testing.',
                't3-android-lab prepare . --owner <thread-id>',
                't3-android-lab prepare ~/git/t3code-wt --owner <thread-id> --hours 3 --skip-native')
    p.add_argument('worktree', nargs='?', default=Path.cwd(), type=expand, metavar='<worktree>', help='T3 Code worktree; default: current directory.')
    p.add_argument('--owner', required=True, metavar='<thread-id>', help='Stable reservation owner, usually your T3 thread ID.')
    p.add_argument('--hours', type=hours, default=RESERVATION_HOURS, help=f'Reservation length; default: {RESERVATION_HOURS} hours, maximum: {MAX_RESERVATION_HOURS}. Renew with extend.')
    p.add_argument('--skip-native', action='store_true', help='Keep the installed native client. Use only when its native fingerprint matches this worktree.')
    command(sub, 'wait-ready', 'Wait up to one minute for Metro to be ready.',
            't3-android-lab wait-ready', 't3-android-lab wait-ready --json')
    p = command(sub, 'pair', 'Open a sandbox pairing route using the bound AgentDevice command. For ADB pairing, use device pair.',
                't3-android-lab pair --owner <thread-id> --agent-device <returned-command>',
                't3-android-lab pair --owner <thread-id> --agent-device <returned-command> --json')
    p.add_argument('--agent-device', required=True, type=expand, metavar='<returned-command>', help='Exact bound executable returned by device_open.')
    p.add_argument('--owner', required=True, metavar='<thread-id>', help='Current reservation owner.')
    p = command(sub, 'extend', 'Extend this owner reservation from now.',
                't3-android-lab extend --owner <thread-id> --hours 2',
                't3-android-lab extend --owner <thread-id> --hours 4')
    p.add_argument('--owner', required=True, metavar='<thread-id>', help='Current reservation owner.')
    p.add_argument('--hours', type=hours, default=RESERVATION_HOURS, help=f'Hours from now; default: {RESERVATION_HOURS}, maximum: {MAX_RESERVATION_HOURS}.')
    p = command(sub, 'release', 'Release this owner reservation and stop its Metro.',
                't3-android-lab release --owner <thread-id>', 't3-android-lab release --owner <thread-id> --json')
    p.add_argument('--owner', required=True, metavar='<thread-id>', help='Current reservation owner.')
    command(sub, 'free', 'Watch check: print JSON only when unreserved; otherwise print nothing. Use status for a summary.',
            't3-android-lab free', 't3-android-lab free --json')
    p = command(sub, 'wake', "Send a Wake-on-LAN packet from the sandbox phone onto its Wi-Fi network.",
                't3-android-lab wake aa:bb:cc:dd:ee:ff', 't3-android-lab wake aa:bb:cc:dd:ee:ff --json')
    p.add_argument('mac', metavar='<mac>', help='MAC address of the computer to wake.')
    p = command(sub, 'build', 'Build a sanitized backend image from a T3 worktree.',
                't3-android-lab build .', 't3-android-lab build ~/git/t3code-wt --owner <thread-id>')
    p.add_argument('worktree', nargs='?', default=Path.cwd(), type=expand, metavar='<worktree>', help='T3 Code worktree; default: current directory.')
    p.add_argument('--owner', metavar='<thread-id>', help='Required when this thread owns an active reservation.')
    p = command(sub, 'stop', 'Stop lab services and the reservation timer; preserve data and credentials.',
                't3-android-lab stop', 't3-android-lab stop --owner <thread-id>')
    p.add_argument('--owner', metavar='<thread-id>', help='Required when this thread owns an active reservation.')
    p = command(sub, 'configure', 'Save sandbox configuration. Device-only commands need no sandbox configuration.',
                't3-android-lab configure --checkout ~/git/t3code --serial 100.x.x.x:35000',
                't3-android-lab configure --checkout ~/git/t3code --serial 100.x.x.x:35000 --metro-port 8082')
    p.add_argument('--checkout', required=True, type=expand, metavar='<worktree>', help='T3 Code source checkout.')
    p.add_argument('--serial', required=True, metavar='<host:connection-port>', help='Sandbox phone wireless ADB address; not the pairing port.')
    p.add_argument('--adb', metavar='<path>', help='ADB executable; default: saved device inventory path, then PATH or an Android SDK.')
    p.add_argument('--codex-binary', default=shutil.which('codex') or str(Path.home() / '.local/bin/codex'), type=expand, metavar='<path>', help='Standalone Linux Codex ELF executable; default: codex on PATH, then ~/.local/bin/codex.')
    p.add_argument('--backend-port', type=int, default=13775, help='Host backend port; default: 13775.')
    p.add_argument('--metro-port', type=int, default=8081, help='Metro port; default: 8081.')
    command(sub, 'install', 'Install sandbox user services and the reservation reconnect timer.',
            't3-android-lab install', 't3-android-lab install --json')
    p = command(sub, 'skill-install', 'Install the skill once in a global user skill directory.',
                't3-android-lab skill-install', 't3-android-lab skill-install --directory ~/.codex/skills')
    p.add_argument('--directory', type=expand, default=Path.home() / '.agents/skills', metavar='<path>', help='User skill directory; default: ~/.agents/skills.')
    p = command(sub, 'auth-import', 'Copy only a ChatGPT Codex login into the sandbox.',
                't3-android-lab auth-import ~/.codex/auth.json', 't3-android-lab auth-import <path> --json')
    p.add_argument('file', type=expand, metavar='<path>', help='Existing Codex ChatGPT login file; never prints credentials.')
    command(sub, 'verify', 'Check actual sandbox isolation without inference. Always prints PASS lines, including with --json.',
            't3-android-lab verify', 't3-android-lab verify --text')
    command(sub, 'keep-alive', 'Reconnect a reserved phone on its saved port without scanning (timer). Always prints JSON.',
            't3-android-lab keep-alive', 't3-android-lab keep-alive --json')
    return parser


def main():
    args = make_parser().parse_args()
    import devices
    def emit(value):
        output(value, args)
    try:
        if args.action == 'configure':
            git_common(args.checkout)
            if not re.fullmatch(r'[A-Za-z0-9.\-]+:[0-9]{1,5}', args.serial):
                raise RuntimeError('Expected a host:port wireless ADB serial.')
            if not all(1024 <= port <= 65535 for port in [args.backend_port,args.metro_port]) or args.backend_port == args.metro_port:
                raise RuntimeError('Metro and backend ports must be distinct and between 1024 and 65535.')
            values = {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items() if key not in {'action', 'output_format'}}
            values['adb'] = adb_executable(args.adb or devices.load()['adb'])
            save_json(CONFIG, values)
            emit({'configured': str(CONFIG)})
            return
        if args.action == 'skill-install':
            emit(skill_install(args.directory)); return
        if args.action == 'device':
            if args.device_action in {'list', 'pair'}:
                emit(devices.dispatch(args))
            else:
                with operation():
                    emit(devices.dispatch(args))
            return
        config = load_config()
        if args.action == 'verify':
            run(sys.executable, ROOT / 'lib/verify.py'); return
        if args.action == 'connect':
            emit(connect(config)); return
        if args.action == 'keep-alive':
            emit(keep_alive(config)); return
        if args.action == 'status':
            emit(status(config)); return
        if args.action == 'free':
            if line := free(): print(line)
            return
        if args.action == 'wake':
            emit(wake(config, args.mac)); return
        if args.action == 'extend':
            emit(extend(args.owner, args.hours)); return
        if args.action == 'wait-ready':
            emit(wait_ready(config)); return
        with operation():
            if args.action == 'install': result = install(config)
            elif args.action == 'build': result = build(args.worktree, config, args.owner)
            elif args.action == 'prepare': result = prepare(args.worktree, args.owner, config, args.skip_native, args.hours)
            elif args.action == 'stop': result = stop(args.owner)
            elif args.action == 'auth-import': result = auth_import(args.file)
            elif args.action == 'pair': result = pair(args.agent_device, config, args.owner)
            elif args.action == 'release':
                session = STATE / 'session.json'
                if not session.exists(): result = {'released': True, 'already_released': True}
                else:
                    previous = json.loads(session.read_text())
                    if reservation() and previous['owner'] != args.owner: raise reserved_by_other(previous)
                    stop_metro()
                    session.unlink()
                    result = {'released': True, 'owner': args.owner}
            emit(result)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print('Error: ' + str(error), file=sys.stderr)
        if isinstance(error, LabError):
            for hint in error.hints:
                print('  Try: ' + hint, file=sys.stderr)
        sys.exit(error.exit_code if isinstance(error, LabError) else 1)

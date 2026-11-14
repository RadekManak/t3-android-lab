import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
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


def run(*args, cwd=None, capture=False, input=None, env=None):
    result = subprocess.run([str(a) for a in args], cwd=cwd, text=True,
                            capture_output=capture, input=input, env=env)
    if result.returncode:
        detail = result.stderr.strip() if capture else ''
        raise RuntimeError(f'Command failed ({result.returncode}): {args[0]}' + (f': {detail}' if detail else ''))
    return result.stdout.strip() if capture else None


# Default native builds exhaust this 12 GB host: one Kotlin daemon per Kotlin version, and ninja runs nproc+2 clang jobs.
# A capped scope is killed instead of freezing the host, and two cores keep ninja at three jobs.
NATIVE_BUILD_LIMITS = ('systemd-run', '--user', '--scope', '--quiet', '-p', 'MemoryMax=5G', '-p', 'MemorySwapMax=1G',
                       'taskset', '-c', '0-1')


def native_build_env():
    return {**os.environ,
            'GRADLE_OPTS': r'-Dorg.gradle.jvmargs=-Xmx2g\ -XX:MaxMetaspaceSize=768m -Dorg.gradle.workers.max=2 -Dorg.gradle.parallel=false',
            'ORG_GRADLE_PROJECT_kotlin.compiler.execution.strategy': 'in-process'}


def output(value):
    print(json.dumps(value, ensure_ascii=False))


def expand(path):
    return Path(path).expanduser().resolve()


def load_config():
    if not CONFIG.exists():
        raise RuntimeError('Run t3-android-lab configure --checkout <t3code> --serial <tailscale-ip:adb-port> first.')
    return json.loads(CONFIG.read_text())


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.new')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)


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
            raise RuntimeError('Another lab operation is running. Retry after it completes.')
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


def worktree_setup(worktree, config):
    validate_worktree(worktree, config)
    target = worktree / '.agents/skills/t3-android-lab'
    source = ROOT / 'skills/t3-android-lab'
    if target.is_symlink():
        if target.resolve() != source:
            raise RuntimeError(f'A different skill already owns {target}.')
    elif target.exists():
        raise RuntimeError(f'Refusing to overwrite {target}.')
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(source, target_is_directory=True)
    exclude = git_common(worktree) / 'info/exclude'
    exclude.parent.mkdir(exist_ok=True)
    contents = exclude.read_text() if exclude.exists() else ''
    line = '/.agents/skills/t3-android-lab'
    if line not in contents.splitlines():
        with exclude.open('a') as handle:
            handle.write(('\n' if contents and not contents.endswith('\n') else '') + line + '\n')
    return {'worktree': str(worktree), 'skill': str(target)}


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
ExecStart={unit_quote(ROOT / 'bin/t3-android-lab')} connect
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


def connect(config):
    adb, serial = config['adb'], config['serial']
    state = subprocess.run([adb, '-s', serial, 'get-state'], capture_output=True, text=True, timeout=5)
    if state.returncode or state.stdout.strip() != 'device':
        subprocess.run([adb, 'connect', serial], check=True, capture_output=True, timeout=15)
        result = subprocess.run([adb, '-s', serial, 'get-state'], check=True, capture_output=True, text=True, timeout=5)
        if result.stdout.strip() != 'device':
            raise RuntimeError('Phone is not connected. Check Tailscale and its Wireless debugging connection port.')
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


def claim(worktree, owner):
    path = STATE / 'session.json'
    previous = json.loads(path.read_text()) if path.exists() else None
    if previous and previous['owner'] != owner:
        raise RuntimeError(f'Phone is reserved by {previous["owner"]} in {previous["worktree"]}. That owner must release it first.')
    if previous and previous['worktree'] != str(worktree):
        raise RuntimeError('This owner already reserved another worktree. Release it before switching worktrees.')
    save_json(path, {'owner': owner, 'worktree': str(worktree)})


def check_owner(owner, worktree=None):
    path = STATE / 'session.json'
    if path.exists():
        session = json.loads(path.read_text())
        if session['owner'] != owner or (worktree is not None and session['worktree'] != str(worktree)):
            raise RuntimeError('Phone is reserved by ' + session['owner'] + '; release it or use the owning thread and worktree.')


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


def prepare(worktree, owner, config, skip_native=False):
    validate_worktree(worktree, config)
    claim(worktree, owner)
    worktree_setup(worktree, config)
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
            env=native_build_env())
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


def status(config):
    session = STATE / 'session.json'
    return {'repository': str(ROOT), 'session': json.loads(session.read_text()) if session.exists() else None,
            'serial': config['serial'], 'backend_port': config['backend_port'], 'metro_port': config['metro_port'],
            'container': run('podman', 'ps', '-a', '--filter', f'name=^{CONTAINER}$', '--format', '{{.State}}', capture=True) or 'absent',
            'services': {name: subprocess.run(['systemctl', '--user', 'is-active', f'{SERVICE}-{name}.service'],capture_output=True,text=True).stdout.strip() for name in ['backend','bridge','metro']}}


def main():
    parser = argparse.ArgumentParser(description='Project-scoped T3 Code Android sandbox.', epilog='Examples: t3-android-lab status; t3-android-lab prepare . --owner <thread-id>')
    sub = parser.add_subparsers(dest='action', required=True)
    def command(name, help, example):
        return sub.add_parser(name, help=help, description=help, epilog='Example: ' + example)
    p = command('configure', 'Save this machine configuration.', 't3-android-lab configure --checkout ~/git/t3code --serial 100.x.x.x:38797')
    p.add_argument('--checkout', required=True, type=expand)
    p.add_argument('--serial', required=True)
    p.add_argument('--adb', default=str(Path.home() / 'android-sdk/platform-tools/adb'), type=expand)
    p.add_argument('--codex-binary', default=shutil.which('codex') or str(Path.home() / '.local/bin/codex'), type=expand)
    p.add_argument('--backend-port', type=int, default=13775)
    p.add_argument('--metro-port', type=int, default=8081)
    command('install', 'Install user services and the reconnect timer.', 't3-android-lab install')
    for name in ['worktree-setup','build','prepare']:
        p = command(name, {'worktree-setup':'Link the skill only into a registered T3 worktree.','build':'Build a sanitized backend image from a T3 worktree.','prepare':'Reserve the phone and prepare this worktree for testing.'}[name], f't3-android-lab {name} .' + (' --owner <thread-id>' if name=='prepare' else ''))
        p.add_argument('worktree', nargs='?', default=Path.cwd(), type=expand)
        if name == 'prepare':
            p.add_argument('--owner', required=True)
            p.add_argument('--skip-native', action='store_true', help='Keep the installed native client instead of installing or building one. Use for JS-only changes when no cached client matches.')
        if name == 'build':
            p.add_argument('--owner', help='Required when this thread owns an active reservation.')
    p = command('release', 'Release this owner reservation and stop its Metro.', 't3-android-lab release --owner <thread-id>')
    p.add_argument('--owner', required=True)
    p = command('stop', 'Stop lab services and reconnect timer; preserve data and credentials.', 't3-android-lab stop')
    p.add_argument('--owner', help='Required when this thread owns an active reservation.')
    p = command('auth-import', 'Copy only a ChatGPT Codex login into the sandbox.', 't3-android-lab auth-import ~/.codex/auth.json')
    p.add_argument('file', type=expand)
    p = command('pair', 'Open a fresh sandbox pairing route using the bound AgentDevice command.', 't3-android-lab pair --agent-device <returned-command>')
    p.add_argument('--agent-device', required=True, type=expand)
    p.add_argument('--owner', required=True)
    for name, help in [('verify','Check actual sandbox isolation without running inference.'),('connect','Restore the saved ADB connection and forwards.'),('status','Show the owner, container, and services.'),('wait-ready','Wait up to one minute for Metro to be ready.')]:
        command(name, help, f't3-android-lab {name}')
    args = parser.parse_args()
    try:
        if args.action == 'configure':
            git_common(args.checkout)
            if not re.fullmatch(r'[A-Za-z0-9.\-]+:[0-9]{1,5}', args.serial):
                raise RuntimeError('Expected a host:port wireless ADB serial.')
            if not all(1024 <= port <= 65535 for port in [args.backend_port,args.metro_port]) or args.backend_port == args.metro_port:
                raise RuntimeError('Metro and backend ports must be distinct and between 1024 and 65535.')
            values = {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items() if key != 'action'}
            save_json(CONFIG, values)
            output({'configured': str(CONFIG)})
            return
        config = load_config()
        if args.action == 'verify':
            run(sys.executable, ROOT / 'lib/verify.py'); return
        if args.action == 'connect':
            output(connect(config)); return
        if args.action == 'status':
            output(status(config)); return
        if args.action == 'wait-ready':
            output(wait_ready(config)); return
        with operation():
            if args.action == 'install': result = install(config)
            elif args.action == 'worktree-setup': result = worktree_setup(args.worktree, config)
            elif args.action == 'build': result = build(args.worktree, config, args.owner)
            elif args.action == 'prepare': result = prepare(args.worktree, args.owner, config, args.skip_native)
            elif args.action == 'stop': result = stop(args.owner)
            elif args.action == 'auth-import': result = auth_import(args.file)
            elif args.action == 'pair': result = pair(args.agent_device, config, args.owner)
            elif args.action == 'release':
                session = STATE / 'session.json'
                if not session.exists(): result = {'released': True, 'already_released': True}
                else:
                    previous = json.loads(session.read_text())
                    if previous['owner'] != args.owner: raise RuntimeError('Reservation belongs to ' + previous['owner'])
                    stop_metro()
                    session.unlink()
                    result = {'released': True, 'owner': args.owner}
            output(result)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print('Error: ' + str(error), file=sys.stderr)
        sys.exit(1)

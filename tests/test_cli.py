"""Exercise the real CLI using an isolated inventory, with no device or service access."""
import argparse
import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import lab
from text import render


class CliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        shutil.copytree(lab.ROOT / 'lib', self.root / 'lib', ignore=shutil.ignore_patterns('__pycache__'))
        (self.root / 'bin').mkdir()
        shutil.copyfile(lab.ROOT / 'bin/t3-android-lab', self.root / 'bin/t3-android-lab')
        self.command = [sys.executable, str(self.root / 'bin/t3-android-lab')]
        closed_server = socket.socket()
        closed_server.bind(('127.0.0.1', 0))
        self.addCleanup(closed_server.close)
        (self.root / 'home').mkdir()
        self.environment = {**os.environ, 'HOME': str(self.root / 'home'),
                            'ADB_SERVER_SOCKET': f'tcp:127.0.0.1:{closed_server.getsockname()[1]}'}
        adb = self.root / 'bin/adb'
        adb.write_text('#!' + sys.executable + '\nfrom pathlib import Path\n'
                       f'Path({str(self.root / "adb-invoked")!r}).touch()\nraise SystemExit(1)\n')
        adb.chmod(0o700)
        self.environment['PATH'] = str(self.root / 'bin') + os.pathsep + self.environment['PATH']

    def cli(self, *args, **kwargs):
        return subprocess.run([*self.command, *args], capture_output=True, text=True, timeout=10,
                              env=self.environment, **kwargs)

    def register(self):
        result = self.cli('device', 'add', 'tablet', '--host', '100.64.0.2')
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def tty(self, *args):
        reader, writer = pty.openpty()
        try:
            process = subprocess.Popen([*self.command, *args], stdout=writer, stderr=subprocess.PIPE, env=self.environment)
            os.close(writer)
            writer = None
            output = b''
            while True:
                try:
                    chunk = os.read(reader, 4096)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    break
                if not chunk:
                    break
                output += chunk
            _, errors = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, errors.decode())
            return output.decode().replace('\r\n', '\n')
        finally:
            os.close(reader)
            if writer is not None:
                os.close(writer)

    def test_piped_output_preserves_json_and_flags_work_at_every_level(self):
        added = self.register()
        expected = {'added': 'tablet', 'host': '100.64.0.2', 'port': None, 'mode': 'on-demand', 'contacted': False}
        self.assertEqual(added.stdout, json.dumps(expected, ensure_ascii=False) + '\n')
        inventory = json.loads((self.root / 'devices.local.json').read_text())
        inventory['devices']['tablet']['connection'] = {'state': 'disconnected', 'serial': None}
        inventory.update(adb_found=True, adb_source='saved', adb_server={'state': 'not-running'})
        for args in [('device', 'list'), ('--json', 'device', 'list'),
                     ('device', '--json', 'list'), ('device', 'list', '--json'),
                     ('--text', 'device', 'list', '--json')]:
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, json.dumps(inventory, ensure_ascii=False) + '\n')
        for args in [('--text', 'device', 'list'), ('device', '--text', 'list'),
                     ('device', 'list', '--text'), ('--json', 'device', 'list', '--text')]:
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('SAVED PORT', result.stdout)
                self.assertIn('port not saved', result.stdout)
                self.assertIn('devices not contacted', result.stdout)
                self.assertIn('disconnected', result.stdout)
                self.assertNotIn('explicit user request', result.stdout)
        self.assertFalse((self.root / 'adb-invoked').exists())

    def test_terminal_defaults_to_text_and_json_can_override_it(self):
        self.register()
        self.assertIn('SAVED PORT', self.tty('device', 'list'))
        result = json.loads(self.tty('device', 'list', '--json'))
        self.assertEqual(result['devices']['tablet']['mode'], 'on-demand')

    def test_usage_and_runtime_errors_are_actionable_without_stdout(self):
        for args in [('device', 'connect'), ('sandbox', 'prepare')]:
            result = self.cli(*args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, '')
            self.assertIn('Example:\n  t3-android-lab', result.stderr)
            self.assertIn('--help', result.stderr)
        self.register()
        for args, message, hint in [
            (('device', 'connect', 'missing'), 'Unknown device', 't3-android-lab device list'),
            (('sandbox', 'status'), 'Sandbox is not configured', 't3-android-lab sandbox setup --checkout')]:
            result = self.cli(*args)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, '')
            self.assertIn('Error: ' + message, result.stderr)
            self.assertIn('Try: ' + hint, result.stderr)
        self.assertIn('Registered: tablet', self.cli('device', 'connect', 'missing').stderr)

    def test_pairing_without_terminal_requires_file_and_requested_flag_is_removed(self):
        self.register()
        result = self.cli('device', 'pair', 'tablet', '--pairing-port', '38063')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')
        self.assertIn('--code-file', result.stderr)
        self.assertFalse((self.root / 'adb-invoked').exists())
        result = self.cli('device', 'connect', 'tablet', '--requested')
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / 'adb-invoked').exists())

    def test_output_flags_are_not_saved_as_sandbox_configuration(self):
        checkout = self.root / 'checkout'
        checkout.mkdir()
        (checkout / 'package.json').write_text('{"name": "@t3tools/monorepo"}')
        subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
        result = self.cli('sandbox', 'configure', '--checkout', str(checkout), '--serial', '100.64.0.1:35000', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads((self.root / 'config.local.json').read_text())
        self.assertNotIn('output_format', config)
        self.assertEqual(config['serial'], '100.64.0.1:35000')

    def test_named_configuration_and_grouped_format_flags_do_not_contact_devices(self):
        self.register()
        checkout = self.root / 'checkout'
        checkout.mkdir()
        (checkout / 'package.json').write_text('{"name": "@t3tools/monorepo"}')
        subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
        result = self.cli('--text', 'sandbox', '--text', 'configure', '--checkout', str(checkout),
                          '--device', 'tablet', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['device'], 'tablet')
        config = json.loads((self.root / 'config.local.json').read_text())
        for key in ['serial', 'adb', 'output_format', 'action', 'sandbox_action']:
            self.assertNotIn(key, config)
        result = self.cli('sandbox', 'configure', '--metro-port', '8082', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.root / 'config.local.json').read_text())['metro_port'], 8082)
        invalid = self.cli('sandbox', 'configure', '--device', 'missing')
        self.assertEqual(invalid.returncode, 1)
        self.assertIn('Unknown device', invalid.stderr)
        self.assertFalse((self.root / 'adb-invoked').exists())

    def test_setup_cli_with_fake_host_tools_preserves_authentication_on_retry(self):
        self.register()
        checkout = self.root / 'checkout'
        checkout.mkdir()
        (checkout / 'package.json').write_text('{"name": "@t3tools/monorepo", "engines": {"node": "24.0.0"}}')
        subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
        (checkout / 'apps/server/node_modules/effect').mkdir(parents=True)
        (checkout / 'apps/server/package.json').write_text('{"dependencies": {}}')
        codex = self.root / 'codex'
        codex.write_bytes(b'\x7fELFfixture')
        shutil.copytree(lab.ROOT / 'container', self.root / 'container')
        (self.root / 'lib/verify.py').write_text('print("PASS fixture isolation verifier")\n')
        fixture_auth = self.root / 'sandbox-auth.json'
        scripts = {
            'node': 'import sys\nif "--input-type=module" in sys.argv: print("{}")\n',
            'vp': '',
            'systemctl': '',
            'podman': 'import sys\nfrom pathlib import Path\n'
                      f'auth = Path({str(fixture_auth)!r})\n'
                      'if "-i" in sys.argv: auth.write_text(sys.stdin.read())\n'
                      'if any(arg.startswith("test -s") for arg in sys.argv):\n'
                      '    raise SystemExit(0 if auth.exists() else 1)\n',
        }
        for name, script in scripts.items():
            tool = self.root / 'bin' / name
            tool.write_text('#!' + sys.executable + '\n' + script)
            tool.chmod(0o700)
        auth = self.root / 'host-auth.json'
        auth.write_text('{"tokens": {"access_token": "fixture"}, "history": "excluded"}')
        result = self.cli('sandbox', 'setup', '--checkout', str(checkout), '--device', 'tablet',
                          '--codex-binary', str(codex), '--auth-file', str(auth), '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['authentication'], 'imported')
        self.assertIn('PASS fixture isolation verifier', result.stderr)
        fixture_auth.write_text('{"tokens": {"access_token": "refreshed"}}')
        result = self.cli('sandbox', 'setup', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['authentication'], 'preserved')
        self.assertEqual(json.loads(fixture_auth.read_text())['tokens']['access_token'], 'refreshed')
        self.assertFalse((self.root / 'adb-invoked').exists())
        self.assertNotIn('auth', (self.root / 'config.local.json').read_text())

    def test_removed_native_skip_flag_is_a_usage_error(self):
        result = self.cli('sandbox', 'prepare', '.', '--owner', 'thread-a', '--skip-native')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')

    def test_help_is_available_at_every_level_without_configuration(self):
        def visit(parser, path):
            result = self.cli(*path, '--help')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Examples:', result.stdout)
            self.assertIn('--json', result.stdout)
            for action in parser._actions:
                if isinstance(action, argparse._SubParsersAction):
                    for name, child in action.choices.items():
                        visit(child, [*path, name])
        visit(lab.make_parser(), [])
        self.assertFalse((self.root / 'config.local.json').exists())
        self.assertFalse((self.root / 'devices.local.json').exists())
        self.assertFalse((self.root / '.state').exists())

    def test_watch_and_timer_output_ignore_text_flags(self):
        (self.root / 'config.local.json').write_text('{}')
        for args in [('free',), ('free', '--text'), ('--text', 'free'),
                     ('sandbox', 'free'), ('sandbox', 'free', '--text')]:
            self.assertEqual(self.cli(*args).stdout, '{"free": true}\n')
        self.assertEqual(self.tty('free', '--text'), '{"free": true}\n')
        for args in [('keep-alive', '--text'), ('device', 'keep-alive', '--text'),
                     ('sandbox', 'keep-alive', '--text')]:
            result = self.cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            json.loads(result.stdout)
        session = {'owner': 'other', 'worktree': '/fixture', 'expires_at': 9999999999}
        (self.root / '.state/session.json').write_text(json.dumps(session))
        self.assertEqual(self.cli('free', '--text').stdout, '')

    def test_inventory_can_be_read_under_lock_but_mutations_cannot(self):
        self.register()
        with (self.root / '.state/operation.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.cli('device', 'list').returncode, 0)
            result = self.cli('device', 'remove', 'tablet')
            self.assertEqual(result.returncode, 1)
            self.assertIn('Another lab operation', result.stderr)
        self.assertIn('tablet', json.loads(self.cli('device', 'list').stdout)['devices'])

    def test_removal_is_idempotent_and_text_explains_it(self):
        self.register()
        self.assertEqual(json.loads(self.cli('device', 'remove', 'tablet').stdout), {'removed': 'tablet'})
        self.assertEqual(json.loads(self.cli('device', 'remove', 'tablet').stdout),
                         {'removed': 'tablet', 'already_removed': True})
        self.assertIn('Nothing to remove', self.cli('device', 'remove', 'tablet', '--text').stdout)

    def test_pairing_accepts_stdin_without_echoing_credentials(self):
        adb = self.root / 'adb'
        adb.write_text('#!' + sys.executable + '\nimport sys\n'
                       'assert sys.argv[1:] == ["pair", "100.64.0.2:38063"]\n'
                       'assert sys.stdin.read() == "123456\\n"\nprint("Successfully paired")\n')
        adb.chmod(0o700)
        self.cli('device', 'add', 'tablet', '--host', '100.64.0.2', '--adb', str(adb))
        for path in ['-', '/dev/stdin']:
            result = self.cli('device', 'pair', 'tablet', '--pairing-port', '38063',
                              '--code-file', path, input='123456\n')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['paired'], 'tablet')
            self.assertNotIn('123456', result.stdout + result.stderr + (self.root / 'devices.local.json').read_text())


class PresentationTests(unittest.TestCase):
    def test_inventory_and_status_describe_saved_configuration(self):
        empty = render('device list', {'devices': {}, 'adb': None, 'adb_source': 'missing',
                                      'adb_found': False, 'adb_server': {'state': 'not-running'}})
        self.assertIn('No devices registered', empty)
        self.assertIn('device add', empty)
        status = {'session': None, 'serial': '100.64.0.1:35000', 'metro_port': 8081,
                  'backend_port': 13775, 'container': 'running', 'services': {'metro': 'inactive'}}
        rendered = render('status', status)
        self.assertIn('not probed', rendered)
        self.assertIn('phone is free', rendered)
        self.assertIn('Next:', rendered)
        status['session'] = {'owner': 'thread-a', 'worktree': '/fixture', 'expires_at': 9999999999,
                             'expires_in_minutes': 42}
        rendered = render('status', status)
        self.assertIn('thread-a for /fixture', rendered)
        self.assertIn('42 min left', rendered)
        self.assertNotIn('Next:', rendered)

    def test_native_install_logs_go_to_stderr(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = {'serial': '100.64.0.1:35000', 'adb': '/fixture/sdk/adb', 'metro_port': 8081, 'backend_port': 13775}
            with patch.object(Path, 'home', return_value=Path(temporary)), \
                 patch.multiple(lab, validate_worktree=lambda *a: None, claim=lambda *a: None,
                                stop_metro=lambda: None, build=lambda *a: None, systemctl=lambda *a: None,
                                register_demo_project=lambda: None, connect=lambda *a: None,
                                executable=lambda name: '/fixture/' + name), patch.object(lab, 'run') as run:
                (Path(temporary) / '.config/systemd/user').mkdir(parents=True)
                result = lab.prepare(Path('/fixture'), 'thread-a', config)
            self.assertEqual(result['owner'], 'thread-a')
            self.assertIn('mobile:install', run.call_args.args)
            self.assertIs(run.call_args.kwargs['stdout'], sys.stderr)
            self.assertTrue(run.call_args.kwargs['env']['PATH'].startswith('/fixture/sdk' + os.pathsep))


if __name__ == '__main__':
    unittest.main()

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import devices
import lab


class SandboxTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config_path = self.root / 'config.local.json'
        self.inventory_path = self.root / 'devices.local.json'
        self.checkout = self.root / 'checkout'
        self.checkout.mkdir()
        (self.checkout / 'package.json').write_text(json.dumps({'name': '@t3tools/monorepo', 'engines': {'node': '24.0.0'}}))
        subprocess.run(['git', 'init', '-q', str(self.checkout)], check=True)
        (self.checkout / 'apps/server/node_modules/effect').mkdir(parents=True)
        self.adb = self.root / 'sdk/adb'
        self.adb.parent.mkdir()
        self.adb.write_text('#!/bin/sh\nexit 1\n')
        self.adb.chmod(0o700)
        self.codex = self.root / 'codex'
        self.codex.write_bytes(b'\x7fELFfixture')
        self.config = {'device': 'phone', 'checkout': str(self.checkout), 'codex_binary': str(self.codex),
                       'backend_port': 13775, 'metro_port': 8081}
        self.inventory = {'adb': str(self.adb), 'devices': {
            'phone': {'host': '100.64.0.1', 'port': None, 'mode': 'managed'},
            'tablet': {'host': '100.64.0.2', 'port': 36000, 'mode': 'on-demand'}}}
        lab.save_json(self.inventory_path, self.inventory)
        patches = [patch.multiple(lab, CONFIG=self.config_path, STATE=self.root / '.state'),
                   patch.object(devices, 'CONFIG', self.inventory_path)]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def args(self, *options):
        return lab.make_parser().parse_args(['sandbox', 'configure', *options])

    def test_named_configuration_has_no_address_snapshot_and_preserves_defaults_on_retry(self):
        args = self.args('--checkout', str(self.checkout), '--device', 'phone', '--metro-port', '8082', '--json')
        with patch.object(lab, 'adb_ready') as ready:
            config = lab.sandbox_configuration(args)
            lab.save_json(self.config_path, config)
            retried = lab.sandbox_configuration(self.args())
        self.assertEqual(config, retried)
        self.assertEqual(config['device'], 'phone')
        self.assertEqual(config['metro_port'], 8082)
        for key in ['serial', 'adb', 'action', 'sandbox_action', 'output_format', 'auth_file']:
            self.assertNotIn(key, config)
        ready.assert_not_called()

    def test_selecting_named_device_replaces_legacy_address_without_contact(self):
        legacy = {**self.config, 'adb': str(self.adb), 'serial': '100.64.0.9:35000'}
        legacy.pop('device')
        lab.save_json(self.config_path, legacy)
        with patch.object(lab, 'adb_ready') as ready:
            self.assertEqual(lab.sandbox_configuration(self.args()), legacy)
            named = lab.sandbox_configuration(self.args('--device', 'phone'))
        self.assertNotIn('serial', named)
        self.assertNotIn('adb', named)
        self.assertEqual(named['device'], 'phone')
        self.assertEqual(json.loads(self.config_path.read_text()), legacy)
        ready.assert_not_called()

    def test_named_connection_discovers_only_selected_host_and_updates_only_inventory(self):
        lab.save_json(self.config_path, self.config)
        original = self.config_path.read_text()
        config = dict(self.config)
        with patch.object(lab, 'open_ports', return_value=[37000]) as scan, \
             patch.object(lab, 'adb_ready', return_value=True) as ready, \
             patch.object(lab, 'run', return_value='') as run:
            result = lab.connect(config)
        self.assertEqual(result['connected'], '100.64.0.1:37000')
        self.assertEqual(config['serial'], result['connected'])
        self.assertEqual(config['adb'], str(self.adb))
        scan.assert_called_once_with('100.64.0.1', lab.ADB_PORTS)
        ready.assert_called_once_with(str(self.adb), result['connected'], attach=True)
        self.assertEqual(devices.load()['devices']['phone']['port'], 37000)
        self.assertEqual(devices.load()['devices']['tablet'], self.inventory['devices']['tablet'])
        self.assertEqual(self.config_path.read_text(), original)
        self.assertEqual(run.call_args.args[-3:], ('reverse', 'tcp:13775', 'tcp:13775'))

    def test_named_timer_never_scans_and_is_idle_without_reservation(self):
        with patch.object(lab, 'open_ports') as scan, patch.object(lab, 'adb_ready') as ready:
            self.assertEqual(lab.keep_alive(dict(self.config)), {'idle': True})
            ready.assert_not_called()
            lab.claim('/fixture', 'thread-a')
            with self.assertRaisesRegex(lab.LabError, 'unavailable'):
                lab.keep_alive(dict(self.config))
        scan.assert_not_called()

    def test_status_uses_inventory_without_contact_or_saving_unknown_port(self):
        with patch.object(lab, 'run', return_value='absent'), \
             patch.object(lab.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='inactive\n')), \
             patch.object(lab, 'adb_ready') as ready:
            status = lab.status(self.config)
        self.assertEqual(status['device'], 'phone')
        self.assertIsNone(status['serial'])
        ready.assert_not_called()
        self.assertFalse(self.config_path.exists())

    def test_setup_retries_preserve_login_and_restart_then_verify(self):
        events = []
        with patch.object(lab, 'validate_worktree'), \
             patch.object(lab, 'build', side_effect=lambda *a: events.append('build')), \
             patch.object(lab, 'install', side_effect=lambda *a: events.append('install')), \
             patch.object(lab, 'systemctl', side_effect=lambda *a: events.append(a)), \
             patch.object(lab, 'register_demo_project', side_effect=lambda: events.append('demo')), \
             patch.object(lab, 'executable', side_effect=lambda name: '/fixture/' + name), \
             patch.object(lab, 'run') as run, patch.object(lab, 'auth_import') as auth_import, \
             patch.object(lab.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
            for _ in range(2):
                result = lab.setup(self.config)
                self.assertTrue(result['verified'])
                self.assertEqual(result['authentication'], 'preserved')
            auth_import.assert_not_called()
        expected = ['build', 'install', ('stop', 't3-android-lab-backend.service'), 'demo',
                    ('start', 't3-android-lab-backend.service')]
        self.assertEqual(events, expected * 2)
        self.assertEqual(json.loads(self.config_path.read_text()), self.config)
        self.assertEqual(run.call_count, 2)
        self.assertIs(run.call_args.kwargs['stdout'], sys.stderr)

    def test_setup_imports_only_explicit_login_and_does_not_save_its_path(self):
        auth_file = self.root / 'auth.json'
        auth_file.write_text(json.dumps({'tokens': {'access_token': 'fixture'}, 'history': 'private'}))
        with patch.multiple(lab, validate_worktree=lambda *a: None, build=lambda *a: None, install=lambda *a: None,
                            systemctl=lambda *a: None, register_demo_project=lambda: None,
                            executable=lambda name: '/fixture/' + name), patch.object(lab, 'run') as run:
            result = lab.setup(self.config, auth_file)
        self.assertEqual(result['authentication'], 'imported')
        imported = json.loads(run.call_args_list[0].kwargs['input'])
        self.assertEqual(imported, {'tokens': {'access_token': 'fixture'}})
        self.assertNotIn('auth', self.config_path.read_text())

    def test_setup_preflight_errors_leave_configuration_and_services_untouched(self):
        invalid_auth = self.root / 'auth.json'
        invalid_auth.write_text('{"OPENAI_API_KEY": "fixture"}')
        cases = [('bad-auth', invalid_auth), ('missing-dependency', None), ('reserved', None)]
        for case, auth in cases:
            with self.subTest(case=case), patch.object(lab, 'build') as build, \
                 patch.object(lab, 'install') as install, patch.object(lab, 'systemctl') as systemctl, \
                 patch.object(lab, 'executable', side_effect=(RuntimeError('Missing node') if case == 'missing-dependency' else lambda name: '/fixture/' + name)):
                if case == 'reserved':
                    lab.claim('/fixture', 'thread-a')
                with self.assertRaises(RuntimeError):
                    lab.setup(self.config, auth)
                build.assert_not_called()
                install.assert_not_called()
                systemctl.assert_not_called()
                self.assertFalse(self.config_path.exists())

    def test_setup_missing_auth_and_failed_verifier_do_not_report_success(self):
        with patch.multiple(lab, validate_worktree=lambda *a: None, build=lambda *a: None, install=lambda *a: None,
                            systemctl=lambda *a: None, register_demo_project=lambda: None,
                            executable=lambda name: '/fixture/' + name), \
             patch.object(lab.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)), \
             patch.object(lab, 'run') as verify:
            with self.assertRaisesRegex(lab.LabError, 'no Codex login') as error:
                lab.setup(self.config)
            self.assertIn('--auth-file', error.exception.hints[0])
            verify.assert_not_called()
        with patch.multiple(lab, validate_worktree=lambda *a: None, build=lambda *a: None, install=lambda *a: None,
                            systemctl=lambda *a: None, register_demo_project=lambda: None,
                            executable=lambda name: '/fixture/' + name), \
             patch.object(lab.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), \
             patch.object(lab, 'run', side_effect=RuntimeError('verification failed')):
            with self.assertRaisesRegex(RuntimeError, 'verification failed'):
                lab.setup(self.config)


if __name__ == '__main__':
    unittest.main()

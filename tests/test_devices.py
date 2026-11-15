import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import devices
import lab


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.patcher = patch.object(devices, 'CONFIG', self.root / 'devices.local.json')
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_on_demand_device_is_not_contacted_by_registration_timer_or_implicit_connect(self):
        with patch.object(lab, 'adb_ready') as ready, patch.object(lab, 'open_ports') as scan:
            devices.add('tablet', '100.64.0.2', 'on-demand', 35000)
            self.assertEqual(devices.dispatch(type('Args', (), {'device_action': 'list'})())['devices']['tablet']['mode'], 'on-demand')
            self.assertEqual(devices.keep_alive(devices.load()), {'devices': {}})
            with self.assertRaisesRegex(RuntimeError, '--requested'):
                devices.connect(devices.load(), 'tablet', discover=True)
            ready.assert_not_called()
            scan.assert_not_called()
        self.assertEqual(devices.CONFIG.stat().st_mode & 0o777, 0o600)

    def test_managed_timer_uses_saved_ports_without_scanning_and_survives_offline_device(self):
        devices.add('phone', '100.64.0.1', 'managed', 35000)
        devices.add('other-phone', '100.64.0.3', 'managed', 36000)
        devices.add('tablet', '100.64.0.2', 'on-demand', 37000)
        with patch.object(lab, 'adb_ready', side_effect=lambda adb, serial, attach=False: serial == '100.64.0.3:36000'), \
             patch.object(lab, 'open_ports') as scan:
            result = devices.keep_alive(devices.load())['devices']
        self.assertIn('unavailable', result['phone'])
        self.assertEqual(result['other-phone']['connected'], '100.64.0.3:36000')
        self.assertNotIn('tablet', result)
        scan.assert_not_called()

    def test_explicit_discovery_saves_verified_port_and_sets_no_app_forwards(self):
        devices.add('tablet', '100.64.0.2', 'on-demand')
        with patch.object(lab, 'open_ports', return_value=[35000, 36000]), \
             patch.object(lab, 'adb_ready', side_effect=lambda adb, serial, attach=False: serial.endswith(':36000')):
            result = devices.connect(devices.load(), 'tablet', explicit=True, discover=True)
        self.assertEqual(result, {'device': 'tablet', 'connected': '100.64.0.2:36000', 'reverse_ports': []})
        self.assertEqual(devices.load()['devices']['tablet']['port'], 36000)
        with self.assertRaisesRegex(RuntimeError, 'already registered'):
            devices.add('alias', '100.64.0.2', 'managed', 36000)
        with self.assertRaisesRegex(RuntimeError, 'different settings'):
            devices.add('tablet', '100.64.0.2', 'managed', 36000)

    def test_failed_connection_does_not_replace_saved_port(self):
        devices.add('tablet', '100.64.0.2', 'on-demand', 35000)
        with patch.object(lab, 'adb_ready', return_value=False), patch.object(lab, 'open_ports') as scan:
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                devices.connect(devices.load(), 'tablet', explicit=True, connection_port=36000)
        scan.assert_not_called()
        self.assertEqual(devices.load()['devices']['tablet']['port'], 35000)

    def test_pairing_code_uses_stdin_and_is_not_saved_or_reported(self):
        adb = self.root / 'adb'
        adb.write_text('#!/usr/bin/env python3\nimport sys\n'
                       'assert sys.argv[1:] == ["pair", "100.64.0.2:38063"]\n'
                       'assert sys.stdin.read() == "123456\\n"\n'
                       'print("Successfully paired to device")\n')
        adb.chmod(0o700)
        code = self.root / 'code'
        code.write_text('123456')
        devices.add('tablet', '100.64.0.2', 'on-demand', adb=adb)
        with self.assertRaisesRegex(RuntimeError, '--requested'):
            devices.pair(devices.load(), 'tablet', 38063, code)
        result = devices.pair(devices.load(), 'tablet', 38063, code, explicit=True)
        self.assertEqual(result, {'paired': 'tablet', 'credential_printed': False})
        self.assertNotIn('123456', devices.CONFIG.read_text() + json.dumps(result))

    def test_device_commands_work_without_sandbox_configuration(self):
        shutil.copytree(lab.ROOT / 'lib', self.root / 'lib', ignore=shutil.ignore_patterns('__pycache__'))
        (self.root / 'bin').mkdir()
        shutil.copyfile(lab.ROOT / 'bin/t3-android-lab', self.root / 'bin/t3-android-lab')
        command = [sys.executable, str(self.root / 'bin/t3-android-lab'), 'device']
        subprocess.run([*command, 'add', 'tablet', '--host', '100.64.0.2'], capture_output=True, check=True)
        result = subprocess.run([*command, 'list'], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)['devices']['tablet']['mode'], 'on-demand')
        self.assertFalse((self.root / 'config.local.json').exists())


if __name__ == '__main__':
    unittest.main()

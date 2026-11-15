import getpass
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import devices
import lab


class OnboardingTests(unittest.TestCase):
    def test_adb_discovery_prefers_path_then_sdk_locations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adb = root / 'sdk/platform-tools/adb'
            adb.parent.mkdir(parents=True)
            adb.write_text('')
            adb.chmod(0o700)
            with patch.dict(os.environ, {'ANDROID_HOME': str(root / 'sdk')}, clear=True), \
                 patch.object(Path, 'home', return_value=root), patch.object(lab.shutil, 'which', return_value=None):
                self.assertEqual(lab.default_adb(), str(adb))
                adb.unlink()
                self.assertIsNone(lab.default_adb())
            with patch.object(lab.shutil, 'which', return_value='/fixture/bin/adb'):
                self.assertEqual(lab.default_adb(), '/fixture/bin/adb')

    def test_missing_adb_explains_installation(self):
        with patch.object(lab.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(lab.LabError, 'Install Android SDK Platform-Tools'):
                lab.adb_executable('adb')

    def test_saved_missing_adb_does_not_switch_to_another_version(self):
        with patch.object(lab, 'default_adb') as discover, patch.object(lab.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(lab.LabError, 'missing or not executable'):
                lab.adb_executable('/fixture/removed-sdk/adb')
            discover.assert_not_called()

    def test_resolved_adb_path_preserves_upgrade_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'adb-v1'
            target.write_text('')
            target.chmod(0o700)
            current = root / 'adb'
            current.symlink_to(target)
            self.assertEqual(lab.adb_executable(str(current)), str(current))

    def test_install_refuses_missing_adb_before_enabling_timer(self):
        from types import SimpleNamespace
        with patch.object(devices, 'load', return_value={'adb': None, 'devices': {}}), \
             patch.object(lab, 'default_adb', return_value=None), patch.object(devices, 'install') as install:
            with self.assertRaisesRegex(lab.LabError, 'ADB was not found'):
                devices.dispatch(SimpleNamespace(device_action='install'))
            install.assert_not_called()

    def test_nonterminal_pairing_fails_without_reading_input(self):
        with patch.object(devices.sys.stdin, 'isatty', return_value=False), patch.object(devices.getpass, 'getpass') as prompt:
            with self.assertRaisesRegex(lab.LabError, 'code file'):
                devices.pairing_code(None)
            prompt.assert_not_called()

    def test_pairing_refuses_getpass_echo_fallback(self):
        def unsafe_prompt(*args):
            warnings.warn('echo unavailable', getpass.GetPassWarning)
            self.fail('Prompt must stop before any unhidden input is read')
        with patch.object(devices.sys.stdin, 'isatty', return_value=True), \
             patch.object(devices.sys.stderr, 'isatty', return_value=True), \
             patch.object(devices.getpass, 'getpass', side_effect=unsafe_prompt):
            with self.assertRaisesRegex(lab.LabError, 'Cannot hide terminal input'):
                devices.pairing_code(None)

    def test_real_terminal_pairing_input_is_hidden(self):
        reader, writer = pty.openpty()
        script = 'import devices; assert devices.pairing_code(None) == "123456"; print("accepted")'
        environment = {**os.environ, 'PYTHONPATH': str(lab.ROOT / 'lib')}
        process = subprocess.Popen([sys.executable, '-c', script], stdin=writer, stdout=writer,
                                   stderr=writer, env=environment, start_new_session=True)
        os.close(writer)
        try:
            output = b''
            while b'Pairing code (hidden): ' not in output:
                ready, _, _ = select.select([reader], [], [], 5)
                self.assertTrue(ready, 'Pairing prompt did not appear')
                chunk = os.read(reader, 4096)
                self.assertTrue(chunk, 'Pairing process exited before prompting')
                output += chunk
            os.write(reader, b'123456\n')
            process.wait(timeout=5)
            while select.select([reader], [], [], 0)[0]:
                try:
                    chunk = os.read(reader, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
            self.assertEqual(process.returncode, 0, output.decode())
            self.assertIn(b'accepted', output)
            self.assertNotIn(b'123456', output)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            os.close(reader)


if __name__ == '__main__':
    unittest.main()

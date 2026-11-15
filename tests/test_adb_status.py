import os
from pathlib import Path
import socket
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import adb_status
import devices
from text import render


class AdbStatusTests(unittest.TestCase):
    def snapshot_from(self, reply):
        errors = []
        requests = []
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            listener.settimeout(3)
            def serve():
                try:
                    with listener.accept()[0] as connection:
                        requests.append(adb_status.receive(connection, 16))
                        for chunk in reply:
                            connection.sendall(chunk)
                except Exception as error:
                    errors.append(error)
            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            with patch.dict(os.environ, {'ADB_SERVER_SOCKET': f'tcp:127.0.0.1:{listener.getsockname()[1]}'}, clear=True):
                result = adb_status.snapshot()
            thread.join(timeout=4)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors, errors)
        self.assertEqual(requests, [b'000chost:devices'])
        return result

    def test_reads_framed_transport_table_and_matches_without_saved_port(self):
        content = b'100.64.0.2:36000\tdevice\nTablet.TS:40000\toffline\n100.64.0.2:35000\toffline\n'
        server, transports = self.snapshot_from([b'OK', b'AY', f'{len(content):04x}'.encode(), content[:10], content[10:]])
        self.assertEqual(server, {'state': 'running'})
        self.assertEqual(adb_status.match({'host': '100.64.0.2', 'port': None}, server, transports),
                         {'state': 'connected', 'serial': '100.64.0.2:36000'})
        self.assertEqual(adb_status.match({'host': 'tablet.ts', 'port': 12345}, server, transports),
                         {'state': 'offline', 'serial': 'Tablet.TS:40000'})
        self.assertEqual(adb_status.match({'host': 'different-alias.ts'}, server, transports)['state'], 'disconnected')

    def test_invalid_and_truncated_responses_are_unknown(self):
        for reply in [[b'FAIL'], [b'OKAYzzzz'], [b'OKAY-001'], [b'OKAY0005x'], [b'OKAY0003bad']]:
            with self.subTest(reply=reply):
                server, transports = self.snapshot_from(reply)
                self.assertEqual(server['state'], 'unknown')
                self.assertEqual(adb_status.match({'host': '100.64.0.2'}, server, transports)['state'], 'unknown')

    def test_refused_local_server_is_not_started(self):
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0))
            with patch.dict(os.environ, {'ADB_SERVER_SOCKET': f'tcp:127.0.0.1:{reserved.getsockname()[1]}'}, clear=True):
                server, transports = adb_status.snapshot()
        self.assertEqual(server, {'state': 'not-running'})
        self.assertEqual(adb_status.match({'host': '100.64.0.2'}, server, transports)['state'], 'disconnected')

    def test_unsupported_or_remote_endpoints_are_not_contacted(self):
        for spec in ['localfilesystem:/fixture/adb.sock', 'tcp:100.64.0.2:5037', 'tcp:remote.example:5037', 'tcp:localhost:bad']:
            with self.subTest(spec=spec), patch.dict(os.environ, {'ADB_SERVER_SOCKET': spec}, clear=True), \
                 patch.object(adb_status.socket, 'create_connection') as connect:
                server, _ = adb_status.snapshot()
                self.assertEqual(server['state'], 'unknown')
                connect.assert_not_called()

    def test_timeout_is_unknown(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(adb_status.socket, 'create_connection', side_effect=socket.timeout('timed out')):
            self.assertEqual(adb_status.snapshot()[0]['state'], 'unknown')

    def test_listing_does_not_save_observed_ports(self):
        config = {'adb': None, 'devices': {'tablet': {'host': '100.64.0.2', 'port': None, 'mode': 'on-demand'}}}
        with patch.object(adb_status, 'snapshot', return_value=({'state': 'running'}, {'100.64.0.2:36000': 'device'})), \
             patch.object(devices.lab, 'save_json') as save:
            result = devices.inventory(config)
        self.assertEqual(result['devices']['tablet']['connection']['state'], 'connected')
        self.assertIn('connected (100.64.0.2:36000)', render('device list', result))
        self.assertIsNone(config['devices']['tablet']['port'])
        self.assertNotIn('connection', config['devices']['tablet'])
        save.assert_not_called()


if __name__ == '__main__':
    unittest.main()

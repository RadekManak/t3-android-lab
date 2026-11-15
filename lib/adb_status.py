"""Read ADB's existing local transport table without launching an ADB client.

Protocol: https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/docs/dev/services.md
"""
import ipaddress
import os
import socket


def server_address():
    spec = os.environ.get('ADB_SERVER_SOCKET')
    if spec:
        if not spec.startswith('tcp:'):
            raise ValueError('Connection status is unavailable for this ADB_SERVER_SOCKET format')
        target = spec[4:]
        host, port = target.rsplit(':', 1) if ':' in target else ('localhost', target)
        host = host.strip('[]')
    else:
        host = os.environ.get('ANDROID_ADB_SERVER_ADDRESS') or 'localhost'
        port = os.environ.get('ANDROID_ADB_SERVER_PORT') or '5037'
    if host != 'localhost' and not ipaddress.ip_address(host).is_loopback:
        raise ValueError('Connection status only queries a local ADB server')
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError('Invalid ADB server port')
    return host, port


def receive(connection, size):
    result = b''
    while len(result) < size:
        chunk = connection.recv(size - len(result))
        if not chunk:
            raise ValueError('Incomplete ADB server response')
        result += chunk
    return result


def snapshot():
    try:
        with socket.create_connection(server_address(), timeout=1) as connection:
            connection.sendall(b'000chost:devices')
            if receive(connection, 4) != b'OKAY':
                raise ValueError('ADB server did not return its device list')
            header = receive(connection, 4)
            if any(byte not in b'0123456789abcdefABCDEF' for byte in header):
                raise ValueError('Invalid ADB response length')
            size = int(header, 16)
            content = receive(connection, size).decode('utf-8')
            transports = dict(line.split('\t', 1) for line in content.splitlines() if line)
        return {'state': 'running'}, transports
    except ConnectionRefusedError:
        return {'state': 'not-running'}, {}
    except (OSError, ValueError) as error:
        return {'state': 'unknown', 'detail': str(error)}, {}


def match(device, server, transports):
    if server['state'] == 'unknown':
        return {'state': 'unknown', 'serial': None}
    matches = [(serial, state) for serial, state in transports.items()
               if ':' in serial and serial.rsplit(':', 1)[0].lower() == device['host'].lower()]
    if not matches:
        return {'state': 'disconnected', 'serial': None}
    serial, state = min(matches, key=lambda item: (item[1] != 'device', item[0]))
    return {'state': 'connected' if state == 'device' else state, 'serial': serial}

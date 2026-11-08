import argparse
import ipaddress
import os
from pathlib import Path
import select
import socket
import socketserver
import stat
import threading

ROOT = Path(__file__).resolve().parent
ALLOWED = {'chatgpt.com', 'auth.openai.com', 'api.openai.com'}


def relay(left, right):
    while True:
        ready, _, _ = select.select([left, right], [], [], 1800)
        if not ready:
            return
        for source in ready:
            data = source.recv(65536)
            if not data:
                return
            (right if source is left else left).sendall(data)


def client_hello(sock, initial, expected_host):
    wire = bytearray(initial)
    position = 0
    handshake = bytearray()

    def need(size):
        while len(wire) < size:
            chunk = sock.recv(min(16384, size - len(wire)))
            if not chunk:
                raise ValueError('Incomplete TLS ClientHello')
            wire.extend(chunk)

    while len(handshake) < 4 or len(handshake) < 4 + int.from_bytes(handshake[1:4], 'big'):
        need(position + 5)
        if wire[position] != 22:
            raise ValueError('TLS handshake required')
        length = int.from_bytes(wire[position + 3:position + 5], 'big')
        if length > 18432 or len(handshake) + length > 65536:
            raise ValueError('Oversized TLS handshake')
        need(position + 5 + length)
        handshake.extend(wire[position + 5:position + 5 + length])
        position += 5 + length
    if handshake[0] != 1:
        raise ValueError('ClientHello required')
    body = handshake[4:4 + int.from_bytes(handshake[1:4], 'big')]
    pos = 34
    pos += 1 + body[pos]
    pos += 2 + int.from_bytes(body[pos:pos + 2], 'big')
    pos += 1 + body[pos]
    end = pos + 2 + int.from_bytes(body[pos:pos + 2], 'big')
    pos += 2
    if end > len(body):
        raise ValueError('Truncated TLS extensions')
    while pos + 4 <= end:
        kind = int.from_bytes(body[pos:pos + 2], 'big')
        size = int.from_bytes(body[pos + 2:pos + 4], 'big')
        extension = body[pos + 4:pos + 4 + size]
        if pos + 4 + size > end:
            raise ValueError('Truncated TLS extension')
        if kind == 0:
            if len(extension) < 5 or extension[2] != 0:
                raise ValueError('Invalid TLS hostname')
            name_size = int.from_bytes(extension[3:5], 'big')
            name = extension[5:5 + name_size].decode('ascii')
            if name != expected_host:
                raise ValueError('TLS hostname does not match CONNECT target')
            return bytes(wire)
        pos += 4 + size
    raise ValueError('TLS hostname required')


class Proxy(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(15)
        data = b''
        try:
            while b'\r\n\r\n' not in data:
                chunk = self.request.recv(4096)
                if not chunk or len(data) + len(chunk) > 16384:
                    return
                data += chunk
            header, extra = data.split(b'\r\n\r\n', 1)
            method, target, version = header.split(b'\r\n', 1)[0].decode('ascii').split()
            host, port = target.rsplit(':', 1)
            if method != 'CONNECT' or host not in ALLOWED or port != '443':
                self.request.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n')
                print('DENY', method, target, flush=True)
                return
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise ValueError('Non-public DNS address')
            remote = None
            for family, kind, proto, _, address in addresses:
                candidate = socket.socket(family, kind, proto)
                candidate.settimeout(15)
                try:
                    candidate.connect(address)
                    remote = candidate
                    break
                except OSError:
                    candidate.close()
            if remote is None:
                raise OSError('Upstream unavailable')
            with remote:
                self.request.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                print('ALLOW', host, flush=True)
                hello = client_hello(self.request, extra, host)
                remote.sendall(hello)
                self.request.settimeout(None)
                remote.settimeout(None)
                relay(self.request, remote)
        except (OSError, ValueError, IndexError, UnicodeError):
            return


class Ingress(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            descriptor = os.open(ROOT / 'ingress/backend.sock', os.O_PATH | os.O_NOFOLLOW)
            try:
                if not stat.S_ISSOCK(os.fstat(descriptor).st_mode):
                    return
                with socket.socket(socket.AF_UNIX) as backend:
                    backend.connect('/proc/self/fd/' + str(descriptor))
                    relay(self.request, backend)
            finally:
                os.close(descriptor)
        except OSError:
            return


class BoundedThreads:
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(*args, **kwargs)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class UnixServer(BoundedThreads, socketserver.ThreadingUnixStreamServer):
    pass


class TcpServer(BoundedThreads, socketserver.ThreadingTCPServer):
    allow_reuse_address = True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Restricted OpenAI proxy and sandbox ingress')
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=13775)
    args = parser.parse_args()
    ROOT = args.state_dir.resolve()
    os.umask(0o077)
    path = ROOT / 'egress/proxy.sock'
    path.unlink(missing_ok=True)
    with UnixServer(str(path), Proxy) as proxy, TcpServer(('127.0.0.1', args.port), Ingress) as ingress:
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        ingress.serve_forever()

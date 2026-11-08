import importlib.util
from pathlib import Path
import socket
import tempfile
import threading

source = Path(__file__).resolve().parents[1] / 'lib/bridge.py'
spec = importlib.util.spec_from_file_location('sandbox_bridge', source)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
with tempfile.TemporaryDirectory() as temporary:
    bridge.ROOT = Path(temporary)
    directory = bridge.ROOT / 'ingress'
    directory.mkdir()
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(directory / 'backend.sock'))
    listener.listen()
    listener.settimeout(1)
    with bridge.TcpServer(('127.0.0.1', 0), bridge.Ingress) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with socket.create_connection(server.server_address, 2) as client:
            backend, _ = listener.accept()
            with backend:
                backend.sendall(b'BRIDGE_OK')
                assert client.recv(64) == b'BRIDGE_OK'
        listener.close()
        (directory / 'backend.sock').unlink()
        with socket.socket(socket.AF_UNIX) as protected:
            protected.bind(str(bridge.ROOT / 'protected.sock'))
            protected.listen()
            protected.settimeout(0.2)
            (directory / 'backend.sock').symlink_to(bridge.ROOT / 'protected.sock')
            with socket.create_connection(server.server_address, 2) as client:
                client.settimeout(2)
                assert client.recv(64) == b''
            try:
                connection, _ = protected.accept()
                connection.close()
                raise AssertionError('Bridge followed a container-controlled symlink')
            except socket.timeout:
                print('PASS ingress serves a socket and refuses a symlink to another host socket')
        server.shutdown()

class Hold(bridge.socketserver.BaseRequestHandler):
    def handle(self):
        self.server.entered.put(True)
        self.server.release.wait()

import queue
with bridge.TcpServer(('127.0.0.1',0),Hold) as server:
    server.slots = threading.BoundedSemaphore(2)
    server.entered = queue.Queue()
    server.release = threading.Event()
    threading.Thread(target=server.serve_forever,daemon=True).start()
    clients = []
    try:
        for _ in range(2):
            clients.append(socket.create_connection(server.server_address,2))
            server.entered.get(timeout=2)
        with socket.create_connection(server.server_address,2) as rejected:
            rejected.settimeout(2)
            assert rejected.recv(1) == b''
        print('PASS bridge rejects excess connections without allocating handler threads')
    finally:
        server.release.set()
        for client in clients:
            client.close()
        server.shutdown()

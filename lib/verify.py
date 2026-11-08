import json
from pathlib import Path
import subprocess


from lab import ROOT, STATE, CONTAINER, load_config
config = load_config()


def inside(*args, input=None):
    return subprocess.check_output(['podman', 'exec', '-i', CONTAINER, *args], input=input, text=True)


info = json.loads(subprocess.check_output(['podman', 'inspect', CONTAINER], text=True))[0]
assert info['State']['Running']
assert info['HostConfig']['NetworkMode'] == 'none'
assert not info['HostConfig']['Privileged']
assert info['HostConfig']['ReadonlyRootfs']
assert info['Config']['User'] == '1000:1000'
assert 'no-new-privileges' in info['HostConfig']['SecurityOpt']
status = inside('cat', '/proc/1/status')
assert all(int(line.split()[1], 16) == 0 for line in status.splitlines() if line.startswith('Cap'))
assert 'NoNewPrivs:\t1' in status
mounts = info['Mounts']
assert len(mounts) == 3
assert {m['Destination'] for m in mounts} == {'/home/node', '/ingress', '/egress'}
assert next(m for m in mounts if m['Destination'] == '/home/node')['Name'] == 't3-a54-sandbox-home'
base = STATE
for name in ['ingress', 'egress']:
    assert next(m for m in mounts if m['Destination'] == '/' + name)['Source'] == str(base / name)
assert not next(m for m in mounts if m['Destination'] == '/egress')['RW']
print('PASS non-root, no capabilities, read-only image, only isolated home and bridge mounts')
print(inside('python3', '-', input=r'''
import os,socket,ssl,subprocess,sqlite3
assert os.getuid()==1000
assert os.listdir('/sys/class/net')==['lo']
for p in ['/root/.ssh','/home/node/.ssh','/home/node/.config/gh','/var/run/docker.sock','/run/podman/podman.sock']:
 assert not os.path.exists(p),p
for ip,port in [('100.64.0.1',443),('1.1.1.1',443)]:
 try:
  socket.create_connection((ip,port),1).close()
  raise AssertionError('Direct route reachable '+ip)
 except OSError:
  print('PASS direct connection blocked',ip,port)
for host in ['github.com','example.com','100.64.0.1','127.0.0.1:13774','169.254.169.254']:
 r=subprocess.run(['curl','--proxy','http://127.0.0.1:3128','--noproxy','','--max-time','5','--silent','--show-error','https://'+host],capture_output=True,text=True)
 assert r.returncode!=0 and '403' in r.stderr,(host,r.returncode,r.stderr)
 print('PASS proxy blocks',host)
r=subprocess.run(['curl','--max-time','15','--silent','--output','/dev/null','--write-out','%{http_code}','https://chatgpt.com'],capture_output=True,text=True)
assert r.returncode==0 and r.stdout=='200',(r.returncode,r.stdout)
print('PASS OpenAI HTTPS allowed')
with socket.create_connection(('127.0.0.1',3128),5) as tunnel:
 tunnel.sendall(b'CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\n\r\n')
 assert b'200' in tunnel.recv(256)
 try:
  ssl.create_default_context().wrap_socket(tunnel,server_hostname='github.com')
  raise AssertionError('Mismatched TLS hostname accepted')
 except (ssl.SSLError,OSError):
  print('PASS proxy rejects mismatched TLS hostname')
db=sqlite3.connect('file:/home/node/t3-state/userdata/statev2.sqlite?mode=ro',uri=True)
rows=db.execute('select workspace_root from projection_projects').fetchall()
assert rows and all(r[0].startswith('/home/node/projects/') for r in rows),rows
print('PASS all project roots are synthetic inside container',rows)
assert os.path.isfile('/home/node/.codex/auth.json')
assert subprocess.check_output(['git','-C','/home/node/projects/demo','remote','-v'],text=True)==''
print('PASS Codex auth exists; demo has no Git remotes')
'''))

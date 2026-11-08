#!/bin/sh
set -eu
mkdir -p "$HOME/projects/demo" "$CODEX_HOME"
cd "$HOME/projects/demo"
if [ ! -d .git ]; then
  git init -q
  git config user.name 'Sandbox Demo'
  git config user.email 'demo@example.invalid'
  printf '# Sandbox demo\n\nSynthetic workspace for phone testing.\n' > README.md
  git add README.md
  git commit -qm 'Initialize synthetic demo'
fi
socat TCP-LISTEN:3128,bind=127.0.0.1,reuseaddr,fork UNIX-CONNECT:/egress/proxy.sock &
socat UNIX-LISTEN:/ingress/backend.sock,unlink-early,fork,mode=0600 TCP:127.0.0.1:13774 &
exec node /opt/t3/dist/bin.mjs serve --host 127.0.0.1 --port 13774 --base-dir "$T3CODE_HOME"

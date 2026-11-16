# Set up and operate the Android lab

Use this guide from a coding agent in T3 Code. Run CLI examples from the repository root. Read the [lab skill](../skills/t3-android-lab/SKILL.md) first for device selection and Device session ownership. The [README](../README.md#system-requirements) lists system requirements.

Choose the task before loading the full procedure:

- For a new host or device, follow [device onboarding](onboarding.md) through pairing and screen verification.
- For a registered device, follow [use a device](#use-a-device).
- For first-time T3 development setup, follow [set up a machine](#set-up-a-machine).
- For an existing sandbox, follow [test a worktree](#test-a-worktree).
- For sandbox infrastructure changes, read the [isolation boundary](isolation.md) before editing and [verify it afterward](#check-and-recover).

## Install the skill

From the cloned repository, install the skill once for your user:

```bash
bin/t3-android-lab skill-install
```

The default destination is `~/.agents/skills/t3-android-lab`. Use `--directory ~/.codex/skills` for an agent that reads that directory instead. The installed skill links to this checkout, so keep it in place. Installation is complete when the link resolves to this repository's `skills/t3-android-lab` directory. No per-worktree installation is needed.

## Use a device

For a new device, complete [onboarding](onboarding.md) first. For an existing entry, read its status and connect by name:

```bash
bin/t3-android-lab device list --json
bin/t3-android-lab device connect <name> --json
```

`device connect` tries the saved port before discovering a replacement on that device. Use `--port <connection-port>` to supply a known port or `--no-discover` to prevent scanning. Background timers never scan. Connection sets no app port forwards.

Use T3 Code's `device_list`, `device_open`, and the returned bound AgentDevice command to inspect the selected device. Access is verified when the connection succeeds and you can inspect its screen. An inventory entry or ADB pairing alone does not prove screen control.

Close the AgentDevice session you opened when finished. Use `device disconnect <name>` to end its ADB connection. `device remove <name>` removes local configuration without revoking Android pairing. To revoke access, forget this host on the device's Wireless debugging screen.

The optional `device install` timer reconnects managed entries on saved ports. `device stop` disables that timer. Follow [managed-device recovery](onboarding.md#enable-recovery-on-a-managed-device) to configure device-side recovery.

Device inventory is stored in `devices.local.json`. Sandbox settings are stored separately in `config.local.json`. Both are local and ignored by Git.

## Use the commands

Run `bin/t3-android-lab --help` to find a command, then add `--help` to that command for options and examples. `device` commands manage Android debugging access. `sandbox` commands manage the optional development environment. `device pair` pairs ADB access; `sandbox pair` connects the Android app to its backend.

Results use readable text on a terminal and JSON when piped. Agents and scripts that parse results should pass `--json`, including when running in a terminal. Use `--text` to force readable output. Both flags work at any command level; the last one wins.

```bash
bin/t3-android-lab device list --text
bin/t3-android-lab device connect --help
bin/t3-android-lab sandbox status --json
```

`device list` reports ADB connection state, not network reachability. It reads the existing local ADB server without starting or restarting it. A disconnected entry may still be reachable; connect it by name. Matching uses the registered host spelling without DNS lookups. Connections made outside the lab using another IP address or hostname alias may not match. Unsupported or remote ADB server sockets show unknown status.

`status` reports the sandbox reservation and local services; it does not probe the phone. The only prompt is the hidden pairing code, shown when stdin and stderr are terminals and `--code-file` is omitted. Agents use `--code-file <path>` or `--code-file -` to stay noninteractive.

Errors go to stderr, with retry commands where available. Exit codes are 0 for success, 1 for an operation failure, and 2 for invalid arguments. Watcher `sandbox free` and timer commands retain JSON output regardless of format flags. `sandbox verify` always prints `PASS` lines. The old root `free` and `keep-alive` entry points remain available for existing watches and installed timers.

## Set up a machine

Complete [device onboarding](onboarding.md) and check the [sandbox requirements](../README.md#system-requirements). Install the T3 Code checkout's dependencies before building.

Set up the sandbox using the registered device name:

```bash
bin/t3-android-lab sandbox setup \
  --checkout ~/git/t3code \
  --device lab-phone \
  --codex-binary <path-to-standalone-codex> \
  --auth-file ~/.codex/auth.json \
  --json
```

`setup` validates build inputs, required executables, and the supplied login before saving configuration. It builds the backend, installs user services, registers the synthetic demo project, starts the updated backend, imports the supplied login, and runs the isolation verifier. Setup does not connect to the phone or start Metro. A live reservation blocks setup.

Settings are saved in the ignored `config.local.json`. Retry with `sandbox setup --json` to reuse them and preserve existing sandbox authentication. After a failure, correct the reported problem and retry; partial service changes can remain. Pass `--auth-file <path>` only to import or replace a login explicitly. If no login exists, setup prints the command to supply one. Credentials and their source path are not saved in sandbox configuration.

Use `sandbox configure` with the same settings flags to save changes without starting services. After changing ports or moving this repository, rerun `sandbox setup` and then `sandbox prepare`. If the old location left a skill symlink behind, remove that link before rerunning `skill-install`.

`config.example.json` shows the configuration fields. The Codex path must resolve to a native ELF binary rather than a shell or JavaScript wrapper. The backend build uses the Node version and runtime dependency versions from the selected T3 Code checkout.

Enable user lingering if the services must start without an interactive login. On systemd hosts, an administrator can run `loginctl enable-linger "$USER"`. The backend, restricted proxy, and ADB reconnect timer start at boot. Metro starts only when a worktree is reserved.

Setup succeeds only after isolation verification passes and a sandbox login is present. Continue with a worktree reservation to verify the Android client's connection.

### Connect the sandbox phone

Complete [device onboarding](onboarding.md) first. Select the registered name with `sandbox setup --device <name>` or `sandbox configure --device <name>`. The sandbox reads its host, saved connection port, and ADB executable from the device inventory. A saved port is optional; `sandbox connect` and `sandbox prepare` discover it when needed. The sandbox reconnect timer uses the inventory's saved port during an active reservation and never scans. Existing configurations with a direct `serial` and `adb` path continue to work; selecting a name replaces those settings.

The sandbox has one selected phone, backend, and Metro instance per host. The device inventory can hold additional phones and tablets for general debugging.

Other T3 installations can use this machine as a remote device host. Alternatively, pair each device host directly. Reservations coordinate threads on one device host, so coordinate use explicitly across independent hosts.

## Test a worktree

Reserve the phone with your thread ID or another stable owner name:

```bash
bin/t3-android-lab sandbox status --json
bin/t3-android-lab sandbox prepare <worktree> --owner <thread-id> --json
bin/t3-android-lab sandbox wait-ready --json
```

`prepare` builds a sanitized backend image from this worktree, restarts the sandbox, switches Metro, installs the native client matching the checkout's fingerprint, and restores ADB forwarding. The default ports are 8081 for Metro and 13775 for the backend. The container itself listens on loopback port 13774 in its separate network namespace. A native build runs on two cores under a 5 GB memory cap. T3 Code's native installer checks the worktree fingerprint and installed app automatically. It keeps a matching client, installs a compatible cached build, or builds one when needed.

Build logs and Metro logs live under `.state/logs`. Resolve missing dependencies or a running T3 worktree setup before preparing the phone. An existing reservation owned by another thread blocks switching. The same owner may retry preparation in the same worktree after a failure. Reservations last two hours by default (`--hours`, at most twelve). The owner renews with `sandbox extend`. `sandbox free` prints one line only while no reservation holds the phone, which makes it a t3-watch check.

Open the phone through T3's Device panel using `device_open`. Use its returned AgentDevice command for every UI operation. Open T3 Code Dev with this URL, substituting the configured Metro port:

```text
t3code-dev://expo-development-client/?url=http%3A%2F%2F127.0.0.1%3A8081&disableOnboarding=1&disableAutoLaunch=1&disableFab=1
```

If the sandbox connection is not saved on the phone, run `sandbox pair` with the exact bound AgentDevice executable. It creates a short-lived credential and opens the pairing route without printing the credential.

```bash
bin/t3-android-lab sandbox pair --owner <thread-id> --agent-device <returned-bound-command> --json
```

The Sandbox Demo project contains a synthetic README and a local Git repository. It has no remotes. Use this project or create other dummy projects inside the container. The backend volume survives container restarts and image rebuilds.

Preparation is verified when Metro reports ready and the Android client shows the synthetic Sandbox Demo project through the isolated backend. Complete the requested app checks before releasing the reservation.

When testing is complete:

```bash
bin/t3-android-lab sandbox release --owner <thread-id>
```

Release stops Metro and frees the reservation. The sandbox backend remains available. An interrupted thread's reservation is retained until it expires, so another thread cannot silently take over its phone. On expiry the reconnect timer stops its Metro and frees the phone.

## Stop the lab

```bash
bin/t3-android-lab sandbox stop
bin/t3-android-lab sandbox status
```

`stop` shuts down Metro, the backend container, the host bridge, and the ADB reconnect timer. It preserves the home volume, Codex login, images, phone pairing, and installed client. Repeating it is safe. An active reservation requires its owner's `--owner <thread-id>`. Close your own AgentDevice session first if you opened one.

The next `prepare` restarts the runtime and reconnect timer. Services configured for boot still start after a host reboot.

## Move to another machine

Clone this repository and repeat machine setup, device pairing, and global skill installation. Authenticate Codex on the new host and import only its login file. Create fresh synthetic state rather than copying an old container home volume.

The native-client fingerprint cache is managed by T3 Code. The setup does not require copying Android build outputs, ADB private keys, Codex history, T3 state, or other credentials into Git.

## Check and recover

```bash
bin/t3-android-lab sandbox verify
python3 tests/test_bridge.py
python3 -m unittest discover -s tests -p 'test_*.py'
systemctl --user status t3-android-lab-backend.service t3-android-lab-bridge.service
systemctl --user restart t3-android-lab-backend.service
```

The verifier checks actual container mounts, capabilities, synthetic project roots, blocked direct routes, proxy restrictions, and allowed OpenAI HTTPS. It does not run paid inference. Follow [the isolation reference](isolation.md) before changing the container boundary.

Codex usage limits are account limits. A successful login does not guarantee another inference request is available. Renew credentials with `auth-import` only when authentication needs renewal. Credentials are never synchronized back to the host.

## Wake a device on the phone's network

`t3-android-lab sandbox wake <mac>` sends a Wake-on-LAN magic packet from the phone onto the Wi-Fi network it is on. The phone's own `nc` cannot broadcast, so the lab builds a small sender from `tools/wol` with Go, copies it to `/data/local/tmp` when it changed, and sends to both `255.255.255.255` and the Wi-Fi subnet's broadcast address. The target needs Wake-on-LAN enabled in its firmware or network settings. It needs no reservation, since it does not touch Metro.

## Check publication contents

Install [Gitleaks](https://github.com/gitleaks/gitleaks), then run:

```bash
python3 scripts/audit-publication.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The audit scans all reachable Git history and a snapshot of tracked and unignored files. It also checks that local configuration, runtime state, and common credential files stay excluded. Review any findings before publishing. Passing a secret scanner does not prove the absence of every private value.

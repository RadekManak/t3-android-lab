# T3 Code Android lab

Use Android phones and tablets remotely from agents in T3 Code. Connect over Tailscale and wireless ADB for app interaction, screenshots, and debugging logs. For T3 Code development, reserve a shared phone and run Metro against an isolated backend with synthetic projects and a separate Codex login.

## Start with device access

General debugging needs a Linux host with Python 3.9 or newer, Android platform-tools, Tailscale, and T3 Code's Device tools. It does not need Podman, a T3 Code source checkout, Metro, or a Codex login. The sandbox workflow below needs those additional dependencies.

Clone this repository, then install the agent skill once for your user:

```bash
git clone https://github.com/RadekManak/t3-android-lab.git ~/git/t3-android-lab
cd ~/git/t3-android-lab
bin/t3-android-lab skill-install
```

The default destination is `~/.agents/skills/t3-android-lab`. Use `--directory ~/.codex/skills` for an agent that reads that directory instead. The installed skill links to this checkout, so keep it in place. No project actions or per-worktree skill installation are needed.

Follow [Onboard an Android device](docs/onboarding.md) to pair a device and choose its availability policy. Register each device locally:

```bash
bin/t3-android-lab device add lab-phone --host 100.x.x.x --port 35000 --mode managed
bin/t3-android-lab device add personal-tablet --host 100.y.y.y --mode on-demand
bin/t3-android-lab device list
bin/t3-android-lab device connect lab-phone
bin/t3-android-lab device connect personal-tablet --port 36000 --requested
```

`device list` reads configuration without contacting devices. Managed devices can use the optional `device install` reconnect timer. On-demand devices require a user request for each session and are excluded from that timer, health checks, and default device selection. `--requested` records that intent for a named operation. It is not an authentication mechanism.

`device connect` uses the saved port or `--port`. Pass `--discover` to scan that one device for a changed port. Neither the managed-device timer nor the sandbox timer scans automatically. These commands create no app port forwards. Use T3's `device_list`, `device_open`, and the returned bound AgentDevice command for UI interaction. Close your own AgentDevice session when finished.

`device disconnect <name> --requested` disconnects an on-demand device. `device remove <name>` removes its local entry without revoking Android pairing. To revoke access, forget this host on the device's Wireless debugging screen. `device stop` disables the managed-device timer.

The machine's device inventory is `devices.local.json`. Sandbox settings remain in `config.local.json`. Both are ignored by Git; the example files contain placeholders only.

## Set up a machine

Use a Linux device host with rootless Podman 5 or newer, Quadlet, systemd user services, Python 3.9 or newer, Git, Vite+, Android platform-tools, and a standalone Linux Codex binary. Install the T3 Code checkout's dependencies. Java and the Android SDK are needed when the native client is not available in the shared cache.

Clone this private repository alongside your other checkouts:

```bash
gh repo clone RadekManak/t3-android-lab ~/git/t3-android-lab
cd ~/git/t3-android-lab
bin/t3-android-lab configure \
  --checkout ~/git/t3code \
  --serial <phone-tailscale-address:connection-port> \
  --adb ~/android-sdk/platform-tools/adb \
  --codex-binary <path-to-standalone-codex>
bin/t3-android-lab build ~/git/t3code
bin/t3-android-lab install
systemctl --user start t3-android-lab-backend.service
bin/t3-android-lab auth-import ~/.codex/auth.json
```

After changing ports or moving this repository, rerun `install` and `prepare` to regenerate service paths and restart the bridge. If the old location left a skill symlink behind, remove that link before rerunning `skill-install`.

`config.local.json` belongs to this machine and is ignored by Git. `config.example.json` shows its fields. The Codex path must resolve to a native ELF binary rather than a shell or JavaScript wrapper. `build` uses the Node version and runtime dependency versions from the selected T3 Code checkout.

Enable user lingering if the services must start without an interactive login. On systemd hosts, an administrator can run `loginctl enable-linger "$USER"`. The backend, restricted proxy, and ADB reconnect timer start at boot. Metro starts only when a worktree is reserved.

### Connect the sandbox phone

Complete [device onboarding](docs/onboarding.md) first. Set the sandbox's `--serial` to that phone's Tailscale address and current connection port. The pairing port is different. `connect` and `prepare` can scan for a changed port when explicitly invoked. The sandbox reconnect timer only restores the saved port during an active reservation.

The sandbox has one selected phone, backend, and Metro instance per host. The independent device inventory can hold additional phones and tablets for general debugging. Selecting an on-demand device for sandbox work requires an explicit request too.

Other T3 installations can use this machine as a remote device host. Alternatively, pair each device host directly. Reservations coordinate threads on one device host, so coordinate use explicitly across independent hosts.

## Test a worktree

Reserve the phone with your thread ID or another stable owner name:

```bash
bin/t3-android-lab status
bin/t3-android-lab prepare <worktree> --owner <thread-id>
bin/t3-android-lab wait-ready
```

`prepare` builds a sanitized backend image from this worktree, restarts the sandbox, switches Metro, installs the native client matching the checkout's fingerprint, and restores ADB forwarding. The default ports are 8081 for Metro and 13775 for the backend. The container itself listens on loopback port 13774 in its separate network namespace. A native build runs on two cores under a 5 GB memory cap so it cannot freeze the host. Pass `--skip-native` to keep the installed client only when it was built from the same base, such as a JS-only change on top of it; a client from an older base fails on launch with a native module error.

Build logs and Metro logs live under `.state/logs`. A missing dependency installation or a running T3 worktree setup must be resolved before preparing the phone. An existing reservation owned by another thread blocks switching. The same owner may retry preparation in the same worktree after a failure. Reservations last two hours by default (`--hours`, at most twelve); the owner renews with `extend`. `free` prints one line only while no reservation holds the phone, which makes it a t3-watch check.

Open the phone through T3's Device panel using `device_open`. Use its returned AgentDevice command for every UI operation. Open T3 Code Dev with this URL, substituting the configured Metro port:

```text
t3code-dev://expo-development-client/?url=http%3A%2F%2F127.0.0.1%3A8081&disableOnboarding=1&disableAutoLaunch=1&disableFab=1
```

If the sandbox connection is not saved on the phone, run the lab's `pair` command with the exact bound AgentDevice executable. It creates a short-lived credential and opens the pairing route without printing the credential.

The Sandbox Demo project contains a synthetic README and a local Git repository. It has no remotes. Use this project or create other dummy projects inside the container. The backend volume survives container restarts and image rebuilds.

When testing is complete:

```bash
bin/t3-android-lab release --owner <thread-id>
```

Release stops Metro and frees the reservation. The sandbox backend remains available. An interrupted thread's reservation is retained until it expires, so another thread cannot silently take over its phone. On expiry the reconnect timer stops its Metro and frees the phone.

## Stop the lab

```bash
bin/t3-android-lab stop
bin/t3-android-lab status
```

`stop` shuts down Metro, the backend container, the host bridge, and the ADB reconnect timer. It preserves the home volume, Codex login, images, phone pairing, and installed client. Repeating it is safe. An active reservation requires its owner's `--owner <thread-id>`. Close your own AgentDevice session first if you opened one.

The next `prepare` restarts the runtime and reconnect timer. Services configured for boot still start after a host reboot.

## Move to another machine

Clone this repository and repeat machine setup, device pairing, and global skill installation. Authenticate Codex on the new host and import only its login file. Create fresh synthetic state rather than copying an old container home volume.

The native-client fingerprint cache is managed by T3 Code. The setup does not require copying Android build outputs, ADB private keys, Codex history, T3 state, or other credentials into Git.

## Check and recover

```bash
bin/t3-android-lab verify
python3 tests/test_bridge.py
python3 -m unittest discover -s tests -p 'test_*.py'
systemctl --user status t3-android-lab-backend.service t3-android-lab-bridge.service
systemctl --user restart t3-android-lab-backend.service
```

The verifier checks actual container mounts, capabilities, synthetic project roots, blocked direct routes, proxy restrictions, and allowed OpenAI HTTPS. It does not run paid inference. Follow [the isolation reference](docs/isolation.md) before changing the container boundary.

Codex usage limits are account limits. A successful login does not guarantee another inference request is available. Renew credentials with `auth-import` only when authentication needs renewal. Credentials are never synchronized back to the host.

AI assistance: gpt-6.1-sol — implementation, documentation, and automated verification.

## Wake a device on the phone's network

`t3-android-lab wake <mac>` sends a Wake-on-LAN magic packet from the phone onto the Wi-Fi network it is on. The phone's own `nc` cannot broadcast, so the lab builds a small sender from `tools/wol` with Go, copies it to `/data/local/tmp` when it changed, and sends to both `255.255.255.255` and the Wi-Fi subnet's broadcast address. The target needs Wake-on-LAN enabled in its firmware or network settings. It needs no reservation, since it does not touch Metro.

## Check publication contents

Install [Gitleaks](https://github.com/gitleaks/gitleaks), then run:

```bash
python3 scripts/audit-publication.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The audit scans all reachable Git history and a snapshot of tracked and unignored files. It also checks that local configuration, runtime state, and common credential files stay excluded. Review any findings before publishing. Passing a secret scanner does not prove the absence of every private value.

## License

This repository is [MIT licensed](LICENSE). T3 Code, Codex, KeepADB, Tailscale, and other tools retain their own licenses. Their binaries and credentials are not included here.

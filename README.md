# T3 Code Android lab

Use a physical Android phone to test T3 Code worktrees against an isolated backend. The phone reaches Metro and the backend through wireless ADB over Tailscale. A rootless Podman container holds synthetic projects and a separate copy of your Codex login.

The skill appears only inside the T3 Code worktrees you register. The lab is not installed in a global skills directory.

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

After changing ports or moving this repository, rerun `install` and `prepare` to regenerate service paths and restart the bridge. If the old location left a skill symlink behind, remove that link before rerunning `worktree-setup`.

`config.local.json` belongs to this machine and is ignored by Git. `config.example.json` shows its fields. The Codex path must resolve to a native ELF binary rather than a shell or JavaScript wrapper. `build` uses the Node version and runtime dependency versions from the selected T3 Code checkout.

Enable user lingering if the services must start without an interactive login. On systemd hosts, an administrator can run `loginctl enable-linger "$USER"`. The backend, restricted proxy, and ADB reconnect timer start at boot. Metro starts only when a worktree is reserved.

### Pair the phone

On the phone, install and sign into Tailscale, enable Developer options and Wireless debugging, and keep the phone on a Wi-Fi network. Use a long finite screen timeout, keep Stay awake while charging disabled, and exempt Tailscale from battery restrictions. The configured Samsung A54 supports a 10-minute timeout.

Pair this host once using `adb pair <phone-address:pairing-port>`. Enter the temporary code through the secure terminal prompt. The pairing port differs from the connection port saved in the lab configuration. Pairing is per host ADB key. Wireless debugging chooses a new connection port after a reboot or toggle. When the saved port stops answering, `connect` and `prepare` scan the phone's Tailscale address for it and save the new port. The reconnect timer only restores a reserved phone on its saved port; it never scans and leaves an unreserved phone alone, because a scan costs several megabytes, possibly on the phone's mobile data.

Other T3 installations can use this machine as a configured remote device host. Alternatively, pair each device host directly. A single phone remains a shared resource; coordinate reservations across hosts before switching it. The local reservation file coordinates threads on this device host, not independent installations on other hosts.

## Make new worktrees discover the skill

Run this once in the main checkout and each existing worktree:

```bash
~/git/t3-android-lab/bin/t3-android-lab worktree-setup <checkout-or-worktree>
```

In the T3 Code project's **Settings → Actions**, add the command below. Enable **Run automatically on worktree creation** and **Wait for setup**:

```bash
/home/your-user/git/t3-android-lab/bin/t3-android-lab worktree-setup .
```

Use the absolute path returned by `realpath bin/t3-android-lab` on this machine. Actions are stored per T3 project. Preserve any existing worktree setup actions when adding this one. Configure a project-specific Actions override when the project inherits global settings. On installations with `projectSettingsFolded` enabled, updating the project record's `scripts` field alone does not change the effective actions; the override lives in `projectSettingsOverrides[projectId].defaultProjectScripts`.

The action links `.agents/skills/t3-android-lab` to this repository's skill and excludes that link through the repository's local Git exclude file. It verifies the Git common directory matches the configured T3 Code checkout. It rejects unrelated repositories. No upstream T3 Code files change.

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

Clone this repository and repeat machine setup, device pairing, and the T3 project action. Authenticate Codex on the new host and import only its login file. Create fresh synthetic state rather than copying an old container home volume.

The native-client fingerprint cache is managed by T3 Code. The setup does not require copying Android build outputs, ADB private keys, Codex history, T3 state, or other credentials into Git.

## Check and recover

```bash
bin/t3-android-lab verify
python3 tests/test_bridge.py
python3 -m unittest discover -s tests -p test_lab.py
systemctl --user status t3-android-lab-backend.service t3-android-lab-bridge.service
systemctl --user restart t3-android-lab-backend.service
```

The verifier checks actual container mounts, capabilities, synthetic project roots, blocked direct routes, proxy restrictions, and allowed OpenAI HTTPS. It does not run paid inference. Follow [the isolation reference](docs/isolation.md) before changing the container boundary.

Codex usage limits are account limits. A successful login does not guarantee another inference request is available. Renew credentials with `auth-import` only when authentication needs renewal. Credentials are never synchronized back to the host.

AI assistance: gpt-6.1-sol — implementation, documentation, and automated verification.

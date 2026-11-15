---
name: t3-android-lab
description: Onboard and debug remote Android phones and tablets in T3 Code over Tailscale, or test T3 Code worktrees against the isolated Android lab backend. Use for device pairing, managed versus on-demand availability, Android diagnostics, and sandbox worktree testing.
---

# Android lab in T3 Code

This is a global user skill. Resolve this file's symlink to locate the lab repository. Run `<lab-repository>/bin/t3-android-lab --help` to discover commands. Machine settings and device inventory live in that repository's ignored `config.local.json` and `devices.local.json`.

## Select the task and device

Read `device list` first. It reads local configuration without contacting devices. Use a named device. Managed entries are available for routine lab work. On-demand entries require the user's explicit request for that particular device, even if it appears online. An offline on-demand device needs no recovery, probing, or health check. Pass `--requested` only when that request exists. Keep personal addresses and device preferences in local configuration or user instructions.

- For a new device, follow [Onboard an Android device](../../docs/onboarding.md). Complete host registration, secure pairing, connection, and Device panel verification. Configure device-side recovery only for managed devices.
- For general debugging, use `device connect <name>` and T3's Device tools. On-demand devices also need `--requested`. This path needs no T3 checkout, container, Metro, or Codex authentication. It sets no app port forwards. Read logs with ADB targeted to the connected serial. Close your own AgentDevice session afterward; disconnect an on-demand device when its requested session ends.
- For T3 Code development, use the sandbox workflow below. It takes precedence over `test-t3-mobile` for reservations, backend and data isolation, Metro, native installation, pairing, and cleanup. Consult that skill only for client test procedures when available.

Always use `device_open` and its returned bound AgentDevice command for device interaction. Use ADB for logs, installs, builds, forwarding, and diagnostics the command cannot provide. Port discovery is explicit with `device connect <name> --discover`; timers never scan.

## Prepare a T3 worktree

Run sandbox `status` first. Its `serial` selects one phone independently of the device inventory. Check that it is the intended device; selecting an on-demand device requires an explicit request too. The sandbox has one shared backend and Metro per host.

Reserve with your T3 thread ID as owner, obtained from context or `orchestrator_capabilities().parentThreadId`:

```bash
<lab-command> prepare <worktree> --owner <thread-id> [--hours 2]
<lab-command> wait-ready
```

Reservations last two hours by default, at most twelve. Only the owner can extend with `extend --owner <thread-id> --hours <n>`. On expiry the sandbox timer stops Metro and frees the reservation.

An existing reservation blocks switching. Read the owning thread with `t3_thread_read` and ask it with `t3_thread_send` to release the phone if it looks finished. Keep the reservation until its owner releases it or it expires. A one-shot watch can wait for `<lab-command> free`, which prints only when unreserved.

`prepare` builds the isolated backend from the worktree, switches Metro, installs the matching cached native client, and restores ADB forwards. It starts no real agent turns. Native builds use two cores and a 5 GB memory cap. Use `--skip-native` only when the installed client matches the worktree's native fingerprint. A client from an older base can fail with a native module error.

Wait for running dependency setup. Otherwise install missing dependencies with `vp i --frozen-lockfile` before preparation. Worktree validation still restricts sandbox builds to the configured T3 checkout; the skill itself is available globally.

## Launch and verify

Call `device_list`, then `device_open` for the configured serial and host. Use only its returned bound command for launches, deep links, input, screenshots, and recordings. If asleep, use that command's `hardware-button wake`, then dismiss a swipe lock with a swipe.

Read Metro's port from `status`. Open `com.t3tools.t3code.dev` with the development-client URL for `http://127.0.0.1:<metro-port>`, adding `disableOnboarding=1&disableAutoLaunch=1&disableFab=1`. A cold `--relaunch` can recover a blank screen after Metro was interrupted.

If the backend connection is missing, run:

```bash
<lab-command> pair --owner <thread-id> --agent-device <returned-bound-command>
```

The helper keeps credentials out of stdout. Verify the synthetic Sandbox Demo project appears. Use Replay or synthetic fixtures for routine tests. Run paid inference only when the task depends on a real provider, and report usage limits instead of retrying them.

## Keep the sandbox boundary

The backend contains synthetic projects and its own copy of a Codex ChatGPT login. Keep host homes, live T3 databases, real workspaces, other credentials, and container-management sockets outside it. Use `auth-import` only for explicit login renewal. Keep the restricted proxy allowlist unchanged for unrelated network failures.

Read [isolation constraints](../../docs/isolation.md) before infrastructure changes and run `verify` against actual mounts and routes afterward. Device control and general debugging run on the host, outside that backend's isolation.

## Release

Close the AgentDevice session you opened using its bound command's `close`, then run `release --owner <thread-id>`, even if device cleanup failed. The lab reservation and AgentDevice ownership are separate. Release stops Metro and frees the reservation; the synthetic backend stays available.

For full sandbox shutdown, use `stop --owner <thread-id>` instead. It preserves sandbox data and credentials. The next `prepare` restarts the runtime. The independent `device stop` command disables the managed-device reconnect timer.

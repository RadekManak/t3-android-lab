---
name: t3-android-lab
description: Preferred workflow for testing T3 Code Android on the configured shared phone. Use before upstream test-t3-mobile for phone testing, worktree switching, pairing recovery, or sandbox diagnostics. Keeps the backend isolated with synthetic data and its own Codex login.
---

# T3 Code Android lab

This skill is linked only into registered T3 Code worktrees. Resolve this file's symlink to find the lab repository. The CLI is `<lab-repository>/bin/t3-android-lab`; read its `--help` for commands. Machine settings are in that repository's ignored `config.local.json`.

For the shared phone, start with this skill. It takes precedence over upstream `test-t3-mobile` for reservations, backend and data isolation, Metro, builds and installation, pairing, and cleanup. Consult upstream only for Device panel and AgentDevice controls or UI test procedures, using this lab's sandbox connection and synthetic fixtures.

## Prepare a worktree

Run `status` first. The phone is shared across threads and machines. Reserve it with your T3 thread ID as the owner, from context or `orchestrator_capabilities().parentThreadId`, so other agents can find the thread that holds it:

```bash
<lab-command> prepare <worktree> --owner <thread-id> [--hours 2]
<lab-command> wait-ready
```

A reservation lasts two hours by default, at most twelve. Run `extend --owner <thread-id> --hours <n>` before it runs out; only the owner can extend. When it expires, the lab stops its Metro and frees the phone within a minute.

`prepare` builds the isolated backend from this worktree, switches Metro to the same checkout, installs the matching cached native client, and restores ADB forwards. A reservation owned by another thread blocks the switch. Do not release it yourself. Read the owning thread with `t3_thread_read` and, if it looks idle or finished, ask it with `t3_thread_send` to release the phone. To continue when the phone frees up, register a one-shot watch on `<lab-command> free`, which prints only when nothing holds the phone: `t3-watch add --thread <thread-id> --name android-phone-free --once --every 2m --expires-in 1d -- <lab-command> free`. A native build runs on two cores under a 5 GB memory cap so it cannot freeze the host. Pass `--skip-native` to keep the installed client only when it was built from the same base, such as a JS-only change on top of it; a client from an older base fails on launch with a native module error.

If dependency setup is still running, wait for it. Otherwise install missing checkout dependencies with `vp i --frozen-lockfile` before preparing the device.

## Launch and verify

Use the repository's `test-t3-mobile` skill for T3 Device panel interaction. Call `device_open` with the configured ADB device, and use only its returned bound AgentDevice command for app launches, links, taps, input and screenshots.

If the screen is asleep, use the returned command's `hardware-button wake`, then swipe up to dismiss the phone's swipe lock.

Read Metro's port from `status`. Through the returned AgentDevice command, open `com.t3tools.t3code.dev` with the development-client URL for `http://127.0.0.1:<metro-port>`. Append `disableOnboarding=1&disableAutoLaunch=1&disableFab=1`. A cold `--relaunch` can recover a blank screen after Metro was interrupted.

If the sandbox is not already saved on the phone, run:

```bash
<lab-command> pair --owner <thread-id> --agent-device <returned-bound-command>
```

The pairing helper keeps credentials out of stdout. Confirm the synthetic Sandbox Demo project appears. Exercise the requested flow and capture evidence through AgentDevice. Use Replay or synthetic fixtures for routine UI checks; run real Codex inference only when needed. Report an account usage limit instead of repeatedly retrying it.

## Keep the boundary

The backend's only external-service credential is its own copy of the Codex ChatGPT login. Its home volume contains synthetic state. Keep host home directories, real projects, live T3 databases, other credentials, and container-management sockets outside the container. Use `auth-import` only when Codex authentication needs explicit renewal. Do not broaden the proxy allowlist to fix unrelated network errors.

Read `docs/isolation.md` in the lab repository when inspecting the boundary. Use the lab verifier to check real container mounts and blocked routes after infrastructure changes.

## Release

When testing is finished, close the AgentDevice session you opened using its returned bound command with `close`, then run `release --owner <thread-id>` even if device cleanup failed. AgentDevice ownership and the lab reservation are separate; releasing only the reservation leaves the device locked to your session. The lab release stops the reserved Metro and frees its reservation. The sandbox backend and automatic ADB reconnection remain available. An interrupted owner can resume with the same ID or release that reservation explicitly before another thread uses the phone.

For full runtime cleanup, close your own AgentDevice session and run `stop --owner <thread-id>` instead of `release`. This stops the backend, bridge, Metro, and reconnect timer while preserving sandbox state and credentials. The next `prepare` restarts them.

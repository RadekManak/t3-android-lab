---
name: t3-android-lab
description: Preferred workflow for testing T3 Code Android on the configured shared phone. Use before upstream test-t3-mobile for phone testing, worktree switching, pairing recovery, or sandbox diagnostics. Keeps the backend isolated with synthetic data and its own Codex login.
---

# T3 Code Android lab

This skill is linked only into registered T3 Code worktrees. Resolve this file's symlink to find the lab repository. The CLI is `<lab-repository>/bin/t3-android-lab`; read its `--help` for commands. Machine settings are in that repository's ignored `config.local.json`.

For the shared phone, start with this skill. It takes precedence over upstream `test-t3-mobile` for reservations, backend and data isolation, Metro, builds and installation, pairing, and cleanup. Consult upstream only for Device panel and AgentDevice controls or UI test procedures, using this lab's sandbox connection and synthetic fixtures.

## Prepare a worktree

Run `status` first. The phone is shared across threads and machines. Reserve it with a stable owner such as this thread's ID:

```bash
<lab-command> prepare <worktree> --owner <thread-id>
<lab-command> wait-ready
```

`prepare` builds the isolated backend from this worktree, switches Metro to the same checkout, installs the matching cached native client, and restores ADB forwards. A reservation owned by another thread blocks the switch. Leave it alone and report the current owner.

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

When testing is finished, run `release --owner <thread-id>`. This stops the reserved Metro and releases the phone. The sandbox backend and automatic ADB reconnection remain available. An interrupted owner can resume with the same ID or release that reservation explicitly before another thread uses the phone.

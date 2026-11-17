# T3 Code Android lab

Give your T3 Code coding agent access to real Android phones and tablets. This agent-written lab manages pairing and connections. T3 Code's Device tools provide app interaction and screenshots.

**T3 Code is required for the supported workflow.** It hosts the coding-agent session and exposes the Device tools the agent uses to open and control devices. The lab's installed skill gives the agent its setup and operating instructions.

## How it works

The agent runs the lab's Python CLI on a Linux device host. Tailscale provides a private network between that host and the Android device. Paired wireless Android Debug Bridge (ADB) provides debugging access, and `device connect <name>` discovers changed connection ports.

T3 Code's Device tools handle app interaction and screenshots. ADB handles logs, installs, and port forwarding. You handle the initial Android settings and authentication until remote access is available.

For T3 Code development, the optional sandbox selects a registered device by name, reserves it for one worktree, and runs an isolated backend with synthetic projects. `sandbox setup` builds, starts, and verifies that environment. `sandbox prepare` switches worktrees and automatically checks whether the native client needs updating. The backend and app development server run on the Linux host. The Android app connects to them through ADB forwarding.

## System requirements

For device access:

- T3 Code with Device tools available to your coding agent.
- A Linux device host with Python 3.9 or newer, Git, Android SDK Platform-Tools (`adb`), and Tailscale.
- An Android phone or tablet with Wireless debugging enabled, connected to Wi-Fi and the same tailnet as the host.

ADB is a host dependency and is not bundled with this repository. Device access does not require a T3 Code source checkout or the optional sandbox.

For the optional development sandbox, also install:

- Rootless Podman 5 or newer with Quadlet and systemd user services.
- A T3 Code source checkout with dependencies installed, Node.js, and Vite+.
- A standalone Linux Codex binary with authentication for the isolated backend.
- Java and the Android SDK when building a native client.

Go is needed only for the optional Wake-on-LAN helper that runs on the phone.

## Get started

Clone the repository on your Linux device host and install the agent skill:

```bash
git clone https://github.com/RadekManak/t3-android-lab.git
cd t3-android-lab
bin/t3-android-lab skill-install
```

This installs the lab instructions as a global user skill by linking `~/.agents/skills/t3-android-lab` to this checkout. Keep the checkout in place. Ask your T3 Code agent to use the `t3-android-lab` skill to onboard a device. Use `--directory ~/.codex/skills` if that is where your agent reads skills.

Use `device …` for pairing and debugging access, and `sandbox …` for the optional development environment. Each command has `--help` with examples.

- [Device onboarding](docs/onboarding.md): host prerequisites, secure pairing, and optional managed-device recovery.
- [Unattended device setup](docs/unattended.md): model-specific research, recovery settings, and tests for managed devices left unattended.
- [Setup and operations guide](docs/guide.md): CLI use, sandbox setup, worktree testing, shutdown, and maintenance.
- [Agent skill](skills/t3-android-lab/SKILL.md): device selection and the agent's working procedure.
- [Isolation boundary](docs/isolation.md): what the development sandbox can access.

## License and authorship

Written primarily by coding agents and intended primarily for coding agents.

[MIT licensed](LICENSE). T3 Code, Codex, KeepADB, Tailscale, and other dependencies retain their own licenses. Their binaries and credentials are not included here.
